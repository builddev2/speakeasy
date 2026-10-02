# Automatic Summaries + Legible Summary Layout — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.
> Sonnet implements each task; Opus reviews and verifies by **mutation**.

**Goal:** Summaries are short and structured, shown with real spacing, and
written automatically by a Claude scheduled task through the MCP connector.
The app makes no network calls.

**Spec:** `docs/superpowers/specs/2026-10-01-auto-summaries-design.md` (0b1ce27).

**Prerequisite (hard):** `docs/superpowers/plans/2026-10-01-meeting-tags.md`
is executed and **merged to master** first. This plan assumes its end state:
`SCHEMA_VERSION = 3` with `(3, _migrate_v3)` in `_MIGRATIONS`, its rewritten
`save_notes`, its MCP tool list ending `save_notes, tag_meetings,
manage_tags`, and its `save_notes` description. Line numbers below are
therefore omitted; locate code by function name. If master is not at that
state, stop and report.

**Architecture:** `speakeasy/summary_format.py` (new, pure) owns the format
text Claude follows and the parser that turns stored text into display
blocks. Schema v4 adds `summary_requests`. `MeetingLibrary` decides what is
pending. `mcp_tools.py` adds read-only `pending_summaries`. The bridge sends
`summaryBlocks` and `summaryQueued` and handles `meetings.requestSummary`.
React only renders blocks (no HTML injection). A Claude Desktop scheduled
task does the summarising.

## Global constraints

- No network calls, no new dependencies (Python or npm).
- Pending = explicit row in `summary_requests` **or** (started within the
  last **7** days **and** duration ≥ **120** s **and** no non-empty summary).
- `pending_summaries` limit: 1–10, default **5**. Explicit requests first
  (oldest `requested_at` first), then automatic (oldest `started_at` first),
  ties by id.
- Saving a non-empty summary deletes that meeting's request in the same
  transaction. Deleting a meeting cascades.
- Every MCP tool or description change is mirrored in
  `packaging/mcpb/manifest.json` (`tests/test_mcpb.py` enforces).
- Tests: `.venv/bin/python -m pytest` with Bash `timeout: 600000` (the full
  suite takes minutes; never let it background). Tests are HOME-isolated
  via `tests/conftest.py`; never point anything at the real
  `~/Library/Application Support` library.
- Mutation runs: `python -B` and clear `__pycache__` first (memory note).

## File structure

- Create `speakeasy/summary_format.py`, `tests/test_summary_format.py`.
- Modify `speakeasy/meeting_store.py` (v4), `speakeasy/meeting_library.py`
  (queue), `speakeasy/mcp_tools.py`, `packaging/mcpb/manifest.json`,
  `speakeasy/ui/meetings_bridge.py`.
- Modify `frontend/src/mock/meetings.ts` (types + mock data),
  `frontend/src/meetings/MeetingDetail.tsx`, `MeetingDetail.module.css`,
  `frontend/src/meetings/App.tsx`, `frontend/src/components/ActionButton.tsx`
  (add `disabled`).
- Modify `README.md` (MCP tool list), `AGENTS.md` (writes rule + queue).
- Tests: `tests/test_meeting_store.py`, `tests/test_meeting_library.py`,
  `tests/test_mcp_tools.py`, `tests/test_meetings_bridge.py`.

---

### Task 1: `summary_format` module

**Files:** create `speakeasy/summary_format.py`, `tests/test_summary_format.py`.

- [ ] **Step 1: failing tests** — `tests/test_summary_format.py`:

```python
from speakeasy.summary_format import SUMMARY_INSTRUCTIONS, parse_summary


def test_new_format():
    text = ("TL;DR: Scoped the MVP.\n\n## Decisions\n- No client docs in MVP\n"
            "- Accuracy over speed\n\n## Key points\n- Top-3 match is success\n\n"
            "## Open questions\n- Team after ERB on Oct 22?")
    assert parse_summary(text) == [
        {"kind": "tldr", "text": "Scoped the MVP."},
        {"kind": "heading", "text": "Decisions"},
        {"kind": "bullets", "items": ["No client docs in MVP", "Accuracy over speed"]},
        {"kind": "heading", "text": "Key points"},
        {"kind": "bullets", "items": ["Top-3 match is success"]},
        {"kind": "heading", "text": "Open questions"},
        {"kind": "bullets", "items": ["Team after ERB on Oct 22?"]},
    ]


def test_legacy_free_form_summary():
    # Shape of the stored summary of meeting 20261001-165512-f167.
    text = ("Project Black working session (Part 2), ~31 min. Continuation\n"
            "of the sprint prep.\n\n"
            "CORE CONCEPT (consensus)\n- Field tool: assessor photographs equipment.\n"
            "- Success test: right answer first or in top 3.\n\n"
            "CLIENT-PROVIDED DOCUMENTS (pre-visit docs, as-builts, old roof reports)\n"
            "- DECISION: Not in MVP / no demo.\n\nNEXT\n- Continue tomorrow.\n\n"
            "Note: transcript has misrecognitions.")
    assert parse_summary(text) == [
        {"kind": "para", "text": "Project Black working session (Part 2), ~31 min. "
                                 "Continuation of the sprint prep."},
        {"kind": "heading", "text": "CORE CONCEPT (consensus)"},
        {"kind": "bullets", "items": ["Field tool: assessor photographs equipment.",
                                      "Success test: right answer first or in top 3."]},
        {"kind": "heading",
         "text": "CLIENT-PROVIDED DOCUMENTS (pre-visit docs, as-builts, old roof reports)"},
        {"kind": "bullets", "items": ["DECISION: Not in MVP / no demo."]},
        {"kind": "heading", "text": "NEXT"},
        {"kind": "bullets", "items": ["Continue tomorrow."]},
        {"kind": "para", "text": "Note: transcript has misrecognitions."},
    ]


def test_wrapped_bullet_and_tldr_continue_and_bold_is_stripped():
    text = ("**TL;DR:** First half\nsecond half.\n### Decisions:\n"
            "* **Ship** it\n  by Friday\n\n• Hire later")
    assert parse_summary(text) == [
        {"kind": "tldr", "text": "First half second half."},
        {"kind": "heading", "text": "Decisions"},
        {"kind": "bullets", "items": ["Ship it by Friday", "Hire later"]},
    ]


def test_caps_heading_rules():
    blocks = parse_summary("OK\n\nDECISIONS:\n\nRSMeans AND CTC\n\n" + "A" * 61)
    assert blocks == [
        {"kind": "para", "text": "OK"},               # fewer than 3 letters
        {"kind": "heading", "text": "DECISIONS"},      # trailing colon removed
        {"kind": "para", "text": "RSMeans AND CTC"},  # has lowercase
        {"kind": "para", "text": "A" * 61},            # longer than 60
    ]


def test_tldr_only_counts_first():
    assert parse_summary("Intro.\n\nTL;DR: late") == [
        {"kind": "para", "text": "Intro."}, {"kind": "para", "text": "TL;DR: late"}]


def test_empty():
    assert parse_summary("") == [] and parse_summary(None) == []


def test_instructions_name_the_sections_and_limits():
    for needle in ("TL;DR:", "## Decisions", "## Key points", "## Open questions",
                   "150 words", "Owner — task", "list_tags", "at most 3 new"):
        assert needle in SUMMARY_INSTRUCTIONS
```

- [ ] **Step 2:** run `.venv/bin/python -m pytest tests/test_summary_format.py` → fails (module missing).

- [ ] **Step 3: implement** `speakeasy/summary_format.py`:

```python
"""How Claude writes a meeting summary, and how the app lays one out.

SUMMARY_INSTRUCTIONS is handed to Claude by the pending_summaries MCP tool,
so the format lives in one place. parse_summary turns stored summary text
(this format, or the free-form text older summaries used) into blocks the
Meetings window renders with real spacing. Pure: no I/O, no AppKit.
"""

import re

SUMMARY_INSTRUCTIONS = """\
Write the summary in exactly this shape (about 150 words in total):

TL;DR: 1-2 sentences: what the meeting was for and where it landed.

## Decisions
- One line each. Write "- None" if nothing was decided.

## Key points
- At most 6, one line each: only what matters later.

## Open questions
- Unresolved issues, risks, and ideas parked for later.

Rules:
- Short, plain sentences. No speaker-mapping notes, no notes about
  transcription errors, no "Speaker 3": use a name if the transcript gives
  one, otherwise describe the role ("the facilitator").
- Action items go in action_items, not the summary, each as
  "Owner — task (due)"; use "Unassigned" when nobody took it. Parked ideas go
  under Open questions, not action items.
- Tags: call list_tags first and reuse existing tags (any spelling or alias
  matches). Create a new tag only for a genuinely new topic, at most 3 new
  per meeting; 3-8 tags in total. If save_notes says it would create too
  many new tags, drop the new ones and save again.
- If the meeting is under 2 minutes or has almost no content, the summary is
  just "TL;DR: Too short to summarise." with no sections, no action items
  and no tags.
- Save with save_notes (summary, action_items and tags together)."""

_MD_HEADING = re.compile(r"^#{1,6}\s+(.*)$")
_BULLET = re.compile(r"^[-*•]\s+(.*)$")
_TLDR = re.compile(r"^tl;?dr\s*:\s*(.*)$", re.IGNORECASE)
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_PARENS = re.compile(r"\([^)]*\)")
_MAX_CAPS_HEADING = 60


def _clean(text: str) -> str:
    return _BOLD.sub(r"\1", text).strip()


def _caps_heading(line: str) -> bool:
    # Older summaries used ALL-CAPS section lines such as
    # "CORE CONCEPT (consensus)"; a parenthesised aside may be lowercase.
    core = _PARENS.sub("", line).strip()
    letters = [c for c in core if c.isalpha()]
    return (len(core) <= _MAX_CAPS_HEADING and len(letters) >= 3
            and not any(c.islower() for c in letters))


def parse_summary(text: str | None) -> list[dict]:
    """Blocks: tldr {text}, heading {text}, bullets {items}, para {text}."""
    blocks: list[dict] = []
    para: list[str] = []
    prev = None  # "tldr" / "bullet" while the previous line continues one

    def flush():
        if para:
            blocks.append({"kind": "para", "text": " ".join(para)})
            para.clear()

    for raw in (text or "").splitlines():
        line = _clean(raw)
        if not line:
            flush()
            prev = None
            continue
        tldr = _TLDR.match(line) if not blocks and not para else None
        if tldr:
            blocks.append({"kind": "tldr", "text": tldr.group(1).strip()})
            prev = "tldr"
            continue
        heading = _MD_HEADING.match(line)
        if heading or _caps_heading(line):
            flush()
            name = heading.group(1) if heading else line
            blocks.append({"kind": "heading", "text": name.strip().rstrip(":").strip()})
            prev = None
            continue
        bullet = _BULLET.match(line)
        if bullet:
            flush()
            item = bullet.group(1).strip()
            if blocks and blocks[-1]["kind"] == "bullets":
                blocks[-1]["items"].append(item)
            else:
                blocks.append({"kind": "bullets", "items": [item]})
            prev = "bullet"
            continue
        if prev == "bullet":
            blocks[-1]["items"][-1] += " " + line
        elif prev == "tldr":
            blocks[-1]["text"] += " " + line
        else:
            para.append(line)
    flush()
    return blocks
```

Note on `test_caps_heading_rules`: `"OK"` and `"RSMeans AND CTC"` are
separated by blank lines so each is its own para. `"• Hire later"` after a
blank line joins the previous bullets block because nothing came between
(spec: consecutive bullet lines; a blank line alone does not split a list).

- [ ] **Step 4:** run the file → all pass.
- [ ] **Step 5: commit** `"Summaries: format instructions and display parser"`.

---

### Task 2: Schema v4 + summary queue in the library

**Files:** `speakeasy/meeting_store.py`, `speakeasy/meeting_library.py`,
`tests/test_meeting_store.py`, `tests/test_meeting_library.py`.

**Interfaces produced:**
- `meeting_store.SCHEMA_VERSION == 4`; table `summary_requests`.
- `MeetingLibrary.request_summary(meeting_id) -> None` (raises `MeetingNotFound`).
- `MeetingLibrary.pending_summaries(limit=5, now=None) -> list[tuple[str, bool]]`
  — `(meeting_id, requested)`.
- `MeetingLibrary.summary_pending(meeting_id, now=None) -> bool`.
- Module constants `PENDING_WINDOW_DAYS = 7`, `PENDING_MIN_SECONDS = 120`.

- [ ] **Step 1: failing tests.**

`tests/test_meeting_store.py`: existing tests compare against
`SCHEMA_VERSION`, so they cover v4 once it exists. Add:

```python
def test_v4_adds_summary_requests(library_path):
    conn = meeting_store.connect(library_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(summary_requests)")]
    assert cols == ["meeting_id", "requested_at"]
    assert meeting_store.SCHEMA_VERSION == 4
```

And a migration test from a populated v3 library: create via `connect`,
insert one meeting with the library API, `DROP TABLE summary_requests;
PRAGMA user_version = 3`, close, reconnect → version 4, table present,
meeting still there.

`tests/test_meeting_library.py` (use `datetime.now(timezone.utc)` as `NOW`
and pass `now=NOW` explicitly):

```python
NOW = datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc)


def _at(lib, days_ago, seconds=600, title="M"):
    return lib.save_meeting(_new(started=NOW - timedelta(days=days_ago),
                                 duration_seconds=seconds, title=title))


def test_pending_window_length_and_summary(lib):
    recent = _at(lib, 1)
    _at(lib, 8)                       # older than 7 days
    _at(lib, 1, seconds=119)          # shorter than 2 minutes
    done = _at(lib, 2)
    lib.save_notes(done, summary="TL;DR: x")
    blank = _at(lib, 3)
    lib.save_notes(blank, action_items=["a"])   # notes but empty summary: pending
    assert lib.pending_summaries(now=NOW) == [(blank, False), (recent, False)]


def test_explicit_request_beats_window_and_orders_first(lib):
    old = _at(lib, 30)
    short = _at(lib, 1, seconds=30)
    recent = _at(lib, 1)
    lib.save_notes(recent, summary="TL;DR: x")
    lib.request_summary(old)
    lib.request_summary(recent)      # redo despite having a summary
    lib.request_summary(short)
    assert lib.pending_summaries(now=NOW) == [(old, True), (recent, True), (short, True)]
    assert lib.pending_summaries(limit=1, now=NOW) == [(old, True)]


def test_saving_a_summary_clears_the_request(lib):
    mid = _at(lib, 30)
    lib.request_summary(mid)
    lib.save_notes(mid, action_items=["a"])          # no summary: still queued
    assert lib.summary_pending(mid, now=NOW)
    lib.save_notes(mid, summary="TL;DR: done")
    assert not lib.summary_pending(mid, now=NOW)
    assert lib.pending_summaries(now=NOW) == []


def test_request_twice_keeps_one_row_and_unknown_id_raises(lib, library_path):
    mid = _at(lib, 30)
    lib.request_summary(mid)
    lib.request_summary(mid)
    assert lib.pending_summaries(now=NOW) == [(mid, True)]
    with pytest.raises(MeetingNotFound):
        lib.request_summary("20990101-000000-dead")


def test_delete_cascades_request(lib, library_path):
    mid = _at(lib, 30)
    lib.request_summary(mid)
    lib.delete(mid)
    conn = meeting_store.connect(library_path)
    assert conn.execute("SELECT COUNT(*) FROM summary_requests").fetchone()[0] == 0
```

(Adapt `_new(...)` kwargs to the helper's real signature; it already
forwards `**kw` to `NewMeeting`. Use the file's existing `lib` fixture or
add `@pytest.fixture def lib(library_path): return MeetingLibrary()`.)

The ordering rule in `test_explicit_request_beats_window_and_orders_first`
relies on `requested_at` resolution: store it with microseconds
(`datetime.now(timezone.utc).isoformat(timespec="microseconds")`) so three
calls in one second still sort in call order; tie-break by `rowid`.

- [ ] **Step 2:** run both files → fail.

- [ ] **Step 3: implement.**

`meeting_store.py` — after the tags plan's v3, add and register:

```python
# v4: meetings waiting for Claude to (re)write their summary. Requests are
# explicit (the app's Summarise / Redo summary); recent unsummarised
# meetings are pending without a row (see MeetingLibrary.pending_summaries).
_SCHEMA_V4 = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS summary_requests (
    meeting_id TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
    requested_at TEXT NOT NULL
);
PRAGMA user_version = 4;
COMMIT;
"""
```

`SCHEMA_VERSION = 4`; `_MIGRATIONS = [..., (3, _migrate_v3), (4, _SCHEMA_V4)]`.
v4 is idempotent (`IF NOT EXISTS`), so the slower-v1 reset race needs no
special handling; confirm `migrate()` (as rewritten by the tags plan) runs
string entries with `executescript`.

`meeting_library.py`:

```python
# A meeting is waiting for a summary when the user asked (summary_requests)
# or it is recent, long enough to matter and still has no summary. The
# window keeps the pre-feature backlog from being summarised automatically.
PENDING_WINDOW_DAYS = 7
PENDING_MIN_SECONDS = 120
_PENDING_SQL = (
    "(r.meeting_id IS NOT NULL OR (m.started_at >= ? AND m.duration_seconds >= ?"
    " AND NOT EXISTS (SELECT 1 FROM notes n WHERE n.meeting_id = m.id"
    " AND n.summary <> '')))")
```

Methods (new `# -- summaries ---` section after the notes section):

```python
    def _pending_params(self, now):
        now = now or datetime.now(timezone.utc)
        return [utc_iso(now - timedelta(days=PENDING_WINDOW_DAYS)), PENDING_MIN_SECONDS]

    def request_summary(self, meeting_id: str) -> None:
        _check_id(meeting_id)
        with self._transaction() as conn:
            self._touch(conn, meeting_id)
            conn.execute(
                "INSERT INTO summary_requests (meeting_id, requested_at) VALUES (?, ?)"
                " ON CONFLICT(meeting_id) DO NOTHING",
                (meeting_id, datetime.now(timezone.utc).isoformat(timespec="microseconds")))

    def pending_summaries(self, limit=5, now=None) -> list[tuple[str, bool]]:
        limit = max(1, min(int(limit), 10))
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT m.id, r.meeting_id IS NOT NULL AS requested FROM meetings m"
                " LEFT JOIN summary_requests r ON r.meeting_id = m.id"
                f" WHERE {_PENDING_SQL}"
                " ORDER BY requested DESC, r.requested_at, r.rowid, m.started_at, m.id"
                " LIMIT ?", (*self._pending_params(now), limit)).fetchall()
        return [(r["id"], bool(r["requested"])) for r in rows]

    def summary_pending(self, meeting_id: str, now=None) -> bool:
        _check_id(meeting_id)
        with self._transaction() as conn:
            return conn.execute(
                "SELECT 1 FROM meetings m LEFT JOIN summary_requests r"
                f" ON r.meeting_id = m.id WHERE m.id = ? AND {_PENDING_SQL}",
                (meeting_id, *self._pending_params(now))).fetchone() is not None
```

`_touch` is used only as the existence check (raises `MeetingNotFound`);
it also bumps `updated_at`, which is what makes `LibraryWatcher` refresh
other windows. Keep it.

`save_notes`: inside its transaction, after the notes upsert, add:

```python
            if summary:  # a written summary answers any pending request
                conn.execute("DELETE FROM summary_requests WHERE meeting_id = ?",
                             (meeting_id,))
```

(`summary` here is the post-merge value; use the *argument* — only clear
when the caller passed a non-empty summary. Keep the argument in a local
such as `new_summary = summary` before the merge line and test that.)

- [ ] **Step 4:** run both files, then the full suite (`timeout: 600000`).
- [ ] **Step 5: commit** `"Summaries: schema v4 request queue and pending rules"`.

---

### Task 3: MCP `pending_summaries`, descriptions, manifest, docs

**Files:** `speakeasy/mcp_tools.py`, `packaging/mcpb/manifest.json`,
`tests/test_mcp_tools.py`, `README.md`, `AGENTS.md`.

- [ ] **Step 1: failing tests** in `tests/test_mcp_tools.py`:
  - `test_tool_names_order_and_definitions`: the tags plan's list plus
    `"pending_summaries"` last; `pending_summaries` is read-only.
  - New:

```python
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
    with pytest.raises(ToolError):
        tools["pending_summaries"].run({"limit": 11})


def test_save_notes_clears_pending(lib, tools):
    old = _seed(lib, day=1)
    lib.request_summary(old)
    tools["save_notes"].run({"id": old, "summary": "TL;DR: done"})
    assert tools["pending_summaries"].run({})["meetings"] == []
```

  (Match `_int`'s actual error behaviour for out-of-range `limit`; if it
  clamps instead of raising, assert the clamp instead.)

- [ ] **Step 2:** run → fail.

- [ ] **Step 3: implement.** In `build_tools`:

```python
    def pending_summaries(args):
        limit = _int(args, "limit", 5, 1, 10)
        meetings = []
        for meeting_id, requested in library.pending_summaries(limit=limit):
            m = library.get_meeting(meeting_id, with_segments=False)
            meetings.append({
                "id": m.meeting_id, "title": m.title,
                "start": _start(m.started_at, m.tz_offset_minutes),
                "duration_minutes": _minutes(m.duration_seconds),
                "speakers": m.speakers, "tags": m.tags,
                "has_summary": bool(m.notes and m.notes.summary),
                "requested": requested})
        return {"meetings": meetings, "instructions": SUMMARY_INSTRUCTIONS}
```

Import `SUMMARY_INSTRUCTIONS` from `.summary_format` (pure module: keeps
`--mcp` import-light; `test_mcp_mode_imports_nothing_heavy` must pass).

Spec entry, appended **after** `manage_tags`:

```python
        ("pending_summaries",
         "Meetings waiting for a summary (new ones from the last 7 days, plus any "
         "the user asked to summarise or redo), with the summary format to use. "
         "For each: read the whole transcript with get_transcript, then save the "
         "summary, action items and tags with save_notes.",
         {"limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5}},
         [], pending_summaries, True),
```

`save_notes`: append to the tags plan's description string, as its last
sentence: `" Write summaries in the format pending_summaries returns."`
Change its `summary` property to:

```python
"summary": {"type": "string", "maxLength": 20000,
            "description": "Use the format from pending_summaries: a TL;DR line, "
                           "then ## Decisions, ## Key points, ## Open questions."},
```

`packaging/mcpb/manifest.json`: update `save_notes` description to the new
full string (one line) and append `{"name": "pending_summaries",
"description": "<the string above, one line>"}` after `manage_tags`.

`README.md` MCP tool list: add
"`pending_summaries` — meetings waiting for a summary (recent unsummarised
ones and any you queued in the app), with the summary format Claude uses."
and a short "Automatic summaries" paragraph: a Claude Desktop scheduled task
(see Task 7) checks every 30 minutes; the app's **Summarise** / **Redo
summary** queue a meeting; the app itself never goes online.

`AGENTS.md`, in the MCP writes rule the tags plan rewrote, add:
"`pending_summaries` is read-only. The only other summary-queue writer is
the app (`MeetingLibrary.request_summary` via `meetings.requestSummary`);
`save_notes` with a non-empty summary clears a request."

- [ ] **Step 4:** run `tests/test_mcp_tools.py tests/test_mcpb.py
  tests/test_mcp_server.py`, then the full suite (`timeout: 600000`).
- [ ] **Step 5: commit** `"Summaries: pending_summaries MCP tool and format guidance"`.

---

### Task 4: Bridge — blocks, queued flag, requestSummary

**Files:** `speakeasy/ui/meetings_bridge.py`, `tests/test_meetings_bridge.py`.

- [ ] **Step 1: failing tests.**
  - In `test_list_and_get_shapes`, the whole-dict `detail` assertion gains
    `"summaryBlocks": [{"kind": "para", "text": "Invest in VFA."}],
    "summaryQueued": False`.
  - New:

```python
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
    out = _call(d, "meetings.requestSummary", {"id": "20990101-000000-dead"})
    # assert the same not_found error shape the other handlers' tests assert
```

- [ ] **Step 2:** run → fail.
- [ ] **Step 3: implement.** In `get_payload`'s `detail.update({...})`:

```python
            "summaryBlocks": parse_summary(m.notes.summary if m.notes else ""),
            "summaryQueued": self.library.summary_pending(m.meeting_id, now=self._now()),
```

New handler, registered as `"meetings.requestSummary"` next to
`"meetings.copyText"`:

```python
    def request_summary_payload(self, params) -> dict:
        self.library.request_summary(str(params.get("id", "")))
        return self.get_payload(params)
```

`self._now()` returns an aware datetime (it is used with `.tzinfo`
already); `summary_pending` compares in UTC via `utc_iso`.

- [ ] **Step 4:** run the bridge tests, then the full suite (`timeout: 600000`).
- [ ] **Step 5: commit** `"Summaries: bridge sends summary blocks and queue state"`.

---

### Task 5: Frontend — spaced layout, Summarise / Redo summary

**Files:** `frontend/src/mock/meetings.ts`, `frontend/src/components/ActionButton.tsx`,
`frontend/src/meetings/MeetingDetail.tsx`, `MeetingDetail.module.css`,
`frontend/src/meetings/App.tsx`.

No frontend test runner exists; correctness is `npm --prefix frontend run
build` (tsc + vite) plus the Task 6 visual check.

- [ ] **Step 1: types + mock** (`frontend/src/mock/meetings.ts`):

```ts
export type SummaryBlock =
  | { kind: 'tldr'; text: string }
  | { kind: 'heading'; text: string }
  | { kind: 'para'; text: string }
  | { kind: 'bullets'; items: string[] };
```

`MeetingDetail` gains `summaryBlocks: SummaryBlock[]; summaryQueued: boolean;`.
Each mock detail gets `summaryQueued: false` and `summaryBlocks`: `[]` when
`summary` is null; otherwise blocks written by hand. Make the first mock
with a summary use the new format (a tldr, two headings with bullets) so
`npm run dev` shows the real layout.

- [ ] **Step 2: `ActionButton`** gains `disabled?: boolean`, passed to `<button disabled={disabled}>`; CSS `.btn:disabled { opacity: 0.45; cursor: default; }` in `ActionButton.module.css`.

- [ ] **Step 3: `MeetingDetail.tsx`.**
  - New prop `onRequestSummary: () => void;`.
  - Replace `<p className={styles.summaryText}>{detail.summary}</p>` with
    `<SummaryBlocks blocks={detail.summaryBlocks} />`, a small component in
    the same file:

```tsx
function SummaryBlocks({ blocks }: { blocks: SummaryBlock[] }) {
  return (
    <div className={styles.summaryBody}>
      {blocks.map((b, i) => {
        switch (b.kind) {
          case 'tldr':
            return <p key={i} className={styles.summaryTldr}>{b.text}</p>;
          case 'heading':
            return <h3 key={i} className={styles.summaryHeading}>{b.text}</h3>;
          case 'para':
            return <p key={i} className={styles.summaryPara}>{b.text}</p>;
          case 'bullets':
            return (
              <ul key={i} className={styles.summaryList}>
                {b.items.map((item, j) => (
                  <li key={j}><span className={styles.summaryDot} aria-hidden="true" />{item}</li>
                ))}
              </ul>
            );
        }
      })}
    </div>
  );
}
```

  - When `detail.summary && detail.summaryQueued`, show above the blocks:
    `<div className={styles.queuedNote}>Queued for a new summary. Claude checks every 30 minutes while the Claude app is open.</div>`
  - No-summary state: delete the whole "Copy prompt" `ActionButton` and its
    clipboard code (the tags plan's edited prompt goes with it). Render:

```tsx
<div className={styles.noSummary}>
  <p>{detail.summaryQueued
        ? 'Queued — Claude will summarise this the next time it checks (every 30 minutes while the Claude app is open).'
        : 'No summary yet.'}</p>
  <ActionButton disabled={detail.summaryQueued} onClick={onRequestSummary}>
    Summarise
  </ActionButton>
</div>
```

  - `···` menu: before "Delete…", when `detail.summary && !detail.summaryQueued`,
    a `menuitem` **Redo summary** that closes the menu and calls `onRequestSummary()`.
  - If `bridge` / `meetings.copyText` are no longer used in this file,
    remove the now-unused imports (tsc `noUnusedLocals` may require it).
    Keep the bridge's `copyText` method (other callers / tests).

- [ ] **Step 4: CSS** (`MeetingDetail.module.css`). Remove `.summaryText`
  (unused) and add:

```css
.summaryBody { display: flex; flex-direction: column; }

.summaryTldr {
  margin: 0 0 20px;
  padding: 12px 14px;
  border-radius: 10px;
  background: var(--glass-fill);
  border-left: 3px solid var(--amber);
  font: 500 14px/1.55 var(--sans);
  color: var(--text);
}

.summaryHeading {
  margin: 22px 0 8px;
  font: 600 11px/1.4 var(--sans);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--text-lo);
}
.summaryBody > .summaryHeading:first-child { margin-top: 0; }

.summaryPara { margin: 0 0 12px; font: 400 13px/1.55 var(--sans); color: var(--text); }

.summaryList {
  margin: 0; padding: 0; list-style: none;
  display: flex; flex-direction: column; gap: 6px;
}
.summaryList li {
  display: flex; align-items: flex-start; gap: 8px;
  font: 400 13px/1.5 var(--sans); color: var(--text);
}
.summaryDot {
  flex-shrink: 0; margin-top: 7px;
  width: 5px; height: 5px; border-radius: 50%;
  background: var(--text-lo);
}

.queuedNote {
  margin: 0 0 14px;
  font: 400 12px/1.4 var(--sans);
  color: var(--text-mid);
}
```

  Change `.actionItemsHeading` `margin-top: 16px` → `24px`.

- [ ] **Step 5: `App.tsx`.** Pass `onRequestSummary`:

```tsx
onRequestSummary={() => {
  if (embedded && selectedId) {
    const id = selectedId;
    void bridge
      .call<MeetingDetailType>('meetings.requestSummary', { id })
      .then((d) => setDetails((prev) => ({ ...prev, [id]: d })))
      .catch((err) => {
        if (isNotFoundError(err)) recoverFromNotFound(id);
        else console.error('meetings.requestSummary failed', err);
      });
  } else {
    console.log('request-summary', selectedId);
  }
}}
```

- [ ] **Step 6:** `npm --prefix frontend run build` → succeeds with no type
  errors. Run the full Python suite (`timeout: 600000`).
- [ ] **Step 7: commit** `"Summaries: spaced summary layout; Summarise and Redo summary"`.

---

### Task 6: Verify, review, install, look at it

- [ ] Full suite (`timeout: 600000`); report the pass count.
- [ ] **Opus review by mutation** (`python -B`, clear `__pycache__`). Each
  must make the suite fail; revert after each:
  1. `_caps_heading`: drop the `not any(c.islower() ...)` test.
  2. `_caps_heading`: measure `len(line)` instead of the paren-stripped core.
  3. `PENDING_WINDOW_DAYS = 70`.
  4. `PENDING_MIN_SECONDS = 0`.
  5. Remove the `DELETE FROM summary_requests` in `save_notes`.
  6. Clear the request on any `save_notes` call (ignore `new_summary`).
  7. `ORDER BY` without `requested DESC`.
  8. Bridge: `summaryQueued` hard-coded `False`.
  9. Drop `pending_summaries` from the manifest.
- [ ] `npm --prefix frontend run build`, then `scripts/build_app.sh --install`.
  This stops Claude Desktop's Speakeasy `--mcp` server (memory note):
  toggle the connector off/on in Claude Desktop afterwards, then confirm
  `pending_summaries` appears in its tool list.
- [ ] **Back up the real library** before the first launch on v4:
  copy `~/Library/Application Support/Speakeasy/` library DB (+ `-wal`,
  `-shm`) to a dated folder in the scratchpad. Launching the installed app
  migrates v3 → v4.
- [ ] **Look at it** (the `run` skill; memory "Installed-app UI checks"):
  open Meetings, select "Project Black Meeting Part 2" (f167): the summary
  shows separated sections (ALL-CAPS headings, bullet lists, gaps between
  sections). Screenshot. Select an old meeting with no summary: "No summary
  yet." + **Summarise**; press it → "Queued — …" and the button disables.
  Do not leave test meetings queued that the user didn't ask for: if one was
  queued only for the check, note its id for Task 7 (it will be summarised,
  which is harmless) or tell the user.
- [ ] Report what was verified by tests vs. by looking.
- [ ] Commit any fixes; push the branch.

---

### Task 7: Claude scheduled task + one real end-to-end run

Run in the main session (needs the user's approval; touches their Claude
Desktop config, not the repo).

- [ ] Create with `mcp__scheduled-tasks__create_scheduled_task`:
  - `taskId`: `speakeasy-auto-summaries`
  - `title`: `Speakeasy: summarise new meetings`
  - `description`: `Summarise new or queued Speakeasy meetings via the local connector.`
  - `cronExpression`: `*/30 * * * *`
  - `notifyOnCompletion`: `false`
  - `prompt`:

```
Using the Speakeasy Meetings connector (local, on this Mac):
1. Call pending_summaries. If it returns no meetings, reply "Nothing to summarise." and stop.
2. For each meeting it returns, in order:
   a. Read the whole transcript with get_transcript, following next_cursor until it is null.
   b. Write the summary, action items and tags exactly as the returned "instructions" say.
   c. Save them with one save_notes call. If save_notes rejects new tags, drop the new tags and save again.
3. Do not read or change any other meeting, and do not call tag_meetings or manage_tags.
4. Reply with one line per meeting saved: its title and the TL;DR.
```

- [ ] Tell the user: runs only while the Claude app is open; if it was
  closed, it runs on next launch; each run uses their Claude plan, at most
  5 meetings.
- [ ] Run it once now (`run_scheduled_task`), then check one summarised
  meeting in the app: new format, spaced layout, action items as
  "Owner — task (due)", request cleared.
- [ ] Update this plan's status, then follow the finishing workflow
  (merge to master, push, delete branch + worktree, remove temp files).

## Status

- [ ] Task 1 — [ ] Task 2 — [ ] Task 3 — [ ] Task 4 — [ ] Task 5 — [ ] Task 6 — [ ] Task 7
- Blocked until the meeting-tags plan (bc50812) is merged.
