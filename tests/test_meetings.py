"""Meeting store: JSON round-trips, listing, rename/delete, rendering."""

import json

import pytest

from speakeasy import meetings
from speakeasy.meetings import Meeting, MeetingSegment


def _segments():
    return [
        MeetingSegment("Speaker 1", 0.0, 12.5, "Good morning everyone."),
        MeetingSegment("Speaker 2", 13.1, 30.0, "Thanks for the introduction."),
    ]


def test_save_load_round_trip(meetings_dir):
    meeting = Meeting.new(
        _segments(),
        duration_seconds=1832.5,
        expected_remote_speaker_count=1,
    )
    meeting.save()

    loaded = Meeting.load(meeting.meeting_id)
    assert loaded.title == meeting.title
    assert loaded.duration_seconds == 1832.5
    assert loaded.expected_remote_speaker_count == 1
    assert [s.speaker for s in loaded.segments] == ["Speaker 1", "Speaker 2"]
    assert loaded.segments[0].text == "Good morning everyone."
    assert not list(meetings_dir.glob("*.tmp"))  # atomic write left no crumbs


def test_load_rejects_path_like_ids(meetings_dir):
    with pytest.raises(ValueError):
        Meeting.load("../evil")
    with pytest.raises(ValueError):
        Meeting.load("not-a-meeting-id")


def test_list_meetings_newest_first_skips_junk(meetings_dir):
    a = Meeting("20260101-090000-aaaa", "Old", "2026-01-01T09:00:00", 60, [])
    b = Meeting("20260708-140000-bbbb", "New", "2026-07-08T14:00:00", 60, [])
    a.save()
    b.save()
    (meetings_dir / "20260301-120000-cccc.json").write_text("{ not json")
    (meetings_dir / "random-notes.json").write_text("{}")

    listed = meetings.list_meetings()
    assert [m.title for m in listed] == ["New", "Old"]


def test_rename_persists_without_moving_file(meetings_dir):
    meeting = Meeting.new(_segments(), duration_seconds=60)
    meeting.save()
    path_before = meeting.path

    meeting.rename("Quarterly review")
    assert meeting.path == path_before
    assert Meeting.load(meeting.meeting_id).title == "Quarterly review"
    with pytest.raises(ValueError):
        meeting.rename("   ")


def test_delete(meetings_dir):
    meeting = Meeting.new([], duration_seconds=5)
    meeting.save()
    meeting.delete()
    assert not meeting.path.exists()
    meeting.delete()  # idempotent


def test_render_txt(meetings_dir):
    meeting = Meeting(
        "20260708-143212-a3f9",
        "Standup",
        "2026-07-08T14:32:12",
        3725.0,
        [
            MeetingSegment("Speaker 1", 0.4, 12.9, "Hello."),
            MeetingSegment("Speaker 2", 3661.0, 3700.0, "Goodbye."),
        ],
    )
    text = meetings.render_txt(meeting)
    assert text == (
        "Standup\n"
        "2026-07-08T14:32:12  ·  1 h 2 min\n"
        "\n"
        "[00:00:00] Speaker 1: Hello.\n"
        "[01:01:01] Speaker 2: Goodbye.\n"
    )


def test_render_md(meetings_dir):
    meeting = Meeting(
        "20260708-143212-a3f9",
        "Standup",
        "2026-07-08T14:32:12",
        180.0,
        [MeetingSegment("Speaker 1", 0.0, 10.0, "Hello.")],
    )
    md = meetings.render_md(meeting)
    assert md.startswith("# Standup\n")
    assert "*2026-07-08T14:32:12  ·  3 min*" in md
    assert "**Speaker 1** [00:00:00]: Hello." in md


def test_saved_json_is_hand_editable(meetings_dir):
    meeting = Meeting.new(_segments(), duration_seconds=60)
    meeting.save()
    data = json.loads(meeting.path.read_text())
    assert set(data) == {
        "id",
        "title",
        "created",
        "duration_seconds",
        "expected_remote_speaker_count",
        "capture_mode",
        "capture_health",
        "capture_scope",
        "system_audio_status",
        "track_offsets_seconds",
        "segments",
    }
    assert data["segments"][0]["speaker"] == "Speaker 1"


def test_capture_health_persists_only_privacy_safe_whitelisted_fields(meetings_dir):
    meeting = Meeting.new(
        _segments(),
        duration_seconds=60,
        capture_health={
            "capture_mode": "mic_and_system",
            "capture_scope": "selected",
            "system_nonzero_signal": True,
            "system_dropped_frames": 4,
            "meeting_name": "Private planning call",
            "application_name": "Private App",
            "device_name": "Private Headset",
            "transcript": "private words",
        },
    )
    meeting.save()

    raw = json.loads(meeting.path.read_text())
    assert raw["capture_health"] == {
        "capture_mode": "mic_and_system",
        "capture_scope": "selected",
        "system_nonzero_signal": True,
        "system_dropped_frames": 4,
    }
    assert Meeting.load(meeting.meeting_id).capture_health == raw["capture_health"]


def test_optional_attribution_metadata_round_trips(meetings_dir):
    meeting = Meeting.new(
        [MeetingSegment("Alice", 0, 1, "Hi", 0.91, True, "Alice", 7)], 1
    )
    meeting.save()
    segment = Meeting.load(meeting.meeting_id).segments[0]
    assert segment.confidence == pytest.approx(0.91)
    assert (segment.overlap, segment.profile_id, segment.cluster_id) == (True, "Alice", 7)


def test_capture_provenance_round_trips_without_audio_paths(meetings_dir):
    meeting = Meeting.new(
        _segments(),
        60,
        capture_mode="mic_and_system",
        system_audio_status="captured",
        track_offsets_seconds={"mic": 0.0, "system": 0.31234567},
        capture_scope="global",
    )
    meeting.save()
    raw = json.loads(meeting.path.read_text())
    assert raw["capture_mode"] == "mic_and_system"
    assert raw["capture_scope"] == "global"
    assert raw["track_offsets_seconds"] == {"mic": 0.0, "system": 0.312346}
    assert "path" not in json.dumps(raw).lower()
    loaded = Meeting.load(meeting.meeting_id)
    assert loaded.system_audio_status == "captured"
    assert loaded.capture_scope == "global"


def test_relabel_one_or_all_matching_segments(meetings_dir):
    meeting = Meeting.new(
        [
            MeetingSegment("Speaker 1", 0, 10, "Hello", 0.9, False, "Alice", 1),
            MeetingSegment("Speaker 2", 11, 30, "Hi", 0.8, False, "Bob", 2),
            MeetingSegment("Speaker 1", 31, 32, "Again", 0.9, False, "Alice", 1),
        ],
        32,
    )
    meeting.save()
    meeting.relabel_speaker(0, "Alice")
    assert [s.speaker for s in meeting.segments] == ["Alice", "Speaker 2", "Speaker 1"]
    assert meeting.segments[0].profile_id is None
    assert meeting.segments[0].confidence is None
    meeting.relabel_speaker(2, "Bob", all_matching=True)
    stored = Meeting.load(meeting.meeting_id).segments[2]
    assert stored.speaker == "Bob"
    assert stored.profile_id is None
    assert stored.confidence is None
