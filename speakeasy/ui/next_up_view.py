"""The Next-up card shown as a custom view inside the menu-bar menu."""

import objc
from AppKit import (
    NSAttributedString,
    NSBezelStyleRounded,
    NSFont,
    NSFontAttributeName,
    NSFontWeightSemibold,
    NSForegroundColorAttributeName,
    NSLineBreakByTruncatingTail,
    NSView,
)
from Foundation import NSMakeRect, NSObject

from .glass import ClickyButton, make_label
from .record_prompt_panel import CORAL, ON_ACCENT

WIDTH = 280
HEIGHT = 54


class NextUpView(NSObject):
    def initWithTarget_(self, target):
        self = objc.super(NextUpView, self).init()
        if self is None:
            return None
        self._view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, HEIGHT))
        self._title = make_label("", 13.0, NSFontWeightSemibold)
        self._title.setFrame_(NSMakeRect(14, 28, WIDTH - 14 - 84, 18))
        self._title.setLineBreakMode_(NSLineBreakByTruncatingTail)
        self._subtitle = make_label("", 11.5, secondary=True)
        self._subtitle.setFrame_(NSMakeRect(14, 10, WIDTH - 14 - 84, 16))
        self._subtitle.setLineBreakMode_(NSLineBreakByTruncatingTail)
        self._record = ClickyButton.alloc().initWithFrame_(NSMakeRect(WIDTH - 14 - 72, 15, 72, 24))
        self._record.setBezelStyle_(NSBezelStyleRounded)
        self._record.setBezelColor_(CORAL)
        self._record.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(
            "Record", {NSForegroundColorAttributeName: ON_ACCENT,
                       NSFontAttributeName: NSFont.systemFontOfSize_weight_(12.5, NSFontWeightSemibold)}))
        self._record.setTarget_(target)
        self._record.setAction_(b"nextUpRecord:")
        for v in (self._title, self._subtitle, self._record):
            self._view.addSubview_(v)
        return self

    @objc.python_method
    def view(self):
        return self._view

    @objc.python_method
    def set_event(self, title, subtitle):
        self._title.setStringValue_(title)
        self._subtitle.setStringValue_(subtitle)
