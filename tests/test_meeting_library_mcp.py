# tests/test_meeting_library_mcp.py
"""Library reads the MCP server needs (phase 2)."""
from datetime import datetime, timedelta, timezone

import pytest

from speakeasy import meeting_store, settings
from speakeasy.meeting_library import (
    LibraryWatcher, MeetingLibrary, MeetingNotFound, NewMeeting,
)
from speakeasy.meetings import MeetingSegment

PDT = timezone(timedelta(hours=-7))


def _new(started=datetime(2026, 9, 24, 10, 0, 0, tzinfo=PDT), **kw):
    kw.setdefault("segments", [MeetingSegment("You", 0.0, 4.0, "hello budget")])
    kw.setdefault("duration_seconds", 600.0)
    return NewMeeting(started_at=started, **kw)


def _insert_event(path, key, start_utc, end_utc, title="Standup"):
    conn = meeting_store.connect(path)
    with conn:
        conn.execute(
            "INSERT INTO calendar_events (event_key, calendar_name, title, start_utc,"
            " end_utc, all_day, declined, synced_at) VALUES (?, 'Work', ?, ?, ?, 0, 0,"
            " '2026-09-28T00:00:00Z')", (key, title, start_utc, end_utc))
    conn.close()


def test_mcp_last_used_path_is_in_app_support(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    assert settings.mcp_last_used_path() == tmp_path / "mcp_last_used"


def test_calendar_events_between_is_empty_before_phase3(library_path):
    assert MeetingLibrary().calendar_events_between("2026-01-01", "2026-12-31") == []


def test_calendar_events_between_returns_overlapping_events_with_linked_meetings(library_path):
    lib = MeetingLibrary()
    _insert_event(library_path, "E1@2026-09-24T17:00:00Z",
                  "2026-09-24T17:00:00Z", "2026-09-24T17:30:00Z")
    _insert_event(library_path, "E2@2026-03-01T17:00:00Z",
                  "2026-03-01T17:00:00Z", "2026-03-01T17:30:00Z", title="Old")
    mid = lib.save_meeting(_new(calendar_event_id="E1@2026-09-24T17:00:00Z"))
    events = lib.calendar_events_between("2026-09-01", "2026-09-30")
    assert [e.title for e in events] == ["Standup"]
    assert events[0].meeting_ids == [mid]
    assert events[0].all_day is False and events[0].declined is False
    assert lib.calendar_event("E1@2026-09-24T17:00:00Z").title == "Standup"
    assert lib.calendar_event("missing") is None
    assert lib.calendar_event(None) is None


def test_meeting_tags(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    lib.save_notes(mid, tags=["budget", "Q3"])
    assert lib.meeting_tags(mid) == ["budget", "Q3"]
    with pytest.raises(MeetingNotFound):
        lib.meeting_tags("20200101-000000-abcd")


def test_watcher_baseline_then_sees_every_kind_of_change(library_path):
    lib = MeetingLibrary()
    watcher = LibraryWatcher(lib)
    assert watcher.changed() is False  # baseline only
    assert watcher.changed() is False
    mid = lib.save_meeting(_new())
    assert watcher.changed() is True
    assert watcher.changed() is False
    # Same wall-clock second as the save above: a timestamp marker misses this.
    lib.rename(mid, "Budget review")
    assert watcher.changed() is True
    lib.save_notes(mid, summary="First")
    assert watcher.changed() is True
    lib.save_notes(mid, tags=["budget"])
    assert watcher.changed() is True
    lib.delete(mid)
    assert watcher.changed() is True
    watcher.close()


def test_watcher_sees_a_write_from_another_process(library_path):
    # The MCP server is a separate process; prove the signal crosses it.
    import os, subprocess, sys
    from pathlib import Path
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    watcher = LibraryWatcher(lib)
    watcher.changed()
    code = ("import sys; from speakeasy.meeting_library import MeetingLibrary;"
            " MeetingLibrary(sys.argv[1]).save_notes(sys.argv[2], summary='From Claude')")
    repo = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "-c", code, str(library_path), mid], check=True,
                   cwd=repo, env={**os.environ, "PYTHONPATH": str(repo)}, timeout=60)
    assert watcher.changed() is True
    assert lib.get_meeting(mid).notes.summary == "From Claude"
    watcher.close()


def test_watcher_close_resets_the_baseline(library_path):
    lib = MeetingLibrary()
    watcher = LibraryWatcher(lib)
    watcher.changed()
    lib.save_meeting(_new())
    watcher.close()
    assert watcher.changed() is False  # reopened: new baseline
    watcher.close()
    watcher.close()  # idempotent
