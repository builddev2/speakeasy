from datetime import datetime, timedelta, timezone

from speakeasy import settings
from speakeasy.meeting_library import CalendarEvent, EventPerson
from speakeasy.meeting_options import MeetingOptions
from speakeasy.ui import record_prompt_controller as rpc

UTC = timezone.utc
NOW = datetime(2026, 10, 2, 15, 0, tzinfo=UTC)
FMT = "%Y-%m-%dT%H:%M:%SZ"


def ev(key, start=NOW, people=1, title="VFA Leads Sync Up"):
    return CalendarEvent(key, "Work", title, start.strftime(FMT),
                         (start + timedelta(minutes=30)).strftime(FMT), False, False, [],
                         other_attendees=people or None,
                         people=[EventPerson("Anju", None, "attendee")] * people)


class FakePanel:
    def __init__(self):
        self.calls = []
        self.subtitles = []
        self.offs = []
        self.tones = []

    def show(self, title, subtitle, primary, secondary, more, tone="record", off_title=""):
        self.subtitles.append(subtitle)
        self.calls.append(("show", title, primary, secondary, tuple(more)))
        self.offs.append(off_title)
        self.tones.append(tone)

    def show_notice(self, title, subtitle, action):
        self.calls.append(("notice", title, subtitle, action))

    def show_confirmation(self, text):
        self.calls.append(("confirm", text))

    def hide(self):
        self.calls.append(("hide",))


class FakeProbe:
    started = stopped = False
    ignored = 0
    forgiven = 0

    def forgive_waved(self):
        self.forgiven += 1

    def ignore_current(self):
        self.ignored += 1

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class FakeLibrary:
    def __init__(self, events):
        self.events = events

    def calendar_events_overlapping(self, start, end):
        return list(self.events)


class FakeEngine:
    def __init__(self, state="ready", events=()):
        self.state = type("S", (), {"value": state})()
        self.library = FakeLibrary(events)
        self.begun, self.ended = [], 0

    def begin_meeting(self, options):
        self.begun.append(options)

    def end_meeting(self):
        self.ended += 1


class Item:
    def __init__(self, tag):
        self._tag = tag

    def tag(self):
        return self._tag


def make(monkeypatch, engine, clock=None):
    panel, probe = FakePanel(), FakeProbe()
    times = clock or [NOW]
    monkeypatch.setattr(rpc, "_make_panel", lambda target: panel)
    monkeypatch.setattr(rpc, "_make_probe", lambda controller: probe)
    monkeypatch.setattr(rpc, "_now", lambda: times[0])
    monkeypatch.setattr(rpc, "_today", lambda: "2026-10-02")
    c = rpc.RecordPromptController.alloc().initWithEngine_(engine)
    return c, panel, probe, times


def test_calendar_offer_records_the_linked_event(monkeypatch):
    engine = FakeEngine(events=[ev("k1"), ev("k2", NOW + timedelta(minutes=1), title="Tactical")])
    c, panel, probe, _ = make(monkeypatch, engine)
    assert probe.started
    c.tick_(None)
    assert panel.calls[-1] == ("show", "VFA Leads Sync Up", "Record", "Not Now", ("Tactical",))
    c.bannerMore_(Item(1))
    assert engine.begun == [MeetingOptions(calendar_event_key="k2")]
    assert panel.calls[-1] == ("confirm", "Recording")
    assert settings.get_prompted_events("2026-10-02") == {"k1", "k2"}
    c.shutdown()
    assert probe.stopped


def test_not_now_hides_and_is_remembered_across_relaunch(monkeypatch):
    engine = FakeEngine(events=[ev("k1")])
    c, panel, _, _ = make(monkeypatch, engine)
    c.tick_(None)
    c.bannerSecondary_(None)
    assert panel.calls[-1] == ("hide",) and engine.begun == []
    c.shutdown()
    c2, panel2, _, _ = make(monkeypatch, engine)
    c2.tick_(None)
    assert [x for x in panel2.calls if x[0] == "show"] == []
    c2.shutdown()


def test_calendar_offer_respects_setting(monkeypatch):
    settings.set_meeting_settings(offer_to_record=False)
    c, panel, _, _ = make(monkeypatch, FakeEngine(events=[ev("k1")]))
    c.tick_(None)
    assert [x for x in panel.calls if x[0] == "show"] == []
    c.shutdown()


def test_call_offer_records_with_engine_matching(monkeypatch):
    c, panel, _, times = make(monkeypatch, FakeEngine())
    c.callObserved_(True)
    times[0] = NOW + timedelta(seconds=10)
    c.callObserved_(True)
    assert panel.calls[-1][:2] == ("show", "Record this call?")
    c.bannerPrimary_(None)
    assert c.engine.begun == [MeetingOptions(calendar_event_key=None)]
    c.shutdown()


def test_call_ended_stop_ends_meeting(monkeypatch):
    engine = FakeEngine(state="meeting_recording")
    c, panel, _, times = make(monkeypatch, engine)
    c.callObserved_(True)
    times[0] = NOW + timedelta(seconds=10)
    c.callObserved_(True)
    times[0] = NOW + timedelta(seconds=30)
    c.callObserved_(False)
    times[0] = NOW + timedelta(seconds=90)
    c.callObserved_(False)
    assert panel.calls[-1] == ("show", "Call ended", "Stop", "Keep Recording", ())
    c.bannerPrimary_(None)
    assert engine.ended == 1 and panel.calls[-1] == ("hide",)
    c.shutdown()


def test_probe_wanted_follows_setting_and_state(monkeypatch):
    engine = FakeEngine()
    c, _, _, _ = make(monkeypatch, engine)
    assert c.probe_wanted is True
    c.engineStateChanged_("meeting_processing")
    assert c.probe_wanted is False
    c.engineStateChanged_("meeting_recording")
    assert c.probe_wanted is True
    settings.set_meeting_settings(detect_calls=False)
    c.tick_(None)
    assert c.probe_wanted is False
    c.shutdown()


def test_library_error_means_no_events(monkeypatch):
    import sqlite3

    engine = FakeEngine()

    def broken(start, end):
        raise sqlite3.OperationalError("locked")

    engine.library.calendar_events_overlapping = broken
    c, panel, _, _ = make(monkeypatch, engine)
    c.tick_(None)
    assert [x for x in panel.calls if x[0] == "show"] == []
    c.shutdown()


def test_tick_interval_is_thirty_seconds():
    assert rpc.TICK_SECONDS == 30.0


def test_countdown_text_is_refreshed_on_tick(monkeypatch):
    engine = FakeEngine(events=[ev("k1", NOW + timedelta(minutes=2))])
    c, panel, _, times = make(monkeypatch, engine)
    c.tick_(None)
    assert panel.subtitles[-1].startswith("Starts in 2 min")
    times[0] = NOW + timedelta(minutes=3)
    c.tick_(None)
    assert panel.subtitles[-1].startswith("Started 1 min ago")
    c.shutdown()


def _call_offer(c, times):
    c.callObserved_(True)
    times[0] = times[0] + timedelta(seconds=10)
    c.callObserved_(True)


def test_turning_call_detection_off_hides_call_offer(monkeypatch):
    c, panel, _, times = make(monkeypatch, FakeEngine())
    _call_offer(c, times)
    assert panel.calls[-1][:2] == ("show", "Record this call?")
    settings.set_meeting_settings(detect_calls=False)
    c.tick_(None)
    assert panel.calls[-1] == ("hide",)
    c.shutdown()


def test_turning_call_detection_off_hides_call_ended(monkeypatch):
    c, panel, _, times = make(monkeypatch, FakeEngine(state="meeting_recording"))
    _call_offer(c, times)
    for s in (60, 60):
        times[0] = times[0] + timedelta(seconds=s)
        c.callObserved_(False)
    assert panel.calls[-1][:2] == ("show", "Call ended")
    settings.set_meeting_settings(detect_calls=False)
    c.tick_(None)
    assert panel.calls[-1] == ("hide",)
    c.shutdown()


def test_call_observed_respects_detection_setting(monkeypatch):
    settings.set_meeting_settings(detect_calls=False)
    c, panel, _, times = make(monkeypatch, FakeEngine())
    _call_offer(c, times)
    assert [x for x in panel.calls if x[0] == "show"] == []
    c.shutdown()


def test_record_click_while_engine_not_ready_does_nothing(monkeypatch):
    engine = FakeEngine(events=[ev("k1")])
    c, panel, _, _ = make(monkeypatch, engine)
    c.tick_(None)
    engine.state = type("S", (), {"value": "paused"})()
    n = len(panel.calls)
    c.bannerPrimary_(None)
    c.bannerMore_(Item(0))
    assert engine.begun == [] and len(panel.calls) == n
    assert settings.get_prompted_events("2026-10-02") == set()
    c.shutdown()


def test_keep_recording_leaves_the_meeting_running(monkeypatch):
    c, panel, _, times = make(monkeypatch, FakeEngine(state="meeting_recording"))
    _call_offer(c, times)
    for s in (60, 60):
        times[0] = times[0] + timedelta(seconds=s)
        c.callObserved_(False)
    c.bannerSecondary_(None)
    assert panel.calls[-1] == ("hide",) and c.engine.ended == 0
    c.shutdown()


def test_more_menu_ignores_out_of_range_tags(monkeypatch):
    engine = FakeEngine(events=[ev("k1"), ev("k2", NOW + timedelta(minutes=1))])
    c, panel, _, _ = make(monkeypatch, engine)
    c.tick_(None)
    for tag in (0, 2, 99, -1):
        c.bannerMore_(Item(tag))
    assert engine.begun == []
    c.shutdown()


def test_not_now_on_a_call_offer_ignores_that_app(monkeypatch):
    c, panel, probe, times = make(monkeypatch, FakeEngine())
    c.callObserved_(True)
    times[0] = NOW + timedelta(seconds=10)
    c.callObserved_(True)
    c.bannerSecondary_(None)
    assert probe.ignored == 1
    c.shutdown()


def test_not_now_on_a_calendar_offer_ignores_nothing(monkeypatch):
    c, panel, probe, _ = make(monkeypatch, FakeEngine(events=[ev("k1")]))
    c.tick_(None)
    c.bannerSecondary_(None)
    assert probe.ignored == 0
    c.shutdown()


def test_starting_a_recording_forgives_waved_off_apps(monkeypatch):
    c, panel, probe, _ = make(monkeypatch, FakeEngine())
    before = probe.forgiven
    c.engineStateChanged_("ready")
    assert probe.forgiven == before
    c.engineStateChanged_("meeting_recording")
    assert probe.forgiven == before + 1
    c.shutdown()


def test_turn_off_calendar_prompts_from_the_banner_and_undo(monkeypatch):
    c, panel, _, _ = make(monkeypatch, FakeEngine(events=[ev("k1")]))
    c.tick_(None)
    assert panel.offs[-1] == "Turn off calendar prompts"
    c.bannerTurnOff_(None)
    assert settings.get_meeting_settings()["offer_to_record"] is False
    assert panel.calls[-1] == ("notice", "Calendar prompts are off",
                               "Turn back on in Meetings › Settings", "Undo")
    assert c.engine.begun == [] and c.engine.ended == 0
    assert c.coordinator.banner is None
    c.engineStateChanged_("ready")   # a re-render must keep the notice, not the offer
    assert panel.calls[-1][0] == "notice"
    c.bannerUndo_(None)
    assert settings.get_meeting_settings()["offer_to_record"] is True
    assert panel.calls[-1] == ("hide",)
    c.shutdown()


def test_turning_off_call_prompts_during_recording_never_ends_it(monkeypatch):
    engine = FakeEngine(state="meeting_recording")
    c, panel, _, times = make(monkeypatch, engine)
    c.callObserved_(True)
    times[0] = NOW + timedelta(seconds=15)
    c.callObserved_(True)
    times[0] = NOW + timedelta(seconds=20)
    c.callObserved_(False)
    times[0] = NOW + timedelta(seconds=90)
    c.callObserved_(False)
    assert panel.calls[-1][:2] == ("show", "Call ended") and panel.tones[-1] == "stop"
    assert panel.offs[-1] == "Turn off call prompts"
    c.bannerTurnOff_(None)
    assert settings.get_meeting_settings()["detect_calls"] is False
    assert panel.calls[-1][1] == "Call prompts are off"
    assert c.coordinator.banner is None
    c.engineStateChanged_("meeting_recording")   # re-render keeps the notice
    assert panel.calls[-1][0] == "notice"
    assert engine.ended == 0 and engine.begun == []
    assert c.probe_wanted is False
    c.shutdown()


def test_undo_after_the_notice_is_gone_does_nothing(monkeypatch):
    c, panel, _, _ = make(monkeypatch, FakeEngine(events=[ev("k1")]))
    c.tick_(None)
    c.bannerTurnOff_(None)
    c.noticeExpired()
    c.bannerUndo_(None)
    assert settings.get_meeting_settings()["offer_to_record"] is False
    c.shutdown()


def test_new_banner_during_notice_clears_the_pending_undo(monkeypatch):
    c, panel, _, times = make(monkeypatch, FakeEngine(events=[ev("k1")]))
    c.tick_(None)
    c.bannerTurnOff_(None)
    assert c._undo_kind == "calendar"
    c.callObserved_(True)
    times[0] = NOW + timedelta(seconds=15)
    c.callObserved_(True)
    assert panel.calls[-1][0] == "show"
    assert c._undo_kind is None
    c.bannerUndo_(None)
    assert settings.get_meeting_settings()["offer_to_record"] is False
    c.bannerSecondary_(None)
    assert panel.calls[-1] == ("hide",)
    c.shutdown()
