# Meeting library, calendar and Claude MCP — design

Date: 26 September 2026 · Status: draft for review

## Goal

Make Speakeasy a local Granola-style meeting tool: see the work calendar,
record meetings (always started by a person), keep every transcript in a
searchable library, and let Claude Desktop and Claude Code query that
library through a local MCP server using the user's Claude subscription
(no API credits).

Reference: a coworker's version stores transcripts on Firebase, exposes a
hosted MCP server, and has Claude label and summarise each meeting so that
queries filter by label before opening transcripts. We keep that "labels and
summaries first, full transcript only when needed" pattern, but everything
Speakeasy does stays on this Mac.

## Decisions already made

| Topic | Decision |
|---|---|
| Privacy boundary | Speakeasy makes no network calls. Claude reads meetings only through a local stdio MCP server that the user's Claude client starts; text Claude reads is sent to Anthropic by that client, at the user's request. |
| Claude clients | Claude Desktop and Claude Code (both local stdio). claude.ai web/phone is out of scope (needs a hosted server). |
| Calendar | Work Exchange/M365 account already synced into macOS Calendar.app; read with EventKit. |
| Storage | SQLite is the master copy (not JSON, not Markdown). Plain-text export on demand. |
| Obsidian | Not used. Only its lessons are adopted (below). |
| v1 scope | Library + search + MCP; calendar in the app; record prompt at meeting start. |
| Recording consent | Speakeasy never starts recording by itself. The prompt only offers. |

### Lessons adopted from file-based note apps

1. The library can always leave SQLite as plain text (`--export-meetings`).
2. Derived data (search index, calendar copy) can always be rebuilt
   (`--rebuild-index`); only meetings, segments, notes, tags and people are
   master data.
3. People and tags are linked records, not just text, so "every meeting with
   X" or "open action items tagged DMT" is one query.
4. Summaries, tags and people are returned first; full transcripts are paged
   and fetched only when needed, to keep Claude's token use low.

## Current-code problems this work fixes

1. `Meeting.new` stamps `created` and the title when processing *finishes*
   (`engine.py` `_process_meeting`), not when recording starts, with no time
   zone. Calendar matching needs the start time.
2. `known_speaker_segments` merges all consecutive mic sentences without limit:
   across the 92 saved meetings the median longest segment is ~15 min and the
   maximum ~2.5 h, so search cannot point at a timestamp.
3. `Meeting.save` drops unknown keys and two processes could overwrite each
   other. Removed by moving to SQLite transactions.
4. `list_meetings` parses every full transcript on the main thread (4.3 M
   characters today). Replaced by indexed queries.
5. `Meeting.load` lets `TypeError` escape, so one malformed file breaks the
   list. Fixed in the importer, which skips and reports bad files.
6. Speaker relabelling uses `window.prompt()`/`window.confirm()`, but
   `ui/webwindow.py` sets no `WKUIDelegate`, so WebKit returns null/false
   and the click probably does nothing in the app (to confirm in the running
   app). Replaced by an in-page popover (see UI/UX).

In-room speaker-mode meetings transcribe the conversation on both tracks
(`merge_tracks` keeps both on purpose). Stored transcripts stay unchanged;
search results collapse the duplicates (see Search).

## Architecture

```
AppKit main thread ── Meetings window bridge ─┐
worker thread ─────── meeting save ───────────┤
calendar thread ───── EventKit sync ──────────┼─► MeetingLibrary (pure Python) ─► library.sqlite (WAL)
Claude ─stdio─► speakeasy --mcp ──────────────┘
                       (future) local web server ┘
```

New modules (flat, matching the repo's layout):

| Module | Responsibility | Depends on |
|---|---|---|
| `meeting_store.py` | Open connections (WAL, `busy_timeout=5000`, foreign keys), schema, numbered migrations via `PRAGMA user_version`, file permissions 0600 | stdlib `sqlite3` |
| `meeting_library.py` | `MeetingLibrary`: the only API for meetings: list, get, transcript pages, search, rename, relabel, delete, notes, tags, people, calendar events, linking. Returns plain dataclasses/dicts, UTC ISO timestamps. No AppKit imports. | `meeting_store` |
| `meeting_import.py` | One-time JSON → SQLite import with verification and archiving | `meetings`, `meeting_library` |
| `calendar_match.py` | Pure logic: pick the event for a recording; pick events to prompt for | none |
| `calendar_sync.py` | Thin EventKit wrapper + sync loop; faked in tests | `pyobjc-framework-EventKit==12.2.1` |
| `mcp_server.py` | Stdlib JSON-RPC 2.0 over stdio; tool definitions call `MeetingLibrary` | `meeting_library` |
| `ui/record_prompt.py` | Glass "Record this meeting?" panel | AppKit, existing glass helpers |

`meetings.py` keeps alignment, segment helpers and rendering. Its JSON
`Meeting` class is kept only for the importer.

Each call to `MeetingLibrary` opens a short-lived connection, so it is safe from
any thread or process without sharing connections.

## Data model (schema version 1)

- `meetings`: `id` (existing id format), `title`, `started_at` (UTC ISO),
  `tz_offset_minutes` (local UTC offset at recording start), `duration_seconds`, `capture_mode`,
  `system_audio_status`, `capture_scope`, `track_offsets_json`,
  `capture_health_json` (same whitelist as today), `calendar_event_id`
  (nullable), `source` (`recorded` | `imported_json`),
  `timestamps_approximate` (0/1), `created_at`, `updated_at`.
- `segments`: (`meeting_id`, `idx`) key, `speaker`, `start`, `end`, `text`,
  `confidence`, `overlap`, `profile_id`, `cluster_id`. Cascade delete.
- `segments_fts`: FTS5 external-content table over `segments(text, speaker)`,
  tokenizer `porter unicode61`, kept in step by triggers.
- `notes`: `meeting_id` key, `summary`, `action_items_json`, `updated_at`,
  `updated_by` (`claude` | `user`). `notes_fts` over summary and action items.
- `tags`, `meeting_tags`: tag names unique, case-insensitive.
- `people`, `meeting_people` (`role`: attendee | organizer): people come
  from calendar attendees (display name, email when EventKit provides one).
- `calendar_events`: `event_key` (external identifier + start, so recurring
  occurrences are distinct), `calendar_name`, `title`, `start_utc`, `end_utc`,
  `all_day`, `declined` (user's own status), `synced_at`; plus
  `calendar_event_people`. **Not stored:** event notes, location, or URLs.
  They often hold dial-in codes and passwords.

## Recording flow changes

- `begin_meeting` records wall-clock start (UTC + local time zone) with the
  recording, and resolves the calendar match from the cached
  `calendar_events` table (no EventKit call on the control thread).
- Match rule (`calendar_match.py`): candidates are non-all-day, not
  declined, with `start − 10 min ≤ recording start < end`. Choose the smallest
  |start − recording start|; ties go to the shorter event.
- `_process_meeting` saves through `MeetingLibrary.save_recorded_meeting(...)`
  in one transaction. Title = event title, or `Meeting — <start time>` when
  unmatched. Event attendees are linked as people.
- `known_speaker_segments` starts a new segment when the gap between
  sentences exceeds 1.5 s or the segment passes 60 s.
- The Meetings window can change or clear the linked event, choosing from
  events on the same day.

## Import of existing meetings

- Runs once at launch when the library is empty and JSON files exist (also
  `speakeasy --import-json-meetings`). Runs on the worker thread before the
  meetings list is first shown. The UI shows "Importing meetings…".
- `started_at` = old `created` − duration (old stamps were taken at end of
  processing). Marked `timestamps_approximate=1`.
- Segments longer than 120 s are split at sentence punctuation into pieces of
  at most ~60 s, with interpolated times (also approximate). Concatenated text
  is unchanged.
- Verification before committing: meeting count, segment text character
  total, and per-meeting speaker set must match what was read. Any mismatch
  rolls back and leaves JSON in place.
- On success the JSON files move, untouched, to `meetings/legacy-json/`.
  Files that fail to parse stay in `meetings/` and are listed in the log and
  the Meetings window.

## Search

- `search(query, from, to, tag, person, limit)` uses FTS5 `bm25()` over
  segments and notes, returning meeting id, title, date, speaker, timestamp
  and a `snippet()` of about 30 words.
- User text is turned into quoted terms before `MATCH`, so FTS syntax in a
  query can never raise an error or change the query.
- Echo collapse: hits in the same meeting whose time ranges overlap (with
  5 s slack) and whose snippets have text similarity ≥ 0.8 (`difflib`) are merged into one result listing both
  speakers.
- Later (not v1): semantic search with `sqlite-vec`, or `pgvector` if the
  library moves to Postgres.

## Calendar

- Permission: `NSCalendarsFullAccessUsageDescription` in the packaging
  `info_plist`; access requested on first use of a calendar feature
  (`requestFullAccessToEventsWithCompletion_`, macOS 14+).
- Sync: a new single-thread `calendar` executor, which never touches audio or
  the model. Syncs at launch, after wake, whenever EventKit posts
  `EKEventStoreChangedNotification`, and every 5 minutes as a backstop,
  covering 90 days back to 14 days ahead. Only calendars the user has
  included in Settings are synced (all by default). Deleted events disappear from the cache.
  Linked meetings keep their title and people.
- Access denied or no calendars: calendar sections are hidden, titles fall
  back, and nothing else changes.
- Only Speakeasy talks to EventKit. The MCP server reads the cached table,
  because a child process of Claude would put the Calendars permission prompt
  on Claude instead of Speakeasy.
- Meetings window: a "Today / Upcoming" list of events, each showing whether
  it has a recording.

## Record prompt

- Checked every 30 s from the cached table: an eligible event (non-all-day,
  not declined, `start − 2 min ≤ now ≤ start + 5 min`, not prompted before, no
  meeting in progress) shows a glass panel: "Record ‘<title>’?", with
  **Record** and **Dismiss**.
- Record calls `begin_meeting` with that event pre-linked. Dismiss or 5
  minutes without action hides it, and it is not shown again for that event.
- Setting: "Offer to record calendar meetings" (default on).
- It never records without a click.

## MCP server

- Started as `/Applications/Speakeasy.app/Contents/MacOS/Speakeasy --mcp`
  (from source: `.venv/bin/python -m speakeasy --mcp`). Dispatched at the top
  of `__main__.main` like `--inference-probe`, so no model, AppKit, microphone,
  spool sweep or Dock icon.
- Stdout carries only protocol messages: the server keeps the original
  stdout for JSON-RPC and points `sys.stdout` at stderr before importing
  anything that prints.
- Protocol: `initialize` (agrees on a protocol version, `2025-06-18` by
  default), `notifications/initialized`, `ping`, `tools/list`, `tools/call`.
  Unknown methods return JSON-RPC errors. Tool failures return `isError` with
  a short safe message.
- Tools (JSON results as text content):

| Tool | Arguments | Returns |
|---|---|---|
| `list_meetings` | `from?`, `to?`, `tag?`, `person?`, `limit=20` (max 100), `offset` | id, title, date, duration, tags, people, whether a summary exists |
| `get_meeting` | `id` | metadata, calendar event, people, tags, notes (no transcript) |
| `search_meetings` | `query`, `from?`, `to?`, `tag?`, `person?`, `limit=10` (max 50) | ranked snippets with timestamps |
| `get_transcript` | `id`, `start_seconds?`, `end_seconds?`, `cursor?`, `max_chars=20000` (max 60000) | lines `[hh:mm:ss] Speaker: text` + next cursor |
| `get_calendar` | `from`, `to` | events with linked meeting ids |
| `list_tags` | none | tags with counts |
| `list_people` | `query?` | people with meeting counts |
| `save_notes` | `id`, `summary?`, `action_items?`, `tags?` | updated notes; only fields given are replaced |

- Capture-health data is never exposed.
- Setup instructions in README for Claude Desktop
  (`claude_desktop_config.json`) and Claude Code (`claude mcp add`).
- **Claude Desktop Extension (`.mcpb`):** a one-click installer containing
  only a manifest and a tiny launcher that runs the installed app with
  `--mcp`. It holds no data, model or app code. Built and validated by
  `build_app.sh` with the `mcpb` CLI (a pinned, build-time-only npm tool) and
  shipped in `Speakeasy.app/Contents/Resources/Speakeasy.mcpb`. It can be
  turned off in Claude Desktop → Settings → Extensions.

## UI/UX design

Guiding model: macOS Calendar, Mail and Notes, following Apple's Human
Interface Guidelines, in Speakeasy's existing dark glass look
(`frontend/src/styles/tokens.css`: glass surfaces, hairlines, SF Pro, coral
brand, speaker palette). No new visual language and no new frontend
dependencies: React + CSS modules, as today.

### Principles

- **Familiar layout:** sidebar, list, detail, like Mail and Notes. Toolbar
  search at top right. Standard keyboard shortcuts.
- **Glance first, depth on request:** summary and people before the
  transcript, just as Claude sees them.
- **Ask in context:** permissions are explained on the screen that needs
  them, before the system prompt.
- **Never interrupt:** the record prompt never takes keyboard focus away from
  the user's meeting app.
- **Calm states:** every empty, loading, denied and error state has one
  sentence and at most one action.
- **Accessible:** VoiceOver labels, visible keyboard focus, and respect for
  Reduce Motion, Reduce Transparency (solid surfaces) and Increase Contrast
  (stronger hairlines).

### Meetings window (redesigned)

Resizable window (default 1040×660, minimum 820×520; today it is a fixed
720×480), three columns:

```
┌──────────────┬───────────────────────┬────────────────────────────────────┐
│ ● ● ●        │                       │                     🔍 Search  ⌘F  │
│ TODAY        │ Today                 │ Weekly 1:1 — Refayet               │
│  Today    3  │ ▸ 9:00  Stand-up   ✓  │ Thu 24 Sep · 1:17 PM · 23 min      │
│ LIBRARY      │ ▸ 1:00  1:1 Refayet ✓ │ 📅 Weekly 1:1 ⌄   (RK)(JC) +1      │
│  All Meetings│ Yesterday             │ [DMT] [VFA] [ACP]                  │
│ TAGS         │   …                   │ ┌ Summary │ Transcript ┐           │
│  DMT      12 │ Earlier this week     │ Summary text…                      │
│  VFA       8 │   …                   │ Action items                       │
│ PEOPLE       │                       │  ○ Confirm Purvi's leave (Naresh)  │
│  Refayet  14 │                       │                                    │
│ ─────────    │                       │ Copy  Export  ···                  │
│ Connect Claude│                      │                                    │
└──────────────┴───────────────────────┴────────────────────────────────────┘
```

- **Sidebar** (sections like Mail's mailboxes): *Today* (calendar), *All
  Meetings*, *Tags* and *People* with counts, and a *Connect Claude* item at
  the bottom. Selecting a tag or person filters the list.
- **List:** grouped by day under sticky headers (Today, Yesterday, weekday
  names, then month and year). Each row: time, title, duration, a small
  summary dot when notes exist. ↑/↓ moves the selection.
- **Detail header:** large title (double-click or Return to rename inline);
  date · start time · duration; linked-event chip with a menu (change to
  another event that day, or unlink); people as initials avatars (up to
  four, then "+n", hover for names); tags as capsules. Timestamps marked
  approximate show a small "≈" with a tooltip.
- **Segmented control:** *Summary | Transcript*, like Apple's segmented
  pickers. Summary is the default when notes exist, otherwise Transcript.
  - *Summary:* summary paragraph and action items (read-only in v1). Empty
    state: "No summary yet. Ask Claude to summarise this meeting." with a
    *Copy prompt* button.
  - *Transcript:* current coloured speaker lines, a find-in-transcript bar
    (⌘F while the transcript has focus), and "≈" on approximate times.
- **Speaker relabel:** clicking a speaker opens an anchored popover (like
  Calendar's event popovers) with a name field, a checkbox "Rename all
  ‘Speaker 2’ in this meeting", and *Rename* / *Cancel*. Replaces
  `window.prompt`/`window.confirm`.
- **Actions:** Copy and Export stay as buttons; Rename and Delete move to a
  "···" menu. Delete opens an in-page confirmation sheet ("Delete
  ‘Weekly 1:1’? The transcript and notes will be removed. This can't be
  undone.", with *Cancel* as the default and a red *Delete*). ⌘⌫ opens the same sheet.
- **Library search:** the toolbar field searches all meetings (debounced
  250 ms). Results replace the list, grouped by meeting with highlighted
  snippets, speaker and time. Choosing a result opens the Transcript tab
  scrolled to that line, with a brief highlight (no animation under Reduce
  Motion). Esc clears the search.

### Today view

Selecting *Today* shows a day agenda like Calendar's list view:

- Rows: time range, title, attendee count, and a status: **Recorded ✓**
  (opens the meeting), **Recording…** (red dot), **Record** button (from 2
  minutes before start to the event's end), or nothing for past meetings
  without a recording. A thin red "now" line sits between past and upcoming
  rows.
- "Upcoming" continues with the next 7 days, collapsed by day.
- **Not yet connected:** a card: "See your meetings here. Speakeasy reads
  Calendar on this Mac to title recordings and offer to record. Nothing
  leaves your Mac." [Connect Calendar] → macOS permission prompt.
- **Denied:** "Calendar access is off." [Open Privacy Settings] opens
  System Settings › Privacy & Security › Calendars.
- **No events today:** "Nothing on your calendar today."

### Record prompt

Styled like a macOS notification banner, but it is Speakeasy's own glass
panel:

```
┌──────────────────────────────────────────────┐
│ (icon)  Weekly 1:1 — Refayet                 │
│         Starting now · 3 people              │
│                          [Not Now] [Record]  │
└──────────────────────────────────────────────┘
```

- 360 pt wide, top-right under the menu bar, slides in (fades in under Reduce
  Motion). It is a non-activating panel, so the user's meeting app keeps
  keyboard focus.
- *Record* (coral primary) starts recording with the event linked, and the
  banner changes to "Recording" for 2 s before dismissing. *Not Now*, or 5
  minutes without action, dismisses it for that event only.
- If two eligible events overlap, the banner shows the nearest one with a
  "1 more" menu to pick the other.

### Dock and menu bar while recording

- The Dock shows the linked event: "Recording · Weekly 1:1 — Refayet ·
  12:03", with a small event menu (change or unlink) and End Meeting as
  today.
- An unlinked recording shows "Recording · Untitled meeting" with a
  *Link to event* menu.
- Menu bar: the existing recording glyph, plus the event title in the menu.

### Connect Claude sheet

Opened from the sidebar item. It gives step-by-step instructions for each
client, each with a *Copy* button:

- *Claude Code:* the exact `claude mcp add speakeasy -- …/Speakeasy --mcp`
  command.
- *Claude Desktop:* **Install in Claude Desktop** opens the bundled
  `.mcpb`, and Claude Desktop shows its own install dialog. Under "Other
  ways to connect": the JSON snippet, and *Reveal config file*, which opens
  `~/Library/Application Support/Claude/` in Finder. Speakeasy never edits
  another app's settings.
- A plain-language note: "Claude reads meetings only when you ask. What it
  reads is sent to Anthropic to answer you. Speakeasy itself stays offline."
- A connection status: "Last used by Claude: 2 min ago", from a timestamp the
  MCP server records on each call.

### Settings (Meetings section)

- Toggle: "Offer to record calendar meetings" (on).
- Calendars to include: checklist grouped by account (all on). Holiday and
  birthday calendars are off by default.
- Button: "Export all meetings…" (same as `--export-meetings`).

### Library upgrade (first launch)

In the Meetings window: "Upgrading your meeting library… 42 of 92" with a
progress bar, then a brief "92 meetings upgraded" note. On failure: "Your
meetings are safe and unchanged. The upgrade will try again next launch."
with *Show Details* (file names and error categories only).

### UI delivery process

Each phase with UI first builds the screens in the existing mock mode
(`frontend/src/mock/`, `npm run dev`) with realistic fake data, including
every empty, denied and error state. Screenshots go to the user for approval
**before** the screens are wired to the bridge. Screens are then checked in
the installed app (the `run` skill), not only in the browser.

## Meetings window data wiring

- List and detail read from `MeetingLibrary` (indexed and fast).
- Detail shows summary, action items, tags and people (read-only in v1;
  edited by Claude through `save_notes`), and the linked event with a
  change/clear control.
- `meetings.changed` is also emitted after a sync, after an import, and when
  notes change. The app polls `notes.updated_at` every 10 s while the window
  is open, to pick up MCP writes.
- New bridge methods: `meetings.search`, `meetings.filters` (tags, people,
  counts), `meetings.linkEvent`, `calendar.today`, `calendar.upcoming`,
  `calendar.requestAccess`, `calendar.openPrivacySettings`, `claude.setupInfo`,
  `settings.meetings.get` / `.set`.

## Commands

- `--mcp`, `--import-json-meetings`, `--rebuild-index` (rebuilds FTS tables
  and clears the calendar cache for re-sync), `--export-meetings <folder>`
  (one `.md` per meeting via `render_md` plus notes).

## Testing

All tests are pure logic with no microphone, model, EventKit or network:

- store: schema creation, migrations, WAL, cascade delete, FTS triggers.
- library: save/list/get/rename/relabel/delete; notes, tags and people;
  paging cursor; search ranking, filters, query quoting, echo collapse.
- import: fixtures for normal, legacy (no capture mode), malformed and giant
  segment files; verification failure rolls back and leaves JSON in place.
- segmentation: 1.5 s gap and 60 s cap.
- calendar match and prompt eligibility: overlapping, back-to-back,
  declined, all-day and early-join cases.
- calendar sync with a fake EventKit store: insert, update, delete, denied.
- MCP: a subprocess with pipes runs initialize → tools/list → each tool; a
  stray `print()` inside a tool does not reach stdout; bad arguments return
  errors.
- Engine: `_process_meeting` saves through the library with start time and
  link (existing mocks).

- bridge: the new bridge methods in `tests/test_webbridge.py` style, and the
  Python side of the record banner's eligibility and single-prompt rule.

Mutation check by the reviewer: break the gap rule, query quoting, the match
tie-break and stdout isolation, and confirm tests fail.

## Acceptance (launched and looked at, not only tests)

1. The installed app imports the 92 meetings. Counts match, and the JSON is
   archived.
2. A real calendar meeting is prompted, recorded after clicking Record, saved
   with the event title and attendees, and shows in Today.
3. Claude Desktop and Claude Code each connect to the installed `--mcp`, list
   meetings, search, read a transcript page, and save notes that then appear
   in the Meetings window.
4. Full test suite passes, and `npm --prefix frontend run build` (type
   check + build) passes. Dictation behaviour is unchanged (no insertion code
   is touched).
5. UI checked in the installed app: every screen and state from the UI/UX
   section matches the approved mock screenshots. Keyboard navigation and
   VoiceOver labels work on the Meetings window. The record banner does not
   take focus from a Teams call window. Relabelling a speaker works
   (issue 6).

## Phases (each can ship on its own)

1. **Library**: store, library, import, segmentation, start-time fix,
   redesigned three-column Meetings window (sidebar, day-grouped list,
   Summary/Transcript detail, library search, speaker popover, delete
   sheet, upgrade progress), export and rebuild commands.
2. **MCP server**: plus README setup and the Connect Claude sheet. First task: prove a stdio
   round-trip from the *installed, frozen* bundle. If the PyInstaller
   windowed executable does not pass stdio through, add a small console
   executable to the bundle for `--mcp`.
3. **Calendar**: EventKit dependency, permission, sync executor, matching,
   Today view with permission cards, linked-event chip and Dock recording
   label, Meetings settings section.
4. **Record prompt**: non-activating banner.

## Documentation to update

AGENTS.md: the `calendar` executor in the threading model; SQLite as the
meeting master; the MCP stdout rule; calendar fields that are never stored.
README: meetings library, calendar, MCP setup.

## Out of scope

Phone recording; hosted or remote MCP; claude.ai web/phone; any cloud sync;
on-device LLM; in-app "Ask" pane; automatic recording; semantic/vector
search; editing notes in the app; Obsidian or a Markdown folder.
