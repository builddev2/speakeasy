"""Insert text at the cursor of the frontmost app via clipboard + Cmd+V."""

import time

import pyperclip
from pynput.keyboard import Controller, Key

from . import config

_keyboard = Controller()


def insert_text(text: str) -> None:
    if not text:
        return
    try:
        previous = pyperclip.paste()
    except pyperclip.PyperclipException:
        previous = None  # non-text clipboard contents can't be saved/restored

    pyperclip.copy(text)
    time.sleep(0.05)  # let the pasteboard update before pasting
    with _keyboard.pressed(Key.cmd):
        _keyboard.press("v")
        _keyboard.release("v")
    time.sleep(config.PASTE_SETTLE_SECONDS)

    if previous:
        pyperclip.copy(previous)
