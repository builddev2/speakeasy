from datetime import datetime, timedelta, timezone

from speakeasy.meeting_library import CalendarEvent, EventPerson
from speakeasy.ui.next_up import next_up

UTC = timezone.utc
NOW = datetime(2026, 10, 3, 15, 0, tzinfo=UTC)
FMT = "%Y-%m-%dT%H:%M:%SZ"


def ev(key, start, minutes=30, people=2, recorded=False, title=None, all_day=False):
    e = CalendarEvent(key, "Work", title or key, start.strftime(FMT),
                      (start + timedelta(minutes=minutes)).strftime(FMT), all_day, False, [],
                      other_attendees=people - 1 if people else None,
                      people=[EventPerson("A", None, "attendee")] * people)
    if recorded:
        e.meeting_ids.append("m-1")
    return e


def test_in_progress_event_is_now():
    got = next_up([ev("a", NOW - timedelta(minutes=5))], NOW, None)
    assert got == {"key": "a", "title": "a", "subtitle": "Now · 2 people"}


def test_next_event_within_an_hour_counts_minutes():
    got = next_up([ev("b", NOW + timedelta(minutes=4), people=1)], NOW, None)
    assert got["subtitle"] == "Starts in 4 min · 1 person"


def test_later_event_shows_clock_time():
    got = next_up([ev("c", NOW + timedelta(hours=2))], NOW, None)
    assert got["subtitle"].startswith("at ")


def test_skips_recorded_finished_recording_and_all_day():
    events = [ev("done", NOW - timedelta(hours=2)), ev("rec", NOW - timedelta(minutes=1), recorded=True),
              ev("live", NOW - timedelta(minutes=1)), ev("allday", NOW, all_day=True),
              ev("next", NOW + timedelta(minutes=30))]
    assert next_up(events, NOW, "live")["key"] == "next"


def test_nothing_left_is_none():
    assert next_up([ev("done", NOW - timedelta(hours=2))], NOW, None) is None
