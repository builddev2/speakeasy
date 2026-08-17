"""Engine wiring for one live streaming job per eligible dictation."""

from concurrent.futures import Future

import numpy as np
import pytest

from speakeasy.dictation_benchmark import DictationTiming
from speakeasy.dictation_stream import StreamResult, StreamStatus, StreamingSession
from speakeasy.engine import DictationEngine, State, _RecorderStopResult


class Recorder:
    level = 0.0

    def __init__(self):
        self.chunk_queue = None
        self.stream_dropped_frames = 0
        self.stream_delivery_complete = True
        self.force_closed = False

    def start(self, *, chunk_queue=None):
        self.chunk_queue = chunk_queue

    def stop(self):
        return np.ones(16_000, dtype=np.float32)

    def force_close(self):
        self.force_closed = True

    @staticmethod
    def duration_seconds(audio):
        return len(audio) / 16_000


class Transcriber:
    def __init__(self):
        self.calls = 0

    def transcribe_stream(self, session):
        self.calls += 1
        return StreamResult(StreamStatus.COMPLETE, text="private final")


class Worker:
    def __init__(self):
        self.submissions = []
        self.future = Future()
        self.shutdown_called = False

    def submit(self, function, *args):
        self.submissions.append((function, args))
        return self.future

    def shutdown(self, wait=False):
        self.shutdown_called = True


class Control:
    def __init__(self):
        self.submissions = []
        self.shutdown_called = False

    def submit(self, function, *args):
        self.submissions.append((function, args))

    def shutdown(self, wait=False):
        self.shutdown_called = True


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr("speakeasy.engine.play_sound", lambda sound: None)
    monkeypatch.setattr(DictationTiming, "emit", lambda self, status: None)
    instance = DictationEngine()
    instance.worker.shutdown(wait=False)
    instance.control.shutdown(wait=False)
    instance.worker = Worker()
    instance.control = Control()
    instance.recorder = Recorder()
    instance.transcriber = Transcriber()
    instance.state = State.READY
    yield instance
    if not instance.worker.future.done():
        instance._cancel_dictation_stream()
        instance.worker.future.set_result(StreamResult(StreamStatus.CANCELLED))


def test_ready_take_passes_one_session_to_recorder_and_one_worker_job(engine):
    engine._start_recording()

    assert isinstance(engine.recorder.chunk_queue, StreamingSession)
    assert len(engine.worker.submissions) == 1
    function, args = engine.worker.submissions[0]
    assert function == engine.transcriber.transcribe_stream
    assert args == (engine.recorder.chunk_queue,)
    assert engine.transcriber.calls == 0  # submitted, never run on control
    assert engine.state is State.RECORDING


@pytest.mark.parametrize(
    ("dropped", "complete", "invalid"),
    [(4, True, True), (0, False, True), (0, True, False)],
)
def test_success_attaches_batch_then_finishes_and_invalidates_dropped_delivery(
    engine, monkeypatch, dropped, complete, invalid
):
    engine._start_recording()
    session = engine.recorder.chunk_queue
    audio = np.ones(20_000, dtype=np.float32)
    engine.recorder.stream_dropped_frames = dropped
    engine.recorder.stream_delivery_complete = complete
    monkeypatch.setattr(
        engine,
        "_stop_recorder_guarded",
        lambda timing=None: _RecorderStopResult(audio, "normal"),
    )

    engine._stop_recording()

    assert session.audio is audio
    assert session.finished is True
    assert session.overflowed is invalid
    assert session.cancelled is False
    assert len(engine.worker.submissions) == 1
    assert engine.state is State.TRANSCRIBING
    assert engine.last_dictation_heard is None
    assert engine.last_dictation_text is None

    status = StreamStatus.OVERFLOW if invalid else StreamStatus.COMPLETE
    engine.worker.future.set_result(StreamResult(status))
    assert engine.state is State.READY


@pytest.mark.parametrize(
    "stopped",
    [
        _RecorderStopResult(np.empty(0, dtype=np.float32), "timeout"),
        _RecorderStopResult(np.ones(10, dtype=np.float32), "normal"),
    ],
)
def test_timeout_and_too_short_cancel_without_finishing(
    engine, monkeypatch, stopped
):
    engine._start_recording()
    session = engine.recorder.chunk_queue
    monkeypatch.setattr(
        engine, "_stop_recorder_guarded", lambda timing=None: stopped
    )

    engine._stop_recording()

    assert session.cancelled is True
    assert session.finished is False
    assert session.audio is None
    assert engine.state is State.READY


@pytest.mark.parametrize("state", [State.LOADING, State.MEETING_RECORDING])
def test_ineligible_take_uses_no_stream_session_or_press_side_worker_job(
    engine, state
):
    engine.state = state
    if state is State.LOADING:
        engine.transcriber = None

    engine._start_recording()

    assert engine.recorder.chunk_queue is None
    assert engine.worker.submissions == []


def test_pause_and_shutdown_cancel_active_session_without_using_model(engine):
    engine._start_recording()
    paused = engine.recorder.chunk_queue

    engine.pause()

    assert paused.cancelled is True
    assert engine.transcriber.calls == 0
    assert engine.state is State.PAUSED

    engine.state = State.READY
    engine._start_recording()
    shutdown = engine.recorder.chunk_queue
    engine.shutdown()

    assert shutdown.cancelled is True
    assert engine.recorder.force_closed is True
    assert engine.transcriber.calls == 0
