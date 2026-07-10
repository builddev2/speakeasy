"""Main window: Dock-accessible fallback for the menu-bar status item.

Speakeasy is menu-bar-first, but the status item can end up invisible (menu
bar overflow with many other apps installed, or a user who simply doesn't
notice a small icon) with no other way back in. This window is reachable via
the Dock icon and shows the same core actions as the status menu — it is
optional UI sugar over the same DictationEngine, not a second source of
truth. Closing it doesn't quit the app (see AppDelegate's
applicationShouldTerminateAfterLastWindowClosed_); the Dock icon reopens it
via applicationShouldHandleReopen_hasVisibleWindows_.

Visual language: a deliberately dark, moody "glass" panel — fixed dark
appearance rather than adapting to the system light/dark setting, matching
the commissioned design (Claude Design project "Speakeasy Dock Window
Redesign"). Idle states are quiet (identity + status dot + one primary
action); the one hero moment is a rainbow "glass" waveform that blooms in
only while a meeting is actively recording, echoing the mic level in real
time — restraint everywhere else, boldness in one place.
"""

import math

import objc
from AppKit import (
    NSAppearance,
    NSAppearanceNameDarkAqua,
    NSApplication,
    NSAttributedString,
    NSBezierPath,
    NSButton,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSFontWeightBold,
    NSFontWeightMedium,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSForegroundColorAttributeName,
    NSGraphicsContext,
    NSImage,
    NSImageLeading,
    NSShadow,
    NSTextField,
    NSView,
)
from Foundation import NSMakeRect, NSObject, NSTimer

from ..engine import DictationEngine, State
from . import glass

_W, _H = 360, 320
_PAD = 18

# -- palette, lifted straight from the commissioned design's rgba() values --


def _rgba(r, g, b, a):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r / 255, g / 255, b / 255, a)


_GLASS_TINT_TOP = _rgba(42, 46, 48, 0.60)
_GLASS_TINT_BOTTOM = _rgba(22, 25, 26, 0.62)
_GLASS_BORDER = _rgba(255, 255, 255, 0.16)

_ICON_BG = _rgba(55, 57, 62, 0.85)

_CORAL_TOP = _rgba(235, 152, 93, 0.95)
_CORAL_BOTTOM = _rgba(214, 120, 68, 0.90)
_RED_TOP = _rgba(255, 90, 78, 0.95)
_RED_BOTTOM = _rgba(226, 59, 50, 0.90)
_BUTTON_BORDER = _rgba(255, 255, 255, 0.35)

_CARD_BG = _rgba(255, 255, 255, 0.08)
_CARD_BORDER = _rgba(255, 255, 255, 0.14)

_GREEN_DOT = _rgba(61, 220, 132, 1.0)
_RED_DOT = _rgba(255, 69, 58, 1.0)
_AMBER_DOT = _rgba(240, 179, 74, 1.0)
_AMBER_TEXT = _rgba(240, 179, 74, 1.0)
_AMBER_BADGE_BG = _rgba(240, 179, 74, 0.13)
_AMBER_BADGE_BORDER = _rgba(240, 179, 74, 0.25)

_TEXT_PRIMARY = _rgba(255, 255, 255, 0.97)
_TEXT_STATUS = _rgba(255, 255, 255, 0.55)
_TEXT_STATUS_LIVE = _rgba(255, 255, 255, 0.90)
_TEXT_QUIT = _rgba(255, 255, 255, 0.32)
_TEXT_CARD = _rgba(255, 255, 255, 0.90)

_WAVE_N = 26
_WAVE_FPS = 24.0


def _titled(field_or_button, text, size, weight, color) -> None:
    font = NSFont.systemFontOfSize_weight_(size, weight)
    field_or_button.setAttributedTitle_(
        NSAttributedString.alloc().initWithString_attributes_(
            text, {NSFontAttributeName: font, NSForegroundColorAttributeName: color}
        )
    )


def _label(text, size, weight, color) -> NSTextField:
    field = NSTextField.labelWithString_(text)
    field.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
    field.setTextColor_(color)
    return field


def _symbol(name: str) -> NSImage:
    return NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)


def _rounded(view: NSView, radius: float, border: NSColor | None = None) -> None:
    view.setWantsLayer_(True)
    layer = view.layer()
    layer.setCornerRadius_(radius)
    if border is not None:
        layer.setBorderWidth_(0.5)
        layer.setBorderColor_(border.CGColor())


def _make_pill_button(text, target, action, radius=12.0) -> tuple[NSView, NSButton, "glass.GradientView"]:
    container = NSView.alloc().init()
    grad = glass.GradientView.alloc().init()
    grad.setWantsLayer_(True)
    _rounded(grad, radius, _BUTTON_BORDER)
    button = glass.ClickyButton.buttonWithTitle_target_action_(text, target, action)
    button.setBordered_(False)
    container.addSubview_(grad)
    container.addSubview_(button)
    return container, button, grad


class _WaveformView(NSView):
    """The recording-state hero: 26 glowing rainbow bars over a soft rainbow
    bloom, matching the commissioned design's waveform. Height driven by the
    live meeting mic level; per-bar phase/speed staggered so it reads as
    organic rather than a single pulsing block."""

    def initWithFrame_(self, frame):
        self = objc.super(_WaveformView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.fracs = [0.24] * _WAVE_N
        self.phase = 0.0
        self.level = 0.0
        return self

    def drawRect_(self, rect):
        bounds = self.bounds()
        w, h = bounds.size.width, bounds.size.height
        bg = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0, 0, w, h), 12.0, 12.0
        )
        _rgba(0, 0, 0, 0.22).setFill()
        bg.fill()

        n = len(self.fracs)
        gap = 3.5
        bar_w = 4.0
        total_w = n * bar_w + (n - 1) * gap
        start_x = (w - total_w) / 2
        for i, frac in enumerate(self.fracs):
            hue = (i * 300.0 / max(n - 1, 1)) / 360.0
            bar_h = max(4.0, h * 0.86 * frac)
            x = start_x + i * (bar_w + gap)
            y = (h - bar_h) / 2
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x, y, bar_w, bar_h), bar_w / 2, bar_w / 2
            )
            glow = NSColor.colorWithCalibratedHue_saturation_brightness_alpha_(
                hue, 0.9, 0.95, 0.55
            )
            NSGraphicsContext.saveGraphicsState()
            shadow = NSShadow.alloc().init()
            shadow.setShadowOffset_((0, 0))
            shadow.setShadowBlurRadius_(4.0)
            shadow.setShadowColor_(glow)
            shadow.set()
            NSColor.colorWithCalibratedHue_saturation_brightness_alpha_(
                hue, 0.85, 0.85, 0.95
            ).setFill()
            path.fill()
            NSGraphicsContext.restoreGraphicsState()

    def tick(self):
        self.phase += 1.0 / _WAVE_FPS
        t = self.phase
        drive = min(1.0, max(0.0, (self.level / 0.15) ** 0.5))
        for i in range(len(self.fracs)):
            speed = 3.0 + (i % 4) * 0.9
            offset = (i % 6) * 0.5 + i * 0.09
            wobble = 0.24 + 0.76 * (0.5 + 0.5 * math.sin(t * speed + offset))
            target = max(0.16, drive) * wobble if drive > 0.02 else 0.10 * wobble
            self.fracs[i] += (target - self.fracs[i]) * 0.35
        self.setNeedsDisplay_(True)


class MainWindowController(NSObject):
    """Owns the Dock-reachable main window. Main-thread only, like the
    status item controller; the engine callback hops threads before
    touching it."""

    def initWithEngine_(self, engine: DictationEngine):
        self = objc.super(MainWindowController, self).init()
        if self is None:
            return None
        self.engine = engine
        self.meetings_window = None  # set lazily by openMeetings:
        self.training_window = None  # set lazily by openTraining:
        self._build_window()
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / _WAVE_FPS, self, b"tick:", None, True
        )
        return self

    @objc.python_method
    def _build_window(self):
        window, content = glass.make_glass_window("Speakeasy", _W, _H)
        window.setDelegate_(self)
        # This design is a deliberately dark, moody surface — not adaptive
        # to system light/dark, so the hand-tuned rgba() overlay colors
        # below always land against dark vibrancy.
        window.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
        self._window = window

        tint = glass.GradientView.alloc().initWithFrame_(NSMakeRect(0, 0, _W, _H))
        tint.setWantsLayer_(True)
        tint.setColors_([_GLASS_TINT_TOP, _GLASS_TINT_BOTTOM])
        _rounded(tint, 16.0, _GLASS_BORDER)
        content.addSubview_(tint)

        # -- identity row (fixed position in every state) --------------
        icon_bg = NSView.alloc().initWithFrame_(NSMakeRect(_PAD, _H - 16 - 34, 34, 34))
        icon_bg.setWantsLayer_(True)
        icon_bg.layer().setCornerRadius_(9.0)
        icon_bg.layer().setBackgroundColor_(_ICON_BG.CGColor())
        content.addSubview_(icon_bg)
        skull = _label("💀", 18, NSFontWeightRegular, NSColor.whiteColor())
        skull.setAlignment_(2)  # NSTextAlignmentCenter
        skull.setFrame_(NSMakeRect(_PAD, _H - 16 - 34, 34, 34))
        content.addSubview_(skull)

        title = _label("Speakeasy", 19, NSFontWeightSemibold, _TEXT_PRIMARY)
        title.setFrame_(NSMakeRect(_PAD + 45, _H - 20 - 16, _W - _PAD - 45 - _PAD, 20))
        content.addSubview_(title)

        self._dot = NSView.alloc().initWithFrame_(NSMakeRect(_PAD + 45, _H - 43 - 16, 7, 7))
        self._dot.setWantsLayer_(True)
        self._dot.layer().setCornerRadius_(3.5)
        content.addSubview_(self._dot)

        self._status = _label("Loading model…", 13, NSFontWeightRegular, _TEXT_STATUS)
        self._status.setFrame_(
            NSMakeRect(_PAD + 45 + 13, _H - 44 - 16, _W - _PAD - 45 - 13 - _PAD, 16)
        )
        content.addSubview_(self._status)

        # -- waveform hero (recording only) ----------------------------
        self._waveform = _WaveformView.alloc().initWithFrame_(
            NSMakeRect(_PAD, _H - 64 - 52, _W - 2 * _PAD, 52)
        )
        self._waveform.setHidden_(True)
        content.addSubview_(self._waveform)

        self._badge = NSView.alloc().initWithFrame_(NSMakeRect(_PAD, _H - 125 - 22, 130, 22))
        self._badge.setWantsLayer_(True)
        self._badge.layer().setCornerRadius_(6.0)
        self._badge.layer().setBackgroundColor_(_AMBER_BADGE_BG.CGColor())
        self._badge.layer().setBorderWidth_(0.5)
        self._badge.layer().setBorderColor_(_AMBER_BADGE_BORDER.CGColor())
        self._badge.setHidden_(True)
        content.addSubview_(self._badge)
        badge_label = _label("Dictation paused", 11.5, NSFontWeightMedium, _AMBER_TEXT)
        badge_label.setFrame_(NSMakeRect(8, 3, 114, 16))  # local to _badge's own bounds
        self._badge.addSubview_(badge_label)

        # -- primary action (position shifts: lower when the waveform is
        # visible, vertically centered in the idle empty space otherwise) --
        self._meeting_container, self._meeting_button, self._meeting_grad = (
            _make_pill_button("Begin Meeting", self, b"toggleMeeting:")
        )
        self._meeting_container.setFrame_(NSMakeRect(_PAD, 0, _W - 2 * _PAD, 46))
        self._meeting_button.setFrame_(NSMakeRect(0, 0, _W - 2 * _PAD, 46))
        self._meeting_grad.setFrame_(NSMakeRect(0, 0, _W - 2 * _PAD, 46))
        self._meeting_button.setToolTip_(
            "Records from the microphone — on video calls, use speakers so "
            "other participants are captured. Only the transcript is saved; "
            "audio is deleted after processing."
        )
        content.addSubview_(self._meeting_container)

        # -- secondary cards --------------------------------------------
        half = (_W - 2 * _PAD - 10) / 2
        self._meetings_btn = self._make_card_button(
            "Meetings", "list.bullet.rectangle.portrait", b"openMeetings:"
        )
        self._meetings_btn.setFrame_(NSMakeRect(_PAD, 0, half, 46))
        content.addSubview_(self._meetings_btn)

        self._train_button = self._make_card_button(
            "Train Profile", "person.fill", b"openTraining:"
        )
        self._train_button.setFrame_(NSMakeRect(_PAD + half + 10, 0, half, 46))
        content.addSubview_(self._train_button)

        # -- quit (fixed position) ---------------------------------------
        quit_btn = glass.ClickyButton.buttonWithTitle_target_action_("", self, b"quitApp:")
        quit_btn.setFrame_(NSMakeRect(_W - _PAD - 132, 16, 132, 16))
        quit_btn.setBordered_(False)
        quit_btn.setAlignment_(2)
        _titled(quit_btn, "Quit Speakeasy", 12, NSFontWeightRegular, _TEXT_QUIT)
        content.addSubview_(quit_btn)

        self._layout_idle()

    @objc.python_method
    def _make_card_button(self, text, symbol_name, action) -> NSButton:
        button = glass.ClickyButton.buttonWithTitle_target_action_(text, self, action)
        button.setImage_(_symbol(symbol_name))
        button.setImagePosition_(NSImageLeading)
        button.setBordered_(False)
        _rounded(button, 11.0, _CARD_BORDER)
        button.layer().setBackgroundColor_(_CARD_BG.CGColor())
        _titled(button, text, 13, NSFontWeightSemibold, _TEXT_CARD)
        return button

    # -- layout: idle (no waveform) vs. recording (waveform + badge) -------

    @objc.python_method
    def _layout_idle(self):
        top = _H - 16 - 34  # bottom of identity row
        bottom = 16 + 16 + 12  # top of the quit row's reserved band
        block_h = 46 + 10 + 46
        y0 = bottom + ((top - bottom) - block_h) / 2
        self._meeting_container.setFrame_(
            NSMakeRect(_PAD, y0 + 46 + 10, _W - 2 * _PAD, 46)
        )
        self._meetings_btn.setFrame_(NSMakeRect(_PAD, y0, self._meetings_btn.frame().size.width, 46))
        half = self._meetings_btn.frame().size.width
        self._train_button.setFrame_(NSMakeRect(_PAD + half + 10, y0, half, 46))

    @objc.python_method
    def _layout_recording(self):
        self._meeting_container.setFrame_(NSMakeRect(_PAD, 51, _W - 2 * _PAD, 46))
        half = self._meetings_btn.frame().size.width
        self._meetings_btn.setFrame_(NSMakeRect(_PAD, 107, half, 46))
        self._train_button.setFrame_(NSMakeRect(_PAD + half + 10, 107, half, 46))

    # -- open / close -------------------------------------------------------

    def show(self):
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._window.makeKeyAndOrderFront_(None)

    def windowWillClose_(self, notification):
        pass  # just hides; the engine and menu-bar item keep running

    def tick_(self, timer):
        if not self._window.isVisible() or self._waveform.isHidden():
            return
        self._waveform.level = self.engine.meeting_recorder.level
        self._waveform.tick()

    # -- engine state (arrives via performSelectorOnMainThread) -------------

    def engineStateChanged_(self, state_name):
        from .menubar import _STATE_TEXT  # shared with the status item

        state = State(str(state_name))
        is_recording = state is State.MEETING_RECORDING
        is_busy = state in (State.TRANSCRIBING, State.MEETING_PROCESSING, State.PAUSED)

        if is_recording:
            elapsed = int(self.engine.meeting_recorder.elapsed_seconds)
            m, s = divmod(elapsed, 60)
            self._status.setStringValue_(f"Recording meeting · {m:02d}:{s:02d}")
            self._status.setTextColor_(_TEXT_STATUS_LIVE)
            self._dot.layer().setBackgroundColor_(_RED_DOT.CGColor())
            self._waveform.setHidden_(False)
            self._badge.setHidden_(False)
            self._meeting_grad.setColors_([_RED_TOP, _RED_BOTTOM])
            _titled(self._meeting_button, "End Meeting", 15, NSFontWeightSemibold, NSColor.whiteColor())
            self._layout_recording()
        else:
            text = _STATE_TEXT[state]
            if state is State.READY:
                profile = self.engine.profile
                text = f"Ready — {profile.name if profile else 'Guest'}"
            self._status.setStringValue_(text)
            self._status.setTextColor_(_TEXT_STATUS)
            dot_color = _AMBER_DOT if is_busy or state is State.LOADING else _GREEN_DOT
            self._dot.layer().setBackgroundColor_(dot_color.CGColor())
            self._waveform.setHidden_(True)
            self._badge.setHidden_(True)
            self._meeting_grad.setColors_([_CORAL_TOP, _CORAL_BOTTOM])
            _titled(self._meeting_button, "Begin Meeting", 15, NSFontWeightSemibold, NSColor.whiteColor())
            self._layout_idle()

        # Alpha only, deliberately — NSButton silently drops a custom
        # attributedTitle (blank button, icon and all) when setEnabled_ is
        # False, so "disabled" here is visual-only. The actual guards
        # (engine.begin_meeting's state check, showForProfile_'s None
        # check) already make a stray click harmless.
        can_meet = state in (State.READY, State.MEETING_RECORDING)
        self._meeting_button.setAlphaValue_(1.0 if can_meet else 0.5)
        can_train = (
            self.engine.transcriber is not None
            and self.engine.profile is not None
            and state not in (State.MEETING_RECORDING, State.MEETING_PROCESSING)
        )
        self._train_button.setAlphaValue_(1.0 if can_train else 0.4)
        self._meetings_btn.setAlphaValue_(0.4 if is_recording else 1.0)

    def meetingProgress_(self, text):
        if self._waveform.isHidden():  # don't fight the live "Recording…" line
            self._status.setStringValue_(str(text))

    def meetingSaved_(self, meeting_id):
        if self.meetings_window is not None:
            self.meetings_window.reload()

    # -- actions --------------------------------------------------------

    def toggleMeeting_(self, sender):
        if self.engine.state is State.MEETING_RECORDING:
            self.engine.end_meeting()
        else:
            self.engine.begin_meeting()

    def openMeetings_(self, sender):
        from .meetings_window import MeetingsWindowController

        if self.meetings_window is None:
            self.meetings_window = MeetingsWindowController.alloc().init()
        self.meetings_window.show()

    def openTraining_(self, sender):
        from .training_window import TrainingWindowController

        if self.engine.profile is None:
            return
        if self.training_window is None:
            self.training_window = TrainingWindowController.alloc().initWithEngine_(
                self.engine
            )
        self.training_window.showForProfile_(self.engine.profile)

    def quitApp_(self, sender):
        print("\nShutting down.")
        self.engine.shutdown()
        NSApplication.sharedApplication().terminate_(None)
