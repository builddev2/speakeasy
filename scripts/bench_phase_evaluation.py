"""Balanced offline batch experiment: lazy baseline versus evaluated mel.

No microphone, clipboard, or runtime setting changes. Each pair reverses order;
--idle-seconds waits physically before EACH run. Use one pair for an initial
idle reproduction, not 30 long idle pairs without planning that elapsed time.
"""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path)
    parser.add_argument("reference", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pairs", type=int, default=30)
    parser.add_argument("--idle-seconds", type=float, default=0)
    args = parser.parse_args()
    if args.pairs < 1 or not 0 <= args.idle_seconds < float("inf"):
        parser.error("pairs must be positive and idle finite/nonnegative")
    os.environ["HF_HUB_OFFLINE"] = "1"
    from speakeasy import transcriber as module, settings
    from speakeasy.dictation_benchmark import DictationTiming
    from speakeasy.evaluate import word_error_rate
    import mlx.core as mx

    audio = module.read_wav_mono_f32(args.wav)
    reference = args.reference.read_text()
    if len(audio) < 4000 or not reference.strip():
        parser.error("nonempty PCM16 audio and reference required")
    with contextlib.redirect_stdout(io.StringIO()):
        model = module.Transcriber()
    original = module.get_logmel
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD"], check=False).returncode != 0
    output_fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(output_fd, "w") as output:
            for pair in range(args.pairs):
                order = (False, True) if pair % 2 == 0 else (True, False)
                for evaluate in order:
                    def mel(*a, **k):
                        result = original(*a, **k)
                        if evaluate:
                            mx.eval(result)
                        return result
                    module.get_logmel = mel
                    before_idle = time.perf_counter()
                    if args.idle_seconds:
                        time.sleep(args.idle_seconds)
                    idle = time.perf_counter() - before_idle
                    timing = DictationTiming(pair + 1)
                    timing.mode = "batch"
                    load = os.getloadavg()
                    started = time.perf_counter()
                    text = model.transcribe(audio, timing=timing)
                    elapsed = (time.perf_counter() - started) * 1000
                    output.write(json.dumps(dict(
                        build_commit=settings.build_commit(), source_tree_dirty=dirty,
                        pair=pair + 1, treatment="evaluated_mel" if evaluate else "lazy_baseline",
                        capture_seconds=len(audio) / 16000, actual_idle_seconds=idle,
                        elapsed_ms=elapsed, wer=word_error_rate(reference, text),
                        empty=not bool(text), phases=timing.record("success"),
                        load_average=list(load), process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                        mlx_active_bytes=mx.get_active_memory(), mlx_peak_bytes=mx.get_peak_memory(),
                    )) + "\n")
                    output.flush()
    finally:
        module.get_logmel = original


if __name__ == "__main__":
    main()
