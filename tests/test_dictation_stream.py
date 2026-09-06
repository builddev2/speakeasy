"""Streaming dictation primitives without a real model or microphone."""

import queue
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from speakeasy import config
from speakeasy.dictation_stream import StreamStatus, StreamingSession, run_stream


class _Config:
    sample_rate = 16_000
    hop_length = 160


class _EncoderConfig:
    subsampling_factor = 8


class _Result:
    text = " streamed text "


class _Stream:
    def __init__(
        self,
        calls,
        *,
        enter_error=None,
        add_error=None,
        result_error=None,
        exit_error=None,
        add_started=None,
        add_release=None,
    ):
        self.calls = calls
        self.enter_error = enter_error
        self.add_error = add_error
        self.result_error = result_error
        self.exit_error = exit_error
        self.add_started = add_started
        self.add_release = add_release
        self.added = []

    def __enter__(self):
        self.calls.append(("enter", threading.get_ident()))
        if self.enter_error is not None:
            raise self.enter_error
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.calls.append(("exit", threading.get_ident()))
        if self.exit_error is not None:
            raise self.exit_error

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
        if self.result_error is not None:
            raise self.result_error
        return _Result()


class _Model:
    preprocessor_config = _Config()
    encoder_config = _EncoderConfig()

    def __init__(self, stream, *, create_error=None):
        self.stream = stream
        self.calls = stream.calls
        self.create_error = create_error

    def transcribe_stream(self, **kwargs):
        self.calls.append(("create", threading.get_ident()))
        self.stream_options = kwargs
        if self.create_error is not None:
            raise self.create_error
        return self.stream


def test_session_is_bounded_and_marks_overflow():
    session = StreamingSession(max_chunks=1, block_seconds=1.0)
    session.put_nowait(np.ones((4, 1), dtype=np.float32))

    with pytest.raises(queue.Full):
        session.put_nowait(np.ones((3, 1), dtype=np.float32))

    assert session.overflowed is True
    assert session.get_nowait().shape == (4, 1)


def test_session_cannot_be_unbounded():
    with pytest.raises(ValueError, match="positive"):
        StreamingSession(max_chunks=0)


def test_default_capacity_is_derived_from_audio_seconds():
    session = StreamingSession()

    assert session.queue_capacity_seconds == pytest.approx(
        session.block_seconds * session.queue_capacity
    )


def test_stream_coalesces_one_second_and_pads_model_safe_tail():
    calls = []
    stream = _Stream(calls)
    model = _Model(stream)
    session = StreamingSession(block_seconds=1.0)
    session.put_nowait(np.ones(8_000, dtype=np.float32))
    session.put_nowait(np.full(9_000, 2.0, dtype=np.float32))
    audio = np.ones(17_000, dtype=np.float32)
    session.finish(audio)

    result = run_stream(model, session, to_device=np.asarray)

    assert result.status is StreamStatus.COMPLETE
    assert result.text == "streamed text"
    assert session.audio is audio
    assert [len(chunk) for chunk in stream.added] == [16_000, 1_280]
    assert stream.added[1][:1_000].tolist() == [2.0] * 1_000
    assert stream.added[1][1_000:].tolist() == [0.0] * 280


def test_stream_metrics_are_content_free_and_cover_lifecycle():
    clock = iter(range(0, 100_000_000, 1_000_000))
    session = StreamingSession(max_chunks=2, clock_ns=lambda: next(clock))
    session.put_nowait(np.ones(8_000, dtype=np.float32))
    session.put_nowait(np.ones(8_000, dtype=np.float32))
    session.finish(np.ones(16_000, dtype=np.float32))

    result = run_stream(_Model(_Stream([])), session, to_device=np.asarray)
    metrics = session.metrics()

    assert result.status is StreamStatus.COMPLETE
    assert metrics["stream_context_ms"] is not None
    assert metrics["stream_first_chunk_ms"] is not None
    assert metrics["stream_add_audio_ms"] is not None
    assert metrics["stream_final_flush_ms"] is not None
    assert metrics["stream_queue_high_water"] == 2
    assert metrics["stream_queue_capacity"] == 2
    assert metrics["stream_overflowed"] is False
    assert set(metrics) == {
        "stream_context_ms",
        "stream_first_chunk_ms",
        "stream_add_audio_ms",
        "stream_provisional_ms",
        "stream_final_flush_ms",
        "stream_queue_high_water",
        "stream_queue_capacity",
        "stream_overflowed",
    }


def test_all_model_context_operations_stay_on_calling_worker():
    calls = []
    stream = _Stream(calls)
    model = _Model(stream)
    session = StreamingSession()
    session.put_nowait(np.ones(16_000, dtype=np.float32))
    session.finish(np.ones(16_000, dtype=np.float32))

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
    assert model.stream_options == {"depth": config.DICTATION_STREAM_CACHE_DEPTH}


def test_provisional_result_is_pulled_after_each_complete_block():
    calls = []
    stream = _Stream(calls)
    session = StreamingSession(block_seconds=1.0)
    session.put_nowait(np.ones(32_000, dtype=np.float32))
    session.finish(np.ones(32_000, dtype=np.float32))

    result = run_stream(_Model(stream), session, to_device=np.asarray)

    assert result.text == "streamed text"
    assert [name for name, _ in calls] == [
        "create",
        "enter",
        "add",
        "result",
        "add",
        "result",
        "exit",
    ]
    assert session.metrics()["stream_provisional_ms"] is not None


def test_long_take_stops_model_work_and_drains_remaining_audio(monkeypatch):
    monkeypatch.setattr(config, "DICTATION_BATCH_FINAL_SECONDS", 2.0)
    calls = []
    stream = _Stream(calls)
    session = StreamingSession(block_seconds=1.0, max_chunks=8)
    for _ in range(5):
        session.put_nowait(np.ones(16_000, dtype=np.float32))
    session.finish(np.ones(80_000, dtype=np.float32))

    result = run_stream(_Model(stream), session, to_device=np.asarray)

    assert result.status is StreamStatus.BATCH_REQUIRED
    assert session.empty is True
    assert [len(chunk) for chunk in stream.added] == [16_000, 16_000]
    assert [name for name, _ in calls] == [
        "create",
        "enter",
        "add",
        "result",
        "add",
        "exit",
    ]


def test_threshold_uses_complete_audio_duration_for_partial_final_block(
    monkeypatch,
):
    monkeypatch.setattr(config, "DICTATION_BATCH_FINAL_SECONDS", 1.5)
    calls = []
    stream = _Stream(calls)
    session = StreamingSession(block_seconds=1.0)
    session.put_nowait(np.ones(24_000, dtype=np.float32))
    session.finish(np.ones(24_000, dtype=np.float32))

    result = run_stream(_Model(stream), session, to_device=np.asarray)

    assert result.status is StreamStatus.BATCH_REQUIRED
    assert [len(chunk) for chunk in stream.added] == [16_000]


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
    session = StreamingSession(max_chunks=1, block_seconds=1.0)
    session.put_nowait(np.ones(16_000, dtype=np.float32))

    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(run_stream, model, session, to_device=np.asarray)
        assert add_started.wait(0.5)
        session.put_nowait(np.ones(4, dtype=np.float32))
        with pytest.raises(queue.Full):
            session.put_nowait(np.ones(4, dtype=np.float32))
        add_release.set()
        session.finish(np.ones(16_008, dtype=np.float32), valid=False)
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
    session.finish(np.ones(16_000, dtype=np.float32))

    result = run_stream(model, session, to_device=np.asarray)

    assert result.status is StreamStatus.FAILED
    assert result.error is error
    assert [name for name, _ in calls][-1] == "exit"


@pytest.mark.parametrize("phase", ["create", "enter", "result", "exit"])
def test_stream_lifecycle_failure_is_a_failed_outcome(phase):
    calls = []
    error = RuntimeError(f"{phase} failed")
    kwargs = {f"{phase}_error": error} if phase != "create" else {}
    stream = _Stream(calls, **kwargs)
    model = _Model(stream, create_error=error if phase == "create" else None)
    session = StreamingSession()
    session.finish(np.ones(16_000, dtype=np.float32))

    result = run_stream(model, session, to_device=np.asarray)

    assert result.status is StreamStatus.FAILED
    assert result.error is error
    if phase in ("result", "exit"):
        assert any(name == "exit" for name, _ in calls)


def test_model_error_waits_for_terminal_and_preserves_failure_for_circuit_breaker():
    calls = []
    stream = _Stream(calls, add_error=RuntimeError("stream failed"))
    session = StreamingSession(block_seconds=1.0)
    session.put_nowait(np.ones(16_000, dtype=np.float32))

    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(run_stream, _Model(stream), session, to_device=np.asarray)
        for _ in range(100):
            if calls and calls[-1][0] == "exit":
                break
            threading.Event().wait(0.005)
        assert calls and calls[-1][0] == "exit"
        assert future.done() is False
        session.cancel()
        result = future.result()

    assert result.status is StreamStatus.FAILED


def test_finish_attaches_audio_before_waking_waiter_and_is_terminal():
    session = StreamingSession()
    audio = np.ones(12, dtype=np.float32)
    observed = []
    waiter = threading.Thread(
        target=lambda: (session.wait(), observed.append(session.audio))
    )
    waiter.start()

    assert session.finish(audio) is True
    waiter.join(0.5)

    assert len(observed) == 1 and observed[0] is audio
    assert session.cancel() is False
    assert session.cancelled is False


def test_invalid_finish_marks_stream_overflow_after_attaching_audio():
    session = StreamingSession()
    audio = np.ones(12, dtype=np.float32)

    assert session.finish(audio, valid=False) is True

    assert session.audio is audio
    assert session.finished is True
    assert session.overflowed is True
