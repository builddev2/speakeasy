"""Pure-Python handlers behind the Meetings window (no AppKit), so the
payload shapes the React page depends on are unit-tested. The ObjC
controller in meetings_window.py only adds Copy/Export and window glue.

Payload shapes and formats are pinned to frontend/src/mock/meetings.ts (the
approved design) — see task-10-report.md for where they deviate from the
original task brief's literal test expectations and why.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from speakeasy import mcp_setup
from speakeasy import meetings as meeting_render
from speakeasy import settings
from speakeasy.meeting_export import render_export_md
from speakeasy.summary_format import parse_summary
from speakeasy.meeting_library import (
    LibraryWatcher, MeetingLibrary, MeetingNotFound, local_start,
)
from speakeasy.meeting_options import default_options
from speakeasy.ui import calendar_payloads
from speakeasy.ui.webbridge import day_label, segments_to_lines, snippet_parts

_IDLE = {"state": "idle", "done": 0, "total": 0, "skipped": []}


def _minutes(seconds: float) -> int:
    return max(1, round(seconds / 60))


def _subtitle(people: list[str], speakers: list[str]) -> str:
    # Controller ruling: linked people win (mock rows show "Alex, Priya,
    # Sam"); otherwise fall back to the distinct speaker labels in order of
    # first appearance (e.g. "You, Speaker 1"). Both lists are already
    # available on the caller's object (MeetingSummary.speakers is a SQL
    # column, StoredMeeting's come from _distinct_speakers(m.segments)) —
    # never a second per-row fetch, which used to make meetings.list load
    # every listed meeting's full transcript on the main thread.
    return ", ".join(people) if people else ", ".join(speakers)


def _distinct_speakers(segments) -> list[str]:
    """Distinct speaker labels, in order of first appearance."""
    order: list[str] = []
    seen: set[str] = set()
    for seg in segments:
        if seg.speaker not in seen:
            seen.add(seg.speaker)
            order.append(seg.speaker)
    return order


def _wallclock_lines(segments, meeting_local_start: datetime) -> list[dict]:
    """segments_to_lines, with `time` overridden to wall-clock (mock format
    "9:00:03 AM"), not the elapsed-time bracket segments_to_lines otherwise
    produces (that format is still used by render_txt/render_md exports)."""
    lines = segments_to_lines(segments)
    for line, seg in zip(lines, segments):
        when = meeting_local_start + timedelta(seconds=seg.start)
        line["time"] = when.strftime("%-I:%M:%S %p")
    return lines


_NOT_RECORDING = {"recording": False, "processing": False, "startedAt": None, "title": None,
                  "mode": "", "micFailure": None, "startError": None, "processingError": None}


class BridgeError(Exception):
    """A named error code (the message) for the page, not a bug."""


class MeetingsBridge:
    def __init__(self, library=None, now=None, set_clipboard=None, open_path=None,
                 calendar=None, begin_meeting=None, recording_event_key=None, open_url=None,
                 recording_info=None, login_status=None, set_login=None,
                 retry_microphone=None):
        self.library = library or MeetingLibrary()
        # Opens a file/folder in its default app; AppKit-backed in the real
        # window, a no-op here so this module stays pure-Python.
        self._open_path = open_path or (lambda path: None)
        # Lazy connection: constructing a bridge stays cheap until first poll.
        self._watcher = LibraryWatcher(self.library)
        self._now = now or (lambda: datetime.now().astimezone())
        self._status = dict(_IDLE)
        # Injectable so this stays pure-Python/unit-testable; meetings_window.py
        # supplies injector.set_clipboard for the real app (AppKit stays out of
        # this module, as the module docstring promises).
        self._set_clipboard = set_clipboard or (lambda text: None)
        # Calendar wiring (phase 3): all None in tests and the MCP server.
        self._calendar = calendar
        self._begin_meeting = begin_meeting
        self._retry_microphone = retry_microphone
        self._recording_event_key = recording_event_key or (lambda: None)
        self._open_url = open_url or (lambda url: None)
        self._recording_info = recording_info or (lambda: dict(_NOT_RECORDING))
        self._login_status = login_status
        self._set_login = set_login
        self._navigation = None  # page the next window open should land on

    def set_navigation(self, view) -> None:
        ok = view in ("recording", "settings") or (
            isinstance(view, dict) and set(view) == {"meeting"}
            and isinstance(view["meeting"], str) and 0 < len(view["meeting"]) <= 200)
        if not ok:
            raise ValueError("Unknown page.")
        self._navigation = view

    def take_navigation_payload(self, params):
        view, self._navigation = self._navigation, None
        return view

    def register(self, dispatcher) -> None:
        for method, fn in {
            "meetings.list": self.list_payload,
            "meetings.filters": self.filters_payload,
            "meetings.get": self.get_payload,
            "meetings.search": self.search_payload,
            "meetings.rename": self.rename_payload,
            "meetings.relabelSpeaker": self.relabel_payload,
            "meetings.delete": self.delete_payload,
            "library.status": self.status_payload,
            "meetings.copyText": self.copy_text_payload,
            "meetings.requestSummary": self.request_summary_payload,
            "meetings.copy": self.copy_payload,
            "claude.setupInfo": self.claude_setup_payload,
            "claude.installExtension": self.install_extension_payload,
            "claude.revealConfig": self.reveal_config_payload,
            "calendar.today": self.calendar_today_payload,
            "calendar.upcoming": self.calendar_upcoming_payload,
            "calendar.requestAccess": self.calendar_request_access_payload,
            "calendar.openPrivacySettings": self.calendar_privacy_payload,
            "calendar.record": self.calendar_record_payload,
            "meeting.start": self.start_meeting_payload,
            "meeting.retryMicrophone": self.retry_microphone_payload,
            "meetings.linkEvent": self.link_event_payload,
            "meetings.eventsForDay": self.events_for_day_payload,
            "settings.meetings.get": self.settings_get_payload,
            "settings.meetings.set": self.settings_set_payload,
            "notes.user.set": self.notes_set_payload,
            "notes.draft.get": self.draft_get_payload,
            "notes.draft.set": self.draft_set_payload,
            "notes.draft.discard": self.draft_discard_payload,
            "notes.draft.finish": self.draft_finish_payload,
            "recording.get": self.recording_payload,
            "meetings.takeNavigation": self.take_navigation_payload,
        }.items():
            dispatcher.register(method, self._wrap(fn))

    @staticmethod
    def _wrap(fn):
        def handler(params, respond):
            try:
                respond(fn(params or {}))
            except MeetingNotFound:
                respond(error="not_found")
            except BridgeError as err:
                respond(error=str(err))
            except (ValueError, IndexError) as err:
                respond(error=str(err))
        return handler

    # -- Connect Claude (phase 2) ------------------------------------------

    def claude_setup_payload(self, params) -> dict:
        return mcp_setup.current_setup_info(self._now())

    def install_extension_payload(self, params) -> bool:
        # Opening the .mcpb hands it to Claude Desktop, which shows its own
        # install dialog; Speakeasy never writes Claude's config itself.
        if not mcp_setup.current_setup_info(self._now()).get("extensionAvailable"):
            raise BridgeError("extension_unavailable")
        self._open_path(mcp_setup.current_mcpb_path())
        return True

    def reveal_config_payload(self, params) -> bool:
        folder = Path.home() / "Library" / "Application Support" / "Claude"
        if not folder.is_dir():
            raise BridgeError("claude_desktop_not_found")
        self._open_path(folder)
        return True

    # -- calendar (phase 3) ------------------------------------------------

    PRIVACY_CALENDARS_URL = (
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Calendars")

    def _access(self) -> str:
        return self._calendar.access() if self._calendar is not None else "unconnected"

    def calendar_today_payload(self, params) -> dict:
        access, now = self._access(), self._now()
        if access != "connected":
            return {"access": access, "agenda": []}
        day = now.date().isoformat()
        return {"access": access, "agenda": calendar_payloads.agenda_payload(
            self.library.calendar_events_between(day, day), now, self._recording_event_key())}

    def calendar_upcoming_payload(self, params) -> dict:
        now = self._now()
        if self._access() != "connected":
            return {"days": []}
        return {"days": calendar_payloads.upcoming_payload(
            self.library.calendar_events_between(
                (now.date() + timedelta(days=1)).isoformat(),
                (now.date() + timedelta(days=7)).isoformat()), now)}

    def calendar_request_access_payload(self, params) -> bool:
        if self._calendar is None:
            raise BridgeError("calendar_unavailable")
        self._calendar.request_access()
        return True

    def calendar_privacy_payload(self, params) -> bool:
        self._open_url(self.PRIVACY_CALENDARS_URL)
        return True

    def calendar_record_payload(self, params) -> bool:
        key = params.get("key")
        if not isinstance(key, str) or not key:
            raise ValueError("Unknown calendar event.")
        if self._begin_meeting is None:
            raise BridgeError("recording_unavailable")
        self._begin_meeting(default_options(calendar_event_key=key))
        return True

    def retry_microphone_payload(self, params) -> bool:
        if self._retry_microphone is None:
            raise BridgeError("recording_unavailable")
        return bool(self._retry_microphone())

    def start_meeting_payload(self, params) -> bool:
        if self._begin_meeting is None:
            raise BridgeError("recording_unavailable")
        self._begin_meeting(default_options())
        return True

    def link_event_payload(self, params) -> dict:
        key = params.get("key")
        if key is not None and not isinstance(key, str):
            raise ValueError("Unknown calendar event.")
        self.library.link_event(str(params.get("id", "")), key)
        return self.get_payload({"id": params.get("id")})

    def events_for_day_payload(self, params) -> list[dict]:
        m = self.library.get_meeting(str(params.get("id", "")))
        day = m.local_start.date().isoformat()
        return calendar_payloads.day_chips(
            self.library.calendar_events_between(day, day), self._now().tzinfo)

    def settings_get_payload(self, params) -> dict:
        stored = settings.get_meeting_settings()
        calendars = self._calendar.calendars if self._calendar is not None else []
        return {"offerToRecord": stored["offer_to_record"],
                "detectCalls": stored["detect_calls"],
                "appearance": settings.get_appearance(),
                "identifyVoices": settings.get_identify_voices(),
                "startAtLogin": self._login_status() if self._login_status else None,
                "accounts": calendar_payloads.calendars_payload(
                    calendars, stored["calendar_choices"])}

    def settings_set_payload(self, params) -> dict:
        if params.get("appearance") is not None:
            from . import appearance
            appearance.apply(settings.set_appearance(params["appearance"]))
        if params.get("identifyVoices") is not None:
            settings.set_identify_voices(params["identifyVoices"])
        if params.get("startAtLogin") is not None:
            if not isinstance(params["startAtLogin"], bool):
                raise ValueError("Start at login must be on or off.")
            if self._set_login is None or self._login_status is None or self._login_status() is None:
                raise ValueError("Start at login is available in the installed app.")
            try:
                self._set_login(params["startAtLogin"])
            except RuntimeError as err:
                raise ValueError(str(err)) from err
        settings.set_meeting_settings(
            offer_to_record=params.get("offerToRecord"),
            detect_calls=params.get("detectCalls"),
            calendar_choices=params.get("calendars"))
        if params.get("calendars") is not None and self._calendar is not None:
            self._calendar.request_sync()
        return self.settings_get_payload({})

    def poll_changed(self) -> bool:
        """True when another connection committed since the last poll (e.g.
        Claude saved notes through the MCP server, a separate process).
        The first call only records a baseline. A busy or unreadable library is
        skipped this tick. Main thread only (the watcher owns one connection)."""
        try:
            return self._watcher.changed()
        except sqlite3.Error:  # busy, or a corrupt file: never raise into the timer
            return False

    def stop_polling(self) -> None:
        self._watcher.close()

    # -- payloads (shapes = frontend/src/mock/meetings.ts) ----------------

    def _meta_fields(self, *, meeting_id, title, meeting_local_start, duration_seconds,
                      speaker_count, has_summary, approximate, tags, people, subtitle) -> dict:
        minutes = _minutes(duration_seconds)
        return {
            "id": meeting_id, "title": title,
            "dayLabel": day_label(meeting_local_start, self._now()),
            "startDate": meeting_local_start.date().isoformat(),
            "time": meeting_local_start.strftime("%-I:%M %p"), "duration": f"{minutes} min",
            "subtitle": subtitle, "speakerCount": speaker_count, "hasSummary": has_summary,
            "approximate": approximate, "tags": tags, "people": people,
        }

    def _meta(self, m) -> dict:
        return self._meta_fields(
            meeting_id=m.meeting_id, title=m.title, meeting_local_start=m.local_start,
            duration_seconds=m.duration_seconds, speaker_count=m.speaker_count,
            has_summary=m.has_summary, approximate=m.timestamps_approximate,
            tags=m.tags, people=m.people, subtitle=_subtitle(m.people, m.speakers),
        )

    def list_payload(self, params) -> list[dict]:
        return [self._meta(m) for m in self.library.list_meetings(
            tag=params.get("tag") or None, person=params.get("person") or None, limit=None)]

    def filters_payload(self, params) -> dict:
        return {
            "total": self.library.count_meetings(),
            "tags": [{"name": n, "count": c} for n, c in self.library.list_tags()],
            "people": [{"name": n, "count": c} for n, c in self.library.list_people()],
            "features": {"calendar": self._calendar is not None, "claude": True,
                         "settings": True},
        }

    def get_payload(self, params) -> dict:
        m = self.library.get_meeting(str(params.get("id", "")))
        speakers = _distinct_speakers(m.segments)
        detail = self._meta_fields(
            meeting_id=m.meeting_id, title=m.title, meeting_local_start=m.local_start,
            duration_seconds=m.duration_seconds, speaker_count=len(speakers),
            has_summary=bool(m.notes and m.notes.summary), approximate=m.timestamps_approximate,
            tags=m.tags, people=m.people, subtitle=_subtitle(m.people, speakers),
        )
        detail.update({
            # Controller ruling: MeetingDetail.tsx composes "date · time ·
            # duration" from these three separate fields, so `date` carries
            # no time/year of its own (mock: "Thu 24 Sep").
            "date": m.local_start.strftime("%a %-d %b"),
            "lines": _wallclock_lines(m.segments, m.local_start),
            "summary": (m.notes.summary or None) if m.notes else None,
            "summaryBlocks": parse_summary(m.notes.summary if m.notes else ""),
            "summaryQueued": self.library.summary_pending(m.meeting_id, now=self._now()),
            "actionItems": m.notes.action_items if m.notes else [],
            "hasUserNotes": bool(m.user_notes),
            "userNotes": {"markdown": m.user_notes.markdown if m.user_notes else "",
                          "stamps": [list(s) for s in m.user_notes.stamps] if m.user_notes else []},
            # Never look up a None key: only linked meetings have an event.
            "event": (calendar_payloads.event_chip(ev, self._now().tzinfo)
                      if m.calendar_event_id
                      and (ev := self.library.calendar_event(m.calendar_event_id)) else None),
        })
        return detail

    def notes_set_payload(self, params) -> dict:
        notes = self.library.set_user_notes(str(params.get("id", "")),
                                            str(params.get("markdown") or ""),
                                            params.get("stamps") or [])
        return {"updatedAt": notes.updated_at if notes else None}

    def draft_get_payload(self, params) -> dict | None:
        d = self.library.get_draft()
        return None if d is None else {"markdown": d.markdown,
                                       "stamps": [list(s) for s in d.stamps],
                                       "startedAt": d.started_at}

    def draft_set_payload(self, params) -> dict:
        d = self.library.set_draft(str(params.get("markdown") or ""), params.get("stamps") or [],
                                   str(params.get("startedAt", "")))
        return {"updatedAt": d.updated_at}

    def draft_discard_payload(self, params) -> bool:
        self.library.discard_draft()
        return True

    def draft_finish_payload(self, params) -> dict:
        notes = self.library.finish_draft(str(params.get("id", "")),
                                          str(params.get("markdown") or ""),
                                          params.get("stamps") or [],
                                          str(params.get("startedAt", "")))
        return {"updatedAt": notes.updated_at if notes else None}

    def recording_payload(self, params) -> dict:
        return {**_NOT_RECORDING, **(self._recording_info() or {})}

    def search_payload(self, params) -> list[dict]:
        results = []
        for h in self.library.search(str(params.get("query", "")), limit=50):
            start = local_start(h.started_at, h.tz_offset_minutes)
            results.append({
                "meetingId": h.meeting_id, "title": h.title,
                "dayLabel": day_label(start, self._now()),
                "time": start.strftime("%-I:%M %p"), "kind": h.kind,
                "speaker": h.speaker, "alsoSpeakers": h.also_speakers,
                "seconds": h.start_seconds, "segmentIndex": h.segment_index,
                "parts": snippet_parts(h.snippet),
            })
        return results

    def rename_payload(self, params) -> list[dict]:
        # Controller ruling: the list this returns re-applies the page's
        # active tag/person filter (Task 11 passes it through in params) —
        # otherwise a rename while filtered would silently reset the view
        # to "all meetings".
        self.library.rename(str(params.get("id", "")), str(params.get("title", "")))
        return self.list_payload(params)

    def relabel_payload(self, params) -> dict:
        self.library.relabel_speaker(
            str(params.get("id", "")), int(params.get("segmentIndex", -1)),
            str(params.get("label", "")), all_matching=bool(params.get("allMatching")))
        return self.get_payload(params)

    def delete_payload(self, params) -> list[dict]:
        self.library.delete(str(params.get("id", "")))
        return self.list_payload(params)

    def status_payload(self, params) -> dict:
        return dict(self._status)

    def request_summary_payload(self, params) -> dict:
        self.library.request_summary(str(params.get("id", "")))
        return self.get_payload(params)

    def copy_text_payload(self, params) -> bool:
        # WKWebView can reject navigator.clipboard.writeText; App.tsx's copy
        # helper falls back to this handler.
        self._set_clipboard(str(params.get("text", "")))
        return True

    def copy_payload(self, params) -> bool:
        """meetings.copy: puts the rendered transcript+notes on the
        clipboard. Registered through `register()` like every other
        method, so a missing meeting rejects with "not_found" via `_wrap`
        instead of meetings_window.py's old bespoke (and untested)
        try/except MeetingNotFound."""
        meeting = self.library.get_meeting(str(params.get("id", "")))
        self._set_clipboard(render_export_md(meeting))
        return True

    def export_meeting(self, params):
        """Looks up the meeting for meetings.export. Raises MeetingNotFound
        (str(params.get("id", "")) not found) like every _wrap'd handler.
        Not registered on the dispatcher itself — meetings_window.py's
        `_export` responds asynchronously (after the NSSavePanel's
        completion handler runs), so it can't route the *whole* call
        through `_wrap` — but it runs this lookup through `_wrap` directly
        for the synchronous not_found pre-check, so that conversion is
        exercised by the same tested code path as every other handler."""
        return self.library.get_meeting(str(params.get("id", "")))

    def set_library_status(self, status: dict) -> None:
        self._status = dict(status)

    def apply_status_json(self, payload_json: str) -> list[tuple[str, dict | None]]:
        """Parse a `library_status` JSON string, store it, and return the
        (event, payload) pairs the window should emit to the page: always
        `library.progress` with the fresh status, plus `meetings.changed`
        once the upgrade reaches `done` (new titles/speakers may have
        appeared). The ObjC side (meetings_window.py) only forwards these
        to WebWindow.emit — kept here so the decision is unit-tested."""
        self.set_library_status(json.loads(payload_json))
        status = self.status_payload({})
        events: list[tuple[str, dict | None]] = [("library.progress", status)]
        if status["state"] == "done":
            events.append(("meetings.changed", None))
        return events
