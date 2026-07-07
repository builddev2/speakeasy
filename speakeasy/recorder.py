"""Microphone capture: start() on hotkey press, stop() on release.

The CoreAudio input stream is opened once and kept across recordings —
opening a stream costs ~100 ms, enough to clip the first syllable if paid
on every keypress. A stopped stream runs no audio I/O, so the macOS
mic-in-use indicator stays off while idle.
"""

import threading

import numpy as np
import sounddevice as sd

from . import config


class Recorder:
    def __init__(self) -> None:
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        self._recording = False
        self._level = 0.0

    @property
    def level(self) -> float:
        """RMS of the most recent audio chunk (0.0 when not recording)."""
        return self._level

    def prewarm(self) -> None:
        """Open the input stream (stopped) so the first start() is instant."""
        if self._stream is None:
            self._stream = sd.InputStream(
                samplerate=config.SAMPLE_RATE,
                channels=1,
                dtype="float32",
                callback=self._on_audio,
            )

    def start(self) -> None:
        if self._recording:
            return
        with self._lock:
            self._chunks = []
        self._level = 0.0
        self._recording = True
        self.prewarm()
        try:
            self._stream.start()
        except Exception:
            # The device backing the stream may be gone (e.g. headphones
            # unplugged since it was opened); rebuild once on the default.
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
            self.prewarm()
            self._stream.start()

    def _on_audio(self, indata: np.ndarray, frames: int, time, status) -> None:
        if not self._recording:
            return  # straggler callback after stop(); don't leak into next take
        with self._lock:
            self._chunks.append(indata.copy())
        self._level = float(np.sqrt(np.mean(indata**2)))

    def stop(self) -> np.ndarray:
        """Stop capture and return the recording as mono float32 samples."""
        if not self._recording:
            return np.empty(0, dtype=np.float32)
        self._recording = False
        self._level = 0.0
        if self._stream is not None:
            try:
                self._stream.stop()  # kept open for the next recording
            except Exception:
                self._stream.close()
                self._stream = None
        with self._lock:
            if not self._chunks:
                return np.empty(0, dtype=np.float32)
            audio = np.concatenate(self._chunks)[:, 0]
            self._chunks = []
        return audio
