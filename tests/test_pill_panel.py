from AppKit import NSApplication, NSWindowStyleMaskNonactivatingPanel

from Foundation import NSPoint

from speakeasy.ui import pill_panel
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


def test_drawer_is_as_wide_as_and_aligned_with_the_pill_six_points_below():
    p = PillPanel.alloc().initWithTarget_(Target())
    p.show((100.0, 700.0))
    p.set_drawer([DrawerRow("Event", "Link to event…", "action")])
    d = p._drawer.frame()
    assert d.size.width == 360.0 == p._panel.frame().size.width
    assert d.origin.x == 100.0 == p._panel.frame().origin.x
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


class RecordingPanel:
    """Delegates to the real panel and records every method called on it."""

    def __init__(self, real):
        self._real = real
        self.called = []

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        if callable(attr):
            def wrapper(*a, **k):
                self.called.append(name)
                return attr(*a, **k)
            return wrapper
        return attr


def test_show_orders_front_without_ever_asking_for_key():
    p = PillPanel.alloc().initWithTarget_(Target())
    assert not p._panel.canBecomeKeyWindow()
    rec = RecordingPanel(p._panel)
    p._panel = rec
    p.render(PillView("recording", "Weekly", "00:01", "waiting", ("notes", "stop")))
    p.show((100.0, 700.0))
    assert "orderFrontRegardless" in rec.called
    assert "makeKeyWindow" not in rec.called
    assert "makeKeyAndOrderFront_" not in rec.called
    p._panel = rec._real
    p.hide()


def test_hide_also_hides_the_drawer():
    p = PillPanel.alloc().initWithTarget_(Target())
    p.show((100.0, 700.0))
    p.set_drawer([DrawerRow("Event", "Link to event…", "action")])
    assert p._drawer.isVisible()
    p.hide()
    assert not p._drawer.isVisible()
    assert not p._panel.isVisible()


class FakeNSEvent:
    point = NSPoint(0.0, 0.0)

    @classmethod
    def mouseLocation(cls):
        return cls.point


def test_drag_moves_pill_and_drawer_but_a_wiggle_is_a_click(monkeypatch):
    monkeypatch.setattr(pill_panel, "NSEvent", FakeNSEvent)
    target = Target()
    moved = []
    target.pillMovedTo_ = lambda arr: (moved.append(list(arr)), target.calls.append("pillMovedTo_"))
    p = PillPanel.alloc().initWithTarget_(target)
    p.show((100.0, 700.0))
    p.set_drawer([DrawerRow("Event", "Link to event…", "action")])
    bg = p._background
    FakeNSEvent.point = NSPoint(500.0, 500.0)
    bg.mouseDown_(None)
    FakeNSEvent.point = NSPoint(502.0, 501.0)
    bg.mouseDragged_(None)
    bg.mouseUp_(None)
    assert target.calls == ["pillBodyClicked_"]
    assert p.frame_origin() == (100.0, 700.0)

    target.calls.clear()
    FakeNSEvent.point = NSPoint(500.0, 500.0)
    bg.mouseDown_(None)
    FakeNSEvent.point = NSPoint(510.0, 492.0)
    bg.mouseDragged_(None)
    bg.mouseUp_(None)
    assert target.calls == ["pillMovedTo_"]
    assert moved == [[110.0, 692.0]]
    assert p.frame_origin() == (110.0, 692.0)
    d = p._drawer.frame()
    assert d.origin.x == 110.0
    assert d.origin.y + d.size.height == 692.0 - 6.0
    p.hide()


def test_recording_title_ends_before_the_timer_and_dots_hit_the_background():
    p = PillPanel.alloc().initWithTarget_(Target())
    p.render(PillView("recording", "x" * 200, "1:02:03", "ok", ("notes", "stop")))
    assert (p._title.frame().origin.x + p._title.frame().size.width
            <= p._detail.frame().origin.x)
    dot = p._dot.frame()
    centre = NSPoint(dot.origin.x + dot.size.width / 2, dot.origin.y + dot.size.height / 2)
    assert p._background.hitTest_(centre) is p._background
    ind = p._indicator.frame()
    centre = NSPoint(ind.origin.x + ind.size.width / 2, ind.origin.y + ind.size.height / 2)
    assert p._background.hitTest_(centre) is p._background
    stop = p._buttons["stop"].frame()
    centre = NSPoint(stop.origin.x + 13, stop.origin.y + 13)
    assert p._background.hitTest_(centre) is p._buttons["stop"]
