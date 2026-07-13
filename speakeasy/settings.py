"""Per-user data locations and persisted app settings.

Speakeasy's user data (profiles, settings.json) lives in the standard macOS
location ~/Library/Application Support/Speakeasy/ so the standalone .app and
the dev checkout share one set of profiles. The first call that creates the
profiles dir migrates any profiles from the legacy in-repo profiles/ folder
(a non-destructive copy; the originals are left in place).

settings.json is tiny ({"schema": 1, "last_profile": ...}) and read on
demand — no caching layer to invalidate.
"""

import json
import shutil
import sys
from pathlib import Path

from . import config

_SCHEMA = 1


def app_support_dir() -> Path:
    d = Path.home() / "Library" / "Application Support" / "Speakeasy"
    d.mkdir(parents=True, exist_ok=True)
    return d


def profiles_dir() -> Path:
    d = app_support_dir() / "profiles"
    if not d.is_dir():
        d.mkdir(parents=True, exist_ok=True)
        _migrate_legacy_profiles(d)
    return d


def _migrate_legacy_profiles(dest: Path) -> None:
    legacy = config.LEGACY_PROFILES_DIR
    if not legacy.is_dir() or legacy == dest:
        return
    for src in legacy.glob("*.json"):
        target = dest / src.name
        if not target.exists():
            shutil.copy2(src, target)
            print(f"Migrated profile '{src.stem}' to {dest}")


def meetings_dir() -> Path:
    """Saved meeting transcripts (JSON, text only — audio is never persisted)."""
    d = app_support_dir() / "meetings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def voice_profiles_dir() -> Path:
    """Versioned local speaker embeddings; enrollment audio is never stored."""
    d = app_support_dir() / "voice_profiles"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def spool_dir() -> Path:
    """In-flight meeting audio spools. App-owned: nothing else may live here,
    because the engine sweeps it clean at every launch — that is what removes
    an orphaned recording after a crash and keeps the "audio is never
    persisted" promise."""
    d = app_support_dir() / "spool"
    d.mkdir(parents=True, exist_ok=True)
    return d


# -- settings.json --------------------------------------------------------


def _settings_path() -> Path:
    return app_support_dir() / "settings.json"


def _read() -> dict:
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    data["schema"] = _SCHEMA
    _settings_path().write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def get_last_profile() -> str | None:
    """Name of the profile active when the app last ran (None = guest)."""
    value = _read().get("last_profile")
    return value if isinstance(value, str) else None


def set_last_profile(name: str | None) -> None:
    data = _read()
    data["last_profile"] = name
    _write(data)


# -- model location --------------------------------------------------------


def model_path() -> str:
    """Where the speech model lives.

    Frozen .app → the bundled snapshot in Contents/Resources/model (two
    files: config.json + model.safetensors; parakeet-mlx loads a plain
    directory). Dev checkout → the Hugging Face model id, resolved through
    the local HF cache exactly as before.
    """
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve().parent.parent / "Resources" / "model")
    return config.MODEL_ID


def diarization_model_dir() -> Path:
    """Where the two speaker-diarization ONNX models live.

    Frozen .app → Contents/Resources/diarization (bundled by build_app.sh).
    Dev checkout → <repo>/models/diarization, populated once by
    scripts/fetch_diarization_models.sh (build/dev-time download only; the
    app itself never touches the network).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent / "Resources" / "diarization"
    return Path(__file__).resolve().parent.parent / "models" / "diarization"


def frontend_dist_path() -> Path:
    """Built web UI: bundled Resources/frontend, or frontend/dist from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent / "Resources" / "frontend"
    return Path(__file__).resolve().parent.parent / "frontend" / "dist"
