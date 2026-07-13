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


class RecorderBusy(Exception):
    """start() refused because a previous stop is still unwinding in CoreAudio.

    Opening a new input stream while a prior stop/abort is still executing
    inside the CoreAudio HAL deadlocks both calls on the HAL mutex (an
    abandoned, watchdog-timed-out stop is exactly this case). Rather than open
    concurrently and freeze the whole hotkey pipeline, start() raises this and
    the engine drops the take, keeping dictation responsive.
    """


class Recorder:
    def __init__(self) -> None:
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        self._recording = False
        self._level = 0.0
        # Set for the duration of a CoreAudio stream teardown (abort/close).
        # It gates start()/prewarm() so a new stream is never opened while a
        # previous stop — including one the watchdog abandoned — is still
        # running inside the HAL and holding its mutex.
        self._stopping = threading.Event()

    @property
    def level(self) -> float:
        """RMS of the most recent audio chunk (0.0 when not recording)."""
        return self._level

    @staticmethod
    def duration_seconds(audio: np.ndarray) -> float:
        """Length of a captured mono buffer in seconds."""
        return len(audio) / config.SAMPLE_RATE

    def prewarm(self) -> None:
        """Open the input stream (stopped) so the first start() is instant."""
        if self._stopping.is_set():
            # A prior stop is still unwinding inside CoreAudio; opening now
            # would deadlock on the HAL mutex (see RecorderBusy / start()).
            return
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
        if self._stopping.is_set():
            # Don't open a second stream on top of a stop that's still running
            # in the HAL — that is the deadlock this guard exists to prevent.
            # Drop the take instead; the mic recovers when the stop clears (or
            # on relaunch if it never does), but the pipeline never freezes.
            raise RecorderBusy()
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
        # Harvest the audio before touching the stream: _on_audio bails once
        # _recording is False, so no more chunks are coming, and doing it first
        # means a stop() the watchdog later abandons (see force_close) can never
        # reach in and clear the *next* recording's chunks after it unwedges.
        with self._lock:
            chunks, self._chunks = self._chunks, []
        audio = (
            np.concatenate(chunks)[:, 0]
            if chunks
            else np.empty(0, dtype=np.float32)
        )
        if self._stream is not None:
            # abort() (Pa_AbortStream) stops the stream at once; stop()
            # (Pa_StopStream) waits for the audio callback to drain, which can
            # block indefinitely when the device changed under an open stream
            # (Bluetooth (dis)connect, input switch, sample-rate renegotiation).
            # A hung stop() there freezes the caller's thread and, with it, the
            # whole hotkey pipeline, leaving the mic held open. There is nothing
            # to drain — the audio is already harvested above.
            #
            # Mark the teardown in flight across the CoreAudio call: if abort()
            # itself wedges on the HAL mutex, _stopping stays set (the finally
            # never runs) and a concurrent start() refuses instead of opening a
            # second stream that would deadlock against this one.
            self._stopping.set()
            try:
                self._stream.abort()  # kept open for the next recording
            except Exception:
                # The device is wedged or gone: drop the stream so the OS
                # releases the mic and the next start() rebuilds on the default.
                try:
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            finally:
                self._stopping.clear()
        return audio

    def force_close(self) -> None:
        """Abandon the current stream so the OS releases the mic.

        Called from the engine's watchdog when stop() has not returned in time
        (a wedged CoreAudio call): the reference is dropped without touching the
        stuck stream — closing it from here could race the in-flight call inside
        PortAudio — and the next start() rebuilds a fresh stream on the default
        device.
        """
        self._recording = False
        self._level = 0.0
        self._stream = None
