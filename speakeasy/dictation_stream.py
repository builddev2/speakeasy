"""Bounded, content-agnostic handoff for streaming dictation audio."""

from __future__ import annotations

import hashlib
import math
import queue
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

import numpy as np

from . import config

if TYPE_CHECKING:
    from .dictation_benchmark import DictationTiming

class StreamStatus(Enum):
    COMPLETE = "complete"
    BATCH_REQUIRED = "batch_required"
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

    def __init__(
        self,
        *,
        max_chunks: int | None = None,
        block_seconds: float = config.DICTATION_STREAM_BLOCK_SECONDS,
        max_buffer_seconds: float = config.DICTATION_STREAM_BUFFER_SECONDS,
        generation: int = 0,
        clock_ns=time.perf_counter_ns,
    ) -> None:
        if block_seconds <= 0 or max_buffer_seconds <= 0:
            raise ValueError("stream buffer durations must be positive")
        if max_chunks is None:
            max_chunks = math.ceil(max_buffer_seconds / block_seconds)
        if max_chunks < 1:
            raise ValueError("max_chunks must be positive")
        self._chunks: queue.Queue[np.ndarray] = queue.Queue(max_chunks)
        self._finished = threading.Event()
        self._cancelled = threading.Event()
        self._overflowed = threading.Event()
        self._terminal = threading.Event()
        self._terminal_lock = threading.Lock()
        self._audio: np.ndarray | None = None
        self._timing: DictationTiming | None = None
        self._clock_ns = clock_ns
        self._block_seconds = block_seconds
        self.generation = generation
        self._created_ns = clock_ns()
        self._first_chunk_ns: int | None = None
        self._context_started_ns: int | None = None
        self._context_ready_ns: int | None = None
        self._add_audio_ns = 0
        self._provisional_ns = 0
        self._final_flush_ns = 0
        self._queue_high_water = 0
        self.received_frames = 0
        self._received_digest = hashlib.sha256()

    def put_nowait(self, chunk: np.ndarray) -> None:
        try:
            self._chunks.put_nowait(chunk)
            if self._first_chunk_ns is None:
                self._first_chunk_ns = self._clock_ns()
            self._queue_high_water = max(self._queue_high_water, self._chunks.qsize())
        except queue.Full:
            self._overflowed.set()
            raise

    def finish(
        self,
        audio: np.ndarray,
        *,
        valid: bool = True,
        timing: DictationTiming | None = None,
    ) -> bool:
        """Attach the authoritative batch before waking the worker."""
        with self._terminal_lock:
            if self._terminal.is_set():
                return False
            self._audio = audio
            self._timing = timing
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

    @property
    def timing(self) -> DictationTiming | None:
        return self._timing

    def wait(self) -> None:
        self._terminal.wait()

    def get(self, timeout: float) -> np.ndarray:
        chunk = self._chunks.get(timeout=timeout)
        audio = np.asarray(chunk, dtype=np.float32)
        if audio.ndim != 1 and not (audio.ndim == 2 and audio.shape[1] == 1):
            raise ValueError("stream audio must be mono")
        self.received_frames += len(audio)
        self._received_digest.update(audio.tobytes())
        return chunk

    def integrity_matches(self) -> bool:
        audio = self.audio
        return (
            audio is not None
            and audio.ndim == 1
            and self.received_frames == len(audio)
            and self._received_digest.digest()
            == hashlib.sha256(np.asarray(audio, dtype=np.float32).tobytes()).digest()
        )

    def get_nowait(self) -> np.ndarray:
        return self._chunks.get_nowait()

    def context_started(self) -> None:
        self._context_started_ns = self._clock_ns()

    def context_ready(self) -> None:
        self._context_ready_ns = self._clock_ns()

    def add_audio_started(self) -> int:
        return self._clock_ns()

    def add_audio_finished(self, started_ns: int) -> None:
        self._add_audio_ns += self._clock_ns() - started_ns

    def provisional_started(self) -> int:
        return self._clock_ns()

    def provisional_finished(self, started_ns: int) -> None:
        self._provisional_ns += self._clock_ns() - started_ns

    def final_flush_started(self) -> int:
        return self._clock_ns()

    def final_flush_finished(self, started_ns: int) -> None:
        self._final_flush_ns += self._clock_ns() - started_ns

    def metrics(self) -> dict:
        def elapsed(start, finish):
            return (
                None
                if start is None or finish is None
                else round((finish - start) / 1_000_000, 1)
            )

        return {
            "stream_context_ms": elapsed(
                self._context_started_ns, self._context_ready_ns
            ),
            "stream_first_chunk_ms": elapsed(
                self._created_ns, self._first_chunk_ns
            ),
            "stream_add_audio_ms": round(self._add_audio_ns / 1_000_000, 1),
            "stream_provisional_ms": (
                round(self._provisional_ns / 1_000_000, 1)
                if self._provisional_ns
                else None
            ),
            "stream_final_flush_ms": round(self._final_flush_ns / 1_000_000, 1),
            "stream_queue_high_water": self._queue_high_water,
            "stream_queue_capacity": self._chunks.maxsize,
            "stream_overflowed": self.overflowed,
        }

    @property
    def block_seconds(self) -> float:
        return self._block_seconds

    @property
    def queue_capacity(self) -> int:
        return self._chunks.maxsize

    @property
    def queue_capacity_seconds(self) -> float:
        return self._chunks.maxsize * self._block_seconds

    @property
    def empty(self) -> bool:
        return self._chunks.empty()


def run_stream(model, session: StreamingSession, *, to_device,
               cache_depth=None, batch_final_seconds=None) -> StreamResult:
    """Run every model streaming operation on the calling worker thread."""
    if session.cancelled:
        return StreamResult(StreamStatus.CANCELLED)
    if session.overflowed:
        session.wait()
        return StreamResult(
            StreamStatus.CANCELLED if session.cancelled else StreamStatus.OVERFLOW
        )

    sample_rate = model.preprocessor_config.sample_rate
    if cache_depth is None:
        cache_depth = config.DICTATION_STREAM_CACHE_DEPTH
    if batch_final_seconds is None:
        batch_final_seconds = config.DICTATION_BATCH_FINAL_SECONDS
    block_samples = round(session.block_seconds * sample_rate)
    safe_tail = (
        model.preprocessor_config.hop_length
        * model.encoder_config.subsampling_factor
    )
    pending = np.empty(0, dtype=np.float32)
    streamed_samples = 0
    outcome = None
    latest_result = None
    try:
        session.context_started()
        with model.transcribe_stream(
            depth=cache_depth
        ) as stream:
            session.context_ready()
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
                while len(pending) >= block_samples:
                    started = session.add_audio_started()
                    try:
                        stream.add_audio(to_device(pending[:block_samples]))
                    finally:
                        session.add_audio_finished(started)
                    streamed_samples += block_samples
                    pending = pending[block_samples:]
                    if (
                        streamed_samples
                        >= batch_final_seconds * sample_rate
                    ):
                        outcome = StreamResult(StreamStatus.BATCH_REQUIRED)
                        pending = np.empty(0, dtype=np.float32)
                        break
                    started = session.provisional_started()
                    try:
                        latest_result = stream.result
                    finally:
                        session.provisional_finished(started)
                if outcome is not None:
                    break

            audio = session.audio
            if (
                outcome is None
                and audio is not None
                and len(audio)
                >= batch_final_seconds * sample_rate
            ):
                outcome = StreamResult(StreamStatus.BATCH_REQUIRED)
                pending = np.empty(0, dtype=np.float32)
            if outcome is None and len(pending):
                if len(pending) < safe_tail:
                    pending = np.pad(pending, (0, safe_tail - len(pending)))
                started = session.add_audio_started()
                try:
                    stream.add_audio(to_device(pending))
                finally:
                    session.add_audio_finished(started)
                started = session.final_flush_started()
                try:
                    latest_result = stream.result
                finally:
                    session.final_flush_finished(started)
            elif outcome is None and latest_result is None:
                started = session.final_flush_started()
                try:
                    latest_result = stream.result
                finally:
                    session.final_flush_finished(started)
            if outcome is None:
                outcome = StreamResult(
                    StreamStatus.COMPLETE, text=latest_result.text.strip()
                )
    except Exception as error:
        outcome = StreamResult(StreamStatus.FAILED, error=error)

    if outcome is not None and outcome.status is StreamStatus.BATCH_REQUIRED:
        while not session.cancelled:
            try:
                session.get(timeout=0.05)
            except queue.Empty:
                if session.finished and session.empty:
                    break

    if not session.finished and not session.cancelled:
        session.wait()
    if session.cancelled:
        if outcome is not None and outcome.status is StreamStatus.FAILED:
            return outcome
        return StreamResult(StreamStatus.CANCELLED)
    if session.overflowed:
        return StreamResult(StreamStatus.OVERFLOW)
    if outcome.status is StreamStatus.COMPLETE and not session.integrity_matches():
        return StreamResult(StreamStatus.FAILED,
                            error=ValueError("stream audio integrity mismatch"))
    return outcome
