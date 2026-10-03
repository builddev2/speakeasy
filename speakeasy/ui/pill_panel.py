"""The recording pill and its capture drawer: borderless, non-activating glass
panels that never become key, so the user's call app keeps keyboard focus.
Main-thread only. They draw what PillController hands them (pill_model.PillView
/ DrawerRow) and forward clicks to a target; no engine imports here."""

import objc
from AppKit import (
    NSAnimationContext,
    NSAttributedString,
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSColor,
    NSEvent,
    NSFont,
    NSFontAttributeName,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSForegroundColorAttributeName,
    NSLineBreakByTruncatingTail,
    NSLineBreakByWordWrapping,
    NSMenu,
    NSMenuItem,
    NSPanel,
    NSStatusWindowLevel,
    NSTextAlignmentLeft,
    NSTextAlignmentRight,
    NSView,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialHUDWindow,
    NSVisualEffectMaterialPopover,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSArray, NSMakePoint, NSMakeRect, NSObject

from .glass import ClickyButton, make_label
from .record_prompt_panel import CORAL, ON_ACCENT, STOP_RED  # noqa: F401  (CORAL re-exported for callers)

PILL_W, PILL_H, PILL_RADIUS = 360.0, 36.0, 18.0
DRAWER_W, DRAWER_RADIUS, DRAWER_GAP = 300.0, 12.0, 6.0
ROW_H, DRAWER_PAD, FOOTER_H = 24.0, 10.0, 30.0
LABEL_W, DRAG_THRESHOLD = 96.0, 3.0
TIMER_W = 52.0
FOOTER_TEXT = "Audio stays on this Mac and is deleted once the transcript is saved."

# id -> (width, height, y, title, action, tooltip)
_BUTTONS = {
    "stop": (26.0, 26.0, 5.0, "■", b"stopClicked:", "End Meeting"),
    "notes": (60.0, 24.0, 6.0, "Notes", b"notesClicked:", None),
    "cancel": (64.0, 24.0, 6.0, "Cancel", b"cancelClicked:", None),
    "discard": (70.0, 24.0, 6.0, "Discard", b"discardClicked:", None),
    "keep": (56.0, 24.0, 6.0, "Keep", b"keepClicked:", None),
    "open": (56.0, 24.0, 6.0, "Open", b"openClicked:", None),
    "open_meetings": (110.0, 24.0, 6.0, "Open Meetings", b"openMeetingsClicked:", None),
    "close": (24.0, 24.0, 6.0, "×", b"closeClicked:", "Close"),
}
_INDICATOR_COLORS = {
    "ok": NSColor.systemGreenColor,
    "warning": NSColor.systemOrangeColor,
    "waiting": NSColor.tertiaryLabelColor,
}


def _make_panel(width, height, radius, material):
    style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, width, height), style, NSBackingStoreBuffered, False)
    panel.setLevel_(NSStatusWindowLevel)
    panel.setOpaque_(False)
    panel.setBackgroundColor_(NSColor.clearColor())
    panel.setHasShadow_(True)
    panel.setHidesOnDeactivate_(False)
    panel.setBecomesKeyOnlyIfNeeded_(True)
    panel.setReleasedWhenClosed_(False)
    panel.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorStationary
        | NSWindowCollectionBehaviorFullScreenAuxiliary)
    effect = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
    effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
    effect.setMaterial_(material)
    effect.setState_(NSVisualEffectStateActive)
    effect.setWantsLayer_(True)
    effect.layer().setCornerRadius_(radius)
    effect.layer().setMasksToBounds_(True)
    panel.setContentView_(effect)
    return panel, effect


def _styled(text, color, size, weight=None):
    font = (NSFont.systemFontOfSize_weight_(size, weight) if weight is not None
            else NSFont.systemFontOfSize_(size))
    return NSAttributedString.alloc().initWithString_attributes_(
        text, {NSForegroundColorAttributeName: color, NSFontAttributeName: font})


class PillBackground(NSView):
    """Drag the pill by its background; a click without movement is a body click."""

    def acceptsFirstMouse_(self, event):
        return True

    def hitTest_(self, point):
        # Only buttons keep their own clicks; the dots and labels resolve to the
        # background so a first click on an inactive app is never swallowed.
        hit = objc.super(PillBackground, self).hitTest_(point)
        if hit is None or isinstance(hit, ClickyButton):
            return hit
        return self

    def mouseDown_(self, event):
        self._down_mouse = NSEvent.mouseLocation()
        origin = self.window().frame().origin
        self._down_origin = (origin.x, origin.y)
        self._moved = False

    def mouseDragged_(self, event):
        if getattr(self, "_down_mouse", None) is None:
            return
        now = NSEvent.mouseLocation()
        dx, dy = now.x - self._down_mouse.x, now.y - self._down_mouse.y
        if abs(dx) > DRAG_THRESHOLD or abs(dy) > DRAG_THRESHOLD:
            self._moved = True
        if self._moved:
            self.window().setFrameOrigin_(
                NSMakePoint(self._down_origin[0] + dx, self._down_origin[1] + dy))
            owner = getattr(self, "owner", None)
            if owner is not None:
                owner.pillDragged()

    def mouseUp_(self, event):
        if getattr(self, "_down_mouse", None) is None:
            return
        self._down_mouse = None
        owner = getattr(self, "owner", None)
        if owner is None:
            return
        if self._moved:
            origin = self.window().frame().origin
            owner.pillMoved(origin.x, origin.y)
        else:
            owner.target.pillBodyClicked_(None)


class PillPanel(NSObject):
    def initWithTarget_(self, target):
        self = objc.super(PillPanel, self).init()
        if self is None:
            return None
        self.target = target
        self._panel, effect = _make_panel(PILL_W, PILL_H, PILL_RADIUS, NSVisualEffectMaterialHUDWindow)
        bg = PillBackground.alloc().initWithFrame_(NSMakeRect(0, 0, PILL_W, PILL_H))
        bg.owner = self
        effect.addSubview_(bg)
        self._background = bg

        self._dot = NSView.alloc().initWithFrame_(NSMakeRect(14, 14, 8, 8))
        self._dot.setWantsLayer_(True)
        self._dot.layer().setBackgroundColor_(STOP_RED.CGColor())
        self._dot.layer().setCornerRadius_(4.0)
        self._title = make_label("", 13.0, NSFontWeightSemibold)
        self._title.setLineBreakMode_(NSLineBreakByTruncatingTail)
        self._detail = make_label("", 12.5, secondary=True)
        self._detail.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(12.5, NSFontWeightRegular))
        self._detail.setLineBreakMode_(NSLineBreakByTruncatingTail)
        self._indicator = NSView.alloc().initWithFrame_(NSMakeRect(0, 14, 8, 8))
        self._indicator.setWantsLayer_(True)
        self._indicator.layer().setCornerRadius_(4.0)
        self._buttons = {}
        for key, (w, h, y, title, action, tip) in _BUTTONS.items():
            b = ClickyButton.alloc().initWithFrame_(NSMakeRect(0, y, w, h))
            b.setBezelStyle_(NSBezelStyleRounded)
            b.setTitle_(title)
            b.setTarget_(self)
            b.setAction_(action)
            if tip:
                b.setToolTip_(tip)
            b.setHidden_(True)
            self._buttons[key] = b
        self._buttons["stop"].setBezelColor_(STOP_RED)
        self._buttons["stop"].setAttributedTitle_(_styled("■", ON_ACCENT, 11.0, NSFontWeightSemibold))
        self._buttons["discard"].setBezelColor_(STOP_RED)
        self._buttons["discard"].setAttributedTitle_(
            _styled("Discard", ON_ACCENT, 13.0, NSFontWeightSemibold))
        self._buttons["close"].setBordered_(False)
        for v in (self._dot, self._title, self._detail, self._indicator, *self._buttons.values()):
            bg.addSubview_(v)

        self._drawer, self._drawer_effect = _make_panel(
            DRAWER_W, 100.0, DRAWER_RADIUS, NSVisualEffectMaterialPopover)
        self._drawer_close = ClickyButton.alloc().initWithFrame_(NSMakeRect(0, 0, 18, 18))
        self._drawer_close.setBezelStyle_(NSBezelStyleRounded)
        self._drawer_close.setBordered_(False)
        self._drawer_close.setTitle_("✕")
        self._drawer_close.setToolTip_("Close")
        self._drawer_close.setTarget_(self)
        self._drawer_close.setAction_(b"drawerCloseClicked:")
        self._event_button = None
        self._drawer_shown = False
        return self

    # -- drawing --------------------------------------------------------------

    @objc.python_method
    def render(self, view):
        for b in self._buttons.values():
            b.setHidden_(True)
        x = PILL_W - 6.0
        for key in reversed(view.buttons):
            b = self._buttons[key]
            x -= b.frame().size.width
            b.setFrameOrigin_(NSMakePoint(x, b.frame().origin.y))
            b.setHidden_(False)
            x -= 6.0
        leftmost = x + 6.0
        recording = view.phase == "recording"
        self._dot.setHidden_(not recording)
        self._title.setStringValue_(("✓ " + view.title) if view.phase == "saved" else view.title)
        self._detail.setStringValue_(view.detail)
        color = _INDICATOR_COLORS.get(view.indicator)
        show_ind = recording and color is not None
        self._indicator.setHidden_(not show_ind)
        if show_ind:
            self._indicator.layer().setBackgroundColor_(color().CGColor())
        if recording:
            right = leftmost - 10.0
            if show_ind:
                self._indicator.setFrameOrigin_(NSMakePoint(right - 8.0, 14))
                right -= 8.0 + 6.0
            timer_x = right - TIMER_W
            self._detail.setAlignment_(NSTextAlignmentRight)
            self._detail.setFrame_(NSMakeRect(timer_x, 9, TIMER_W, 18))
            self._title.setFrame_(NSMakeRect(28, 9, max(0.0, timer_x - 6.0 - 28.0), 18))
        else:
            self._detail.setAlignment_(NSTextAlignmentLeft)
            avail = max(0.0, leftmost - 8.0 - 16.0)
            self._title.sizeToFit()
            tw = min(self._title.frame().size.width, avail) if view.detail else avail
            self._title.setFrame_(NSMakeRect(16, 9, tw, 18))
            gap = 8.0 if view.detail else 0.0
            self._detail.setFrame_(NSMakeRect(16 + tw + gap, 9, max(0.0, avail - tw - gap), 18))

    @objc.python_method
    def show(self, origin):
        self._panel.setFrameOrigin_(NSMakePoint(origin[0], origin[1]))
        if not self._panel.isVisible():
            self._panel.setAlphaValue_(0.0)
            self._panel.orderFrontRegardless()
            NSAnimationContext.beginGrouping()
            NSAnimationContext.currentContext().setDuration_(0.18)
            self._panel.animator().setAlphaValue_(1.0)
            NSAnimationContext.endGrouping()
        if self._drawer_shown:
            self._place_drawer()
            self._drawer.orderFrontRegardless()

    @objc.python_method
    def hide(self):
        self._drawer.orderOut_(None)
        self._panel.orderOut_(None)

    @objc.python_method
    def frame_origin(self):
        o = self._panel.frame().origin
        return (o.x, o.y)

    # -- drawer ---------------------------------------------------------------

    @objc.python_method
    def is_drawer_visible(self):
        return bool(self._drawer_shown and self._drawer.isVisible())

    @objc.python_method
    def set_drawer(self, rows):
        if not rows:
            self._drawer_shown = False
            self._drawer.orderOut_(None)
            return
        for sub in list(self._drawer_effect.subviews()):
            sub.removeFromSuperview()
        self._event_button = None
        height = DRAWER_PAD + ROW_H * len(rows) + 6.0 + FOOTER_H + DRAWER_PAD
        self._drawer.setContentSize_((DRAWER_W, height))
        self._drawer_effect.setFrame_(NSMakeRect(0, 0, DRAWER_W, height))
        value_x = DRAWER_PAD + LABEL_W
        for i, row in enumerate(rows):
            y = height - DRAWER_PAD - ROW_H * (i + 1)
            label = make_label(row.label, 12.0, secondary=True)
            label.setFrame_(NSMakeRect(DRAWER_PAD, y + 4, LABEL_W, 16))
            self._drawer_effect.addSubview_(label)
            vw = DRAWER_W - DRAWER_PAD - value_x - (22.0 if i == 0 else 0.0)
            if row.label == "Event":
                b = ClickyButton.alloc().initWithFrame_(NSMakeRect(value_x, y + 2, vw, 20))
                b.setBordered_(False)
                b.setAlignment_(NSTextAlignmentLeft)
                b.setLineBreakMode_(NSLineBreakByTruncatingTail)
                b.setAttributedTitle_(_styled(row.value, NSColor.controlAccentColor(), 12.0))
                b.setTarget_(self)
                b.setAction_(b"eventClicked:")
                self._event_button = b
                self._drawer_effect.addSubview_(b)
            else:
                v = make_label(row.value, 12.0)
                if row.tone == "warning":
                    v.setTextColor_(NSColor.systemOrangeColor())
                elif row.tone == "muted":
                    v.setTextColor_(NSColor.secondaryLabelColor())
                v.setLineBreakMode_(NSLineBreakByTruncatingTail)
                v.setFrame_(NSMakeRect(value_x, y + 4, vw, 16))
                self._drawer_effect.addSubview_(v)
        self._drawer_close.setFrame_(NSMakeRect(DRAWER_W - 10 - 18, height - 10 - 18, 18, 18))
        self._drawer_effect.addSubview_(self._drawer_close)
        footer = make_label(FOOTER_TEXT, 11.0)
        footer.setTextColor_(NSColor.tertiaryLabelColor())
        footer.setLineBreakMode_(NSLineBreakByWordWrapping)
        footer.setMaximumNumberOfLines_(2)
        footer.setFrame_(NSMakeRect(DRAWER_PAD, DRAWER_PAD, DRAWER_W - 2 * DRAWER_PAD, FOOTER_H))
        self._drawer_effect.addSubview_(footer)
        self._drawer_shown = True
        self._place_drawer()
        if self._panel.isVisible():
            self._drawer.orderFrontRegardless()

    @objc.python_method
    def _place_drawer(self):
        f = self._panel.frame()
        h = self._drawer.frame().size.height
        self._drawer.setFrameOrigin_(NSMakePoint(
            f.origin.x + (f.size.width - DRAWER_W) / 2.0, f.origin.y - DRAWER_GAP - h))

    @objc.python_method
    def event_menu(self, items):
        if self._event_button is None:
            return
        menu = NSMenu.alloc().initWithTitle_("")
        for title, key, checked in items:
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title, b"eventChosen:", "")
            item.setTarget_(self)
            item.setRepresentedObject_(key)
            item.setState_(1 if checked else 0)
            menu.addItem_(item)
        menu.popUpMenuPositioningItem_atLocation_inView_(None, NSMakePoint(0, 0), self._event_button)

    # -- background callbacks (python-only) ------------------------------------

    @objc.python_method
    def pillDragged(self):
        if self._drawer_shown:
            self._place_drawer()

    @objc.python_method
    def pillMoved(self, x, y):
        self.target.pillMovedTo_(NSArray.arrayWithArray_([x, y]))

    # -- button actions: forwarded to the target ---------------------------------

    def notesClicked_(self, s): self.target.pillNotes_(s)
    def stopClicked_(self, s): self.target.pillStop_(s)
    def cancelClicked_(self, s): self.target.pillCancel_(s)
    def discardClicked_(self, s): self.target.pillDiscard_(s)
    def keepClicked_(self, s): self.target.pillKeep_(s)
    def openClicked_(self, s): self.target.pillOpen_(s)
    def openMeetingsClicked_(self, s): self.target.pillOpenMeetings_(s)
    def closeClicked_(self, s): self.target.pillClose_(s)
    def eventClicked_(self, s): self.target.drawerEventClicked_(s)
    def eventChosen_(self, s): self.target.drawerEventChosen_(s)
    def drawerCloseClicked_(self, s): self.target.drawerClose_(s)
