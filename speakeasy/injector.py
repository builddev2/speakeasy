"""Insert text at the cursor of the frontmost app via clipboard + Cmd+V.

Clipboard access goes through NSPasteboard directly — pyperclip shells out to
pbcopy/pbpaste, costing three process spawns per dictation. The ⌘V itself is
posted as raw Quartz keyboard events (pynput's Controller was dropped: its
backend touches the Carbon Text Input Services, which SIGTRAPs once the app
has shown a text field — see hotkey.py).
"""

import time
import threading
from dataclasses import dataclass

import Quartz
from AppKit import NSPasteboard, NSPasteboardItem, NSPasteboardTypeString, NSWorkspace

from . import config
from .dictation_benchmark import DictationTiming

_CMD_KEYCODE = 55
_V_KEYCODE = 9  # 'v' on the ANSI layout


_transaction = threading.local()


def _pasteboard():
    return NSPasteboard.generalPasteboard()


@dataclass
class ClipboardSnapshot:
    items: list
    restore_count: int | None = None


def _make_item(values):
    item = NSPasteboardItem.alloc().init()
    for kind, data in values:
        item.setData_forType_(data, kind)
    return item


def read_clipboard() -> ClipboardSnapshot:
    """Materialize advertised data without interpreting or logging content."""
    pb = _pasteboard()
    items = []
    for item in pb.pasteboardItems() or []:
        values = []
        for kind in item.types():
            data = item.dataForType_(kind)
            if data is None:
                raise RuntimeError("clipboard representation unavailable")
            values.append((kind, data))
        items.append(values)
    return ClipboardSnapshot(items)


def restore_clipboard(previous) -> None:
    saved = getattr(_transaction, "snapshot", None)
    _transaction.snapshot = None
    if saved is not None:
        previous = saved
    if not isinstance(previous, ClipboardSnapshot):
        return
    pb = _pasteboard()
    if previous.restore_count is None or pb.changeCount() != previous.restore_count:
        return
    items = [_make_item(values) for values in previous.items]
    pb.clearContents()
    if items and not pb.writeObjects_(items):
        raise RuntimeError("clipboard restoration failed")


def _set_clipboard(text: str) -> None:
    pb = _pasteboard()
    pb.clearContents()
    if not pb.setString_forType_(text, NSPasteboardTypeString):
        raise RuntimeError("clipboard write failed")


def set_clipboard(text: str) -> None:
    """Put text on the clipboard deliberately (e.g. copying a meeting
    transcript) — unlike insert_text's save/paste/restore dance, here the
    clipboard is the destination."""
    _set_clipboard(text)


def _post_cmd_v() -> None:
    """Synthesize ⌘V by posting the 'v' key with the Command modifier.

    Apps match the paste shortcut on the key's *keycode*, not on any character
    string the event carries. An earlier version created the key event with
    keycode 0 and overrode its text to "v" via CGEventKeyboardSetUnicodeString;
    keycode 0 is 'a', so keycode-driven apps (Terminal, iTerm) read ⌘A and
    selected all instead of pasting. Posting the real 'v' keycode (9) fixes it.
    """
    cmd_down = Quartz.CGEventCreateKeyboardEvent(None, _CMD_KEYCODE, True)
    Quartz.CGEventSetFlags(cmd_down, Quartz.kCGEventFlagMaskCommand)
    v_down = Quartz.CGEventCreateKeyboardEvent(None, _V_KEYCODE, True)
    Quartz.CGEventSetFlags(v_down, Quartz.kCGEventFlagMaskCommand)
    v_up = Quartz.CGEventCreateKeyboardEvent(None, _V_KEYCODE, False)
    Quartz.CGEventSetFlags(v_up, Quartz.kCGEventFlagMaskCommand)
    cmd_up = Quartz.CGEventCreateKeyboardEvent(None, _CMD_KEYCODE, False)
    Quartz.CGEventSetFlags(cmd_up, 0)
    for event in (cmd_down, v_down, v_up, cmd_up):
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def insert_text(text: str, *, timing: DictationTiming | None = None, target=None):
    if not text:
        return
    # Snapshot at delivery time so a copy during inference is preserved.
    saved = read_clipboard()
    _transaction.snapshot = saved
    try:
        _set_clipboard(text)
    finally:
        saved.restore_count = _pasteboard().changeCount()
    time.sleep(config.CLIPBOARD_SETTLE_SECONDS)  # let the app observe the new pasteboard
    if target is not None and focused_target() != target:
        return "focus_changed"
    if _pasteboard().changeCount() != saved.restore_count:
        return "clipboard_changed"
    _post_cmd_v()
    if timing is not None:
        timing.mark("paste_dispatched")


def focused_target(diagnostics: dict | None = None, *,
                   deadline_ns: int | None = None,
                   clock_ns=time.perf_counter_ns,
                   retry_attempts: int | None = None):
    """Only AX element identity/role metadata; never fetch value or selection.

    ``diagnostics``, when given, is filled with content-free lookup facts
    (AX error codes, whether accessibility was enabled, whether the frontmost
    app changed, its bundle identifier). It never affects the result.

    ``deadline_ns`` (on ``clock_ns``) stops further retries once another
    retry interval would pass it; ``retry_attempts`` overrides the configured
    attempt count (0 = a single query, no sleeps).
    """
    import ApplicationServices as ax
    if diagnostics is not None:
        diagnostics.update(
            target_first_ax_error=None, target_ax_enabled=False,
            target_retry_ax_error=None, target_retry_count=0,
            target_app_switched=False,
            target_app=None, target_deadline_stop=False,
        )
    workspace = NSWorkspace.sharedWorkspace()
    application = workspace.frontmostApplication()
    if application is None:
        return None
    if diagnostics is not None:
        try:
            bundle = application.bundleIdentifier()
            diagnostics["target_app"] = str(bundle) if bundle else None
        except Exception:
            pass
    pid = application.processIdentifier()
    # Query the owning application: the system-wide proxy can return
    # kAXErrorCannotComplete even when an application's focused field is readable.
    owner = ax.AXUIElementCreateApplication(pid)
    ax.AXUIElementSetMessagingTimeout(owner, .1)
    error, element = ax.AXUIElementCopyAttributeValue(
        owner, "AXFocusedUIElement", None)
    if diagnostics is not None:
        diagnostics["target_first_ax_error"] = int(error)
    current = workspace.frontmostApplication()
    if current is None or current.processIdentifier() != pid:
        if diagnostics is not None:
            diagnostics["target_app_switched"] = True
        return None
    if error != 0 or element is None:
        enabled = _enable_accessibility(owner)
        if diagnostics is not None:
            diagnostics["target_ax_enabled"] = bool(enabled)
        # Fresh Electron apps answer NoValue until their accessibility tree is
        # built, and enabling can report False yet still work: retry regardless.
        # Never substitute an application/window for an unidentified field.
        attempts = (config.DICTATION_TARGET_RETRY_ATTEMPTS
                    if retry_attempts is None else retry_attempts)
        interval_ns = int(config.DICTATION_TARGET_RETRY_INTERVAL_SECONDS * 1_000_000_000)
        for attempt in range(1, attempts + 1):
            if deadline_ns is not None and clock_ns() + interval_ns > deadline_ns:
                if diagnostics is not None:
                    diagnostics["target_deadline_stop"] = True
                break
            time.sleep(config.DICTATION_TARGET_RETRY_INTERVAL_SECONDS)
            error, element = ax.AXUIElementCopyAttributeValue(
                owner, "AXFocusedUIElement", None)
            if diagnostics is not None:
                diagnostics["target_retry_ax_error"] = int(error)
                diagnostics["target_retry_count"] = attempt
            current = workspace.frontmostApplication()
            if current is None or current.processIdentifier() != pid:
                if diagnostics is not None:
                    diagnostics["target_app_switched"] = True
                return None
            if error == 0 and element is not None:
                break
    if error != 0 or element is None:
        return None
    ax.AXUIElementSetMessagingTimeout(element, .1)
    return element


def _enable_accessibility(owner):
    import ApplicationServices as ax
    enabled = False
    for name in ("AXManualAccessibility", "AXEnhancedUserInterface"):
        error, writable = ax.AXUIElementIsAttributeSettable(owner, name, None)
        if error == 0 and writable:
            enabled = ax.AXUIElementSetAttributeValue(owner, name, True) == 0 or enabled
    return enabled


def _web_text_field(element):
    import ApplicationServices as ax
    error, names = ax.AXUIElementCopyAttributeNames(element, None)
    # AX writes can acknowledge a DOM change without notifying the editor.
    # Use the normal paste event for web fields (and unknown implementations).
    return error != 0 or names is None or "AXDOMIdentifier" in names


def _attribute(element, name):
    import ApplicationServices as ax
    return ax.AXUIElementCopyAttributeValue(element, name, None)


def deliver_final(text, target, *, timing=None):
    """AX write acknowledgement or one unacknowledged Quartz dispatch.

    A failed AX write is ambiguous and must never trigger a second insertion.
    Secure or uninspectable fields fail closed before touching the clipboard.
    """
    import ApplicationServices as ax
    current = focused_target()
    if target is None or current is None:
        return "permission_or_focus_unavailable"
    if current != target:
        return "focus_changed"
    role_error, role = _attribute(current, "AXRole")
    subrole_error, subrole = _attribute(current, "AXSubrole")
    # Subrole is optional: Chromium returns nil for ordinary text editors.
    # NoValue is absence, whereas timeout and permission errors fail closed.
    if (role_error != 0 or role is None
            or subrole_error not in (0, ax.kAXErrorAttributeUnsupported,
                                     ax.kAXErrorNoValue)
            or subrole == "AXSecureTextField"):
        return "secure_or_unknown_field"
    error, writable = ax.AXUIElementIsAttributeSettable(current, "AXSelectedText", None)
    if (role in {"AXTextField", "AXTextArea"} and error == 0 and writable
            and not _web_text_field(current)):
        result = ax.AXUIElementSetAttributeValue(current, "AXSelectedText", text)
        if timing is not None and result == 0:
            timing.mark("paste_dispatched")
        return "ax_acknowledged" if result == 0 else "delivery_unknown"
    outcome = insert_text(text, timing=timing, target=target)
    return outcome if isinstance(outcome, str) else "dispatched_unconfirmed"
