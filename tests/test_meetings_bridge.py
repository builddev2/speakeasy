"""MeetingsBridge: pure-Python handlers behind the Meetings window.

Payload shapes are pinned to frontend/src/mock/meetings.ts (the approved
design). Formats follow the mock, not always the brief's original literals —
see task-10-report.md for the deviations and why.
"""

from datetime import datetime, timedelta, timezone

from speakeasy.meeting_library import MeetingLibrary, NewMeeting
from speakeasy.meetings import MeetingSegment
from speakeasy.ui.meetings_bridge import MeetingsBridge
from speakeasy.ui.webbridge import BridgeDispatcher

TZ = timezone(timedelta(hours=-4))


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
    bridge = MeetingsBridge(lib, now=lambda: datetime(2026, 9, 24, 15, 0, tzinfo=TZ))
    d = BridgeDispatcher()
    bridge.register(d)
    return lib, mid, bridge, d


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
    detail = bridge.get_payload({"id": mid})
    # MeetingDetail.tsx composes "date · time · duration" from these three
    # separate fields (metaLine), so `date` carries no time/year of its own —
    # controller ruling; mock uses e.g. "Thu 24 Sep".
    assert detail["date"] == "Thu 24 Sep"
    assert detail["summary"] == "Invest in VFA." and detail["actionItems"] == []
    assert detail["event"] is None
    assert detail["lines"][0]["start"] == 0.0
    # TranscriptLine.time is wall-clock (mock: "9:00:03 AM"), not an
    # elapsed-time bracket — controller ruling.
    assert detail["lines"][0]["time"] == "1:17:00 PM"
    assert detail["lines"][1]["time"] == "1:17:06 PM"


def test_filters_and_search(library_path):
    lib, mid, bridge, d = _setup(library_path)
    f = bridge.filters_payload({})
    assert f["total"] == 1 and f["tags"] == [{"name": "VFA", "count": 1}]
    assert f["features"] == {"calendar": False, "claude": False, "settings": False}
    res = bridge.search_payload({"query": "cognos"})
    assert res[0]["meetingId"] == mid and res[0]["seconds"] == 0.0
    assert {"text": "cognos", "hit": True} in res[0]["parts"]
    assert bridge.search_payload({"query": "   "}) == []


def test_missing_meeting_is_not_found_error(library_path):
    lib, mid, bridge, d = _setup(library_path)
    lib.delete(mid)
    js = _call(d, "meetings.get", {"id": mid})
    assert "_reject" in js and "not_found" in js


def test_relabel_and_delete_round_trip(library_path):
    lib, mid, bridge, d = _setup(library_path)
    detail = bridge.relabel_payload({"id": mid, "segmentIndex": 1, "label": "Alex",
                                     "allMatching": True})
    assert detail["lines"][1]["speakerLabel"] == "Alex"
    assert bridge.delete_payload({"id": mid}) == []


def test_library_status(library_path):
    lib, mid, bridge, d = _setup(library_path)
    assert bridge.status_payload({})["state"] == "idle"
    bridge.set_library_status({"state": "upgrading", "done": 1, "total": 2, "skipped": []})
    assert bridge.status_payload({})["done"] == 1


def test_subtitle_uses_linked_people_when_present(library_path):
    lib, mid, bridge, d = _setup(library_path)
    lib.link_people(mid, [("Alex", None, "organizer"), ("Priya", None, "attendee"),
                           ("Sam", None, "attendee")])
    metas = bridge.list_payload({})
    assert metas[0]["subtitle"] == "Alex, Priya, Sam"
    assert metas[0]["people"] == ["Alex", "Priya", "Sam"]
