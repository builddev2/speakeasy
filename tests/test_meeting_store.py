import os
import sqlite3
import stat
import threading
import time

import pytest

from speakeasy import meeting_store


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}


def test_connect_creates_schema_wal_and_private_file(library_path):
    conn = meeting_store.connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == meeting_store.SCHEMA_VERSION
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert {
        "meetings", "segments", "segments_fts", "notes", "notes_fts", "tags",
        "meeting_tags", "people", "meeting_people", "calendar_events",
        "calendar_event_people",
    } <= _tables(conn)
    assert stat.S_IMODE(os.stat(library_path).st_mode) == 0o600
    conn.close()


def test_connect_uses_row_factory(library_path):
    conn = meeting_store.connect()
    assert conn.execute("SELECT 1 AS x").fetchone()["x"] == 1
    conn.close()


def test_connect_twice_is_idempotent(library_path):
    meeting_store.connect().close()
    conn = meeting_store.connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == meeting_store.SCHEMA_VERSION
    conn.close()


def test_newer_schema_is_refused(library_path):
    conn = meeting_store.connect()
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(RuntimeError, match="newer"):
        meeting_store.connect()


def test_concurrent_first_connect_all_succeed_and_report_wal(tmp_path):
    """Reproduces the fresh-file WAL race: several connections opening a
    brand-new file at once can make `PRAGMA journal_mode = WAL` fail with
    'database is locked' even though busy_timeout is set (SQLite raises this
    one synchronously, ignoring the busy handler). connect() must retry
    instead of propagating that error, and every thread must still end up
    on WAL.

    Whether the race actually triggers depends on thread scheduling, so a
    single attempt only catches a missing retry occasionally (about 1 in 7
    runs when this was tried against the retry removed). Looping over many
    fresh files makes a regression caught reliably instead of by chance,
    while each individual connect() stays fast."""
    n = 4
    iterations = 20
    for k in range(iterations):
        path = tmp_path / f"lib{k}.sqlite"
        barrier = threading.Barrier(n)
        results = [None] * n
        errors = [None] * n

        def worker(i, path=path, results=results, errors=errors):
            try:
                barrier.wait(timeout=10)
                conn = meeting_store.connect(path)
                results[i] = conn.execute("PRAGMA journal_mode").fetchone()[0]
                conn.close()
            except BaseException as exc:  # noqa: BLE001 - surfaced via errors[i]
                errors[i] = exc

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert errors == [None] * n, (k, errors)
        assert results == ["wal"] * n, (k, results)
        check = sqlite3.connect(path)
        try:
            assert (check.execute("PRAGMA user_version").fetchone()[0]
                    == meeting_store.SCHEMA_VERSION), k
        finally:
            check.close()


def test_migrate_converges_when_a_slower_v1_resets_the_version(library_path):
    """The fresh-library interleave: both connections read version 0, A runs
    v1 and v2, then B's v1 sets user_version back to 1 and B's v2 hits
    'duplicate column'. migrate() must land on SCHEMA_VERSION anyway."""
    a = meeting_store.connect()
    a.close()
    b = sqlite3.connect(library_path)
    try:
        b.executescript(meeting_store._SCHEMA_V1)
        assert b.execute("PRAGMA user_version").fetchone()[0] == 1
        meeting_store.migrate(b)
        assert b.execute("PRAGMA user_version").fetchone()[0] == meeting_store.SCHEMA_VERSION
    finally:
        b.close()


def test_wal_pragma_non_locked_error_is_not_retried(library_path, monkeypatch):
    """A non-'locked' OperationalError from the WAL pragma (e.g. a real I/O
    failure) must propagate immediately rather than being treated as the
    fresh-file race and retried for up to 5 seconds."""
    attempts = []

    class _FailingConnection(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql == "PRAGMA journal_mode = WAL":
                attempts.append(1)
                raise sqlite3.OperationalError("disk I/O error")
            return super().execute(sql, *args)

    real_connect = sqlite3.connect

    def fake_connect(path, timeout=5.0):
        return real_connect(path, timeout=timeout, factory=_FailingConnection)

    monkeypatch.setattr(meeting_store.sqlite3, "connect", fake_connect)

    start = time.monotonic()
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        meeting_store.connect()
    elapsed = time.monotonic() - start

    assert attempts == [1]
    assert elapsed < 1.0


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


def _insert_note(conn, meeting_id="20260924-131723-abcd", summary="roadmap for kestrel"):
    conn.execute(
        "INSERT INTO notes (meeting_id, summary, action_items_json,"
        " action_items_text, updated_at, updated_by) VALUES (?, ?, '[]', '', 'x',"
        " 'claude')",
        (meeting_id, summary),
    )


def test_note_delete_cascades_and_drops_from_fts(library_path):
    """Exercises ruling 3's notes.meeting_id FK (ON DELETE CASCADE) together
    with the notes_ad trigger that keys off new.id/old.id."""
    conn = meeting_store.connect()
    with conn:
        _insert_meeting(conn)
        _insert_note(conn)
    assert _fts_hits(conn, "notes_fts", "kestrel") == 1
    with conn:
        conn.execute("DELETE FROM meetings")
    assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0
    assert _fts_hits(conn, "notes_fts", "kestrel") == 0
    conn.close()


def test_rebuild_derived_restores_notes_search(library_path):
    conn = meeting_store.connect()
    with conn:
        _insert_meeting(conn)
        _insert_note(conn, summary="orbital mechanics primer")
        conn.execute("INSERT INTO notes_fts(notes_fts) VALUES ('delete-all')")
    assert _fts_hits(conn, "notes_fts", "orbital") == 0
    meeting_store.rebuild_derived(conn)
    assert _fts_hits(conn, "notes_fts", "orbital") == 1
    conn.close()


def test_rebuild_derived_clears_calendar_cache(library_path):
    conn = meeting_store.connect()
    with conn:
        conn.execute(
            "INSERT INTO calendar_events (event_key, calendar_name, title,"
            " start_utc, end_utc, all_day, synced_at) VALUES ('k', 'Work',"
            " 'Standup', '2026-09-24T17:00:00Z', '2026-09-24T17:30:00Z', 0, 'x')"
        )
    assert conn.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0] == 1
    meeting_store.rebuild_derived(conn)
    assert conn.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0] == 0
    conn.close()


def test_segments_and_notes_have_explicit_integer_primary_key(library_path):
    """Ruling 3: segments and notes must have an explicit `id` column (not
    the implicit sqlite rowid) because FTS content_rowid is pinned to it."""
    conn = meeting_store.connect()
    for table in ("segments", "notes"):
        cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
        pk_cols = [c["name"] for c in cols if c["pk"] == 1]
        assert pk_cols == ["id"], table
    conn.close()


def test_fts_tables_use_explicit_id_as_content_rowid(library_path):
    """Ruling 3, stated directly: segments_fts/notes_fts must be declared
    with content_rowid='id' (not the implicit rowid), independent of
    whether a given VACUUM happens to renumber rowids on tiny test tables."""
    conn = meeting_store.connect()
    for table in ("segments_fts", "notes_fts"):
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = ?", (table,)
        ).fetchone()["sql"]
        assert "content_rowid='id'" in sql, (table, sql)
    conn.close()


def test_segments_fts_survives_vacuum_via_explicit_id(library_path):
    """content_rowid='id' (an explicit column) rather than the implicit
    sqlite rowid must keep segments_fts in sync across a VACUUM, which is
    free to renumber unpinned implicit rowids."""
    conn = meeting_store.connect()
    with conn:
        _insert_meeting(conn)
        for i, text in enumerate(["first alpha", "middle beta", "third gamma"]):
            conn.execute(
                "INSERT INTO segments (meeting_id, idx, speaker,"
                " start_seconds, end_seconds, text) VALUES"
                " ('20260924-131723-abcd', ?, 'You', ?, ?, ?)",
                (i, i, i + 1, text),
            )
    with conn:
        conn.execute("DELETE FROM segments WHERE text = 'middle beta'")
    conn.execute("VACUUM")
    with conn:
        conn.execute(
            "UPDATE segments SET text = 'fourth delta' WHERE text = 'third gamma'"
        )
    assert _fts_hits(conn, "segments_fts", "alpha") == 1
    assert _fts_hits(conn, "segments_fts", "gamma") == 0
    assert _fts_hits(conn, "segments_fts", "delta") == 1
    conn.close()


def _v2_library(path, meetings):
    """A v2 library file: one meeting per entry, each a list of tag names."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(meeting_store._SCHEMA_V1)
        conn.executescript(meeting_store._SCHEMA_V2)
        for i, names in enumerate(meetings):
            mid = f"20260901-0000{i:02d}-abcd"
            conn.execute(
                "INSERT INTO meetings (id, title, started_at, tz_offset_minutes,"
                " duration_seconds, capture_mode, system_audio_status, capture_scope,"
                " track_offsets_json, capture_health_json, source, created_at, updated_at)"
                " VALUES (?, 't', '2026-09-01T00:00:00Z', 0, 60, 'mic', 'off', 'mic',"
                " '{}', '{}', 'recorded', 'x', 'x')", (mid,))
            for name in names:
                conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
                conn.execute("INSERT INTO meeting_tags (meeting_id, tag_id)"
                             " SELECT ?, id FROM tags WHERE name = ?", (mid, name))
        conn.commit()
    finally:
        conn.close()


def test_v3_merges_tags_that_collide_on_slug(library_path):
    # Insertion order gives ids: Project X=1, project-x=2, Ops=3, !!!=4.
    _v2_library(library_path, [["Project X"], ["project-x", "Ops"],
                               ["project-x"], ["!!!"]])
    conn = meeting_store.connect(library_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == meeting_store.SCHEMA_VERSION
        tags = {r["slug"]: r["name"] for r in conn.execute("SELECT name, slug FROM tags")}
        # project-x has the most links, so it survives; an empty slug keeps
        # its tag under tag<id> rather than losing it.
        assert tags == {"projectx": "project-x", "ops": "Ops", "tag4": "!!!"}
        links = conn.execute(
            "SELECT mt.meeting_id, mt.source, mt.added_at FROM meeting_tags mt"
            " JOIN tags t ON t.id = mt.tag_id WHERE t.slug = 'projectx'"
            " ORDER BY mt.meeting_id").fetchall()
        assert [tuple(r) for r in links] == [
            ("20260901-000000-abcd", "claude", None),
            ("20260901-000001-abcd", "claude", None),
            ("20260901-000002-abcd", "claude", None)]
        assert _tables(conn) >= {"tag_aliases", "tag_suppressions"}
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO tags (name, slug) VALUES ('PROJECT x!', 'projectx')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE meeting_tags SET source = 'robot'")
    finally:
        conn.close()


def test_v3_empty_slug_fallback_never_collides_with_a_real_slug(library_path):
    # "tag2" is id 1, "!!!" is id 2: the fallback tag2 must not eat the real one.
    _v2_library(library_path, [["tag2"], ["!!!"], ["!!!"]])
    conn = meeting_store.connect(library_path)
    try:
        assert {r["slug"]: r["name"] for r in conn.execute(
            "SELECT name, slug FROM tags")} == {"tag2": "tag2", "tag2v2": "!!!"}
        counts = {r["name"]: r["n"] for r in conn.execute(
            "SELECT t.name, COUNT(mt.meeting_id) AS n FROM tags t"
            " LEFT JOIN meeting_tags mt ON mt.tag_id = t.id GROUP BY t.id")}
        assert counts == {"tag2": 1, "!!!": 2}
    finally:
        conn.close()


def test_failed_v3_migration_leaves_a_v2_file_intact(library_path, monkeypatch):
    _v2_library(library_path, [["Alpha"], ["Beta", "Alpha"]])
    real = meeting_store.tag_slug
    calls = []

    def flaky(name):
        calls.append(name)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return real(name)

    monkeypatch.setattr(meeting_store, "tag_slug", flaky)
    with pytest.raises(RuntimeError, match="boom"):
        meeting_store.connect(library_path)
    raw = sqlite3.connect(library_path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 2
        assert "slug" not in [r[1] for r in raw.execute("PRAGMA table_info(tags)")]
        assert raw.execute("SELECT name FROM sqlite_master"
                           " WHERE name = 'tag_aliases'").fetchall() == []
        assert raw.execute("SELECT id, name FROM tags ORDER BY id").fetchall() == [
            (1, "Alpha"), (2, "Beta")]
        assert raw.execute("SELECT meeting_id, tag_id FROM meeting_tags"
                           " ORDER BY meeting_id, tag_id").fetchall() == [
            ("20260901-000000-abcd", 1), ("20260901-000001-abcd", 1),
            ("20260901-000001-abcd", 2)]
    finally:
        raw.close()
    monkeypatch.setattr(meeting_store, "tag_slug", real)
    conn = meeting_store.connect(library_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == meeting_store.SCHEMA_VERSION
    finally:
        conn.close()


def test_v3_collision_tie_keeps_the_lowest_id(library_path):
    _v2_library(library_path, [["B-1"], ["b1"]])
    conn = meeting_store.connect(library_path)
    try:
        assert [tuple(r) for r in conn.execute("SELECT name, slug FROM tags")] == [("B-1", "b1")]
        assert conn.execute("SELECT COUNT(*) FROM meeting_tags").fetchone()[0] == 2
    finally:
        conn.close()


def test_concurrent_v3_migration_converges(tmp_path):
    """The app and the MCP server open a v2 library at the same moment
    (first launch after install): both succeed and the backfill runs once."""
    for k in range(10):
        path = tmp_path / f"lib{k}.sqlite"
        _v2_library(path, [["Project X"], ["project-x"]])
        barrier = threading.Barrier(2)
        errors = [None, None]

        def worker(i, path=path, errors=errors):
            try:
                barrier.wait(timeout=10)
                meeting_store.connect(path).close()
            except BaseException as exc:  # noqa: BLE001 - surfaced via errors[i]
                errors[i] = exc

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        assert errors == [None, None], (k, errors)
        check = sqlite3.connect(path)
        try:
            assert check.execute("PRAGMA user_version").fetchone()[0] == meeting_store.SCHEMA_VERSION
            assert check.execute("SELECT slug FROM tags").fetchall() == [("projectx",)]
        finally:
            check.close()


def test_v4_adds_summary_requests(library_path):
    conn = meeting_store.connect(library_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(summary_requests)")]
    assert cols == ["meeting_id", "requested_at"]
    assert meeting_store.SCHEMA_VERSION == 4
    conn.close()


def test_migrates_populated_v3_library_to_v4(library_path):
    from datetime import datetime, timezone
    from speakeasy.meeting_library import MeetingLibrary, NewMeeting
    meeting_store.connect(library_path).close()
    mid = MeetingLibrary().save_meeting(NewMeeting(
        started_at=datetime(2026, 9, 24, 13, 0, tzinfo=timezone.utc),
        segments=[], duration_seconds=600.0))
    conn = meeting_store.connect(library_path)
    conn.executescript("DROP TABLE summary_requests; PRAGMA user_version = 3;")
    conn.close()
    conn = meeting_store.connect(library_path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM summary_requests").fetchone()[0] == 0
    assert conn.execute("SELECT id FROM meetings").fetchone()["id"] == mid
    conn.close()
