"""Parent-process memory with diarization in a child, on synthetic meetings.

Synthetic audio only (macOS `say`, four voices); HOME is redirected to a temp
dir and asserted BEFORE speakeasy is imported, so the real App Support folder
is never touched. Bundled dev models (models/diarization) only.

Usage: .venv/bin/python -B scripts/measure_diarization_memory.py
Runs ~8 minutes. Prints PASS/FAIL against the spec's acceptance numbers:
parent growth <= 5 MB after each meeting, child overhead <= 1.5 s, child
turns == in-process turns.
"""
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="speakeasy-diar-bench-home-")
os.environ["HOME"] = HOME
assert str(Path.home()) == HOME

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from speakeasy import settings  # noqa: E402
from speakeasy.diarization_process import (  # noqa: E402
    ChildDiarizationRunner,
    InProcessDiarizationRunner,
)

assert str(settings.app_support_dir()).startswith(HOME)

VOICES = ["Samantha", "Daniel", "Karen", "Fred"]
LINES = [
    "Let's go through the budget for the next quarter before we decide anything.",
    "I think the telescope schedule is the bigger risk, not the money.",
    "Jupiter's moon Europa probably has a salty ocean under its ice shell.",
    "Can we agree to send the draft to the whole team by Friday afternoon?",
    "The spectrometer results came back and the redshift is about zero point three.",
    "I'd rather test it twice than explain a wrong number to the review board.",
]


def footprint_mb() -> float:
    out = subprocess.run(["footprint", "-p", str(os.getpid())],
                         capture_output=True, text=True).stdout
    number, unit = re.search(r"Footprint: ([\d.]+) ([KMG]?B)", out).groups()
    return float(number) * {"B": 1 / 2**20, "KB": 1 / 1024, "MB": 1, "GB": 1024}[unit]


def make_meeting(folder: Path, minutes: float) -> Path:
    clips = []
    for i, line in enumerate(LINES):
        for voice in VOICES:
            aiff, wav = folder / f"{voice}_{i}.aiff", folder / f"{voice}_{i}.wav"
            if not wav.exists():
                subprocess.run(["say", "-v", voice, "-o", str(aiff), line], check=True)
                subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                                str(aiff), str(wav)], check=True)
            with wave.open(str(wav)) as source:
                clips.append(source.readframes(source.getnframes()))
    target = int(minutes * 60 * 16000) * 2
    gap = b"\0" * (int(0.4 * 16000) * 2)
    out = folder / f"meeting_{minutes}min.wav"
    with wave.open(str(out), "wb") as dest:
        dest.setnchannels(1); dest.setsampwidth(2); dest.setframerate(16000)
        written, k = 0, 0
        while written < target:
            block = (clips[k % len(clips)] + gap)[: target - written]
            dest.writeframes(block)
            written += len(block)
            k += 7  # rotate voices so each speaker keeps coming back
    return out


def run(runner, path):
    started = time.perf_counter()
    turns = runner(path, expected_count=None, max_speakers=None, voice_profile_names=(),
                   progress=lambda f: None, cancel=threading.Event())
    return turns, time.perf_counter() - started


def main() -> None:
    folder = Path(tempfile.mkdtemp(prefix="speakeasy-diar-bench-audio-"))
    try:
        _run_benchmark(folder)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        shutil.rmtree(HOME, ignore_errors=True)


def _run_benchmark(folder: Path) -> None:
    short, long_ = make_meeting(folder, 3.2), make_meeting(folder, 29.3)
    import numpy  # noqa: F401  (baseline includes what the app always has)
    baseline = footprint_mb()
    print(f"parent baseline: {baseline:.0f} MB")
    child = ChildDiarizationRunner()
    child_turns, child_short_times, growth = {}, [], []
    for n, path in enumerate((short, short, long_, short, short), start=1):
        turns, seconds = run(child, path)
        child_turns[path] = turns
        if path == short:
            child_short_times.append(seconds)
        now = footprint_mb()
        growth.append(now - baseline)
        speakers = len({t.speaker for t in turns})
        print(f"meeting {n} ({path.stem}): {seconds:.1f} s wall, {speakers} speakers, "
              f"parent {now:.0f} MB ({now - baseline:+.0f} MB)")
    reference = InProcessDiarizationRunner()
    in_short_times = []
    for _ in range(2):
        in_turns_short, seconds = run(reference, short)
        in_short_times.append(seconds)
    in_turns_long, in_long_seconds = run(reference, long_)
    overhead = statistics.median(child_short_times) - statistics.median(in_short_times)
    print(f"3.2 min: child median {statistics.median(child_short_times):.1f} s, "
          f"in-process median {statistics.median(in_short_times):.1f} s, "
          f"overhead {overhead:.2f} s; 29.3 min in-process {in_long_seconds:.1f} s")
    same_short = child_turns[short] == in_turns_short
    same_long = child_turns[long_] == in_turns_long
    checks = {
        "parent growth <= 5 MB after every meeting": max(growth) <= 5,
        "child overhead <= 1.5 s (3.2 min, medians)": overhead <= 1.5,
        "child turns == in-process turns (3.2 min)": same_short,
        "child turns == in-process turns (29.3 min)": same_long,
    }
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
