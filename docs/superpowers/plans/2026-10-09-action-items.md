# Action Items Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn per-meeting action-item strings into tracked items with a dedicated sidebar view: complete, annotate, re-date, tag, filter, sort, bulk-edit, export, and full MCP CRUD. Edits survive re-summarising.

**Architecture:**

- Schema v7 adds `action_items` and `action_item_tags`. A migration imports
  the existing strings.
- Pure rules live in `speakeasy/action_items.py`. SQL lives in
  `speakeasy/action_item_store.py`, wrapped by thin `MeetingLibrary`
  methods.
- `save_notes` merges Claude's new list and keeps user-touched items.
  `notes.action_items_json` stays as a mirror for search and old readers.
- MCP tools and WKWebView bridge methods expose CRUD.
- In React, pure helpers drive an `ActionItemsView` and a shared
  `ActionItemRow`.

**Tech Stack:** Python 3 + SQLite (stdlib `sqlite3`, `difflib`), pytest; React 18 + TypeScript + Vite, `node --test` for frontend helpers. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-09-action-items-design.md`. Read it before any task; it holds the reasons behind each rule.

## Model routing and escalation (user requirement)

| Role | Model | When |
|---|---|---|
| Controller (this session) | **Opus** | Runs the plan, writes briefs, resolves questions, merges |
| Implementer per task | **Sonnet** (`model: "sonnet"`) | Every coding task (1–10). The brief contains the task text verbatim plus Global Constraints |
| Task reviewer | **Opus** (`model: "opus"`) | After every task: spec compliance + code quality, verified **by mutation** (break the code, confirm a test fails, restore) |
| Hard blocker / debugging | **Opus** | When an implementer reports BLOCKED or NEEDS_CONTEXT, when the same test fails after two Sonnet attempts, or when the reviewer and implementer disagree. Use superpowers:systematic-debugging; the fix may be re-dispatched to Sonnet with the diagnosis |
| Design question not answered here | **Opus** decides if it's technical and records the decision in this plan. **Ask the user** if it changes behaviour they will see |
| Final whole-branch review (Task 11) | **Opus** | Mutation-verified, plus the Review Focus list below |

Brief rules for every subagent:

- Keep reports short: a verdict, the files changed and the test result.
- Never run app code against the real `~/Library/Application Support/Speakeasy`.
- Give any full-suite Bash call `timeout: 600000`. The Python suite takes
  several minutes, and 1,285+ tests ran on 9 Oct.
- Mutation runs use this command:

  ```
  PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider <file>
  ```

  Afterwards run:

  ```
  find speakeasy tests -name __pycache__ -type d -prune -exec rm -rf {} +
  ```

## Global Constraints

- Offline always: no network calls and no new runtime or npm dependencies.
  Similarity uses `difflib`.
- Schema version becomes **7**. Never edit an existing `_MIGRATIONS` entry; only append.
- Bridge integer params arrive as **floats** from WKWebView. Accept integral
  floats such as `3.0` and reject bools. Bridge tests send floats.
- Every MCP tool description added or changed must be copied **verbatim**
  into `packaging/mcpb/manifest.json` (`tests/test_mcpb.py`).
- Limits:

  | Field | Limit |
  |---|---|
  | task | 1–500 characters |
  | owner | ≤ 80 characters |
  | due_phrase | ≤ 80 characters |
  | notes | ≤ 5,000 characters |
  | own tags per item | ≤ 10 |
  | items per `save_notes` | ≤ 50 |
  | ids per bulk call | ≤ 500 |
  | identity name | ≤ 80 characters |
  | aliases | ≤ 10, each ≤ 40 characters |
  | due dates | `YYYY-MM-DD`, year 2000–2100 |

- `SIMILAR_TASK_RATIO = 0.85`.
- Owner `Unassigned` (any case) is stored as `''` and displayed as `Unassigned`.
- Due weeks start on Monday, and "This week" runs through Sunday. Dates are
  the user's local days.
- The UI follows the existing glass look and CSS variables (`--text-hi`,
  `--hairline-lo`, `--sans`, …). Copy patterns from `TodayView.module.css`.
  No `dangerouslySetInnerHTML`.
- `localStorage` access is always wrapped in try/catch.
- Python tests: `.venv/bin/python -m pytest <files> -q`. Frontend tests:
  `npm --prefix frontend test`. Frontend build: `npm --prefix frontend run build`.

## Review Focus

These are the five failure modes most likely to bite the user. No task's
happy-path tests would catch them unless pinned; each has a test in the
owning task.

1. **Re-summarise after the user deleted a wrong item.** Claude sends the
   same item again, and it must not come back. Pinned in Task 4:
   `test_deleted_item_suppresses_readd`.
2. **The WKWebView float id `7.0` on `actions.update`** must work, and
   `True` must be rejected. Pinned in Task 7: `test_update_accepts_float_id_rejects_bool`.
3. **Tag clean-up deleting a tag only an item uses.** Removing the last
   meeting that used tag X must keep X when an item has it. Pinned in Task 2:
   `test_orphan_cleanup_keeps_item_only_tags`.
4. **Deleting a meeting with touched items** must keep the touched or done
   items with `meeting_id NULL` and drop the untouched ones. Pinned in Task 3:
   `test_delete_meeting_keeps_touched_items`.
5. **The week boundary on Sunday and Monday:** an item due Sunday viewed on
   Sunday is "today", and viewed on Monday it is "overdue". An item due
   Monday viewed on Sunday is "later", not "this week". Pinned in Task 1
   (Python `due_bucket`) and Task 9 (TS `dueBucket`).

## Pre-flight rulings (controller, 2026-10-09)

The pre-flight scan applied Tasks 1–7 to a scratch copy and found that every
new test passes, but these existing tests break or these gaps exist. The
fixes below override the task text where they differ.

- **T2:** In `test_backup_restore_preserves_master_data_and_rebuilds_derived`, also insert one `action_item_tags` row (F1). Add `tests/test_user_notes.py` to the run list and change its `user_version == 6` literals to `meeting_store.SCHEMA_VERSION`. Change `test_meeting_store.py:459` to `== 7` (F5). The idempotency test resets `user_version` to 6 with the tables kept, reopens, and asserts one row and version 7 (F7). `_v6_with_notes` uses a plain INSERT (F9). Drop the moot `_meeting` fallback (F20). Add a v6-backup→v7 restore-imports test (F18).
- **T3:** The orphan-tag test asserts `SELECT COUNT(*) FROM tags WHERE name='A'` is 0, not `tag_catalog()` (F6). Use `ai.check_status` instead of duplicating it (F10). Reject a `str` passed as tags (F11). `_now()` ends with `Z` and keeps microseconds (F12). Raise `ValueError` instead of `assert` for column names (F13).
- **T4:** The merge's DELETE calls `delete_untouched_for_meeting` (F10).
- **T5:** Merge the import into the top of `test_summary_format.py`; the `"Owner — task"` needle becomes `"due_phrase"` (F19).
- **T6:** Don't wrap `save_notes` in `_call` (F3). Update the existing exact assertions: tool list, `writers`, instructions `== summary_instructions(settings.get_identity())`, and the tool count changes to 15 (F4).
- **T8:** Write `Action items.md` only when at least one item exists; add `list_action_items() -> []` to `_FakeLibrary` (F2).
- **T9:** Add a `created` sort assertion (F8). The mock details' `actionItems` are built from `MOCK_ACTION_ITEMS` (`import type` only) (F14). Add `MeetingDetail.tsx` to the Files list (F15).
- **T10:** Use the spec §8 wording for the banner and "Nothing due — nice." for the empty state. Show the phrase whenever there is one (F16). Completed starts collapsed only when status is All (F17). Add `forcedSummaryId`, mirroring `forcedNotesId` (F21).

---

### Task 0: Preconditions and worktree (controller, no subagent)

- [ ] **Step 1: Confirm the dependency is merged**

Run: `git log master --oneline | grep -i -m1 "export\|backup\|restore"` and `git show master:speakeasy/meeting_store.py | grep -n "SCHEMA_VERSION ="`
Expected: `SCHEMA_VERSION = 6` on master, and the library export/recovery merge commit present. **If master is still at 5 or the merge is missing, stop and ask the user.** Don't build v7 on top of unmerged v6 work.

- [ ] **Step 2: Create the worktree**

Use superpowers:using-git-worktrees. Branch: `feature/action-items` from local `master` (if `EnterWorktree` was used, run `git merge --ff-only master` first — it branches from origin/master). Then:

```bash
ln -s "<main checkout>/.venv" .venv
npm --prefix frontend ci
```

Never symlink `frontend/node_modules`. Bring the spec and plan into the worktree if master lacks them (they were committed on `codex/library-export-recovery`, so they arrive with that merge).

- [ ] **Step 3: Baseline**

Run: `.venv/bin/python -m pytest -q` (Bash `timeout: 600000`) and `npm --prefix frontend test`
Expected: all pass. Record the counts in this plan under "Progress".

---

### Task 1: Pure helpers — `speakeasy/action_items.py`

**Files:**
- Create: `speakeasy/action_items.py`
- Test: `tests/test_action_items.py`

**Interfaces:**
- Consumes: `speakeasy.tag_names.clean_tag_names(list[str]) -> list[str]` (raises `ValueError` on a tag with no letter or digit).
- Produces (used by Tasks 2–8):
  - Sentinel and constants: `UNSET`, `SIMILAR_TASK_RATIO`, `PRIORITIES`,
    `MAX_ITEMS_PER_SAVE = 50`, `MAX_BULK_IDS = 500`
  - `@dataclass(frozen=True) ItemInput(task: str, owner: str = "", due_date: str | None = None, due_phrase: str = "", priority: str = "normal", tags: tuple[str, ...] = ())`
  - Checkers:
    - `check_id(v) -> int`
    - `check_task(v) -> str`
    - `clean_owner(v) -> str`
    - `check_priority(v) -> str`
    - `check_notes(v) -> str`
    - `check_date(v, name="due_date") -> str | None`
    - `check_tags(v) -> list[str]`
  - Parsing and matching:
    - `parse_legacy(text) -> ItemInput`
    - `validate_input(raw) -> ItemInput`
    - `norm(text) -> str`
    - `similar(a, b) -> float`
  - Identity: `clean_identity(name, aliases) -> dict`, `is_mine(owner, identity) -> bool`
  - Dates and display:
    - `effective_due(due_date, due_override, due_cleared) -> tuple[str | None, str | None]`
    - `due_bucket(due: str | None, today: date) -> str` (one of `overdue`, `today`, `week`, `later`, `none`)
    - `format_mirror(owner, task, due_phrase, due) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_action_items.py
from datetime import date

import pytest

from speakeasy import action_items as ai


@pytest.mark.parametrize("text, owner, task, phrase", [
    ("Jason — Send budget draft (Fri)", "Jason", "Send budget draft", "Fri"),
    ("Unassigned — Book the room", "", "Book the room", ""),
    ("Siam, Refayet – Review the deck (by end of month)", "Siam, Refayet", "Review the deck", "by end of month"),
    ("Follow up - urgent", "", "Follow up - urgent", ""),           # plain hyphen is not an owner split
    ("A very long owner name here — task", "", "A very long owner name here — task", ""),  # 5 words: not an owner
    ("Just a task", "", "Just a task", ""),
    ("(Fri)", "", "(Fri)", ""),                                     # empty task falls back to the original
    ("Jason —   Send   it  ", "Jason", "Send it", ""),
])
def test_parse_legacy(text, owner, task, phrase):
    item = ai.parse_legacy(text)
    assert (item.owner, item.task, item.due_phrase, item.due_date) == (owner, task, phrase, None)


def test_validate_input_object():
    item = ai.validate_input({"task": " Ship  it ", "owner": "unassigned", "due_date": "2026-10-16",
                              "due_phrase": "by Friday", "priority": "high", "tags": ["Budget", "budget"]})
    assert item == ai.ItemInput("Ship it", "", "2026-10-16", "by Friday", "high", ("Budget",))


@pytest.mark.parametrize("raw, message", [
    ({"task": ""}, "task"),
    ({"task": "x" * 501}, "500"),
    ({"task": "ok", "owner": "x" * 81}, "80"),
    ({"task": "ok", "priority": "urgent"}, "priority"),
    ({"task": "ok", "due_date": "2026-02-30"}, "date"),
    ({"task": "ok", "due_date": "1999-12-31"}, "date"),
    ({"task": "ok", "due_phrase": "x" * 81}, "80"),
    ({"task": "ok", "tags": ["t"] * 0 + [f"t{i}" for i in range(11)]}, "10"),
    ({"task": "ok", "colour": "red"}, "colour"),
    (42, "text or an object"),
])
def test_validate_input_rejects(raw, message):
    with pytest.raises(ValueError, match=message):
        ai.validate_input(raw)


def test_check_id_accepts_integral_float_and_rejects_bool():
    assert ai.check_id(7.0) == 7 and ai.check_id(7) == 7
    for bad in (True, 7.5, 0, -1, "7", None):
        with pytest.raises(ValueError):
            ai.check_id(bad)


def test_check_notes_limit_and_keeps_newlines():
    assert ai.check_notes("a\nb  ") == "a\nb"
    with pytest.raises(ValueError, match="5,000"):
        ai.check_notes("x" * 5001)


def test_similar():
    assert ai.norm("Send the Budget-draft!") == "send budget draft"
    assert ai.similar("Send budget draft", "send the budget draft") >= ai.SIMILAR_TASK_RATIO
    assert ai.similar("Book the room", "Book room") >= ai.SIMILAR_TASK_RATIO   # 0.82 without filler removal
    assert ai.similar("Send budget draft", "Book the room") < ai.SIMILAR_TASK_RATIO
    assert ai.similar("Email Siam", "Email Refayet") < ai.SIMILAR_TASK_RATIO


@pytest.mark.parametrize("owner, expected", [
    ("Jason", True), ("jason chiu", True), ("JC", True), ("Siam, Jason", True),
    ("Siam and JC", True), ("Siam & Jas", True), ("Siam", False), ("", False), ("Jasonette", False),
])
def test_is_mine(owner, expected):
    identity = {"name": "Jason Chiu", "aliases": ["JC", "Jas"]}
    assert ai.is_mine(owner, identity) is expected


def test_is_mine_without_name_is_false():
    assert ai.is_mine("Jason", {"name": "", "aliases": ["Jason"]}) is False


def test_clean_identity():
    assert ai.clean_identity("  Jason  Chiu ", ["JC", "jc", " ", "Jas"]) == \
        {"name": "Jason Chiu", "aliases": ["JC", "Jas"]}
    with pytest.raises(ValueError):
        ai.clean_identity("x" * 81, [])
    with pytest.raises(ValueError):
        ai.clean_identity("J", [f"a{i}" for i in range(11)])
    with pytest.raises(ValueError):
        ai.clean_identity("J", ["x" * 41])


def test_effective_due():
    assert ai.effective_due("2026-10-16", None, False) == ("2026-10-16", "claude")
    assert ai.effective_due("2026-10-16", "2026-10-20", False) == ("2026-10-20", "user")
    assert ai.effective_due("2026-10-16", None, True) == (None, "user")
    assert ai.effective_due(None, None, False) == (None, None)


SUN, MON = date(2026, 10, 11), date(2026, 10, 12)

@pytest.mark.parametrize("due, today, bucket", [
    (None, SUN, "none"),
    ("2026-10-11", SUN, "today"), ("2026-10-11", MON, "overdue"),
    ("2026-10-12", SUN, "later"),                 # Monday is next week when today is Sunday
    ("2026-10-18", MON, "week"), ("2026-10-19", MON, "later"),
    ("2026-10-10", SUN, "overdue"),
])
def test_due_bucket(due, today, bucket):
    assert ai.due_bucket(due, today) == bucket


def test_format_mirror():
    assert ai.format_mirror("Jason", "Send it", "by Friday", "2026-10-16") == "Jason — Send it (by Friday)"
    assert ai.format_mirror("", "Send it", "", "2026-10-16") == "Send it (2026-10-16)"
    assert ai.format_mirror("", "Send it", "", None) == "Send it"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_action_items.py -q`
Expected: FAIL. `ModuleNotFoundError: speakeasy.action_items`

- [ ] **Step 3: Implement**

```python
# speakeasy/action_items.py
"""Action items: pure parsing, validation, matching and date rules.

No SQL and no I/O. action_item_store.py, mcp_tools.py and the meetings
bridge build on these; frontend/src/meetings/actionItems.ts mirrors
due_bucket. Every ValueError message is safe to show Claude or the user.
"""

import difflib
import re
from dataclasses import dataclass
from datetime import date, timedelta

from .tag_names import clean_tag_names

SIMILAR_TASK_RATIO = 0.85
PRIORITIES = ("high", "normal", "low")
MAX_TASK, MAX_OWNER, MAX_PHRASE, MAX_NOTES = 500, 80, 80, 5000
MAX_ITEM_TAGS = 10
MAX_ITEMS_PER_SAVE = 50
MAX_BULK_IDS = 500
MAX_NAME, MAX_ALIASES, MAX_ALIAS = 80, 10, 40


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


UNSET = _Unset()  # "argument not given", distinct from None (= clear)


@dataclass(frozen=True)
class ItemInput:
    task: str
    owner: str = ""
    due_date: str | None = None
    due_phrase: str = ""
    priority: str = "normal"
    tags: tuple[str, ...] = ()


_OWNER_DASH = re.compile(r"\s+[—–]\s+")
_TRAILING_PAREN = re.compile(r"\s*\(([^()]{1,80})\)\s*$")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NON_WORD = re.compile(r"[\W_]+")
_OWNER_SPLIT = re.compile(r"\s*(?:,|&|/|\band\b)\s*", re.IGNORECASE)
_FIELDS = {"task", "owner", "due_date", "due_phrase", "priority", "tags"}


def _squash(text: str) -> str:
    return " ".join(text.split())


def check_id(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Action item id must be a number.")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("Action item id must be a whole number.")
    if int(value) < 1:
        raise ValueError("Action item id must be positive.")
    return int(value)


def check_task(value) -> str:
    if not isinstance(value, str) or not _squash(value):
        raise ValueError("Each action item needs a task (text).")
    task = _squash(value)
    if len(task) > MAX_TASK:
        raise ValueError(f"Task is longer than {MAX_TASK} characters.")
    return task


def clean_owner(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("owner must be text.")
    owner = _squash(value)
    if owner.casefold() == "unassigned":
        return ""
    if len(owner) > MAX_OWNER:
        raise ValueError(f"owner is longer than {MAX_OWNER} characters.")
    return owner


def check_priority(value) -> str:
    if value is None or value == "":
        return "normal"
    if value not in PRIORITIES:
        raise ValueError("priority must be high, normal or low.")
    return value


def check_notes(value) -> str:
    if not isinstance(value, str):
        raise ValueError("notes must be text.")
    notes = value.rstrip()
    if len(notes) > MAX_NOTES:
        raise ValueError("Notes are longer than 5,000 characters.")
    return notes


def check_date(value, name: str = "due_date") -> str | None:
    if value is None or value == "":
        return None
    message = f"{name} must be a date like 2026-10-16."
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise ValueError(message)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(message) from None
    if not 2000 <= parsed.year <= 2100:
        raise ValueError(message)
    return value


def check_tags(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise ValueError("tags must be a list of text.")
    tags = clean_tag_names(value)
    if len(tags) > MAX_ITEM_TAGS:
        raise ValueError(f"At most {MAX_ITEM_TAGS} tags per action item.")
    return tags


def _check_phrase(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("due_phrase must be text.")
    phrase = _squash(value)
    if len(phrase) > MAX_PHRASE:
        raise ValueError(f"due_phrase is longer than {MAX_PHRASE} characters.")
    return phrase


def parse_legacy(text) -> ItemInput:
    """'Owner — task (due)' -> parts. Owner only before an em/en dash, and
    only when it looks like names (<= 40 chars, <= 4 words)."""
    original = _squash(str(text))
    rest, owner = original, ""
    m = _OWNER_DASH.search(rest)
    if m:
        head = rest[:m.start()].strip()
        if head and len(head) <= 40 and len(head.split()) <= 4:
            owner, rest = head, rest[m.end():].strip()
    phrase = ""
    p = _TRAILING_PAREN.search(rest)
    if p and p.start() > 0:
        phrase, rest = p.group(1).strip(), rest[:p.start()].strip()
    if not rest:
        return ItemInput(task=original)
    return ItemInput(task=rest, owner=clean_owner(owner[:MAX_OWNER]), due_phrase=phrase)


def validate_input(raw) -> ItemInput:
    if isinstance(raw, str):
        item = parse_legacy(raw)
        return ItemInput(check_task(item.task), item.owner, None, _check_phrase(item.due_phrase))
    if not isinstance(raw, dict):
        raise ValueError("Each action item must be text or an object with task.")
    unknown = sorted(set(raw) - _FIELDS)
    if unknown:
        raise ValueError(f"Unknown action item field: {unknown[0]}.")
    return ItemInput(
        task=check_task(raw.get("task")),
        owner=clean_owner(raw.get("owner")),
        due_date=check_date(raw.get("due_date")),
        due_phrase=_check_phrase(raw.get("due_phrase")),
        priority=check_priority(raw.get("priority")),
        tags=tuple(check_tags(raw.get("tags"))),
    )


# Filler words dropped before comparing, so "Book the room" matches "Book
# room" (0.82 with them kept, under SIMILAR_TASK_RATIO).
_FILLER = frozenset({"a", "an", "the", "to", "of", "for", "with", "and", "on", "in", "at"})


def norm(text: str) -> str:
    words = _NON_WORD.sub(" ", text.casefold()).split()
    return " ".join(w for w in words if w not in _FILLER)


def similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def clean_identity(name, aliases) -> dict:
    if not isinstance(name, str):
        raise ValueError("Your name must be text.")
    if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
        raise ValueError("Other names must be a list of text.")
    name = _squash(name)
    if len(name) > MAX_NAME:
        raise ValueError(f"Your name is longer than {MAX_NAME} characters.")
    kept, seen = [], set()
    for alias in (_squash(a) for a in aliases):
        if not alias or alias.casefold() in seen:
            continue
        if len(alias) > MAX_ALIAS:
            raise ValueError(f"Each other name is at most {MAX_ALIAS} characters.")
        seen.add(alias.casefold())
        kept.append(alias)
    if len(kept) > MAX_ALIASES:
        raise ValueError(f"At most {MAX_ALIASES} other names.")
    return {"name": name, "aliases": kept}


def is_mine(owner: str, identity: dict | None) -> bool:
    identity = identity or {}
    name = (identity.get("name") or "").strip()
    if not name:
        return False
    candidates = [_squash(c).casefold() for c in [name, *identity.get("aliases", [])] if c.strip()]
    for part in _OWNER_SPLIT.split(owner or ""):
        part = _squash(part).casefold()
        if not part:
            continue
        for c in candidates:
            if part == c or part.split()[0] == c.split()[0]:
                return True
    return False


def effective_due(due_date, due_override, due_cleared):
    if due_cleared:
        return None, "user"
    if due_override:
        return due_override, "user"
    if due_date:
        return due_date, "claude"
    return None, None


def due_bucket(due: str | None, today: date) -> str:
    if not due:
        return "none"
    day = date.fromisoformat(due)
    if day < today:
        return "overdue"
    if day == today:
        return "today"
    week_end = today + timedelta(days=6 - today.weekday())  # Sunday
    return "week" if day <= week_end else "later"


def format_mirror(owner: str, task: str, due_phrase: str, due: str | None) -> str:
    text = f"{owner} — {task}" if owner else task
    when = due_phrase or due
    return f"{text} ({when})" if when else text
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_action_items.py -q`
Expected: PASS. Check `difflib` ratios in a REPL if a similarity case fails; don't change `SIMILAR_TASK_RATIO` without asking the controller. The `"(Fri)"` case relies on `p.start() > 0`; the 5-word owner case relies on `len(head.split()) <= 4`.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/action_items.py tests/test_action_items.py
git commit -m "Action items: pure parsing, validation and due rules"
```

---

### Task 2: Schema v7, legacy import, orphan-tag fix, backup coverage

**Files:**
- Modify: `speakeasy/meeting_store.py` (add `_migrate_v7`, `SCHEMA_VERSION = 7`, append to `_MIGRATIONS`)
- Modify: `speakeasy/meeting_library.py:1085-1092` (`_drop_orphan_tags`)
- Modify: `tests/test_library_backup.py:13` (`MASTER` gains `"action_items", "action_item_tags"`)
- Test: `tests/test_action_item_schema.py`

**Interfaces:**
- Consumes: `action_items.parse_legacy`.
- Produces: tables exactly as in spec §1. `meeting_store.SCHEMA_VERSION == 7`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_action_item_schema.py
import json
import sqlite3

from speakeasy import meeting_store
from speakeasy.meeting_library import MeetingLibrary
from tests.test_library_backup import _meeting  # reuse the existing NewMeeting helper


def _v6_with_notes(path, items):
    """A v6 library holding one meeting whose notes carry legacy strings."""
    lib = MeetingLibrary(path)
    mid = _meeting(lib, "Legacy")
    conn = sqlite3.connect(path)
    conn.execute("UPDATE notes SET action_items_json = ? WHERE meeting_id = ?", (json.dumps(items), mid)) \
        if conn.execute("SELECT 1 FROM notes WHERE meeting_id = ?", (mid,)).fetchone() else \
        conn.execute("INSERT INTO notes (meeting_id, summary, action_items_json, action_items_text,"
                     " updated_at, updated_by) VALUES (?, 's', ?, '', '2026-10-01T10:00:00Z', 'claude')",
                     (mid, json.dumps(items)))
    conn.execute("DROP TABLE action_item_tags")
    conn.execute("DROP TABLE action_items")
    conn.execute("PRAGMA user_version = 6")
    conn.commit()
    conn.close()
    return mid


def test_fresh_library_is_v7_with_tables(library_path):
    conn = meeting_store.connect(library_path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 7 == meeting_store.SCHEMA_VERSION
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"action_items", "action_item_tags"} <= names


def test_v6_items_import_as_open_summary_items(library_path):
    mid = _v6_with_notes(library_path, ["Jason — Send budget (Fri)", "Unassigned — Book room", "  ", "Plain task"])
    conn = meeting_store.connect(library_path)
    rows = conn.execute("SELECT task, owner, due_phrase, due_date, status, source, user_touched,"
                        " position, created_at, updated_by FROM action_items WHERE meeting_id = ?"
                        " ORDER BY position", (mid,)).fetchall()
    assert [tuple(r) for r in rows] == [
        ("Send budget", "Jason", "Fri", None, "open", "summary", 0, 0, "2026-10-01T10:00:00Z", "claude"),
        ("Book room", "", "", None, "open", "summary", 0, 1, "2026-10-01T10:00:00Z", "claude"),
        ("Plain task", "", "", None, "open", "summary", 0, 2, "2026-10-01T10:00:00Z", "claude"),
    ]


def test_migration_is_idempotent_on_reopen(library_path):
    _v6_with_notes(library_path, ["A task"])
    meeting_store.connect(library_path).close()
    conn = meeting_store.connect(library_path)
    assert conn.execute("SELECT COUNT(*) FROM action_items").fetchone()[0] == 1


def test_orphan_cleanup_keeps_item_only_tags(library_path):
    lib = MeetingLibrary(library_path)
    mid = _meeting(lib, "Tagged")
    lib.save_notes(mid, tags=["Budget"])
    conn = meeting_store.connect(library_path)
    tag_id = conn.execute("SELECT id FROM tags WHERE name = 'Budget'").fetchone()[0]
    conn.execute("INSERT INTO action_items (meeting_id, task, source, created_at, updated_at, updated_by)"
                 " VALUES (NULL, 'Keep tag', 'manual', 'x', 'x', 'user')")
    conn.execute("INSERT INTO action_item_tags (item_id, tag_id) VALUES (last_insert_rowid(), ?)", (tag_id,))
    conn.commit()
    conn.close()
    lib.delete(mid)  # last meeting using Budget
    conn = meeting_store.connect(library_path)
    assert conn.execute("SELECT COUNT(*) FROM tags WHERE name = 'Budget'").fetchone()[0] == 1
```

If `tests/test_library_backup.py` has no reusable `_meeting(lib, title) -> id` helper, move the one it uses into `tests/conftest.py` as a plain function `make_meeting(lib, title)` and import that instead. Keep the existing tests passing.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_action_item_schema.py -q`
Expected: FAIL (user_version 6, no tables).

- [ ] **Step 3: Implement the migration** (in `meeting_store.py`, after `_SCHEMA_V6`)

```python
# v7: action items as rows (spec 2026-10-09-action-items-design.md §1).
# Python, like v3: the import parses legacy "Owner — task (due)" strings.
# Takes the write lock first, then checks whether another connection already
# created the table, so concurrent first opens converge.
_ACTION_ITEMS_DDL = (
    "CREATE TABLE action_items ("
    " id INTEGER PRIMARY KEY,"
    " meeting_id TEXT REFERENCES meetings(id) ON DELETE SET NULL,"
    " task TEXT NOT NULL,"
    " owner TEXT NOT NULL DEFAULT '',"
    " priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('high','normal','low')),"
    " status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done')),"
    " completed_at TEXT,"
    " due_date TEXT,"
    " due_phrase TEXT NOT NULL DEFAULT '',"
    " due_override TEXT,"
    " due_cleared INTEGER NOT NULL DEFAULT 0,"
    " notes TEXT NOT NULL DEFAULT '',"
    " source TEXT NOT NULL CHECK (source IN ('summary','manual')),"
    " user_touched INTEGER NOT NULL DEFAULT 0,"
    " position INTEGER NOT NULL DEFAULT 0,"
    " created_at TEXT NOT NULL,"
    " updated_at TEXT NOT NULL,"
    " updated_by TEXT NOT NULL CHECK (updated_by IN ('claude','user')),"
    " deleted_at TEXT)",
    "CREATE INDEX action_items_meeting ON action_items(meeting_id)",
    "CREATE TABLE action_item_tags ("
    " item_id INTEGER NOT NULL REFERENCES action_items(id) ON DELETE CASCADE,"
    " tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,"
    " PRIMARY KEY (item_id, tag_id))",
)


def _migrate_v7(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'action_items'").fetchone()
        if not exists:
            for statement in _ACTION_ITEMS_DDL:
                conn.execute(statement)
            _import_legacy_action_items(conn)
        conn.execute("PRAGMA user_version = 7")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _import_legacy_action_items(conn: sqlite3.Connection) -> None:
    """Every notes.action_items_json string becomes an open summary item.
    Indexed access: migrate() may run without the Row factory."""
    from .action_items import parse_legacy
    rows = conn.execute("SELECT meeting_id, action_items_json, updated_at FROM notes").fetchall()
    for meeting_id, items_json, updated_at in rows:
        try:
            items = json.loads(items_json)
        except ValueError:
            continue
        if not isinstance(items, list):
            continue
        position = 0
        for raw in items:
            text = " ".join(str(raw).split())[:500]
            if not text:
                continue
            item = parse_legacy(text)
            conn.execute(
                "INSERT INTO action_items (meeting_id, task, owner, due_phrase, source,"
                " position, created_at, updated_at, updated_by)"
                " VALUES (?, ?, ?, ?, 'summary', ?, ?, ?, 'claude')",
                (meeting_id, item.task[:500], item.owner, item.due_phrase[:80],
                 position, updated_at, updated_at))
            position += 1
```

Then set `SCHEMA_VERSION = 7` and change `_MIGRATIONS` to end with `(6, _SCHEMA_V6), (7, _migrate_v7)]`. Add `import json` if absent. Update the v6 comment to say v7 likewise fences v6 builds.

- [ ] **Step 4: Fix orphan clean-up** (`meeting_library.py`, `_drop_orphan_tags`): add the line `" AND id NOT IN (SELECT tag_id FROM action_item_tags)"` and extend the comment ("…or an action item's own tag").

- [ ] **Step 5: Backup coverage.** Add `"action_items", "action_item_tags"` to `MASTER` in `tests/test_library_backup.py`. In `test_backup_restore_preserves_master_data_and_rebuilds_derived`, the existing `save_notes(... action_items=["Send it"])` now produces an item row (after Task 4; before Task 4 the migration path does not create it, so add one row directly with SQL in that test to make the MASTER equality meaningful now).

- [ ] **Step 6: Run**

Run: `.venv/bin/python -m pytest tests/test_action_item_schema.py tests/test_meeting_store.py tests/test_library_backup.py tests/test_meeting_tags.py -q`
Expected: PASS. If an existing test asserts version 6 literally, change it to `meeting_store.SCHEMA_VERSION`.

- [ ] **Step 7: Commit**

```bash
git add speakeasy/meeting_store.py speakeasy/meeting_library.py tests/
git commit -m "Schema v7: action_items tables with legacy import"
```

---

### Task 3: Store + library CRUD, mirror, meeting deletion

**Files:**
- Create: `speakeasy/action_item_store.py`
- Modify: `speakeasy/meeting_library.py`: import, thin methods, `delete()`, `StoredMeeting.action_items`, re-export `ActionItemNotFound`
- Test: `tests/test_action_item_store.py`

**Interfaces:**
- Consumes: Task 1 helpers; `MeetingLibrary._find_tag(conn, name) -> int | None`, `MeetingLibrary._create_tag(conn, name) -> int`, `MeetingLibrary._drop_orphan_tags(conn)`, `MeetingLibrary._transaction()`.
- Produces:
  - `ActionItemNotFound(LookupError)`
  - the `ActionItem` dataclass (fields exactly as spec §5)
  - store functions:
    - `list_items(conn, *, meeting_id=None, include_deleted=False) -> list[ActionItem]`
    - `get_item(conn, item_id) -> ActionItem`
    - `insert_item(conn, inp, *, meeting_id, source, updated_by, position=0, notes="", due_override=None, touched=False) -> int`
    - `update_item(conn, item_id, sets: dict, *, updated_by, tags=None, resolve=None) -> None`
    - `set_own_tags(conn, item_id, names, resolve)`
    - `sync_mirror(conn, meeting_id)`
    - `delete_untouched_for_meeting(conn, meeting_id)`
  - `MeetingLibrary` methods, with signatures exactly as spec §5:
    `list_action_items`, `get_action_item`, `create_action_item`,
    `update_action_item`, `delete_action_item`, `restore_action_item`,
    `bulk_update_action_items`
  - `MeetingLibrary._resolve_or_create_tag(conn, name) -> int`
  - `StoredMeeting.action_items: list[ActionItem]` (default empty list), filled by `get_meeting`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_action_item_store.py
import pytest

from speakeasy.action_item_store import ActionItemNotFound
from speakeasy.meeting_library import MeetingLibrary
from tests.test_library_backup import _meeting   # or tests.conftest.make_meeting (see Task 2)


@pytest.fixture
def lib(library_path):
    return MeetingLibrary(library_path)


def test_create_manual_item_without_meeting(lib):
    item = lib.create_action_item(task="Call the bank", owner="Jason", due="2026-10-20",
                                  priority="high", notes="ask about fees", tags=["Money"])
    assert (item.task, item.owner, item.priority, item.status, item.source) == \
        ("Call the bank", "Jason", "high", "open", "manual")
    assert (item.effective_due, item.due_source, item.due_override) == ("2026-10-20", "user", "2026-10-20")
    assert item.tags == ["Money"] and item.meeting_id is None and item.user_touched
    assert lib.list_action_items()[0].id == item.id


def test_create_on_missing_meeting_raises(lib):
    from speakeasy.meeting_library import MeetingNotFound
    with pytest.raises(MeetingNotFound):
        lib.create_action_item(task="x", meeting_id="20260101-000000-dead")


def test_update_status_stamps_and_clears_completed_at(lib):
    item = lib.create_action_item(task="Do it")
    done = lib.update_action_item(item.id, updated_by="user", status="done")
    assert done.status == "done" and done.completed_at
    reopened = lib.update_action_item(item.id, updated_by="user", status="open")
    assert reopened.completed_at is None


def test_due_semantics(lib, library_path):
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=[{"task": "Ship", "due_date": "2026-10-16",
                                                    "due_phrase": "by Friday"}])
    item = lib.list_action_items(meeting_id=mid)[0]
    assert (item.effective_due, item.due_source) == ("2026-10-16", "claude")
    item = lib.update_action_item(item.id, updated_by="user", due="2026-10-19")
    assert (item.effective_due, item.due_source) == ("2026-10-19", "user")
    item = lib.update_action_item(item.id, updated_by="user", due=None)
    assert (item.effective_due, item.due_source, item.due_cleared) == (None, "user", True)
    item = lib.update_action_item(item.id, updated_by="user", due_reset=True)
    assert (item.effective_due, item.due_source, item.due_cleared) == ("2026-10-16", "claude", False)


def test_update_marks_touched_and_rewrites_mirror(lib):
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=["Jason — Send it (Fri)"])
    item = lib.list_action_items(meeting_id=mid)[0]
    assert not item.user_touched
    item = lib.update_action_item(item.id, updated_by="claude", task="Send it today", notes="n")
    assert item.user_touched and item.updated_by == "claude"
    assert lib.get_meeting(mid, with_segments=False).notes.action_items == ["Jason — Send it today (Fri)"]


def test_tags_replace_and_orphans_dropped(lib):
    item = lib.create_action_item(task="t", tags=["A", "B"])
    item = lib.update_action_item(item.id, updated_by="user", tags=["b", "C"])
    assert item.tags == ["B", "C"]                       # existing tag keeps its display name
    assert "A" not in [t.name for t in lib.tag_catalog()]


def test_meeting_tags_are_reported(lib):
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", tags=["Budget"], action_items=["Do"])
    assert lib.list_action_items(meeting_id=mid)[0].meeting_tags == ["Budget"]


def test_soft_delete_and_restore(lib):
    item = lib.create_action_item(task="t")
    lib.delete_action_item(item.id, updated_by="user")
    assert lib.list_action_items() == []
    assert lib.list_action_items(include_deleted=True)[0].deleted_at
    assert lib.restore_action_item(item.id, updated_by="user").deleted_at is None


def test_not_found(lib):
    for call in (lambda: lib.get_action_item(999), lambda: lib.update_action_item(999, updated_by="user", notes="x"),
                 lambda: lib.delete_action_item(999, updated_by="user")):
        with pytest.raises(ActionItemNotFound):
            call()


def test_bulk_update(lib):
    a, b = lib.create_action_item(task="a"), lib.create_action_item(task="b", tags=["X"])
    items = lib.bulk_update_action_items([a.id, float(b.id)], updated_by="user", status="done",
                                         add_tags=["Y"], remove_tags=["X"])
    assert [(i.status, i.tags) for i in items] == [("done", ["Y"]), ("done", ["Y"])]
    lib.bulk_update_action_items([a.id], updated_by="user", delete=True)
    assert [i.id for i in lib.list_action_items()] == [b.id]
    with pytest.raises(ValueError, match="500"):
        lib.bulk_update_action_items(list(range(1, 502)), updated_by="user", status="done")


def test_delete_meeting_keeps_touched_items(lib):
    mid = _meeting(lib, "Gone")
    lib.save_notes(mid, summary="s", action_items=["Untouched", "Touched", "Done"])
    items = {i.task: i for i in lib.list_action_items(meeting_id=mid)}
    lib.update_action_item(items["Touched"].id, updated_by="user", notes="mine")
    lib.update_action_item(items["Done"].id, updated_by="user", status="done")
    manual = lib.create_action_item(task="Manual", meeting_id=mid)
    lib.delete(mid)
    left = {i.task: i for i in lib.list_action_items()}
    assert set(left) == {"Touched", "Done", "Manual"}
    assert all(i.meeting_id is None for i in left.values())
    assert manual.id in {i.id for i in left.values()}


def test_get_meeting_includes_items(lib):
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=["A", "B"])
    assert [i.task for i in lib.get_meeting(mid, with_segments=False).action_items] == ["A", "B"]
```

Note: `test_due_semantics`, `test_update_marks_touched…`, `test_meeting_tags…` and the delete test need Task 4's structured `save_notes`. Write them now; they stay red until Task 4. In this task, run only the tests that don't call `save_notes(action_items=…)`: `-k "not due_semantics and not mirror and not meeting_tags and not keeps_touched and not includes_items"`. Task 4 runs the whole file.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_action_item_store.py -q`
Expected: FAIL. `ModuleNotFoundError: speakeasy.action_item_store`

- [ ] **Step 3: Implement `speakeasy/action_item_store.py`**

```python
"""SQL for action items. Every function takes an open connection inside the
caller's transaction; MeetingLibrary wraps each one (spec §5)."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from . import action_items as ai


class ActionItemNotFound(LookupError):
    pass


@dataclass
class ActionItem:
    id: int
    meeting_id: str | None
    meeting_title: str | None
    meeting_started_at: str | None
    meeting_tz_offset_minutes: int | None
    task: str
    owner: str
    priority: str
    status: str
    completed_at: str | None
    due_date: str | None
    due_phrase: str
    due_override: str | None
    due_cleared: bool
    effective_due: str | None
    due_source: str | None
    notes: str
    source: str
    user_touched: bool
    tags: list[str]
    meeting_tags: list[str]
    position: int
    created_at: str
    updated_at: str
    updated_by: str
    deleted_at: str | None


_UPDATABLE = {"task", "owner", "priority", "status", "notes", "due_override",
              "due_cleared", "deleted_at", "completed_at"}
_SELECT = ("SELECT a.*, m.title AS meeting_title, m.started_at AS meeting_started_at,"
           " m.tz_offset_minutes AS meeting_tz FROM action_items a"
           " LEFT JOIN meetings m ON m.id = a.meeting_id")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _names(conn, sql, params=()) -> dict:
    out: dict = {}
    for key, name in conn.execute(sql, params):
        out.setdefault(key, []).append(name)
    return out


def _rows_to_items(conn, rows) -> list[ActionItem]:
    if not rows:
        return []
    own = _names(conn, "SELECT at.item_id, t.name FROM action_item_tags at"
                       " JOIN tags t ON t.id = at.tag_id ORDER BY t.name COLLATE NOCASE")
    meeting = _names(conn, "SELECT mt.meeting_id, t.name FROM meeting_tags mt"
                           " JOIN tags t ON t.id = mt.tag_id ORDER BY t.name COLLATE NOCASE")
    items = []
    for r in rows:
        due, source = ai.effective_due(r["due_date"], r["due_override"], r["due_cleared"])
        items.append(ActionItem(
            id=r["id"], meeting_id=r["meeting_id"], meeting_title=r["meeting_title"],
            meeting_started_at=r["meeting_started_at"], meeting_tz_offset_minutes=r["meeting_tz"],
            task=r["task"], owner=r["owner"], priority=r["priority"], status=r["status"],
            completed_at=r["completed_at"], due_date=r["due_date"], due_phrase=r["due_phrase"],
            due_override=r["due_override"], due_cleared=bool(r["due_cleared"]),
            effective_due=due, due_source=source, notes=r["notes"], source=r["source"],
            user_touched=bool(r["user_touched"]), tags=own.get(r["id"], []),
            meeting_tags=meeting.get(r["meeting_id"], []), position=r["position"],
            created_at=r["created_at"], updated_at=r["updated_at"],
            updated_by=r["updated_by"], deleted_at=r["deleted_at"]))
    return items


def list_items(conn, *, meeting_id=None, include_deleted=False) -> list[ActionItem]:
    where, params = [], []
    if meeting_id is not None:
        where.append("a.meeting_id = ?")
        params.append(meeting_id)
    if not include_deleted:
        where.append("a.deleted_at IS NULL")
    sql = _SELECT + (" WHERE " + " AND ".join(where) if where else "") + \
        " ORDER BY a.meeting_id IS NULL, m.started_at DESC, a.position, a.id"
    return _rows_to_items(conn, conn.execute(sql, params).fetchall())


def get_item(conn, item_id: int) -> ActionItem:
    rows = conn.execute(_SELECT + " WHERE a.id = ?", (item_id,)).fetchall()
    if not rows:
        raise ActionItemNotFound(item_id)
    return _rows_to_items(conn, rows)[0]


def insert_item(conn, inp: ai.ItemInput, *, meeting_id, source, updated_by, position=0,
                notes="", due_override=None, touched=False) -> int:
    now = _now()
    return conn.execute(
        "INSERT INTO action_items (meeting_id, task, owner, priority, due_date, due_phrase,"
        " due_override, notes, source, user_touched, position, created_at, updated_at, updated_by)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (meeting_id, inp.task, inp.owner, inp.priority, inp.due_date, inp.due_phrase,
         due_override, notes, source, int(touched), position, now, now, updated_by)).lastrowid


def set_own_tags(conn, item_id: int, names, resolve) -> None:
    conn.execute("DELETE FROM action_item_tags WHERE item_id = ?", (item_id,))
    for name in names:
        conn.execute("INSERT OR IGNORE INTO action_item_tags (item_id, tag_id) VALUES (?, ?)",
                     (item_id, resolve(conn, name)))


def update_item(conn, item_id: int, sets: dict, *, updated_by, tags=None, resolve=None) -> None:
    row = conn.execute("SELECT meeting_id, status FROM action_items WHERE id = ?",
                       (item_id,)).fetchone()
    if row is None:
        raise ActionItemNotFound(item_id)
    sets = dict(sets)
    assert set(sets) <= _UPDATABLE, sets   # column names come from code, never from callers
    if "status" in sets and sets["status"] != row["status"]:
        sets["completed_at"] = _now() if sets["status"] == "done" else None
    sets.update(user_touched=1, updated_at=_now(), updated_by=updated_by)
    cols = ", ".join(f"{k} = ?" for k in sets)
    conn.execute(f"UPDATE action_items SET {cols} WHERE id = ?", (*sets.values(), item_id))
    if tags is not None:
        set_own_tags(conn, item_id, tags, resolve)
    if row["meeting_id"]:
        sync_mirror(conn, row["meeting_id"])


def sync_mirror(conn, meeting_id: str) -> None:
    """notes.action_items_json/_text follow the items (search + old readers).
    Only an existing notes row is updated: creating one would make an
    unsummarised meeting look summarised."""
    rows = conn.execute(
        "SELECT owner, task, due_phrase, due_date, due_override, due_cleared FROM action_items"
        " WHERE meeting_id = ? AND deleted_at IS NULL ORDER BY position, id",
        (meeting_id,)).fetchall()
    lines = [ai.format_mirror(r["owner"], r["task"], r["due_phrase"],
                              ai.effective_due(r["due_date"], r["due_override"], r["due_cleared"])[0])
             for r in rows]
    conn.execute("UPDATE notes SET action_items_json = ?, action_items_text = ? WHERE meeting_id = ?",
                 (json.dumps(lines), "\n".join(lines), meeting_id))


def delete_untouched_for_meeting(conn, meeting_id: str) -> None:
    conn.execute("DELETE FROM action_items WHERE meeting_id = ? AND source = 'summary'"
                 " AND user_touched = 0 AND status = 'open'", (meeting_id,))
```

- [ ] **Step 4: Library methods** (`meeting_library.py`, new section `# -- action items --` after the tags section). Add these imports:

```python
from . import action_item_store, action_items as ai
from .action_item_store import ActionItem, ActionItemNotFound  # re-exported
```

```python
    def _resolve_or_create_tag(self, conn, name) -> int:
        return self._find_tag(conn, name) or self._create_tag(conn, name)

    def list_action_items(self, *, meeting_id=None, include_deleted=False) -> list[ActionItem]:
        with self._transaction() as conn:
            return action_item_store.list_items(conn, meeting_id=meeting_id,
                                                include_deleted=include_deleted)

    def get_action_item(self, item_id) -> ActionItem:
        with self._transaction() as conn:
            return action_item_store.get_item(conn, ai.check_id(item_id))

    def create_action_item(self, *, task, owner="", meeting_id=None, due=None, priority="normal",
                           notes="", tags=(), updated_by="user") -> ActionItem:
        inp = ai.ItemInput(ai.check_task(task), ai.clean_owner(owner), None, "",
                           ai.check_priority(priority), tuple(ai.check_tags(list(tags))))
        due_override, notes = ai.check_date(due, "due"), ai.check_notes(notes or "")
        if meeting_id is not None:
            _check_id(meeting_id)
        with self._transaction() as conn:
            if meeting_id is not None and conn.execute(
                    "SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)).fetchone() is None:
                raise MeetingNotFound(meeting_id)
            item_id = action_item_store.insert_item(
                conn, inp, meeting_id=meeting_id, source="manual", updated_by=updated_by,
                position=1_000_000, notes=notes, due_override=due_override, touched=True)
            action_item_store.set_own_tags(conn, item_id, inp.tags, self._resolve_or_create_tag)
            if meeting_id:
                action_item_store.sync_mirror(conn, meeting_id)
            return action_item_store.get_item(conn, item_id)

    def update_action_item(self, item_id, *, updated_by, task=ai.UNSET, owner=ai.UNSET,
                           priority=ai.UNSET, status=ai.UNSET, notes=ai.UNSET, due=ai.UNSET,
                           due_reset=False, tags=ai.UNSET) -> ActionItem:
        item_id = ai.check_id(item_id)
        sets = {}
        if task is not ai.UNSET:
            sets["task"] = ai.check_task(task)
        if owner is not ai.UNSET:
            sets["owner"] = ai.clean_owner(owner)
        if priority is not ai.UNSET:
            sets["priority"] = ai.check_priority(priority)
        if status is not ai.UNSET:
            if status not in ("open", "done"):
                raise ValueError("status must be open or done.")
            sets["status"] = status
        if notes is not ai.UNSET:
            sets["notes"] = ai.check_notes(notes)
        if due_reset:
            sets.update(due_override=None, due_cleared=0)
        elif due is not ai.UNSET:
            day = ai.check_date(due, "due")
            sets.update(due_override=day, due_cleared=0 if day else 1)
        names = None if tags is ai.UNSET else ai.check_tags(tags)
        with self._transaction() as conn:
            action_item_store.update_item(conn, item_id, sets, updated_by=updated_by,
                                          tags=names, resolve=self._resolve_or_create_tag)
            if names is not None:
                self._drop_orphan_tags(conn)
            return action_item_store.get_item(conn, item_id)

    def delete_action_item(self, item_id, *, updated_by) -> None:
        with self._transaction() as conn:
            action_item_store.update_item(conn, ai.check_id(item_id),
                                          {"deleted_at": action_item_store._now()},
                                          updated_by=updated_by)

    def restore_action_item(self, item_id, *, updated_by) -> ActionItem:
        item_id = ai.check_id(item_id)
        with self._transaction() as conn:
            action_item_store.update_item(conn, item_id, {"deleted_at": None}, updated_by=updated_by)
            return action_item_store.get_item(conn, item_id)

    def bulk_update_action_items(self, ids, *, updated_by, status=ai.UNSET, add_tags=(),
                                 remove_tags=(), delete=False) -> list[ActionItem]:
        if not isinstance(ids, list) or not ids:
            raise ValueError("ids must list at least one action item.")
        if len(ids) > ai.MAX_BULK_IDS:
            raise ValueError(f"At most {ai.MAX_BULK_IDS} action items at once.")
        ids = list(dict.fromkeys(ai.check_id(i) for i in ids))
        add, remove = ai.check_tags(list(add_tags)), ai.check_tags(list(remove_tags))
        sets = {}
        if status is not ai.UNSET:
            if status not in ("open", "done"):
                raise ValueError("status must be open or done.")
            sets["status"] = status
        if delete:
            sets["deleted_at"] = action_item_store._now()
        with self._transaction() as conn:
            for item_id in ids:
                current = action_item_store.get_item(conn, item_id)
                names = None
                if add or remove:
                    gone = {self._find_tag(conn, n) for n in remove} - {None}
                    keep = [t for t in current.tags if self._find_tag(conn, t) not in gone]
                    names = keep + [n for n in add if self._find_tag(conn, n) not in
                                    {self._find_tag(conn, k) for k in keep}]
                action_item_store.update_item(conn, item_id, sets, updated_by=updated_by,
                                              tags=names, resolve=self._resolve_or_create_tag)
            self._drop_orphan_tags(conn)
            return [action_item_store.get_item(conn, i) for i in ids]
```

In `delete()`, call `action_item_store.delete_untouched_for_meeting(conn, meeting_id)` **before** `DELETE FROM meetings`. In `StoredMeeting`, add `action_items: list = field(default_factory=list)`. In `get_meeting`, set it from `action_item_store.list_items(conn, meeting_id=meeting_id)`.

- [ ] **Step 5: Run**

Run: `.venv/bin/python -m pytest tests/test_action_item_store.py -q -k "not due_semantics and not mirror and not meeting_tags and not keeps_touched and not includes_items"` then `.venv/bin/python -m pytest tests/test_meeting_library.py tests/test_meeting_tags.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add speakeasy/action_item_store.py speakeasy/meeting_library.py tests/test_action_item_store.py
git commit -m "Action items: library CRUD, soft delete, bulk, mirror"
```

---

### Task 4: `save_notes` merge (keep edited, replace untouched)

**Files:**
- Modify: `speakeasy/meeting_library.py:869-915` (`save_notes`)
- Modify: `speakeasy/action_item_store.py` (add `merge_summary_items`)
- Test: `tests/test_action_item_merge.py` (plus the Task 3 tests that were deferred)

**Interfaces:**
- Consumes: Task 1 `validate_input`, `similar`, `SIMILAR_TASK_RATIO`, `MAX_ITEMS_PER_SAVE`; Task 3 store functions.
- Produces:
  - `save_notes(meeting_id, *, action_items: list[str | dict] | None, ...)`, whose
    return `Notes.action_items` is the mirror (a list of strings)
  - `action_item_store.merge_summary_items(conn, meeting_id, inputs, resolve, *, updated_by) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_action_item_merge.py
import pytest

from speakeasy.meeting_library import MeetingLibrary
from tests.test_library_backup import _meeting


@pytest.fixture
def lib(library_path):
    return MeetingLibrary(library_path)


def tasks(lib, mid):
    return [i.task for i in lib.list_action_items(meeting_id=mid)]


def test_untouched_items_are_replaced(lib):
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=["Old one", "Old two"])
    lib.save_notes(mid, action_items=["New one"])
    assert tasks(lib, mid) == ["New one"]


def test_touched_done_manual_items_are_kept_and_not_duplicated(lib):
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=["Send the budget draft", "Book room", "Email Siam"])
    items = {i.task: i for i in lib.list_action_items(meeting_id=mid)}
    lib.update_action_item(items["Send the budget draft"].id, updated_by="user", notes="n")
    lib.update_action_item(items["Book room"].id, updated_by="user", status="done")
    lib.create_action_item(task="My own thing", meeting_id=mid)
    lib.save_notes(mid, action_items=["Send budget draft", "Book the room", "Brand new", "Email Siam"])
    assert sorted(tasks(lib, mid)) == sorted(
        ["Send the budget draft", "Book room", "My own thing", "Brand new", "Email Siam"])


def test_deleted_item_suppresses_readd(lib):
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=["Wrong item"])
    lib.delete_action_item(lib.list_action_items(meeting_id=mid)[0].id, updated_by="user")
    lib.save_notes(mid, action_items=["Wrong item", "Right item"])
    assert tasks(lib, mid) == ["Right item"]


def test_structured_items_and_order(lib):
    mid = _meeting(lib, "M")
    notes = lib.save_notes(mid, summary="s", action_items=[
        {"task": "B", "owner": "Jason", "due_date": "2026-10-16", "due_phrase": "Fri",
         "priority": "high", "tags": ["Budget"]},
        "Siam — A"])
    items = lib.list_action_items(meeting_id=mid)
    assert [(i.task, i.owner, i.priority, i.tags, i.position) for i in items] == [
        ("B", "Jason", "high", ["Budget"], 0), ("A", "Siam", "normal", [], 1)]
    assert notes.action_items == ["Jason — B (Fri)", "Siam — A"]


def test_limits_and_bad_items_save_nothing(lib):
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=["Keep"])
    with pytest.raises(ValueError, match="50"):
        lib.save_notes(mid, action_items=[f"t{i}" for i in range(51)])
    with pytest.raises(ValueError, match="priority"):
        lib.save_notes(mid, summary="changed", action_items=[{"task": "x", "priority": "urgent"}])
    assert tasks(lib, mid) == ["Keep"]
    assert lib.get_meeting(mid, with_segments=False).notes.summary == "s"


def test_blank_strings_are_dropped(lib):
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=["  ", "Real"])
    assert tasks(lib, mid) == ["Real"]


def test_omitting_action_items_leaves_items_alone(lib):
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=["Keep"])
    lib.save_notes(mid, summary="new summary")
    assert tasks(lib, mid) == ["Keep"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_action_item_merge.py -q`
Expected: FAIL (`save_notes` still stores strings only; no item rows).

- [ ] **Step 3: Implement.** Add to `action_item_store.py`:

```python
def merge_summary_items(conn, meeting_id: str, inputs, resolve, *, updated_by) -> None:
    """Spec §2: keep manual/touched/done/deleted items, replace the rest, and
    skip incoming items that match a kept one."""
    kept = [r["task"] for r in conn.execute(
        "SELECT task FROM action_items WHERE meeting_id = ? AND (source = 'manual'"
        " OR user_touched = 1 OR status = 'done' OR deleted_at IS NOT NULL)", (meeting_id,))]
    conn.execute("DELETE FROM action_items WHERE meeting_id = ? AND source = 'summary'"
                 " AND user_touched = 0 AND status = 'open' AND deleted_at IS NULL", (meeting_id,))
    for position, inp in enumerate(inputs):
        if any(ai.similar(inp.task, k) >= ai.SIMILAR_TASK_RATIO for k in kept):
            continue
        item_id = insert_item(conn, inp, meeting_id=meeting_id, source="summary",
                              updated_by=updated_by, position=position)
        if inp.tags:
            set_own_tags(conn, item_id, inp.tags, resolve)
    sync_mirror(conn, meeting_id)
```

In `save_notes`, replace the action-items validation block with:

```python
        inputs = None
        if action_items is not None:
            raw_items = [a for a in action_items if not (isinstance(a, str) and not a.strip())]
            if len(raw_items) > ai.MAX_ITEMS_PER_SAVE:
                raise ValueError(f"At most {ai.MAX_ITEMS_PER_SAVE} action items.")
            inputs = [ai.validate_input(a) for a in raw_items]
```

Inside the transaction, the upsert always writes `current.action_items`, so
the mirror is rewritten afterwards. Delete the line
`action_items = current.action_items if action_items is None else action_items`,
then pass `json.dumps(current.action_items)` and
`"\n".join(current.action_items)`. Right after the upsert (so the notes row
exists for the mirror), add:

```python
            if inputs is not None:
                action_item_store.merge_summary_items(
                    conn, meeting_id, inputs, self._resolve_or_create_tag, updated_by=updated_by)
```

All validation happens before the transaction, so a bad item saves nothing.

- [ ] **Step 4: Run**

Run: `.venv/bin/python -m pytest tests/test_action_item_merge.py tests/test_action_item_store.py tests/test_meeting_library.py tests/test_meeting_library_mcp.py tests/test_mcp_tools.py tests/test_library_backup.py -q`
Expected: PASS. Existing tests that asserted the old error text (`"At most 50 action items of 500 characters each."`) or the replace-everything rule get updated to the new messages and behaviour. List each changed assertion in the report.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/ tests/
git commit -m "save_notes: merge action items, keeping the user's edits"
```

---

### Task 5: Identity setting + summary instructions

**Files:**
- Modify: `speakeasy/settings.py` (add `get_identity` / `set_identity`)
- Modify: `speakeasy/summary_format.py` (action-item rule text + `summary_instructions(identity)`)
- Test: `tests/test_settings.py` (append), `tests/test_summary_format.py` (append)

**Interfaces:**
- Produces:
  - `settings.get_identity() -> {"name": str, "aliases": list[str]}` (defaults to `{"name": "", "aliases": []}`)
  - `settings.set_identity(name=None, aliases=None) -> dict` (validated with `action_items.clean_identity`)
  - `summary_format.summary_instructions(identity: dict) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_settings.py (append)
def test_identity_round_trip_and_validation():
    from speakeasy import settings
    assert settings.get_identity() == {"name": "", "aliases": []}
    assert settings.set_identity(name=" Jason ", aliases=["JC", "jc"]) == {"name": "Jason", "aliases": ["JC"]}
    assert settings.set_identity(aliases=["Jas"]) == {"name": "Jason", "aliases": ["Jas"]}
    assert settings.get_identity() == {"name": "Jason", "aliases": ["Jas"]}
    import pytest
    with pytest.raises(ValueError):
        settings.set_identity(name="x" * 81)


def test_identity_ignores_corrupt_stored_value():
    from speakeasy import settings
    settings._write({"identity": {"name": 5, "aliases": "nope"}})
    assert settings.get_identity() == {"name": "", "aliases": []}
```

```python
# tests/test_summary_format.py (append)
from speakeasy.summary_format import SUMMARY_INSTRUCTIONS, summary_instructions


def test_instructions_describe_structured_items_and_due_dates():
    text = summary_instructions({"name": "", "aliases": []})
    assert text.startswith(SUMMARY_INSTRUCTIONS)
    assert "due_date" in text and "due_phrase" in text and "meeting's start date" in text
    assert "The user is" not in text


def test_instructions_name_the_user():
    text = summary_instructions({"name": "Jason Chiu", "aliases": ["JC"]})
    assert 'The user is Jason Chiu (also JC). Write the user\'s own items with owner "Jason Chiu".' in text
```

- [ ] **Step 2: Run to verify failure.** Run: `.venv/bin/python -m pytest tests/test_settings.py tests/test_summary_format.py -q`. Expected: FAIL (ImportError / AttributeError).

- [ ] **Step 3: Implement.** In `settings.py`, add a new section:

```python
# -- identity (action items: which owner is "me") --------------------------------


def get_identity() -> dict:
    raw = _read().get("identity")
    raw = raw if isinstance(raw, dict) else {}
    name = raw.get("name") if isinstance(raw.get("name"), str) else ""
    aliases = raw.get("aliases") if isinstance(raw.get("aliases"), list) else []
    try:
        from .action_items import clean_identity
        return clean_identity(name, [a for a in aliases if isinstance(a, str)])
    except ValueError:
        return {"name": "", "aliases": []}


def set_identity(name=None, aliases=None) -> dict:
    from .action_items import clean_identity
    current = get_identity()
    identity = clean_identity(current["name"] if name is None else name,
                              current["aliases"] if aliases is None else aliases)
    data = _read()
    data["identity"] = identity
    _write(data)
    return identity
```

In `summary_format.py`, replace the action-items bullet inside `SUMMARY_INSTRUCTIONS` with:

```
- Action items go in action_items, not the summary, each as an object
  {"task", "owner", "due_date", "due_phrase", "priority"}: owner "Unassigned"
  when nobody took it; priority "high" only when stressed as urgent, else
  leave it out. Parked ideas go under Open questions, not action items.
```

Then add:

```python
DUE_DATE_RULE = (
    "- Resolve relative due dates (\"by Friday\", \"next week\", \"end of month\") "
    "to a YYYY-MM-DD due_date counted from the meeting's start date, and put the "
    "words said in due_phrase. Leave due_date out when no time was given. \"Next "
    "week\" means that week's Friday; \"end of month\" means the month's last day.")


def summary_instructions(identity: dict) -> str:
    """SUMMARY_INSTRUCTIONS plus the rules that depend on settings."""
    lines = [SUMMARY_INSTRUCTIONS, DUE_DATE_RULE]
    name = (identity or {}).get("name") or ""
    if name:
        aliases = identity.get("aliases") or []
        also = f" (also {', '.join(aliases)})" if aliases else ""
        lines.append(f'- The user is {name}{also}. Write the user\'s own items with owner "{name}".')
    return "\n".join(lines)
```

- [ ] **Step 4: Run.** Run: `.venv/bin/python -m pytest tests/test_settings.py tests/test_summary_format.py tests/test_mcp_tools.py -q`. Expected: PASS. Update existing assertions on the old action-item sentence if any.

- [ ] **Step 5: Commit.** `git add speakeasy/settings.py speakeasy/summary_format.py tests/ && git commit -m "Identity setting and due-date rules for summaries"`

---

### Task 6: MCP tools + manifest mirror

**Files:**
- Modify: `speakeasy/mcp_tools.py`
- Modify: `packaging/mcpb/manifest.json` (new tools + changed descriptions, verbatim)
- Modify: `speakeasy/mcp_server.py:33` (server instructions: mention action items)
- Test: `tests/test_mcp_action_items.py`; run `tests/test_mcpb.py`, `tests/test_mcp_tools.py`, `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: library methods (Tasks 3–4), `settings.get_identity`, `action_items.is_mine/due_bucket/UNSET`, `summary_instructions`.
- Produces tools: `list_action_items` (read-only), `create_action_item`, `update_action_item`, `delete_action_item`. Changes `save_notes`, `get_meeting` and `pending_summaries`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mcp_action_items.py
import pytest

from speakeasy import settings
from speakeasy.mcp_tools import ToolError, build_tools
from speakeasy.meeting_library import MeetingLibrary
from tests.test_library_backup import _meeting


@pytest.fixture
def env(library_path):
    lib = MeetingLibrary(library_path)
    return lib, build_tools(lib)


def run(tools, name, **args):
    return tools[name].run(args)


def test_tool_flags(env):
    _, tools = env
    assert tools["list_action_items"].read_only is True
    for name in ("create_action_item", "update_action_item", "delete_action_item"):
        assert tools[name].read_only is False


def test_save_notes_accepts_objects_and_get_meeting_returns_ids(env):
    lib, tools = env
    mid = _meeting(lib, "M")
    run(tools, "save_notes", id=mid, summary="s",
        action_items=[{"task": "Ship", "owner": "Jason", "due_date": "2026-10-16", "due_phrase": "Fri"}, "Siam — Review"])
    items = run(tools, "get_meeting", id=mid)["notes"]["action_items"]
    assert [(i["task"], i["owner"], i["due"], i["status"], i["priority"]) for i in items] == [
        ("Ship", "Jason", "2026-10-16", "open", "normal"), ("Review", "Siam", None, "open", "normal")]
    assert all(isinstance(i["id"], int) for i in items)


def test_list_filters(env):
    lib, tools = env
    settings.set_identity(name="Jason", aliases=[])
    mid = _meeting(lib, "Budget sync")
    lib.save_notes(mid, summary="s", tags=["Budget"], action_items=[
        {"task": "Mine overdue", "owner": "Jason", "due_date": "2000-01-03"},
        {"task": "Theirs", "owner": "Siam", "priority": "high"}])
    done = lib.create_action_item(task="Done one", owner="Jason")
    lib.update_action_item(done.id, updated_by="user", status="done")
    out = run(tools, "list_action_items")
    assert out["identity_set"] is True and {i["task"] for i in out["items"]} == {"Mine overdue", "Theirs"}
    assert [i["task"] for i in run(tools, "list_action_items", mine=True)["items"]] == ["Mine overdue"]
    assert [i["task"] for i in run(tools, "list_action_items", overdue=True)["items"]] == ["Mine overdue"]
    assert [i["task"] for i in run(tools, "list_action_items", status="done")["items"]] == ["Done one"]
    assert len(run(tools, "list_action_items", tag="budget")["items"]) == 2
    assert [i["task"] for i in run(tools, "list_action_items", query="THEIR")["items"]] == ["Theirs"]
    assert [i["task"] for i in run(tools, "list_action_items", sort="priority")["items"]][0] == "Theirs"
    first = run(tools, "list_action_items", limit=1)
    assert len(first["items"]) == 1 and first["next_offset"] == 1
    item = run(tools, "list_action_items", query="Mine")["items"][0]
    assert item["mine"] is True and item["meeting"]["title"] == "Budget sync"
    assert item["tags"] == ["Budget"] and item["due_source"] == "claude"


def test_create_update_delete_round_trip(env):
    lib, tools = env
    created = run(tools, "create_action_item", task="Call bank", due="2026-10-20", tags=["Money"])
    assert created["due"] == "2026-10-20" and created["due_source"] == "user"
    updated = run(tools, "update_action_item", id=str(created["id"]), status="done", notes="called")
    assert updated["status"] == "done" and updated["notes"] == "called"
    cleared = run(tools, "update_action_item", id=created["id"], due="")
    assert cleared["due"] is None
    assert lib.get_action_item(created["id"]).updated_by == "claude"
    assert run(tools, "delete_action_item", id=created["id"]) == {"deleted": created["id"]}
    assert run(tools, "list_action_items", status="all")["items"] == []
    restored = run(tools, "delete_action_item", id=created["id"], undo=True)
    assert restored["id"] == created["id"]


@pytest.mark.parametrize("name, args, message", [
    ("update_action_item", {"id": 999, "notes": "x"}, "not found"),
    ("update_action_item", {"id": 1}, "Give at least one"),
    ("create_action_item", {"task": ""}, "task"),
    ("list_action_items", {"status": "maybe"}, "status"),
    ("list_action_items", {"sort": "random"}, "sort"),
    ("update_action_item", {"id": "abc", "notes": "x"}, "id"),
])
def test_errors_are_tool_errors(env, name, args, message):
    _, tools = env
    with pytest.raises(ToolError, match=message):
        run(tools, name, **args)


def test_pending_summaries_instructions_name_the_user(env):
    _, tools = env
    settings.set_identity(name="Jason", aliases=[])
    assert 'owner "Jason"' in run(tools, "pending_summaries")["instructions"]
```

- [ ] **Step 2: Run to verify failure.** Run: `.venv/bin/python -m pytest tests/test_mcp_action_items.py -q`. Expected: FAIL (KeyError `list_action_items`).

- [ ] **Step 3: Implement** in `mcp_tools.py`. Add the imports
`from . import action_items as ai` and
`from .action_item_store import ActionItemNotFound`, then add helpers above
`build_tools`:

```python
_SORTS = ("due", "priority", "meeting", "created")
_PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}


def _item_id(args):
    value = args.get("id")
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    try:
        return ai.check_id(value)
    except ValueError as err:
        raise ToolError(f"id: {err}") from None


def _item_json(item, identity) -> dict:
    tags = sorted({t.casefold(): t for t in [*item.meeting_tags, *item.tags]}.values(), key=str.casefold)
    return {
        "id": item.id, "task": item.task, "owner": item.owner or "Unassigned",
        "mine": ai.is_mine(item.owner, identity), "priority": item.priority,
        "status": item.status, "due": item.effective_due, "due_source": item.due_source,
        **({"due_phrase": item.due_phrase} if item.due_phrase else {}),
        **({"notes": item.notes} if item.notes else {}),
        "tags": tags,
        "meeting": None if item.meeting_id is None else {
            "id": item.meeting_id, "title": item.meeting_title,
            "start": _start(item.meeting_started_at, item.meeting_tz_offset_minutes)},
        **({"completed_at": item.completed_at} if item.completed_at else {}),
    }


def _sorted(items, sort):
    def due_key(i):
        return (i.effective_due is None, i.effective_due or "")
    if sort == "meeting":     # newest meeting first; items without a meeting last
        dated = sorted((i for i in items if i.meeting_started_at),
                       key=lambda i: (i.meeting_started_at, -i.position), reverse=True)
        return dated + [i for i in items if not i.meeting_started_at]
    if sort == "created":
        return sorted(items, key=lambda i: (i.created_at, i.id), reverse=True)
    if sort == "priority":
        return sorted(items, key=lambda i: (_PRIORITY_RANK[i.priority], *due_key(i), i.id))
    return sorted(items, key=lambda i: (*due_key(i), _PRIORITY_RANK[i.priority], i.id))


def _call(fn, *args, **kwargs):
    """Library errors -> ToolError messages Claude can act on."""
    try:
        return fn(*args, **kwargs)
    except ActionItemNotFound:
        raise ToolError("Action item not found: check the id with list_action_items.") from None
    except ValueError as err:
        raise ToolError(str(err)) from None
```

Inside `build_tools`, add:

```python
    def list_action_items(args):
        status = _text(args, "status", max_len=10) or "open"
        if status not in ("open", "done", "all"):
            raise ToolError("status must be open, done or all.")
        sort = _text(args, "sort", max_len=10) or "due"
        if sort not in _SORTS:
            raise ToolError("sort must be due, priority, meeting or created.")
        limit, offset = _int(args, "limit", 50, 1, 100), _int(args, "offset", 0, 0, 1_000_000)
        identity = settings.get_identity()
        today = datetime.now().astimezone().date()
        due_from, due_to = _date(args, "due_from"), _date(args, "due_to")
        owner, tag = _text(args, "owner"), _text(args, "tag")
        query, meeting_id = _text(args, "query", max_len=200), args.get("meeting_id")
        items = library.list_action_items()
        def keep(i):
            if status != "all" and i.status != status: return False
            if _bool(args, "mine") and not ai.is_mine(i.owner, identity): return False
            if owner and (i.owner or "Unassigned").casefold() != owner.casefold(): return False
            if tag and tag_slug(tag) not in {tag_slug(t) for t in [*i.tags, *i.meeting_tags]}: return False
            if meeting_id and i.meeting_id != meeting_id: return False
            if due_from and (i.effective_due is None or i.effective_due < due_from): return False
            if due_to and (i.effective_due is None or i.effective_due > due_to): return False
            if _bool(args, "overdue") and not (i.status == "open"
                                               and ai.due_bucket(i.effective_due, today) == "overdue"):
                return False
            if query and query.casefold() not in " ".join(
                    [i.task, i.notes, i.owner, i.meeting_title or ""]).casefold(): return False
            return True
        rows = _sorted(list(filter(keep, items)), sort)
        page = rows[offset:offset + limit]
        return {"items": [_item_json(i, identity) for i in page], "identity_set": bool(identity["name"]),
                "offset": offset, "next_offset": offset + limit if len(rows) > offset + limit else None}

    def create_action_item(args):
        item = _call(library.create_action_item, task=args.get("task"), owner=args.get("owner") or "",
                     meeting_id=args.get("meeting_id"), due=args.get("due"),
                     priority=args.get("priority") or "normal", notes=args.get("notes") or "",
                     tags=args.get("tags") or [], updated_by="claude")
        return _item_json(item, settings.get_identity())

    def update_action_item(args):
        item_id = _item_id(args)
        fields = {k: args[k] for k in ("task", "owner", "priority", "status", "notes", "due", "tags")
                  if k in args}
        reset = _bool(args, "reset_due")
        if not fields and not reset:
            raise ToolError("Give at least one field to change.")
        item = _call(library.update_action_item, item_id, updated_by="claude", due_reset=reset, **fields)
        return _item_json(item, settings.get_identity())

    def delete_action_item(args):
        item_id = _item_id(args)
        if _bool(args, "undo"):
            return _item_json(_call(library.restore_action_item, item_id, updated_by="claude"),
                              settings.get_identity())
        _call(library.delete_action_item, item_id, updated_by="claude")
        return {"deleted": item_id}
```

`meeting_id` passed to `create_action_item` that doesn't exist raises `MeetingNotFound`, which the server already maps to a safe message. Import `tag_slug` from `.tag_names`.

In `save_notes`, read action items with
`raw = args.get("action_items")`, require `raw is None or isinstance(raw, list)`
(else `ToolError("action_items must be a list.")`), and pass
`action_items=raw` through `_call(library.save_notes, …)` so a `ValueError`
becomes a `ToolError`. In `get_meeting`, set
`notes["action_items"] = [{"id": i.id, "task": i.task, "owner": i.owner or "Unassigned", "status": i.status, "due": i.effective_due, "priority": i.priority} for i in m.action_items]`.
In `pending_summaries`, return
`"instructions": summary_instructions(settings.get_identity())`.

Spec entries (append to `specs`, and change the `save_notes` action_items schema):

```python
        ("list_action_items",
         "List action items across meetings (open ones by default), soonest due first. "
         "Each has an id, task, owner (mine is true when it is the user's), priority, "
         "due date (due_source user means the user set it), notes, tags (the item's and "
         "its meeting's) and its meeting. Filter by mine, owner, tag, meeting_id, "
         "due_from/due_to, overdue or query (words in the task, notes, owner or meeting "
         "title). Pass next_offset as offset for more.",
         {"status": {"type": "string", "enum": ["open", "done", "all"], "default": "open"},
          "mine": {"type": "boolean"}, "owner": {"type": "string"}, "tag": {"type": "string"},
          "meeting_id": {"type": "string"}, "due_from": _FILTERS["from"], "due_to": _FILTERS["to"],
          "overdue": {"type": "boolean"}, "query": {"type": "string"},
          "sort": {"type": "string", "enum": list(_SORTS), "default": "due"},
          "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
          "offset": {"type": "integer", "minimum": 0, "default": 0}},
         [], list_action_items, True),
        ("create_action_item",
         "Add an action item because the user asked you to, optionally linked to a "
         "meeting. due is YYYY-MM-DD. Items you add or change are kept when the meeting "
         "is summarised again.",
         {"task": {"type": "string", "maxLength": 500}, "owner": {"type": "string", "maxLength": 80},
          "meeting_id": {"type": "string"}, "due": {"type": "string"},
          "priority": {"type": "string", "enum": ["high", "normal", "low"]},
          "notes": {"type": "string", "maxLength": 5000},
          "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 10}},
         ["task"], create_action_item, False),
        ("update_action_item",
         "Change an action item because the user asked you to: mark it done (status "
         "done) or open, edit task, owner, priority or notes, set due (YYYY-MM-DD; empty "
         "text means no date), reset_due to go back to the date from the summary, or "
         "replace its own tags. Only the fields you give change.",
         {"id": {"type": "integer"}, "task": {"type": "string", "maxLength": 500},
          "owner": {"type": "string", "maxLength": 80},
          "priority": {"type": "string", "enum": ["high", "normal", "low"]},
          "status": {"type": "string", "enum": ["open", "done"]},
          "notes": {"type": "string", "maxLength": 5000}, "due": {"type": "string"},
          "reset_due": {"type": "boolean"},
          "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 10}},
         ["id"], update_action_item, False),
        ("delete_action_item",
         "Delete an action item because the user asked you to (it stays out of future "
         "summaries of that meeting). undo true restores it.",
         {"id": {"type": "integer"}, "undo": {"type": "boolean"}},
         ["id"], delete_action_item, False),
```

Change `save_notes`'s `action_items` property to:

```python
          "action_items": {"type": "array", "maxItems": 50, "items": {"anyOf": [
              {"type": "string"},
              {"type": "object", "additionalProperties": False, "required": ["task"],
               "properties": {"task": {"type": "string", "maxLength": 500},
                              "owner": {"type": "string", "maxLength": 80},
                              "due_date": {"type": "string"}, "due_phrase": {"type": "string", "maxLength": 80},
                              "priority": {"type": "string", "enum": ["high", "normal", "low"]},
                              "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 10}}}]}},
```

Append to the `save_notes` description:
` Action items the user completed, edited, added or deleted are kept; the rest are replaced by the list you give.`

Change the `get_meeting` description's `"action items)"` to `"action items with ids)"`.

Copy every new or changed description string verbatim into
`packaging/mcpb/manifest.json`, keeping the tool order identical to
`build_tools`. In `mcp_server.py`, extend the instructions sentence to
`save_notes stores a summary, action items or tags the user can see in Speakeasy; list_action_items, create_action_item, update_action_item and delete_action_item manage the user's action items.`
If the manifest or `.mcpb` metadata mirrors that text, update it there too.

- [ ] **Step 4: Run.** Run: `.venv/bin/python -m pytest tests/test_mcp_action_items.py tests/test_mcpb.py tests/test_mcp_tools.py tests/test_mcp_server.py tests/test_meeting_library_mcp.py -q`. Expected: PASS.

- [ ] **Step 5: Commit.** `git add speakeasy/mcp_tools.py speakeasy/mcp_server.py packaging/mcpb/manifest.json tests/ && git commit -m "MCP: list, create, update and delete action items"`

---

### Task 7: Meetings bridge

**Files:**
- Modify: `speakeasy/ui/meetings_bridge.py`
- Test: `tests/test_meetings_bridge_actions.py`

**Interfaces:**
- Consumes: library methods; `settings.get_identity/set_identity`; `action_items.is_mine`; `local_start`.
- Produces bridge methods `actions.list`, `actions.create`, `actions.update`,
  `actions.delete`, `actions.restore` and `actions.bulk`. The payload uses
  camelCase exactly as spec §7. `settings.meetings.get` adds `userName` and
  `userAliases`, and `settings.meetings.set` accepts them. `meetings.get`
  sends `actionItems: ActionItemPayload[]`.

- [ ] **Step 1: Write the failing tests.** Follow the construction pattern in `tests/test_meetings_bridge.py`: build `MeetingsBridge(library=MeetingLibrary(library_path), now=...)` and call `*_payload` methods directly, plus one `_wrap` test for error mapping.

```python
# tests/test_meetings_bridge_actions.py
import pytest

from speakeasy import settings
from speakeasy.meeting_library import MeetingLibrary
from speakeasy.ui.meetings_bridge import MeetingsBridge
from tests.test_library_backup import _meeting


@pytest.fixture
def env(library_path):
    lib = MeetingLibrary(library_path)
    return lib, MeetingsBridge(library=lib)


def test_list_payload_shape(env):
    lib, bridge = env
    settings.set_identity(name="Jason", aliases=[])
    mid = _meeting(lib, "Sync")
    lib.save_notes(mid, summary="s", tags=["Budget"], action_items=[
        {"task": "Ship", "owner": "Jason", "due_date": "2026-10-16", "due_phrase": "Fri"}])
    out = bridge.actions_list_payload({})
    assert out["identitySet"] is True and len(out["today"]) == 10
    item = out["items"][0]
    assert set(item) == {"id", "meetingId", "meetingTitle", "meetingDate", "task", "owner", "mine",
                         "priority", "status", "completedAt", "due", "dueSource", "duePhrase",
                         "claudeDue", "notes", "tags", "meetingTags", "source", "createdAt", "updatedAt"}
    assert (item["mine"], item["due"], item["dueSource"], item["claudeDue"], item["meetingTags"]) == \
        (True, "2026-10-16", "claude", "2026-10-16", ["Budget"])


def test_update_accepts_float_id_rejects_bool(env):
    lib, bridge = env
    item = lib.create_action_item(task="t")
    out = bridge.actions_update_payload({"id": float(item.id), "status": "done", "dueReset": False})
    assert out["status"] == "done"
    with pytest.raises(ValueError):
        bridge.actions_update_payload({"id": True, "status": "done"})


def test_update_due_semantics(env):
    lib, bridge = env
    item = lib.create_action_item(task="t")
    assert bridge.actions_update_payload({"id": item.id, "due": "2026-10-20"})["due"] == "2026-10-20"
    assert bridge.actions_update_payload({"id": item.id, "due": None})["due"] is None
    assert bridge.actions_update_payload({"id": item.id, "dueReset": True})["dueSource"] is None


def test_create_delete_restore_bulk(env):
    lib, bridge = env
    created = bridge.actions_create_payload({"task": "New", "owner": "", "tags": ["X"], "priority": "low"})
    assert created["source"] == "manual" and created["tags"] == ["X"]
    assert bridge.actions_delete_payload({"id": float(created["id"])}) is True
    assert bridge.actions_list_payload({})["items"] == []
    assert bridge.actions_restore_payload({"id": created["id"]})["id"] == created["id"]
    out = bridge.actions_bulk_payload({"ids": [float(created["id"])], "status": "done", "addTags": ["Y"]})
    assert out["items"][0]["status"] == "done" and out["items"][0]["tags"] == ["X", "Y"]


def test_not_found_maps_to_error(env):
    _, bridge = env
    responses = []
    handler = MeetingsBridge._wrap(bridge.actions_update_payload)
    handler({"id": 999.0, "notes": "x"}, lambda result=None, error=None: responses.append(error))
    assert responses == ["not_found"]


def test_settings_identity_round_trip(env):
    _, bridge = env
    out = bridge.settings_set_payload({"userName": "Jason", "userAliases": ["JC"]})
    assert (out["userName"], out["userAliases"]) == ("Jason", ["JC"])


def test_meeting_detail_sends_item_payloads(env):
    lib, bridge = env
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=["A"])
    detail = bridge.get_payload({"id": mid})
    assert detail["actionItems"][0]["task"] == "A" and "id" in detail["actionItems"][0]
```

Adjust the `respond` lambda's signature to the real one used in `_wrap`
(`respond(result)` / `respond(error=...)`). Mirror an existing `_wrap` test
in `tests/test_meetings_bridge.py`.

- [ ] **Step 2: Run to verify failure.** Run: `.venv/bin/python -m pytest tests/test_meetings_bridge_actions.py -q`. Expected: FAIL (AttributeError).

- [ ] **Step 3: Implement.** Add the imports
`from .. import action_items as ai` and
`from ..action_item_store import ActionItemNotFound`, then register the new
methods in `register()`:

```python
            "actions.list": self.actions_list_payload,
            "actions.create": self.actions_create_payload,
            "actions.update": self.actions_update_payload,
            "actions.delete": self.actions_delete_payload,
            "actions.restore": self.actions_restore_payload,
            "actions.bulk": self.actions_bulk_payload,
```

In `_wrap`, map `ActionItemNotFound` to `respond(error="not_found")`,
alongside `MeetingNotFound`. Add the payload methods:

```python
    # -- action items -------------------------------------------------------

    @staticmethod
    def _action_payload(item, identity) -> dict:
        meeting_date = (local_start(item.meeting_started_at, item.meeting_tz_offset_minutes)
                        .date().isoformat() if item.meeting_started_at else None)
        return {
            "id": item.id, "meetingId": item.meeting_id, "meetingTitle": item.meeting_title,
            "meetingDate": meeting_date, "task": item.task, "owner": item.owner,
            "mine": ai.is_mine(item.owner, identity), "priority": item.priority,
            "status": item.status, "completedAt": item.completed_at, "due": item.effective_due,
            "dueSource": item.due_source, "duePhrase": item.due_phrase, "claudeDue": item.due_date,
            "notes": item.notes, "tags": item.tags, "meetingTags": item.meeting_tags,
            "source": item.source, "createdAt": item.created_at, "updatedAt": item.updated_at,
        }

    def actions_list_payload(self, params) -> dict:
        identity = settings.get_identity()
        return {"items": [self._action_payload(i, identity) for i in self.library.list_action_items()],
                "identitySet": bool(identity["name"]),
                "today": self._now().date().isoformat()}

    def actions_create_payload(self, params) -> dict:
        item = self.library.create_action_item(
            task=params.get("task"), owner=params.get("owner") or "",
            meeting_id=params.get("meetingId"), due=params.get("due"),
            priority=params.get("priority") or "normal", notes=params.get("notes") or "",
            tags=params.get("tags") or [], updated_by="user")
        return self._action_payload(item, settings.get_identity())

    def actions_update_payload(self, params) -> dict:
        fields = {k: params[k] for k in ("task", "owner", "priority", "status", "notes", "due", "tags")
                  if k in params}
        item = self.library.update_action_item(
            ai.check_id(params.get("id")), updated_by="user",
            due_reset=params.get("dueReset") is True, **fields)
        return self._action_payload(item, settings.get_identity())

    def actions_delete_payload(self, params) -> bool:
        self.library.delete_action_item(ai.check_id(params.get("id")), updated_by="user")
        return True

    def actions_restore_payload(self, params) -> dict:
        item = self.library.restore_action_item(ai.check_id(params.get("id")), updated_by="user")
        return self._action_payload(item, settings.get_identity())

    def actions_bulk_payload(self, params) -> dict:
        kwargs = {"status": params["status"]} if params.get("status") is not None else {}
        items = self.library.bulk_update_action_items(
            params.get("ids"), updated_by="user", add_tags=params.get("addTags") or [],
            remove_tags=params.get("removeTags") or [], delete=params.get("delete") is True, **kwargs)
        identity = settings.get_identity()
        return {"items": [self._action_payload(i, identity) for i in items]}
```

`self._now()` must return a local-aware datetime. Check how `get_payload`
uses it, and use `.astimezone()` if it's UTC. Import `local_start` from
`..meeting_library`.

In `settings_get_payload`, add
`identity = settings.get_identity()` and the fields
`"userName": identity["name"], "userAliases": identity["aliases"]`. In
`settings_set_payload`, call
`settings.set_identity(name=params.get("userName"), aliases=params.get("userAliases"))`
when either is not None. In `get_payload`, compute the identity once and set
`"actionItems": [self._action_payload(i, identity) for i in m.action_items]`.

- [ ] **Step 4: Run.** Run: `.venv/bin/python -m pytest tests/test_meetings_bridge_actions.py tests/test_meetings_bridge.py tests/test_webbridge.py -q`. Expected: PASS.

- [ ] **Step 5: Commit.** `git add speakeasy/ui/meetings_bridge.py tests/test_meetings_bridge_actions.py && git commit -m "Bridge: action item methods and identity settings"`

---

### Task 8: Markdown export

**Files:**
- Modify: `speakeasy/meeting_export.py` (`render_export_md`, `export_all`)
- Test: `tests/test_meeting_export.py` (append)

**Interfaces:**
- Consumes: `StoredMeeting.action_items` (Task 3), `library.list_action_items()`.
- Produces: `format_item_md(item) -> list[str]` and the file `Action items.md` in the export folder.

- [ ] **Step 1: Write the failing tests** (append; reuse the module's existing meeting fixture helpers)

```python
def test_export_renders_item_status_owner_due_priority_notes(library_path):
    from speakeasy.meeting_export import format_item_md
    from speakeasy.meeting_library import MeetingLibrary
    from tests.test_library_backup import _meeting
    lib = MeetingLibrary(library_path)
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=[
        {"task": "Send budget draft", "owner": "Jason", "due_date": "2026-10-16",
         "due_phrase": "by Friday", "priority": "high"}, "Plain"])
    first, second = lib.list_action_items(meeting_id=mid)
    lib.update_action_item(first.id, updated_by="user", status="done", notes="line one\nline two")
    assert format_item_md(lib.get_action_item(first.id)) == [
        '- [x] Send budget draft — Jason · due Fri 16 Oct 2026 ("by Friday") · high',
        "  Notes: line one", "  line two"]
    assert format_item_md(second) == ["- [ ] Plain"]


def test_export_all_writes_action_items_file(tmp_path, library_path):
    from speakeasy.meeting_export import export_all
    from speakeasy.meeting_library import MeetingLibrary
    from tests.test_library_backup import _meeting
    lib = MeetingLibrary(library_path)
    mid = _meeting(lib, "Weekly")
    lib.save_notes(mid, summary="s", action_items=["Open one"])
    done = lib.create_action_item(task="Manual done")
    lib.update_action_item(done.id, updated_by="user", status="done")
    count = export_all(tmp_path / "out", lib)
    text = (tmp_path / "out" / "Action items.md").read_text()
    assert count == 1                                         # still counts meetings only
    assert "## Open" in text and "- [ ] Open one" in text and "Weekly" in text
    assert "## Completed" in text and "- [x] Manual done" in text
```

- [ ] **Step 2: Run to verify failure.** Run: `.venv/bin/python -m pytest tests/test_meeting_export.py -q`. Expected: FAIL (ImportError `format_item_md`).

- [ ] **Step 3: Implement**

```python
def _due_text(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d:%a} {d.day} {d:%b %Y}"


def format_item_md(item) -> list[str]:
    parts = [f"- [{'x' if item.status == 'done' else ' '}] {item.task}"]
    if item.owner:
        parts.append(item.owner)
    head = " — ".join(parts)
    extras = []
    if item.effective_due:
        due = f"due {_due_text(item.effective_due)}"
        extras.append(f'{due} ("{item.due_phrase}")' if item.due_phrase else due)
    if item.priority != "normal":
        extras.append(item.priority)
    line = " · ".join([head, *extras])
    lines = [line]
    if item.notes:
        first, *rest = item.notes.split("\n")
        lines += [f"  Notes: {first}", *[f"  {r}" for r in rest]]
    return lines
```

In `render_export_md`, replace the action-items block with a check on
`getattr(stored, "action_items", None)`. When it's present, write
`## Action items`, then the lines from `format_item_md` for each item, then a
blank line. Otherwise fall back to the old `stored.notes.action_items`
strings.

At the end of `export_all`, before returning, write `Action items.md`:

- `# Action items`
- `## Open` with each open item's `format_item_md` lines, plus an indented
  line `  From: <meeting title> (<YYYY-MM-DD>)` when it has a meeting
- `## Completed` in the same format

Use the same `library` passed in, and write UTF-8. The export-recovery merge may have changed `export_all`'s signature (for example, exporting from a snapshot library); follow the merged signature, and adapt the test call to it. Import `date` from
`datetime` if missing. If the export-recovery code verifies the folder
contents strictly, read `export_all`'s callers and keep that check counting
meetings only. Note it in the report.

- [ ] **Step 4: Run.** Run: `.venv/bin/python -m pytest tests/test_meeting_export.py tests/test_library_backup.py -q`. Expected: PASS.

- [ ] **Step 5: Commit.** `git add speakeasy/meeting_export.py tests/test_meeting_export.py && git commit -m "Export: action item status, due, notes and a library-wide list"`

---

### Task 9: Frontend pure helpers, types, mock data, API

**Files:**
- Create: `frontend/src/meetings/actionItems.ts` (pure, **import-free** apart from types, like `notesMarkdown.ts`)
- Create: `frontend/src/mock/actionItems.ts`
- Create: `frontend/src/meetings/actionItemsApi.ts`
- Modify: `frontend/src/mock/meetings.ts`
  - `MeetingDetail.actionItems: ActionItem[]`
  - `MeetingSettings` gains `userName: string; userAliases: string[]`
  - mock details and settings updated
- Test: `frontend/tests/actionItems.test.ts`

**Interfaces:**
- Produces (used by Task 10):

```ts
export type Priority = 'high' | 'normal' | 'low';
export interface ActionItem {
  id: number; meetingId: string | null; meetingTitle: string | null; meetingDate: string | null;
  task: string; owner: string; mine: boolean; priority: Priority; status: 'open' | 'done';
  completedAt: string | null; due: string | null; dueSource: 'claude' | 'user' | null;
  duePhrase: string; claudeDue: string | null; notes: string; tags: string[]; meetingTags: string[];
  source: 'summary' | 'manual'; createdAt: string; updatedAt: string;
}
export type DueBucket = 'overdue' | 'today' | 'week' | 'later' | 'none';
export type OwnerFilter = { kind: 'mine' } | { kind: 'all' } | { kind: 'person'; name: string };
export type GroupBy = 'due' | 'meeting' | 'owner' | 'tag' | 'none';
export type SortBy = 'due' | 'priority' | 'meeting' | 'created';
export interface ViewState { owner: OwnerFilter; tags: string[]; priorities: Priority[];
  status: 'open' | 'done' | 'all'; query: string; groupBy: GroupBy; sortBy: SortBy }
export interface Group { key: string; label: string; items: ActionItem[]; tone?: 'overdue' | 'done' }
export const DEFAULT_VIEW: ViewState;
export function addDays(iso: string, n: number): string;
export function dueBucket(due: string | null, today: string): DueBucket;
export function effectiveTags(item: ActionItem): string[];
export function filterItems(items: ActionItem[], view: ViewState, identitySet: boolean): ActionItem[];
export function sortItems(items: ActionItem[], sortBy: SortBy): ActionItem[];
export function groupItems(items: ActionItem[], view: ViewState, today: string): Group[];
export function sidebarCounts(items: ActionItem[], identitySet: boolean, today: string): { open: number; overdue: number };
export function formatDue(due: string): string;          // 'Fri 16 Oct'
export function ownersOf(items: ActionItem[]): string[]; // distinct, 'Unassigned' last
export function parseViewState(raw: string | null): ViewState;
// actionItemsApi.ts
export const actionItemsApi: {
  seedMock(items: ActionItem[]): void;
  list(): Promise<{ items: ActionItem[]; identitySet: boolean; today: string }>;
  create(fields: { task: string; owner?: string; meetingId?: string | null; due?: string | null; priority?: Priority; notes?: string; tags?: string[] }): Promise<ActionItem>;
  update(id: number, fields: Partial<Pick<ActionItem, 'task' | 'owner' | 'priority' | 'status' | 'notes' | 'tags'>> & { due?: string | null; dueReset?: boolean }): Promise<ActionItem>;
  remove(id: number): Promise<void>;
  restore(id: number): Promise<ActionItem>;
  bulk(ids: number[], change: { status?: 'open' | 'done'; addTags?: string[]; removeTags?: string[]; delete?: boolean }): Promise<ActionItem[]>;
};
```

- [ ] **Step 1: Write the failing tests**

```ts
// frontend/tests/actionItems.test.ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_VIEW, addDays, dueBucket, effectiveTags, filterItems, formatDue, groupItems,
  ownersOf, parseViewState, sidebarCounts, sortItems,
} from '../src/meetings/actionItems.ts';
import type { ActionItem, ViewState } from '../src/meetings/actionItems.ts';

let n = 0;
function item(p: Partial<ActionItem>): ActionItem {
  n += 1;
  return {
    id: n, meetingId: 'm1', meetingTitle: 'Sync', meetingDate: '2026-10-05', task: `t${n}`, owner: '',
    mine: false, priority: 'normal', status: 'open', completedAt: null, due: null, dueSource: null,
    duePhrase: '', claudeDue: null, notes: '', tags: [], meetingTags: [], source: 'summary',
    createdAt: `2026-10-0${(n % 9) + 1}T10:00:00Z`, updatedAt: '', ...p,
  };
}
const view = (p: Partial<ViewState> = {}): ViewState => ({ ...DEFAULT_VIEW, ...p });
const SUN = '2026-10-11', MON = '2026-10-12';

test('addDays crosses months', () => {
  assert.equal(addDays('2026-10-31', 1), '2026-11-01');
  assert.equal(addDays('2026-03-01', -1), '2026-02-28');
});

test('dueBucket week boundaries (Monday-start weeks)', () => {
  assert.equal(dueBucket(null, SUN), 'none');
  assert.equal(dueBucket(SUN, SUN), 'today');
  assert.equal(dueBucket(SUN, MON), 'overdue');
  assert.equal(dueBucket(MON, SUN), 'later');
  assert.equal(dueBucket('2026-10-18', MON), 'week');
  assert.equal(dueBucket('2026-10-19', MON), 'later');
});

test('effectiveTags merges case-insensitively, own spelling wins', () => {
  assert.deepEqual(effectiveTags(item({ tags: ['budget'], meetingTags: ['Budget', 'Ops'] })), ['budget', 'Ops']);
});

test('filter: mine falls back to everyone when no identity', () => {
  const items = [item({ mine: true }), item({ mine: false })];
  assert.equal(filterItems(items, view(), true).length, 1);
  assert.equal(filterItems(items, view(), false).length, 2);
});

test('filter: person, Unassigned, tags ANY, priority, status, query', () => {
  const a = item({ owner: 'Siam', tags: ['X'], priority: 'high', notes: 'call the BANK' });
  const b = item({ owner: '', meetingTags: ['Y'] });
  const c = item({ owner: 'Siam', status: 'done' });
  const all = [a, b, c];
  const base = { owner: { kind: 'all' } as const };
  assert.deepEqual(filterItems(all, view({ ...base, owner: { kind: 'person', name: 'siam' } }), true), [a]);
  assert.deepEqual(filterItems(all, view({ ...base, owner: { kind: 'person', name: 'Unassigned' } }), true), [b]);
  assert.deepEqual(filterItems(all, view({ ...base, tags: ['x', 'y'] }), true), [a, b]);
  assert.deepEqual(filterItems(all, view({ ...base, priorities: ['high'] }), true), [a]);
  assert.deepEqual(filterItems(all, view({ ...base, status: 'done' }), true), [c]);
  assert.equal(filterItems(all, view({ ...base, status: 'all' }), true).length, 3);
  assert.deepEqual(filterItems(all, view({ ...base, query: 'bank' }), true), [a]);
  assert.deepEqual(filterItems(all, view({ ...base, query: 'sync' }), true), [a, b]);   // meeting title
});

test('sort: due nulls last, priority, meeting newest, created newest', () => {
  const a = item({ due: '2026-10-20', priority: 'low', meetingDate: '2026-10-01' });
  const b = item({ due: null, priority: 'high', meetingDate: '2026-10-09' });
  const c = item({ due: '2026-10-13', priority: 'normal', meetingDate: null });
  assert.deepEqual(sortItems([a, b, c], 'due').map((i) => i.id), [c.id, a.id, b.id]);
  assert.deepEqual(sortItems([a, b, c], 'priority').map((i) => i.id), [b.id, c.id, a.id]);
  assert.deepEqual(sortItems([a, b, c], 'meeting').map((i) => i.id), [b.id, a.id, c.id]);
});

test('group by due: order, labels, completed last, empty groups dropped', () => {
  const items = [item({ due: '2026-10-09' }), item({ due: MON }), item({ due: null }),
    item({ status: 'done', completedAt: '2026-10-10T00:00:00Z' })];
  const groups = groupItems(items, view({ status: 'all', owner: { kind: 'all' } }), MON);
  assert.deepEqual(groups.map((g) => g.key), ['overdue', 'today', 'none', 'done']);
  assert.deepEqual(groups.map((g) => g.label), ['Overdue', 'Today', 'No date', 'Completed']);
  assert.equal(groups[0].tone, 'overdue');
});

test('group by tag puts an item under each tag, Untagged last', () => {
  const groups = groupItems([item({ tags: ['B', 'A'] }), item({})], view({ groupBy: 'tag' }), MON);
  assert.deepEqual(groups.map((g) => g.label), ['A', 'B', 'Untagged']);
});

test('group by meeting and owner', () => {
  const items = [item({ meetingId: null, meetingTitle: null, meetingDate: null, source: 'manual' }),
    item({ meetingId: 'm2', meetingTitle: 'Later', meetingDate: '2026-10-09', owner: 'Zed' })];
  assert.deepEqual(groupItems(items, view({ groupBy: 'meeting' }), MON).map((g) => g.label),
    ['Later · 2026-10-09', 'No meeting']);
  assert.deepEqual(groupItems(items, view({ groupBy: 'owner' }), MON).map((g) => g.label), ['Zed', 'Unassigned']);
});

test('sidebarCounts counts open items (mine when identity set) and overdue', () => {
  const items = [item({ mine: true, due: '2026-10-01' }), item({ mine: true }), item({ mine: false, due: '2026-10-01' }),
    item({ mine: true, status: 'done', due: '2026-10-01' })];
  assert.deepEqual(sidebarCounts(items, true, MON), { open: 2, overdue: 1 });
  assert.deepEqual(sidebarCounts(items, false, MON), { open: 3, overdue: 2 });
});

test('formatDue and ownersOf', () => {
  assert.equal(formatDue('2026-10-16'), 'Fri 16 Oct');
  assert.deepEqual(ownersOf([item({ owner: 'b' }), item({ owner: '' }), item({ owner: 'A' }), item({ owner: 'B' })]),
    ['A', 'b', 'Unassigned']);
});

test('parseViewState falls back on junk and keeps valid fields', () => {
  assert.deepEqual(parseViewState(null), DEFAULT_VIEW);
  assert.deepEqual(parseViewState('{not json'), DEFAULT_VIEW);
  const v = parseViewState(JSON.stringify({ ...DEFAULT_VIEW, groupBy: 'tag', sortBy: 'bogus', tags: ['A', 3] }));
  assert.equal(v.groupBy, 'tag'); assert.equal(v.sortBy, 'due'); assert.deepEqual(v.tags, ['A']);
});
```

- [ ] **Step 2: Run to verify failure.** Run: `npm --prefix frontend test`. Expected: FAIL (cannot find module `actionItems.ts`).

- [ ] **Step 3: Implement `frontend/src/meetings/actionItems.ts`**

```ts
// Pure action-item helpers (no imports: run directly by node --test).
// dueBucket mirrors speakeasy/action_items.py due_bucket.

export type Priority = 'high' | 'normal' | 'low';
export interface ActionItem {
  id: number; meetingId: string | null; meetingTitle: string | null; meetingDate: string | null;
  task: string; owner: string; mine: boolean; priority: Priority; status: 'open' | 'done';
  completedAt: string | null; due: string | null; dueSource: 'claude' | 'user' | null;
  duePhrase: string; claudeDue: string | null; notes: string; tags: string[]; meetingTags: string[];
  source: 'summary' | 'manual'; createdAt: string; updatedAt: string;
}
export type DueBucket = 'overdue' | 'today' | 'week' | 'later' | 'none';
export type OwnerFilter = { kind: 'mine' } | { kind: 'all' } | { kind: 'person'; name: string };
export type GroupBy = 'due' | 'meeting' | 'owner' | 'tag' | 'none';
export type SortBy = 'due' | 'priority' | 'meeting' | 'created';
export interface ViewState {
  owner: OwnerFilter; tags: string[]; priorities: Priority[];
  status: 'open' | 'done' | 'all'; query: string; groupBy: GroupBy; sortBy: SortBy;
}
export interface Group { key: string; label: string; items: ActionItem[]; tone?: 'overdue' | 'done' }

export const DEFAULT_VIEW: ViewState = {
  owner: { kind: 'mine' }, tags: [], priorities: [], status: 'open', query: '', groupBy: 'due', sortBy: 'due',
};
const RANK: Record<Priority, number> = { high: 0, normal: 1, low: 2 };
const BUCKETS: [DueBucket, string][] = [
  ['overdue', 'Overdue'], ['today', 'Today'], ['week', 'This week'], ['later', 'Later'], ['none', 'No date'],
];
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const UNASSIGNED = 'Unassigned';

function utcNoon(iso: string): Date { return new Date(`${iso}T12:00:00Z`); }

export function addDays(iso: string, n: number): string {
  const d = utcNoon(iso);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export function dueBucket(due: string | null, today: string): DueBucket {
  if (!due) return 'none';
  if (due < today) return 'overdue';
  if (due === today) return 'today';
  const toSunday = (7 - utcNoon(today).getUTCDay()) % 7;
  return due <= addDays(today, toSunday) ? 'week' : 'later';
}

export function effectiveTags(item: ActionItem): string[] {
  const seen = new Map<string, string>();
  for (const t of [...item.tags, ...item.meetingTags]) if (!seen.has(t.toLowerCase())) seen.set(t.toLowerCase(), t);
  return [...seen.values()];
}

const ownerLabel = (i: ActionItem) => i.owner || UNASSIGNED;

export function filterItems(items: ActionItem[], view: ViewState, identitySet: boolean): ActionItem[] {
  const tags = view.tags.map((t) => t.toLowerCase());
  const q = view.query.trim().toLowerCase();
  return items.filter((i) => {
    if (view.owner.kind === 'mine' && identitySet && !i.mine) return false;
    if (view.owner.kind === 'person' && ownerLabel(i).toLowerCase() !== view.owner.name.toLowerCase()) return false;
    if (tags.length && !effectiveTags(i).some((t) => tags.includes(t.toLowerCase()))) return false;
    if (view.priorities.length && !view.priorities.includes(i.priority)) return false;
    if (view.status !== 'all' && i.status !== view.status) return false;
    if (q && ![i.task, i.notes, i.owner, i.meetingTitle ?? ''].join(' ').toLowerCase().includes(q)) return false;
    return true;
  });
}

function byDue(a: ActionItem, b: ActionItem): number {
  if (a.due === b.due) return 0;
  if (a.due === null) return 1;
  if (b.due === null) return -1;
  return a.due < b.due ? -1 : 1;
}
function desc(a: string | null, b: string | null): number {
  if (a === b) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return a > b ? -1 : 1;
}

export function sortItems(items: ActionItem[], sortBy: SortBy): ActionItem[] {
  const cmp: Record<SortBy, (a: ActionItem, b: ActionItem) => number> = {
    due: (a, b) => byDue(a, b) || RANK[a.priority] - RANK[b.priority] || a.id - b.id,
    priority: (a, b) => RANK[a.priority] - RANK[b.priority] || byDue(a, b) || a.id - b.id,
    meeting: (a, b) => desc(a.meetingDate, b.meetingDate) || a.id - b.id,
    created: (a, b) => desc(a.createdAt, b.createdAt) || a.id - b.id,
  };
  return [...items].sort(cmp[sortBy]);
}

function alpha(a: string, b: string): number { return a.localeCompare(b, undefined, { sensitivity: 'base' }); }

export function groupItems(items: ActionItem[], view: ViewState, today: string): Group[] {
  const open = items.filter((i) => i.status === 'open');
  const done = items.filter((i) => i.status === 'done')
    .sort((a, b) => desc(a.completedAt, b.completedAt) || a.id - b.id);
  const sorted = (list: ActionItem[]) => sortItems(list, view.sortBy);
  const keyed = (list: ActionItem[], keyOf: (i: ActionItem) => string[]) => {
    const map = new Map<string, ActionItem[]>();
    for (const i of list) for (const k of keyOf(i)) map.set(k, [...(map.get(k) ?? []), i]);
    return map;
  };
  let groups: Group[];
  if (view.groupBy === 'due') {
    groups = BUCKETS.map(([key, label]) => ({
      key, label, items: sorted(open.filter((i) => dueBucket(i.due, today) === key)),
      ...(key === 'overdue' ? { tone: 'overdue' as const } : {}),
    }));
    groups.push({ key: 'done', label: 'Completed', items: done, tone: 'done' });
    return groups.filter((g) => g.items.length > 0);
  }
  if (view.groupBy === 'none') {
    return [{ key: 'all', label: '', items: [...sorted(open), ...done] }].filter((g) => g.items.length > 0);
  }
  const keyOf: (i: ActionItem) => string[] =
    view.groupBy === 'meeting' ? (i) => [i.meetingId ?? '']
    : view.groupBy === 'owner' ? (i) => [ownerLabel(i)]
    : (i) => (effectiveTags(i).length ? effectiveTags(i) : ['']);
  const map = keyed(items, keyOf);
  const sample = (k: string) => map.get(k)![0];
  const keys = [...map.keys()].sort((a, b) => {
    if (view.groupBy === 'meeting') {
      if (!a || !b) return a ? -1 : b ? 1 : 0;
      return desc(sample(a).meetingDate, sample(b).meetingDate) || alpha(a, b);
    }
    if (view.groupBy === 'owner') return a === UNASSIGNED ? 1 : b === UNASSIGNED ? -1 : alpha(a, b);
    return !a ? 1 : !b ? -1 : alpha(a, b);
  });
  return keys.map((k) => {
    const list = map.get(k)!;
    const label = view.groupBy === 'meeting'
      ? (k ? `${sample(k).meetingTitle} · ${sample(k).meetingDate}` : 'No meeting')
      : view.groupBy === 'tag' ? (k || 'Untagged') : k;
    return { key: `${view.groupBy}:${k}`, label,
      items: [...sorted(list.filter((i) => i.status === 'open')), ...list.filter((i) => i.status === 'done')] };
  });
}

export function sidebarCounts(items: ActionItem[], identitySet: boolean, today: string) {
  const open = items.filter((i) => i.status === 'open' && (!identitySet || i.mine));
  return { open: open.length, overdue: open.filter((i) => dueBucket(i.due, today) === 'overdue').length };
}

export function formatDue(due: string): string {
  const d = utcNoon(due);
  return `${DAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
}

export function ownersOf(items: ActionItem[]): string[] {
  const seen = new Map<string, string>();
  for (const i of items) if (i.owner && !seen.has(i.owner.toLowerCase())) seen.set(i.owner.toLowerCase(), i.owner);
  const names = [...seen.values()].sort(alpha);
  return items.some((i) => !i.owner) ? [...names, UNASSIGNED] : names;
}

export function parseViewState(raw: string | null): ViewState {
  let v: Partial<Record<keyof ViewState, unknown>>;
  try { v = raw ? JSON.parse(raw) : {}; } catch { return DEFAULT_VIEW; }
  if (!v || typeof v !== 'object') return DEFAULT_VIEW;
  const pick = <T,>(x: unknown, ok: readonly T[], d: T): T => (ok.includes(x as T) ? (x as T) : d);
  const o = v.owner as OwnerFilter | undefined;
  const owner: OwnerFilter = o && (o.kind === 'mine' || o.kind === 'all') ? { kind: o.kind }
    : o && o.kind === 'person' && typeof o.name === 'string' ? { kind: 'person', name: o.name } : DEFAULT_VIEW.owner;
  return {
    owner,
    tags: Array.isArray(v.tags) ? v.tags.filter((t): t is string => typeof t === 'string') : [],
    priorities: Array.isArray(v.priorities)
      ? v.priorities.filter((p): p is Priority => p === 'high' || p === 'normal' || p === 'low') : [],
    status: pick(v.status, ['open', 'done', 'all'] as const, DEFAULT_VIEW.status),
    query: typeof v.query === 'string' ? v.query : '',
    groupBy: pick(v.groupBy, ['due', 'meeting', 'owner', 'tag', 'none'] as const, DEFAULT_VIEW.groupBy),
    sortBy: pick(v.sortBy, ['due', 'priority', 'meeting', 'created'] as const, DEFAULT_VIEW.sortBy),
  };
}
```

If `node --test`'s type stripping rejects the `<T,>` generic arrow, change
it to a `function pick<T>(…)` declaration.

- [ ] **Step 4: Mock data + API.**

`frontend/src/mock/actionItems.ts` exports `MOCK_ACTION_ITEMS: ActionItem[]`. It should have about 10 items:

- items across 3 of the existing mock meetings, using their real ids and titles from `mock/meetings.ts`
- 2 manual items with no meeting
- one each of overdue, due today, due this week, later and no date (dates relative to `MOCK_NOW_MS`'s day)
- 2 done items
- a mix of priorities, `mine` true and false, own tags and meeting tags
- one item with a `duePhrase` and one with a user override

`actionItemsApi.ts` follows `draftApi.ts`. When embedded, it calls the bridge
methods; otherwise it mutates an in-memory copy of the seeded items. The mock
`update` applies the same due rules:

- `due` sets `dueSource` to `'user'`
- `due: null` clears it to `'user'` with `due` null
- `dueReset` restores `claudeDue` with source `'claude'`, or null

The mock `bulk` and `create` return the changed items. The `mine` of a new
mock item is `owner === 'Jason'`.

Update `frontend/src/mock/meetings.ts` types. In `MeetingDetail`,
`actionItems` becomes `ActionItem[]` (import the type from
`../meetings/actionItems`); convert each existing mock string into an item.
`MeetingSettings` gains `userName` and `userAliases`, and the mock settings
get `userName: 'Jason', userAliases: []`.

- [ ] **Step 5: Run.** Run: `npm --prefix frontend test` then `npm --prefix frontend run build`. Expected: tests PASS. The build fails only where `MeetingDetail.tsx` still renders strings; fix that minimally (`{item.task}`) so the build is green. Task 10 replaces it.

- [ ] **Step 6: Commit.** `git add frontend/ && git commit -m "Frontend: action item helpers, mock data and API"`

---

### Task 10: Frontend UI — row, view, sidebar, meeting tab, settings

**Files:**
- Create: `frontend/src/meetings/ActionItemRow.tsx` + `ActionItemRow.module.css`
- Create: `frontend/src/meetings/ActionItemsView.tsx` + `ActionItemsView.module.css`
- Modify: `frontend/src/meetings/Sidebar.tsx` (row under Today, always shown)
- Modify: `frontend/src/meetings/App.tsx` (state, loading, refresh on `meetings.changed`, view switch, mock state `actions`)
- Modify: `frontend/src/meetings/MeetingDetail.tsx:648-660` (interactive rows)
- Modify: `frontend/src/meetings/SettingsSheet.tsx` ("Your name" + "Other names")

**Interfaces:**
- Consumes: everything from Task 9.
- Produces:
  - `<ActionItemRow item onChange onDelete onOpenMeeting? selectable? selected? onSelect? showMeeting? />`, where:
    - `onChange(fields) => Promise<void>`
    - `onDelete() => void`
    - `onOpenMeeting(id: string) => void`
  - `<ActionItemsView items identitySet today onUpdate onCreate onDelete onBulk onOpenMeeting onOpenSettings userName />`

- [ ] **Step 1: `ActionItemRow.tsx`**

```tsx
import { useEffect, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { NO_AUTOCORRECT } from '../components/noAutocorrect';
import { formatDue } from './actionItems';
import type { ActionItem, Priority } from './actionItems';
import styles from './ActionItemRow.module.css';

export type ItemChange = Partial<Pick<ActionItem, 'task' | 'owner' | 'priority' | 'status' | 'notes' | 'tags'>>
  & { due?: string | null; dueReset?: boolean };

interface Props {
  item: ActionItem;
  onChange: (change: ItemChange) => Promise<void>;
  onDelete: () => void;
  onOpenMeeting?: (meetingId: string) => void;
  showMeeting?: boolean;
  selectable?: boolean;
  selected?: boolean;
  onSelect?: (selected: boolean) => void;
}

export function ActionItemRow({ item, onChange, onDelete, onOpenMeeting, showMeeting = true,
  selectable = false, selected = false, onSelect }: Props) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState(false);
  const [draft, setDraft] = useState({ task: item.task, owner: item.owner, notes: item.notes, tags: item.tags.join(', ') });
  // A refresh (e.g. Claude edited via MCP) updates fields the user isn't editing.
  useEffect(() => {
    if (!open) setDraft({ task: item.task, owner: item.owner, notes: item.notes, tags: item.tags.join(', ') });
  }, [item, open]);

  const save = (change: ItemChange) => {
    setError(false);
    onChange(change).catch(() => setError(true));
  };
  const commitText = (field: 'task' | 'owner' | 'notes') => {
    const value = field === 'notes' ? draft.notes : draft[field].trim();
    if (field === 'task' && !value) { setDraft({ ...draft, task: item.task }); return; }
    if (value !== item[field]) save({ [field]: value });
  };
  const commitTags = () => {
    const tags = draft.tags.split(',').map((t) => t.trim()).filter(Boolean);
    if (tags.join('\n') !== item.tags.join('\n')) save({ tags });
  };
  const enter = (fn: () => void) => (e: KeyboardEvent) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); fn(); } };
  const done = item.status === 'done';

  return (
    <li className={`${styles.row} ${done ? styles.done : ''}`}>
      <div className={styles.line}>
        {selectable && (
          <input type="checkbox" className={styles.select} aria-label="Select item"
            checked={selected} onChange={(e) => onSelect?.(e.target.checked)} />
        )}
        <button type="button" role="checkbox" aria-checked={done} aria-label={done ? 'Mark as open' : 'Mark as done'}
          className={`${styles.check} ${done ? styles.checkOn : ''}`}
          onClick={() => save({ status: done ? 'open' : 'done' })} />
        <button type="button" className={styles.main} aria-expanded={open} onClick={() => setOpen(!open)}>
          <span className={styles.task}>{item.task}</span>
          <span className={styles.meta}>
            {item.priority !== 'normal' && <span className={`${styles.priority} ${styles[item.priority]}`}>{item.priority}</span>}
            <span className={styles.owner}>{item.owner || 'Unassigned'}</span>
            {item.due && (
              <span className={styles.due}>
                {formatDue(item.due)}
                {item.duePhrase && item.dueSource === 'claude' && <span className={styles.phrase}> “{item.duePhrase}”</span>}
                {item.dueSource === 'user' && <span className={styles.edited}> edited</span>}
              </span>
            )}
            {item.meetingTags.map((t) => <span key={`m-${t}`} className={styles.tagMuted}>{t}</span>)}
            {item.tags.map((t) => <span key={`o-${t}`} className={styles.tag}>{t}</span>)}
            {item.notes && <span className={styles.hasNotes} aria-label="Has notes">✎</span>}
          </span>
        </button>
        {showMeeting && (item.meetingId && onOpenMeeting ? (
          <button type="button" className={styles.meeting} onClick={() => onOpenMeeting(item.meetingId!)}>
            {item.meetingTitle} · {item.meetingDate}
          </button>
        ) : !item.meetingId && item.source === 'summary' ? (
          <span className={styles.meetingGone}>Meeting deleted</span>
        ) : null)}
      </div>
      {open && (
        <div className={styles.editor}>
          <label>Task<input {...NO_AUTOCORRECT} value={draft.task} maxLength={500}
            onChange={(e) => setDraft({ ...draft, task: e.target.value })} onBlur={() => commitText('task')} onKeyDown={enter(() => commitText('task'))} /></label>
          <label>Owner<input {...NO_AUTOCORRECT} value={draft.owner} maxLength={80} placeholder="Unassigned"
            onChange={(e) => setDraft({ ...draft, owner: e.target.value })} onBlur={() => commitText('owner')} onKeyDown={enter(() => commitText('owner'))} /></label>
          <label>Priority<select value={item.priority} onChange={(e) => save({ priority: e.target.value as Priority })}>
            <option value="high">High</option><option value="normal">Normal</option><option value="low">Low</option>
          </select></label>
          <label>Due<input type="date" value={item.due ?? ''} onChange={(e) => save({ due: e.target.value || null })} /></label>
          <div className={styles.dueActions}>
            <button type="button" onClick={() => save({ due: null })} disabled={item.due === null}>No date</button>
            {item.claudeDue !== null || item.dueSource === 'user' ? (
              <button type="button" onClick={() => save({ dueReset: true })} disabled={item.dueSource !== 'user'}>
                Reset to Claude’s{item.claudeDue ? ` (${formatDue(item.claudeDue)})` : ''}
              </button>
            ) : null}
          </div>
          <label>Tags<input {...NO_AUTOCORRECT} value={draft.tags} placeholder="Comma-separated"
            onChange={(e) => setDraft({ ...draft, tags: e.target.value })} onBlur={commitTags} onKeyDown={enter(commitTags)} /></label>
          <label className={styles.notes}>Notes<textarea value={draft.notes} maxLength={5000} rows={3}
            onChange={(e) => setDraft({ ...draft, notes: e.target.value })} onBlur={() => commitText('notes')} /></label>
          <button type="button" className={styles.delete} onClick={onDelete}>Delete item</button>
        </div>
      )}
      {error && <div className={styles.error} role="status">Couldn’t save — try again.</div>}
    </li>
  );
}
```

`ActionItemRow.module.css`:

- Base the row, hairline and spacing on `TodayView.module.css` `.row`. The
  check is an 16px rounded square with a hairline border; `.checkOn` fills
  with the accent colour used elsewhere and shows a ✓.
- `.done .task` uses `text-decoration: line-through` and a muted colour.
- `.priority.high` is a red chip and `.low` a muted chip.
- `.tagMuted` is a hairline outline chip; `.tag` is a filled glass chip.
- `.phrase` and `.edited` are muted 11px text.
- `.editor` is a 2-column grid on wide panes and 1 column under 560px
  (`@container` or `@media`).
- `.error` is red 12px text.

Use existing CSS variables only. Grep `frontend/src/styles` for the accent
and red tokens before inventing colours.

- [ ] **Step 2: `ActionItemsView.tsx`.** Structure:

```tsx
import { useEffect, useMemo, useState } from 'react';
import { NO_AUTOCORRECT } from '../components/noAutocorrect';
import { ActionItemRow } from './ActionItemRow';
import type { ItemChange } from './ActionItemRow';
import { DEFAULT_VIEW, effectiveTags, filterItems, groupItems, ownersOf, parseViewState } from './actionItems';
import type { ActionItem, Priority, ViewState } from './actionItems';
import styles from './ActionItemsView.module.css';

const VIEW_KEY = 'actions.view.v1';
function readView(): ViewState { try { return parseViewState(window.localStorage.getItem(VIEW_KEY)); } catch { return DEFAULT_VIEW; } }
function writeView(v: ViewState) { try { window.localStorage.setItem(VIEW_KEY, JSON.stringify(v)); } catch { /* private mode */ } }

interface Props {
  items: ActionItem[]; identitySet: boolean; today: string; userName: string;
  onUpdate: (id: number, change: ItemChange) => Promise<void>;
  onCreate: (fields: { task: string; owner: string; due: string | null; priority: Priority }) => Promise<void>;
  onDelete: (id: number) => void;
  onBulk: (ids: number[], change: { status?: 'open' | 'done'; addTags?: string[]; delete?: boolean }) => Promise<void>;
  onOpenMeeting: (meetingId: string) => void;
  onOpenSettings: () => void;
}

export function ActionItemsView(props: Props) {
  const { items, identitySet, today, userName } = props;
  const [view, setView] = useState<ViewState>(readView);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [bulkMode, setBulkMode] = useState(false);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set(['done']));
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ task: '', owner: userName, due: '', priority: 'normal' as Priority });
  useEffect(() => writeView(view), [view]);
  useEffect(() => setSelected((s) => new Set([...s].filter((id) => items.some((i) => i.id === id)))), [items]);

  const update = (patch: Partial<ViewState>) => setView((v) => ({ ...v, ...patch }));
  const visible = useMemo(() => filterItems(items, view, identitySet), [items, view, identitySet]);
  const groups = useMemo(() => groupItems(visible, view, today), [visible, view, today]);
  const allTags = useMemo(() => [...new Set(items.flatMap(effectiveTags))].sort((a, b) => a.localeCompare(b)), [items]);
  const owners = useMemo(() => ownersOf(items), [items]);
  // ... render (below)
}
```

Render, top to bottom:

1. **Header.**
   - The title "Action items" uses the `TodayView` `.title` style.
   - On the right: a "Select" toggle (bulk mode) and "+ Add item".
2. **Toolbar** (wraps on narrow widths):
   - an owner `<select>`: `Mine`, `Everyone`, then each of `owners` as
     `person:<name>`
   - a status segmented control: Open / Completed / All
   - priority chips: High / Normal / Low, toggling `view.priorities`
   - a tag menu, `<details>` with checkboxes over `allTags`, whose summary
     shows "Tags" or "Tags · 2"
   - a search `<input {...NO_AUTOCORRECT} type="search">` bound to `view.query`
   - a Group `<select>` and a Sort `<select>`
   - "Clear filters" when anything differs from `DEFAULT_VIEW` (excluding
     `groupBy` and `sortBy`)
3. **No-identity banner.** When `view.owner.kind === 'mine' && !identitySet`,
   show an inline note: "Set your name in Settings to see only your items.
   Showing everyone's." with an "Open Settings" button that calls
   `onOpenSettings`.
4. **Add row** (when `adding`). Fields:
   - task input (autofocus), Enter submits
   - owner input (default `userName`)
   - `type="date"` due
   - priority select
   - Add and Cancel buttons

   Submitting with an empty task does nothing. On success, reset the draft
   and close.
5. **Bulk bar** (when `bulkMode && selected.size`):
   - the text "N selected"
   - Complete, Reopen, "Add tag…" (inline input then Enter) and Delete
     buttons, each calling `onBulk`, then clearing the selection
6. **Groups.** Each group is a `<section>` with a heading button showing the
   label and count, which toggles `collapsed`. Completed starts collapsed,
   and `tone` maps to a CSS class (red heading for overdue). Inside is a
   `<ul>` of `ActionItemRow` with:
   - `onChange={(c) => props.onUpdate(item.id, c)}`
   - `onDelete={() => props.onDelete(item.id)}`
   - `onOpenMeeting={props.onOpenMeeting}`
   - `selectable={bulkMode}`
   - `selected={selected.has(item.id)}`
   - `onSelect`

   With `groupBy === 'none'`, the heading is omitted.
7. **Empty state.** When `visible.length === 0`, show "Nothing due — nice."
   for Open, or "No items match these filters." when filters are active.
   Reuse `EmptyState` if its props fit.

`ActionItemsView.module.css`:

- The page frame is copied from `TodayView.module.css` (`.view`, `.header`,
  `.title`).
- The toolbar is a flex row that wraps with an 8px gap; chips use the
  sidebar's pill style.
- The bulk bar is sticky at the top with an opaque background (memory:
  sticky headers must be opaque).

- [ ] **Step 3: Sidebar.** New props:

```ts
  activeActions: boolean;
  actionCounts: { open: number; overdue: number };
  onSelectActions: () => void;
```

Render directly after the Today button, **outside** the
`filters.features.calendar` condition:

```tsx
        <button className={activeActions ? `${styles.row} ${styles.rowActive}` : styles.row} onClick={onSelectActions}>
          <span className={styles.rowLabel}>Action items</span>
          {actionCounts.overdue > 0 && <span className={styles.overdueBadge} aria-label={`${actionCounts.overdue} overdue`}>{actionCounts.overdue}</span>}
          <span className={styles.count}>{actionCounts.open}</span>
        </button>
```

`rowClass(...)` for filters must also return inactive while `activeActions`
is set. Mirror how `activeToday` is handled at `Sidebar.tsx:174`.
`.overdueBadge` is a small red pill, using the same red token as above.

- [ ] **Step 4: App.tsx wiring.**

New state:

- `actionsView` (boolean), initialised to `mockState === 'actions'`
- `actionItems: ActionItem[]`
- `identitySet`
- `actionsToday` (string; mock uses `localIsoDate(MOCK_NOW_MS)`)
- `pendingUndo: { id: number; task: string } | null`

Add `'actions'` to the `MockState` union and to whatever reads `?state=`.

Behaviour:

- **Load.** Call `load = () => actionItemsApi.list().then(r => { setActionItems(r.items); setIdentitySet(r.identitySet); setActionsToday(r.today); })`
  on mount and on every `meetings.changed` event. Subscribe like the
  existing `bridge.on('meetings.changed', …)` at line ~457. In mock mode,
  seed `MOCK_ACTION_ITEMS` once.
- **Selecting the view.** `onSelectActions()` mirrors `onSelectToday()`: it
  sets `actionsView` true, and today, recording view, searching, popover and
  confirm all off. Every other selection path (`onSelectToday`,
  `onSelectRecording`, `onSelectTodayMeeting`, the `MeetingList` `onSelect`
  and `onSelectFilter`) sets `actionsView` false.
- **Rendering.** In the detail-pane switch, add `: actionsView ? (<ActionItemsView … />)`
  before `today ?`. Pass `selectedId={today || recordingView || actionsView ? null : selectedId}`
  to `MeetingList`.
- **Edits.** Apply them optimistically:
  - `onUpdate(id, change)` patches the item locally (status, due and
    tags shown immediately), calls `actionItemsApi.update`, and replaces the
    item with the server's result.
  - On rejection it restores the previous item and rethrows, so the row
    shows its error.
  - When a meeting detail is open, an update for an item of that meeting
    also patches `details[meetingId].actionItems`.
- **Delete with undo.** `onDelete(id)` calls `actionItemsApi.remove`,
  removes the item locally and sets `pendingUndo`. A toast at the bottom of
  the detail pane reads "Deleted “task” — Undo" and auto-clears after
  6,000 ms. Undo calls `actionItemsApi.restore(id)` and reinserts the item.
  Reuse an existing toast style if one exists; otherwise add `.toast` to
  `App.module.css`.
- **Create and bulk.** `onCreate` calls `create` and appends the result.
  `onBulk` calls `bulk` and replaces the returned items; for `delete`, it
  removes them and offers no undo, after a `ConfirmSheet` for more than one
  item.
- **Opening a meeting.** `onOpenMeeting(id)` sets `actionsView` false,
  calls `select(id)` and forces the Summary tab (use `forcedTab`).
- **Sidebar.** Pass `actionCounts={sidebarCounts(actionItems, identitySet, actionsToday)}`.

- [ ] **Step 5: MeetingDetail.** Replace the dots list (lines ~648–660) with a `<ul className={styles.actionItems}>` of `<ActionItemRow showMeeting={false} …>` driven by `detail.actionItems`. New props: `onUpdateItem(id, change) => Promise<void>` and `onDeleteItem(id) => void`, which App wires to the same handlers. Keep the "Action items" heading. Show it when there are items even if `detail.summary` is empty (manual items can exist without a summary).

- [ ] **Step 6: SettingsSheet.** Add an "Action items" group:

- a "Your name" text input
- an "Other names" text input (comma-separated)
- the help text: "Items whose owner matches any of these are yours. Anyone
  who shares your first name also matches."

Save on blur via `onChange({ userName })` and
`onChange({ userAliases: value.split(',').map(s => s.trim()).filter(Boolean) })`.
Extend the `onChange` patch type. In `App.tsx`, after the settings save
returns, call `load()` so the `mine` flags refresh.

- [ ] **Step 7: Build and check the mock in the browser.**

Run: `npm --prefix frontend test && npm --prefix frontend run build`. Expected: PASS, including the offline check.

The worktree can't use `preview_start` by name (memory
`worktree-session-guards`). Start the dev server instead:

```bash
npm --prefix frontend run dev -- --port 5199 --strictPort
```

Run it in the background, then open
`http://localhost:5199/meetings.html?state=actions` with `preview_start url=…`.
Check each of these:

1. The groups show in order, and the overdue heading is red.
2. Ticking an item moves it out of Open.
3. Expanding a row, editing the due date and seeing `edited`, then "Reset
   to Claude’s", restores the date.
4. Notes persist when collapsed and expanded again.
5. The tag filter works with ANY matching.
6. The owner switch works: Mine, Everyone, a person.
7. The search box filters the list.
8. Group by Tag lists an item under several tags.
9. Bulk complete works.
10. Delete and Undo work.
11. The meeting link opens the meeting's Summary tab with interactive rows.
12. The sidebar counts and badge are right.
13. Dark mode works (`resize_window colorScheme: dark`).
14. A narrow width (`?fit` overflow check) causes no horizontal overflow.

Take one screenshot for the report. Stop the dev server.

- [ ] **Step 8: Commit.** `git add frontend/ && git commit -m "Action items view, interactive rows, sidebar row and name setting"`

---

### Task 11: Whole-branch verification and hand-back (controller + Opus reviewer)

- [ ] **Step 1: Full suites.** Run `.venv/bin/python -m pytest -q` (Bash `timeout: 600000`), `npm --prefix frontend test` and `npm --prefix frontend run build`. All must pass. Record the counts under Progress.
- [ ] **Step 2: Opus whole-branch review** (`model: "opus"`), using superpowers:requesting-code-review against `master...feature/action-items`. The brief requires:
  - mutation verification of each Review Focus line: break the rule and
    confirm a named test fails, using the mutation command in "Model routing"
  - a check of the spec sections §1–§9 against the code
  - a check that the manifest mirror matches
  - a check that there are no network calls

  Fix any findings with a Sonnet implementer, then have the Opus reviewer
  re-check.
- [ ] **Step 3: Real-app check** (the `run` skill; memories `installed-app-ui-checks`, `subagents-real-data-sandbox`).
  1. Build with `scripts/build_app.sh` (in a worktree, symlink `models`
     first). **Don't use `--install` without asking the user**: it replaces
     /Applications and stops Claude Desktop's MCP connector.
  2. Launch `dist/Speakeasy.app/Contents/MacOS/Speakeasy` via Python
     `subprocess.run(env={"HOME": tmp})`, with a few seeded meetings whose
     notes carry legacy action-item strings, so the v7 import is exercised.
  3. Run the Task 10 Step 7 checklist in the real window. Above all:
     - a checkbox tick saves (the float id path)
     - a due override saves
     - notes save
  4. If screen control is declined, hand this checklist to the user rather
     than claiming it passed.
  5. Pipe JSON-RPC `tools/call` for `list_action_items`,
     `update_action_item` and `delete_action_item` into
     `Speakeasy --mcp` under the same temp HOME.
  6. Afterwards, `ls ~/Library/Application Support/Speakeasy/` and confirm
     nothing new appeared.
- [ ] **Step 4: Update this plan.** Tick the tasks and write the Progress
  section: test counts, what the real-app check covered and what it did not,
  and any deviations. Then commit.
- [ ] **Step 5: Tell the user what is done.**
  - Report the tests and the real-app check separately, per the global
    CLAUDE.md: say which of the two was actually done.
  - Ask before merging: "merge and clean up" is the user's call.
  - Give the `/clear` checkpoint block from CLAUDE.md.

## Progress

- Plan written 2026-10-09.
- Task 0 done 2026-10-09: export/recovery (v6) committed as `6af9601` and
  merged into local master as `9cef40d` (push to origin still pending, the
  user's call). Worktree `.claude/worktrees/action-items`, branch
  `feature/action-items`. Baseline: 1,285 Python passed, 52 frontend passed.
- Pre-flight scan done; rulings F1–F21 in "Pre-flight rulings" above.
- Tasks 1–8 complete, each Opus-reviewed with mutation checks and one fix
  round (commits 8c885ae..fd30fee). Full Python suite 1,391 passed after Task 8.
- Tasks 9–10 complete (Task 10 needed two fix rounds: typed dates now commit
  on blur/Enter, failed writes show visible errors, toast shadow uses a token).
- Task 11 (2026-10-09): full suites at 0b200f0 — 1,395 Python passed, 64
  frontend passed, build OK. Opus whole-branch review: all five Review Focus
  rules mutation-verified; spec §1–§7 and §9 met; manifest mirror exact; no
  network calls or new dependencies. Its fix wave (0b200f0) fixed the phrase
  without a date (row and export), single-item Select delete now has undo,
  plus `deleted_at IS NULL` and empty-`similar()` guards.
- Real-app check: built `dist/Speakeasy.app` (not installed). Launched it
  under a temp HOME with a seeded **v6** library holding 3 meetings with
  legacy strings: it migrated to v7 and imported all 6 items (3 with
  phrases). MCP over stdio: 15 tools; `list_action_items`,
  `update_action_item` (float id `1.0` accepted, `True` rejected) and
  `delete_action_item` all worked. The real App Support folder listing was
  unchanged and the real library stayed at schema 5.
  **Not done:** the on-screen UI checklist in the real window (Task 10 Step 7
  list: tick saves, due override saves, notes save, etc.). Screen access was
  declined, so this is the user's hand check. The mock-browser checklist passed
  14/14 in Task 10.
- Rulings made during execution: `ai.check_status` added in Task 1;
  legacy import positions count non-blank entries; `updated_by` validated as
  ValueError; get_meeting shows items only under `notes` (spec §6); export
  omits an empty owner (spec §9).

### TODO / open items

- Fixed after the final review (`89682a6`): deleting a meeting now refreshes
  the action-items list straight away (`loadActions()` in `onConfirmDelete`).
- User hand check of the real window: all 7 checks passed (2026-10-09).
- Follow-up fixes from that check (branch `fix/action-items-owner-and-settings`):
  the user's own items under "Jason", "You" and "Mine" now merge into one owner
  (owner words you/me/myself count as the user; the row, person list, filter,
  owner groups and the MCP owner filter use one label); settings sheet spacing
  (8 px between the name fields and between the library buttons). Opus-reviewed
  with mutation checks; 1,420 Python and 69 frontend tests pass.
- Stored-owner normalisation (same branch): with a profile name set, owners
  "me", "mine", "myself" and "you" are stored as the user's name on every write
  (save_notes merge, create, update); old rows are rewritten when the name is
  saved and on the first `actions.list` after launch (user_touched/updated_*
  left alone, mirror re-synced). Real library: profile name set to "Jason",
  1 row rewritten ("You" → "Jason"); backup taken first. 1,449 Python and 69
  frontend tests pass. A later rename (e.g. Jason → Jay) does not rewrite
  owners already stored as "Jason".
- The user's person entry now counts exactly the same items as Mine,
  including shared owners ("Refayet & Jason"); shared rows still show the
  stored owner. 72 frontend tests pass.
- Deferred minors judged "can wait" by the final review: `similar` edge cases
  now guarded; remaining ones are listed in the SDD ledger, e.g. explicit null
  handling in `update_action_item`, empty Open/Completed export headings,
  mock-data mismatches, a TZ-pinned dueBucket test, and add-row/bulk error
  text not cleared on Cancel.
- Installing this build migrates the real library 5 → 6 → 7 on first open
  (the v6 export/recovery work comes with it).
- The full list of rulings and deferred minors is in the appendix below (copied from the SDD ledger).
- Pre-existing flake seen twice: `test_meeting_recorder.py::test_gap_fill_queue_full_is_retried_on_the_next_block` (passes on rerun).

### Appendix: rulings and deferred minors (from the SDD ledger)

- Ruling: accept all F1–F21 proposed fixes, written into plan "Pre-flight rulings" — each follows spec or fixes a breaking existing test — cost if wrong: small rework per task.
- Ruling: F16 follow spec §8 wording + show phrase whenever present (plan deviated from spec) — spec is binding — cost: UI copy tweak.
- Ruling: add ai.check_status(value) to action_items.py in Task 1 fix round (F10 referenced it but no task defined it) — keeps the helper with the other pure checkers — cost if wrong: trivial move.
- Task 1: minor (deferred): similar("","") returns 1.0 when both texts are only filler words — guard `if not na or not nb: return 0.0`.
- Task 1: minor (deferred): missing tests — validate_input legacy string path, year 2000/2100/2101 limits, "UNASSIGNED" upper case; no-op `["t"]*0+` in tags case.
- Task 1: minor (deferred): due_bucket raises on malformed due — store must validate dates first.
- Ruling: legacy import position counts non-blank entries only (spec says "list index") — same ordering, contiguous positions, brief's test requires it — cost if wrong: none visible.
- Task 2: minor (deferred): import str(raw) turns non-string legacy entries (null/dict) into "None"/repr — skip non-str.
- Task 2: minor (deferred): no test for import skipping malformed/non-list action_items_json.
- Task 3: minor (deferred): _names loads all tag rows per list/get call (brief design).
- Task 3: minor (deferred): status='open' clause in delete_untouched_for_meeting redundant.
- Task 3: minor (deferred): bulk two-pass "validate first" comment overstates — transaction already gives atomicity.
- Task 4: minor (deferred): add `AND deleted_at IS NULL` to delete_untouched_for_meeting (suppression relies on soft-delete always setting user_touched=1).
- Ruling: add the over-limit stored-identity assertion — adds coverage only, no plan conflict — cost if wrong: none.
- Task 5: minor (deferred): one bad stored alias discards whole identity incl. valid name (brief's code).
- Task 5: minor (deferred): due-date rule tested by 3 keywords, not verbatim; 80-char/alias limits tested only in Task 1.
- Ruling: get_meeting shows items only inside notes (manual items on unsummarised meeting reachable via list_action_items meeting_id) — spec §6 says get_meeting.notes.action_items — cost if wrong: Claude misses manual items on unsummarised meetings via get_meeting.
- Task 6: minor (deferred): explicit null inconsistent in update_action_item (due/owner/tags clear, priority resets, task/notes/status error); spec undefined.
- Task 6: minor (deferred): MCP-layer tests missing for sort=meeting/created and owner/meeting_id/due_from/due_to filters; meeting_id not validated.
- Task 7: minor (deferred): settings.meetings.set applies appearance/login before set_identity — bad name leaves partial save (existing pattern).
- Task 7: minor (deferred): no action-item test sends ValueError through _wrap (generic test covers branch); bridge check_id duplicates library's (defence in depth).
- Ruling: export omits empty owner rather than printing "Unassigned" — spec §9 and brief say omit; the specific export rule beats the general display constraint — cost if wrong: one-line format change.
- Task 8: minor (deferred): empty "## Open"/"## Completed" headings still printed when a group is empty; From line skipped untested when meeting title/start missing.
- Task 9: minor (deferred): local-vs-UTC weekday mutation only caught west of UTC — pin with a fixed TZ test.
- Task 9: minor (deferred): mock items moved between meetings/owners (Retro→Sprint Planning, Priya→Jason) — mock summaries no longer match items.
- Task 9: minor (deferred): mock create takes title/tags from an existing item (null for item-less meeting); MOCK_TODAY duplicates MOCK_NOW_MS day.
- Task 10: minor (deferred): opening multi-delete confirm clears selection, cancel loses it (ids correct); single-item bulk delete has no confirm/undo (per brief); add-row owner draft stale after name change, Cancel doesn't reset; loadActions rewrites every cached detail per meetings.changed.
- Task 10: minor (deferred): after a due edit regroups a row the input loses focus (mostly addressed by blur-commit fix).
- Task 10: minor (deferred): formError/bulkError not cleared by Cancel; failure notice can reappear after undo toast; No date/Reset while draft unsaved = two ordered writes; list-load note not browser-checked.
- Final: parked — meeting delete doesn't refresh action items until 10 s poll — Ruling: no second fix wave per process; self-heals within 10 s; one-line fix `void loadActions();` after meetings.filters refetch in onConfirmDelete (App.tsx ~1057) — cost if wrong: stale items/dead links visible up to 10 s after deleting a meeting.
- Task 11: Step 2 done (final review: With fixes → fixed except parked item 4)
