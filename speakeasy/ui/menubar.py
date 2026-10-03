"""Menu bar front end: an NSStatusItem driving the shared DictationEngine.

The status icon mirrors engine state (skull at rest → mic.fill while recording
→ waveform while transcribing); the menu offers profile switching, training,
settings and quit. Engine state changes arrive on worker/control
threads and are marshaled to the main thread with the same
performSelectorOnMainThread pattern as the overlay.

The app also has a normal Dock icon (NSApplicationActivationPolicyRegular) so
there's a way back in if the status item ever ends up hidden by menu-bar
overflow: launch and a Dock click open the Meetings window via
applicationShouldHandleReopen_hasVisibleWindows_. The status item remains the
primary interface.
"""

import json
import logging
import sys

import objc
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSBezierPath,
    NSColor,
    NSCompositingOperationClear,
    NSControlStateValueOff,
    NSControlStateValueOn,
    NSEventModifierFlagCommand,
    NSEventModifierFlagOption,
    NSEventModifierFlagShift,
    NSGraphicsContext,
    NSImage,
    NSMenu,
    NSMenuItem,
    NSStatusBar,
    NSTextField,
    NSVariableStatusItemLength,
    NSWorkspace,
    NSWorkspaceDidActivateApplicationNotification,
    NSWorkspaceDidWakeNotification,
)
from Foundation import NSMakeRect, NSMakeSize, NSObject, NSTimer

from .. import config, settings
from ..engine import DictationEngine, State
from ..meeting_options import default_options
from ..profiles import Profile, list_profiles, load_profiles
from . import permissions
from .menu_model import menu_flags
from .next_up import next_up
from .next_up_view import NextUpView

_STATE_TEXT = {
    State.LOADING: "Loading model…",
    State.READY: "Ready",
    State.RECORDING: "Recording…",
    State.TRANSCRIBING: "Transcribing…",
    State.PAUSED: "Training…",
    State.MIC_RECOVERING: "Restarting microphone — please wait…",
    State.MIC_FAILED: "Microphone unavailable — check input and permission, then try again",
    State.MEETING_RECORDING: "Recording meeting — dictation paused",
    State.MEETING_PROCESSING: "Processing meeting…",
}
_STATE_SYMBOL = {
    State.RECORDING: "mic.fill",
    State.TRANSCRIBING: "waveform",
    State.MEETING_RECORDING: "record.circle",
    State.MEETING_PROCESSING: "waveform.circle",
}

_MEETING_TOOLTIP = (
    "On macOS 14.2+, records microphone and system audio separately. "
    "If system audio is unavailable or denied, Speakeasy visibly falls back "
    "to microphone-only. Temporary audio is deleted after processing."
)
_GUEST_TAG = "\x00guest"  # representedObject marker distinct from any name


def _meeting_status_text(recorder) -> str:
    health = getattr(recorder, "health", None)
    if health is not None:
        if health.helper_exited and health.helper_exit_reason != "requested_stop":
            return "Recording meeting — microphone only (system helper exited)"
        if health.system_writer_failed:
            return "Recording meeting — microphone only (system writer failed)"
        if health.mic_writer_failed:
            return "Recording meeting — microphone writer failed"
        if health.capture_mode == "mic_and_system":
            source = (
                "selected application"
                if health.capture_scope == "selected"
                else "system audio"
            )
            if not health.system_first_buffer:
                return f"Recording meeting — microphone + {source} (waiting for data)"
            if not health.system_nonzero_signal:
                return f"Recording meeting — microphone + {source} (no signal yet)"
            if health.mic_writer_lagged or health.system_writer_lagged:
                return f"Recording meeting — microphone + {source} (capture lag)"
            return f"Recording meeting — microphone + {source}"
    status = getattr(recorder, "system_audio_status", "unavailable")
    reason = {
        "requires_macos_14_2": "requires macOS 14.2+",
        "permission_denied_or_unavailable": "permission denied",
        "helper_missing": "capture helper unavailable",
        "helper_exited": "capture helper exited",
        "selected_app_unavailable": "selected application unavailable",
    }.get(status, "system audio unavailable")
    return f"Recording meeting — microphone only ({reason})"


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


class StatusItemController(NSObject):
    """Owns the status item and menu. Main-thread only (like the overlay's
    controller); the engine callback hops threads before touching it."""

    def initWithEngine_(self, engine: DictationEngine):
        self = objc.super(StatusItemController, self).init()
        if self is None:
            return None
        self.engine = engine
        self.training_window = None  # set lazily by openTraining:
        self.meetings_window = None  # set lazily by openMeetings:
        self._pending_library_status = None  # replayed into a window created later
        self.diagnostic_window = None  # built lazily by openDiagnostic:

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
        # Shown only while a recording is linked to a calendar event; a
        # separate item so it never hides the capture-health warnings above.
        self._event_line = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "", None, ""
        )
        self._event_line.setEnabled_(False)
        self._event_line.setHidden_(True)
        menu.addItem_(self._event_line)
        menu.addItem_(NSMenuItem.separatorItem())

        def add(title, action, key="", tooltip=None):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
            item.setTarget_(self)
            if tooltip:
                item.setToolTip_(tooltip)
            menu.addItem_(item)
            return item

        # Next-up card: a custom view, shown only while idle with an event due.
        self._next_up = NextUpView.alloc().initWithTarget_(self)
        self._next_up_key = None
        self._next_up_item = NSMenuItem.alloc().init()
        self._next_up_item.setView_(self._next_up.view())
        self._next_up_item.setHidden_(True)
        menu.addItem_(self._next_up_item)

        self._meeting_item = add("Start Meeting", b"toggleMeeting:", "", _MEETING_TOOLTIP)
        self._meeting_item.setEnabled_(False)  # enabled once the model is up
        self._cancel_item = add(
            "Cancel Processing", b"cancelProcessing:", "",
            "Discards this meeting. Cancellation is checked between "
            "transcription chunks and speaker-identification passes.")
        self._cancel_item.setHidden_(True)
        self._retry_item = add("Retry Microphone", b"retryMicrophone:")
        self._retry_item.setHidden_(True)
        menu.addItem_(NSMenuItem.separatorItem())

        self._meetings_item = add("Open Meetings", b"openMeetings:", "o")
        self._notes_item = add("Meeting Notes", b"openMeetingNotes:")
        self._notes_item.setHidden_(True)
        menu.addItem_(NSMenuItem.separatorItem())

        profile_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Profile", None, ""
        )
        self._profile_menu = NSMenu.alloc().init()
        self._profile_menu.setAutoenablesItems_(False)
        profile_item.setSubmenu_(self._profile_menu)
        menu.addItem_(profile_item)
        self._rebuild_profile_menu()

        self._train_item = add("Train My Voice…", b"openTraining:")
        self._diagnostic_item = add("Check Microphone…", b"openDiagnostic:")
        self._correct_item = add("Correct Last Dictation…", b"correctLastDictation:")
        self._recovery_items = []
        for title, action in (
            ("Copy Last Dictation", b"copyLastDictation:"),
            ("Paste Last Dictation (may duplicate)", b"pasteLastDictation:"),
        ):
            item = add(title, action)
            item.setEnabled_(False)
            self._recovery_items.append(item)
        self._sync_train_item()

        menu.addItem_(NSMenuItem.separatorItem())
        add("Settings…", b"openSettings:", ",")
        add("Quit Speakeasy", b"quitApp:", "q")

        menu.setDelegate_(self)

        self._item.setMenu_(menu)

        # Low-frequency no-op timer so Python signal handlers (Ctrl-C in dev)
        # run while the AppKit run loop owns the main thread — the overlay
        # provides one too, but only when enabled.
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.5, self, b"heartbeat:", None, True
        )
        return self

    def heartbeat_(self, timer):
        available = bool(self.engine.last_dictation_text)
        self._recovery_items[0].setEnabled_(available)
        self._recovery_items[1].setEnabled_(
            available and self.engine.state in {State.READY, State.MIC_FAILED}
        )
        # Doubles as the meeting elapsed-time ticker; otherwise it exists
        # only so Python signal handlers run under the AppKit run loop.
        if self.engine.state is State.MEETING_RECORDING:
            recorder = self.engine.meeting_recorder
            elapsed = int(recorder.elapsed_seconds)
            m, s = divmod(elapsed, 60)
            h, m = divmod(m, 60)
            clock = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
            self._meeting_item.setTitle_(f"End Meeting ({clock})")
            self._status_line.setTitle_(_meeting_status_text(recorder))

    # -- engine state (arrives via performSelectorOnMainThread) ----------

    def engineStateChanged_(self, state_name):
        state = State(str(state_name))
        profile = self.engine.profile
        text = _STATE_TEXT[state]
        if state is State.READY:
            text = f"Ready — {profile.name if profile else 'Guest'}"
            outcome = self.engine.last_insertion_outcome
            if outcome in {"focus_changed", "clipboard_changed", "permission_or_focus_unavailable",
                           "secure_or_unknown_field", "delivery_unknown"}:
                text = "Text retained for 60 seconds — use Copy Last Dictation"
            if self.engine.meeting_processing_error:
                text = "Meeting could not finish — check saved meetings"
        elif state is State.MIC_FAILED:
            if self.engine.recorder.state == "permission_blocked":
                text = "Allow Microphone in System Settings, then try again"
            elif self.engine.recorder.state == "device_unavailable":
                text = "Connect a microphone or select an input, then try again"
        elif state is State.MEETING_RECORDING:
            text = _meeting_status_text(self.engine.meeting_recorder)
        self._status_line.setTitle_(text)
        symbol = _STATE_SYMBOL.get(state)
        self._item.button().setImage_(
            _symbol(symbol) if symbol else _skull_image()
        )
        self._sync_train_item()
        self._correct_item.setEnabled_(
            self.engine.profile is not None
            and bool(self.engine.last_dictation_heard)
            and state is State.READY
        )
        self._sync_meeting_items(state)
        self._apply_flags(menu_flags(state.value))

    @objc.python_method
    def _sync_meeting_items(self, state):
        event = self.engine.meeting_event if state is State.MEETING_RECORDING else None
        self._event_line.setHidden_(event is None)
        if event is not None:
            self._event_line.setTitle_(f"Recording · {event.title}")
        if state is State.MEETING_RECORDING:
            self._meeting_item.setTitle_("End Meeting (00:00)")
            self._meeting_item.setEnabled_(True)
        else:
            self._meeting_item.setTitle_("Start Meeting")
            self._meeting_item.setEnabled_(state is State.READY)

    def meetingProgress_(self, text):
        # Live "Transcribing meeting… 42%" line from the worker thread.
        self._status_line.setTitle_(str(text))

    def meetingSaved_(self, meeting_id):
        if self.meetings_window is not None:
            self.meetings_window.meetingSaved_(meeting_id)

    def libraryStatus_(self, payload):
        self._pending_library_status = payload
        if self.meetings_window is not None:
            self.meetings_window.libraryStatus_(payload)

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

    # -- meetings -----------------------------------------------------------

    def toggleMeeting_(self, sender):
        if self.engine.state is State.MEETING_RECORDING:
            self.engine.end_meeting()
        else:
            self.engine.begin_meeting(default_options())

    def cancelProcessing_(self, sender):
        self.engine.cancel_meeting_processing()

    @objc.python_method
    def open_meetings(self, view=None):
        from .meetings_window import MeetingsWindowController

        if self.meetings_window is None:
            self.meetings_window = MeetingsWindowController.alloc().init()
            if self._pending_library_status is not None:
                self.meetings_window.libraryStatus_(self._pending_library_status)
        self.meetings_window.show(view)

    def openMeetings_(self, sender):
        self.open_meetings(None)

    def openMeetingNotes_(self, sender):
        self.open_meetings("recording")

    def openSettings_(self, sender):
        self.open_meetings("settings")

    def retryMicrophone_(self, sender):
        self.engine.retry_microphone()

    def openDiagnostic_(self, sender):
        if getattr(self, "diagnostic_window", None) is None:
            from .diagnostic_window import DiagnosticWindowController
            self.diagnostic_window = DiagnosticWindowController.alloc().initWithEngine_(self.engine)
        self.diagnostic_window.show()

    @objc.python_method
    def _apply_flags(self, flags):
        self._notes_item.setHidden_(not flags["meeting_notes"])
        self._retry_item.setHidden_(not flags["retry_mic"])
        self._cancel_item.setHidden_(not flags["cancel"])
        if not flags["next_up"]:
            self._next_up_item.setHidden_(True)

    def menuWillOpen_(self, menu):
        # Must never raise: a library or calendar failure just hides the card.
        try:
            flags = menu_flags(self.engine.state.value)
            self._apply_flags(flags)
            card = None
            if flags["next_up"]:
                from datetime import datetime

                from ..meeting_library import MeetingLibrary
                from . import services

                sync = services.calendar_sync
                if sync is not None and sync.access() == "connected":
                    now = datetime.now().astimezone()
                    today = now.date().isoformat()
                    card = next_up(MeetingLibrary().calendar_events_between(today, today), now, None)
            if card is None:
                self._next_up_key = None
                self._next_up_item.setHidden_(True)
            else:
                self._next_up_key = card["key"]
                self._next_up.set_event(card["title"], card["subtitle"])
                self._next_up_item.setHidden_(False)
        except Exception:
            logging.getLogger(__name__).exception("menu open failed")
            try:
                self._next_up_key = None
                self._next_up_item.setHidden_(True)
            except Exception:
                pass

    def nextUpRecord_(self, sender):
        self._item.menu().cancelTracking()
        self.engine.begin_meeting(default_options(calendar_event_key=self._next_up_key))

    # -- training window (wired in training_window.py phase) --------------

    @objc.python_method
    def _sync_train_item(self):
        can_train = self.engine.can_train
        self._train_item.setEnabled_(can_train)
        self._train_item.setToolTip_(
            None
            if can_train
            else "Pick a profile (not Guest) and wait for the model to load."
        )

    def openTraining_(self, sender):
        # This is now the single entry point for opening Training (the menu
        # item's own enabled state gates the native menu, but this is also
        # reached by delegation from web pages, which have no equivalent native
        # disablement) — so the profile guard has to live here too.
        if self.engine.profile is None or self.engine._diagnostic_cancel is not None:
            return
        from .training_window import TrainingWindowController

        if self.training_window is None:
            self.training_window = TrainingWindowController.alloc().initWithEngine_(
                self.engine
            )
        self.training_window.showForProfile_(self.engine.profile)

    def copyLastDictation_(self, sender):
        self.engine.copy_last_dictation()

    def pasteLastDictation_(self, sender):
        self.engine.paste_last_dictation()

    def correctLastDictation_(self, sender):
        if self.engine.profile is None or not self.engine.last_dictation_heard:
            return
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Correct Last Dictation")
        alert.setInformativeText_(f"Heard: {self.engine.last_dictation_heard}")
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 320, 24))
        field.setStringValue_(self.engine.last_dictation_text or "")
        alert.setAccessoryView_(field)
        alert.window().setInitialFirstResponder_(field)
        alert.addButtonWithTitle_("Learn Correction")
        alert.addButtonWithTitle_("Cancel")
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if alert.runModal() == NSAlertFirstButtonReturn:
            if not self.engine.correct_last_dictation(field.stringValue()):
                self._error("Correction not saved", "The correction was empty or conflicted with the profile.")
        self.engineStateChanged_(self.engine.state.value)

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
        self.calendar_sync = None
        self.ax_warmer = None
        self.controller = None
        self.record_prompt = None
        return self

    def appDidActivate_(self, notification):
        warmer = getattr(self, "ax_warmer", None)
        if warmer is not None:
            from ..ax_warmup import activated_pid
            warmer.app_activated(activated_pid(notification))

    def systemDidWake_(self, notification):
        if self.engine is not None and not self.engine._shutting_down:
            self.engine.system_woke()
        self.calendarTick_(None)

    def calendarTick_(self, timer):
        sync = getattr(self, "calendar_sync", None)
        if sync is not None:
            sync.tick()

    def calendarSynced_(self, _):
        window = getattr(self.controller, "meetings_window", None)
        if window is not None:
            window.calendarChanged_(None)

    def applicationDidFinishLaunching_(self, notification):
        engine = DictationEngine()
        engine.set_profile(_initial_profile(self._preselected))
        self.engine = engine

        from ..calendar_sync import CalendarSync
        from . import services

        services.engine = engine
        self.calendar_sync = CalendarSync(
            on_synced=lambda: self.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"calendarSynced:", None, False))
        services.calendar_sync = self.calendar_sync
        # Never prompt at launch: only an explicit Connect Calendar click asks.
        self.calendar_sync.tick()
        # Backstop for changes EventKit doesn't announce (and missed wakes).
        self._calendar_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            300.0, self, b"calendarTick:", None, True)
        NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
            self, b"systemDidWake:", NSWorkspaceDidWakeNotification, None,
        )
        from ..ax_warmup import AccessibilityWarmer

        # Fresh Electron apps build their AX tree ~2 s after being asked;
        # ask on activation so the first dictation finds its field.
        self.ax_warmer = AccessibilityWarmer()
        NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
            self, b"appDidActivate:", NSWorkspaceDidActivateApplicationNotification, None,
        )
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is not None:
            self.ax_warmer.app_activated(front.processIdentifier())
        self.controller = StatusItemController.alloc().initWithEngine_(engine)
        from .pill_controller import PillController

        self.pill = PillController.alloc().initWithEngine_opener_(engine, self.controller.open_meetings)
        from .record_prompt_controller import RecordPromptController

        # Offers to record when a calendar meeting starts or another app
        # starts using the mic; never records without a click.
        try:
            self.record_prompt = RecordPromptController.alloc().initWithEngine_(engine)
        except Exception:
            logging.getLogger(__name__).exception("record prompt unavailable")
            self.record_prompt = None
        record_prompt = self.record_prompt
        NSApplication.sharedApplication().setMainMenu_(
            _build_main_menu(self.controller)
        )

        if config.OVERLAY_ENABLED:
            from .overlay import Overlay

            engine.overlay = Overlay()

        controller = self.controller
        pill = self.pill

        def on_state_changed(state):
            pill.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"engineStateChanged:", state.value, False
            )
            controller.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"engineStateChanged:", state.value, False
            )
            if record_prompt is not None:
                record_prompt.performSelectorOnMainThread_withObject_waitUntilDone_(
                    b"engineStateChanged:", state.value, False
                )

        def on_meeting_progress(text):
            pill.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"meetingProgress:", text, False
            )
            controller.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"meetingProgress:", text, False
            )

        def on_meeting_saved(meeting_id):
            pill.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"meetingSaved:", meeting_id, False
            )
            controller.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"meetingSaved:", meeting_id, False
            )

        def on_library_status(status):
            controller.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"libraryStatus:", json.dumps(status), False
            )

        engine.on_state_changed = on_state_changed
        engine.on_meeting_progress = on_meeting_progress
        engine.on_meeting_saved = on_meeting_saved
        engine.on_library_status = on_library_status

        # Start first: the model warms up on its worker thread while the
        # user reads the (modal) first-run permissions guidance.
        engine.start()
        engine.upgrade_library()
        controller.engineStateChanged_(engine.state.value)
        pill.engineStateChanged_(engine.state.value)
        if self.record_prompt is not None:
            self.record_prompt.engineStateChanged_(engine.state.value)
        # Cold launch from the Dock counts as "the user clicked the icon":
        # open Meetings right away rather than only on a later reopen click.
        self.controller.open_meetings(None)
        if not permissions.all_granted():
            permissions.show_guidance()
        hotkey_name = config.hotkey_name()
        profile = engine.profile
        profile_tag = f" [profile: {profile.name}]" if profile else ""
        print(f"Speakeasy in the menu bar{profile_tag}. Hold [{hotkey_name}] to dictate.")

    def applicationWillTerminate_(self, notification):
        if getattr(self, "pill", None) is not None:
            self.pill.shutdown()
        if getattr(self, "record_prompt", None) is not None:
            self.record_prompt.shutdown()
        if getattr(self, "calendar_sync", None) is not None:
            self.calendar_sync.shutdown()
        if getattr(self, "ax_warmer", None) is not None:
            self.ax_warmer.shutdown()
        if self.engine is not None:
            self.engine.shutdown()

    def applicationShouldTerminateAfterLastWindowClosed_(self, sender):
        # Background dictation service: closing the main window must not
        # quit the app, any more than closing Meetings/Training does.
        return False

    def applicationShouldHandleReopen_hasVisibleWindows_(self, sender, has_visible_windows):
        # Dock-icon click with no window open: a way back in when the status
        # item is hidden/overflowed.
        if not has_visible_windows:
            self.controller.open_meetings(None)
        return True


def _build_main_menu(quit_target) -> NSMenu:
    """A minimal system menu bar: the app menu (Quit, so Cmd+Q works with the
    main window focused) and an Edit menu. Regular-policy apps don't get
    either for free without Interface Builder; StatusItemController already
    implements quitApp_.

    The Edit items deliberately have no target: AppKit then sends the action
    down the responder chain to the first responder, which is what makes
    Cmd+A/C/V/X/Z work in WKWebView text fields."""
    menu = NSMenu.alloc().init()
    app_menu_item = NSMenuItem.alloc().init()
    menu.addItem_(app_menu_item)
    app_menu = NSMenu.alloc().init()
    quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Quit Speakeasy", b"quitApp:", "q"
    )
    quit_item.setTarget_(quit_target)
    app_menu.addItem_(quit_item)
    app_menu_item.setSubmenu_(app_menu)

    edit_menu_item = NSMenuItem.alloc().init()
    menu.addItem_(edit_menu_item)
    edit_menu = NSMenu.alloc().initWithTitle_("Edit")
    # (title, action, key equivalent, extra modifiers); None is a separator.
    # A capital key equivalent implies Shift (Cmd+Shift+Z for Redo).
    for spec in (
        ("Undo", b"undo:", "z", None),
        ("Redo", b"redo:", "Z", None),
        None,
        ("Cut", b"cut:", "x", None),
        ("Copy", b"copy:", "c", None),
        ("Paste", b"paste:", "v", None),
        (
            "Paste and Match Style",
            b"pasteAsPlainText:",
            "V",
            NSEventModifierFlagCommand
            | NSEventModifierFlagOption
            | NSEventModifierFlagShift,
        ),
        ("Delete", b"delete:", "", None),
        ("Select All", b"selectAll:", "a", None),
    ):
        if spec is None:
            edit_menu.addItem_(NSMenuItem.separatorItem())
            continue
        title, action, key, modifiers = spec
        item = edit_menu.addItemWithTitle_action_keyEquivalent_(title, action, key)
        if modifiers is not None:
            item.setKeyEquivalentModifierMask_(modifiers)
    edit_menu_item.setSubmenu_(edit_menu)
    return menu


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
    # Regular (not Accessory) policy: gives Speakeasy a normal Dock icon and
    # app-switcher entry, so the Meetings window is reachable even if the
    # menu bar status item is hidden by overflow. The status item is still the
    # primary interface.
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
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
