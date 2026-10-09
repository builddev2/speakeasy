import unicodedata
import pytest
from datetime import datetime, timedelta, timezone

from speakeasy.meeting_export import export_all, export_snapshot, render_export_md, safe_filename
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


def test_export_snapshot_publishes_unique_folder(tmp_path, library_path):
    lib = MeetingLibrary()
    lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0.0, 1.0, "snapshot text")],
        duration_seconds=60, started_at=datetime(2026, 9, 24, 9, tzinfo=EDT), title="Snapshot"))
    first, count = export_snapshot(tmp_path, lib)
    second, count_again = export_snapshot(tmp_path, lib)
    assert count == count_again == 1
    assert first != second
    assert "snapshot text" in next(first.glob("*.md")).read_text()
    assert not list(tmp_path.glob(".Speakeasy-export-*"))


def test_export_snapshot_failure_does_not_publish_partial_folder(tmp_path, library_path, monkeypatch):
    import speakeasy.meeting_export as module

    def fail(folder, library):
        (folder / "partial.md").write_text("partial")
        raise OSError("disk full")

    monkeypatch.setattr(module, "export_all", fail)
    with pytest.raises(OSError, match="disk full"):
        export_snapshot(tmp_path, MeetingLibrary())
    assert not list(tmp_path.glob(".Speakeasy-export-*"))


def test_export_snapshot_includes_more_than_one_page_of_real_meetings(tmp_path, library_path):
    lib = MeetingLibrary(library_path)
    for index in range(501):
        lib.save_meeting(NewMeeting(
            segments=[MeetingSegment("You", 0.0, 1.0, f"meeting {index}")],
            duration_seconds=60, started_at=datetime(2026, 9, 24, 9, tzinfo=EDT),
            title=f"Meeting {index}", meeting_id=f"20260924-090000-{index:04x}"))
    folder, count = export_snapshot(tmp_path, lib)
    assert count == 501
    assert len(list(folder.glob("*.md"))) == 501


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


def test_export_disambiguates_case_insensitive_collisions(tmp_path, library_path):
    # macOS's default filesystem (APFS) is case-insensitive, so "Standup"
    # and "standup" name the SAME file on disk even though they're distinct
    # Python strings; the disambiguation logic must treat them as a
    # collision too, or the second export silently clobbers the first while
    # export_all still reports 2 meetings exported.
    lib = MeetingLibrary()
    lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0.0, 1.0, "first meeting")], duration_seconds=60,
        started_at=datetime(2026, 9, 24, 9, 0, tzinfo=EDT), title="Standup"))
    lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0.0, 1.0, "second meeting")], duration_seconds=60,
        started_at=datetime(2026, 9, 24, 15, 0, tzinfo=EDT), title="standup"))

    out = tmp_path / "export"
    assert export_all(out) == 2

    # Assert against the actual files on disk, not just the return value --
    # a silent overwrite would still report 2 while leaving only one file.
    files = list(out.iterdir())
    assert len(files) == 2
    contents = [p.read_text(encoding="utf-8") for p in files]
    assert any("first meeting" in c for c in contents)
    assert any("second meeting" in c for c in contents)


def test_export_disambiguates_unicode_normalisation_collisions(tmp_path, library_path):
    # APFS also compares filenames after Unicode normalisation, so an NFC
    # "café" (single precomposed U+00E9) and an NFD "café" (e + combining
    # acute, U+0065 U+0301) name the same file on disk despite being
    # different Python strings.
    nfc_title = unicodedata.normalize("NFC", "café")
    nfd_title = unicodedata.normalize("NFD", "café")
    assert nfc_title != nfd_title  # sanity: genuinely different strings

    lib = MeetingLibrary()
    lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0.0, 1.0, "nfc meeting")], duration_seconds=60,
        started_at=datetime(2026, 9, 24, 9, 0, tzinfo=EDT), title=nfc_title))
    lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0.0, 1.0, "nfd meeting")], duration_seconds=60,
        started_at=datetime(2026, 9, 24, 15, 0, tzinfo=EDT), title=nfd_title))

    out = tmp_path / "export"
    assert export_all(out) == 2

    files = list(out.iterdir())
    assert len(files) == 2
    contents = [p.read_text(encoding="utf-8") for p in files]
    assert any("nfc meeting" in c for c in contents)
    assert any("nfd meeting" in c for c in contents)


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

    def list_action_items(self, **kw):
        return []

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


def test_format_elapsed():
    from speakeasy.meeting_export import format_elapsed
    assert [format_elapsed(s) for s in (3, 760, 3735)] == ["0:03", "12:40", "1:02:15"]


def test_export_puts_my_notes_after_the_summary_with_stamps(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0, 4, "hi there")], duration_seconds=900,
        started_at=datetime(2026, 10, 2, 9, 0, tzinfo=EDT), title="Planning"))
    lib.save_notes(mid, summary="TL;DR: planned.")
    lib.set_user_notes(mid, "## Plan\n- [ ] send deck\nplain", [(1, 760.0)])
    md = render_export_md(lib.get_meeting(mid))
    assert md.index("## Summary") < md.index("## My notes") < md.index("## Transcript")
    assert "## Plan\n- [ ] [12:40] send deck\nplain" in md


def test_export_renders_item_status_owner_due_priority_notes(library_path):
    from speakeasy.meeting_export import format_item_md
    from tests.test_library_backup import _meeting
    lib = MeetingLibrary(library_path)
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=[
        {"task": "Send budget draft", "owner": "Jason", "due_date": "2026-10-16",
         "due_phrase": "by Friday", "priority": "high"}, "Plain"])
    first, second = lib.list_action_items(meeting_id=mid)
    lib.update_action_item(first.id, updated_by="user", status="done", notes="line one\nline two")
    assert format_item_md(lib.get_action_item(first.id)) == [
        '- [x] Send budget draft — Jason · due Fri 16 Oct 2026 ("by Friday") · high',
        "  Notes: line one", "  line two"]
    assert format_item_md(second) == ["- [ ] Plain"]


def test_export_all_writes_action_items_file(tmp_path, library_path):
    from tests.test_library_backup import _meeting
    lib = MeetingLibrary(library_path)
    mid = _meeting(lib, "Weekly")
    lib.save_notes(mid, summary="s", action_items=["Open one"])
    done = lib.create_action_item(task="Manual done")
    lib.update_action_item(done.id, updated_by="user", status="done")
    count = export_all(tmp_path / "out", lib)
    text = (tmp_path / "out" / "Action items.md").read_text()
    assert count == 1                                         # still counts meetings only
    assert "## Open" in text and "- [ ] Open one" in text and "Weekly" in text
    assert "  From: Weekly (2026-09-30)" in text
    assert "## Completed" in text and "- [x] Manual done" in text


def test_export_without_items_writes_no_action_items_file(tmp_path, library_path):
    from tests.test_library_backup import _meeting
    lib = MeetingLibrary(library_path)
    _meeting(lib, "Quiet")
    export_all(tmp_path / "out", lib)
    assert not (tmp_path / "out" / "Action items.md").exists()


def test_export_excludes_soft_deleted_items(tmp_path, library_path):
    from tests.test_library_backup import _meeting
    lib = MeetingLibrary(library_path)
    mid = _meeting(lib, "Weekly")
    lib.save_notes(mid, summary="s", action_items=["Keep me", "Drop me"])
    keep, drop = lib.list_action_items(meeting_id=mid)
    lib.delete_action_item(drop.id, updated_by="user")
    export_all(tmp_path / "out", lib)
    assert "Keep me" in (tmp_path / "out" / "Action items.md").read_text()
    for f in (tmp_path / "out").iterdir():
        assert "Drop me" not in f.read_text(encoding="utf-8")


def test_per_meeting_section_formats_status_due_and_notes(library_path):
    from tests.test_library_backup import _meeting
    lib = MeetingLibrary(library_path)
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=[
        {"task": "Send draft", "owner": "Jason", "due_date": "2026-10-16",
         "due_phrase": "by Friday", "priority": "high"}])
    (item,) = lib.list_action_items(meeting_id=mid)
    lib.update_action_item(item.id, updated_by="user", status="done", notes="a\nb")
    md = render_export_md(lib.get_meeting(mid))
    assert ('## Action items\n\n- [x] Send draft — Jason · due Fri 16 Oct 2026 '
            '("by Friday") · high\n  Notes: a\n  b\n') in md
