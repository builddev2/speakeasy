"""Offline inference replay; never captures, inserts, or prints recognized text.

Run with a consented local PCM16 WAV and its fixed UTF-8 reference. Cold samples
require separate process launches. Idle uses actual elapsed time, never a fake
clock. Replay measures total inference, NOT release-to-paste latency.
"""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path)
    parser.add_argument("reference", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--attempts", type=int, default=30)
    parser.add_argument("--idle-seconds", type=float, default=0)
    parser.add_argument("--mode", choices=("batch", "streaming"), default="batch")
    parser.add_argument("--after", choices=("dictation", "diagnostic", "meeting"), default="dictation")
    args = parser.parse_args()
    if args.attempts < 1 or args.idle_seconds < 0:
        parser.error("attempts must be positive and idle seconds nonnegative")
    os.environ["HF_HUB_OFFLINE"] = "1"
    from speakeasy import config, settings
    from speakeasy.dictation_benchmark import DictationTiming
    from speakeasy.dictation_stream import StreamingSession, run_stream
    from speakeasy.evaluate import word_error_rate
    from speakeasy.latency_protocol import classify
    from speakeasy.transcriber import Transcriber, read_wav_mono_f32
    import mlx.core as mx

    audio = read_wav_mono_f32(args.wav)
    if len(audio) < config.SAMPLE_RATE * config.MIN_DURATION_SECONDS:
        parser.error("fixture is empty or shorter than the minimum take duration")
    reference = args.reference.read_text()
    if not reference.strip():
        parser.error("a fixed nonempty reference is required")
    duration = len(audio) / config.SAMPLE_RATE
    group = "short" if duration < 5 else "medium" if duration < 15 else "long"
    started = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()):
        transcriber = Transcriber()
    warmup_finished = time.perf_counter()
    load_ms = (warmup_finished - started) * 1000
    source_tree_dirty = subprocess.run(
        ["git", "diff", "--quiet", "HEAD"],
        cwd=Path(__file__).resolve().parent.parent, check=False,
    ).returncode != 0
    last_finished = None
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        for attempt in range(args.attempts):
            if args.after == "diagnostic":
                from speakeasy.dictation_diagnostic import compare
                compare(transcriber, audio, reference)
                last_finished = time.perf_counter()
            elif args.after == "meeting":
                transcriber.transcribe_long(audio)
                last_finished = time.perf_counter()
            if args.idle_seconds:
                time.sleep(args.idle_seconds)
            now = time.perf_counter()
            timing = DictationTiming(attempt + 1)
            temperature = classify(last_finished, now, args.after,
                                   args.idle_seconds or 1800)
            if args.mode == "batch":
                text = transcriber.transcribe(audio, timing=timing)
                parity = None
                status = "batch"
            else:
                session = StreamingSession(max_chunks=1)
                session.put_nowait(audio)
                session.finish(audio)
                result = run_stream(transcriber._model, session, to_device=mx.array,
                                    cache_depth=2, batch_final_seconds=float("inf"))
                text = result.text or ""
                parity = session.integrity_matches()
                status = result.status.value
            finished = time.perf_counter()
            row = dict(build_commit=settings.build_commit(), source_tree_dirty=source_tree_dirty,
                       attempt=attempt + 1,
                       mode=args.mode, temperature=temperature, duration_group=group,
                       capture_seconds=duration, idle_requested_seconds=args.idle_seconds,
                       since_last_inference_seconds=now-(warmup_finished if last_finished is None else last_finished),
                       model_load_warmup_complete=True, model_load_warmup_ms=load_ms,
                       elapsed_ms=(finished-now)*1000, wer=word_error_rate(reference, text),
                       empty_result=not bool(text), integrity_matches=parity, status=status,
                       phases=timing.record("success"),
                       active_memory_bytes=mx.get_active_memory(),
                       peak_memory_bytes=mx.get_peak_memory())
            output.write(json.dumps(row) + "\n")
            output.flush()
            last_finished = finished


if __name__ == "__main__":
    main()
