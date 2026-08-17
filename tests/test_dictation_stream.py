"""Streaming dictation primitives without a real model or microphone."""

import queue
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from speakeasy.dictation_stream import StreamStatus, StreamingSession, run_stream


class _Config:
    sample_rate = 16_000
    hop_length = 160


class _EncoderConfig:
    subsampling_factor = 8


class _Result:
    text = " streamed text "


class _Stream:
    def __init__(self, calls, *, add_error=None, add_started=None, add_release=None):
        self.calls = calls
        self.add_error = add_error
        self.add_started = add_started
        self.add_release = add_release
        self.added = []

    def __enter__(self):
        self.calls.append(("enter", threading.get_ident()))
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.calls.append(("exit", threading.get_ident()))

    def add_audio(self, audio):
        self.calls.append(("add", threading.get_ident()))
        self.added.append(np.asarray(audio))
        if self.add_started is not None:
            self.add_started.set()
        if self.add_release is not None:
            self.add_release.wait(1.0)
        if self.add_error is not None:
            raise self.add_error

    @property
    def result(self):
        self.calls.append(("result", threading.get_ident()))
        return _Result()


class _Model:
    preprocessor_config = _Config()
    encoder_config = _EncoderConfig()

    def __init__(self, stream):
        self.stream = stream
        self.calls = stream.calls

    def transcribe_stream(self):
        self.calls.append(("create", threading.get_ident()))
        return self.stream


def test_session_is_bounded_and_marks_overflow():
    session = StreamingSession(max_chunks=1)
    session.put_nowait(np.ones((4, 1), dtype=np.float32))

    with pytest.raises(queue.Full):
        session.put_nowait(np.ones((3, 1), dtype=np.float32))

    assert session.overflowed is True
    assert session.get_nowait().shape == (4, 1)


def test_session_cannot_be_unbounded():
    with pytest.raises(ValueError, match="positive"):
        StreamingSession(max_chunks=0)


def test_stream_coalesces_one_second_and_pads_model_safe_tail():
    calls = []
    stream = _Stream(calls)
    model = _Model(stream)
    session = StreamingSession()
    session.put_nowait(np.ones(8_000, dtype=np.float32))
    session.put_nowait(np.full(9_000, 2.0, dtype=np.float32))
    session.finish()

    result = run_stream(model, session, to_device=np.asarray)

    assert result.status is StreamStatus.COMPLETE
    assert result.text == "streamed text"
    assert [len(chunk) for chunk in stream.added] == [16_000, 1_280]
    assert stream.added[1][:1_000].tolist() == [2.0] * 1_000
    assert stream.added[1][1_000:].tolist() == [0.0] * 280


def test_all_model_context_operations_stay_on_calling_worker():
    calls = []
    stream = _Stream(calls)
    model = _Model(stream)
    session = StreamingSession()
    session.put_nowait(np.ones(16_000, dtype=np.float32))
    session.finish()

    with ThreadPoolExecutor(max_workers=1) as worker:
        worker_thread, result = worker.submit(
            lambda: (
                threading.get_ident(),
                run_stream(model, session, to_device=np.asarray),
            )
        ).result()

    assert result.status is StreamStatus.COMPLETE
    assert [name for name, _ in calls] == ["create", "enter", "add", "result", "exit"]
    assert {thread_id for _, thread_id in calls} == {worker_thread}


def test_cancel_before_start_does_not_touch_model():
    calls = []
    model = _Model(_Stream(calls))
    session = StreamingSession()
    session.cancel()

    result = run_stream(model, session, to_device=np.asarray)

    assert result.status is StreamStatus.CANCELLED
    assert result.text is None
    assert calls == []


def test_overflow_during_inference_exits_context():
    calls = []
    add_started = threading.Event()
    add_release = threading.Event()
    stream = _Stream(calls, add_started=add_started, add_release=add_release)
    model = _Model(stream)
    session = StreamingSession(max_chunks=1)
    session.put_nowait(np.ones(16_000, dtype=np.float32))

    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(run_stream, model, session, to_device=np.asarray)
        assert add_started.wait(0.5)
        session.put_nowait(np.ones(4, dtype=np.float32))
        with pytest.raises(queue.Full):
            session.put_nowait(np.ones(4, dtype=np.float32))
        add_release.set()
        result = future.result()

    assert result.status is StreamStatus.OVERFLOW
    assert [name for name, _ in calls][-1] == "exit"


def test_model_error_is_a_failed_outcome_and_exits_context():
    calls = []
    error = RuntimeError("stream failed")
    stream = _Stream(calls, add_error=error)
    model = _Model(stream)
    session = StreamingSession()
    session.put_nowait(np.ones(16_000, dtype=np.float32))
    session.finish()

    result = run_stream(model, session, to_device=np.asarray)

    assert result.status is StreamStatus.FAILED
    assert result.error is error
    assert [name for name, _ in calls][-1] == "exit"
