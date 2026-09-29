"""Pure shaping of cached calendar events for the pages (shapes =
frontend/src/mock/meetings.ts). `now` carries the display time zone."""

from datetime import datetime, timedelta

from ..calendar_match import PROMPT_BEFORE, event_end, event_start, remote_speaker_cap
from ..calendar_sync import CalendarInfo


def _clock(dt: datetime) -> str:
    return dt.strftime("%-I:%M %p")


def _visible(e) -> bool:
    # Agenda rows are meetings the user attends; all-day items and declined
    # invitations are not.
    return not e.all_day and not e.declined


def attendee_count(e) -> int:
    return e.other_attendees + 1 if e.other_attendees is not None else len(e.people)


def agenda_row(e, now: datetime, recording_key: str | None) -> dict:
    start, end = event_start(e).astimezone(now.tzinfo), event_end(e).astimezone(now.tzinfo)
    if recording_key == e.event_key:
        status = "recording"
    elif e.meeting_ids:
        status = "recorded"
    elif start - PROMPT_BEFORE <= now < end:
        status = "record"
    else:
        status = "none"
    return {"key": e.event_key, "time": _clock(start), "endTime": _clock(end),
            "title": e.title, "attendeeCount": attendee_count(e), "status": status,
            "meetingId": e.meeting_ids[-1] if e.meeting_ids else None}


def agenda_payload(events, now: datetime, recording_key: str | None) -> list[dict]:
    return [agenda_row(e, now, recording_key)
            for e in sorted(filter(_visible, events), key=lambda e: (e.start_utc, e.event_key))]


def upcoming_payload(events, now: datetime, days: int = 7) -> list[dict]:
    today = now.date()
    by_day: dict = {}
    for e in sorted(filter(_visible, events), key=lambda e: (e.start_utc, e.event_key)):
        day = event_start(e).astimezone(now.tzinfo).date()
        if today < day <= today + timedelta(days=days):
            by_day.setdefault(day, []).append(agenda_row(e, now, None))
    return [{"dayLabel": day.strftime("%a %-d %b"), "events": rows}
            for day, rows in sorted(by_day.items())]


def event_chip(e, tz) -> dict:
    return {"key": e.event_key, "title": e.title, "time": _clock(event_start(e).astimezone(tz))}


def day_chips(events, tz) -> list[dict]:
    return [event_chip(e, tz) for e in sorted(filter(_visible, events),
                                              key=lambda e: (e.start_utc, e.event_key))]


def linked_event_payload(event, capture_mode: str, tz=None) -> dict | None:
    if event is None:
        return None
    return {**event_chip(event, tz or datetime.now().astimezone().tzinfo),
            "speakerHint": remote_speaker_cap(event, capture_mode)}


def calendars_payload(calendars: list[CalendarInfo], choices: dict) -> list[dict]:
    accounts: dict[str, list[dict]] = {}
    for c in calendars:
        accounts.setdefault(c.account, []).append(
            {"id": c.id, "name": c.title, "enabled": choices.get(c.id, c.default_enabled)})
    return [{"name": name, "calendars": sorted(cals, key=lambda c: c["name"].lower())}
            for name, cals in sorted(accounts.items(), key=lambda kv: kv[0].lower())]
