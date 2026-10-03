from datetime import datetime, timedelta, timezone

import pytest

from speakeasy import meeting_store
from speakeasy.meeting_library import (
    USER_NOTES_MAX_CHARS, MeetingLibrary, MeetingNotFound, NewMeeting, notes_plain_text,
)
from speakeasy.meetings import MeetingSegment

EDT = timezone(timedelta(hours=-4))
START = datetime(2026, 10, 2, 9, 0, 0, tzinfo=EDT)


def _new(started=START, **kw):
    kw.setdefault("segments", [MeetingSegment("You", 0.0, 4.0, "hello there"),
                               MeetingSegment("Speaker 1", 4.5, 9.0, "budget review")])
    kw.setdefault("duration_seconds", 600.0)
    return NewMeeting(started_at=started, **kw)


def test_schema_is_v5_with_notepad_tables(library_path):
    conn = meeting_store.connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert {"user_notes", "user_notes_fts", "note_draft"} <= names
    finally:
        conn.close()


def test_v4_library_migrates_to_v5_keeping_meetings(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    conn = meeting_store.connect()
    conn.executescript("DROP TABLE user_notes_fts; DROP TABLE user_notes;"
                       " DROP TABLE note_draft; PRAGMA user_version = 4;")
    conn.close()
    assert lib.get_meeting(mid).title
    assert lib.get_user_notes(mid) is None
    conn = meeting_store.connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    conn.close()


def test_set_and_get_user_notes_round_trip(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    saved = lib.set_user_notes(mid, "## Plan\n- [ ] send deck", [(1, 12.5)])
    got = lib.get_user_notes(mid)
    assert (got.markdown, got.stamps) == ("## Plan\n- [ ] send deck", [(1, 12.5)])
    assert got.updated_at == saved.updated_at
    assert lib.get_meeting(mid).user_notes == got


def test_blank_notes_delete_the_row(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    lib.set_user_notes(mid, "keep", [])
    assert lib.set_user_notes(mid, "  \n ", []) is None
    assert lib.get_user_notes(mid) is None


def test_too_long_notes_are_refused(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    with pytest.raises(ValueError, match="Notes are too long to save"):
        lib.set_user_notes(mid, "x" * (USER_NOTES_MAX_CHARS + 1), [])
    lib.set_user_notes(mid, "x" * USER_NOTES_MAX_CHARS, [])


@pytest.mark.parametrize("stamps", [[(-1, 3.0)], [(0, -2.0)], [(0, "a")], [(True, 1.0)], [(0,)]])
def test_invalid_stamps_are_refused(library_path, stamps):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    with pytest.raises(ValueError, match="Invalid note stamps"):
        lib.set_user_notes(mid, "text", stamps)


def test_unknown_meeting_is_not_found(library_path):
    lib = MeetingLibrary()
    with pytest.raises(MeetingNotFound):
        lib.set_user_notes("20260101-000000-abcd", "text", [])
    with pytest.raises(MeetingNotFound):
        lib.get_user_notes("20260101-000000-abcd")


def test_search_finds_user_notes_with_their_own_kind(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    lib.set_user_notes(mid, "- [ ] **Escalate** the vendor contract", [])
    hits = lib.search("vendor")
    assert [(h.meeting_id, h.kind) for h in hits] == [(mid, "user_notes")]
    assert "\x02vendor\x03" in hits[0].snippet
    assert [h.kind for h in lib.search("budget")] == ["transcript"]


def test_deleting_a_meeting_removes_its_notes_and_hits(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    lib.set_user_notes(mid, "vendor contract", [])
    lib.delete(mid)
    assert lib.search("vendor") == []
    conn = meeting_store.connect()
    assert conn.execute("SELECT COUNT(*) FROM user_notes").fetchone()[0] == 0
    conn.close()


def test_rebuild_derived_keeps_user_notes_searchable(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    lib.set_user_notes(mid, "vendor contract", [])
    conn = meeting_store.connect()
    conn.execute("INSERT INTO user_notes_fts(user_notes_fts) VALUES ('delete-all')")
    conn.commit()
    meeting_store.rebuild_derived(conn)
    conn.close()
    assert [h.kind for h in lib.search("vendor")] == ["user_notes"]


def test_notes_plain_text_strips_markdown():
    md = "# Title\n- [x] **done** item\n  1. *one*\n\\- not a list\n\nend \\*star\\*"
    assert notes_plain_text(md) == "Title\ndone item\none\n- not a list\nend *star*"


def test_replacing_notes_updates_the_search_index(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    lib.set_user_notes(mid, "vendor contract", [])
    lib.set_user_notes(mid, "hiring plan", [])
    assert lib.search("vendor") == []
    assert [(h.meeting_id, h.kind) for h in lib.search("hiring")] == [(mid, "user_notes")]


def test_blanked_notes_leave_no_stale_index_entries(library_path):
    lib = MeetingLibrary()
    a = lib.save_meeting(_new())
    b = lib.save_meeting(_new(started=START + timedelta(days=1)))
    lib.set_user_notes(a, "vendor contract", [])
    lib.set_user_notes(a, "", [])
    lib.set_user_notes(b, "hiring plan", [])
    assert lib.search("vendor") == []
    assert [(h.meeting_id, h.kind) for h in lib.search("hiring")] == [(b, "user_notes")]
