"""Global hold-to-talk hotkey listener — a raw Quartz event tap.

This replaced pynput's keyboard.Listener: pynput's macOS backend queries the
Carbon Text Input Services from its listener thread, and once the app has
shown any editable text field (activating the Text Services Manager) macOS
kills the process with a dispatch main-queue assertion (SIGTRAP). A
listen-only CGEventTap makes no TIS calls at all, and pausing around the
synthesized paste becomes a CGEventTapEnable flip instead of a tap
teardown/rebuild — so the hotkey-dead window during a paste is gone too.
"""

import threading
from collections.abc import Callable

import Quartz

from . import config

# Virtual keycodes for keys that make sense as hold-to-talk hotkeys.
_KEYCODES = {
    "cmd_r": 54,
    "cmd_l": 55,
    "shift_l": 56,
    "alt_l": 58,
    "ctrl_l": 59,
    "shift_r": 60,
    "alt_r": 61,
    "ctrl_r": 62,
    "fn": 63,
    "f13": 105,
    "f14": 107,
    "f15": 113,
}

# Modifier press state must be read from the event flags. The NX_DEVICE*
# bits distinguish left/right siblings that share one public mask.
_DEVICE_BITS = {
    54: 0x0010,  # right command
    55: 0x0008,  # left command
    56: 0x0002,  # left shift
    58: 0x0020,  # left option
    59: 0x0001,  # left control
    60: 0x0004,  # right shift
    61: 0x0040,  # right option
    62: 0x2000,  # right control
}
_FLAG_MASKS = {63: Quartz.kCGEventFlagMaskSecondaryFn}


class HotkeyListener:
    """Calls on_hold_start when HOTKEY goes down, on_hold_end on release.

    Callbacks arrive on the tap thread and must return instantly — submit
    real work to an executor (same contract as always).
    """

    def __init__(
        self,
        on_hold_start: Callable[[], None],
        on_hold_end: Callable[[], None],
    ) -> None:
        self._on_hold_start = on_hold_start
        self._on_hold_end = on_hold_end
        try:
            self._keycode = _KEYCODES[config.HOTKEY]
        except KeyError:
            raise ValueError(
                f"HOTKEY {config.HOTKEY!r} isn't supported; "
                f"pick one of {', '.join(sorted(_KEYCODES))}"
            ) from None
        self._held = False
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._tap = None
        self._loop = None
        self._started = threading.Event()

    # -- event handling (tap thread) --------------------------------------

    def _pressed(self, etype, event):
        """Press state of our key from one event; None if not our key."""
        keycode = Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGKeyboardEventKeycode
        )
        if keycode != self._keycode:
            return None
        if etype == Quartz.kCGEventFlagsChanged:
            flags = Quartz.CGEventGetFlags(event)
            device_bit = _DEVICE_BITS.get(self._keycode)
            if device_bit is not None:
                return bool(flags & device_bit)
            flag_mask = _FLAG_MASKS.get(self._keycode)
            if flag_mask is not None:
                return bool(flags & flag_mask)
            return not self._held  # unknown modifier: alternate on each event
        if etype == Quartz.kCGEventKeyDown:
            return True
        if etype == Quartz.kCGEventKeyUp:
            return False
        return None

    def _callback(self, proxy, etype, event, refcon):
        if etype == Quartz.kCGEventTapDisabledByTimeout:
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, True)
            return event
        pressed = self._pressed(etype, event)
        if pressed is True and not self._held:  # keyDown autorepeats; gate it
            self._held = True
            self._on_hold_start()
        elif pressed is False and self._held:
            self._held = False
            self._on_hold_end()
        return event

    # -- tap lifecycle -----------------------------------------------------

    def _run(self) -> None:
        mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
        )
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            self._callback,
            None,
        )
        if self._tap is None:
            print(
                "The hotkey can't be watched — enable Speakeasy under "
                "System Settings → Privacy & Security → Input Monitoring."
            )
            self._started.set()
            return
        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        self._loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._loop, source, Quartz.kCFRunLoopDefaultMode)
        Quartz.CGEventTapEnable(self._tap, True)
        self._started.set()
        Quartz.CFRunLoopRun()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._held = False
            self._started.clear()
            self._thread = threading.Thread(
                target=self._run, name="hotkey-tap", daemon=True
            )
            self._thread.start()
            self._started.wait(timeout=2.0)

    def stop(self) -> None:
        with self._lock:
            thread, loop = self._thread, self._loop
            self._thread = None
            self._loop = None
            self._tap = None
            self._held = False
        if loop is not None:
            Quartz.CFRunLoopStop(loop)
        if thread is not None:
            thread.join(timeout=1.0)

    def pause(self) -> None:
        """Deaf-en the tap for the moment the ⌘V paste is synthesized."""
        with self._lock:
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, False)

    def resume(self) -> None:
        with self._lock:
            self._held = False
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, True)
                return
            need_start = self._thread is None
        if need_start:
            self.start()
