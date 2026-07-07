"""Microphone capture: start() on hotkey press, stop() on release."""

import threading

import numpy as np
import sounddevice as sd

from . import config


class Recorder:
    def __init__(self) -> None:
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None

    def start(self) -> None:
        if self._stream is not None:
            return
        with self._lock:
            self._chunks = []
        self._stream = sd.InputStream(
            samplerate=config.SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=self._on_audio,
        )
        self._stream.start()

    def _on_audio(self, indata: np.ndarray, frames: int, time, status) -> None:
        with self._lock:
            self._chunks.append(indata.copy())

    def stop(self) -> np.ndarray:
        """Stop capture and return the recording as mono float32 samples."""
        if self._stream is None:
            return np.empty(0, dtype=np.float32)
        self._stream.stop()
        self._stream.close()
        self._stream = None
        with self._lock:
            if not self._chunks:
                return np.empty(0, dtype=np.float32)
            audio = np.concatenate(self._chunks)[:, 0]
            self._chunks = []
        return audio
