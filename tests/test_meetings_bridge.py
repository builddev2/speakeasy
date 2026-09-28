"""MeetingsBridge: pure-Python handlers behind the Meetings window.

Payload shapes are pinned to frontend/src/mock/meetings.ts (the approved
design). Formats follow the mock, not always the brief's original literals —
see task-10-report.md for the deviations and why.
"""

import json
from datetime import datetime, timedelta, timezone

from speakeasy.meeting_library import MeetingLibrary, NewMeeting
from speakeasy.meetings import MeetingSegment
from speakeasy.ui.meetings_bridge import MeetingsBridge
from speakeasy.ui.webbridge import BridgeDispatcher

TZ = timezone(timedelta(hours=-4))
NOW = lambda: datetime(2026, 9, 24, 15, 0, tzinfo=TZ)  # noqa: E731 — Today


def _call(dispatcher, method, params=None):
    out = []
    dispatcher.dispatch({"id": 1, "method": method, "params": params or {}}, out.append)
    return out[-1]


def _setup(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0, 5, "cognos reporting"),
                  MeetingSegment("Speaker 1", 6, 9, "agreed")],
        duration_seconds=1380, started_at=datetime(2026, 9, 24, 13, 17, tzinfo=TZ),
        title="1:1 Alex"))
    lib.save_notes(mid, summary="Invest in VFA.", tags=["VFA"])
    bridge = MeetingsBridge(lib, now=NOW)
    d = BridgeDispatcher()
    bridge.register(d)
    return lib, mid, bridge, d


def _meeting(library_path, *, segments, started_at, title="Meeting", duration=60):
    lib = MeetingLibrary()
    mid = lib.save_meeting(NewMeeting(
        segments=segments, duration_seconds=duration, started_at=started_at, title=title))
    bridge = MeetingsBridge(lib, now=NOW)
    return lib, mid, bridge


def test_list_and_get_shapes(library_path):
    lib, mid, bridge, d = _setup(library_path)
    metas = bridge.list_payload({})
    # No linked people, so subtitle falls back to distinct speaker labels in
    # order of first appearance (controller ruling; MeetingList.tsx renders
    # meta.subtitle verbatim, and the mock's only populated example is
    # linked people joined by ", ").
    assert metas == [{
        "id": mid, "title": "1:1 Alex", "dayLabel": "Today", "time": "1:17 PM",
        "duration": "23 min", "subtitle": "You, Speaker 1", "speakerCount": 2,
        "hasSummary": True, "approximate": False, "tags": ["VFA"], "people": []}]

    # Whole dict, not just a few fields: pins every key the TS MeetingDetail
    # contract declares, including lines[].time (wall-clock, controller
    # ruling) and lines[].start (jump-to seconds).
    detail = bridge.get_payload({"id": mid})
    assert detail == {
        "id": mid, "title": "1:1 Alex", "dayLabel": "Today", "time": "1:17 PM",
        "duration": "23 min", "subtitle": "You, Speaker 1", "speakerCount": 2,
        "hasSummary": True, "approximate": False, "tags": ["VFA"], "people": [],
        # MeetingDetail.tsx composes "date · time · duration" from these
        # three separate fields (metaLine), so `date` carries no time/year
        # of its own — controller ruling; mock uses e.g. "Thu 24 Sep".
        "date": "Thu 24 Sep",
        "lines": [
            {"time": "1:17:00 PM", "speakerNumber": 1, "speakerLabel": "You",
             "text": "cognos reporting", "segmentIndex": 0, "confidence": None,
             "overlap": False, "start": 0.0},
            {"time": "1:17:06 PM", "speakerNumber": 2, "speakerLabel": "Speaker 1",
             "text": "agreed", "segmentIndex": 1, "confidence": None,
             "overlap": False, "start": 6.0},
        ],
        "summary": "Invest in VFA.",
        "actionItems": [],
        "event": None,
    }


def test_filters_and_search(library_path):
    lib, mid, bridge, d = _setup(library_path)
    f = bridge.filters_payload({})
    assert f["total"] == 1 and f["tags"] == [{"name": "VFA", "count": 1}]
    assert f["features"] == {"calendar": False, "claude": False, "settings": False}

    # Whole search result, not just a couple of fields.
    res = bridge.search_payload({"query": "cognos"})
    assert res == [{
        "meetingId": mid, "title": "1:1 Alex", "dayLabel": "Today", "time": "1:17 PM",
        "kind": "transcript", "speaker": "You", "alsoSpeakers": [], "seconds": 0.0,
        "segmentIndex": 0,
        "parts": [{"text": "cognos", "hit": True}, {"text": " reporting", "hit": False}],
    }]
    assert bridge.search_payload({"query": "   "}) == []


def test_missing_meeting_is_not_found_error(library_path):
    lib, mid, bridge, d = _setup(library_path)
    lib.delete(mid)
    js = _call(d, "meetings.get", {"id": mid})
    assert "_reject" in js and "not_found" in js


def test_value_error_becomes_a_rejection_with_its_own_message(library_path):
    # An empty (whitespace-only) title fails MeetingLibrary.rename's own
    # validation with a ValueError; _wrap must turn that into a rejection
    # carrying the ValueError's own message verbatim (not BridgeDispatcher's
    # generic "ValueError: <msg>" fallback for an uncaught exception, which
    # would still reject but lose the distinction that this is expected,
    # user-facing validation rather than a handler bug).
    lib, mid, bridge, d = _setup(library_path)
    js = _call(d, "meetings.rename", {"id": mid, "title": "   "})
    assert "_reject" in js
    assert "Meeting title can't be empty." in js
    assert "ValueError:" not in js


def test_has_summary_false_when_no_notes(library_path):
    lib, mid, bridge = _meeting(
        library_path, segments=[MeetingSegment("You", 0, 1, "hi")],
        started_at=datetime(2026, 9, 24, 9, 0, tzinfo=TZ))
    assert bridge.list_payload({})[0]["hasSummary"] is False
    assert bridge.get_payload({"id": mid})["hasSummary"] is False


def test_relabel_and_delete_round_trip(library_path):
    lib, mid, bridge, d = _setup(library_path)
    detail = bridge.relabel_payload({"id": mid, "segmentIndex": 1, "label": "Alex",
                                     "allMatching": True})
    assert detail["lines"][1]["speakerLabel"] == "Alex"
    assert bridge.delete_payload({"id": mid}) == []


def test_relabel_single_vs_all_matching(library_path):
    segments = [MeetingSegment("Speaker 1", 0, 1, "a"),
                MeetingSegment("Speaker 2", 2, 3, "b"),
                MeetingSegment("Speaker 1", 4, 5, "c")]
    started = datetime(2026, 9, 24, 9, 0, tzinfo=TZ)

    lib, mid, bridge = _meeting(library_path, segments=list(segments), started_at=started)
    detail = bridge.relabel_payload(
        {"id": mid, "segmentIndex": 0, "label": "Alex", "allMatching": False})
    assert [l["speakerLabel"] for l in detail["lines"]] == ["Alex", "Speaker 2", "Speaker 1"]

    lib2, mid2, bridge2 = _meeting(library_path, segments=list(segments), started_at=started)
    detail2 = bridge2.relabel_payload(
        {"id": mid2, "segmentIndex": 0, "label": "Alex", "allMatching": True})
    assert [l["speakerLabel"] for l in detail2["lines"]] == ["Alex", "Speaker 2", "Alex"]


def test_duration_floor_rounds_up_to_one_minute(library_path):
    lib, mid, bridge = _meeting(
        library_path, segments=[MeetingSegment("You", 0, 1, "hi")],
        started_at=datetime(2026, 9, 24, 9, 0, tzinfo=TZ), duration=10)
    assert bridge.list_payload({})[0]["duration"] == "1 min"


def test_library_status(library_path):
    lib, mid, bridge, d = _setup(library_path)
    assert bridge.status_payload({})["state"] == "idle"
    bridge.set_library_status({"state": "upgrading", "done": 1, "total": 2, "skipped": []})
    assert bridge.status_payload({})["done"] == 1


def test_copy_text_registers_and_writes_via_injected_clipboard(library_path):
    # Regression: meetings_window.py's "Copy prompt" fallback (Task 11) posts
    # meetings.copyText with {"text": ...}. The handler lives on the bridge
    # (pure-Python, unlike _copy/_export which stay ObjC glue) precisely so
    # its registration and its exact clipboard call are unit-testable here —
    # a prior round silently renamed the registered method and dropped the
    # payload without any test catching it.
    captured = []
    bridge = MeetingsBridge(set_clipboard=captured.append)
    d = BridgeDispatcher()
    bridge.register(d)
    assert "meetings.copyText" in d._methods

    js = _call(d, "meetings.copyText", {"text": "hello clipboard"})
    assert "_resolve" in js
    assert captured == ["hello clipboard"]


def test_apply_status_json_emits_progress_and_changed_when_done(library_path):
    # Ruling 3: the parse/store/emit-decision logic lives in the bridge
    # (unit-tested here), not in meetings_window.py's ObjC glue.
    lib, mid, bridge, d = _setup(library_path)

    events = bridge.apply_status_json(
        json.dumps({"state": "upgrading", "done": 1, "total": 2, "skipped": []}))
    assert events == [
        ("library.progress", {"state": "upgrading", "done": 1, "total": 2, "skipped": []})]
    assert bridge.status_payload({}) == {
        "state": "upgrading", "done": 1, "total": 2, "skipped": []}

    events = bridge.apply_status_json(
        json.dumps({"state": "done", "done": 2, "total": 2, "skipped": []}))
    assert events == [
        ("library.progress", {"state": "done", "done": 2, "total": 2, "skipped": []}),
        ("meetings.changed", None),
    ]


def test_subtitle_uses_linked_people_when_present(library_path):
    lib, mid, bridge, d = _setup(library_path)
    lib.link_people(mid, [("Alex", None, "organizer"), ("Priya", None, "attendee"),
                           ("Sam", None, "attendee")])
    metas = bridge.list_payload({})
    assert metas[0]["subtitle"] == "Alex, Priya, Sam"
    assert metas[0]["people"] == ["Alex", "Priya", "Sam"]


def test_list_does_not_fetch_full_transcripts(library_path, monkeypatch):
    # Handoff issue 4: meetings.list ran on the main thread and, for every
    # row without linked people (the common case pre-phase-3), fetched that
    # meeting's full transcript just to build the subtitle — 350+ ms per
    # 100 rows, and a race against concurrent deletes. list_meetings now
    # carries ordered distinct speakers itself, so list_payload must never
    # call get_meeting.
    lib, mid, bridge, d = _setup(library_path)

    def boom(*args, **kwargs):
        raise AssertionError("list_payload must not fetch a full meeting")

    monkeypatch.setattr(lib, "get_meeting", boom)
    metas = bridge.list_payload({})
    assert metas[0]["subtitle"] == "You, Speaker 1"


def test_day_label_uses_local_calendar_day_not_utc(library_path):
    # 23:50 local (-04:00) on 23 Sep is 03:50 UTC on 24 Sep. Grouping by the
    # UTC calendar day (a real regression risk, since started_at is stored
    # as a UTC ISO string) would call this "Today" — same UTC date as `now`
    # — but the local calendar day is Yesterday.
    lib, mid, bridge = _meeting(
        library_path, segments=[MeetingSegment("You", 0, 1, "hi")],
        started_at=datetime(2026, 9, 23, 23, 50, tzinfo=TZ), title="Late night sync")
    assert bridge.list_payload({})[0]["dayLabel"] == "Yesterday"


def test_list_filtered_by_tag_and_person(library_path):
    lib, mid, bridge, d = _setup(library_path)  # tagged "VFA", no people
    other = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0, 1, "hi")], duration_seconds=60,
        started_at=datetime(2026, 9, 24, 10, 0, tzinfo=TZ), title="Standup"))
    lib.save_notes(other, tags=["Product"])
    lib.link_people(other, [("Priya", None, "attendee")])

    assert [m["id"] for m in bridge.list_payload({"tag": "VFA"})] == [mid]
    assert [m["id"] for m in bridge.list_payload({"tag": "Product"})] == [other]
    assert [m["id"] for m in bridge.list_payload({"person": "Priya"})] == [other]
    assert [m["id"] for m in bridge.list_payload({})] == [mid, other]  # newest (13:17) first


def test_filters_people(library_path):
    lib, mid, bridge, d = _setup(library_path)
    lib.link_people(mid, [("Alex", None, "organizer")])
    assert bridge.filters_payload({})["people"] == [{"name": "Alex", "count": 1}]


def test_rename_round_trip(library_path):
    lib, mid, bridge, d = _setup(library_path)
    metas = bridge.rename_payload({"id": mid, "title": "Renamed 1:1"})
    assert metas[0]["title"] == "Renamed 1:1"
    assert bridge.get_payload({"id": mid})["title"] == "Renamed 1:1"


def test_rename_and_delete_apply_the_active_filter(library_path):
    # Ruling 4: Task 11 passes the page's active tag/person filter through,
    # so the returned list matches what's still on screen instead of
    # silently resetting to "all meetings". `third` is never touched and
    # stays in the library throughout, so an unfiltered list would always
    # include it — that's what makes the filtered assertions below actually
    # exercise the filter (deleting `other` alone would have made the
    # filtered and unfiltered results coincide).
    lib, mid, bridge, d = _setup(library_path)  # tagged "VFA"
    other = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0, 1, "hi")], duration_seconds=60,
        started_at=datetime(2026, 9, 24, 10, 0, tzinfo=TZ), title="Standup"))
    lib.save_notes(other, tags=["Product"])
    lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0, 1, "hi")], duration_seconds=60,
        started_at=datetime(2026, 9, 24, 8, 0, tzinfo=TZ), title="Retro"))

    metas = bridge.rename_payload({"id": mid, "title": "Renamed", "tag": "VFA"})
    assert [m["id"] for m in metas] == [mid]

    metas = bridge.delete_payload({"id": other, "tag": "VFA"})
    assert [m["id"] for m in metas] == [mid]
