"""The recorder-stop watchdog must not let a wedged mic freeze the pipeline."""

import threading

from speakeasy import config
from speakeasy.engine import DictationEngine, State
from speakeasy.meeting_recorder import MeetingRecording
from speakeasy.recorder import RecorderBusy


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


def test_start_recording_stays_idle_when_mic_is_busy():
    """If the recorder refuses (a prior stop still unwinding), the engine drops
    the take and stays idle rather than propagating — the hotkey stays live."""
    engine = DictationEngine()
    engine.transcriber = object()  # non-None so idle resolves to READY

    class BusyRecorder:
        def start(self):
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
