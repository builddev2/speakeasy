"""Long-form meeting capture: mic → bounded queue → writer thread → spool WAV.

Unlike the dictation Recorder (which keeps a take in RAM for seconds), a
meeting runs for hours, so audio spools straight to an int16 WAV in
settings.spool_dir() and RAM stays flat regardless of length. The PortAudio
callback must never block or touch the disk — it only copies bytes into a
bounded queue that a dedicated writer thread drains; if the writer ever
stalls (disk hiccup), frames are dropped rather than letting the queue grow
without bound.

The input stream is opened in shared mode (CoreAudio's default — PortAudio
never requests exclusive access), so a live Zoom/Teams call and a meeting
recording can use the microphone at the same time; neither blocks the other.

Spool files are the app's only unpersisted-audio window: the engine sweeps
spool_dir() clean at every launch, and an atexit hook unlinks the active
spool, so a crash or force-quit mid-meeting never leaves audio behind.
"""

import atexit
import queue
import threading
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd

from . import config, settings

# ~30 s of audio at 16 kHz int16 in ~1 KiB blocks. The exact block size varies
# by host buffer; this is generous enough that only a genuinely stuck writer
# ever drops frames.
_QUEUE_MAX_BLOCKS = 1024

_active_spools: set[Path] = set()
_active_lock = threading.Lock()


def _cleanup_active_spools() -> None:
    with _active_lock:
        for path in _active_spools:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


atexit.register(_cleanup_active_spools)


def sweep_spool_dir() -> None:
    """Delete every file in spool_dir(). Called once per launch: anything
    still there is an orphan from a crash or force-quit and must go —
    meeting audio is never persisted."""
    try:
        for path in settings.spool_dir().iterdir():
            try:
                path.unlink()
                print(f"  Swept orphaned spool file: {path.name}")
            except OSError:
                pass
    except OSError:
        pass


class MeetingRecorder:
    def __init__(self) -> None:
        self._stream: sd.InputStream | None = None
        self._queue: queue.Queue[bytes | None] = queue.Queue(_QUEUE_MAX_BLOCKS)
        self._writer: threading.Thread | None = None
        self._wav: wave.Wave_write | None = None
        self._path: Path | None = None
        self._recording = False
        self._level = 0.0
        self._frames = 0
        self._dropped = 0

    @property
    def level(self) -> float:
        """RMS of the most recent block (0.0 when not recording)."""
        return self._level

    @property
    def elapsed_seconds(self) -> float:
        return self._frames / config.SAMPLE_RATE

    def start(self) -> None:
        if self._recording:
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._path = settings.spool_dir() / f"meeting-{stamp}.wav"
        with _active_lock:
            _active_spools.add(self._path)
        self._wav = wave.open(str(self._path), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(config.SAMPLE_RATE)
        self._queue = queue.Queue(_QUEUE_MAX_BLOCKS)
        self._frames = 0
        self._dropped = 0
        self._level = 0.0
        self._writer = threading.Thread(
            target=self._drain, name="meeting-writer", daemon=True
        )
        self._recording = True
        self._writer.start()
        # Own stream, separate from the dictation Recorder's prewarmed one —
        # int16 (half the disk), and its lifetime is the meeting's, not the
        # app's. Shared-mode input: coexists with Zoom/Teams on the same mic.
        self._stream = sd.InputStream(
            samplerate=config.SAMPLE_RATE,
            channels=1,
            dtype="int16",
            callback=self._on_audio,
        )
        self._stream.start()

    def _on_audio(self, indata: np.ndarray, frames: int, time, status) -> None:
        if not self._recording:
            return
        if self._frames >= config.MEETING_MAX_SECONDS * config.SAMPLE_RATE:
            return  # spool cap: an abandoned recording must not fill the disk
        try:
            self._queue.put_nowait(bytes(indata))
        except queue.Full:
            self._dropped += frames
            return
        self._frames += frames
        block = indata[:, 0].astype(np.float32) / 32768.0
        self._level = float(np.sqrt(np.mean(block**2)))

    def _drain(self) -> None:
        while True:
            block = self._queue.get()
            if block is None:
                return
            try:
                self._wav.writeframes(block)
            except Exception:
                # Disk full/unwritable, or the WAV was closed under us by
                # force_close (wedged-stream path): keep draining so stop()
                # doesn't hang; the shortfall shows up as a shorter WAV.
                pass

    def stop(self) -> Path | None:
        """Stop capture, finish the WAV (header fix-up happens on close),
        and return the spool path. The caller owns the file from here."""
        if not self._recording:
            return None
        self._recording = False
        self._level = 0.0
        if self._stream is not None:
            # abort(), not stop(): same wedged-device rationale as
            # Recorder.stop — there is nothing to drain that we can't afford
            # to lose (at most the final host buffer).
            try:
                self._stream.abort()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._writer is not None:
            self._queue.put(None)  # sentinel: writer exits after the backlog
            self._writer.join(timeout=10.0)
            self._writer = None
        if self._wav is not None:
            try:
                self._wav.close()
            except OSError:
                pass
            self._wav = None
        if self._dropped:
            print(f"  → meeting writer dropped {self._dropped} frames")
        path, self._path = self._path, None
        return path

    def force_close(self) -> None:
        """Abandon a wedged stream (watchdog path — same contract as
        Recorder.force_close): drop the reference without touching the stuck
        call, close the WAV so the header is valid, keep the path so the
        caller can still process or discard what made it to disk."""
        self._recording = False
        self._level = 0.0
        self._stream = None
        if self._writer is not None:
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
            self._writer = None
        if self._wav is not None:
            try:
                self._wav.close()
            except OSError:
                pass
            self._wav = None

    def take_path(self) -> Path | None:
        """The current spool path (after force_close), surrendering ownership."""
        path, self._path = self._path, None
        return path

    def discard(self) -> None:
        """Delete the spool without processing it (cancel/quit paths)."""
        path, self._path = self._path, None
        if path is not None:
            path.unlink(missing_ok=True)
            release_spool(path)


def release_spool(path: Path) -> None:
    """Forget a spool path once it is deleted (pairs with the atexit hook)."""
    with _active_lock:
        _active_spools.discard(path)
