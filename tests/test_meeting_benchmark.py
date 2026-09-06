"""Privacy-safe meeting phase telemetry."""

import json

from speakeasy import meeting_benchmark as benchmark


def test_meeting_record_is_exact_allowlist_and_contains_no_content(tmp_path):
    clock = iter(range(0, 100_000_000, 1_000_000))
    timing = benchmark.MeetingTiming(clock_ns=lambda: next(clock))
    timing.capture_mode = "mic_and_system"
    timing.mic_frames = 16_000
    timing.system_frames = 32_000
    for phase in (
        "stop",
        "mic_asr",
        "system_asr",
        "diarization",
        "voice_identification",
        "alignment",
        "save",
    ):
        timing.start(phase)
        timing.finish(phase)

    path = tmp_path / "meeting.jsonl"
    timing.emit("success", path=path)

    record = json.loads(path.read_text(encoding="utf-8"))
    assert list(record) == list(benchmark.FIELDS)
    assert record["capture_mode"] == "mic_and_system"
    assert record["mic_frames"] == 16_000
    assert record["system_frames"] == 32_000
    assert "private transcript" not in json.dumps(record)


def test_invalid_values_are_not_persisted(tmp_path):
    path = tmp_path / "meeting.jsonl"
    timing = benchmark.MeetingTiming(clock_ns=lambda: 0)
    timing.capture_mode = "private application"
    timing.emit("success", path=path)

    assert benchmark.read_records(path) == []
