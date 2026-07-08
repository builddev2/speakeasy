"""Menu bar front end: an NSStatusItem driving the shared DictationEngine.

The status icon mirrors engine state (skull at rest → mic.fill while recording
→ waveform while transcribing); the menu offers profile switching, training,
launch-at-login and quit. Engine state changes arrive on worker/control
threads and are marshaled to the main thread with the same
performSelectorOnMainThread pattern as the overlay.
"""

import sys

import objc
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBezierPath,
    NSColor,
    NSCompositingOperationClear,
    NSControlStateValueOff,
    NSControlStateValueOn,
    NSGraphicsContext,
    NSImage,
    NSMenu,
    NSMenuItem,
    NSStatusBar,
    NSTextField,
    NSVariableStatusItemLength,
)
from Foundation import NSMakeRect, NSMakeSize, NSObject, NSTimer

from .. import config, settings
from ..engine import DictationEngine, State
from ..profiles import Profile, list_profiles, load_profiles
from . import permissions

_STATE_TEXT = {
    State.LOADING: "Loading model…",
    State.READY: "Ready",
    State.RECORDING: "Recording…",
    State.TRANSCRIBING: "Transcribing…",
    State.PAUSED: "Training…",
}
_STATE_SYMBOL = {
    State.RECORDING: "mic.fill",
    State.TRANSCRIBING: "waveform",
}
_GUEST_TAG = "\x00guest"  # representedObject marker distinct from any name


def _symbol(name: str) -> NSImage:
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
        name, "Speakeasy"
    )
    image.setTemplate_(True)  # adapts to menu bar light/dark/tint
    return image


_skull_cache = None


def _skull_image() -> NSImage:
    """The idle menu-bar glyph. macOS ships no `skull` SF Symbol, so draw one
    as a monochrome *template* image — like the SF Symbols above, template
    images ignore their own color and tint to the menu bar's light/dark/accent.
    Built once and cached (the glyph never changes)."""
    global _skull_cache
    if _skull_cache is not None:
        return _skull_cache

    pt = 16.0
    image = NSImage.alloc().initWithSize_(NSMakeSize(pt, pt))
    image.lockFocus()
    NSColor.blackColor().set()
    # Cranium (dome) overlapping the jaw below it — filled solid; the template
    # renderer uses only the resulting alpha, so overlap is harmless.
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(0.12 * pt, 0.34 * pt, 0.76 * pt, 0.60 * pt)
    ).fill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(0.30 * pt, 0.06 * pt, 0.40 * pt, 0.42 * pt), 0.12 * pt, 0.12 * pt
    ).fill()
    # Punch the eye sockets and nose by clearing alpha — real holes, so the
    # skull reads correctly once tinted.
    NSGraphicsContext.currentContext().setCompositingOperation_(
        NSCompositingOperationClear
    )
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(0.22 * pt, 0.50 * pt, 0.24 * pt, 0.24 * pt)
    ).fill()  # left eye
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(0.54 * pt, 0.50 * pt, 0.24 * pt, 0.24 * pt)
    ).fill()  # right eye
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(0.42 * pt, 0.34 * pt, 0.16 * pt, 0.16 * pt)
    ).fill()  # nose
    image.unlockFocus()

    image.setTemplate_(True)
    _skull_cache = image
    return image


def _login_service():
    """SMAppService for the running bundle, or None when not applicable.

    Launch-at-login only makes sense for the packaged .app (a bare python
    process has no bundle to register), and the API is macOS 13+.
    """
    if not getattr(sys, "frozen", False):
        return None
    try:
        sm = {}
        objc.loadBundle(
            "ServiceManagement",
            sm,
            bundle_path="/System/Library/Frameworks/ServiceManagement.framework",
        )
        return objc.lookUpClass("SMAppService").mainAppService()
    except Exception:
        return None


class StatusItemController(NSObject):
    """Owns the status item and menu. Main-thread only (like the overlay's
    controller); the engine callback hops threads before touching it."""

    def initWithEngine_(self, engine: DictationEngine):
        self = objc.super(StatusItemController, self).init()
        if self is None:
            return None
        self.engine = engine
        self.training_window = None  # set lazily by openTraining:

        self._item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self._item.button().setImage_(_skull_image())
        self._item.button().setToolTip_("Speakeasy")

        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        self._status_line = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Loading model…", None, ""
        )
        self._status_line.setEnabled_(False)
        menu.addItem_(self._status_line)
        menu.addItem_(NSMenuItem.separatorItem())

        profile_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Profile", None, ""
        )
        self._profile_menu = NSMenu.alloc().init()
        self._profile_menu.setAutoenablesItems_(False)
        profile_item.setSubmenu_(self._profile_menu)
        menu.addItem_(profile_item)
        self._rebuild_profile_menu()

        self._train_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Train Profile…", b"openTraining:", ""
        )
        self._train_item.setTarget_(self)
        menu.addItem_(self._train_item)
        self._sync_train_item()

        menu.addItem_(NSMenuItem.separatorItem())
        self._login_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Start at Login", b"toggleLogin:", ""
        )
        self._login_item.setTarget_(self)
        menu.addItem_(self._login_item)
        self._sync_login_item()

        menu.addItem_(NSMenuItem.separatorItem())
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit Speakeasy", b"quitApp:", "q"
        )
        quit_item.setTarget_(self)
        menu.addItem_(quit_item)

        self._item.setMenu_(menu)

        # Low-frequency no-op timer so Python signal handlers (Ctrl-C in dev)
        # run while the AppKit run loop owns the main thread — the overlay
        # provides one too, but only when enabled.
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.5, self, b"heartbeat:", None, True
        )
        return self

    def heartbeat_(self, timer):
        pass

    # -- engine state (arrives via performSelectorOnMainThread) ----------

    def engineStateChanged_(self, state_name):
        state = State(str(state_name))
        profile = self.engine.profile
        text = _STATE_TEXT[state]
        if state is State.READY:
            text = f"Ready — {profile.name if profile else 'Guest'}"
        self._status_line.setTitle_(text)
        symbol = _STATE_SYMBOL.get(state)
        self._item.button().setImage_(
            _symbol(symbol) if symbol else _skull_image()
        )
        self._sync_train_item()

    # -- profile menu -----------------------------------------------------

    @objc.python_method
    def _rebuild_profile_menu(self):
        self._profile_menu.removeAllItems()
        active = self.engine.profile.name if self.engine.profile else None
        for name in list_profiles():
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                name, b"selectProfile:", ""
            )
            item.setTarget_(self)
            item.setRepresentedObject_(name)
            item.setState_(
                NSControlStateValueOn if name == active else NSControlStateValueOff
            )
            self._profile_menu.addItem_(item)
        guest = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Guest (no corrections)", b"selectProfile:", ""
        )
        guest.setTarget_(self)
        guest.setRepresentedObject_(_GUEST_TAG)
        guest.setState_(
            NSControlStateValueOn if active is None else NSControlStateValueOff
        )
        self._profile_menu.addItem_(guest)
        self._profile_menu.addItem_(NSMenuItem.separatorItem())
        new = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "New Profile…", b"newProfile:", ""
        )
        new.setTarget_(self)
        self._profile_menu.addItem_(new)

    def selectProfile_(self, sender):
        tag = sender.representedObject()
        if tag == _GUEST_TAG:
            self._activate_profile(None)
            return
        try:
            self._activate_profile(Profile.load(str(tag)))
        except (OSError, ValueError) as err:
            self._error(f"Profile '{tag}' couldn't be read", str(err))

    @objc.python_method
    def _activate_profile(self, profile):
        self.engine.set_profile(profile)
        settings.set_last_profile(profile.name if profile else None)
        self._rebuild_profile_menu()
        self.engineStateChanged_(self.engine.state.value)

    def newProfile_(self, sender):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("New Profile")
        alert.setInformativeText_(
            "A profile learns how you say names and jargon "
            "for sharper transcription."
        )
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 220, 24))
        field.setPlaceholderString_("Name")
        alert.setAccessoryView_(field)
        alert.window().setInitialFirstResponder_(field)
        alert.addButtonWithTitle_("Create")
        alert.addButtonWithTitle_("Cancel")
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if alert.runModal() != NSAlertFirstButtonReturn:
            return
        try:
            self._activate_profile(Profile.create(field.stringValue()))
        except ValueError as err:
            self._error("Couldn't create profile", str(err))

    # -- training window (wired in training_window.py phase) --------------

    @objc.python_method
    def _sync_train_item(self):
        can_train = (
            self.engine.transcriber is not None and self.engine.profile is not None
        )
        self._train_item.setEnabled_(can_train)
        self._train_item.setToolTip_(
            None
            if can_train
            else "Pick a profile (not Guest) and wait for the model to load."
        )

    def openTraining_(self, sender):
        from .training_window import TrainingWindowController

        if self.training_window is None:
            self.training_window = TrainingWindowController.alloc().initWithEngine_(
                self.engine
            )
        self.training_window.showForProfile_(self.engine.profile)

    # -- launch at login ----------------------------------------------------

    @objc.python_method
    def _sync_login_item(self):
        service = _login_service()
        if service is None:
            self._login_item.setEnabled_(False)
            self._login_item.setToolTip_(
                "Available in the installed Speakeasy.app (macOS 13+)."
            )
            return
        self._login_item.setEnabled_(True)
        enabled = service.status() == 1  # SMAppServiceStatusEnabled
        self._login_item.setState_(
            NSControlStateValueOn if enabled else NSControlStateValueOff
        )

    def toggleLogin_(self, sender):
        service = _login_service()
        if service is None:
            return
        if service.status() == 1:
            ok, err = service.unregisterAndReturnError_(None)
        else:
            ok, err = service.registerAndReturnError_(None)
        if not ok:
            self._error("Couldn't update login item", str(err))
        self._sync_login_item()

    # -- misc ---------------------------------------------------------------

    @objc.python_method
    def _error(self, message: str, detail: str):
        alert = NSAlert.alloc().init()
        alert.setMessageText_(message)
        alert.setInformativeText_(detail)
        alert.runModal()

    def quitApp_(self, sender):
        print("\nShutting down.")
        self.engine.shutdown()
        NSApplication.sharedApplication().terminate_(None)


class AppDelegate(NSObject):
    """Builds everything once the run loop is up. Keeps strong refs."""

    def initWithProfileName_(self, profile_name):
        self = objc.super(AppDelegate, self).init()
        if self is None:
            return None
        self._preselected = str(profile_name) if profile_name else None
        self.engine = None
        self.controller = None
        return self

    def applicationDidFinishLaunching_(self, notification):
        engine = DictationEngine()
        engine.set_profile(_initial_profile(self._preselected))
        self.engine = engine
        self.controller = StatusItemController.alloc().initWithEngine_(engine)

        if config.OVERLAY_ENABLED:
            from .overlay import Overlay

            engine.overlay = Overlay()

        controller = self.controller
        engine.on_state_changed = lambda state: (
            controller.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"engineStateChanged:", state.value, False
            )
        )

        # Start first: the model warms up on its worker thread while the
        # user reads the (modal) first-run permissions guidance.
        engine.start()
        controller.engineStateChanged_(engine.state.value)
        if not permissions.all_granted():
            permissions.show_guidance()
        hotkey_name = config.hotkey_name()
        profile = engine.profile
        profile_tag = f" [profile: {profile.name}]" if profile else ""
        print(f"Speakeasy in the menu bar{profile_tag}. Hold [{hotkey_name}] to dictate.")

    def applicationWillTerminate_(self, notification):
        if self.engine is not None:
            self.engine.shutdown()


def _initial_profile(preselected: str | None):
    """Last-used profile (or --profile override); guest when unavailable."""
    name = preselected or settings.get_last_profile()
    if name and name in list_profiles():
        try:
            return Profile.load(name)
        except (OSError, ValueError) as err:
            print(f"Profile '{name}' couldn't be read: {err}")
    return None


def run_app(profile_name: str | None = None) -> None:
    app = NSApplication.sharedApplication()
    # No Dock icon / app switcher entry; the packaged app also sets LSUIElement.
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    delegate = AppDelegate.alloc().initWithProfileName_(profile_name)
    app.setDelegate_(delegate)

    import signal

    def _sigint(signum, frame):
        print("\nShutting down.")
        if delegate.engine is not None:
            delegate.engine.shutdown()
        app.terminate_(None)

    signal.signal(signal.SIGINT, _sigint)
    signal.signal(signal.SIGTERM, _sigint)
    app.run()
