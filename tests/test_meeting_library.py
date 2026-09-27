import threading
from datetime import datetime, timedelta, timezone

import pytest

from speakeasy import meeting_store
from speakeasy.meeting_library import (
    MeetingLibrary, MeetingNotFound, NewMeeting,
)
from speakeasy.meetings import MeetingSegment, render_txt

EDT = timezone(timedelta(hours=-4))


def _segs(*items):
    return [MeetingSegment(sp, s, e, t) for sp, s, e, t in items]


def _new(started=datetime(2026, 9, 24, 13, 17, 23, tzinfo=EDT), **kw):
    kw.setdefault("segments", _segs(("You", 0.0, 4.0, "hello there"),
                                    ("Speaker 1", 4.5, 9.0, "hi Jason")))
    kw.setdefault("duration_seconds", 1380.0)
    return NewMeeting(started_at=started, **kw)


def test_save_and_get_round_trip_keeps_local_start(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new(capture_health={"mic_first_buffer": True, "pid": 7}))
    assert mid.startswith("20260924-131723-")
    m = lib.get_meeting(mid)
    assert m.started_at == "2026-09-24T17:17:23Z"
    assert m.tz_offset_minutes == -240
    assert m.local_start == datetime(2026, 9, 24, 13, 17, 23, tzinfo=EDT)
    assert m.title == "Meeting — Sep 24, 1:17 PM"
    assert [s.text for s in m.segments] == ["hello there", "hi Jason"]
    assert m.capture_health == {"mic_first_buffer": True}  # whitelist kept
    assert m.created == "2026-09-24T13:17:23"
    assert render_txt(m).startswith("Meeting — Sep 24, 1:17 PM\n2026-09-24T13:17:23")


def test_naive_start_is_rejected(library_path):
    with pytest.raises(ValueError, match="timezone"):
        MeetingLibrary().save_meeting(_new(started=datetime(2026, 9, 24, 13, 0)))


def test_get_missing_and_invalid_ids(library_path):
    lib = MeetingLibrary()
    with pytest.raises(MeetingNotFound):
        lib.get_meeting("20260101-000000-0000")
    with pytest.raises(ValueError):
        lib.get_meeting("../etc/passwd")


def test_list_newest_first_with_speaker_count(library_path):
    lib = MeetingLibrary()
    old = lib.save_meeting(_new(started=datetime(2026, 9, 1, 9, 0, tzinfo=EDT)))
    new = lib.save_meeting(_new())
    rows = lib.list_meetings()
    assert [r.meeting_id for r in rows] == [new, old]
    assert rows[0].speaker_count == 2 and rows[0].has_summary is False


def test_list_date_filter_uses_local_day(library_path):
    # 23:50 local on Sep 24 is Sep 25 in UTC on this (EDT) Mac; it must
    # still count as Sep 24. Built from the machine's zone rather than by
    # changing TZ, which would leak into later tests. This assertion only
    # exercises the day-boundary crossing on a Mac whose local timezone is
    # not UTC (true for this dev machine); on a UTC machine it still
    # passes, just without crossing midnight.
    lib = MeetingLibrary()
    late = lib.save_meeting(_new(started=datetime(2026, 9, 24, 23, 50).astimezone()))
    assert [r.meeting_id for r in lib.list_meetings(from_date="2026-09-24", to_date="2026-09-24")] == [late]
    assert lib.list_meetings(from_date="2026-09-25") == []


def test_rename_and_relabel(library_path):
    lib = MeetingLibrary()
    segs = _segs(("Speaker 1", 0, 1, "a"), ("Speaker 1", 2, 3, "b"), ("Speaker 2", 4, 5, "c"))
    segs[0].confidence, segs[0].profile_id = 0.9, "Refayet"
    mid = lib.save_meeting(_new(segments=segs))
    lib.rename(mid, "  1:1 Refayet ")
    with pytest.raises(ValueError):
        lib.rename(mid, "   ")
    lib.relabel_speaker(mid, 0, "Refayet")
    m = lib.get_meeting(mid)
    assert m.title == "1:1 Refayet"
    assert [s.speaker for s in m.segments] == ["Refayet", "Speaker 1", "Speaker 2"]
    assert m.segments[0].confidence is None and m.segments[0].profile_id is None
    lib.relabel_speaker(mid, 1, "Refayet", all_matching=True)
    assert [s.speaker for s in lib.get_meeting(mid).segments] == ["Refayet", "Refayet", "Speaker 2"]
    with pytest.raises(IndexError):
        lib.relabel_speaker(mid, 9, "x")
    with pytest.raises(ValueError):
        lib.relabel_speaker(mid, 0, "  ")


def test_rename_relabel_and_transcript_page_raise_for_missing_meeting(library_path):
    lib = MeetingLibrary()
    missing = "20260101-000000-0000"
    with pytest.raises(MeetingNotFound):
        lib.rename(missing, "New title")
    with pytest.raises(MeetingNotFound):
        lib.relabel_speaker(missing, 0, "Someone")
    with pytest.raises(MeetingNotFound):
        lib.transcript_page(missing)


def test_delete(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    lib.delete(mid)
    assert lib.count_meetings() == 0
    with pytest.raises(MeetingNotFound):
        lib.delete(mid)


def test_transcript_pages(library_path):
    lib = MeetingLibrary()
    segs = _segs(*[("You", i * 10.0, i * 10.0 + 9, "x" * 1500) for i in range(5)])
    mid = lib.save_meeting(_new(segments=segs))
    page = lib.transcript_page(mid, max_chars=3000)
    assert [i for i, _ in page.segments] == [0, 1] and page.next_cursor == 2
    page = lib.transcript_page(mid, cursor=page.next_cursor, max_chars=3000)
    assert [i for i, _ in page.segments] == [2, 3] and page.next_cursor == 4
    last = lib.transcript_page(mid, cursor=4)
    assert [i for i, _ in last.segments] == [4] and last.next_cursor is None
    assert lib.transcript_page(mid, cursor=99).segments == []
    window = lib.transcript_page(mid, start_seconds=15, end_seconds=25)
    assert [i for i, _ in window.segments] == [1, 2]


def test_transcript_page_progress_guard_on_oversized_segment(library_path):
    # A single segment bigger than max_chars must still be returned (not
    # dropped) with a terminal cursor — otherwise a phase-2 MCP pager that
    # keeps re-requesting the same cursor would loop forever.
    lib = MeetingLibrary()
    segs = _segs(("You", 0.0, 10.0, "x" * 5000))
    mid = lib.save_meeting(_new(segments=segs))
    page = lib.transcript_page(mid, max_chars=1000)
    assert [i for i, _ in page.segments] == [0]
    assert page.next_cursor is None


def test_concurrent_writers(library_path):
    lib = MeetingLibrary()
    lib.count_meetings()  # create schema before the race

    def writer(hour):
        for minute in range(20):
            lib.save_meeting(_new(started=datetime(2026, 9, 1, hour, minute, tzinfo=EDT)))

    threads = [threading.Thread(target=writer, args=(h,)) for h in (9, 10)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert lib.count_meetings() == 40


def test_save_notes_partial_updates_and_tag_normalisation(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    notes = lib.save_notes(mid, summary="Invest in VFA.", tags=[" DMT", "dmt", "VFA ", ""])
    assert notes.summary == "Invest in VFA." and notes.action_items == []
    lib.save_notes(mid, action_items=["Confirm Purvi's leave with Naresh", "  "])
    m = lib.get_meeting(mid)
    assert m.notes.summary == "Invest in VFA."           # untouched field kept
    assert m.notes.action_items == ["Confirm Purvi's leave with Naresh"]
    assert m.notes.updated_by == "claude"
    assert m.tags == ["DMT", "VFA"]
    lib.save_notes(mid, tags=["VFA"])
    assert lib.list_tags() == [("VFA", 1)]                # orphan DMT removed
    # list_tags() inner-joins meeting_tags, so it would hide an orphan row
    # left behind in the `tags` table even if the cleanup didn't run; check
    # the raw table so a regression that drops the cleanup is still caught.
    conn = meeting_store.connect(library_path)
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM tags")}
    finally:
        conn.close()
    assert names == {"VFA"}
    assert lib.list_meetings(tag="vfa")[0].has_summary is True
    # Replacing tags is a partial update too: action items set earlier
    # must survive a tags-only save.
    assert lib.get_meeting(mid).notes.action_items == ["Confirm Purvi's leave with Naresh"]
    lib.save_notes(mid, summary="Invest in VFA, revised.")
    assert lib.get_meeting(mid).notes.action_items == ["Confirm Purvi's leave with Naresh"]


def test_save_notes_limits_and_missing(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    with pytest.raises(ValueError):
        lib.save_notes(mid, tags=[f"t{i}" for i in range(21)])
    with pytest.raises(ValueError):
        lib.save_notes(mid, tags=["x" * 41])
    with pytest.raises(ValueError):
        lib.save_notes(mid, summary="x" * 20_001)
    with pytest.raises(MeetingNotFound):
        lib.save_notes("20260101-000000-0000", summary="x")


def test_save_notes_action_item_limits(library_path):
    # Controller ruling: pin the action-item cap explicitly (list length and
    # per-item length), since the brief's limits test only covered tags and
    # summary.
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    with pytest.raises(ValueError):
        lib.save_notes(mid, action_items=[f"item {i}" for i in range(51)])
    with pytest.raises(ValueError):
        lib.save_notes(mid, action_items=["x" * 501])
    # Right at the limits should be accepted.
    lib.save_notes(mid, action_items=[f"item {i}" for i in range(50)])
    assert len(lib.get_meeting(mid).notes.action_items) == 50


def test_people_link_by_email_then_name(library_path):
    lib = MeetingLibrary()
    a = lib.save_meeting(_new())
    b = lib.save_meeting(_new(started=datetime(2026, 9, 25, 9, 0, tzinfo=EDT)))
    lib.link_people(a, [("Refayet K", "refayet@example.com", "organizer"),
                        ("Jason", None, "attendee")])
    lib.link_people(b, [("Refayet Khan", "REFAYET@example.com", "attendee")])
    assert lib.list_people() == [("Refayet K", 2), ("Jason", 1)]
    assert lib.list_people("jas") == [("Jason", 1)]
    assert [m.meeting_id for m in lib.list_meetings(person="refayet@example.com")] == [b, a]
    assert lib.get_meeting(a).people == ["Refayet K", "Jason"]  # organizer first


def test_link_people_reuses_name_only_person_case_insensitively(library_path):
    # Covers both branches of the no-email path in _person_id: the
    # case-insensitive lookup (COLLATE NOCASE) and reusing the existing
    # row rather than inserting a duplicate.
    lib = MeetingLibrary()
    a = lib.save_meeting(_new())
    b = lib.save_meeting(_new(started=datetime(2026, 9, 25, 9, 0, tzinfo=EDT)))
    lib.link_people(a, [("Jason", None, "attendee")])
    lib.link_people(b, [("jason", None, "attendee")])
    assert lib.list_people() == [("Jason", 2)]


def test_link_people_replaces_previous_links(library_path):
    # link_people replaces a meeting's people wholesale; a person dropped
    # from the new list must no longer be linked to that meeting.
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    lib.link_people(mid, [("Jason", None, "attendee")])
    assert lib.get_meeting(mid).people == ["Jason"]
    lib.link_people(mid, [("Naresh", None, "attendee")])
    assert lib.get_meeting(mid).people == ["Naresh"]
    assert lib.list_people() == [("Naresh", 1)]  # Jason no longer linked


def test_person_email_normalisation_merges_case_and_whitespace(library_path):
    # Controller ruling 1: normalise email once (strip + lower) and use the
    # normalised value for both the lookup and the insert. people.email is
    # UNIQUE COLLATE NOCASE, so case alone is already handled by SQLite;
    # a padded email (leading/trailing whitespace) is not, so without the
    # strip a padded email misses the lookup and raises IntegrityError on
    # the UNIQUE constraint instead of reusing the existing person.
    lib = MeetingLibrary()
    a = lib.save_meeting(_new())
    b = lib.save_meeting(_new(started=datetime(2026, 9, 25, 9, 0, tzinfo=EDT)))
    lib.link_people(a, [("Refayet K", " Refayet@example.com", "attendee")])
    lib.link_people(b, [("Refayet K", "refayet@example.com ", "attendee")])
    assert lib.list_people() == [("Refayet K", 2)]


def test_list_meetings_tag_filter_excludes_untagged(library_path):
    lib = MeetingLibrary()
    tagged = lib.save_meeting(_new())
    untagged = lib.save_meeting(_new(started=datetime(2026, 9, 25, 9, 0, tzinfo=EDT)))
    lib.save_notes(tagged, tags=["Project X"])
    result = lib.list_meetings(tag="Project X")
    assert [m.meeting_id for m in result] == [tagged]
    assert untagged not in [m.meeting_id for m in result]


def test_list_meetings_person_filter_excludes_unlinked(library_path):
    lib = MeetingLibrary()
    linked = lib.save_meeting(_new())
    unlinked = lib.save_meeting(_new(started=datetime(2026, 9, 25, 9, 0, tzinfo=EDT)))
    lib.link_people(linked, [("Naresh", "naresh@example.com", "attendee")])
    result = lib.list_meetings(person="naresh@example.com")
    assert [m.meeting_id for m in result] == [linked]
    assert unlinked not in [m.meeting_id for m in result]


def test_has_summary_true_only_after_nonempty_summary(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    assert lib.list_meetings()[0].has_summary is False
    # Notes with only action items (no summary) still leave has_summary False.
    lib.save_notes(mid, action_items=["Follow up"])
    assert lib.list_meetings()[0].has_summary is False
    lib.save_notes(mid, summary="Discussed roadmap.")
    assert lib.list_meetings()[0].has_summary is True


def test_delete_removes_orphan_tags_but_keeps_shared_tags(library_path):
    lib = MeetingLibrary()
    a = lib.save_meeting(_new())
    b = lib.save_meeting(_new(started=datetime(2026, 9, 25, 9, 0, tzinfo=EDT)))
    lib.save_notes(a, tags=["Solo", "Shared"])
    lib.save_notes(b, tags=["Shared"])
    lib.delete(a)
    assert lib.list_tags() == [("Shared", 1)]  # Solo (orphaned) is gone, Shared kept
    # list_tags() joins through meeting_tags, so an orphan row left behind
    # in the `tags` table itself wouldn't show up there either way; check
    # the raw table so a regression that drops the cleanup is still caught.
    conn = meeting_store.connect(library_path)
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM tags")}
    finally:
        conn.close()
    assert names == {"Shared"}
