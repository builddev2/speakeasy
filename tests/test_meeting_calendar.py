"""Calendar cache writes, event people and linking (schema v2)."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from speakeasy import meeting_store
from speakeasy.meeting_library import (
    EventPerson, MeetingLibrary, MeetingNotFound, NewMeeting, SyncedEvent,
)
from speakeasy.meetings import MeetingSegment

UTC = timezone.utc
REFAYET = EventPerson("Refayet K", "refayet@example.com", "organizer")
NARESH = EventPerson("Naresh P", "naresh@example.com", "attendee")


def ev(key="ev1", start=datetime(2026, 9, 29, 17, 0, tzinfo=UTC), minutes=30,
       title="Weekly 1:1 — Refayet", people=(REFAYET,), other=1, **kw):
    return SyncedEvent(key, kw.get("calendar", "Work"), title, start,
                       start + timedelta(minutes=minutes), kw.get("all_day", False),
                       kw.get("declined", False), other, tuple(people))


WINDOW = (datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 10, 13, tzinfo=UTC))


def _meeting(lib, start=datetime(2026, 9, 29, 17, 2, tzinfo=UTC), **kw):
    return lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0.0, 5.0, "hello")],
        duration_seconds=60.0, started_at=start, **kw))


def test_schema_v2_adds_other_attendees(library_path):
    conn = meeting_store.connect(library_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        cols = [r[1] for r in conn.execute("PRAGMA table_info(calendar_events)")]
        assert "other_attendees" in cols
    finally:
        conn.close()


def test_v1_library_upgrades_in_place(library_path):
    raw = sqlite3.connect(library_path)
    raw.executescript(meeting_store._SCHEMA_V1)
    raw.execute(
        "INSERT INTO meetings (id, title, started_at, tz_offset_minutes,"
        " duration_seconds, capture_mode, system_audio_status, capture_scope,"
        " track_offsets_json, capture_health_json, source,"
        " timestamps_approximate, created_at, updated_at) VALUES"
        " ('20260901-100000-abcd', 'Old', '2026-09-01T10:00:00Z', 0, 60,"
        " 'mic_only', 'unavailable', 'mic_only', '{}', '{}', 'recorded', 0,"
        " '2026-09-01T10:01:00Z', '2026-09-01T10:01:00Z')")
    raw.commit()
    raw.close()
    assert MeetingLibrary(library_path).get_meeting("20260901-100000-abcd").title == "Old"
    conn = meeting_store.connect(library_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        conn.close()


def test_replace_window_inserts_updates_and_deletes(library_path):
    lib = MeetingLibrary(library_path)
    lib.replace_calendar_window([ev("a"), ev("b", title="Stand-up")], *WINDOW)
    lib.replace_calendar_window([ev("a", title="Renamed")], *WINDOW)
    events = lib.calendar_events_between("2026-09-29", "2026-09-29")
    assert [(e.event_key, e.title) for e in events] == [("a", "Renamed")]
    assert events[0].people == [REFAYET]
    assert events[0].other_attendees == 1


def test_replace_window_keeps_events_outside_it(library_path):
    lib = MeetingLibrary(library_path)
    old = ev("old", start=datetime(2026, 5, 1, 9, 0, tzinfo=UTC))
    lib.replace_calendar_window([old], datetime(2026, 4, 1, tzinfo=UTC), WINDOW[0])
    lib.replace_calendar_window([], *WINDOW)
    assert lib.calendar_event("old") is not None


def test_recurring_occurrences_are_distinct(library_path):
    lib = MeetingLibrary(library_path)
    monday = datetime(2026, 9, 28, 17, 0, tzinfo=UTC)
    lib.replace_calendar_window([
        ev("X@2026-09-28T17:00:00Z", start=monday),
        ev("X@2026-10-05T17:00:00Z", start=monday + timedelta(days=7)),
    ], *WINDOW)
    assert len(lib.calendar_events_between("2026-09-28", "2026-10-05")) == 2


def test_naive_event_time_is_rejected(library_path):
    bad = ev(start=datetime(2026, 9, 29, 17, 0))
    with pytest.raises(ValueError):
        MeetingLibrary(library_path).replace_calendar_window([bad], *WINDOW)


def test_people_only_on_dropped_events_are_removed(library_path):
    lib = MeetingLibrary(library_path)
    lib.replace_calendar_window([ev("a", people=(REFAYET, NARESH), other=2)], *WINDOW)
    lib.replace_calendar_window([], *WINDOW)
    conn = meeting_store.connect(library_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 0
    finally:
        conn.close()


def test_clear_cache(library_path):
    lib = MeetingLibrary(library_path)
    lib.replace_calendar_window([ev("a")], *WINDOW)
    lib.clear_calendar_cache()
    assert lib.calendar_event("a") is None


def test_overlapping_uses_utc_instants(library_path):
    lib = MeetingLibrary(library_path)
    lib.replace_calendar_window([ev("a")], *WINDOW)   # 17:00-17:30Z
    at = datetime(2026, 9, 29, 17, 29, tzinfo=UTC)
    assert [e.event_key for e in lib.calendar_events_overlapping(
        at, at + timedelta(minutes=2))] == ["a"]
    assert [e.event_key for e in lib.calendar_events_overlapping(
        at - timedelta(minutes=1), at)] == ["a"]
    later = datetime(2026, 9, 29, 17, 30, tzinfo=UTC)
    assert lib.calendar_events_overlapping(later, later + timedelta(minutes=5)) == []


def test_save_meeting_links_event_people(library_path):
    lib = MeetingLibrary(library_path)
    lib.replace_calendar_window([ev("a", people=(REFAYET, NARESH), other=2)], *WINDOW)
    mid = _meeting(lib, title="Weekly 1:1 — Refayet", calendar_event_id="a",
                   people=[REFAYET, NARESH])
    stored = lib.get_meeting(mid)
    assert stored.calendar_event_id == "a"
    assert stored.people == ["Refayet K", "Naresh P"]   # organizer first
    assert lib.calendar_event("a").meeting_ids == [mid]


def test_link_event_sets_title_and_people(library_path):
    lib = MeetingLibrary(library_path)
    lib.replace_calendar_window([ev("a")], *WINDOW)
    mid = _meeting(lib)
    lib.link_event(mid, "a")
    stored = lib.get_meeting(mid)
    assert (stored.title, stored.calendar_event_id, stored.people) == (
        "Weekly 1:1 — Refayet", "a", ["Refayet K"])


def test_unlink_keeps_title_and_people(library_path):
    lib = MeetingLibrary(library_path)
    lib.replace_calendar_window([ev("a")], *WINDOW)
    mid = _meeting(lib)
    lib.link_event(mid, "a")
    lib.link_event(mid, None)
    stored = lib.get_meeting(mid)
    assert (stored.title, stored.calendar_event_id, stored.people) == (
        "Weekly 1:1 — Refayet", None, ["Refayet K"])


def test_link_event_errors(library_path):
    lib = MeetingLibrary(library_path)
    mid = _meeting(lib)
    with pytest.raises(ValueError, match="no longer in the calendar"):
        lib.link_event(mid, "missing")
    with pytest.raises(MeetingNotFound):
        lib.link_event("20260101-000000-ffff", None)


def test_linked_meeting_survives_event_deletion(library_path):
    lib = MeetingLibrary(library_path)
    lib.replace_calendar_window([ev("a")], *WINDOW)
    mid = _meeting(lib, title="Weekly 1:1 — Refayet", calendar_event_id="a",
                   people=[REFAYET])
    lib.replace_calendar_window([], *WINDOW)
    stored = lib.get_meeting(mid)
    assert (stored.title, stored.people) == ("Weekly 1:1 — Refayet", ["Refayet K"])
