"""The record banner: a borderless, non-activating glass panel in the
top-right corner. It never becomes key, so the meeting app keeps keyboard
focus, and its ClickyButtons act on the first click. Main-thread only; it
draws what RecordPromptController tells it and forwards clicks to it."""

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSColor,
    NSFontWeightSemibold,
    NSLineBreakByTruncatingTail,
    NSMenu,
    NSMenuItem,
    NSPanel,
    NSScreen,
    NSStatusWindowLevel,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialPopover,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
    NSAnimationContext,
)
from Foundation import NSMakePoint, NSMakeRect, NSObject, NSTimer

from .glass import ClickyButton, make_label

WIDTH, HEIGHT, MARGIN = 360.0, 92.0, 12.0
CONFIRM_SECONDS = 2.0


class RecordPromptPanel(NSObject):
    def initWithTarget_(self, target):
        self = objc.super(RecordPromptPanel, self).init()
        if self is None:
            return None
        self.target = target
        self._more_titles = []
        self._hide_timer = None
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIDTH, HEIGHT), style, NSBackingStoreBuffered, False)
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
        effect = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, HEIGHT))
        effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        effect.setMaterial_(NSVisualEffectMaterialPopover)
        effect.setState_(NSVisualEffectStateActive)
        effect.setWantsLayer_(True)
        effect.layer().setCornerRadius_(14.0)
        effect.layer().setMasksToBounds_(True)
        panel.setContentView_(effect)

        self._title = make_label("", 13.0, NSFontWeightSemibold)
        self._title.setFrame_(NSMakeRect(16, 58, WIDTH - 32, 18))
        self._title.setLineBreakMode_(NSLineBreakByTruncatingTail)
        self._subtitle = make_label("", 12.0, secondary=True)
        self._subtitle.setFrame_(NSMakeRect(16, 40, WIDTH - 32, 16))
        self._subtitle.setLineBreakMode_(NSLineBreakByTruncatingTail)
        self._primary = self._button(b"primaryClicked:", NSMakeRect(WIDTH - 16 - 96, 8, 96, 28))
        # Coral primary, as in the Meetings window.
        self._primary.setBezelColor_(
            NSColor.colorWithSRGBRed_green_blue_alpha_(1.0, 0.45, 0.38, 1.0))
        self._secondary = self._button(
            b"secondaryClicked:", NSMakeRect(WIDTH - 16 - 96 - 8 - 128, 8, 128, 28))
        self._more = self._button(b"moreClicked:", NSMakeRect(12, 8, 84, 28))
        for view in (self._title, self._subtitle, self._primary, self._secondary, self._more):
            effect.addSubview_(view)
        self._panel = panel
        return self

    @objc.python_method
    def _button(self, action, frame):
        button = ClickyButton.alloc().initWithFrame_(frame)
        button.setBezelStyle_(NSBezelStyleRounded)
        button.setTarget_(self)
        button.setAction_(action)
        return button

    # -- drawing (controller) ---------------------------------------------------

    @objc.python_method
    def show(self, title, subtitle, primary, secondary, more):
        self._cancel_hide_timer()
        self._title.setStringValue_(title)
        self._subtitle.setStringValue_(subtitle)
        self._primary.setTitle_(primary)
        self._secondary.setTitle_(secondary)
        self._primary.setHidden_(False)
        self._secondary.setHidden_(False)
        self._more_titles = list(more)
        self._more.setTitle_(f"{len(self._more_titles)} more")
        self._more.setHidden_(not self._more_titles)
        self._place()
        if not self._panel.isVisible():
            self._panel.setAlphaValue_(0.0)
            self._panel.orderFrontRegardless()
            NSAnimationContext.beginGrouping()
            NSAnimationContext.currentContext().setDuration_(0.18)
            self._panel.animator().setAlphaValue_(1.0)
            NSAnimationContext.endGrouping()

    @objc.python_method
    def show_confirmation(self, text):
        self._subtitle.setStringValue_(text)
        for button in (self._primary, self._secondary, self._more):
            button.setHidden_(True)
        self._cancel_hide_timer()
        self._hide_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            CONFIRM_SECONDS, self, b"confirmationDone:", None, False)

    @objc.python_method
    def hide(self):
        self._cancel_hide_timer()
        # Ordered out at once: a faded-but-visible panel would still eat clicks.
        self._panel.orderOut_(None)

    def confirmationDone_(self, timer):
        self._hide_timer = None
        self.hide()

    @objc.python_method
    def _cancel_hide_timer(self):
        if self._hide_timer is not None:
            self._hide_timer.invalidate()
            self._hide_timer = None

    @objc.python_method
    def _place(self):
        screen = (NSScreen.mainScreen() or NSScreen.screens()[0]).visibleFrame()
        self._panel.setFrameOrigin_(NSMakePoint(
            screen.origin.x + screen.size.width - WIDTH - MARGIN,
            screen.origin.y + screen.size.height - HEIGHT - MARGIN))

    # -- clicks -------------------------------------------------------------

    def primaryClicked_(self, sender):
        self.target.bannerPrimary_(sender)

    def secondaryClicked_(self, sender):
        self.target.bannerSecondary_(sender)

    def moreClicked_(self, sender):
        menu = NSMenu.alloc().initWithTitle_("")
        for index, title in enumerate(self._more_titles, start=1):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                f"Record ‘{title}’", b"moreChosen:", "")
            item.setTarget_(self)
            item.setTag_(index)
            menu.addItem_(item)
        menu.popUpMenuPositioningItem_atLocation_inView_(None, NSMakePoint(0, 0), sender)

    def moreChosen_(self, item):
        self.target.bannerMore_(item)
