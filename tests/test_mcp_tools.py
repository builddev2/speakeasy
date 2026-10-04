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
                           "list_people", "save_notes", "tag_meetings", "manage_tags",
                           "pending_summaries"]
    writers = {"save_notes", "tag_meetings", "manage_tags"}
    for t in tools.values():
        d = t.definition()
        assert d["name"] == t.name and d["description"]
        assert d["inputSchema"]["type"] == "object"
        assert d["annotations"]["readOnlyHint"] is (t.name not in writers)


def test_list_meetings_shape_and_paging(lib, tools):
    ids = [_seed(lib, day=d, title=f"M{d}") for d in (20, 21, 22)]
    page = tools["list_meetings"].run({"limit": 2})
    assert [m["title"] for m in page["meetings"]] == ["M22", "M21"]
    assert page["offset"] == 0 and page["next_offset"] == 2
    m = page["meetings"][0]
    assert m["id"] == ids[2]
    assert m["start"] == "2026-09-22T10:00-07:00"
    assert m["duration_minutes"] == 62.2
    assert "speakers" not in m
    assert m["named_speakers"] == ["You"] and m["speaker_count"] == 2
    assert m["has_summary"] is False
    assert "tags" not in m and "people" not in m
    assert "timestamps_approximate" not in m
    last = tools["list_meetings"].run({"limit": 2, "offset": 2})
    assert [m["title"] for m in last["meetings"]] == ["M20"]
    assert last["next_offset"] is None


def test_list_meetings_timestamps_approximate_only_when_true(lib, tools):
    normal = _seed(lib, day=20, title="Normal")
    approx = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0.0, 4.0, "hi")], duration_seconds=4.0,
        title="Approx", started_at=datetime(2026, 9, 21, 10, 0, tzinfo=PDT),
        timestamps_approximate=True))
    rows = {m["id"]: m for m in tools["list_meetings"].run({})["meetings"]}
    assert "timestamps_approximate" not in rows[normal]
    assert rows[approx]["timestamps_approximate"] is True


def test_list_meetings_people_and_tags_only_when_present(lib, tools):
    from speakeasy.meeting_library import EventPerson
    bare = _seed(lib, day=20, title="Bare")
    rich = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0.0, 4.0, "hi")], duration_seconds=4.0,
        title="Rich", started_at=datetime(2026, 9, 21, 10, 0, tzinfo=PDT),
        people=[EventPerson("Refayet K", None, "organizer")]))
    tools["tag_meetings"].run({"ids": [rich], "add": ["Ops"]})
    rows = {m["id"]: m for m in tools["list_meetings"].run({})["meetings"]}
    assert "people" not in rows[bare] and "tags" not in rows[bare]
    assert rows[rich]["people"] == ["Refayet K"] and rows[rich]["tags"] == ["Ops"]


def test_list_meetings_title_filter_and_paging(lib, tools):
    ids = [_seed(lib, day=d, title=t) for d, t in
           ((20, "1on1 Todd TEST"), (21, "Budget review"), (22, "Todd standup"),
            (23, "todd retro"))]
    page = tools["list_meetings"].run({"title": "TODD", "limit": 2})
    assert [m["title"] for m in page["meetings"]] == ["todd retro", "Todd standup"]
    assert page["next_offset"] == 2
    rest = tools["list_meetings"].run({"title": "TODD", "limit": 2, "offset": 2})
    assert [m["id"] for m in rest["meetings"]] == [ids[0]]
    assert rest["next_offset"] is None
    assert [m["id"] for m in tools["list_meetings"].run({"title": "test 1on1"})["meetings"]] == [ids[0]]
    assert tools["list_meetings"].run({"title": "nobody"})["meetings"] == []


def test_title_lookup_is_described(tools):
    listing = tools["list_meetings"].definition()
    assert "title" in listing["inputSchema"]["properties"]
    assert "find a meeting by name" in listing["description"]
    search = tools["search_meetings"].definition()
    assert "title" not in search["inputSchema"]["properties"]
    assert "Search meetings by title, date and content" in search["description"]
    assert "a date alone lists that day's meetings" in search["description"]


def test_search_meetings_title_and_date_hits(lib, tools):
    mid = _seed(lib, title="Budget review")
    hits = tools["search_meetings"].run({"query": "review"})["results"]
    assert hits[0] == {
        "meeting_id": mid, "title": "Budget review", "meeting_start": hits[0]["meeting_start"],
        "kind": "meeting", "speaker": None, "also_speakers": [], "at": None,
        "start_seconds": None, "snippet": "Budget **review**"}
    day = tools["search_meetings"].run({"query": "2026-09-24"})["results"]
    assert [(h["meeting_id"], h["kind"]) for h in day] == [(mid, "meeting")]


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
    assert tools["list_tags"].run({}) == {"tags": [
        {"name": "budget", "count": 1, "description": "", "aliases": []}]}
    assert first["created_tags"] == ["budget"] and first["suppressed"] == []


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


def test_tag_tools_round_trip(lib, tools):
    a, b = _seed(lib, day=24), _seed(lib, day=25)
    out = tools["tag_meetings"].run({"ids": [a, b], "add": ["Project X"]})
    assert out == {"meetings": [{"id": a, "tags": ["Project X"]},
                                {"id": b, "tags": ["Project X"]}],
                   "created_tags": ["Project X"], "not_found": []}
    saved = tools["save_notes"].run({"id": a, "tags": ["project-x", "Ops"]})
    assert saved["tags"] == ["Ops", "Project X"]
    assert saved["created_tags"] == ["Ops"] and saved["suppressed"] == []
    assert tools["get_meeting"].run({"id": a})["tags_detail"] == [
        {"name": "Ops", "source": "claude"}, {"name": "Project X", "source": "user"}]
    assert tools["manage_tags"].run({"action": "merge", "tag": "Ops", "into": "project x"}) == {
        "name": "Project X", "description": "", "aliases": ["Ops"], "count": 2}
    described = tools["manage_tags"].run(
        {"action": "describe", "tag": "Project X", "description": "Phoenix rebuild"})
    assert described["description"] == "Phoenix rebuild"
    assert tools["manage_tags"].run({"action": "rename", "tag": "ops", "name": "Phoenix"})["aliases"] == [
        "Ops", "Project X"]
    assert tools["list_tags"].run({}) == {"tags": [
        {"name": "Phoenix", "count": 2, "description": "Phoenix rebuild",
         "aliases": ["Ops", "Project X"]}]}
    removed = tools["tag_meetings"].run({"ids": [b], "remove": ["phoenix", "nope"]})
    assert removed["meetings"] == [{"id": b, "tags": []}] and removed["not_found"] == ["nope"]
    assert tools["manage_tags"].run({"action": "delete", "tag": "Ops"}) == {"deleted": "Phoenix"}


def test_tag_tools_reject_wrong_shapes(lib, tools):
    mid = _seed(lib)
    for args in [{"ids": []}, {"ids": [mid]}, {"ids": mid, "add": ["x"]},
                 {"ids": [mid], "add": "x"}, {"ids": [5], "add": ["x"]}]:
        with pytest.raises(ToolError):
            tools["tag_meetings"].run(args)
    tools["tag_meetings"].run({"ids": [mid], "add": ["x"]})
    for args in [{"action": "explode", "tag": "x"}, {"action": "rename", "tag": "x"},
                 {"action": "merge", "tag": "x"},
                 {"action": "delete"}, {"tag": "x"}]:
        with pytest.raises(ToolError):
            tools["manage_tags"].run(args)
    with pytest.raises(ToolError, match="describe needs description"):
        tools["manage_tags"].run({"action": "describe", "tag": "x"})


def test_tag_descriptions_steer_reuse(tools):
    save = tools["save_notes"].description
    assert "call list_tags first" in save and "at most 3" in save
    assert "never removes tags the user added" in save
    assert "the user asked" in tools["tag_meetings"].description
    assert "alias" in tools["manage_tags"].description
    assert "aliases" in tools["list_tags"].description


def test_pending_summaries_returns_meetings_and_instructions(lib, tools):
    from speakeasy.summary_format import SUMMARY_INSTRUCTIONS
    recent = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0, 4, "hello")], duration_seconds=600,
        title="Recent", started_at=datetime.now(timezone.utc) - timedelta(days=1)))
    old = _seed(lib, day=1)                       # September: outside the window
    lib.request_summary(old)
    out = tools["pending_summaries"].run({})
    assert [m["id"] for m in out["meetings"]] == [old, recent]
    assert [m["requested"] for m in out["meetings"]] == [True, False]
    first = out["meetings"][0]
    assert set(first) == {"id", "title", "start", "duration_minutes", "speakers",
                          "tags", "has_summary", "requested"}
    assert out["instructions"] == SUMMARY_INSTRUCTIONS
    assert tools["pending_summaries"].run({"limit": 1})["meetings"][0]["id"] == old
    # Out-of-range limits clamp (like every other tool's _int), not raise.
    assert len(tools["pending_summaries"].run({"limit": 11})["meetings"]) == 2
    with pytest.raises(ToolError):
        tools["pending_summaries"].run({"limit": "many"})


def test_pending_summaries_default_and_max_limit(lib, tools):
    for i in range(12):
        lib.request_summary(_seed(lib, day=1, title=f"P{i}"))
    assert len(tools["pending_summaries"].run({})["meetings"]) == 5
    assert len(tools["pending_summaries"].run({"limit": 11})["meetings"]) == 10


def test_pending_summaries_redo_of_summarised_meeting(lib, tools):
    mid = _seed(lib, day=1)
    tools["save_notes"].run({"id": mid, "summary": "TL;DR: done"})
    lib.request_summary(mid)
    [m] = tools["pending_summaries"].run({})["meetings"]
    assert m["id"] == mid and m["has_summary"] is True and m["requested"] is True


def test_pending_summaries_skips_meeting_deleted_mid_run(lib, tools, monkeypatch):
    real = _seed(lib, day=1)
    gone = _seed(lib, day=2)
    lib.delete(gone)
    monkeypatch.setattr(lib, "pending_summaries",
                        lambda limit, **kw: [(gone, True), (real, True)])
    out = tools["pending_summaries"].run({})
    assert [m["id"] for m in out["meetings"]] == [real]


def test_save_notes_clears_pending(lib, tools):
    old = _seed(lib, day=1)
    lib.request_summary(old)
    tools["save_notes"].run({"id": old, "summary": "TL;DR: done"})
    assert tools["pending_summaries"].run({})["meetings"] == []


def test_save_notes_description_points_at_summary_format(tools):
    assert "pending_summaries returns" in tools["save_notes"].description
    props = tools["save_notes"].definition()["inputSchema"]["properties"]
    assert "TL;DR" in props["summary"]["description"]


def test_get_meeting_returns_the_users_notes(library_path):
    from datetime import datetime, timedelta, timezone
    from speakeasy.meeting_library import MeetingLibrary, NewMeeting
    from speakeasy.meetings import MeetingSegment
    lib = MeetingLibrary()
    mid = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0, 4, "hi")], duration_seconds=900,
        started_at=datetime(2026, 10, 2, 9, 0, tzinfo=timezone(timedelta(hours=-4)))))
    out = build_tools(lib)["get_meeting"].run({"id": mid})
    assert out["user_notes"] is None
    lib.set_user_notes(mid, "## Plan\n- [ ] send deck", [(1, 760.0)])
    out = build_tools(lib)["get_meeting"].run({"id": mid})
    assert out["user_notes"]["markdown"] == "## Plan\n- [ ] send deck"
    assert out["user_notes"]["stamps"] == [{"line": 1, "at": "00:12:40"}]
    assert "save_notes" in build_tools(lib) and "user_notes" not in str(
        build_tools(lib)["save_notes"].input_schema)


def test_summary_instructions_and_descriptions_point_claude_at_user_notes():
    from speakeasy.summary_format import SUMMARY_INSTRUCTIONS
    assert "user_notes" in SUMMARY_INSTRUCTIONS
    tools = build_tools(None)
    assert "user_notes" in tools["get_meeting"].description
    assert "the user's own notes" in tools["search_meetings"].description


def test_list_meetings_drops_anonymous_speaker_labels(lib, tools):
    lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("Speaker 12", 0, 2, "a"), MeetingSegment("Priya", 2, 4, "b"),
                  MeetingSegment("speaker 3", 4, 6, "c"), MeetingSegment("Speaker Phone", 6, 8, "d"),
                  MeetingSegment("Speaker 2b", 8, 9, "f"),
                  MeetingSegment("Guest Speaker 2", 9, 10, "g"),
                  MeetingSegment("Speaker 01", 10, 11, "h"),
                  MeetingSegment("You", 11, 12, "e")],
        duration_seconds=600, title="Big", started_at=datetime(2026, 9, 20, 9, tzinfo=PDT)))
    [m] = tools["list_meetings"].run({})["meetings"]
    assert m["named_speakers"] == ["Priya", "Speaker Phone", "Speaker 2b",
                                  "Guest Speaker 2", "Speaker 01", "You"]
    assert m["speaker_count"] == 8


def test_list_meetings_description_mentions_named_speakers(tools):
    d = tools["list_meetings"].definition()["description"]
    assert "named speakers" in d and "get_meeting" in d


def test_search_meetings_offset_and_next_offset(lib, tools):
    for d in range(1, 8):
        _seed(lib, day=d, title=f"M{d}")
    page = tools["search_meetings"].run({"query": "budget", "limit": 3})
    assert len(page["results"]) == 3 and page["offset"] == 0 and page["next_offset"] == 3
    tail = tools["search_meetings"].run({"query": "budget", "limit": 50, "offset": 3})
    assert tail["next_offset"] is None
    past = tools["search_meetings"].run({"query": "budget", "offset": 900})
    assert past["results"] == [] and past["next_offset"] is None


def test_search_meetings_by_meeting(lib, tools):
    a = _seed(lib, day=20, title="Alpha")
    _seed(lib, day=21, title="Beta")
    out = tools["search_meetings"].run({"query": "budget", "by_meeting": True})
    assert len(out["results"]) == 2
    row = next(r for r in out["results"] if r["meeting_id"] == a)
    assert set(row) == {"meeting_id", "title", "meeting_start", "hit_count", "first_at",
                        "last_at", "kinds", "snippets"}
    assert row["hit_count"] == 2 and row["first_at"] == "00:00:00" and row["last_at"] == "00:00:05"
    assert row["kinds"] == ["transcript"]
    assert [set(s) for s in row["snippets"]] == [{"kind", "speaker", "at", "start_seconds", "snippet"}] * 2
    assert "**budget**" in row["snippets"][0]["snippet"].lower()
    assert out["next_offset"] is None
    assert out["truncated"] is False
    assert "truncated" not in tools["search_meetings"].run({"query": "budget"})


def test_search_meetings_by_meeting_pages(lib, tools):
    for day in (20, 21, 22):
        _seed(lib, day=day, title=f"M{day}")
    first = tools["search_meetings"].run({"query": "budget", "by_meeting": True, "limit": 2})
    assert len(first["results"]) == 2 and first["next_offset"] == 2
    rest = tools["search_meetings"].run(
        {"query": "budget", "by_meeting": True, "limit": 2, "offset": 2})
    assert len(rest["results"]) == 1 and rest["next_offset"] is None


def test_search_meetings_rejects_non_boolean_by_meeting(tools):
    with pytest.raises(ToolError):
        tools["search_meetings"].run({"query": "x", "by_meeting": "yes"})


def test_pending_summaries_catches_up_a_past_range(lib, tools):
    early_sep = [_seed(lib, day=d, title=f"A{d}") for d in (2, 3)]   # 2026-09-02/03, outside 7 days
    done = _seed(lib, day=4, title="Done")
    tools["save_notes"].run({"id": done, "summary": "TL;DR: x"})
    short = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0, 4, "hi")], duration_seconds=60, title="Short",
        started_at=datetime(2026, 9, 3, 12, 0, tzinfo=PDT)))
    requested = _seed(lib, day=10, title="Asked")
    lib.request_summary(requested)
    out = tools["pending_summaries"].run({"from": "2026-09-01", "to": "2026-09-05"})
    assert [m["id"] for m in out["meetings"]] == [requested, *early_sep]
    assert short not in [m["id"] for m in out["meetings"]] and done not in [m["id"] for m in out["meetings"]]
    # Without a range: unchanged (only the explicit request; September is outside 7 days).
    assert [m["id"] for m in tools["pending_summaries"].run({})["meetings"]] == [requested]


def test_pending_summaries_range_validation_and_description(tools):
    with pytest.raises(ToolError):
        tools["pending_summaries"].run({"from": "2026-09-05", "to": "2026-09-01"})
    with pytest.raises(ToolError):
        tools["pending_summaries"].run({"from": "Sept"})
    d = tools["pending_summaries"].definition()
    assert {"from", "to"} <= set(d["inputSchema"]["properties"])
    assert "catch up" in d["description"]


def test_search_description_teaches_recall_strategy(tools):
    d = tools["search_meetings"].definition()["description"]
    for phrase in ("one or two distinctive terms", "synonyms", "by_meeting", "from/to",
                   "get_meeting", "get_transcript", "next_offset"):
        assert phrase in d, phrase
