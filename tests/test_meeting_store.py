import os
import stat
import threading

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


def test_concurrent_first_connect_all_succeed_and_report_wal(library_path):
    """Reproduces the fresh-file WAL race: several connections opening a
    brand-new file at once can make `PRAGMA journal_mode = WAL` fail with
    'database is locked' even though busy_timeout is set (SQLite raises this
    one synchronously, ignoring the busy handler). connect() must retry
    instead of propagating that error, and every thread must still end up
    on WAL."""
    n = 4
    barrier = threading.Barrier(n)
    results = [None] * n
    errors = [None] * n

    def worker(i):
        try:
            barrier.wait(timeout=10)
            conn = meeting_store.connect()
            results[i] = conn.execute("PRAGMA journal_mode").fetchone()[0]
            conn.close()
        except BaseException as exc:  # noqa: BLE001 - surfaced via errors[i]
            errors[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert errors == [None] * n, errors
    assert results == ["wal"] * n


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
