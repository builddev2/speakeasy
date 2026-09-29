# Meeting Library Phase 3: Calendar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read the work calendar from Calendar.app (EventKit) into the library's cache. Title recordings from the matched event and link its attendees. Use the attendee count to stop diarization over-splitting. Wire the Today view, event chip, Dock event label and Meetings settings. Also fix the Phase 2 post-implementation eval follow-ups.

**Architecture:** `calendar_sync.py` is the only code that touches EventKit. It runs on a new single-thread `calendar` executor and writes plain `SyncedEvent` rows through `MeetingLibrary.replace_calendar_window`. `calendar_match.py` is pure: it picks the event for a recording, lists events to offer to record (Phase 4 uses this), and turns an event into a speaker cap. The engine resolves the event from the cached table after capture has started (it never calls EventKit), and holds it on `engine.meeting_event`. `_process_meeting` saves the title, attendees and link, and passes the cap to `speaker_merge.tidy_speakers`. That function folds tiny diarization clusters into the nearest real voice and merges down to the cap. `ui/calendar_payloads.py` (pure) shapes everything the pages show.

**Tech Stack:** Python 3.11 stdlib + numpy (existing), `pyobjc-framework-EventKit==12.2.1` (new, the only new runtime dependency), pyobjc/AppKit/WebKit (existing), React 18 + CSS modules (existing, no new frontend deps), pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-meeting-library-mcp-design.md`, sections "Data model", "Recording flow changes", "Calendar", "Today view", "Dock and menu bar while recording", "Settings (Meetings section)", "Meetings window data wiring", "Testing", "Acceptance" item 2. Parent plan: `docs/superpowers/plans/2026-09-27-meeting-library-mcp.md` (Phase 3 scope). Phase 2 plan: `docs/superpowers/plans/2026-09-28-meeting-library-mcp-phase2.md`; its "Follow-ups from the post-implementation eval" are Tasks 3, 8 and 9 here.

## What Phases 1–2 shipped that this plan builds on

- `MeetingLibrary(path=None)` (`speakeasy/meeting_library.py`). Every call opens its own connection through `meeting_store.connect` (WAL, busy timeout 5000 ms, `migrate()` on every open).
  - `save_meeting(NewMeeting) -> str`. `NewMeeting` already has `title` and `calendar_event_id`. `_insert` makes the title `Meeting — <start>` when `title` is empty.
  - `link_people(meeting_id, [(name, email, role)])` and `_person_id(conn, name, email)`, which normalises emails (strip + lower).
  - `calendar_event(event_key) -> CalendarEvent | None` and `calendar_events_between(from_date, to_date)`, which takes local `YYYY-MM-DD` days via `local_day_bounds`. `CalendarEvent` has `event_key, calendar_name, title, start_utc, end_utc, all_day, declined, meeting_ids`.
  - `_touch(conn, meeting_id)` bumps `updated_at`. `_tags` orders by name NOCASE; `_people` orders by `role DESC, display_name NOCASE`.
  - `LibraryWatcher` notices commits from any other connection (`PRAGMA data_version`), so the Meetings window re-lists after a calendar sync without extra wiring.
- Schema v1 (`speakeasy/meeting_store.py`, `SCHEMA_VERSION = 1`, `_MIGRATIONS = [(1, _SCHEMA_V1)]`) already has `calendar_events`, `calendar_event_people`, `people`, `meeting_people` and `meetings.calendar_event_id`. `rebuild_derived` clears `calendar_events`.
- MCP `get_calendar` (`speakeasy/mcp_tools.py`, `_event(e)`) reads the cache and returns `[]` today.
- `MeetingsBridge` (`speakeasy/ui/meetings_bridge.py`) is pure Python. It has `register(dispatcher)` and `_wrap`, which maps `MeetingNotFound` to `not_found`, and `BridgeError`, `ValueError` and `IndexError` to their message. `filters_payload` returns `features: {"calendar": False, "claude": True, "settings": False}`. `get_payload` returns `"event": None`.
- `MeetingsWindowController.alloc().init()` is built in two places (`ui/main_window.py:187`, `ui/menubar.py:460`), sharing one owner.
- Engine (`speakeasy/engine.py`):
  - `MeetingOptions(expected_speaker_count, expected_voice_profile_names, system_audio_pid)`. `expected_speaker_count` is the Dock's "remote speakers" field; blank = Auto.
  - `_begin_meeting` runs on `control` and sets `_meeting_started_at` (aware local).
  - `_process_meeting` runs on `worker`. It diarizes the system track only in dual-track mode, and the mic track in mic-only fallback. It calls `self._diarize_track(audio, progress, timing)`, which builds `Diarizer(expected_count)` with `num_clusters=expected_count or -1` and then runs `VoiceProfileStore().identify`.
  - `on_state_changed(state)` is the UI refresh hook; menubar hops it to the main thread.
- `meetings.align_speakers(sentences, turns)` merges consecutive same-speaker sentences with **no length cap**. `known_speaker_segments` already caps the mic track at 60 s (`config.KNOWN_SPEAKER_MAX_SEGMENT_SECONDS`).
- `VoiceProfileStore._embedding(samples) -> list[float]` computes a speaker embedding with the bundled `embedding.onnx`. It raises `ValueError` when the audio is too short.
- Frontend (built against mocks in Phase 1, approved screens):
  - `TodayView.tsx` takes `connection`, `agenda`, `upcoming`, `onRecord`, `onConnectCalendar` and `onOpenPrivacySettings`, with a fixed mock "now".
  - `SettingsSheet.tsx` uses mock accounts.
  - `MeetingDetail.tsx` renders `detail.event` as a chip with no menu.
  - The Dock's event label and menu show only in mock `?state=recording-linked` (`showEventUI`).

## Measured on the real library (29 Sep 2026, read-only copy)

These numbers set the speaker constants in Task 3 and the title fix in Task 9. Recompute them in Task 10 against a fresh read-only copy.

- **Over-splitting is far worse than the eval's "9 and 14".** Speaker labels per 1-on-1: 1on1 Jim (58 min) **74**, 1on1 Victor 69, 1on1 Bing 57/50/39/35/34, 1on1 Refayet 14 and 6, Todd TEST 2. A group call (Canada expansion, 86 min) had **144**.
- **The real voices dominate.** In 1-on-1s, the two largest labels ("You" plus one remote voice) hold 73–96% of talk time. In 1on1 Jim, 57 of 74 labels hold under 15 s each and 45 hold under 5 s.
- **So for a 1-on-1, a calendar cap of one remote voice is the main fix.** A 15 s minimum-talk merge covers meetings with no event: it would take 1on1 Jim from 74 to 17 labels without a calendar.
- **Imported titles:** all 92 meetings are `imported_json`. **74** have titles that end with the recording's *end* time (`… — Sep 2, 2:07 PM`), not its start.

## Global Constraints

- Speakeasy makes no network calls at runtime. Phase 3 adds exactly one runtime dependency: `pyobjc-framework-EventKit==12.2.1`, in `requirements.txt` and `requirements.lock.txt`. Installing it into `.venv` needs the network once, at dev time.
- Tests never touch the real mic, model, sherpa-onnx, **an `EKEventStore`**, the network or **real user data**. Importing `EventKit` constants is allowed; creating a store is not. Use the `library_path`/`meetings_dir`/`spool_dir` fixtures, and `monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)` for `settings.json`. Never run app code against `~/Library/Application Support/Speakeasy` from a subagent; any real-data measurement uses `sqlite3 <real> ".backup <scratch copy>"` and reads the copy.
- Test command: `.venv/bin/python -m pytest -q`. Always give the Bash call `timeout: 300000`. The baseline is the count on `master` at branch time (593 at the end of Phase 2); record it in Execution notes.
- Frontend check: `npm --prefix frontend run build` (tsc + vite).
- Never share a `sqlite3` connection across threads or processes. The calendar thread uses its own `MeetingLibrary`.
- **Only Speakeasy talks to EventKit**, and only on the `calendar` thread (`EventKitStore` is built and used there). The MCP server reads the cached table only.
- **Calendar event notes, location, URLs and structured locations are never read or stored.** `SyncedEvent` has no field for them.
- **Calendar access is requested only when the user clicks Connect Calendar.** Launch, wake and timers sync only when access is already granted. They never show a prompt.
- Recording start never waits on the library: the calendar match runs *after* `_set_state(State.MEETING_RECORDING)`, and any `sqlite3.Error` means "unlinked".
- The Dock's manual remote-speaker count, when set, is exact and wins over anything derived from the calendar.
- UI uses the existing tokens in `frontend/src/styles/tokens.css`, the dark glass look and SF system fonts. **No new frontend dependencies.** UI objects and webview calls stay on the main thread.
- Do not modify dictation, focus, AX or insertion code (`injector.py`, `hotkey.py`, dictation paths in `engine.py`). Dictation into TextEdit, Codex and Teams must still work (CLAUDE.md).
- Work on branch `meeting-library-phase3` in worktree `.claude/worktrees/meeting-library-phase3` (`superpowers:using-git-worktrees`), never on `master`. The worktree needs the `.venv` and `models` symlinks, as in Phases 1–2 (see memory "Worktree session guards").
- Match surrounding comment style: comments explain *why* a constraint exists.

## Review Focus

1. **Invite lists that don't say who will talk.** A distribution-list (group) invite, room or resource attendees, people who declined, or a calendar where the user can't be identified. The speaker cap must never go *below* the real number of voices. A group means "unknown" (no cap); rooms, resources and declined people don't count; an unidentified self over-counts by one, which is the safe direction. Pinned in Task 4 (`event_from_ek`) and Task 2 (`remote_speaker_cap`).
2. **Calendar access not granted, denied, or revoked later.** No prompt at launch, wake or on timers. Revoking access clears the cache, so MCP `get_calendar` stops returning events. The Today view shows the right card. Recording and saving work unchanged. Pinned in Tasks 4 and 6.
3. **Times around the edges.** A meeting joined a minute early, back-to-back meetings, all-day events, recurring occurrences (same external id, different starts), and local vs UTC days in Today. Each occurrence has its own key; all-day events never match; Today groups by the *local* day. Pinned in Tasks 1, 2 and 6.
4. **The event changes between Record and Save.** The event is deleted or renamed in Calendar, the library is busy at start, or the user unlinks or relinks during recording. The recording always starts and saves. The linked event is re-read at save time, falling back to the copy held at start. Pinned in Task 5.
5. **Very short or odd diarization output.** A single cluster, all clusters tiny, an embedding failure for a short cluster, tuple-shaped turns from test fakes, or a manual count set. `tidy_speakers` never raises, never merges when every cluster is small, and never runs when the user gave a count. Pinned in Task 3.

---

## File map

| File | Status | Responsibility |
|---|---|---|
| `speakeasy/meeting_store.py` | modify | schema v2: `calendar_events.other_attendees` |
| `speakeasy/meeting_library.py` | modify | `EventPerson`, `SyncedEvent`, `CalendarEvent.people/other_attendees`, `NewMeeting.people`, `replace_calendar_window`, `clear_calendar_cache`, `calendar_events_overlapping`, `link_event`; Task 8 query fixes; Task 9 title fix |
| `speakeasy/calendar_match.py` | create | pure: `pick_event`, `prompt_candidates`, `remote_speaker_cap` |
| `speakeasy/speaker_merge.py` | create | fold tiny clusters, merge to a cap, recompute overlap |
| `speakeasy/meetings.py`, `speakeasy/config.py` | modify | 60 s cap on diarised segments; speaker-merge constants |
| `speakeasy/voice_profiles.py` | modify | public `embed(samples)` |
| `speakeasy/settings.py` | modify | `get_meeting_settings` / `set_meeting_settings` |
| `speakeasy/calendar_sync.py` | create | `event_from_ek`, `EventKitStore`, `CalendarSync` (calendar executor) |
| `speakeasy/engine.py` | modify | `MeetingOptions.calendar_event_key`, `meeting_event`, `link_meeting_event`, save title/people/link, speaker cap |
| `speakeasy/ui/calendar_payloads.py` | create | pure payload shaping for Today, Upcoming, chips, Dock, Settings |
| `speakeasy/ui/services.py` | create | process-wide handles (`engine`, `calendar_sync`) for window controllers |
| `speakeasy/ui/meetings_bridge.py` | modify | `calendar.*`, `meetings.linkEvent`, `meetings.eventsForDay`, `settings.meetings.*`, features, detail `event` |
| `speakeasy/ui/meetings_window.py` | modify | pass services into the bridge; emit `calendar.changed` |
| `speakeasy/ui/main_window.py` | modify | Dock `linkedEvent` state, `app.meetingEvents`, `app.linkMeetingEvent` |
| `speakeasy/ui/menubar.py` | modify | build `CalendarSync`; sync on launch/wake/5 min; event title in menu |
| `speakeasy/mcp_tools.py` | modify | `_event` adds `people`; Task 8 `get_transcript`/`get_meeting` |
| `speakeasy/__main__.py` | modify | `--fix-imported-titles [--apply]` |
| `packaging/Speakeasy.spec`, `requirements.txt`, `requirements.lock.txt` | modify | EventKit dep, hidden import, `NSCalendarsFullAccessUsageDescription` |
| `frontend/src/mock/meetings.ts`, `frontend/src/meetings/{App,TodayView,MeetingDetail,SettingsSheet,Sidebar}.tsx`, `frontend/src/dock/App.tsx` | modify | wire calendar screens to the bridge |
| `tests/test_meeting_calendar.py`, `test_calendar_match.py`, `test_speaker_merge.py`, `test_calendar_sync.py`, `test_calendar_payloads.py`, `test_title_fix.py` | create | tests |
| `tests/test_meeting_store.py`, `test_meetings.py`, `test_engine_meeting.py`, `test_meetings_bridge.py`, `test_mcp_tools.py`, `test_meeting_search.py`, `test_meeting_library.py`, `test_settings.py` | modify | tests |
| `AGENTS.md`, `README.md` | modify | calendar executor, privacy, commands, duplicate MCP connection note |

---

### Task 1: Schema v2, calendar cache writes and event people

**Files:**
- Modify: `speakeasy/meeting_store.py` (after `_SCHEMA_V1`, `SCHEMA_VERSION`, `_MIGRATIONS`)
- Modify: `speakeasy/meeting_library.py` (dataclasses near line 108/208; `_insert`; calendar section near line 701)
- Modify: `speakeasy/mcp_tools.py:156` (`_event`)
- Create: `tests/test_meeting_calendar.py`
- Modify: `tests/test_meeting_store.py` (any assertion that `user_version == 1` becomes `meeting_store.SCHEMA_VERSION`), `tests/test_mcp_tools.py`

**Interfaces:**
- Produces:
  - `EventPerson(name: str, email: str | None, role: str)`, frozen; role is `"attendee"` or `"organizer"`.
  - `SyncedEvent(key, calendar_name, title, start: datetime, end: datetime, all_day: bool, declined: bool, other_attendees: int | None, people: tuple[EventPerson, ...] = ())`, frozen, with aware datetimes.
  - `CalendarEvent` gains `other_attendees: int | None = None` and `people: list[EventPerson] = field(default_factory=list)`.
  - `NewMeeting.people: list[EventPerson] = field(default_factory=list)`.
  - `MeetingLibrary.replace_calendar_window(events: list[SyncedEvent], window_start: datetime, window_end: datetime) -> None`
  - `MeetingLibrary.clear_calendar_cache() -> None`
  - `MeetingLibrary.calendar_events_overlapping(start: datetime, end: datetime) -> list[CalendarEvent]`
  - `MeetingLibrary.link_event(meeting_id: str, event_key: str | None) -> None`
  - MCP `_event(e)` adds `"people": [names]`.

- [ ] **Step 1: Write the failing tests** in `tests/test_meeting_calendar.py`:

```python
"""Calendar cache writes, event people and linking (schema v2)."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from speakeasy import meeting_store
from speakeasy.meeting_library import (
    EventPerson, MeetingLibrary, MeetingNotFound, NewMeeting, SyncedEvent,
)
from speakeasy.meetings import MeetingSegment

UTC = timezone.utc
REFAYET = EventPerson("Refayet K", "refayet@example.com", "organizer")
NARESH = EventPerson("Naresh P", "naresh@example.com", "attendee")


def ev(key="ev1", start=datetime(2026, 9, 29, 17, 0, tzinfo=UTC), minutes=30,
       title="Weekly 1:1 — Refayet", people=(REFAYET,), other=1, **kw):
    return SyncedEvent(key, kw.get("calendar", "Work"), title, start,
                       start + timedelta(minutes=minutes), kw.get("all_day", False),
                       kw.get("declined", False), other, tuple(people))


WINDOW = (datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 10, 13, tzinfo=UTC))


def _meeting(lib, start=datetime(2026, 9, 29, 17, 2, tzinfo=UTC), **kw):
    return lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0.0, 5.0, "hello")],
        duration_seconds=60.0, started_at=start, **kw))


def test_schema_v2_adds_other_attendees(library_path):
    conn = meeting_store.connect(library_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        cols = [r[1] for r in conn.execute("PRAGMA table_info(calendar_events)")]
        assert "other_attendees" in cols
    finally:
        conn.close()


def test_v1_library_upgrades_in_place(library_path):
    raw = sqlite3.connect(library_path)
    raw.executescript(meeting_store._SCHEMA_V1)
    raw.execute(
        "INSERT INTO meetings (id, title, started_at, tz_offset_minutes,"
        " duration_seconds, capture_mode, system_audio_status, capture_scope,"
        " track_offsets_json, capture_health_json, source,"
        " timestamps_approximate, created_at, updated_at) VALUES"
        " ('20260901-100000-abcd', 'Old', '2026-09-01T10:00:00Z', 0, 60,"
        " 'mic_only', 'unavailable', 'mic_only', '{}', '{}', 'recorded', 0,"
        " '2026-09-01T10:01:00Z', '2026-09-01T10:01:00Z')")
    raw.commit()
    raw.close()
    assert MeetingLibrary(library_path).get_meeting("20260901-100000-abcd").title == "Old"
    conn = meeting_store.connect(library_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        conn.close()


def test_replace_window_inserts_updates_and_deletes(library_path):
    lib = MeetingLibrary(library_path)
    lib.replace_calendar_window([ev("a"), ev("b", title="Stand-up")], *WINDOW)
    lib.replace_calendar_window([ev("a", title="Renamed")], *WINDOW)
    events = lib.calendar_events_between("2026-09-29", "2026-09-29")
    assert [(e.event_key, e.title) for e in events] == [("a", "Renamed")]
    assert events[0].people == [REFAYET]
    assert events[0].other_attendees == 1


def test_replace_window_keeps_events_outside_it(library_path):
    lib = MeetingLibrary(library_path)
    old = ev("old", start=datetime(2026, 5, 1, 9, 0, tzinfo=UTC))
    lib.replace_calendar_window([old], datetime(2026, 4, 1, tzinfo=UTC), WINDOW[0])
    lib.replace_calendar_window([], *WINDOW)
    assert lib.calendar_event("old") is not None


def test_recurring_occurrences_are_distinct(library_path):
    lib = MeetingLibrary(library_path)
    monday = datetime(2026, 9, 28, 17, 0, tzinfo=UTC)
    lib.replace_calendar_window([
        ev("X@2026-09-28T17:00:00Z", start=monday),
        ev("X@2026-10-05T17:00:00Z", start=monday + timedelta(days=7)),
    ], *WINDOW)
    assert len(lib.calendar_events_between("2026-09-28", "2026-10-05")) == 2


def test_naive_event_time_is_rejected(library_path):
    bad = ev(start=datetime(2026, 9, 29, 17, 0))
    with pytest.raises(ValueError):
        MeetingLibrary(library_path).replace_calendar_window([bad], *WINDOW)


def test_people_only_on_dropped_events_are_removed(library_path):
    lib = MeetingLibrary(library_path)
    lib.replace_calendar_window([ev("a", people=(REFAYET, NARESH), other=2)], *WINDOW)
    lib.replace_calendar_window([], *WINDOW)
    conn = meeting_store.connect(library_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 0
    finally:
        conn.close()


def test_clear_cache(library_path):
    lib = MeetingLibrary(library_path)
    lib.replace_calendar_window([ev("a")], *WINDOW)
    lib.clear_calendar_cache()
    assert lib.calendar_event("a") is None


def test_overlapping_uses_utc_instants(library_path):
    lib = MeetingLibrary(library_path)
    lib.replace_calendar_window([ev("a")], *WINDOW)   # 17:00-17:30Z
    at = datetime(2026, 9, 29, 17, 29, tzinfo=UTC)
    assert [e.event_key for e in lib.calendar_events_overlapping(
        at, at + timedelta(minutes=2))] == ["a"]
    assert [e.event_key for e in lib.calendar_events_overlapping(
        at - timedelta(minutes=1), at)] == ["a"]
    later = datetime(2026, 9, 29, 17, 30, tzinfo=UTC)
    assert lib.calendar_events_overlapping(later, later + timedelta(minutes=5)) == []


def test_save_meeting_links_event_people(library_path):
    lib = MeetingLibrary(library_path)
    lib.replace_calendar_window([ev("a", people=(REFAYET, NARESH), other=2)], *WINDOW)
    mid = _meeting(lib, title="Weekly 1:1 — Refayet", calendar_event_id="a",
                   people=[REFAYET, NARESH])
    stored = lib.get_meeting(mid)
    assert stored.calendar_event_id == "a"
    assert stored.people == ["Refayet K", "Naresh P"]   # organizer first
    assert lib.calendar_event("a").meeting_ids == [mid]


def test_link_event_sets_title_and_people(library_path):
    lib = MeetingLibrary(library_path)
    lib.replace_calendar_window([ev("a")], *WINDOW)
    mid = _meeting(lib)
    lib.link_event(mid, "a")
    stored = lib.get_meeting(mid)
    assert (stored.title, stored.calendar_event_id, stored.people) == (
        "Weekly 1:1 — Refayet", "a", ["Refayet K"])


def test_unlink_keeps_title_and_people(library_path):
    lib = MeetingLibrary(library_path)
    lib.replace_calendar_window([ev("a")], *WINDOW)
    mid = _meeting(lib)
    lib.link_event(mid, "a")
    lib.link_event(mid, None)
    stored = lib.get_meeting(mid)
    assert (stored.title, stored.calendar_event_id, stored.people) == (
        "Weekly 1:1 — Refayet", None, ["Refayet K"])


def test_link_event_errors(library_path):
    lib = MeetingLibrary(library_path)
    mid = _meeting(lib)
    with pytest.raises(ValueError, match="no longer in the calendar"):
        lib.link_event(mid, "missing")
    with pytest.raises(MeetingNotFound):
        lib.link_event("20260101-000000-ffff", None)


def test_linked_meeting_survives_event_deletion(library_path):
    lib = MeetingLibrary(library_path)
    lib.replace_calendar_window([ev("a")], *WINDOW)
    mid = _meeting(lib, title="Weekly 1:1 — Refayet", calendar_event_id="a",
                   people=[REFAYET])
    lib.replace_calendar_window([], *WINDOW)
    stored = lib.get_meeting(mid)
    assert (stored.title, stored.people) == ("Weekly 1:1 — Refayet", ["Refayet K"])
```

In `tests/test_mcp_tools.py`, add:

```python
def test_get_calendar_lists_people(library_path):
    from datetime import datetime, timedelta, timezone
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_meeting_calendar.py tests/test_mcp_tools.py -q` (timeout 300000)
Expected: FAIL. `ImportError` on `EventPerson`/`SyncedEvent`.

- [ ] **Step 3: Implement schema v2** in `meeting_store.py`, after `_SCHEMA_V1`:

```python
# v2 (phase 3): how many *other* people were invited (the user, rooms,
# resources and people who declined excluded). NULL means unknown, e.g. a
# distribution-list invite, and then diarization must not be capped by it.
_SCHEMA_V2 = """
BEGIN;
ALTER TABLE calendar_events ADD COLUMN other_attendees INTEGER;
PRAGMA user_version = 2;
COMMIT;
"""
```

Set `SCHEMA_VERSION = 2` and `_MIGRATIONS = [(1, _SCHEMA_V1), (2, _SCHEMA_V2)]`. If `_SCHEMA_V1` does not start with `BEGIN;`, match whatever transaction framing it uses.

- [ ] **Step 4: Implement the library changes** in `meeting_library.py`:

```python
@dataclass(frozen=True)
class EventPerson:
    name: str
    email: str | None
    role: str  # "attendee" | "organizer"


@dataclass(frozen=True)
class SyncedEvent:
    """One Calendar.app occurrence as calendar_sync hands it over. It has no
    notes, location or URL fields on purpose: those often hold dial-in codes
    and passwords, and are never stored."""
    key: str
    calendar_name: str
    title: str
    start: datetime
    end: datetime
    all_day: bool
    declined: bool
    other_attendees: int | None
    people: tuple[EventPerson, ...] = ()
```

Add `people: list[EventPerson] = field(default_factory=list)` as the last field of `NewMeeting`. Add `other_attendees: int | None = None` and `people: list[EventPerson] = field(default_factory=list)` as the last fields of `CalendarEvent`.

In `_insert`, after the segments `executemany`:

```python
        for person in new.people:
            conn.execute(
                "INSERT OR IGNORE INTO meeting_people (meeting_id, person_id, role)"
                " VALUES (?, ?, ?)",
                (meeting_id, self._person_id(conn, person.name, person.email), person.role))
```

Replace `_calendar_event` and add the new methods in the calendar section:

```python
    def _calendar_event(self, conn, row) -> CalendarEvent:
        return CalendarEvent(
            event_key=row["event_key"], calendar_name=row["calendar_name"],
            title=row["title"], start_utc=row["start_utc"], end_utc=row["end_utc"],
            all_day=bool(row["all_day"]), declined=bool(row["declined"]),
            meeting_ids=[r[0] for r in conn.execute(
                "SELECT id FROM meetings WHERE calendar_event_id = ?"
                " ORDER BY started_at, id", (row["event_key"],))],
            other_attendees=row["other_attendees"],
            people=[EventPerson(r[0], r[1], r[2]) for r in conn.execute(
                "SELECT p.display_name, p.email, cep.role FROM calendar_event_people cep"
                " JOIN people p ON p.id = cep.person_id WHERE cep.event_key = ?"
                " ORDER BY cep.role DESC, p.display_name COLLATE NOCASE",
                (row["event_key"],))],
        )

    @staticmethod
    def _drop_orphan_people(conn) -> None:
        # People arrive with calendar events; once neither a meeting nor a
        # cached event refers to them they are just stale contact data.
        conn.execute(
            "DELETE FROM people WHERE id NOT IN (SELECT person_id FROM meeting_people)"
            " AND id NOT IN (SELECT person_id FROM calendar_event_people)")

    def replace_calendar_window(self, events, window_start: datetime,
                                window_end: datetime) -> None:
        """Make the cached events starting in [window_start, window_end)
        exactly `events`. One transaction, so a reader never sees a half-synced
        window; events outside it (older history) are left alone."""
        lo, hi = utc_iso(window_start), utc_iso(window_end)
        now = _now_iso()
        with self._transaction() as conn:
            conn.execute("DELETE FROM calendar_events WHERE start_utc >= ? AND start_utc < ?",
                         (lo, hi))
            for e in events:
                if e.start.tzinfo is None or e.end.tzinfo is None:
                    raise ValueError("Calendar event times must be timezone-aware.")
                conn.execute(
                    "INSERT OR REPLACE INTO calendar_events (event_key, calendar_name,"
                    " title, start_utc, end_utc, all_day, declined, other_attendees,"
                    " synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (e.key, e.calendar_name, e.title, utc_iso(e.start), utc_iso(e.end),
                     int(e.all_day), int(e.declined), e.other_attendees, now))
                # Explicit: REPLACE's implicit delete is not relied on to
                # cascade to the people rows.
                conn.execute("DELETE FROM calendar_event_people WHERE event_key = ?", (e.key,))
                for p in e.people:
                    conn.execute(
                        "INSERT OR IGNORE INTO calendar_event_people (event_key, person_id,"
                        " role) VALUES (?, ?, ?)",
                        (e.key, self._person_id(conn, p.name, p.email), p.role))
            self._drop_orphan_people(conn)

    def clear_calendar_cache(self) -> None:
        with self._transaction() as conn:
            conn.execute("DELETE FROM calendar_events")
            self._drop_orphan_people(conn)

    def calendar_events_overlapping(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        """Cached events with start < `end` and end > `start` (UTC instants)."""
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM calendar_events WHERE start_utc < ? AND end_utc > ?"
                " ORDER BY start_utc, event_key", (utc_iso(end), utc_iso(start))).fetchall()
            return [self._calendar_event(conn, r) for r in rows]

    def link_event(self, meeting_id: str, event_key: str | None) -> None:
        """Link a meeting to a cached event (taking its title and people), or
        unlink it. Unlinking keeps the title and people: the user may have
        renamed it since, and the spec keeps them when an event disappears."""
        _check_id(meeting_id)
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)).fetchone() is None:
                raise MeetingNotFound(meeting_id)
            if event_key is None:
                conn.execute("UPDATE meetings SET calendar_event_id = NULL WHERE id = ?",
                             (meeting_id,))
                self._touch(conn, meeting_id)
                return
            row = conn.execute("SELECT * FROM calendar_events WHERE event_key = ?",
                               (event_key,)).fetchone()
            if row is None:
                raise ValueError("That calendar event is no longer in the calendar.")
            event = self._calendar_event(conn, row)
            conn.execute("UPDATE meetings SET calendar_event_id = ?, title = ? WHERE id = ?",
                         (event_key, event.title, meeting_id))
            conn.execute("DELETE FROM meeting_people WHERE meeting_id = ?", (meeting_id,))
            for p in event.people:
                conn.execute(
                    "INSERT OR IGNORE INTO meeting_people (meeting_id, person_id, role)"
                    " VALUES (?, ?, ?)",
                    (meeting_id, self._person_id(conn, p.name, p.email), p.role))
            self._touch(conn, meeting_id)
```

Read `_touch` first. If it raises `MeetingNotFound` itself, the explicit existence check stays anyway, for clarity.

In `mcp_tools._event`, add `"people": [p.name for p in e.people]` to the returned dict.

- [ ] **Step 5: Run the tests and the full suite**

Run: `.venv/bin/python -m pytest -q` (timeout 300000)
Expected: all pass. Fix `tests/test_meeting_store.py` assertions on version 1 by using `meeting_store.SCHEMA_VERSION`.

- [ ] **Step 6: Commit**

```bash
git add speakeasy/meeting_store.py speakeasy/meeting_library.py speakeasy/mcp_tools.py tests/test_meeting_calendar.py tests/test_meeting_store.py tests/test_mcp_tools.py
git commit -m "Add schema v2 and calendar cache writes with event people"
```

---

### Task 2: `calendar_match` (pure matching, prompt eligibility, speaker cap)

**Files:**
- Create: `speakeasy/calendar_match.py`
- Create: `tests/test_calendar_match.py`

**Interfaces:**
- Consumes: `CalendarEvent` (Task 1: `start_utc`, `end_utc` as `%Y-%m-%dT%H:%M:%SZ`, `all_day`, `declined`, `event_key`, `other_attendees`).
- Produces:
  - `MATCH_EARLY_JOIN = timedelta(minutes=10)`, `PROMPT_BEFORE = timedelta(minutes=2)`, `PROMPT_AFTER = timedelta(minutes=5)`, `MAX_SPEAKERS = 20`
  - `pick_event(events, recording_start: datetime) -> CalendarEvent | None`
  - `prompt_candidates(events, now: datetime, prompted: set[str]) -> list[CalendarEvent]` (Phase 4 consumes it)
  - `remote_speaker_cap(event: CalendarEvent | None, capture_mode: str) -> int | None`
  - `event_start(e) -> datetime`, `event_end(e) -> datetime` (aware UTC)

- [ ] **Step 1: Write the failing tests** in `tests/test_calendar_match.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from speakeasy import calendar_match as cm
from speakeasy.meeting_library import CalendarEvent

UTC = timezone.utc
NINE = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)


def e(key, start, minutes=30, *, all_day=False, declined=False, other=None):
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return CalendarEvent(key, "Work", key, start.strftime(fmt),
                         (start + timedelta(minutes=minutes)).strftime(fmt),
                         all_day, declined, [], other_attendees=other)


def at(minutes, seconds=0):
    return NINE + timedelta(minutes=minutes, seconds=seconds)


def test_recording_inside_event_matches():
    assert cm.pick_event([e("a", NINE)], at(12)).event_key == "a"


def test_early_join_boundary_is_inclusive_at_ten_minutes():
    events = [e("a", NINE)]
    assert cm.pick_event(events, at(-10)).event_key == "a"
    assert cm.pick_event(events, at(-10, -1)) is None


def test_event_end_is_exclusive():
    assert cm.pick_event([e("a", NINE)], at(30)) is None


def test_back_to_back_prefers_closest_start():
    events = [e("first", NINE), e("second", at(30))]
    assert cm.pick_event(events, at(15)).event_key == "first"
    # Joining the next call a minute early: 1 min from its start beats 29.
    assert cm.pick_event(events, at(29)).event_key == "second"


def test_declined_and_all_day_never_match():
    events = [e("d", NINE, declined=True), e("ad", NINE, 24 * 60, all_day=True)]
    assert cm.pick_event(events, at(5)) is None


def test_tie_goes_to_shorter_event_then_key():
    assert cm.pick_event([e("long", NINE, 60), e("short", NINE, 30)], at(0)).event_key == "short"
    assert cm.pick_event([e("b", NINE), e("a", NINE)], at(0)).event_key == "a"


def test_naive_time_is_rejected():
    with pytest.raises(ValueError):
        cm.pick_event([], datetime(2026, 9, 29, 9, 0))


def test_prompt_window_and_prompted_set():
    events = [e("a", NINE), e("b", at(3))]
    assert [x.event_key for x in cm.prompt_candidates(events, at(-2), set())] == ["a"]
    assert [x.event_key for x in cm.prompt_candidates(events, at(5), set())] == ["a", "b"]
    assert [x.event_key for x in cm.prompt_candidates(events, at(5, 1), set())] == ["b"]
    assert cm.prompt_candidates(events, at(5), {"a", "b"}) == []
    assert cm.prompt_candidates([e("d", NINE, declined=True)], at(0), set()) == []


def test_remote_speaker_cap():
    one = e("a", NINE, other=1)
    assert cm.remote_speaker_cap(one, "mic_and_system") == 1   # 1-on-1: just them
    assert cm.remote_speaker_cap(one, "mic_only") == 2         # mic hears you too
    assert cm.remote_speaker_cap(e("a", NINE, other=None), "mic_and_system") is None
    assert cm.remote_speaker_cap(e("a", NINE, other=0), "mic_and_system") is None
    assert cm.remote_speaker_cap(e("a", NINE, other=30), "mic_only") == 20
    assert cm.remote_speaker_cap(None, "mic_only") is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_calendar_match.py -q` (timeout 300000)
Expected: FAIL with `ModuleNotFoundError: speakeasy.calendar_match`.

- [ ] **Step 3: Implement** `speakeasy/calendar_match.py`:

```python
"""Which calendar event a recording belongs to, which events to offer to
record, and how many voices an event implies. Pure: it works on the cached
CalendarEvent rows and never on EventKit, so the control thread can call it."""

from datetime import datetime, timedelta, timezone

from .meeting_library import CalendarEvent

# People join a few minutes early; a recording started up to this long before
# an event still belongs to it.
MATCH_EARLY_JOIN = timedelta(minutes=10)
# Record-prompt window (phase 4): from 2 min before the start to 5 min after.
PROMPT_BEFORE = timedelta(minutes=2)
PROMPT_AFTER = timedelta(minutes=5)
# MeetingOptions' own limit on the expected speaker count.
MAX_SPEAKERS = 20

_FMT = "%Y-%m-%dT%H:%M:%SZ"


def event_start(e: CalendarEvent) -> datetime:
    return datetime.strptime(e.start_utc, _FMT).replace(tzinfo=timezone.utc)


def event_end(e: CalendarEvent) -> datetime:
    return datetime.strptime(e.end_utc, _FMT).replace(tzinfo=timezone.utc)


def _eligible(e: CalendarEvent) -> bool:
    return not e.all_day and not e.declined


def pick_event(events, recording_start: datetime) -> CalendarEvent | None:
    """Candidates: not all-day, not declined, start - 10 min <= recording
    start < end. The closest start wins; ties go to the shorter event, then
    the key, so the choice never depends on row order."""
    if recording_start.tzinfo is None:
        raise ValueError("recording_start must be timezone-aware")
    best, best_rank = None, None
    for e in events:
        if not _eligible(e):
            continue
        start, end = event_start(e), event_end(e)
        if not start - MATCH_EARLY_JOIN <= recording_start < end:
            continue
        rank = (abs((start - recording_start).total_seconds()),
                (end - start).total_seconds(), e.event_key)
        if best_rank is None or rank < best_rank:
            best, best_rank = e, rank
    return best


def prompt_candidates(events, now: datetime, prompted: set[str]) -> list[CalendarEvent]:
    """Events to offer to record now, oldest start first. The caller checks
    the setting and that no meeting is in progress."""
    return sorted(
        (e for e in events
         if _eligible(e) and e.event_key not in prompted
         and event_start(e) - PROMPT_BEFORE <= now <= event_start(e) + PROMPT_AFTER),
        key=lambda e: (e.start_utc, e.event_key),
    )


def remote_speaker_cap(event: CalendarEvent | None, capture_mode: str) -> int | None:
    """Most voices diarization should report for a meeting linked to `event`.

    Dual-track mode diarizes only the system track, which carries the other
    people; mic-only fallback hears the user as well. It is an upper bound,
    not an exact count, because invitees often stay silent. None when the
    invite list doesn't say (no other people, or a distribution list)."""
    if event is None or not event.other_attendees or event.other_attendees < 1:
        return None
    voices = event.other_attendees + (0 if capture_mode == "mic_and_system" else 1)
    return min(voices, MAX_SPEAKERS)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_calendar_match.py -q` (timeout 300000)
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/calendar_match.py tests/test_calendar_match.py
git commit -m "Add pure calendar matching, prompt eligibility and speaker cap"
```

---

### Task 3: Stop speaker over-splitting (cluster merge + 60 s diarised segments)

**Files:**
- Create: `speakeasy/speaker_merge.py`
- Modify: `speakeasy/config.py` (after `DIARIZATION_SPLIT_MIN_SECONDS`), `speakeasy/meetings.py` (`align_speakers` merge condition), `speakeasy/voice_profiles.py` (public `embed`), `speakeasy/engine.py` (`_diarize_track`)
- Create: `tests/test_speaker_merge.py`
- Modify: `tests/test_meetings.py`, `tests/test_engine_meeting.py`

**Interfaces:**
- Consumes: `meetings.DiarizationTurn(start, end, speaker, confidence=None, overlap=False, profile_id=None)`.
- Produces:
  - `speaker_merge.merge_speakers(turns, embeddings: dict[int, np.ndarray], *, max_speakers: int | None, min_talk_seconds: float) -> list[DiarizationTurn]`
  - `speaker_merge.tidy_speakers(samples, turns, embed, *, max_speakers=None, min_talk_seconds=config.SPEAKER_MIN_TALK_SECONDS, cancelled=lambda: False) -> list[DiarizationTurn]`
  - `VoiceProfileStore.embed(samples) -> np.ndarray`
  - `DictationEngine._diarize_track(audio, progress, timing=None, *, max_speakers: int | None = None)`
  - `DictationEngine.speaker_embedder`: a callable or `None` (tests inject one).
  - `config.SPEAKER_MIN_TALK_SECONDS = 15.0`, `config.SPEAKER_MERGE_EMBED_SECONDS = 10.0`, `config.DIARIZED_MAX_SEGMENT_SECONDS = 60.0`

- [ ] **Step 1: Write the failing tests** in `tests/test_speaker_merge.py`:

```python
import numpy as np

from speakeasy.meetings import DiarizationTurn as T
from speakeasy.speaker_merge import merge_speakers, tidy_speakers


def v(*xs):
    return np.array(xs, dtype=np.float32)


def speakers(turns):
    return [t.speaker for t in turns]


def test_small_cluster_joins_most_similar_voice():
    turns = [T(0, 40, 0), T(40, 100, 1), T(100, 140, 0), T(140, 143, 2)]
    out = merge_speakers(turns, {0: v(1, 0), 1: v(0, 1), 2: v(0.1, 1)},
                         max_speakers=None, min_talk_seconds=15)
    assert speakers(out) == [0, 1, 0, 1]


def test_all_clusters_small_are_left_alone():
    turns = [T(0, 2, 0), T(2, 4, 1), T(4, 6, 2)]
    out = merge_speakers(turns, {}, max_speakers=None, min_talk_seconds=15)
    assert speakers(out) == [0, 1, 2]


def test_small_cluster_without_embedding_joins_nearest_in_time():
    turns = [T(0, 40, 0), T(50, 100, 1), T(100.5, 102, 2)]
    out = merge_speakers(turns, {0: v(1, 0), 1: v(0, 1)},
                         max_speakers=None, min_talk_seconds=15)
    assert speakers(out) == [0, 1, 1]


def test_cap_merges_most_similar_pairs_into_the_larger_voice():
    turns = [T(0, 40, 0), T(40, 70, 1), T(70, 90, 2), T(90, 125, 3)]
    emb = {0: v(1, 0, 0), 1: v(0.9, 0.1, 0), 2: v(0, 0, 1), 3: v(0, 0.1, 0.9)}
    out = merge_speakers(turns, emb, max_speakers=2, min_talk_seconds=15)
    assert speakers(out) == [0, 0, 3, 3]


def test_cap_of_one_needs_no_embeddings():
    turns = [T(0, 20, 5), T(20, 60, 7), T(60, 61, 9)]
    out = merge_speakers(turns, {}, max_speakers=1, min_talk_seconds=15)
    assert speakers(out) == [7, 7, 7]


def test_cap_above_cluster_count_changes_nothing():
    turns = [T(0, 20, 0), T(20, 40, 1)]
    assert speakers(merge_speakers(turns, {}, max_speakers=3, min_talk_seconds=15)) == [0, 1]


def test_overlap_is_recomputed_after_merge():
    turns = [T(0, 20, 0, overlap=True), T(19, 21, 1, overlap=True)]
    out = merge_speakers(turns, {0: v(1, 0), 1: v(1, 0.01)},
                         max_speakers=None, min_talk_seconds=15)
    assert [(t.speaker, t.overlap) for t in out] == [(0, False), (0, False)]


def test_tidy_accepts_tuples_and_skips_embedding_when_nothing_to_merge():
    calls = []
    out = tidy_speakers(np.zeros(16000 * 4, dtype=np.float32),
                        [(0.0, 0.8, 4), (1.0, 1.8, 9)], lambda s: calls.append(s))
    assert speakers(out) == [4, 9] and calls == []


def test_tidy_survives_embedding_failure():
    def embed(samples):
        raise ValueError("too short")
    samples = np.zeros(16000 * 50, dtype=np.float32)
    out = tidy_speakers(samples, [T(0, 20, 0), T(20, 40, 1), T(40, 41, 2)], embed)
    assert speakers(out) == [0, 1, 1]   # time fallback: 2 touches 1


def test_tidy_stops_embedding_when_cancelled():
    calls = []
    samples = np.zeros(16000 * 50, dtype=np.float32)
    out = tidy_speakers(samples, [T(0, 20, 0), T(20, 40, 1), T(40, 41, 2)],
                        lambda s: calls.append(1) or v(1, 0), cancelled=lambda: True)
    assert calls == [] and len(out) == 3
```

Add to `tests/test_meetings.py` (reuse the file's existing sentence/token helper if it has one; otherwise add this one):

```python
def _sentence(start, end, text):
    tok = type("Tok", (), {"start": start, "end": end, "duration": end - start, "text": " " + text})
    return type("S", (), {"start": start, "end": end, "text": text, "tokens": [tok]})()


def test_diarised_segments_split_at_sixty_seconds():
    sentences = [_sentence(0, 25, "a"), _sentence(26, 50, "b"), _sentence(51, 75, "c")]
    segments = meetings.align_speakers(sentences, [(0.0, 80.0, 0)])
    assert [(s.start, s.end) for s in segments] == [(0, 50), (51, 75)]
```

Add to `tests/test_engine_meeting.py`:

```python
class SplitRemoteDiarizer:
    """One real remote voice split into a big and a small cluster."""
    def diarize(self, samples, progress=lambda f: None):
        progress(1.0)
        return [(0.0, 0.8, 4), (1.0, 1.8, 9)]


def test_diarize_track_caps_speakers(spool_dir):
    engine = _engine(spool_dir)
    engine.diarizer = SplitRemoteDiarizer()
    engine._meeting_options = MeetingOptions()
    audio = np.zeros(config.SAMPLE_RATE * 2, dtype=np.float32)
    turns = engine._diarize_track(audio, lambda f: None, max_speakers=1)
    # Equal talk (0.8 s each): the tie goes to the lower cluster id.
    assert {t.speaker for t in turns} == {4}
    engine.shutdown()


def test_manual_speaker_count_skips_merging(spool_dir):
    engine = _engine(spool_dir)
    engine.diarizer = SplitRemoteDiarizer()
    engine._diarizer_speaker_count = 2
    engine._meeting_options = MeetingOptions(expected_speaker_count=2)
    audio = np.zeros(config.SAMPLE_RATE * 2, dtype=np.float32)
    turns = engine._diarize_track(audio, lambda f: None, max_speakers=1)
    # Manual count: turns come back exactly as the diarizer gave them.
    assert turns == [(0.0, 0.8, 4), (1.0, 1.8, 9)]
    engine.shutdown()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_speaker_merge.py tests/test_meetings.py tests/test_engine_meeting.py -q` (timeout 300000)
Expected: FAIL. `speaker_merge` is missing, the 60 s split gives one segment, and `_diarize_track` has no `max_speakers`.

- [ ] **Step 3: Add the constants** to `config.py` after `DIARIZATION_SPLIT_MIN_SECONDS`:

```python
# Diarization over-splits real meetings. In the imported library, 1-on-1s
# (two voices) came out with up to 74 speaker labels. In 1on1 Jim (58 min),
# 57 of 74 labels held under 15 s of talk each, while the two largest (You
# and one remote voice) held 74% of it. Clusters with less total talk than
# this are folded into the nearest real voice. The cost: a genuinely quiet
# participant (<15 s in the whole meeting) may be labelled as someone else.
SPEAKER_MIN_TALK_SECONDS = 15.0
# Audio per cluster fed to the embedding model when merging (longest turns
# first). Enough for a stable embedding without re-embedding whole meetings.
SPEAKER_MERGE_EMBED_SECONDS = 10.0
# Diarised (system/remote) segments split at this length, like the mic track,
# so search can cite a moment; the TEST meeting had one 71.9 s segment.
DIARIZED_MAX_SEGMENT_SECONDS = 60.0
```

- [ ] **Step 4: Cap diarised segments** in `meetings.align_speakers`. In the merge condition near the end (`if segments and segments[-1].speaker == labels[speaker] and segments[-1].overlap == overlap:`), add a third clause:

```python
            and end - segments[-1].start <= config.DIARIZED_MAX_SEGMENT_SECONDS
```

- [ ] **Step 5: Implement** `speakeasy/speaker_merge.py`:

```python
"""Merge diarization clusters that are really one voice.

sherpa-onnx's threshold clustering over-splits real calls into dozens of
labels (see config.SPEAKER_MIN_TALK_SECONDS). Two passes, both on cluster
embeddings from the same bundled model voice profiles use:
1. clusters with little talk fold into the most similar real voice (or, with
   no usable embedding, the voice nearest in time);
2. with a cap (from the calendar), the most similar voices merge until the
   cap is met; a cap of 1 needs no embeddings at all.
The user's own speaker count bypasses all of this (engine._diarize_track)."""

from collections.abc import Callable
from dataclasses import replace

import numpy as np

from . import config
from .meetings import DiarizationTurn


def _as_turn(t) -> DiarizationTurn:
    if isinstance(t, DiarizationTurn):
        return t
    return DiarizationTurn(float(t[0]), float(t[1]), int(t[2]))


def _talk(turns) -> dict[int, float]:
    talk: dict[int, float] = {}
    for t in turns:
        talk[t.speaker] = talk.get(t.speaker, 0.0) + max(0.0, t.end - t.start)
    return talk


def _unit(vector) -> np.ndarray | None:
    vector = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm and np.all(np.isfinite(vector)) else None


def _gap(turns, a: int, b: int) -> float:
    """Shortest silence between any turn of `a` and any turn of `b`."""
    best = float("inf")
    for x in turns:
        if x.speaker != a:
            continue
        for y in turns:
            if y.speaker == b:
                best = min(best, max(0.0, y.start - x.end, x.start - y.end))
    return best


class _Groups:
    def __init__(self, turns, embeddings):
        self.turns = turns
        self.talk = _talk(turns)
        self.owner = {s: s for s in self.talk}
        self.vec = {s: u for s, e in embeddings.items() if s in self.talk
                    and (u := _unit(e)) is not None}

    def roots(self) -> list[int]:
        return sorted({self.owner[s] for s in self.talk})

    def root_talk(self, root: int) -> float:
        return sum(t for s, t in self.talk.items() if self.owner[s] == root)

    def similarity(self, a: int, b: int) -> float | None:
        if a in self.vec and b in self.vec:
            return float(np.dot(self.vec[a], self.vec[b]))
        return None

    def nearest_in_time(self, source: int, candidates) -> int:
        members = [s for s in self.talk if self.owner[s] == source]
        return min(candidates, key=lambda c: (
            min(_gap(self.turns, m, o) for m in members
                for o in self.talk if self.owner[o] == c),
            -self.root_talk(c), c))

    def merge(self, small: int, big: int) -> None:
        # The voice with more talk survives, so its label and id stay.
        if (self.root_talk(small), -small) > (self.root_talk(big), -big):
            small, big = big, small
        if small in self.vec and big in self.vec:
            w_s, w_b = self.root_talk(small), self.root_talk(big)
            merged = _unit(self.vec[small] * w_s + self.vec[big] * w_b)
            if merged is not None:
                self.vec[big] = merged
        self.vec.pop(small, None)
        for s, root in self.owner.items():
            if root == small:
                self.owner[s] = big


def merge_speakers(turns, embeddings, *, max_speakers, min_talk_seconds) -> list[DiarizationTurn]:
    turns = [_as_turn(t) for t in turns]
    if len({t.speaker for t in turns}) < 2:
        return turns
    g = _Groups(turns, embeddings)
    anchors = [s for s in g.roots() if g.talk[s] >= min_talk_seconds]
    if anchors:
        small = sorted((s for s in g.roots() if s not in anchors),
                       key=lambda s: (g.talk[s], s))
        for s in small:
            scored = [(sim, a) for a in anchors
                      if (sim := g.similarity(s, a)) is not None]
            target = (max(scored, key=lambda x: (x[0], -x[1]))[1] if scored
                      else g.nearest_in_time(s, anchors))
            g.merge(s, target)
    while max_speakers and len(g.roots()) > max_speakers:
        roots = g.roots()
        if max_speakers == 1:
            big = max(roots, key=lambda r: (g.root_talk(r), -r))
            for r in roots:
                if r != big:
                    g.merge(r, big)
            break
        pairs = [(sim, a, b) for i, a in enumerate(roots) for b in roots[i + 1:]
                 if (sim := g.similarity(a, b)) is not None]
        if pairs:
            _, a, b = max(pairs, key=lambda p: (p[0], -p[1], -p[2]))
        else:
            a = min(roots, key=lambda r: (g.root_talk(r), r))
            b = g.nearest_in_time(a, [r for r in roots if r != a])
        g.merge(a, b)
    merged = [replace(t, speaker=g.owner[t.speaker]) for t in turns]
    # Overlap marks concurrent *different* voices; merging can make two
    # overlapping turns the same voice, so recompute it.
    flagged = set()
    for i, first in enumerate(merged):
        for j in range(i + 1, len(merged)):
            second = merged[j]
            if second.start >= first.end:
                break
            if first.speaker != second.speaker and second.end > first.start:
                flagged.update((i, j))
    return [replace(t, overlap=i in flagged) for i, t in enumerate(merged)]


def _cluster_audio(samples, turns, speaker, max_seconds) -> np.ndarray:
    rate = config.SAMPLE_RATE
    chunks, used = [], 0.0
    for t in sorted((t for t in turns if t.speaker == speaker),
                    key=lambda t: (t.start - t.end, t.start)):
        take = min(t.end - t.start, max_seconds - used)
        if take <= 0:
            break
        chunk = samples[max(0, int(t.start * rate)):min(len(samples), int((t.start + take) * rate))]
        if len(chunk):
            chunks.append(chunk)
            used += take
    return np.concatenate(chunks) if chunks else np.empty(0, dtype=np.float32)


def tidy_speakers(samples, turns, embed: Callable, *, max_speakers=None,
                  min_talk_seconds=config.SPEAKER_MIN_TALK_SECONDS,
                  cancelled: Callable[[], bool] = lambda: False) -> list[DiarizationTurn]:
    """merge_speakers with embeddings computed only when a merge needs them.
    Never raises for a bad cluster: it just has no embedding."""
    turns = sorted((_as_turn(t) for t in turns), key=lambda t: (t.start, t.end))
    talk = _talk(turns)
    small_with_anchor = (any(t >= min_talk_seconds for t in talk.values())
                         and any(t < min_talk_seconds for t in talk.values()))
    over_cap = bool(max_speakers) and len(talk) > max_speakers
    if not small_with_anchor and not over_cap:
        return turns
    embeddings = {}
    if small_with_anchor or (over_cap and max_speakers > 1):
        for speaker in sorted(talk):
            if cancelled():
                return turns
            audio = _cluster_audio(samples, turns, speaker, config.SPEAKER_MERGE_EMBED_SECONDS)
            if not len(audio):
                continue
            try:
                embeddings[speaker] = embed(audio)
            except (ValueError, RuntimeError, OSError, TypeError):
                continue
    return merge_speakers(turns, embeddings, max_speakers=max_speakers,
                          min_talk_seconds=min_talk_seconds)
```

Note on the `test_tidy_survives_embedding_failure` expectation: clusters 0 and 1 are anchors (20 s each), and 2 (1 s) has no embedding. Its nearest in time is 1 (gap 0), so it becomes 1.

- [ ] **Step 6: Add** `VoiceProfileStore.embed` in `voice_profiles.py`, after `_embedding`:

```python
    def embed(self, samples: np.ndarray) -> np.ndarray:
        """Public embedding for speaker_merge (same model and checks)."""
        return np.asarray(self._embedding(samples), dtype=np.float32)
```

- [ ] **Step 7: Hook it into the engine.** In `DictationEngine.__init__`, next to `self.diarizer = None`, add:

```python
        # Speaker embedding for cluster merging; built lazily (loads ONNX).
        # Tests inject a fake.
        self.speaker_embedder = None
```

Change `_diarize_track` to take `*, max_speakers: int | None = None`. Right after the `diarize` try/finally (before `voice_identification`), add:

```python
        if expected_count is None:
            # The user's own count is exact and wins. Otherwise fold the
            # over-split clusters (and apply the calendar cap) before naming
            # voices, so profile matching sees whole voices.
            from . import speaker_merge

            turns = speaker_merge.tidy_speakers(
                audio, turns, self._speaker_embedding, max_speakers=max_speakers,
                cancelled=self._meeting_cancel.is_set)
```

And add the method:

```python
    def _speaker_embedding(self, samples):
        if self.speaker_embedder is None:
            from .voice_profiles import VoiceProfileStore

            self.speaker_embedder = VoiceProfileStore().embed
        return self.speaker_embedder(samples)
```

Leave the two `_diarize_track` call sites in `_process_meeting` unchanged for now; Task 5 passes the cap.

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest -q` (timeout 300000)
Expected: all pass. The existing dual-track tests use two 0.8 s clusters, all below 15 s, so they are unchanged.

- [ ] **Step 9: Commit**

```bash
git add speakeasy/speaker_merge.py speakeasy/config.py speakeasy/meetings.py speakeasy/voice_profiles.py speakeasy/engine.py tests/test_speaker_merge.py tests/test_meetings.py tests/test_engine_meeting.py
git commit -m "Merge over-split speaker clusters and cap diarised segments at 60 s"
```

---

### Task 4: Meetings settings and `calendar_sync` (EventKit on the calendar thread)

**Files:**
- Modify: `requirements.txt`, `requirements.lock.txt`, `packaging/Speakeasy.spec`
- Modify: `speakeasy/settings.py` (after `set_last_profile`)
- Create: `speakeasy/calendar_sync.py`
- Create: `tests/test_calendar_sync.py`
- Modify: `tests/test_settings.py`

**Interfaces:**
- Consumes: `SyncedEvent`, `EventPerson`, `MeetingLibrary.replace_calendar_window`, `clear_calendar_cache` (Task 1).
- Produces:
  - `settings.get_meeting_settings() -> {"offer_to_record": bool, "calendar_choices": dict[str, bool]}`
  - `settings.set_meeting_settings(*, offer_to_record=None, calendar_choices=None) -> dict`
  - `calendar_sync.CalendarInfo(id, title, account, kind)` with `.default_enabled`
  - `calendar_sync.event_from_ek(ek, calendar_title) -> SyncedEvent | None`
  - `calendar_sync.included_ids(calendars, choices) -> set[str]`
  - `calendar_sync.EventKitStore` (real)
  - `calendar_sync.CalendarSync(*, library=None, store_factory=EventKitStore, authorization=EventKitStore.authorization, now=..., read_settings=settings.get_meeting_settings, on_synced=lambda: None)`, with `.access() -> "connected" | "denied" | "unconnected"`, `.request_sync()`, `.request_access()`, `.sync_now() -> int`, `.calendars: list[CalendarInfo]` and `.shutdown()`.

- [ ] **Step 1: Add the dependency.** In `requirements.txt`, after the WebKit line:

```
# Reads the work calendar from Calendar.app on this Mac (titles, times,
# attendees; never notes, locations or links). No network: EventKit reads the
# local store that macOS keeps in sync.
pyobjc-framework-EventKit==12.2.1
```

Add `pyobjc-framework-EventKit==12.2.1` to `requirements.lock.txt` in alphabetical position (after `pyobjc-framework-CoreText`). Then run `.venv/bin/pip install pyobjc-framework-EventKit==12.2.1` (dev-time network) and `.venv/bin/python -c "import EventKit; print(EventKit.EKAuthorizationStatusFullAccess)"`. Expected output: `3`.

In `packaging/Speakeasy.spec`, add `"EventKit"` and `"speakeasy.calendar_sync"` to `hiddenimports` next to `"WebKit"`. Add to `info_plist` after `NSAudioCaptureUsageDescription`:

```python
        "NSCalendarsFullAccessUsageDescription": (
            "Speakeasy reads Calendar on this Mac to title meeting recordings, "
            "list attendees and offer to record meetings as they start. Event "
            "notes, locations and links are never read. Nothing leaves this Mac."
        ),
```

The build signs without `--options runtime`, so no calendar entitlement is needed. If hardened runtime is ever turned on, add `com.apple.security.personal-information.calendars`.

- [ ] **Step 2: Write the failing settings tests** in `tests/test_settings.py`:

```python
def test_meeting_settings_defaults_and_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    assert settings.get_meeting_settings() == {"offer_to_record": True, "calendar_choices": {}}
    settings.set_meeting_settings(offer_to_record=False, calendar_choices={"cal-1": False})
    settings.set_meeting_settings(calendar_choices={"cal-2": True})
    assert settings.get_meeting_settings() == {
        "offer_to_record": False, "calendar_choices": {"cal-1": False, "cal-2": True}}


def test_meeting_settings_reject_bad_values(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    import pytest
    with pytest.raises(ValueError):
        settings.set_meeting_settings(offer_to_record="yes")
    with pytest.raises(ValueError):
        settings.set_meeting_settings(calendar_choices={"cal": "on"})


def test_meeting_settings_ignore_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text('{"meetings": {"offer_to_record": 1,'
                                            ' "calendar_choices": {"a": 1, "b": false}}}')
    assert settings.get_meeting_settings() == {"offer_to_record": True,
                                               "calendar_choices": {"b": False}}
```

(If `app_support_dir` creates the directory or `_settings_path` is cached differently, patch whatever `_settings_path()` reads; check the existing tests in that file first.)

- [ ] **Step 3: Write the failing sync tests** in `tests/test_calendar_sync.py`:

```python
"""calendar_sync with a fake EventKit store; no EKEventStore is ever built."""
import threading
from datetime import datetime, timedelta, timezone

import pytest

from speakeasy import calendar_sync as cs
from speakeasy import meeting_store
from speakeasy.meeting_library import MeetingLibrary

UTC = timezone.utc
NOW = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)
SECRET = "SECRET-DIAL-IN 555-0100 pin 4242"


class Date:
    def __init__(self, dt): self._dt = dt
    def timeIntervalSince1970(self): return self._dt.timestamp()


class URL:
    def __init__(self, s): self._s = s
    def absoluteString(self): return self._s


class P:
    def __init__(self, name, email=None, *, me=False, status=2, kind=1):
        self._n, self._e, self._me, self._st, self._k = name, email, me, status, kind
    def name(self): return self._n
    def URL(self): return URL(f"mailto:{self._e}") if self._e else None
    def isCurrentUser(self): return self._me
    def participantStatus(self): return self._st
    def participantType(self): return self._k


class Ev:
    def __init__(self, title, start, minutes=30, *, attendees=(), organizer=None,
                 ext="EXT1", all_day=False, status=1):
        self._t, self._s, self._m = title, start, minutes
        self._a, self._o, self._x, self._ad, self._st = list(attendees), organizer, ext, all_day, status
    def title(self): return self._t
    def startDate(self): return Date(self._s)
    def endDate(self): return Date(self._s + timedelta(minutes=self._m))
    def isAllDay(self): return self._ad
    def status(self): return self._st
    def calendarItemExternalIdentifier(self): return self._x
    def eventIdentifier(self): return "LOCAL-" + self._x
    def organizer(self): return self._o
    def attendees(self): return self._a
    # Privacy: these must never be read.
    def notes(self): raise AssertionError(SECRET)
    def location(self): raise AssertionError(SECRET)
    def structuredLocation(self): raise AssertionError(SECRET)
    def URL(self): raise AssertionError(SECRET)


ME = P("Jason", "jason@example.com", me=True)
REF = P("Refayet K", "Refayet@Example.com")


def test_event_from_ek_counts_other_people_only():
    ev = Ev("1:1", NOW, attendees=[ME, REF, P("Room 4", kind=cs.PARTICIPANT_TYPE_ROOM),
                                   P("Declined D", "d@example.com", status=cs.STATUS_DECLINED)],
            organizer=REF)
    out = cs.event_from_ek(ev, "Work")
    assert out.other_attendees == 1
    assert [(p.name, p.role) for p in out.people] == [("Refayet K", "organizer")]
    assert out.declined is False
    assert out.key == "EXT1@2026-09-29T16:00:00Z"


def test_group_invite_means_unknown_count():
    ev = Ev("All hands", NOW, attendees=[ME, REF, P("Eng DL", kind=cs.PARTICIPANT_TYPE_GROUP)])
    assert cs.event_from_ek(ev, "Work").other_attendees is None


def test_no_invitees_means_unknown_count():
    assert cs.event_from_ek(Ev("Focus", NOW), "Work").other_attendees is None


def test_self_declined_and_cancelled():
    me_no = P("Jason", "jason@example.com", me=True, status=cs.STATUS_DECLINED)
    assert cs.event_from_ek(Ev("x", NOW, attendees=[me_no, REF]), "Work").declined is True
    assert cs.event_from_ek(Ev("x", NOW, status=cs.EVENT_STATUS_CANCELED), "Work") is None


def test_untitled_and_organizer_not_in_attendees():
    out = cs.event_from_ek(Ev("  ", NOW, attendees=[ME], organizer=REF), "Work")
    assert out.title == "Untitled event"
    assert [p.name for p in out.people] == ["Refayet K"] and out.other_attendees == 1


class FakeStore:
    def __init__(self, calendars, events_by_cal):
        self._cals, self._events = calendars, events_by_cal
        self.requested = []
        self.observer = None
    def calendars(self): return list(self._cals)
    def events(self, start, end, ids):
        return [e for cal_id in sorted(ids)
                for ek in self._events.get(cal_id, [])
                if (e := cs.event_from_ek(ek, cal_id))]
    def request_access(self, done): self.requested.append(done)
    def observe_changes(self, callback): self.observer = callback


WORK = cs.CalendarInfo("work", "Calendar", "Exchange", 2)
BDAY = cs.CalendarInfo("bday", "Birthdays", "Other", cs.CALENDAR_TYPE_BIRTHDAY)


def make_sync(library_path, store, status=cs.AUTH_FULL, choices=None):
    return cs.CalendarSync(
        library=MeetingLibrary(library_path), store_factory=lambda: store,
        authorization=lambda: status, now=lambda: NOW,
        read_settings=lambda: {"offer_to_record": True, "calendar_choices": choices or {}})


def test_sync_inserts_updates_and_deletes(library_path):
    store = FakeStore([WORK, BDAY], {"work": [Ev("1:1", NOW, attendees=[ME, REF])],
                                     "bday": [Ev("Mum", NOW, ext="B1", all_day=True)]})
    sync = make_sync(library_path, store)
    assert sync.sync_now() == 1   # birthdays off by default
    lib = MeetingLibrary(library_path)
    assert [e.title for e in lib.calendar_events_between("2026-09-29", "2026-09-29")] == ["1:1"]
    store._events["work"] = [Ev("1:1 moved", NOW + timedelta(hours=1), ext="EXT2")]
    sync.sync_now()
    assert [e.title for e in lib.calendar_events_between("2026-09-29", "2026-09-29")] == ["1:1 moved"]


def test_unticked_calendar_is_dropped(library_path):
    store = FakeStore([WORK], {"work": [Ev("1:1", NOW)]})
    make_sync(library_path, store).sync_now()
    make_sync(library_path, store, choices={"work": False}).sync_now()
    assert MeetingLibrary(library_path).calendar_events_between("2026-09-29", "2026-09-29") == []


def test_private_fields_never_reach_the_database(library_path):
    store = FakeStore([WORK], {"work": [Ev("1:1", NOW, attendees=[ME, REF])]})
    make_sync(library_path, store).sync_now()
    conn = meeting_store.connect(library_path)
    try:
        dump = "\n".join(conn.iterdump())
    finally:
        conn.close()
    assert "SECRET" not in dump and "1:1" in dump


@pytest.mark.parametrize("status", [cs.AUTH_DENIED, cs.AUTH_RESTRICTED, cs.AUTH_NOT_DETERMINED])
def test_without_access_the_cache_is_cleared_and_no_store_is_built(library_path, status):
    store = FakeStore([WORK], {"work": [Ev("1:1", NOW)]})
    make_sync(library_path, store).sync_now()
    built = []
    sync = cs.CalendarSync(
        library=MeetingLibrary(library_path),
        store_factory=lambda: built.append(1) or store, authorization=lambda: status,
        now=lambda: NOW, read_settings=lambda: {"calendar_choices": {}})
    assert sync.sync_now() == 0 and built == [] and sync.calendars == []
    assert MeetingLibrary(library_path).calendar_events_between("2026-09-29", "2026-09-29") == []


def test_access_states():
    for status, name in [(cs.AUTH_FULL, "connected"), (cs.AUTH_NOT_DETERMINED, "unconnected"),
                         (cs.AUTH_DENIED, "denied"), (cs.AUTH_RESTRICTED, "denied"),
                         (cs.AUTH_WRITE_ONLY, "denied")]:
        assert cs.CalendarSync(authorization=lambda s=status: s, library=object()).access() == name


def test_request_sync_coalesces(library_path):
    gate, calls = threading.Event(), []
    store = FakeStore([WORK], {})
    original = store.events
    def slow(start, end, ids):
        calls.append(1)
        gate.wait(5)
        return original(start, end, ids)
    store.events = slow
    done = threading.Event()
    sync = make_sync(library_path, store)
    sync.on_synced = done.set
    for _ in range(5):
        sync.request_sync()
    gate.set()
    sync.shutdown(wait=True)
    assert 1 <= len(calls) <= 2


def test_request_access_asks_once_then_syncs(library_path):
    status = {"v": cs.AUTH_NOT_DETERMINED}
    store = FakeStore([WORK], {"work": [Ev("1:1", NOW)]})
    sync = cs.CalendarSync(library=MeetingLibrary(library_path), store_factory=lambda: store,
                           authorization=lambda: status["v"], now=lambda: NOW,
                           read_settings=lambda: {"calendar_choices": {}})
    synced = threading.Event()
    sync.on_synced = synced.set
    sync.request_access()
    sync.shutdown(wait=True, restart=True)
    assert len(store.requested) == 1
    status["v"] = cs.AUTH_FULL
    store.requested[0](True)
    assert synced.wait(5)
    assert MeetingLibrary(library_path).calendar_events_between("2026-09-29", "2026-09-29")


def test_constants_match_eventkit():
    EventKit = pytest.importorskip("EventKit")   # constants only; no store
    assert cs.AUTH_FULL == EventKit.EKAuthorizationStatusFullAccess
    assert cs.AUTH_DENIED == EventKit.EKAuthorizationStatusDenied
    assert cs.AUTH_NOT_DETERMINED == EventKit.EKAuthorizationStatusNotDetermined
    assert cs.AUTH_RESTRICTED == EventKit.EKAuthorizationStatusRestricted
    assert cs.AUTH_WRITE_ONLY == EventKit.EKAuthorizationStatusWriteOnly
    assert cs.STATUS_DECLINED == EventKit.EKParticipantStatusDeclined
    assert cs.PARTICIPANT_TYPE_ROOM == EventKit.EKParticipantTypeRoom
    assert cs.PARTICIPANT_TYPE_RESOURCE == EventKit.EKParticipantTypeResource
    assert cs.PARTICIPANT_TYPE_GROUP == EventKit.EKParticipantTypeGroup
    assert cs.EVENT_STATUS_CANCELED == EventKit.EKEventStatusCanceled
    assert cs.CALENDAR_TYPE_SUBSCRIPTION == EventKit.EKCalendarTypeSubscription
    assert cs.CALENDAR_TYPE_BIRTHDAY == EventKit.EKCalendarTypeBirthday
```

- [ ] **Step 4: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_settings.py tests/test_calendar_sync.py -q` (timeout 300000)
Expected: FAIL (missing functions/module).

- [ ] **Step 5: Implement settings** in `settings.py`:

```python
# -- meetings (phase 3) ------------------------------------------------------


def get_meeting_settings() -> dict:
    """Offer-to-record toggle and per-calendar include choices. A calendar
    missing from calendar_choices uses its default (calendar_sync)."""
    raw = _read().get("meetings")
    raw = raw if isinstance(raw, dict) else {}
    offer = raw.get("offer_to_record")
    choices = raw.get("calendar_choices")
    return {
        "offer_to_record": offer if isinstance(offer, bool) else True,
        "calendar_choices": {
            k: v for k, v in choices.items() if isinstance(k, str) and isinstance(v, bool)
        } if isinstance(choices, dict) else {},
    }


def set_meeting_settings(*, offer_to_record=None, calendar_choices=None) -> dict:
    current = get_meeting_settings()
    if offer_to_record is not None:
        if not isinstance(offer_to_record, bool):
            raise ValueError("Offer to record must be on or off.")
        current["offer_to_record"] = offer_to_record
    if calendar_choices is not None:
        if (not isinstance(calendar_choices, dict) or len(calendar_choices) > 500
                or not all(isinstance(k, str) and 0 < len(k) <= 300 and isinstance(v, bool)
                           for k, v in calendar_choices.items())):
            raise ValueError("Calendar choices must be on or off for each calendar.")
        current["calendar_choices"] = {**current["calendar_choices"], **calendar_choices}
    data = _read()
    data["meetings"] = current
    _write(data)
    return current
```

- [ ] **Step 6: Implement** `speakeasy/calendar_sync.py`:

```python
"""Calendar.app → the library's calendar cache, through EventKit.

Only Speakeasy talks to EventKit, never the MCP server: a child process of
Claude would put the Calendars permission prompt on Claude. Every EventKit
call runs on one `calendar` thread that never touches audio or the model.
What is copied is deliberately narrow: titles, times, the user's own RSVP and
attendee names/emails. Event notes, location and URLs are never read; they
routinely hold dial-in codes and passwords."""

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

from . import settings
from .meeting_library import EventPerson, MeetingLibrary, SyncedEvent

SYNC_BACK = timedelta(days=90)
SYNC_AHEAD = timedelta(days=14)

# EventKit enum values (EKTypes.h), mirrored so event_from_ek stays testable
# with plain fakes; test_constants_match_eventkit checks them.
AUTH_NOT_DETERMINED, AUTH_RESTRICTED, AUTH_DENIED, AUTH_FULL, AUTH_WRITE_ONLY = 0, 1, 2, 3, 4
PARTICIPANT_TYPE_ROOM, PARTICIPANT_TYPE_RESOURCE, PARTICIPANT_TYPE_GROUP = 2, 3, 4
STATUS_DECLINED = 3
EVENT_STATUS_CANCELED = 3
CALENDAR_TYPE_SUBSCRIPTION, CALENDAR_TYPE_BIRTHDAY = 3, 4


@dataclass(frozen=True)
class CalendarInfo:
    id: str
    title: str
    account: str
    kind: int

    @property
    def default_enabled(self) -> bool:
        # Holiday (subscribed) and birthday calendars are not meetings.
        return self.kind not in (CALENDAR_TYPE_SUBSCRIPTION, CALENDAR_TYPE_BIRTHDAY)


def included_ids(calendars, choices: dict) -> set[str]:
    return {c.id for c in calendars if choices.get(c.id, c.default_enabled)}


def _utc(nsdate) -> datetime:
    return datetime.fromtimestamp(float(nsdate.timeIntervalSince1970()), tz=timezone.utc)


def _email(participant) -> str | None:
    url = participant.URL()
    text = str(url.absoluteString()) if url is not None else ""
    if not text.lower().startswith("mailto:"):
        return None
    return unquote(text[7:]).strip().lower() or None


def _identity(participant) -> str:
    return _email(participant) or (participant.name() or "").strip().lower()


def event_from_ek(ek, calendar_title: str) -> SyncedEvent | None:
    """A plain SyncedEvent, or None for a cancelled or unidentifiable event.
    Reads only the fields named here, never notes/location/URL."""
    if ek.status() == EVENT_STATUS_CANCELED:
        return None
    ident = ek.calendarItemExternalIdentifier() or ek.eventIdentifier()
    if not ident:
        return None
    start, end = _utc(ek.startDate()), _utc(ek.endDate())
    organizer = ek.organizer()
    participants = list(ek.attendees() or [])
    if organizer is not None and all(_identity(p) != _identity(organizer) for p in participants):
        participants.append(organizer)
    declined_self = unknown = False
    people: dict[str, EventPerson] = {}
    for p in participants:
        if p.isCurrentUser():
            declined_self = declined_self or p.participantStatus() == STATUS_DECLINED
            continue
        kind = p.participantType()
        if kind in (PARTICIPANT_TYPE_ROOM, PARTICIPANT_TYPE_RESOURCE):
            continue
        if kind == PARTICIPANT_TYPE_GROUP:
            unknown = True   # a mailing list: we can't tell how many will talk
            continue
        if p.participantStatus() == STATUS_DECLINED:
            continue
        email = _email(p)
        name = (p.name() or "").strip() or email
        if not name:
            continue
        role = ("organizer" if organizer is not None and _identity(p) == _identity(organizer)
                else "attendee")
        people[_identity(p)] = EventPerson(name, email, role)
    ordered = sorted(people.values(), key=lambda p: (p.role != "organizer", p.name.lower()))
    return SyncedEvent(
        key=f"{ident}@{start.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        calendar_name=calendar_title,
        title=(ek.title() or "").strip() or "Untitled event",
        start=start, end=end, all_day=bool(ek.isAllDay()), declined=declined_self,
        other_attendees=None if unknown or not ordered else len(ordered),
        people=tuple(ordered),
    )


class EventKitStore:
    """The only code that touches an EKEventStore. Build and use it on the
    calendar thread only."""

    def __init__(self) -> None:
        import EventKit

        self._ek = EventKit
        self._store = EventKit.EKEventStore.alloc().init()
        self._observer = None

    @staticmethod
    def authorization() -> int:
        import EventKit

        return int(EventKit.EKEventStore.authorizationStatusForEntityType_(
            EventKit.EKEntityTypeEvent))

    def request_access(self, done) -> None:
        # macOS 14+; the completion arrives on an arbitrary queue.
        self._store.requestFullAccessToEventsWithCompletion_(
            lambda granted, error: done(bool(granted)))

    def _ek_calendars(self):
        return list(self._store.calendarsForEntityType_(self._ek.EKEntityTypeEvent) or [])

    def calendars(self) -> list[CalendarInfo]:
        return [CalendarInfo(str(c.calendarIdentifier()), str(c.title()),
                             str(c.source().title()) if c.source() is not None else "Other",
                             int(c.type()))
                for c in self._ek_calendars()]

    def events(self, start: datetime, end: datetime, ids: set[str]) -> list[SyncedEvent]:
        from Foundation import NSDate

        cals = [c for c in self._ek_calendars() if str(c.calendarIdentifier()) in ids]
        if not cals:
            return []
        predicate = self._store.predicateForEventsWithStartDate_endDate_calendars_(
            NSDate.dateWithTimeIntervalSince1970_(start.timestamp()),
            NSDate.dateWithTimeIntervalSince1970_(end.timestamp()), cals)
        out = []
        for ek in self._store.eventsMatchingPredicate_(predicate) or []:
            event = event_from_ek(ek, str(ek.calendar().title()))
            if event is not None:
                out.append(event)
        return out

    def observe_changes(self, callback) -> None:
        from Foundation import NSNotificationCenter

        # The block runs on the posting thread; callback only queues a sync.
        self._observer = NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
            self._ek.EKEventStoreChangedNotification, self._store, None,
            lambda note: callback())


class CalendarSync:
    """Owns the `calendar` executor. Public methods are safe from any thread."""

    def __init__(self, *, library=None, store_factory=EventKitStore,
                 authorization=EventKitStore.authorization,
                 now=lambda: datetime.now(timezone.utc),
                 read_settings=settings.get_meeting_settings,
                 on_synced=lambda: None) -> None:
        self._library = library if library is not None else MeetingLibrary()
        self._store_factory = store_factory
        self._authorization = authorization
        self._now = now
        self._read_settings = read_settings
        self.on_synced = on_synced
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="calendar")
        self._lock = threading.Lock()
        self._pending = False
        self._store = None
        self.calendars: list[CalendarInfo] = []

    def access(self) -> str:
        status = self._authorization()
        if status == AUTH_FULL:
            return "connected"
        return "unconnected" if status == AUTH_NOT_DETERMINED else "denied"

    def request_sync(self) -> None:
        with self._lock:
            if self._pending:
                return
            self._pending = True
        self._executor.submit(self._run_sync)

    def request_access(self) -> None:
        self._executor.submit(self._request_access)

    def _request_access(self) -> None:
        if self.access() != "unconnected":
            # Already decided: macOS won't ask again; the page offers
            # Privacy Settings instead.
            self.request_sync()
            return
        self._get_store().request_access(self._after_access)

    def _after_access(self, granted: bool) -> None:
        # Any thread. A store created before the grant can keep answering
        # "no calendars", so start from a fresh one.
        self._executor.submit(self._drop_store)
        self.request_sync()

    def _drop_store(self) -> None:
        self._store = None

    def _get_store(self):
        if self._store is None:
            self._store = self._store_factory()
            self._store.observe_changes(self.request_sync)
        return self._store

    def _run_sync(self) -> None:
        with self._lock:
            self._pending = False   # a change during this sync queues another
        try:
            self.sync_now()
        except Exception as err:   # never kill the executor; log the type only
            print(f"  → calendar sync failed: {type(err).__name__}")
        self.on_synced()

    def sync_now(self) -> int:
        """Calendar thread only. Returns the number of events cached."""
        if self.access() != "connected":
            # Access withdrawn: stop serving events (MCP included).
            self.calendars = []
            self._library.clear_calendar_cache()
            return 0
        store = self._get_store()
        self.calendars = store.calendars()
        ids = included_ids(self.calendars, self._read_settings().get("calendar_choices", {}))
        now = self._now()
        start, end = now - SYNC_BACK, now + SYNC_AHEAD
        events = store.events(start, end, ids)
        self._library.replace_calendar_window(events, start, end)
        return len(events)

    def shutdown(self, wait: bool = False, restart: bool = False) -> None:
        """`restart=True` (tests) drains the queue, then accepts work again."""
        self._executor.shutdown(wait=wait, cancel_futures=not wait)
        if restart:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="calendar")
```

- [ ] **Step 7: Run tests and the full suite**

Run: `.venv/bin/python -m pytest -q` (timeout 300000)
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add requirements.txt requirements.lock.txt packaging/Speakeasy.spec speakeasy/settings.py speakeasy/calendar_sync.py tests/test_settings.py tests/test_calendar_sync.py
git commit -m "Sync Calendar.app into the cache on a calendar thread"
```

---

### Task 5: Engine: link the event, save title and attendees, cap speakers

**Files:**
- Modify: `speakeasy/engine.py` (`MeetingOptions`, `__init__`, `_begin_meeting`, `_meeting_start_failed`, `_end_meeting` no-audio branch, `_process_meeting`, new `link_meeting_event`)
- Modify: `tests/test_engine_meeting.py`

**Interfaces:**
- Consumes: `calendar_match.pick_event`, `remote_speaker_cap`, `MATCH_EARLY_JOIN` (Task 2); `MeetingLibrary.calendar_event`, `calendar_events_overlapping`, `NewMeeting.people` (Task 1); `_diarize_track(..., max_speakers=)` (Task 3).
- Produces:
  - `MeetingOptions.calendar_event_key: str | None = None`
  - `engine.meeting_event: CalendarEvent | None`. It is replaced whole, never mutated, so UI threads can read it.
  - `engine.link_meeting_event(event_key: str | None) -> None` (submits to control)

- [ ] **Step 1: Write the failing tests** (append to `tests/test_engine_meeting.py`; it already has `_engine`, `_wait_for`, `FakeDualMeetingRecorder`, `FakeDualTranscriber`, `FakeRemoteDiarizer`):

```python
from datetime import timezone as _tz
import sqlite3 as _sqlite3

from speakeasy.meeting_library import EventPerson, SyncedEvent

_REF = EventPerson("Refayet K", "refayet@example.com", "organizer")


def _seed_event(key="ev-now", minutes_ago=3, title="Weekly 1:1 — Refayet", other=1, people=(_REF,)):
    now = datetime.now(_tz.utc).replace(microsecond=0)
    start = now - timedelta(minutes=minutes_ago)
    MeetingLibrary().replace_calendar_window(
        [SyncedEvent(key, "Work", title, start, start + timedelta(minutes=30),
                     False, False, other, tuple(people))],
        start - timedelta(hours=1), start + timedelta(hours=1))


def _record(engine, options=None):
    saved = []
    engine.on_meeting_saved = saved.append
    engine.begin_meeting(options)
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    return saved


def _finish(engine, saved):
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    return MeetingLibrary().get_meeting(saved[0])


def test_recording_takes_the_matching_event(meetings_dir, spool_dir):
    _seed_event()
    engine = _engine(spool_dir)
    saved = _record(engine)
    assert _wait_for(lambda: engine.meeting_event is not None)
    stored = _finish(engine, saved)
    assert (stored.title, stored.calendar_event_id, stored.people) == (
        "Weekly 1:1 — Refayet", "ev-now", ["Refayet K"])
    assert engine.meeting_event is None
    engine.shutdown()


def test_explicit_event_key_wins(meetings_dir, spool_dir):
    _seed_event("near", minutes_ago=1, title="Near")
    _seed_event("chosen", minutes_ago=20, title="Chosen")
    engine = _engine(spool_dir)
    stored = _finish(engine, _record(engine, MeetingOptions(calendar_event_key="chosen")))
    assert stored.title == "Chosen"
    engine.shutdown()


def test_no_event_keeps_default_title(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    stored = _finish(engine, _record(engine))
    assert stored.title.startswith("Meeting — ") and stored.calendar_event_id is None
    engine.shutdown()


def test_calendar_cap_merges_remote_speakers(meetings_dir, spool_dir):
    _seed_event(other=1)
    engine = _engine(spool_dir)
    engine.meeting_recorder = FakeDualMeetingRecorder(spool_dir)
    engine.transcriber = FakeDualTranscriber()
    engine.diarizer = FakeRemoteDiarizer()
    stored = _finish(engine, _record(engine))
    assert [s.speaker for s in stored.segments] == ["You", "Speaker 1"]
    engine.shutdown()


def test_manual_count_beats_calendar(meetings_dir, spool_dir):
    _seed_event(other=1)
    engine = _engine(spool_dir)
    engine.meeting_recorder = FakeDualMeetingRecorder(spool_dir)
    engine.transcriber = FakeDualTranscriber()
    engine.diarizer = FakeRemoteDiarizer()
    engine._diarizer_speaker_count = 2
    stored = _finish(engine, _record(engine, MeetingOptions(expected_speaker_count=2)))
    assert [s.speaker for s in stored.segments] == ["You", "Speaker 1", "Speaker 2"]
    engine.shutdown()


def test_link_and_unlink_while_recording(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    states = []
    engine.on_state_changed = states.append
    saved = _record(engine)
    _seed_event("later")
    engine.link_meeting_event("later")
    assert _wait_for(lambda: engine.meeting_event is not None)
    assert states.count(State.MEETING_RECORDING) >= 2   # Dock refreshed
    engine.link_meeting_event(None)
    assert _wait_for(lambda: engine.meeting_event is None)
    assert _finish(engine, saved).calendar_event_id is None
    engine.shutdown()


def test_busy_library_at_start_records_unlinked(meetings_dir, spool_dir, monkeypatch):
    _seed_event()
    engine = _engine(spool_dir)
    def busy(*a, **k):
        raise _sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(engine.library, "calendar_events_overlapping", busy)
    stored = _finish(engine, _record(engine))
    assert stored.calendar_event_id is None
    engine.shutdown()


def test_event_deleted_before_save_uses_held_copy(meetings_dir, spool_dir):
    _seed_event()
    engine = _engine(spool_dir)
    saved = _record(engine)
    assert _wait_for(lambda: engine.meeting_event is not None)
    MeetingLibrary().clear_calendar_cache()
    stored = _finish(engine, saved)
    assert (stored.title, stored.people) == ("Weekly 1:1 — Refayet", ["Refayet K"])
    engine.shutdown()
```

Import `datetime`/`timedelta` at the top of the file if they aren't already. Check that `engine.library` exists in `_engine` (the engine builds `MeetingLibrary()` in `__init__`; the `meetings_dir` fixture points it at the temp library). If the attribute has another name, use that.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_engine_meeting.py -q` (timeout 300000)
Expected: the new tests FAIL (`calendar_event_key` unknown, `meeting_event` missing).

- [ ] **Step 3: Implement.** `MeetingOptions`: add the field `calendar_event_key: str | None = None`, and in `__post_init__`:

```python
        if self.calendar_event_key is not None and (
            not isinstance(self.calendar_event_key, str)
            or not 0 < len(self.calendar_event_key) <= 300
        ):
            raise ValueError("Unknown calendar event.")
```

In `__init__`, near `_meeting_options`:

```python
        # The calendar event this recording belongs to (cached copy). UI
        # threads read it; it is only ever replaced whole, never mutated.
        self.meeting_event = None
```

In `_begin_meeting`, after `self._set_state(State.MEETING_RECORDING)` and the print:

```python
        # Only after capture is running: a busy library can hold a read for
        # up to 5 s, and that must never delay the recording itself.
        self.meeting_event = self._resolve_meeting_event(
            options.calendar_event_key, self._meeting_started_at)
        if self.meeting_event is not None:
            self.on_state_changed(self.state)   # the Dock shows the title
```

New methods (next to `begin_meeting`):

```python
    def link_meeting_event(self, event_key: str | None) -> None:
        """Change or clear the current recording's event (Dock menu)."""
        self.control.submit(self._link_meeting_event, event_key)

    def _link_meeting_event(self, event_key: str | None) -> None:
        if self.state is not State.MEETING_RECORDING:
            return
        event = None
        if event_key is not None:
            try:
                event = self.library.calendar_event(event_key)
            except sqlite3.Error:
                return
            if event is None:
                return
        self.meeting_event = event
        self.on_state_changed(self.state)

    def _resolve_meeting_event(self, event_key, started_at):
        try:
            if event_key is not None:
                return self.library.calendar_event(event_key)
            return calendar_match.pick_event(
                self.library.calendar_events_overlapping(
                    started_at,
                    started_at + calendar_match.MATCH_EARLY_JOIN + timedelta(seconds=1)),
                started_at)
        except (sqlite3.Error, ValueError, RuntimeError):
            print("  → calendar match skipped (library unavailable)")
            return None

    def _saved_meeting_event(self):
        """The event re-read at save time (renamed since?), else the copy
        held since the start (deleted from Calendar since?)."""
        held = self.meeting_event
        if held is None:
            return None
        try:
            return self.library.calendar_event(held.event_key) or held
        except sqlite3.Error:
            return held
```

Add `import sqlite3` and `from . import calendar_match` at the top of `engine.py` if they are missing; `timedelta` is already imported. In `_meeting_start_failed`, and in `_end_meeting`'s "Nothing made it to disk" branch, set `self.meeting_event = None`. In `_process_meeting`'s `finally`, set `self.meeting_event = None` before `self._set_state(self._idle_state())`.

In `_process_meeting`, after `dual_track = ...`:

```python
            event = self._saved_meeting_event()
            speaker_cap = calendar_match.remote_speaker_cap(
                event, "mic_and_system" if dual_track else "mic_only")
```

Pass `max_speakers=speaker_cap` to both `self._diarize_track(...)` calls. In the `NewMeeting(...)` construction, add:

```python
                title=event.title if event is not None else None,
                calendar_event_id=event.event_key if event is not None else None,
                people=list(event.people) if event is not None else [],
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q` (timeout 300000)
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/engine.py tests/test_engine_meeting.py
git commit -m "Link recordings to calendar events and cap speakers by attendees"
```

---

### Task 6: Payloads, bridges and app wiring

**Files:**
- Create: `speakeasy/ui/calendar_payloads.py`, `speakeasy/ui/services.py`
- Modify: `speakeasy/ui/meetings_bridge.py`, `speakeasy/ui/meetings_window.py`, `speakeasy/ui/main_window.py`, `speakeasy/ui/menubar.py`, `AGENTS.md` (threading model)
- Create: `tests/test_calendar_payloads.py`
- Modify: `tests/test_meetings_bridge.py`

**Interfaces:**
- Consumes: Tasks 1, 2, 4 and 5.
- Produces (page contracts used by Task 7):
  - `calendar.today` → `{"access": "connected"|"denied"|"unconnected", "agenda": AgendaEvent[]}`
  - `calendar.upcoming` → `{"days": [{"dayLabel": "Wed 30 Sep", "events": AgendaEvent[]}]}`
  - `calendar.requestAccess` → `true`; `calendar.openPrivacySettings` → `true`; `calendar.record {key}` → `true`
  - `meetings.linkEvent {id, key|null}` → the same shape as `meetings.get`
  - `meetings.eventsForDay {id}` → `[{"key", "title", "time"}]`
  - `settings.meetings.get` / `.set {offerToRecord?, calendars?: {id: bool}}` → `{"offerToRecord": bool, "accounts": [{"name", "calendars": [{"id", "name", "enabled"}]}]}`
  - `meetings.get` → `"event": {"key", "title", "time"} | null`
  - `filters.features` → `{"calendar": <CalendarSync present>, "claude": true, "settings": true}`
  - page events `calendar.changed` (Meetings window) and Dock state `linkedEvent: {"key", "title", "time", "speakerHint": int|null} | null`
  - Dock methods `app.meetingEvents` → `[{"key", "title", "time"}]` and `app.linkMeetingEvent {key|null}` → `true`
  - `AgendaEvent` = `{"key", "time", "endTime", "title", "attendeeCount", "status": "recorded"|"recording"|"record"|"none", "meetingId"}`

- [ ] **Step 1: Write the failing payload tests** in `tests/test_calendar_payloads.py`:

```python
from datetime import datetime, timedelta, timezone

from speakeasy.calendar_sync import CalendarInfo
from speakeasy.meeting_library import CalendarEvent, EventPerson
from speakeasy.ui import calendar_payloads as cp

LOCAL = timezone(timedelta(hours=-7))           # PDT
NOW = datetime(2026, 9, 29, 16, 29, tzinfo=LOCAL)


def e(key, hh, mm, minutes=30, *, ids=(), other=1, people=1, all_day=False, declined=False):
    start = datetime(2026, 9, 29, hh, mm, tzinfo=LOCAL).astimezone(timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return CalendarEvent(key, "Work", key.title(), start.strftime(fmt),
                         (start + timedelta(minutes=minutes)).strftime(fmt), all_day, declined,
                         list(ids), other_attendees=other,
                         people=[EventPerson(f"P{i}", None, "attendee") for i in range(people)])


def test_agenda_statuses_and_times():
    events = [e("done", 9, 0, ids=["m1"]), e("live", 15, 30, 60), e("soon", 16, 30),
              e("later", 17, 0), e("gone", 11, 0), e("allday", 0, 0, 1440, all_day=True),
              e("no", 12, 0, declined=True)]
    rows = cp.agenda_payload(events, NOW, recording_key="live")
    assert [(r["key"], r["status"]) for r in rows] == [
        ("done", "recorded"), ("gone", "none"), ("live", "recording"),
        ("soon", "record"), ("later", "none")]
    assert rows[0]["time"] == "9:00 AM" and rows[0]["endTime"] == "9:30 AM"
    assert rows[0]["meetingId"] == "m1" and rows[0]["attendeeCount"] == 2


def test_attendee_count_falls_back_to_people():
    assert cp.agenda_payload([e("x", 17, 0, other=None, people=3)], NOW, None)[0]["attendeeCount"] == 3
    assert cp.agenda_payload([e("x", 17, 0, other=None, people=0)], NOW, None)[0]["attendeeCount"] == 0


def test_upcoming_groups_by_local_day_and_skips_empty_days():
    from dataclasses import replace
    base = e("t", 9, 0)
    tomorrow = replace(base, start_utc="2026-09-30T16:00:00Z", end_utc="2026-09-30T16:30:00Z")
    late = replace(base, event_key="late",
                   start_utc="2026-10-01T06:30:00Z", end_utc="2026-10-01T07:00:00Z")
    today = e("today", 17, 0)
    days = cp.upcoming_payload([today, tomorrow, late], NOW)
    # 06:30Z on 1 Oct is 11:30 PM on 30 Sep in PDT: same local day. Today's
    # event belongs to the agenda, not Upcoming.
    assert [(d["dayLabel"], [x["key"] for x in d["events"]]) for d in days] == [
        ("Wed 30 Sep", ["t", "late"])]


def test_linked_event_payload():
    assert cp.linked_event_payload(None, "mic_only", LOCAL) is None
    out = cp.linked_event_payload(e("x", 16, 0, other=1), "mic_and_system", LOCAL)
    assert out == {"key": "x", "title": "X", "time": "4:00 PM", "speakerHint": 1}


def test_calendars_payload_groups_accounts_and_applies_defaults():
    cals = [CalendarInfo("w", "Calendar", "Exchange", 2),
            CalendarInfo("h", "US Holidays", "Other", 3),
            CalendarInfo("b", "Birthdays", "Other", 4)]
    out = cp.calendars_payload(cals, {"h": True})
    assert out == [
        {"name": "Exchange", "calendars": [{"id": "w", "name": "Calendar", "enabled": True}]},
        {"name": "Other", "calendars": [{"id": "b", "name": "Birthdays", "enabled": False},
                                        {"id": "h", "name": "US Holidays", "enabled": True}]}]
```

Payload functions take an explicit `now` whose `tzinfo` is the local zone used for display. Tests pass a fixed `-07:00` zone, so they don't depend on the machine's time zone.

- [ ] **Step 2: Write the failing bridge tests** in `tests/test_meetings_bridge.py` (reuse the file's `_bcall` helper and fixtures):

```python
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


def test_today_without_access_is_empty(library_path):
    b = _calendar_bridge(library_path, calendar=FakeCalendar("denied"))
    assert b.calendar_today_payload({}) == {"access": "denied", "agenda": []}


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
```

(Import `datetime`/`timedelta` at the top of the test file if they aren't already there.)

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_calendar_payloads.py tests/test_meetings_bridge.py -q` (timeout 300000)
Expected: FAIL.

- [ ] **Step 4: Implement** `speakeasy/ui/calendar_payloads.py`:

```python
"""Pure shaping of cached calendar events for the pages (shapes =
frontend/src/mock/meetings.ts). `now` carries the display time zone."""

from datetime import datetime, timedelta

from ..calendar_match import PROMPT_BEFORE, event_end, event_start, remote_speaker_cap
from ..calendar_sync import CalendarInfo


def _clock(dt: datetime) -> str:
    return dt.strftime("%-I:%M %p")


def _visible(e) -> bool:
    # Agenda rows are meetings the user attends; all-day items and declined
    # invitations are not.
    return not e.all_day and not e.declined


def attendee_count(e) -> int:
    return e.other_attendees + 1 if e.other_attendees is not None else len(e.people)


def agenda_row(e, now: datetime, recording_key: str | None) -> dict:
    start, end = event_start(e).astimezone(now.tzinfo), event_end(e).astimezone(now.tzinfo)
    if recording_key == e.event_key:
        status = "recording"
    elif e.meeting_ids:
        status = "recorded"
    elif start - PROMPT_BEFORE <= now < end:
        status = "record"
    else:
        status = "none"
    return {"key": e.event_key, "time": _clock(start), "endTime": _clock(end),
            "title": e.title, "attendeeCount": attendee_count(e), "status": status,
            "meetingId": e.meeting_ids[-1] if e.meeting_ids else None}


def agenda_payload(events, now: datetime, recording_key: str | None) -> list[dict]:
    return [agenda_row(e, now, recording_key)
            for e in sorted(filter(_visible, events), key=lambda e: (e.start_utc, e.event_key))]


def upcoming_payload(events, now: datetime, days: int = 7) -> list[dict]:
    today = now.date()
    by_day: dict = {}
    for e in sorted(filter(_visible, events), key=lambda e: (e.start_utc, e.event_key)):
        day = event_start(e).astimezone(now.tzinfo).date()
        if today < day <= today + timedelta(days=days):
            by_day.setdefault(day, []).append(agenda_row(e, now, None))
    return [{"dayLabel": day.strftime("%a %-d %b"), "events": rows}
            for day, rows in sorted(by_day.items())]


def event_chip(e, tz) -> dict:
    return {"key": e.event_key, "title": e.title, "time": _clock(event_start(e).astimezone(tz))}


def day_chips(events, tz) -> list[dict]:
    return [event_chip(e, tz) for e in sorted(filter(_visible, events),
                                              key=lambda e: (e.start_utc, e.event_key))]


def linked_event_payload(event, capture_mode: str, tz=None) -> dict | None:
    if event is None:
        return None
    return {**event_chip(event, tz or datetime.now().astimezone().tzinfo),
            "speakerHint": remote_speaker_cap(event, capture_mode)}


def calendars_payload(calendars: list[CalendarInfo], choices: dict) -> list[dict]:
    accounts: dict[str, list[dict]] = {}
    for c in calendars:
        accounts.setdefault(c.account, []).append(
            {"id": c.id, "name": c.title, "enabled": choices.get(c.id, c.default_enabled)})
    return [{"name": name, "calendars": sorted(cals, key=lambda c: c["name"].lower())}
            for name, cals in sorted(accounts.items(), key=lambda kv: kv[0].lower())]
```

`speakeasy/ui/services.py`:

```python
"""Process-wide handles set once by menubar at launch, so window controllers
built from several places (menubar, the Dock) share them. None in tests and
in the MCP server, which must never touch EventKit."""

engine = None          # DictationEngine
calendar_sync = None   # calendar_sync.CalendarSync
```

`MeetingsBridge`: constructor gains `calendar=None, begin_meeting=None, recording_event_key=None, open_url=None`. Store them (`recording_event_key` defaults to `lambda: None`; `open_url` defaults to a no-op). Register:

```python
            "calendar.today": self.calendar_today_payload,
            "calendar.upcoming": self.calendar_upcoming_payload,
            "calendar.requestAccess": self.calendar_request_access_payload,
            "calendar.openPrivacySettings": self.calendar_privacy_payload,
            "calendar.record": self.calendar_record_payload,
            "meetings.linkEvent": self.link_event_payload,
            "meetings.eventsForDay": self.events_for_day_payload,
            "settings.meetings.get": self.settings_get_payload,
            "settings.meetings.set": self.settings_set_payload,
```

Implementations (in a `# -- calendar (phase 3)` section):

```python
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
        self._begin_meeting(MeetingOptions(calendar_event_key=key))
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
                "accounts": calendar_payloads.calendars_payload(
                    calendars, stored["calendar_choices"])}

    def settings_set_payload(self, params) -> dict:
        settings.set_meeting_settings(
            offer_to_record=params.get("offerToRecord"),
            calendar_choices=params.get("calendars"))
        if params.get("calendars") is not None and self._calendar is not None:
            self._calendar.request_sync()
        return self.settings_get_payload({})
```

(`MeetingOptions` is imported from `speakeasy.engine`. If importing the engine into the bridge pulls in heavy modules or AppKit, move `MeetingOptions` to a light module such as `speakeasy/meeting_options.py`, re-export it from `engine.py`, and import it from there. Check with `python -X importtime -c "import speakeasy.ui.meetings_bridge"`.)

In `filters_payload`: `"features": {"calendar": self._calendar is not None, "claude": True, "settings": True}`. In `get_payload`, replace `"event": None` with:

```python
            "event": (calendar_payloads.event_chip(ev, self._now().tzinfo)
                      if (ev := self.library.calendar_event(m.calendar_event_id)) else None),
```

`meetings_window.py` `init`: pass the services into the bridge:

```python
        from speakeasy.ui import services

        engine = services.engine
        self._bridge = MeetingsBridge(
            set_clipboard=injector.set_clipboard, open_path=...,   # existing args unchanged
            calendar=services.calendar_sync,
            begin_meeting=engine.begin_meeting if engine is not None else None,
            recording_event_key=lambda: (
                engine.meeting_event.event_key
                if engine is not None and engine.meeting_event is not None else None),
            open_url=lambda url: NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url)),
        )
```

Also add an ObjC method `calendarChanged_(self, _)` that calls `self._web.emit("calendar.changed")`. Emit only when the window's web view exists, following the file's existing pattern for `meetingSaved`.

`main_window.py` (Dock):
- In `_state_payload`, add `"linkedEvent": calendar_payloads.linked_event_payload(self.engine.meeting_event, getattr(meeting_recorder, "capture_mode", "mic_only"))`.
- Register `app.meetingEvents` → today's `day_chips(MeetingLibrary().calendar_events_between(today, today), tz)`. Respond `[]` if `services.calendar_sync` is `None` or its `access()` isn't connected.
- Register `app.linkMeetingEvent` → `self.engine.link_meeting_event(params.get("key"))`, then `respond(True)`. Validate that `key` is `None` or a non-empty `str` ≤ 300 chars; otherwise `respond(error="Unknown calendar event.")` (use the dispatcher's error form, as other handlers in that file do).

`menubar.py`, in `applicationDidFinishLaunching_` after `self.engine = engine`:

```python
        from ..calendar_sync import CalendarSync
        from . import services

        services.engine = engine
        self.calendar_sync = CalendarSync(
            on_synced=lambda: self.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"calendarSynced:", None, False))
        services.calendar_sync = self.calendar_sync
        # Never prompt at launch: only an explicit Connect Calendar click asks.
        if self.calendar_sync.access() == "connected":
            self.calendar_sync.request_sync()
        # Backstop for changes EventKit doesn't announce (and missed wakes).
        self._calendar_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            300.0, self, b"calendarTick:", None, True)
```

Add the handlers:

```python
    def calendarTick_(self, timer):
        if self.calendar_sync.access() == "connected":
            self.calendar_sync.request_sync()

    def calendarSynced_(self, _):
        owner = self.controller
        window = getattr(owner, "meetings_window", None)
        if window is not None:
            window.calendarChanged_(None)
```

In `systemDidWake_`, also call `self.calendarTick_(None)`. In the app's terminate path (find `applicationWillTerminate_` or the engine shutdown call), call `self.calendar_sync.shutdown()`. Import `NSTimer` from AppKit/Foundation, as the file already does for other classes.

Status-item menu: where the meeting menu item is built or updated on state change, show `Recording · <title>` as a disabled item while `engine.meeting_event` is set. Find the recording menu code with `grep -n "End Meeting\|Recording" speakeasy/ui/menubar.py`.

`AGENTS.md`, threading model: add a bullet after `control`:

```markdown
- **`calendar`** (1 thread, `calendar_sync.CalendarSync`) — the only place
  EventKit is used. Syncs Calendar.app into `calendar_events` at launch (only
  if access is already granted), after wake, on `EKEventStoreChangedNotification`
  and every 5 minutes. Never touches audio or the model. The engine never calls
  EventKit: it reads the cached table.
```

- [ ] **Step 5: Run the full suite and the frontend build**

Run: `.venv/bin/python -m pytest -q` (timeout 300000) and `npm --prefix frontend run build`
Expected: all pass; build clean (no frontend change yet).

- [ ] **Step 6: Commit**

```bash
git add speakeasy/ui/calendar_payloads.py speakeasy/ui/services.py speakeasy/ui/meetings_bridge.py speakeasy/ui/meetings_window.py speakeasy/ui/main_window.py speakeasy/ui/menubar.py AGENTS.md tests/test_calendar_payloads.py tests/test_meetings_bridge.py
git commit -m "Expose calendar, event linking and meeting settings to the pages"
```

---

### Task 7: Wire the calendar screens (Today, event chip, Settings, Dock)

The screens were approved as mocks in Phase 1 (Task 9 there). Keep their layout and copy. Replace mock data with bridge calls when `bridge.embedded`, and keep every existing mock state working in the browser preview.

**Files:**
- Modify: `frontend/src/mock/meetings.ts`, `frontend/src/meetings/App.tsx`, `TodayView.tsx`, `MeetingDetail.tsx` (+ `.module.css` for the chip menu), `SettingsSheet.tsx`, `Sidebar.tsx`, `frontend/src/dock/App.tsx`

**Interfaces:**
- Consumes: the Task 6 contracts, exactly as listed there.

- [ ] **Step 1: Types.** In `mock/meetings.ts`, change `MeetingDetail.event` to `{ key: string; title: string; time: string } | null` and update the mock details to include a `key`. Add:

```ts
export type CalendarAccess = 'connected' | 'denied' | 'unconnected';
export interface EventChip { key: string; title: string; time: string }
export interface UpcomingDay { dayLabel: string; events: AgendaEvent[] }
export interface CalendarToggle { id: string; name: string; enabled: boolean }
export interface CalendarAccountSettings { name: string; calendars: CalendarToggle[] }
export interface MeetingSettings { offerToRecord: boolean; accounts: CalendarAccountSettings[] }
```

Remove the duplicate `UpcomingDay` declaration in `TodayView.tsx` (a Phase 1 parked minor) and import it from here. Make `TodayConnection` an alias of `CalendarAccess`.

- [ ] **Step 2: Today view.** Add the prop `nowMinutes: number` to `TodayView` and use it instead of `MOCK_NOW_MINUTES` (the mock passes `MOCK_NOW_MINUTES`). Hide the people label when `attendeeCount === 0`. In `App.tsx`, when embedded:

```tsx
const [calendar, setCalendar] = useState<{ access: CalendarAccess; agenda: AgendaEvent[]; upcoming: UpcomingDay[] }>(
  { access: 'unconnected', agenda: [], upcoming: [] });
const [nowMinutes, setNowMinutes] = useState(() => minutesNow());

const refreshCalendar = useCallback(() => {
  if (!embedded) return;
  void Promise.all([bridge.call('calendar.today'), bridge.call('calendar.upcoming')])
    .then(([today, upcoming]) => setCalendar({
      access: today.access, agenda: today.agenda, upcoming: upcoming.days }))
    .catch(() => undefined);
}, [embedded]);

useEffect(() => { refreshCalendar(); return bridge.on('calendar.changed', refreshCalendar); }, [refreshCalendar]);
useEffect(() => {           // the now-line and Record buttons move with time
  const id = window.setInterval(() => { setNowMinutes(minutesNow()); refreshCalendar(); }, 30_000);
  return () => window.clearInterval(id);
}, [refreshCalendar]);
```

Here `minutesNow = () => { const d = new Date(); return d.getHours() * 60 + d.getMinutes(); }`. Use the file's existing bridge event-subscription helper; if `bridge.on` has another name or return shape, follow the `meetings.changed` subscription already in `App.tsx`. Also refresh on `meetings.changed` (a new recording flips an agenda row to Recorded).

Wire the handlers:
- `onConnectCalendar` → `bridge.call('calendar.requestAccess')`
- `onOpenPrivacySettings` → `bridge.call('calendar.openPrivacySettings')`
- `onRecord(key)` → `bridge.call('calendar.record', { key })`
- `onSelectMeeting` → the existing selection path.

`Sidebar`'s Today count is `calendar.agenda.length` when embedded and connected.

- [ ] **Step 3: Event chip menu.** In `MeetingDetail.tsx`, the chip button opens a small menu, built the same way as the existing ⋯ menu and registered with `overlayStack` so Esc closes it:
- The header item shows the event time.
- Then the same-day events from `meetings.eventsForDay {id}` (the current one checked), each calling `meetings.linkEvent {id, key}`.
- Then **Unlink**, which calls `meetings.linkEvent {id, key: null}`.

A meeting with no event shows a quiet **Link to Event…** text button in the same place, opening the same list. After a link or unlink, replace the detail with the returned payload and re-list, keeping the selection. In mock mode, use two fixed chips.

- [ ] **Step 4: Settings sheet.** `SettingsSheet` takes `settings: MeetingSettings`, `onChange(patch: { offerToRecord?: boolean; calendars?: Record<string, boolean> })` and `onExportAll`. When embedded, App loads `settings.meetings.get` on open and sends `settings.meetings.set` on each change, adopting the returned settings. The mock keeps its invented accounts, converted to the new shape with ids. An account list that is empty while connected shows "No calendars found."

- [ ] **Step 5: Dock.** In `dock/App.tsx`:
- Add `linkedEvent?: { key: string; title: string; time: string; speakerHint: number | null } | null` to `AppState`.
- Set `showEventUI = isMock ? dockMockState === 'recording-linked' : isRecording && calendarAvailable`. `calendarAvailable` is true once `app.meetingEvents` has returned without error.
- The label uses `app.linkedEvent` when embedded.
- The event menu fetches `app.meetingEvents` when opened. It lists them (current one checked) and calls `app.linkMeetingEvent {key}`. **Unlink** calls `{key: null}`.
- While recording with `linkedEvent?.speakerHint` set and no manual count, show helper text under the label: "Speakers: up to N (from calendar)". (The event is only known once recording starts, so the pre-recording Auto field is unchanged.)

- [ ] **Step 6: Build and check in the browser preview**

Run: `npm --prefix frontend run build`. Expected: clean.

Then open the built pages in the browser pane in mock mode: Meetings with `?state=today`, `today-denied`, `today-unconnected` and `settings`, and the Dock with `?state=recording-linked`. Screenshot each and compare them with the approved Phase 1 screenshots (same layout, copy and colours).

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "Wire Today, event chip, Meetings settings and Dock event menu"
```

---

### Task 8: Library and MCP efficiency follow-ups (Phase 2 eval)

**Files:**
- Modify: `speakeasy/meeting_library.py` (`TranscriptPage`, `transcript_page`, `get_meeting`, `list_meetings`, `search`)
- Modify: `speakeasy/mcp_tools.py` (`get_transcript`, `get_meeting`)
- Modify: `tests/test_meeting_library.py`, `tests/test_meeting_search.py`, `tests/test_mcp_tools.py`

**Interfaces:**
- Produces:
  - `TranscriptPage(segments, next_cursor, title: str = "", timestamps_approximate: bool = False)`
  - `MeetingLibrary.get_meeting(meeting_id, *, with_segments: bool = True)`. `StoredMeeting` gains `speakers: list[str]` and `segment_count: int`, always filled by SQL.
  - Search order: transcript and notes hits are interleaved by their rank within their own table.

- [ ] **Step 1: Write the failing tests.**

`tests/test_mcp_tools.py`:

```python
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
```

`tests/test_meeting_library.py`:

```python
def test_transcript_page_stops_reading_when_budget_is_spent(library_path, monkeypatch):
    lib = MeetingLibrary(library_path)
    mid = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", i, i + 1, "x" * 900) for i in range(200)],
        duration_seconds=200, started_at=datetime.now().astimezone()))
    seen = []
    real = meeting_library._segment
    monkeypatch.setattr(meeting_library, "_segment", lambda row: seen.append(1) or real(row))
    page = lib.transcript_page(mid, max_chars=1000)
    assert len(page.segments) == 1 and page.next_cursor == 1
    assert page.title and len(seen) <= 2


def test_get_meeting_without_segments_counts_in_sql(library_path):
    lib = MeetingLibrary(library_path)
    mid = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0, 1, "a"), MeetingSegment("Speaker 1", 1, 2, "b"),
                  MeetingSegment("You", 2, 3, "c")],
        duration_seconds=3, started_at=datetime.now().astimezone()))
    m = lib.get_meeting(mid, with_segments=False)
    assert m.segments == [] and m.segment_count == 3 and m.speakers == ["You", "Speaker 1"]
    assert lib.get_meeting(mid).speakers == ["You", "Speaker 1"]


def test_list_meetings_query_count_does_not_grow_with_rows(library_path, monkeypatch):
    lib = MeetingLibrary(library_path)
    for i in range(5):
        mid = lib.save_meeting(NewMeeting(segments=[MeetingSegment("You", 0, 1, "a")],
                                          duration_seconds=1,
                                          started_at=datetime.now().astimezone() - timedelta(hours=i)))
        lib.save_notes(mid, tags=["b", "A"])
    statements = []
    real_connect = meeting_store.connect
    def tracing(*a, **k):
        conn = real_connect(*a, **k)
        conn.set_trace_callback(statements.append)
        return conn
    monkeypatch.setattr(meeting_store, "connect", tracing)
    rows = lib.list_meetings()
    monkeypatch.undo()
    assert all(r.tags == ["A", "b"] for r in rows)
    assert sum(s.lstrip().upper().startswith("SELECT") for s in statements) == 1
```

(`MeetingLibrary._transaction` calls `meeting_store.connect`, so patching the module attribute is enough. The trace also records the migration's `PRAGMA`s, which is why only `SELECT`s are counted.)

`tests/test_meeting_search.py`:

```python
def test_notes_and_transcript_hits_interleave_by_rank(library_path):
    lib = MeetingLibrary()
    for day, text in [(21, "budget talk"), (22, "the budget again"), (23, "budget budget")]:
        _save(lib, day, ("You", 0, 5, text))
    for day in (24, 25):
        mid = _save(lib, day, ("You", 0, 5, "hello"))
        lib.save_notes(mid, summary="Agreed the budget")
    kinds = [h.kind for h in lib.search("budget", limit=10)]
    assert kinds == ["transcript", "notes", "transcript", "notes", "transcript"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_meeting_library.py tests/test_meeting_search.py tests/test_mcp_tools.py -q` (timeout 300000)
Expected: the new tests FAIL.

- [ ] **Step 3: Implement.**
- `TranscriptPage`: add the two defaulted fields.
- `transcript_page`: fetch `SELECT title, timestamps_approximate FROM meetings WHERE id = ?` in place of the existence check (not found → `MeetingNotFound`). Iterate the segment cursor lazily *inside* the `with` block (`for row in conn.execute(...)`) and `break` once the budget is spent, returning `next_cursor = row["idx"]` for the first row that didn't fit. Keep the "always admit one" progress guard.
- `get_meeting(meeting_id, *, with_segments=True)`: always compute:

```python
            speakers = [r[0] for r in conn.execute(
                "SELECT speaker FROM segments WHERE meeting_id = ? GROUP BY speaker"
                " ORDER BY MIN(idx)", (meeting_id,))]
            segment_count = conn.execute(
                "SELECT COUNT(*) FROM segments WHERE meeting_id = ?", (meeting_id,)).fetchone()[0]
```

  Load `segments` only when `with_segments`. Add `speakers` and `segment_count` to `StoredMeeting` as defaulted trailing fields.
- `list_meetings`: replace the per-row `self._tags`/`self._people` calls with two correlated subqueries in the main `SELECT`, separated by `char(31)` like `speakers_blob`. Keep the same order as the helpers:

```sql
(SELECT group_concat(name, char(31)) FROM (
   SELECT t.name FROM meeting_tags mt JOIN tags t ON t.id = mt.tag_id
   WHERE mt.meeting_id = m.id ORDER BY t.name COLLATE NOCASE)) AS tags_blob,
(SELECT group_concat(display_name, char(31)) FROM (
   SELECT p.display_name FROM meeting_people mp JOIN people p ON p.id = mp.person_id
   WHERE mp.meeting_id = m.id ORDER BY mp.role DESC, p.display_name COLLATE NOCASE)) AS people_blob
```

  While here, fold in the Phase 1 parked item and change `speakers_blob` to `group_concat(speaker, char(31))` over the ordered subquery. It already is ordered; leave it if so.
- `search`: after collecting hits, rank each kind separately by its own bm25 (`ORDER BY score` within the kind). Then sort all hits by `(rank_within_kind, 0 if kind == "transcript" else 1)` before `collapse_echoes`. Raw bm25 across two FTS tables is not comparable (the tables have different sizes and document lengths). Put that reason in a comment.
- `mcp_tools.get_transcript`: use `page.title` and `page.timestamps_approximate`, and drop the `library.get_meeting` call. `mcp_tools.get_meeting`: `library.get_meeting(id, with_segments=False)`, then `"speakers": m.speakers, "segment_count": m.segment_count`.

- [ ] **Step 4: Measure connection cost** (the eval said to measure before changing). Run in scratch, against a temp library only:

```bash
.venv/bin/python - <<'EOF'
import tempfile, time
from datetime import datetime
from pathlib import Path
from speakeasy.meeting_library import MeetingLibrary, NewMeeting
from speakeasy.meetings import MeetingSegment
lib = MeetingLibrary(Path(tempfile.mkdtemp()) / "library.sqlite")
mid = lib.save_meeting(NewMeeting([MeetingSegment("You", i, i + 1, "word " * 30) for i in range(3600)],
                                  3600, datetime.now().astimezone()))
t = time.perf_counter()
for _ in range(200):
    lib.transcript_page(mid, max_chars=20000)
print(f"{(time.perf_counter() - t) / 200 * 1000:.2f} ms per page")
EOF
```

Record the figure in Execution notes. **Decision rule:** under 20 ms per page, leave connections as they are. Over that, write a follow-up to give the MCP server one long-lived connection (single-threaded). Don't change it in this task.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q` (timeout 300000). Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add speakeasy/meeting_library.py speakeasy/mcp_tools.py tests/test_meeting_library.py tests/test_meeting_search.py tests/test_mcp_tools.py
git commit -m "Page transcripts lazily, count in SQL and interleave search kinds"
```

---

### Task 9: Fix imported titles that carry the end time

74 of the 92 imported titles end with the recording's *end* time (for example `1on1 Jim Meeting — Sep 2, 2:07 PM`, where the start was about 1:09 PM). Phase 1 regenerated only titles that exactly matched the old default. This task adds an explicit, previewable command. It never runs automatically, because it rewrites titles the user typed.

**Files:**
- Modify: `speakeasy/meeting_library.py` (new method), `speakeasy/__main__.py` (flag)
- Create: `tests/test_title_fix.py`

**Interfaces:**
- Produces: `MeetingLibrary.imported_title_fixes() -> list[tuple[str, str, str]]` (id, old, new) and `MeetingLibrary.apply_title_fixes(fixes) -> int`. CLI: `--fix-imported-titles` (dry run: prints `old → new`) and `--fix-imported-titles --apply`.

- [ ] **Step 1: Write the failing tests** in `tests/test_title_fix.py`:

```python
from datetime import datetime, timedelta, timezone

from speakeasy.meeting_library import MeetingLibrary, NewMeeting
from speakeasy.meetings import MeetingSegment

PDT = timezone(timedelta(hours=-7))


def _imported(lib, title, start, minutes, mid):
    return lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0, 1, "a")], duration_seconds=minutes * 60,
        started_at=start, title=title, meeting_id=mid, source="imported_json",
        timestamps_approximate=True))


def test_end_time_suffix_becomes_start_time(library_path):
    lib = MeetingLibrary(library_path)
    start = datetime(2026, 9, 2, 13, 9, tzinfo=PDT)
    _imported(lib, "1on1 Jim Meeting — Sep 2, 2:07 PM", start, 58, "20260902-140700-aaaa")
    fixes = lib.imported_title_fixes()
    assert fixes == [("20260902-140700-aaaa", "1on1 Jim Meeting — Sep 2, 2:07 PM",
                      "1on1 Jim Meeting — Sep 2, 1:09 PM")]
    assert lib.apply_title_fixes(fixes) == 1
    assert lib.get_meeting("20260902-140700-aaaa").title.endswith("1:09 PM")
    assert lib.imported_title_fixes() == []          # idempotent


def test_other_titles_are_left_alone(library_path):
    lib = MeetingLibrary(library_path)
    start = datetime(2026, 9, 2, 13, 9, tzinfo=PDT)
    _imported(lib, "Board prep", start, 58, "20260902-140700-bbbb")
    _imported(lib, "Sync — Sep 2, 1:09 PM", start, 58, "20260902-140700-cccc")   # already start
    lib.save_meeting(NewMeeting(segments=[MeetingSegment("You", 0, 1, "a")],
                                duration_seconds=3480, started_at=start,
                                title="Recorded — Sep 2, 2:07 PM"))           # not imported
    assert lib.imported_title_fixes() == []


def test_apply_skips_titles_changed_since_preview(library_path):
    lib = MeetingLibrary(library_path)
    start = datetime(2026, 9, 2, 13, 9, tzinfo=PDT)
    _imported(lib, "X — Sep 2, 2:07 PM", start, 58, "20260902-140700-dddd")
    fixes = lib.imported_title_fixes()
    lib.rename("20260902-140700-dddd", "Renamed by user")
    assert lib.apply_title_fixes(fixes) == 0
    assert lib.get_meeting("20260902-140700-dddd").title == "Renamed by user"
```

- [ ] **Step 2: Run to verify they fail**, then **implement**:

```python
    def imported_title_fixes(self) -> list[tuple[str, str, str]]:
        """Imported titles ending in the recording's *end* time (the old app
        stamped titles when processing finished), with that time replaced by
        the start. Preview only; apply_title_fixes writes."""
        fixes = []
        with self._transaction() as conn:
            for r in conn.execute(
                    "SELECT id, title, started_at, tz_offset_minutes, duration_seconds"
                    " FROM meetings WHERE source = 'imported_json' ORDER BY started_at"):
                start = local_start(r["started_at"], r["tz_offset_minutes"])
                end = start + timedelta(seconds=r["duration_seconds"])
                stamp = lambda d: d.strftime("%b %-d, %-I:%M %p")
                suffix = " — " + stamp(end)
                if stamp(end) != stamp(start) and r["title"].endswith(suffix):
                    fixes.append((r["id"], r["title"],
                                  r["title"][: -len(suffix)] + " — " + stamp(start)))
        return fixes

    def apply_title_fixes(self, fixes) -> int:
        changed = 0
        with self._transaction() as conn:
            for meeting_id, old, new in fixes:
                cur = conn.execute("UPDATE meetings SET title = ? WHERE id = ? AND title = ?",
                                   (new, meeting_id, old))
                if cur.rowcount:
                    self._touch(conn, meeting_id)
                    changed += 1
        return changed
```

(`timedelta` and `local_start` are already in the module. If `local_start` has a different signature, read it first.) In `__main__.py`, add the flags next to `--export-meetings`:

```python
    parser.add_argument("--fix-imported-titles", action="store_true",
                        help="show imported meeting titles that carry the end time; add --apply to fix them")
    parser.add_argument("--apply", action="store_true", help=argparse.SUPPRESS)
```

Include `args.fix_imported_titles` in the library-command branch:

```python
        if args.fix_imported_titles:
            fixes = MeetingLibrary().imported_title_fixes()
            for _, old, new in fixes:
                print(f"{old}  →  {new}")
            if args.apply:
                print(f"Renamed {MeetingLibrary().apply_title_fixes(fixes)} meeting(s).")
            else:
                print(f"{len(fixes)} title(s) would change. Run again with --apply to rename them.")
```

- [ ] **Step 3: Run the full suite**, then **commit**

Run: `.venv/bin/python -m pytest -q` (timeout 300000). Expected: all pass.

```bash
git add speakeasy/meeting_library.py speakeasy/__main__.py tests/test_title_fix.py
git commit -m "Add a previewable fix for imported titles that carry the end time"
```

---

### Task 10: Docs, full verification, installed-app acceptance

**Files:**
- Modify: `README.md`, `AGENTS.md`, this plan (Execution notes), `docs/superpowers/plans/2026-09-27-meeting-library-mcp.md` (Phase 3 status)

- [ ] **Step 1: Docs.**
  - README: a Calendar section covering what is read and what is never read, the permission click and how to turn it off (System Settings › Privacy & Security › Calendars), the Today view, and that recordings take the event title and attendees. Add a line saying the attendee count caps the speaker count, and that the Dock's own count overrides it. Document `--fix-imported-titles [--apply]`. Add a note under the MCP section: "Connect Speakeasy to Claude Code once. Adding both the extension and `claude mcp add` gives Claude two copies of every tool. To check, run `claude mcp list`."
  - AGENTS.md: the privacy rule (EventKit only on the `calendar` thread; notes, location and URLs never read) and the rule that calendar access is never requested at launch.
- [ ] **Step 2: Full verification.** Run `.venv/bin/python -m pytest -q` (timeout 300000) and `npm --prefix frontend run build`. Record the counts in Execution notes.
- [ ] **Step 3: Re-measure over-splitting on a read-only copy** (the numbers above are from 29 Sep): `sqlite3 "$HOME/Library/Application Support/Speakeasy/library.sqlite" ".backup '<scratchpad>/lib.sqlite'"`, then rerun the per-meeting speaker-count query on the copy. Imported meetings don't change (no audio is kept), so this is the "before" baseline for Step 6.
- [ ] **Step 4: Build and install** (needs the user's go-ahead: it quits the running app). Run `scripts/build_app.sh --install`. The worktree needs the `models` symlink. Confirm `/Applications/Speakeasy.app/Contents/Info.plist` has `NSCalendarsFullAccessUsageDescription`, and that `python -c` inside the bundle is not needed: check `Contents/Resources/lib` or the PyInstaller archive listing for `EventKit` (`pyi-archive_viewer` or `grep -a EventKit`).
- [ ] **Step 5: Acceptance, with the user** ("launched and looked at", spec Acceptance item 2):
  1. Launch shows **no** calendar prompt. Meetings → Today shows the "See your meetings here" card.
  2. Connect Calendar → the macOS prompt names Speakeasy → Allow. Today lists today's Exchange events with correct times, the now-line and attendee counts. Upcoming shows the next 7 days.
  3. Settings shows the Exchange calendars ticked and Birthdays/Holidays unticked. Unticking a calendar removes its events within a few seconds.
  4. Start a recording during (or up to 10 min before) a real event, or click Record on its Today row. The Dock shows "Recording · <title>". The event menu lists that day's events, and changing and unlinking works.
  5. After End Meeting, the meeting is titled from the event. The chip shows the event; people are the attendees; Today shows **Recorded ✓**.
  6. **Speaker check:** a real 1-on-1 (dual-track, linked) shows at most "You" plus one remote speaker. Record the before/after numbers in Execution notes, against the Step 3 baseline for similar meetings.
  7. MCP: in Claude, `get_calendar` for today returns the events with people.
  8. Revoke access in System Settings. Within 5 minutes (or on the next wake), Today shows the denied card and `get_calendar` returns `[]`.
  9. Dictation still types into TextEdit, **Codex and Teams** (CLAUDE.md recurring-insertion rule).
  10. Run `Speakeasy --fix-imported-titles` (dry run) against the real library and show the user the list. Apply only if the user says so.
- [ ] **Step 6: Update Execution notes and the parent plan** (Phase 3 done, with a link here). Then stop for the phase-boundary checkpoint (CLAUDE.md). Phase 4 (record prompt) starts in a fresh session.

---

## Execution notes

(Record deviations, surprises and review findings here during execution.)

### Status (29 Sep 2026)

- **Tasks 1–9 done**, each reviewed with mutation checks. **Task 10:** Steps 1–3 done. Steps 4–6 (build and install, acceptance with the user, final notes) are **not done**.
- Branch `meeting-library-phase3`, worktree `.claude/worktrees/meeting-library-phase3`, head `e4afaf2` at the final fix wave. Nothing is merged.
- **Test results (not launched):** full suite **697 passed**, against a baseline of 593 on master. `npm --prefix frontend run build` is clean. The mock screens were checked in the browser pane: Today in all three access states, Settings, the event-chip menu and the Dock event menu.
- **Final whole-branch review (Opus):**
  - Critical: revoking Calendar access never cleared the cache, because the plan's menubar code only synced when access was "connected". Fixed with `CalendarSync.tick()`, which runs on launch, wake and the 5-minute timer and syncs whenever access is not "unconnected".
  - Important: a fresh library opened by two connections at once could leave `user_version` stuck at 1. Fixed in `migrate`.
  - Both fixes were re-reviewed, and mutation checks caught both.
- **Step 2 measurement (Task 8):** `transcript_page` took **1.02 ms** per page (3600 segments, 20 000 chars). That is under 20 ms, so connections stay per-call.
- **Step 3 re-measure** on a read-only `.backup` copy (92 imported and 2 recorded meetings). Each figure is total labels / labels that hold at least 15 s of talk; the second number estimates the fold with no calendar:
  - Canada expansion: 144/27 (26 Aug), 139/34 (19 Aug), 112/27 (12 Aug)
  - 1on1 Jim: 74/17
  - 1on1 Victor: 69/30
  - 1on1 Bing: 57/20, 50/18, 39/13

  Imported meetings don't change, because no audio is kept. This is the "before" baseline for acceptance step 6.
- **Title-fix dry run on the copy:** 74 fixes, which matches the plan. For example, `Mesa Meeting — Jul 13, 12:28 PM → 12:05 PM`. Nothing has been applied.

### Surprises

- **A test wrote into real user data.** `test_add_word_updates_fuzzy_index_without_reload` (which predates this phase) saved `profiles/t.json` into the real App Support folder on every suite run. It now uses the `profiles_dir` fixture. An autouse `isolated_home` fixture in `tests/conftest.py` points HOME and `settings.app_support_dir` at a tmp dir for every test. The junk `t.json` was moved to the Trash at the user's request.
- **Stale `.pyc` after a mutation.** A same-length mutation (`!=` → `==`) that was restored within the same second left a stale `.pyc`, and later runs failed on it. Mutation runs now use `python -B`, and `__pycache__` is cleared afterwards.
- **Empty `node_modules`.** The main checkout's `frontend/node_modules` was empty, so `npm ci` was run in the worktree. `pyobjc-framework-EventKit==12.2.1` was installed into the shared `.venv`.
- **Install note (Step 4):** after installing, restart Claude Desktop and Claude Code. An MCP server process started before the install still expects schema v1, and it refuses the v2 library until it is relaunched.

### Rulings made during execution (the cost if wrong is in brackets)

- R1. Task 5's "explicit event wins" test seeds both events in one window. [none]
- R2. `get_payload` looks up an event only when the meeting is linked. [none]
- R3. v2 uses `BEGIN IMMEDIATE` framing. [none]
- R5. Commit trailers name the model that wrote the commit. [cosmetic]
- R6. One shared `meetings.flag_overlaps` replaces the overlap loop that was duplicated in `diarizer.py`. [a small refactor]
- R7. `tidy_speakers` catches the same errors as `identify`. [none]
- R8. `event_from_ek` never undercounts. An unnameable attendee makes the count unknown, and people with the same name are counted individually. [some caps become None]
- R9. Added post-grant fresh-store resync, observer removal and quiet shutdown. [extra code]
- R10. The bridge reads `meeting_event` once. [none]
- R11. The Dock `app.meetingEvents` errors with `calendar_unavailable` when Calendar is off, and the Dock re-probes when a recording starts. [the Dock UI is hidden if the probe fails for another reason]
- R12. Three frontend fixes: Settings reset on a failed load, the token colour, and "No calendars found." shown only while connected. [none]
- R13. Two plan tests that could not detect regressions were strengthened. [none]
- R14. Test isolation from the real App Support folder. [none]
- R15. The revoke-clears-cache fix, through `CalendarSync.tick()`. [a cheap store-less sync every 5 min while denied]
- R16. Fixed the stale migration comment. `--apply` alone is now an error. Added a comment on the thread-safety of `access()`. [none]
- R17. Parked: `migrate` records the target version on any duplicate-column error. That is safe for v2, which has a single statement. **A multi-statement v3 must guard it** with a target check or a column-exists check. [a v3 could be marked done early]
- R18. Parked: after a revoke, a store built while connected keeps observing until quit or the next grant. [harmless]

### Deferred minors (final review: all can wait)

- **Calendar cache and matching**
  - Moving an event in Calendar changes its key, so a meeting linked to it loses its chip. Its title and people are kept.
  - Today's list and the day chips include events that started the previous evening.
  - An equal-distance tie can pick an event that hasn't started yet over the running one.
  - `link_event` doesn't drop orphaned people (the next sync does).
  - Naive datetimes are not rejected in the window and overlap helpers.
  - An attendee name that contains `\x1f` would split in `list_meetings`.
- **Recording and speakers**
  - Record on a Today row does nothing visible when the engine isn't READY.
  - The event is re-read before ASR, not exactly at save.
  - Partial embeddings can leave an unembedded cluster standing during the cap merge.
  - `tidy_speakers` time is untimed.
- **Search:** echo collapse can leave two notes hits adjacent.
- **Frontend**
  - `bridge.call<T>` is an unchecked cast. No key-set contract test ties the Python payloads to the TS types.
  - Settings toggles are not optimistic.
  - The chip menu shows the previous list while loading and has no empty state.
  - Clicking the checked event behaves differently in the Dock and in the chip menu.
  - Dock link errors are swallowed.
  - Mock mode cannot preview the Dock speaker hint.
- **Test gaps**
  - Organizer-first event people order.
  - The window-end boundary.
  - The observer firing a coalesced sync.
  - The engine's embedding path and the 15 s anchor boundary.
  - Clearing `meeting_event` on start failure.
  - The title fix in a non-machine time zone, and its sub-minute guard.
  - Upcoming, the privacy URL, the Dock handlers and the menubar wiring.
  - The isolation test hard-codes `/Users/jchiu`.
- **Title fix:** truncated import seconds can make the end stamp a minute early, which can only cause a missed fix.
- **Threading:** `shutdown()` drops the store off the executor. `access()` runs on the main thread (a documented exception).

### Acceptance run (29 Sep 2026, installed builds 46918ca → 14541f3)

Passed:
1. No calendar prompt at launch.
2. Connect Calendar prompts, and Today lists the events.
3. Settings shows the ticks, and unticking a calendar removes its events.
4. Dock "Recording · pickup": the event menu switches events, and Unlink saves the meeting unlinked.
5. Record from the Today row links the event and saves its title. The user renamed one test meeting themselves.
7. `get_calendar` returns events with people (1,186 cached).
9. Dictation into TextEdit, Codex and Teams works, including the first take after relaunching Codex and Teams.

Not done yet:
- 6: speaker check on a real 1-on-1.
- 8: revoke access.
- 10: title-fix dry run on the real library. It already ran on a copy and found 74 fixes.

Bugs found and fixed during acceptance:
- **`35a46f5`**: the Dock event menu ran off the window, so Unlink couldn't be reached. Unlink now comes first and the list scrolls, in both menus.
- **`e563df9`**: an old coral focus frame around the whole meeting list.
- **`c81273e` + `14541f3`**: the first dictation into a freshly launched Electron app was blocked (`permission_or_focus_unavailable`). The diagnostic log showed two causes:
  - The key-down target was only kept if its lookup beat the mic's first buffer (5–20 ms), and AX lookups take about 15 ms. This random loss could hit any take.
  - A fresh Electron app answers the first query with `kAXErrorNoValue`, and the retry only ran if switching accessibility on succeeded.

  The fix has two parts:
  - **R20:** accept the target if it resolved within 300 ms of key-down, or before the first buffer. Delivery still refuses if focus changed.
  - **R21:** retry the lookup up to 4 times, 40 ms apart, whatever the switch-on returned. Stop if the frontmost app changes.

  Opus review: no Critical or Important findings, and 11 of 11 mutations were caught. Live check: the first takes in Codex and Teams inserted, but neither race condition happened in that run, with 0 retries and every lookup ahead of the buffer. So this is confirmed not to regress; the fix itself is not yet confirmed live. **Residual risk:** a non-secure field that gains focus within 300 ms of key-down and is still focused at release gets the text. The old rule held it for recovery. Password fields stay blocked.
- Build `14541f3` passes 712 tests.

### Follow-ups for the dictation fix (next session)

1. Stop the retry loop in `injector.focused_target` at the grace deadline: key-down + `DICTATION_TARGET_GRACE_SECONDS`. A later answer is discarded as late anyway, so extra retries only hold the single worker, which also runs streaming decode. `focused_target` doesn't know key-down, so pass a deadline in from `_resolve_dictation_target`. The delivery-time and `paste_last_dictation` calls have no key-down, so keep the fixed 4-attempt cap there.
2. Fix the `config.py` comment on the worst case. It is about 1.06 s for an unresponsive app: 0.1 s AX timeout per call, up to 4 enable calls, and 4 × (0.04 + 0.1) s. It is about 0.2 s for a fresh Electron app.
3. `paste_last_dictation` calls `focused_target()` on the AppKit main thread (`menubar.py:509` → `engine.py:~1593`). With no focused field it now blocks for about 0.2 s, up to about 1 s. Either move the lookup off the main thread without changing the recovery semantics, or cap it at a single attempt there. Decide, and document why.
4. Restore a direct test that `focused_target` writes `AXManualAccessibility` / `AXEnhancedUserInterface` through `_enable_accessibility`. The deleted `test_lazy_accessibility_retry_is_bounded_and_checks_foreground` covered it; now it is caught only through a diagnostics flag.
5. `tests/test_engine_streaming.py` `test_slow_focus_lookup_does_not_delay_capture_or_bind_later_field` never sets `_hold_started_ns`, so it only exercises the hold-started-None fallback. Add a threaded variant that goes through the grace path. Also fix the stale "1_000_000 ns hold start" comment near line 449.
6. `docs/insertion-focus-regression.md`: record the live 29 Sep results above, including that neither race condition happened in that run. Leave the status pending until a take with `target_retry_count > 0`, or with a lookup that finished between the first buffer and 300 ms, is seen inserting correctly.

Rules:
- Read CLAUDE.md, AGENTS.md (the recurring insertion rules) and `docs/insertion-focus-regression.md` first.
- Keep secure-field, target-identity, clipboard-ownership and fail-closed behaviour.
- Don't log PIDs or field content.
- Before claiming success, check TextEdit, Codex and Teams on the installed build, including the first take after relaunching each app.

### Remaining (Task 10 Steps 4–6)

1. Done: build and install. The installed build is `14541f3`, with `NSCalendarsFullAccessUsageDescription` and EventKit in the bundle. Claude Desktop and Claude Code were restarted.
2. Acceptance still to do:
   - 10: title-fix dry run on the real library. Apply only if the user says so.
   - 8: revoke Calendar access. Today should show the denied card, and `get_calendar` should return `[]` within 5 minutes or after a wake. Reconnect afterwards.
   - 6: speaker check on a real 1-on-1 against the Step 3 baseline. This can happen after the merge.
3. Record the results here, mark Phase 3 done in the parent plan, then merge and clean up. Merging means merging to master, pushing, and deleting the branch and worktree.
