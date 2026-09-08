"""Explicit, local microphone comparisons. Never writes product telemetry."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import time

import numpy as np

from . import config, settings
from .dictation_stream import StreamStatus, StreamingSession, run_stream

TEMP_TTL_SECONDS = 900


def sweep_temporary_audio():
    root = settings.app_support_dir() / "dictation-diagnostic-temp"
    if root.exists():
        for path in root.glob("take-*.wav"):
            path.unlink(missing_ok=True)


@contextmanager
def temporary_wav(audio):
    """IEEE float WAV preserves the authoritative float32 samples exactly."""
    root = settings.app_support_dir() / "dictation-diagnostic-temp"
    root.mkdir(mode=0o700, exist_ok=True)
    root.chmod(0o700)
    fd, name = tempfile.mkstemp(prefix="take-", suffix=".wav", dir=root)
    path = Path(name)
    timer = threading.Timer(TEMP_TTL_SECONDS, lambda: path.unlink(missing_ok=True))
    timer.daemon = True
    try:
        with os.fdopen(fd, "wb") as target:
            data = np.asarray(audio, dtype="<f4").tobytes()
            target.write(struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(data),
                                     b"WAVE", b"fmt ", 16, 3, 1, config.SAMPLE_RATE,
                                     config.SAMPLE_RATE * 4, 4, 32, b"data", len(data)))
            target.write(data)
        timer.start()
        yield path
    finally:
        timer.cancel()
        path.unlink(missing_ok=True)


def measured(function):
    started = time.perf_counter()
    result = function()
    return result, (time.perf_counter() - started) * 1000


def compare(transcriber, audio, reference):
    """Called only on the existing model worker, after live context exit."""
    from .evaluate import word_error_rate
    import mlx.core as mx

    report = {}
    text, elapsed = measured(lambda: transcriber.transcribe(audio))
    report["batch"] = {"text": text, "ms": elapsed,
                       "wer": word_error_rate(reference, text)}
    trim_enabled = config.DICTATION_TRIM_ENABLED
    try:
        config.DICTATION_TRIM_ENABLED = False
        text, elapsed = measured(lambda: transcriber.transcribe(audio))
        report["batch_untrimmed"] = {"text": text, "ms": elapsed,
                                     "wer": word_error_rate(reference, text)}
    finally:
        config.DICTATION_TRIM_ENABLED = trim_enabled
    for depth in (1, 2):
        session = StreamingSession(max_chunks=1)
        # run_stream still coalesces this into the exact live two-second blocks.
        session.put_nowait(audio)
        session.finish(audio)
        result, elapsed = measured(lambda: run_stream(
            transcriber._model, session, to_device=mx.array, cache_depth=depth,
            batch_final_seconds=float("inf")))
        report[f"stream_depth_{depth}"] = {
            "text": result.text, "status": result.status.value, "ms": elapsed,
            "wer": word_error_rate(reference, result.text or ""),
            "received_frames": session.received_frames,
            "integrity_matches": session.integrity_matches(),
        }
    return report


def record_take(transcriber, worker, control, reference, *, sounds=False,
                prompt=None, recorder=None, session=None, processing=lambda: None,
                is_cancelled=lambda: False):
    from .recorder import Recorder
    from .engine import play_sound

    prompt = prompt or input
    recorder = recorder or Recorder()
    session = session or StreamingSession()
    future = None
    try:
        control.submit(recorder.prewarm).result()
        prompt("Reference is fixed. Press Return to start recording: ")
        control.submit(recorder.start, chunk_queue=session).result()
        future = worker.submit(transcriber.transcribe_stream, session)
        if sounds:
            play_sound(config.SOUND_START)
        prompt("RECORDING — speak the reference, then press Return to stop: ")
        processing()
        released = time.perf_counter()
        stop = control.submit(recorder.stop)
        try:
            audio = stop.result(timeout=config.RECORDER_STOP_TIMEOUT_SECONDS)
        except TimeoutError:
            recorder.force_close()
            raise RuntimeError("diagnostic recorder stop timed out") from None
        session.finish(audio, valid=recorder.stream_delivery_complete
                       and recorder.stream_dropped_frames == 0)
        if sounds:
            play_sound(config.SOUND_STOP)
        live = future.result()
        if is_cancelled():
            raise InterruptedError()
        live_ms = (time.perf_counter() - released) * 1000
        with temporary_wav(audio) as wav:
            saved = np.frombuffer(wav.read_bytes(), dtype="<f4", offset=44).copy()
            if not np.array_equal(saved, audio):
                raise ValueError("diagnostic WAV roundtrip mismatch")
            report = worker.submit(compare, transcriber, saved, reference).result()
        from .evaluate import word_error_rate
        report["live"] = {
            "text": live.text, "status": live.status.value,
            "release_to_result_ms": live_ms,
            "wer": (word_error_rate(reference, live.text or "")
                    if live.status is StreamStatus.COMPLETE else None),
            "outcome": ("batch_handoff" if live.status is StreamStatus.BATCH_REQUIRED
                        else live.status.value),
            "received_frames": session.received_frames,
            "integrity_matches": session.integrity_matches(),
            "delivery_complete": recorder.stream_delivery_complete,
            "stream_dropped_frames": recorder.stream_dropped_frames,
            "stream_metrics": session.metrics(),
        }
        signal = np.flatnonzero(np.abs(audio) >= config.TRIM_ABSOLUTE_FLOOR)
        report["audio"] = {
            "frames": len(audio), "seconds": len(audio) / config.SAMPLE_RATE,
            "sample_rate": config.SAMPLE_RATE, "channels": 1, "dtype": "float32",
            "finite": bool(np.isfinite(audio).all()),
            "peak": float(np.max(np.abs(audio))) if len(audio) else 0,
            "rms": float(np.sqrt(np.mean(audio ** 2))) if len(audio) else 0,
            "clipped_frames": int(np.count_nonzero(np.abs(audio) >= 1)),
            "silent_frames": int(np.count_nonzero(audio == 0)),
            "first_signal_frame": int(signal[0]) if len(signal) else None,
            "last_signal_frame": int(signal[-1]) if len(signal) else None,
            "start_sound_enabled": sounds,
        }
        return report
    finally:
        session.cancel()
        recorder.force_close()
        if future is not None:
            future.result()


def summarize(takes):
    from .evaluate import _words, edit_distance

    def wer(rows, mode):
        words = sum(len(_words(row["reference"])) for row in rows)
        errors = sum(edit_distance(_words(row["reference"]),
                                  _words(row[mode]["text"] or "")) for row in rows)
        return errors / words if words else None

    groups = {name: [row for row in takes if row["duration_group"] == name]
              for name in ("short", "medium", "long")}
    modes = ("batch", "stream_depth_1", "stream_depth_2")
    overall = {mode: wer(takes, mode) for mode in modes}
    per_group = {name: {mode: wer(rows, mode) for mode in modes}
                 for name, rows in groups.items()}
    catastrophes = sum(any(row[mode]["wer"] > 0.5 or not row[mode]["text"]
                           for mode in ("batch", "stream_depth_2")) for row in takes)
    integrity_failures = sum(not row["live"]["integrity_matches"]
                             or not row["live"]["delivery_complete"]
                             or row["live"]["stream_dropped_frames"] > 0
                             for row in takes)
    coverage = len(takes) >= 30 and all(len(rows) >= 10 for rows in groups.values())
    coverage = coverage and all(sum(row.get(field) == value for row in takes) >= 15
                                for field, values in (("condition", ("quiet", "moderate_noise")),
                                                      ("vocabulary", ("ordinary", "technical")))
                                for value in values)
    required_wers = [overall[mode] for mode in ("batch", "stream_depth_2")]
    required_wers += [group[mode] for group in per_group.values()
                      for mode in ("batch", "stream_depth_2")]
    numerical_pass = (coverage and not catastrophes and not integrity_failures
                      and all(value is not None and value <= .05 for value in required_wers)
                      and overall["stream_depth_2"] <= overall["batch"] + .01)
    missing = {name: max(0, 10 - len(rows)) for name, rows in groups.items()}
    reasons = []
    for name, count in missing.items():
        if count:
            reasons.append(f"{name.title()} recordings: {len(groups[name])}/10; {count} more needed.")
    if len(takes) < 30:
        reasons.append(f"Completed recordings: {len(takes)}/30.")
    for field, values in (("condition", ("quiet", "moderate_noise")),
                          ("vocabulary", ("ordinary", "technical"))):
        for value in values:
            count = sum(row.get(field) == value for row in takes)
            if count < 15:
                reasons.append(f"{value.replace('_', ' ').title()} coverage: {count}/15.")
    for name, rates in [("Overall", overall), *[(k.title(), v) for k, v in per_group.items()]]:
        for mode, label in (("batch", "batch"), ("stream_depth_2", "streaming")):
            rate = rates[mode]
            if rate is not None and rate > .05:
                reasons.append(f"{name} {label} accuracy: {100 * (1 - rate):.2f}%; requires 95%.")
    if overall["batch"] is not None and overall["stream_depth_2"] > overall["batch"] + .01:
        reasons.append("Streaming WER exceeds batch by more than one percentage point.")
    if catastrophes:
        reasons.append(f"{catastrophes} potential catastrophic transcription(s).")
    if integrity_failures:
        reasons.append(f"{integrity_failures} audio integrity failure(s).")
    return {"protocol": "30_prompt_screen", "takes": len(takes), "duration_counts": {k: len(v) for k, v in groups.items()},
            "weighted_wer": overall, "duration_wer": per_group,
            "missing_duration_counts": missing, "failure_reasons": reasons,
            "batch_handoffs": sum(row["live"].get("status") == "batch_required" for row in takes),
            "catastrophic_candidates": catastrophes, "integrity_failures": integrity_failures,
            "numerical_gate_pass": numerical_pass,
            "gate": "unmet: manual audible-speech, truncation, repetition and condition review required"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="new private JSON report; contains reference and transcripts")
    parser.add_argument("--sounds", action="store_true", help="include normal app sounds")
    parser.add_argument("--corpus", type=Path,
                        help="JSON list of prewritten references, condition and vocabulary labels")
    args = parser.parse_args(argv)
    print("LOCAL DIAGNOSTIC: microphone audio is temporarily retained in a private WAV.")
    print("Audio is deleted after comparison, on failure, or after 15 minutes;")
    print("crash leftovers are deleted on the next Speakeasy/diagnostic launch.")
    print("The requested JSON retains reference and transcripts. Nothing is pasted.")
    corpus = json.loads(args.corpus.read_text()) if args.corpus else None
    if corpus is not None and (not isinstance(corpus, list) or not corpus or
            any(not isinstance(row, dict) or not isinstance(row.get("reference"), str) or not row["reference"].strip()
                for row in corpus)):
        raise ValueError("corpus must contain nonempty reference strings")
    count = len(corpus) if corpus else 1
    if input(f"Type RECORD to consent to {count} microphone take(s): ") != "RECORD":
        return
    if corpus is None:
        reference = input("Type the exact reference BEFORE speaking: ").strip()
        if not reference:
            raise ValueError("a nonempty reference is required")
        corpus = [{"reference": reference, "condition": "unspecified",
                   "vocabulary": "unspecified"}]
    # Reserve before recording: never overwrite an existing report or symlink.
    fd = os.open(args.output.expanduser(), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as target:
        sweep_temporary_audio()
        # A missing dev cache must fail locally, never initiate a model download.
        os.environ["HF_HUB_OFFLINE"] = "1"
        from .transcriber import Transcriber
        takes = []

        def save_report(failure=None):
            summary = summarize(takes)
            if failure is not None:
                summary["numerical_gate_pass"] = False
            target.seek(0)
            json.dump({"source": "consented_real_microphone",
                       "build_commit": settings.build_commit(), "takes": takes,
                       "failed_attempt": failure, "summary": summary},
                      target, indent=2, allow_nan=False)
            target.truncate()
            target.flush()
            os.fsync(target.fileno())

        with ThreadPoolExecutor(max_workers=1) as worker, ThreadPoolExecutor(max_workers=1) as control:
            transcriber = worker.submit(Transcriber).result()
            for index, entry in enumerate(corpus):
                print(f"Take {index + 1}/{count}; condition: {entry.get('condition', 'unspecified')}")
                print("Read the prewritten reference in your corpus file for this take.")
                try:
                    report = record_take(transcriber, worker, control, entry["reference"], sounds=args.sounds)
                except BaseException as error:
                    save_report({"attempt": index + 1, "type": type(error).__name__})
                    raise
                seconds = report["audio"]["seconds"]
                report.update(reference=entry["reference"],
                              condition=entry.get("condition", "unspecified"),
                              vocabulary=entry.get("vocabulary", "unspecified"),
                              duration_group="short" if seconds < 5 else "medium" if seconds < 15 else "long")
                takes.append(report)
                save_report()
    print("Comparison complete. Audio deleted; private JSON saved.")


if __name__ == "__main__":
    main()
