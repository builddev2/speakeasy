"""Speech-model update check. The ONLY module allowed to use the network
(via /usr/bin/curl subprocesses), and only for config.MODEL_ID on
huggingface.co. Never raises to callers; never logs response bodies.
"""

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Protocol

from . import config, model_source, model_store

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
            p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=timeout)
        except subprocess.TimeoutExpired:
            return 28, b""
        except OSError:
            return 127, b""
        return p.returncode, p.stdout

    def start(self, argv):
        return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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


# ---------------------------------------------------------------- download

_POLL = 0.5          # seconds between progress polls while curl runs
_KILL_AFTER = 2.0    # seconds to wait after terminate() before kill()
_VERIFY_TIMEOUT = 300
_CHUNK = 8 * 1024 * 1024
_FILES = ("config.json", "model.safetensors")
_active = threading.Lock()   # held for the lifetime of one download


class _Cancelled(Exception):
    pass


class _Failed(Exception):
    def __init__(self, state, message):
        super().__init__(message)
        self.state = state
        self.message = message


_UNCHANGED = "Your current model is unchanged."


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _git_blob_sha1(path) -> str:
    data = open(path, "rb").read()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _verify_argv(directory) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--verify-model", str(directory)]
    return [sys.executable, "-m", "speakeasy", "--verify-model", str(directory)]


def _reason(rc, out) -> str:
    if rc == 28 and not out:
        return "timed out"
    lines = [ln.strip() for ln in out.decode("utf-8", "replace").splitlines() if ln.strip()]
    return (lines[-1] if lines else f"exit {rc}")[:200]


class Downloader:
    """Downloads, verifies and stages a speech-model update on its own thread."""

    def __init__(self, result, runner=None, on_state=None):
        self._result = result
        self._runner = runner or SubprocessRunner()
        self._on_state = on_state
        self._cancel = threading.Event()
        self._proc = None
        self._thread = None

    def start(self):
        if not _active.acquire(blocking=False):
            raise ValueError("A model download is already in progress.")
        try:
            self._thread = threading.Thread(target=self._thread_main, daemon=True,
                                            name="model-download")
            self._thread.start()
        except BaseException:
            _active.release()
            raise

    def cancel(self):
        self._cancel.set()

    def _emit(self, state, progress=0.0, message=""):
        if self._on_state is None:
            return
        try:
            self._on_state(state, progress, message)
        except Exception:
            log.warning("model download state callback failed")

    def _thread_main(self):
        try:
            self._run()
        except _Cancelled:
            self._cleanup()
            self._emit("cancelled", 0.0, f"Download cancelled. {_UNCHANGED}")
        except _Failed as exc:
            self._cleanup()
            self._emit(exc.state, 0.0, exc.message)
        except Exception as exc:
            log.warning("model download failed: %s", type(exc).__name__)
            self._cleanup()
            self._emit("failed", 0.0, f"The update couldn't be installed. {_UNCHANGED}")
        finally:
            _active.release()

    # staging path is tracked on self so cleanup works from any failure point
    def _cleanup(self):
        path = getattr(self, "_staging", None)
        if path is not None:
            shutil.rmtree(path, ignore_errors=True)
            model_store.release_staging(path)
            self._staging = None

    def _check_cancel(self):
        if self._cancel.is_set():
            raise _Cancelled()

    def _run(self):
        r = self._result
        root = model_store.speech_models_dir()
        root.mkdir(parents=True, exist_ok=True)
        need = (r.download_bytes or 0) + 1_000_000_000
        if shutil.disk_usage(root).free < need:
            raise _Failed("failed", f"Not enough free disk space: the update needs about "
                          f"{format_gb(need)} free ({format_gb(r.download_bytes or 0)} "
                          "download plus room to verify).")
        sha = r.remote_revision
        self._staging = staging = root / f".staging-{sha}"
        model_store.mark_staging_in_use(staging)
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        sizes = r.remote_sizes
        total = sum(sizes[n] for n in _FILES) or 1
        done = 0
        self._emit("downloading", 0.0)
        for name in _FILES:
            self._check_cancel()
            self._fetch(name, staging, sha, sizes[name], done, total)
            done += sizes[name]
        self._check_cancel()
        files = r.remote_files
        if (_git_blob_sha1(staging / "config.json") != files["config.json"]
                or _sha256(staging / "model.safetensors") != files["model.safetensors"]):
            raise _Failed("failed", f"The download was damaged (checksum mismatch). {_UNCHANGED}")
        self._check_cancel()
        self._emit("verifying", 1.0)
        rc, out = self._runner.run(_verify_argv(staging), timeout=_VERIFY_TIMEOUT)
        if rc != 0:
            raise _Failed("failed", f"The new model didn't pass its check ({_reason(rc, out)}). "
                          f"{_UNCHANGED}")
        self._check_cancel()
        (staging / "source.json").write_text(json.dumps({
            "model_id": config.MODEL_ID, "revision": sha,
            "files": files, "sizes": sizes}))
        final = root / f"rev-{sha}"
        if final.exists():
            shutil.rmtree(final)
        os.replace(staging, final)
        active = model_store.read_active()
        previous = active.get("dir") if active else None
        model_store.write_active({"revision": sha, "dir": final.name,
                                  "status": "pending", "previous": previous})
        model_store.release_staging(staging)
        self._staging = None
        self._emit("ready", 1.0, "Update ready. It will be used the next time Speakeasy starts.")

    def _fetch(self, name, staging, sha, expected, done, total):
        target = staging / name
        url = f"https://huggingface.co/{config.MODEL_ID}/resolve/{sha}/{name}"
        argv = [CURL, "-sS", "-f", "-L", "--connect-timeout", "10", "--speed-limit", "1024",
                "--speed-time", "60", "-o", str(target), url]
        proc = self._proc = self._runner.start(argv)
        while proc.poll() is None:
            if self._cancel.is_set():
                self._stop(proc)
                raise _Cancelled()
            try:
                got = target.stat().st_size
            except OSError:
                got = 0
            self._emit("downloading", min(1.0, (done + min(got, expected)) / total))
            self._cancel.wait(_POLL)
        rc = proc.poll()
        if self._cancel.is_set():
            raise _Cancelled()
        if rc in _OFFLINE_CODES:
            raise _Failed("offline", _OFFLINE_MSG)
        if rc != 0:
            raise _Failed("failed", f"The download didn't finish (curl exit {rc}). {_UNCHANGED}")
        try:
            size = target.stat().st_size
        except OSError:
            size = -1
        if size != expected:
            raise _Failed("failed", f"The download didn't finish (file incomplete). {_UNCHANGED}")
        self._emit("downloading", (done + expected) / total)

    @staticmethod
    def _stop(proc):
        proc.terminate()
        deadline = _KILL_AFTER
        waited = 0.0
        while proc.poll() is None and waited < deadline:
            threading.Event().wait(0.02)
            waited += 0.02
        if proc.poll() is None:
            proc.kill()
