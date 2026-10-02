from datetime import datetime, timedelta, timezone

import pytest

from speakeasy import record_prompt as rp
from speakeasy.meeting_library import CalendarEvent, EventPerson

UTC = timezone.utc
T0 = datetime(2026, 10, 2, 15, 0, tzinfo=UTC)
FMT = "%Y-%m-%dT%H:%M:%SZ"


def ev(key, start=T0, minutes=30, people=1, title=None):
    return CalendarEvent(
        key, "Work", title or key, start.strftime(FMT),
        (start + timedelta(minutes=minutes)).strftime(FMT), False, False, [],
        other_attendees=people or None,
        people=[EventPerson(f"P{i}", f"p{i}@example.test", "attendee") for i in range(people)])


def at(minutes=0, seconds=0):
    return T0 + timedelta(minutes=minutes, seconds=seconds)


def coord(state="ready", prompted=()):
    c = rp.RecordPromptCoordinator(set(prompted))
    c.engine_state(state)
    return c


def observe(c, using, t, events=(), detect=True):
    c.call_observed(using, t, list(events), detect_enabled=detect)


# -- calendar offers --------------------------------------------------------

def test_calendar_offer_needs_people_and_prefers_nearest():
    c = coord()
    c.calendar_tick([ev("solo", people=0), ev("far", at(-4)), ev("near", at(1))],
                    at(0), offer_enabled=True)
    assert isinstance(c.banner, rp.Offer) and c.banner.kind == "calendar"
    assert [e.event_key for e in c.banner.events] == ["near", "far"]


def test_calendar_offer_off_or_not_ready_shows_nothing():
    c = coord()
    c.calendar_tick([ev("a")], at(0), offer_enabled=False)
    assert c.banner is None
    for state in ("recording", "transcribing", "paused", "meeting_recording", "loading"):
        c = coord(state)
        c.calendar_tick([ev("a")], at(0), offer_enabled=True)
        assert c.banner is None, state


def test_not_now_marks_every_offered_event_prompted():
    events = [ev("a"), ev("b", at(1))]
    c = coord()
    c.calendar_tick(events, at(0), offer_enabled=True)
    c.not_now()
    assert c.banner is None and c.prompted == {"a", "b"}
    c.calendar_tick(events, at(1), offer_enabled=True)
    assert c.banner is None


def test_offer_expires_after_five_minutes():
    c = coord()
    c.calendar_tick([ev("a")], at(0), offer_enabled=True)
    c.calendar_tick([ev("a")], at(4, 59), offer_enabled=True)
    assert c.banner is not None
    c.calendar_tick([ev("a")], at(5), offer_enabled=True)
    assert c.banner is None and "a" in c.prompted


def test_record_returns_chosen_key_and_closes():
    c = coord()
    c.calendar_tick([ev("a"), ev("b", at(1))], at(0), offer_enabled=True)
    assert c.record(1) == "b"
    assert c.banner is None and c.prompted == {"a", "b"}


def test_record_without_offer_is_an_error():
    with pytest.raises(RuntimeError):
        coord().record()


def test_starting_a_meeting_closes_the_offer():
    c = coord()
    c.calendar_tick([ev("a")], at(0), offer_enabled=True)
    c.engine_state("meeting_recording")
    assert c.banner is None and "a" in c.prompted


def test_turning_calendar_offers_off_hides_without_marking():
    c = coord()
    c.calendar_tick([ev("a")], at(0), offer_enabled=True)
    c.calendar_tick([ev("a")], at(0, 30), offer_enabled=False)
    assert c.banner is None and "a" not in c.prompted


# -- call offers ------------------------------------------------------------

def test_call_offer_after_ten_seconds_of_other_app_mic():
    c = coord()
    observe(c, True, at(0))
    observe(c, True, at(0, 5))
    assert c.banner is None
    observe(c, True, at(0, 10))
    assert c.banner.kind == "call" and c.banner.events == ()


def test_brief_mic_use_never_offers():
    c = coord()
    observe(c, True, at(0))
    observe(c, False, at(0, 5))
    observe(c, True, at(0, 6))
    observe(c, True, at(0, 15))
    assert c.banner is None


def test_call_offer_links_matching_event_with_people():
    c = coord()
    observe(c, True, at(0), [ev("solo", at(-3), people=0), ev("a", at(-3))])
    observe(c, True, at(0, 10), [ev("solo", at(-3), people=0), ev("a", at(-3))])
    assert [e.event_key for e in c.banner.events] == ["a"]


def test_call_for_an_already_prompted_event_is_not_offered():
    c = coord(prompted={"a"})
    observe(c, True, at(0), [ev("a", at(-3))])
    observe(c, True, at(0, 10), [ev("a", at(-3))])
    assert c.banner is None


def test_one_call_offer_per_call():
    c = coord()
    observe(c, True, at(0))
    observe(c, True, at(0, 10))
    c.not_now()
    observe(c, True, at(1))
    observe(c, True, at(3))
    assert c.banner is None
    observe(c, False, at(4))
    observe(c, False, at(5))          # free for 60 s: that call is over
    observe(c, True, at(5, 1))
    observe(c, True, at(5, 11))
    assert c.banner.kind == "call"


def test_call_offer_hides_when_the_call_ends():
    c = coord()
    observe(c, True, at(0))
    observe(c, True, at(0, 10))
    observe(c, False, at(0, 20))
    assert c.banner is not None
    observe(c, False, at(1, 20))
    assert c.banner is None


def test_call_detection_off_never_offers_and_hides():
    c = coord()
    observe(c, True, at(0), detect=False)
    observe(c, True, at(0, 10), detect=False)
    assert c.banner is None
    observe(c, True, at(0, 20))
    observe(c, True, at(0, 30))
    assert c.banner is not None
    observe(c, True, at(0, 35), detect=False)
    assert c.banner is None


def test_not_ready_never_offers_call():
    c = coord("recording")              # a dictation take holds our own mic
    observe(c, True, at(0))
    observe(c, True, at(0, 10))
    assert c.banner is None


def test_calendar_offer_is_not_replaced_by_call_offer():
    c = coord()
    c.calendar_tick([ev("a")], at(0), offer_enabled=True)
    observe(c, True, at(0))
    observe(c, True, at(0, 10))
    assert c.banner.kind == "calendar"


# -- call ended ---------------------------------------------------------------

def test_call_ended_after_sixty_seconds_free():
    c = coord("meeting_recording")
    observe(c, True, at(0))
    observe(c, True, at(0, 10))
    observe(c, False, at(1))
    observe(c, False, at(1, 59))
    assert c.banner is None
    observe(c, False, at(2))
    assert isinstance(c.banner, rp.CallEnded)


def test_no_call_ended_without_a_call():
    c = coord("meeting_recording")
    observe(c, False, at(0))
    observe(c, False, at(5))
    assert c.banner is None


def test_mic_resuming_hides_call_ended():
    c = coord("meeting_recording")
    observe(c, True, at(0))
    observe(c, True, at(0, 10))
    observe(c, False, at(1))
    observe(c, False, at(2))
    observe(c, True, at(2, 5))
    assert c.banner is None


def test_keep_recording_waits_for_the_next_call_end():
    c = coord("meeting_recording")
    observe(c, True, at(0))
    observe(c, True, at(0, 10))
    observe(c, False, at(1))
    observe(c, False, at(2))
    c.keep()
    observe(c, False, at(3))
    assert c.banner is None
    observe(c, True, at(4))
    observe(c, True, at(4, 10))
    observe(c, False, at(4, 11))
    observe(c, False, at(5, 11))
    assert isinstance(c.banner, rp.CallEnded)


def test_stop_clears_call_ended():
    c = coord("meeting_recording")
    observe(c, True, at(0))
    observe(c, True, at(0, 10))
    observe(c, False, at(1))
    observe(c, False, at(2))
    c.stop()
    assert c.banner is None


def test_call_ended_needs_call_detection_on():
    c = coord("meeting_recording")
    observe(c, True, at(0), detect=False)
    observe(c, False, at(1), detect=False)
    observe(c, False, at(2), detect=False)
    assert c.banner is None


def test_leaving_recording_resets_call_tracking():
    c = coord("meeting_recording")
    observe(c, True, at(0))
    observe(c, True, at(0, 10))
    observe(c, False, at(1))
    observe(c, False, at(2))
    c.engine_state("meeting_processing")
    assert c.banner is None
    c.engine_state("meeting_recording")
    observe(c, False, at(10))
    assert c.banner is None


def test_a_recorded_call_is_not_offered_after_recording():
    c = coord("meeting_recording")
    observe(c, True, at(0))
    observe(c, True, at(0, 10))
    c.engine_state("meeting_processing")
    c.engine_state("ready")
    observe(c, True, at(1))
    observe(c, True, at(1, 30))
    assert c.banner is None


def test_short_mic_blip_while_recording_is_not_a_call():
    c = coord("meeting_recording")
    observe(c, True, at(0))
    observe(c, False, at(0, 5))
    observe(c, False, at(1, 10))
    assert c.banner is None


def test_ten_seconds_of_mic_use_while_recording_is_a_call():
    c = coord("meeting_recording")
    observe(c, True, at(0))
    observe(c, True, at(0, 10))
    observe(c, False, at(1))
    observe(c, False, at(2))
    assert isinstance(c.banner, rp.CallEnded)


def test_calls_disabled_drops_call_banners_only():
    c = coord()
    observe(c, True, at(0))
    observe(c, True, at(0, 10))
    assert c.banner.kind == "call"
    c.calls_disabled()
    assert c.banner is None and c.prompted == set()
    c = coord()
    c.calendar_tick([ev("a", T0)], T0, offer_enabled=True)
    c.calls_disabled()
    assert c.banner.kind == "calendar"
    c = coord("meeting_recording")
    observe(c, True, at(0))
    observe(c, True, at(0, 10))
    observe(c, False, at(1))
    observe(c, False, at(2))
    assert isinstance(c.banner, rp.CallEnded)
    c.calls_disabled()
    assert c.banner is None


# -- text -------------------------------------------------------------------

def test_banner_text():
    cal = rp.Offer("calendar", (ev("a", at(1), people=3, title="VFA Leads Sync Up"),
                               ev("b", title="Weekly Tactical")), at(0))
    text = rp.banner_text(cal, at(0))
    assert (text.title, text.subtitle) == ("VFA Leads Sync Up", "Starts in 1 min · 3 invited")
    assert (text.primary, text.secondary, text.more) == ("Record", "Not Now", ("Weekly Tactical",))
    assert rp.banner_text(cal, at(1)).subtitle.startswith("Starting now")
    assert rp.banner_text(cal, at(4)).subtitle.startswith("Started 3 min ago")
    call = rp.banner_text(rp.Offer("call", (), at(0)), at(0))
    assert (call.title, call.subtitle) == ("Record this call?", "Another app is using the microphone")
    linked = rp.banner_text(rp.Offer("call", (ev("a", title="SSO follow up"),), at(0)), at(0))
    assert linked.title == "SSO follow up" and linked.primary == "Record"
    ended = rp.banner_text(rp.CallEnded(), at(0))
    assert (ended.title, ended.subtitle, ended.primary, ended.secondary) == (
        "Call ended", "Stop recording?", "Stop", "Keep Recording")
