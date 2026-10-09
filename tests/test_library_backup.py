import sqlite3
import stat
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from speakeasy import library_backup, meeting_store
from speakeasy.meeting_library import EventPerson, MeetingLibrary, NewMeeting
from speakeasy.meetings import MeetingSegment

MASTER = (
    "meetings", "segments", "notes", "user_notes", "note_draft", "tags",
    "meeting_tags", "tag_aliases", "tag_suppressions", "people",
    "meeting_people", "summary_requests",
)


def _meeting(lib, title):
    return lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 1, 2, title + " words")],
        duration_seconds=100, started_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
        title=title, calendar_event_id="linked-event",
        people=[EventPerson("Ada", "ada@example.com", "organizer")]))


def _rows(path):
    conn = sqlite3.connect(path)
    try:
        return {table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in MASTER}
    finally:
        conn.close()


def test_backup_restore_preserves_master_data_and_rebuilds_derived(tmp_path, library_path):
    source = MeetingLibrary(library_path)
    first = _meeting(source, "Keep")
    second = _meeting(source, "Pending")
    source.save_notes(first, summary="Important recap", action_items=["Send it"], tags=["Work"])
    source.set_user_notes(first, "My private note", [])
    source.request_summary(second)
    source.set_draft("Unfinished draft", [], "2026-09-30T12:00:00Z")
    conn = meeting_store.connect(library_path)
    try:
        tag_id = conn.execute("SELECT id FROM tags WHERE name = 'Work'").fetchone()[0]
        conn.execute("INSERT INTO tag_aliases (slug, name, tag_id) VALUES ('office', 'Office', ?)",
                     (tag_id,))
        conn.execute("INSERT INTO tag_suppressions (meeting_id, tag_id, created_at)"
                     " VALUES (?, ?, '2026-09-30')", (second, tag_id))
        conn.execute("INSERT INTO calendar_events (event_key, calendar_name, title, start_utc,"
                     " end_utc, all_day, synced_at) VALUES"
                     " ('linked-event', 'Calendar', 'Event', '2026-09-30T12:00:00Z',"
                     " '2026-09-30T13:00:00Z', 0, '2026-09-30')")
        conn.execute("INSERT INTO calendar_event_people (event_key, person_id, role)"
                     " SELECT 'linked-event', id, 'organizer' FROM people")
        conn.commit()
    finally:
        conn.close()
    expected = _rows(library_path)
    assert all(expected.values())
    backup = tmp_path / "saved.speakeasy-library"
    assert library_backup.backup_library(backup, library_path) == 2
    assert library_backup.inspect_backup(backup) == 2
    assert _rows(backup) == expected
    backed = sqlite3.connect(backup)
    try:
        assert backed.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0] == 0
        assert backed.execute("SELECT COUNT(*) FROM calendar_event_people").fetchone()[0] == 0
    finally:
        backed.close()
    source.save_notes(first, summary="later edit")
    _meeting(source, "Newer")
    count, recovery = library_backup.restore_library(backup, library_path)
    assert count == 2
    assert recovery.is_file()
    assert stat.S_IMODE(recovery.stat().st_mode) == 0o600
    assert _rows(library_path) == expected
    assert MeetingLibrary(library_path).search("Important recap")
    assert MeetingLibrary(library_path).search("My private note")
    assert MeetingLibrary(recovery).get_meeting(first).notes.summary == "later edit"


def test_rejects_unknown_or_corrupt_backup_without_changing_current(tmp_path, library_path):
    lib = MeetingLibrary(library_path)
    _meeting(lib, "Current")
    before = _rows(library_path)
    bad = tmp_path / "bad.speakeasy-library"
    bad.write_bytes(b"not sqlite")
    with pytest.raises(library_backup.InvalidLibrary):
        library_backup.restore_library(bad, library_path)
    good = tmp_path / "good.speakeasy-library"
    library_backup.backup_library(good, library_path)
    conn = sqlite3.connect(good)
    conn.execute("CREATE TABLE future_data (secret TEXT)")
    conn.commit()
    conn.close()
    with pytest.raises(library_backup.InvalidLibrary, match="Unknown"):
        library_backup.restore_library(good, library_path)
    assert _rows(library_path) == before


def test_restore_refuses_active_other_process(tmp_path, library_path):
    lib = MeetingLibrary(library_path)
    _meeting(lib, "Current")
    backup = tmp_path / "good.speakeasy-library"
    library_backup.backup_library(backup, library_path)
    before = _rows(library_path)
    process = subprocess.Popen(
        [sys.executable, "-c", "import fcntl,sys; f=open(sys.argv[1], 'a+');"
         " fcntl.flock(f,fcntl.LOCK_SH); print('ready',flush=True); input()",
         str(library_path) + ".lock"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True)
    try:
        assert process.stdout.readline().strip() == "ready"
        with pytest.raises(BlockingIOError):
            library_backup.restore_library(backup, library_path)
        assert _rows(library_path) == before
    finally:
        process.stdin.write("\n")
        process.stdin.flush()
        process.wait(timeout=5)


def test_backup_includes_committed_wal_pages(tmp_path, library_path):
    lib = MeetingLibrary(library_path)
    mid = _meeting(lib, "Before")
    writer = meeting_store.connect(library_path)
    try:
        writer.execute("UPDATE meetings SET title = 'Only in WAL' WHERE id = ?", (mid,))
        writer.commit()
        assert (tmp_path / "library.sqlite-wal").stat().st_size > 0
        backup = tmp_path / "wal.speakeasy-library"
        library_backup.backup_library(backup, library_path)
        assert MeetingLibrary(backup).get_meeting(mid).title == "Only in WAL"
    finally:
        writer.close()


def test_unknown_trigger_and_live_wal_backup_are_rejected(tmp_path, library_path):
    _meeting(MeetingLibrary(library_path), "Current")
    backup = tmp_path / "source.speakeasy-library"
    library_backup.backup_library(backup, library_path)
    conn = sqlite3.connect(backup)
    conn.executescript("DROP TRIGGER segments_ai;"
                       "CREATE TRIGGER segments_ai AFTER INSERT ON segments BEGIN"
                       " DELETE FROM notes; END;")
    conn.close()
    with pytest.raises(library_backup.InvalidLibrary, match="Unknown"):
        library_backup.inspect_backup(backup)
    library_backup.backup_library(backup, library_path)
    conn = sqlite3.connect(backup)
    conn.execute("CREATE TRIGGER sqliteEvil AFTER INSERT ON segments BEGIN"
                 " DELETE FROM notes; END")
    conn.close()
    with pytest.raises(library_backup.InvalidLibrary, match="Unknown"):
        library_backup.inspect_backup(backup)
    library_backup.backup_library(backup, library_path)
    conn = sqlite3.connect(backup)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA user_version = 999")
    with pytest.raises(library_backup.InvalidLibrary, match="standalone"):
        library_backup.inspect_backup(backup)
    conn.close()


def test_crash_during_copy_keeps_original_library(tmp_path, library_path):
    from speakeasy import library_lease

    target = MeetingLibrary(library_path)
    old_id = _meeting(target, "Original")
    source_path = tmp_path / "other.sqlite"
    source = MeetingLibrary(source_path)
    new_id = _meeting(source, "Replacement")
    conn = meeting_store.connect(source_path)
    conn.execute("UPDATE segments SET text = ? WHERE meeting_id = ?",
                 ("large transcript " * 50000, new_id))
    conn.commit()
    conn.close()
    backup = tmp_path / "other.speakeasy-library"
    library_backup.backup_library(backup, source_path)
    library_lease.release_private(library_path)
    process = subprocess.run(
        [sys.executable, "-c",
         "from pathlib import Path; import os,sys;"
         " from speakeasy import library_backup as b;"
         " b._restore_progress=lambda *args: os._exit(37);"
         " b.restore_library(Path(sys.argv[1]),Path(sys.argv[2]))",
         str(backup), str(library_path)], capture_output=True, text=True, timeout=20)
    assert process.returncode == 37, process.stderr
    assert MeetingLibrary(library_path).get_meeting(old_id).title == "Original"


@pytest.mark.parametrize("marker_fails", [False, True])
def test_uncertain_post_copy_failure_blocks_reopen(tmp_path, library_path, monkeypatch,
                                                    marker_fails):
    from speakeasy import library_lease

    _meeting(MeetingLibrary(library_path), "Original")
    backup = tmp_path / "saved.speakeasy-library"
    library_backup.backup_library(backup, library_path)
    real_fsync = library_backup._fsync_file

    def fail_target(path):
        if path == library_path:
            raise OSError("disk error")
        return real_fsync(path)

    monkeypatch.setattr(library_backup, "_fsync_file", fail_target)
    if marker_fails:
        real_dir = library_backup._fsync_dir

        def fail_marker_dir(path):
            if library_lease.recovery_marker(library_path).exists():
                raise OSError("marker sync failed")
            return real_dir(path)

        monkeypatch.setattr(library_backup, "_fsync_dir", fail_marker_dir)
    try:
        with pytest.raises(library_backup.RestoreIndeterminate) as err:
            library_backup.restore_library(backup, library_path)
        assert err.value.recovery.is_file()
        assert err.value.marker_persisted is not marker_fails
        assert library_lease.recovery_marker(library_path).is_file()
        with pytest.raises(RuntimeError, match="requires recovery"):
            meeting_store.connect(library_path)
    finally:
        library_lease.recovery_marker(library_path).unlink(missing_ok=True)
        library_lease.end_maintenance(library_path)
