"""SQLite storage for the meeting library: connection setup and schema.

The database is the master copy of meetings, segments, notes, tags and
people. The FTS tables and the calendar cache are derived and can always be
rebuilt (rebuild_derived / `--rebuild-index`). WAL plus a busy timeout let
the app and the MCP server process write safely at the same time; every
caller opens its own short-lived connection rather than sharing one across
threads or processes.

Every schema statement uses IF NOT EXISTS so two processes migrating a
fresh file at the same moment cannot fail each other.

Fresh-file WAL race: when several connections open a brand-new file at once,
`PRAGMA journal_mode = WAL` can raise "database is locked" immediately, even
though busy_timeout is set — SQLite raises that one synchronously rather
than handing it to the busy handler. connect() retries setting WAL (sleeping
briefly between tries, within the same 5s busy budget) until it either
succeeds or the file already reports 'wal' from a connection that won the
race.
"""

import os
import sqlite3
import time
from pathlib import Path

from . import settings

SCHEMA_VERSION = 2

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
    id INTEGER PRIMARY KEY,
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
-- content_rowid is pinned to the explicit `id` column (not the implicit
-- rowid): schema v1 is frozen once shipped, and a VACUUM can renumber
-- implicit rowids out from under an FTS index built against them.
CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
    text, speaker, content='segments', content_rowid='id', {_TOKENIZE}
);
CREATE TRIGGER IF NOT EXISTS segments_ai AFTER INSERT ON segments BEGIN
    INSERT INTO segments_fts(rowid, text, speaker)
    VALUES (new.id, new.text, new.speaker);
END;
CREATE TRIGGER IF NOT EXISTS segments_ad AFTER DELETE ON segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, text, speaker)
    VALUES ('delete', old.id, old.text, old.speaker);
END;
CREATE TRIGGER IF NOT EXISTS segments_au AFTER UPDATE ON segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, text, speaker)
    VALUES ('delete', old.id, old.text, old.speaker);
    INSERT INTO segments_fts(rowid, text, speaker)
    VALUES (new.id, new.text, new.speaker);
END;

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    meeting_id TEXT NOT NULL UNIQUE REFERENCES meetings(id) ON DELETE CASCADE,
    summary TEXT NOT NULL DEFAULT '',
    action_items_json TEXT NOT NULL DEFAULT '[]',
    action_items_text TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL CHECK (updated_by IN ('claude', 'user'))
);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    summary, action_items_text, content='notes', content_rowid='id',
    {_TOKENIZE}
);
CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, summary, action_items_text)
    VALUES (new.id, new.summary, new.action_items_text);
END;
CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, summary, action_items_text)
    VALUES ('delete', old.id, old.summary, old.action_items_text);
END;
CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, summary, action_items_text)
    VALUES ('delete', old.id, old.summary, old.action_items_text);
    INSERT INTO notes_fts(rowid, summary, action_items_text)
    VALUES (new.id, new.summary, new.action_items_text);
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

# v2 (phase 3): how many *other* people were invited (the user, rooms,
# resources and people who declined excluded). NULL means unknown, e.g. a
# distribution-list invite, and then diarization must not be capped by it.
_SCHEMA_V2 = """
BEGIN IMMEDIATE;
ALTER TABLE calendar_events ADD COLUMN other_attendees INTEGER;
PRAGMA user_version = 2;
COMMIT;
"""

# (target version, script). Append; never edit a shipped entry. migrate()
# reads the starting version before it takes the write lock, so two fresh
# connections can both start from 0 and both run v1 (idempotent: IF NOT
# EXISTS); the slower one's v1 then sets `user_version` back to 1. Its v2
# ALTER fails with "duplicate column name" and migrate() repairs the version
# itself (see below). A later non-idempotent migration needs the same
# treatment: detect "already applied" from the error and set the version.
_MIGRATIONS = [(1, _SCHEMA_V1), (2, _SCHEMA_V2)]

_WAL_RETRY_BUDGET_SECONDS = 5.0
_WAL_RETRY_INTERVAL_SECONDS = 0.05


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path) if path is not None else settings.library_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        # Create the file with owner-only permissions up front so it is
        # never briefly world-readable between creation and chmod.
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    _set_wal_mode(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def _set_wal_mode(conn: sqlite3.Connection) -> None:
    """Put the connection in WAL mode, retrying through the fresh-file race.

    Several processes/threads opening a brand-new database file at the same
    moment can make `PRAGMA journal_mode = WAL` raise "database is locked"
    immediately — SQLite raises that one synchronously rather than routing
    it through busy_timeout. If another connection already won the race,
    `PRAGMA journal_mode` (a query, not a mode change) already reports
    'wal' and there is nothing to do; otherwise retry the mode change
    within the same busy budget used elsewhere in this module.
    """
    if conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal":
        return
    deadline = time.monotonic() + _WAL_RETRY_BUDGET_SECONDS
    while True:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(_WAL_RETRY_INTERVAL_SECONDS)


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
            try:
                conn.executescript(script)
            except sqlite3.OperationalError as exc:
                # v2's ALTER is not idempotent: if a concurrent connection
                # migrated between our version read and our lock, the column
                # already exists. Roll back and accept that as done.
                if "duplicate column name" not in str(exc):
                    raise
                conn.rollback()
                # The other connection's slower v1 may have set the version
                # back to 1 after its v2 ran; the duplicate column proves this
                # script's only statement already applied, so record it, or
                # every later connection would retry the failing ALTER.
                conn.execute(f"PRAGMA user_version = {target}")
            version = target


def rebuild_derived(conn: sqlite3.Connection) -> None:
    """Rebuild everything that is not master data."""
    with conn:
        conn.execute("INSERT INTO segments_fts(segments_fts) VALUES ('rebuild')")
        conn.execute("INSERT INTO notes_fts(notes_fts) VALUES ('rebuild')")
        conn.execute("DELETE FROM calendar_events")
