"""App-wide light/dark appearance. Set once on NSApp, so every window, its
WKWebView (via prefers-color-scheme) and the record banner follow it; None
means follow macOS. Main thread only."""

from .. import settings

CHOICES = settings.APPEARANCES


def apply(choice: str) -> None:
    from AppKit import (NSApplication, NSAppearance, NSAppearanceNameAqua,
                        NSAppearanceNameDarkAqua)

    name = {"light": NSAppearanceNameAqua, "dark": NSAppearanceNameDarkAqua}.get(choice)
    NSApplication.sharedApplication().setAppearance_(
        NSAppearance.appearanceNamed_(name) if name else None)


def apply_saved() -> None:
    apply(settings.get_appearance())
