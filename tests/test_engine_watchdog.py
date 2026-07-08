"""The recorder-stop watchdog must not let a wedged mic freeze the pipeline."""

import threading

from speakeasy import config
from speakeasy.engine import DictationEngine


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

    audio = engine._stop_recorder_guarded()

    assert audio.size == 0          # this take is dropped, not awaited forever
    assert rec.force_closed is True  # mic forcibly released
    rec.release.set()               # let the orphaned stop() thread exit


def test_guarded_stop_returns_audio_on_the_fast_path():
    import numpy as np

    engine = DictationEngine()

    class QuickRecorder:
        def stop(self):
            return np.ones(8, dtype="float32")

    engine.recorder = QuickRecorder()
    audio = engine._stop_recorder_guarded()
    assert audio.shape == (8,)
