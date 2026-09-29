from datetime import datetime, timedelta, timezone

from speakeasy.calendar_sync import CalendarInfo
from speakeasy.meeting_library import CalendarEvent, EventPerson
from speakeasy.ui import calendar_payloads as cp

LOCAL = timezone(timedelta(hours=-7))           # PDT
NOW = datetime(2026, 9, 29, 16, 29, tzinfo=LOCAL)


def e(key, hh, mm, minutes=30, *, ids=(), other=1, people=1, all_day=False, declined=False):
    start = datetime(2026, 9, 29, hh, mm, tzinfo=LOCAL).astimezone(timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return CalendarEvent(key, "Work", key.title(), start.strftime(fmt),
                         (start + timedelta(minutes=minutes)).strftime(fmt), all_day, declined,
                         list(ids), other_attendees=other,
                         people=[EventPerson(f"P{i}", None, "attendee") for i in range(people)])


def test_agenda_statuses_and_times():
    events = [e("done", 9, 0, ids=["m1"]), e("live", 15, 30, 60), e("soon", 16, 30),
              e("later", 17, 0), e("gone", 11, 0), e("allday", 0, 0, 1440, all_day=True),
              e("no", 12, 0, declined=True)]
    rows = cp.agenda_payload(events, NOW, recording_key="live")
    assert [(r["key"], r["status"]) for r in rows] == [
        ("done", "recorded"), ("gone", "none"), ("live", "recording"),
        ("soon", "record"), ("later", "none")]
    assert rows[0]["time"] == "9:00 AM" and rows[0]["endTime"] == "9:30 AM"
    assert rows[0]["meetingId"] == "m1" and rows[0]["attendeeCount"] == 2


def test_attendee_count_falls_back_to_people():
    assert cp.agenda_payload([e("x", 17, 0, other=None, people=3)], NOW, None)[0]["attendeeCount"] == 3
    assert cp.agenda_payload([e("x", 17, 0, other=None, people=0)], NOW, None)[0]["attendeeCount"] == 0


def test_upcoming_groups_by_local_day_and_skips_empty_days():
    from dataclasses import replace
    base = e("t", 9, 0)
    tomorrow = replace(base, start_utc="2026-09-30T16:00:00Z", end_utc="2026-09-30T16:30:00Z")
    late = replace(base, event_key="late",
                   start_utc="2026-10-01T06:30:00Z", end_utc="2026-10-01T07:00:00Z")
    today = e("today", 17, 0)
    days = cp.upcoming_payload([today, tomorrow, late], NOW)
    # 06:30Z on 1 Oct is 11:30 PM on 30 Sep in PDT: same local day. Today's
    # event belongs to the agenda, not Upcoming.
    assert [(d["dayLabel"], [x["key"] for x in d["events"]]) for d in days] == [
        ("Wed 30 Sep", ["t", "late"])]


def test_linked_event_payload():
    assert cp.linked_event_payload(None, "mic_only", LOCAL) is None
    out = cp.linked_event_payload(e("x", 16, 0, other=1), "mic_and_system", LOCAL)
    assert out == {"key": "x", "title": "X", "time": "4:00 PM", "speakerHint": 1}


def test_calendars_payload_groups_accounts_and_applies_defaults():
    cals = [CalendarInfo("w", "Calendar", "Exchange", 2),
            CalendarInfo("h", "US Holidays", "Other", 3),
            CalendarInfo("b", "Birthdays", "Other", 4)]
    out = cp.calendars_payload(cals, {"h": True})
    assert out == [
        {"name": "Exchange", "calendars": [{"id": "w", "name": "Calendar", "enabled": True}]},
        {"name": "Other", "calendars": [{"id": "b", "name": "Birthdays", "enabled": False},
                                        {"id": "h", "name": "US Holidays", "enabled": True}]}]
