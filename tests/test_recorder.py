"""Recorder stop/force_close: releasing the mic must never hang or leak.

No real CoreAudio here — a fake stream stands in for sounddevice so we can
drive the abort/close paths deterministically.
"""

import numpy as np

from speakeasy.recorder import Recorder


class FakeStream:
    def __init__(self, abort_raises=False):
        self.abort_raises = abort_raises
        self.aborted = False
        self.stopped = False
        self.closed = False

    def abort(self):
        self.aborted = True
        if self.abort_raises:
            raise RuntimeError("device gone")

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


def _arm(rec, stream):
    """Put a recorder into the recording state with one captured chunk."""
    rec._stream = stream
    rec._recording = True
    rec._chunks = [np.ones((4, 1), dtype=np.float32)]


def test_stop_aborts_not_stops_and_returns_audio():
    rec = Recorder()
    stream = FakeStream()
    _arm(rec, stream)

    audio = rec.stop()

    assert stream.aborted and not stream.stopped  # abort(), never the draining stop()
    assert audio.shape == (4,)
    assert rec._stream is stream  # kept open for the next take
    assert rec._recording is False


def test_stop_drops_stream_when_abort_fails():
    rec = Recorder()
    stream = FakeStream(abort_raises=True)
    _arm(rec, stream)

    audio = rec.stop()

    assert stream.closed
    assert rec._stream is None  # rebuilt on next start()
    assert audio.shape == (4,)


def test_stop_is_noop_when_not_recording():
    rec = Recorder()
    assert rec.stop().size == 0


def test_force_close_abandons_stream_without_touching_it():
    rec = Recorder()
    stream = FakeStream()
    _arm(rec, stream)

    rec.force_close()

    assert rec._stream is None
    assert rec._recording is False
    # The wedged stream is left untouched — closing it could race the in-flight
    # PortAudio call the watchdog is escaping.
    assert not stream.closed
