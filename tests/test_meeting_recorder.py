"""MeetingRecorder: spool-to-WAV capture without CoreAudio.

A fake stream stands in for sounddevice; audio blocks are pushed through
_on_audio directly, so the queue → writer thread → WAV pipeline runs for
real against a temp spool dir.
"""

import threading
import wave

import numpy as np
import pytest

from speakeasy import config, meeting_recorder
from speakeasy.meeting_recorder import MeetingRecorder
from speakeasy.meeting_stream import MeetingASRSession


class FakeStream:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
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


def test_stream_requests_fixed_100ms_blocks(spool_dir, fake_stream):
    # Left to PortAudio, the 16 kHz stream used 15-frame blocks: about 1,070
    # GIL-taking callbacks a second. Under contention PortAudio skipped
    # callbacks without flagging an overflow, so the mic track lost ~0.7% idle
    # and far more during pretranscription, drifting ahead of the system track.
    rec = MeetingRecorder()
    rec.start()
    kwargs = fake_stream["stream"].kwargs
    assert kwargs["blocksize"] == config.MEETING_CAPTURE_BLOCK_FRAMES
    assert config.MEETING_CAPTURE_BLOCK_FRAMES == config.SAMPLE_RATE // 10
    rec.force_close()
    rec.discard()


def test_writer_queue_holds_buffer_seconds_of_audio(spool_dir, fake_stream):
    rec = MeetingRecorder()
    rec.start()
    held = rec._queue.maxsize * config.MEETING_CAPTURE_BLOCK_FRAMES
    assert held / config.SAMPLE_RATE >= config.MEETING_CAPTURE_BUFFER_SECONDS
    assert config.MEETING_CAPTURE_BUFFER_SECONDS >= 30.0
    rec.force_close()
    rec.discard()


def test_writer_feeds_pretranscription_after_persisting_audio(
    spool_dir, fake_stream, monkeypatch
):
    monkeypatch.setattr(config, "MEETING_CHUNK_SECONDS", 0.1)
    monkeypatch.setattr(config, "MEETING_OVERLAP_SECONDS", 0.02)
    session = MeetingASRSession(max_chunks=2)
    rec = MeetingRecorder()
    rec.configure_pretranscription(session)
    rec.start()

    rec._on_audio(_block(), 1600, None, None)
    path = rec.stop()

    start, pcm = session.get(timeout=0)
    assert start == 0
    assert len(pcm) == 1600 * 2
    assert session.finished is True
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
    writer_queue = rec._queue

    class FullQueue:
        def put_nowait(self, block):
            raise meeting_recorder.queue.Full

    rec._queue = FullQueue()
    rec._on_audio(_block(80), 80, None, None)
    assert rec.dropped_frames == 80
    assert rec.writer_lagged is True
    rec._queue = writer_queue
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


def test_force_close_never_closes_wav_while_writer_is_writing(
    spool_dir, fake_stream
):
    rec = MeetingRecorder()
    rec.start()
    writer_entered = threading.Event()
    allow_write = threading.Event()
    force_close_done = threading.Event()
    writeframes = rec._wav.writeframes

    def held_writeframes(block):
        writer_entered.set()
        assert allow_write.wait(timeout=1.0)
        writeframes(block)

    rec._wav.writeframes = held_writeframes
    rec._on_audio(_block(), 1600, None, None)
    assert writer_entered.wait(timeout=1.0)

    closer = threading.Thread(
        target=lambda: (rec.force_close(), force_close_done.set())
    )
    closer.start()
    assert not force_close_done.wait(timeout=0.05)
    allow_write.set()
    closer.join(timeout=1.0)
    assert force_close_done.is_set()

    path = rec.take_path()
    assert path is not None
    with wave.open(str(path)) as wav:
        assert wav.getnframes() == 1600


def test_sweep_spool_dir_removes_orphans(spool_dir):
    orphan = spool_dir / "meeting-20260101-000000.wav"
    orphan.write_bytes(b"leftover")
    meeting_recorder.sweep_spool_dir()
    assert not orphan.exists()


def test_input_overflow_flags_and_timelines_are_recorded(spool_dir, fake_stream):
    class Status:
        input_overflow = True

    class Timing:
        def __init__(self, adc):
            self.inputBufferAdcTime = adc
            self.currentTime = adc + 0.01

    rec = MeetingRecorder()
    rec.start()
    rec._on_audio(_block(), 1600, Timing(50.0), None)
    rec._on_audio(_block(), 1600, Timing(50.1), Status())
    # 0.25 s of input lost before this block reached PortAudio.
    rec._on_audio(_block(), 1600, Timing(50.45), None)
    rec.stop()

    assert rec.input_overflows == 1
    assert rec.timeline["observations"] == 3
    # The 250 ms loss is repaired with silence, so the timeline (measured
    # after the fill) sees a continuous track.
    assert rec.gap_fills == 1
    assert rec.gap_fill_ms == 250
    assert abs(rec.timeline["max_jump_ms"]) <= 20
    assert rec.arrival_timeline["observations"] == 3


class _Timing:
    def __init__(self, adc):
        self.inputBufferAdcTime = adc
        self.currentTime = adc + 0.01 if isinstance(adc, (int, float)) else None


def _wav_samples(path):
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def test_skipped_mic_cycle_is_filled_with_silence(spool_dir, fake_stream):
    rec = MeetingRecorder()
    rec.start()
    rec._on_audio(_block(), 1600, _Timing(50.0), None)
    rec._on_audio(_block(), 1600, _Timing(50.1), None)
    # 258 ms of input lost: the ADC clock jumps, the sample count does not.
    rec._on_audio(_block(), 1600, _Timing(50.458), None)
    path = rec.stop()

    samples = _wav_samples(path)
    assert len(samples) == 3 * 1600 + 4128
    assert (samples[:3200] == 1000).all()
    assert (samples[3200:3200 + 4128] == 0).all()
    assert (samples[3200 + 4128:] == 1000).all()
    assert rec.gap_fills == 1
    assert rec.gap_fill_ms == 258
    assert abs(rec.timeline["error_ms"]) <= 20
    path.unlink()


def test_gap_fill_reaches_asr_chunks_at_the_same_position(
    spool_dir, fake_stream, monkeypatch
):
    monkeypatch.setattr(config, "MEETING_CHUNK_SECONDS", 1.0)
    monkeypatch.setattr(config, "MEETING_OVERLAP_SECONDS", 0.0)
    session = MeetingASRSession(max_chunks=8)
    rec = MeetingRecorder()
    rec.configure_pretranscription(session)
    rec.start()
    rec._on_audio(_block(), 1600, _Timing(50.0), None)
    rec._on_audio(_block(), 1600, _Timing(50.358), None)  # 258 ms lost
    path = rec.stop()

    chunks = []
    while True:
        try:
            chunks.append(session.get(timeout=0))
        except Exception:
            break
    pcm = b"".join(p for _, p in chunks)
    assert chunks[0][0] == 0
    samples = np.frombuffer(pcm, dtype=np.int16)
    assert len(samples) == 2 * 1600 + 4128
    assert (samples[:1600] == 1000).all()
    assert (samples[1600:1600 + 4128] == 0).all()
    assert (samples[1600 + 4128:] == 1000).all()
    path.unlink()


@pytest.mark.parametrize(
    "second_adc, expected_fills",
    [
        (50.1 + 0.019, 0),   # below the 20 ms threshold
        (50.1 + 0.021, 1),   # just above it
        (50.05, 0),          # ADC earlier than expected: never acts
        (None, 0),           # missing ADC time: no repair
        (0, 0),              # invalid ADC time: no repair
    ],
)
def test_gap_fill_threshold_and_invalid_clocks(
    spool_dir, fake_stream, second_adc, expected_fills
):
    rec = MeetingRecorder()
    rec.start()
    rec._on_audio(_block(), 1600, _Timing(50.0), None)
    rec._on_audio(_block(), 1600, _Timing(second_adc), None)
    path = rec.stop()

    assert rec.gap_fills == expected_fills
    samples = _wav_samples(path)
    if expected_fills == 0:
        assert len(samples) == 3200
        assert rec.gap_fill_ms == 0
    else:
        assert len(samples) > 3200
    path.unlink()


def test_gap_fill_never_uses_arrival_time(spool_dir, fake_stream, monkeypatch):
    # A late-but-complete callback is not lost audio.
    ticks = iter([1_000_000_000, 9_000_000_000])
    monkeypatch.setattr(
        meeting_recorder.monotonic_time, "monotonic_ns", lambda: next(ticks)
    )
    rec = MeetingRecorder()
    rec.start()
    rec._on_audio(_block(), 1600, None, None)
    rec._on_audio(_block(), 1600, None, None)
    path = rec.stop()
    assert rec.gap_fills == 0
    path.unlink()


def test_gap_fill_state_resets_on_start(spool_dir, fake_stream):
    rec = MeetingRecorder()
    rec.start()
    rec._on_audio(_block(), 1600, _Timing(50.0), None)
    rec._on_audio(_block(), 1600, _Timing(50.358), None)
    rec.stop().unlink()
    assert rec.gap_fills == 1

    rec.start()
    assert rec.gap_fills == 0 and rec.gap_fill_ms == 0
    # New recording, new ADC origin: a distant first time is not a gap.
    rec._on_audio(_block(), 1600, _Timing(900.0), None)
    path = rec.stop()
    assert rec.gap_fills == 0
    path.unlink()


def test_gap_fill_queue_full_is_retried_on_the_next_block(spool_dir, fake_stream):
    rec = MeetingRecorder()
    rec.start()
    real_queue = rec._queue

    class FlakyQueue:
        failed = False

        def put_nowait(self, item):
            if isinstance(item, int) and not self.failed:
                self.failed = True
                raise meeting_recorder.queue.Full
            real_queue.put_nowait(item)

        def qsize(self):
            return real_queue.qsize()

    flaky = FlakyQueue()
    rec._on_audio(_block(), 1600, _Timing(50.0), None)
    rec._queue = flaky
    # Fill hits Full; the block itself is queued normally.
    rec._on_audio(_block(), 1600, _Timing(50.358), None)
    assert flaky.failed
    assert rec.gap_fills == 0
    assert rec.gap_fill_ms == 0
    assert rec.elapsed_seconds == pytest.approx(0.2)

    # frames did not move for the failed fill, so the same 258 ms gap is
    # still owed and is retried here.
    rec._on_audio(_block(), 1600, _Timing(50.458), None)
    rec._queue = real_queue
    path = rec.stop()

    assert rec.gap_fills == 1
    assert rec.gap_fill_ms == 258
    samples = _wav_samples(path)
    assert len(samples) == 3 * 1600 + 4128
    assert (samples[3200:3200 + 4128] == 0).all()
    path.unlink()
