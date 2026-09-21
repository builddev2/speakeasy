"""Private, offline same-input probe for source and frozen executables.

No capture, UI, insertion or diagnostic cleanup. Timings exclude those paths.
Explicit mel evaluation is experimental; baseline preserves lazy evaluation.
"""
import argparse
import contextlib
from dataclasses import asdict
import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import resource
import subprocess
import sys
import time
import wave


def validate_fixture(wav, reference):
    with wave.open(str(wav)) as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 16000):
            raise ValueError("fixture must be mono PCM16 at 16000 Hz")
        frames = source.getnframes()
        if frames < 4000 or len(source.readframes(frames)) != frames * 2:
            raise ValueError("fixture is empty, short or truncated")
    text = reference.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError("reference must be nonempty")
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path)
    parser.add_argument("reference", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pairs", type=int, default=31)
    parser.add_argument("--idle-seconds", type=float, default=0)
    parser.add_argument("--treatment", choices=("evaluated_mel", "refresh"), default="evaluated_mel")
    args = parser.parse_args(argv)
    if args.pairs < 1 or not math.isfinite(args.idle_seconds) or args.idle_seconds < 0:
        parser.error("pairs must be positive; idle must be finite and nonnegative")
    reference = validate_fixture(args.wav, args.reference)  # before any MLX/model import
    fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as output, contextlib.redirect_stdout(io.StringIO()):
        os.environ["HF_HUB_OFFLINE"] = "1"
        from . import transcriber as module, settings, config
        from .dictation_benchmark import DictationTiming
        from .evaluate import word_error_rate
        import mlx.core as mx
        import numpy as np
        from mlx.utils import tree_flatten
        audio = module.read_wav_mono_f32(args.wav)
        model = module.Transcriber()
        frozen = bool(getattr(sys, "frozen", False))
        dirty = None if frozen else bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=Path(__file__).resolve().parent.parent).strip())
        versions = {}
        for name in ("mlx", "mlx-metal", "parakeet-mlx", "numpy", "librosa"):
            try:
                versions[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                versions[name] = None
        if frozen:
            build = json.loads((Path(sys.executable).resolve().parent.parent / "Resources/release-build.json").read_text())
            versions.update(build["versions"])
        original_mel = module.get_logmel
        cls = type(model._model)
        original_generate, original_decode = cls.generate, cls.decode
        events = {}

        def generate(instance, *a, **k):
            events["generate"] = time.perf_counter()
            return original_generate(instance, *a, **k)

        def decode(instance, *a, **k):
            events["decode"] = time.perf_counter()
            try:
                return original_decode(instance, *a, **k)
            finally:
                events["decoded"] = time.perf_counter()

        metadata = dict(
            build_commit=settings.build_commit(), dirty=dirty,
            runtime="packaged" if frozen else "source", model_id=config.MODEL_ID,
            configuration="batch_trimmed_default", versions=versions,
            parameter_dtypes=sorted({str(v.dtype) for _, v in tree_flatten(model._model.parameters())}),
            preprocessor=asdict(model._model.preprocessor_config),
            filterbank_sha256=hashlib.sha256(np.array(model._model.preprocessor_config._filterbanks).tobytes()).hexdigest(),
            fixture_sha256=hashlib.sha256(args.wav.read_bytes()).hexdigest(),
            capture_seconds=len(audio) / config.SAMPLE_RATE,
            model_load_ms=model._model_load_ms, model_warmup_ms=model._model_warmup_ms,
            memory_definitions={"process_peak_rss_bytes": "process lifetime maximum RSS, macOS bytes",
                                "mlx_active_bytes": "MLX live allocator bytes, not RSS or GPU residency",
                                "mlx_peak_bytes": "MLX allocator high-water bytes, not GPU residency"},
        )
        cls.generate, cls.decode = generate, decode
        try:
            for pair in range(args.pairs):
                for treatment in ((False, True) if pair % 2 == 0 else (True, False)):
                    def mel(*a, **k):
                        value = original_mel(*a, **k)
                        if treatment and args.treatment == "evaluated_mel":
                            mx.eval(value)
                        return value
                    module.get_logmel = mel
                    idle_start = time.perf_counter()
                    if args.idle_seconds:
                        time.sleep(args.idle_seconds)
                    idle = time.perf_counter() - idle_start
                    refresh_ms = 0
                    if treatment and args.treatment == "refresh":
                        start = time.perf_counter()
                        model.transcribe(np.zeros(config.SAMPLE_RATE, dtype=np.float32))
                        refresh_ms = (time.perf_counter() - start) * 1000
                    timing = DictationTiming(pair * 2 + int(treatment) + 1)
                    timing.mode = "batch"
                    events.clear()
                    start = time.perf_counter()
                    text = model.transcribe(audio, timing=timing)
                    elapsed = (time.perf_counter() - start) * 1000
                    row = dict(metadata, pair=pair, treatment=args.treatment if treatment else "baseline",
                               elapsed_ms=elapsed, refresh_ms=refresh_ms, total_compute_wall_ms=elapsed + refresh_ms,
                               actual_idle_seconds=idle, empty=not bool(text), wer=word_error_rate(reference, text),
                               output_sha256=hashlib.sha256(text.encode()).hexdigest(),
                               phases=timing.record("success"),
                               encoder_and_deferred_mel_ms=(events["decode"] - events["generate"]) * 1000,
                               decoder_ms=(events["decoded"] - events["decode"]) * 1000,
                               process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                               mlx_active_bytes=mx.get_active_memory(), mlx_peak_bytes=mx.get_peak_memory())
                    output.write(json.dumps(row) + "\n")
                    output.flush()
        finally:
            module.get_logmel = original_mel
            cls.generate, cls.decode = original_generate, original_decode
