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

    def bannerTurnOff_(self, sender):
        self.calls.append("off")

    def bannerUndo_(self, sender):
        self.calls.append("undo")


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


def test_banner_geometry_and_timings_are_pinned():
    from pathlib import Path

    from AppKit import NSScreen

    from speakeasy.ui import record_prompt_controller as rpc
    from speakeasy.ui import record_prompt_panel as rpp

    assert rpc.TICK_SECONDS == 30.0
    assert (rpp.WIDTH, rpp.HEIGHT, rpp.MARGIN) == (360.0, 92.0, 12.0)
    assert rpp.CONFIRM_SECONDS == 2.0
    p = RecordPromptPanel.alloc().initWithTarget_(Target())
    p.show("T", "S", "Record", "Not Now", [])
    frame = p._panel.frame()
    vf = (NSScreen.mainScreen() or NSScreen.screens()[0]).visibleFrame()
    assert abs(frame.size.width - 360.0) < 0.5 and abs(frame.size.height - 92.0) < 0.5
    assert abs(frame.origin.x - (vf.origin.x + vf.size.width - 360.0 - 12.0)) < 0.5
    assert abs(frame.origin.y - (vf.origin.y + vf.size.height - 92.0 - 12.0)) < 0.5
    assert not p._panel.canBecomeKeyWindow()
    p.hide()


def test_banner_code_never_takes_focus():
    from pathlib import Path

    import speakeasy.ui.record_prompt_controller as rpc
    import speakeasy.ui.record_prompt_panel as rpp

    for module in (rpp, rpc):
        text = Path(module.__file__).read_text()
        assert "makeKeyAndOrderFront" not in text
        assert "activateIgnoringOtherApps" not in text


def test_more_button_notice_and_title_never_overlap():
    from speakeasy.ui import record_prompt_panel as rpp
    assert rpp.NOTICE_SECONDS == 5.0
    target = Target()
    p = RecordPromptPanel.alloc().initWithTarget_(target)
    p.show("A very long meeting title that will not fit in the banner at all, really",
           "Starting now · 3 invited", "Record", "Not Now", [], off_title="Turn off calendar prompts")
    assert not p._off.isHidden()
    assert p._title.frame().origin.x + p._title.frame().size.width <= p._off.frame().origin.x - 4
    p.offChosen_(None)
    p.show_notice("Calendar prompts are off", "Turn back on in Meetings › Settings", "Undo")
    assert p._secondary.title() == "Undo" and not p._secondary.isHidden()
    assert p._primary.isHidden() and p._off.isHidden() and p._more.isHidden()
    p.secondaryClicked_(None)
    assert target.calls[-2:] == ["off", "undo"]
    p.show("Call ended", "Stop recording?", "Stop", "Keep Recording", [], tone="stop", off_title="")
    assert p._off.isHidden()
    assert p._primary.bezelColor() == rpp.STOP_RED
    p.show("T", "S", "Record", "Not Now", [], tone="record", off_title="Turn off call prompts")
    assert p._primary.bezelColor() == rpp.CORAL
    assert not p._off.isHidden()
    p.show_confirmation("Recording")
    assert p._off.isHidden()
    p.hide()


def test_every_banner_button_title_fits_its_button():
    p = RecordPromptPanel.alloc().initWithTarget_(Target())
    for primary, secondary in (("Record", "Not Now"), ("Stop", "Keep Recording")):
        p.show("T", "S", primary, secondary, ["One", "Two"], off_title="Turn off call prompts")
        for button in (p._primary, p._secondary, p._more):
            assert button.fittingSize().width <= button.frame().size.width + 0.5, button.title()
    p.show_notice("Call prompts are off", "Turn back on in Meetings › Settings", "Undo")
    assert p._secondary.fittingSize().width <= p._secondary.frame().size.width + 0.5
    p.hide()
