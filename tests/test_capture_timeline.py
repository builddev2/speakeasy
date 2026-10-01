"""CaptureTimeline: per-track clock-vs-sample-count error, numbers only."""

from speakeasy.capture_timeline import CaptureTimeline

RATE = 16_000
BLOCK = 1_600  # 0.1 s


def _feed(timeline, seconds, *, start_frames=0, start_time=100.0, jitter=None):
    frames = start_frames
    for index in range(int(seconds * 10)):
        t = start_time + frames / RATE
        if jitter is not None:
            t += jitter(index)
        timeline.observe(t, frames)
        frames += BLOCK
    return frames


def test_continuous_track_has_zero_error():
    timeline = CaptureTimeline(RATE)
    _feed(timeline, 90)
    summary = timeline.summary()
    assert summary["error_ms"] == 0
    assert summary["min_ms"] == 0
    assert summary["max_ms"] == 0
    assert summary["step_ms"] == 0
    assert summary["series_ms"] == [0, 0, 0]
    assert summary["max_jump_ms"] == 0
    assert summary["observations"] == 900


def test_lost_samples_show_as_positive_step_at_their_time():
    timeline = CaptureTimeline(RATE)
    frames = 0
    for index in range(1200):  # 120 s of stream position
        t = 100.0 + frames / RATE + (0.25 if index >= 700 else 0.0)
        timeline.observe(t, frames)
        frames += BLOCK
    summary = timeline.summary()
    # 0.25 s of audio missing after stream position 70 s: later buffers
    # arrive 250 ms after their sample count says they should.
    assert summary["error_ms"] == 250
    assert summary["min_ms"] == 0
    assert summary["max_ms"] == 250
    # Window minima: the 60-90 s window still holds its pre-step samples.
    assert summary["series_ms"] == [0, 0, 0, 250]
    assert summary["step_ms"] == 250
    assert summary["step_at_s"] == 90.0
    # The single largest buffer-to-buffer jump pins it to the buffer.
    assert summary["max_jump_ms"] == 250
    assert summary["max_jump_at_s"] == 70.0


def test_window_minimum_removes_positive_scheduling_jitter():
    timeline = CaptureTimeline(RATE)
    # Callbacks can only be late, never early; the window minimum is the
    # true offset.
    _feed(timeline, 60, jitter=lambda i: 0.04 if i % 3 else 0.0)
    summary = timeline.summary()
    assert summary["series_ms"] == [0, 0]
    assert summary["step_ms"] == 0
    assert summary["max_jitter_ms"] == 40


def test_clock_drift_ramps_steadily():
    timeline = CaptureTimeline(RATE)
    frames = 0
    for _ in range(3000):  # 300 s
        t = 100.0 + (frames / RATE) * (1 + 200e-6)
        timeline.observe(t, frames)
        frames += BLOCK
    summary = timeline.summary()
    assert summary["error_ms"] == 54  # window starting at 270 s: 270 * 200 ppm
    assert summary["series_ms"][:3] == [0, 6, 12]
    assert summary["step_ms"] == 6


def test_first_observation_may_start_mid_stream():
    timeline = CaptureTimeline(RATE)
    _feed(timeline, 10, start_frames=RATE * 5)
    assert timeline.summary()["error_ms"] == 0


def test_series_is_capped():
    timeline = CaptureTimeline(RATE, window_seconds=1.0, max_windows=4)
    _feed(timeline, 10)
    assert len(timeline.summary()["series_ms"]) == 4


def test_empty_summary():
    summary = CaptureTimeline(RATE).summary()
    assert summary["observations"] == 0
    assert summary["series_ms"] == []
    assert summary["error_ms"] is None


def test_timeline_fields_survive_the_capture_health_whitelist():
    from speakeasy.meetings import filter_capture_health

    health = {
        "mic_input_overflows": 2,
        "mic_gap_fills": 1,
        "mic_gap_fill_ms": 258,
        "mic_timeline": {"error_ms": 250},
        "mic_arrival_timeline": {"error_ms": 251},
        "system_timeline": {"error_ms": 0},
        "system_sample_timeline": {"error_ms": 0},
        "device_name": "never persisted",
    }
    rejected = {
        "mic_timeline": {"error_ms": "a name"},
        "system_timeline": {"series_ms": ["a name"]},
        "mic_input_overflows": {"error_ms": 1},  # not a timeline key
    }
    expected = dict(health)
    del expected["device_name"]
    assert filter_capture_health(health) == expected
    assert filter_capture_health(rejected) == {}
