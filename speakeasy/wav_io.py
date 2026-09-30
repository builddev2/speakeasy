"""PCM16 WAV → 16 kHz mono float32, with no model imports.

Lives apart from transcriber.py (which imports MLX at module level) so the
diarization child process can read a meeting spool without loading the
speech-model stack.
"""

import wave
from pathlib import Path

import numpy as np

from . import config


def read_wav_mono_f32(path: Path) -> np.ndarray:
    """Read a PCM16 WAV and convert it to the model's 16 kHz mono format."""
    with wave.open(str(path)) as w:
        sample_rate = w.getframerate()
        channels = w.getnchannels()
        if w.getsampwidth() != 2 or sample_rate <= 0 or channels <= 0:
            raise ValueError(
                f"Expected PCM16 meeting spool, got {w.getsampwidth() * 8}-bit / "
                f"{sample_rate} Hz / {channels} ch: {path}"
            )
        if sample_rate == config.SAMPLE_RATE and channels == 1:
            audio = np.empty(w.getnframes(), dtype=np.float32)
            written = 0
            while written < len(audio):
                raw = w.readframes(
                    min(config.SAMPLE_RATE * 60, len(audio) - written)
                )
                samples = np.frombuffer(raw, dtype=np.int16)
                if not len(samples):
                    break
                audio[written : written + len(samples)] = samples
                written += len(samples)
            audio = audio[:written]
            audio /= 32768.0
            return audio
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    complete = len(data) - (len(data) % channels)
    if complete == 0:
        return np.empty(0, dtype=np.float32)
    audio = data[:complete].reshape(-1, channels).astype(np.float32).mean(axis=1)
    audio /= 32768.0
    if sample_rate == config.SAMPLE_RATE:
        return audio
    output_frames = max(1, round(len(audio) * config.SAMPLE_RATE / sample_rate))
    source_positions = np.arange(len(audio), dtype=np.float64)
    target_positions = np.arange(output_frames, dtype=np.float64) * (
        sample_rate / config.SAMPLE_RATE
    )
    return np.interp(target_positions, source_positions, audio).astype(np.float32)
