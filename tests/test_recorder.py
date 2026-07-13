"""Recorder stop/force_close: releasing the mic must never hang or leak.

No real CoreAudio here — a fake stream stands in for sounddevice so we can
drive the abort/close paths deterministically.
"""

import threading
import time

import numpy as np
import pytest

from speakeasy import coreaudio
from speakeasy.recorder import Recorder, RecorderBusy


@pytest.fixture(autouse=True)
def clean_guard():
    """The teardown guard is a process-wide singleton — don't leak a simulated
    wedge from one test into the next."""
    coreaudio.teardown.reset()
    yield
    coreaudio.teardown.reset()


class FakeStream:
    def __init__(self, abort_raises=False):
        self.abort_raises = abort_raises
        self.aborted = False
        self.stopped = False
        self.closed = False
        self.started = False

    def start(self):
        self.started = True

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


def test_stop_clears_teardown_marker_on_clean_teardown():
    rec = Recorder()
    _arm(rec, FakeStream())
    rec.stop()
    # A clean abort() returns, so the in-flight marker is cleared and the mic
    # is immediately reusable.
    assert not coreaudio.teardown.in_flight


def test_start_refuses_while_a_stop_is_unwinding():
    # The deadlock scenario: the watchdog force_closed a wedged stream (so
    # _stream is None), but that stream's CoreAudio teardown is STILL running
    # on the abandoned recorder-stop thread. start() must refuse rather than
    # open a second stream — opening now deadlocks both on the HAL mutex, which
    # is exactly the hang this guard prevents.
    rec = Recorder()
    rec._stream = None

    with coreaudio.teardown.in_progress():
        with pytest.raises(RecorderBusy):
            rec.start()

        assert rec._stream is None      # refused WITHOUT opening a new stream
        assert rec._recording is False


def test_teardown_marker_stays_set_while_abort_wedges_then_clears():
    # Realistic concurrency: stop() runs on a background thread and blocks
    # inside abort() (a wedged CoreAudio call). While it blocks, a concurrent
    # start() must refuse; once it unblocks, the mic recovers on its own.
    release = threading.Event()

    class BlockingStream(FakeStream):
        def abort(self):
            self.aborted = True
            release.wait(2.0)

    rec = Recorder()
    _arm(rec, BlockingStream())
    stopper = threading.Thread(target=rec.stop, daemon=True)
    stopper.start()

    entered = False
    for _ in range(400):
        if coreaudio.teardown.in_flight:
            entered = True
            break
        time.sleep(0.005)
    assert entered, "stop() never marked the CoreAudio teardown in flight"

    with pytest.raises(RecorderBusy):
        rec.start()

    release.set()
    stopper.join(2.0)
    # Recovered once the wedged stop finished.
    assert not coreaudio.teardown.in_flight
