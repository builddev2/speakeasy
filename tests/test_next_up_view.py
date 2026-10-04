from AppKit import NSApplication

from speakeasy.ui.next_up_view import NextUpView

NSApplication.sharedApplication()


class Target:
    def __init__(self):
        self.clicks = 0

    def nextUpRecord_(self, sender):
        self.clicks += 1


def test_view_shows_event_and_forwards_record():
    t = Target()
    v = NextUpView.alloc().initWithTarget_(t)
    v.set_event("Weekly 1:1 — Alex", "Starts in 4 min · 2 people")
    assert v._title.stringValue() == "Weekly 1:1 — Alex"
    assert v._subtitle.stringValue() == "Starts in 4 min · 2 people"
    v._record.performClick_(None)
    assert t.clicks == 1
    assert v.view().frame().size.width == 280
