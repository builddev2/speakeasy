"""Floating waveform indicator: borderless rainbow bars, no chrome.

All AppKit objects live on the main thread. The public Overlay methods are
safe to call from any thread; they dispatch to the main thread via
performSelectorOnMainThread.
"""

import math

import objc
from AppKit import (
    NSAnimationContext,
    NSBezierPath,
    NSColor,
    NSPanel,
    NSScreen,
    NSShadow,
    NSStatusWindowLevel,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
    NSBackingStoreBuffered,
)
from Foundation import NSMakeRect, NSObject, NSTimer

from . import config

_PADDING = 14.0  # room around the bars so the glow isn't clipped
_FPS = 30.0
# Bars taller than this fraction of max at silence look dead; resting size.
_REST_FRAC = 0.06
# RMS of normal speech is roughly 0.02-0.2; this maps it to bar height.
_LEVEL_FULL_SCALE = 0.15


def _view_size() -> tuple[float, float]:
    n = config.OVERLAY_BAR_COUNT
    width = n * config.OVERLAY_BAR_WIDTH + (n - 1) * config.OVERLAY_BAR_GAP
    return width + 2 * _PADDING, config.OVERLAY_MAX_BAR_HEIGHT + 2 * _PADDING


class _BarsView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(_BarsView, self).initWithFrame_(frame)
        if self is None:
            return None
        n = config.OVERLAY_BAR_COUNT
        self.fracs = [_REST_FRAC] * n
        # Pastel spectrum left->right: rose, amber, mint, sky, violet.
        self.colors = [
            NSColor.colorWithCalibratedHue_saturation_brightness_alpha_(
                0.8 * i / max(n - 1, 1), 0.55, 1.0, config.OVERLAY_ALPHA
            )
            for i in range(n)
        ]
        return self

    def drawRect_(self, rect):
        w = config.OVERLAY_BAR_WIDTH
        gap = config.OVERLAY_BAR_GAP
        max_h = config.OVERLAY_MAX_BAR_HEIGHT
        min_h = config.OVERLAY_MIN_BAR_HEIGHT
        view_h = self.bounds().size.height
        shadow = NSShadow.alloc().init()
        shadow.setShadowOffset_((0, 0))
        shadow.setShadowBlurRadius_(6.0)
        for i, frac in enumerate(self.fracs):
            h = min_h + (max_h - min_h) * frac
            x = _PADDING + i * (w + gap)
            y = (view_h - h) / 2
            shadow.setShadowColor_(self.colors[i])
            shadow.set()
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x, y, w, h), w / 2, w / 2
            )
            self.colors[i].setFill()
            path.fill()


class _Controller(NSObject):
    """Owns the panel, view, and animation timer. Main-thread only."""

    def init(self):
        self = objc.super(_Controller, self).init()
        if self is None:
            return None
        self.level_source = None
        self._mode = None
        self._timer = None
        self._phase = 0.0

        view_w, view_h = _view_size()
        screen = NSScreen.mainScreen().visibleFrame()
        x = screen.origin.x + (screen.size.width - view_w) / 2
        y = screen.origin.y + config.OVERLAY_BOTTOM_OFFSET
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, view_w, view_h), style, NSBackingStoreBuffered, False
        )
        panel.setLevel_(NSStatusWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(False)
        panel.setIgnoresMouseEvents_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        panel.setAlphaValue_(0.0)
        self._view = _BarsView.alloc().initWithFrame_(
            NSMakeRect(0, 0, view_w, view_h)
        )
        panel.setContentView_(self._view)
        self._panel = panel

        # Low-frequency no-op timer so Python-level signal handlers (Ctrl-C)
        # get a chance to run while the AppKit run loop is in charge.
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.5, self, b"heartbeat:", None, True
        )
        return self

    def heartbeat_(self, timer):
        pass

    def show_(self, mode):
        self._mode = str(mode)
        if self._timer is None:
            self._timer = (
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    1.0 / _FPS, self, b"tick:", None, True
                )
            )
        self._panel.orderFrontRegardless()
        self._fade_to_duration(1.0, 0.12)

    def dismiss(self):
        self._mode = None
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        self._view.fracs = [_REST_FRAC] * config.OVERLAY_BAR_COUNT
        self._fade_to_duration(0.0, 0.25)

    def _fade_to_duration(self, alpha, duration):
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(duration)
        self._panel.animator().setAlphaValue_(alpha)
        NSAnimationContext.endGrouping()

    def tick_(self, timer):
        self._phase += 1.0 / _FPS
        t = self._phase
        fracs = self._view.fracs
        if self._mode == "recording":
            level = self.level_source() if self.level_source else 0.0
            drive = min(1.0, max(0.0, (level / _LEVEL_FULL_SCALE) ** 0.5))
            for i in range(len(fracs)):
                wobble = 0.72 + 0.28 * math.sin(t * 9.0 + i * 1.35)
                target = max(_REST_FRAC, drive * wobble)
                fracs[i] += (target - fracs[i]) * 0.4
        else:  # transcribing: gentle uniform ripple
            for i in range(len(fracs)):
                target = 0.3 + 0.2 * math.sin(t * 5.0 + i * 0.8)
                fracs[i] += (target - fracs[i]) * 0.25
        self._view.setNeedsDisplay_(True)


class Overlay:
    """Thread-safe facade over the AppKit controller.

    Must be constructed on the main thread (before NSApp.run()).
    """

    def __init__(self) -> None:
        self._c = _Controller.alloc().init()

    def show_recording(self, level_source) -> None:
        self._c.level_source = level_source
        self._c.performSelectorOnMainThread_withObject_waitUntilDone_(
            b"show:", "recording", False
        )

    def show_transcribing(self) -> None:
        self._c.performSelectorOnMainThread_withObject_waitUntilDone_(
            b"show:", "transcribing", False
        )

    def hide(self) -> None:
        self._c.performSelectorOnMainThread_withObject_waitUntilDone_(
            b"dismiss", None, False
        )
