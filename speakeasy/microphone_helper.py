"""Killable microphone-capture process used by immediate dictation.

CoreAudio teardown can wedge inside PortAudio indefinitely. Keeping the stream
in this helper lets the parent terminate the whole process and create a fresh
one; Python cannot safely stop a thread blocked in the HAL.
"""

from __future__ import annotations

import math
import multiprocessing
import queue
import threading
import time as clock
from multiprocessing.connection import Connection

import numpy as np
import sounddevice as sd

from . import config

_COMMAND_TIMEOUT_SECONDS = 1.5
# Spawn/import and opening CoreAudio are cold-start work, not a warm command.
_LAUNCH_TIMEOUT_SECONDS = 4.0
_TERMINATE_TIMEOUT_SECONDS = 0.5
_CAPTURE_QUEUE_MAX = math.ceil(
    config.DICTATION_CAPTURE_BUFFER_SECONDS
    * config.SAMPLE_RATE
    / config.DICTATION_CAPTURE_BLOCK_FRAMES
)
_STREAM_QUEUE_MAX = math.ceil(
    config.DICTATION_STREAM_BUFFER_SECONDS / config.DICTATION_STREAM_BLOCK_SECONDS
)
_STREAM_BLOCK_FRAMES = round(
    config.DICTATION_STREAM_BLOCK_SECONDS * config.SAMPLE_RATE
)


class MicrophoneHelperError(Exception):
    """The helper failed or did not answer a bounded control request."""

    def __init__(self, message="", *, code="helper_exit", stage=None):
        super().__init__(message)
        self.stage = stage if stage in {"process_start", "device_query", "stream_open"} else None
        self.code = code if code in {
            "helper_exit", "launch_failure", "device_unavailable", "overflow", "helper_timeout",
        } else "helper_exit"


class _CaptureBuffer:
    """Drain realtime callback copies away from the callback thread."""

    def __init__(self, level, stream_queue) -> None:
        self._level = level
        self._stream_queue = stream_queue
        self._session_lock = threading.Lock()
        self._session: tuple[queue.Queue, list[np.ndarray], threading.Thread] | None = None
        self.first_buffer_ns = None
        self.capture_dropped_frames = 0
        self.stream_dropped_frames = 0

    def start(self, deliver_chunks: bool = False) -> None:
        self.first_buffer_ns = None
        self.capture_dropped_frames = 0
        self.stream_dropped_frames = 0
        chunks: list[np.ndarray] = []
        audio_queue: queue.Queue[np.ndarray | None] = queue.Queue(_CAPTURE_QUEUE_MAX)

        def collect() -> None:
            stream_parts = []
            stream_frames = 0

            def deliver(*, final: bool = False) -> None:
                nonlocal stream_parts, stream_frames
                if not stream_parts or (
                    stream_frames < _STREAM_BLOCK_FRAMES and not final
                ):
                    return
                combined = np.concatenate(stream_parts, axis=0)
                deliver_frames = len(combined) if final else _STREAM_BLOCK_FRAMES
                block = combined[:deliver_frames]
                remainder = combined[deliver_frames:]
                stream_parts = [remainder] if len(remainder) else []
                stream_frames = len(remainder)
                try:
                    self._stream_queue.put_nowait(block)
                except queue.Full:
                    self.stream_dropped_frames += len(block)

            while True:
                chunk = audio_queue.get()
                if chunk is None:
                    if deliver_chunks:
                        deliver(final=True)
                    return
                chunks.append(chunk)
                self._level.value = float(np.sqrt(np.mean(chunk**2)))
                if deliver_chunks:
                    stream_parts.append(chunk)
                    stream_frames += len(chunk)
                    while stream_frames >= _STREAM_BLOCK_FRAMES:
                        deliver()

        collector = threading.Thread(
            target=collect, name="dictation-audio-collector", daemon=True
        )
        with self._session_lock:
            self._session = (audio_queue, chunks, collector)
        self._level.value = 0.0
        collector.start()

    def callback(self, indata: np.ndarray, frames: int, time, status) -> None:
        if status and status.input_overflow:
            # PortAudio lost input before our queue; the batch is incomplete too.
            self.capture_dropped_frames += frames
        if not self._session_lock.acquire(blocking=False):
            self.capture_dropped_frames += frames
            return
        try:
            session = self._session
            if session is None:
                return
            if self.first_buffer_ns is None:
                observed = clock.perf_counter_ns()
                age = max(0.0, time.currentTime - time.inputBufferAdcTime) if time is not None else frames / config.SAMPLE_RATE
                self.first_buffer_ns = observed - round(age * 1_000_000_000)
            try:
                session[0].put_nowait(indata.copy())
            except queue.Full:
                self.capture_dropped_frames += frames
        finally:
            self._session_lock.release()

    def finish(self) -> tuple[np.ndarray, int, int]:
        with self._session_lock:
            session, self._session = self._session, None
        self._level.value = 0.0
        if session is None:
            return np.empty(0, dtype=np.float32), 0, 0
        audio_queue, chunks, collector = session
        audio_queue.put(None)
        collector.join()
        # A callback that already held the old session may enqueue immediately
        # after the sentinel. Drain those final copies without racing a writer.
        while True:
            try:
                chunk = audio_queue.get_nowait()
            except queue.Empty:
                break
            if chunk is not None:
                chunks.append(chunk)
        audio = (
            np.concatenate(chunks)[:, 0]
            if chunks
            else np.empty(0, dtype=np.float32)
        )
        return audio, self.capture_dropped_frames, self.stream_dropped_frames


def _default_input():
    # Transient numeric identity only, never emitted across IPC or to logs.
    return sd.default.device[0]


def _open_stream(capture: _CaptureBuffer):
    return sd.InputStream(
        samplerate=config.SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=config.DICTATION_CAPTURE_BLOCK_FRAMES,
        callback=capture.callback,
    )


def run_microphone_helper(connection: Connection, level, stream_queue) -> None:
    """Own the PortAudio stream until the parent terminates this process."""
    capture = _CaptureBuffer(level, stream_queue)
    connection.send(("launch_stage", "device_query"))
    try:
        input_device = _default_input()
        if input_device < 0:
            connection.send(("error", "device_unavailable"))
            connection.close()
            return
        connection.send(("launch_stage", "stream_open"))
        stream = _open_stream(capture)
    except Exception:
        connection.send(("error", "open_failed"))
        connection.close()
        return
    connection.send(("ready",))
    deliver_chunks = False
    try:
        while True:
            command = connection.recv()
            if isinstance(command, tuple) and command[0] == "start":
                deliver_chunks = bool(command[1])
                current_input = _default_input()
                if current_input != input_device:
                    if current_input < 0:
                        connection.send(("error", "device_unavailable"))
                        return
                    try:
                        stream.close(ignore_errors=False)
                        stream = _open_stream(capture)
                        input_device = current_input
                    except Exception:
                        connection.send(("error", "open_failed"))
                        return
                capture.start(deliver_chunks)
                try:
                    stream.start()
                except Exception:
                    capture.finish()
                    try:
                        stream.close(ignore_errors=False)
                    except Exception:
                        pass
                    try:
                        stream = _open_stream(capture)
                        capture.start(deliver_chunks)
                        stream.start()
                    except Exception:
                        capture.finish()
                        connection.send(("error", "start_failed"))
                        continue
                connection.send(("started",))
            elif command == "stop":
                audio, capture_dropped, stream_dropped = capture.finish()
                try:
                    stream.abort(ignore_errors=False)
                except Exception:
                    try:
                        stream.close(ignore_errors=False)
                    except Exception:
                        pass
                    stream = None
                if deliver_chunks:
                    stream_queue.put(None)
                connection.send(
                    ("stopped", audio, capture_dropped, stream_dropped, capture.first_buffer_ns)
                )
                if stream is None:
                    try:
                        stream = _open_stream(capture)
                    except Exception:
                        connection.send(("error", "open_failed"))
                        return
            else:
                return
    except (EOFError, BrokenPipeError, OSError):
        return
    finally:
        level.value = 0.0
        connection.close()


class MicrophoneHelper:
    """Small parent-side wrapper around the spawned capture process."""

    def __init__(self) -> None:
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        self._connection = parent
        self._child_connection = child
        self._child_connection_closed = False
        self._level = context.Value("d", 0.0, lock=False)
        self._stream_queue = context.Queue(_STREAM_QUEUE_MAX)
        self._process = context.Process(
            target=run_microphone_helper,
            args=(child, self._level, self._stream_queue),
            name="speakeasy-microphone",
            daemon=True,
        )
        self.first_buffer_ns = None
        self.capture_dropped_frames = 0
        self.stream_dropped_frames = 0
        self._forwarder_dropped_frames = 0
        self._chunk_queue = None
        self._chunk_reader_stop = threading.Event()
        self._chunk_reader = None
        self.stream_delivery_complete = True
        self._terminate_lock = threading.Lock()
        self._terminated = False

    @property
    def level(self) -> float:
        return float(self._level.value)

    def launch(self) -> None:
        stage = "process_start"
        deadline = clock.monotonic() + _LAUNCH_TIMEOUT_SECONDS
        try:
            self._process.start()
            self._child_connection.close()
            self._child_connection_closed = True
            while True:
                remaining = deadline - clock.monotonic()
                if remaining <= 0:
                    raise MicrophoneHelperError(code="helper_timeout")
                response = self._receive(remaining)
                if response in (("launch_stage", "device_query"),
                                ("launch_stage", "stream_open")):
                    stage = response[1]
                    continue
                break
            if response != ("ready",):
                code = "device_unavailable" if response == ("error", "device_unavailable") else "launch_failure"
                raise MicrophoneHelperError("helper failed to prewarm", code=code)
        except MicrophoneHelperError as error:
            error.stage = stage
            self.terminate()
            raise
        except OSError as exc:
            self.terminate()
            raise MicrophoneHelperError("helper failed to launch") from exc

    def start(self, chunk_queue=None) -> None:
        if self._chunk_reader is not None and self._chunk_reader.is_alive():
            raise MicrophoneHelperError("previous chunk delivery did not finish")
        self._send(("start", chunk_queue is not None))
        response = self._receive(_COMMAND_TIMEOUT_SECONDS)
        if response != ("started",):
            code = "device_unavailable" if response == ("error", "device_unavailable") else "helper_exit"
            raise MicrophoneHelperError("helper failed to start", code=code)
        self._chunk_queue = chunk_queue
        self._chunk_reader_stop = threading.Event()
        self._chunk_reader = None
        self.first_buffer_ns = None
        self.capture_dropped_frames = 0
        self.stream_dropped_frames = 0
        self._forwarder_dropped_frames = 0
        self.stream_delivery_complete = chunk_queue is None
        if chunk_queue is not None:
            stop_event = self._chunk_reader_stop
            self._chunk_reader = threading.Thread(
                target=self._forward_chunks,
                args=(stop_event, chunk_queue),
                name="dictation-audio-forwarder",
                daemon=True,
            )
            self._chunk_reader.start()

    def stop(self) -> np.ndarray:
        self._send("stop")
        response = self._receive(None)
        self._finish_chunk_reader()
        if len(response) != 5 or response[0] != "stopped":
            raise MicrophoneHelperError("helper failed to stop")
        self.first_buffer_ns = response[4]
        self.capture_dropped_frames = int(response[2])
        self.stream_dropped_frames = int(response[3]) + self._forwarder_dropped_frames
        if self.capture_dropped_frames:
            raise MicrophoneHelperError("capture queue overflowed", code="overflow")
        return response[1]

    def _forward_chunks(self, stop_event, destination) -> None:
        while not stop_event.is_set():
            try:
                chunk = self._stream_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            except (EOFError, OSError, ValueError):
                return
            if chunk is None:
                self.stream_delivery_complete = True
                return
            try:
                destination.put_nowait(chunk)
            except queue.Full:
                self._forwarder_dropped_frames += len(chunk)

    def _finish_chunk_reader(self) -> None:
        reader = self._chunk_reader
        if reader is not None:
            reader.join(_TERMINATE_TIMEOUT_SECONDS)
            if reader.is_alive():
                self.stream_delivery_complete = False
                self._chunk_reader_stop.set()
                reader.join(0.1)
            if reader.is_alive():
                return
        self._chunk_reader = None

    def _stop_chunk_reader(self) -> None:
        self._chunk_reader_stop.set()
        self._finish_chunk_reader()

    def _receive(self, timeout: float | None):
        try:
            if timeout is not None and not self._connection.poll(timeout):
                raise MicrophoneHelperError("helper timed out", code="helper_timeout")
            return self._connection.recv()
        except (EOFError, BrokenPipeError, OSError) as exc:
            raise MicrophoneHelperError("helper exited") from exc

    def _send(self, command) -> None:
        try:
            self._connection.send(command)
        except (EOFError, BrokenPipeError, OSError) as exc:
            raise MicrophoneHelperError("helper exited") from exc

    def terminate(self) -> None:
        with self._terminate_lock:
            if self._terminated:
                return
            self._terminated = True
            process = self._process
            self._stop_chunk_reader()
            try:
                if process.is_alive():
                    process.terminate()
                    process.join(_TERMINATE_TIMEOUT_SECONDS)
                if process.is_alive():
                    process.kill()
                    process.join(_TERMINATE_TIMEOUT_SECONDS)
            finally:
                self._connection.close()
                if not self._child_connection_closed:
                    try:
                        self._child_connection.close()
                    except OSError:
                        pass
                    self._child_connection_closed = True
                self._stream_queue.cancel_join_thread()
                self._stream_queue.close()
