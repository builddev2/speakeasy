"""Identity (revision + file ids) of the speech model in use. Import-light:
no MLX, no transcriber. Used to compare with Hugging Face for update checks.
"""

import json
import os
import sys
from pathlib import Path

from . import config, model_store

_FILES = ("config.json", "model.safetensors")


def _snapshots_dir(model_id: str) -> Path:
    # Same path logic as transcriber._is_cached (which can't be imported: MLX).
    cache_home = os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface"))
    hub_dir = Path(os.environ.get("HF_HUB_CACHE", os.path.join(cache_home, "hub")))
    return hub_dir / f"models--{model_id.replace('/', '--')}" / "snapshots"


def _read_json_dict(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _dev_identity() -> dict | None:
    snapshots = _snapshots_dir(config.MODEL_ID)
    try:
        revs = sorted(p for p in snapshots.iterdir() if p.is_dir())
    except OSError:
        return None
    if not revs:
        return None
    snap = revs[0]
    try:
        main = (snapshots.parent / "refs" / "main").read_text().strip()
    except OSError:
        main = ""
    if main and main == Path(main).name and (snapshots / main).is_dir():
        snap = snapshots / main
    files = {}
    for name in _FILES:
        path = snap / name
        if not path.is_symlink():
            return None
        files[name] = os.path.basename(os.readlink(path))
    return {"model_id": config.MODEL_ID, "revision": snap.name, "files": files}


def bundled_identity() -> dict | None:
    if getattr(sys, "frozen", False):
        model = Path(sys.executable).resolve().parent.parent / "Resources" / "model"
        return _read_json_dict(model / ".source")
    return _dev_identity()


def active_identity() -> dict | None:
    update = model_store.update_dir_if_valid()
    if update is not None:
        data = _read_json_dict(update / "source.json")
        if data is not None:
            return {**data, "origin": "update"}
    bundled = bundled_identity()
    if bundled is None:
        return None
    return {**bundled, "origin": "bundled"}
