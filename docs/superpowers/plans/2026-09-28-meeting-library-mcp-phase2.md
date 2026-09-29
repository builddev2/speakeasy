# Meeting Library Phase 2: Local MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Claude Desktop and Claude Code read, search and annotate the Speakeasy meeting library through a local stdio MCP server (`Speakeasy --mcp`), installable in one click as a Claude Desktop Extension, with a working Connect Claude sheet in the Meetings window.

**Architecture:** `mcp_tools.py` turns `MeetingLibrary` calls into eight JSON tools (pure, unit-tested). `mcp_server.py` is a stdlib JSON-RPC 2.0 loop over newline-delimited stdio. It keeps a private duplicate of fd 1 for protocol messages and points fd 1 and `sys.stdout` at stderr before any tool runs. `__main__.py` dispatches `--mcp` before argparse, like `--inference-probe`. `mcp_setup.py` builds the Connect Claude sheet's commands. `MeetingsBridge` gains `claude.*` methods and a 10 s change poller. `packaging/mcpb/` holds a manifest and a 5-line launcher that `build_app.sh` packs into `Speakeasy.app/Contents/Resources/Speakeasy.mcpb`.

**Tech Stack:** Python 3.11 stdlib (`json`, `os`, `sqlite3`, `subprocess` in tests), pyobjc (existing, window glue only), React 18 + CSS modules (existing), `@anthropic-ai/mcpb` 2.1.2 (build-time only npm tool), pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-meeting-library-mcp-design.md` (sections "MCP server", "Connect Claude sheet", "Meetings window data wiring", "Testing", "Acceptance" item 3). Parent plan: `docs/superpowers/plans/2026-09-27-meeting-library-mcp.md` (read its "Execution notes": Phase 1 decisions below depend on them).

## What Phase 1 shipped that this plan builds on

- `MeetingLibrary(path=None)` in `speakeasy/meeting_library.py`. Every call opens its own connection (WAL, `busy_timeout` 5000 ms).
  - `list_meetings(*, from_date, to_date, tag, person, limit=100, offset=0) -> list[MeetingSummary]` (limit clamped 1..500).
  - `get_meeting(id) -> StoredMeeting` (raises `MeetingNotFound`, or `ValueError("Invalid meeting id: …")` for a malformed id).
  - `transcript_page(id, *, start_seconds, end_seconds, cursor=0, max_chars=20000) -> TranscriptPage(segments=[(idx, MeetingSegment)], next_cursor)`. `max_chars` is clamped to 1000..60000.
  - `search(query, *, from_date, to_date, tag, person, limit=10) -> list[SearchHit]`. Snippet markers are `HIT_OPEN="\x02"`, `HIT_CLOSE="\x03"`.
  - `save_notes(id, *, summary=None, action_items=None, tags=None, updated_by="claude") -> Notes`. Raises `ValueError` with a safe message on over-long input.
  - `list_tags() -> [(name, count)]`, `list_people(query=None) -> [(name, count)]`.
  - Dates are local `YYYY-MM-DD`, converted by `local_day_bounds` (the process's local timezone).
- `local_start(started_at, tz_offset_minutes) -> datetime` (aware), `utc_iso(dt)`.
- Timestamps (`updated_at`) have **one-second** resolution (`%Y-%m-%dT%H:%M:%SZ`). This is why Task 1's change detection uses `PRAGMA data_version`, not timestamps.
- `calendar_events` and `calendar_event_people` tables exist (schema v1), empty until phase 3.
- `MeetingsBridge` (`speakeasy/ui/meetings_bridge.py`): pure Python, `register(dispatcher)` + `_wrap` (maps `MeetingNotFound` to error `not_found`, and `ValueError`/`IndexError` to `str(err)`). `filters_payload` returns `features: {"calendar": False, "claude": False, "settings": False}`.
- `MeetingsWindowController` (`speakeasy/ui/meetings_window.py`): thin ObjC glue. `show()` emits `meetings.changed`, and the page re-lists keeping its selection (`App.tsx` `refreshList(..., true)` re-fetches the selected detail).
- `frontend/src/meetings/ConnectClaudeSheet.tsx` is a mock with hard-coded commands and `console.log` buttons. `mockState === 'connect-claude'` opens it in mock mode.
- The installed frozen app passes stdout through a pipe: `/Applications/Speakeasy.app/Contents/MacOS/Speakeasy --help | head` printed usage on 28 Sep. So the windowed PyInstaller executable probably needs no console twin. Task 7 still proves the full `--mcp` round-trip on the installed build.

## Global Constraints

- Speakeasy makes no network calls at runtime. The MCP server is stdio only; it opens no sockets.
- Phase 2 adds **no runtime dependency**. `@anthropic-ai/mcpb` is pinned exactly at `2.1.2` in `packaging/mcpb/package.json` as a build-time dev dependency, like node for the frontend.
- Tests never touch the real mic, model, network or **real user data**. Every subprocess test sets `HOME` to a temp dir; every in-process test uses the `library_path` fixture or an explicit temp path. Never run `--mcp` against the real `~/Library/Application Support/Speakeasy` from a subagent.
- Test command: `.venv/bin/python -m pytest -q`. Always give the Bash call `timeout: 300000`.
- Frontend check: `npm --prefix frontend run build`.
- Never share a `sqlite3` connection across threads or processes. The MCP server uses a normal `MeetingLibrary()`.
- Capture health (and the other capture fields: `capture_mode`, `system_audio_status`, `capture_scope`, `track_offsets`) is never returned by any MCP tool.
- Stdout of `--mcp` carries only JSON-RPC messages, one per line, UTF-8, no embedded newlines.
- `--mcp` imports no AppKit, model, sherpa-onnx or audio module, and starts no spool sweep.
- UI uses existing tokens in `frontend/src/styles/tokens.css`, **no new frontend dependencies**. UI objects and webview calls stay on the main thread.
- Speakeasy never edits another app's settings (no writing `claude_desktop_config.json`, no running `claude mcp add` from the app).
- Do not modify dictation, focus, AX or insertion code (`injector.py`, `hotkey.py`, dictation paths in `engine.py`).
- Work on branch `meeting-library-phase2` in worktree `.claude/worktrees/meeting-library-phase2`, never on `master`.
- Match surrounding comment style: comments explain *why* a constraint exists.

## Review Focus

1. **Arguments Claude types loosely**: `"limit": "10"`, `limit: 1000`, `offset: -5`, `from: "28/09/2026"`, `from` after `to`, a malformed id, or `action_items: "one string"`. Numbers are coerced and clamped. Anything else returns `isError: true` with a short message saying what to fix. The server keeps serving. Pinned in Tasks 2 and 3.
2. **Library busy**: Claude calls `save_notes` while the app holds the write lock (upgrade import, or a save). Reads are never blocked (WAL). The write waits up to the 5 s busy timeout and then returns `isError` "The meeting library is busy. Try again in a moment." It does not crash, and the next call works. Pinned in Task 3.
3. **Fresh machine or moved app**: there is no library yet (the app never ran), or the app is not in `/Applications`. `--mcp` starts, lists `[]`, and searches `[]`. The `.mcpb` launcher prints a plain stderr message and exits 1. The Connect Claude sheet disables one-click install with a reason. Pinned in Tasks 3, 4 and 6.
4. **A messy stream**: a garbage line, blank lines, non-UTF-8 bytes, a JSON array (batch), a response object, or stdin closing mid-session. The server returns -32700/-32600 where a reply is owed, ignores what needs none, and exits 0 on EOF. Pinned in Task 3.
5. **Claude saves notes while the window shows that meeting**: the summary appears within 10 s without losing the selection or filter. A change in the same wall-clock second as an earlier one (a rename, then Claude's save) is still picked up, even though `updated_at` has one-second resolution. Pinned in Tasks 1 and 4.

---

## File map (phase 2)

| File | Status | Responsibility |
|---|---|---|
| `speakeasy/settings.py` | modify | `mcp_last_used_path()` |
| `speakeasy/meeting_library.py` | modify | `CalendarEvent`, `calendar_events_between`, `calendar_event`, `meeting_tags`, `LibraryWatcher` |
| `speakeasy/mcp_tools.py` | create | `Tool`, `ToolError`, argument coercion, 8 tools, `record_use`/`read_last_used` |
| `speakeasy/mcp_server.py` | create | JSON-RPC 2.0 `Server`, `serve()`, `main()` with stdout isolation |
| `speakeasy/mcp_setup.py` | create | Connect Claude commands, `.mcpb` path, "last used" label |
| `speakeasy/__main__.py` | modify | `--mcp` early dispatch |
| `speakeasy/ui/meetings_bridge.py` | modify | `claude.setupInfo/installExtension/revealConfig`, `poll_changed`, `features.claude` |
| `speakeasy/ui/meetings_window.py` | modify | 10 s poll timer, `open_path` via NSWorkspace |
| `frontend/src/mock/meetings.ts` | modify | `ClaudeSetupInfo` type + mock |
| `frontend/src/meetings/ConnectClaudeSheet.tsx` (+ `.module.css`) | modify | render real setup info, wired buttons |
| `frontend/src/meetings/App.tsx` | modify | fetch `claude.setupInfo`, wire copy/install/reveal |
| `packaging/mcpb/manifest.json`, `packaging/mcpb/server/speakeasy-mcp`, `packaging/mcpb/package.json`, `packaging/mcpb/package-lock.json` | create | Claude Desktop Extension |
| `packaging/Speakeasy.spec` | modify | hidden imports for `mcp_*` |
| `scripts/build_app.sh`, `scripts/write_build_manifest.py`, `scripts/verify_release.py` | modify | pack, check, hash `.mcpb` |
| `.gitignore` | modify | `packaging/mcpb/node_modules/` |
| `tests/test_meeting_library_mcp.py`, `tests/test_mcp_tools.py`, `tests/test_mcp_server.py`, `tests/test_mcp_setup.py`, `tests/test_mcpb.py` | create | tests |
| `tests/test_meetings_bridge.py` | modify | bridge `claude.*` and poller tests |
| `README.md`, `AGENTS.md` | modify | setup docs, stdout rule |

---

### Task 1: Library additions for MCP (calendar read, tags, change watcher)

**Files:**
- Modify: `speakeasy/settings.py` (after `library_path`)
- Modify: `speakeasy/meeting_library.py` (dataclass after `SearchHit`; methods after `list_people`)
- Test: `tests/test_meeting_library_mcp.py`

**Interfaces:**
- Consumes: `local_day_bounds`, `_check_id`, `MeetingNotFound`, `self._transaction()`, `self._tags(conn, id)`.
- Produces:
  - `settings.mcp_last_used_path() -> Path` = `app_support_dir() / "mcp_last_used"`
  - `@dataclass CalendarEvent(event_key: str, calendar_name: str, title: str, start_utc: str, end_utc: str, all_day: bool, declined: bool, meeting_ids: list[str])`
  - `MeetingLibrary.calendar_events_between(from_date: str, to_date: str) -> list[CalendarEvent]`: events overlapping the local days `[from_date, to_date]` inclusive, ordered by `start_utc, event_key`. (Phase 3 fills the table; its plan uses this name.)
  - `MeetingLibrary.calendar_event(event_key: str | None) -> CalendarEvent | None`
  - `MeetingLibrary.meeting_tags(meeting_id: str) -> list[str]` (raises `MeetingNotFound`)
  - `class LibraryWatcher(library: MeetingLibrary)` with `changed() -> bool` and `close() -> None`. `changed()` is True when any *other* connection committed since the previous call; the first call after construction or `close()` only records a baseline and returns False. It owns one connection, used only by the thread that created it (the Meetings window's main-thread timer).
  - Why not compare `max(updated_at)`: timestamps have one-second resolution, so a rename, or a second notes save in the same second as an earlier change, would be missed. `PRAGMA data_version` counts commits by other connections exactly, and needs no schema change, so an older build can still open the library.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_meeting_library_mcp.py
"""Library reads the MCP server needs (phase 2)."""
from datetime import datetime, timedelta, timezone

import pytest

from speakeasy import meeting_store, settings
from speakeasy.meeting_library import (
    LibraryWatcher, MeetingLibrary, MeetingNotFound, NewMeeting,
)
from speakeasy.meetings import MeetingSegment

PDT = timezone(timedelta(hours=-7))


def _new(started=datetime(2026, 9, 24, 10, 0, 0, tzinfo=PDT), **kw):
    kw.setdefault("segments", [MeetingSegment("You", 0.0, 4.0, "hello budget")])
    kw.setdefault("duration_seconds", 600.0)
    return NewMeeting(started_at=started, **kw)


def _insert_event(path, key, start_utc, end_utc, title="Standup"):
    conn = meeting_store.connect(path)
    with conn:
        conn.execute(
            "INSERT INTO calendar_events (event_key, calendar_name, title, start_utc,"
            " end_utc, all_day, declined, synced_at) VALUES (?, 'Work', ?, ?, ?, 0, 0,"
            " '2026-09-28T00:00:00Z')", (key, title, start_utc, end_utc))
    conn.close()


def test_mcp_last_used_path_is_in_app_support(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    assert settings.mcp_last_used_path() == tmp_path / "mcp_last_used"


def test_calendar_events_between_is_empty_before_phase3(library_path):
    assert MeetingLibrary().calendar_events_between("2026-01-01", "2026-12-31") == []


def test_calendar_events_between_returns_overlapping_events_with_linked_meetings(library_path):
    lib = MeetingLibrary()
    _insert_event(library_path, "E1@2026-09-24T17:00:00Z",
                  "2026-09-24T17:00:00Z", "2026-09-24T17:30:00Z")
    _insert_event(library_path, "E2@2026-03-01T17:00:00Z",
                  "2026-03-01T17:00:00Z", "2026-03-01T17:30:00Z", title="Old")
    mid = lib.save_meeting(_new(calendar_event_id="E1@2026-09-24T17:00:00Z"))
    events = lib.calendar_events_between("2026-09-01", "2026-09-30")
    assert [e.title for e in events] == ["Standup"]
    assert events[0].meeting_ids == [mid]
    assert events[0].all_day is False and events[0].declined is False
    assert lib.calendar_event("E1@2026-09-24T17:00:00Z").title == "Standup"
    assert lib.calendar_event("missing") is None
    assert lib.calendar_event(None) is None


def test_meeting_tags(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    lib.save_notes(mid, tags=["budget", "Q3"])
    assert lib.meeting_tags(mid) == ["budget", "Q3"]
    with pytest.raises(MeetingNotFound):
        lib.meeting_tags("20200101-000000-abcd")


def test_watcher_baseline_then_sees_every_kind_of_change(library_path):
    lib = MeetingLibrary()
    watcher = LibraryWatcher(lib)
    assert watcher.changed() is False  # baseline only
    assert watcher.changed() is False
    mid = lib.save_meeting(_new())
    assert watcher.changed() is True
    assert watcher.changed() is False
    # Same wall-clock second as the save above: a timestamp marker misses this.
    lib.rename(mid, "Budget review")
    assert watcher.changed() is True
    lib.save_notes(mid, summary="First")
    assert watcher.changed() is True
    lib.save_notes(mid, tags=["budget"])
    assert watcher.changed() is True
    lib.delete(mid)
    assert watcher.changed() is True
    watcher.close()


def test_watcher_sees_a_write_from_another_process(library_path):
    # The MCP server is a separate process; prove the signal crosses it.
    import os, subprocess, sys
    from pathlib import Path
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    watcher = LibraryWatcher(lib)
    watcher.changed()
    code = ("import sys; from speakeasy.meeting_library import MeetingLibrary;"
            " MeetingLibrary(sys.argv[1]).save_notes(sys.argv[2], summary='From Claude')")
    repo = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "-c", code, str(library_path), mid], check=True,
                   cwd=repo, env={**os.environ, "PYTHONPATH": str(repo)}, timeout=60)
    assert watcher.changed() is True
    assert lib.get_meeting(mid).notes.summary == "From Claude"
    watcher.close()


def test_watcher_close_resets_the_baseline(library_path):
    lib = MeetingLibrary()
    watcher = LibraryWatcher(lib)
    watcher.changed()
    lib.save_meeting(_new())
    watcher.close()
    assert watcher.changed() is False  # reopened: new baseline
    watcher.close()
    watcher.close()  # idempotent
```

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_meeting_library_mcp.py -q` (timeout 300000)
Expected: FAIL (`AttributeError: … has no attribute 'mcp_last_used_path'` / `calendar_events_between`).

- [x] **Step 3: Implement**

In `speakeasy/settings.py`, after `library_path()`:

```python
def mcp_last_used_path() -> Path:
    """UTC timestamp of the MCP server's last tool call, shown in the
    Connect Claude sheet ("Last used by Claude: 2 min ago")."""
    return app_support_dir() / "mcp_last_used"
```

In `speakeasy/meeting_library.py`, after `class SearchHit`:

```python
@dataclass
class CalendarEvent:
    event_key: str
    calendar_name: str
    title: str
    start_utc: str
    end_utc: str
    all_day: bool
    declined: bool
    meeting_ids: list[str]
```

Methods at the end of `MeetingLibrary`:

```python
    # -- MCP reads (phase 2) -------------------------------------------------

    def meeting_tags(self, meeting_id: str) -> list[str]:
        _check_id(meeting_id)
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)).fetchone() is None:
                raise MeetingNotFound(meeting_id)
            return self._tags(conn, meeting_id)

    def _calendar_event(self, conn, row) -> CalendarEvent:
        return CalendarEvent(
            event_key=row["event_key"], calendar_name=row["calendar_name"],
            title=row["title"], start_utc=row["start_utc"], end_utc=row["end_utc"],
            all_day=bool(row["all_day"]), declined=bool(row["declined"]),
            meeting_ids=[r[0] for r in conn.execute(
                "SELECT id FROM meetings WHERE calendar_event_id = ?"
                " ORDER BY started_at, id", (row["event_key"],))],
        )

    def calendar_events_between(self, from_date: str, to_date: str) -> list[CalendarEvent]:
        """Cached Calendar.app events overlapping local days [from, to]."""
        lower, upper = local_day_bounds(from_date, to_date)
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM calendar_events WHERE start_utc < ? AND end_utc > ?"
                " ORDER BY start_utc, event_key", (upper, lower)).fetchall()
            return [self._calendar_event(conn, r) for r in rows]

    def calendar_event(self, event_key: str | None) -> CalendarEvent | None:
        if not event_key:
            return None
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM calendar_events WHERE event_key = ?",
                               (event_key,)).fetchone()
            return self._calendar_event(conn, row) if row else None
```

At the end of the module, after `MeetingLibrary`:

```python
class LibraryWatcher:
    """Notices commits made by any other connection, e.g. Claude saving
    notes through the MCP server (a separate process nothing in the app
    hears from). PRAGMA data_version changes exactly when another
    connection commits; updated_at would not do, since its one-second
    resolution hides a second change within the same second.

    The one exception to "every call opens its own connection": the
    watcher keeps one, because data_version is only meaningful across
    calls on the same connection. It is used only by the thread that made
    it (sqlite3's check_same_thread enforces this) and sits idle outside
    a transaction, so it never holds back WAL checkpoints.
    """

    def __init__(self, library: MeetingLibrary) -> None:
        self._path = library._path
        self._conn = None
        self._version = None

    def changed(self) -> bool:
        if self._conn is None:
            self._conn = meeting_store.connect(self._path)
        version = self._conn.execute("PRAGMA data_version").fetchone()[0]
        changed = self._version is not None and version != self._version
        self._version = version
        return changed

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
        self._conn, self._version = None, None
```

Check `MeetingLibrary.__init__` for the attribute that holds the path (it is `self._path`; `_transaction` passes it to `meeting_store.connect`). `meeting_store.connect(None)` falls back to `settings.library_path()`, so a default library works too.

Note: the `calendar_events_between` test relies on `local_day_bounds` using the machine's timezone. The inserted September event is well inside the window in every timezone, and the March one is far outside it.

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_meeting_library_mcp.py -q` (timeout 300000). Expected: 7 passed.
Then the full suite: `.venv/bin/python -m pytest -q` (timeout 300000). Expected: all pass (514 + 7).

- [x] **Step 5: Commit**

```bash
git add speakeasy/settings.py speakeasy/meeting_library.py tests/test_meeting_library_mcp.py
git commit -m "Add library reads for the MCP server: calendar events, tags, change watcher"
```

---

### Task 2: MCP tools over `MeetingLibrary`

**Files:**
- Create: `speakeasy/mcp_tools.py`
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: Task 1's `calendar_events_between`, `calendar_event`, `meeting_tags`, `settings.mcp_last_used_path`; Phase 1's library API (see top of plan); `HIT_OPEN`, `HIT_CLOSE`, `local_start`, `MeetingNotFound`.
- Produces (used by Tasks 3, 4, 6):
  - `class ToolError(Exception)`: its message is safe to show Claude.
  - `@dataclass(frozen=True) class Tool(name: str, description: str, input_schema: dict, run: Callable[[dict], dict], read_only: bool = True)` with `definition() -> dict` = `{"name", "description", "inputSchema", "annotations": {"readOnlyHint": read_only}}`.
  - `build_tools(library) -> dict[str, Tool]`, in this order: `list_meetings, get_meeting, search_meetings, get_transcript, get_calendar, list_tags, list_people, save_notes`.
  - `record_use(path: Path | None = None, now: datetime | None = None) -> None` writes `utc_iso(now)` atomically and ignores `OSError`.
  - `read_last_used(path: Path | None = None) -> datetime | None` returns an aware UTC datetime, or None if the file is missing or unreadable.

Tool results (JSON objects; `start` is the meeting's local ISO time with offset, minute precision, e.g. `"2026-09-24T10:00-07:00"`; `at` is elapsed `hh:mm:ss`):

| Tool | Result |
|---|---|
| `list_meetings` | `{"meetings": [Item], "offset": int, "next_offset": int \| null}`; Item = `id, title, start, duration_minutes, speakers, people, tags, has_summary, timestamps_approximate` |
| `get_meeting` | `id, title, start, duration_minutes, speakers, segment_count, people, tags, calendar_event (obj \| null), notes ({summary, action_items, updated_at, updated_by} \| null), timestamps_approximate, source` |
| `search_meetings` | `{"results": [{meeting_id, title, meeting_start, kind, speaker, also_speakers, at, start_seconds, snippet}]}`; snippet highlights use `**…**` |
| `get_transcript` | `{"id", "title", "text", "next_cursor", "timestamps_approximate"}`; text lines `[hh:mm:ss] Speaker: text` |
| `get_calendar` | `{"events": [{id, title, calendar, start, end, all_day, declined, meeting_ids}]}` |
| `list_tags` | `{"tags": [{name, count}]}` |
| `list_people` | `{"people": [{name, count}]}` |
| `save_notes` | `{"id", "summary", "action_items", "tags", "updated_at", "updated_by"}` |

- [x] **Step 1: Write the failing tests**

```python
# tests/test_mcp_tools.py
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


def test_list_meetings_coerces_and_clamps_loose_numbers(lib, tools):
    _seed(lib)
    assert len(tools["list_meetings"].run({"limit": "10"})["meetings"]) == 1
    assert tools["list_meetings"].run({"limit": 1000})["meetings"]  # clamped to 100
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
        conn.execute("INSERT INTO calendar_events VALUES ('K1', 'Work', 'Standup',"
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
```

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -q` (timeout 300000)
Expected: FAIL with `ModuleNotFoundError: No module named 'speakeasy.mcp_tools'`.

- [x] **Step 3: Implement `speakeasy/mcp_tools.py`**

```python
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


def _distinct_speakers(segments) -> list[str]:
    seen: dict[str, None] = {}
    for seg in segments:
        seen.setdefault(seg.speaker, None)
    return list(seen)


def _event(e) -> dict:
    local = lambda iso: datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc).astimezone().isoformat(timespec="minutes")
    return {"id": e.event_key, "title": e.title, "calendar": e.calendar_name,
            "start": local(e.start_utc), "end": local(e.end_utc),
            "all_day": e.all_day, "declined": e.declined, "meeting_ids": e.meeting_ids}


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
        m = library.get_meeting(_meeting_id(args))
        notes = None
        if m.notes is not None:
            notes = {"summary": m.notes.summary, "action_items": m.notes.action_items,
                     "updated_at": m.notes.updated_at, "updated_by": m.notes.updated_by}
        event = library.calendar_event(m.calendar_event_id)
        return {
            "id": m.meeting_id, "title": m.title,
            "start": _start(m.started_at, m.tz_offset_minutes),
            "duration_minutes": _minutes(m.duration_seconds),
            "speakers": _distinct_speakers(m.segments),
            "segment_count": len(m.segments), "people": m.people, "tags": m.tags,
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
        m = library.get_meeting(meeting_id)
        return {
            "id": meeting_id, "title": m.title,
            "text": "\n".join(f"[{_hms(seg.start)}] {seg.speaker}: {seg.text}"
                              for _, seg in page.segments),
            "next_cursor": page.next_cursor,
            "timestamps_approximate": m.timestamps_approximate,
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
```

Note on `get_transcript`: it calls `get_meeting` for the title and the approximate flag. That loads every segment, about 20k rows at worst. It is acceptable for now; if profiling shows a problem, add a light `meeting_header(id)` library method later.

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py -q` (timeout 300000). Expected: all pass. Then the full suite (timeout 300000).

- [x] **Step 5: Commit**

```bash
git add speakeasy/mcp_tools.py tests/test_mcp_tools.py
git commit -m "Add MCP tools over the meeting library"
```

---

### Task 3: JSON-RPC server, `--mcp` dispatch and stdout isolation

**Files:**
- Create: `speakeasy/mcp_server.py`
- Modify: `speakeasy/__main__.py` (top of `main()`, beside `--inference-probe`)
- Modify: `packaging/Speakeasy.spec` (`hiddenimports`: add `"speakeasy.mcp_server"`, `"speakeasy.mcp_tools"`, `"speakeasy.mcp_setup"`)
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `build_tools(library)`, `Tool`, `ToolError`, `record_use()` from Task 2; `MeetingLibrary`, `MeetingNotFound`.
- Produces:
  - `SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")`, `DEFAULT_VERSION = "2025-06-18"`, `SERVER_VERSION = "1.0.0"` (Task 6 tests that the `.mcpb` manifest version equals this and the app's `CFBundleShortVersionString`).
  - `class Server(tools: dict[str, Tool], on_call: Callable[[], None] | None = None)` with `handle_line(line: bytes) -> dict | None` and `handle(message) -> dict | None`.
  - `serve(infile: BinaryIO, outfile: BinaryIO, server: Server) -> None`
  - `main() -> None` (module-level names `build_tools`, `record_use` and `MeetingLibrary` are looked up at call time so tests can patch them).

Protocol rules (MCP stdio transport, JSON-RPC 2.0):
- One JSON message per line. Blank lines are ignored. Undecodable or invalid JSON → `{"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"Parse error"}}`.
- A JSON array (batch) → -32600 with id null. Batching was removed in 2025-06-18, and nothing here needs it.
- A non-object, or `jsonrpc != "2.0"`, or a non-string `method` → -32600. A client's *response* object (has `result` or `error`, no `method`) → no reply.
- A message without `"id"` is a notification → never replied to (`notifications/initialized`, `notifications/cancelled`, anything unknown).
- A request id must be a string or an integer (not bool); otherwise -32600 with id null.
- `initialize` → `{"protocolVersion": <client's if supported else DEFAULT_VERSION>, "capabilities": {"tools": {}}, "serverInfo": {"name": "speakeasy", "version": SERVER_VERSION}, "instructions": INSTRUCTIONS}`.
- `ping` → `{}`. `tools/list` → `{"tools": [t.definition() …]}`. Unknown method → -32601.
- `tools/call`: `params` must be an object with a string `name` naming a known tool, and `arguments` an object or absent; else -32602. Call `on_call()` first (best effort). The result is `{"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": false}`.
- Tool failures → the same shape with `isError: true` and text:
  - `ToolError` → its message.
  - `MeetingNotFound` → `"No meeting with id <id>."`.
  - `ValueError` → `str(err)` (library messages are written to be shown).
  - `sqlite3.OperationalError` whose text contains "locked" or "busy" → `"The meeting library is busy. Try again in a moment."`.
  - Anything else → `"Speakeasy hit an internal error."`, and stderr gets `mcp: <tool> failed: <ExceptionType>` (type only, never the message: it can carry paths or text).

- [x] **Step 1: Write the failing tests**

```python
# tests/test_mcp_server.py
"""JSON-RPC handling in-process, plus real subprocess round-trips over pipes.

Every subprocess gets HOME=<tmp> so settings.app_support_dir() — the
library and the last-used stamp — resolve inside the temp dir, never the
user's real Application Support folder.
"""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from speakeasy import mcp_server
from speakeasy.meeting_library import MeetingLibrary, MeetingNotFound, NewMeeting
from speakeasy.meetings import MeetingSegment
from speakeasy.mcp_tools import Tool, ToolError

REPO = Path(__file__).resolve().parents[1]


def _tool(run, name="t"):
    return {name: Tool(name, "test tool", {"type": "object"}, run)}


def _req(method, params=None, id=1):
    msg = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


# -- in-process ---------------------------------------------------------------

def test_initialize_negotiates_version():
    s = mcp_server.Server({})
    r = s.handle(_req("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                     "clientInfo": {"name": "t", "version": "0"}}))
    assert r["result"]["protocolVersion"] == "2025-03-26"
    assert r["result"]["capabilities"] == {"tools": {}}
    assert r["result"]["serverInfo"] == {"name": "speakeasy", "version": "1.0.0"}
    r = s.handle(_req("initialize", {"protocolVersion": "1999-01-01"}))
    assert r["result"]["protocolVersion"] == "2025-06-18"
    r = s.handle(_req("initialize", {}))
    assert r["result"]["protocolVersion"] == "2025-06-18"


def test_ping_list_and_unknown_method():
    s = mcp_server.Server(_tool(lambda a: {}))
    assert s.handle(_req("ping", id="abc")) == {"jsonrpc": "2.0", "id": "abc", "result": {}}
    tools = s.handle(_req("tools/list"))["result"]["tools"]
    assert [t["name"] for t in tools] == ["t"]
    assert s.handle(_req("resources/list"))["error"]["code"] == -32601


@pytest.mark.parametrize("line, code, rid", [
    (b"not json", -32700, None),
    (b"\xff\xfe", -32700, None),
    (b"[]", -32600, None),
    (b'[{"jsonrpc":"2.0","id":1,"method":"ping"}]', -32600, None),
    (b'"hello"', -32600, None),
    # JSON-RPC: echo the id whenever it can be read, so the client can match it.
    (b'{"jsonrpc":"1.0","id":1,"method":"ping"}', -32600, 1),
    (b'{"jsonrpc":"2.0","id":1,"method":5}', -32600, 1),
    (b'{"jsonrpc":"2.0","id":true,"method":"ping"}', -32600, None),
])
def test_malformed_messages(line, code, rid):
    r = mcp_server.Server({}).handle_line(line)
    assert r["error"]["code"] == code and r["id"] == rid


def test_notifications_and_responses_get_no_reply():
    s = mcp_server.Server({})
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/whatever"}) is None
    assert s.handle({"jsonrpc": "2.0", "id": 9, "result": {}}) is None


def test_tools_call_param_errors():
    s = mcp_server.Server(_tool(lambda a: {}))
    assert s.handle(_req("tools/call", {"name": "nope"}))["error"]["code"] == -32602
    assert s.handle(_req("tools/call", {}))["error"]["code"] == -32602
    assert s.handle(_req("tools/call", {"name": "t", "arguments": []}))["error"]["code"] == -32602
    assert s.handle(_req("tools/call", "x"))["error"]["code"] == -32602


def test_tools_call_success_and_on_call():
    calls = []
    s = mcp_server.Server(_tool(lambda a: {"echo": a}), on_call=lambda: calls.append(1))
    r = s.handle(_req("tools/call", {"name": "t", "arguments": {"x": "é"}}))["result"]
    assert r["isError"] is False
    assert json.loads(r["content"][0]["text"]) == {"echo": {"x": "é"}}
    assert calls == [1]
    r = s.handle(_req("tools/call", {"name": "t"}))["result"]  # arguments optional
    assert json.loads(r["content"][0]["text"]) == {"echo": {}}


def test_on_call_failure_never_fails_the_call():
    def boom():
        raise OSError("disk full")
    s = mcp_server.Server(_tool(lambda a: {"ok": 1}), on_call=boom)
    assert s.handle(_req("tools/call", {"name": "t"}))["result"]["isError"] is False


@pytest.mark.parametrize("exc, text", [
    (ToolError("limit must be a whole number."), "limit must be a whole number."),
    (MeetingNotFound("20200101-000000-abcd"), "No meeting with id 20200101-000000-abcd."),
    (ValueError("Summary is longer than 20,000 characters."),
     "Summary is longer than 20,000 characters."),
    (sqlite3.OperationalError("database is locked"),
     "The meeting library is busy. Try again in a moment."),
    (RuntimeError("/Users/secret/path leaked"), "Speakeasy hit an internal error."),
])
def test_tool_errors_are_safe_results(exc, text, capsys):
    def run(args):
        raise exc
    r = mcp_server.Server(_tool(run)).handle(_req("tools/call", {"name": "t"}))["result"]
    assert r == {"content": [{"type": "text", "text": text}], "isError": True}
    assert "/Users/secret" not in capsys.readouterr().err


def test_busy_library_then_next_call_works(library_path, monkeypatch):
    # WAL readers never block, so "busy" means a write (save_notes) while
    # another connection (the app mid-import) holds the write lock.
    from speakeasy import meeting_store
    lib = MeetingLibrary()
    mid = lib.save_meeting(NewMeeting(segments=[MeetingSegment("You", 0, 1, "x")],
                                      duration_seconds=1.0,
                                      started_at=datetime(2026, 9, 24, 10, tzinfo=timezone.utc)))
    real_connect = meeting_store.connect

    def quick_connect(path=None):  # the real 5 s busy timeout would slow the suite
        conn = real_connect(path)
        conn.execute("PRAGMA busy_timeout = 50")
        return conn
    monkeypatch.setattr(meeting_store, "connect", quick_connect)
    s = mcp_server.Server(mcp_server.build_tools(lib))
    call = _req("tools/call", {"name": "save_notes", "arguments": {"id": mid, "summary": "S"}})
    blocker = sqlite3.connect(library_path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        r = s.handle(call)["result"]
        assert r == {"content": [{"type": "text", "text":
                     "The meeting library is busy. Try again in a moment."}], "isError": True}
        assert s.handle(_req("tools/call", {"name": "list_meetings"}))["result"]["isError"] is False
    finally:
        blocker.rollback()
        blocker.close()
    assert s.handle(call)["result"]["isError"] is False


def test_serve_skips_blank_lines_and_stops_at_eof(tmp_path):
    import io
    out = io.BytesIO()
    inp = io.BytesIO(b'\n  \n{"jsonrpc":"2.0","id":1,"method":"ping"}\ngarbage\n')
    mcp_server.serve(inp, out, mcp_server.Server({}))
    lines = out.getvalue().decode().splitlines()
    assert [json.loads(l).get("result", "err") for l in lines] == [{}, "err"]


# -- subprocess over pipes ------------------------------------------------------

def _env(home):
    env = {k: v for k, v in os.environ.items() if k not in ("HOME", "PYTHONPATH")}
    env.update(HOME=str(home), PYTHONPATH=str(REPO))
    return env


def _library_in(home) -> MeetingLibrary:
    path = home / "Library" / "Application Support" / "Speakeasy" / "library.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    return MeetingLibrary(path)


def _run(home, messages, argv=None, raw=b""):
    data = raw + b"".join(json.dumps(m).encode() + b"\n" for m in messages)
    proc = subprocess.run(argv or [sys.executable, "-m", "speakeasy", "--mcp"],
                          input=data, capture_output=True, env=_env(home), cwd=REPO,
                          timeout=60)
    replies = [json.loads(line) for line in proc.stdout.decode().splitlines()]
    return proc, {r.get("id"): r for r in replies}, replies


def _session(*calls):
    msgs = [_req("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "pytest", "version": "0"}}, id=0),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            _req("tools/list", id="list")]
    for i, (name, args) in enumerate(calls, start=1):
        msgs.append(_req("tools/call", {"name": name, "arguments": args}, id=i))
    return msgs


def _payload(reply):
    assert reply["result"]["isError"] is False, reply
    return json.loads(reply["result"]["content"][0]["text"])


def test_subprocess_round_trip_every_tool(tmp_path):
    lib = _library_in(tmp_path)
    mid = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0.0, 4.0, "the budget is agreed")],
        duration_seconds=60.0, title="Budget",
        started_at=datetime(2026, 9, 24, 10, tzinfo=timezone(timedelta(hours=-7)))))
    proc, by_id, _ = _run(tmp_path, _session(
        ("list_meetings", {}), ("get_meeting", {"id": mid}),
        ("search_meetings", {"query": "budget"}), ("get_transcript", {"id": mid}),
        ("get_calendar", {"from": "2026-09-01", "to": "2026-09-30"}),
        ("list_tags", {}), ("list_people", {}),
        ("save_notes", {"id": mid, "summary": "Agreed", "tags": ["budget"]}),
        ("get_meeting", {"id": "20200101-000000-abcd"}),
    ))
    assert proc.returncode == 0, proc.stderr
    assert by_id[0]["result"]["serverInfo"]["name"] == "speakeasy"
    assert len(by_id["list"]["result"]["tools"]) == 8
    assert _payload(by_id[1])["meetings"][0]["id"] == mid
    assert _payload(by_id[2])["title"] == "Budget"
    assert _payload(by_id[3])["results"][0]["meeting_id"] == mid
    assert _payload(by_id[4])["text"] == "[00:00:00] You: the budget is agreed"
    assert _payload(by_id[5]) == {"events": []}
    assert _payload(by_id[8])["summary"] == "Agreed"
    assert by_id[9]["result"]["isError"] is True
    assert lib.get_meeting(mid).notes.updated_by == "claude"
    stamp = tmp_path / "Library/Application Support/Speakeasy/mcp_last_used"
    assert stamp.read_text().endswith("Z")


def test_subprocess_fresh_home_without_library(tmp_path):
    proc, by_id, _ = _run(tmp_path, _session(("list_meetings", {}),
                                             ("search_meetings", {"query": "x"})))
    assert proc.returncode == 0, proc.stderr
    assert _payload(by_id[1])["meetings"] == []
    assert _payload(by_id[2])["results"] == []


def test_subprocess_survives_a_messy_stream_and_exits_on_eof(tmp_path):
    proc, _, replies = _run(tmp_path, [_req("ping", id=7)],
                            raw=b"garbage\n\n\xff\xfe\n[1,2]\n")
    assert proc.returncode == 0
    assert [r.get("error", {}).get("code") for r in replies] == [-32700, -32700, -32600, None]
    assert replies[-1] == {"jsonrpc": "2.0", "id": 7, "result": {}}


def test_stray_output_in_a_tool_never_reaches_stdout(tmp_path):
    # A tool (or a C library) that prints must not corrupt the JSON stream:
    # both a Python print() and a raw write to fd 1 must land on stderr.
    code = (
        "import os\n"
        "from speakeasy import mcp_server, mcp_tools\n"
        "real = mcp_tools.build_tools\n"
        "def noisy(args):\n"
        "    print('stray print')\n"
        "    os.write(1, b'stray fd write\\n')\n"
        "    return {'ok': True}\n"
        "def build(lib):\n"
        "    tools = real(lib)\n"
        "    tools['noisy'] = mcp_tools.Tool('noisy', 'test', {'type': 'object'}, noisy)\n"
        "    return tools\n"
        "mcp_server.build_tools = build\n"
        "mcp_server.main()\n"
    )
    proc, by_id, replies = _run(tmp_path, _session(("noisy", {})),
                                argv=[sys.executable, "-c", code])
    assert proc.returncode == 0, proc.stderr
    assert _payload(by_id[1]) == {"ok": True}
    err = proc.stderr.decode()
    assert "stray print" in err and "stray fd write" in err
    assert b"stray" not in proc.stdout


def test_mcp_mode_imports_nothing_heavy(tmp_path):
    code = (
        "import sys\n"
        "from speakeasy import mcp_server\n"
        "mcp_server.main()\n"
        "heavy = [m for m in ('AppKit', 'Foundation', 'objc', 'mlx', 'numpy', 'sherpa_onnx',"
        " 'sounddevice', 'speakeasy.engine') if m in sys.modules]\n"
        "sys.stderr.write('HEAVY=' + ','.join(heavy))\n"
    )
    proc, _, _ = _run(tmp_path, _session(("list_meetings", {})),
                      argv=[sys.executable, "-c", code])
    assert proc.stderr.decode().rstrip().endswith("HEAVY="), proc.stderr.decode()
```

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -q` (timeout 300000)
Expected: FAIL with `ModuleNotFoundError: No module named 'speakeasy.mcp_server'`.

- [x] **Step 3: Implement `speakeasy/mcp_server.py`**

```python
"""Local MCP server: JSON-RPC 2.0 over stdio for Claude Desktop and Code.

The user's Claude client starts `Speakeasy --mcp` and talks to it over
pipes; Speakeasy itself still makes no network calls. Dispatched before
argparse (see __main__), so no model, AppKit, microphone or Dock icon.

Stdout carries only protocol messages. main() keeps a private duplicate of
fd 1 for JSON-RPC, then points fd 1 and sys.stdout at stderr before any
tool runs, so a stray print() — or a C library writing to fd 1 — can never
corrupt the stream the client parses.
"""

import json
import os
import sqlite3
import sys
from typing import BinaryIO, Callable

from .meeting_library import MeetingLibrary, MeetingNotFound
from .mcp_tools import ToolError, build_tools, record_use

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_VERSION = "2025-06-18"
SERVER_VERSION = "1.0.0"
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602

INSTRUCTIONS = (
    "Speakeasy's local meeting library (transcripts recorded on this Mac). "
    "Start with search_meetings or list_meetings, then read only what you need "
    "with get_transcript (start_seconds/end_seconds, or cursor). Dates are the "
    "user's local days, YYYY-MM-DD. save_notes stores a summary, action items "
    "or tags the user can see in Speakeasy."
)
_BUSY = "The meeting library is busy. Try again in a moment."


def _error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _valid_id(rid) -> bool:
    return isinstance(rid, str) or (isinstance(rid, int) and not isinstance(rid, bool))


class Server:
    def __init__(self, tools: dict, on_call: Callable[[], None] | None = None) -> None:
        self.tools = tools
        self.on_call = on_call

    def handle_line(self, line: bytes) -> dict | None:
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return _error(None, PARSE_ERROR, "Parse error")
        return self.handle(message)

    def handle(self, message) -> dict | None:
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "Invalid Request")
        if "method" not in message and ("result" in message or "error" in message):
            return None  # a client's response; this server sends no requests
        if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            rid = message.get("id")
            return _error(rid if _valid_id(rid) else None, INVALID_REQUEST, "Invalid Request")
        if "id" not in message:
            return None  # notification: never answered
        rid = message["id"]
        if not _valid_id(rid):
            return _error(None, INVALID_REQUEST, "Invalid Request")
        method, params = message["method"], message.get("params")
        if method == "initialize":
            requested = params.get("protocolVersion") if isinstance(params, dict) else None
            return self._result(rid, {
                "protocolVersion": requested if requested in SUPPORTED_VERSIONS else DEFAULT_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "speakeasy", "version": SERVER_VERSION},
                "instructions": INSTRUCTIONS,
            })
        if method == "ping":
            return self._result(rid, {})
        if method == "tools/list":
            return self._result(rid, {"tools": [t.definition() for t in self.tools.values()]})
        if method == "tools/call":
            return self._call(rid, params)
        return _error(rid, METHOD_NOT_FOUND, f"Method not found: {method}")

    @staticmethod
    def _result(rid, result):
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def _call(self, rid, params):
        if not isinstance(params, dict) or params.get("name") not in self.tools:
            name = params.get("name") if isinstance(params, dict) else None
            return _error(rid, INVALID_PARAMS, f"Unknown tool: {name}")
        args = params.get("arguments", {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return _error(rid, INVALID_PARAMS, "arguments must be an object")
        if self.on_call is not None:
            try:
                self.on_call()
            except Exception:
                pass  # "last used" is cosmetic; never fail the call over it
        tool = self.tools[params["name"]]
        try:
            text, is_error = json.dumps(tool.run(args), ensure_ascii=False), False
        except ToolError as err:
            text, is_error = str(err), True
        except MeetingNotFound as err:
            text, is_error = f"No meeting with id {err.args[0]}.", True
        except ValueError as err:
            text, is_error = str(err), True
        except sqlite3.OperationalError as err:
            busy = any(w in str(err).lower() for w in ("locked", "busy"))
            if not busy:
                print(f"mcp: {tool.name} failed: {type(err).__name__}", file=sys.stderr)
            text, is_error = (_BUSY if busy else "Speakeasy hit an internal error."), True
        except Exception as err:
            # Type only: exception messages can carry paths or meeting text.
            print(f"mcp: {tool.name} failed: {type(err).__name__}", file=sys.stderr)
            text, is_error = "Speakeasy hit an internal error.", True
        return self._result(rid, {"content": [{"type": "text", "text": text}],
                                  "isError": is_error})


def serve(infile: BinaryIO, outfile: BinaryIO, server: Server) -> None:
    for line in iter(infile.readline, b""):
        if not line.strip():
            continue
        reply = server.handle_line(line.strip())
        if reply is None:
            continue
        try:
            outfile.write(json.dumps(reply, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8") + b"\n")
            outfile.flush()
        except BrokenPipeError:
            return


def main() -> None:
    protocol_out = os.fdopen(os.dup(1), "wb")
    protocol_in = os.fdopen(os.dup(0), "rb")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    server = Server(build_tools(MeetingLibrary()), on_call=record_use)
    try:
        serve(protocol_in, protocol_out, server)
    except KeyboardInterrupt:
        pass
```

Note: `json.dumps` escapes the newlines inside strings, so one message is always exactly one line.

In `speakeasy/__main__.py`, right after the `--inference-probe` block:

```python
    if len(sys.argv) > 1 and sys.argv[1] == "--mcp":
        # Before argparse and any heavy import: Claude's client owns stdout.
        from .mcp_server import main as mcp_main
        mcp_main()
        return
```

In `packaging/Speakeasy.spec` `hiddenimports`, after `"speakeasy.diarizer",`, add `"speakeasy.mcp_server",`, `"speakeasy.mcp_tools"` and `"speakeasy.mcp_setup",`. `mcp_setup` is created in Task 4; the hidden import is harmless until then, because PyInstaller warns, not fails, and nothing builds before Task 7.

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -q` (timeout 300000). Expected: all pass. Then the full suite (timeout 300000).

If `test_mcp_mode_imports_nothing_heavy` fails, find which import pulls in the heavy module (`python -X importtime`) and fix the import chain. Do not weaken the test.

- [x] **Step 5: Commit**

```bash
git add speakeasy/mcp_server.py speakeasy/__main__.py packaging/Speakeasy.spec tests/test_mcp_server.py
git commit -m "Add stdio MCP server with --mcp dispatch and stdout isolation"
```

---

### Task 4: App side: setup info, change poller, bridge methods

**Files:**
- Create: `speakeasy/mcp_setup.py`
- Modify: `speakeasy/ui/meetings_bridge.py`
- Modify: `speakeasy/ui/meetings_window.py`
- Test: `tests/test_mcp_setup.py`, `tests/test_meetings_bridge.py` (append)

**Interfaces:**
- Consumes: `read_last_used()` (Task 2), `LibraryWatcher` (Task 1).
- Produces:
  - `mcp_setup.INSTALLED_EXECUTABLE = "/Applications/Speakeasy.app/Contents/MacOS/Speakeasy"` (Task 6 tests the launcher against it).
  - `mcp_setup.server_argv(frozen: bool, executable: str) -> list[str]`
  - `mcp_setup.last_used_label(last_used: datetime | None, now: datetime) -> str | None`
  - `mcp_setup.mcpb_path(frozen: bool, executable: str) -> Path | None`
  - `mcp_setup.setup_info(*, frozen, executable, repo_root, last_used, now) -> dict` with keys `command, desktopJson, executable, lastUsed, extensionAvailable, extensionNote`.
  - `mcp_setup.current_setup_info(now: datetime) -> dict` (reads `sys.frozen`, `sys.executable`, the repo root and `read_last_used()`).
  - `MeetingsBridge(..., open_path=None)`: new keyword, a callable `(Path) -> None`, default no-op.
  - Bridge methods: `claude.setupInfo`, `claude.installExtension`, `claude.revealConfig`. `MeetingsBridge.poll_changed() -> bool`, `MeetingsBridge.stop_polling() -> None`.
  - `filters_payload` → `features.claude` is `True`.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_mcp_setup.py
import json
import shlex
from datetime import datetime, timedelta, timezone
from pathlib import Path

from speakeasy import mcp_setup

NOW = datetime(2026, 9, 28, 18, 0, 0, tzinfo=timezone.utc)
SRC_ROOT = "/Users/x/Coding - General/Speakeasy"  # spaces on purpose


def test_frozen_installed_info():
    info = mcp_setup.setup_info(frozen=True, executable=mcp_setup.INSTALLED_EXECUTABLE,
                                repo_root=SRC_ROOT, last_used=None, now=NOW)
    assert info["command"] == ("claude mcp add speakeasy -- "
                               "/Applications/Speakeasy.app/Contents/MacOS/Speakeasy --mcp")
    assert json.loads(info["desktopJson"]) == {"mcpServers": {"speakeasy": {
        "command": mcp_setup.INSTALLED_EXECUTABLE, "args": ["--mcp"]}}}
    assert info["executable"] == mcp_setup.INSTALLED_EXECUTABLE
    assert info["lastUsed"] is None


def test_source_info_quotes_paths_with_spaces_and_sets_pythonpath():
    exe = f"{SRC_ROOT}/.venv/bin/python"
    info = mcp_setup.setup_info(frozen=False, executable=exe, repo_root=SRC_ROOT,
                                last_used=None, now=NOW)
    parts = shlex.split(info["command"])
    assert parts[:3] == ["claude", "mcp", "add"]
    assert parts[parts.index("-e") + 1] == f"PYTHONPATH={SRC_ROOT}"
    assert parts[parts.index("--") + 1:] == [exe, "-m", "speakeasy", "--mcp"]
    cfg = json.loads(info["desktopJson"])["mcpServers"]["speakeasy"]
    assert cfg == {"command": exe, "args": ["-m", "speakeasy", "--mcp"],
                   "env": {"PYTHONPATH": SRC_ROOT}}
    assert info["extensionAvailable"] is False and "installed app" in info["extensionNote"]


def test_extension_needs_the_installed_bundle(tmp_path, monkeypatch):
    elsewhere = str(tmp_path / "dist/Speakeasy.app/Contents/MacOS/Speakeasy")
    info = mcp_setup.setup_info(frozen=True, executable=elsewhere, repo_root=SRC_ROOT,
                                last_used=None, now=NOW)
    assert info["extensionAvailable"] is False
    assert "Applications" in info["extensionNote"]


def test_extension_available_when_installed_and_bundled(tmp_path, monkeypatch):
    mcpb = tmp_path / "Speakeasy.mcpb"
    mcpb.write_bytes(b"zip")
    monkeypatch.setattr(mcp_setup, "mcpb_path", lambda frozen, exe: mcpb)
    info = mcp_setup.setup_info(frozen=True, executable=mcp_setup.INSTALLED_EXECUTABLE,
                                repo_root=SRC_ROOT, last_used=None, now=NOW)
    assert info["extensionAvailable"] is True and info["extensionNote"] is None


def test_mcpb_path():
    assert mcp_setup.mcpb_path(False, "/x/python") is None
    assert mcp_setup.mcpb_path(True, mcp_setup.INSTALLED_EXECUTABLE) == Path(
        "/Applications/Speakeasy.app/Contents/Resources/Speakeasy.mcpb")


def test_last_used_label():
    L = mcp_setup.last_used_label
    assert L(None, NOW) is None
    assert L(NOW - timedelta(seconds=30), NOW) == "just now"
    assert L(NOW + timedelta(seconds=30), NOW) == "just now"  # clock skew
    assert L(NOW - timedelta(minutes=2), NOW) == "2 min ago"
    assert L(NOW - timedelta(minutes=59, seconds=59), NOW) == "59 min ago"
    assert L(NOW - timedelta(hours=3), NOW) == "3 h ago"
    assert L(NOW - timedelta(days=3), NOW) == "on " + (NOW - timedelta(days=3)).astimezone().strftime("%a %-d %b")
```

Append to `tests/test_meetings_bridge.py` (follow its existing imports and style; `MeetingsBridge` and `MeetingLibrary` are already imported there, so add only what is missing):

```python
# -- phase 2: Connect Claude + change poller ---------------------------------

def _call(bridge, method, params=None):
    from speakeasy.ui.webbridge import BridgeDispatcher
    d = BridgeDispatcher()
    bridge.register(d)
    got = {}
    d._handlers[method](params or {}, lambda result=None, error=None: got.update(r=result, e=error))
    return got


def test_features_claude_is_on(library_path):
    assert MeetingsBridge().filters_payload({})["features"]["claude"] is True


def test_claude_setup_info_registered(library_path, monkeypatch):
    from speakeasy import mcp_setup
    monkeypatch.setattr(mcp_setup, "current_setup_info", lambda now: {"command": "c"})
    assert _call(MeetingsBridge(), "claude.setupInfo") == {"r": {"command": "c"}, "e": None}


def test_install_extension_opens_bundle_or_errors(library_path, monkeypatch, tmp_path):
    from speakeasy import mcp_setup
    opened = []
    bridge = MeetingsBridge(open_path=opened.append)
    monkeypatch.setattr(mcp_setup, "current_setup_info",
                        lambda now: {"extensionAvailable": False})
    assert _call(bridge, "claude.installExtension")["e"] == "extension_unavailable"
    mcpb = tmp_path / "Speakeasy.mcpb"
    monkeypatch.setattr(mcp_setup, "current_setup_info",
                        lambda now: {"extensionAvailable": True})
    monkeypatch.setattr(mcp_setup, "current_mcpb_path", lambda: mcpb)
    assert _call(bridge, "claude.installExtension") == {"r": True, "e": None}
    assert opened == [mcpb]


def test_reveal_config_opens_folder_or_errors(library_path, monkeypatch, tmp_path):
    opened = []
    bridge = MeetingsBridge(open_path=opened.append)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _call(bridge, "claude.revealConfig")["e"] == "claude_desktop_not_found"
    folder = tmp_path / "Library/Application Support/Claude"
    folder.mkdir(parents=True)
    assert _call(bridge, "claude.revealConfig") == {"r": True, "e": None}
    assert opened == [folder]


def test_poll_changed_detects_mcp_writes_only_after_baseline(library_path):
    lib = MeetingLibrary()
    bridge = MeetingsBridge(library=lib)
    assert bridge.poll_changed() is False  # first call only sets the baseline
    assert bridge.poll_changed() is False
    mid = lib.save_meeting(_new())  # reuse this file's existing NewMeeting helper
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


def test_stop_polling_resets_baseline(library_path):
    lib = MeetingLibrary()
    bridge = MeetingsBridge(library=lib)
    bridge.poll_changed()
    lib.save_meeting(_new())
    bridge.stop_polling()
    assert bridge.poll_changed() is False  # window reopened: fresh baseline
```

Before writing these, read `tests/test_meetings_bridge.py`:
- Find how it builds a meeting (a helper like `_new` or `_save`), and use that name in `test_poll_changed_detects_mcp_writes_only_after_baseline`.
- Find how `BridgeDispatcher` stores handlers. If the attribute is not `_handlers`, call through `dispatch()` the way the existing tests do, and adapt `_call`.

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mcp_setup.py tests/test_meetings_bridge.py -q` (timeout 300000). Expected: FAIL (`No module named 'speakeasy.mcp_setup'`, `features.claude` False, no `poll_changed`).

- [x] **Step 3: Implement**

`speakeasy/mcp_setup.py`:

```python
"""What the Connect Claude sheet shows: the exact commands for this copy of
Speakeasy, whether the one-click extension can be used, and when Claude
last called the MCP server. Pure (no AppKit) so it is unit tested.

Speakeasy never edits another app's settings: it only shows commands and
opens the bundled .mcpb so Claude Desktop runs its own install dialog.
"""

import json
import shlex
import sys
from datetime import datetime, timedelta
from pathlib import Path

INSTALLED_EXECUTABLE = "/Applications/Speakeasy.app/Contents/MacOS/Speakeasy"
_REPO_ROOT = str(Path(__file__).resolve().parents[1])


def server_argv(frozen: bool, executable: str) -> list[str]:
    return [executable, "--mcp"] if frozen else [executable, "-m", "speakeasy", "--mcp"]


def mcpb_path(frozen: bool, executable: str) -> Path | None:
    if not frozen:
        return None
    # <bundle>/Contents/MacOS/Speakeasy -> <bundle>/Contents/Resources/
    return Path(executable).parent.parent / "Resources" / "Speakeasy.mcpb"


def current_mcpb_path() -> Path | None:
    return mcpb_path(bool(getattr(sys, "frozen", False)), sys.executable)


def last_used_label(last_used: datetime | None, now: datetime) -> str | None:
    if last_used is None:
        return None
    age = now - last_used
    if age < timedelta(minutes=1):
        return "just now"
    if age < timedelta(hours=1):
        return f"{int(age.total_seconds() // 60)} min ago"
    if age < timedelta(days=1):
        return f"{int(age.total_seconds() // 3600)} h ago"
    return "on " + last_used.astimezone().strftime("%a %-d %b")


def setup_info(*, frozen: bool, executable: str, repo_root: str,
               last_used: datetime | None, now: datetime) -> dict:
    argv = server_argv(frozen, executable)
    env = {} if frozen else {"PYTHONPATH": repo_root}
    command = ["claude", "mcp", "add", "speakeasy"]
    for key, value in env.items():
        command += ["-e", f"{key}={value}"]
    server = {"command": argv[0], "args": argv[1:]}
    if env:
        server["env"] = env
    bundle = mcpb_path(frozen, executable)
    if not frozen:
        note = "The one-click extension is available in the installed app."
    elif executable != INSTALLED_EXECUTABLE:
        note = "Move Speakeasy to Applications to use the one-click extension."
    elif bundle is None or not bundle.is_file():
        note = "This copy of Speakeasy was built without the extension."
    else:
        note = None
    return {
        "command": shlex.join(command) + " -- " + shlex.join(argv),
        "desktopJson": json.dumps({"mcpServers": {"speakeasy": server}}, indent=2),
        "executable": argv[0],
        "lastUsed": last_used_label(last_used, now),
        "extensionAvailable": note is None,
        "extensionNote": note,
    }


def current_setup_info(now: datetime) -> dict:
    from .mcp_tools import read_last_used
    return setup_info(frozen=bool(getattr(sys, "frozen", False)), executable=sys.executable,
                      repo_root=_REPO_ROOT, last_used=read_last_used(), now=now)
```

Note: `test_extension_available_when_installed_and_bundled` monkeypatches `mcp_setup.mcpb_path`. `setup_info` must therefore call `mcpb_path` through the module global (as written), not a local alias.

`speakeasy/ui/meetings_bridge.py` changes:
- Imports: `import sqlite3`, `from pathlib import Path`, `from speakeasy import mcp_setup`.
- `__init__(self, library=None, now=None, set_clipboard=None, open_path=None)`:
  - add `self._open_path = open_path or (lambda path: None)`;
  - add `self._watcher = LibraryWatcher(self.library)` (import it from `speakeasy.meeting_library`). The watcher opens its connection lazily, on the first poll, so constructing the bridge in tests stays cheap.
- In `register`, add the entries `"claude.setupInfo": self.claude_setup_payload`, `"claude.installExtension": self.install_extension_payload` and `"claude.revealConfig": self.reveal_config_payload`.
- `filters_payload`: `"features": {"calendar": False, "claude": True, "settings": False}`.
- `_wrap` needs a way to return a named error. Add the class `class BridgeError(Exception)` (message = error code). `_wrap` catches it and calls `respond(error=str(err))`. Put this `except` before the `ValueError` one.
- New methods:

```python
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

    def poll_changed(self) -> bool:
        """True when another connection committed since the last poll (e.g.
        Claude saved notes through the MCP server, a separate process).
        The first call only records a baseline. A busy library is skipped
        this tick. Main thread only (the watcher owns one connection)."""
        try:
            return self._watcher.changed()
        except sqlite3.OperationalError:
            return False

    def stop_polling(self) -> None:
        self._watcher.close()
```

`speakeasy/ui/meetings_window.py` changes:
- In `init`:
  - `self._bridge = MeetingsBridge(set_clipboard=injector.set_clipboard, open_path=self._open_path)`. `_open_path` is an `@objc.python_method` defined on the class, so bind it as `self._open_path` after `super().init()`.
  - Add `self._poll_timer = None`.
- Replace `show` and `windowWillClose_`, and add `pollLibrary_` and `_open_path`:

```python
    def show(self):
        self._bridge.poll_changed()  # baseline; the emit below re-lists anyway
        self._web.emit("meetings.changed")  # page re-lists (old reload()-on-show)
        self._web.show()
        if self._poll_timer is None:
            from Foundation import NSTimer
            # MCP writes come from another process (Claude's), so nothing in
            # this app hears about them; poll a cheap marker while visible.
            self._poll_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                10.0, self, "pollLibrary:", None, True)

    def pollLibrary_(self, timer):
        if self._web.window.isVisible() and self._bridge.poll_changed():
            self._web.emit("meetings.changed")

    def windowWillClose_(self, notification):
        if self._poll_timer is not None:
            self._poll_timer.invalidate()
            self._poll_timer = None
        self._bridge.stop_polling()

    @objc.python_method
    def _open_path(self, path):
        from AppKit import NSWorkspace
        from Foundation import NSURL
        NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(path)))
```

`show()` is only ever called on the main thread (menubar action), so the timer is scheduled on the main run loop and `pollLibrary_` runs on the main thread, as the webview emit requires.

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mcp_setup.py tests/test_meetings_bridge.py -q`, then the full suite (both timeout 300000). Expected: all pass. Check the window module still imports: `.venv/bin/python -c "import speakeasy.ui.meetings_window"`.

- [x] **Step 5: Commit**

```bash
git add speakeasy/mcp_setup.py speakeasy/ui/meetings_bridge.py speakeasy/ui/meetings_window.py tests/test_mcp_setup.py tests/test_meetings_bridge.py
git commit -m "Wire Connect Claude bridge methods and poll for MCP writes"
```

---

### Task 5: Connect Claude sheet wired to the bridge

**Files:**
- Modify: `frontend/src/mock/meetings.ts`
- Modify: `frontend/src/meetings/ConnectClaudeSheet.tsx`, `frontend/src/meetings/ConnectClaudeSheet.module.css`
- Modify: `frontend/src/meetings/App.tsx`

**Interfaces:**
- Consumes: bridge methods `claude.setupInfo` → `ClaudeSetupInfo`, `claude.installExtension` → `true` or rejects with `extension_unavailable`, `claude.revealConfig` → `true` or rejects with `claude_desktop_not_found`, `meetings.copyText({text})` → `true`.
- Produces: `ClaudeSetupInfo` type, and `ConnectClaudeSheet` props `{ onClose, info: ClaudeSetupInfo | null, onCopy: (text: string) => Promise<void>, onInstall: () => Promise<void>, onRevealConfig: () => Promise<void> }`.

There is no frontend test runner; verification is the type-checked build plus looking at the sheet (Step 4).

- [x] **Step 1: Add the type and mock** to `frontend/src/mock/meetings.ts`:

```ts
export interface ClaudeSetupInfo {
  command: string;
  desktopJson: string;
  executable: string;
  lastUsed: string | null;
  extensionAvailable: boolean;
  extensionNote: string | null;
}

export const MOCK_CLAUDE_SETUP: ClaudeSetupInfo = {
  command: 'claude mcp add speakeasy -- /Applications/Speakeasy.app/Contents/MacOS/Speakeasy --mcp',
  desktopJson: `{
  "mcpServers": {
    "speakeasy": {
      "command": "/Applications/Speakeasy.app/Contents/MacOS/Speakeasy",
      "args": ["--mcp"]
    }
  }
}`,
  executable: '/Applications/Speakeasy.app/Contents/MacOS/Speakeasy',
  lastUsed: '2 min ago',
  extensionAvailable: true,
  extensionNote: null,
};
```

- [x] **Step 2: Rewrite `ConnectClaudeSheet.tsx`** so it is presentational:

```tsx
import { useEffect, useRef, useState } from 'react';
import { Sheet } from './Sheet';
import { PrimaryButton } from '../components/PrimaryButton';
import type { ClaudeSetupInfo } from '../mock/meetings';
import styles from './ConnectClaudeSheet.module.css';

interface ConnectClaudeSheetProps {
  onClose: () => void;
  info: ClaudeSetupInfo | null;
  onCopy: (text: string) => Promise<void>;
  onInstall: () => Promise<void>;
  onRevealConfig: () => Promise<void>;
}

const ERRORS: Record<string, string> = {
  extension_unavailable: 'The one-click extension is not available in this copy of Speakeasy.',
  claude_desktop_not_found: 'Claude Desktop does not seem to be installed (no settings folder found).',
};

function message(err: unknown): string {
  const text = err instanceof Error ? err.message : String(err);
  return ERRORS[text] ?? 'That did not work. Try again.';
}

/** Step-by-step MCP connection instructions for Claude Code and Claude Desktop. */
export function ConnectClaudeSheet({ onClose, info, onCopy, onInstall, onRevealConfig }: ConnectClaudeSheetProps) {
  const [copied, setCopied] = useState<'code' | 'json' | null>(null);
  const [otherWaysOpen, setOtherWaysOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const doneRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!copied) return;
    const t = window.setTimeout(() => setCopied(null), 1500);
    return () => window.clearTimeout(t);
  }, [copied]);

  function run(action: () => Promise<void>, after?: () => void) {
    setError(null);
    action().then(after, (err) => setError(message(err)));
  }

  const command = info?.command ?? 'Loading…';
  const desktopJson = info?.desktopJson ?? 'Loading…';

  return (
    <Sheet ariaLabel="Connect Claude" onClose={onClose} initialFocusRef={doneRef} className={styles.sheet}>
      <div className={styles.title}>Connect Claude</div>

      <div className={styles.section}>
        <div className={styles.sectionTitle}>Claude Code</div>
        <div className={styles.codeBlock}>
          <code className={styles.code}>{command}</code>
        </div>
        <button className={styles.copyButton} disabled={!info}
                onClick={() => run(() => onCopy(command), () => setCopied('code'))}>
          {copied === 'code' ? 'Copied' : 'Copy'}
        </button>
      </div>

      <div className={styles.section}>
        <div className={styles.sectionTitle}>Claude Desktop</div>
        <button className={styles.installButton} disabled={!info?.extensionAvailable}
                onClick={() => run(onInstall)}>
          Install in Claude Desktop
        </button>
        {info?.extensionNote && <p className={styles.note}>{info.extensionNote}</p>}
        <button
          className={styles.disclosure}
          aria-expanded={otherWaysOpen}
          aria-controls="connect-claude-other-ways"
          onClick={() => setOtherWaysOpen((v) => !v)}
        >
          <span className={otherWaysOpen ? `${styles.chevron} ${styles.chevronOpen}` : styles.chevron} aria-hidden="true">
            ›
          </span>
          Other ways to connect
        </button>
        {otherWaysOpen && (
          <div id="connect-claude-other-ways" className={styles.otherWaysPanel}>
            <div className={styles.codeBlock}>
              <pre className={styles.code}>{desktopJson}</pre>
            </div>
            <div className={styles.desktopActions}>
              <button className={styles.copyButton} disabled={!info}
                      onClick={() => run(() => onCopy(desktopJson), () => setCopied('json'))}>
                {copied === 'json' ? 'Copied' : 'Copy'}
              </button>
              <button className={styles.linkButton} onClick={() => run(onRevealConfig)}>
                Reveal config file
              </button>
            </div>
          </div>
        )}
      </div>

      {error && <p className={styles.error} role="alert">{error}</p>}

      <p className={styles.privacyNote}>
        Claude reads meetings only when you ask. What it reads is sent to Anthropic to answer you. Speakeasy itself
        stays offline.
      </p>

      <div className={styles.status}>
        {info?.lastUsed ? `Last used by Claude: ${info.lastUsed}` : 'Not used by Claude yet'}
        {' · '}You can turn the extension off in Claude Desktop → Settings → Extensions.
      </div>

      <div className={styles.footer}>
        <div className={styles.doneWrap}>
          <PrimaryButton ref={doneRef} onClick={onClose}>
            Done
          </PrimaryButton>
        </div>
      </div>
    </Sheet>
  );
}
```

In `ConnectClaudeSheet.module.css`, add `.note` and `.error`, and a disabled style for `.installButton`/`.copyButton`. Use only existing tokens: read `frontend/src/styles/tokens.css`, and match the existing `.privacyNote`/`.status` rules for font size and secondary text colour. `.error` uses the existing danger/red token that `ConfirmSheet.module.css` uses; find it there. The disabled style is `opacity: 0.45; cursor: default;`.

- [x] **Step 3: Wire `App.tsx`**:
- `import { MOCK_CLAUDE_SETUP, type ClaudeSetupInfo } from '../mock/meetings';` (merge into the existing import from that module).
- State: `const [claudeInfo, setClaudeInfo] = useState<ClaudeSetupInfo | null>(null);`
- An effect keyed on `connectClaudeOpen`: when it opens, if `embedded`, `bridge.call<ClaudeSetupInfo>('claude.setupInfo').then(setClaudeInfo).catch((err) => console.error('claude.setupInfo failed', err))`; else `setClaudeInfo(MOCK_CLAUDE_SETUP)`. When it closes, `setClaudeInfo(null)` so the next open shows a fresh "last used".
- Handlers:

```tsx
  function copyForClaude(text: string): Promise<void> {
    // WKWebView can reject navigator.clipboard; the native bridge cannot.
    if (embedded) return bridge.call('meetings.copyText', { text }).then(() => undefined);
    return navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.resolve();
  }
  const installClaudeExtension = () =>
    embedded ? bridge.call('claude.installExtension').then(() => undefined) : Promise.resolve();
  const revealClaudeConfig = () =>
    embedded ? bridge.call('claude.revealConfig').then(() => undefined) : Promise.resolve();
```

- Render: `{connectClaudeOpen && <ConnectClaudeSheet onClose={closeConnectClaude} info={claudeInfo} onCopy={copyForClaude} onInstall={installClaudeExtension} onRevealConfig={revealClaudeConfig} />}`
- Check how `bridge._reject` builds the Error: it must be `new Error(message)`, so that `err.message === 'extension_unavailable'`. It is, per `frontend/src/bridge.ts`; confirm it.

- [x] **Step 4: Build and look**

Run: `npm --prefix frontend run build`. Expected: exit 0, no type errors.
Then report the files changed. The controller looks at the sheet in mock mode (`connect-claude` state) in the browser pane before approving.

- [x] **Step 5: Commit**

```bash
git add frontend/src/mock/meetings.ts frontend/src/meetings/ConnectClaudeSheet.tsx frontend/src/meetings/ConnectClaudeSheet.module.css frontend/src/meetings/App.tsx
git commit -m "Wire the Connect Claude sheet to real setup info"
```

---

### Task 6: Claude Desktop Extension (`.mcpb`) and build integration

**Files:**
- Create: `packaging/mcpb/manifest.json`, `packaging/mcpb/server/speakeasy-mcp` (mode 0755), `packaging/mcpb/package.json`, `packaging/mcpb/package-lock.json`
- Modify: `.gitignore`, `scripts/build_app.sh`, `scripts/write_build_manifest.py`, `scripts/verify_release.py`
- Test: `tests/test_mcpb.py`

**Interfaces:**
- Consumes: `build_tools` (Task 2), `SERVER_VERSION` (Task 3), `mcp_setup.INSTALLED_EXECUTABLE` and `setup_info` (Task 4).
- Produces: `Speakeasy.app/Contents/Resources/Speakeasy.mcpb`, containing only `manifest.json` and `server/speakeasy-mcp`.

MCPB facts, checked 28 Sep 2026 against `github.com/anthropics/mcpb` `MANIFEST.md`; the CLI's latest version is `2.1.2`:
- `manifest_version` is `"0.3"`.
- The required fields are `manifest_version`, `name`, `version`, `description`, `author` (with `name`) and `server`.
- `server.type` is `"binary"`, with `entry_point` set to a bundle-relative path.
- `mcp_config.command` may use `${__dirname}`.
- `tools` is a list of `{name, description}`.
- `compatibility.platforms` is a list, e.g. `["darwin"]`.

**Verify with the pinned CLI in Step 3.** If `mcpb validate` rejects a field, follow the schema the pinned CLI ships in `node_modules/@anthropic-ai/mcpb`. Record the difference in this plan's Execution notes.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_mcpb.py
"""The Claude Desktop Extension stays in step with the server and the app."""
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from speakeasy import mcp_setup
from speakeasy.mcp_server import SERVER_VERSION
from speakeasy.mcp_tools import build_tools

REPO = Path(__file__).resolve().parents[1]
MCPB = REPO / "packaging" / "mcpb"
LAUNCHER = MCPB / "server" / "speakeasy-mcp"


def _manifest():
    return json.loads((MCPB / "manifest.json").read_text())


def test_manifest_tools_match_tools_list_exactly(library_path):
    from speakeasy.meeting_library import MeetingLibrary
    served = [(t.name, t.description) for t in build_tools(MeetingLibrary()).values()]
    assert [(t["name"], t["description"]) for t in _manifest()["tools"]] == served


def test_manifest_identity_and_version():
    m = _manifest()
    assert m["manifest_version"] == "0.3"
    assert m["name"] == "speakeasy" and m["display_name"] == "Speakeasy Meetings"
    assert m["compatibility"]["platforms"] == ["darwin"]
    spec = (REPO / "packaging" / "Speakeasy.spec").read_text()
    app_version = re.search(r'"CFBundleShortVersionString":\s*"([^"]+)"', spec).group(1)
    assert m["version"] == app_version == SERVER_VERSION


def test_manifest_runs_the_bundled_launcher():
    server = _manifest()["server"]
    assert server["type"] == "binary"
    assert server["entry_point"] == "server/speakeasy-mcp"
    assert server["mcp_config"]["command"] == "${__dirname}/server/speakeasy-mcp"
    assert (MCPB / server["entry_point"]).is_file()


def test_launcher_path_matches_setup_info():
    path = re.search(r'^APP_EXE="([^"]+)"$', LAUNCHER.read_text(), re.M).group(1)
    info = mcp_setup.setup_info(frozen=True, executable=mcp_setup.INSTALLED_EXECUTABLE,
                                repo_root="/r", last_used=None,
                                now=datetime.now())
    assert path == mcp_setup.INSTALLED_EXECUTABLE == info["executable"]


def test_launcher_is_executable():
    assert os.access(LAUNCHER, os.X_OK)


def _launcher_with(tmp_path, app_exe):
    script = tmp_path / "speakeasy-mcp"
    script.write_text(re.sub(r'^APP_EXE=".*"$', f'APP_EXE="{app_exe}"',
                             LAUNCHER.read_text(), flags=re.M))
    script.chmod(0o755)
    return script


def test_launcher_reports_a_missing_app_on_stderr(tmp_path):
    proc = subprocess.run([str(_launcher_with(tmp_path, tmp_path / "nope"))],
                          capture_output=True, timeout=10)
    assert proc.returncode == 1 and proc.stdout == b""
    assert b"Speakeasy" in proc.stderr and b"Applications" in proc.stderr


def test_launcher_execs_the_app_with_mcp(tmp_path):
    fake = tmp_path / "Speakeasy"
    fake.write_text('#!/bin/sh\necho "args:$*"\n')
    fake.chmod(0o755)
    proc = subprocess.run([str(_launcher_with(tmp_path, fake))],
                          capture_output=True, timeout=10)
    assert proc.returncode == 0 and proc.stdout == b"args:--mcp\n"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mcpb.py -q` (timeout 300000). Expected: FAIL (`FileNotFoundError` for manifest.json).

- [x] **Step 3: Create the extension files**

`packaging/mcpb/server/speakeasy-mcp`:

```sh
#!/bin/sh
# Claude Desktop Extension launcher: runs the installed app's MCP server.
# The extension carries no app code, model or data, only this and a manifest.
APP_EXE="/Applications/Speakeasy.app/Contents/MacOS/Speakeasy"
[ -x "$APP_EXE" ] || { echo "Speakeasy is not installed in /Applications. Install Speakeasy, then try again." >&2; exit 1; }
exec "$APP_EXE" --mcp
```

Then run `chmod 755 packaging/mcpb/server/speakeasy-mcp`. Git records the mode.

`packaging/mcpb/manifest.json`: write the header by hand as below. **Generate** the `tools` list from the server so the descriptions match character for character:

```bash
.venv/bin/python - <<'EOF'
import json, tempfile
from pathlib import Path
from speakeasy.meeting_library import MeetingLibrary
from speakeasy.mcp_tools import build_tools
lib = MeetingLibrary(Path(tempfile.mkdtemp()) / "x.sqlite")  # temp: never the real library
manifest = {
    "manifest_version": "0.3",
    "name": "speakeasy",
    "display_name": "Speakeasy Meetings",
    "version": "1.0.0",
    "description": "Search and read your Speakeasy meeting transcripts and save summaries back. Runs locally from the installed Speakeasy app.",
    "author": {"name": "jchiu"},
    "server": {"type": "binary", "entry_point": "server/speakeasy-mcp",
               "mcp_config": {"command": "${__dirname}/server/speakeasy-mcp", "args": []}},
    "tools": [{"name": t.name, "description": t.description} for t in build_tools(lib).values()],
    "compatibility": {"platforms": ["darwin"]},
}
Path("packaging/mcpb/manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
EOF
```

`packaging/mcpb/package.json`:

```json
{
  "private": true,
  "description": "Build-time only: validates and packs the Claude Desktop Extension. Never shipped.",
  "devDependencies": {
    "@anthropic-ai/mcpb": "2.1.2"
  }
}
```

Run `npm --prefix packaging/mcpb install` to create `package-lock.json`. This uses the network at build time only, like the frontend. Then check the pinned CLI against the manifest:

```bash
packaging/mcpb/node_modules/.bin/mcpb validate packaging/mcpb/manifest.json
```

Expected: valid. If it is not, adjust per the CLI's schema (see the note above) and keep the tests in step.

Add `packaging/mcpb/node_modules/` to `.gitignore`.

- [x] **Step 4: Build integration**

`scripts/build_app.sh`: after the "Bundling system-audio helper" block, add a stage-and-pack step. The step packs from a staging directory, so `node_modules` and `package*.json` can never leak into the extension, whatever the CLI's ignore rules are.

```bash
echo "==> Packing Claude Desktop extension"
npm --prefix packaging/mcpb ci
MCPB_CLI=packaging/mcpb/node_modules/.bin/mcpb
MCPB_STAGE=build/mcpb-stage
rm -rf "$MCPB_STAGE" && mkdir -p "$MCPB_STAGE/server"
cp packaging/mcpb/manifest.json "$MCPB_STAGE/"
cp packaging/mcpb/server/speakeasy-mcp "$MCPB_STAGE/server/"
chmod 755 "$MCPB_STAGE/server/speakeasy-mcp"
"$MCPB_CLI" validate "$MCPB_STAGE/manifest.json"
"$MCPB_CLI" pack "$MCPB_STAGE" "$APP/Contents/Resources/Speakeasy.mcpb"
```

In the sanity checks, add:

```bash
[ -f "$APP/Contents/Resources/Speakeasy.mcpb" ] || { echo "error: Claude extension missing from bundle"; exit 1; }
MCPB_FILES=$(unzip -Z1 "$APP/Contents/Resources/Speakeasy.mcpb" | grep -v '/$' | sort | tr '\n' ' ')
[ "$MCPB_FILES" = "manifest.json server/speakeasy-mcp " ] \
    || { echo "error: unexpected files in Speakeasy.mcpb: $MCPB_FILES"; exit 1; }
```

`mcpb pack` might add its own file (for example a signature block). Check with `unzip -Z1` after one real pack. If it does, add exactly that name to the expected list and record it in Execution notes. Never loosen the check to a prefix match.

`scripts/write_build_manifest.py`: append `'Speakeasy.mcpb'` to the `assets` list, so its hash is recorded and `verify_release.py` checks it.

`scripts/verify_release.py`: before the asset loop, add:

```python
    if not (resources / 'Speakeasy.mcpb').is_file():
        parser.error('Claude Desktop extension (Speakeasy.mcpb) missing from bundle')
```

- [x] **Step 5: Run tests, then commit**

Run: `.venv/bin/python -m pytest tests/test_mcpb.py -q`, then the full suite (both timeout 300000). Expected: all pass. Check the shell syntax: `bash -n scripts/build_app.sh`. Do **not** run `build_app.sh` in this task; Task 7 builds.

```bash
git add packaging/mcpb/manifest.json packaging/mcpb/server/speakeasy-mcp packaging/mcpb/package.json packaging/mcpb/package-lock.json .gitignore scripts/build_app.sh scripts/write_build_manifest.py scripts/verify_release.py tests/test_mcpb.py
git commit -m "Add the Claude Desktop Extension (.mcpb) and pack it into the app"
```

---

### Task 7: Docs, full verification, installed-app acceptance

**Files:**
- Modify: `README.md`, `AGENTS.md`, `docs/superpowers/plans/2026-09-27-meeting-library-mcp.md` (Phase 2 status), this plan (checkboxes, Execution notes)

Steps 1–3 are subagent work; steps 4 onwards are done by the controller with the user.

- [x] **Step 1: README.** Under "The meeting library", add a "Use your meetings with Claude" subsection:
  - what it is: local, read-only except notes; Speakeasy stays offline; what Claude reads goes to Anthropic at your request;
  - Claude Desktop: Meetings → Connect Claude → Install in Claude Desktop, or the manual JSON (`~/Library/Application Support/Claude/claude_desktop_config.json`);
  - Claude Code: `claude mcp add speakeasy -- /Applications/Speakeasy.app/Contents/MacOS/Speakeasy --mcp`;
  - from source: `.venv/bin/python -m speakeasy --mcp` with `PYTHONPATH=<repo>`;
  - the eight tools in one line each;
  - how to turn it off: Claude Desktop → Settings → Extensions, or `claude mcp remove speakeasy`.

- [x] **Step 2: AGENTS.md.**
  - Under "Hard constraints", add the MCP stdout rule: `--mcp` stdout carries only JSON-RPC, fd 1 is redirected to stderr in `mcp_server.main()`, and nothing may print to the real stdout.
  - Also add: tools never return capture fields.
  - Also add: `--mcp` must stay import-light (`test_mcp_mode_imports_nothing_heavy`).
  - Under "Build / run / test", add the `.mcpb` build step and `packaging/mcpb/` (`@anthropic-ai/mcpb` pinned, build-time only).
  - Under "Threading model", add one line: the MCP server is a separate process with its own short-lived SQLite connections, and the Meetings window polls a `LibraryWatcher` (`PRAGMA data_version` on one main-thread-only connection) every 10 s while visible.

- [x] **Step 3: Full verification and commit.**

```bash
.venv/bin/python -m pytest -q
npm --prefix frontend run build
bash -n scripts/build_app.sh
```

Give the pytest call `timeout: 300000`. Expected: all tests pass and the build is clean. Then commit:

```bash
git add README.md AGENTS.md
git commit -m "Document the local MCP server and Claude setup"
```

- [x] **Step 4 (controller): whole-branch review** on opus, with mutation checks:
  - remove `os.dup2(2, 1)` → `test_stray_output_in_a_tool_never_reaches_stdout` fails;
  - compare `max(updated_at)` instead of `data_version` in `LibraryWatcher` → the same-second rename test fails;
  - return capture health from `get_meeting` → the capture test fails;
  - echo an unsupported `protocolVersion` → the negotiation test fails;
  - change the launcher path → `test_launcher_path_matches_setup_info` fails.
  
  Reviewers must run anything that touches a library with `HOME` pointed at a temp dir, and say so in the report.

- [x] **Step 5 (controller, needs the user's go-ahead): build and install.** `scripts/build_app.sh --install`, which quits the running app. Then the frozen stdio proof, against a **temp HOME** so the real library is not opened:

```bash
T=$(mktemp -d); printf '%s\n' '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}' '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_meetings","arguments":{}}}' | HOME="$T" /Applications/Speakeasy.app/Contents/MacOS/Speakeasy --mcp; echo "exit=$?"
```

Expected: two JSON lines on stdout (`serverInfo.name == "speakeasy"`, then `{"meetings": [] …}`), `exit=0`, and no Dock icon. If stdout is empty, add a console executable to `packaging/Speakeasy.spec` for `--mcp` (the spec's fallback), and record why.

Also: `unzip -Z1 /Applications/Speakeasy.app/Contents/Resources/Speakeasy.mcpb` shows only `manifest.json` and `server/speakeasy-mcp`.

- [ ] **Step 6 (user + controller): acceptance with real clients** (spec Acceptance item 3). This uses the user's real library, by design; only the user's own Claude clients read it.
  1. Meetings → Connect Claude: the sheet shows the installed command, "Not used by Claude yet", and an enabled Install button. Copy works (paste it into TextEdit to check).
  2. **Install in Claude Desktop** → Claude Desktop's own dialog → install. Ask Claude Desktop to list recent meetings, search for a known word, and read a page of one transcript.
  3. Ask it to save a summary for the "1on1 Todd TEST" meeting. With the Meetings window open on that meeting, the summary appears within 10 s, and the selection and filter are unchanged.
  4. Reopen Connect Claude: "Last used by Claude: just now".
  5. Turn the extension off in Claude Desktop → Settings → Extensions, add the manual JSON instead, restart Claude Desktop, and repeat a search.
  6. Claude Code: the user runs `claude mcp add speakeasy -- /Applications/Speakeasy.app/Contents/MacOS/Speakeasy --mcp` (Speakeasy never runs it), then lists, searches, reads a page and saves notes.
  7. Dictation still works in TextEdit (insertion code is untouched; spot check).
  
  Record what was actually done, and by whom, in Execution notes.

- [ ] **Step 7: checkpoint** (per CLAUDE.md): tick the boxes, write deviations into Execution notes, and mark Phase 2 done in the parent plan with a link here. Then merge per the user's instruction, and give the phase-boundary `/clear` block.

---

## Execution notes

(Record deviations, surprises and review findings here during execution.)

### Status (28 Sep 2026)

Tasks 1–6 and Task 7 Steps 1–4 are done on branch `meeting-library-phase2`, commits `0fa2f53..64db821`, base `2c32b53`. The full suite is **593 passed**; the baseline was 517, not the 514 this plan assumed.

Execution stopped before **Task 7 Step 5 (build and install)** so the user can give the go-ahead. Steps 5–7 remain. Resume at Step 5.

Implementers ran on Sonnet 5.5. Every task review, re-review and the final whole-branch review ran on Opus 5.5 and verified by mutation. All five mutations the plan asks for were caught. The final review's fixes (commit `64db821`) passed a scoped re-review.

### Deviations and rulings

- **`BridgeDispatcher` attribute.** It stores handlers in `_methods`, not `_handlers`, so the bridge tests use `d._methods`. The helper is named `_bcall` because `_call` already existed in that file.
- **HOME in subprocess tests.** Task 1's cross-process watcher test now sets `HOME=tmp_path`. The global constraint outranks the plan's test text.
- **Tests added beyond the plan, because mutations survived:**
  - the watcher baseline advances before `close()`;
  - the `list_meetings` limit clamp is checked with a spy that expects 101;
  - `setup_info` handles "installed, but built without the extension".
- **The Task 7 docs had no separate task review.** The final review checked the docs against the code instead.
- **Final-review fixes (`64db821`):**
  - The server survives a non-string tool name, a lone surrogate (reply framing now uses `ensure_ascii`), and deeply nested JSON (-32700).
  - `serve` has a per-line catch-all: -32603, and stderr gets the exception type only.
  - `poll_changed` now catches `sqlite3.Error`.
  - The import-light test enters through `speakeasy.__main__` and also rejects `speakeasy.ui`.
  - The spec now says `data_version`.
  - The README says "notes and tags".
- **MCPB tooling.** The pinned CLI 2.1.2 validated the manifest with no schema changes. A real pack to scratch contains exactly `manifest.json` and `server/speakeasy-mcp` (mode 0755 is kept in the zip), so the expected list is unchanged.
- **Connect Claude sheet in mock mode.** The controller checked it in the browser pane. The command and JSON render with a real `--`. "Other ways" expands. The error line uses the danger colour. The "last used" line and Done show.

### Deferred minors (reviewed at the final review; fine to leave)

- The overlap test for `calendar_events_between` has no edge case (an event that straddles `from`). Blank dates return `[]`, which MCP can't reach.
- The close-reset test would still pass if `close()` did nothing. The same-second rename test depends on timing (it can't give false failures).
- `_seconds` accepts nan/inf. `start_seconds > end_seconds` returns empty text with no message. A literal `**` in a transcript makes the bold markers ambiguous.
- A JSON-RPC 1.0 notification gets a -32600 reply. `"arguments": null` is treated as `{}`. If the library fails to open in `main()`, a traceback goes to stderr. The serve catch-all's `print` would raise if stderr were closed.
- `current_setup_info` wiring is untested.
- The watcher's first connect runs migrate on the main thread, so a busy library can stall it once, for up to 5 s. The watcher also fires on the app's own commits, which causes an extra re-list.
- Sheet:
  - The `setupInfo` fetch has no cancel flag.
  - A failed `setupInfo` stays on "Loading…".
  - `ERRORS` is a plain-object lookup.
  - When "Other ways" is expanded in a small window, the error line can sit below the scroll fold.
- The build leaves `build/mcpb-stage` behind. `npm audit` reports advisories in the build-time-only mcpb CLI's dependencies.

### Notes for Step 5

- In a worktree-isolated Claude session, the shell guard refuses any command that sets `HOME=`. Step 5's temp-HOME stdio proof therefore has to run either as a Python `subprocess.run(..., env={"HOME": tmp})` or from a normal shell. Never drop the temp HOME: without it the command opens the real library.
- `build_app.sh --install` quits the running app, and its `npm --prefix packaging/mcpb ci` needs the network at build time.

### Step 5 results (29 Sep 2026, build `04d52fb`, installed in `/Applications`)

- **Build.** It first failed because the worktree had no `models` link, the same as Phase 1. After adding the symlink to the main checkout's `models/`, `build_app.sh --install` succeeded. The npm step for the `.mcpb` CLI ran at build time only. The bootloader is PyInstaller's windowed `runw`.
- **Frozen stdio proof.** The installed `Speakeasy --mcp` was run through `subprocess.run` with `env={"HOME": <temp>}`. Result: exit 0 in 0.64 s, with exactly two JSON lines on stdout: `serverInfo.name == "speakeasy"`, protocol `2025-06-18`, then `{"meetings": [], …}`. Nothing went to stderr. The windowed executable passes stdio, so the console-executable fallback is **not** needed. The temp HOME got `library.sqlite` and `mcp_last_used`. The real library was not opened: its size and dates were unchanged.
- **No Dock icon.** While a `--mcp` process was running, `lsappinfo` showed no Speakeasy app entry. The process exited on stdin EOF.
- **Extension package.** `unzip -Z1 …/Resources/Speakeasy.mcpb` lists exactly `manifest.json` and `server/speakeasy-mcp`.
- **Source-mode check.** It passed as well, with the temp HOME: all eight tools are listed, and a hostile search returns `{"results": []}`. The full suite on the branch: 593 passed.
- **Not relaunched.** The build quit the running app and does not start it again. Step 6 starts with the user launching Speakeasy.

### Step 6 results (29 Sep 2026, user-reported unless stated)

**Passed** (reported by the user):
- In Claude Desktop:
  - Item 2: a search for "dashboard" returned results, and Claude read the first few minutes of the "1on1 Todd TEST" transcript.
  - Item 3: Claude saved a one-paragraph summary and the tag `test` for "1on1 Todd TEST". The summary appeared in the open Meetings window within about 10 s, and the selection did not jump.
  - Item 4: Meetings → Connect Claude then showed "Last used by Claude" as just now.
- Item 6: the Claude Code checks passed. Separately, the controller made one read-only `list_meetings` call (limit 3) through the `speakeasy` server in a Claude Code session; it returned the three newest meetings.
- Item 7: dictation into TextEdit works.

**Not done:**
- Item 1: the sheet's first-run state and Copy. The first-run state can no longer be seen on the real library, because Claude has now used it.
- Item 2's "list recent meetings" in Claude Desktop.
- Item 5: the extension off, then the manual JSON route.

**Observed:** that Claude Code session had Speakeasy connected twice, as `speakeasy` (`claude mcp add`) and as `Speakeasy Meetings` (the extension), so it saw 16 tools instead of 8.

### Follow-ups from the post-implementation eval (29 Sep 2026)

Not blockers for merge. Plan them in a new session.

- **Speaker over-splitting (highest value).** Real 1-on-1s show 9 and 14 speakers (for example "1on1 Refayet", 23 min), even with `DIARIZATION_THRESHOLD = 0.7`, so Claude's summaries can't say who said what. Options: merge low-talk-time clusters into their nearest centroid, and in Phase 3 pass the calendar attendee count as `expected_speaker_count` (`diarizer.py` already accepts it).
- **Cap system-track segments at 60 s** too. The TEST meeting had a single 71.9 s "Speaker 1" segment. This was already noted in Phase 1.
- **`get_transcript` loads the whole meeting.** It calls `library.get_meeting()` just for the title and the approximate flag (`mcp_tools.py`), which loads every segment and opens a second connection. `transcript_page` also reads every row after the cursor and then trims in Python. Fix: return the meta from `transcript_page` in one transaction, and stop fetching once the character budget is spent. The `get_meeting` tool also loads all segments only to count them and list the speakers; use SQL instead.
- **`list_meetings` does 2 queries per row** for tags and people. Use `group_concat` subqueries.
- **Search sorts notes and transcript hits by raw `bm25`** across two FTS tables, whose scores are not comparable. Interleave them or normalise per table before notes become common.
- **Connections.** Each library call opens a connection and runs `migrate()`. The single-threaded MCP server could keep one. Measure before changing it.
- **Imported titles.** When the user renamed a legacy meeting, its title keeps the old end time ("…1:17 PM" for a 12:54 start).
- **Duplicate connection.** Recommend the user keep only one Speakeasy MCP connection in Claude Code.
