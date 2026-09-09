"""Microphone helper protocol and cleanup with PortAudio fully mocked."""

import multiprocessing
import queue
import threading

import numpy as np
import pytest

from speakeasy import microphone_helper
from speakeasy.microphone_helper import MicrophoneHelper, MicrophoneHelperError


class Level:
    value = 0.0


class FakeStream:
    def __init__(self, callback):
        self.callback = callback
        self.started = False
        self.aborted = False

    def start(self):
        self.started = True

    def abort(self, ignore_errors=True):
        self.aborted = True

    def close(self, ignore_errors=True):
        pass


def test_helper_protocol_retains_complete_batch_and_streams_optional_chunks(
    monkeypatch,
):
    streams = []

    def open_stream(capture):
        stream = FakeStream(capture.callback)
        streams.append(stream)
        return stream

    monkeypatch.setattr(microphone_helper, "_open_stream", open_stream)
    parent, child = multiprocessing.Pipe()
    streamed = queue.Queue(maxsize=8)
    thread = threading.Thread(
        target=microphone_helper.run_microphone_helper,
        args=(child, Level(), streamed),
    )
    thread.start()
    assert parent.recv() == ("ready",)
    parent.send(("start", True))
    assert parent.recv() == ("started",)
    first = np.ones((40_000, 1), dtype=np.float32)
    second = np.full((45_000, 1), 2.0, dtype=np.float32)
    streams[0].callback(first, 40_000, None, None)
    streams[0].callback(second, 45_000, None, None)

    parent.send("stop")
    response = parent.recv()

    assert response[0] == "stopped"
    assert np.array_equal(response[1][:40_000], np.ones(40_000))
    assert np.array_equal(response[1][40_000:], np.full(45_000, 2.0))
    assert response[2:] == (0, 0)
    assert streamed.get().shape == (32_000, 1)
    assert streamed.get().shape == (32_000, 1)
    assert streamed.get().shape == (21_000, 1)
    assert streamed.get() is None
    assert streams[0].aborted is True
    parent.send("quit")
    thread.join(0.5)
    assert not thread.is_alive()


class FakeConnection:
    def __init__(self, responses=(), *, ready=True, send_error=None):
        self.responses = list(responses)
        self.ready = ready
        self.send_error = send_error
        self.sent = []
        self.closed = 0

    def send(self, value):
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(value)

    def poll(self, timeout):
        return self.ready

    def recv(self):
        return self.responses.pop(0)

    def close(self):
        self.closed += 1


class FakeProcess:
    def __init__(self):
        self.alive = False
        self.started = 0
        self.terminated = 0
        self.killed = 0
        self.joined = 0

    def start(self):
        self.started += 1
        self.alive = True

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.terminated += 1
        self.alive = False

    def kill(self):
        self.killed += 1
        self.alive = False

    def join(self, timeout):
        self.joined += 1


class FakeResourceQueue:
    def __init__(self):
        self.cancelled = 0
        self.closed = 0

    def cancel_join_thread(self):
        self.cancelled += 1

    def close(self):
        self.closed += 1


def _bare_helper(connection):
    helper = MicrophoneHelper.__new__(MicrophoneHelper)
    helper._connection = connection
    helper._child_connection = FakeConnection()
    helper._child_connection_closed = False
    helper._process = FakeProcess()
    helper._stream_queue = FakeResourceQueue()
    helper._chunk_queue = None
    helper._chunk_reader_stop = threading.Event()
    helper._chunk_reader = None
    helper.stream_delivery_complete = True
    helper._terminate_lock = threading.Lock()
    helper._terminated = False
    helper.capture_dropped_frames = 0
    helper.stream_dropped_frames = 0
    helper._forwarder_dropped_frames = 0
    return helper


def test_launch_timeout_terminates_process_and_closes_ipc_resources():
    helper = _bare_helper(FakeConnection(ready=False))

    with pytest.raises(MicrophoneHelperError):
        helper.launch()

    assert helper._process.terminated == 1
    assert helper._connection.closed == 1
    assert helper._child_connection.closed == 1
    assert helper._stream_queue.cancelled == 1
    assert helper._stream_queue.closed == 1
    helper.terminate()  # cleanup is idempotent
    assert helper._process.terminated == 1


def test_stop_reports_stream_overflow_without_losing_batch_audio():
    audio = np.ones(5, dtype=np.float32)
    connection = FakeConnection([("stopped", audio, 0, 7)])
    helper = _bare_helper(connection)

    result = helper.stop()

    assert result is audio
    assert helper.stream_dropped_frames == 7


def test_capture_buffer_coalesces_before_stream_queue_boundary():
    streamed = queue.Queue(maxsize=8)
    capture = microphone_helper._CaptureBuffer(Level(), streamed)
    capture.start(deliver_chunks=True)
    for _ in range(80):
        capture.callback(np.ones((1_000, 1), dtype=np.float32), 1_000, None, None)

    audio, capture_dropped, stream_dropped = capture.finish()

    assert len(audio) == 80_000
    assert streamed.qsize() == 3
    assert streamed.get().shape == (32_000, 1)
    assert streamed.get().shape == (32_000, 1)
    assert streamed.get().shape == (16_000, 1)
    assert capture_dropped == stream_dropped == 0


def test_stop_rejects_callback_overflow_as_authoritative_batch():
    connection = FakeConnection([("stopped", np.ones(5), 4, 0)])
    helper = _bare_helper(connection)

    with pytest.raises(MicrophoneHelperError, match="overflow"):
        helper.stop()


@pytest.mark.parametrize("method", ["start", "stop"])
def test_broken_pipe_is_normalized_as_helper_error(method):
    helper = _bare_helper(FakeConnection(send_error=BrokenPipeError()))

    with pytest.raises(MicrophoneHelperError, match="helper exited"):
        getattr(helper, method)()


def test_busy_capture_lock_counts_drop_instead_of_blocking_callback():
    capture = microphone_helper._CaptureBuffer(Level(), queue.Queue(maxsize=1))
    capture.start()
    capture._session_lock.acquire()
    try:
        capture.callback(np.ones((6, 1), dtype=np.float32), 6, None, None)
    finally:
        capture._session_lock.release()

    _, dropped, _ = capture.finish()
    assert dropped == 6


def test_portaudio_input_overflow_invalidates_capture():
    class Status:
        input_overflow = True

    capture = microphone_helper._CaptureBuffer(Level(), queue.Queue(maxsize=1))
    capture.start()
    capture.callback(np.ones((512, 1), dtype=np.float32), 512, None, Status())
    audio, dropped, _ = capture.finish()
    assert len(audio) == 512
    assert dropped > 0
