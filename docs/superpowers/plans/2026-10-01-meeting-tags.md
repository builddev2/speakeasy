# Meeting Tags (slugs, aliases, provenance) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make meeting tags consistent and safe to scale. One tag per canonical slug, with aliases. Each meeting–tag link records whether the user or Claude added it, and tags the user removed stay removed. New MCP tools `tag_meetings` and `manage_tags`, plus steering so Claude reuses tags.

**Architecture:** A new pure module `speakeasy/tag_names.py` owns slug and name rules. Schema v3 (`meeting_store.py`) adds slug, description, aliases, link provenance and suppressions, through a Python migration that merges tags whose slugs collide. `MeetingLibrary` resolves every tag name through slug, then alias. `save_notes` touches only Claude's links; new `tag_meetings`, `rename_tag`, `merge_tags`, `delete_tag` and `describe_tag` methods cover the rest. `mcp_tools.py` exposes the tools, with descriptions mirrored in the MCPB manifest.

**Tech Stack:** Python 3.11, sqlite3 (WAL, FTS5), pytest; React/TS frontend (one string change).

**Spec:** `docs/superpowers/specs/2026-10-01-meeting-tags-design.md`

## Global Constraints

- Fully offline; no new dependencies.
- Slug = `casefold()` → `unicodedata.normalize("NFKD", …)` → keep only `str.isalnum()` chars. No stemming. An empty slug is rejected: `"Tag … must contain a letter or digit."`
- Display name: whitespace collapsed, at most **40** characters (`"Tags are at most 40 characters."`).
- At most **20** tags per meeting, all sources together (`"At most 20 tags per meeting."`).
- `save_notes` creates at most **3** new tags per call. Otherwise nothing is written and it raises `"Would create N new tags (a, b, c, d); at most 3 per call. Reuse tags from list_tags or drop some."`
- Tag descriptions are at most **200** characters. `tag_meetings` takes **1–100** meeting ids.
- `meeting_tags.source` ∈ `{'user', 'claude'}`; pre-v3 links become `'claude'`.
- `SCHEMA_VERSION = 3`. Migrations: append only, never edit a shipped entry.
- Every MCP tool or description change is mirrored in `packaging/mcpb/manifest.json` (`tests/test_mcpb.py` enforces this).
- Tests never touch real App Support data: use the `library_path` fixture (HOME-isolated by `tests/conftest.py`).
- Run tests with `.venv/bin/python -m pytest …`. For the full suite, give the Bash call `timeout: 600000`.
- Mutation checks: run with `python -B` and clear `__pycache__` (see memory note on mutation gotchas).

## Review Focus

1. **Removing a tag that then has no meetings must keep the suppression.** Otherwise orphan cleanup cascades it away and Claude re-adds the tag. Pinned in Task 3 (`test_tag_meetings_remove_unlinks_and_suppresses_even_unlinked`).
2. **Claude naming a user tag with another spelling.** The link must stay `user`, no duplicate is created, and it survives `save_notes(tags=[])`. Pinned in Task 4 (`test_claude_naming_a_user_tag_keeps_it_user`).
3. **A failed `save_notes` (new-tag cap or 20 limit) must not save the summary either.** Pinned in Task 4 (`test_new_tag_cap_allows_three_and_rejects_four_atomically`).
4. **Merging where a meeting has the target linked and the source suppressed.** The link wins, and suppressions on other meetings move to the target. Pinned in Task 5 (`test_merge_link_beats_suppression_and_moves_suppressions`).
5. **The app and the MCP server opening a v2 library at the same moment** (the first launch after install). Both must succeed on v3 with no duplicated work. Pinned in Task 2 (`test_concurrent_v3_migration_converges`). The existing `test_migrate_converges_when_a_slower_v1_resets_the_version` also covers the v1-reset interleave, now that `SCHEMA_VERSION` is 3.

## File Structure

- Create `speakeasy/tag_names.py`: slug, name cleaning and limit constants. No I/O.
- Modify `speakeasy/meeting_store.py`: v3 migration (callable migrations).
- Modify `speakeasy/meeting_library.py`: tag resolution, filters, reads, `tag_meetings`, `save_notes` provenance, tag admin.
- Modify `speakeasy/mcp_tools.py` and `packaging/mcpb/manifest.json`: tools and descriptions.
- Modify `frontend/src/meetings/MeetingDetail.tsx`: the copied prompt.
- Modify `README.md` and `AGENTS.md`: the tool list and the "only save_notes writes" rule.
- Tests:
  - Create `tests/test_tag_names.py` and `tests/test_meeting_tags.py`.
  - Modify `tests/test_meeting_store.py`, `tests/test_mcp_tools.py`, and `tests/test_meeting_library.py` if needed.

---

### Task 1: `tag_names` module

**Files:**
- Create: `speakeasy/tag_names.py`
- Test: `tests/test_tag_names.py`

**Interfaces:**
- Produces: `tag_slug(name) -> str`, `clean_tag_name(raw) -> str`, `clean_tag_names(raws) -> list[str]`, and the constants `MAX_TAG_CHARS = 40`, `MAX_TAGS_PER_MEETING = 20`, `MAX_NEW_TAGS_PER_SAVE = 3`, `MAX_DESCRIPTION_CHARS = 200`.

- [ ] **Step 1: Write the failing tests** in `tests/test_tag_names.py`

```python
import pytest

from speakeasy.tag_names import clean_tag_name, clean_tag_names, tag_slug


@pytest.mark.parametrize("name", ["Project X", "project-x", "ProjectX",
                                  "  PROJECT_x ", "project.x!"])
def test_spelling_variants_share_one_slug(name):
    assert tag_slug(name) == "projectx"


def test_diacritics_and_compatibility_forms_fold():
    assert tag_slug("Café") == tag_slug("cafe") == "cafe"
    assert tag_slug("ﬁnance") == "finance"
    assert tag_slug("Straße") == "strasse"
    assert tag_slug("İstanbul") == "istanbul"


def test_no_stemming_and_digits_kept():
    assert tag_slug("plan") != tag_slug("plans")
    assert tag_slug("Q3 2026") == "q32026"


def test_clean_tag_name_collapses_whitespace_and_validates():
    assert clean_tag_name("  Q3   planning ") == "Q3 planning"
    assert clean_tag_name("x" * 40) == "x" * 40
    with pytest.raises(ValueError, match="at most 40 characters"):
        clean_tag_name("x" * 41)
    with pytest.raises(ValueError, match="letter or digit"):
        clean_tag_name("!!!")


def test_clean_tag_names_skips_blanks_and_keeps_first_spelling():
    assert clean_tag_names([" DMT", "dmt", "", "  ", "D-M-T", "VFA "]) == ["DMT", "VFA"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_tag_names.py -v`
Expected: FAIL. `ModuleNotFoundError: speakeasy.tag_names`.

- [ ] **Step 3: Implement** `speakeasy/tag_names.py`

```python
"""Tag display names and canonical slugs.

A slug is a tag's identity: names with the same slug are the same tag
("Project X", "project-x", "ProjectX" -> "projectx"). Case, spacing,
punctuation and diacritics never split a tag; there is no stemming, so
"plan" and "plans" stay apart. Shared by meeting_store (the v3 backfill)
and meeting_library. Pure: no I/O.
"""

import unicodedata

MAX_TAG_CHARS = 40
MAX_TAGS_PER_MEETING = 20
MAX_NEW_TAGS_PER_SAVE = 3
MAX_DESCRIPTION_CHARS = 200


def tag_slug(name) -> str:
    # NFKD after casefold splits accents (and casefold's own combining dot,
    # as in "İ") into marks, which isalnum() then drops with punctuation.
    folded = unicodedata.normalize("NFKD", str(name).casefold())
    return "".join(c for c in folded if c.isalnum())


def clean_tag_name(raw) -> str:
    """One display name: whitespace collapsed, length and slug checked."""
    name = " ".join(str(raw).split())
    if len(name) > MAX_TAG_CHARS:
        raise ValueError(f"Tags are at most {MAX_TAG_CHARS} characters.")
    if not tag_slug(name):
        raise ValueError(f"Tag {name!r} must contain a letter or digit.")
    return name


def clean_tag_names(raws) -> list[str]:
    """Clean a list: blank entries skipped, one name per slug (first wins)."""
    seen, result = set(), []
    for raw in raws:
        if not str(raw).strip():
            continue
        name = clean_tag_name(raw)
        slug = tag_slug(name)
        if slug not in seen:
            seen.add(slug)
            result.append(name)
    return result
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_tag_names.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/tag_names.py tests/test_tag_names.py
git commit -m "Tags: canonical slug and name rules (tag_names)"
```

---

### Task 2: Schema v3 migration

**Files:**
- Modify: `speakeasy/meeting_store.py`: `SCHEMA_VERSION` (line 29), `_MIGRATIONS` and its comment (lines 171–178), and `migrate()` (lines 230–254).
- Test: `tests/test_meeting_store.py`

**Interfaces:**
- Consumes: `tag_slug` (Task 1).
- Produces these tables and columns, which later tasks rely on:
  - `tags(id, name UNIQUE NOCASE, slug UNIQUE, description NOT NULL DEFAULT '', created_at)`
  - `tag_aliases(slug PK, name, tag_id → tags CASCADE)`
  - `meeting_tags(meeting_id, tag_id, source NOT NULL DEFAULT 'claude' CHECK IN ('user','claude'), added_at)`
  - `tag_suppressions(meeting_id → meetings CASCADE, tag_id → tags CASCADE, created_at NOT NULL, PK(meeting_id, tag_id))`
  - `meeting_store.SCHEMA_VERSION == 3`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_meeting_store.py`:

```python
def _v2_library(path, meetings):
    """A v2 library file: one meeting per entry, each a list of tag names."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(meeting_store._SCHEMA_V1)
        conn.executescript(meeting_store._SCHEMA_V2)
        for i, names in enumerate(meetings):
            mid = f"20260901-0000{i:02d}-abcd"
            conn.execute(
                "INSERT INTO meetings (id, title, started_at, tz_offset_minutes,"
                " duration_seconds, capture_mode, system_audio_status, capture_scope,"
                " track_offsets_json, capture_health_json, source, created_at, updated_at)"
                " VALUES (?, 't', '2026-09-01T00:00:00Z', 0, 60, 'mic', 'off', 'mic',"
                " '{}', '{}', 'recorded', 'x', 'x')", (mid,))
            for name in names:
                conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
                conn.execute("INSERT INTO meeting_tags (meeting_id, tag_id)"
                             " SELECT ?, id FROM tags WHERE name = ?", (mid, name))
        conn.commit()
    finally:
        conn.close()


def test_v3_merges_tags_that_collide_on_slug(library_path):
    # Insertion order gives ids: Project X=1, project-x=2, Ops=3, !!!=4.
    _v2_library(library_path, [["Project X"], ["project-x", "Ops"],
                               ["project-x"], ["!!!"]])
    conn = meeting_store.connect(library_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        tags = {r["slug"]: r["name"] for r in conn.execute("SELECT name, slug FROM tags")}
        # project-x has the most links, so it survives; an empty slug keeps
        # its tag under tag<id> rather than losing it.
        assert tags == {"projectx": "project-x", "ops": "Ops", "tag4": "!!!"}
        links = conn.execute(
            "SELECT mt.meeting_id, mt.source, mt.added_at FROM meeting_tags mt"
            " JOIN tags t ON t.id = mt.tag_id WHERE t.slug = 'projectx'"
            " ORDER BY mt.meeting_id").fetchall()
        assert [tuple(r) for r in links] == [
            ("20260901-000000-abcd", "claude", None),
            ("20260901-000001-abcd", "claude", None),
            ("20260901-000002-abcd", "claude", None)]
        assert _tables(conn) >= {"tag_aliases", "tag_suppressions"}
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO tags (name, slug) VALUES ('PROJECT x!', 'projectx')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE meeting_tags SET source = 'robot'")
    finally:
        conn.close()


def test_v3_collision_tie_keeps_the_lowest_id(library_path):
    _v2_library(library_path, [["B-1"], ["b1"]])
    conn = meeting_store.connect(library_path)
    try:
        assert [tuple(r) for r in conn.execute("SELECT name, slug FROM tags")] == [("B-1", "b1")]
        assert conn.execute("SELECT COUNT(*) FROM meeting_tags").fetchone()[0] == 2
    finally:
        conn.close()


def test_concurrent_v3_migration_converges(tmp_path):
    """The app and the MCP server open a v2 library at the same moment
    (first launch after install): both succeed and the backfill runs once."""
    for k in range(10):
        path = tmp_path / f"lib{k}.sqlite"
        _v2_library(path, [["Project X"], ["project-x"]])
        barrier = threading.Barrier(2)
        errors = [None, None]

        def worker(i, path=path, errors=errors):
            try:
                barrier.wait(timeout=10)
                meeting_store.connect(path).close()
            except BaseException as exc:  # noqa: BLE001 - surfaced via errors[i]
                errors[i] = exc

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        assert errors == [None, None], (k, errors)
        check = sqlite3.connect(path)
        try:
            assert check.execute("PRAGMA user_version").fetchone()[0] == 3
            assert check.execute("SELECT slug FROM tags").fetchall() == [("projectx",)]
        finally:
            check.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_meeting_store.py -v`
Expected: the 3 new tests FAIL (`user_version` is 2, and there is no `slug` column).

- [ ] **Step 3: Implement** in `speakeasy/meeting_store.py`

Add `from .tag_names import tag_slug` to the imports, and set `SCHEMA_VERSION = 3`. After `_SCHEMA_V2`, add:

```python
# v3: canonical tag slugs, aliases, per-link provenance and suppressions.
# Python rather than a script: the backfill needs tag_slug() and merges tags
# whose names collide on slug. It takes the write lock first and only then
# checks whether another connection already applied it (slug column
# present), so concurrent first opens and the slower-v1 version reset
# (see below) both converge without a duplicate-column error.
def _migrate_v3(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(tags)")}
        if "slug" not in columns:
            conn.execute("ALTER TABLE tags ADD COLUMN slug TEXT")
            conn.execute("ALTER TABLE tags ADD COLUMN description TEXT NOT NULL DEFAULT ''")
            conn.execute("ALTER TABLE tags ADD COLUMN created_at TEXT")
            conn.execute(
                "ALTER TABLE meeting_tags ADD COLUMN source TEXT NOT NULL"
                " DEFAULT 'claude' CHECK (source IN ('user', 'claude'))")
            conn.execute("ALTER TABLE meeting_tags ADD COLUMN added_at TEXT")
            conn.execute(
                "CREATE TABLE tag_aliases ("
                " slug TEXT PRIMARY KEY,"
                " name TEXT NOT NULL,"
                " tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE)")
            conn.execute(
                "CREATE TABLE tag_suppressions ("
                " meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,"
                " tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,"
                " created_at TEXT NOT NULL,"
                " PRIMARY KEY (meeting_id, tag_id))")
            _backfill_tag_slugs(conn)
            conn.execute("CREATE UNIQUE INDEX tags_slug ON tags(slug)")
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _backfill_tag_slugs(conn: sqlite3.Connection) -> None:
    """Give every tag its slug; fold tags that share one into the tag with
    the most meetings (ties: lowest id). Indexed access, not names: migrate()
    may run on a plain connection without the Row factory, and with
    foreign keys off, so links are deleted explicitly."""
    survivors = {}
    rows = conn.execute(
        "SELECT t.id, t.name, (SELECT COUNT(*) FROM meeting_tags mt"
        " WHERE mt.tag_id = t.id) AS n FROM tags t ORDER BY n DESC, t.id").fetchall()
    for tag_id, name, _ in rows:
        slug = tag_slug(name) or f"tag{tag_id}"
        survivor = survivors.setdefault(slug, tag_id)
        if survivor == tag_id:
            conn.execute("UPDATE tags SET slug = ? WHERE id = ?", (slug, tag_id))
            continue
        conn.execute(
            "INSERT OR IGNORE INTO meeting_tags (meeting_id, tag_id)"
            " SELECT meeting_id, ? FROM meeting_tags WHERE tag_id = ?", (survivor, tag_id))
        conn.execute("DELETE FROM meeting_tags WHERE tag_id = ?", (tag_id,))
        conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
```

Change the list to `_MIGRATIONS = [(1, _SCHEMA_V1), (2, _SCHEMA_V2), (3, _migrate_v3)]`. Extend its comment with: "An entry may be a callable taking the connection, for a migration that needs Python (v3); it must take the write lock itself and detect 'already applied' inside it."

In `migrate()`, replace the body of `if version < target:` so that callables run directly:

```python
        if version < target:
            if callable(script):
                script(conn)
                version = target
                continue
            try:
                conn.executescript(script)
            except sqlite3.OperationalError as exc:
                ...  # unchanged duplicate-column repair
            version = target
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_meeting_store.py tests/test_meeting_library.py tests/test_meeting_library_mcp.py -v`
Expected:
- all of `test_meeting_store.py` passes, including `test_migrate_converges_when_a_slower_v1_resets_the_version` and `test_newer_schema_is_refused`;
- the library tests still pass (old writes don't set `slug`; NULLs are allowed by the unique index until Task 3).

- [ ] **Step 5: Commit**

```bash
git add speakeasy/meeting_store.py tests/test_meeting_store.py
git commit -m "Tags: schema v3 — slug, aliases, link provenance, suppressions"
```

---

### Task 3: Tag resolution, filters, reads and `tag_meetings`

**Files:**
- Modify: `speakeasy/meeting_library.py`:
  - imports;
  - `_normalise_tags` (lines 92–104), which is replaced by `clean_tag_names` plus a count check;
  - `meeting_filters` tag clause (lines 300–305);
  - `delete()` orphan cleanup (lines 485–489);
  - the `save_notes` tag block (lines 645–673);
  - new dataclasses and methods.
- Create: `tests/test_meeting_tags.py`

**Interfaces:**
- Consumes: Task 1 functions and constants; the Task 2 schema.
- Produces:
  - `@dataclass TagInfo(name: str, description: str, aliases: list[str], count: int)`
  - `@dataclass TagUpdate(meetings: list[tuple[str, list[str]]], created_tags: list[str], not_found: list[str])`
  - `MeetingLibrary.tag_meetings(meeting_ids, *, add=(), remove=()) -> TagUpdate`
  - `MeetingLibrary.meeting_tag_details(meeting_id) -> list[tuple[str, str]]`, giving `(name, source)` ordered by name, NOCASE
  - `MeetingLibrary.tag_catalog() -> list[TagInfo]`, for used tags only, ordered by count desc then name
  - Private helpers used by Tasks 4 and 5: `_find_tag(conn, name) -> int | None`, `_create_tag(conn, name) -> int`, `_require_tag(conn, name) -> int`, `_tag_name(conn, tag_id) -> str`, `_tag_count(conn, meeting_id) -> int`, `_tag_info(conn, tag_id) -> TagInfo`, `_drop_orphan_tags(conn)`
  - `list_tags()` keeps returning `[(name, count)]`.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_meeting_tags.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from speakeasy import meeting_store
from speakeasy.meeting_library import MeetingLibrary, NewMeeting, TagInfo
from speakeasy.meetings import MeetingSegment

EDT = timezone(timedelta(hours=-4))


@pytest.fixture
def lib(library_path):
    return MeetingLibrary()


def _meeting(lib, minute=0):
    return lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0.0, 4.0, "hello there")],
        duration_seconds=60.0,
        started_at=datetime(2026, 9, 24, 13, minute, 0, tzinfo=EDT)))


def _sql(path, query, params=()):
    conn = meeting_store.connect(path)
    try:
        with conn:
            return [tuple(r) for r in conn.execute(query, params)]
    finally:
        conn.close()


def test_tag_meetings_adds_user_tags_across_meetings(lib):
    a, b = _meeting(lib, 0), _meeting(lib, 1)
    update = lib.tag_meetings([a, b], add=["Project X", "Ops"])
    assert update.created_tags == ["Project X", "Ops"]
    assert update.not_found == []
    assert update.meetings == [(a, ["Ops", "Project X"]), (b, ["Ops", "Project X"])]
    assert lib.meeting_tag_details(a) == [("Ops", "user"), ("Project X", "user")]


def test_tag_meetings_upgrades_claude_link_and_reuses_spelling(lib):
    a = _meeting(lib)
    lib.save_notes(a, tags=["Project X"])
    assert lib.meeting_tag_details(a) == [("Project X", "claude")]
    update = lib.tag_meetings([a], add=["project-x"])
    assert update.created_tags == []
    assert lib.meeting_tag_details(a) == [("Project X", "user")]


def test_tag_meetings_remove_unlinks_and_suppresses_even_unlinked(lib, library_path):
    a, b = _meeting(lib, 0), _meeting(lib, 1)
    lib.tag_meetings([a], add=["Ops"])
    update = lib.tag_meetings([a, b], remove=["ops", "Nope"])
    assert update.not_found == ["Nope"]
    assert lib.meeting_tags(a) == [] and lib.meeting_tags(b) == []
    # Ops is now on no meeting, but orphan cleanup must keep it: deleting it
    # would cascade the suppressions away and let Claude re-add it.
    assert _sql(library_path,
                "SELECT ts.meeting_id FROM tag_suppressions ts JOIN tags t"
                " ON t.id = ts.tag_id WHERE t.slug = 'ops' ORDER BY ts.meeting_id") == [(a,), (b,)]


def test_tag_meetings_add_clears_suppression(lib, library_path):
    a = _meeting(lib)
    lib.tag_meetings([a], add=["Ops"])
    lib.tag_meetings([a], remove=["Ops"])
    lib.tag_meetings([a], add=["OPS"])
    assert lib.meeting_tag_details(a) == [("Ops", "user")]
    assert _sql(library_path, "SELECT COUNT(*) FROM tag_suppressions") == [(0,)]


def test_tag_meetings_validates_before_writing(lib):
    a = _meeting(lib)
    with pytest.raises(ValueError, match="No meeting with id 20200101-000000-abcd"):
        lib.tag_meetings([a, "20200101-000000-abcd"], add=["Ops"])
    assert lib.meeting_tags(a) == [] and lib.list_tags() == []
    with pytest.raises(ValueError, match="both add and remove"):
        lib.tag_meetings([a], add=["Ops"], remove=["ops"])
    assert lib.list_tags() == []
    with pytest.raises(ValueError, match="between 1 and 100"):
        lib.tag_meetings([], add=["Ops"])
    with pytest.raises(ValueError, match="between 1 and 100"):
        lib.tag_meetings([a] + [f"20200101-0000{i:02d}-abcd" for i in range(100)], add=["x"])
    with pytest.raises(ValueError, match="at least one tag"):
        lib.tag_meetings([a])


def test_tag_meetings_enforces_twenty_per_meeting(lib):
    a = _meeting(lib)
    lib.tag_meetings([a], add=[f"t{i}" for i in range(20)])
    with pytest.raises(ValueError, match="At most 20 tags per meeting"):
        lib.tag_meetings([a], add=["one more"])
    assert len(lib.meeting_tags(a)) == 20


def test_tag_filter_matches_variant_spelling_and_alias(lib, library_path):
    a, b = _meeting(lib, 0), _meeting(lib, 1)
    lib.tag_meetings([a], add=["Project X"])
    lib.tag_meetings([b], add=["Ops"])
    assert [m.meeting_id for m in lib.list_meetings(tag="project-x")] == [a]
    assert [m.meeting_id for m in lib.list_meetings(tag="Project X")] == [a]
    _sql(library_path, "INSERT INTO tag_aliases (slug, name, tag_id)"
                       " SELECT 'operations', 'Operations', id FROM tags WHERE slug = 'ops'")
    assert [m.meeting_id for m in lib.list_meetings(tag="Operations")] == [b]
    assert [h.meeting_id for h in lib.search("hello", tag="operations")] == [b]
    assert lib.list_meetings(tag="!!!") == []


def test_tag_catalog_lists_used_tags_with_description_and_aliases(lib, library_path):
    a, b = _meeting(lib, 0), _meeting(lib, 1)
    lib.tag_meetings([a, b], add=["Ops"])
    lib.tag_meetings([a], add=["Budget"])
    _sql(library_path, "UPDATE tags SET description = 'Run the shop' WHERE slug = 'ops'")
    _sql(library_path, "INSERT INTO tag_aliases (slug, name, tag_id)"
                       " SELECT 'operations', 'Operations', id FROM tags WHERE slug = 'ops'")
    assert lib.tag_catalog() == [TagInfo("Ops", "Run the shop", ["Operations"], 2),
                                 TagInfo("Budget", "", [], 1)]
    assert lib.list_tags() == [("Ops", 2), ("Budget", 1)]


def test_orphan_cleanup_keeps_tags_that_still_mean_something(lib, library_path):
    a = _meeting(lib)
    lib.save_notes(a, tags=["Plain", "Described", "Aliased"])
    _sql(library_path, "UPDATE tags SET description = 'kept' WHERE slug = 'described'")
    _sql(library_path, "INSERT INTO tag_aliases (slug, name, tag_id)"
                       " SELECT 'other', 'Other', id FROM tags WHERE slug = 'aliased'")
    lib.save_notes(a, tags=[])
    assert {r[0] for r in _sql(library_path, "SELECT name FROM tags")} == {"Described", "Aliased"}


def test_new_tags_get_slug_and_created_at(lib, library_path):
    a = _meeting(lib)
    lib.save_notes(a, tags=["Q3 Planning"])
    [(slug, created_at)] = _sql(library_path, "SELECT slug, created_at FROM tags")
    assert slug == "q3planning" and created_at.endswith("Z")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_meeting_tags.py -v`
Expected: FAIL. `ImportError: cannot import name 'TagInfo'`.

- [ ] **Step 3: Implement** in `speakeasy/meeting_library.py`

Imports:

```python
from .tag_names import (MAX_DESCRIPTION_CHARS, MAX_NEW_TAGS_PER_SAVE,
                        MAX_TAGS_PER_MEETING, clean_tag_name, clean_tag_names, tag_slug)
```

Delete `_normalise_tags`. After `CalendarEvent`, add:

```python
@dataclass
class TagInfo:
    name: str
    description: str
    aliases: list[str]   # other spellings that resolve to this tag
    count: int           # meetings using it


@dataclass
class TagUpdate:
    meetings: list[tuple[str, list[str]]]  # (meeting id, its tags afterwards)
    created_tags: list[str]
    not_found: list[str]                   # remove names that matched no tag
```

Replace the `meeting_filters` tag clause:

```python
    if tag:
        # Any spelling of the tag, or an alias left by a rename/merge.
        slug = tag_slug(tag)
        clauses.append(
            "EXISTS (SELECT 1 FROM meeting_tags mt JOIN tags t ON t.id = mt.tag_id"
            " WHERE mt.meeting_id = m.id AND (t.slug = ? OR t.id IN"
            " (SELECT tag_id FROM tag_aliases WHERE slug = ?)))"
        )
        params += [slug, slug]
```

In `delete()`, replace the inline orphan `DELETE` with `self._drop_orphan_tags(conn)` and update the comment.

In `save_notes`, replace validation `tags = _normalise_tags(tags)` with:

```python
        if tags is not None:
            tags = clean_tag_names(tags)
            if len(tags) > MAX_TAGS_PER_MEETING:
                raise ValueError(f"At most {MAX_TAGS_PER_MEETING} tags per meeting.")
```

and replace the tag write block with this interim version (Task 4 replaces it again):

```python
            if tags is not None:
                conn.execute("DELETE FROM meeting_tags WHERE meeting_id = ?", (meeting_id,))
                now = _now_iso()
                for name in tags:
                    tag_id = self._find_tag(conn, name)
                    if tag_id is None:
                        tag_id = self._create_tag(conn, name)
                    conn.execute(
                        "INSERT OR IGNORE INTO meeting_tags (meeting_id, tag_id, added_at)"
                        " VALUES (?, ?, ?)", (meeting_id, tag_id, now))
                self._drop_orphan_tags(conn)
```

Add a `# -- tags ---` section to `MeetingLibrary`, after `list_tags`:

```python
    # -- tags -----------------------------------------------------------------

    @staticmethod
    def _find_tag(conn, name) -> int | None:
        """The tag a name means: by slug, then by alias. Never creates."""
        slug = tag_slug(name)
        row = conn.execute("SELECT id FROM tags WHERE slug = ?", (slug,)).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT tag_id FROM tag_aliases WHERE slug = ?", (slug,)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _create_tag(conn, name) -> int:
        return conn.execute(
            "INSERT INTO tags (name, slug, created_at) VALUES (?, ?, ?)",
            (name, tag_slug(name), _now_iso())).lastrowid

    def _require_tag(self, conn, name) -> int:
        tag_id = self._find_tag(conn, clean_tag_name(name))
        if tag_id is None:
            raise ValueError(f"No tag named {name!r}.")
        return tag_id

    @staticmethod
    def _tag_name(conn, tag_id) -> str:
        return conn.execute("SELECT name FROM tags WHERE id = ?", (tag_id,)).fetchone()[0]

    @staticmethod
    def _tag_count(conn, meeting_id) -> int:
        return conn.execute(
            "SELECT COUNT(*) FROM meeting_tags WHERE meeting_id = ?", (meeting_id,)).fetchone()[0]

    @staticmethod
    def _tag_info(conn, tag_id) -> TagInfo:
        name, description = conn.execute(
            "SELECT name, description FROM tags WHERE id = ?", (tag_id,)).fetchone()
        aliases = [r[0] for r in conn.execute(
            "SELECT name FROM tag_aliases WHERE tag_id = ? ORDER BY name COLLATE NOCASE",
            (tag_id,))]
        count = conn.execute(
            "SELECT COUNT(*) FROM meeting_tags WHERE tag_id = ?", (tag_id,)).fetchone()[0]
        return TagInfo(name, description, aliases, count)

    @staticmethod
    def _drop_orphan_tags(conn) -> None:
        # A tag no meeting uses is clutter, unless it still means something:
        # a description, an alias, or a user's "not this tag on this meeting"
        # (deleting the tag would cascade that suppression away).
        conn.execute(
            "DELETE FROM tags WHERE description = ''"
            " AND id NOT IN (SELECT tag_id FROM meeting_tags)"
            " AND id NOT IN (SELECT tag_id FROM tag_aliases)"
            " AND id NOT IN (SELECT tag_id FROM tag_suppressions)")

    def tag_catalog(self) -> list[TagInfo]:
        """Tags in use, most used first, with descriptions and aliases."""
        with self._transaction() as conn:
            ids = [r[0] for r in conn.execute(
                "SELECT t.id FROM tags t JOIN meeting_tags mt ON mt.tag_id = t.id"
                " GROUP BY t.id ORDER BY COUNT(*) DESC, t.name COLLATE NOCASE")]
            return [self._tag_info(conn, tag_id) for tag_id in ids]

    def meeting_tag_details(self, meeting_id: str) -> list[tuple[str, str]]:
        """[(tag name, 'user' | 'claude')] for one meeting."""
        _check_id(meeting_id)
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)).fetchone() is None:
                raise MeetingNotFound(meeting_id)
            return [(r[0], r[1]) for r in conn.execute(
                "SELECT t.name, mt.source FROM meeting_tags mt JOIN tags t ON t.id = mt.tag_id"
                " WHERE mt.meeting_id = ? ORDER BY t.name COLLATE NOCASE", (meeting_id,))]

    def tag_meetings(self, meeting_ids, *, add=(), remove=()) -> TagUpdate:
        """The user's explicit tagging. Added tags become source 'user'
        (kept when Claude re-saves notes); removed tags are unlinked and
        suppressed so save_notes will not add them back. Everything is
        checked before the first write; one transaction for all meetings."""
        ids = list(dict.fromkeys(str(i) for i in meeting_ids))
        if not 1 <= len(ids) <= 100:
            raise ValueError("Give between 1 and 100 meeting ids.")
        for meeting_id in ids:
            _check_id(meeting_id)
        add_names, remove_names = clean_tag_names(add), clean_tag_names(remove)
        if not add_names and not remove_names:
            raise ValueError("Give at least one tag to add or remove.")
        with self._transaction() as conn:
            missing = [i for i in ids if conn.execute(
                "SELECT 1 FROM meetings WHERE id = ?", (i,)).fetchone() is None]
            if missing:
                raise ValueError(f"No meeting with id {', '.join(missing)}.")
            add_ids, created = [], []
            for name in add_names:
                tag_id = self._find_tag(conn, name)
                if tag_id is None:
                    tag_id = self._create_tag(conn, name)
                    created.append(name)
                if tag_id not in add_ids:
                    add_ids.append(tag_id)
            remove_ids, not_found = [], []
            for name in remove_names:
                tag_id = self._find_tag(conn, name)
                if tag_id is None:
                    not_found.append(name)
                elif tag_id not in remove_ids:
                    remove_ids.append(tag_id)
            both = sorted(self._tag_name(conn, t) for t in set(add_ids) & set(remove_ids))
            if both:
                raise ValueError(f"{', '.join(both)} is in both add and remove.")
            now = _now_iso()
            for meeting_id in ids:
                self._touch(conn, meeting_id)
                for tag_id in add_ids:
                    conn.execute(
                        "INSERT INTO meeting_tags (meeting_id, tag_id, source, added_at)"
                        " VALUES (?, ?, 'user', ?) ON CONFLICT(meeting_id, tag_id)"
                        " DO UPDATE SET source = 'user'", (meeting_id, tag_id, now))
                    conn.execute(
                        "DELETE FROM tag_suppressions WHERE meeting_id = ? AND tag_id = ?",
                        (meeting_id, tag_id))
                for tag_id in remove_ids:
                    conn.execute(
                        "DELETE FROM meeting_tags WHERE meeting_id = ? AND tag_id = ?",
                        (meeting_id, tag_id))
                    conn.execute(
                        "INSERT OR IGNORE INTO tag_suppressions (meeting_id, tag_id, created_at)"
                        " VALUES (?, ?, ?)", (meeting_id, tag_id, now))
                if self._tag_count(conn, meeting_id) > MAX_TAGS_PER_MEETING:
                    raise ValueError(f"At most {MAX_TAGS_PER_MEETING} tags per meeting.")
            self._drop_orphan_tags(conn)
            return TagUpdate([(i, self._tags(conn, i)) for i in ids], created, not_found)
```

(`MAX_DESCRIPTION_CHARS` and `MAX_NEW_TAGS_PER_SAVE` are imported now and used in Tasks 4–5. If the linter flags them before then, add the import in the task that uses them instead.)

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_meeting_tags.py tests/test_meeting_library.py tests/test_meeting_library_mcp.py tests/test_meetings_bridge.py tests/test_mcp_tools.py -v`
Expected: all pass. The existing `test_save_notes_partial_updates_and_tag_normalisation` still gives `["DMT", "VFA"]` and removes the orphan `DMT`.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/meeting_library.py tests/test_meeting_tags.py
git commit -m "Tags: resolve by slug/alias, tag_meetings with provenance and suppressions"
```

---

### Task 4: `save_notes` touches only Claude's tags

**Files:**
- Modify: `speakeasy/meeting_library.py`:
  - `Notes` dataclass (line 149);
  - `save_notes` (the interim tag block from Task 3);
  - new `_set_claude_tags`.
- Test: `tests/test_meeting_tags.py`

**Interfaces:**
- Consumes: Task 3 helpers.
- Produces: `Notes.created_tags: list[str]` and `Notes.suppressed_tags: list[str]`. Both are defaulted fields, filled only on the `Notes` that `save_notes` returns. Task 6 reads them.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_meeting_tags.py`:

```python
def test_save_notes_reuses_existing_tag_for_any_spelling(lib):
    a, b = _meeting(lib, 0), _meeting(lib, 1)
    assert lib.save_notes(a, tags=["Project X", "Budget"]).created_tags == ["Project X", "Budget"]
    notes = lib.save_notes(b, tags=["project-x"])
    assert notes.created_tags == [] and notes.suppressed_tags == []
    assert lib.meeting_tags(b) == ["Project X"]
    assert lib.list_tags() == [("Project X", 2), ("Budget", 1)]


def test_resaving_claude_tags_never_removes_user_tags(lib):
    a = _meeting(lib)
    lib.tag_meetings([a], add=["Mine"])
    lib.save_notes(a, tags=["Theirs"])
    assert lib.meeting_tag_details(a) == [("Mine", "user"), ("Theirs", "claude")]
    lib.save_notes(a, tags=["Other"])
    assert lib.meeting_tag_details(a) == [("Mine", "user"), ("Other", "claude")]
    lib.save_notes(a, tags=[])
    assert lib.meeting_tag_details(a) == [("Mine", "user")]


def test_claude_naming_a_user_tag_keeps_it_user(lib):
    a = _meeting(lib)
    lib.tag_meetings([a], add=["Project X"])
    notes = lib.save_notes(a, tags=["project x"])
    assert notes.created_tags == []
    assert lib.meeting_tag_details(a) == [("Project X", "user")]
    lib.save_notes(a, tags=[])
    assert lib.meeting_tag_details(a) == [("Project X", "user")]


def test_save_notes_skips_suppressed_tags_per_meeting(lib):
    a, b = _meeting(lib, 0), _meeting(lib, 1)
    lib.save_notes(a, tags=["Ops"])
    lib.tag_meetings([a], remove=["Ops"])
    notes = lib.save_notes(a, tags=["OPS", "Budget"])
    assert notes.suppressed_tags == ["Ops"]
    assert lib.meeting_tags(a) == ["Budget"]
    assert lib.save_notes(b, tags=["Ops"]).suppressed_tags == []
    assert lib.meeting_tags(b) == ["Ops"]


def test_new_tag_cap_allows_three_and_rejects_four_atomically(lib):
    a, b = _meeting(lib, 0), _meeting(lib, 1)
    assert lib.save_notes(a, tags=["a1", "a2", "a3"]).created_tags == ["a1", "a2", "a3"]
    with pytest.raises(ValueError, match=r"Would create 4 new tags \(n1, n2, n3, n4\); at most 3 per call"):
        lib.save_notes(b, summary="S", tags=["a1", "n1", "n2", "n3", "n4"])
    assert lib.get_meeting(b).notes is None          # the summary was not saved either
    assert lib.meeting_tags(b) == []
    assert {n for n, _ in lib.list_tags()} == {"a1", "a2", "a3"}


def test_twenty_tag_limit_counts_user_and_claude_tags(lib):
    a = _meeting(lib)
    lib.tag_meetings([a], add=[f"u{i}" for i in range(18)])
    with pytest.raises(ValueError, match="At most 20 tags per meeting"):
        lib.save_notes(a, tags=["n1", "n2", "n3"])
    assert len(lib.meeting_tags(a)) == 18
    lib.save_notes(a, tags=["n1", "n2"])
    assert len(lib.meeting_tags(a)) == 20
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_meeting_tags.py -v`
Expected: the new tests FAIL (`Notes` has no `created_tags`, and user tags are wiped).

- [ ] **Step 3: Implement**

Add the fields to `Notes`:

```python
@dataclass
class Notes:
    summary: str
    action_items: list[str]
    updated_at: str
    updated_by: str
    # Filled only on the Notes that save_notes returns: what its tags did.
    created_tags: list[str] = field(default_factory=list)
    suppressed_tags: list[str] = field(default_factory=list)
```

In `save_notes`, call `_set_claude_tags` straight after `self._touch(conn, meeting_id)`, before the notes upsert. Replace the interim tag block and the final `return`:

```python
        with self._transaction() as conn:
            self._touch(conn, meeting_id)
            created, suppressed = ([], []) if tags is None else \
                self._set_claude_tags(conn, meeting_id, tags)
            current = ...                                   # unchanged upsert
            ...
            notes = self._notes(conn, meeting_id)
            notes.created_tags, notes.suppressed_tags = created, suppressed
            return notes
```

Add:

```python
    def _set_claude_tags(self, conn, meeting_id, names) -> tuple[list[str], list[str]]:
        """Make `names` this meeting's Claude-suggested tags. User tags are
        never removed (and a requested user tag stays 'user'); tags the
        user removed here are skipped; at most MAX_NEW_TAGS_PER_SAVE tags
        are created. Raises before any write if the cap or the per-meeting
        limit would be broken; the caller's transaction rolls back.
        Returns (created names, suppressed names)."""
        found, new = [], []
        for name in names:
            tag_id = self._find_tag(conn, name)
            if tag_id is None:
                new.append(name)
            elif tag_id not in found:
                found.append(tag_id)
        if len(new) > MAX_NEW_TAGS_PER_SAVE:
            raise ValueError(
                f"Would create {len(new)} new tags ({', '.join(new)}); at most "
                f"{MAX_NEW_TAGS_PER_SAVE} per call. Reuse tags from list_tags or drop some.")
        blocked = {r[0] for r in conn.execute(
            "SELECT tag_id FROM tag_suppressions WHERE meeting_id = ?", (meeting_id,))}
        suppressed = [self._tag_name(conn, t) for t in found if t in blocked]
        wanted = [t for t in found if t not in blocked]
        wanted += [self._create_tag(conn, name) for name in new]
        keep = f" AND tag_id NOT IN ({','.join('?' * len(wanted))})" if wanted else ""
        conn.execute(
            f"DELETE FROM meeting_tags WHERE meeting_id = ? AND source = 'claude'{keep}",
            (meeting_id, *wanted))
        now = _now_iso()
        for tag_id in wanted:
            # OR IGNORE: an existing link keeps its source and added_at.
            conn.execute(
                "INSERT OR IGNORE INTO meeting_tags (meeting_id, tag_id, source, added_at)"
                " VALUES (?, ?, 'claude', ?)", (meeting_id, tag_id, now))
        if self._tag_count(conn, meeting_id) > MAX_TAGS_PER_MEETING:
            raise ValueError(f"At most {MAX_TAGS_PER_MEETING} tags per meeting.")
        self._drop_orphan_tags(conn)
        return new, suppressed
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_meeting_tags.py tests/test_meeting_library.py tests/test_meeting_library_mcp.py tests/test_mcp_tools.py -v`
Expected: all pass. If an existing test compares a returned `Notes` with `==` against a `Notes(...)` built with four fields, update that test to compare the four fields.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/meeting_library.py tests/test_meeting_tags.py
git commit -m "Tags: save_notes replaces only Claude's tags, skips suppressed, caps new tags"
```

---

### Task 5: Tag admin — rename, merge, delete, describe

**Files:**
- Modify: `speakeasy/meeting_library.py` (new methods in the tags section).
- Test: `tests/test_meeting_tags.py`

**Interfaces:**
- Consumes: Task 3 helpers.
- Produces:
  - `rename_tag(tag, name) -> TagInfo`
  - `merge_tags(tag, into) -> TagInfo` (the target)
  - `delete_tag(tag) -> str` (the deleted display name)
  - `describe_tag(tag, description) -> TagInfo`
  - All four raise `ValueError` with a message that tells the caller what to do next.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_meeting_tags.py`:

```python
def test_rename_keeps_old_name_resolving(lib):
    a = _meeting(lib)
    lib.tag_meetings([a], add=["Proj"])
    assert lib.rename_tag("proj", "Project Phoenix") == TagInfo("Project Phoenix", "", ["Proj"], 1)
    assert [m.meeting_id for m in lib.list_meetings(tag="Proj")] == [a]
    assert lib.save_notes(a, tags=["proj"]).created_tags == []
    assert lib.meeting_tags(a) == ["Project Phoenix"]


def test_rename_case_only_changes_display_without_alias(lib):
    a = _meeting(lib)
    lib.tag_meetings([a], add=["budget"])
    assert lib.rename_tag("budget", "Budget") == TagInfo("Budget", "", [], 1)


def test_rename_back_to_an_own_alias(lib):
    a = _meeting(lib)
    lib.tag_meetings([a], add=["Proj"])
    lib.rename_tag("Proj", "Phoenix")
    assert lib.rename_tag("Phoenix", "Proj") == TagInfo("Proj", "", ["Phoenix"], 1)


def test_rename_onto_another_tag_or_its_alias_says_merge(lib):
    a = _meeting(lib)
    lib.tag_meetings([a], add=["Ops", "Budget"])
    with pytest.raises(ValueError, match="already exists; use merge"):
        lib.rename_tag("Ops", "budget")
    lib.rename_tag("Budget", "Finance")          # "Budget" is now an alias of Finance
    with pytest.raises(ValueError, match="already exists; use merge"):
        lib.rename_tag("Ops", "BUDGET")
    with pytest.raises(ValueError, match="No tag named"):
        lib.rename_tag("missing", "Whatever")


def test_merge_moves_links_user_wins_and_leaves_alias(lib):
    a, b, c = _meeting(lib, 0), _meeting(lib, 1), _meeting(lib, 2)
    lib.tag_meetings([a], add=["proj-x"])         # user link on the source
    lib.save_notes(a, tags=["Project X"])         # claude link on the target
    lib.save_notes(b, tags=["proj-x"])            # source only, from Claude
    lib.tag_meetings([c], add=["Project X"])      # target only, from the user
    assert lib.merge_tags("proj-x", "project x") == TagInfo("Project X", "", ["proj-x"], 3)
    assert lib.meeting_tag_details(a) == [("Project X", "user")]
    assert lib.meeting_tag_details(b) == [("Project X", "claude")]
    assert lib.meeting_tag_details(c) == [("Project X", "user")]
    assert len(lib.list_meetings(tag="projx")) == 3


def test_merge_link_beats_suppression_and_moves_suppressions(lib, library_path):
    a, b = _meeting(lib, 0), _meeting(lib, 1)
    lib.tag_meetings([a], add=["Target", "Source"])
    lib.tag_meetings([a], remove=["Source"])      # a: Target linked, Source suppressed
    lib.tag_meetings([b], add=["Source"])
    lib.tag_meetings([b], remove=["Source"])      # b: Source suppressed only
    lib.merge_tags("Source", "Target")
    assert lib.meeting_tags(a) == ["Target"]
    assert _sql(library_path, "SELECT meeting_id FROM tag_suppressions") == [(b,)]
    assert lib.save_notes(b, tags=["Target"]).suppressed_tags == ["Target"]
    assert lib.save_notes(a, tags=["Target"]).suppressed_tags == []


def test_merge_repoints_aliases_and_rejects_self_and_missing(lib):
    a = _meeting(lib)
    lib.tag_meetings([a], add=["A", "C"])
    lib.rename_tag("A", "B")                      # alias A -> B
    assert lib.merge_tags("B", "C") == TagInfo("C", "", ["A", "B"], 1)
    with pytest.raises(ValueError, match="into itself"):
        lib.merge_tags("C", "a")
    with pytest.raises(ValueError, match="No tag named"):
        lib.merge_tags("missing", "C")


def test_delete_tag_removes_links_aliases_and_suppressions(lib, library_path):
    a, b = _meeting(lib, 0), _meeting(lib, 1)
    lib.tag_meetings([a, b], add=["Ops"])
    lib.tag_meetings([b], remove=["Ops"])
    lib.rename_tag("Ops", "Operations")
    assert lib.delete_tag("ops") == "Operations"
    for table in ("tags", "meeting_tags", "tag_aliases", "tag_suppressions"):
        assert _sql(library_path, f"SELECT COUNT(*) FROM {table}") == [(0,)], table
    assert lib.save_notes(b, tags=["Ops"]).created_tags == ["Ops"]


def test_describe_tag(lib):
    a = _meeting(lib)
    lib.tag_meetings([a], add=["Ops"])
    assert lib.describe_tag("ops", "  Running   the shop ").description == "Running the shop"
    with pytest.raises(ValueError, match="at most 200 characters"):
        lib.describe_tag("Ops", "x" * 201)
    assert lib.describe_tag("Ops", "x" * 200).description == "x" * 200
    assert lib.describe_tag("Ops", "").description == ""
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_meeting_tags.py -v`
Expected: the new tests FAIL with `AttributeError` (`rename_tag` etc. do not exist).

- [ ] **Step 3: Implement.** Add to the tags section of `MeetingLibrary`:

```python
    def rename_tag(self, tag: str, name: str) -> TagInfo:
        """Change a tag's display name everywhere; the old spelling stays
        as an alias so it keeps resolving. Refuses a name another tag (or
        another tag's alias) already has: that is a merge."""
        new = clean_tag_name(name)
        slug = tag_slug(new)
        with self._transaction() as conn:
            tag_id = self._require_tag(conn, tag)
            owner = self._find_tag(conn, new)
            if owner is not None and owner != tag_id:
                raise ValueError(f"A tag named {new!r} already exists; use merge.")
            old_name, old_slug = conn.execute(
                "SELECT name, slug FROM tags WHERE id = ?", (tag_id,)).fetchone()
            # Renaming back to one of its own aliases: the alias becomes the name.
            conn.execute("DELETE FROM tag_aliases WHERE slug = ?", (slug,))
            conn.execute("UPDATE tags SET name = ?, slug = ? WHERE id = ?", (new, slug, tag_id))
            if slug != old_slug:
                conn.execute("INSERT INTO tag_aliases (slug, name, tag_id) VALUES (?, ?, ?)",
                             (old_slug, old_name, tag_id))
            return self._tag_info(conn, tag_id)

    def merge_tags(self, tag: str, into: str) -> TagInfo:
        """Fold `tag` into `into`: links (a 'user' link wins), suppressions
        and aliases move; the merged name becomes an alias of the target."""
        with self._transaction() as conn:
            source, target = self._require_tag(conn, tag), self._require_tag(conn, into)
            if source == target:
                raise ValueError("A tag cannot be merged into itself.")
            conn.execute(
                "UPDATE meeting_tags SET source = 'user' WHERE tag_id = ? AND meeting_id IN"
                " (SELECT meeting_id FROM meeting_tags WHERE tag_id = ? AND source = 'user')",
                (target, source))
            conn.execute(
                "INSERT OR IGNORE INTO meeting_tags (meeting_id, tag_id, source, added_at)"
                " SELECT meeting_id, ?, source, added_at FROM meeting_tags WHERE tag_id = ?",
                (target, source))
            conn.execute(
                "INSERT OR IGNORE INTO tag_suppressions (meeting_id, tag_id, created_at)"
                " SELECT meeting_id, ?, created_at FROM tag_suppressions WHERE tag_id = ?",
                (target, source))
            # Where the merged tag is on a meeting, the link beats a suppression.
            conn.execute(
                "DELETE FROM tag_suppressions WHERE tag_id = ? AND meeting_id IN"
                " (SELECT meeting_id FROM meeting_tags WHERE tag_id = ?)", (target, target))
            conn.execute("UPDATE tag_aliases SET tag_id = ? WHERE tag_id = ?", (target, source))
            name, slug = conn.execute(
                "SELECT name, slug FROM tags WHERE id = ?", (source,)).fetchone()
            conn.execute("DELETE FROM tags WHERE id = ?", (source,))  # cascades its links
            conn.execute("INSERT INTO tag_aliases (slug, name, tag_id) VALUES (?, ?, ?)",
                         (slug, name, target))
            return self._tag_info(conn, target)

    def delete_tag(self, tag: str) -> str:
        """Remove a tag from every meeting, with its aliases and
        suppressions (all cascade). Returns the deleted display name."""
        with self._transaction() as conn:
            tag_id = self._require_tag(conn, tag)
            name = self._tag_name(conn, tag_id)
            conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
            return name

    def describe_tag(self, tag: str, description: str) -> TagInfo:
        text = " ".join(str(description).split())
        if len(text) > MAX_DESCRIPTION_CHARS:
            raise ValueError(f"Tag descriptions are at most {MAX_DESCRIPTION_CHARS} characters.")
        with self._transaction() as conn:
            tag_id = self._require_tag(conn, tag)
            conn.execute("UPDATE tags SET description = ? WHERE id = ?", (text, tag_id))
            return self._tag_info(conn, tag_id)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_meeting_tags.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/meeting_library.py tests/test_meeting_tags.py
git commit -m "Tags: rename, merge, delete and describe with aliases"
```

---

### Task 6: MCP tools, manifest, prompt and docs

**Files:**
- Modify: `speakeasy/mcp_tools.py`:
  - `get_meeting` (~line 214);
  - `list_tags` (line 265);
  - `save_notes` (lines 272–285);
  - new `tag_meetings` and `manage_tags`;
  - the `specs` list (lines 287–333).
- Modify: `packaging/mcpb/manifest.json` (the `tools` array).
- Modify: `frontend/src/meetings/MeetingDetail.tsx:592`.
- Modify: `README.md` (tool list, ~lines 364–374) and `AGENTS.md:74-76`.
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `tag_catalog`, `meeting_tag_details`, `tag_meetings`, `rename_tag`, `merge_tags`, `delete_tag`, `describe_tag`, `Notes.created_tags` and `Notes.suppressed_tags`.
- Produces these MCP tools, in this order: `list_meetings, get_meeting, search_meetings, get_transcript, get_calendar, list_tags, list_people, save_notes, tag_meetings, manage_tags`.

- [ ] **Step 1: Write the failing tests.** In `tests/test_mcp_tools.py`:

Update `test_tool_names_order_and_definitions`:

```python
    assert list(tools) == ["list_meetings", "get_meeting", "search_meetings",
                           "get_transcript", "get_calendar", "list_tags",
                           "list_people", "save_notes", "tag_meetings", "manage_tags"]
    writers = {"save_notes", "tag_meetings", "manage_tags"}
    for t in tools.values():
        ...
        assert d["annotations"]["readOnlyHint"] is (t.name not in writers)
```

In `test_save_notes_replaces_only_given_fields`, change the last assertion to:

```python
    assert tools["list_tags"].run({}) == {"tags": [
        {"name": "budget", "count": 1, "description": "", "aliases": []}]}
    assert first["created_tags"] == ["budget"] and first["suppressed"] == []
```

Append:

```python
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
    for args in [{"action": "explode", "tag": "x"}, {"action": "rename", "tag": "x"},
                 {"action": "merge", "tag": "x"}, {"action": "describe", "tag": "x"},
                 {"action": "delete"}, {"tag": "x"}]:
        with pytest.raises(ToolError):
            tools["manage_tags"].run(args)


def test_tag_descriptions_steer_reuse(tools):
    save = tools["save_notes"].description
    assert "call list_tags first" in save and "at most 3" in save
    assert "never removes tags the user added" in save
    assert "the user asked" in tools["tag_meetings"].description
    assert "alias" in tools["manage_tags"].description
    assert "aliases" in tools["list_tags"].description
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py tests/test_mcpb.py -v`
Expected: FAIL (the tool list differs, and there are no new keys).

- [ ] **Step 3: Implement** in `speakeasy/mcp_tools.py`

In `get_meeting`'s return dict, add after `"tags": m.tags,`:

```python
            "tags_detail": [{"name": n, "source": s}
                            for n, s in library.meeting_tag_details(m.meeting_id)],
```

Replace `list_tags` and extend `save_notes`'s return value:

```python
    def list_tags(args):
        return {"tags": [{"name": t.name, "count": t.count, "description": t.description,
                          "aliases": t.aliases} for t in library.tag_catalog()]}
```

```python
        return {"id": meeting_id, "summary": notes.summary,
                "action_items": notes.action_items,
                "tags": library.meeting_tags(meeting_id),
                "created_tags": notes.created_tags, "suppressed": notes.suppressed_tags,
                "updated_at": notes.updated_at, "updated_by": notes.updated_by}
```

Add after `save_notes`:

```python
    def tag_meetings(args):
        ids = _str_list(args, "ids")
        if not ids:
            raise ToolError("ids must list at least one meeting id.")
        add, remove = _str_list(args, "add") or [], _str_list(args, "remove") or []
        if not add and not remove:
            raise ToolError("Give tags to add and/or remove.")
        update = library.tag_meetings(ids, add=add, remove=remove)
        return {"meetings": [{"id": i, "tags": t} for i, t in update.meetings],
                "created_tags": update.created_tags, "not_found": update.not_found}

    def manage_tags(args):
        action = _text(args, "action", required=True, max_len=20)
        tag = _text(args, "tag", required=True)
        if action == "rename":
            info = library.rename_tag(tag, _text(args, "name", required=True))
        elif action == "merge":
            info = library.merge_tags(tag, _text(args, "into", required=True))
        elif action == "describe":
            description = args.get("description")
            if not isinstance(description, str):
                raise ToolError("describe needs description (text; empty clears it).")
            info = library.describe_tag(tag, description)
        elif action == "delete":
            return {"deleted": library.delete_tag(tag)}
        else:
            raise ToolError("action must be rename, merge, delete or describe.")
        return {"name": info.name, "description": info.description,
                "aliases": info.aliases, "count": info.count}
```

Note: `{"ids": [5], ...}` is rejected by `_str_list` ("must be a list of text items").

In `specs`, update the `list_tags` and `save_notes` descriptions, and append the two new tools after `save_notes`:

```python
        ("list_tags",
         "All meeting tags with how many meetings use each, plus each tag's "
         "description and aliases (other spellings that resolve to it).",
         {}, [], list_tags, True),
        ...
        ("save_notes",
         "Save a meeting's summary, action items and/or tags. Only the fields you "
         "give are replaced. Tags are your own suggestions: call list_tags first and "
         "reuse existing tags (any spelling of a tag or its aliases matches it); "
         "create a new tag only for a genuinely new topic, at most 3 per call. "
         "Replacing tags never removes tags the user added, and tags the user "
         "removed from this meeting are skipped.",
         {...unchanged...}, ["id"], save_notes, False),
        ("tag_meetings",
         "Add or remove tags on one or more meetings because the user asked you to. "
         "These tags are kept when notes are saved again, and tags removed here "
         "won't be re-added by save_notes.",
         {"ids": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                  "maxItems": 100,
                  "description": "Meeting ids from list_meetings or search_meetings."},
          "add": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
          "remove": {"type": "array", "items": {"type": "string"}, "maxItems": 20}},
         ["ids"], tag_meetings, False),
        ("manage_tags",
         "Rename, merge, delete or describe a tag across all meetings, when the user "
         "asks to tidy their tags. Renaming or merging keeps the old name as an alias, "
         "so it still finds the tag.",
         {"action": {"type": "string", "enum": ["rename", "merge", "delete", "describe"]},
          "tag": {"type": "string", "description": "The tag to change (any spelling or alias)."},
          "name": {"type": "string", "description": "rename: the new name."},
          "into": {"type": "string", "description": "merge: the tag to merge into."},
          "description": {"type": "string", "maxLength": 200,
                          "description": "describe: what the tag is for; empty clears it."}},
         ["action", "tag"], manage_tags, False),
```

`packaging/mcpb/manifest.json`: in `tools`, set the `list_tags` and `save_notes` descriptions to the exact strings above, and append `{"name": "tag_meetings", "description": …}` and `{"name": "manage_tags", "description": …}` in that order. Each description is the Python string literal joined into one line.

`frontend/src/meetings/MeetingDetail.tsx:592`:

```ts
const prompt = `Summarise and tag my Speakeasy meeting "${detail.title}" (${detail.id}) and save the notes. Reuse my existing tags where they fit.`;
```

`README.md`:
- set the `list_tags` line to: "every tag with its meeting count, description and aliases."
- set the `save_notes` bullet to: "the summary, action items and/or Claude's suggested tags; replaces only the fields given, never removes tags you added, and records that Claude made the change. The Meetings window notices within about 10 seconds."
- add:
  - `tag_meetings`: "add or remove tags on meetings when you ask; your tags are kept, and ones you remove are not re-added."
  - `manage_tags`: "rename, merge, delete or describe a tag everywhere; old names stay as aliases."

`AGENTS.md:75-76`: replace "Only `save_notes` writes, only the fields given, with `updated_by` "claude"." with: "Writes are `save_notes` (only the fields given, `updated_by` "claude"; its tags are source `claude` and never displace `user` tags or re-add suppressed ones), `tag_meetings` (source `user`) and `manage_tags`. Every tag name resolves through `tag_names.tag_slug` then `tag_aliases`; never insert into `tags` directly."

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py tests/test_mcpb.py tests/test_mcp_server.py -v`
Expected: all pass. If `test_mcp_server.py` asserts a tool count or list, update it to the 10 tools.

Run: `npm --prefix frontend run build`
Expected: `tsc` and the vite build succeed.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/mcp_tools.py packaging/mcpb/manifest.json frontend/src/meetings/MeetingDetail.tsx README.md AGENTS.md tests/test_mcp_tools.py tests/test_mcp_server.py
git commit -m "Tags: tag_meetings and manage_tags MCP tools; steer save_notes to reuse tags"
```

---

### Task 7: Verify, install and look at it

**Files:** none changed, unless a check fails.

- [ ] **Step 1: Run the full suite.** Run `.venv/bin/python -m pytest -q` with Bash `timeout: 600000`.
Expected: all pass (more than 388 tests now). Report the count.

- [ ] **Step 2: Whole-branch review** (opus), verifying by mutation. For each mutation, break the code, run `python -B -m pytest tests/test_meeting_tags.py tests/test_meeting_store.py` after clearing `__pycache__`, and confirm a test fails:
  - drop the `tag_suppressions` clause from `_drop_orphan_tags`;
  - make `_set_claude_tags` delete all sources;
  - change the cap `>` to `>=`;
  - drop the alias lookup in `_find_tag`;
  - drop the link-beats-suppression `DELETE` in `merge_tags`;
  - make `_backfill_tag_slugs` keep the highest id on a tie.

- [ ] **Step 3: Back up the real library before the first v3 open.** Run:

```bash
cp ~/Library/Application\ Support/Speakeasy/library.sqlite ~/Library/Application\ Support/Speakeasy/library.v2-backup.sqlite
```

Confirm the real path with `settings.library_path()` first. Copy the `-wal` file too if present, or quit the app first so the WAL is checkpointed.

- [ ] **Step 4: Install and launch.** Run `scripts/build_app.sh --install`. This stops Claude Desktop's Speakeasy MCP connector; ask the user to toggle it back on. Launch the app and open the Meetings window. Check that the sidebar still lists the `test` tag with count 1, and that clicking it filters to its meeting.

- [ ] **Step 5: Live MCP check** (read-only). Call `list_tags` through the Speakeasy MCP connector. Expect `[{"name": "test", "count": 1, "description": "", "aliases": []}]`, which proves the installed server runs schema v3. Ask the user before calling any write tool on real data.

- [ ] **Step 6: Record the outcome.** Note in this plan what was tested versus launched and looked at, then commit.

---

## Progress

- [ ] Task 1 · [ ] Task 2 · [ ] Task 3 · [ ] Task 4 · [ ] Task 5 · [ ] Task 6 · [ ] Task 7

## Phase 2 (not this plan)

App tag editor and tag manager UI (`source = 'user'` writes from the bridge), automatic rule tagging (calendar series, attendees, title words), multi-tag filters, sidebar tag search.
