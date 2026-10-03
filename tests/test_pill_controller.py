from types import SimpleNamespace

import pytest

from speakeasy import settings
from speakeasy.ui import pill_controller as pc


class FakePanel:
    def __init__(self):
        self.views, self.shown, self.drawer, self.menus = [], [], None, []
        self.visible = False
        self.origin = None

    def render(self, view): self.views.append(view)
    def show(self, origin): self.visible, self.origin = True, origin; self.shown.append(origin)
    def hide(self): self.visible = False; self.drawer = None
    def frame_origin(self): return self.origin
    def set_drawer(self, rows): self.drawer = rows
    def is_drawer_visible(self): return self.drawer is not None
    def event_menu(self, items): self.menus.append(items)


class Health:
    def __init__(self, **kw): self.kw = kw
    def to_dict(self): return dict(self.kw)


def make_engine(state="ready"):
    calls = []
    engine = SimpleNamespace(
        state=SimpleNamespace(value=state), meeting_event=None, meeting_processing_error=None,
        meeting_recorder=SimpleNamespace(elapsed_seconds=5.0, capture_mode="mic_and_system",
                                         system_audio_status="available",
                                         health=Health(mic_first_buffer=True, system_first_buffer=True,
                                                       system_nonzero_signal=True)),
        end_meeting=lambda: calls.append("end"),
        cancel_meeting_processing=lambda: calls.append("cancel"),
        link_meeting_event=lambda key: calls.append(("link", key)))
    return engine, calls


@pytest.fixture
def setup(monkeypatch):
    panel = FakePanel()
    monkeypatch.setattr(pc, "_make_panel", lambda target: panel)
    monkeypatch.setattr(pc, "_screens", lambda: [(0, 0, 1440, 875)])
    monkeypatch.setattr(pc, "_mouse_screen", lambda: (0, 0, 1440, 875))
    monkeypatch.setattr(pc, "_today_events", lambda: [{"key": "e1", "title": "Weekly", "time": "9:00 AM"}])
    monkeypatch.setattr(pc, "_start_timer", lambda target, seconds, selector, repeats: SimpleNamespace(invalidate=lambda: None))
    engine, calls = make_engine()
    opened = []
    pill = pc.PillController.alloc().initWithEngine_opener_(engine, opened.append)
    return SimpleNamespace(pill=pill, panel=panel, engine=engine, calls=calls, opened=opened)


def go(s, state, error=None):
    s.engine.state.value = state
    s.engine.meeting_processing_error = error
    s.pill.engineStateChanged_(state)


def test_pill_shows_on_any_meeting_recording_state(setup):
    go(setup, "meeting_recording")
    assert setup.panel.visible and setup.panel.origin == (540.0, 831.0)
    assert setup.panel.views[-1].buttons == ("notes", "stop")


def test_hidden_while_idle(setup):
    go(setup, "ready")
    assert not setup.panel.visible


def test_notes_opens_recording_and_stop_ends(setup):
    go(setup, "meeting_recording")
    setup.pill.pillNotes_(None)
    setup.pill.pillStop_(None)
    assert setup.opened == ["recording"] and setup.calls == ["end"]


def test_cancel_discard_cancels_engine_and_hides_on_idle(setup):
    go(setup, "meeting_processing")
    setup.pill.pillCancel_(None)
    assert setup.panel.views[-1].title == "Discard meeting?"
    setup.pill.pillDiscard_(None)
    assert setup.calls == ["cancel"]
    go(setup, "ready")
    assert not setup.panel.visible


def test_saved_then_open_opens_that_meeting(setup):
    go(setup, "meeting_processing")
    setup.pill.meetingSaved_("m-1")
    go(setup, "ready")
    assert setup.panel.views[-1].title == "Saved"
    setup.pill.pillOpen_(None)
    assert setup.opened == [{"meeting": "m-1"}]
    assert not setup.panel.visible


def test_saved_expires(setup):
    go(setup, "meeting_processing")
    setup.pill.meetingSaved_("m-1")
    setup.pill.savedExpired_(None)
    assert not setup.panel.visible


def test_failed_open_meetings_and_close(setup):
    go(setup, "meeting_processing")
    go(setup, "ready", "processing_failed")
    assert setup.panel.views[-1].title == "Couldn't finish"
    setup.pill.pillOpenMeetings_(None)
    assert setup.opened == [None]
    setup.pill.pillClose_(None)
    assert not setup.panel.visible


def test_progress_text_reaches_the_pill(setup):
    go(setup, "meeting_processing")
    setup.pill.meetingProgress_("Transcribing meeting… 42%")
    assert setup.panel.views[-1].detail == "Transcribing meeting… 42%"


def test_moved_position_is_saved_and_reused(setup):
    setup.pill.pillMovedTo_([100.0, 500.0])
    assert settings.get_pill_origin() == (100.0, 500.0)
    go(setup, "meeting_recording")
    assert setup.panel.origin == (100.0, 500.0)


def test_saved_origin_offscreen_uses_default(setup):
    settings.set_pill_origin(5000.0, 500.0)
    go(setup, "meeting_recording")
    assert setup.panel.origin == (540.0, 831.0)


def test_body_click_toggles_drawer(setup):
    go(setup, "meeting_recording")
    setup.pill.pillBodyClicked_(None)
    assert [r.label for r in setup.panel.drawer] == ["Event", "Microphone", "System audio", "Dictation"]
    setup.pill.pillBodyClicked_(None)
    assert setup.panel.drawer is None


def test_warning_opens_drawer_once(setup):
    go(setup, "meeting_recording")
    setup.engine.meeting_recorder.health = Health(helper_exited=True, helper_exit_reason="crash")
    setup.pill.tick_(None)
    assert setup.panel.drawer is not None
    setup.pill.drawerClose_(None)
    setup.pill.tick_(None)
    assert setup.panel.drawer is None


def test_event_menu_links_and_unlinks(setup):
    go(setup, "meeting_recording")
    setup.pill.pillBodyClicked_(None)
    setup.pill.drawerEventClicked_(None)
    assert setup.panel.menus[-1] == [("9:00 AM · Weekly", "e1", False)]
    setup.pill.drawerEventChosen_(SimpleNamespace(representedObject=lambda: "e1"))
    setup.engine.meeting_event = SimpleNamespace(event_key="e1", title="Weekly")
    setup.pill.drawerEventClicked_(None)
    assert setup.panel.menus[-1] == [("9:00 AM · Weekly", "e1", True), ("Unlink", None, False)]
    setup.pill.drawerEventChosen_(SimpleNamespace(representedObject=lambda: None))
    assert setup.calls == [("link", "e1"), ("link", None)]


def test_no_calendar_means_no_event_menu(setup, monkeypatch):
    monkeypatch.setattr(pc, "_today_events", lambda: None)
    go(setup, "meeting_recording")
    setup.pill.drawerEventClicked_(None)
    assert setup.panel.menus == []
