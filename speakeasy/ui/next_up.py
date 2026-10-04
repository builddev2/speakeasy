"""The menu's Next-up card: the event in progress, else the next one today."""

from ..calendar_match import event_end, event_start
from .calendar_payloads import _visible, attendee_count


def _people(n: int) -> str:
    return "1 person" if n == 1 else f"{n} people"


def next_up(events, now, recording_key):
    candidates = []
    for e in filter(_visible, events):
        start, end = event_start(e).astimezone(now.tzinfo), event_end(e).astimezone(now.tzinfo)
        if end <= now or e.meeting_ids or e.event_key == recording_key or (start > now and start.date() != now.date()):
            continue
        candidates.append((start, end, e))
    if not candidates:
        return None
    start, end, e = min(candidates, key=lambda c: (c[0], c[2].event_key))
    minutes = max(1, round((start - now).total_seconds() / 60))
    if start <= now:
        when = "Now"
    elif minutes < 60:
        when = f"Starts in {minutes} min"
    else:
        when = "at " + start.strftime("%-I:%M %p")
    return {"key": e.event_key, "title": e.title, "subtitle": f"{when} · {_people(attendee_count(e))}"}
