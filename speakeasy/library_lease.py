"""Process-lifetime cooperative lock for meeting-library replacement."""

import fcntl
import os
import threading
from pathlib import Path

from . import settings

_guard = threading.RLock()
_fds: dict[Path, int] = {}
_maintenance: set[Path] = set()


def _lock_path(path: Path | None) -> Path:
    db = (Path(path) if path is not None else settings.library_path()).resolve()
    return db.with_name(db.name + ".lock")


def recovery_marker(path: Path | None = None) -> Path:
    lock_path = _lock_path(path)
    return lock_path.with_name(lock_path.name + ".recovery-required")


def shared(path: Path | None = None) -> None:
    """Keep one shared lease until process exit, including MCP idle time."""
    lock_path = _lock_path(path)
    with _guard:
        if recovery_marker(path).exists():
            raise RuntimeError("Meeting library requires recovery before it can be opened.")
        if lock_path in _maintenance:
            raise RuntimeError("Meeting library maintenance is in progress.")
        if lock_path in _fds:
            return
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise
        _fds[lock_path] = fd
        if recovery_marker(path).exists():
            os.close(_fds.pop(lock_path))
            raise RuntimeError("Meeting library requires recovery before it can be opened.")


def begin_maintenance(path: Path | None = None) -> None:
    """Fail quickly if another Speakeasy process still has the library open."""
    shared(path)
    lock_path = _lock_path(path)
    with _guard:
        _maintenance.add(lock_path)
        try:
            fcntl.flock(_fds[lock_path], fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            _maintenance.discard(lock_path)
            fcntl.flock(_fds[lock_path], fcntl.LOCK_SH)
            raise


def end_maintenance(path: Path | None = None) -> None:
    lock_path = _lock_path(path)
    with _guard:
        if lock_path in _maintenance:
            fcntl.flock(_fds[lock_path], fcntl.LOCK_SH)
            _maintenance.discard(lock_path)


def release_private(path: Path) -> None:
    """Drop a temporary snapshot's lease after its last connection closes."""
    lock_path = _lock_path(path)
    with _guard:
        if lock_path in _maintenance:
            raise RuntimeError("Cannot release a library under maintenance.")
        fd = _fds.pop(lock_path, None)
        if fd is not None:
            os.close(fd)
