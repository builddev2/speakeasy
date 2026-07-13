"""The CoreAudio teardown guard is process-wide, not per-recorder.

The HAL mutex is per-process/per-device: a stream teardown still unwinding
inside CoreAudio blocks an open on *any* stream, not just its own. So a wedged
MeetingRecorder.stop() must refuse a dictation Recorder.start() (and vice
versa) — a per-instance guard would let the two deadlock against each other.

No real CoreAudio here: fake streams stand in for sounddevice so the wedge is
driven deterministically.
"""

import threading
import time

import numpy as np
import pytest

from speakeasy import coreaudio, meeting_recorder
from speakeasy.coreaudio import RecorderBusy
from speakeasy.meeting_recorder import MeetingRecorder
from speakeasy.recorder import Recorder


@pytest.fixture(autouse=True)
def clean_guard():
    """The guard is a module singleton; don't leak a wedge across tests."""
    coreaudio.teardown.reset()
    yield
    coreaudio.teardown.reset()


class FakeStream:
    """Blocks inside abort() until released — a wedged CoreAudio teardown."""

    def __init__(self, release=None, **kwargs):
        self.release = release
        self.started = False
        self.aborted = False
        self.closed = False

    def start(self):
        self.started = True

    def abort(self):
        self.aborted = True
        if self.release is not None:
            self.release.wait(2.0)

    def close(self):
        self.closed = True


def _wait_for_teardown():
    for _ in range(400):
        if coreaudio.teardown.in_flight:
            return True
        time.sleep(0.005)
    return False


def _wedge_meeting_stop(spool_dir, monkeypatch):
    """Start a meeting, then wedge its stop() inside abort() on a side thread.

    Returns (recorder, release_event, stop_thread) with the teardown in flight —
    exactly the state the watchdog leaves behind after it gives up on a stop.
    """
    release = threading.Event()
    monkeypatch.setattr(
        meeting_recorder.sd, "InputStream", lambda **kw: FakeStream(release, **kw)
    )
    rec = MeetingRecorder()
    rec.start()
    stopper = threading.Thread(target=rec.stop, daemon=True)
    stopper.start()
    assert _wait_for_teardown(), "MeetingRecorder.stop() never marked the teardown"
    return rec, release, stopper


def test_wedged_meeting_stop_blocks_dictation_open(spool_dir, monkeypatch):
    # THE BUG: after a meeting whose stop wedged, the watchdog abandons it and
    # dictation re-arms — but that abort() is still holding the HAL mutex. The
    # next hotkey press opened a second stream straight into the deadlock,
    # because Recorder's guard only knew about Recorder's own stops.
    _, release, stopper = _wedge_meeting_stop(spool_dir, monkeypatch)

    dictation = Recorder()
    opened = []
    monkeypatch.setattr(
        "speakeasy.recorder.sd.InputStream",
        lambda **kw: opened.append(kw) or FakeStream(**kw),
    )

    with pytest.raises(RecorderBusy):
        dictation.start()
    assert not opened, "opened a stream while a meeting teardown was in the HAL"
    assert dictation._stream is None
    assert dictation._recording is False

    # prewarm() is the other way in — it must refuse silently, not open.
    dictation.prewarm()
    assert not opened

    release.set()
    stopper.join(2.0)
    assert not coreaudio.teardown.in_flight  # mic self-recovers

    dictation.start()  # and the next take works
    assert opened


def test_wedged_meeting_stop_blocks_another_meeting_open(spool_dir, monkeypatch):
    _, release, stopper = _wedge_meeting_stop(spool_dir, monkeypatch)

    second = MeetingRecorder()
    with pytest.raises(RecorderBusy):
        second.start()
    assert second._stream is None
    assert second._recording is False
    assert second._path is None  # no orphan spool left behind by the refusal

    release.set()
    stopper.join(2.0)


def test_wedged_dictation_stop_blocks_meeting_open(spool_dir, monkeypatch):
    # The mirror image: Begin Meeting while a dictation stop is still unwinding.
    # _begin_meeting runs on the control thread with no watchdog, so opening
    # here froze control outright.
    release = threading.Event()
    dictation = Recorder()
    dictation._stream = FakeStream(release)
    dictation._recording = True
    dictation._chunks = [np.ones((4, 1), dtype=np.float32)]
    stopper = threading.Thread(target=dictation.stop, daemon=True)
    stopper.start()
    assert _wait_for_teardown(), "Recorder.stop() never marked the teardown"

    opened = []
    monkeypatch.setattr(
        meeting_recorder.sd,
        "InputStream",
        lambda **kw: opened.append(kw) or FakeStream(**kw),
    )
    rec = MeetingRecorder()
    with pytest.raises(RecorderBusy):
        rec.start()
    assert not opened, "opened a meeting stream while a dictation teardown was live"

    release.set()
    stopper.join(2.0)
    rec.start()  # recovered
    assert opened


def test_meeting_stop_clears_guard_on_clean_teardown(spool_dir, monkeypatch):
    monkeypatch.setattr(
        meeting_recorder.sd, "InputStream", lambda **kw: FakeStream(**kw)
    )
    rec = MeetingRecorder()
    rec.start()
    path = rec.stop()
    assert not coreaudio.teardown.in_flight
    assert path is not None
    path.unlink()


def test_force_close_leaves_the_wedge_marked(spool_dir, monkeypatch):
    # force_close() abandons the reference but must NOT clear the guard: the
    # abandoned stop is still inside the HAL, and that is the whole point.
    rec, release, stopper = _wedge_meeting_stop(spool_dir, monkeypatch)
    rec.force_close()
    assert coreaudio.teardown.in_flight

    release.set()
    stopper.join(2.0)
    assert not coreaudio.teardown.in_flight


def test_overlapping_teardowns_do_not_clear_early(spool_dir):
    # Two teardowns can overlap (a wedged one plus a later clean one). A plain
    # flag would let the second's exit clear the first's wedge; the guard must
    # stay set until every teardown has actually returned.
    with coreaudio.teardown.in_progress():
        with coreaudio.teardown.in_progress():
            assert coreaudio.teardown.in_flight
        assert coreaudio.teardown.in_flight  # inner exit must not unwedge outer
    assert not coreaudio.teardown.in_flight
