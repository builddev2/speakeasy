"""Bounded, content-agnostic handoff for streaming dictation audio."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from enum import Enum

import numpy as np

_DEFAULT_MAX_CHUNKS = 128


class StreamStatus(Enum):
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    OVERFLOW = "overflow"
    FAILED = "failed"


@dataclass(frozen=True)
class StreamResult:
    status: StreamStatus
    text: str | None = None
    error: Exception | None = None
    fallback_reason: str | None = None


class StreamingSession:
    """Thread-safe mailbox; producers only perform bounded, nonblocking puts."""

    def __init__(self, *, max_chunks: int = _DEFAULT_MAX_CHUNKS) -> None:
        if max_chunks < 1:
            raise ValueError("max_chunks must be positive")
        self._chunks: queue.Queue[np.ndarray] = queue.Queue(max_chunks)
        self._finished = threading.Event()
        self._cancelled = threading.Event()
        self._overflowed = threading.Event()
        self._terminal = threading.Event()
        self._terminal_lock = threading.Lock()
        self._audio: np.ndarray | None = None

    def put_nowait(self, chunk: np.ndarray) -> None:
        try:
            self._chunks.put_nowait(chunk)
        except queue.Full:
            self._overflowed.set()
            raise

    def finish(self, audio: np.ndarray, *, valid: bool = True) -> bool:
        """Attach the authoritative batch before waking the worker."""
        with self._terminal_lock:
            if self._terminal.is_set():
                return False
            self._audio = audio
            if not valid:
                self._overflowed.set()
            self._finished.set()
            self._terminal.set()
            return True

    def cancel(self) -> bool:
        with self._terminal_lock:
            if self._terminal.is_set():
                return False
            self._cancelled.set()
            self._terminal.set()
            return True

    @property
    def finished(self) -> bool:
        return self._finished.is_set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def overflowed(self) -> bool:
        return self._overflowed.is_set()

    @property
    def audio(self) -> np.ndarray | None:
        return self._audio

    def wait(self) -> None:
        self._terminal.wait()

    def get(self, timeout: float) -> np.ndarray:
        return self._chunks.get(timeout=timeout)

    def get_nowait(self) -> np.ndarray:
        return self._chunks.get_nowait()

    @property
    def empty(self) -> bool:
        return self._chunks.empty()


def run_stream(model, session: StreamingSession, *, to_device) -> StreamResult:
    """Run every model streaming operation on the calling worker thread."""
    if session.cancelled:
        return StreamResult(StreamStatus.CANCELLED)
    if session.overflowed:
        session.wait()
        return StreamResult(
            StreamStatus.CANCELLED if session.cancelled else StreamStatus.OVERFLOW
        )

    sample_rate = model.preprocessor_config.sample_rate
    safe_tail = (
        model.preprocessor_config.hop_length
        * model.encoder_config.subsampling_factor
    )
    pending = np.empty(0, dtype=np.float32)
    outcome = None
    try:
        with model.transcribe_stream() as stream:
            while True:
                if session.cancelled:
                    return StreamResult(StreamStatus.CANCELLED)
                if session.overflowed:
                    outcome = StreamResult(StreamStatus.OVERFLOW)
                    break
                try:
                    chunk = session.get(timeout=0.05)
                except queue.Empty:
                    if session.finished and session.empty:
                        break
                    continue
                chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
                pending = np.concatenate((pending, chunk))
                while len(pending) >= sample_rate:
                    stream.add_audio(to_device(pending[:sample_rate]))
                    pending = pending[sample_rate:]

            if outcome is None and len(pending):
                if len(pending) < safe_tail:
                    pending = np.pad(pending, (0, safe_tail - len(pending)))
                stream.add_audio(to_device(pending))
            if outcome is None:
                outcome = StreamResult(
                    StreamStatus.COMPLETE, text=stream.result.text.strip()
                )
    except Exception as error:
        outcome = StreamResult(StreamStatus.FAILED, error=error)

    if not session.finished and not session.cancelled:
        session.wait()
    if session.cancelled:
        if outcome is not None and outcome.status is StreamStatus.FAILED:
            return outcome
        return StreamResult(StreamStatus.CANCELLED)
    if session.overflowed:
        return StreamResult(StreamStatus.OVERFLOW)
    return outcome
