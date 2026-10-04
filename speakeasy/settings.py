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
import math
import re
import shutil
import sys
from pathlib import Path

from . import config

_SCHEMA = 1
_BUILD_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}(?:-dirty)?$")


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


def library_path() -> Path:
    """The meeting library (SQLite, WAL): master copy of every transcript,
    note, tag and person. Text only — audio is never persisted."""
    return app_support_dir() / "library.sqlite"


def mcp_last_used_path() -> Path:
    """UTC timestamp of the MCP server's last tool call, shown in the
    Connect Claude sheet ("Last used by Claude: 2 min ago")."""
    return app_support_dir() / "mcp_last_used"


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


# -- meetings (phase 3) ------------------------------------------------------


def get_meeting_settings() -> dict:
    """Record-offer toggles and per-calendar include choices. A calendar
    missing from calendar_choices uses its default (calendar_sync)."""
    raw = _read().get("meetings")
    raw = raw if isinstance(raw, dict) else {}
    offer = raw.get("offer_to_record")
    detect = raw.get("detect_calls")
    choices = raw.get("calendar_choices")
    return {
        "offer_to_record": offer if isinstance(offer, bool) else True,
        "detect_calls": detect if isinstance(detect, bool) else True,
        "calendar_choices": {
            k: v for k, v in choices.items() if isinstance(k, str) and isinstance(v, bool)
        } if isinstance(choices, dict) else {},
    }


def set_meeting_settings(*, offer_to_record=None, detect_calls=None,
                         calendar_choices=None) -> dict:
    current = get_meeting_settings()
    if offer_to_record is not None:
        if not isinstance(offer_to_record, bool):
            raise ValueError("Offer to record must be on or off.")
        current["offer_to_record"] = offer_to_record
    if detect_calls is not None:
        if not isinstance(detect_calls, bool):
            raise ValueError("Call detection must be on or off.")
        current["detect_calls"] = detect_calls
    if calendar_choices is not None:
        if (not isinstance(calendar_choices, dict) or len(calendar_choices) > 500
                or not all(isinstance(k, str) and 0 < len(k) <= 300 and isinstance(v, bool)
                           for k, v in calendar_choices.items())):
            raise ValueError("Calendar choices must be on or off for each calendar.")
        current["calendar_choices"] = {**current["calendar_choices"], **calendar_choices}
    data = _read()
    data["meetings"] = current
    _write(data)
    return current


# -- appearance -----------------------------------------------------------------

APPEARANCES = ("system", "light", "dark")


def get_appearance() -> str:
    """System (follow macOS), light or dark; anything unreadable is system."""
    value = _read().get("appearance")
    return value if value in APPEARANCES else "system"


def set_appearance(choice) -> str:
    if choice not in APPEARANCES:
        raise ValueError("Appearance must be System, Light or Dark.")
    data = _read()
    data["appearance"] = choice
    _write(data)
    return choice


# -- recording ------------------------------------------------------------------


def get_identify_voices() -> bool:
    value = _read().get("identify_voices")
    return value if isinstance(value, bool) else False


def set_identify_voices(value) -> bool:
    if not isinstance(value, bool):
        raise ValueError("Identify voices must be on or off.")
    data = _read()
    data["identify_voices"] = value
    _write(data)
    return value


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def get_pill_origin() -> tuple[float, float] | None:
    raw = _read().get("pill_origin")
    if isinstance(raw, list) and len(raw) == 2 and all(_is_number(v) for v in raw):
        return (float(raw[0]), float(raw[1]))
    return None


def set_pill_origin(x, y) -> None:
    if not (_is_number(x) and _is_number(y)):
        raise ValueError("Pill position must be two numbers.")
    data = _read()
    data["pill_origin"] = [float(x), float(y)]
    _write(data)


# Event keys end in "@<UTC start>", so sorting on that suffix keeps the latest.
_PROMPTED_MAX = 500


def get_prompted_events(day: str) -> set[str]:
    """Events the record banner already offered on local day `day`, so a
    relaunch doesn't ask again. Another day's list reads as empty."""
    raw = _read().get("record_prompted")
    if not isinstance(raw, dict) or raw.get("day") != day:
        return set()
    keys = raw.get("keys")
    return {k for k in keys if isinstance(k, str)} if isinstance(keys, list) else set()


def set_prompted_events(day: str, keys) -> None:
    latest = sorted(keys, key=lambda k: (k.rsplit("@", 1)[-1], k))[-_PROMPTED_MAX:]
    data = _read()
    data["record_prompted"] = {"day": day, "keys": latest}
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


def system_audio_helper_path() -> Path:
    """Bundled process-tap helper, or the dev build output."""
    if getattr(sys, "frozen", False):
        return (
            Path(sys.executable).resolve().parent.parent
            / "Resources"
            / "native"
            / "SpeakeasySystemAudioCapture"
        )
    return (
        Path(__file__).resolve().parent.parent
        / "build"
        / "native"
        / "SpeakeasySystemAudioCapture"
    )


def build_commit() -> str:
    """Return only the source revision embedded at build time."""
    if getattr(sys, "frozen", False):
        path = (
            Path(sys.executable).resolve().parent.parent
            / "Resources"
            / "build-commit.txt"
        )
    else:
        path = Path(__file__).resolve().parent.parent / ".git"
        try:
            marker = path.read_text(encoding="utf-8").strip()
        except OSError:
            marker = ""
        if marker.startswith("gitdir: "):
            git_dir = Path(marker[8:])
        else:
            git_dir = path
        try:
            head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
            if head.startswith("ref: "):
                ref = head[5:]
                candidates = [git_dir / ref]
                try:
                    common_dir = (
                        git_dir
                        / (git_dir / "commondir").read_text(encoding="utf-8").strip()
                    ).resolve()
                    candidates.append(common_dir / ref)
                except OSError:
                    pass
                for candidate in candidates:
                    try:
                        head = candidate.read_text(encoding="utf-8").strip()
                        break
                    except OSError:
                        continue
            marker = head
        except OSError:
            marker = ""
        if _BUILD_COMMIT_RE.fullmatch(marker):
            return marker
        return "development"
    try:
        marker = path.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    return marker if _BUILD_COMMIT_RE.fullmatch(marker) else "unknown"
