"""Legacy per-meeting JSON parsing (Meeting.load, for meeting_import.py) and
rendering (render_txt/render_md). The live store is MeetingLibrary
(test_meeting_library.py); Meeting no longer writes JSON — new/save/rename/
delete/relabel_speaker and the module-level list_meetings() were removed
since nothing calls them any more."""

import pytest

from speakeasy import meetings
from speakeasy.meetings import Meeting, MeetingSegment


def test_load_rejects_path_like_ids(meetings_dir):
    with pytest.raises(ValueError):
        Meeting.load("../evil")
    with pytest.raises(ValueError):
        Meeting.load("not-a-meeting-id")


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


def _sentence(start, end, text):
    tok = type("Tok", (), {"start": start, "end": end, "duration": end - start, "text": " " + text})
    return type("S", (), {"start": start, "end": end, "text": text, "tokens": [tok]})()


def test_diarised_segments_split_at_sixty_seconds():
    sentences = [_sentence(0, 25, "a"), _sentence(26, 50, "b"), _sentence(51, 75, "c")]
    segments = meetings.align_speakers(sentences, [(0.0, 80.0, 0)])
    assert [(s.start, s.end) for s in segments] == [(0, 50), (51, 75)]
