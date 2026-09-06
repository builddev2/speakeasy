"""Bounded handoff for transcribing completed meeting chunks during capture."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from enum import Enum

from . import config


class MeetingASRStatus(Enum):
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    OVERFLOW = "overflow"
    FAILED = "failed"


@dataclass(frozen=True)
class MeetingASRResult:
    status: MeetingASRStatus
    transcript: object | None = None
    error: Exception | None = None


class MeetingASRSession:
    """Build overlapping PCM chunks on the writer and hand them to worker."""

    def __init__(self, *, max_chunks: int = 2) -> None:
        if max_chunks < 1:
            raise ValueError("max_chunks must be positive")
        self._chunks: queue.Queue[tuple[int, bytes]] = queue.Queue(max_chunks)
        self._finished = threading.Event()
        self._cancelled = threading.Event()
        self._overflowed = threading.Event()
        self._buffer = bytearray()
        self._start_frame = 0
        self._chunk_frames = round(
            config.MEETING_CHUNK_SECONDS * config.SAMPLE_RATE
        )
        overlap_frames = round(
            config.MEETING_OVERLAP_SECONDS * config.SAMPLE_RATE
        )
        self._step_frames = self._chunk_frames - overlap_frames
        if self._step_frames <= 0:
            raise ValueError("meeting overlap must be shorter than its chunk")

    def add_pcm(self, block: bytes) -> None:
        """Called only by meeting-writer; never by the audio callback."""
        if self._cancelled.is_set() or self._overflowed.is_set():
            return
        self._buffer.extend(block)
        chunk_bytes = self._chunk_frames * 2
        step_bytes = self._step_frames * 2
        while len(self._buffer) >= chunk_bytes:
            if not self._put(self._start_frame, bytes(self._buffer[:chunk_bytes])):
                self._buffer.clear()
                return
            del self._buffer[:step_bytes]
            self._start_frame += self._step_frames

    def finish(self) -> None:
        if not self._cancelled.is_set() and not self._overflowed.is_set():
            if self._buffer:
                self._put(self._start_frame, bytes(self._buffer))
        self._buffer.clear()
        self._finished.set()

    def cancel(self) -> None:
        self._buffer.clear()
        self._cancelled.set()

    def get(self, timeout: float) -> tuple[int, bytes]:
        return self._chunks.get(timeout=timeout)

    def _put(self, start_frame: int, pcm: bytes) -> bool:
        try:
            self._chunks.put_nowait((start_frame, pcm))
        except queue.Full:
            self._overflowed.set()
            return False
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
    def empty(self) -> bool:
        return self._chunks.empty()
