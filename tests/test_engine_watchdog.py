"""The recorder-stop watchdog must not let a wedged mic freeze the pipeline."""

import threading

from speakeasy import config
from speakeasy.engine import DictationEngine, State
from speakeasy.meeting_recorder import MeetingRecording
from speakeasy.microphone_helper import MicrophoneHelperError
from speakeasy.recorder import Recorder, RecorderBusy


class HangingRecorder:
    """Stands in for Recorder: stop() blocks until released, like a wedged
    CoreAudio call."""

    def __init__(self):
        self.release = threading.Event()
        self.force_closed = False

    def stop(self):
        self.release.wait()  # never, within the test's timeout
        import numpy as np

        return np.empty(0, dtype="float32")

    def force_close(self):
        self.force_closed = True


def test_guarded_stop_gives_up_and_releases_the_mic(monkeypatch):
    monkeypatch.setattr(config, "RECORDER_STOP_TIMEOUT_SECONDS", 0.1)
    engine = DictationEngine()
    rec = HangingRecorder()
    engine.recorder = rec

    stopped = engine._stop_recorder_guarded()

    assert stopped.audio.size == 0  # this take is dropped, not awaited forever
    assert stopped.outcome == "timeout"
    assert rec.force_closed is True  # mic forcibly released
    rec.release.set()               # let the orphaned stop() thread exit


def test_guarded_stop_returns_audio_on_the_fast_path():
    import numpy as np

    engine = DictationEngine()

    class QuickRecorder:
        def stop(self):
            return np.ones(8, dtype="float32")

    engine.recorder = QuickRecorder()
    stopped = engine._stop_recorder_guarded()
    assert stopped.audio.shape == (8,)
    assert stopped.outcome == "normal"


def test_guarded_stop_reports_helper_error_and_releases_it():
    class BrokenRecorder:
        def __init__(self):
            self.force_closed = False

        def stop(self):
            raise MicrophoneHelperError("helper exited")

        def force_close(self):
            self.force_closed = True

    engine = DictationEngine()
    recorder = BrokenRecorder()
    engine.recorder = recorder

    stopped = engine._stop_recorder_guarded()

    assert stopped.outcome == "error"
    assert recorder.force_closed is True


def test_helper_timeout_is_classified_and_next_take_recreates_helper(monkeypatch):
    import numpy as np

    monkeypatch.setattr(config, "RECORDER_STOP_TIMEOUT_SECONDS", 0.05)

    class Helper:
        level = 0.0
        stream_dropped_frames = 0
        stream_delivery_complete = True

        def __init__(self, *, wedge=False):
            self.wedge = wedge
            self.release = threading.Event()
            self.started = False
            self.terminated = False

        def launch(self):
            pass

        def start(self, chunk_queue=None):
            self.started = True

        def stop(self):
            if self.wedge:
                self.release.wait()
                raise MicrophoneHelperError()
            return np.ones(8, dtype=np.float32)

        def terminate(self):
            self.terminated = True
            self.release.set()

    wedged = Helper(wedge=True)
    recovered = Helper()
    helpers = iter((wedged, recovered))
    recorder = Recorder()
    recorder._new_helper = lambda: next(helpers)
    recorder.start()
    engine = DictationEngine()
    engine.recorder = recorder

    stopped = engine._stop_recorder_guarded()

    assert stopped.outcome == "timeout"
    assert wedged.terminated is True
    recorder.start()
    assert recovered.started is True


def test_start_recording_stays_idle_when_mic_is_busy():
    """If the recorder refuses (a prior stop still unwinding), the engine drops
    the take and stays idle rather than propagating — the hotkey stays live."""
    engine = DictationEngine()
    engine.transcriber = object()  # non-None so idle resolves to READY

    class BusyRecorder:
        def start(self, *, chunk_queue=None):
            raise RecorderBusy()

    engine.recorder = BusyRecorder()
    engine._start_recording()

    assert engine.state is State.MIC_FAILED  # explicit failure, never RECORDING


def test_guarded_dual_track_stop_times_out_without_blocking_control(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(config, "RECORDER_STOP_TIMEOUT_SECONDS", 0.1)
    engine = DictationEngine()

    class HangingMeetingRecorder:
        def __init__(self):
            self.release = threading.Event()
            self.force_closed = False

        def stop(self):
            self.release.wait()
            return None

        def force_close(self):
            self.force_closed = True

        def take_recording(self):
            return MeetingRecording(mic_path=tmp_path / "mic.wav")

    recorder = HangingMeetingRecorder()
    engine.meeting_recorder = recorder

    recording = engine._stop_meeting_recorder_guarded()

    assert recording.mic_path == tmp_path / "mic.wav"
    assert recorder.force_closed is True
    recorder.release.set()


def test_stop_error_prewarms_on_control_before_next_take():
    class Recoverable:
        def __init__(self):
            self.reasons = []
        def stop(self):
            raise MicrophoneHelperError()
        def force_close(self):
            pass
        def recover(self, reason):
            self.reasons.append(reason)
            return True
    engine = DictationEngine()
    engine.recorder = Recoverable()
    assert engine._stop_recorder_guarded().outcome == "error"
    assert engine.recorder.reasons == ["helper_exit"]


def test_1000_control_recovery_cycles_have_no_busy_cascade():
    import json
    import time
    import numpy as np
    from speakeasy.dictation_benchmark import _percentile
    helpers = []
    class Helper:
        level = 0
        first_buffer_ns = 123
        stream_dropped_frames = 0
        stream_delivery_complete = True
        def __init__(self):
            self.terminated = False
            self.fail = False
        def launch(self):
            pass
        def start(self, chunk_queue=None):
            pass
        def stop(self):
            if self.fail:
                raise MicrophoneHelperError()
            return np.ones(8, dtype=np.float32)
        def terminate(self):
            self.terminated = True
    engine = DictationEngine()
    def create():
        helper = Helper()
        helpers.append(helper)
        return helper
    engine.recorder._new_helper = create
    latencies = []
    initial_threads = threading.active_count()
    def cycle(index):
        engine.recorder.start()
        helpers[-1].fail = index % 10 == 0
        started = time.perf_counter()
        result = engine._stop_recorder_guarded()
        latencies.append((time.perf_counter() - started) * 1000)
        assert result.outcome == ("error" if index % 10 == 0 else "normal")
        assert engine.recorder.state == "ready"
        assert sum(not helper.terminated for helper in helpers) == 1
    for index in range(1000):
        engine.control.submit(cycle, index).result(timeout=2)
    engine.recorder.shutdown()
    engine.control.shutdown(wait=True)
    engine.worker.shutdown(wait=True)
    assert all(helper.terminated for helper in helpers)
    assert threading.active_count() <= initial_threads
    print("RECOVERY_SOAK " + json.dumps(dict(
        cycles=1000, injected_failures=100, leaked_helpers=0,
        p50_ms=round(_percentile(latencies, .5), 2),
        p95_ms=round(_percentile(latencies, .95), 2), max_ms=round(max(latencies), 2))))


def test_wake_recovery_runs_without_relaunch_and_respects_shutdown(monkeypatch):
    engine = DictationEngine()
    calls = []
    monkeypatch.setattr(engine, "_recover_microphone", lambda reason: calls.append(reason))
    engine._after_system_wake()
    assert calls == ["sleep_wake"]
    engine._shutting_down = True
    engine._after_system_wake()
    assert calls == ["sleep_wake"]


def test_rapid_holds_during_recovery_are_rejected_as_pairs():
    from types import SimpleNamespace
    engine = DictationEngine()
    submitted = []
    engine.control.shutdown(wait=True)
    engine.control = SimpleNamespace(submit=lambda *args: submitted.append(args))
    for _ in range(1000):
        engine.state = State.MIC_RECOVERING
        engine._on_hold_start()
        engine.state = State.READY
        engine._on_hold_end()
    assert submitted == []
    engine.worker.shutdown(wait=True)


def test_manual_recovery_is_serialized_and_repeated_clicks_do_not_queue():
    from types import SimpleNamespace
    engine = DictationEngine()
    engine.state = State.MIC_FAILED
    submitted = []
    old_control = engine.control
    engine.control = SimpleNamespace(submit=lambda *args: submitted.append(args))
    try:
        assert engine.retry_microphone()
        assert engine.state is State.MIC_RECOVERING
        assert not engine.retry_microphone()
        assert submitted == [(engine._recover_microphone, 'manual_retry')]
    finally:
        old_control.shutdown(wait=True)
        engine.worker.shutdown(wait=True)


def test_manual_recovery_does_not_reopen_while_previous_stop_owns_recorder():
    from types import SimpleNamespace
    engine = DictationEngine()
    calls = []
    engine.recorder = SimpleNamespace(recover=lambda reason: calls.append(reason))
    engine._recorder_stop_pending = threading.Event()
    try:
        assert not engine._recover_microphone('manual_retry')
        assert engine.state is State.MIC_FAILED
        assert not calls
    finally:
        engine.control.shutdown(wait=True)
        engine.worker.shutdown(wait=True)
