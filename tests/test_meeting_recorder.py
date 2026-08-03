"""MeetingRecorder: spool-to-WAV capture without CoreAudio.

A fake stream stands in for sounddevice; audio blocks are pushed through
_on_audio directly, so the queue → writer thread → WAV pipeline runs for
real against a temp spool dir.
"""

import wave

import numpy as np
import pytest

from speakeasy import config, meeting_recorder
from speakeasy.meeting_recorder import MeetingRecorder


class FakeStream:
    def __init__(self, *args, **kwargs):
        self.callback = kwargs.get("callback")
        self.started = False
        self.aborted = False
        self.closed = False

    def start(self):
        self.started = True

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True


@pytest.fixture
def fake_stream(monkeypatch):
    holder = {}

    def make(*args, **kwargs):
        holder["stream"] = FakeStream(*args, **kwargs)
        return holder["stream"]

    monkeypatch.setattr(meeting_recorder.sd, "InputStream", make)
    return holder


def _block(frames=1600, value=1000):
    return np.full((frames, 1), value, dtype=np.int16)


def test_spool_round_trip(spool_dir, fake_stream):
    rec = MeetingRecorder()
    rec.start()
    stream = fake_stream["stream"]
    assert stream.started

    for _ in range(10):
        rec._on_audio(_block(), 1600, None, None)
    assert rec.elapsed_seconds == pytest.approx(1.0)
    assert rec.level > 0

    path = rec.stop()
    assert stream.aborted
    assert path is not None and path.parent == spool_dir
    with wave.open(str(path)) as w:
        assert w.getframerate() == config.SAMPLE_RATE
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getnframes() == 16000
    path.unlink()


def test_first_buffer_uses_callback_time_on_shared_monotonic_clock(
    spool_dir, fake_stream, monkeypatch
):
    monkeypatch.setattr(meeting_recorder.monotonic_time, "monotonic_ns", lambda: 5_000_000_000)
    timing = type(
        "Timing",
        (),
        {"currentTime": 20.0, "inputBufferAdcTime": 19.75},
    )()
    rec = MeetingRecorder()
    rec.start()
    rec._on_audio(_block(), 1600, timing, None)
    path = rec.stop()
    assert rec.first_buffer_ns == 4_750_000_000
    path.unlink()


def test_bounded_queue_overflow_counts_dropped_frames(
    spool_dir, fake_stream, monkeypatch
):
    rec = MeetingRecorder()
    rec.start()

    class FullQueue:
        def put_nowait(self, block):
            raise meeting_recorder.queue.Full

    rec._queue = FullQueue()
    rec._on_audio(_block(80), 80, None, None)
    assert rec.dropped_frames == 80
    rec.force_close()
    rec.discard()


def test_stop_without_start_is_none(spool_dir, fake_stream):
    assert MeetingRecorder().stop() is None


def test_straggler_callbacks_after_stop_are_ignored(spool_dir, fake_stream):
    rec = MeetingRecorder()
    rec.start()
    rec._on_audio(_block(), 1600, None, None)
    path = rec.stop()
    rec._on_audio(_block(), 1600, None, None)  # must not touch the closed WAV
    with wave.open(str(path)) as w:
        assert w.getnframes() == 1600


def test_spool_cap_drops_frames(spool_dir, fake_stream, monkeypatch):
    monkeypatch.setattr(config, "MEETING_MAX_SECONDS", 0.1)  # 1600 frames
    rec = MeetingRecorder()
    rec.start()
    for _ in range(5):
        rec._on_audio(_block(), 1600, None, None)
    path = rec.stop()
    with wave.open(str(path)) as w:
        assert w.getnframes() == 1600  # capped, disk can't fill


def test_discard_unlinks_spool(spool_dir, fake_stream):
    rec = MeetingRecorder()
    rec.start()
    rec._on_audio(_block(), 1600, None, None)
    spool = rec._path
    rec.stop()
    # stop() surrendered the path; simulate the cancel path instead:
    rec._path = spool
    rec.discard()
    assert not spool.exists()
    assert rec._path is None


def test_force_close_keeps_valid_wav_and_path(spool_dir, fake_stream):
    import time

    rec = MeetingRecorder()
    rec.start()
    rec._on_audio(_block(), 1600, None, None)
    # Let the writer drain before the wedge simulation.
    deadline = time.time() + 2.0
    while not rec._queue.empty() and time.time() < deadline:
        time.sleep(0.01)
    rec.force_close()
    path = rec.take_path()
    assert path is not None
    with wave.open(str(path)) as w:  # header was fixed up on close
        assert w.getframerate() == config.SAMPLE_RATE


def test_sweep_spool_dir_removes_orphans(spool_dir):
    orphan = spool_dir / "meeting-20260101-000000.wav"
    orphan.write_bytes(b"leftover")
    meeting_recorder.sweep_spool_dir()
    assert not orphan.exists()
