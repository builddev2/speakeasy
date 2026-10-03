# Meeting Notepad Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every meeting a rich-text notes page the user owns, writable while recording (with time stamps that jump into the transcript) and any time afterwards, readable by Claude, searchable and exported.

**Architecture:** Python stores notes as Markdown in a new `user_notes` table (schema v5) plus a single-row `note_draft` for the meeting being recorded; `MeetingLibrary.save_meeting` adopts the draft so `engine.py` is never edited. The bridge exposes `notes.*` and `recording.get`. The page uses TipTap (bundled, offline) with a pure Markdown ↔ document converter; a Notes tab joins Summary/Transcript and a "Recording now" sidebar row opens the draft.

**Tech Stack:** Python 3.11 + SQLite FTS5 + PyObjC, React 18 + TypeScript + Vite, TipTap 3.31.4 (`@tiptap/core`, `@tiptap/pm`, `@tiptap/react`, `@tiptap/starter-kit`, `@tiptap/extension-list`), pytest, `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-02-meeting-notepad-design.md` (approved 2 Oct 2026). The Quiet Library spec's UI rules also bind: `docs/superpowers/specs/2026-10-02-quiet-library-design.md`.

## Execution roles (user requirement — binding)

| Role | Model | Agent `model` value | Covers |
|---|---|---|---|
| Implementer (writes code and tests) | Sonnet 5.5 (`claude-sonnet-5-5`) | `"sonnet"` | Every task's Steps, including every fix after a review |
| Task reviewer / evaluator | Opus 5.5 (`claude-opus-5-5`) | `"opus"` | Spec-compliance and code-quality review after each task, mutation checks, visual and overflow checks, the real-app evaluation in Task 9 |
| Final whole-branch review | Opus 5.5 | `"opus"` | One review of the complete branch before merge |
| Controller | The session model (Opus) | — | Dispatches, records, merges; writes no feature code |

- **Set `model` explicitly on every Agent dispatch.** An omitted model inherits the controller's model, which would put Opus on implementation.
- If a Sonnet implementer is stuck after two attempts, re-dispatch on Sonnet with a sharper brief; never switch implementation to Opus without asking the user.
- Reviewers verify by **mutation**: break the code, confirm a test fails, restore with `git checkout`, clear `__pycache__`. A review that ran no mutation is not a pass. Python mutation runs: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider <file>`.
- Record the model used for each dispatch in Execution notes (one line per task).
- Long Bash calls (full pytest ≈ 2 min, builds) get `timeout: 600000`. Never run app code on the real `~/Library/Application Support/Speakeasy`; pytest's conftest isolates HOME, and real-app checks use a temp HOME.

## Usage-limit handoff (user requirement, 2 Oct 2026)

The user may be away from the computer (on the phone) while this runs. When a usage-limit notice arrives, or the session is about to stop for any other reason before the plan is finished:

1. Finish only the piece in hand (do not start a new task); let a running subagent finish if the allowance permits, otherwise note which task it was on.
2. Checkpoint: append a dated **Progress** block to Execution notes in this plan file (tasks complete with commit ranges, the task in progress and its last fix round, open findings, rulings), and commit it on the feature branch. The SDD ledger lives in the worktree's `.superpowers/sdd/2026-10-02-meeting-notepad/progress.md`.
3. Nothing left running: stop dev servers and test apps.
4. End the message with `Safe to /clear: yes` (or what is missing) and a **fresh-session prompt** the user can paste at home, naming the plan file, the worktree path, the branch and the first task without a `complete` line, e.g. `Execute docs/superpowers/plans/2026-10-02-meeting-notepad.md, subagent-driven, resuming at Task N in worktree .claude/worktrees/meeting-notepad (ledger .superpowers/sdd/2026-10-02-meeting-notepad/progress.md).`

## Setup (once, before Task 1)

- [ ] `git worktree add .claude/worktrees/meeting-notepad -b meeting-notepad master`; in it `ln -s ../../../.venv .venv`, `ln -s ../../../models models`, `npm --prefix frontend ci`.
- [ ] Baseline: `npm --prefix frontend run build`, `npm --prefix frontend test`, `.venv/bin/python -m pytest -q` (≈ 1024 passed; `timeout: 600000`).

## Plan-time decisions (where the plan departs from the spec's wording)

- `user_notes` uses `id INTEGER PRIMARY KEY` with `meeting_id UNIQUE` (spec: `meeting_id` primary key) so the FTS index has a stable rowid, as `notes_fts` does.
- Draft stamps are stored as seconds since the draft's `started_at` (spec: UTC ISO per stamp). Same information, simpler; adoption shifts them by `draft.started_at − meeting.started_at`.
- There is no `notes.user.get`: the notes arrive with `meetings.get` (`userNotes`, `hasUserNotes`), saving a round trip. A `notes.draft.finish` call is added so the editor's last words reach the saved meeting.
- Copy and Markdown export use `render_export_md` (summary, action items, My notes, transcript); Copy previously used the plain transcript.
- A draft left over from an unsaved recording loses its stamps when it reopens: they point at audio that was never saved.
- The sidebar row stays (dot turns grey) while the meeting is processing, so the draft remains editable until the meeting is saved.

## Global Constraints

- Fully offline at run time. The only new dependencies are `@tiptap/core`, `@tiptap/pm`, `@tiptap/react`, `@tiptap/starter-kit`, `@tiptap/extension-list`, all pinned to exactly `3.31.4` (no `^`/`~`) in `frontend/package.json` `dependencies`.
- Do not edit `speakeasy/engine.py`, `speakeasy/ui/menubar.py`, or any dictation/insertion code (the Codex `latency-cancellation` worktree edits engine.py).
- Library schema becomes version 5 by appending to `_MIGRATIONS`; never edit a shipped migration.
- Notes limit: 200,000 characters of Markdown; refusal message exactly `Notes are too long to save`.
- Autosave 500 ms after the last change; failed save retries every 5 s; draft adoption window: draft start between meeting start − 10 min and meeting start + duration.
- CSS: colours only from `tokens.css` tokens (the lint in `tests/test_frontend_tokens.py` covers new modules); coral (`--accent`) only for Record actions and the focus ring; active formatting buttons use `--selection` + `--text-hi`; checklist boxes neutral (`--text-mid` checked fill, `--hairline` border); Recording now dot `--rec`.
- Typography/layout: notes body `400 14.5px/1.6 var(--sans)`, colour `--text`; H1 `600 20px/1.3 var(--serif)`, H2 `600 16.5px/1.35 var(--serif)`; stamps `400 11.5px` tabular nums `--text-lo` in a 52 px gutter + 12 px gap; editor inside `.bodyInner`, no card; `.body` is the only scroller; the formatting bar is sticky like the find bar.
- Nothing clips from 820 × 520 to 2560 × 1440 in light or dark (`frontend/scripts/overflow-check.js` returns `[]`).
- Copy strings, verbatim: "Notes", "Recording now", "Meeting in progress", "Saved", "Saving…", "Couldn't save — retrying", "Notes from a recording that wasn't saved", "Discard", "My notes", "Notes are too long to save".
- MCP tool description changes are mirrored word for word in `packaging/mcpb/manifest.json` (`tests/test_mcpb.py` enforces).
- Claude can read user notes; nothing lets Claude write them (`save_notes` unchanged).

## Review Focus

1. **The library refreshes while the user is typing** (`meetings.changed` fires after Claude saves a summary): the editor must not reload and lose the caret or recent text. Test: Task 6 keys `NotesPane` by meeting id and loads content only on mount; its Step 7 check types, emits a refresh in the mock and confirms the text and caret survive.
2. **The user types in the half-second before the meeting is saved**: the last words must reach the saved meeting, not a stray draft. Test: Task 2 `test_finish_draft_overwrites_with_latest_text`; Task 7 calls `notes.draft.finish` on `meetings.saved`.
3. **The Meetings window is closed during the recording**: notes still arrive on the saved meeting. Test: Task 2 `test_save_meeting_adopts_a_draft_from_this_recording`.
4. **A recording is cancelled, then another starts**: the old notes appear with the notice and without stale stamps, and are not attached to the wrong meeting. Test: Task 2 `test_draft_outside_window_is_left_alone`; Task 7 strips stamps from a leftover draft (`stripStamps` node test).
5. **Pasting from a web page or Word** (links, images, tables, colours): only the supported formats survive, the rest becomes plain text. Test: Task 5 `notesToDoc` ignores unknown syntax; Task 6 Step 7 pastes rich HTML in the mock and checks the saved Markdown.

---

### Task 1: Notes storage, plain text and search

**Files:**
- Modify: `speakeasy/meeting_store.py` (SCHEMA_VERSION, `_SCHEMA_V5`, `_MIGRATIONS`, `rebuild_derived`)
- Modify: `speakeasy/meeting_library.py` (constants, `UserNotes`, `notes_plain_text`, `StoredMeeting.user_notes`, `get_user_notes`, `set_user_notes`, `_write_user_notes`, `_user_notes`, search)
- Test: `tests/test_user_notes.py` (new); `tests/test_meeting_store.py` only if an existing assertion pins version 4

**Interfaces:**
- Produces: `USER_NOTES_MAX_CHARS = 200_000`; `NOTES_LINE_PREFIX` (compiled regex of a line's list/heading marker); `notes_plain_text(markdown: str) -> str`; `@dataclass UserNotes(markdown: str, stamps: list[tuple[int, float]], updated_at: str)`; `MeetingLibrary.get_user_notes(meeting_id) -> UserNotes | None` (raises `MeetingNotFound`); `MeetingLibrary.set_user_notes(meeting_id, markdown, stamps) -> UserNotes | None` (None when the Markdown is blank, which deletes the row); `MeetingLibrary._write_user_notes(conn, meeting_id, markdown, stamps) -> UserNotes | None`; `StoredMeeting.user_notes: UserNotes | None = None`; `SearchHit.kind` gains `"user_notes"`; `_check_stamps(stamps) -> list[tuple[int, float]]` (module-level).

- [ ] **Step 1: Write the failing tests** — `tests/test_user_notes.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from speakeasy import meeting_store
from speakeasy.meeting_library import (
    USER_NOTES_MAX_CHARS, MeetingLibrary, MeetingNotFound, NewMeeting, notes_plain_text,
)
from speakeasy.meetings import MeetingSegment

EDT = timezone(timedelta(hours=-4))
START = datetime(2026, 10, 2, 9, 0, 0, tzinfo=EDT)


def _new(started=START, **kw):
    kw.setdefault("segments", [MeetingSegment("You", 0.0, 4.0, "hello there"),
                               MeetingSegment("Speaker 1", 4.5, 9.0, "budget review")])
    kw.setdefault("duration_seconds", 600.0)
    return NewMeeting(started_at=started, **kw)


def test_schema_is_v5_with_notepad_tables(library_path):
    conn = meeting_store.connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert {"user_notes", "user_notes_fts", "note_draft"} <= names
    finally:
        conn.close()


def test_v4_library_migrates_to_v5_keeping_meetings(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    conn = meeting_store.connect()
    conn.executescript("DROP TABLE user_notes_fts; DROP TABLE user_notes;"
                       " DROP TABLE note_draft; PRAGMA user_version = 4;")
    conn.close()
    assert lib.get_meeting(mid).title
    assert lib.get_user_notes(mid) is None
    conn = meeting_store.connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    conn.close()


def test_set_and_get_user_notes_round_trip(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    saved = lib.set_user_notes(mid, "## Plan\n- [ ] send deck", [(1, 12.5)])
    got = lib.get_user_notes(mid)
    assert (got.markdown, got.stamps) == ("## Plan\n- [ ] send deck", [(1, 12.5)])
    assert got.updated_at == saved.updated_at
    assert lib.get_meeting(mid).user_notes == got


def test_blank_notes_delete_the_row(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    lib.set_user_notes(mid, "keep", [])
    assert lib.set_user_notes(mid, "  \n ", []) is None
    assert lib.get_user_notes(mid) is None


def test_too_long_notes_are_refused(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    with pytest.raises(ValueError, match="Notes are too long to save"):
        lib.set_user_notes(mid, "x" * (USER_NOTES_MAX_CHARS + 1), [])
    lib.set_user_notes(mid, "x" * USER_NOTES_MAX_CHARS, [])


@pytest.mark.parametrize("stamps", [[(-1, 3.0)], [(0, -2.0)], [(0, "a")], [(True, 1.0)], [(0,)]])
def test_invalid_stamps_are_refused(library_path, stamps):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    with pytest.raises(ValueError, match="Invalid note stamps"):
        lib.set_user_notes(mid, "text", stamps)


def test_unknown_meeting_is_not_found(library_path):
    lib = MeetingLibrary()
    with pytest.raises(MeetingNotFound):
        lib.set_user_notes("20260101-000000-abcd", "text", [])
    with pytest.raises(MeetingNotFound):
        lib.get_user_notes("20260101-000000-abcd")


def test_search_finds_user_notes_with_their_own_kind(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    lib.set_user_notes(mid, "- [ ] **Escalate** the vendor contract", [])
    hits = lib.search("vendor")
    assert [(h.meeting_id, h.kind) for h in hits] == [(mid, "user_notes")]
    assert "\x02vendor\x03" in hits[0].snippet
    assert [h.kind for h in lib.search("budget")] == ["transcript"]


def test_deleting_a_meeting_removes_its_notes_and_hits(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    lib.set_user_notes(mid, "vendor contract", [])
    lib.delete(mid)
    assert lib.search("vendor") == []
    conn = meeting_store.connect()
    assert conn.execute("SELECT COUNT(*) FROM user_notes").fetchone()[0] == 0
    conn.close()


def test_rebuild_derived_keeps_user_notes_searchable(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(_new())
    lib.set_user_notes(mid, "vendor contract", [])
    conn = meeting_store.connect()
    meeting_store.rebuild_derived(conn)
    conn.close()
    assert [h.kind for h in lib.search("vendor")] == ["user_notes"]


def test_notes_plain_text_strips_markdown():
    md = "# Title\n- [x] **done** item\n  1. *one*\n\\- not a list\n\nend \\*star\\*"
    assert notes_plain_text(md) == "Title\ndone item\none\n- not a list\nend *star*"
```

- [ ] **Step 2: Run to see them fail**

Run: `.venv/bin/python -m pytest tests/test_user_notes.py -q`
Expected: FAIL — `ImportError: cannot import name 'USER_NOTES_MAX_CHARS'`.

- [ ] **Step 3: Schema v5** — in `speakeasy/meeting_store.py` set `SCHEMA_VERSION = 5`, add after `_SCHEMA_V4`:

```python
# v5: the user's own notes per meeting (Markdown; `text` is the plain copy
# the FTS index reads) and the single draft being typed during a recording.
# Kept apart from `notes` (Claude's summary) so neither can overwrite the other.
# `id INTEGER PRIMARY KEY` (not meeting_id) because the FTS table is keyed on
# a stable rowid, as notes_fts is.
_SCHEMA_V5 = f"""
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS user_notes (
    id INTEGER PRIMARY KEY,
    meeting_id TEXT NOT NULL UNIQUE REFERENCES meetings(id) ON DELETE CASCADE,
    markdown TEXT NOT NULL,
    text TEXT NOT NULL,
    stamps_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS user_notes_fts USING fts5(
    text, content='user_notes', content_rowid='id',
    {_TOKENIZE}
);
CREATE TRIGGER IF NOT EXISTS user_notes_ai AFTER INSERT ON user_notes BEGIN
    INSERT INTO user_notes_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS user_notes_ad AFTER DELETE ON user_notes BEGIN
    INSERT INTO user_notes_fts(user_notes_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS user_notes_au AFTER UPDATE ON user_notes BEGIN
    INSERT INTO user_notes_fts(user_notes_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO user_notes_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TABLE IF NOT EXISTS note_draft (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    markdown TEXT NOT NULL,
    stamps_json TEXT NOT NULL DEFAULT '[]',
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
PRAGMA user_version = 5;
COMMIT;
"""
```

Append `(5, _SCHEMA_V5)` to `_MIGRATIONS`, and in `rebuild_derived` add `conn.execute("INSERT INTO user_notes_fts(user_notes_fts) VALUES ('rebuild')")` after the notes_fts line. (The spec's `meeting_id TEXT PRIMARY KEY` became `id INTEGER PRIMARY KEY` + `meeting_id UNIQUE` for a stable FTS rowid — record this in Execution notes.)

- [ ] **Step 4: Library API** — in `speakeasy/meeting_library.py` (add `import re` if absent):

```python
USER_NOTES_MAX_CHARS = 200_000

# Line markers the notepad's Markdown uses (see frontend/src/meetings/notesMarkdown.ts).
NOTES_LINE_PREFIX = re.compile(r"^\s*(?:#{1,2} |[-*] \[[ xX]\] |[-*] |\d+\. )")
_MD_INLINE = re.compile(r"\\(.)|\*+")


def notes_plain_text(markdown: str) -> str:
    """The notepad's Markdown as plain lines for search: list/heading markers
    and emphasis removed, backslash escapes resolved, blank lines dropped."""
    out = []
    for line in markdown.splitlines():
        if not line.startswith("\\"):
            line = NOTES_LINE_PREFIX.sub("", line)
        line = _MD_INLINE.sub(lambda m: m.group(1) or "", line).strip()
        if line:
            out.append(line)
    return "\n".join(out)


def _check_stamps(stamps) -> list[tuple[int, float]]:
    """[(line, seconds)] with a non-negative int line and number; else ValueError."""
    out = []
    for item in stamps or []:
        try:
            line, seconds = item
        except (TypeError, ValueError):
            raise ValueError("Invalid note stamps") from None
        if (isinstance(line, bool) or not isinstance(line, int) or line < 0
                or isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                or seconds < 0):
            raise ValueError("Invalid note stamps")
        out.append((line, round(float(seconds), 1)))
    return out


@dataclass
class UserNotes:
    markdown: str
    stamps: list[tuple[int, float]]
    updated_at: str
```

Add `user_notes: UserNotes | None = None` as the last field of `StoredMeeting`, and in `get_meeting` pass `user_notes=self._user_notes(conn, meeting_id)`. Methods on `MeetingLibrary`:

```python
    def _user_notes(self, conn, meeting_id):
        row = conn.execute("SELECT * FROM user_notes WHERE meeting_id = ?",
                           (meeting_id,)).fetchone()
        if row is None:
            return None
        return UserNotes(row["markdown"], [tuple(s) for s in json.loads(row["stamps_json"])],
                         row["updated_at"])

    def get_user_notes(self, meeting_id: str) -> UserNotes | None:
        _check_id(meeting_id)
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)).fetchone() is None:
                raise MeetingNotFound(meeting_id)
            return self._user_notes(conn, meeting_id)

    def set_user_notes(self, meeting_id: str, markdown: str, stamps) -> UserNotes | None:
        _check_id(meeting_id)
        with self._transaction() as conn:
            self._touch(conn, meeting_id)
            return self._write_user_notes(conn, meeting_id, markdown, stamps)

    def _write_user_notes(self, conn, meeting_id, markdown, stamps) -> UserNotes | None:
        markdown = str(markdown)
        if len(markdown) > USER_NOTES_MAX_CHARS:
            raise ValueError("Notes are too long to save")
        checked = _check_stamps(stamps)
        if not markdown.strip():
            conn.execute("DELETE FROM user_notes WHERE meeting_id = ?", (meeting_id,))
            return None
        now = _now_iso()
        conn.execute(
            "INSERT INTO user_notes (meeting_id, markdown, text, stamps_json, updated_at)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT(meeting_id) DO UPDATE SET"
            " markdown = excluded.markdown, text = excluded.text,"
            " stamps_json = excluded.stamps_json, updated_at = excluded.updated_at",
            (meeting_id, markdown, notes_plain_text(markdown),
             json.dumps([list(s) for s in checked]), now))
        return UserNotes(markdown, checked, now)
```

- [ ] **Step 5: Search** — in `_search`, append user-notes hits after the notes hits:

```python
        hits += [
            SearchHit(r["id"], r["title"], r["started_at"], r["tz_offset_minutes"],
                      "user_notes", None, None, None, None, r["snip"], [], r["score"])
            for r in conn.execute(
                "SELECT m.id, m.title, m.started_at, m.tz_offset_minutes,"
                f" {snippet.format(t='user_notes_fts')} AS snip,"
                " bm25(user_notes_fts) AS score FROM user_notes_fts"
                " JOIN user_notes u ON u.id = user_notes_fts.rowid"
                " JOIN meetings m ON m.id = u.meeting_id"
                f" WHERE user_notes_fts MATCH ? AND {where} ORDER BY score LIMIT ?",
                (match, *params, fetch))
        ]
```

and in `search` change the ranking loop to `for kind in ("transcript", "notes", "user_notes"):` and the sort key's tiebreak to `{"transcript": 0, "notes": 1, "user_notes": 2}[h.kind]`.

- [ ] **Step 6: Run** — `.venv/bin/python -m pytest tests/test_user_notes.py tests/test_meeting_store.py tests/test_meeting_library.py -q` → PASS (update any existing assertion that pins schema version 4 to read `meeting_store.SCHEMA_VERSION`).

- [ ] **Step 7: Commit**

```bash
git add speakeasy/meeting_store.py speakeasy/meeting_library.py tests/test_user_notes.py tests/test_meeting_store.py
git commit -m "Library: user notes table (schema v5), plain-text search"
```

---

### Task 2: Recording draft and hand-off to the saved meeting

**Files:**
- Modify: `speakeasy/meeting_library.py` (`NoteDraft`, `DRAFT_EARLY_WINDOW`, `get_draft`, `set_draft`, `discard_draft`, `finish_draft`, `_adopt_stamps`, `save_meeting`)
- Test: `tests/test_note_draft.py` (new)

**Interfaces:**
- Consumes: `_write_user_notes`, `_check_stamps`, `utc_iso` (Task 1 / existing).
- Produces: `@dataclass NoteDraft(markdown: str, stamps: list[tuple[int, float]], started_at: str, updated_at: str)` — stamps are seconds since `started_at`; `get_draft() -> NoteDraft | None`; `set_draft(markdown, stamps, started_at: str) -> NoteDraft` (`started_at` is a UTC ISO string like `2026-10-02T13:00:00Z`); `discard_draft() -> None`; `finish_draft(meeting_id, markdown, stamps, started_at) -> UserNotes | None` (writes the given text to the meeting with stamps shifted, deletes the draft; raises `MeetingNotFound` and keeps the draft when the meeting is missing); `DRAFT_EARLY_WINDOW = timedelta(minutes=10)`.

- [ ] **Step 1: Failing tests** — `tests/test_note_draft.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from speakeasy.meeting_library import MeetingLibrary, MeetingNotFound, NewMeeting, utc_iso
from speakeasy.meetings import MeetingSegment

EDT = timezone(timedelta(hours=-4))
START = datetime(2026, 10, 2, 9, 0, 0, tzinfo=EDT)


def _new(started=START):
    return NewMeeting(segments=[MeetingSegment("You", 0.0, 4.0, "hello")],
                      duration_seconds=600.0, started_at=started)


def test_draft_round_trip_and_discard(library_path):
    lib = MeetingLibrary()
    assert lib.get_draft() is None
    lib.set_draft("hello", [(0, 3.0)], utc_iso(START))
    d = lib.get_draft()
    assert (d.markdown, d.stamps, d.started_at) == ("hello", [(0, 3.0)], utc_iso(START))
    lib.set_draft("hello again", [], utc_iso(START))
    assert lib.get_draft().markdown == "hello again"
    lib.discard_draft()
    assert lib.get_draft() is None


def test_draft_refuses_bad_input(library_path):
    lib = MeetingLibrary()
    with pytest.raises(ValueError, match="Notes are too long to save"):
        lib.set_draft("x" * 200_001, [], utc_iso(START))
    with pytest.raises(ValueError, match="Invalid note stamps"):
        lib.set_draft("x", [(0, -1)], utc_iso(START))


def test_save_meeting_adopts_a_draft_from_this_recording(library_path):
    lib = MeetingLibrary()
    lib.set_draft("first\n- second", [(0, 30.0), (1, 95.5)], utc_iso(START))
    mid = lib.save_meeting(_new())
    notes = lib.get_user_notes(mid)
    assert (notes.markdown, notes.stamps) == ("first\n- second", [(0, 30.0), (1, 95.5)])
    assert lib.get_draft() is None


def test_adoption_shifts_stamps_by_the_draft_start(library_path):
    lib = MeetingLibrary()
    lib.set_draft("late start", [(0, 10.0)], utc_iso(START + timedelta(seconds=20)))
    mid = lib.save_meeting(_new())
    assert lib.get_user_notes(mid).stamps == [(0, 30.0)]


def test_stamps_before_the_meeting_clamp_to_zero(library_path):
    lib = MeetingLibrary()
    lib.set_draft("early", [(0, 10.0)], utc_iso(START - timedelta(minutes=5)))
    mid = lib.save_meeting(_new())
    assert lib.get_user_notes(mid).stamps == [(0, 0.0)]


@pytest.mark.parametrize("offset", [timedelta(minutes=-11), timedelta(seconds=601)])
def test_draft_outside_window_is_left_alone(library_path, offset):
    lib = MeetingLibrary()
    lib.set_draft("other recording", [], utc_iso(START + offset))
    mid = lib.save_meeting(_new())
    assert lib.get_user_notes(mid) is None
    assert lib.get_draft().markdown == "other recording"


def test_blank_draft_is_cleared_without_notes(library_path):
    lib = MeetingLibrary()
    lib.set_draft("   ", [], utc_iso(START))
    mid = lib.save_meeting(_new())
    assert lib.get_user_notes(mid) is None and lib.get_draft() is None


def test_finish_draft_overwrites_with_latest_text(library_path):
    lib = MeetingLibrary()
    lib.set_draft("A", [(0, 30.0)], utc_iso(START))
    mid = lib.save_meeting(_new())
    lib.set_draft("stray", [], utc_iso(START))          # a debounced save that lost the race
    lib.finish_draft(mid, "A and B", [(0, 30.0), (1, 40.0)], utc_iso(START))
    notes = lib.get_user_notes(mid)
    assert (notes.markdown, notes.stamps) == ("A and B", [(0, 30.0), (1, 40.0)])
    assert lib.get_draft() is None


def test_finish_draft_for_a_missing_meeting_keeps_the_draft(library_path):
    lib = MeetingLibrary()
    lib.set_draft("keep me", [], utc_iso(START))
    with pytest.raises(MeetingNotFound):
        lib.finish_draft("20260101-000000-abcd", "keep me", [], utc_iso(START))
    assert lib.get_draft().markdown == "keep me"
```

- [ ] **Step 2: Run** — `.venv/bin/python -m pytest tests/test_note_draft.py -q` → FAIL (`AttributeError: 'MeetingLibrary' object has no attribute 'get_draft'`).

- [ ] **Step 3: Implement** in `speakeasy/meeting_library.py`:

```python
DRAFT_EARLY_WINDOW = timedelta(minutes=10)


@dataclass
class NoteDraft:
    markdown: str
    stamps: list[tuple[int, float]]   # seconds since started_at
    started_at: str                   # UTC ISO, the recording's start
    updated_at: str


def _adopt_stamps(stamps, drafted: datetime, meeting_start: datetime) -> list[tuple[int, float]]:
    """Re-base draft stamps (seconds since the draft's start) onto the meeting."""
    shift = (drafted - meeting_start).total_seconds()
    return [(line, max(0.0, round(s + shift, 1))) for line, s in stamps]
```

Methods:

```python
    def get_draft(self) -> NoteDraft | None:
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM note_draft WHERE id = 1").fetchone()
        if row is None:
            return None
        return NoteDraft(row["markdown"], [tuple(s) for s in json.loads(row["stamps_json"])],
                         row["started_at"], row["updated_at"])

    def set_draft(self, markdown: str, stamps, started_at: str) -> NoteDraft:
        markdown = str(markdown)
        if len(markdown) > USER_NOTES_MAX_CHARS:
            raise ValueError("Notes are too long to save")
        checked = _check_stamps(stamps)
        drafted = utc_iso(datetime.fromisoformat(str(started_at)))
        now = _now_iso()
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO note_draft (id, markdown, stamps_json, started_at, updated_at)"
                " VALUES (1, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET"
                " markdown = excluded.markdown, stamps_json = excluded.stamps_json,"
                " started_at = excluded.started_at, updated_at = excluded.updated_at",
                (markdown, json.dumps([list(s) for s in checked]), drafted, now))
        return NoteDraft(markdown, checked, drafted, now)

    def discard_draft(self) -> None:
        with self._transaction() as conn:
            conn.execute("DELETE FROM note_draft WHERE id = 1")

    def finish_draft(self, meeting_id: str, markdown: str, stamps, started_at: str) -> UserNotes | None:
        """The page's last word when the meeting it was drafting is saved:
        overwrite that meeting's notes with the editor's text and drop the draft."""
        _check_id(meeting_id)
        with self._transaction() as conn:
            m = conn.execute("SELECT started_at FROM meetings WHERE id = ?",
                             (meeting_id,)).fetchone()
            if m is None:
                raise MeetingNotFound(meeting_id)
            shifted = _adopt_stamps(_check_stamps(stamps),
                                    datetime.fromisoformat(str(started_at)),
                                    datetime.fromisoformat(m["started_at"]))
            self._touch(conn, meeting_id)
            notes = self._write_user_notes(conn, meeting_id, markdown, shifted)
            conn.execute("DELETE FROM note_draft WHERE id = 1")
            return notes
```

`save_meeting` adopts the draft in the same transaction:

```python
    def save_meeting(self, new: NewMeeting) -> str:
        with self._transaction() as conn:
            meeting_id = self._insert(conn, new)
            self._adopt_draft(conn, meeting_id, new)
            return meeting_id

    def _adopt_draft(self, conn, meeting_id: str, new: NewMeeting) -> None:
        """Attach the notes typed during this recording (engine.py stays untouched:
        the library is the one place every saved recording passes through)."""
        row = conn.execute("SELECT * FROM note_draft WHERE id = 1").fetchone()
        if row is None:
            return
        drafted = datetime.fromisoformat(row["started_at"])
        start = new.started_at
        if not (start - DRAFT_EARLY_WINDOW <= drafted
                <= start + timedelta(seconds=float(new.duration_seconds))):
            return
        stamps = _adopt_stamps([tuple(s) for s in json.loads(row["stamps_json"])], drafted, start)
        self._write_user_notes(conn, meeting_id, row["markdown"], stamps)
        conn.execute("DELETE FROM note_draft WHERE id = 1")
```

(`import_meetings` keeps calling `_insert` directly, so legacy imports never adopt a draft.)

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_note_draft.py tests/test_user_notes.py tests/test_meeting_library.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/meeting_library.py tests/test_note_draft.py
git commit -m "Library: recording note draft adopted by the saved meeting"
```

---

### Task 3: Claude (MCP), Copy and Export

**Files:**
- Modify: `speakeasy/mcp_tools.py` (`get_meeting` payload; `get_meeting` and `search_meetings` descriptions)
- Modify: `speakeasy/summary_format.py` (`SUMMARY_INSTRUCTIONS` rule)
- Modify: `packaging/mcpb/manifest.json` (mirror the two descriptions verbatim)
- Modify: `speakeasy/meeting_export.py` (`notes_section`, `render_export_md`)
- Modify: `speakeasy/ui/meetings_bridge.py` (`copy_payload` → `render_export_md`)
- Modify: `speakeasy/ui/meetings_window.py` (`.md` export → `render_export_md`)
- Test: `tests/test_mcp_tools.py`, `tests/test_meeting_export.py` (create if absent), `tests/test_meetings_bridge.py`

**Interfaces:**
- Consumes: `StoredMeeting.user_notes` (Task 1).
- Produces: MCP `get_meeting` field `user_notes: {"markdown": str, "stamps": [{"line": int, "at": "h:mm:ss"}], "updated_at": str} | None`; `meeting_export.format_elapsed(seconds) -> str` (`0:03`, `12:40`, `1:02:15`); `meeting_export.notes_section(stored) -> list[str]`.

- [ ] **Step 1: Failing tests.** Append to `tests/test_mcp_tools.py` (the file builds tools with `build_tools(lib)` and calls `.run(args)`):

```python
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
```

(`"at"` uses the module's existing `_hms`; if `_hms` formats differently, assert its actual `hh:mm:ss` output for 760 s.) Create/append `tests/test_meeting_export.py`:

```python
from datetime import datetime, timedelta, timezone

from speakeasy.meeting_export import format_elapsed, render_export_md
from speakeasy.meeting_library import MeetingLibrary, NewMeeting
from speakeasy.meetings import MeetingSegment


def test_format_elapsed():
    assert [format_elapsed(s) for s in (3, 760, 3735)] == ["0:03", "12:40", "1:02:15"]


def test_export_puts_my_notes_after_the_summary_with_stamps(library_path):
    lib = MeetingLibrary()
    mid = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0, 4, "hi there")], duration_seconds=900,
        started_at=datetime(2026, 10, 2, 9, 0, tzinfo=timezone(timedelta(hours=-4))),
        title="Planning"))
    lib.save_notes(mid, summary="TL;DR: planned.")
    lib.set_user_notes(mid, "## Plan\n- [ ] send deck\nplain", [(1, 760.0)])
    md = render_export_md(lib.get_meeting(mid))
    assert md.index("## Summary") < md.index("## My notes") < md.index("## Transcript")
    assert "## Plan\n- [ ] [12:40] send deck\nplain" in md
```

In `tests/test_meetings_bridge.py`, extend `test_copy_payload_registers_and_renders_via_injected_clipboard`: before copying, `lib.set_user_notes(mid, "remember the deck", [])`; after, `assert "## My notes" in captured[0] and "remember the deck" in captured[0]`.

- [ ] **Step 2: Run** — `.venv/bin/python -m pytest tests/test_mcp_tools.py tests/test_meeting_export.py tests/test_meetings_bridge.py -q` → FAIL (`KeyError: 'user_notes'`, `ImportError: format_elapsed`).

- [ ] **Step 3: Implement.**

`speakeasy/meeting_export.py` (add `NOTES_LINE_PREFIX` to its `from .meeting_library import …` line):

```python
def format_elapsed(seconds: float) -> str:
    """0:03, 12:40, 1:02:15 — matches the page's formatElapsed."""
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, ss = divmod(rem, 60)
    return f"{h}:{m:02d}:{ss:02d}" if h else f"{m}:{ss:02d}"


def notes_section(stored) -> list[str]:
    """'## My notes' with each stamped line prefixed '[12:40] ' after its list marker."""
    notes = getattr(stored, "user_notes", None)
    if not notes or not notes.markdown.strip():
        return []
    stamps = dict(notes.stamps)
    lines = []
    for i, line in enumerate(notes.markdown.split("\n")):
        if i in stamps:
            m = NOTES_LINE_PREFIX.match(line)
            cut = m.end() if m else 0
            line = f"{line[:cut]}[{format_elapsed(stamps[i])}] {line[cut:]}"
        lines.append(line)
    return ["## My notes", "", *lines, ""]
```

In `render_export_md`, after the action-items block and before tags: `extra += notes_section(stored)`.

`speakeasy/ui/meetings_bridge.py` `copy_payload`: `self._set_clipboard(render_export_md(meeting))` (import `from speakeasy.meeting_export import render_export_md`). `speakeasy/ui/meetings_window.py` `_export`: for `.md` paths use `meeting_export.render_export_md`; `.txt` keeps `meetings.render_txt`. (Ruling recorded in Execution notes: Copy and Markdown export now carry summary, action items, My notes and transcript, as the spec's "Copy / Export add My notes" requires.)

`speakeasy/mcp_tools.py` `get_meeting`: add

```python
        un = m.user_notes
        user_notes = None if un is None else {
            "markdown": un.markdown,
            "stamps": [{"line": line, "at": _hms(s)} for line, s in un.stamps],
            "updated_at": un.updated_at}
```

and `"user_notes": user_notes` in the returned dict. Descriptions (copy into `manifest.json` word for word):
- `get_meeting`: `"Get one meeting's details: the user's own notes (user_notes; read these first, they show what mattered to the user), Claude's notes (summary, action items), tags, people and calendar event. No transcript: use get_transcript for that."`
- `search_meetings`: `"Full-text search across transcripts, summaries and the user's own notes (kind user_notes). Returns ranked snippets (matches in **bold**) with the meeting id and time offset. Meeting titles aren't searched: use list_meetings with title."`

`speakeasy/summary_format.py` `SUMMARY_INSTRUCTIONS`, add to Rules:

```
- If get_meeting returns user_notes, read them first: what the user wrote
  down is what mattered. Build the summary on them; don't copy them back word
  for word. Their checklist items stay theirs: don't repeat them as action items.
```

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_mcp_tools.py tests/test_meeting_export.py tests/test_meetings_bridge.py tests/test_mcpb.py tests/test_summary_format.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/mcp_tools.py speakeasy/summary_format.py packaging/mcpb/manifest.json speakeasy/meeting_export.py speakeasy/meeting_library.py speakeasy/ui/meetings_bridge.py speakeasy/ui/meetings_window.py tests
git commit -m "Notes for Claude, Copy and Export"
```

---

### Task 4: Bridge calls and recording state

**Files:**
- Modify: `speakeasy/ui/meetings_bridge.py` (constructor `recording_info`, handlers, `register`, `get_payload`)
- Modify: `speakeasy/ui/meetings_window.py` (`recording_info` wiring, `meetingSaved_` emits `meetings.saved`)
- Test: `tests/test_meetings_bridge.py`

**Interfaces:**
- Consumes: Task 1–2 library API.
- Produces bridge methods: `notes.user.set {id, markdown, stamps}` → `{"updatedAt": str | None}`; `notes.draft.get` → `{"markdown", "stamps", "startedAt"} | None`; `notes.draft.set {markdown, stamps, startedAt}` → `{"updatedAt"}`; `notes.draft.discard` → `True`; `notes.draft.finish {id, markdown, stamps, startedAt}` → `{"updatedAt": str | None}`; `recording.get` → `{"recording": bool, "processing": bool, "startedAt": str | None, "title": str | None}`. `meetings.get` gains `"hasUserNotes": bool` and `"userNotes": {"markdown": str, "stamps": [[line, seconds], ...]}`. Search results keep `"kind"` (now possibly `"user_notes"`). Event `meetings.saved {id}` emitted after `meetings.changed`.
- `MeetingsBridge(..., recording_info=None)`: a zero-argument callable returning the `recording.get` dict; default returns not recording.

- [ ] **Step 1: Failing tests** — append to `tests/test_meetings_bridge.py`:

```python
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
        "recording": False, "processing": False, "startedAt": None, "title": None}
    info = {"recording": True, "processing": False, "startedAt": "2026-09-24T17:17:00Z", "title": "1:1"}
    assert _bcall(MeetingsBridge(recording_info=lambda: info), "recording.get")["r"] == info


def test_search_reports_user_notes_kind(library_path):
    lib, mid, bridge, d = _setup(library_path)
    lib.set_user_notes(mid, "vendor contract", [])
    assert [r["kind"] for r in bridge.search_payload({"query": "vendor"})] == ["user_notes"]
```

- [ ] **Step 2: Run** — `.venv/bin/python -m pytest tests/test_meetings_bridge.py -q` → FAIL (`KeyError: 'hasUserNotes'`, unknown methods).

- [ ] **Step 3: Implement** in `speakeasy/ui/meetings_bridge.py`:

```python
_NOT_RECORDING = {"recording": False, "processing": False, "startedAt": None, "title": None}
```

Constructor: add `recording_info=None` parameter; `self._recording_info = recording_info or (lambda: dict(_NOT_RECORDING))`. Handlers:

```python
    def notes_set_payload(self, params) -> dict:
        notes = self.library.set_user_notes(str(params.get("id", "")),
                                            str(params.get("markdown", "")),
                                            params.get("stamps") or [])
        return {"updatedAt": notes.updated_at if notes else None}

    def draft_get_payload(self, params) -> dict | None:
        d = self.library.get_draft()
        return None if d is None else {"markdown": d.markdown,
                                       "stamps": [list(s) for s in d.stamps],
                                       "startedAt": d.started_at}

    def draft_set_payload(self, params) -> dict:
        d = self.library.set_draft(str(params.get("markdown", "")), params.get("stamps") or [],
                                   str(params.get("startedAt", "")))
        return {"updatedAt": d.updated_at}

    def draft_discard_payload(self, params) -> bool:
        self.library.discard_draft()
        return True

    def draft_finish_payload(self, params) -> dict:
        notes = self.library.finish_draft(str(params.get("id", "")),
                                          str(params.get("markdown", "")),
                                          params.get("stamps") or [],
                                          str(params.get("startedAt", "")))
        return {"updatedAt": notes.updated_at if notes else None}

    def recording_payload(self, params) -> dict:
        return {**_NOT_RECORDING, **(self._recording_info() or {})}
```

Register `"notes.user.set"`, `"notes.draft.get"`, `"notes.draft.set"`, `"notes.draft.discard"`, `"notes.draft.finish"`, `"recording.get"`. (`datetime.fromisoformat("")` raises `ValueError`, which `_wrap` already turns into an error response.) In `get_payload`'s `detail.update`, add:

```python
            "hasUserNotes": bool(m.user_notes),
            "userNotes": {"markdown": m.user_notes.markdown if m.user_notes else "",
                          "stamps": [list(s) for s in m.user_notes.stamps] if m.user_notes else []},
```

`speakeasy/ui/meetings_window.py`: beside `recording_event_key`, add

```python
        def recording_info():
            # Read-only view of the engine for the notepad; engine.py is not
            # changed. _meeting_started_at is private, so read it defensively;
            # the page falls back to the time it first saw recording on.
            if engine is None:
                return None
            state = getattr(engine.state, "value", "")
            event = engine.meeting_event
            started = getattr(engine, "_meeting_started_at", None)
            return {"recording": state == "meeting_recording",
                    "processing": state == "meeting_processing",
                    "startedAt": utc_iso(started) if started is not None else None,
                    "title": event.title if event is not None else None}
```

(import `utc_iso` from `speakeasy.meeting_library`), pass `recording_info=recording_info` to `MeetingsBridge`, and make `meetingSaved_` emit `self._web.emit("meetings.saved", {"id": str(meeting_id)})` after the existing `meetings.changed`.

- [ ] **Step 4: Run** — `.venv/bin/python -m pytest tests/test_meetings_bridge.py tests/test_webbridge.py -q` → PASS; then the full suite once (`timeout: 600000`).

- [ ] **Step 5: Commit**

```bash
git add speakeasy/ui/meetings_bridge.py speakeasy/ui/meetings_window.py tests/test_meetings_bridge.py
git commit -m "Bridge: notes, draft and recording-state calls"
```

---

### Task 5: Editor dependency, Markdown converter, offline check

**Files:**
- Modify: `frontend/package.json`, `frontend/package-lock.json` (TipTap, exact `3.31.4`; build script runs the offline check)
- Create: `frontend/src/meetings/notesMarkdown.ts`, `frontend/tests/notesMarkdown.test.ts`
- Create: `frontend/scripts/check-offline.mjs`, `frontend/tests/checkOffline.test.ts`

**Interfaces:**
- Produces (`notesMarkdown.ts`, pure, no non-type imports):
  - `type Stamp = [number, number]` (line, seconds)
  - `interface NotesValue { markdown: string; stamps: Stamp[] }`
  - `interface DocNode { type: string; attrs?: Record<string, unknown>; content?: DocNode[]; text?: string; marks?: { type: string }[] }` (TipTap `JSONContent`-compatible)
  - `docToNotes(doc: DocNode): NotesValue`
  - `notesToDoc(value: NotesValue): DocNode`
  - `stripStamps(doc: DocNode): DocNode`
  - `STAMPED_TYPES = ['paragraph', 'heading', 'listItem', 'taskItem']`
- Produces (`check-offline.mjs`): `export function findRemoteLoads(dir: string): string[]`; run directly it exits 1 and prints offenders when `dist/` loads anything remote.

Markdown format (one line per block; line index = stamp line): `# ` / `## ` headings; `- ` bullets; `1. ` numbered (first number is the list's start); `- [ ] ` / `- [x] ` checklist; nesting by two spaces per level; empty line = empty paragraph; `**bold**`, `*italic*`, `***both***`; `\` escapes `\`, `*`, and a whole paragraph line that would otherwise read as a marker or starts with whitespace (prefix `\`). Extra paragraphs inside one list item are joined to the item's text with a space.

- [ ] **Step 1: Install** — `npm --prefix frontend install --save-exact @tiptap/core@3.31.4 @tiptap/pm@3.31.4 @tiptap/react@3.31.4 @tiptap/starter-kit@3.31.4 @tiptap/extension-list@3.31.4`. Confirm `frontend/package.json` lists them under `dependencies` without `^`.

- [ ] **Step 2: Failing converter tests** — `frontend/tests/notesMarkdown.test.ts`:

```ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { docToNotes, notesToDoc, stripStamps, type DocNode } from '../src/meetings/notesMarkdown.ts';

const SAMPLES = [
  '',
  'plain line',
  '# Title\n## Sub\nbody',
  '- one\n- two\n  - nested\n- three',
  '3. third\n4. fourth',
  '- [ ] todo\n- [x] done',
  '**bold** and *italic* and ***both***',
  '**a*b*** tail',
  'first\n\nafter blank',
  '\\- not a list\n\\# not a heading\n\\  indented',
  'stars \\* and slash \\\\',
  '- item with **bold**\n  1. inner number\n  - [ ] inner task',
];

test('canonical markdown round-trips', () => {
  for (const md of SAMPLES) {
    assert.equal(docToNotes(notesToDoc({ markdown: md, stamps: [] })).markdown, md, md);
  }
});

test('stamps stay on their lines', () => {
  const md = '# Plan\n- send deck\n  - to Alex\nwrap up';
  const stamps: [number, number][] = [[0, 3], [2, 40.5], [3, 61]];
  assert.deepEqual(docToNotes(notesToDoc({ markdown: md, stamps })), { markdown: md, stamps });
});

test('document shape', () => {
  const doc = notesToDoc({ markdown: '## H\n- [x] **done**', stamps: [[1, 9]] });
  assert.equal(doc.content![0].type, 'heading');
  assert.equal(doc.content![0].attrs!.level, 2);
  const item = doc.content![1].content![0];
  assert.equal(doc.content![1].type, 'taskList');
  assert.deepEqual([item.type, item.attrs!.checked, item.attrs!.stamp], ['taskItem', true, 9]);
  assert.deepEqual(item.content![0].content![0], { type: 'text', text: 'done', marks: [{ type: 'bold' }] });
});

test('text with markdown characters is escaped', () => {
  const doc: DocNode = { type: 'doc', content: [
    { type: 'paragraph', content: [{ type: 'text', text: '- a * b \\ c' }] },
    { type: 'paragraph', content: [{ type: 'text', text: '2. not numbered' }] },
  ] };
  const { markdown } = docToNotes(doc);
  assert.equal(markdown, '\\- a \\* b \\\\ c\n\\2. not numbered');
  assert.deepEqual(notesToDoc({ markdown, stamps: [] }).content![0].content![0].text, '- a * b \\ c');
});

test('unknown nodes and marks become plain text', () => {
  const doc: DocNode = { type: 'doc', content: [
    { type: 'blockquote', content: [{ type: 'paragraph', content: [{ type: 'text', text: 'quoted', marks: [{ type: 'link' }] }] }] },
    { type: 'paragraph', content: [{ type: 'text', text: 'x', marks: [{ type: 'strike' }] }] },
  ] };
  assert.equal(docToNotes(doc).markdown, 'quoted\nx');
});

test('stripStamps clears every stamp', () => {
  const doc = notesToDoc({ markdown: 'a\n- b', stamps: [[0, 1], [1, 2]] });
  assert.deepEqual(docToNotes(stripStamps(doc)).stamps, []);
});
```

- [ ] **Step 3: Run** — `npm --prefix frontend test` → FAIL (`Cannot find module …/notesMarkdown.ts`).

- [ ] **Step 4: Implement** `frontend/src/meetings/notesMarkdown.ts`:

```ts
// The notepad's storage format: Markdown with one line per block, so a stamp's
// line index is simply its line number. Pure: runs under node --test.

export type Stamp = [number, number];
export interface NotesValue { markdown: string; stamps: Stamp[] }
export interface DocNode {
  type: string;
  attrs?: Record<string, unknown>;
  content?: DocNode[];
  text?: string;
  marks?: { type: string }[];
}

export const STAMPED_TYPES = ['paragraph', 'heading', 'listItem', 'taskItem'];
const LIST_TYPES = ['bulletList', 'orderedList', 'taskList'];
const MARKER = /^(\s|#{1,2} |[-*] |\d+\. |\\)/;

function escapeText(text: string): string {
  return text.replace(/[\\*]/g, (c) => `\\${c}`);
}

function inlineMarkdown(nodes: DocNode[] = []): string {
  let out = '';
  let bold = false;
  let italic = false;
  const toggle = (b: boolean, i: boolean) => {
    const db = b !== bold;
    const di = i !== italic;
    out += db && di ? '***' : db ? '**' : di ? '*' : '';
    bold = b;
    italic = i;
  };
  for (const node of nodes) {
    if (node.type !== 'text' || !node.text) continue;
    const marks = (node.marks ?? []).map((m) => m.type);
    toggle(marks.includes('bold'), marks.includes('italic'));
    out += escapeText(node.text);
  }
  toggle(false, false);
  return out;
}

function stampOf(node: DocNode): number | null {
  const s = node.attrs?.stamp;
  return typeof s === 'number' ? s : null;
}

function itemText(item: DocNode): string {
  return (item.content ?? [])
    .filter((c) => !LIST_TYPES.includes(c.type))
    .map((c) => inlineMarkdown(c.content))
    .filter((t) => t !== '')
    .join(' ');
}

export function docToNotes(doc: DocNode): NotesValue {
  const lines: string[] = [];
  const stamps: Stamp[] = [];
  const push = (line: string, node: DocNode) => {
    const s = stampOf(node);
    if (s !== null) stamps.push([lines.length, s]);
    lines.push(line);
  };
  const list = (node: DocNode, indent: string) => {
    let n = Number(node.attrs?.start ?? 1);
    for (const item of node.content ?? []) {
      const marker = node.type === 'orderedList' ? `${n++}. `
        : node.type === 'taskList' ? (item.attrs?.checked ? '- [x] ' : '- [ ] ') : '- ';
      push(indent + marker + itemText(item), item);
      for (const child of item.content ?? []) {
        if (LIST_TYPES.includes(child.type)) list(child, indent + '  ');
      }
    }
  };
  const block = (node: DocNode) => {
    if (node.type === 'heading') {
      push('#'.repeat(node.attrs?.level === 1 ? 1 : 2) + ' ' + inlineMarkdown(node.content), node);
    } else if (LIST_TYPES.includes(node.type)) {
      list(node, '');
    } else if (node.type === 'paragraph') {
      const text = inlineMarkdown(node.content);
      push(MARKER.test(text) ? `\\${text}` : text, node);
    } else {
      for (const child of node.content ?? []) block(child);   // unknown wrapper: keep its blocks
    }
  };
  for (const node of doc.content ?? []) block(node);
  if (lines.length === 1 && lines[0] === '' && stamps.length === 0) return { markdown: '', stamps };
  return { markdown: lines.join('\n'), stamps };
}

function parseInline(src: string): DocNode[] {
  const out: DocNode[] = [];
  let bold = false;
  let italic = false;
  let buf = '';
  const flush = () => {
    if (!buf) return;
    const marks = [...(bold ? [{ type: 'bold' }] : []), ...(italic ? [{ type: 'italic' }] : [])];
    out.push(marks.length ? { type: 'text', text: buf, marks } : { type: 'text', text: buf });
    buf = '';
  };
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (c === '\\' && i + 1 < src.length) {
      buf += src[++i];
    } else if (c === '*') {
      let n = 1;
      while (src[i + n] === '*' && n < 3) n++;
      flush();
      if (n === 3) { bold = !bold; italic = !italic; } else if (n === 2) bold = !bold; else italic = !italic;
      i += n - 1;
    } else {
      buf += c;
    }
  }
  flush();
  return out;
}

const LIST_LINE = /^( *)(?:(- \[( |x|X)\] )|([-*] )|(\d+)\. )(.*)$/;

export function notesToDoc({ markdown, stamps }: NotesValue): DocNode {
  const stampAt = new Map(stamps);
  const stamp = (i: number) => stampAt.get(i) ?? null;
  const content: DocNode[] = [];
  const stack: { level: number; type: string; node: DocNode }[] = [];
  const lines = markdown === '' ? [''] : markdown.split('\n');
  lines.forEach((line, i) => {
    const m = line.startsWith('\\') ? null : LIST_LINE.exec(line);
    if (!m) {
      stack.length = 0;
      const h = line.startsWith('\\') ? null : /^(#{1,2}) (.*)$/.exec(line);
      if (h) {
        content.push({ type: 'heading', attrs: { level: h[1].length, stamp: stamp(i) },
          content: parseInline(h[2]) });
      } else {
        const text = parseInline(line);
        content.push({ type: 'paragraph', attrs: { stamp: stamp(i) }, ...(text.length ? { content: text } : {}) });
      }
      return;
    }
    const type = m[2] ? 'taskList' : m[4] ? 'bulletList' : 'orderedList';
    let level = Math.floor(m[1].length / 2);
    while (stack.length && stack[stack.length - 1].level > level) stack.pop();
    let top = stack[stack.length - 1];
    if (top && top.level === level && top.type !== type) { stack.pop(); top = stack[stack.length - 1]; }
    const para = { type: 'paragraph', ...(parseInline(m[6]).length ? { content: parseInline(m[6]) } : {}) };
    const item: DocNode = type === 'taskList'
      ? { type: 'taskItem', attrs: { checked: m[3] !== ' ', stamp: stamp(i) }, content: [para] }
      : { type: 'listItem', attrs: { stamp: stamp(i) }, content: [para] };
    if (top && top.level === level) {
      top.node.content!.push(item);
      return;
    }
    const list: DocNode = { type, ...(type === 'orderedList' ? { attrs: { start: Number(m[5]) } } : {}), content: [item] };
    if (top) {
      const parent = top.node.content![top.node.content!.length - 1];
      parent.content!.push(list);
      level = top.level + 1;
    } else {
      content.push(list);
      level = 0;
    }
    stack.push({ level, type, node: list });
  });
  return { type: 'doc', content };
}

export function stripStamps(doc: DocNode): DocNode {
  const walk = (n: DocNode): DocNode => ({
    ...n,
    ...(n.attrs && 'stamp' in n.attrs ? { attrs: { ...n.attrs, stamp: null } } : {}),
    ...(n.content ? { content: n.content.map(walk) } : {}),
  });
  return walk(doc);
}
```

Run `npm --prefix frontend test` → converter tests PASS. If a SAMPLES case fails, fix the converter, not the sample (the samples are the canonical format).

- [ ] **Step 5: Offline check (test first)** — `frontend/tests/checkOffline.test.ts`:

```ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { findRemoteLoads } from '../scripts/check-offline.mjs';

function dist(files: Record<string, string>): string {
  const dir = mkdtempSync(join(tmpdir(), 'offline-'));
  mkdirSync(join(dir, 'assets'));
  for (const [name, body] of Object.entries(files)) writeFileSync(join(dir, name), body);
  return dir;
}

test('local bundle passes, documentation URLs in strings are fine', () => {
  const dir = dist({ 'index.html': '<script type="module" src="/assets/a.js"></script>',
    'assets/a.js': 'throw Error("see https://react.dev/errors/1")', 'assets/a.css': 'a{background:url(/x.png)}' });
  assert.deepEqual(findRemoteLoads(dir), []);
});

test('remote script, stylesheet, font and dynamic import are caught', () => {
  const dir = dist({
    'index.html': '<script src="https://cdn.example/x.js"></script><link rel="stylesheet" href="http://f.example/a.css">',
    'assets/a.css': '@import url("https://fonts.example/f.css"); b{src:url(https://f.example/x.woff2)}',
    'assets/a.js': 'import("https://esm.example/m.js")',
  });
  assert.equal(findRemoteLoads(dir).length, 5);
});
```

`frontend/scripts/check-offline.mjs`:

```js
// Fails the build when the bundle would load anything from the network
// (Speakeasy must run fully offline). URLs inside ordinary strings, such as
// React's error-docs links, are not loads and are allowed.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const PATTERNS = {
  '.html': [/<script[^>]+src=["']?https?:/gi, /<link[^>]+href=["']?https?:/gi],
  '.css': [/@import\s+(?:url\()?\s*["']?https?:/gi, /url\(\s*["']?https?:/gi],
  '.js': [/import\(\s*["']https?:/gi, /importScripts\(\s*["']https?:/gi],
};

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });
}

export function findRemoteLoads(dir) {
  const out = [];
  for (const path of files(dir)) {
    const ext = Object.keys(PATTERNS).find((e) => path.endsWith(e));
    if (!ext) continue;
    const text = readFileSync(path, 'utf8');
    for (const re of PATTERNS[ext]) for (const m of text.matchAll(re)) out.push(`${path}: ${m[0]}`);
  }
  return out;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const found = findRemoteLoads(new URL('../dist', import.meta.url).pathname);
  if (found.length) {
    console.error('Remote loads in the bundle (must be offline):\n' + found.join('\n'));
    process.exit(1);
  }
}
```

(The `@import url("https://…")` line matches both CSS patterns — count 2 — plus `url(https://…woff2)` 1, the script 1, the link 1, the dynamic import 1: if the count differs from 5 because of that overlap, change the test's expected number to the real distinct count and say why in the report; the behaviour that matters is that each kind is caught.) Set `"build": "tsc --noEmit && vite build && node scripts/check-offline.mjs"` in `frontend/package.json`. If `tsc` rejects the `.mjs` import in the test, add `frontend/scripts/check-offline.d.mts` declaring `export function findRemoteLoads(dir: string): string[];`.

- [ ] **Step 6: Run** — `npm --prefix frontend test` → PASS; `npm --prefix frontend run build` → succeeds (the offline check prints nothing).

- [ ] **Step 7: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/meetings/notesMarkdown.ts frontend/tests frontend/scripts
git commit -m "Notepad: TipTap (pinned, bundled), Markdown converter, offline build check"
```

---

### Task 6: Notes tab with the editor and autosave

**Files:**
- Create: `frontend/src/meetings/NotesEditor.tsx`, `frontend/src/meetings/NotesEditor.module.css`, `frontend/src/meetings/notesStamps.ts`
- Modify: `frontend/src/mock/meetings.ts` (types, mock notes), `frontend/src/meetings/MeetingDetail.tsx` (third tab, default tab, stamp jump), `frontend/src/meetings/MeetingDetail.module.css`, `frontend/src/meetings/App.tsx` (save callback, mock state `notes`)

**Interfaces:**
- Consumes: `notesToDoc`, `docToNotes`, `STAMPED_TYPES`, `NotesValue`, `Stamp` (Task 5); bridge `notes.user.set` and `meetings.get` fields `hasUserNotes`, `userNotes` (Task 4).
- Produces:
  - `MeetingDetail` type gains `hasUserNotes: boolean; userNotes: NotesValue` (`NotesValue` re-exported from `mock/meetings.ts` as a type).
  - `notesStamps.ts`: `export const Stamps = Extension.create<{ now: () => number | null; onStampClick: ((seconds: number) => void) | null }>(…)` — global `stamp` attribute (`default: null`, `keepOnSplit: false`, `rendered: false`) on `STAMPED_TYPES`; an `appendTransaction` that stamps the cursor's block when `now()` is not null, the block is unstamped and non-empty, and the transaction is not `preventUpdate`; widget decorations rendering each stamp in the gutter (a `<button class="notes-stamp">` when `onStampClick` is set, else `<span class="notes-stamp">`; label `formatElapsed(seconds)` from `transcriptTurns.ts`). For a paragraph whose parent is a `listItem`/`taskItem`, the item is the block (the paragraph is never stamped).
  - `NotesEditor` props: `{ initial: NotesValue; save: (value: NotesValue) => Promise<void>; now?: () => number | null; onStampClick?: (seconds: number) => void; notice?: ReactNode }`. Status line strings: "Saving…", "Saved", "Couldn't save — retrying", "Notes are too long to save".
  - `MeetingDetail` props gain `onSaveNotes: (id: string, value: NotesValue) => Promise<void>`; `Tab` becomes `'summary' | 'transcript' | 'notes'`; `JumpTarget.kind` accepts `'user_notes'`.

- [ ] **Step 1: Types and mock.** In `mock/meetings.ts`: `export interface NotesValue { markdown: string; stamps: [number, number][] }` (identical shape to `notesMarkdown.ts`; keep both, `notesMarkdown.ts` must stay import-free); `MeetingDetail` gains `hasUserNotes: boolean; userNotes: NotesValue`; every `MOCK_DETAILS` entry gets `hasUserNotes: false, userNotes: { markdown: '', stamps: [] }` except the default meeting, which gets `hasUserNotes: true` and `userNotes: { markdown: '## Before Friday\n- [ ] Send the revised deck to **Priya**\n- [x] Book the review room\nOffline sync is now *P1*', stamps: [[1, 754], [3, 1210]] }`. `SearchResult.kind` becomes `'transcript' | 'notes' | 'user_notes'`; add one `MOCK_RESULTS` entry with `kind: 'user_notes'`, `speaker: null`, `seconds: null`, `segmentIndex: null`, `parts` containing a hit on "deck".

- [ ] **Step 2: `notesStamps.ts`.**

```ts
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import type { Node as PMNode } from '@tiptap/pm/model';
import { STAMPED_TYPES } from './notesMarkdown';
import { formatElapsed } from './transcriptTurns';

interface StampOptions { now: () => number | null; onStampClick: ((seconds: number) => void) | null }
const ITEM_TYPES = ['listItem', 'taskItem'];

function isLine(node: PMNode, parent: PMNode | null): boolean {
  if (!STAMPED_TYPES.includes(node.type.name)) return false;
  return !(node.type.name === 'paragraph' && parent && ITEM_TYPES.includes(parent.type.name));
}

export const Stamps = Extension.create<StampOptions>({
  name: 'stamps',
  addOptions() {
    return { now: () => null, onStampClick: null };
  },
  addGlobalAttributes() {
    return [{ types: STAMPED_TYPES, attributes: { stamp: { default: null, keepOnSplit: false, rendered: false } } }];
  },
  addProseMirrorPlugins() {
    const options = this.options;
    return [new Plugin({
      key: new PluginKey('stamps'),
      appendTransaction(trs, _old, state) {
        if (!trs.some((t) => t.docChanged && !t.getMeta('preventUpdate'))) return null;
        const now = options.now();
        if (now === null) return null;
        const $from = state.selection.$from;
        for (let d = $from.depth; d > 0; d--) {
          const node = $from.node(d);
          if (!isLine(node, d > 1 ? $from.node(d - 1) : null)) continue;
          if (node.attrs.stamp !== null || node.textContent.trim() === '') return null;
          return state.tr.setNodeAttribute($from.before(d), 'stamp', Math.round(now * 10) / 10);
        }
        return null;
      },
      props: {
        decorations(state) {
          const widgets: Decoration[] = [];
          state.doc.descendants((node, pos, parent) => {
            if (!isLine(node, parent) || typeof node.attrs.stamp !== 'number') return;
            const seconds = node.attrs.stamp as number;
            widgets.push(Decoration.widget(pos + 1, () => {
              const el = document.createElement(options.onStampClick ? 'button' : 'span');
              el.className = 'notes-stamp';
              el.textContent = formatElapsed(seconds);
              el.contentEditable = 'false';
              if (options.onStampClick) {
                el.setAttribute('type', 'button');
                el.title = 'Show in transcript';
                el.addEventListener('mousedown', (e) => e.preventDefault());
                el.addEventListener('click', () => options.onStampClick?.(seconds));
              }
              return el;
            }, { side: -1, ignoreSelection: true, key: `stamp-${pos}-${seconds}` }));
          });
          return DecorationSet.create(state.doc, widgets);
        },
      },
    })];
  },
});
```

(If `setNodeAttribute` is missing in the installed `@tiptap/pm`, use `state.tr.setNodeMarkup(pos, undefined, { ...node.attrs, stamp })`.)

- [ ] **Step 3: `NotesEditor.tsx`.**

```tsx
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { EditorContent, useEditor, useEditorState } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { TaskItem, TaskList } from '@tiptap/extension-list';
import { docToNotes, notesToDoc, type NotesValue } from './notesMarkdown';
import { Stamps } from './notesStamps';
import styles from './NotesEditor.module.css';

const SAVE_DELAY_MS = 500;
const RETRY_MS = 5000;
type Status = 'idle' | 'saving' | 'saved' | 'error' | 'tooLong';

interface NotesEditorProps {
  initial: NotesValue;
  save: (value: NotesValue) => Promise<void>;
  now?: () => number | null;
  onStampClick?: (seconds: number) => void;
  notice?: ReactNode;
}

export function NotesEditor({ initial, save, now, onStampClick, notice }: NotesEditorProps) {
  const [status, setStatus] = useState<Status>('idle');
  const timer = useRef<number | null>(null);
  const pending = useRef<NotesValue | null>(null);
  const saveRef = useRef(save);
  saveRef.current = save;

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: { levels: [1, 2] },
        blockquote: false, code: false, codeBlock: false, horizontalRule: false,
        strike: false, underline: false, link: false, hardBreak: false,
      }),
      TaskList,
      TaskItem.configure({ nested: true }),
      Stamps.configure({ now: now ?? (() => null), onStampClick: onStampClick ?? null }),
    ],
    content: notesToDoc(initial),
    editorProps: { attributes: { class: styles.prose, 'aria-label': 'Notes' } },
    onUpdate: ({ editor: e }) => schedule(docToNotes(e.getJSON())),
  });

  const flush = () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
    const value = pending.current;
    if (value === null) return;
    setStatus('saving');
    saveRef.current(value).then(
      () => { if (pending.current === value) { pending.current = null; setStatus('saved'); } },
      (err: Error) => {
        if (String(err?.message).includes('Notes are too long to save')) { setStatus('tooLong'); return; }
        setStatus('error');
        timer.current = window.setTimeout(flush, RETRY_MS);
      },
    );
  };

  function schedule(value: NotesValue) {
    pending.current = value;
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(flush, SAVE_DELAY_MS);
  }

  useEffect(() => {
    const onHide = () => flush();
    window.addEventListener('pagehide', onHide);
    return () => { window.removeEventListener('pagehide', onHide); flush(); };
  }, []);

  const active = useEditorState({
    editor,
    selector: ({ editor: e }) => ({
      bold: e?.isActive('bold') ?? false, italic: e?.isActive('italic') ?? false,
      h1: e?.isActive('heading', { level: 1 }) ?? false, h2: e?.isActive('heading', { level: 2 }) ?? false,
      bullet: e?.isActive('bulletList') ?? false, ordered: e?.isActive('orderedList') ?? false,
      task: e?.isActive('taskList') ?? false,
    }),
  });

  const label = { idle: '', saving: 'Saving…', saved: 'Saved', error: 'Couldn\'t save — retrying',
    tooLong: 'Notes are too long to save' }[status];
  const button = (key: keyof NonNullable<typeof active>, title: string, run: () => void, body: ReactNode) => (
    <button type="button" className={active?.[key] ? `${styles.tool} ${styles.toolOn}` : styles.tool}
      aria-label={title} title={title} aria-pressed={active?.[key] ?? false}
      onMouseDown={(e) => e.preventDefault()} onClick={run}>{body}</button>
  );
  const chain = () => editor!.chain().focus();

  return (
    <div className={styles.notes}>
      <div className={styles.bar} role="toolbar" aria-label="Formatting">
        {button('bold', 'Bold', () => chain().toggleBold().run(), <b>B</b>)}
        {button('italic', 'Italic', () => chain().toggleItalic().run(), <i>I</i>)}
        {button('h1', 'Heading 1', () => chain().toggleHeading({ level: 1 }).run(), 'H1')}
        {button('h2', 'Heading 2', () => chain().toggleHeading({ level: 2 }).run(), 'H2')}
        {button('bullet', 'Bulleted list', () => chain().toggleBulletList().run(),
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 4h7.5M6 8h7.5M6 12h7.5"/><circle cx="3" cy="4" r="0.9"/><circle cx="3" cy="8" r="0.9"/><circle cx="3" cy="12" r="0.9"/></svg>)}
        {button('ordered', 'Numbered list', () => chain().toggleOrderedList().run(),
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6.5 4h7M6.5 8h7M6.5 12h7M2.5 3l1-.5V5.5M2.3 7.3c.4-.6 1.7-.5 1.7.3 0 .7-1.7 1.4-1.7 2.3H4"/></svg>)}
        {button('task', 'Checklist', () => chain().toggleTaskList().run(),
          <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2" y="2.5" width="4" height="4" rx="1"/><path d="M8.5 4.5h5M8.5 11.5h5"/><rect x="2" y="9.5" width="4" height="4" rx="1"/><path d="M2.8 11.4l.9.9 1.6-1.8"/></svg>)}
        <span className={styles.spacer} />
        <span className={status === 'error' || status === 'tooLong' ? styles.statusError : styles.status} role="status">{label}</span>
      </div>
      {notice}
      <EditorContent editor={editor} />
    </div>
  );
}
```

`NotesEditor.module.css` (tokens only):

```css
.notes { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.bar { position: sticky; top: -28px; z-index: 2; display: flex; align-items: center; gap: 2px; padding: 6px 0; background: var(--surface-content); border-bottom: 1px solid var(--hairline-lo); }
.tool { width: 28px; height: 26px; display: grid; place-items: center; border: 0; border-radius: 7px; background: transparent; color: var(--text-mid); font: 600 12px var(--sans); cursor: pointer; }
.tool:hover { background: var(--glass-fill); color: var(--text-hi); }
.tool:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.toolOn { background: var(--selection); color: var(--text-hi); }
.tool svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.4; }
.tool svg circle { fill: currentColor; stroke: none; }
.spacer { flex: 1; }
.status { font: 400 11.5px var(--sans); color: var(--text-lo); white-space: nowrap; }
.statusError { font: 400 11.5px var(--sans); color: var(--amber); white-space: nowrap; }
.prose { position: relative; padding-left: 64px; min-height: 240px; outline: none; color: var(--text); font: 400 14.5px/1.6 var(--sans); }
.prose :global(p) { margin: 0 0 6px; }
.prose :global(h1) { margin: 14px 0 6px; font: 600 20px/1.3 var(--serif); color: var(--text-hi); }
.prose :global(h2) { margin: 12px 0 4px; font: 600 16.5px/1.35 var(--serif); color: var(--text-hi); }
.prose :global(ul), .prose :global(ol) { margin: 0 0 6px; padding-left: 22px; }
.prose :global(ul[data-type="taskList"]) { list-style: none; padding-left: 2px; }
.prose :global(li[data-type="taskItem"]) { display: flex; gap: 8px; align-items: flex-start; }
.prose :global(li[data-type="taskItem"] > label) { flex-shrink: 0; margin-top: 4px; }
.prose :global(li[data-type="taskItem"] input) { appearance: none; width: 14px; height: 14px; margin: 0; border: 1.5px solid var(--hairline); border-radius: 4px; background: transparent; cursor: pointer; }
.prose :global(li[data-type="taskItem"] input:checked) { background: var(--text-mid); border-color: var(--text-mid); }
.prose :global(li[data-type="taskItem"] input:focus-visible) { outline: 2px solid var(--accent); outline-offset: 1px; }
.prose :global(li[data-checked="true"] > div) { color: var(--text-mid); text-decoration: line-through; }
.prose :global(.notes-stamp) { position: absolute; left: 0; width: 52px; text-align: right; border: 0; padding: 0; background: transparent; color: var(--text-lo); font: 400 11.5px/2.2 var(--sans); font-variant-numeric: tabular-nums; }
.prose :global(button.notes-stamp) { cursor: pointer; }
.prose :global(button.notes-stamp:hover) { color: var(--text-hi); text-decoration: underline; }
.prose :global(button.notes-stamp:focus-visible) { outline: 2px solid var(--accent); outline-offset: 1px; }
.prose :global(p.is-editor-empty:first-child::before) { content: attr(data-placeholder); color: var(--text-xlo); float: left; height: 0; pointer-events: none; }
```

- [ ] **Step 4: Notes tab in `MeetingDetail.tsx`.**
  - `type Tab = 'summary' | 'transcript' | 'notes';` and add a third tab button "Notes" after Transcript (same classes, `aria-selected`); tablist `aria-label="Summary, transcript or notes"`.
  - Default tab: `setTab(forcedTab ?? (detail.summary ? 'summary' : detail.hasUserNotes ? 'notes' : 'transcript'));`
  - Render, beside the summary/transcript branches: `tab === 'notes'` → `<NotesEditor key={detail.id} initial={detail.userNotes} save={(v) => onSaveNotes(detail.id, v)} onStampClick={(s) => jumpToSeconds(s)} />`. **Keyed by `detail.id` and reading `initial` only on mount**, so `meetings.changed` refreshes never reset the editor (Review Focus 1).
  - `jumpToSeconds(seconds)`: `setTab('transcript')`; pick the segment with the largest `start` ≤ `seconds` (else the first segment); then scroll and flash it exactly as the search-jump effect does — move that effect's scroll+highlight body into a helper `flashSegment(index)` used by both.
  - Search jump: `if (jumpTarget.kind === 'user_notes') { setTab('notes'); return; }` before the existing `'notes'` branch.
  - `onDetailKeyDown`: ⌘⌫ must not delete the meeting while typing in the editor — confirm `isEditableTarget` returns true for `contenteditable` targets (`target.isContentEditable`); add that check if missing.

- [ ] **Step 5: App wiring.** `onSaveNotes = (id, value) => embedded ? bridge.call('notes.user.set', { id, ...value }).then(() => undefined) : Promise.resolve()`; in mock mode also update the in-memory `MOCK_DETAILS[id].userNotes` so switching meetings and back shows the edit. Add mock state `'notes'` to `MockState`/`KNOWN_STATES` that opens the default meeting with `forcedTab="notes"`.

- [ ] **Step 6: Verify** — `npm --prefix frontend test`; `npm --prefix frontend run build`; `.venv/bin/python -m pytest tests/test_frontend_tokens.py -q`.

- [ ] **Step 7: Look at it** (the stamping rule in `notesStamps.ts` needs a DOM, so this check and the Task 9 evaluation are its verification; the reviewer mutates it and confirms Step 7's stamp check notices) (dev server on 5199, browser tools, dark then light, reload after switching): `meetings.html?state=notes` shows the mock notes with stamps `12:34` and `20:10` in the gutter; clicking `12:34` switches to Transcript and highlights the segment at or before 754 s; ⌘B/⌘I/⌘⌥1/⌘⇧8/⌘⇧9 and typing `- `, `[ ] `, `## ` format as expected; the bar's active state is neutral (not coral); "Saved" appears ~0.5 s after typing; switching meetings and back keeps the edit. Review Focus 1: type a word, then run `window.speakeasyBridge._emit('meetings.changed', null)` in the console; the word and caret stay. Review Focus 5: paste HTML containing a link, an image, a table and coloured text (`document.execCommand('insertHTML', …)` or a clipboard paste) and confirm the result is plain text in supported blocks; log `docToNotes(editor.getJSON()).markdown` from a temporary `window.__notesEditor = editor` hook (remove before commit). Run `frontend/scripts/overflow-check.js` inline at 820 × 520 and 2560 × 1440 (same-size iframe, `?state=notes&fit`) → `[]`. Stop the server; reset the viewport.

- [ ] **Step 8: Commit**

```bash
git add frontend
git commit -m "Notes tab: rich-text editor with autosave and transcript stamps"
```

---

### Task 7: Recording now — draft notepad while recording

**Files:**
- Create: `frontend/src/meetings/RecordingNotes.tsx`, `frontend/src/meetings/RecordingNotes.module.css`
- Modify: `frontend/src/meetings/Sidebar.tsx`, `Sidebar.module.css` (Recording now row), `frontend/src/meetings/App.tsx` (poll `recording.get`, `recordingView` state, `meetings.saved` hand-off, mock states), `frontend/src/mock/meetings.ts` (`RecordingInfo` type, mocks)

**Interfaces:**
- Consumes: `NotesEditor` (Task 6), `stripStamps`, `notesToDoc`, `docToNotes` (Task 5), bridge `recording.get`, `notes.draft.get|set|discard|finish`, event `meetings.saved {id}` (Task 4).
- Produces: `interface RecordingInfo { recording: boolean; processing: boolean; startedAt: string | null; title: string | null }`; `RecordingNotes` props `{ info: RecordingInfo; startedAt: string; editorRef: MutableRefObject<(() => NotesValue) | null> }`; `Sidebar` props gain `recordingRow?: { elapsed: string; live: boolean } | null`, `activeRecording: boolean`, `onSelectRecording: () => void`; `NotesEditor` gains optional `valueRef?: MutableRefObject<(() => NotesValue) | null>` that it sets to `() => docToNotes(editor.getJSON())` (used for the hand-off).

- [ ] **Step 1: App state.** When embedded, poll `recording.get` every 2000 ms (`window.setInterval`, cleared on unmount) into `recording: RecordingInfo`. Keep `recordingStartedAt`: the bridge `startedAt`, or — when it is null — the ISO time the page first saw `recording || processing` become true (reset when both turn false). Show the sidebar row while `recording || processing`. `recordingView` (boolean): selecting the row sets it true and clears `today`; selecting any meeting, filter or Today clears it. If the row disappears while `recordingView` is true and no `meetings.saved` arrived within 10 s, fall back to All meetings.

- [ ] **Step 2: Sidebar row** above Today: red dot (`--rec`; `--text-lo` while processing), label "Recording now", elapsed time (`formatElapsed((Date.now() - Date.parse(startedAt)) / 1000)`, ticking each second, `flex-shrink: 0`, tabular nums, `--text-lo`), row classes and `--selection` exactly like the Today row; the label ellipsizes.

- [ ] **Step 3: `RecordingNotes.tsx`.** On mount: `notes.draft.get`. If a draft exists and its `startedAt` differs from the current `startedAt`, it is left over from an unsaved recording: load it through `stripStamps(notesToDoc(draft))` → `docToNotes` (its stamps point at audio that was never saved) and show the notice row "Notes from a recording that wasn't saved" with a "Discard" button (calls `notes.draft.discard` and remounts the editor empty). Title above the editor: `info.title ?? 'Meeting in progress'` (serif 26 px like `.title`). Editor: `<NotesEditor key={draftKey} initial={…} now={() => (Date.now() - Date.parse(startedAt)) / 1000} save={(v) => bridge.call('notes.draft.set', { ...v, startedAt })} valueRef={editorRef} notice={…} />` — no `onStampClick` (no transcript yet), so stamps render as plain text.

- [ ] **Step 4: Hand-off.** `bridge.on('meetings.saved', ({ id }) => …)`: if `recordingView` is true, call `notes.draft.finish { id, ...editorRef.current!(), startedAt }`, then select `id` with `forcedTab = 'notes'` and clear `recordingView`. (`finish` carries the editor's latest text, so a debounced save that lost the race to `save_meeting` cannot leave a stray draft — Review Focus 2.) If `recordingView` is false, do nothing extra: `save_meeting` already adopted the draft.

- [ ] **Step 5: Mock.** States `'recording'` (row live, started 12 min ago, empty draft, Recording now selected) and `'recording-leftover'` (a leftover draft `"Agenda\n- [ ] ask about budget"` with stamps, notice shown, stamps stripped). In mock mode the draft calls resolve locally.

- [ ] **Step 6: Verify** — build, node tests, token tests. Mock, dark and light: `?state=recording` (row with ticking time, red dot; typing creates gutter stamps like `12:03`; status shows Saved); `?state=recording-leftover` (notice + Discard; no stale stamps; Discard empties it); long title ellipsizes while the time stays whole; overflow check `[]` at 820 × 520. Stop the server; reset the viewport.

- [ ] **Step 7: Commit**

```bash
git add frontend
git commit -m "Recording now: draft notepad while recording, hand-off to the saved meeting"
```

---

### Task 8: Search results show where the match is

**Files:**
- Modify: `frontend/src/meetings/SearchResults.tsx`, `SearchResults.module.css`, `frontend/src/meetings/App.tsx` (pass `kind` through to `JumpTarget`)

**Interfaces:**
- Consumes: `SearchResult.kind` `'transcript' | 'notes' | 'user_notes'` (Task 4/6), `JumpTarget.kind` `'user_notes'` (Task 6).

- [ ] **Step 1:** In each result row, show a source line: transcript hits keep the speaker line; `kind === 'notes'` shows "Summary"; `kind === 'user_notes'` shows "Notes" — same `.speakerLine` style (`--text-mid`, 11.5 px), no coral.
- [ ] **Step 2:** Choosing a `user_notes` result opens the meeting on the Notes tab (`JumpTarget.kind = 'user_notes'`, handled in Task 6's jump effect).
- [ ] **Step 3: Verify** — build, node tests, token tests; mock `?state=search`: the Notes result shows "Notes" and opens the Notes tab; a summary result shows "Summary" and opens Summary. Dark and light.
- [ ] **Step 4: Commit**

```bash
git add frontend
git commit -m "Search: label summary and notes matches, open notes hits on the Notes tab"
```

---

### Task 9: Docs, full suite, real-app evaluation

**Files:**
- Modify: `README.md` (Meetings: Notes tab, Recording now, Claude reads notes), `AGENTS.md` (notepad storage `user_notes`/`note_draft`, draft adoption in `save_meeting`, TipTap pinned and the offline build check), this plan's Execution notes

- [ ] **Step 1: Docs** (Sonnet implementer) — short additions in the existing style.
- [ ] **Step 2: Full suite** (Sonnet implementer) — `.venv/bin/python -m pytest -q` (`timeout: 600000`), `npm --prefix frontend test`, `npm --prefix frontend run build`. Record counts.
- [ ] **Step 3: Evaluation (Opus evaluator, after the final review's fixes).** Build `scripts/build_app.sh` (not `--install`), launch `dist/Speakeasy.app/Contents/MacOS/Speakeasy` via Python `subprocess.Popen(env={**os.environ, "HOME": tmp})` with a temp HOME. If screen access is available: open Meetings, type notes in a meeting, quit, relaunch, confirm they persist; start a short meeting recording (synthetic `say` audio is fine), type in Recording now, stop, confirm the notes and stamps arrive on the saved meeting and a stamp jumps to the transcript; check light and dark. If screen access is declined, hand these exact steps to the user and record that the agent did not do them.
- [ ] **Step 4: Commit and report** — `git add README.md AGENTS.md docs/superpowers/plans/2026-10-02-meeting-notepad.md && git commit -m "Meeting notepad: docs and acceptance notes"`. Report which checks the agent ran and which are the user's.

---

## Execution notes

### Progress (2 Oct 2026, paused by the user)

- Branch `meeting-notepad`, worktree `.claude/worktrees/meeting-notepad`. Ledger with every ruling and deferred minor: `.superpowers/sdd/2026-10-02-meeting-notepad/progress.md` (git-ignored, in the worktree).
- **Task 1 complete** (762eae3..1d6d31b; one fix round added FTS trigger-sync and real rebuild tests). **Task 2 complete** (..dfb0f93). **Task 3 complete** (..dd687dd). **Task 4 complete** (..22b1d9a). Each: Sonnet implementer, Opus reviewer with mutation checks.
- **Task 5 implemented, NOT yet reviewed** (22b1d9a..8c4d34d). Next step: Opus task review of that range, then Task 6. Deviations to judge: offline-check test expects 6 hits (the CSS `@import url(...)` matches two patterns); `check-offline.mjs` uses `fileURLToPath` because the repo path has spaces; `npm audit` advisories were reported on install and not addressed.
- Plan-time decision recorded: `user_notes` uses `id INTEGER PRIMARY KEY` + `meeting_id UNIQUE` (stable FTS rowid).
- Carry into Task 7: (a) cancel the debounced draft save before `notes.draft.finish` and never send `notes.draft.set` after it; (b) read `recording.startedAt` only while `recording || processing` (the engine keeps the last start after a meeting ends).
- Tell the user at the end: a cancelled recording's notes are adopted by the next recording if it starts within 10 minutes (the spec's window); Copy is now Markdown.
- Deferred minors for the final review (details in the ledger): `_check_stamps` should reject NaN/inf and non-lists with ValueError; `finish_draft` should reject naive `startedAt` with ValueError and truncate sub-seconds like adoption does; null markdown saved as "None"; `recording.get` should drop `startedAt` when idle; draft window edges and finish shift untested; export test should stamp nested/heading lines; stale docstrings.

### Execution record (3 Oct 2026) — all tasks complete; merged to master with UI checks waived

**UI checks waived by the user (3 Oct 2026).** The user chose to skip the UI checks listed below and merge. The notepad has therefore been tested in code (unit/node/build suites) and at data level only (temp-HOME run of the built app); **no person or agent has looked at it on screen.** Treat the UI checks below as still open: run them before relying on the Notes tab, Recording now row, stamps, or light/dark legibility.

Every task: Sonnet implementer, Opus reviewer with mutation checks.

| Task | Commits | Review |
|---|---|---|
| 1 | 762eae3..1d6d31b | clean after 1 fix round (FTS sync + rebuild tests) |
| 2 | ..dfb0f93 | clean; 9/12 mutations |
| 3 | ..dd687dd | clean; 9/9 |
| 4 | ..22b1d9a | clean; 7/7 |
| 5 | ..67d5e29 | 1 fix round: Markdown `MARKER` no longer escapes a leading `*` (paragraph "*x" was corrupted on each save); 17/18 + fix caught |
| 6 | ..c4fd0e1 | 1 fix round: a line keeps its stamp when it becomes or leaves a list item; 5/5 + 2/2 (browser) |
| 7 | ..e19c12f | clean; 8/8 |
| 8 | ..9dec8e4 | clean; 4/4 |
| 9 docs | ..0f1f1e0 | — |
| Final review (Opus) | fixes ..58e5637 | "With fixes": README leftover-draft wording, AGENTS.md `user_notes_fts` name and offline-check wording; plus null markdown → `""`, `_check_stamps` rejects NaN/inf, finish_draft shift test (closes surviving mutation M4). Re-review: 6/6 addressed. |

Final suite at 58e5637: pytest 1065 passed, node tests 20 passed, frontend build (with offline check) OK.

**Rulings made during execution** (each with its cost if wrong):
1. Reviewers mutate the worktree temporarily and restore — cost: a leftover mutation (controller checked `git status` after each).
2. Task 1: FTS trigger-sync and real rebuild tests added beyond the plan — cost: a few extra tests.
3. Task 5: removed `|\\` from the brief's `MARKER` (plan-mandated code broke lossless round trip) — cost: a literal leading backslash may not be escaped (rare).
4. Task 6: stamps move between a list item and its paragraph on wrap/unwrap (brief's code dropped them) — cost: a little extra code in `appendTransaction`.
5. Task 7: `NotesEditor` `valueRef` means "take the final value": it cancels the pending/retry save and stops further saves, so no `notes.draft.set` follows `notes.draft.finish` — cost: if later used for a non-final read, that editor stops saving.
6. Final wave also took the reviewer's recommended one-liners and the shift test — cost: small extra diff.

**Real-app evaluation (Opus, Task 9 Step 3):** screen access was declined, so the agent checked only at data level, against a temp HOME with the built `dist/` app. Passed: schema v5 created; `recording.get` idle/recording/processing; a real 60 s synthetic meeting (Parakeet ASR + diarization) adopted the draft with correct stamps; `notes.draft.finish` hand-off; stamps fall inside the transcript; every autosave triggers `meetings.changed`; notes survive quit/relaunch (read via the built binary's MCP); bundle offline. No bugs found.

**Still for the user (UI, not seen by any agent; waived at merge, still open)** — in Terminal.app with a test HOME (the worktree is deleted after merge, so build `dist/` from master first with `scripts/build_app.sh`):
```
mkdir -p /tmp/se-notes-home
HOME=/tmp/se-notes-home dist/Speakeasy.app/Contents/MacOS/Speakeasy
```
1. Record a ~1 min meeting (or play `say` audio while recording); in Meetings open its **Notes** tab, type a heading, bullet and checklist item; "Saved" appears.
2. Type steadily ~10 s: no lost characters, caret doesn't jump, focus stays (each autosave refreshes the list). Also watch for the selection moving if the open meeting drops out of the current list/filter (unverified risk).
3. Start a meeting recording: **Recording now** row appears within ~2 s, time ticking; open it, type two lines seconds apart — each gets a stamp. Stop; when processing finishes the saved meeting opens on **Notes** with the lines and stamps.
4. Click a stamp: the transcript scrolls to and highlights that time.
5. Quit the test copy, relaunch with the same command: notes and stamps are still there.
6. Switch light/dark: editor, toolbar, stamps, Recording now row and sidebar stay legible.
7. Optional: `sqlite3 -readonly "$HOME/Library/Application Support/Speakeasy/library.sqlite" "PRAGMA user_version"` in your normal shell still prints 4 (the agent's read was blocked).
8. Quit the test copy; `rm -rf /tmp/se-notes-home`.

**Tell the user:** a cancelled recording's notes join the next recording that starts within 10 minutes of it (spec window), or the next meeting saved while they're open/edited on Recording now; Copy is now Markdown (summary, action items, My notes, transcript), and the date line lands under "## Transcript" when extras exist.

**Deferred minors (final review triaged all as "stay"):** `recording.get` keeps the last `startedAt` while idle (page ignores it); adoption errors would abort `save_meeting` (no reachable path; a try/except would harden it); every autosave re-fetches the selected meeting's full transcript (watch on long meetings); pasted `<br>` lines and table cells run together; leftover notice stays visible after typing; converter edge cases (bullet text starting "[ ] " reloads as a checklist, italic with a leading space, 4-space indents); offline check doesn't scan static `https` imports, `new Worker`, img/iframe, `.mjs`; Backspace merging a stamped paragraph into a list item drops its stamp; fallback `startedAt` ms vs whole seconds (unreachable: engine always sets the start); `npm audit --omit=dev` not run (needs network); no automated DOM tests for the editor, labels or hand-off.
