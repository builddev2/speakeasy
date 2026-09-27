from datetime import datetime, timedelta, timezone

from speakeasy.meeting_export import export_all, render_export_md, safe_filename
from speakeasy.meeting_library import MeetingLibrary, NewMeeting
from speakeasy.meetings import MeetingSegment

EDT = timezone(timedelta(hours=-4))


def test_safe_filename():
    assert safe_filename("1:1 Refayet / VFA", "x") == "1-1 Refayet - VFA"
    assert safe_filename("  ...  ", "20260924-131723-abcd") == "20260924-131723-abcd"
    assert safe_filename("Stand-up 🚀", "x") == "Stand-up 🚀"
    assert len(safe_filename("a" * 300, "x")) == 120


def test_export_writes_notes_and_transcript(tmp_path, library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 65.0, 70.0, "hello")], duration_seconds=1380,
        started_at=datetime(2026, 9, 24, 13, 17, tzinfo=EDT), title="1:1 / Refayet"))
    lib.save_notes(mid, summary="Invest in VFA.", action_items=["Estimate"], tags=["VFA"])
    text = render_export_md(lib.get_meeting(mid))
    assert text.startswith("# 1:1 / Refayet\n")
    assert "## Summary\n\nInvest in VFA." in text
    assert "- [ ] Estimate" in text and "Tags: VFA" in text
    assert "**You** [00:01:05]: hello" in text
    out = tmp_path / "export"
    assert export_all(out) == 1
    assert (out / "2026-09-24 1-1 - Refayet.md").read_text(encoding="utf-8") == text


def test_export_disambiguates_same_day_titles(tmp_path, library_path):
    # Controller ruling: two same-title, same-day meetings must not
    # overwrite each other's export file; the second gets a numeric suffix.
    lib = MeetingLibrary()
    mid1 = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0.0, 1.0, "first meeting")], duration_seconds=60,
        started_at=datetime(2026, 9, 24, 9, 0, tzinfo=EDT), title="Standup"))
    mid2 = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0.0, 1.0, "second meeting")], duration_seconds=60,
        started_at=datetime(2026, 9, 24, 15, 0, tzinfo=EDT), title="Standup"))

    out = tmp_path / "export"
    assert export_all(out) == 2

    first = out / "2026-09-24 Standup.md"
    second = out / "2026-09-24 Standup (2).md"
    assert first.exists() and second.exists()

    first_text = first.read_text(encoding="utf-8")
    second_text = second.read_text(encoding="utf-8")
    assert first_text != second_text
    assert ("first meeting" in first_text) != ("first meeting" in second_text)
    assert ("second meeting" in first_text) != ("second meeting" in second_text)
