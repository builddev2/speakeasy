"""Engine wiring for one live streaming job per eligible dictation."""

from concurrent.futures import Future

import numpy as np
import pytest

from speakeasy import config
from speakeasy.dictation_benchmark import DictationTiming
from speakeasy.dictation_stream import StreamResult, StreamStatus, StreamingSession
from speakeasy.engine import DictationEngine, State, _RecorderStopResult


class Recorder:
    level = 0.0

    def __init__(self):
        self.first_buffer_ns = 1
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
        if function.__name__ == "_resolve_dictation_target":
            from speakeasy import injector
            future = Future()
            future.set_result((injector.focused_target(), 0, {}))
            return future
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


class Listener:
    def __init__(self):
        self.calls = []

    def pause(self):
        self.calls.append("pause")

    def resume(self):
        self.calls.append("resume")

    def stop(self):
        self.calls.append("stop")


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr("speakeasy.engine.play_sound", lambda sound: None)
    monkeypatch.setattr("speakeasy.engine.time.sleep", lambda seconds: None)
    monkeypatch.setattr("speakeasy.engine.injector.read_clipboard", lambda: "old")
    monkeypatch.setattr(
        "speakeasy.engine.injector.insert_text", lambda text, **kwargs: None
    )
    monkeypatch.setattr(
        "speakeasy.engine.injector.restore_clipboard", lambda value: None
    )
    monkeypatch.setattr(DictationTiming, "emit", lambda self, status: None)
    instance = DictationEngine()
    instance.worker.shutdown(wait=False)
    instance.control.shutdown(wait=False)
    instance.worker = Worker()
    instance.control = Control()
    instance.recorder = Recorder()
    instance._listener = Listener()
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
    assert function == engine._transcribe_stream_with_fallback
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


def test_streaming_publishes_only_final_result_once(engine, monkeypatch):
    order = []

    class Profile:
        def apply(self, text):
            order.append(("profile", text))
            return text.upper()

    monkeypatch.setattr(
        "speakeasy.engine.injector.read_clipboard",
        lambda: order.append(("clipboard", "old")) or "old",
    )
    monkeypatch.setattr(
        "speakeasy.engine.injector.insert_text",
        lambda text, **kwargs: order.append(("insert", text)),
    )
    monkeypatch.setattr(
        "speakeasy.engine.injector.restore_clipboard",
        lambda value: order.append(("restore", value)),
    )
    engine.profile = Profile()
    engine._start_recording()
    monkeypatch.setattr(
        engine,
        "_stop_recorder_guarded",
        lambda timing=None: _RecorderStopResult(
            np.ones(20_000, dtype=np.float32), "normal"
        ),
    )

    engine._stop_recording()
    engine.worker.future.set_result(
        StreamResult(StreamStatus.COMPLETE, text="only the final")
    )

    assert order == [
        ("clipboard", "old"),
        ("profile", "only the final"),
        ("insert", "ONLY THE FINAL"),
        ("restore", "old"),
    ]
    assert engine._listener.calls == ["pause", "resume"]
    assert engine.last_dictation_heard == "only the final"
    assert engine.last_dictation_text == "ONLY THE FINAL"


def test_older_settlement_cannot_reset_newer_recording(engine, monkeypatch):
    engine._dictation_generation = 2
    engine._recording_generation = 2
    engine.state = State.RECORDING
    hidden = []
    states = []
    engine.overlay = type("Overlay", (), {"hide": lambda self: hidden.append(True)})()
    engine.on_state_changed = states.append
    timing = DictationTiming(1, clock_ns=lambda: 0)

    engine._restore_after_dictation("old", timing, "success_streaming", generation=1)

    assert engine.state is State.RECORDING
    assert hidden == []
    assert states == []


@pytest.mark.parametrize(
    ("fallback_reason", "expected_status"),
    [
        (None, "success_streaming"),
        ("stream_overflow", "success_fallback_queue_overflow"),
        ("stream_error", "success_fallback_stream_error"),
        ("long_take_batch", "success_batch_long_take"),
    ],
)
def test_streaming_result_maps_privacy_safe_success_status_and_timing(
    engine, monkeypatch, fallback_reason, expected_status
):
    emitted = []
    monkeypatch.setattr(
        DictationTiming,
        "emit",
        lambda self, status: emitted.append((status, self.record(status))),
    )
    monkeypatch.setattr(
        "speakeasy.engine.injector.insert_text",
        lambda text, timing, **kwargs: timing.mark("paste_dispatched"),
    )
    engine._start_recording()
    audio = np.ones(20_000, dtype=np.float32)
    monkeypatch.setattr(
        engine,
        "_stop_recorder_guarded",
        lambda timing=None: _RecorderStopResult(audio, "normal"),
    )

    engine._stop_recording()
    engine.worker.future.set_result(
        StreamResult(
            StreamStatus.COMPLETE,
            text="private final",
            fallback_reason=fallback_reason,
        )
    )

    assert emitted[0][0] == expected_status
    record = emitted[0][1]
    assert record["worker_queue_ms"] == 0.0
    assert record["samples_before"] == 20_000
    assert record["samples_after"] == 20_000
    assert record["release_to_paste_ms"] is not None
    if fallback_reason is None:
        assert record["inference_ms"] is None


def test_empty_streaming_final_profiles_once_and_does_not_insert(
    engine, monkeypatch
):
    applied = []
    inserted = []

    class Profile:
        def apply(self, text):
            applied.append(text)
            return text

    engine.profile = Profile()
    monkeypatch.setattr(
        "speakeasy.engine.injector.insert_text",
        lambda text, **kwargs: inserted.append(text),
    )
    engine._start_recording()
    monkeypatch.setattr(
        engine,
        "_stop_recorder_guarded",
        lambda timing=None: _RecorderStopResult(
            np.ones(20_000, dtype=np.float32), "normal"
        ),
    )

    engine._stop_recording()
    engine.worker.future.set_result(StreamResult(StreamStatus.COMPLETE, text=""))

    assert applied == [""]
    assert inserted == []
    assert engine.last_dictation_heard == ""
    assert engine.last_dictation_text == ""


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


@pytest.fixture(autouse=True)
def enable_streaming_for_stream_path_tests(monkeypatch):
    monkeypatch.setattr("speakeasy.config.DICTATION_STREAMING_ENABLED", True)


def test_slow_focus_lookup_without_hold_start_uses_first_buffer_rule(engine, monkeypatch):
    # No _hold_started_ns is set here, so this covers only the strict first-buffer fallback.
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from speakeasy import injector
    entered, release = threading.Event(), threading.Event()
    def delayed(diagnostics=None, **kwargs):
        entered.set()
        assert release.wait(2)
        return object()
    monkeypatch.setattr(injector, "focused_target", delayed)
    engine._dictation_stream_disabled = True
    original = engine.worker
    with ThreadPoolExecutor(max_workers=1) as worker:
        engine.worker = worker
        try:
            engine._start_recording()
            assert entered.wait(1)
            assert engine.state is State.RECORDING
            # Capture is already active while the AX call remains blocked.
            assert not engine._target_future.done()
            release.set()
            engine._target_future.result(timeout=1)
            monkeypatch.setattr(engine, "_transcribe_and_paste", lambda *a: None)
            engine._stop_recording()
            assert engine._dictation_target is None
        finally:
            release.set()
            engine.worker = original


def _threaded_slow_lookup(engine, monkeypatch, resolved_clock_ns):
    """Run a take whose blocked lookup finishes at engine-clock `resolved_clock_ns`.

    Returns (fake's received kwargs, timing of the stopped take, returned target).
    """
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from speakeasy import injector
    entered, release = threading.Event(), threading.Event()
    returned, received, submitted = object(), {}, []

    def delayed(diagnostics=None, **kwargs):
        received.update(kwargs)
        entered.set()
        assert release.wait(2)
        return returned
    monkeypatch.setattr(injector, "focused_target", delayed)
    monkeypatch.setattr(engine, "_transcribe_and_paste",
                        lambda *args: submitted.append(args))
    clock = [0]
    engine._dictation_clock_ns = lambda: clock[0]
    engine._dictation_stream_disabled = True
    engine._hold_started_ns = 0
    engine.recorder.first_buffer_ns = 11_500_000
    original = engine.worker
    with ThreadPoolExecutor(max_workers=1) as worker:
        engine.worker = worker
        try:
            engine._start_recording()
            assert entered.wait(1)
            assert engine.state is State.RECORDING
            assert not engine._target_future.done()
            clock[0] = resolved_clock_ns
            release.set()
            engine._target_future.result(timeout=1)
            engine._stop_recording()
            worker.shutdown(wait=True)
        finally:
            release.set()
            engine.worker = original
    timings = [arg for args in submitted for arg in args if isinstance(arg, DictationTiming)]
    assert len(timings) == 1
    return received, timings[0], returned


def test_slow_focus_lookup_within_grace_binds_key_down_target(engine, monkeypatch):
    received, timing, returned = _threaded_slow_lookup(engine, monkeypatch, 250_000_000)
    assert received["deadline_ns"]() == 300_000_000
    assert engine._dictation_target is returned
    assert timing.record("success")["target_status"] == "accepted"


def test_slow_focus_lookup_past_grace_is_late_and_not_bound(engine, monkeypatch):
    received, timing, _ = _threaded_slow_lookup(engine, monkeypatch, 350_000_000)
    assert received["deadline_ns"]() == 300_000_000
    assert engine._dictation_target is None
    assert timing.record("success")["target_status"] == "late"


def test_target_resolved_before_first_buffer_is_retained(engine, monkeypatch):
    from speakeasy import injector
    engine._dictation_stream_disabled = True
    engine._start_recording()
    engine._stop_recording()
    assert engine._dictation_target is injector.focused_target()


_DIAG = {
    "target_first_ax_error": -25212,
    "target_ax_enabled": True,
    "target_retry_ax_error": 0,
    "target_retry_count": 1,
    "target_app_switched": False,
    "target_app": "com.example.App",
    "target_deadline_stop": True,
}


def _stopped_timing(engine, future):
    """Stop a take whose target future is `future`; return the take's timing."""
    engine._dictation_stream_disabled = True
    engine._start_recording()
    engine._hold_started_ns = 0
    engine._target_future = future
    engine._stop_recording()
    for function, args in reversed(engine.worker.submissions):
        for arg in args:
            if isinstance(arg, DictationTiming):
                return arg
    raise AssertionError("no timing submitted")


def _done(*result):
    future = Future()
    future.set_result(result)
    return future


def test_target_status_accepted_records_resolve_time_and_diagnostics(engine):
    target = object()
    timing = _stopped_timing(engine, _done(target, 1, dict(_DIAG)))
    assert engine._dictation_target is target
    record = timing.record("success")
    assert record["target_status"] == "accepted"
    assert record["target_resolve_ms"] == 0.0  # 1 ns after key-down
    # _stopped_timing sets hold start 0; the fake recorder's first buffer is 1 ns
    # and the target resolved at 1 ns.
    assert record["target_first_ax_error"] == -25212
    assert record["target_ax_enabled"] is True
    assert record["target_retry_ax_error"] == 0
    assert record["target_retry_count"] == 1
    assert record["target_app_switched"] is False
    assert record["target_deadline_stop"] is True
    assert record["target_app"] == "com.example.App"


def test_target_status_late_when_resolved_after_grace_window(engine):
    # 350 ms after key-down and after the first buffer: not bound.
    timing = _stopped_timing(engine, _done(object(), 350_000_000, dict(_DIAG)))
    assert engine._dictation_target is None
    record = timing.record("success")
    assert record["target_status"] == "late"
    assert record["target_resolve_ms"] == 350.0


def test_target_accepted_when_resolved_after_first_buffer_within_grace(engine):
    # Teams: first buffer 11.5 ms, AX lookup resolved at 15.3 ms.
    target = object()
    engine.recorder.first_buffer_ns = 11_500_000
    timing = _stopped_timing(engine, _done(target, 15_300_000, dict(_DIAG)))
    assert engine._dictation_target is target
    record = timing.record("success")
    assert record["target_status"] == "accepted"
    assert record["target_resolve_ms"] == 15.3


def test_target_grace_boundary_is_inclusive(engine):
    grace_ns = int(config.DICTATION_TARGET_GRACE_SECONDS * 1_000_000_000)
    target = object()
    _stopped_timing(engine, _done(target, grace_ns, dict(_DIAG)))
    assert engine._dictation_target is target


def test_target_without_hold_start_uses_first_buffer_rule(engine):
    engine.recorder.first_buffer_ns = 11_500_000
    for resolved, bound in ((11_500_000, True), (15_300_000, False)):
        engine._dictation_target = None
        engine._dictation_stream_disabled = True
        engine._start_recording()
        engine._hold_started_ns = None
        target = object()
        engine._target_future = _done(target, resolved, dict(_DIAG))
        engine._stop_recording()
        assert (engine._dictation_target is target) is bound


def test_target_without_first_buffer_uses_grace_window(engine):
    engine.recorder.first_buffer_ns = None
    target = object()
    _stopped_timing(engine, _done(target, 15_300_000, dict(_DIAG)))
    assert engine._dictation_target is target
    engine._dictation_target = None
    _stopped_timing(engine, _done(object(), 350_000_000, dict(_DIAG)))
    assert engine._dictation_target is None


def test_target_status_missing_when_lookup_returns_none(engine):
    timing = _stopped_timing(engine, _done(None, 1_000_100, {
        **_DIAG, "target_retry_ax_error": -25212}))
    assert engine._dictation_target is None
    record = timing.record("insertion_blocked")
    assert record["target_status"] == "missing"
    assert record["target_retry_ax_error"] == -25212
    assert record["target_resolve_ms"] == 1.0


def test_target_status_pending_cancelled_and_error(engine):
    assert _stopped_timing(engine, Future()).record("success")["target_status"] == "pending"
    cancelled = Future()
    cancelled.cancel()
    record = _stopped_timing(engine, cancelled).record("success")
    assert record["target_status"] == "cancelled"
    assert record["target_resolve_ms"] is None
    failed = Future()
    failed.set_exception(RuntimeError("private"))
    record = _stopped_timing(engine, failed).record("success")
    assert record["target_status"] == "error"
    assert record["target_first_ax_error"] is None


def test_resolve_target_passes_grace_deadline_and_engine_clock(engine, monkeypatch):
    from speakeasy import injector
    seen = []
    monkeypatch.setattr(
        injector, "focused_target",
        lambda diagnostics=None, **kwargs: seen.append(kwargs) or object())
    generation = engine._dictation_generation
    engine._resolve_dictation_target(generation, hold_started_ns=5_000)
    engine._resolve_dictation_target(generation, None)
    engine._resolve_dictation_target(generation)
    engine.recorder.first_buffer_ns = 1
    assert seen[0]["deadline_ns"]() == 5_000 + 300_000_000
    assert seen[0]["clock_ns"] == engine._dictation_clock_ns
    assert seen[1]["deadline_ns"] is None
    assert seen[2]["deadline_ns"] is None


def test_start_recording_submits_hold_started_ns(engine):
    submitted = []
    original = engine.worker.submit
    def submit(function, *args):
        if function.__name__ == "_resolve_dictation_target":
            submitted.append(args)
        return original(function, *args)
    engine.worker.submit = submit
    engine._hold_started_ns = 123_456
    engine._start_recording()
    assert submitted == [(engine._dictation_generation, 123_456)]


def test_paste_last_dictation_lookup_makes_no_retries(engine, monkeypatch):
    from speakeasy import injector
    seen = []
    monkeypatch.setattr(
        injector, "focused_target",
        lambda diagnostics=None, **kwargs: seen.append(kwargs) or object())
    engine.last_dictation_text = "recovered"
    engine.state = State.READY
    engine.paste_last_dictation()
    assert seen == [{"retry_attempts": 0}]


def test_target_deadline_follows_acceptance_bound(engine):
    grace = 300_000_000
    engine.recorder.first_buffer_ns = None
    assert engine._target_deadline_ns(1_000) is None
    engine.recorder.first_buffer_ns = 1_000 + 900_000_000  # late mic start
    assert engine._target_deadline_ns(1_000) == 1_000 + 900_000_000
    engine.recorder.first_buffer_ns = 1_000 + 10_000_000   # early buffer
    assert engine._target_deadline_ns(1_000) == 1_000 + grace


def test_late_first_buffer_keeps_retrying_target_lookup(engine, monkeypatch):
    from speakeasy import injector
    seen = []
    monkeypatch.setattr(
        injector, "focused_target",
        lambda diagnostics=None, **kwargs: seen.append(kwargs) or object())
    engine.recorder.first_buffer_ns = None  # mic has not delivered yet
    engine._resolve_dictation_target(engine._dictation_generation, 0)
    deadline = seen[0]["deadline_ns"]
    assert deadline() is None
    engine.recorder.first_buffer_ns = 900_000_000
    assert deadline() == 900_000_000
