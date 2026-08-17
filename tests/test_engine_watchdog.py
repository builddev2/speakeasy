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

    assert engine.state is State.READY  # stayed idle, never entered RECORDING


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
