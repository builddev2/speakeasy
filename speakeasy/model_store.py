"""On-disk store for downloaded speech-model updates (pure filesystem).

Layout under ``<app support>/models/speech/``: ``active.json`` (pointer,
replaced atomically), ``rev-<sha40>/`` (a verified update), ``.staging-*``
(in-progress download) and ``failed-*`` (a revision that failed to load).
Nothing here touches the network.
"""

import json
import os
import shutil
import threading
from pathlib import Path

from . import settings

_VALID_STATUSES = ("pending", "confirmed")
_in_use: set[Path] = set()
_in_use_lock = threading.Lock()


def mark_staging_in_use(path) -> None:
    """Register a staging dir so cleanup_leftovers() leaves it alone."""
    with _in_use_lock:
        _in_use.add(Path(path))


def release_staging(path) -> None:
    with _in_use_lock:
        _in_use.discard(Path(path))


def speech_models_dir() -> Path:
    return settings.app_support_dir() / "models" / "speech"


def _active_path() -> Path:
    return speech_models_dir() / "active.json"


def read_active() -> dict | None:
    try:
        data = json.loads(_active_path().read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_active(data: dict) -> None:
    """Atomically replace active.json (temp file, fsync, os.replace)."""
    directory = speech_models_dir()
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / "active.json.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, _active_path())


def _safe_name(name) -> str | None:
    """A single path component, or None (never trust the pointer file)."""
    if isinstance(name, str) and name and name == Path(name).name and name not in (".", ".."):
        return name
    return None


def update_dir_if_valid() -> Path | None:
    active = read_active()
    if not active or active.get("status") not in _VALID_STATUSES:
        return None
    name = _safe_name(active.get("dir"))
    if name is None:
        return None
    directory = speech_models_dir() / name
    try:
        json.loads((directory / "config.json").read_text())
        source = json.loads((directory / "source.json").read_text())
        expected = source["sizes"]["model.safetensors"]
        actual = (directory / "model.safetensors").stat().st_size
        if isinstance(expected, bool) or float(expected) != actual:
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return directory


def confirm_loaded() -> None:
    active = read_active()
    if not active:
        return
    previous = _safe_name(active.get("previous"))
    active["status"] = "confirmed"
    active["previous"] = None
    write_active(active)
    if previous:
        shutil.rmtree(speech_models_dir() / previous, ignore_errors=True)


def mark_failed() -> None:
    active = read_active()
    if not active:
        return
    root = speech_models_dir()
    name = _safe_name(active.get("dir"))
    try:
        _active_path().unlink()
    except OSError:
        pass
    if name and (root / name).is_dir():
        target = root / (
            _safe_name(f"failed-{active.get('revision') or name}") or f"failed-{name}")
        shutil.rmtree(target, ignore_errors=True)
        try:
            os.rename(root / name, target)
        except OSError:
            shutil.rmtree(root / name, ignore_errors=True)


def cleanup_leftovers() -> None:
    root = speech_models_dir()
    if not root.is_dir():
        return
    active = read_active() or {}
    keep = {_safe_name(active.get("dir")), _safe_name(active.get("previous"))}
    with _in_use_lock:
        busy = set(_in_use)
    for entry in root.iterdir():
        n = entry.name
        if entry in busy:
            continue
        if n.startswith((".staging-", "failed-")) or (n.startswith("rev-") and n not in keep):
            shutil.rmtree(entry, ignore_errors=True)


def use_bundled() -> None:
    try:
        _active_path().unlink()
    except OSError:
        pass
    root = speech_models_dir()
    if root.is_dir():
        for entry in root.glob("rev-*"):
            shutil.rmtree(entry, ignore_errors=True)
