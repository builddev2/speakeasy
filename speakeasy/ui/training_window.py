"""Native training window: read-aloud sessions on a glass surface.

Same training logic as the terminal flow — _TakeRecorder owns the hotkey for
the whole visit (the dictation engine is paused meanwhile), sessions run on a
background thread, and every progress callback is marshaled onto the AppKit
main thread through a TrainingUI adapter.

Visual language matches main_window.py / meetings_window.py: a fixed dark
glass surface (the commissioned "Speakeasy Dock Window Redesign"), not
adaptive to system light/dark.
"""

import threading
from collections import deque

import objc
from AppKit import (
    NSAppearance,
    NSAppearanceNameDarkAqua,
    NSApplication,
    NSAttributedString,
    NSButton,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSFontWeightBold,
    NSFontWeightMedium,
    NSFontWeightRegular,
    NSFocusRingTypeNone,
    NSFontWeightSemibold,
    NSForegroundColorAttributeName,
    NSTextAlignmentLeft,
    NSTextField,
    NSView,
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

_W, _H = 640, 440
_SIDEBAR_W = 222
_PANE_X = _SIDEBAR_W + 20
_PANE_W = _W - _PANE_X - 20
_ROW_H = 32


def _rgba(r, g, b, a):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r / 255, g / 255, b / 255, a)


_CORAL = _rgba(232, 149, 90, 1.0)
_CORAL_TOP = _rgba(235, 152, 93, 0.95)
_CORAL_BOTTOM = _rgba(214, 120, 68, 0.90)
_BUTTON_BORDER = _rgba(255, 255, 255, 0.35)
_AMBER = _rgba(240, 179, 74, 1.0)

_TEXT_PRIMARY = _rgba(255, 255, 255, 0.95)
_TEXT_META = _rgba(255, 255, 255, 0.50)
_TEXT_SESSION = _rgba(255, 255, 255, 0.90)
_TEXT_SESSION_DIM = _rgba(255, 255, 255, 0.78)
_TEXT_HEADER = _rgba(255, 255, 255, 0.40)
_ROW_ACTIVE_BG = _rgba(255, 255, 255, 0.06)
_ROW_ACTIVE_BORDER = _rgba(255, 255, 255, 0.10)
_DOT_COLOR = _rgba(255, 255, 255, 0.45)

_FIELD_BG = _rgba(0, 0, 0, 0.28)


def _titled(button, text, size, weight, color) -> None:
    font = NSFont.systemFontOfSize_weight_(size, weight)
    button.setAttributedTitle_(
        NSAttributedString.alloc().initWithString_attributes_(
            text, {NSFontAttributeName: font, NSForegroundColorAttributeName: color}
        )
    )


def _rounded(view, radius, bg=None, border=None, border_width=0.5) -> None:
    view.setWantsLayer_(True)
    layer = view.layer()
    layer.setCornerRadius_(radius)
    if bg is not None:
        layer.setBackgroundColor_(bg.CGColor())
    if border is not None:
        layer.setBorderWidth_(border_width)
        layer.setBorderColor_(border.CGColor())


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
        window.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
        self._window = window

        sidebar = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, _SIDEBAR_W, _H))
        _rounded(sidebar, 0.0, _rgba(255, 255, 255, 0.02))
        content.addSubview_(sidebar)
        divider = NSView.alloc().initWithFrame_(NSMakeRect(_SIDEBAR_W - 1, 0, 1, _H))
        divider.setWantsLayer_(True)
        divider.layer().setBackgroundColor_(_rgba(255, 255, 255, 0.07).CGColor())
        content.addSubview_(divider)

        header = glass.make_label("SESSIONS", size=11, weight=NSFontWeightSemibold)
        header.setTextColor_(_TEXT_HEADER)
        header.setFrame_(NSMakeRect(16, _H - 30, _SIDEBAR_W - 32, 16))
        sidebar.addSubview_(header)

        self._session_rows = []
        self._session_buttons = []
        for i, session in enumerate(SESSIONS):
            row = NSView.alloc().initWithFrame_(
                NSMakeRect(8, _H - 48 - i * _ROW_H, _SIDEBAR_W - 16, _ROW_H - 4)
            )
            sidebar.addSubview_(row)
            self._session_rows.append(row)

            button = glass.ClickyButton.buttonWithTitle_target_action_(
                session["name"], self, b"startSession:"
            )
            button.setTag_(i)
            button.setBordered_(False)
            button.setAlignment_(NSTextAlignmentLeft)
            button.setFrame_(NSMakeRect(0, 0, row.frame().size.width, row.frame().size.height))
            row.addSubview_(button)
            self._session_buttons.append(button)

        self._status = glass.make_label("", size=13, weight=NSFontWeightRegular)
        self._status.setTextColor_(_TEXT_META)
        self._status.setFrame_(NSMakeRect(_PANE_X, _H - 44, _PANE_W, 18))
        content.addSubview_(self._status)

        self._prompt = NSTextField.wrappingLabelWithString_("")
        self._prompt.setFont_(NSFont.systemFontOfSize_weight_(22, NSFontWeightSemibold))
        self._prompt.setTextColor_(_TEXT_PRIMARY)
        self._prompt.setFrame_(NSMakeRect(_PANE_X, _H - 175, _PANE_W - 20, 110))
        self._prompt.setEditable_(False)
        content.addSubview_(self._prompt)

        self._feedback = NSTextField.wrappingLabelWithString_("")
        self._feedback.setFont_(NSFont.systemFontOfSize_weight_(12, NSFontWeightRegular))
        self._feedback.setTextColor_(_TEXT_META)
        self._feedback.setFrame_(NSMakeRect(_PANE_X, 68, _PANE_W, 88))
        self._feedback.setEditable_(False)
        content.addSubview_(self._feedback)

        self._custom_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(_PANE_X, 20, _PANE_W - 122, 42)
        )
        self._custom_field.setPlaceholderString_("Your own word or phrase…")
        self._custom_field.setBordered_(False)
        self._custom_field.setFocusRingType_(NSFocusRingTypeNone)
        self._custom_field.setDrawsBackground_(False)
        self._custom_field.setTextColor_(_TEXT_PRIMARY)
        field_bg = NSView.alloc().initWithFrame_(NSMakeRect(_PANE_X, 20, _PANE_W - 122, 42))
        _rounded(field_bg, 11.0, _FIELD_BG, _CORAL, border_width=1.5)
        content.addSubview_(field_bg)
        self._custom_field.setFrame_(NSMakeRect(_PANE_X + 14, 20 + 11, _PANE_W - 122 - 28, 20))
        content.addSubview_(self._custom_field)

        self._practice_container = glass.GradientView.alloc().init()
        self._practice_container.setWantsLayer_(True)
        _rounded(self._practice_container, 11.0, None, _BUTTON_BORDER)
        self._practice_container.setColors_([_CORAL_TOP, _CORAL_BOTTOM])
        self._practice_container.setFrame_(NSMakeRect(_W - 130, 20, 110, 42))
        content.addSubview_(self._practice_container)
        self._custom_button = glass.ClickyButton.buttonWithTitle_target_action_(
            "", self, b"addCustom:"
        )
        self._custom_button.setBordered_(False)
        _titled(self._custom_button, "Practice", 14.5, NSFontWeightSemibold, NSColor.whiteColor())
        self._custom_button.setFrame_(NSMakeRect(0, 0, 110, 42))
        self._practice_container.addSubview_(self._custom_button)

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
        for i, (row, button) in enumerate(zip(self._session_rows, self._session_buttons)):
            name = SESSIONS[i]["name"]
            for view in list(row.subviews()):
                if view is not button:
                    view.removeFromSuperview()
            is_suggested = i == suggested
            if is_suggested:
                _rounded(row, 8.0, _ROW_ACTIVE_BG, _ROW_ACTIVE_BORDER)
            else:
                row.setWantsLayer_(True)
                row.layer().setCornerRadius_(8.0)
                row.layer().setBorderWidth_(0)

            marker_x = 11
            if name in done:
                marker = glass.make_label("✓", 12, NSFontWeightBold)
                marker.setTextColor_(_CORAL)
                marker.setFrame_(NSMakeRect(0, 8, 14, 14))
                row.addSubview_(marker)
                text_color = _TEXT_SESSION
            else:
                dot = NSView.alloc().initWithFrame_(NSMakeRect(marker_x, 12, 5, 5))
                dot.setWantsLayer_(True)
                dot.layer().setCornerRadius_(2.5)
                dot.layer().setBackgroundColor_(_DOT_COLOR.CGColor())
                row.addSubview_(dot)
                text_color = _TEXT_SESSION_DIM

            label_x = 24 if name in done else marker_x + 5 + 6
            _titled(button, name, 13.5, NSFontWeightRegular, text_color)
            button.setFrame_(NSMakeRect(label_x, 0, row.frame().size.width - label_x - 20, row.frame().size.height))

            if is_suggested:
                star = glass.make_label("★", 12, NSFontWeightRegular)
                star.setTextColor_(_AMBER)
                star.setFrame_(NSMakeRect(row.frame().size.width - 16, 8, 14, 14))
                row.addSubview_(star)

    @objc.python_method
    def _set_controls_enabled(self, enabled: bool):
        for button in self._session_buttons:
            button.setEnabled_(enabled)
        self._custom_field.setEnabled_(enabled)
        self._custom_button.setEnabled_(enabled)
