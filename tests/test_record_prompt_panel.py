from AppKit import NSApplication, NSWindowStyleMaskNonactivatingPanel

from speakeasy.ui.record_prompt_panel import RecordPromptPanel

NSApplication.sharedApplication()   # windows need the shared app object


class Target:
    def __init__(self):
        self.calls = []

    def bannerPrimary_(self, sender):
        self.calls.append("primary")

    def bannerSecondary_(self, sender):
        self.calls.append("secondary")

    def bannerMore_(self, item):
        self.calls.append(("more", item.tag()))


def test_panel_never_takes_focus_and_forwards_clicks():
    target = Target()
    p = RecordPromptPanel.alloc().initWithTarget_(target)
    assert p._panel.styleMask() & NSWindowStyleMaskNonactivatingPanel
    assert not p._panel.canBecomeMainWindow()
    p.show("VFA Leads Sync Up", "Starting now · 3 invited", "Record", "Not Now", ["Tactical"])
    assert p._title.stringValue() == "VFA Leads Sync Up"
    assert p._primary.title() == "Record" and not p._primary.isHidden()
    assert p._more.title() == "1 more" and not p._more.isHidden()
    p.primaryClicked_(None)
    p.secondaryClicked_(None)
    item = type("I", (), {"tag": lambda self: 1})()
    p.moreChosen_(item)
    assert target.calls == ["primary", "secondary", ("more", 1)]
    p.show("Call ended", "Stop recording?", "Stop", "Keep Recording", [])
    assert p._more.isHidden()
    p.show_confirmation("Recording")
    assert p._primary.isHidden() and p._subtitle.stringValue() == "Recording"
    p.hide()
    assert not p._panel.isVisible()
