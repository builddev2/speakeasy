"""Batch recovery for invalid or failed live dictation streams."""

import threading
from concurrent.futures import Future, ThreadPoolExecutor

import numpy as np
import pytest

from speakeasy.dictation_benchmark import DictationTiming
from speakeasy.dictation_stream import StreamResult, StreamStatus, StreamingSession
from speakeasy.engine import DictationEngine, MeetingOptions, State, _RecorderStopResult


class _FallbackTranscriber:
    def __init__(self, stream_result, *, batch_error=None):
        self.stream_result = stream_result
        self.batch_error = batch_error
        self.events = []
        self.batch_audio = []
        self.thread_ids = []

    def transcribe_stream(self, session):
        self.events.append("stream_exit")
        self.thread_ids.append(threading.get_ident())
        return self.stream_result

    def transcribe(self, audio, timing=None):
        self.events.append("batch")
        self.thread_ids.append(threading.get_ident())
        self.batch_audio.append(audio)
        if self.batch_error is not None:
            raise self.batch_error
        return "batch final"


@pytest.mark.parametrize(
    ("stream_result", "reason", "disabled"),
    [
        (StreamResult(StreamStatus.OVERFLOW), "stream_overflow", False),
        (
            StreamResult(StreamStatus.FAILED, error=RuntimeError("stream")),
            "stream_error",
            True,
        ),
    ],
)
def test_fallback_uses_complete_audio_after_stream_exit_on_same_worker(
    stream_result, reason, disabled
):
    engine = DictationEngine()
    engine.worker.shutdown(wait=False)
    engine.control.shutdown(wait=False)
    transcriber = _FallbackTranscriber(stream_result)
    engine.transcriber = transcriber
    session = StreamingSession()
    audio = np.arange(20_000, dtype=np.float32)
    session.finish(audio, valid=stream_result.status is not StreamStatus.OVERFLOW)

    with ThreadPoolExecutor(max_workers=1) as worker:
        worker_thread, result = worker.submit(
            lambda: (
                threading.get_ident(),
                engine._transcribe_stream_with_fallback(session),
            )
        ).result()

    assert result == StreamResult(
        StreamStatus.COMPLETE,
        text="batch final",
        fallback_reason=reason,
    )
    assert transcriber.events == ["stream_exit", "batch"]
    assert transcriber.batch_audio == [audio]
    assert transcriber.batch_audio[0] is audio
    assert set(transcriber.thread_ids) == {worker_thread}
    assert engine._dictation_stream_disabled is disabled


def test_unexpected_stream_exception_uses_batch_and_trips_circuit_breaker():
    class RaisingTranscriber(_FallbackTranscriber):
        def transcribe_stream(self, session):
            self.events.append("stream_exit")
            raise RuntimeError("create failed")

    engine = DictationEngine()
    engine.worker.shutdown(wait=False)
    engine.control.shutdown(wait=False)
    transcriber = RaisingTranscriber(StreamResult(StreamStatus.COMPLETE))
    engine.transcriber = transcriber
    session = StreamingSession()
    session.finish(np.ones(20_000, dtype=np.float32))

    result = engine._transcribe_stream_with_fallback(session)

    assert result.status is StreamStatus.COMPLETE
    assert result.fallback_reason == "stream_error"
    assert transcriber.events == ["stream_exit", "batch"]
    assert engine._dictation_stream_disabled is True


def test_cancelled_failed_stream_never_batch_transcribes():
    engine = DictationEngine()
    engine.worker.shutdown(wait=False)
    engine.control.shutdown(wait=False)
    transcriber = _FallbackTranscriber(StreamResult(StreamStatus.FAILED))
    engine.transcriber = transcriber
    session = StreamingSession()
    session.cancel()

    result = engine._transcribe_stream_with_fallback(session)

    assert result.status is StreamStatus.CANCELLED
    assert transcriber.batch_audio == []
    assert engine._dictation_stream_disabled is True


def test_batch_fallback_exception_leaves_single_worker_usable():
    engine = DictationEngine()
    engine.worker.shutdown(wait=False)
    engine.control.shutdown(wait=False)
    transcriber = _FallbackTranscriber(
        StreamResult(StreamStatus.FAILED), batch_error=RuntimeError("batch")
    )
    engine.transcriber = transcriber
    session = StreamingSession()
    session.finish(np.ones(20_000, dtype=np.float32))

    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(engine._transcribe_stream_with_fallback, session)
        with pytest.raises(RuntimeError, match="batch"):
            future.result()
        assert worker.submit(lambda: "still usable").result() == "still usable"

    assert engine._dictation_stream_disabled is True


def test_circuit_breaker_sends_next_take_through_existing_batch_path(monkeypatch):
    engine = DictationEngine()
    engine.worker.shutdown(wait=False)
    engine.control.shutdown(wait=False)
    submissions = []

    class Worker:
        def submit(self, function, *args):
            submissions.append((function, args))
            return Future()

    class Recorder:
        level = 0.0

        def start(self, *, chunk_queue=None):
            self.chunk_queue = chunk_queue

        def stop(self):
            return np.ones(20_000, dtype=np.float32)

        def duration_seconds(self, audio):
            return len(audio) / 16_000

    engine.worker = Worker()
    engine.recorder = Recorder()
    engine.transcriber = _FallbackTranscriber(StreamResult(StreamStatus.FAILED))
    engine.state = State.READY
    engine._dictation_stream_disabled = True
    monkeypatch.setattr("speakeasy.engine.play_sound", lambda sound: None)
    monkeypatch.setattr(
        engine,
        "_stop_recorder_guarded",
        lambda timing=None: _RecorderStopResult(
            np.ones(20_000, dtype=np.float32), "normal"
        ),
    )

    engine._start_recording()
    engine._stop_recording()

    assert engine.recorder.chunk_queue is None
    assert len(submissions) == 1
    assert submissions[0][0] == engine._transcribe_and_paste


def test_fallback_result_reaches_shared_finalizer_once(monkeypatch):
    engine = DictationEngine()
    engine.worker.shutdown(wait=False)
    engine.control.shutdown(wait=False)
    transcriber = _FallbackTranscriber(StreamResult(StreamStatus.OVERFLOW))
    engine.transcriber = transcriber
    session = StreamingSession()
    session.finish(np.ones(20_000, dtype=np.float32), valid=False)
    with ThreadPoolExecutor(max_workers=1) as worker:
        result = worker.submit(
            engine._transcribe_stream_with_fallback, session
        ).result()

    inserted = []
    finalized = []
    engine._dictation_stream = session
    engine._dictation_stream_timing = DictationTiming(1)
    monkeypatch.setattr(
        "speakeasy.engine.injector.read_clipboard", lambda: "previous"
    )
    monkeypatch.setattr(
        "speakeasy.engine.injector.insert_text",
        lambda text, **kwargs: inserted.append(text),
    )
    monkeypatch.setattr(
        "speakeasy.engine.injector.restore_clipboard", lambda previous: None
    )
    monkeypatch.setattr("speakeasy.engine.time.sleep", lambda seconds: None)
    monkeypatch.setattr(DictationTiming, "emit", lambda self, status: None)
    original = engine._finalize_dictation

    def finalizer(*args, **kwargs):
        finalized.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(engine, "_finalize_dictation", finalizer)
    future = Future()
    future.set_result(result)

    engine._streaming_finished(session, future)

    assert finalized == ["batch final"]
    assert inserted == ["batch final"]


def test_begin_meeting_refuses_stale_active_stream(monkeypatch):
    engine = DictationEngine()
    engine.worker.shutdown(wait=False)
    engine.control.shutdown(wait=False)
    engine.state = State.READY
    engine.transcriber = _FallbackTranscriber(StreamResult(StreamStatus.COMPLETE))
    engine._dictation_stream = StreamingSession()
    started = []
    monkeypatch.setattr(
        engine.meeting_recorder,
        "start",
        lambda **kwargs: started.append(kwargs),
    )

    engine._begin_meeting(MeetingOptions())

    assert started == []
    assert engine._meeting_active is False
