# list_meetings title filter

**Status:** Tasks 1–6 done 30 Sep 2026 (commit 26b969d); full suite 789 passed;
Opus mutation review approved (9 mutations, all caught). Not merged yet.
Implemented inline by Opus: the Sonnet implementer hit a spend limit before
making changes. `packaging/mcpb/manifest.json` also changed: it mirrors every
tool description and `tests/test_mcpb.py` requires an exact match.
**Branch:** `claude/jovial-heyrovsky-b7c6e2` (worktree `jovial-heyrovsky-b7c6e2`).

## Why

In a Claude Desktop test Claude couldn't find the meeting "1on1 Todd TEST" by
name: `search_meetings` only searches `segments_fts` and `notes_fts`, so it had
to page through `list_meetings`. Titles must be findable in one call.

## Decisions (agreed in chat)

- A `title` filter on `list_meetings` only. `search_meetings` and
  `MeetingLibrary.search` do **not** gain title hits: the app's search box
  (`speakeasy/ui/meetings_bridge.py` `search_payload`) shares `library.search`.
- Matching: split the text on whitespace; **every** word must appear somewhere
  in the title, any order, case-insensitive (Unicode casefold, not SQLite's
  ASCII-only LIKE/lower). `"todd 1on1"` finds `"1on1 Todd TEST"`.
- Empty or whitespace-only title = no filter.
- `%` and `_` are ordinary characters (use `instr`, not LIKE).

## Tasks (TDD: write each test, watch it fail, then implement)

1. **`speakeasy/meeting_store.py` `connect()`**: register
   `conn.create_function("casefold", 1, <None-safe str.casefold>, deterministic=True)`.
2. **`speakeasy/meeting_library.py`**: `meeting_filters(..., title=None)` adds one
   `instr(casefold(m.title), ?) > 0` clause per word (param `word.casefold()`).
   `MeetingLibrary.list_meetings(..., title=None)` passes it through. `search()`
   unchanged (keeps calling `meeting_filters` without `title`).
3. **`speakeasy/mcp_tools.py`**:
   - `list_meetings` handler passes `title=_text(args, "title")`.
   - schema property `title`: `{"type": "string", "description": "Words that must
     all appear in the meeting title, any order, case-insensitive."}` (only on
     list_meetings, not added to shared `_FILTERS`).
   - list_meetings description appends: "Filter by title words to find a meeting
     by name."
   - search_meetings description appends: "Meeting titles aren't searched: use
     list_meetings with title."
4. **Tests**
   - `tests/test_meeting_library.py`: any-order/any-case words match
     ("todd 1on1" → "1on1 Todd TEST"); a missing word excludes; `%`/`_` literal
     ("100%" doesn't match "1000 things", "a_b" doesn't match "axb"); non-ASCII
     case ("zoë" matches "ZOË sync"); combines with from/to and tag; whitespace-only
     title returns all.
   - `tests/test_mcp_tools.py`: title passes through and paging (`next_offset`)
     works on the filtered set; schema has `title`; both description sentences
     present.
   - `tests/test_mcp_server.py`: subprocess round trip adds a
     `("list_meetings", {"title": "budget"})` call (append at the end so existing
     ids don't shift) and one with a non-matching title returning `[]`.
   - All tests use the existing `library_path` / temp-HOME fixtures; never the real
     `~/Library/Application Support/Speakeasy`.
5. Full suite: `.venv/bin/python -m pytest` (give Bash a 600000 ms timeout).
6. Opus review with mutation (break each clause; confirm the suite notices).

## Remaining / follow-ups

- **Merged** to master (90141da) on 2026-09-30; 789 tests passed on the branch first.
  Installed with `build_app.sh --install`.
- **Installed binary checked over stdio** (`Speakeasy --mcp`, real library, read-only):
  `list_meetings` schema has `title`; `"todd 1on1"` and `"1on1 Todd TEST"` both return
  only "1on1 Todd TEST Meeting — Sep 28, 11:10 AM"; `"zzz-nomatch"` returns `[]`.
- **Claude Desktop check passed** (user, 2026-09-30): after toggling the connector, a new
  chat found "1on1 Todd TEST Meeting" (Sep 28, 11:10 AM) with one `list_meetings` call
  using `title`. It also noted that `search_meetings` doesn't search titles, which is the
  intended steer. Feature complete.
