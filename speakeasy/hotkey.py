"""Global hold-to-talk hotkey listener."""

import threading
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
        self._listener: keyboard.Listener | None = None
        self._lock = threading.Lock()

    def _on_press(self, key) -> None:
        if key == config.HOTKEY and not self._held:
            self._held = True
            self._on_hold_start()

    def _on_release(self, key) -> None:
        if key == config.HOTKEY and self._held:
            self._held = False
            self._on_hold_end()

    def start(self) -> None:
        with self._lock:
            if self._listener is None:
                self._listener = keyboard.Listener(
                    on_press=self._on_press, on_release=self._on_release
                )
                self._listener.start()

    def stop(self) -> None:
        with self._lock:
            if self._listener is not None:
                self._listener.stop()
                self._listener.join(timeout=1.0)
                self._listener = None

    def pause(self) -> None:
        """Tear down the event tap around a synthesized Cmd+V paste.

        pynput's Controller and a running Listener in the same process collide:
        the injected Cmd+V is duplicated, pasting the text twice. Removing the
        tap for the paste window eliminates the collision. join() in stop()
        ensures the tap is actually gone before we paste. Listeners can't be
        restarted, so resume() builds a fresh one.
        """
        self.stop()

    def resume(self) -> None:
        self._held = False
        self.start()
