"""MCP tool behaviour over a temp library (no subprocess; see test_mcp_server)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from speakeasy import meeting_store
from speakeasy.meeting_library import MeetingLibrary, NewMeeting
from speakeasy.meetings import MeetingSegment
from speakeasy.mcp_tools import ToolError, build_tools, read_last_used, record_use

PDT = timezone(timedelta(hours=-7))


def _seed(lib, day=24, title="Budget review", health=None):
    return lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0.0, 4.0, "Let's review the budget"),
                  MeetingSegment("Speaker 1", 5.0, 3725.0, "The cafe budget is fine")],
        duration_seconds=3730.0, title=title,
        started_at=datetime(2026, 9, day, 10, 0, 0, tzinfo=PDT),
        capture_health=health or {}))


@pytest.fixture
def lib(library_path):
    return MeetingLibrary()


@pytest.fixture
def tools(lib):
    return build_tools(lib)


def test_tool_names_order_and_definitions(tools):
    assert list(tools) == ["list_meetings", "get_meeting", "search_meetings",
                           "get_transcript", "get_calendar", "list_tags",
                           "list_people", "save_notes"]
    for t in tools.values():
        d = t.definition()
        assert d["name"] == t.name and d["description"]
        assert d["inputSchema"]["type"] == "object"
        assert d["annotations"]["readOnlyHint"] is (t.name != "save_notes")


def test_list_meetings_shape_and_paging(lib, tools):
    ids = [_seed(lib, day=d, title=f"M{d}") for d in (20, 21, 22)]
    page = tools["list_meetings"].run({"limit": 2})
    assert [m["title"] for m in page["meetings"]] == ["M22", "M21"]
    assert page["offset"] == 0 and page["next_offset"] == 2
    m = page["meetings"][0]
    assert m["id"] == ids[2]
    assert m["start"] == "2026-09-22T10:00-07:00"
    assert m["duration_minutes"] == 62.2
    assert m["speakers"] == ["You", "Speaker 1"]
    assert m["has_summary"] is False and m["tags"] == [] and m["people"] == []
    last = tools["list_meetings"].run({"limit": 2, "offset": 2})
    assert [m["title"] for m in last["meetings"]] == ["M20"]
    assert last["next_offset"] is None


def test_list_meetings_coerces_and_clamps_loose_numbers(lib, tools, monkeypatch):
    _seed(lib)
    assert len(tools["list_meetings"].run({"limit": "10"})["meetings"]) == 1
    assert tools["list_meetings"].run({"limit": 1000})["meetings"]  # clamped to 100
    # Pin the clamp itself: the tool asks the library for limit + 1 rows.
    seen = []
    real = lib.list_meetings
    monkeypatch.setattr(lib, "list_meetings",
                        lambda **kw: seen.append(kw["limit"]) or real(**kw))
    tools["list_meetings"].run({"limit": 1000})
    tools["list_meetings"].run({"limit": 0})
    assert seen == [101, 2]  # 1000 -> 100, 0 -> 1
    assert tools["list_meetings"].run({"offset": -5})["offset"] == 0
    with pytest.raises(ToolError, match="limit"):
        tools["list_meetings"].run({"limit": "ten"})
    with pytest.raises(ToolError, match="limit"):
        tools["list_meetings"].run({"limit": True})


def test_dates_are_validated(lib, tools):
    _seed(lib)
    with pytest.raises(ToolError, match="YYYY-MM-DD"):
        tools["list_meetings"].run({"from": "28/09/2026"})
    with pytest.raises(ToolError, match="after"):
        tools["list_meetings"].run({"from": "2026-09-30", "to": "2026-09-01"})
    assert tools["list_meetings"].run({"from": "2026-09-01", "to": "2026-09-30"})["meetings"]


def test_get_meeting_never_exposes_capture_fields(lib, tools):
    mid = _seed(lib, health={"mic_first_buffer": True, "system_dropped_frames": 3})
    result = tools["get_meeting"].run({"id": mid})
    text = json.dumps(result)
    for key in ("capture", "mic_first_buffer", "system_dropped_frames",
                "track_offsets", "system_audio_status"):
        assert key not in text
    assert result["segment_count"] == 2 and result["notes"] is None
    assert result["calendar_event"] is None and result["source"] == "recorded"
    assert "segments" not in result  # no transcript in get_meeting


def test_get_meeting_bad_and_missing_ids(tools):
    with pytest.raises(ToolError, match="Invalid meeting id"):
        tools["get_meeting"].run({"id": "../etc"})
    with pytest.raises(ToolError, match="id is required"):
        tools["get_meeting"].run({})
    # MeetingNotFound is mapped by the server (Task 3), not here.
    from speakeasy.meeting_library import MeetingNotFound
    with pytest.raises(MeetingNotFound):
        tools["get_meeting"].run({"id": "20200101-000000-abcd"})


def test_search_meetings_markdown_snippets_and_elapsed_time(lib, tools):
    mid = _seed(lib)
    result = tools["search_meetings"].run({"query": "café"})
    hit = result["results"][0]
    assert hit["meeting_id"] == mid and hit["kind"] == "transcript"
    assert hit["speaker"] == "Speaker 1" and hit["at"] == "00:00:05"
    assert "**cafe**" in hit["snippet"].lower() and "\x02" not in hit["snippet"]
    assert tools["search_meetings"].run({"query": 'C++ "unbalanced'})["results"] == []
    with pytest.raises(ToolError, match="query"):
        tools["search_meetings"].run({"query": "   "})


def test_search_finds_notes_and_action_items_written_by_claude(lib, tools):
    # Parked from phase 1: notes had no writer until save_notes existed.
    mid = _seed(lib)
    tools["save_notes"].run({"id": mid, "summary": "Quarterly forecast agreed",
                             "action_items": ["Email the spreadsheet to Priya"]})
    hits = tools["search_meetings"].run({"query": "spreadsheet"})["results"]
    assert [(h["meeting_id"], h["kind"]) for h in hits] == [(mid, "notes")]
    assert hits[0]["at"] is None and hits[0]["speaker"] is None


def test_get_transcript_format_and_cursor(lib, tools):
    mid = _seed(lib)
    page = tools["get_transcript"].run({"id": mid})
    assert page["text"].splitlines() == [
        "[00:00:00] You: Let's review the budget",
        "[00:00:05] Speaker 1: The cafe budget is fine",
    ]
    assert page["next_cursor"] is None and page["timestamps_approximate"] is False
    ranged = tools["get_transcript"].run({"id": mid, "start_seconds": 4.5})
    assert ranged["text"].startswith("[00:00:05]")
    with pytest.raises(ToolError, match="cursor"):
        tools["get_transcript"].run({"id": mid, "cursor": "abc"})


def test_get_transcript_elapsed_time_over_an_hour(lib, tools):
    mid = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 3725.0, 3730.0, "late remark")],
        duration_seconds=3730.0, started_at=datetime(2026, 9, 24, 10, 0, tzinfo=PDT)))
    assert tools["get_transcript"].run({"id": mid})["text"] == "[01:02:05] You: late remark"


def test_get_calendar_requires_range_and_returns_linked_ids(lib, tools, library_path):
    assert tools["get_calendar"].run({"from": "2026-09-01", "to": "2026-09-30"}) == {"events": []}
    with pytest.raises(ToolError, match="from"):
        tools["get_calendar"].run({"to": "2026-09-30"})
    with pytest.raises(ToolError, match="366"):
        tools["get_calendar"].run({"from": "2024-01-01", "to": "2026-01-01"})
    conn = meeting_store.connect(library_path)
    with conn:
        conn.execute("INSERT INTO calendar_events (event_key, calendar_name, title, start_utc,"
                     " end_utc, all_day, declined, synced_at) VALUES ('K1', 'Work', 'Standup',"
                     " '2026-09-24T17:00:00Z', '2026-09-24T17:30:00Z', 0, 0, 'x')")
    conn.close()
    ev = tools["get_calendar"].run({"from": "2026-09-01", "to": "2026-09-30"})["events"][0]
    assert ev["id"] == "K1" and ev["title"] == "Standup" and ev["calendar"] == "Work"
    assert ev["meeting_ids"] == [] and ev["all_day"] is False


def test_save_notes_replaces_only_given_fields(lib, tools):
    mid = _seed(lib)
    first = tools["save_notes"].run({"id": mid, "summary": "S1", "tags": ["budget"]})
    assert first["summary"] == "S1" and first["tags"] == ["budget"]
    assert first["updated_by"] == "claude"
    second = tools["save_notes"].run({"id": mid, "action_items": ["A"]})
    assert second["summary"] == "S1" and second["action_items"] == ["A"]
    assert second["tags"] == ["budget"]
    assert tools["list_tags"].run({}) == {"tags": [{"name": "budget", "count": 1}]}


def test_save_notes_rejects_wrong_shapes(lib, tools):
    mid = _seed(lib)
    with pytest.raises(ToolError, match="at least one"):
        tools["save_notes"].run({"id": mid})
    with pytest.raises(ToolError, match="action_items"):
        tools["save_notes"].run({"id": mid, "action_items": "one string"})
    with pytest.raises(ToolError, match="tags"):
        tools["save_notes"].run({"id": mid, "tags": [1, 2]})
    with pytest.raises(ToolError, match="summary"):
        tools["save_notes"].run({"id": mid, "summary": 5})
    with pytest.raises(ValueError, match="20,000"):  # library message; server maps it
        tools["save_notes"].run({"id": mid, "summary": "x" * 20_001})


def test_list_people_empty_until_phase3(tools):
    assert tools["list_people"].run({}) == {"people": []}
    assert tools["list_people"].run({"query": "pri"}) == {"people": []}


def test_record_and_read_last_used(tmp_path):
    path = tmp_path / "mcp_last_used"
    assert read_last_used(path) is None
    record_use(path, now=datetime(2026, 9, 28, 18, 0, 5, tzinfo=timezone.utc))
    assert path.read_text() == "2026-09-28T18:00:05Z"
    assert read_last_used(path) == datetime(2026, 9, 28, 18, 0, 5, tzinfo=timezone.utc)
    path.write_text("garbage")
    assert read_last_used(path) is None
    record_use(tmp_path / "missing-dir" / "x")  # OSError swallowed: never breaks a call


def test_get_calendar_lists_people(library_path):
    from speakeasy.meeting_library import EventPerson, SyncedEvent
    lib = MeetingLibrary(library_path)
    start = datetime.now(timezone.utc).replace(microsecond=0)
    lib.replace_calendar_window([SyncedEvent(
        "k", "Work", "1:1", start, start + timedelta(minutes=30), False, False, 1,
        (EventPerson("Refayet K", None, "organizer"),))],
        start - timedelta(days=1), start + timedelta(days=1))
    day = start.astimezone().date().isoformat()
    out = build_tools(lib)["get_calendar"].run({"from": day, "to": day})
    assert out["events"][0]["people"] == ["Refayet K"]


class NoFullLoad(MeetingLibrary):
    def get_meeting(self, meeting_id, *, with_segments=True):
        assert not with_segments, "the MCP tools must not load every segment"
        return super().get_meeting(meeting_id, with_segments=False)


def test_transcript_and_meeting_tools_avoid_full_loads(library_path):
    lib = NoFullLoad(library_path)
    mid = _seed(lib)   # 2 segments: "You", "Speaker 1"
    tools = build_tools(lib)
    page = tools["get_transcript"].run({"id": mid})
    assert page["title"] == "Budget review" and page["timestamps_approximate"] is False
    meta = tools["get_meeting"].run({"id": mid})
    assert meta["segment_count"] == 2 and meta["speakers"] == ["You", "Speaker 1"]
