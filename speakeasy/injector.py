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
from AppKit import NSPasteboard, NSPasteboardItem, NSPasteboardTypeString

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


def focused_target():
    """Only AX element identity/role metadata; never fetch value or selection."""
    import ApplicationServices as ax
    system = ax.AXUIElementCreateSystemWide()
    ax.AXUIElementSetMessagingTimeout(system, .1)
    error, element = ax.AXUIElementCopyAttributeValue(
        system, "AXFocusedUIElement", None)
    if error != 0:
        return None
    ax.AXUIElementSetMessagingTimeout(element, .1)
    return element


def _attribute(element, name):
    import ApplicationServices as ax
    error, value = ax.AXUIElementCopyAttributeValue(element, name, None)
    return value if error == 0 else None


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
    role = _attribute(current, "AXRole")
    subrole = _attribute(current, "AXSubrole")
    if role is None or subrole == "AXSecureTextField":
        return "secure_or_unknown_field"
    error, writable = ax.AXUIElementIsAttributeSettable(current, "AXSelectedText", None)
    if role in {"AXTextField", "AXTextArea"} and error == 0 and writable:
        result = ax.AXUIElementSetAttributeValue(current, "AXSelectedText", text)
        if timing is not None and result == 0:
            timing.mark("paste_dispatched")
        return "ax_acknowledged" if result == 0 else "delivery_unknown"
    outcome = insert_text(text, timing=timing, target=target)
    return outcome if isinstance(outcome, str) else "dispatched_unconfirmed"
