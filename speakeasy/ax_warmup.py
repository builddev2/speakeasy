"""Switch on a fresh Chromium/Electron app's accessibility tree before dictation.

A freshly launched Codex answers AXFocusedUIElement with NoValue indefinitely
until AXEnhancedUserInterface is written. The write returns NotImplemented yet
reads back True, and focus answers about 2 s later (probe, 29 Sep 2026; see
docs/insertion-focus-regression.md). Key-down retries last about 0.3 s and
must not wait longer, so the write happens when the app is activated instead.

Only AX metadata (error codes, one boolean) is read -- never values. All work
runs on one `ax-warmup` thread: never the main thread, worker or control.
AXManualAccessibility is not written: Codex does not implement it.
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from AppKit import NSWorkspaceApplicationKey

from . import config

_NO_VALUE = -25212
_CANNOT_COMPLETE = -25204
_ATTRIBUTE = "AXEnhancedUserInterface"


def activated_pid(notification):
    try:
        info = notification.userInfo()
        app = info[NSWorkspaceApplicationKey] if info else None
        return int(app.processIdentifier()) if app is not None else None
    except Exception:
        return None


class AccessibilityWarmer:
    def __init__(self, ax_module=None, *, own_pid=None, sleep=time.sleep,
                 attempts=None, interval=None, executor=None):
        if ax_module is None:
            import ApplicationServices as ax_module
        self._ax = ax_module
        self._own_pid = os.getpid() if own_pid is None else own_pid
        self._sleep = sleep
        self._attempts = config.AX_WARMUP_ATTEMPTS if attempts is None else attempts
        self._interval = (config.AX_WARMUP_RETRY_INTERVAL_SECONDS
                          if interval is None else interval)
        self._executor = executor or ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="ax-warmup")
        self._lock = threading.Lock()
        self._in_flight: set[int] = set()
        self._stopped = False

    def app_activated(self, pid) -> None:
        if pid is None or pid <= 0 or pid == self._own_pid:
            return
        with self._lock:
            if self._stopped or pid in self._in_flight:
                return
            self._in_flight.add(pid)
        try:
            self._executor.submit(self._run, pid)
        except RuntimeError:  # executor already shut down
            with self._lock:
                self._in_flight.discard(pid)

    def _run(self, pid):
        try:
            return self.warm(pid)
        except Exception:
            return "failed"
        finally:
            with self._lock:
                self._in_flight.discard(pid)

    def warm(self, pid) -> str:
        outcome = "busy"
        for attempt in range(self._attempts):
            if attempt:
                self._sleep(self._interval)
            if self._stopped:
                return "shutdown"
            outcome = self._try(pid)
            if outcome != "busy":
                break
        return outcome

    def _try(self, pid) -> str:
        ax = self._ax
        owner = ax.AXUIElementCreateApplication(pid)
        ax.AXUIElementSetMessagingTimeout(owner, .1)
        error, element = ax.AXUIElementCopyAttributeValue(
            owner, "AXFocusedUIElement", None)
        if error == 0 and element is not None:
            return "focus_answers"
        if error == _CANNOT_COMPLETE:
            return "busy"
        if error != _NO_VALUE:
            return "failed"
        error, settable = ax.AXUIElementIsAttributeSettable(owner, _ATTRIBUTE, None)
        if error != 0 or not settable:
            return "unsupported"
        error, value = ax.AXUIElementCopyAttributeValue(owner, _ATTRIBUTE, None)
        if error == 0 and value is True:
            return "already_enabled"
        # Chromium applies the value but answers NotImplemented: trust the read-back.
        ax.AXUIElementSetAttributeValue(owner, _ATTRIBUTE, True)
        error, value = ax.AXUIElementCopyAttributeValue(owner, _ATTRIBUTE, None)
        return "enabled" if error == 0 and value is True else "failed"

    def shutdown(self) -> None:
        with self._lock:
            self._stopped = True
        self._executor.shutdown(wait=False, cancel_futures=True)
