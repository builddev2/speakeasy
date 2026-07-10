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
_V_KEYCODE = 9  # 'v' on the ANSI layout


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


def insert_text(text: str) -> None:
    if not text:
        return
    _set_clipboard(text)
    time.sleep(config.CLIPBOARD_SETTLE_SECONDS)  # let the app observe the new pasteboard
    _post_cmd_v()
