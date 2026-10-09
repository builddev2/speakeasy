"""Restorable, text-only SQLite copies of the meeting library."""

import os
import sqlite3
import tempfile
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from . import library_lease, meeting_store, settings

EXTENSION = ".speakeasy-library"


class InvalidLibrary(ValueError):
    pass


class RestoreIndeterminate(RuntimeError):
    def __init__(self, recovery: Path):
        super().__init__("Restore outcome requires manual review.")
        self.recovery = recovery
        self.marker_persisted = False


def _open_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise InvalidLibrary("The selected library file does not exist.")
    with path.open("rb") as fh:
        header = fh.read(100)
    if len(header) < 100 or header[:16] != b"SQLite format 3\x00":
        raise InvalidLibrary("The selected file is not a SQLite library.")
    if header[18:20] != b"\x01\x01" or (path.parent / (path.name + "-wal")).exists():
        raise InvalidLibrary("The selected library is not a standalone backup.")
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)


@lru_cache(maxsize=1)
def _schema() -> dict[tuple[str, str], tuple]:
    conn = sqlite3.connect(":memory:")
    try:
        meeting_store.migrate(conn)
        return _schema_of(conn)
    finally:
        conn.close()


def _schema_of(conn: sqlite3.Connection) -> dict[tuple[str, str], tuple]:
    objects = conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT GLOB 'sqlite_*'").fetchall()
    return {(kind, name): (table, sql,
            tuple(tuple(row) for row in conn.execute(f'PRAGMA table_info("{name}")'))
            if kind == "table" else ()) for kind, name, table, sql in objects}


def _validate(conn: sqlite3.Connection, *, require_current: bool = False) -> int:
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if not 1 <= version <= meeting_store.SCHEMA_VERSION:
            raise InvalidLibrary("Unsupported meeting library version.")
        if require_current and version != meeting_store.SCHEMA_VERSION:
            raise InvalidLibrary("The meeting library is not fully upgraded.")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise InvalidLibrary("Meeting library integrity check failed.")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise InvalidLibrary("Meeting library relationships are invalid.")
        if require_current and _schema_of(conn) != _schema():
            raise InvalidLibrary("Unknown meeting library schema.")
        return conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        raise InvalidLibrary("The selected file is not a valid meeting library.") from exc


def _fsync_file(path: Path) -> None:
    with path.open("rb") as fh:
        os.fsync(fh.fileno())


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _checkpoint(conn: sqlite3.Connection) -> None:
    row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if row is not None and row[0] != 0:
        raise sqlite3.OperationalError("Meeting library checkpoint is busy.")


def _restore_progress(status: int, remaining: int, total: int) -> None:
    """SQLite backup progress boundary (also used by the crash test)."""


def _copy_and_prepare(source: Path, destination: Path) -> int:
    reader = _open_readonly(source)
    try:
        _validate(reader)
        writer = sqlite3.connect(destination)
        try:
            reader.backup(writer)
        finally:
            writer.close()
    finally:
        reader.close()
    prepared = meeting_store.connect(destination)
    try:
        count = _validate(prepared, require_current=True)
        meeting_store.rebuild_derived(prepared)
        _validate(prepared, require_current=True)
        prepared.execute("PRAGMA journal_mode = DELETE")
    finally:
        prepared.close()
        library_lease.release_private(destination)
    _fsync_file(destination)
    return count


def backup_library(destination: Path, source: Path | None = None) -> int:
    """Write one independently restorable SQLite file, then publish it."""
    source = Path(source) if source is not None else settings.library_path()
    destination = Path(destination)
    if destination.resolve() == source.resolve():
        raise ValueError("Backup destination must differ from the library.")
    if destination.suffix != EXTENSION:
        raise ValueError("Backup file must use the Speakeasy library extension.")
    if not destination.parent.is_dir():
        raise NotADirectoryError(destination.parent)
    fd, name = tempfile.mkstemp(prefix=".Speakeasy-backup-", dir=destination.parent)
    os.close(fd)
    staged = Path(name)
    try:
        # Open through meeting_store so an older file is upgraded before the
        # SQLite backup captures its consistent WAL snapshot.
        current = meeting_store.connect(source)
        try:
            target = sqlite3.connect(staged)
            try:
                current.backup(target)
            finally:
                target.close()
        finally:
            current.close()
        prepared = meeting_store.connect(staged)
        try:
            count = _validate(prepared, require_current=True)
            meeting_store.rebuild_derived(prepared)
            _validate(prepared, require_current=True)
            prepared.execute("PRAGMA journal_mode = DELETE")
        finally:
            prepared.close()
            library_lease.release_private(staged)
        _fsync_file(staged)
        os.replace(staged, destination)
        _fsync_dir(destination.parent)
        return count
    finally:
        staged.unlink(missing_ok=True)


def inspect_backup(source: Path) -> int:
    """Validate without migrating or modifying the user's selected file."""
    with tempfile.TemporaryDirectory(prefix="speakeasy-restore-check-") as temp:
        return _copy_and_prepare(Path(source), Path(temp) / "candidate.sqlite")


def prepare_backup(source: Path, destination: Path) -> int:
    """Validate one selected backup into a private candidate retained by the UI."""
    return _copy_and_prepare(Path(source), Path(destination))


def restore_library(source: Path, target_path: Path | None = None) -> tuple[int, Path]:
    """Replace a healthy idle library while retaining a verified recovery copy.

    The caller must stop in-process activity and close its watcher first. A
    lifetime process lease excludes current Speakeasy/MCP/CLI peers; SQLite's
    retained exclusive lock keeps every other connection out until the copy
    and post-copy checks finish. A corrupt current database is left untouched.
    """
    target_path = Path(target_path) if target_path is not None else settings.library_path()
    source = Path(source)
    if source.resolve() == target_path.resolve():
        raise ValueError("Cannot restore a library from itself.")
    with tempfile.TemporaryDirectory(prefix="speakeasy-restore-") as temp:
        candidate = Path(temp) / "candidate.sqlite"
        count = _copy_and_prepare(source, candidate)
        return restore_prepared(candidate, target_path, count)


def restore_prepared(candidate: Path, target_path: Path | None = None,
                     expected_count: int | None = None) -> tuple[int, Path]:
    """Restore the exact validated private candidate shown in confirmation."""
    target_path = Path(target_path) if target_path is not None else settings.library_path()
    candidate = Path(candidate)
    check = _open_readonly(candidate)
    try:
        count = _validate(check, require_current=True)
    finally:
        check.close()
    if expected_count is not None and count != expected_count:
        raise InvalidLibrary("Selected backup changed after validation.")
    library_lease.begin_maintenance(target_path)
    recovery = None
    fatal = False
    try:
        if not target_path.is_file():
            raise InvalidLibrary("The current library is missing; automatic restore is unavailable.")
        current = sqlite3.connect(target_path, timeout=1.0)
        try:
            current.execute("PRAGMA foreign_keys = ON")
            current.execute("PRAGMA synchronous = FULL")
            current.execute("PRAGMA locking_mode = EXCLUSIVE")
            current.execute("BEGIN EXCLUSIVE")
            current.execute("COMMIT")
            _validate(current, require_current=True)
            recovery = target_path.with_name(
                f"{target_path.stem}-before-restore-"
                f"{datetime.now().astimezone():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
                f"{EXTENSION}")
            os.close(os.open(recovery, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            old_copy = sqlite3.connect(recovery)
            try:
                current.backup(old_copy)
                old_copy.execute("PRAGMA journal_mode = DELETE")
            finally:
                old_copy.close()
            check = _open_readonly(recovery)
            try:
                _validate(check, require_current=True)
            finally:
                check.close()
            _fsync_file(recovery)
            _fsync_dir(recovery.parent)
            try:
                replacement = sqlite3.connect(candidate)
                try:
                    replacement.backup(current, pages=64, progress=_restore_progress)
                finally:
                    replacement.close()
                if _validate(current, require_current=True) != count:
                    raise InvalidLibrary("Restored meeting count did not match.")
                _checkpoint(current)
            except BaseException:
                recovery_reader = sqlite3.connect(recovery)
                try:
                    recovery_reader.backup(current)
                    if _validate(current, require_current=True) != _validate(
                            recovery_reader, require_current=True):
                        raise InvalidLibrary("Recovery verification failed.")
                    _checkpoint(current)
                    _fsync_file(target_path)
                    _fsync_dir(target_path.parent)
                except BaseException as exc:
                    raise RestoreIndeterminate(recovery) from exc
                finally:
                    recovery_reader.close()
                raise
        finally:
            current.close()
        try:
            _fsync_file(target_path)
            _fsync_dir(target_path.parent)
        except BaseException as exc:
            raise RestoreIndeterminate(recovery) from exc
        return count, recovery
    except RestoreIndeterminate as outcome:
        fatal = True
        marker = library_lease.recovery_marker(target_path)
        try:
            os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            _fsync_file(marker)
            _fsync_dir(marker.parent)
            outcome.marker_persisted = True
        except OSError as exc:
            print(f"Recovery marker could not be persisted: {type(exc).__name__}")
        raise
    finally:
        if not fatal:
            library_lease.end_maintenance(target_path)
