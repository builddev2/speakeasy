from AppKit import NSApplication, NSWindowStyleMaskNonactivatingPanel

from speakeasy.ui.pill_model import DrawerRow, PillView
from speakeasy.ui.pill_panel import PillPanel

NSApplication.sharedApplication()


class Target:
    """Explicit methods: PyObjC dispatches button actions only to methods that
    really exist on the target (a __getattr__ fallback is never consulted)."""

    def __init__(self):
        self.calls = []

    def _hit(self, name):
        self.calls.append(name)

    def pillNotes_(self, s): self._hit("pillNotes_")
    def pillStop_(self, s): self._hit("pillStop_")
    def pillCancel_(self, s): self._hit("pillCancel_")
    def pillDiscard_(self, s): self._hit("pillDiscard_")
    def pillKeep_(self, s): self._hit("pillKeep_")
    def pillOpen_(self, s): self._hit("pillOpen_")
    def pillOpenMeetings_(self, s): self._hit("pillOpenMeetings_")
    def pillClose_(self, s): self._hit("pillClose_")
    def pillBodyClicked_(self, s): self._hit("pillBodyClicked_")
    def pillMovedTo_(self, s): self._hit("pillMovedTo_")
    def drawerEventClicked_(self, s): self._hit("drawerEventClicked_")
    def drawerEventChosen_(self, s): self._hit("drawerEventChosen_")
    def drawerClose_(self, s): self._hit("drawerClose_")


def test_pill_panel_is_non_activating_and_forwards_clicks():
    target = Target()
    p = PillPanel.alloc().initWithTarget_(target)
    assert p._panel.styleMask() & NSWindowStyleMaskNonactivatingPanel
    assert p._panel.becomesKeyOnlyIfNeeded()
    assert not p._panel.hidesOnDeactivate()
    p.render(PillView("recording", "Weekly", "12:03", "ok", ("notes", "stop")))
    p._buttons["notes"].performClick_(None)
    p._buttons["stop"].performClick_(None)
    assert target.calls == ["pillNotes_", "pillStop_"]


def test_render_shows_only_the_phase_buttons():
    p = PillPanel.alloc().initWithTarget_(Target())
    p.render(PillView("recording", "Weekly", "12:03", "ok", ("notes", "stop")))
    assert set(k for k, b in p._buttons.items() if not b.isHidden()) == {"notes", "stop"}
    p.render(PillView("confirm_discard", "Discard meeting?", "", "none", ("discard", "keep")))
    assert set(k for k, b in p._buttons.items() if not b.isHidden()) == {"discard", "keep"}
    assert p._indicator.isHidden()


def test_title_never_overlaps_buttons():
    p = PillPanel.alloc().initWithTarget_(Target())
    for view in (PillView("recording", "x" * 200, "1:02:03", "warning", ("notes", "stop")),
                 PillView("processing", "Processing", "Transcribing meeting… 42%", "none", ("notes", "cancel")),
                 PillView("failed", "Couldn't finish", "", "none", ("open_meetings", "close"))):
        p.render(view)
        right = p._title.frame().origin.x + p._title.frame().size.width
        lefts = [b.frame().origin.x for b in p._buttons.values() if not b.isHidden()]
        assert right <= min(lefts)


def test_show_does_not_make_the_panel_key():
    p = PillPanel.alloc().initWithTarget_(Target())
    p.render(PillView("recording", "Weekly", "00:01", "waiting", ("notes", "stop")))
    p.show((100.0, 700.0))
    assert p._panel.isVisible() and not p._panel.isKeyWindow()
    assert p.frame_origin() == (100.0, 700.0)
    p.hide()
    assert not p._panel.isVisible()


def test_drawer_rows_and_close():
    target = Target()
    p = PillPanel.alloc().initWithTarget_(target)
    p.show((100.0, 700.0))
    p.set_drawer([DrawerRow("Event", "Link to event…", "action"),
                  DrawerRow("Microphone", "You · working", "ok"),
                  DrawerRow("System audio", "All apps · no sound yet", "warning"),
                  DrawerRow("Dictation", "Paused until the meeting ends", "muted")])
    assert p.is_drawer_visible()
    p._drawer_close.performClick_(None)
    assert target.calls[-1] == "drawerClose_"
    p.set_drawer(None)
    assert not p.is_drawer_visible()
    p.hide()


def test_drawer_sits_centred_six_points_below_the_pill():
    p = PillPanel.alloc().initWithTarget_(Target())
    p.show((100.0, 700.0))
    p.set_drawer([DrawerRow("Event", "Link to event…", "action")])
    d = p._drawer.frame()
    assert d.size.width == 300.0
    assert d.origin.x == 100.0 + (360.0 - 300.0) / 2
    assert d.origin.y + d.size.height == 700.0 - 6.0
    p.hide()


def test_background_click_and_drag_forward_to_target():
    target = Target()
    p = PillPanel.alloc().initWithTarget_(target)
    p.show((100.0, 700.0))
    bg = p._background
    assert bg.acceptsFirstMouse_(None)
    p.pillMoved(5.0, 6.0)
    assert target.calls == ["pillMovedTo_"]
    p.hide()


def test_event_menu_items_carry_keys():
    target = Target()
    p = PillPanel.alloc().initWithTarget_(target)
    p.show((100.0, 700.0))
    p.set_drawer([DrawerRow("Event", "Link to event…", "action")])
    assert p._event_button is not None
    p.eventChosen_(None)
    assert target.calls == ["drawerEventChosen_"]
    p.hide()
