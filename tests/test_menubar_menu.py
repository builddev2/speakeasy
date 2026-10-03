from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest
from AppKit import NSApplication

from speakeasy.engine import State
from speakeasy.meeting_library import CalendarEvent, EventPerson, MeetingLibrary
from speakeasy.ui import menubar, services

NSApplication.sharedApplication()


class Engine:
    def __init__(self):
        self.state = State.READY
        self.profile = None
        self.last_dictation_text = ""
        self.last_dictation_heard = ""
        self.can_train = False
        self.meeting_event = None
        self.last_insertion_outcome = None
        self.meeting_processing_error = None
        self.recorder = NS(state="x")
        self.began = []

    def begin_meeting(self, options):
        self.began.append(options)


def _event(key="evt-1"):
    start = datetime.now().astimezone() - timedelta(minutes=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    utc = start.utctimetuple()
    s = datetime(*utc[:6])
    return CalendarEvent(key, "Work", "Standup", s.strftime(fmt),
                         (s + timedelta(minutes=30)).strftime(fmt), False, False, [],
                         other_attendees=1, people=[EventPerson("A", None, "attendee")] * 2)


@pytest.fixture
def ctl(monkeypatch):
    monkeypatch.setattr(services, "calendar_sync", NS(access=lambda: "connected"), raising=False)
    monkeypatch.setattr(MeetingLibrary, "calendar_events_between",
                        lambda self, a, b: [_event()])
    engine = Engine()
    c = menubar.StatusItemController.alloc().initWithEngine_(engine)
    yield c, engine
    from AppKit import NSStatusBar
    NSStatusBar.systemStatusBar().removeStatusItem_(c._item)


def test_card_visible_when_ready_with_event(ctl):
    c, _ = ctl
    c.menuWillOpen_(None)
    assert not c._next_up_item.isHidden()
    assert c._next_up_key == "evt-1"


def test_card_hidden_when_not_ready(ctl):
    c, engine = ctl
    engine.state = State.MIC_FAILED
    c.menuWillOpen_(None)
    assert c._next_up_item.isHidden()


def test_library_failure_hides_card_without_raising(ctl, monkeypatch):
    c, _ = ctl
    c.menuWillOpen_(None)

    def boom(self, a, b):
        raise RuntimeError("db")

    monkeypatch.setattr(MeetingLibrary, "calendar_events_between", boom)
    c.menuWillOpen_(None)
    assert c._next_up_item.isHidden()


def test_record_passes_event_key(ctl):
    c, engine = ctl
    c.menuWillOpen_(None)
    c.nextUpRecord_(None)
    assert engine.began[0].calendar_event_key == "evt-1"


def test_mic_failed_state_shows_retry(ctl):
    c, engine = ctl
    engine.state = State.MIC_FAILED
    c.engineStateChanged_("mic_failed")
    assert not c._retry_item.isHidden()
    engine.state = State.READY
    c.engineStateChanged_("ready")
    assert c._retry_item.isHidden()
