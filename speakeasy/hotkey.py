"""Global hold-to-talk hotkey listener."""

from collections.abc import Callable

from pynput import keyboard

from . import config


class HotkeyListener:
    def __init__(
        self,
        on_hold_start: Callable[[], None],
        on_hold_end: Callable[[], None],
    ) -> None:
        self._on_hold_start = on_hold_start
        self._on_hold_end = on_hold_end
        self._held = False
        self._listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release
        )

    def _on_press(self, key) -> None:
        if key == config.HOTKEY and not self._held:
            self._held = True
            self._on_hold_start()

    def _on_release(self, key) -> None:
        if key == config.HOTKEY and self._held:
            self._held = False
            self._on_hold_end()

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        self._listener.stop()
