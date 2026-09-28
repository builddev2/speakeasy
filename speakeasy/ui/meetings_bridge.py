"""Pure-Python handlers behind the Meetings window (no AppKit), so the
payload shapes the React page depends on are unit-tested. The ObjC
controller in meetings_window.py only adds Copy/Export and window glue.

Payload shapes and formats are pinned to frontend/src/mock/meetings.ts (the
approved design) — see task-10-report.md for where they deviate from the
original task brief's literal test expectations and why.
"""

from datetime import datetime, timedelta

from speakeasy.meeting_library import MeetingLibrary, MeetingNotFound, local_start
from speakeasy.ui.webbridge import day_label, segments_to_lines, snippet_parts

_IDLE = {"state": "idle", "done": 0, "total": 0, "skipped": []}


def _minutes(seconds: float) -> int:
    return max(1, round(seconds / 60))


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


class MeetingsBridge:
    def __init__(self, library=None, now=None):
        self.library = library or MeetingLibrary()
        self._now = now or (lambda: datetime.now().astimezone())
        self._status = dict(_IDLE)

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
        }.items():
            dispatcher.register(method, self._wrap(fn))

    @staticmethod
    def _wrap(fn):
        def handler(params, respond):
            try:
                respond(fn(params or {}))
            except MeetingNotFound:
                respond(error="not_found")
            except (ValueError, IndexError) as err:
                respond(error=str(err))
        return handler

    # -- payloads (shapes = frontend/src/mock/meetings.ts) ----------------

    def _meta_fields(self, *, meeting_id, title, meeting_local_start, duration_seconds,
                      speaker_count, has_summary, approximate, tags, people, subtitle) -> dict:
        minutes = _minutes(duration_seconds)
        return {
            "id": meeting_id, "title": title,
            "dayLabel": day_label(meeting_local_start, self._now()),
            "time": meeting_local_start.strftime("%-I:%M %p"), "duration": f"{minutes} min",
            "subtitle": subtitle, "speakerCount": speaker_count, "hasSummary": has_summary,
            "approximate": approximate, "tags": tags, "people": people,
        }

    def _subtitle(self, meeting_id: str, people: list[str], speakers=None) -> str:
        # Controller ruling: linked people win (mock rows show "Alex, Priya,
        # Sam"); otherwise fall back to the distinct speaker labels in order
        # of first appearance (e.g. "You, Speaker 1"), fetching segments only
        # when we don't already have them (list rows have no segments).
        if people:
            return ", ".join(people)
        if speakers is None:
            speakers = _distinct_speakers(self.library.get_meeting(meeting_id).segments)
        return ", ".join(speakers)

    def _meta(self, m) -> dict:
        return self._meta_fields(
            meeting_id=m.meeting_id, title=m.title, meeting_local_start=m.local_start,
            duration_seconds=m.duration_seconds, speaker_count=m.speaker_count,
            has_summary=m.has_summary, approximate=m.timestamps_approximate,
            tags=m.tags, people=m.people, subtitle=self._subtitle(m.meeting_id, m.people),
        )

    def list_payload(self, params) -> list[dict]:
        return [self._meta(m) for m in self.library.list_meetings(
            tag=params.get("tag") or None, person=params.get("person") or None, limit=500)]

    def filters_payload(self, params) -> dict:
        return {
            "total": self.library.count_meetings(),
            "tags": [{"name": n, "count": c} for n, c in self.library.list_tags()],
            "people": [{"name": n, "count": c} for n, c in self.library.list_people()],
            # Flipped on by phases 2-4 as each feature ships.
            "features": {"calendar": False, "claude": False, "settings": False},
        }

    def get_payload(self, params) -> dict:
        m = self.library.get_meeting(str(params.get("id", "")))
        speakers = _distinct_speakers(m.segments)
        detail = self._meta_fields(
            meeting_id=m.meeting_id, title=m.title, meeting_local_start=m.local_start,
            duration_seconds=m.duration_seconds, speaker_count=len(speakers),
            has_summary=bool(m.notes and m.notes.summary), approximate=m.timestamps_approximate,
            tags=m.tags, people=m.people, subtitle=self._subtitle(m.meeting_id, m.people, speakers),
        )
        detail.update({
            # Controller ruling: MeetingDetail.tsx composes "date · time ·
            # duration" from these three separate fields, so `date` carries
            # no time/year of its own (mock: "Thu 24 Sep").
            "date": m.local_start.strftime("%a %-d %b"),
            "lines": _wallclock_lines(m.segments, m.local_start),
            "summary": (m.notes.summary or None) if m.notes else None,
            "actionItems": m.notes.action_items if m.notes else [],
            "event": None,
        })
        return detail

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
        self.library.rename(str(params.get("id", "")), str(params.get("title", "")))
        return self.list_payload({})

    def relabel_payload(self, params) -> dict:
        self.library.relabel_speaker(
            str(params.get("id", "")), int(params.get("segmentIndex", -1)),
            str(params.get("label", "")), all_matching=bool(params.get("allMatching")))
        return self.get_payload(params)

    def delete_payload(self, params) -> list[dict]:
        self.library.delete(str(params.get("id", "")))
        return self.list_payload({})

    def status_payload(self, params) -> dict:
        return dict(self._status)

    def set_library_status(self, status: dict) -> None:
        self._status = dict(status)
