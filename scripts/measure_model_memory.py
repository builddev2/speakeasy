"""Measure the speech model's memory with and without the MLX cache cap.

Synthetic audio only (macOS `say`), bundled model only; never reads user data.
Usage: .venv/bin/python -B scripts/measure_model_memory.py [--no-cap]
"""
import os
import re
import subprocess
import sys
import tempfile
import time
import wave

import mlx.core as mx
from parakeet_mlx import from_pretrained

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from speakeasy import config  # noqa: E402

MODEL = "/Applications/Speakeasy.app/Contents/Resources/model"
SENTENCES = [
    "The quick brown fox jumps over the lazy dog while the committee reviews the quarterly budget.",
    "Jupiter is the largest planet in our solar system, and its great red spot is a storm bigger than Earth.",
    "Neutron stars are so dense that a teaspoon of their material would weigh about a billion tonnes on Earth.",
    "Remember to buy milk, eggs, bread and some fresh strawberries on the way home tonight.",
]


def footprint_mb() -> str:
    out = subprocess.run(["footprint", "-p", str(os.getpid())],
                         capture_output=True, text=True).stdout
    return re.search(r"Footprint: ([\d.]+ [KMG]B)", out).group(1)


def report(stage: str) -> None:
    mb = lambda b: round(b / 2**20)
    print(f"{stage:18s} weights+live={mb(mx.get_active_memory())} MB "
          f"cache={mb(mx.get_cache_memory())} MB footprint={footprint_mb()}")


def make_audio(folder: str) -> tuple[list[str], str]:
    clips = []
    for i, text in enumerate(SENTENCES):
        aiff, wav = f"{folder}/s{i}.aiff", f"{folder}/s{i}.wav"
        subprocess.run(["say", "-o", aiff, text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                        aiff, wav], check=True)
        clips.append(wav)
    long = f"{folder}/long.wav"
    frames = b"".join(wave.open(c).readframes(10**8) + b"\0" * 32000
                      for c in clips * 8)
    with wave.open(long, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(frames)
    return clips, long


def main() -> None:
    if "--no-cap" not in sys.argv:
        mx.set_cache_limit(config.MLX_CACHE_LIMIT_BYTES)
    print("cache cap:", "off" if "--no-cap" in sys.argv else
          f"{config.MLX_CACHE_LIMIT_BYTES // 2**20} MiB")
    with tempfile.TemporaryDirectory() as folder:
        clips, long = make_audio(folder)
        model = from_pretrained(MODEL)
        report("after load")
        for clip in clips:
            model.transcribe(clip)
        report("after 4 clips")
        started = time.perf_counter()
        model.transcribe(long, chunk_duration=config.MEETING_CHUNK_SECONDS,
                         overlap_duration=config.MEETING_OVERLAP_SECONDS)
        report(f"after long ({time.perf_counter() - started:.1f}s)")


if __name__ == "__main__":
    main()
