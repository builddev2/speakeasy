"""Single owner of the speech-model update flow as the Settings page sees it.

It holds the last CheckResult, the active Downloader, the current state and
progress, and emits ``modelUpdate.changed`` payloads. Pure Python (no AppKit):
the Meetings window supplies the emitter, which hops to the main thread before
touching the WKWebView. ``model_update`` is imported lazily inside methods so
loading the UI never imports the one module allowed to reach the network.
"""

import threading
import time

from speakeasy import model_source, model_store, settings

_PROGRESS_INTERVAL = 0.25          # at most 4 progress events a second
_BUSY = ("checking", "downloading", "verifying")
_VERIFYING_TEXT = "Checking the new model…"
_NOT_AVAILABLE = "No update is available to download."


def _gb(n) -> str:
    from speakeasy import model_update
    return model_update.format_gb(n)


class ModelUpdateController:
    def __init__(self, check=None, downloader_factory=None, clock=None, emitter=None):
        self._check = check
        self._downloader_factory = downloader_factory
        self._clock = clock or time.monotonic
        self._emitter = emitter
        self._lock = threading.RLock()
        self._result = None            # last CheckResult with state "available"
        self._downloader = None
        self._thread = None
        self._state = "idle"
        self._message = ""
        self._progress = None
        self._last_progress_emit = None
        self._switched_to_bundled = False

    def set_emitter(self, emitter) -> None:
        self._emitter = emitter

    # -- status ------------------------------------------------------------

    def status(self) -> dict:
        with self._lock:
            state, message, progress = self._state, self._message, self._progress
            result = self._result
        try:
            identity = model_source.active_identity()
        except Exception:
            identity = None
        identity = identity if isinstance(identity, dict) else {}
        origin = identity.get("origin")
        revision = identity.get("revision")
        try:
            pointer = model_store.read_active()
        except Exception:
            pointer = None
        pending = (isinstance(pointer, dict) and pointer.get("status") == "pending") \
            or self._switched_to_bundled
        remote = None
        total = None
        if result is not None:
            total = result.download_bytes
            remote = {"revision": str(result.remote_revision or "")[:7], "bytes": total,
                      "sizeText": _gb(total) if total else None}
        progress_text = None
        if state == "downloading" and progress is not None and total:
            progress_text = f"{progress * total / 1e9:.1f} of {_gb(total)}"
        update_size = None
        sizes = identity.get("sizes")
        if origin == "update" and isinstance(sizes, dict):
            update_size = _gb(sum(v for v in sizes.values()
                                  if isinstance(v, (int, float)) and not isinstance(v, bool)))
        return {
            "current": {"revision": str(revision)[:7] if revision else None,
                        "origin": origin, "pending": bool(pending)},
            "autoCheck": settings.get_model_update_settings()["auto_check"],
            "state": state,
            "message": message,
            "remote": remote,
            "progress": progress if state == "downloading" else None,
            "progressText": progress_text,
            "previousRevertible": origin == "update",
            "updateSize": update_size,
        }

    def _emit(self) -> None:
        self._emit_payload(self.status())

    def _emit_payload(self, payload) -> None:
        emitter = self._emitter
        if emitter is not None:
            try:
                emitter(payload)
            except Exception:
                pass

    def _set(self, state, message="", progress=None) -> None:
        with self._lock:
            self._state, self._message, self._progress = state, message, progress
            self._last_progress_emit = None

    # -- check -------------------------------------------------------------

    def check(self) -> dict:
        with self._lock:
            if self._state in _BUSY:
                raise ValueError("A model check or download is already in progress.")
            self._set("checking")
            snapshot = self.status()
            self._thread = threading.Thread(target=self._run_check, daemon=True,
                                            name="model-check")
            self._thread.start()
        self._emit_payload(snapshot)
        return snapshot

    def _run_check(self) -> None:
        try:
            check = self._check
            if check is None:
                from speakeasy import model_update
                check = model_update.check
            result = check()
        except Exception:
            self._set("error", "Couldn't check for a model update. Speakeasy works offline as usual.")
            self._emit()
            return
        self._apply_result(result, from_check=True)

    def join(self, timeout=5.0) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def accept_check_result(self, result) -> None:
        """An automatic check finished elsewhere (menubar): remember it."""
        self._apply_result(result, from_check=False)

    def _apply_result(self, result, from_check) -> None:
        with self._lock:
            if self._state in ("downloading", "verifying"):
                return
            if not from_check and self._state == "checking":
                return                 # the user's own check will report
            self._result = result if result.state == "available" else None
            self._set(result.state, result.message)
        self._emit()

    # -- download ----------------------------------------------------------

    def download(self) -> dict:
        with self._lock:
            if self._downloader is not None or self._state in ("downloading", "verifying"):
                raise ValueError("A model download is already in progress.")
            if self._result is None or self._state not in ("available", "cancelled", "failed", "offline"):
                raise ValueError(_NOT_AVAILABLE)
            factory = self._downloader_factory
            if factory is None:
                from speakeasy import model_update
                factory = model_update.Downloader
            previous = (self._state, self._message, self._progress)
            downloader = factory(self._result, on_state=self._on_download_state)
            self._downloader = downloader
            self._set("downloading", "", 0.0)
        try:
            downloader.start()
        except BaseException:
            with self._lock:
                self._downloader = None
                self._set(*previous)
            raise
        self._emit()
        return self.status()

    def _on_download_state(self, state, progress, message) -> None:
        # Called on the downloader thread.
        with self._lock:
            if state == "downloading":
                self._state, self._message, self._progress = state, "", float(progress)
                now = self._clock()
                last = self._last_progress_emit
                if last is not None and now - last < _PROGRESS_INTERVAL:
                    return
                self._last_progress_emit = now
            elif state == "verifying":
                self._set(state, _VERIFYING_TEXT, 1.0)
            else:
                self._downloader = None
                if state == "ready":
                    self._result = None
                self._set(state, message or ("Download cancelled." if state == "cancelled" else ""))
        self._emit()

    def cancel(self) -> dict:
        with self._lock:
            downloader = self._downloader if self._state == "downloading" else None
        if downloader is not None:
            downloader.cancel()
        return self.status()

    # -- settings ----------------------------------------------------------

    def use_bundled(self) -> dict:
        with self._lock:
            if self._state in _BUSY:
                raise ValueError("Wait for the current model check or download to finish.")
        model_store.use_bundled()
        self._switched_to_bundled = True
        with self._lock:
            self._result = None
            self._set("idle", "Switching to the bundled model from the next launch.")
        self._emit()
        return self.status()

    def set_auto_check(self, value) -> dict:
        # JSON true arrives as a bool, but 1.0 is a float: accept only a bool.
        if type(value) is not bool:
            raise ValueError("Automatic check must be on or off.")
        settings.set_model_update_settings(auto_check=value)
        return self.status()


_shared = None
_shared_lock = threading.Lock()


def shared() -> ModelUpdateController:
    """The one controller per app process (bridge, menubar and window share it)."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = ModelUpdateController()
        return _shared
