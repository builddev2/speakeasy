"""Shared glassmorphism building blocks — Apple design language.

Every Speakeasy window sits on an NSVisualEffectView so content floats on
frosted glass that picks up the desktop behind it, with the title bar
transparent and the material running edge-to-edge. Labels use the system
dynamic colors, so vibrancy and light/dark adaptation come for free.
"""

from AppKit import (
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSFont,
    NSFontWeightRegular,
    NSTextField,
    NSView,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialUnderWindowBackground,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskTitled,
    NSWindowTitleHidden,
)
from Foundation import NSMakeRect
from Quartz import CAGradientLayer


class GradientView(NSView):
    """A view whose backing layer is a CAGradientLayer — used as a
    decoration sitting *behind* a transparent, borderless button, since a
    button's own drawn title/image isn't reliably paintable-over by a
    manually added sublayer.

    Objective-C class names are global (not scoped per Python module), so
    this must be defined once here and imported — two windows each
    defining their own same-named subclass crashes the second one to load
    with "overriding existing Objective-C class".
    """

    def makeBackingLayer(self):
        return CAGradientLayer.layer()

    def setColors_(self, colors) -> None:
        self.layer().setColors_([c.CGColor() for c in colors])


class ClickyButton(NSButton):
    """A single click both focuses this window and triggers the button.

    Plain NSButton defaults to acceptsFirstMouse: NO, so a click on an
    unfocused window is consumed just to bring it forward — the button
    itself doesn't fire. That's invisible on a document window you're
    already working in, but on these small utility panels (opened fresh
    from the Dock, clicked once, expected to act) it reads as "this button
    doesn't work." Use this in place of NSButton wherever the button might
    be a window's very first click.
    """

    def acceptsFirstMouse_(self, event):
        return True


def make_glass_window(
    title: str,
    width: float,
    height: float,
    material=NSVisualEffectMaterialUnderWindowBackground,
) -> tuple[NSWindow, NSVisualEffectView]:
    """A centered titled window whose content view is active frosted glass.

    Returns (window, effect_view); add subviews to the effect view.
    """
    style = (
        NSWindowStyleMaskTitled
        | NSWindowStyleMaskClosable
        | NSWindowStyleMaskMiniaturizable
        | NSWindowStyleMaskFullSizeContentView
    )
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, width, height), style, NSBackingStoreBuffered, False
    )
    window.setTitle_(title)
    window.setTitlebarAppearsTransparent_(True)
    # The title string above stays set for the window-switcher/VoiceOver;
    # every glass window draws its own custom title label in its content,
    # so the native titlebar's rendering of it must be hidden or the two
    # visibly overlap/duplicate.
    window.setTitleVisibility_(NSWindowTitleHidden)
    # Windows are created once and reopened; closing must not deallocate.
    window.setReleasedWhenClosed_(False)

    effect = NSVisualEffectView.alloc().initWithFrame_(
        NSMakeRect(0, 0, width, height)
    )
    effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
    effect.setState_(NSVisualEffectStateActive)
    effect.setMaterial_(material)
    effect.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
    window.setContentView_(effect)
    window.center()
    return window, effect


def make_label(
    text: str,
    size: float = 13.0,
    weight=NSFontWeightRegular,
    secondary: bool = False,
) -> NSTextField:
    """A vibrancy-aware static label using the system font."""
    label = NSTextField.labelWithString_(text)
    label.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
    label.setTextColor_(
        NSColor.secondaryLabelColor() if secondary else NSColor.labelColor()
    )
    return label


def make_card(frame) -> NSView:
    """A rounded translucent card to group content on the glass."""
    card = NSView.alloc().initWithFrame_(frame)
    card.setWantsLayer_(True)
    layer = card.layer()
    layer.setCornerRadius_(10.0)
    layer.setBackgroundColor_(
        NSColor.colorWithCalibratedWhite_alpha_(0.5, 0.14).CGColor()
    )
    return card
