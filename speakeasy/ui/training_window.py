"""Native training window: read-aloud sessions on a glass surface.

Same training logic as the terminal flow — _TakeRecorder owns the hotkey for
the whole visit (the dictation engine is paused meanwhile), sessions run on a
background thread, and every progress callback is marshaled onto the AppKit
main thread through a TrainingUI adapter.
"""

import threading
from collections import deque

import objc
from AppKit import (
    NSApplication,
    NSButton,
    NSFontWeightSemibold,
    NSTextAlignmentLeft,
    NSTextField,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialSidebar,
    NSVisualEffectStateActive,
    NSVisualEffectView,
)
from Foundation import NSMakeRect, NSObject

from .. import config
from ..engine import DictationEngine, play_sound
from ..training import (
    TrainingUI,
    _run_session,
    _suggested_index,
    _TakeRecorder,
    _train_prompt,
)
from ..training_content import SESSIONS
from . import glass

_W, _H = 600, 400
_SIDEBAR_W = 200
_PANE_X = _SIDEBAR_W + 20
_PANE_W = _W - _PANE_X - 20


class _Cancelled(Exception):
    """Raised on the session thread when the window closes mid-session."""


class _WindowUI(TrainingUI):
    """TrainingUI that forwards each callback to the window's main thread."""

    def __init__(self, controller) -> None:
        self._c = controller

    def _feedback(self, line: str) -> None:
        self._c.performSelectorOnMainThread_withObject_waitUntilDone_(
            b"uiFeedback:", line, False
        )

    def show_prompt(self, text: str, hotkey_name: str) -> None:
        self._c.performSelectorOnMainThread_withObject_waitUntilDone_(
            b"uiShowPrompt:", text, False
        )

    def nothing_heard(self) -> None:
        self._feedback("Nothing heard — try again.")

    def heard_correctly(self) -> None:
        self._feedback("✓ Heard it correctly")

    def correction_saved(self, heard_span: str, target: str) -> None:
        self._feedback(f'✗ Heard “{heard_span}” — saved correction → “{target}”')

    def correction_conflict(self, heard_span: str, target: str) -> None:
        self._feedback(f'✗ Heard “{heard_span}” — not saved (would conflict)')

    def session_started(self, name: str) -> None:
        pass  # the sidebar selection already shows it

    def session_finished(self, name: str, saved: int) -> None:
        self._c.performSelectorOnMainThread_withObject_waitUntilDone_(
            b"uiSessionDone:",
            f"Session complete — {saved} correction(s) learned.",
            False,
        )


class TrainingWindowController(NSObject):
    """Owns the training window. Main-thread only, except where noted."""

    def initWithEngine_(self, engine: DictationEngine):
        self = objc.super(TrainingWindowController, self).init()
        if self is None:
            return None
        self.engine = engine
        self.profile = None
        self._taker = None
        self._thread = None
        self._cancelled = False
        self._feedback_lines = deque(maxlen=4)
        self._build_window()
        return self

    # -- layout -----------------------------------------------------------

    @objc.python_method
    def _build_window(self):
        window, content = glass.make_glass_window("Training", _W, _H)
        window.setDelegate_(self)
        self._window = window

        sidebar = NSVisualEffectView.alloc().initWithFrame_(
            NSMakeRect(0, 0, _SIDEBAR_W, _H)
        )
        sidebar.setMaterial_(NSVisualEffectMaterialSidebar)
        sidebar.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        sidebar.setState_(NSVisualEffectStateActive)
        content.addSubview_(sidebar)

        header = glass.make_label(
            "SESSIONS", size=11, weight=NSFontWeightSemibold, secondary=True
        )
        header.setFrame_(NSMakeRect(16, _H - 58, _SIDEBAR_W - 32, 16))
        sidebar.addSubview_(header)

        self._session_buttons = []
        for i, session in enumerate(SESSIONS):
            button = NSButton.buttonWithTitle_target_action_(
                session["name"], self, b"startSession:"
            )
            button.setTag_(i)
            button.setBordered_(False)
            button.setAlignment_(NSTextAlignmentLeft)
            button.setFrame_(NSMakeRect(12, _H - 92 - i * 30, _SIDEBAR_W - 24, 24))
            sidebar.addSubview_(button)
            self._session_buttons.append(button)

        self._status = glass.make_label("", size=12, secondary=True)
        self._status.setFrame_(NSMakeRect(_PANE_X, _H - 58, _PANE_W, 18))
        content.addSubview_(self._status)

        self._prompt = NSTextField.wrappingLabelWithString_("")
        self._prompt.setFont_(
            glass.make_label("", size=17, weight=NSFontWeightSemibold).font()
        )
        self._prompt.setFrame_(NSMakeRect(_PANE_X, _H - 175, _PANE_W, 100))
        self._prompt.setEditable_(False)
        content.addSubview_(self._prompt)

        self._feedback = NSTextField.wrappingLabelWithString_("")
        self._feedback.setFont_(glass.make_label("", size=12).font())
        self._feedback.setTextColor_(self._status.textColor())
        self._feedback.setFrame_(NSMakeRect(_PANE_X, 64, _PANE_W, 92))
        self._feedback.setEditable_(False)
        content.addSubview_(self._feedback)

        self._custom_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(_PANE_X, 20, _PANE_W - 122, 24)
        )
        self._custom_field.setPlaceholderString_("Your own word or phrase…")
        content.addSubview_(self._custom_field)

        self._custom_button = NSButton.buttonWithTitle_target_action_(
            "Practice", self, b"addCustom:"
        )
        self._custom_button.setFrame_(NSMakeRect(_W - 130, 17, 110, 30))
        content.addSubview_(self._custom_button)

    # -- open / close -------------------------------------------------------

    def showForProfile_(self, profile):
        if profile is None or self.engine.transcriber is None:
            return
        self.profile = profile
        self._window.setTitle_(f"Training — {profile.name}")
        if self._taker is None:
            # The window owns the hotkey while open; dictation is suspended.
            self.engine.pause()
            self._taker = _TakeRecorder(
                self.engine.recorder,
                self.engine.transcriber,
                self.engine.worker,
                self.engine.control,
                play_sound,
            )
            self._taker.__enter__()
        self._cancelled = False
        self._feedback_lines.clear()
        self._feedback.setStringValue_("")
        self._prompt.setStringValue_("Pick a session on the left, or practice your own words below.")
        self._status.setStringValue_("Short read-aloud sessions teach Speakeasy your words.")
        self._refresh_sessions()
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._window.makeKeyAndOrderFront_(None)

    def windowWillClose_(self, notification):
        self._cancelled = True
        if self._taker is not None:
            self._taker.unblock()  # free a session thread waiting on a take
            self._taker.__exit__(None, None, None)
            self._taker = None
        self.engine.resume()

    # -- actions (main thread) -----------------------------------------------

    def startSession_(self, sender):
        session = SESSIONS[sender.tag()]
        self._start_run(
            lambda ui, record: _run_session(
                session, self.profile, record, config.hotkey_name(), ui
            )
        )

    def addCustom_(self, sender):
        term = str(self._custom_field.stringValue()).strip()
        if not term or self.profile is None:
            return
        self.profile.add_word(term)
        self._custom_field.setStringValue_("")
        self._start_run(
            lambda ui, record: _train_prompt(
                term, [term], self.profile, record, config.hotkey_name(), ui
            )
        )

    @objc.python_method
    def _start_run(self, work):
        if self._thread is not None and self._thread.is_alive():
            return
        self._set_controls_enabled(False)
        self._feedback_lines.clear()
        self._feedback.setStringValue_("")
        ui = _WindowUI(self)
        taker = self._taker

        def record() -> str:
            heard = taker.record()
            if self._cancelled:
                raise _Cancelled()
            return heard

        def run():
            try:
                work(ui, record)
            except _Cancelled:
                pass
            finally:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    b"uiRunEnded:", None, False
                )

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    # -- UI updates marshaled from the session thread -------------------------

    def uiShowPrompt_(self, text):
        hotkey = config.hotkey_name()
        self._status.setStringValue_(
            f"Hold [{hotkey}], read the line aloud, release when done."
        )
        self._prompt.setStringValue_(f"“{text}”")

    def uiFeedback_(self, line):
        self._feedback_lines.append(str(line))
        self._feedback.setStringValue_("\n".join(self._feedback_lines))

    def uiSessionDone_(self, message):
        self._status.setStringValue_(str(message))
        self._prompt.setStringValue_("Pick another session, or close the window to dictate.")

    def uiRunEnded_(self, _):
        self._set_controls_enabled(True)
        self._refresh_sessions()

    # -- helpers ---------------------------------------------------------------

    @objc.python_method
    def _refresh_sessions(self):
        done = set(self.profile.sessions_done) if self.profile else set()
        suggested = _suggested_index(self.profile) if self.profile else None
        for i, button in enumerate(self._session_buttons):
            name = SESSIONS[i]["name"]
            if name in done:
                button.setTitle_(f"✓ {name}")
            elif i == suggested:
                button.setTitle_(f"◦ {name}  ★")
            else:
                button.setTitle_(f"◦ {name}")

    @objc.python_method
    def _set_controls_enabled(self, enabled: bool):
        for button in self._session_buttons:
            button.setEnabled_(enabled)
        self._custom_field.setEnabled_(enabled)
        self._custom_button.setEnabled_(enabled)
