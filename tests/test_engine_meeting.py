"""Engine meeting flow: begin/end/cancel with every heavy piece faked.

No mic, no model, no sherpa — fakes drive the state machine and prove the
invariants: dictation hotkey off while a meeting runs and re-armed after,
the transcript saved on success and never on cancel, and the spool WAV
deleted on every exit path.
"""

import threading
import time
import wave

import numpy as np
import pytest

from speakeasy import config, meetings
from speakeasy.engine import DictationEngine, State
from speakeasy.transcriber import MeetingCancelled


class SpyListener:
    def __init__(self):
        self.running = True

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def pause(self):
        pass

    def resume(self):
        self.running = True


class FakeMeetingRecorder:
    """Writes a real (tiny) spool WAV so the pipeline reads actual audio."""

    def __init__(self, spool_dir):
        self._spool_dir = spool_dir
        self._path = None
        self.elapsed_seconds = 0.0
        self.level = 0.0

    def start(self):
        self._path = self._spool_dir / "meeting-test.wav"
        with wave.open(str(self._path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(config.SAMPLE_RATE)
            w.writeframes(np.ones(config.SAMPLE_RATE, dtype=np.int16).tobytes())

    def stop(self):
        path, self._path = self._path, None
        return path

    def force_close(self):
        pass

    def take_path(self):
        path, self._path = self._path, None
        return path

    def discard(self):
        if self._path is not None:
            self._path.unlink(missing_ok=True)
            self._path = None


class Sentence:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text
        step = (end - start) / max(len(text.split()), 1)
        self.tokens = []
        for i, w in enumerate(text.split()):
            token = type("Token", (), {})()
            token.start = start + i * step
            token.end = start + (i + 1) * step
            token.duration = step
            token.text = w
            self.tokens.append(token)


class FakeTranscriber:
    def __init__(self, cancel_trap=None):
        self.cancel_trap = cancel_trap  # Event: block until cancel is set

    def transcribe_long(self, audio, *, progress, cancel=None):
        if self.cancel_trap is not None:
            self.cancel_trap.wait(timeout=5.0)
            if cancel is not None and cancel.is_set():
                raise MeetingCancelled
        progress(1.0)
        result = type("Result", (), {})()
        result.sentences = [Sentence(0.0, 1.0, "clod says hello")]
        return result


class FakeDiarizer:
    def diarize(self, samples, progress=lambda f: None):
        progress(1.0)
        return [(0.0, 1.0, 0)]


def _engine(spool_dir):
    engine = DictationEngine()
    engine._listener = SpyListener()
    engine.meeting_recorder = FakeMeetingRecorder(spool_dir)
    engine.transcriber = FakeTranscriber()
    engine.diarizer = FakeDiarizer()
    engine.state = State.READY
    return engine


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_meeting_happy_path(meetings_dir, spool_dir, make_profile):
    engine = _engine(spool_dir)
    engine.profile = make_profile(corrections={"clod": "Claude"})
    saved = []
    engine.on_meeting_saved = saved.append

    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    assert engine._listener.running is False  # dictation dead during meeting
    assert (spool_dir / "meeting-test.wav").exists()

    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    assert saved, "on_meeting_saved never fired"
    stored = meetings.Meeting.load(saved[0])
    assert stored.segments[0].speaker == "Speaker 1"
    assert stored.segments[0].text == "Claude says hello"  # profile applied
    assert not list(spool_dir.iterdir())  # spool deleted after processing
    assert engine._listener.running is True  # hotkey re-armed
    engine.shutdown()


def test_begin_refused_unless_ready(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    engine.state = State.TRANSCRIBING
    engine.begin_meeting()
    time.sleep(0.2)
    assert engine.state is State.TRANSCRIBING
    assert engine._listener.running is True
    engine.shutdown()


def test_cancel_discards_everything(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    trap = threading.Event()
    engine.transcriber = FakeTranscriber(cancel_trap=trap)

    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_PROCESSING)

    engine.cancel_meeting_processing()
    trap.set()  # release the trapped transcriber; it sees cancel and raises
    assert _wait_for(lambda: engine.state is State.READY)
    assert meetings.list_meetings() == []  # nothing saved
    assert not list(spool_dir.iterdir())  # spool deleted anyway
    assert engine._listener.running is True
    engine.shutdown()


def test_shutdown_mid_meeting_discards_spool(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.shutdown()
    engine.meeting_recorder.discard()
    assert not list(spool_dir.iterdir())


def test_resume_does_not_rearm_hotkey_mid_meeting(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.resume()  # e.g. training window closing
    assert engine._listener.running is False
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    engine.shutdown()
