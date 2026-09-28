# Meeting Library, Calendar and Claude MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Speakeasy's saved meetings into a searchable SQLite library with an Apple-style Meetings window, then (later phases) expose it to Claude Desktop/Code through a local MCP server, link recordings to the Calendar.app work calendar, and offer to record meetings as they start.

**Architecture:** `meeting_store.py` owns the SQLite schema (WAL, FTS5). `meeting_library.py`'s `MeetingLibrary` is the only API for meetings. The engine, the importer, the CLI, the Meetings window bridge, and (phase 2) the MCP server all call it. Existing JSON meetings are imported once, verified and archived. The Meetings window becomes a resizable three-column React page. Its screens are built first against mock data and approved by screenshot before being wired to the bridge.

**Tech Stack:** Python 3.11 stdlib `sqlite3` (FTS5, SQLite 3.54), pyobjc/AppKit/WebKit (existing), React 18 + CSS modules + Vite (existing, build-time only), pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-meeting-library-mcp-design.md`

**Status:** Phase 1 is fully detailed below. Phases 2–4 are scoped task lists. Expand each one with `superpowers:writing-plans` at the start of its own session, against the interfaces phase 1 actually shipped.

## Global Constraints

- Speakeasy makes no network calls at runtime. Phase 1 adds **no** runtime dependency (stdlib `sqlite3` only). Phase 3 adds exactly `pyobjc-framework-EventKit==12.2.1`.
- Tests never touch the real mic, model, sherpa-onnx, EventKit or network. Use temp dirs through the `library_path` / `meetings_dir` fixtures.
- Test command: `.venv/bin/python -m pytest -q`. Always give the Bash call `timeout: 300000` (suite is ~7 s today; the explicit timeout stops silent backgrounding).
- Frontend check: `npm --prefix frontend run build` (runs `tsc --noEmit` and `vite build`).
- Never share a `sqlite3` connection across threads or processes. Every `MeetingLibrary` call opens its own connection.
- UI objects and webview calls stay on the main thread. Engine callbacks hop with `performSelectorOnMainThread_withObject_waitUntilDone_`.
- The capture-health whitelist (`meetings._CAPTURE_HEALTH_KEYS`) stays the persistence boundary. The MCP server never exposes capture health.
- Calendar event notes, location and URLs are never stored (phase 3).
- Speakeasy never starts a recording without a user click (phase 4).
- UI uses the existing tokens in `frontend/src/styles/tokens.css`, the dark glass look, SF system fonts, and **no new frontend dependencies**.
- Do not modify dictation, focus, AX or insertion code (`injector.py`, `hotkey.py`, dictation paths in `engine.py`).
- Work on a branch in a worktree (`superpowers:using-git-worktrees`), never on `master`.
- Match surrounding comment style: comments explain *why* a constraint exists.

## Review Focus

1. **Search text that looks like FTS syntax** (`C++`, `"unbalanced`, `AND`, `NEAR(`, `-x`, emoji, empty string). Search returns results or an empty list and never raises. Pinned in Task 4.
2. **Meeting deleted or missing while the window shows it** (another process, or a stale id). The bridge responds with the error `not_found` and the page re-lists; nothing crashes. Pinned in Tasks 2 and 10.
3. **Import interrupted or re-run** (crash after commit but before archiving; malformed file; verification mismatch). No duplicate-key crash, no half import, nothing archived unless it is in the library. Pinned in Task 6.
4. **Local time vs UTC at day boundaries and DST** (meeting at 23:50 local is stored as the next UTC day). Lists group and filter by the *local* day. Pinned in Tasks 2 and 10.
5. **Titles with `/`, `:`, emoji or empty-after-cleaning in export filenames**, and accented words in search (`cafe` finds `café`). Pinned in Tasks 4 and 8.

---

## File map (phase 1)

| File | Status | Responsibility |
|---|---|---|
| `speakeasy/settings.py` | modify | `library_path()` |
| `speakeasy/meeting_store.py` | create | connection setup, schema v1, migrations |
| `speakeasy/meeting_library.py` | create | `MeetingLibrary` API + dataclasses + search |
| `speakeasy/meeting_import.py` | create | legacy JSON import, long-segment split, verification, archiving |
| `speakeasy/meeting_export.py` | create | `--export-meetings` Markdown export |
| `speakeasy/meetings.py` | modify | `known_speaker_segments` gap/cap rule |
| `speakeasy/config.py` | modify | two segmentation constants |
| `speakeasy/meeting_recorder.py` | modify | `MeetingRecording.started_at` |
| `speakeasy/engine.py` | modify | start time, save via library, library upgrade job |
| `speakeasy/__main__.py` | modify | three CLI commands |
| `speakeasy/ui/meetings_bridge.py` | create | pure-Python bridge handlers (testable) |
| `speakeasy/ui/webbridge.py` | modify | `day_label`, `snippet_parts`, `start` in lines |
| `speakeasy/ui/meetings_window.py` | modify | thin ObjC controller over `MeetingsBridge` |
| `speakeasy/ui/glass.py`, `speakeasy/ui/webwindow.py` | modify | optional resizable windows |
| `speakeasy/ui/menubar.py` | modify | start library upgrade, forward status |
| `frontend/src/meetings/*`, `frontend/src/mock/meetings.ts`, `frontend/src/components/*` | modify/create | redesigned Meetings window |
| `frontend/prompt.html`, `frontend/src/prompt/*` | create | record-banner page (mock in phase 1, hosted in phase 4) |
| `tests/conftest.py` | modify | `library_path` fixture |
| `tests/test_meeting_store.py`, `test_meeting_library.py`, `test_meeting_search.py`, `test_meeting_import.py`, `test_meeting_export.py`, `test_meetings_bridge.py` | create | tests |
| `tests/test_engine_meeting.py`, `tests/test_webbridge.py`, `tests/test_dual_track_meeting.py` | modify | follow the new save path |
| `AGENTS.md`, `README.md` | modify | docs |

---

## Phase 1: Library, import and redesigned Meetings window

### Task 1: Store: library path, schema v1, migrations

**Files:**
- Modify: `speakeasy/settings.py` (after `meetings_dir()`, ~line 55)
- Create: `speakeasy/meeting_store.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_meeting_store.py`

**Interfaces:**
- Produces: `settings.library_path() -> Path`; `meeting_store.connect(path: Path | None = None) -> sqlite3.Connection` (row factory `sqlite3.Row`, WAL, foreign keys on, busy timeout 5000 ms, migrated); `meeting_store.SCHEMA_VERSION = 1`; `meeting_store.rebuild_derived(conn)`; pytest fixture `library_path`.

- [ ] **Step 1: Add the fixture and write the failing tests**

In `tests/conftest.py`, add the fixture and make `meetings_dir` depend on it, so every existing meeting test also gets a throwaway library:

```python
@pytest.fixture
def library_path(tmp_path, monkeypatch):
    """Point the SQLite meeting library at a throwaway file."""
    path = tmp_path / "library.sqlite"
    monkeypatch.setattr(settings, "library_path", lambda: path)
    return path


@pytest.fixture
def meetings_dir(tmp_path, monkeypatch, library_path):
    """Point the meetings dir (legacy JSON) at a throwaway directory."""
    d = tmp_path / "meetings"
    d.mkdir()
    monkeypatch.setattr(settings, "meetings_dir", lambda: d)
    return d
```

Create `tests/test_meeting_store.py`:

```python
import os
import stat

import pytest

from speakeasy import meeting_store


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}


def test_connect_creates_schema_wal_and_private_file(library_path):
    conn = meeting_store.connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert {
        "meetings", "segments", "segments_fts", "notes", "notes_fts", "tags",
        "meeting_tags", "people", "meeting_people", "calendar_events",
        "calendar_event_people",
    } <= _tables(conn)
    assert stat.S_IMODE(os.stat(library_path).st_mode) == 0o600
    conn.close()


def test_connect_twice_is_idempotent(library_path):
    meeting_store.connect().close()
    conn = meeting_store.connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    conn.close()


def test_newer_schema_is_refused(library_path):
    conn = meeting_store.connect()
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(RuntimeError, match="newer"):
        meeting_store.connect()


def _insert_meeting(conn, meeting_id="20260924-131723-abcd"):
    conn.execute(
        "INSERT INTO meetings (id, title, started_at, tz_offset_minutes,"
        " duration_seconds, capture_mode, system_audio_status, capture_scope,"
        " track_offsets_json, capture_health_json, source, created_at, updated_at)"
        " VALUES (?, 't', '2026-09-24T17:17:23Z', -240, 60, 'mic_only',"
        " 'unavailable', 'mic_only', '{}', '{}', 'recorded', 'x', 'x')",
        (meeting_id,),
    )


def _fts_hits(conn, table, term):
    return conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {table} MATCH ?", (f'"{term}"',)
    ).fetchone()[0]


def test_segment_fts_follows_insert_update_delete_and_cascade(library_path):
    conn = meeting_store.connect()
    with conn:
        _insert_meeting(conn)
        conn.execute(
            "INSERT INTO segments (meeting_id, idx, speaker, start_seconds,"
            " end_seconds, text) VALUES ('20260924-131723-abcd', 0, 'You', 0, 1,"
            " 'cognos reporting')"
        )
    assert _fts_hits(conn, "segments_fts", "cognos") == 1
    with conn:
        conn.execute("UPDATE segments SET text = 'rbac gaps'")
    assert _fts_hits(conn, "segments_fts", "cognos") == 0
    assert _fts_hits(conn, "segments_fts", "rbac") == 1
    with conn:
        conn.execute("DELETE FROM meetings")
    assert conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 0
    assert _fts_hits(conn, "segments_fts", "rbac") == 0
    conn.close()


def test_notes_upsert_keeps_fts_in_step(library_path):
    conn = meeting_store.connect()
    upsert = (
        "INSERT INTO notes (meeting_id, summary, action_items_json,"
        " action_items_text, updated_at, updated_by) VALUES (?, ?, '[]', '', 'x',"
        " 'claude') ON CONFLICT(meeting_id) DO UPDATE SET summary = excluded.summary"
    )
    with conn:
        _insert_meeting(conn)
        conn.execute(upsert, ("20260924-131723-abcd", "invest in VFA"))
        conn.execute(upsert, ("20260924-131723-abcd", "sustain ACP"))
    assert _fts_hits(conn, "notes_fts", "vfa") == 0
    assert _fts_hits(conn, "notes_fts", "acp") == 1
    conn.close()


def test_rebuild_derived_restores_search(library_path):
    conn = meeting_store.connect()
    with conn:
        _insert_meeting(conn)
        conn.execute(
            "INSERT INTO segments (meeting_id, idx, speaker, start_seconds,"
            " end_seconds, text) VALUES ('20260924-131723-abcd', 0, 'You', 0, 1,"
            " 'alberta migration')"
        )
        conn.execute("INSERT INTO segments_fts(segments_fts) VALUES ('delete-all')")
    assert _fts_hits(conn, "segments_fts", "alberta") == 0
    meeting_store.rebuild_derived(conn)
    assert _fts_hits(conn, "segments_fts", "alberta") == 1
    conn.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_meeting_store.py -q` (timeout 300000)
Expected: FAIL with `ImportError: cannot import name 'meeting_store'`.

- [ ] **Step 3: Implement**

`speakeasy/settings.py`, after `meetings_dir()`:

```python
def library_path() -> Path:
    """The meeting library (SQLite, WAL): master copy of every transcript,
    note, tag and person. Text only — audio is never persisted."""
    return app_support_dir() / "library.sqlite"
```

Create `speakeasy/meeting_store.py`:

```python
"""SQLite storage for the meeting library: connection setup and schema.

The database is the master copy of meetings, segments, notes, tags and
people. The FTS tables and the calendar cache are derived and can always be
rebuilt (rebuild_derived / `--rebuild-index`). WAL plus a busy timeout let
the app and the MCP server process write safely at the same time; every
caller opens its own short-lived connection rather than sharing one across
threads or processes.

Every statement uses IF NOT EXISTS so two processes migrating a fresh file
at the same moment cannot fail each other.
"""

import os
import sqlite3
from pathlib import Path

from . import settings

SCHEMA_VERSION = 1

_TOKENIZE = "tokenize='porter unicode61 remove_diacritics 2'"

_SCHEMA_V1 = f"""
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    started_at TEXT NOT NULL,
    tz_offset_minutes INTEGER NOT NULL,
    duration_seconds REAL NOT NULL,
    capture_mode TEXT NOT NULL,
    system_audio_status TEXT NOT NULL,
    capture_scope TEXT NOT NULL,
    track_offsets_json TEXT NOT NULL,
    capture_health_json TEXT NOT NULL,
    calendar_event_id TEXT,
    source TEXT NOT NULL CHECK (source IN ('recorded', 'imported_json')),
    timestamps_approximate INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS meetings_started ON meetings(started_at);

CREATE TABLE IF NOT EXISTS segments (
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    speaker TEXT NOT NULL,
    start_seconds REAL NOT NULL,
    end_seconds REAL NOT NULL,
    text TEXT NOT NULL,
    confidence REAL,
    overlap INTEGER NOT NULL DEFAULT 0,
    profile_id TEXT,
    cluster_id INTEGER,
    UNIQUE (meeting_id, idx)
);
CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
    text, speaker, content='segments', content_rowid='rowid', {_TOKENIZE}
);
CREATE TRIGGER IF NOT EXISTS segments_ai AFTER INSERT ON segments BEGIN
    INSERT INTO segments_fts(rowid, text, speaker)
    VALUES (new.rowid, new.text, new.speaker);
END;
CREATE TRIGGER IF NOT EXISTS segments_ad AFTER DELETE ON segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, text, speaker)
    VALUES ('delete', old.rowid, old.text, old.speaker);
END;
CREATE TRIGGER IF NOT EXISTS segments_au AFTER UPDATE ON segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, text, speaker)
    VALUES ('delete', old.rowid, old.text, old.speaker);
    INSERT INTO segments_fts(rowid, text, speaker)
    VALUES (new.rowid, new.text, new.speaker);
END;

CREATE TABLE IF NOT EXISTS notes (
    meeting_id TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
    summary TEXT NOT NULL DEFAULT '',
    action_items_json TEXT NOT NULL DEFAULT '[]',
    action_items_text TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL CHECK (updated_by IN ('claude', 'user'))
);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    summary, action_items_text, content='notes', content_rowid='rowid',
    {_TOKENIZE}
);
CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, summary, action_items_text)
    VALUES (new.rowid, new.summary, new.action_items_text);
END;
CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, summary, action_items_text)
    VALUES ('delete', old.rowid, old.summary, old.action_items_text);
END;
CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, summary, action_items_text)
    VALUES ('delete', old.rowid, old.summary, old.action_items_text);
    INSERT INTO notes_fts(rowid, summary, action_items_text)
    VALUES (new.rowid, new.summary, new.action_items_text);
END;

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS meeting_tags (
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (meeting_id, tag_id)
);
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL,
    email TEXT UNIQUE COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS meeting_people (
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('attendee', 'organizer')),
    PRIMARY KEY (meeting_id, person_id)
);

-- Derived cache of Calendar.app (phase 3). Never stores event notes,
-- location or URLs: they routinely carry dial-in codes and passwords.
CREATE TABLE IF NOT EXISTS calendar_events (
    event_key TEXT PRIMARY KEY,
    calendar_name TEXT NOT NULL,
    title TEXT NOT NULL,
    start_utc TEXT NOT NULL,
    end_utc TEXT NOT NULL,
    all_day INTEGER NOT NULL,
    declined INTEGER NOT NULL DEFAULT 0,
    synced_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS calendar_events_start ON calendar_events(start_utc);
CREATE TABLE IF NOT EXISTS calendar_event_people (
    event_key TEXT NOT NULL REFERENCES calendar_events(event_key) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('attendee', 'organizer')),
    PRIMARY KEY (event_key, person_id)
);
PRAGMA user_version = 1;
COMMIT;
"""

# (target version, script). Append; never edit a shipped entry.
_MIGRATIONS = [(1, _SCHEMA_V1)]


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path) if path is not None else settings.library_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    conn = sqlite3.connect(path, timeout=5.0)
    if created:
        os.chmod(path, 0o600)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        conn.close()
        raise RuntimeError(
            f"Meeting library schema {version} is newer than this build "
            f"supports ({SCHEMA_VERSION}); refusing to open it."
        )
    for target, script in _MIGRATIONS:
        if version < target:
            conn.executescript(script)
            version = target


def rebuild_derived(conn: sqlite3.Connection) -> None:
    """Rebuild everything that is not master data."""
    with conn:
        conn.execute("INSERT INTO segments_fts(segments_fts) VALUES ('rebuild')")
        conn.execute("INSERT INTO notes_fts(notes_fts) VALUES ('rebuild')")
        conn.execute("DELETE FROM calendar_events")
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_meeting_store.py -q` (timeout 300000)
Expected: 6 passed.

- [ ] **Step 5: Run the full suite** (the `meetings_dir` fixture changed)

Run: `.venv/bin/python -m pytest -q` (timeout 300000)
Expected: all pass (393 before this plan, plus the new tests).

- [ ] **Step 6: Commit**

```bash
git add speakeasy/settings.py speakeasy/meeting_store.py tests/conftest.py tests/test_meeting_store.py
git commit -m "Add SQLite meeting library store with FTS and schema v1"
```

---

### Task 2: `MeetingLibrary` core (save, get, list, rename, relabel, delete, pages)

**Files:**
- Create: `speakeasy/meeting_library.py`
- Test: `tests/test_meeting_library.py`

**Interfaces:**
- Consumes: `meeting_store.connect`, `meetings.MeetingSegment`, `meetings._ID_RE`, `meetings._CAPTURE_HEALTH_KEYS`.
- Produces (exact names used by every later task):
  - `NewMeeting(segments, duration_seconds, started_at, title=None, meeting_id=None, capture_mode="mic_only", system_audio_status="unavailable", capture_scope="mic_only", track_offsets_seconds={"mic": 0.0}, capture_health={}, calendar_event_id=None, source="recorded", timestamps_approximate=False)`. `started_at` must be timezone-aware.
  - `Notes(summary: str, action_items: list[str], updated_at: str, updated_by: str)`
  - `MeetingSummary(meeting_id, title, started_at, tz_offset_minutes, duration_seconds, speaker_count, has_summary, timestamps_approximate, tags, people)` with `.local_start -> datetime`
  - `StoredMeeting(meeting_id, title, started_at, tz_offset_minutes, duration_seconds, segments, capture_mode, system_audio_status, capture_scope, track_offsets_seconds, capture_health, calendar_event_id, source, timestamps_approximate, notes, tags, people)` with `.local_start` and `.created` (local naive ISO, so `meetings.render_txt/render_md` keep working)
  - `TranscriptPage(segments: list[tuple[int, MeetingSegment]], next_cursor: int | None)`
  - `MeetingNotFound(KeyError)`
  - `MeetingLibrary(path: Path | None = None)` with `save_meeting(new) -> str`, `get_meeting(id) -> StoredMeeting`, `list_meetings(*, from_date=None, to_date=None, tag=None, person=None, limit=100, offset=0) -> list[MeetingSummary]`, `count_meetings() -> int`, `meeting_ids() -> set[str]`, `transcript_page(id, *, start_seconds=None, end_seconds=None, cursor=0, max_chars=20000) -> TranscriptPage`, `rename(id, title)`, `relabel_speaker(id, segment_index, label, *, all_matching=False)`, `delete(id)`
  - module functions `utc_iso(dt) -> str`, `local_start(started_at, tz_offset_minutes) -> datetime`, `local_day_bounds(from_date, to_date) -> tuple[str | None, str | None]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_meeting_library.py`:

```python
import threading
from datetime import datetime, timedelta, timezone

import pytest

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
    # changing TZ, which would leak into later tests.
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_meeting_library.py -q` (timeout 300000)
Expected: FAIL with `ModuleNotFoundError: speakeasy.meeting_library`.

- [ ] **Step 3: Implement**

Create `speakeasy/meeting_library.py`:

```python
"""The meeting library: the only API for stored meetings.

Called by the engine's save path, the JSON importer, the CLI, the Meetings
window bridge and (phase 2) the MCP server. Pure Python over meeting_store
with no AppKit import, so a future local web server can call it unchanged.
Times cross this boundary as UTC ISO strings ending in 'Z' plus the local
UTC offset captured at recording start.
"""

import json
import secrets
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import meeting_store
from .meetings import _CAPTURE_HEALTH_KEYS, _ID_RE, MeetingSegment

_MAX_PAGE_CHARS = 60_000
_MIN_PAGE_CHARS = 1_000


class MeetingNotFound(KeyError):
    pass


def utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_iso() -> str:
    return utc_iso(datetime.now(timezone.utc))


def local_start(started_at: str, tz_offset_minutes: int) -> datetime:
    utc = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    return utc.astimezone(timezone(timedelta(minutes=tz_offset_minutes)))


def local_day_bounds(from_date, to_date):
    """'YYYY-MM-DD' local dates (inclusive) -> UTC ISO [lower, upper)."""
    lower = upper = None
    if from_date:
        lower = utc_iso(datetime.fromisoformat(from_date).astimezone())
    if to_date:
        upper = utc_iso(
            (datetime.fromisoformat(to_date) + timedelta(days=1)).astimezone()
        )
    return lower, upper


def _check_id(meeting_id: str) -> None:
    # The id reaches SQL only as a bound parameter, but it is also a
    # filename in exports and legacy JSON; keep the one accepted shape.
    if not _ID_RE.fullmatch(str(meeting_id)):
        raise ValueError(f"Invalid meeting id: {meeting_id!r}")


@dataclass
class NewMeeting:
    segments: list[MeetingSegment]
    duration_seconds: float
    started_at: datetime
    title: str | None = None
    meeting_id: str | None = None
    capture_mode: str = "mic_only"
    system_audio_status: str = "unavailable"
    capture_scope: str = "mic_only"
    track_offsets_seconds: dict = field(default_factory=lambda: {"mic": 0.0})
    capture_health: dict = field(default_factory=dict)
    calendar_event_id: str | None = None
    source: str = "recorded"
    timestamps_approximate: bool = False


@dataclass
class Notes:
    summary: str
    action_items: list[str]
    updated_at: str
    updated_by: str


@dataclass
class MeetingSummary:
    meeting_id: str
    title: str
    started_at: str
    tz_offset_minutes: int
    duration_seconds: float
    speaker_count: int
    has_summary: bool
    timestamps_approximate: bool
    tags: list[str]
    people: list[str]

    @property
    def local_start(self) -> datetime:
        return local_start(self.started_at, self.tz_offset_minutes)


@dataclass
class StoredMeeting:
    meeting_id: str
    title: str
    started_at: str
    tz_offset_minutes: int
    duration_seconds: float
    segments: list[MeetingSegment]
    capture_mode: str
    system_audio_status: str
    capture_scope: str
    track_offsets_seconds: dict
    capture_health: dict
    calendar_event_id: str | None
    source: str
    timestamps_approximate: bool
    notes: Notes | None
    tags: list[str]
    people: list[str]

    @property
    def local_start(self) -> datetime:
        return local_start(self.started_at, self.tz_offset_minutes)

    @property
    def created(self) -> str:
        # Duck-types the legacy Meeting for meetings.render_txt/render_md.
        return self.local_start.replace(tzinfo=None).isoformat(timespec="seconds")


@dataclass
class TranscriptPage:
    segments: list[tuple[int, MeetingSegment]]
    next_cursor: int | None


def _segment(row) -> MeetingSegment:
    return MeetingSegment(
        speaker=row["speaker"],
        start=row["start_seconds"],
        end=row["end_seconds"],
        text=row["text"],
        confidence=row["confidence"],
        overlap=bool(row["overlap"]),
        profile_id=row["profile_id"],
        cluster_id=row["cluster_id"],
    )


def meeting_filters(from_date=None, to_date=None, tag=None, person=None):
    """SQL WHERE fragments over alias `m` plus parameters; shared by list
    and search so both filter identically."""
    clauses, params = [], []
    lower, upper = local_day_bounds(from_date, to_date)
    if lower:
        clauses.append("m.started_at >= ?")
        params.append(lower)
    if upper:
        clauses.append("m.started_at < ?")
        params.append(upper)
    if tag:
        clauses.append(
            "EXISTS (SELECT 1 FROM meeting_tags mt JOIN tags t ON t.id = mt.tag_id"
            " WHERE mt.meeting_id = m.id AND t.name = ?)"
        )
        params.append(tag.strip())
    if person:
        clauses.append(
            "EXISTS (SELECT 1 FROM meeting_people mp JOIN people p"
            " ON p.id = mp.person_id WHERE mp.meeting_id = m.id AND"
            " (p.display_name = ? COLLATE NOCASE OR p.email = ?))"
        )
        params += [person.strip(), person.strip()]
    return (" AND ".join(clauses) or "1"), params


class MeetingLibrary:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @contextmanager
    def _transaction(self):
        conn = meeting_store.connect(self._path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # -- writes -----------------------------------------------------------

    def save_meeting(self, new: NewMeeting) -> str:
        with self._transaction() as conn:
            return self._insert(conn, new)

    def _insert(self, conn, new: NewMeeting) -> str:
        if new.started_at.tzinfo is None:
            raise ValueError("NewMeeting.started_at must be timezone-aware")
        start = new.started_at
        offset = int(start.utcoffset().total_seconds() // 60)
        meeting_id = new.meeting_id or (
            f"{start.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        )
        _check_id(meeting_id)
        title = (new.title or "").strip() or (
            f"Meeting — {start.strftime('%b %-d, %-I:%M %p')}"
        )
        health = {
            k: v for k, v in (new.capture_health or {}).items()
            if k in _CAPTURE_HEALTH_KEYS
            and (v is None or isinstance(v, (bool, int, str)))
        }
        now = _now_iso()
        conn.execute(
            "INSERT INTO meetings (id, title, started_at, tz_offset_minutes,"
            " duration_seconds, capture_mode, system_audio_status, capture_scope,"
            " track_offsets_json, capture_health_json, calendar_event_id, source,"
            " timestamps_approximate, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                meeting_id, title, utc_iso(start), offset,
                float(new.duration_seconds), new.capture_mode,
                new.system_audio_status, new.capture_scope,
                json.dumps({k: round(float(v), 6)
                            for k, v in new.track_offsets_seconds.items()
                            if k in {"mic", "system"}}),
                json.dumps(health), new.calendar_event_id, new.source,
                int(new.timestamps_approximate), now, now,
            ),
        )
        conn.executemany(
            "INSERT INTO segments (meeting_id, idx, speaker, start_seconds,"
            " end_seconds, text, confidence, overlap, profile_id, cluster_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (meeting_id, i, s.speaker, round(s.start, 2), round(s.end, 2),
                 s.text, s.confidence, int(s.overlap), s.profile_id, s.cluster_id)
                for i, s in enumerate(new.segments)
            ],
        )
        return meeting_id

    def _touch(self, conn, meeting_id: str) -> None:
        cur = conn.execute(
            "UPDATE meetings SET updated_at = ? WHERE id = ?",
            (_now_iso(), meeting_id),
        )
        if cur.rowcount == 0:
            raise MeetingNotFound(meeting_id)

    def rename(self, meeting_id: str, title: str) -> None:
        _check_id(meeting_id)
        title = title.strip()
        if not title:
            raise ValueError("Meeting title can't be empty.")
        with self._transaction() as conn:
            self._touch(conn, meeting_id)
            conn.execute("UPDATE meetings SET title = ? WHERE id = ?", (title, meeting_id))

    def relabel_speaker(self, meeting_id: str, segment_index: int, label: str,
                        *, all_matching: bool = False) -> None:
        """Relabel one segment, or every segment sharing its current label."""
        _check_id(meeting_id)
        label = label.strip()
        if not label:
            raise ValueError("Speaker label can't be empty.")
        with self._transaction() as conn:
            self._touch(conn, meeting_id)
            row = conn.execute(
                "SELECT speaker FROM segments WHERE meeting_id = ? AND idx = ?",
                (meeting_id, int(segment_index)),
            ).fetchone()
            if row is None:
                raise IndexError("Invalid meeting segment index.")
            where = "speaker = ?" if all_matching else "idx = ?"
            conn.execute(
                "UPDATE segments SET speaker = ?, profile_id = NULL,"
                f" confidence = NULL WHERE meeting_id = ? AND {where}",
                (label, meeting_id,
                 row["speaker"] if all_matching else int(segment_index)),
            )

    def delete(self, meeting_id: str) -> None:
        _check_id(meeting_id)
        with self._transaction() as conn:
            if conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,)).rowcount == 0:
                raise MeetingNotFound(meeting_id)
            # Orphaned tags would linger in list_tags(); people stay (shared
            # with calendar events).
            conn.execute(
                "DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM meeting_tags)"
            )

    # -- reads ------------------------------------------------------------

    def count_meetings(self) -> int:
        with self._transaction() as conn:
            return conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]

    def meeting_ids(self) -> set[str]:
        with self._transaction() as conn:
            return {r[0] for r in conn.execute("SELECT id FROM meetings")}

    def _tags(self, conn, meeting_id):
        return [r[0] for r in conn.execute(
            "SELECT t.name FROM meeting_tags mt JOIN tags t ON t.id = mt.tag_id"
            " WHERE mt.meeting_id = ? ORDER BY t.name COLLATE NOCASE", (meeting_id,))]

    def _people(self, conn, meeting_id):
        return [r[0] for r in conn.execute(
            "SELECT p.display_name FROM meeting_people mp JOIN people p"
            " ON p.id = mp.person_id WHERE mp.meeting_id = ?"
            " ORDER BY mp.role DESC, p.display_name COLLATE NOCASE", (meeting_id,))]

    def _notes(self, conn, meeting_id):
        row = conn.execute("SELECT * FROM notes WHERE meeting_id = ?", (meeting_id,)).fetchone()
        if row is None:
            return None
        return Notes(row["summary"], json.loads(row["action_items_json"]),
                     row["updated_at"], row["updated_by"])

    def get_meeting(self, meeting_id: str) -> StoredMeeting:
        _check_id(meeting_id)
        with self._transaction() as conn:
            m = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            if m is None:
                raise MeetingNotFound(meeting_id)
            segments = [_segment(r) for r in conn.execute(
                "SELECT * FROM segments WHERE meeting_id = ? ORDER BY idx", (meeting_id,))]
            return StoredMeeting(
                meeting_id=m["id"], title=m["title"], started_at=m["started_at"],
                tz_offset_minutes=m["tz_offset_minutes"],
                duration_seconds=m["duration_seconds"], segments=segments,
                capture_mode=m["capture_mode"],
                system_audio_status=m["system_audio_status"],
                capture_scope=m["capture_scope"],
                track_offsets_seconds=json.loads(m["track_offsets_json"]),
                capture_health=json.loads(m["capture_health_json"]),
                calendar_event_id=m["calendar_event_id"], source=m["source"],
                timestamps_approximate=bool(m["timestamps_approximate"]),
                notes=self._notes(conn, meeting_id),
                tags=self._tags(conn, meeting_id),
                people=self._people(conn, meeting_id),
            )

    def list_meetings(self, *, from_date=None, to_date=None, tag=None,
                      person=None, limit=100, offset=0) -> list[MeetingSummary]:
        where, params = meeting_filters(from_date, to_date, tag, person)
        limit = max(1, min(int(limit), 500))
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT m.*, (SELECT COUNT(DISTINCT speaker) FROM segments s"
                " WHERE s.meeting_id = m.id) AS speaker_count,"
                " EXISTS (SELECT 1 FROM notes n WHERE n.meeting_id = m.id"
                " AND n.summary <> '') AS has_summary"
                f" FROM meetings m WHERE {where}"
                " ORDER BY m.started_at DESC, m.id DESC LIMIT ? OFFSET ?",
                (*params, limit, max(0, int(offset))),
            ).fetchall()
            return [
                MeetingSummary(
                    meeting_id=r["id"], title=r["title"], started_at=r["started_at"],
                    tz_offset_minutes=r["tz_offset_minutes"],
                    duration_seconds=r["duration_seconds"],
                    speaker_count=r["speaker_count"],
                    has_summary=bool(r["has_summary"]),
                    timestamps_approximate=bool(r["timestamps_approximate"]),
                    tags=self._tags(conn, r["id"]),
                    people=self._people(conn, r["id"]),
                )
                for r in rows
            ]

    def transcript_page(self, meeting_id: str, *, start_seconds=None,
                        end_seconds=None, cursor=0, max_chars=20_000) -> TranscriptPage:
        _check_id(meeting_id)
        max_chars = max(_MIN_PAGE_CHARS, min(int(max_chars), _MAX_PAGE_CHARS))
        clauses, params = ["meeting_id = ?", "idx >= ?"], [meeting_id, max(0, int(cursor))]
        if start_seconds is not None:
            clauses.append("end_seconds >= ?")
            params.append(float(start_seconds))
        if end_seconds is not None:
            clauses.append("start_seconds <= ?")
            params.append(float(end_seconds))
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)).fetchone() is None:
                raise MeetingNotFound(meeting_id)
            rows = conn.execute(
                f"SELECT * FROM segments WHERE {' AND '.join(clauses)} ORDER BY idx",
                params,
            ).fetchall()
        page, used = [], 0
        for i, row in enumerate(rows):
            if page and used + len(row["text"]) > max_chars:
                return TranscriptPage(page, rows[i]["idx"])
            page.append((row["idx"], _segment(row)))
            used += len(row["text"])
        return TranscriptPage(page, None)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_meeting_library.py -q` (timeout 300000)
Expected: 9 passed. If the concurrent test reports `database is locked`, confirm that `connect` sets `busy_timeout` *before* `journal_mode`, and that `_SCHEMA_V1` uses `BEGIN IMMEDIATE`.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/meeting_library.py tests/test_meeting_library.py
git commit -m "Add MeetingLibrary core API over the SQLite store"
```

---

### Task 3: Notes, tags and people

**Files:**
- Modify: `speakeasy/meeting_library.py` (append methods to `MeetingLibrary`)
- Test: `tests/test_meeting_library.py` (append)

**Interfaces:**
- Produces: `save_notes(meeting_id, *, summary=None, action_items=None, tags=None, updated_by="claude") -> Notes`; `link_people(meeting_id, people: list[tuple[str, str | None, str]])` where each tuple is `(display_name, email_or_None, "attendee" | "organizer")`; `list_tags() -> list[tuple[str, int]]`; `list_people(query=None) -> list[tuple[str, int]]`. Limits: tags ≤ 20 per meeting, each ≤ 40 chars; action items ≤ 50, each ≤ 500 chars; summary ≤ 20 000 chars. Exceeding a limit raises `ValueError`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_meeting_library.py`)

```python
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
    assert lib.list_meetings(tag="vfa")[0].has_summary is True


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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_meeting_library.py -q -k "notes or people"` (timeout 300000)
Expected: FAIL with `AttributeError: 'MeetingLibrary' object has no attribute 'save_notes'`.

- [ ] **Step 3: Implement** (append inside `class MeetingLibrary`)

```python
    # -- notes, tags, people ---------------------------------------------

    def save_notes(self, meeting_id: str, *, summary=None, action_items=None,
                   tags=None, updated_by="claude") -> Notes:
        """Replace only the fields given; the rest keep their stored value."""
        _check_id(meeting_id)
        if summary is not None:
            summary = str(summary).strip()
            if len(summary) > 20_000:
                raise ValueError("Summary is longer than 20,000 characters.")
        if action_items is not None:
            action_items = [str(a).strip() for a in action_items if str(a).strip()]
            if len(action_items) > 50 or any(len(a) > 500 for a in action_items):
                raise ValueError("At most 50 action items of 500 characters each.")
        if tags is not None:
            tags = _normalise_tags(tags)
        with self._transaction() as conn:
            self._touch(conn, meeting_id)
            current = self._notes(conn, meeting_id) or Notes("", [], "", updated_by)
            summary = current.summary if summary is None else summary
            action_items = current.action_items if action_items is None else action_items
            now = _now_iso()
            conn.execute(
                "INSERT INTO notes (meeting_id, summary, action_items_json,"
                " action_items_text, updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?)"
                # Upsert, not INSERT OR REPLACE: REPLACE deletes without firing
                # the FTS delete trigger, leaving stale search entries.
                " ON CONFLICT(meeting_id) DO UPDATE SET summary = excluded.summary,"
                " action_items_json = excluded.action_items_json,"
                " action_items_text = excluded.action_items_text,"
                " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                (meeting_id, summary, json.dumps(action_items),
                 "\n".join(action_items), now, updated_by),
            )
            if tags is not None:
                conn.execute("DELETE FROM meeting_tags WHERE meeting_id = ?", (meeting_id,))
                for name in tags:
                    conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
                    conn.execute(
                        "INSERT INTO meeting_tags (meeting_id, tag_id)"
                        " SELECT ?, id FROM tags WHERE name = ?", (meeting_id, name))
                conn.execute(
                    "DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM meeting_tags)")
            return self._notes(conn, meeting_id)

    def _person_id(self, conn, name: str, email: str | None) -> int:
        name = name.strip() or (email or "Unknown")
        if email:
            row = conn.execute("SELECT id FROM people WHERE email = ?", (email,)).fetchone()
            if row:
                return row["id"]
            return conn.execute(
                "INSERT INTO people (display_name, email) VALUES (?, ?)",
                (name, email.strip().lower())).lastrowid
        row = conn.execute(
            "SELECT id FROM people WHERE email IS NULL AND display_name = ?"
            " COLLATE NOCASE", (name,)).fetchone()
        if row:
            return row["id"]
        return conn.execute(
            "INSERT INTO people (display_name) VALUES (?)", (name,)).lastrowid

    def link_people(self, meeting_id: str, people) -> None:
        """Replace a meeting's people with [(name, email|None, role)]."""
        _check_id(meeting_id)
        with self._transaction() as conn:
            self._touch(conn, meeting_id)
            conn.execute("DELETE FROM meeting_people WHERE meeting_id = ?", (meeting_id,))
            for name, email, role in people:
                pid = self._person_id(conn, name, email)
                conn.execute(
                    "INSERT OR IGNORE INTO meeting_people (meeting_id, person_id, role)"
                    " VALUES (?, ?, ?)", (meeting_id, pid, role))

    def list_tags(self) -> list[tuple[str, int]]:
        with self._transaction() as conn:
            return [(r[0], r[1]) for r in conn.execute(
                "SELECT t.name, COUNT(*) FROM tags t JOIN meeting_tags mt"
                " ON mt.tag_id = t.id GROUP BY t.id"
                " ORDER BY COUNT(*) DESC, t.name COLLATE NOCASE")]

    def list_people(self, query=None) -> list[tuple[str, int]]:
        where, params = "1", []
        if query:
            where = "(p.display_name LIKE ? OR p.email LIKE ?)"
            params = [f"%{query.strip()}%"] * 2
        with self._transaction() as conn:
            return [(r[0], r[1]) for r in conn.execute(
                "SELECT p.display_name, COUNT(*) FROM people p JOIN meeting_people mp"
                f" ON mp.person_id = p.id WHERE {where} GROUP BY p.id"
                " ORDER BY COUNT(*) DESC, p.display_name COLLATE NOCASE", params)]
```

Add this module-level helper above the class:

```python
def _normalise_tags(tags) -> list[str]:
    seen, result = set(), []
    for raw in tags:
        name = " ".join(str(raw).split())
        if not name or name.lower() in seen:
            continue
        if len(name) > 40:
            raise ValueError("Tags are at most 40 characters.")
        seen.add(name.lower())
        result.append(name)
    if len(result) > 20:
        raise ValueError("At most 20 tags per meeting.")
    return result
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_meeting_library.py -q` (timeout 300000)
Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/meeting_library.py tests/test_meeting_library.py
git commit -m "Add notes, tags and people to the meeting library"
```

---

### Task 4: Search (FTS5 + quoting + echo collapse)

**Files:**
- Modify: `speakeasy/meeting_library.py`
- Test: `tests/test_meeting_search.py`

**Interfaces:**
- Produces: `HIT_OPEN = "\x02"`, `HIT_CLOSE = "\x03"` (snippet highlight markers; consumers convert them); `fts_query(text, *, any_term=False) -> str | None`; `SearchHit(meeting_id, title, started_at, tz_offset_minutes, kind, speaker, start_seconds, end_seconds, segment_index, snippet, also_speakers, score)` where `kind` is `"transcript"` or `"notes"`; `collapse_echoes(hits) -> list[SearchHit]`; `MeetingLibrary.search(query, *, from_date=None, to_date=None, tag=None, person=None, limit=10) -> list[SearchHit]` (limit clamped to 1..50).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_meeting_search.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from speakeasy.meeting_library import (
    HIT_CLOSE, HIT_OPEN, MeetingLibrary, NewMeeting, SearchHit,
    collapse_echoes, fts_query,
)
from speakeasy.meetings import MeetingSegment

EDT = timezone(timedelta(hours=-4))


def _save(lib, day, *segs, **kw):
    return lib.save_meeting(NewMeeting(
        segments=[MeetingSegment(sp, s, e, t) for sp, s, e, t in segs],
        duration_seconds=600, started_at=datetime(2026, 9, day, 13, 0, tzinfo=EDT), **kw))


def test_fts_query_quotes_every_term():
    assert fts_query('C++ "unbalanced AND NEAR( -x') == '"C" "unbalanced" "AND" "NEAR" "x"'
    assert fts_query("rbac cognos", any_term=True) == '"rbac" OR "cognos"'
    assert fts_query("  ?!  ") is None


@pytest.mark.parametrize("query", ['C++', '"', 'AND', 'NEAR(', '-x', '🙂', '', 'a:b*'])
def test_hostile_queries_never_raise(library_path, query):
    lib = MeetingLibrary()
    _save(lib, 24, ("You", 0, 5, "we use C and cognos"))
    assert isinstance(lib.search(query), list)


def test_ranked_hits_with_marked_snippets(library_path):
    lib = MeetingLibrary()
    a = _save(lib, 24, ("You", 0, 5, "cognos reporting is massive"),
              ("Speaker 1", 6, 9, "unrelated chat"))
    _save(lib, 23, ("You", 0, 5, "nothing relevant here"))
    hits = lib.search("Cognos reports")
    assert [h.meeting_id for h in hits] == [a]
    assert hits[0].kind == "transcript" and hits[0].speaker == "You"
    assert hits[0].start_seconds == 0 and hits[0].segment_index == 0
    assert f"{HIT_OPEN}cognos{HIT_CLOSE}" in hits[0].snippet
    assert f"{HIT_OPEN}reporting{HIT_CLOSE}" in hits[0].snippet  # porter stem


def test_all_terms_first_then_any_term_fallback(library_path):
    lib = MeetingLibrary()
    both = _save(lib, 24, ("You", 0, 5, "rbac and cognos gaps"))
    one = _save(lib, 23, ("You", 0, 5, "rbac only"))
    assert [h.meeting_id for h in lib.search("rbac cognos")] == [both]
    assert {h.meeting_id for h in lib.search("rbac sso")} == {both, one}


def test_diacritics_and_filters(library_path):
    lib = MeetingLibrary()
    a = _save(lib, 24, ("You", 0, 5, "lunch at the café"))
    b = _save(lib, 20, ("You", 0, 5, "cafe again"))
    lib.save_notes(a, tags=["DMT"])
    lib.link_people(b, [("Refayet", None, "attendee")])
    assert {h.meeting_id for h in lib.search("cafe")} == {a, b}
    assert [h.meeting_id for h in lib.search("cafe", tag="dmt")] == [a]
    assert [h.meeting_id for h in lib.search("cafe", person="Refayet")] == [b]
    assert [h.meeting_id for h in lib.search("cafe", from_date="2026-09-22")] == [a]


def test_notes_are_searchable(library_path):
    lib = MeetingLibrary()
    a = _save(lib, 24, ("You", 0, 5, "hello"))
    lib.save_notes(a, summary="Recommend sustaining VFA", action_items=["Estimate modernization"])
    hits = lib.search("modernization")
    assert hits[0].kind == "notes" and hits[0].meeting_id == a
    assert hits[0].speaker is None and hits[0].start_seconds is None


def test_echo_across_tracks_collapses_to_one_hit(library_path):
    lib = MeetingLibrary()
    text = "the product ACP is so thin it does not make sense to invest"
    a = _save(lib, 24, ("You", 100, 150, text), ("Speaker 1", 120, 131, text),
              ("Speaker 1", 900, 905, "ACP is thin, I said it again later"))
    hits = lib.search("ACP thin invest")
    assert len([h for h in hits if h.meeting_id == a and h.start_seconds < 200]) == 1
    merged = next(h for h in hits if h.start_seconds < 200)
    assert set([merged.speaker, *merged.also_speakers]) == {"You", "Speaker 1"}


def test_collapse_keeps_different_meetings_and_distant_times():
    def hit(mid, start, end, speaker, snippet):
        return SearchHit(mid, "t", "2026-09-24T17:00:00Z", -240, "transcript",
                         speaker, start, end, 0, snippet, [], -1.0)
    kept = collapse_echoes([
        hit("m1", 0, 10, "You", "ACP is so thin"),
        hit("m1", 12, 14, "Speaker 1", "ACP is so thin"),   # overlaps within 5 s
        hit("m1", 300, 310, "Speaker 1", "ACP is so thin"), # far away: kept
        hit("m2", 0, 10, "You", "ACP is so thin"),          # other meeting: kept
    ])
    assert [(h.meeting_id, h.start_seconds) for h in kept] == [("m1", 0), ("m1", 300), ("m2", 0)]
    assert kept[0].also_speakers == ["Speaker 1"]


def test_limit_is_clamped(library_path):
    lib = MeetingLibrary()
    for day in range(1, 29):
        _save(lib, day, ("You", 0, 5, "standup notes"))
    assert len(lib.search("standup", limit=500)) == 28
    assert len(lib.search("standup", limit=0)) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_meeting_search.py -q` (timeout 300000)
Expected: FAIL with `ImportError: cannot import name 'HIT_CLOSE'`.

Note: `test_limit_is_clamped` expects 28 because the result count is capped by the data, not the limit of 50.

- [ ] **Step 3: Implement** (module level in `meeting_library.py`, plus a method)

```python
import re
from difflib import SequenceMatcher

HIT_OPEN, HIT_CLOSE = "\x02", "\x03"
_MAX_QUERY_TERMS = 12
_ECHO_SLACK_SECONDS = 5.0
_ECHO_MIN_RATIO = 0.8


def fts_query(text: str, *, any_term: bool = False) -> str | None:
    """Turn free text into a safe FTS5 query: every word becomes a quoted
    string, so FTS operators and punctuation in user or Claude input can
    never raise a syntax error or change the query's meaning."""
    terms = re.findall(r"\w+", str(text))[:_MAX_QUERY_TERMS]
    if not terms:
        return None
    return (" OR " if any_term else " ").join(f'"{t}"' for t in terms)


@dataclass
class SearchHit:
    meeting_id: str
    title: str
    started_at: str
    tz_offset_minutes: int
    kind: str
    speaker: str | None
    start_seconds: float | None
    end_seconds: float | None
    segment_index: int | None
    snippet: str
    also_speakers: list[str]
    score: float


def _plain(snippet: str) -> str:
    return snippet.replace(HIT_OPEN, "").replace(HIT_CLOSE, "").lower()


def collapse_echoes(hits: list[SearchHit]) -> list[SearchHit]:
    """Merge a passage that both tracks transcribed (in-room speaker mode)
    into one result. Stored transcripts are never changed; only results."""
    kept: list[SearchHit] = []
    for hit in hits:
        twin = None
        if hit.kind == "transcript":
            for k in kept:
                if (k.kind == "transcript" and k.meeting_id == hit.meeting_id
                        and k.start_seconds - _ECHO_SLACK_SECONDS <= hit.end_seconds
                        and hit.start_seconds - _ECHO_SLACK_SECONDS <= k.end_seconds
                        and SequenceMatcher(None, _plain(k.snippet),
                                            _plain(hit.snippet)).ratio() >= _ECHO_MIN_RATIO):
                    twin = k
                    break
        if twin is None:
            kept.append(hit)
        elif hit.speaker != twin.speaker and hit.speaker not in twin.also_speakers:
            twin.also_speakers.append(hit.speaker)
    return kept
```

Inside `MeetingLibrary`:

```python
    def search(self, query: str, *, from_date=None, to_date=None, tag=None,
               person=None, limit=10) -> list[SearchHit]:
        limit = max(1, min(int(limit), 50))
        where, params = meeting_filters(from_date, to_date, tag, person)
        with self._transaction() as conn:
            for any_term in (False, True):
                match = fts_query(query, any_term=any_term)
                if match is None:
                    return []
                hits = self._search(conn, match, where, params, limit * 4)
                if hits:
                    break
        hits.sort(key=lambda h: h.score)
        return collapse_echoes(hits)[:limit]

    def _search(self, conn, match, where, params, fetch) -> list[SearchHit]:
        snippet = f"snippet({{t}}, -1, char(2), char(3), '…', 24)"
        hits = [
            SearchHit(r["id"], r["title"], r["started_at"], r["tz_offset_minutes"],
                      "transcript", r["speaker"], r["start_seconds"],
                      r["end_seconds"], r["idx"], r["snip"], [], r["score"])
            for r in conn.execute(
                "SELECT m.id, m.title, m.started_at, m.tz_offset_minutes, s.speaker,"
                " s.start_seconds, s.end_seconds, s.idx,"
                f" {snippet.format(t='segments_fts')} AS snip,"
                " bm25(segments_fts) AS score FROM segments_fts"
                " JOIN segments s ON s.rowid = segments_fts.rowid"
                " JOIN meetings m ON m.id = s.meeting_id"
                f" WHERE segments_fts MATCH ? AND {where} ORDER BY score LIMIT ?",
                (match, *params, fetch))
        ]
        hits += [
            SearchHit(r["id"], r["title"], r["started_at"], r["tz_offset_minutes"],
                      "notes", None, None, None, None, r["snip"], [], r["score"])
            for r in conn.execute(
                "SELECT m.id, m.title, m.started_at, m.tz_offset_minutes,"
                f" {snippet.format(t='notes_fts')} AS snip,"
                " bm25(notes_fts) AS score FROM notes_fts"
                " JOIN notes n ON n.rowid = notes_fts.rowid"
                " JOIN meetings m ON m.id = n.meeting_id"
                f" WHERE notes_fts MATCH ? AND {where} ORDER BY score LIMIT ?",
                (match, *params, fetch))
        ]
        return hits
```

Move the `import re` and `from difflib import SequenceMatcher` lines to the module's import block.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_meeting_search.py -q` (timeout 300000)
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/meeting_library.py tests/test_meeting_search.py
git commit -m "Add ranked meeting search with safe query quoting and echo collapse"
```

---

### Task 5: Mic segmentation rule (review issue 2)

**Files:**
- Modify: `speakeasy/config.py` (append constants)
- Modify: `speakeasy/meetings.py` (`known_speaker_segments`, ~line 480)
- Test: `tests/test_dual_track_meeting.py` (append)

**Interfaces:**
- Produces: `config.KNOWN_SPEAKER_MAX_GAP_SECONDS = 1.5`, `config.KNOWN_SPEAKER_MAX_SEGMENT_SECONDS = 60.0`; `known_speaker_segments(sentences, speaker="You", *, max_gap_seconds=None, max_segment_seconds=None)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_dual_track_meeting.py`; it already defines `Sentence`)

```python
def test_known_track_splits_on_pause_longer_than_gap():
    segments = meetings.known_speaker_segments(
        [Sentence(0.0, 1.0, "one"), Sentence(2.5, 3.0, "two"),   # 1.5 s gap: merge
         Sentence(4.6, 5.0, "three")]                            # 1.6 s gap: split
    )
    assert [(s.start, s.end, s.text) for s in segments] == [
        (0.0, 3.0, "one two"), (4.6, 5.0, "three")]


def test_known_track_caps_segment_length():
    sentences = [Sentence(i * 10.0, i * 10.0 + 9.5, f"s{i}") for i in range(13)]
    segments = meetings.known_speaker_segments(sentences)
    assert all(s.end - s.start <= 60.0 for s in segments)
    assert " ".join(s.text for s in segments) == " ".join(f"s{i}" for i in range(13))
    assert len(segments) == 3
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_dual_track_meeting.py -q -k known_track` (timeout 300000)
Expected: FAIL (everything merges into one segment).

- [ ] **Step 3: Implement**

`speakeasy/config.py`, append:

```python
# The known local (mic) track has no diarization turns to break it up, so
# without a limit a whole meeting became one segment timestamped 00:00:00.
# Split on real pauses and cap length so search can cite a moment.
KNOWN_SPEAKER_MAX_GAP_SECONDS = 1.5
KNOWN_SPEAKER_MAX_SEGMENT_SECONDS = 60.0
```

Replace `known_speaker_segments` in `speakeasy/meetings.py`:

```python
def known_speaker_segments(
    sentences,
    speaker: str = "You",
    *,
    max_gap_seconds: float | None = None,
    max_segment_seconds: float | None = None,
) -> list[MeetingSegment]:
    """Convert one known-source ASR track without running diarization.

    Consecutive sentences merge unless the pause between them exceeds
    max_gap_seconds or the merged segment would exceed max_segment_seconds.
    """
    gap = config.KNOWN_SPEAKER_MAX_GAP_SECONDS if max_gap_seconds is None else max_gap_seconds
    cap = (config.KNOWN_SPEAKER_MAX_SEGMENT_SECONDS
           if max_segment_seconds is None else max_segment_seconds)
    segments = []
    for sentence in sentences or []:
        text = sentence.text.strip()
        if not text:
            continue
        start, end = float(sentence.start), float(sentence.end)
        last = segments[-1] if segments else None
        if (last is not None and last.speaker == speaker
                and start - last.end <= gap and end - last.start <= cap):
            last.text = (last.text + " " + text).strip()
            last.end = end
        else:
            segments.append(MeetingSegment(speaker=speaker, start=start, end=end, text=text))
    return segments
```

- [ ] **Step 4: Run to verify pass, including the existing test**

Run: `.venv/bin/python -m pytest tests/test_dual_track_meeting.py -q` (timeout 300000)
Expected: all pass (`test_known_mic_track_is_always_you` has a 0.2 s gap and still merges).

- [ ] **Step 5: Commit**

```bash
git add speakeasy/config.py speakeasy/meetings.py tests/test_dual_track_meeting.py
git commit -m "Split the known mic track on pauses and cap segment length"
```

---

### Task 6: Import legacy JSON meetings (split, verify, archive)

**Files:**
- Modify: `speakeasy/meeting_library.py` (add `import_meetings`)
- Create: `speakeasy/meeting_import.py`
- Test: `tests/test_meeting_import.py`

**Interfaces:**
- Consumes: `meetings.Meeting.load`, `meetings._ID_RE`, `settings.meetings_dir`, `MeetingLibrary`, `NewMeeting`.
- Produces: `MeetingLibrary.import_meetings(batch: list[NewMeeting], expected: dict[str, tuple[int, frozenset[str]]]) -> None` (one transaction; raises `ImportVerificationError` and rolls back on mismatch); `meeting_import.ImportVerificationError(RuntimeError)`, defined in `meeting_library` and re-exported; `meeting_import.ImportReport(imported: int, already_present: int, skipped: list[tuple[str, str]])`; `meeting_import.LEGACY_DIR_NAME = "legacy-json"`; `legacy_files() -> list[Path]`; `split_long_segment(segment, *, threshold_seconds=120.0, target_seconds=60.0) -> list[MeetingSegment]`; `import_json_meetings(library=None, progress=None) -> ImportReport`, where `progress(done: int, total: int)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_meeting_import.py`:

```python
import json
import re
from datetime import datetime

import pytest

from speakeasy import meeting_import
from speakeasy.meeting_import import (
    ImportVerificationError, import_json_meetings, legacy_files, split_long_segment,
)
from speakeasy.meeting_library import MeetingLibrary
from speakeasy.meetings import MeetingSegment


def _nonspace(text):
    return re.sub(r"\s", "", text)


def _write(meetings_dir, meeting_id, created="2026-09-24T13:40:23", duration=1380.0,
           segments=None, **extra):
    data = {"id": meeting_id, "title": f"Meeting {meeting_id}", "created": created,
            "duration_seconds": duration,
            "segments": segments if segments is not None else [
                {"speaker": "You", "start": 0.0, "end": 5.0, "text": "hello"},
                {"speaker": "Speaker 1", "start": 5.0, "end": 9.0, "text": "hi"}],
            **extra}
    path = meetings_dir / f"{meeting_id}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_short_segment_is_unchanged():
    seg = MeetingSegment("You", 0.0, 100.0, "short. text.")
    assert split_long_segment(seg) == [seg]


def test_long_segment_splits_at_sentences_with_interpolated_times():
    text = " ".join(f"Sentence number {i} is here." for i in range(200))
    seg = MeetingSegment("You", 10.0, 910.0, text, confidence=0.5, cluster_id=3)
    pieces = split_long_segment(seg)
    assert len(pieces) >= 14
    assert pieces[0].start == 10.0 and pieces[-1].end == 910.0
    assert all(a.end == pytest.approx(b.start) for a, b in zip(pieces, pieces[1:]))
    assert all(p.end - p.start <= 66.0 for p in pieces)
    assert _nonspace("".join(p.text for p in pieces)) == _nonspace(text)
    assert all(p.text.endswith(".") for p in pieces[:-1])        # sentence boundaries
    assert {(p.speaker, p.confidence, p.cluster_id) for p in pieces} == {("You", 0.5, 3)}


def test_long_segment_without_punctuation_splits_by_words():
    text = " ".join(["word"] * 3000)
    pieces = split_long_segment(MeetingSegment("You", 0.0, 600.0, text))
    assert len(pieces) >= 10
    assert _nonspace("".join(p.text for p in pieces)) == _nonspace(text)


def test_import_happy_path(meetings_dir):
    a = _write(meetings_dir, "20260924-134023-aaaa")
    _write(meetings_dir, "20260923-100000-bbbb", created="2026-09-23T10:00:00",
           duration=60.0, capture_mode="mic_and_system")
    (meetings_dir / "notes.json").write_text("{}")             # not a meeting id
    seen = []
    report = import_json_meetings(progress=lambda d, t: seen.append((d, t)))
    assert (report.imported, report.already_present, report.skipped) == (2, 0, [])
    assert seen == [(1, 2), (2, 2)]
    lib = MeetingLibrary()
    m = lib.get_meeting("20260924-134023-aaaa")
    assert m.local_start.replace(tzinfo=None) == datetime(2026, 9, 24, 13, 17, 23)
    assert m.timestamps_approximate and m.source == "imported_json"
    assert m.title == "Meeting 20260924-134023-aaaa"
    assert not a.exists()
    assert (meetings_dir / "legacy-json" / a.name).exists()
    assert (meetings_dir / "notes.json").exists()
    assert legacy_files() == []


def test_malformed_file_is_skipped_reported_and_left(meetings_dir):
    _write(meetings_dir, "20260924-134023-aaaa")
    bad = _write(meetings_dir, "20260924-140000-cccc", segments=["not a dict"])
    report = import_json_meetings()
    assert report.imported == 1
    assert report.skipped == [(bad.name, "TypeError")]
    assert bad.exists()


def test_verification_failure_rolls_back_and_moves_nothing(meetings_dir, monkeypatch):
    path = _write(meetings_dir, "20260924-134023-aaaa")
    def lossy(segment, **kw):
        return [MeetingSegment(segment.speaker, segment.start, segment.end, segment.text[:-1])]
    monkeypatch.setattr(meeting_import, "split_long_segment", lossy)
    with pytest.raises(ImportVerificationError):
        import_json_meetings()
    assert MeetingLibrary().count_meetings() == 0
    assert path.exists()


def test_rerun_after_crash_between_commit_and_archive(meetings_dir):
    path = _write(meetings_dir, "20260924-134023-aaaa")
    import_json_meetings()
    (meetings_dir / "legacy-json" / path.name).rename(path)   # simulate un-archived file
    report = import_json_meetings()
    assert (report.imported, report.already_present) == (0, 1)
    assert MeetingLibrary().count_meetings() == 1
    assert not path.exists()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_meeting_import.py -q` (timeout 300000)
Expected: FAIL with `ModuleNotFoundError: speakeasy.meeting_import`.

- [ ] **Step 3: Implement**

In `speakeasy/meeting_library.py`, add near `MeetingNotFound`:

```python
class ImportVerificationError(RuntimeError):
    pass


def nonspace_len(text: str) -> int:
    return len(re.sub(r"\s", "", text))
```

and inside `MeetingLibrary`:

```python
    def import_meetings(self, batch, expected) -> None:
        """Insert a batch in one transaction and verify it against what the
        caller read from the source files (non-whitespace character count and
        speaker set per meeting) before committing. Any mismatch raises and
        the whole import rolls back."""
        if set(expected) != {n.meeting_id for n in batch}:
            raise ImportVerificationError("batch and expectations differ")
        with self._transaction() as conn:
            for new in batch:
                self._insert(conn, new)
            for meeting_id, (chars, speakers) in expected.items():
                rows = conn.execute(
                    "SELECT speaker, text FROM segments WHERE meeting_id = ?",
                    (meeting_id,)).fetchall()
                got = (sum(nonspace_len(r["text"]) for r in rows),
                       frozenset(r["speaker"] for r in rows))
                if got != (chars, speakers):
                    raise ImportVerificationError(f"content mismatch in {meeting_id}")
```

Create `speakeasy/meeting_import.py`:

```python
"""One-time import of legacy JSON meetings into the SQLite library.

The JSON files stay untouched until the whole batch has been inserted and
verified in one transaction; only then are they moved, unchanged, to
meetings/legacy-json/. Files that fail to parse stay where they are and are
reported. Re-running is safe: meetings already in the library are not
inserted again, only archived.

Legacy `created` stamps were taken when processing *finished*, so the start
is estimated as created − duration and every imported meeting is marked
timestamps_approximate.
"""

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import meetings, settings
from .meeting_library import (
    ImportVerificationError, MeetingLibrary, NewMeeting, nonspace_len,
)
from .meetings import MeetingSegment

__all__ = ["ImportVerificationError", "ImportReport", "LEGACY_DIR_NAME",
           "legacy_files", "split_long_segment", "import_json_meetings"]

LEGACY_DIR_NAME = "legacy-json"


@dataclass
class ImportReport:
    imported: int = 0
    already_present: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)


def legacy_files():
    return sorted(
        p for p in settings.meetings_dir().glob("*.json")
        if meetings._ID_RE.fullmatch(p.stem)
    )


def split_long_segment(segment, *, threshold_seconds=120.0, target_seconds=60.0):
    duration = segment.end - segment.start
    text = segment.text.strip()
    if duration <= threshold_seconds or not text:
        return [segment]
    budget = max(1, int(len(text) * target_seconds / duration))
    pieces, current = [], []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        parts = sentence.split() if len(sentence) > budget else [sentence]
        for part in parts:
            if current and len(" ".join(current + [part])) > budget:
                pieces.append(" ".join(current))
                current = []
            current.append(part)
    if current:
        pieces.append(" ".join(current))
    per_char = duration / sum(len(p) for p in pieces)
    result, cursor = [], segment.start
    for i, piece in enumerate(pieces):
        end = segment.end if i == len(pieces) - 1 else cursor + len(piece) * per_char
        result.append(MeetingSegment(
            speaker=segment.speaker, start=cursor, end=end, text=piece,
            confidence=segment.confidence, overlap=segment.overlap,
            profile_id=segment.profile_id, cluster_id=segment.cluster_id))
        cursor = end
    return result


def _convert(legacy) -> NewMeeting:
    if legacy.created:
        finished = datetime.fromisoformat(legacy.created)
    else:
        finished = datetime.strptime(legacy.meeting_id[:15], "%Y%m%d-%H%M%S")
    if finished.tzinfo is None:
        finished = finished.astimezone()  # the Mac's zone on that date (DST-aware)
    segments = [
        piece for seg in legacy.segments for piece in split_long_segment(seg)
    ]
    return NewMeeting(
        segments=segments,
        duration_seconds=legacy.duration_seconds,
        started_at=finished - timedelta(seconds=legacy.duration_seconds),
        title=legacy.title,
        meeting_id=legacy.meeting_id,
        capture_mode=legacy.capture_mode,
        system_audio_status=legacy.system_audio_status,
        capture_scope=legacy.capture_scope,
        track_offsets_seconds=legacy.track_offsets_seconds,
        capture_health=legacy.capture_health,
        source="imported_json",
        timestamps_approximate=True,
    )


def import_json_meetings(library=None, progress=None) -> ImportReport:
    library = library or MeetingLibrary()
    progress = progress or (lambda done, total: None)
    files = legacy_files()
    present = library.meeting_ids()
    report = ImportReport()
    batch, expected, to_archive = [], {}, []
    for done, path in enumerate(files, 1):
        try:
            legacy = meetings.Meeting.load(path.stem)
            if legacy.meeting_id in present:
                report.already_present += 1
            else:
                batch.append(_convert(legacy))
                expected[legacy.meeting_id] = (
                    sum(nonspace_len(s.text) for s in legacy.segments),
                    frozenset(s.speaker for s in legacy.segments),
                )
            to_archive.append(path)
        except Exception as err:  # hand-edited JSON can fail in any way
            report.skipped.append((path.name, type(err).__name__))
        progress(done, len(files))
    library.import_meetings(batch, expected)
    report.imported = len(batch)
    archive = settings.meetings_dir() / LEGACY_DIR_NAME
    archive.mkdir(exist_ok=True)
    for path in to_archive:
        os.replace(path, archive / path.name)
    return report
```

The verification-failure test monkeypatches `meeting_import.split_long_segment`. `_convert` looks the name up in the module globals at call time, so the patch takes effect. Keep that reference as a plain module-level name.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_meeting_import.py -q` (timeout 300000)
Expected: 7 passed.

- [ ] **Step 5: Mutation spot-check**

Temporarily change the verification line in `import_meetings` from `if got != (chars, speakers):` to `if False:`. Run the import tests and confirm `test_verification_failure_rolls_back_and_moves_nothing` FAILS. Revert.

- [ ] **Step 6: Commit**

```bash
git add speakeasy/meeting_library.py speakeasy/meeting_import.py tests/test_meeting_import.py
git commit -m "Import legacy JSON meetings with verification and archiving"
```

---

### Task 7: Engine: real start time, save through the library, library upgrade job

**Files:**
- Modify: `speakeasy/meeting_recorder.py` (`MeetingRecording`, frozen dataclass ~line 86)
- Modify: `speakeasy/engine.py` (`__init__` ~line 122, `_begin_meeting` ~line 780, `_end_meeting` ~line 783, `_process_meeting` ~lines 1095–1117)
- Modify: `speakeasy/ui/menubar.py` (engine wiring ~line 614)
- Modify: `tests/test_engine_meeting.py`
- Test: `tests/test_engine_meeting.py` (append)

**Interfaces:**
- Consumes: `MeetingLibrary`, `NewMeeting`, `meeting_import.import_json_meetings`, `meeting_import.legacy_files`.
- Produces: `MeetingRecording.started_at: datetime | None = None`; `engine.library: MeetingLibrary`; `engine.library_status: dict`, shaped `{"state": "idle"|"upgrading"|"done"|"failed", "done": int, "total": int, "skipped": [{"file": str, "reason": str}]}`; `engine.on_library_status: Callable[[dict], None]`; `engine.upgrade_library() -> None`. `on_meeting_saved(meeting_id)` is unchanged.

- [ ] **Step 1: Move the existing tests to the library**

```bash
sed -i '' 's/meetings\.Meeting\.load(/MeetingLibrary().get_meeting(/g' tests/test_engine_meeting.py
```

Add `from speakeasy.meeting_library import MeetingLibrary` to that file's imports. Run `grep -n "Meeting.load\|meetings_dir.glob\|\.json" tests/test_engine_meeting.py` and convert any remaining JSON-file assertions to `MeetingLibrary().list_meetings()` / `count_meetings()`.

- [ ] **Step 2: Write the failing new tests** (append)

```python
def test_meeting_start_time_is_capture_start(meetings_dir, spool_dir):
    from datetime import datetime
    engine = _engine(spool_dir)
    saved = []
    engine.on_meeting_saved = saved.append
    before = datetime.now().astimezone().replace(microsecond=0)
    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    after_begin = datetime.now().astimezone()
    time.sleep(1.1)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    stored = MeetingLibrary().get_meeting(saved[0])
    assert before <= stored.local_start <= after_begin
    assert stored.source == "recorded" and not stored.timestamps_approximate
    engine.shutdown()


def test_upgrade_library_imports_and_publishes_status(meetings_dir, spool_dir):
    import json as _json
    (meetings_dir / "20260924-134023-aaaa.json").write_text(_json.dumps({
        "id": "20260924-134023-aaaa", "title": "t", "created": "2026-09-24T13:40:23",
        "duration_seconds": 60, "segments": [
            {"speaker": "You", "start": 0, "end": 1, "text": "hi"}]}))
    engine = _engine(spool_dir)
    statuses = []
    engine.on_library_status = statuses.append
    engine.upgrade_library()
    assert _wait_for(lambda: statuses and statuses[-1]["state"] == "done")
    assert statuses[0] == {"state": "upgrading", "done": 1, "total": 1, "skipped": []}
    assert statuses[-1] == {"state": "done", "done": 1, "total": 1, "skipped": []}
    assert engine.library_status == statuses[-1]
    assert MeetingLibrary().count_meetings() == 1
    engine.shutdown()


def test_upgrade_library_is_a_no_op_without_legacy_files(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    statuses = []
    engine.on_library_status = statuses.append
    engine.upgrade_library()
    time.sleep(0.2)
    assert statuses == [] and engine.library_status["state"] == "idle"
    engine.shutdown()
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_engine_meeting.py -q` (timeout 300000)
Expected: the three new tests FAIL (`AttributeError: ... 'upgrade_library'`, and the start time is after `after_begin`). Converted older tests FAIL because `get_meeting` raises `MeetingNotFound` (the engine still writes JSON).

- [ ] **Step 4: Implement**

`meeting_recorder.py`, add a last field to `MeetingRecording` (import `datetime` at the top):

```python
    # Wall-clock capture start (timezone-aware), stamped by the engine; the
    # saved meeting's start time and calendar match both come from here.
    started_at: datetime | None = None
```

`engine.py`:
- Imports: `import dataclasses`, `from datetime import datetime, timedelta`, `from . import meeting_import`, `from .meeting_library import MeetingLibrary, NewMeeting`.
- In `__init__` next to `self.on_meeting_saved`:

```python
        self.library = MeetingLibrary()
        self.library_status = {"state": "idle", "done": 0, "total": 0, "skipped": []}
        self.on_library_status: Callable[[dict], None] = lambda status: None
        self._meeting_started_at = None
```

- In `_begin_meeting`, immediately before `self._set_state(State.MEETING_RECORDING)`:

```python
        self._meeting_started_at = datetime.now().astimezone()
```

- In `_end_meeting`, right after `recording = self._stop_meeting_recorder_guarded()`:

```python
        if recording is not None and self._meeting_started_at is not None:
            recording = dataclasses.replace(recording, started_at=self._meeting_started_at)
```

- In `_process_meeting`, replace the `meeting = meetings.Meeting.new(...)` block through `self.on_meeting_saved(meeting.meeting_id)` with:

```python
            started_at = recording.started_at or (
                datetime.now().astimezone() - timedelta(seconds=duration)
            )
            new_meeting = NewMeeting(
                segments=segments,
                duration_seconds=duration,
                started_at=started_at,
                capture_mode=capture_mode,
                system_audio_status=system_status,
                track_offsets_seconds=offsets,
                capture_health=(recording.health.to_dict() if recording.health else {}),
                capture_scope=recording.capture_scope,
            )
            if self._meeting_cancel.is_set():
                raise MeetingCancelled
            report_progress("Saving transcript…")
            if timing is not None:
                timing.start("save")
            try:
                meeting_id = self.library.save_meeting(new_meeting)
            finally:
                if timing is not None:
                    timing.finish("save")
            print(f"  → meeting saved: {meeting_id} ({len(segments)} segments)")
            self.on_meeting_saved(meeting_id)
```

- Add these methods next to `begin_meeting`:

```python
    def upgrade_library(self) -> None:
        """Import legacy JSON meetings once. Queued on `worker` behind model
        load so it never races meeting processing, which also runs there."""
        if meeting_import.legacy_files():
            self.worker.submit(self._upgrade_library)

    def _publish_library_status(self, status: dict) -> None:
        self.library_status = status
        self.on_library_status(status)

    def _upgrade_library(self) -> None:
        def progress(done, total):
            self._publish_library_status(
                {"state": "upgrading", "done": done, "total": total, "skipped": []})
        try:
            report = meeting_import.import_json_meetings(self.library, progress=progress)
        except Exception:
            traceback.print_exc()
            self._publish_library_status(
                {"state": "failed", "done": 0, "total": 0, "skipped": []})
            return
        total = report.imported + report.already_present + len(report.skipped)
        self._publish_library_status({
            "state": "done",
            "done": report.imported + report.already_present,
            "total": total,
            "skipped": [{"file": f, "reason": r} for f, r in report.skipped],
        })
```

`ui/menubar.py`, next to `on_meeting_saved`. The status crosses threads as a JSON string so pyobjc passes a plain `str`:

```python
        def on_library_status(status):
            controller.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"libraryStatus:", json.dumps(status), False
            )

        engine.on_library_status = on_library_status
```

After `engine.start()`, add `engine.upgrade_library()`. Add a `libraryStatus_(self, payload)` method to the delegate class that owns `meetingSaved_` (~line 356):

```python
    def libraryStatus_(self, payload):
        if self.meetings_window is not None:
            self.meetings_window.libraryStatus_(payload)
```

`MeetingsWindowController.libraryStatus_` is added in Task 10. Until then, guard it with `hasattr`. Add `import json` to menubar if absent.

- [ ] **Step 5: Run the engine tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_engine_meeting.py -q` then `.venv/bin/python -m pytest -q` (timeout 300000 each)
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add speakeasy/meeting_recorder.py speakeasy/engine.py speakeasy/ui/menubar.py tests/test_engine_meeting.py
git commit -m "Save meetings to the library with their real start time; upgrade legacy JSON"
```

---

### Task 8: CLI commands and Markdown export

**Files:**
- Create: `speakeasy/meeting_export.py`
- Modify: `speakeasy/__main__.py` (argparse block and dispatch, before `sweep_temporary_audio()` returns into the app path)
- Test: `tests/test_meeting_export.py`

**Interfaces:**
- Produces: `meeting_export.safe_filename(title: str, fallback: str) -> str`; `render_export_md(stored: StoredMeeting) -> str`; `export_all(folder: Path, library=None) -> int`; CLI `--import-json-meetings`, `--rebuild-index`, `--export-meetings DIR`.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_meeting_export.py -q` (timeout 300000)
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement** `speakeasy/meeting_export.py`

```python
"""Plain-text way out of the SQLite library (`--export-meetings`): one
Markdown file per meeting, for backups and troubleshooting."""

import re
from pathlib import Path

from .meeting_library import MeetingLibrary
from .meetings import render_md

_UNSAFE = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


def safe_filename(title: str, fallback: str) -> str:
    name = _UNSAFE.sub("-", title).strip(" .")
    name = re.sub(r"\s+", " ", name)[:120].strip(" .")
    return name or fallback


def render_export_md(stored) -> str:
    body = render_md(stored)
    title_line, _, rest = body.partition("\n")
    extra = []
    if stored.notes and stored.notes.summary:
        extra += ["## Summary", "", stored.notes.summary, ""]
    if stored.notes and stored.notes.action_items:
        extra += ["## Action items", ""] + [f"- [ ] {a}" for a in stored.notes.action_items] + [""]
    if stored.tags:
        extra += [f"Tags: {', '.join(stored.tags)}", ""]
    if stored.people:
        extra += [f"People: {', '.join(stored.people)}", ""]
    if stored.timestamps_approximate:
        extra += ["_Timestamps are approximate (imported meeting)._", ""]
    if extra:
        extra += ["## Transcript", ""]
    return "\n".join([title_line, *([""] + extra if extra else []), rest.lstrip("\n")])


def export_all(folder: Path, library=None) -> int:
    library = library or MeetingLibrary()
    folder = Path(folder).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    count = 0
    offset = 0
    while batch := library.list_meetings(limit=500, offset=offset):
        for summary in batch:
            stored = library.get_meeting(summary.meeting_id)
            name = safe_filename(stored.title, stored.meeting_id)
            day = stored.local_start.strftime("%Y-%m-%d")
            (folder / f"{day} {name}.md").write_text(render_export_md(stored), encoding="utf-8")
            count += 1
        offset += len(batch)
    return count
```

If two meetings on the same day share a title, append ` (2)`, ` (3)` before `.md`. Add a test `test_export_disambiguates_same_day_titles` asserting both files exist, and implement it by checking `path.exists()` in a loop.

`__main__.py`: add to the parser

```python
    parser.add_argument("--import-json-meetings", action="store_true",
                        help="import legacy JSON meetings into the library")
    parser.add_argument("--rebuild-index", action="store_true",
                        help="rebuild meeting search and the calendar cache")
    parser.add_argument("--export-meetings", metavar="DIR",
                        help="write every meeting as Markdown into DIR")
```

and dispatch right after `args = parser.parse_args()`:

```python
    if args.import_json_meetings or args.rebuild_index or args.export_meetings:
        from . import meeting_store
        from .meeting_export import export_all
        from .meeting_import import import_json_meetings

        if args.import_json_meetings:
            report = import_json_meetings()
            print(f"Imported {report.imported}, already present {report.already_present}, "
                  f"skipped {len(report.skipped)}.")
            for name, reason in report.skipped:
                print(f"  skipped {name}: {reason}")
        if args.rebuild_index:
            conn = meeting_store.connect()
            meeting_store.rebuild_derived(conn)
            conn.close()
            print("Rebuilt meeting search index and cleared the calendar cache.")
        if args.export_meetings:
            print(f"Exported {export_all(Path(args.export_meetings))} meeting(s).")
        return
```

- [ ] **Step 4: Run tests and a CLI smoke check against a temp library**

Run: `.venv/bin/python -m pytest tests/test_meeting_export.py -q` (timeout 300000). Expected: pass.
Run: `HOME=$(mktemp -d) .venv/bin/python -m speakeasy --rebuild-index`. Expected: prints the rebuild line and exits 0 (a throwaway `HOME` keeps it off the real library).

- [ ] **Step 5: Commit**

```bash
git add speakeasy/meeting_export.py speakeasy/__main__.py tests/test_meeting_export.py
git commit -m "Add meeting import, index rebuild and Markdown export commands"
```

---

### Task 9: UI design: mock every screen, then STOP for approval

This task builds the whole redesigned UI **against mock data only**, for all four phases, so the user approves the complete design once. Nothing here talks to the bridge. Later tasks wire it up.

**Files:**
- Modify: `frontend/src/mock/meetings.ts` (types + realistic mock data)
- Modify: `frontend/src/meetings/App.tsx`, `App.module.css`
- Create: `frontend/src/meetings/Sidebar.tsx`, `MeetingList.tsx`, `MeetingDetail.tsx`, `SearchResults.tsx`, `TodayView.tsx`, `SpeakerPopover.tsx`, `ConfirmSheet.tsx`, `ConnectClaudeSheet.tsx`, `SettingsSheet.tsx`, `LibraryBanner.tsx`, `EmptyState.tsx`, each with a `.module.css`
- Create: `frontend/prompt.html`, `frontend/src/prompt/main.tsx`, `frontend/src/prompt/App.tsx`, `App.module.css`
- Modify: `frontend/vite.config.ts` (add `prompt` input), `frontend/src/dock/App.tsx` (recording label, mock only)

**Interfaces:**
- Produces the TypeScript contract that Task 10's Python must emit exactly:

```ts
export interface TranscriptLine {
  time: string; speakerNumber: number; speakerLabel: string; text: string;
  segmentIndex: number; confidence: number | null; overlap: boolean;
  start: number;                       // seconds, for jump-to
}
export interface MeetingMeta {
  id: string; title: string; dayLabel: string; time: string; duration: string;
  subtitle: string; speakerCount: number; hasSummary: boolean; approximate: boolean;
  tags: string[]; people: string[];
}
export interface MeetingDetail extends MeetingMeta {
  date: string; lines: TranscriptLine[];
  summary: string | null; actionItems: string[];
  event: { title: string; time: string } | null;        // phase 3; null until then
}
export interface Filters {
  total: number; tags: { name: string; count: number }[];
  people: { name: string; count: number }[];
  features: { calendar: boolean; claude: boolean; settings: boolean };
}
export interface SearchResult {
  meetingId: string; title: string; dayLabel: string; time: string;
  kind: 'transcript' | 'notes'; speaker: string | null; alsoSpeakers: string[];
  seconds: number | null; segmentIndex: number | null;
  parts: { text: string; hit: boolean }[];
}
export interface LibraryStatus {
  state: 'idle' | 'upgrading' | 'done' | 'failed';
  done: number; total: number; skipped: { file: string; reason: string }[];
}
export interface AgendaEvent {                 // phase 3
  key: string; time: string; title: string; attendeeCount: number;
  status: 'recorded' | 'recording' | 'record' | 'none'; meetingId: string | null;
}
```

**Design rules (from the spec's UI/UX section; follow exactly):**
- Window mock size 1040×660 (`GlassPanel width={1040} height={660}`). Columns: sidebar 200 px, list 280 px, detail flexes. Use existing tokens only: `--glass-sidebar` for the sidebar, `--glass-pane` for the list, `--hairline-lo` dividers, `--text-hi` titles, `--text-mid` secondary, `--coral-1` for the selected row and primary buttons, `--rec-red` for recording and "now", `--sp-*` for speakers.
- Type: window title 13 px semibold; sidebar section headings 11 px uppercase `--text-lo`; row title 13 px, sub 11 px; detail title 22 px semibold; body 13 px/1.5.
- Sidebar: TODAY › Today (phase 3); LIBRARY › All Meetings (count); TAGS › each tag (count); PEOPLE › each person (count); footer › "Connect Claude" row + gear (Settings). A section is hidden when its list is empty or its `features` flag is false.
- List: sticky day headers (`dayLabel`), rows with time · title, subtitle line, summary dot (6 px `--coral-1`) when `hasSummary`. ↑/↓ changes the selection when the list has focus.
- Detail header: title (double-click or Return → inline rename); `date · duration` line; event chip (📅 title ⌄) when `event`; people as initials circles (max 4 + "+n", `title` attribute lists all names); tags as capsules (`--glass-fill`, 11 px); "≈ approximate times" note when `approximate`.
- Segmented control *Summary | Transcript* (Apple segmented look: 28 px high, `--glass-fill-2` track, `--glass-fill` selected thumb). Summary is the default when `summary` is non-null.
- Summary empty state: "No summary yet. Ask Claude to summarise this meeting." + **Copy prompt** (copies `Summarise and tag my Speakeasy meeting "<title>" (<id>) and save the notes.`).
- Transcript: current coloured lines; `≈` before times when approximate; a find bar opened by ⌘F when the detail has focus (input + "n of m" + ‹ › + Done).
- Speaker click → `SpeakerPopover`, anchored under the name: text field (prefilled), checkbox "Rename all ‘<label>’ in this meeting", buttons Cancel / **Rename** (Return = Rename, Esc = Cancel).
- Actions row: **Copy**, **Export**, and a "···" menu with Rename…, Delete…. Delete and ⌘⌫ open `ConfirmSheet`: title "Delete ‘<title>’?", body "The transcript and notes will be removed. This can't be undone.", Cancel (default, focused) / **Delete** (`--red-2`).
- Toolbar search field top-right of the detail column ("Search meetings", ⌘F when the list has focus). Debounced 250 ms. Results replace the list, one row per hit: title · dayLabel, speaker (+ "also: …"), snippet with `<mark>` for `hit` parts (mark style: `--coral-1` at 30% alpha background, no colour change). Esc clears.
- `LibraryBanner` at the top of the list: upgrading "Upgrading your meeting library… 42 of 92" + thin progress bar; done "92 meetings upgraded" (fades after 4 s); failed "Your meetings are safe and unchanged. The upgrade will try again next launch." + Show Details (lists `file — reason`); skipped files add "3 files couldn't be imported · Show Details".
- Today view (detail area, list hidden): agenda rows per the spec (Recorded ✓ / Recording… red dot / **Record** button / nothing), red "now" line, collapsed "Upcoming" days. Permission cards: not connected ("See your meetings here…" + **Connect Calendar**), denied ("Calendar access is off." + **Open Privacy Settings**), empty ("Nothing on your calendar today.").
- `ConnectClaudeSheet`: two sections (Claude Code, Claude Desktop), each with a monospace block and **Copy**; "Reveal config file" link-button for Desktop; the privacy sentence from the spec; status line "Last used by Claude: 2 min ago" / "Not used yet".
- `SettingsSheet`: toggle "Offer to record calendar meetings"; calendars checklist grouped by account; **Export all meetings…**.
- Record banner (`prompt.html`, 360×92): icon, title, "Starting now · 3 people", Not Now / **Record**; slide-in from the right 200 ms (opacity only under `prefers-reduced-motion`).
- Dock mock state "recording-linked": "Recording · Weekly 1:1 — Refayet · 12:03" with event menu.
- Accessibility: every icon-only control has `aria-label`; list uses `role="listbox"`/`option` with `aria-selected`; visible `:focus-visible` rings (2 px `--coral-1`); `@media (prefers-reduced-motion: reduce)` disables transitions; `@media (prefers-contrast: more)` raises hairlines to `--hairline`; `@media (prefers-reduced-transparency: reduce)` swaps glass fills for solid `#1b1f1d`.
- Mock state switch: `?state=` URL parameter read in each `App` when `!bridge.embedded`. Values: `default`, `empty`, `upgrading`, `upgrade-failed`, `search`, `no-summary`, `popover`, `delete`, `today`, `today-denied`, `today-unconnected`, `connect-claude`, `settings` (meetings); `default` (prompt); `recording-linked` (dock).

- [ ] **Step 1: Types and mock data.** Replace the interfaces in `frontend/src/mock/meetings.ts` with the contract above, keep `SPEAKER_PALETTE`/`speakerColor`, and export `MOCK_METAS` (≥ 8 meetings over Today, Yesterday, a weekday and last month; ≥ 2 with summaries/tags/people; 1 approximate), `MOCK_DETAILS`, `MOCK_FILTERS`, `MOCK_RESULTS`, `MOCK_STATUS`, `MOCK_AGENDA`. Use invented names (Alex, Priya, Sam), not people from the reference transcript.
- [ ] **Step 2: Components.** Build each file listed above following the design rules, with `App.tsx` composing them and reading `?state=`. Keep bridge calls out of this task.
- [ ] **Step 3: Record banner page.** Add `prompt.html` (copy `meetings.html`, point at `/src/prompt/main.tsx`) and the `prompt` input in `vite.config.ts`.
- [ ] **Step 4: Build.** Run `npm --prefix frontend run build` (timeout 300000). Expected: no TypeScript errors.
- [ ] **Step 5: Screenshots.** Start `npm --prefix frontend run dev` as a background process. Open each `?state=` URL in the built-in browser (`mcp__Claude_Browser__preview_start` with the dev URL), resize to 1040×660 (the banner to 360×92 at 2×), and save a screenshot per state to the scratchpad. Also capture `default` with the emulated `prefers-contrast: more` and reduced-transparency states if the browser supports them; otherwise note that it doesn't.
- [ ] **Step 6: STOP: user design review.** Send all screenshots to the user in one message with a one-line caption each. **Do not start Task 10 until the user approves.** Apply requested changes, rebuild, re-screenshot the changed states, and ask again.
- [ ] **Step 7: Commit** (after approval; the dev server is stopped)

```bash
git add frontend
git commit -m "Design the Apple-style Meetings window, Today view, sheets and record banner (mock)"
```

---

### Task 10: Python bridge for the redesigned Meetings window

**Files:**
- Modify: `speakeasy/ui/webbridge.py` (`segments_to_lines` adds `start`; add `day_label`, `snippet_parts`)
- Create: `speakeasy/ui/meetings_bridge.py`
- Modify: `speakeasy/ui/meetings_window.py`, `speakeasy/ui/glass.py`, `speakeasy/ui/webwindow.py`
- Modify: `tests/test_webbridge.py`
- Test: `tests/test_meetings_bridge.py`

**Interfaces:**
- Consumes: `MeetingLibrary` API (Tasks 2–4), `HIT_OPEN/HIT_CLOSE`, Task 9's TS contract.
- Produces: `day_label(local_start: datetime, now: datetime) -> str`; `snippet_parts(snippet: str) -> list[dict]`; `MeetingsBridge(library=None, now=None)` with `register(dispatcher)` and handler methods for `meetings.list`, `meetings.filters`, `meetings.get`, `meetings.search`, `meetings.rename`, `meetings.relabelSpeaker`, `meetings.delete`, `library.status`; `MeetingsBridge.set_library_status(status: dict)`; `glass.make_glass_window(..., resizable=False, min_size=None)`; `WebWindow(..., resizable=False, min_size=None)`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_webbridge.py`, add `"start": 0.0` (the `Seg` start value) to the expected dict in the existing `segments_to_lines` test. Then add:

```python
from datetime import datetime, timedelta, timezone
from speakeasy.ui.webbridge import day_label, snippet_parts

TZ = timezone(timedelta(hours=-4))
NOW = datetime(2026, 9, 24, 15, 0, tzinfo=TZ)   # Thursday


@pytest.mark.parametrize("when,label", [
    (datetime(2026, 9, 24, 0, 5, tzinfo=TZ), "Today"),
    (datetime(2026, 9, 23, 23, 50, tzinfo=TZ), "Yesterday"),
    (datetime(2026, 9, 21, 9, 0, tzinfo=TZ), "Monday"),
    (datetime(2026, 9, 17, 9, 0, tzinfo=TZ), "September 2026"),
    (datetime(2025, 12, 1, 9, 0, tzinfo=TZ), "December 2025"),
    (datetime(2026, 9, 25, 9, 0, tzinfo=TZ), "Today"),            # clock skew
])
def test_day_label(when, label):
    assert day_label(when, NOW) == label


def test_snippet_parts():
    assert snippet_parts("…the \x02ACP\x03 is \x02thin\x03") == [
        {"text": "…the ", "hit": False}, {"text": "ACP", "hit": True},
        {"text": " is ", "hit": False}, {"text": "thin", "hit": True}]
```

Create `tests/test_meetings_bridge.py`:

```python
from datetime import datetime, timedelta, timezone

from speakeasy.meeting_library import MeetingLibrary, NewMeeting
from speakeasy.meetings import MeetingSegment
from speakeasy.ui.meetings_bridge import MeetingsBridge
from speakeasy.ui.webbridge import BridgeDispatcher

TZ = timezone(timedelta(hours=-4))


def _call(dispatcher, method, params=None):
    out = []
    dispatcher.dispatch({"id": 1, "method": method, "params": params or {}}, out.append)
    return out[-1]


def _setup(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0, 5, "cognos reporting"),
                  MeetingSegment("Speaker 1", 6, 9, "agreed")],
        duration_seconds=1380, started_at=datetime(2026, 9, 24, 13, 17, tzinfo=TZ),
        title="1:1 Alex"))
    lib.save_notes(mid, summary="Invest in VFA.", tags=["VFA"])
    bridge = MeetingsBridge(lib, now=lambda: datetime(2026, 9, 24, 15, 0, tzinfo=TZ))
    d = BridgeDispatcher()
    bridge.register(d)
    return lib, mid, bridge, d


def test_list_and_get_shapes(library_path):
    lib, mid, bridge, d = _setup(library_path)
    metas = bridge.list_payload({})
    assert metas == [{
        "id": mid, "title": "1:1 Alex", "dayLabel": "Today", "time": "1:17 PM",
        "duration": "23 min", "subtitle": "23 min · 2 speakers", "speakerCount": 2,
        "hasSummary": True, "approximate": False, "tags": ["VFA"], "people": []}]
    detail = bridge.get_payload({"id": mid})
    assert detail["date"] == "Thu 24 Sep 2026 · 1:17 PM"
    assert detail["summary"] == "Invest in VFA." and detail["actionItems"] == []
    assert detail["event"] is None
    assert detail["lines"][0]["start"] == 0.0


def test_filters_and_search(library_path):
    lib, mid, bridge, d = _setup(library_path)
    f = bridge.filters_payload({})
    assert f["total"] == 1 and f["tags"] == [{"name": "VFA", "count": 1}]
    assert f["features"] == {"calendar": False, "claude": False, "settings": False}
    res = bridge.search_payload({"query": "cognos"})
    assert res[0]["meetingId"] == mid and res[0]["seconds"] == 0.0
    assert {"text": "cognos", "hit": True} in res[0]["parts"]
    assert bridge.search_payload({"query": "   "}) == []


def test_missing_meeting_is_not_found_error(library_path):
    lib, mid, bridge, d = _setup(library_path)
    lib.delete(mid)
    js = _call(d, "meetings.get", {"id": mid})
    assert "_reject" in js and "not_found" in js


def test_relabel_and_delete_round_trip(library_path):
    lib, mid, bridge, d = _setup(library_path)
    detail = bridge.relabel_payload({"id": mid, "segmentIndex": 1, "label": "Alex",
                                     "allMatching": True})
    assert detail["lines"][1]["speakerLabel"] == "Alex"
    assert bridge.delete_payload({"id": mid}) == []


def test_library_status(library_path):
    lib, mid, bridge, d = _setup(library_path)
    assert bridge.status_payload({})["state"] == "idle"
    bridge.set_library_status({"state": "upgrading", "done": 1, "total": 2, "skipped": []})
    assert bridge.status_payload({})["done"] == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_webbridge.py tests/test_meetings_bridge.py -q` (timeout 300000)
Expected: FAIL (imports missing).

- [ ] **Step 3: Implement**

`webbridge.py`: in `segments_to_lines`, add `"start": float(seg.start),` to each dict. Append:

```python
def day_label(local_start, now) -> str:
    """List group header in the Mail/Notes style, by *local* calendar day."""
    days = (now.date() - local_start.date()).days
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 7:
        return local_start.strftime("%A")
    return local_start.strftime("%B %Y")


def snippet_parts(snippet: str) -> list[dict]:
    parts, hit = [], False
    for piece in re.split("([\x02\x03])", snippet):
        if piece == "\x02":
            hit = True
        elif piece == "\x03":
            hit = False
        elif piece:
            parts.append({"text": piece, "hit": hit})
    return parts
```

(add `import re` at the top of `webbridge.py`).

Create `speakeasy/ui/meetings_bridge.py`:

```python
"""Pure-Python handlers behind the Meetings window (no AppKit), so the
payload shapes the React page depends on are unit-tested. The ObjC
controller in meetings_window.py only adds Copy/Export and window glue."""

from datetime import datetime

from speakeasy.meeting_library import MeetingLibrary, MeetingNotFound
from speakeasy.ui.webbridge import day_label, segments_to_lines, snippet_parts

_IDLE = {"state": "idle", "done": 0, "total": 0, "skipped": []}


def _minutes(seconds: float) -> int:
    return max(1, round(seconds / 60))


class MeetingsBridge:
    def __init__(self, library=None, now=None):
        self.library = library or MeetingLibrary()
        self._now = now or (lambda: datetime.now().astimezone())
        self._status = dict(_IDLE)

    def register(self, dispatcher) -> None:
        for method, fn in {
            "meetings.list": self.list_payload,
            "meetings.filters": self.filters_payload,
            "meetings.get": self.get_payload,
            "meetings.search": self.search_payload,
            "meetings.rename": self.rename_payload,
            "meetings.relabelSpeaker": self.relabel_payload,
            "meetings.delete": self.delete_payload,
            "library.status": self.status_payload,
        }.items():
            dispatcher.register(method, self._wrap(fn))

    @staticmethod
    def _wrap(fn):
        def handler(params, respond):
            try:
                respond(fn(params or {}))
            except MeetingNotFound:
                respond(error="not_found")
            except (ValueError, IndexError) as err:
                respond(error=str(err))
        return handler

    # -- payloads (shapes = frontend/src/mock/meetings.ts) ----------------

    def _meta(self, m) -> dict:
        start = m.local_start
        minutes = _minutes(m.duration_seconds)
        speakers = "speaker" if m.speaker_count == 1 else "speakers"
        return {
            "id": m.meeting_id, "title": m.title,
            "dayLabel": day_label(start, self._now()),
            "time": start.strftime("%-I:%M %p"), "duration": f"{minutes} min",
            "subtitle": f"{minutes} min · {m.speaker_count} {speakers}",
            "speakerCount": m.speaker_count, "hasSummary": m.has_summary,
            "approximate": m.timestamps_approximate, "tags": m.tags, "people": m.people,
        }

    def list_payload(self, params) -> list[dict]:
        return [self._meta(m) for m in self.library.list_meetings(
            tag=params.get("tag") or None, person=params.get("person") or None, limit=500)]

    def filters_payload(self, params) -> dict:
        return {
            "total": self.library.count_meetings(),
            "tags": [{"name": n, "count": c} for n, c in self.library.list_tags()],
            "people": [{"name": n, "count": c} for n, c in self.library.list_people()],
            # Flipped on by phases 2–4 as each feature ships.
            "features": {"calendar": False, "claude": False, "settings": False},
        }

    def get_payload(self, params) -> dict:
        m = self.library.get_meeting(str(params.get("id", "")))
        speaker_count = len({s.speaker for s in m.segments})
        summary_like = type("S", (), {})()
        for key in ("meeting_id", "title", "duration_seconds", "tags", "people",
                    "timestamps_approximate", "local_start"):
            setattr(summary_like, key, getattr(m, key))
        summary_like.speaker_count = speaker_count
        summary_like.has_summary = bool(m.notes and m.notes.summary)
        detail = self._meta(summary_like)
        detail.update({
            "date": m.local_start.strftime("%a %-d %b %Y · %-I:%M %p"),
            "lines": segments_to_lines(m.segments),
            "summary": (m.notes.summary or None) if m.notes else None,
            "actionItems": m.notes.action_items if m.notes else [],
            "event": None,
        })
        return detail

    def search_payload(self, params) -> list[dict]:
        results = []
        for h in self.library.search(str(params.get("query", "")), limit=50):
            from speakeasy.meeting_library import local_start
            start = local_start(h.started_at, h.tz_offset_minutes)
            results.append({
                "meetingId": h.meeting_id, "title": h.title,
                "dayLabel": day_label(start, self._now()),
                "time": start.strftime("%-I:%M %p"), "kind": h.kind,
                "speaker": h.speaker, "alsoSpeakers": h.also_speakers,
                "seconds": h.start_seconds, "segmentIndex": h.segment_index,
                "parts": snippet_parts(h.snippet),
            })
        return results

    def rename_payload(self, params) -> list[dict]:
        self.library.rename(str(params.get("id", "")), str(params.get("title", "")))
        return self.list_payload({})

    def relabel_payload(self, params) -> dict:
        self.library.relabel_speaker(
            str(params.get("id", "")), int(params.get("segmentIndex", -1)),
            str(params.get("label", "")), all_matching=bool(params.get("allMatching")))
        return self.get_payload(params)

    def delete_payload(self, params) -> list[dict]:
        self.library.delete(str(params.get("id", "")))
        return self.list_payload({})

    def status_payload(self, params) -> dict:
        return dict(self._status)

    def set_library_status(self, status: dict) -> None:
        self._status = dict(status)
```

Clean-up while implementing: replace the ad-hoc `summary_like` object in `get_payload` with a small helper `_meta_fields(meeting_id, title, local_start, duration_seconds, speaker_count, has_summary, approximate, tags, people)` that both `_meta` and `get_payload` call, and move the `local_start` import to the top. The tests pin the output shape, so the refactor is safe.

`glass.make_glass_window`: add `resizable: bool = False, min_size: tuple[float, float] | None = None`; when `resizable`, OR `NSWindowStyleMaskResizable` into `style`; when `min_size`, call `window.setContentMinSize_(NSMakeSize(*min_size))`. `WebWindow.__init__`: accept the same two keyword arguments and pass them through. The webview already autoresizes.

`meetings_window.py`: keep the `copy` and `export` handlers (convert them to `self._bridge.library.get_meeting` and `meetings.render_txt/render_md`, which accept `StoredMeeting`). Build `self._bridge = MeetingsBridge()`, call `self._bridge.register(dispatcher)`, and create `WebWindow("Meetings", 1040, 660, "meetings", dispatcher, resizable=True, min_size=(820, 520))`. Add:

```python
    def libraryStatus_(self, payload):
        self._bridge.set_library_status(json.loads(str(payload)))
        self._web.emit("library.progress", self._bridge.status_payload({}))
        if self._bridge.status_payload({})["state"] == "done":
            self._web.emit("meetings.changed")
```

Check `self._web.emit`'s signature in `webwindow.py`. If `emit` takes only an event name, add an optional payload argument that is JSON-encoded the same way `_js_call` does. The menubar delegate should then always call `libraryStatus_` when a window exists, and when it doesn't, store the latest status so a newly created window starts with it. Implement this with `self._pending_library_status`, passed to the controller after creation. Remove the Task 7 `hasattr` guard.

- [ ] **Step 4: Run tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_webbridge.py tests/test_meetings_bridge.py -q`, then `.venv/bin/python -m pytest -q` (timeout 300000 each)
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/ui tests/test_webbridge.py tests/test_meetings_bridge.py
git commit -m "Serve the redesigned Meetings window from the library via a tested bridge"
```

---

### Task 11: Wire the approved screens to the bridge

**Files:**
- Modify: `frontend/src/meetings/*.tsx` (from Task 9), `frontend/src/bridge.ts` (only if `on` needs payload typing)

**Interfaces:**
- Consumes: bridge methods and events from Task 10 (`meetings.list/filters/get/search/rename/relabelSpeaker/delete/copy/export`, `library.status`; events `meetings.changed`, `library.progress`).

- [ ] **Step 1: Data flow.** When `bridge.embedded`: load `meetings.filters` + `meetings.list` + `library.status` on mount; re-load list and filters on `meetings.changed`; update the banner on `library.progress`. When not embedded, keep the Task 9 mocks and `?state=` switch unchanged.
- [ ] **Step 2: Selection and detail.** Selecting a row calls `meetings.get`. A rejected call with `not_found` re-lists and selects the first row (Review Focus 2). Sidebar tag/person selection passes `{tag}` / `{person}` to `meetings.list`. Today, Connect Claude and the gear stay hidden because `features` are false.
- [ ] **Step 3: Search.** 250 ms debounce → `meetings.search`. Choosing a result calls `meetings.get`, switches to Transcript, scrolls the line whose `segmentIndex` matches (or the nearest `start` for notes hits → Summary tab), and adds a 1.2 s highlight class (none under reduced motion). Esc clears and restores the list.
- [ ] **Step 4: Edits.** Popover Rename → `meetings.relabelSpeaker` → replace detail. Inline title rename → `meetings.rename`. Delete sheet → `meetings.delete` → re-list, select the next row. Copy/Export → existing methods. "Copy prompt" copies with `navigator.clipboard.writeText`. If that is rejected in WKWebView, call `meetings.copyText` instead: add a two-line handler to `meetings_window.py` using `injector.set_clipboard`.
- [ ] **Step 5: Build.** `npm --prefix frontend run build` (timeout 300000). Expected: no errors.
- [ ] **Step 6: Commit**

```bash
git add frontend speakeasy/ui/meetings_window.py
git commit -m "Wire the Meetings window to the meeting library"
```

---

### Task 12: Docs, full verification, installed-app check

**Files:**
- Modify: `AGENTS.md`, `README.md`

- [ ] **Step 1: AGENTS.md.** Replace the "Only the transcript is persisted … `meetings.py`" sentence with: meetings persist to SQLite at `settings.library_path()` via `MeetingLibrary` (`meeting_library.py`, `meeting_store.py`); legacy JSON is imported once by `meeting_import.py` and archived to `meetings/legacy-json/`; never write meeting JSON again. Add bullets: every `MeetingLibrary` call opens its own connection (never share across threads/processes); FTS and the calendar cache are derived (`--rebuild-index`); the capture-health whitelist still applies at save; the mic track is split at 1.5 s pauses / 60 s.
- [ ] **Step 2: README.** In "Meeting transcription", describe the library, search, `≈` approximate imported times, the three CLI commands, and where the database lives.
- [ ] **Step 3: Full suite + frontend build**

Run: `.venv/bin/python -m pytest -q` and `npm --prefix frontend run build` (timeout 300000 each). Expected: all pass. Record the test count.

- [ ] **Step 4: Back up the real library inputs before first launch**

```bash
cp -R ~/Library/Application\ Support/Speakeasy/meetings /private/tmp/claude-501/speakeasy-meetings-backup-$(date +%Y%m%d)
```

- [ ] **Step 5: Build and install in place, then launch** (`run` skill): `scripts/build_app.sh --install`, open Speakeasy, open the Meetings window. Check and report each item:
  1. The upgrade banner appears; the final count equals the number of legacy files (92 at spec time) minus any reported skipped files; `meetings/legacy-json/` holds the imported files.
  2. The list is grouped by day; old meetings show `≈`; the window resizes to its 820×520 minimum.
  3. Search "cognos" (or any word from a recent meeting) returns ranked results with highlighted snippets; clicking one jumps to the line.
  4. Clicking a speaker opens the popover, and renaming works (closes review issue 6).
  5. Delete a throwaway test recording through the sheet.
  6. Record a 1-minute test meeting: its title shows the start time, not the end time, and "You" appears in several segments rather than one.
  7. Dictation still works once in TextEdit (smoke check only; insertion code is untouched).
- [ ] **Step 6: Commit docs**

```bash
git add AGENTS.md README.md
git commit -m "Document the SQLite meeting library"
```

- [ ] **Step 7: Update this plan's checkboxes, note any deviations under "Execution notes" at the end of this file, and stop for the phase-boundary checkpoint** (per CLAUDE.md). Phase 2 starts in a fresh session.

---

## Phase 2: Local MCP server (expand at session start)

Scope: `speakeasy/mcp_server.py` (stdlib JSON-RPC 2.0 over stdio), `--mcp` early dispatch in `__main__.py` (before argparse, like `--inference-probe`), a Claude Desktop Extension (`.mcpb`) for one-click install, the Connect Claude sheet, README setup.

1. **Frozen-bundle stdio spike (first, throwaway).** Build the app, run `printf '...initialize...' | /Applications/Speakeasy.app/Contents/MacOS/Speakeasy --mcp`, and confirm the response arrives on stdout. If the windowed PyInstaller executable does not pass stdio, add a console executable to `packaging/Speakeasy.spec` for `--mcp` and re-test.
2. **Protocol core.** `initialize` (echo the client's `protocolVersion` if it is in `{"2025-06-18", "2025-03-26", "2024-11-05"}`, else `2025-06-18`; capabilities `{"tools": {}}`), `notifications/initialized`, `ping`, `tools/list`, `tools/call`, JSON-RPC errors `-32700/-32600/-32601/-32602`. Keep the real stdout for protocol messages and set `sys.stdout = sys.stderr` before importing anything that prints. Tests run a subprocess over pipes, including a tool that `print()`s.
3. **Tools** over `MeetingLibrary`, exactly as the spec table says: `list_meetings`, `get_meeting`, `search_meetings`, `get_transcript` (cursor = `TranscriptPage.next_cursor`), `get_calendar` (reads `calendar_events`; returns `[]` until phase 3), `list_tags`, `list_people`, `save_notes` (`updated_by="claude"`). Snippet markers become `**…**`. Capture health is never returned. Errors come back as `isError: true` with a short message.
4. **"Last used by Claude."** The server writes a UTC timestamp to `settings.app_support_dir()/mcp_last_used` on each `tools/call`, which `claude.setupInfo` reads.
5. **App side.** Flip `features.claude` on; add `claude.setupInfo` to the bridge (exact command, JSON snippet, executable path, last used); `ConnectClaudeSheet` wired; the Meetings window polls `MeetingLibrary.updated_marker()` (new: `max(updated_at)` across meetings and notes) every 10 s while visible and emits `meetings.changed` when it changes.
6. **Claude Desktop Extension (`.mcpb`, one-click install).**
   - Create `packaging/mcpb/`:
     - `manifest.json`: name `speakeasy`, display name "Speakeasy Meetings", version = app version, `compatibility.platforms: ["darwin"]`, and one tool entry per MCP tool.
     - `server/speakeasy-mcp`: a 3-line `sh` launcher that execs `/Applications/Speakeasy.app/Contents/MacOS/Speakeasy --mcp`, or the console executable from step 1. It prints a clear stderr message and exits 1 if the app is missing.
     - `package.json`: pins `@anthropic-ai/mcpb` exactly, as a build-time-only dev dependency, like node for the frontend.
   - **Verify the manifest fields against the current MCPB spec at session start**, and pin the `manifest_version` it documents. The field names here are from memory.
   - `scripts/build_app.sh` runs `mcpb validate`, then `mcpb pack`, and copies `Speakeasy.mcpb` into `Speakeasy.app/Contents/Resources/`. `scripts/verify_release.py` fails if it is missing.
   - The `.mcpb` does not bundle the app, a model or any data. It is only a launcher and a manifest, so it contains nothing private.
   - Connect Claude sheet:
     - **Install in Claude Desktop** opens the bundled `.mcpb` with `NSWorkspace` (Claude Desktop shows its own install dialog; local, no network).
     - The manual JSON snippet stays under "Other ways to connect".
     - The status line notes that the extension can be turned off in Claude Desktop → Settings → Extensions.
   - Tests:
     - `manifest.json` lists exactly the tools `tools/list` returns (names and descriptions).
     - The launcher path in `server/speakeasy-mcp` equals the path `claude.setupInfo` reports.
7. **Acceptance.** Install via the `.mcpb` in Claude Desktop (and via `claude mcp add` in Claude Code); list, search, read a page, and save notes that appear in the window within 10 s. Uninstall the extension and confirm the manual JSON route also works. Connect Claude Code (`claude mcp add speakeasy -- /Applications/Speakeasy.app/Contents/MacOS/Speakeasy --mcp`) and Claude Desktop, then list, search, read a page, and save notes that appear in the window within 10 s.

## Phase 3: Calendar (expand at session start)

Scope: `pyobjc-framework-EventKit==12.2.1` (requirements + lock file), `NSCalendarsFullAccessUsageDescription` in `packaging/Speakeasy.spec`, `speakeasy/calendar_match.py` (pure), `speakeasy/calendar_sync.py` (EventKit wrapper faked in tests), a single-thread `calendar` executor (documented in AGENTS.md's threading model).

1. `calendar_match.pick_event(events, recording_start)` and `prompt_candidates(events, now, prompted)`, with the rules from the spec; tests for overlap, back-to-back, declined, all-day, early join, and ties.
2. `MeetingLibrary.replace_calendar_window(events, window_start, window_end)`, `calendar_events_between(start, end)`, `link_event(meeting_id, event_key | None)` (sets title and people from the event).
3. `calendar_sync`: access request, sync of the included calendars (90 days back / 14 ahead) on launch, wake, `EKEventStoreChangedNotification`, and every 5 minutes; never stores notes, location or URLs (test asserts that the fake event's notes/location never reach the DB); handles denied access.
4. Engine: at `_begin_meeting`, `pick_event` from the cached table (no EventKit call on `control`); `_process_meeting` saves `calendar_event_id`, title and people.
5. UI: flip `features.calendar` and `features.settings`; `calendar.today/upcoming/requestAccess/openPrivacySettings`, `meetings.linkEvent`, `settings.meetings.get/set` (persist via `settings.py`); Today view, event chip, Dock recording label, Settings sheet.
6. Acceptance: grant access; today's Exchange events appear; a recorded meeting takes the event title and attendees.

## Phase 4: Record prompt (expand at session start)

Scope: `speakeasy/ui/record_prompt.py` hosting `prompt.html` in a **non-activating `NSPanel`** (`NSWindowStyleMaskNonactivatingPanel`, `setBecomesKeyOnlyIfNeeded_(True)`, level `NSStatusWindowLevel`), top-right under the menu bar.

1. A 30 s main-thread timer asks `calendar_match.prompt_candidates` (Phase 3) with the prompted-event set held in memory and persisted per day; the setting toggle is respected; no prompt during a meeting or processing.
2. Record → `engine.begin_meeting(MeetingOptions(calendar_event_key=...))` (new option field); Not Now or 5 minutes → dismissed for that event; "1 more" menu for overlapping events.
3. Tests: eligibility, dismissal persistence, no prompt while recording, and that Record passes the event key.
4. Acceptance: a real meeting start shows the banner while Teams keeps keyboard focus (type in Teams after it appears); Record starts a linked recording; nothing records without a click.

## Execution notes

(Record deviations, surprises and review findings here during execution.)

### Phase 1 progress (27–28 Sep 2026, branch `meeting-library-phase1`, worktree `.claude/worktrees/meeting-library-phase1`)

**Done and review-clean (Opus review with mutation checks):** Tasks 1–8, and Task 9 Steps 1–5 (split into 9a and 9b). Python suite: 489 passed. Frontend build is clean. **Task 9 design approved by the user on 28 Sep 2026** (open questions about search in the Today view and the transcript gap left as-is). The screenshots are in the session scratchpad, and they can be regenerated from the dev server with any `?state=` value.

**Decisions made during execution** (pre-flight scan and reviews; these change the text above):
- Schema v1: `segments` and `notes` have an explicit `id INTEGER PRIMARY KEY`, used as the FTS `content_rowid` (VACUUM-safe). `notes.meeting_id` is `UNIQUE`.
- `connect()` retries the WAL pragma on "locked" when a fresh file is opened concurrently. The file is pre-created with mode 0600.
- `meetings.filter_capture_health()` is shared by `Meeting` and the library.
- Search snippets are 30 tokens (the spec), not 24. A `None` or empty query returns `[]`.
- Import:
  - A title that exactly equals the old default title (computed from `created`) is regenerated from the estimated start.
  - Value types are validated per file, so a bad file is skipped rather than blocking the whole import.
  - An existing archive is never overwritten; a unique name is used instead.
- Auto-import runs only when the library is empty (per the spec).
  - A failed upgrade publishes `skipped=[{"file":"","reason":<ErrorType>}]`.
  - Skipped files are logged by name and category only.
- A save failure is logged with frames and the error type only; the exception message is never logged (privacy).
- Export:
  - Collisions are tracked per run, keyed on NFC + casefold (APFS is insensitive to case and normalisation).
  - Re-exporting overwrites the previous export's files.
- Frontend:
  - New tokens: `--surface-elevated` (opaque), `--scrim` and others.
  - A shared `Sheet` component and `useFocusTrap` hook.
  - Extra mock state `upgrade-skipped`.
  - Older list groups are headed by month.
  - `AgendaEvent` gains `endTime` (phase 3 contract).
  - Mock "now" in the Today view is 4:29 PM.

**For Task 10/11 (next session):**
- 9a removed every bridge call from `meetings/App.tsx`, so the installed window is inert until Tasks 10–11 restore list/get/rename/delete/relabel/copy/export and `meetings.changed`. Do not install before Task 12.
- Delete the dead `Meeting` JSON write paths (`new`, `save`, `rename`, `delete`, `relabel_speaker`, `list_meetings`) and prune `tests/test_meetings.py`.
- Open design question for the user: search is not reachable while the Today view is shown.

**Deferred minors, to triage at the final whole-branch review:**
- `collapse_echoes` inner guard is untested.
- Extreme hand-edited legacy values (`cluster_id` > int64; year-1 `created`) still abort the whole import batch.
- `_upgrade_library` still uses `traceback.print_exc()`, whose message may contain a path.
- ⌘F refocus and editable-target guard.
- No-op media-query rules in the dock, Switch and ConnectClaude CSS.
- Pre-existing raw colours in `dock/App.module.css`.
- The full list is in the SDD ledger (`.superpowers/sdd/2026-09-27-meeting-library-mcp/progress.md`, git-ignored).
