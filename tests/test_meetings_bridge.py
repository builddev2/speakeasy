"""MeetingsBridge: pure-Python handlers behind the Meetings window.

Payload shapes are pinned to frontend/src/mock/meetings.ts (the approved
design). Formats follow the mock, not always the brief's original literals —
see task-10-report.md for the deviations and why.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from speakeasy.meeting_library import MeetingLibrary, MeetingNotFound, NewMeeting
from speakeasy.meeting_options import MeetingOptions
from speakeasy.meetings import MeetingSegment
from speakeasy.ui.meetings_bridge import MeetingsBridge
from speakeasy.ui.webbridge import BridgeDispatcher

TZ = timezone(timedelta(hours=-4))
NOW = lambda: datetime(2026, 9, 24, 15, 0, tzinfo=TZ)  # noqa: E731 — Today


def make_bridge(tmp_path, **kw):
    return MeetingsBridge(library=MeetingLibrary(tmp_path / "l.sqlite"), **kw)


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
        "hasUserNotes": False, "userNotes": {"markdown": "", "stamps": []},
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
        "summaryBlocks": [{"kind": "para", "text": "Invest in VFA."}],
        "summaryQueued": False,
        "actionItems": [],
        "event": None,
    }


def test_request_summary_queues_and_returns_detail(library_path):
    lib, mid, bridge, d = _setup(library_path)
    assert "meetings.requestSummary" in d._methods
    detail = bridge.request_summary_payload({"id": mid})
    assert detail["summaryQueued"] is True and detail["id"] == mid
    assert lib.pending_summaries(now=NOW()) == [(mid, True)]


def test_recent_unsummarised_meeting_shows_queued(library_path):
    lib, mid, bridge = _meeting(
        library_path, segments=[MeetingSegment("You", 0, 5, "hi")],
        started_at=datetime(2026, 9, 24, 10, 0, tzinfo=TZ), duration=600)
    assert bridge.get_payload({"id": mid})["summaryQueued"] is True
    assert bridge.get_payload({"id": mid})["summaryBlocks"] == []


def test_request_summary_unknown_id_is_not_found(library_path):
    _, _, _, d = _setup(library_path)
    js = _call(d, "meetings.requestSummary", {"id": "20990101-000000-dead"})
    assert "_reject" in js and "not_found" in js


def test_filters_and_search(library_path):
    lib, mid, bridge, d = _setup(library_path)
    f = bridge.filters_payload({})
    assert f["total"] == 1 and f["tags"] == [{"name": "VFA", "count": 1}]
    assert f["features"] == {"calendar": False, "claude": True, "settings": True}

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
    # Regression: App.tsx's copy helper posts
    # meetings.copyText with {"text": ...}. The handler lives on the bridge
    # (pure-Python) precisely so its registration and its exact clipboard
    # call are unit-testable here — a prior round silently renamed the
    # registered method and dropped the payload without any test catching it.
    captured = []
    bridge = MeetingsBridge(set_clipboard=captured.append)
    d = BridgeDispatcher()
    bridge.register(d)
    assert "meetings.copyText" in d._methods

    js = _call(d, "meetings.copyText", {"text": "hello clipboard"})
    assert "_resolve" in js
    assert captured == ["hello clipboard"]


def test_copy_payload_registers_and_renders_via_injected_clipboard(library_path):
    # Regression: meetings_window.py's old bespoke _copy (ObjC glue) had its
    # own manual try/except MeetingNotFound -> "not_found" that no test
    # covered; a prior fix round changed it to reject with "MeetingNotFound"
    # instead and all tests still passed. meetings.copy now lives on
    # MeetingsBridge and goes through `register()`/`_wrap` like every other
    # handler, so both the registration and the not_found conversion are
    # exercised here.
    lib, mid, bridge = _meeting(
        library_path, segments=[MeetingSegment("You", 0, 1, "hi there")],
        started_at=datetime(2026, 9, 24, 9, 0, tzinfo=TZ), title="Copy Me")
    captured = []
    bridge = MeetingsBridge(lib, now=NOW, set_clipboard=captured.append)
    d = BridgeDispatcher()
    bridge.register(d)
    assert "meetings.copy" in d._methods

    lib.set_user_notes(mid, "remember the deck", [])
    js = _call(d, "meetings.copy", {"id": mid})
    assert "_resolve" in js
    assert len(captured) == 1
    assert "Copy Me" in captured[0]
    assert "hi there" in captured[0]
    assert "## My notes" in captured[0] and "remember the deck" in captured[0]


def test_copy_payload_missing_meeting_is_not_found_error(library_path):
    lib, mid, bridge = _meeting(
        library_path, segments=[MeetingSegment("You", 0, 1, "hi")],
        started_at=datetime(2026, 9, 24, 9, 0, tzinfo=TZ))
    lib.delete(mid)
    captured = []
    bridge = MeetingsBridge(lib, now=NOW, set_clipboard=captured.append)
    d = BridgeDispatcher()
    bridge.register(d)

    js = _call(d, "meetings.copy", {"id": mid})
    assert "_reject" in js and "not_found" in js
    assert captured == []


def test_export_meeting_returns_the_meeting(library_path):
    lib, mid, bridge = _meeting(
        library_path, segments=[MeetingSegment("You", 0, 1, "hi")],
        started_at=datetime(2026, 9, 24, 9, 0, tzinfo=TZ), title="Export Me")
    meeting = bridge.export_meeting({"id": mid})
    assert meeting.meeting_id == mid
    assert meeting.title == "Export Me"


def test_export_meeting_missing_raises_meeting_not_found(library_path):
    # meetings_window.py's _export can't route the whole call through
    # `_wrap` (it responds asynchronously, from the NSSavePanel completion
    # handler), so it runs this lookup through `_wrap` itself for the
    # synchronous not_found pre-check. Pin both ends of that contract here:
    # export_meeting raises MeetingNotFound directly...
    lib, mid, bridge = _meeting(
        library_path, segments=[MeetingSegment("You", 0, 1, "hi")],
        started_at=datetime(2026, 9, 24, 9, 0, tzinfo=TZ))
    lib.delete(mid)
    with pytest.raises(MeetingNotFound):
        bridge.export_meeting({"id": mid})

    # ...and wrapping it with the same `_wrap` every other handler uses
    # converts that into the standard "not_found" rejection — the exact
    # mechanism meetings_window.py's _export relies on. A mutation that
    # reverted _export to respond with "MeetingNotFound" (the raw exception
    # string) instead of "not_found" left the full suite passing before this
    # test existed; it must fail now.
    responses = []
    MeetingsBridge._wrap(bridge.export_meeting)({"id": mid}, lambda result=None, error=None: responses.append((result, error)))
    assert responses == [(None, "not_found")]


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


# -- phase 2: Connect Claude + change poller ---------------------------------

def _new():
    return NewMeeting(
        segments=[MeetingSegment("You", 0, 5, "hello")], duration_seconds=60,
        started_at=datetime(2026, 9, 24, 13, 17, tzinfo=TZ), title="Polled")


def _bcall(bridge, method, params=None):
    d = BridgeDispatcher()
    bridge.register(d)
    got = {}
    d._methods[method](params or {}, lambda result=None, error=None: got.update(r=result, e=error))
    return got


def test_features_claude_is_on(library_path):
    assert MeetingsBridge().filters_payload({})["features"]["claude"] is True


def test_claude_setup_info_registered(library_path, monkeypatch):
    from speakeasy import mcp_setup
    monkeypatch.setattr(mcp_setup, "current_setup_info", lambda now: {"command": "c"})
    assert _bcall(MeetingsBridge(), "claude.setupInfo") == {"r": {"command": "c"}, "e": None}


def test_install_extension_opens_bundle_or_errors(library_path, monkeypatch, tmp_path):
    from speakeasy import mcp_setup
    opened = []
    bridge = MeetingsBridge(open_path=opened.append)
    monkeypatch.setattr(mcp_setup, "current_setup_info",
                        lambda now: {"extensionAvailable": False})
    assert _bcall(bridge, "claude.installExtension")["e"] == "extension_unavailable"
    mcpb = tmp_path / "Speakeasy.mcpb"
    monkeypatch.setattr(mcp_setup, "current_setup_info",
                        lambda now: {"extensionAvailable": True})
    monkeypatch.setattr(mcp_setup, "current_mcpb_path", lambda: mcpb)
    assert _bcall(bridge, "claude.installExtension") == {"r": True, "e": None}
    assert opened == [mcpb]


def test_reveal_config_opens_folder_or_errors(library_path, monkeypatch, tmp_path):
    opened = []
    bridge = MeetingsBridge(open_path=opened.append)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _bcall(bridge, "claude.revealConfig")["e"] == "claude_desktop_not_found"
    folder = tmp_path / "Library/Application Support/Claude"
    folder.mkdir(parents=True)
    assert _bcall(bridge, "claude.revealConfig") == {"r": True, "e": None}
    assert opened == [folder]


def test_poll_changed_detects_mcp_writes_only_after_baseline(library_path):
    lib = MeetingLibrary()
    bridge = MeetingsBridge(library=lib)
    assert bridge.poll_changed() is False  # first call only sets the baseline
    assert bridge.poll_changed() is False
    mid = lib.save_meeting(_new())
    assert bridge.poll_changed() is True
    assert bridge.poll_changed() is False
    MeetingLibrary().save_notes(mid, summary="From Claude")  # another "process"
    assert bridge.poll_changed() is True


def test_poll_changed_swallows_a_busy_library(library_path, monkeypatch):
    import sqlite3
    bridge = MeetingsBridge()
    bridge.poll_changed()

    def locked():
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(bridge._watcher, "changed", locked)
    assert bridge.poll_changed() is False


def test_poll_changed_swallows_a_corrupt_library(library_path, monkeypatch):
    import sqlite3
    bridge = MeetingsBridge()
    bridge.poll_changed()

    def corrupt():
        raise sqlite3.DatabaseError("database disk image is malformed")
    monkeypatch.setattr(bridge._watcher, "changed", corrupt)
    assert bridge.poll_changed() is False


def test_stop_polling_resets_baseline(library_path):
    lib = MeetingLibrary()
    bridge = MeetingsBridge(library=lib)
    bridge.poll_changed()
    lib.save_meeting(_new())
    bridge.stop_polling()
    assert bridge.poll_changed() is False  # window reopened: fresh baseline


# -- calendar (phase 3) ------------------------------------------------

class FakeCalendar:
    def __init__(self, access="connected"):
        self._access, self.calendars, self.asked, self.synced = access, [], 0, 0
    def access(self): return self._access
    def request_access(self): self.asked += 1
    def request_sync(self): self.synced += 1


def _calendar_bridge(library_path, **kw):
    return MeetingsBridge(library=MeetingLibrary(library_path), calendar=kw.pop("calendar", FakeCalendar()),
                          now=lambda: datetime.now().astimezone(), **kw)


def test_features_follow_calendar_presence(library_path):
    assert _calendar_bridge(library_path).filters_payload({})["features"] == {
        "calendar": True, "claude": True, "settings": True}
    plain = MeetingsBridge(library=MeetingLibrary(library_path))
    assert plain.filters_payload({})["features"]["calendar"] is False


def _seed_today_and_tomorrow(library_path):
    from speakeasy.meeting_library import SyncedEvent
    noon = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    events = [SyncedEvent(key, "Work", title, start, start + timedelta(minutes=30),
                          False, False, 1, ())
              for key, title, start in (("today1", "Standup", noon),
                                        ("tmrw1", "Planning", noon + timedelta(days=1)))]
    MeetingLibrary(library_path).replace_calendar_window(
        events, noon - timedelta(days=2), noon + timedelta(days=3))


@pytest.mark.parametrize("access", ["denied", "unconnected"])
def test_today_and_upcoming_are_gated_on_access(library_path, access):
    _seed_today_and_tomorrow(library_path)
    b = _calendar_bridge(library_path, calendar=FakeCalendar(access))
    assert b.calendar_today_payload({}) == {"access": access, "agenda": []}
    assert b.calendar_upcoming_payload({}) == {"days": []}


def test_today_and_upcoming_show_cached_events_when_connected(library_path):
    _seed_today_and_tomorrow(library_path)
    b = _calendar_bridge(library_path)
    today = b.calendar_today_payload({})
    assert today["access"] == "connected"
    assert [r["key"] for r in today["agenda"]] == ["today1"]
    days = b.calendar_upcoming_payload({})["days"]
    assert [[e["key"] for e in d["events"]] for d in days] == [["tmrw1"]]


def test_only_the_connect_click_requests_access(library_path, tmp_path, monkeypatch):
    from speakeasy import settings
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    started = []
    cal = FakeCalendar()
    b = _calendar_bridge(library_path, calendar=cal, begin_meeting=started.append,
                         open_url=lambda url: None)
    lib = MeetingLibrary(library_path)
    mid = lib.save_meeting(NewMeeting(segments=[MeetingSegment("You", 0, 1, "hi")],
                                      duration_seconds=60,
                                      started_at=datetime.now().astimezone()))
    b.calendar_today_payload({})
    b.calendar_upcoming_payload({})
    b.settings_get_payload({})
    b.settings_set_payload({"offerToRecord": True, "calendars": {"w": True}})
    b.calendar_record_payload({"key": "ev1"})
    b.calendar_privacy_payload({})
    b.events_for_day_payload({"id": mid})
    b.link_event_payload({"id": mid, "key": None})
    b.filters_payload({})
    assert cal.asked == 0
    assert b.calendar_request_access_payload({}) is True
    assert cal.asked == 1


def test_record_passes_event_key(library_path):
    started = []
    b = _calendar_bridge(library_path, begin_meeting=started.append)
    assert b.calendar_record_payload({"key": "ev1"}) is True
    assert started[0].calendar_event_key == "ev1"


def test_record_rejects_bad_key(library_path):
    import pytest
    b = _calendar_bridge(library_path, begin_meeting=lambda o: None)
    with pytest.raises(ValueError):
        b.calendar_record_payload({"key": 5})


def test_link_event_round_trip(library_path):
    from speakeasy.meeting_library import NewMeeting, SyncedEvent
    from speakeasy.meetings import MeetingSegment
    lib = MeetingLibrary(library_path)
    now = datetime.now().astimezone().replace(microsecond=0)
    mid = lib.save_meeting(NewMeeting(segments=[MeetingSegment("You", 0, 1, "hi")],
                                      duration_seconds=60, started_at=now))
    start = now - timedelta(minutes=5)
    lib.replace_calendar_window(
        [SyncedEvent("ev1", "Work", "Weekly 1:1", start, start + timedelta(minutes=30),
                     False, False, 1, ())],
        start - timedelta(hours=1), start + timedelta(hours=1))
    b = _calendar_bridge(library_path)
    assert [c["key"] for c in b.events_for_day_payload({"id": mid})] == ["ev1"]
    linked = b.link_event_payload({"id": mid, "key": "ev1"})
    assert linked["event"]["key"] == "ev1" and linked["title"] == "Weekly 1:1"
    assert b.link_event_payload({"id": mid, "key": None})["event"] is None


def test_meeting_settings_round_trip(library_path, tmp_path, monkeypatch):
    from speakeasy import settings
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    b = _calendar_bridge(library_path)
    out = b.settings_set_payload({"offerToRecord": False, "calendars": {"w": False}})
    assert out["offerToRecord"] is False
    assert settings.get_meeting_settings()["calendar_choices"] == {"w": False}
    assert b._calendar.synced == 1
    out = b.settings_set_payload({"detectCalls": False})
    assert out["detectCalls"] is False
    assert settings.get_meeting_settings()["detect_calls"] is False


def test_settings_round_trip_appearance(library_path, tmp_path, monkeypatch):
    from speakeasy import settings
    from speakeasy.ui import appearance
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    applied = []
    monkeypatch.setattr(appearance, "apply", applied.append)
    b = _calendar_bridge(library_path)
    assert b.settings_get_payload({})["appearance"] == "system"
    out = b.settings_set_payload({"appearance": "dark"})
    assert out["appearance"] == "dark" and applied == ["dark"]
    with pytest.raises(ValueError):
        b.settings_set_payload({"appearance": "neon"})
    assert applied == ["dark"]


def test_get_payload_never_looks_up_a_missing_event_key(library_path, monkeypatch):
    lib = MeetingLibrary(library_path)
    mid = lib.save_meeting(NewMeeting(segments=[MeetingSegment("You", 0, 1, "hi")],
                                      duration_seconds=60,
                                      started_at=datetime.now().astimezone()))
    monkeypatch.setattr(lib, "calendar_event",
                        lambda key: pytest.fail("looked up an unlinked meeting's event"))
    assert MeetingsBridge(library=lib).get_payload({"id": mid})["event"] is None


def test_user_notes_round_trip_through_the_bridge(library_path):
    lib, mid, bridge, d = _setup(library_path)
    got = _bcall(bridge, "notes.user.set", {"id": mid, "markdown": "deck", "stamps": [[0, 12.5]]})
    assert got["e"] is None and got["r"]["updatedAt"]
    detail = bridge.get_payload({"id": mid})
    assert detail["hasUserNotes"] is True
    assert detail["userNotes"] == {"markdown": "deck", "stamps": [[0, 12.5]]}
    assert _bcall(bridge, "notes.user.set", {"id": mid, "markdown": "x" * 200_001, "stamps": []})["e"] \
        == "Notes are too long to save"
    assert _bcall(bridge, "notes.user.set", {"id": "20260101-000000-abcd", "markdown": "x",
                                             "stamps": []})["e"] == "not_found"


def test_meeting_without_user_notes(library_path):
    lib, mid, bridge, d = _setup(library_path)
    detail = bridge.get_payload({"id": mid})
    assert detail["hasUserNotes"] is False
    assert detail["userNotes"] == {"markdown": "", "stamps": []}


def test_draft_calls(library_path):
    lib, mid, bridge, d = _setup(library_path)
    assert _bcall(bridge, "notes.draft.get")["r"] is None
    _bcall(bridge, "notes.draft.set", {"markdown": "live", "stamps": [[0, 5]],
                                      "startedAt": "2026-09-24T17:17:00Z"})
    assert _bcall(bridge, "notes.draft.get")["r"] == {
        "markdown": "live", "stamps": [[0, 5.0]], "startedAt": "2026-09-24T17:17:00Z"}
    got = _bcall(bridge, "notes.draft.finish", {"id": mid, "markdown": "live!", "stamps": [],
                                               "startedAt": "2026-09-24T17:17:00Z"})
    assert got["e"] is None
    assert lib.get_user_notes(mid).markdown == "live!" and lib.get_draft() is None
    _bcall(bridge, "notes.draft.set", {"markdown": "x", "stamps": [], "startedAt": "2026-09-24T17:17:00Z"})
    assert _bcall(bridge, "notes.draft.discard")["r"] is True
    assert lib.get_draft() is None


def test_recording_get_defaults_and_uses_the_injected_reader(library_path):
    assert _bcall(MeetingsBridge(), "recording.get")["r"] == {
        "recording": False, "processing": False, "startedAt": None, "title": None,
        "mode": "", "micFailure": None, "startError": None, "processingError": None}
    info = {"recording": True, "processing": False, "startedAt": "2026-09-24T17:17:00Z", "title": "1:1"}
    got = _bcall(MeetingsBridge(recording_info=lambda: info), "recording.get")["r"]
    assert {k: got[k] for k in info} == info and got["mode"] == ""


def test_search_reports_user_notes_kind(library_path):
    lib, mid, bridge, d = _setup(library_path)
    lib.set_user_notes(mid, "vendor contract", [])
    assert [r["kind"] for r in bridge.search_payload({"query": "vendor"})] == ["user_notes"]


def test_null_markdown_is_empty_not_the_word_none(library_path):
    lib, mid, bridge, d = _setup(library_path)
    _bcall(bridge, "notes.user.set", {"id": mid, "markdown": "keep", "stamps": []})
    _bcall(bridge, "notes.user.set", {"id": mid, "markdown": None, "stamps": []})
    assert lib.get_user_notes(mid) is None or lib.get_user_notes(mid).markdown == ""
    _bcall(bridge, "notes.draft.set", {"markdown": None, "stamps": [],
                                      "startedAt": "2026-09-24T17:17:00Z"})
    assert lib.get_draft().markdown == ""
    _bcall(bridge, "notes.draft.finish", {"id": mid, "markdown": None, "stamps": [],
                                         "startedAt": "2026-09-24T17:17:00Z"})
    assert lib.get_user_notes(mid) is None or lib.get_user_notes(mid).markdown == ""


def test_meeting_start_uses_default_options(tmp_path):
    started = []
    bridge = make_bridge(tmp_path, begin_meeting=started.append)
    assert bridge.start_meeting_payload({}) is True
    assert started == [MeetingOptions()]


def _enrol_ana(monkeypatch):
    import speakeasy.voice_profiles as vp
    from speakeasy import settings
    settings.set_identify_voices(True)
    monkeypatch.setattr(vp.VoiceProfileStore, "names", lambda self: ["Ana"])


def test_meeting_start_applies_identify_voices(tmp_path, monkeypatch):
    _enrol_ana(monkeypatch)
    started = []
    make_bridge(tmp_path, begin_meeting=started.append).start_meeting_payload({})
    assert started[0].expected_voice_profile_names == ("Ana",)


def test_calendar_record_applies_identify_voices(tmp_path, monkeypatch):
    _enrol_ana(monkeypatch)
    started = []
    make_bridge(tmp_path, begin_meeting=started.append).calendar_record_payload({"key": "ev-9"})
    assert started[0].expected_voice_profile_names == ("Ana",)
    assert started[0].calendar_event_key == "ev-9"


def test_calendar_record_uses_default_options(tmp_path):
    started = []
    bridge = make_bridge(tmp_path, begin_meeting=started.append)
    bridge.calendar_record_payload({"key": "ev-9"})
    assert started == [MeetingOptions(calendar_event_key="ev-9")]


def test_settings_include_identify_voices_and_login(tmp_path):
    state = {"login": False}
    bridge = make_bridge(tmp_path, login_status=lambda: state["login"],
                         set_login=lambda on: state.update(login=on))
    got = bridge.settings_get_payload({})
    assert got["identifyVoices"] is False and got["startAtLogin"] is False
    got = bridge.settings_set_payload({"identifyVoices": True, "startAtLogin": True})
    assert got["identifyVoices"] is True and got["startAtLogin"] is True


def test_login_unavailable_reads_null_and_refuses_set(tmp_path):
    bridge = make_bridge(tmp_path)
    assert bridge.settings_get_payload({})["startAtLogin"] is None
    with pytest.raises(ValueError, match="Start at login is available in the installed app."):
        bridge.settings_set_payload({"startAtLogin": True})


def test_take_navigation_returns_once(tmp_path):
    bridge = make_bridge(tmp_path)
    assert bridge.take_navigation_payload({}) is None
    bridge.set_navigation("recording")
    assert bridge.take_navigation_payload({}) == "recording"
    assert bridge.take_navigation_payload({}) is None


def test_latest_navigation_wins(tmp_path):
    bridge = make_bridge(tmp_path)
    bridge.set_navigation("settings")
    bridge.set_navigation({"meeting": "m-1"})
    assert bridge.take_navigation_payload({}) == {"meeting": "m-1"}


@pytest.mark.parametrize("bad", ["today", {"meeting": ""}, {"meeting": "x" * 201}, {"other": "m"}, 3])
def test_unknown_navigation_is_refused(tmp_path, bad):
    with pytest.raises(ValueError, match="Unknown page."):
        make_bridge(tmp_path).set_navigation(bad)


def test_take_navigation_is_registered_on_the_dispatcher(tmp_path):
    bridge = make_bridge(tmp_path)
    d = BridgeDispatcher()
    bridge.register(d)
    bridge.set_navigation("settings")
    first = _call(d, "meetings.takeNavigation")
    assert "_resolve" in first and "settings" in first
    second = _call(d, "meetings.takeNavigation")
    assert "_resolve" in second and "null" in second


def test_meeting_start_is_registered_on_the_dispatcher(tmp_path):
    started = []
    bridge = make_bridge(tmp_path, begin_meeting=started.append)
    d = BridgeDispatcher()
    bridge.register(d)
    js = _call(d, "meeting.start")
    assert "_resolve" in js and "true" in js
    assert len(started) == 1


def test_retry_microphone_calls_engine(tmp_path):
    calls = []
    bridge = make_bridge(tmp_path, retry_microphone=lambda: calls.append(1) or True)
    assert bridge.retry_microphone_payload({}) is True and calls == [1]


def test_recording_get_defaults_include_engine_fields(tmp_path):
    got = make_bridge(tmp_path).recording_payload({})
    assert got["mode"] == "" and got["micFailure"] is None
    assert got["startError"] is None and got["processingError"] is None
