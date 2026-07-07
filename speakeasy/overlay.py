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
    NSColorSpace,
    NSGradient,
    NSGraphicsContext,
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

_PADDING = 16.0  # room around the bars so the glow isn't clipped
_FPS = 30.0
# Bars taller than this fraction of max at silence look dead; resting size.
_REST_FRAC = 0.06
# RMS of normal speech is roughly 0.02-0.2; this maps it to bar height.
_LEVEL_FULL_SCALE = 0.15

# --- Dynamic lighting -------------------------------------------------------
_LIGHT_SWEEP_SPEED = 0.9    # how fast the light source glides across the row
_LIGHT_ROTATE_SPEED = 52.0  # deg/s the specular reflection band rotates
_SHINE_SPREAD_BARS = 1.5    # width of the light's falloff, in bar-pitches


def _view_size() -> tuple[float, float]:
    n = config.OVERLAY_BAR_COUNT
    width = n * config.OVERLAY_BAR_WIDTH + (n - 1) * config.OVERLAY_BAR_GAP
    return width + 2 * _PADDING, config.OVERLAY_MAX_BAR_HEIGHT + 2 * _PADDING


class _BarsView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(_BarsView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.fracs = [_REST_FRAC] * config.OVERLAY_BAR_COUNT
        self.phase = 0.0  # animation clock, advanced by the controller
        return self

    def drawRect_(self, rect):
        w = config.OVERLAY_BAR_WIDTH
        gap = config.OVERLAY_BAR_GAP
        max_h = config.OVERLAY_MAX_BAR_HEIGHT
        min_h = config.OVERLAY_MIN_BAR_HEIGHT
        view_h = self.bounds().size.height
        t = self.phase
        n = len(self.fracs)
        total_w = n * w + (n - 1) * gap
        rgb = NSColorSpace.genericRGBColorSpace()
        clear = NSColor.clearColor()

        # A single light source glides horizontally across the row; its
        # reflection band rotates. Each bar brightens as the light passes
        # (specular glint) and the whole cluster breathes with a soft flicker.
        light_x = (
            _PADDING + total_w / 2
            + (total_w / 2 + w) * math.sin(t * _LIGHT_SWEEP_SPEED)
        )
        sheen_angle = (t * _LIGHT_ROTATE_SPEED) % 360.0
        spread = (w + gap) * _SHINE_SPREAD_BARS
        # Irregular, candle-like flicker: two incommensurate sines.
        flicker = 0.82 + 0.18 * (
            0.6 * math.sin(t * 7.3) + 0.4 * math.sin(t * 13.7 + 1.1)
        )

        for i, frac in enumerate(self.fracs):
            h = min_h + (max_h - min_h) * frac
            x = _PADDING + i * (w + gap)
            y = (view_h - h) / 2
            cx = x + w / 2
            hue = 0.8 * i / max(n - 1, 1)
            shine = math.exp(-((cx - light_x) / spread) ** 2)  # 0..1 proximity
            glint = shine * flicker

            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x, y, w, h), w / 2, w / 2
            )

            # 1) Coloured glow behind the glass, pulsing as the light nears.
            NSGraphicsContext.saveGraphicsState()
            glow = NSColor.colorWithCalibratedHue_saturation_brightness_alpha_(
                hue, 0.6, 1.0, 0.30 + 0.5 * glint
            )
            sh = NSShadow.alloc().init()
            sh.setShadowOffset_((0, 0))
            sh.setShadowBlurRadius_(4.0 + 11.0 * glint)
            sh.setShadowColor_(glow)
            sh.set()
            base = NSColor.colorWithCalibratedHue_saturation_brightness_alpha_(
                hue, 0.45, 1.0, config.OVERLAY_ALPHA
            )
            base.setFill()
            path.fill()
            NSGraphicsContext.restoreGraphicsState()

            # 2) Glassy volume: bright translucent top fading to clear bottom.
            top = NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.20 + 0.16 * shine)
            NSGradient.alloc().initWithStartingColor_endingColor_(
                clear, top
            ).drawInBezierPath_angle_(path, 90.0)

            # 3) Rotating specular reflection band sweeping across the glass.
            band_a = (0.18 + 0.55 * shine) * flicker
            NSGradient.alloc().initWithColors_atLocations_colorSpace_(
                [clear, NSColor.colorWithCalibratedWhite_alpha_(1.0, band_a), clear],
                [0.28, 0.5, 0.72],
                rgb,
            ).drawInBezierPath_angle_(path, sheen_angle)

            # 4) Tiny hot specular dot near the top when the light is on it.
            if shine > 0.30:
                r = w * 0.6
                NSColor.colorWithCalibratedWhite_alpha_(
                    1.0, 0.55 * (shine - 0.30) / 0.70 * flicker
                ).setFill()
                NSBezierPath.bezierPathWithOvalInRect_(
                    NSMakeRect(cx - r / 2, y + h - r * 1.3, r, r)
                ).fill()


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
        self._view.phase = t
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
