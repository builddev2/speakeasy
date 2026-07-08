"""Insert text at the cursor of the frontmost app via clipboard + Cmd+V.

Clipboard access goes through NSPasteboard directly — pyperclip shells out to
pbcopy/pbpaste, costing three process spawns per dictation.
"""

import time

from AppKit import NSPasteboard, NSPasteboardTypeString
from pynput.keyboard import Controller, Key

from . import config

_keyboard = Controller()


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


def insert_text(text: str) -> None:
    if not text:
        return
    _set_clipboard(text)
    time.sleep(config.CLIPBOARD_SETTLE_SECONDS)  # let the app observe the new pasteboard
    with _keyboard.pressed(Key.cmd):
        _keyboard.press("v")
        _keyboard.release("v")
