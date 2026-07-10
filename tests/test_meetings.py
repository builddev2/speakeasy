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
    meeting = Meeting.new(_segments(), duration_seconds=1832.5)
    meeting.save()

    loaded = Meeting.load(meeting.meeting_id)
    assert loaded.title == meeting.title
    assert loaded.duration_seconds == 1832.5
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
    assert set(data) == {"id", "title", "created", "duration_seconds", "segments"}
    assert data["segments"][0]["speaker"] == "Speaker 1"
