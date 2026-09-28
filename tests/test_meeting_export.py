from datetime import datetime, timedelta, timezone

from speakeasy.meeting_export import export_all, render_export_md, safe_filename
from speakeasy.meeting_library import (
    MeetingLibrary,
    MeetingNotFound,
    MeetingSummary,
    NewMeeting,
    StoredMeeting,
)
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
    # overwrite each other's export file within the same run; the second
    # gets a numeric suffix (tracked in-memory for this run, not by probing
    # the filesystem -- see test_export_rerun_overwrites_without_suffixes).
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


def test_export_rerun_overwrites_without_suffixes(tmp_path, library_path):
    # A second export into the same folder must overwrite each meeting's own
    # file in place, not pile up "(2)" copies of everything on every rerun.
    lib = MeetingLibrary()
    lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0.0, 1.0, "hello")], duration_seconds=60,
        started_at=datetime(2026, 9, 24, 9, 0, tzinfo=EDT), title="Standup"))

    out = tmp_path / "export"
    assert export_all(out) == 1
    assert export_all(out) == 1

    names = sorted(p.name for p in out.iterdir())
    assert names == ["2026-09-24 Standup.md"]


def test_export_people_and_approximate_timestamps(tmp_path, library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0.0, 1.0, "hello")], duration_seconds=60,
        started_at=datetime(2026, 9, 24, 9, 0, tzinfo=EDT), title="Imported",
        timestamps_approximate=True))
    lib.link_people(mid, [("Alice", None, "attendee"), ("Bob", None, "attendee")])

    text = render_export_md(lib.get_meeting(mid))
    assert "People: Alice, Bob" in text
    assert "_Timestamps are approximate (imported meeting)._" in text
    assert "## Transcript" in text


class _FakeLibrary:
    """A minimal stand-in for MeetingLibrary that never touches SQLite, so
    a 501-meeting paging test runs instantly instead of writing 501 rows."""

    def __init__(self, total):
        self.total = total
        self.deleted_id = None

    def list_meetings(self, *, limit, offset):
        remaining = self.total - offset
        if remaining <= 0:
            return []
        n = min(limit, remaining)
        return [
            MeetingSummary(
                meeting_id=f"m{offset + i}", title=f"Meeting {offset + i}",
                started_at="2026-09-24T13:00:00Z", tz_offset_minutes=-240,
                duration_seconds=60, speaker_count=1, has_summary=False,
                timestamps_approximate=False, tags=[], people=[],
            )
            for i in range(n)
        ]

    def get_meeting(self, meeting_id):
        if meeting_id == self.deleted_id:
            raise MeetingNotFound(meeting_id)
        return StoredMeeting(
            meeting_id=meeting_id, title=f"Meeting {meeting_id[1:]}",
            started_at="2026-09-24T13:00:00Z", tz_offset_minutes=-240,
            duration_seconds=60, segments=[MeetingSegment("You", 0.0, 1.0, "hi")],
            capture_mode="mic_only", system_audio_status="unavailable",
            capture_scope="mic_only", track_offsets_seconds={"mic": 0.0},
            capture_health={}, calendar_event_id=None, source="recorded",
            timestamps_approximate=False, notes=None, tags=[], people=[],
        )


def test_export_pages_past_500_meetings(tmp_path):
    # list_meetings pages in batches of (at most) 500; export_all must keep
    # advancing the offset instead of stopping after the first page, or a
    # library with more than 500 meetings would silently lose backups.
    fake = _FakeLibrary(total=501)
    out = tmp_path / "export"
    assert export_all(out, library=fake) == 501
    assert len(list(out.iterdir())) == 501


def test_export_skips_meeting_deleted_between_list_and_get(tmp_path):
    # get_meeting can raise MeetingNotFound for a meeting that was deleted
    # after list_meetings returned its summary; the export must skip it
    # rather than crash and lose every meeting after it in the batch.
    fake = _FakeLibrary(total=3)
    fake.deleted_id = "m1"
    out = tmp_path / "export"
    assert export_all(out, library=fake) == 2
    names = sorted(p.name for p in out.iterdir())
    assert names == ["2026-09-24 Meeting 0.md", "2026-09-24 Meeting 2.md"]
