"""Which calendar event a recording belongs to, which events to offer to
record, and how many voices an event implies. Pure: it works on the cached
CalendarEvent rows and never on EventKit, so the control thread can call it."""

from datetime import datetime, timedelta, timezone

from .meeting_library import CalendarEvent

# People join a few minutes early; a recording started up to this long before
# an event still belongs to it.
MATCH_EARLY_JOIN = timedelta(minutes=10)
# Record-prompt window (phase 4): from 2 min before the start to 5 min after.
PROMPT_BEFORE = timedelta(minutes=2)
PROMPT_AFTER = timedelta(minutes=5)
# MeetingOptions' own limit on the expected speaker count.
MAX_SPEAKERS = 20

_FMT = "%Y-%m-%dT%H:%M:%SZ"


def event_start(e: CalendarEvent) -> datetime:
    return datetime.strptime(e.start_utc, _FMT).replace(tzinfo=timezone.utc)


def event_end(e: CalendarEvent) -> datetime:
    return datetime.strptime(e.end_utc, _FMT).replace(tzinfo=timezone.utc)


def _eligible(e: CalendarEvent) -> bool:
    return not e.all_day and not e.declined


def pick_event(events, recording_start: datetime) -> CalendarEvent | None:
    """Candidates: not all-day, not declined, start - 10 min <= recording
    start < end. The closest start wins; ties go to the shorter event, then
    the key, so the choice never depends on row order."""
    if recording_start.tzinfo is None:
        raise ValueError("recording_start must be timezone-aware")
    best, best_rank = None, None
    for e in events:
        if not _eligible(e):
            continue
        start, end = event_start(e), event_end(e)
        if not start - MATCH_EARLY_JOIN <= recording_start < end:
            continue
        rank = (abs((start - recording_start).total_seconds()),
                (end - start).total_seconds(), e.event_key)
        if best_rank is None or rank < best_rank:
            best, best_rank = e, rank
    return best


def prompt_candidates(events, now: datetime, prompted: set[str]) -> list[CalendarEvent]:
    """Events to offer to record now, oldest start first. The caller checks
    the setting and that no meeting is in progress."""
    return sorted(
        (e for e in events
         if _eligible(e) and e.event_key not in prompted
         and event_start(e) - PROMPT_BEFORE <= now <= event_start(e) + PROMPT_AFTER),
        key=lambda e: (e.start_utc, e.event_key),
    )


def remote_speaker_cap(event: CalendarEvent | None, capture_mode: str) -> int | None:
    """Most voices diarization should report for a meeting linked to `event`.

    Dual-track mode diarizes only the system track, which carries the other
    people; mic-only fallback hears the user as well. It is an upper bound,
    not an exact count, because invitees often stay silent. None when the
    invite list doesn't say (no other people, or a distribution list)."""
    if event is None or not event.other_attendees or event.other_attendees < 1:
        return None
    voices = event.other_attendees + (0 if capture_mode == "mic_and_system" else 1)
    return min(voices, MAX_SPEAKERS)
