"""Speech-model update check. The ONLY module allowed to use the network
(via /usr/bin/curl subprocesses), and only for config.MODEL_ID on
huggingface.co. Never raises to callers; never logs response bodies.
"""

import json
import logging
import subprocess
from dataclasses import dataclass
from typing import Protocol

from . import config, model_source

log = logging.getLogger(__name__)

CURL = "/usr/bin/curl"
CHECK_URL = f"https://huggingface.co/api/models/{config.MODEL_ID}/revision/main?blobs=true"
_OFFLINE_CODES = (6, 7, 28)
_RUN_TIMEOUT = 20.0
_OFFLINE_MSG = ("Couldn't reach Hugging Face. Check your internet connection and try "
                "again. Speakeasy works offline as usual.")


class Runner(Protocol):
    def run(self, argv: list[str], *, timeout: float) -> tuple[int, bytes]: ...

    def start(self, argv: list[str]): ...


class SubprocessRunner:
    def run(self, argv, *, timeout):
        try:
            p = subprocess.run(argv, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 28, b""
        except OSError:
            return 127, b""
        return p.returncode, p.stdout

    def start(self, argv):
        return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


@dataclass(frozen=True)
class CheckResult:
    state: str
    remote_revision: str | None
    current_revision: str | None
    download_bytes: int | None
    message: str
    remote_files: dict | None = None
    remote_sizes: dict | None = None


def format_gb(n) -> str:
    return f"{n / 1e9:.1f} GB"


def _short(rev) -> str:
    return str(rev)[:7]


def _error(reason, remote=None, current=None) -> CheckResult:
    return CheckResult("error", remote, current, None,
                       f"Couldn't check for a model update ({reason}). "
                       "Speakeasy works offline as usual.")


def _current():
    try:
        ident = model_source.active_identity()
    except Exception:
        return None
    if not isinstance(ident, dict):
        return None
    files = ident.get("files")
    rev = ident.get("revision")
    if not isinstance(rev, str) or not rev or not isinstance(files, dict):
        return None
    cfg, safe = files.get("config.json"), files.get("model.safetensors")
    if not isinstance(cfg, str) or not isinstance(safe, str) or not cfg or not safe:
        return None
    return rev, cfg, safe


def _size(v) -> int:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else 0


def _check(runner) -> CheckResult:
    cur = _current()
    if cur is None:
        return CheckResult("unknown_current", None, None, None,
                           "Can't tell which model is installed, so no update can be offered.")
    cur_rev, cur_cfg, cur_safe = cur
    argv = [CURL, "-sS", "-f", "-L", "--max-time", "15", "--connect-timeout", "5", CHECK_URL]
    rc, out = runner.run(argv, timeout=_RUN_TIMEOUT)
    if rc in _OFFLINE_CODES:
        return CheckResult("offline", None, cur_rev, None, _OFFLINE_MSG)
    if rc != 0:
        return _error(f"curl exit {rc}", current=cur_rev)
    try:
        data = json.loads(out)
    except ValueError:
        return _error("unreadable reply", current=cur_rev)
    if not isinstance(data, dict):
        return _error("unexpected reply", current=cur_rev)
    sha = data.get("sha")
    if not isinstance(sha, str) or not sha:
        return _error("reply has no revision", current=cur_rev)
    sibs = {s.get("rfilename"): s for s in data.get("siblings") or [] if isinstance(s, dict)}
    cfg_s, safe_s = sibs.get("config.json"), sibs.get("model.safetensors")
    if cfg_s is None or safe_s is None:
        return _error("model files missing from reply", sha, cur_rev)
    lfs = safe_s.get("lfs")
    cfg_id = cfg_s.get("blobId")
    safe_id = lfs.get("sha256") if isinstance(lfs, dict) else None
    if not isinstance(cfg_id, str) or not isinstance(safe_id, str):
        return _error("model file ids missing from reply", sha, cur_rev)
    safe_size = _size(lfs.get("size")) or _size(safe_s.get("size"))
    cfg_size = _size(cfg_s.get("size"))
    if not safe_size or not cfg_size:
        return _error("model size missing from reply", sha, cur_rev)
    files = {"config.json": cfg_id, "model.safetensors": safe_id}
    sizes = {"config.json": cfg_size, "model.safetensors": safe_size}
    if cfg_id == cur_cfg and safe_id == cur_safe:
        if sha == cur_rev:
            return CheckResult("up_to_date", sha, cur_rev, None,
                               f"Speech model is up to date (revision {_short(sha)}).",
                               files, sizes)
        return CheckResult("same_files", sha, cur_rev, None,
                           f"Up to date. The repository changed ({_short(cur_rev)} → "
                           f"{_short(sha)}), but the model files are the same.", files, sizes)
    total = safe_size + cfg_size
    return CheckResult("available", sha, cur_rev, total, "A newer speech model is available "
                       f"(revision {_short(sha)}, {format_gb(total)} download).",
                       files, sizes)


def check(runner: Runner | None = None) -> CheckResult:
    try:
        return _check(runner or SubprocessRunner())
    except Exception as exc:
        log.warning("model update check failed: %s", type(exc).__name__)
        return _error("unexpected problem")
