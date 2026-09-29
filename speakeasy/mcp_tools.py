"""The MCP server's tools: JSON views over MeetingLibrary for Claude.

Kept apart from the JSON-RPC loop (mcp_server.py) so every tool is unit
tested in-process. Results never include capture health or the other
capture fields: they describe the user's machine, not the meeting.
Tools raise ToolError for bad arguments (message shown to Claude so it can
fix the call); library errors are mapped to safe messages by the server.
"""

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from . import settings
from .meeting_library import HIT_CLOSE, HIT_OPEN, local_start, utc_iso
from .meetings import _ID_RE

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_MAX_CALENDAR_DAYS = 366


class ToolError(Exception):
    """A tool failure whose message is safe to show Claude."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    run: Callable[[dict], dict]
    read_only: bool = True

    def definition(self) -> dict:
        return {"name": self.name, "description": self.description,
                "inputSchema": self.input_schema,
                "annotations": {"readOnlyHint": self.read_only}}


# -- argument coercion -----------------------------------------------------
# Claude sometimes sends numbers as strings ("10"); accept those, clamp to
# the documented range, and reject anything else with a message that says
# how to fix the call.

def _int(args, key, default, lo, hi):
    value = args.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool):
        raise ToolError(f"{key} must be a whole number.")
    if isinstance(value, str) and re.fullmatch(r"\s*-?\d+\s*", value):
        value = int(value)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int):
        raise ToolError(f"{key} must be a whole number.")
    return max(lo, min(value, hi))


def _seconds(args, key):
    value = args.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ToolError(f"{key} must be a number of seconds.")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ToolError(f"{key} must be a number of seconds.") from None
    return max(0.0, value)


def _text(args, key, *, required=False, max_len=200):
    value = args.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ToolError(f"{key} is required.")
        return None
    if not isinstance(value, str):
        raise ToolError(f"{key} must be text.")
    if len(value) > max_len:
        raise ToolError(f"{key} is longer than {max_len} characters.")
    return value.strip()


def _date(args, key, *, required=False):
    value = _text(args, key, required=required, max_len=40)
    if value is None:
        return None
    try:
        if not _DATE_RE.fullmatch(value):
            raise ValueError
        date.fromisoformat(value)
    except ValueError:
        raise ToolError(f"{key} must be a date written YYYY-MM-DD.") from None
    return value


def _meeting_id(args):
    # Checked here (not only in the library) so Claude gets a ToolError that
    # says where valid ids come from, rather than a bare ValueError.
    value = _text(args, "id", required=True, max_len=64)
    if not _ID_RE.fullmatch(value):
        raise ToolError(f"Invalid meeting id: {value!r}. Use an id from "
                        "list_meetings or search_meetings.")
    return value


def _range(args, *, required=False):
    start, end = _date(args, "from", required=required), _date(args, "to", required=required)
    if start and end and start > end:
        raise ToolError("from must not be after to.")
    return start, end


def _str_list(args, key):
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ToolError(f"{key} must be a list of text items.")
    return value


# -- formatting --------------------------------------------------------------

def _start(started_at, tz_offset_minutes) -> str:
    return local_start(started_at, tz_offset_minutes).isoformat(timespec="minutes")


def _hms(seconds) -> str | None:
    if seconds is None:
        return None
    total = int(seconds)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _minutes(seconds) -> float:
    return round(seconds / 60, 1)


def _markdown(snippet: str) -> str:
    return snippet.replace(HIT_OPEN, "**").replace(HIT_CLOSE, "**")


def _event(e) -> dict:
    local = lambda iso: datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc).astimezone().isoformat(timespec="minutes")
    return {"id": e.event_key, "title": e.title, "calendar": e.calendar_name,
            "start": local(e.start_utc), "end": local(e.end_utc),
            "all_day": e.all_day, "declined": e.declined, "meeting_ids": e.meeting_ids,
            "people": [p.name for p in e.people]}


# -- "last used by Claude" ---------------------------------------------------

def record_use(path: Path | None = None, now: datetime | None = None) -> None:
    """Best effort: a stamp that can't be written must never fail a call."""
    try:
        path = Path(path) if path is not None else settings.mcp_last_used_path()
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(utc_iso(now or datetime.now(timezone.utc)))
        os.replace(tmp, path)
    except OSError:
        pass


def read_last_used(path: Path | None = None) -> datetime | None:
    try:
        path = Path(path) if path is not None else settings.mcp_last_used_path()
        return datetime.strptime(path.read_text().strip(), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except (OSError, ValueError):
        return None


# -- tools -------------------------------------------------------------------

_FILTERS = {
    "from": {"type": "string", "description": "First local day, YYYY-MM-DD."},
    "to": {"type": "string", "description": "Last local day (inclusive), YYYY-MM-DD."},
    "tag": {"type": "string"},
    "person": {"type": "string", "description": "Display name or email."},
}
_ID = {"id": {"type": "string", "description": "Meeting id from list_meetings or search_meetings."}}


def build_tools(library) -> dict[str, Tool]:
    def list_meetings(args):
        start, end = _range(args)
        limit = _int(args, "limit", 20, 1, 100)
        offset = _int(args, "offset", 0, 0, 1_000_000)
        rows = library.list_meetings(from_date=start, to_date=end,
                                     tag=_text(args, "tag"), person=_text(args, "person"),
                                     limit=limit + 1, offset=offset)
        return {
            "meetings": [{
                "id": m.meeting_id, "title": m.title,
                "start": _start(m.started_at, m.tz_offset_minutes),
                "duration_minutes": _minutes(m.duration_seconds),
                "speakers": m.speakers, "people": m.people, "tags": m.tags,
                "has_summary": m.has_summary,
                "timestamps_approximate": m.timestamps_approximate,
            } for m in rows[:limit]],
            "offset": offset,
            "next_offset": offset + limit if len(rows) > limit else None,
        }

    def get_meeting(args):
        m = library.get_meeting(_meeting_id(args), with_segments=False)
        notes = None
        if m.notes is not None:
            notes = {"summary": m.notes.summary, "action_items": m.notes.action_items,
                     "updated_at": m.notes.updated_at, "updated_by": m.notes.updated_by}
        event = library.calendar_event(m.calendar_event_id)
        return {
            "id": m.meeting_id, "title": m.title,
            "start": _start(m.started_at, m.tz_offset_minutes),
            "duration_minutes": _minutes(m.duration_seconds),
            "speakers": m.speakers,
            "segment_count": m.segment_count, "people": m.people, "tags": m.tags,
            "calendar_event": _event(event) if event else None, "notes": notes,
            "timestamps_approximate": m.timestamps_approximate, "source": m.source,
        }

    def search_meetings(args):
        query = _text(args, "query", required=True, max_len=500)
        start, end = _range(args)
        hits = library.search(query, from_date=start, to_date=end,
                              tag=_text(args, "tag"), person=_text(args, "person"),
                              limit=_int(args, "limit", 10, 1, 50))
        return {"results": [{
            "meeting_id": h.meeting_id, "title": h.title,
            "meeting_start": _start(h.started_at, h.tz_offset_minutes),
            "kind": h.kind, "speaker": h.speaker, "also_speakers": h.also_speakers,
            "at": _hms(h.start_seconds), "start_seconds": h.start_seconds,
            "snippet": _markdown(h.snippet),
        } for h in hits]}

    def get_transcript(args):
        meeting_id = _meeting_id(args)
        cursor = _int(args, "cursor", 0, 0, 10_000_000)
        page = library.transcript_page(
            meeting_id, start_seconds=_seconds(args, "start_seconds"),
            end_seconds=_seconds(args, "end_seconds"), cursor=cursor,
            max_chars=_int(args, "max_chars", 20_000, 1_000, 60_000))
        return {
            "id": meeting_id, "title": page.title,
            "text": "\n".join(f"[{_hms(seg.start)}] {seg.speaker}: {seg.text}"
                              for _, seg in page.segments),
            "next_cursor": page.next_cursor,
            "timestamps_approximate": page.timestamps_approximate,
        }

    def get_calendar(args):
        start, end = _range(args, required=True)
        if (date.fromisoformat(end) - date.fromisoformat(start)).days > _MAX_CALENDAR_DAYS:
            raise ToolError(f"Ask for at most {_MAX_CALENDAR_DAYS} days at a time.")
        return {"events": [_event(e) for e in library.calendar_events_between(start, end)]}

    def list_tags(args):
        return {"tags": [{"name": n, "count": c} for n, c in library.list_tags()]}

    def list_people(args):
        return {"people": [{"name": n, "count": c}
                           for n, c in library.list_people(_text(args, "query"))]}

    def save_notes(args):
        meeting_id = _meeting_id(args)
        summary = args.get("summary")
        if summary is not None and not isinstance(summary, str):
            raise ToolError("summary must be text.")
        action_items, tags = _str_list(args, "action_items"), _str_list(args, "tags")
        if summary is None and action_items is None and tags is None:
            raise ToolError("Give at least one of summary, action_items or tags.")
        notes = library.save_notes(meeting_id, summary=summary, action_items=action_items,
                                   tags=tags, updated_by="claude")
        return {"id": meeting_id, "summary": notes.summary,
                "action_items": notes.action_items,
                "tags": library.meeting_tags(meeting_id),
                "updated_at": notes.updated_at, "updated_by": notes.updated_by}

    specs = [
        ("list_meetings",
         "List saved meetings, newest first, with date, duration, speakers, tags "
         "and whether a summary exists. No transcript text.",
         {**_FILTERS,
          "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
          "offset": {"type": "integer", "minimum": 0, "default": 0}}, [], list_meetings, True),
        ("get_meeting",
         "Get one meeting's details, notes (summary, action items), tags, people and "
         "calendar event. No transcript: use get_transcript for that.",
         _ID, ["id"], get_meeting, True),
        ("search_meetings",
         "Full-text search across transcripts and notes. Returns ranked snippets "
         "(matches in **bold**) with the meeting id and time offset.",
         {"query": {"type": "string"}, **_FILTERS,
          "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}},
         ["query"], search_meetings, True),
        ("get_transcript",
         "Read part of a meeting transcript as lines '[hh:mm:ss] Speaker: text'. "
         "Narrow with start_seconds/end_seconds; pass next_cursor as cursor to continue.",
         {**_ID, "start_seconds": {"type": "number", "minimum": 0},
          "end_seconds": {"type": "number", "minimum": 0},
          "cursor": {"type": "integer", "minimum": 0},
          "max_chars": {"type": "integer", "minimum": 1000, "maximum": 60000,
                        "default": 20000}},
         ["id"], get_transcript, True),
        ("get_calendar",
         "Calendar events (from the Mac's Calendar app, cached by Speakeasy) between "
         "two local days, with the ids of meetings recorded for them.",
         {"from": _FILTERS["from"], "to": _FILTERS["to"]}, ["from", "to"], get_calendar, True),
        ("list_tags", "All meeting tags with how many meetings use each.",
         {}, [], list_tags, True),
        ("list_people", "People linked to meetings, with meeting counts.",
         {"query": {"type": "string", "description": "Part of a name or email."}},
         [], list_people, True),
        ("save_notes",
         "Save a meeting's summary, action items and/or tags. Only the fields you "
         "give are replaced; tags replace the meeting's whole tag list.",
         {**_ID, "summary": {"type": "string", "maxLength": 20000},
          "action_items": {"type": "array", "items": {"type": "string"}, "maxItems": 50},
          "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 20}},
         ["id"], save_notes, False),
    ]
    return {
        name: Tool(name, description,
                   {"type": "object", "properties": props, "required": required,
                    "additionalProperties": False} if required
                   else {"type": "object", "properties": props, "additionalProperties": False},
                   run, read_only)
        for name, description, props, required, run, read_only in specs
    }
