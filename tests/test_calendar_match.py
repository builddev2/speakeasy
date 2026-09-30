from datetime import datetime, timedelta, timezone

import pytest

from speakeasy import calendar_match as cm
from speakeasy.meeting_library import CalendarEvent

UTC = timezone.utc
NINE = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)


def e(key, start, minutes=30, *, all_day=False, declined=False, other=None):
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return CalendarEvent(key, "Work", key, start.strftime(fmt),
                         (start + timedelta(minutes=minutes)).strftime(fmt),
                         all_day, declined, [], other_attendees=other)


def at(minutes, seconds=0):
    return NINE + timedelta(minutes=minutes, seconds=seconds)


def test_recording_inside_event_matches():
    assert cm.pick_event([e("a", NINE)], at(12)).event_key == "a"


def test_early_join_boundary_is_inclusive_at_ten_minutes():
    events = [e("a", NINE)]
    assert cm.pick_event(events, at(-10)).event_key == "a"
    assert cm.pick_event(events, at(-10, -1)) is None


def test_event_end_is_exclusive():
    assert cm.pick_event([e("a", NINE)], at(30)) is None


def test_back_to_back_prefers_closest_start():
    events = [e("first", NINE), e("second", at(30))]
    assert cm.pick_event(events, at(15)).event_key == "first"
    # Joining the next call a minute early: 1 min from its start beats 29.
    assert cm.pick_event(events, at(29)).event_key == "second"


def test_declined_and_all_day_never_match():
    events = [e("d", NINE, declined=True), e("ad", NINE, 24 * 60, all_day=True)]
    assert cm.pick_event(events, at(5)) is None


def test_tie_goes_to_shorter_event_then_key():
    assert cm.pick_event([e("long", NINE, 60), e("short", NINE, 30)], at(0)).event_key == "short"
    assert cm.pick_event([e("b", NINE), e("a", NINE)], at(0)).event_key == "a"


def test_naive_time_is_rejected():
    with pytest.raises(ValueError):
        cm.pick_event([], datetime(2026, 9, 29, 9, 0))


def test_prompt_window_and_prompted_set():
    events = [e("a", NINE), e("b", at(3))]
    assert [x.event_key for x in cm.prompt_candidates(events, at(-2), set())] == ["a"]
    assert [x.event_key for x in cm.prompt_candidates(events, at(5), set())] == ["a", "b"]
    assert [x.event_key for x in cm.prompt_candidates(events, at(5, 1), set())] == ["b"]
    assert cm.prompt_candidates(events, at(5), {"a", "b"}) == []
    assert cm.prompt_candidates([e("d", NINE, declined=True)], at(0), set()) == []


def test_remote_speaker_cap():
    one = e("a", NINE, other=1)
    assert cm.remote_speaker_cap(one, "mic_and_system") == 1   # 1-on-1: just them
    assert cm.remote_speaker_cap(one, "mic_only") == 2         # mic hears you too
    assert cm.remote_speaker_cap(e("a", NINE, other=None), "mic_and_system") is None
    assert cm.remote_speaker_cap(e("a", NINE, other=0), "mic_and_system") is None
    assert cm.remote_speaker_cap(e("a", NINE, other=30), "mic_only") == 20
    assert cm.remote_speaker_cap(None, "mic_only") is None
