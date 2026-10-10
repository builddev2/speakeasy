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

from . import action_items as ai, settings
from .action_item_store import ActionItemNotFound
from .meeting_library import HIT_CLOSE, HIT_OPEN, MeetingNotFound, local_start, utc_iso
from .meetings import _ID_RE
from .summary_format import summary_instructions
from .tag_names import tag_slug

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Diarization's placeholder labels ("Speaker 12"); same pattern as
# voice_profiles._ANONYMOUS_RE. Listing them made a 100-row page ~74k chars.
_ANONYMOUS_SPEAKER_RE = re.compile(r"Speaker [1-9][0-9]*", re.IGNORECASE)
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


def _bool(args, key, default=False):
    value = args.get(key, default)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ToolError(f"{key} must be true or false.")
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


_SORTS = ("due", "priority", "meeting", "created")
_PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}


def _item_id(args):
    value = args.get("id")
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    try:
        return ai.check_id(value)
    except ValueError as err:
        raise ToolError(f"id: {err}") from None


def _item_json(item, identity) -> dict:
    tags = sorted({t.casefold(): t for t in [*item.meeting_tags, *item.tags]}.values(), key=str.casefold)
    return {
        "id": item.id, "task": item.task, "owner": item.owner or "Unassigned",
        "mine": ai.is_mine(item.owner, identity), "priority": item.priority,
        "status": item.status, "due": item.effective_due, "due_source": item.due_source,
        **({"due_phrase": item.due_phrase} if item.due_phrase else {}),
        **({"notes": item.notes} if item.notes else {}),
        "tags": tags,
        "meeting": None if item.meeting_id is None else {
            "id": item.meeting_id, "title": item.meeting_title,
            "start": _start(item.meeting_started_at, item.meeting_tz_offset_minutes)},
        **({"completed_at": item.completed_at} if item.completed_at else {}),
    }


def _sorted(items, sort):
    def due_key(i):
        return (i.effective_due is None, i.effective_due or "")
    if sort == "meeting":     # newest meeting first; items without a meeting last
        dated = sorted((i for i in items if i.meeting_started_at),
                       key=lambda i: (i.meeting_started_at, -i.position), reverse=True)
        return dated + [i for i in items if not i.meeting_started_at]
    if sort == "created":
        return sorted(items, key=lambda i: (i.created_at, i.id), reverse=True)
    if sort == "priority":
        return sorted(items, key=lambda i: (_PRIORITY_RANK[i.priority], *due_key(i), i.id))
    return sorted(items, key=lambda i: (*due_key(i), _PRIORITY_RANK[i.priority], i.id))


def _call(fn, *args, **kwargs):
    """Library errors -> ToolError messages Claude can act on."""
    try:
        return fn(*args, **kwargs)
    except ActionItemNotFound:
        raise ToolError("Action item not found: check the id with list_action_items.") from None
    except ValueError as err:
        raise ToolError(str(err)) from None


def build_tools(library) -> dict[str, Tool]:
    def list_meetings(args):
        start, end = _range(args)
        limit = _int(args, "limit", 20, 1, 100)
        offset = _int(args, "offset", 0, 0, 1_000_000)
        rows = library.list_meetings(from_date=start, to_date=end,
                                     tag=_text(args, "tag"), person=_text(args, "person"),
                                     title=_text(args, "title"),
                                     limit=limit + 1, offset=offset)
        return {
            "meetings": [{
                "id": m.meeting_id, "title": m.title,
                "start": _start(m.started_at, m.tz_offset_minutes),
                "duration_minutes": _minutes(m.duration_seconds),
                "named_speakers": [x for x in m.speakers
                                   if not _ANONYMOUS_SPEAKER_RE.fullmatch(x)],
                "speaker_count": m.speaker_count,
                **({"people": m.people} if m.people else {}),
                **({"tags": m.tags} if m.tags else {}),
                "has_summary": m.has_summary,
                **({"timestamps_approximate": True} if m.timestamps_approximate else {}),
            } for m in rows[:limit]],
            "offset": offset,
            "next_offset": offset + limit if len(rows) > limit else None,
        }

    def get_meeting(args):
        m = library.get_meeting(_meeting_id(args), with_segments=False)
        notes = None
        if m.notes is not None:
            notes = {"summary": m.notes.summary,
                     "action_items": [{"id": i.id, "task": i.task, "owner": i.owner or "Unassigned",
                                       "status": i.status, "due": i.effective_due,
                                       "priority": i.priority} for i in m.action_items],
                     "updated_at": m.notes.updated_at, "updated_by": m.notes.updated_by}
        un = m.user_notes
        user_notes = None if un is None else {
            "markdown": un.markdown,
            "stamps": [{"line": line, "at": _hms(s)} for line, s in un.stamps],
            "updated_at": un.updated_at}
        event = library.calendar_event(m.calendar_event_id)
        return {
            "id": m.meeting_id, "title": m.title,
            "start": _start(m.started_at, m.tz_offset_minutes),
            "duration_minutes": _minutes(m.duration_seconds),
            "speakers": m.speakers,
            "segment_count": m.segment_count, "people": m.people, "tags": m.tags,
            "tags_detail": [{"name": n, "source": src}
                            for n, src in library.meeting_tag_details(m.meeting_id)],
            "calendar_event": _event(event) if event else None, "notes": notes,
            "timestamps_approximate": m.timestamps_approximate, "source": m.source,
            "user_notes": user_notes,
        }

    def _snippet(h):
        return {"kind": h.kind, "speaker": h.speaker, "at": _hms(h.start_seconds),
                "start_seconds": h.start_seconds, "snippet": _markdown(h.snippet)}

    def search_meetings(args):
        query = _text(args, "query", required=True, max_len=500)
        start, end = _range(args)
        limit = _int(args, "limit", 10, 1, 50)
        offset = _int(args, "offset", 0, 0, 1000)
        filters = dict(from_date=start, to_date=end, tag=_text(args, "tag"),
                       person=_text(args, "person"), limit=limit + 1, offset=offset)
        by_meeting = _bool(args, "by_meeting")
        if by_meeting:
            rows, truncated = library.search_grouped(query, **filters)
            results = [{
                "meeting_id": g.meeting_id, "title": g.title,
                "meeting_start": _start(g.started_at, g.tz_offset_minutes),
                "hit_count": g.hit_count, "first_at": _hms(g.first_seconds),
                "last_at": _hms(g.last_seconds), "kinds": g.kinds,
                "snippets": [_snippet(h) for h in g.best],
            } for g in rows[:limit]]
        else:
            rows = library.search(query, **filters)
            results = [{
                "meeting_id": h.meeting_id, "title": h.title,
                "meeting_start": _start(h.started_at, h.tz_offset_minutes),
                "kind": h.kind, "speaker": h.speaker, "also_speakers": h.also_speakers,
                "at": _hms(h.start_seconds), "start_seconds": h.start_seconds,
                "snippet": _markdown(h.snippet),
            } for h in rows[:limit]]
        out = {"results": results, "offset": offset,
               "next_offset": offset + limit if len(rows) > limit else None}
        if by_meeting:
            out["truncated"] = truncated
        return out

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
        return {"tags": [{"name": t.name, "count": t.count, "description": t.description,
                          "aliases": t.aliases} for t in library.tag_catalog()]}

    def list_people(args):
        return {"people": [{"name": n, "count": c}
                           for n, c in library.list_people(_text(args, "query"))]}

    def save_notes(args):
        meeting_id = _meeting_id(args)
        summary = args.get("summary")
        if summary is not None and not isinstance(summary, str):
            raise ToolError("summary must be text.")
        action_items, tags = args.get("action_items"), _str_list(args, "tags")
        if action_items is not None and not isinstance(action_items, list):
            raise ToolError("action_items must be a list.")
        if summary is None and action_items is None and tags is None:
            raise ToolError("Give at least one of summary, action_items or tags.")
        notes = library.save_notes(meeting_id, summary=summary, action_items=action_items,
                                   tags=tags, updated_by="claude")
        return {"id": meeting_id, "summary": notes.summary,
                "action_items": notes.action_items,
                "tags": library.meeting_tags(meeting_id),
                "created_tags": notes.created_tags, "suppressed": notes.suppressed_tags,
                "updated_at": notes.updated_at, "updated_by": notes.updated_by}

    def tag_meetings(args):
        ids = _str_list(args, "ids")
        if not ids:
            raise ToolError("ids must list at least one meeting id.")
        add, remove = _str_list(args, "add") or [], _str_list(args, "remove") or []
        if not add and not remove:
            raise ToolError("Give tags to add and/or remove.")
        update = library.tag_meetings(ids, add=add, remove=remove)
        return {"meetings": [{"id": i, "tags": t} for i, t in update.meetings],
                "created_tags": update.created_tags, "not_found": update.not_found}

    def manage_tags(args):
        action = _text(args, "action", required=True, max_len=20)
        tag = _text(args, "tag", required=True)
        if action == "rename":
            info = library.rename_tag(tag, _text(args, "name", required=True))
        elif action == "merge":
            info = library.merge_tags(tag, _text(args, "into", required=True))
        elif action == "describe":
            description = args.get("description")
            if not isinstance(description, str):
                raise ToolError("describe needs description (text; empty clears it).")
            info = library.describe_tag(tag, description)
        elif action == "delete":
            return {"deleted": library.delete_tag(tag)}
        else:
            raise ToolError("action must be rename, merge, delete or describe.")
        return {"name": info.name, "description": info.description,
                "aliases": info.aliases, "count": info.count}

    def pending_summaries(args):
        limit = _int(args, "limit", 5, 1, 10)
        start, end = _range(args)
        meetings = []
        for meeting_id, requested in library.pending_summaries(
                limit=limit, from_date=start, to_date=end):
            try:
                m = library.get_meeting(meeting_id, with_segments=False)
            except MeetingNotFound:
                continue          # deleted since the queue query ran
            meetings.append({
                "id": m.meeting_id, "title": m.title,
                "start": _start(m.started_at, m.tz_offset_minutes),
                "duration_minutes": _minutes(m.duration_seconds),
                "speakers": m.speakers, "tags": m.tags,
                "has_summary": bool(m.notes and m.notes.summary),
                "requested": requested})
        return {"meetings": meetings, "instructions": summary_instructions(settings.get_identity())}

    def list_action_items(args):
        status = _text(args, "status", max_len=10) or "open"
        if status not in ("open", "done", "all"):
            raise ToolError("status must be open, done or all.")
        sort = _text(args, "sort", max_len=10) or "due"
        if sort not in _SORTS:
            raise ToolError("sort must be due, priority, meeting or created.")
        limit, offset = _int(args, "limit", 50, 1, 100), _int(args, "offset", 0, 0, 1_000_000)
        identity = settings.get_identity()
        today = datetime.now().astimezone().date()
        due_from, due_to = _date(args, "due_from"), _date(args, "due_to")
        owner, tag = _text(args, "owner"), _text(args, "tag")
        query, meeting_id = _text(args, "query", max_len=200), args.get("meeting_id")
        mine, overdue = _bool(args, "mine"), _bool(args, "overdue")
        items = library.list_action_items()

        def keep(i):
            if status != "all" and i.status != status:
                return False
            if mine and not ai.is_mine(i.owner, identity):
                return False
            if owner and (i.owner or "Unassigned").casefold() != owner.casefold():
                return False
            if tag and tag_slug(tag) not in {tag_slug(t) for t in [*i.tags, *i.meeting_tags]}:
                return False
            if meeting_id and i.meeting_id != meeting_id:
                return False
            if due_from and (i.effective_due is None or i.effective_due < due_from):
                return False
            if due_to and (i.effective_due is None or i.effective_due > due_to):
                return False
            if overdue and not (i.status == "open"
                                and ai.due_bucket(i.effective_due, today) == "overdue"):
                return False
            if query and query.casefold() not in " ".join(
                    [i.task, i.notes, i.owner, i.meeting_title or ""]).casefold():
                return False
            return True

        rows = _sorted([i for i in items if keep(i)], sort)
        page = rows[offset:offset + limit]
        return {"items": [_item_json(i, identity) for i in page],
                "identity_set": bool(identity["name"]),
                "offset": offset,
                "next_offset": offset + limit if len(rows) > offset + limit else None}

    def create_action_item(args):
        item = _call(library.create_action_item, task=args.get("task"), owner=args.get("owner") or "",
                     meeting_id=args.get("meeting_id"), due=args.get("due"),
                     priority=args.get("priority") or "normal", notes=args.get("notes") or "",
                     tags=args.get("tags") or [], updated_by="claude")
        return _item_json(item, settings.get_identity())

    def update_action_item(args):
        item_id = _item_id(args)
        fields = {k: args[k] for k in ("task", "owner", "priority", "status", "notes", "due", "tags")
                  if k in args}
        reset = _bool(args, "reset_due")
        if not fields and not reset:
            raise ToolError("Give at least one field to change.")
        item = _call(library.update_action_item, item_id, updated_by="claude",
                     due_reset=reset, **fields)
        return _item_json(item, settings.get_identity())

    def delete_action_item(args):
        item_id = _item_id(args)
        if _bool(args, "undo"):
            return _item_json(_call(library.restore_action_item, item_id, updated_by="claude"),
                              settings.get_identity())
        _call(library.delete_action_item, item_id, updated_by="claude")
        return {"deleted": item_id}

    specs = [
        ("list_meetings",
         "List saved meetings, newest first, with date, duration, named speakers "
         "(unnamed 'Speaker N' labels are only counted in speaker_count), people, "
         "tags and whether a summary exists (people, tags and timestamps_approximate "
         "appear only when present). No transcript text. Filter by title "
         "words to find a meeting by name; use get_meeting for full detail.",
         {**_FILTERS,
          "title": {"type": "string", "description": "Words that must all appear in "
                    "the meeting title, any order, case-insensitive."},
          "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
          "offset": {"type": "integer", "minimum": 0, "default": 0}}, [], list_meetings, True),
        ("get_meeting",
         "Get one meeting's details: the user's own notes (user_notes; read these "
         "first, they show what mattered to the user), Claude's notes (summary, "
         "action items with ids), tags, people and calendar event. No transcript: use "
         "get_transcript for that.",
         _ID, ["id"], get_meeting, True),
        ("search_meetings",
         "Search meetings by title, date and content: transcripts, summaries and the "
         "user's own notes (kind user_notes). A date in the query (e.g. 'Oct 3', "
         "'3 October 2026', '2026-10-03', 'today', 'yesterday') limits results to that "
         "day; a date alone lists that day's meetings. Title matches come first as kind "
         "meeting; content matches return ranked snippets (matches in **bold**) with the "
         "meeting id and time offset. Every word must appear in the same passage, so "
         "search one or two distinctive terms at a time and try synonyms (e.g. API, "
         "endpoint, REST, GraphQL) as separate searches. To answer 'when and why did we "
         "decide X': search with by_meeting=true to see which meetings discussed it and "
         "when, narrow with from/to, then read get_meeting (summary Decisions, the "
         "user's notes) and get_transcript around start_seconds. If truncated is true, the term was too common to count every passage, so some meetings may be missing: use a more distinctive term or narrow from/to. Pass next_offset as "
         "offset for more.",
         {"query": {"type": "string"}, **_FILTERS,
          "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
          "by_meeting": {"type": "boolean", "default": False,
                         "description": "One result per meeting (hit count, first/last "
                                        "time, best 2 snippets) instead of per passage."},
          "offset": {"type": "integer", "minimum": 0, "maximum": 1000, "default": 0}},
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
        ("list_tags",
         "All meeting tags with how many meetings use each, plus each tag's "
         "description and aliases (other spellings that resolve to it).",
         {}, [], list_tags, True),
        ("list_people", "People linked to meetings, with meeting counts.",
         {"query": {"type": "string", "description": "Part of a name or email."}},
         [], list_people, True),
        ("save_notes",
         "Save a meeting's summary, action items and/or tags. Only the fields you "
         "give are replaced. Tags are your own suggestions: call list_tags first and "
         "reuse existing tags (any spelling of a tag or its aliases matches it); "
         "create a new tag only for a genuinely new topic, at most 3 per call. "
         "Replacing tags never removes tags the user added, and tags the user "
         "removed from this meeting are skipped. "
         "Write summaries in the format pending_summaries returns. "
         "Action items the user completed, edited, added or deleted are kept; the rest are replaced by the list you give.",
         {**_ID, "summary": {"type": "string", "maxLength": 20000,
                             "description": "Use the format from pending_summaries: a TL;DR line, "
                                            "then ## Decisions, ## Key points, ## Open questions."},
          "action_items": {"type": "array", "maxItems": 50, "items": {"anyOf": [
              {"type": "string"},
              {"type": "object", "additionalProperties": False, "required": ["task"],
               "properties": {"task": {"type": "string", "maxLength": 500},
                              "owner": {"type": "string", "maxLength": 80},
                              "due_date": {"type": "string"}, "due_phrase": {"type": "string", "maxLength": 80},
                              "priority": {"type": "string", "enum": ["high", "normal", "low"]},
                              "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 10}}}]}},
          "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 20}},
         ["id"], save_notes, False),
        ("tag_meetings",
         "Add or remove tags on one or more meetings because the user asked you to. "
         "These tags are kept when notes are saved again, and tags removed here "
         "won't be re-added by save_notes.",
         {"ids": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                  "maxItems": 100,
                  "description": "Meeting ids from list_meetings or search_meetings."},
          "add": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
          "remove": {"type": "array", "items": {"type": "string"}, "maxItems": 20}},
         ["ids"], tag_meetings, False),
        ("manage_tags",
         "Rename, merge, delete or describe a tag across all meetings, when the user "
         "asks to tidy their tags. Renaming or merging keeps the old name as an alias, "
         "so it still finds the tag.",
         {"action": {"type": "string", "enum": ["rename", "merge", "delete", "describe"]},
          "tag": {"type": "string", "description": "The tag to change (any spelling or alias)."},
          "name": {"type": "string", "description": "rename: the new name."},
          "into": {"type": "string", "description": "merge: the tag to merge into."},
          "description": {"type": "string", "maxLength": 200,
                          "description": "describe: what the tag is for; empty clears it."}},
         ["action", "tag"], manage_tags, False),
        ("pending_summaries",
         "Meetings waiting for a summary (new ones from the last 7 days, plus any "
         "the user asked to summarise or redo), with the summary format to use. "
         "To catch up older meetings when the user asks (e.g. 'summarise everything "
         "from August'), pass from/to: unsummarised meetings in that range come back "
         "oldest first; call again until none are left. For each: read the whole "
         "transcript with get_transcript, then save the summary, action items and "
         "tags with save_notes.",
         {"limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
          "from": _FILTERS["from"], "to": _FILTERS["to"]},
         [], pending_summaries, True),
        ("list_action_items",
         "List action items across meetings (open ones by default), soonest due first. "
         "Each has an id, task, owner (mine is true when it is the user's), priority, "
         "due date (due_source user means the user set it), notes, tags (the item's and "
         "its meeting's) and its meeting. Filter by mine, owner, tag, meeting_id, "
         "due_from/due_to, overdue or query (words in the task, notes, owner or meeting "
         "title). Pass next_offset as offset for more.",
         {"status": {"type": "string", "enum": ["open", "done", "all"], "default": "open"},
          "mine": {"type": "boolean"}, "owner": {"type": "string"}, "tag": {"type": "string"},
          "meeting_id": {"type": "string"}, "due_from": _FILTERS["from"], "due_to": _FILTERS["to"],
          "overdue": {"type": "boolean"}, "query": {"type": "string"},
          "sort": {"type": "string", "enum": list(_SORTS), "default": "due"},
          "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
          "offset": {"type": "integer", "minimum": 0, "default": 0}},
         [], list_action_items, True),
        ("create_action_item",
         "Add an action item because the user asked you to, optionally linked to a "
         "meeting. due is YYYY-MM-DD. Items you add or change are kept when the meeting "
         "is summarised again.",
         {"task": {"type": "string", "maxLength": 500}, "owner": {"type": "string", "maxLength": 80},
          "meeting_id": {"type": "string"}, "due": {"type": "string"},
          "priority": {"type": "string", "enum": ["high", "normal", "low"]},
          "notes": {"type": "string", "maxLength": 5000},
          "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 10}},
         ["task"], create_action_item, False),
        ("update_action_item",
         "Change an action item because the user asked you to: mark it done (status "
         "done) or open, edit task, owner, priority or notes, set due (YYYY-MM-DD; empty "
         "text means no date), reset_due to go back to the date from the summary, or "
         "replace its own tags. Only the fields you give change.",
         {"id": {"type": "integer"}, "task": {"type": "string", "maxLength": 500},
          "owner": {"type": "string", "maxLength": 80},
          "priority": {"type": "string", "enum": ["high", "normal", "low"]},
          "status": {"type": "string", "enum": ["open", "done"]},
          "notes": {"type": "string", "maxLength": 5000}, "due": {"type": "string"},
          "reset_due": {"type": "boolean"},
          "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 10}},
         ["id"], update_action_item, False),
        ("delete_action_item",
         "Delete an action item because the user asked you to (it stays out of future "
         "summaries of that meeting). undo true restores it.",
         {"id": {"type": "integer"}, "undo": {"type": "boolean"}},
         ["id"], delete_action_item, False),
    ]
    return {
        name: Tool(name, description,
                   {"type": "object", "properties": props, "required": required,
                    "additionalProperties": False} if required
                   else {"type": "object", "properties": props, "additionalProperties": False},
                   run, read_only)
        for name, description, props, required, run, read_only in specs
    }
