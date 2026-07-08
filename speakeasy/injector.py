"""Insert text at the cursor of the frontmost app via clipboard + Cmd+V.

Clipboard access goes through NSPasteboard directly — pyperclip shells out to
pbcopy/pbpaste, costing three process spawns per dictation. The ⌘V itself is
posted as raw Quartz keyboard events (pynput's Controller was dropped: its
backend touches the Carbon Text Input Services, which SIGTRAPs once the app
has shown a text field — see hotkey.py).
"""

import time

import Quartz
from AppKit import NSPasteboard, NSPasteboardTypeString

from . import config

_CMD_KEYCODE = 55


def read_clipboard() -> str | None:
    """Return the clipboard's text, or None if it holds no text."""
    return NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)


def restore_clipboard(previous: str | None) -> None:
    if previous:
        _set_clipboard(previous)


def _set_clipboard(text: str) -> None:
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)


def _post_cmd_v() -> None:
    """Synthesize ⌘V: cmd down, 'v' down/up (as a unicode payload, so it
    works on any keyboard layout), cmd up."""
    cmd_down = Quartz.CGEventCreateKeyboardEvent(None, _CMD_KEYCODE, True)
    Quartz.CGEventSetFlags(cmd_down, Quartz.kCGEventFlagMaskCommand)
    v_down = Quartz.CGEventCreateKeyboardEvent(None, 0, True)
    Quartz.CGEventKeyboardSetUnicodeString(v_down, 1, "v")
    Quartz.CGEventSetFlags(v_down, Quartz.kCGEventFlagMaskCommand)
    v_up = Quartz.CGEventCreateKeyboardEvent(None, 0, False)
    Quartz.CGEventKeyboardSetUnicodeString(v_up, 1, "v")
    Quartz.CGEventSetFlags(v_up, Quartz.kCGEventFlagMaskCommand)
    cmd_up = Quartz.CGEventCreateKeyboardEvent(None, _CMD_KEYCODE, False)
    Quartz.CGEventSetFlags(cmd_up, 0)
    for event in (cmd_down, v_down, v_up, cmd_up):
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def insert_text(text: str) -> None:
    if not text:
        return
    _set_clipboard(text)
    time.sleep(config.CLIPBOARD_SETTLE_SECONDS)  # let the app observe the new pasteboard
    _post_cmd_v()
