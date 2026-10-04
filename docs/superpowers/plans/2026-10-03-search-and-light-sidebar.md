# Meeting search by title and date; light floating sidebar: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Meeting search, in the app and in Claude's `search_meetings`, also matches meeting titles and a date phrase. The light-mode Meetings sidebar becomes a floating frosted panel ("option C").

**Architecture:** A new pure module `speakeasy/search_dates.py` pulls one date phrase out of the query. `MeetingLibrary.search` turns that date into a local-day bound and puts meeting-level `kind="meeting"` title hits ahead of the existing FTS content hits. The MCP tool and the WKWebView bridge pass the new kind through. The sidebar change is CSS: new tokens whose dark values equal today's look, a Meetings-only canvas wrapper, and the inset rounded panel.

**Tech Stack:** Python 3 + SQLite FTS5 (pytest), React + TypeScript + CSS modules (Vite), WKWebView host.

**Spec:** [docs/superpowers/specs/2026-10-03-search-and-light-sidebar-design.md](../specs/2026-10-03-search-and-light-sidebar-design.md)

## Models

- **Coding:** every implementer subagent runs on **Sonnet 5.5** (`model: "sonnet"`, `claude-sonnet-5-5`). Give it the task text with the exact values from this plan.
- **Evals and review:** every reviewer subagent runs on **Opus 5.5** (`model: "opus"`, `claude-opus-5-5`). That covers per-task spec and quality reviews, mutation checks and the final whole-branch review. Reviewers verify by **mutation**: break the code, confirm the suite notices, revert.
- The controller (the session running the plan) does the Task 6 on-screen check itself.

## Global Constraints

- Fully offline: no new dependencies (Python or npm).
- Tests never touch the real App Support folder: use the `library_path` fixture. Never set HOME by hand.
- **Run the full Python suite with a Bash timeout of 600000 ms:** `.venv/bin/python -m pytest -q`. It has about 390 tests and must never be silently backgrounded.
- Mutation checks: `python -B` and clear `__pycache__` first (see the memory notes).
- No literal colours in any `*.module.css`. `tests/test_frontend_tokens.py::test_css_modules_use_tokens_only` enforces this.
- Every token defined in the dark `:root` must be redefined in the light block (`test_light_mode_redefines_every_colour_token`).
- Dark mode must render exactly as today: every new token's dark value is the value the CSS used before.
- Contrast floors: ≥ 4.5:1 for `--text-hi`, `--text` and `--text-mid`; ≥ 3:1 for `--text-lo`.
- MCP tool descriptions are mirrored verbatim in `packaging/mcpb/manifest.json` (`tests/test_mcpb.py`).
- `--mcp` stays import-light: `search_dates.py` imports only `re` and `datetime`.
- Branch first (worktree). Never implement on `master`.

## Review Focus

1. **A date-only query must not return "everything":** `"Oct 3"` with no meeting that day returns `[]`, not every meeting. Pinned in Task 2 (`test_date_only_with_no_meeting_that_day_is_empty`).
2. **Default titles contain dates** ("Meeting — Oct 3, 7:58 PM"). After the date is removed, `"Oct 3"` must not also be title-matched as the words "Oct" and "3". Only the date bound applies. Pinned in Task 2 (`test_date_only_lists_day_earliest_first`, which asserts every hit is `kind="meeting"` and nothing is duplicated).
3. **Leftover punctuation after removing the date** (`"Oct 3, standup"` leaves `", standup"`) must not demand a comma in the title. Pinned in Task 2 (`test_punctuation_left_by_date_is_ignored`).
4. **A query that contains a date and also `from_date`/`to_date` outside it** returns `[]`. It does not fall back to the explicit range. Pinned in Task 2 (`test_date_and_explicit_range_intersect`).
5. **Titles with regex or SQL-special characters** (`"C++ review"`, `"100% done"`, `"a_b"`) must match literally and highlight without raising. Pinned in Task 2 (`test_title_special_characters_match_literally`).

---

## File map

| File | Change |
|---|---|
| `speakeasy/search_dates.py` | **Create.** `parse_date_phrase(text, today) -> tuple[date | None, str]` |
| `tests/test_search_dates.py` | **Create.** Parser table tests |
| `speakeasy/meeting_library.py` | `mark_title()`, `_today()`, `_meeting_hits()`; `search()` restructured; `SearchHit.kind` comment |
| `tests/test_meeting_search.py` | Title and date search tests |
| `speakeasy/mcp_tools.py` | `search_meetings` description |
| `packaging/mcpb/manifest.json` | Mirrored description |
| `tests/test_mcp_tools.py` | Update the description test; add a `kind: "meeting"` test |
| `tests/test_meetings_bridge.py` | Bridge payload for a title hit |
| `frontend/src/mock/meetings.ts` | `SearchResult.kind` adds `'meeting'`; one mock title result |
| `frontend/src/meetings/SearchResults.tsx` | Render a title hit |
| `frontend/src/styles/tokens.css` | New sidebar/canvas tokens |
| `frontend/src/meetings/App.tsx`, `App.module.css` | `.window` canvas wrapper |
| `frontend/src/meetings/Sidebar.module.css` | Inset panel, chip, search fill |
| `frontend/src/meetings/MeetingList.module.css` | Selected meeting uses the chip |
| `tests/test_frontend_tokens.py` | Dark values pinned; light panel contrast |

---

### Task 1: Date phrase parser

**Files:**
- Create: `speakeasy/search_dates.py`
- Test: `tests/test_search_dates.py`

**Interfaces:**
- Produces: `parse_date_phrase(text: str, today: datetime.date) -> tuple[datetime.date | None, str]`. This returns the date of the **first** valid date phrase in `text` (or `None`) and the text with that phrase removed and whitespace collapsed (stripped). With no valid phrase, it returns `(None, text)` unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_search_dates.py
from datetime import date

import pytest

from speakeasy.search_dates import parse_date_phrase

TODAY = date(2026, 10, 3)


@pytest.mark.parametrize("text, day, rest", [
    ("Oct 3", date(2026, 10, 3), ""),
    ("oct 3", date(2026, 10, 3), ""),
    ("October 3", date(2026, 10, 3), ""),
    ("Oct. 3", date(2026, 10, 3), ""),
    ("oct 3rd", date(2026, 10, 3), ""),
    ("Sept 3", date(2026, 9, 3), ""),
    ("Sep 3", date(2026, 9, 3), ""),
    ("3 Oct", date(2026, 10, 3), ""),
    ("03 oct", date(2026, 10, 3), ""),
    ("3rd October", date(2026, 10, 3), ""),
    ("1st May", date(2026, 5, 1), ""),
    ("Oct 3 2025", date(2025, 10, 3), ""),
    ("Oct 3, 2025", date(2025, 10, 3), ""),
    ("3 Oct 2025", date(2025, 10, 3), ""),
    ("3rd October, 2025", date(2025, 10, 3), ""),
    ("2026-10-03", date(2026, 10, 3), ""),
    ("today", date(2026, 10, 3), ""),
    ("Yesterday", date(2026, 10, 2), ""),
    ("standup Oct 3", date(2026, 10, 3), "standup"),
    ("Oct 3 standup review", date(2026, 10, 3), "standup review"),
    ("budget  today  please", date(2026, 10, 3), "budget please"),
])
def test_recognised_phrases(text, day, rest):
    assert parse_date_phrase(text, TODAY) == (day, rest)


def test_no_year_means_most_recent_not_after_today():
    assert parse_date_phrase("Oct 4", TODAY)[0] == date(2025, 10, 4)
    assert parse_date_phrase("Dec 20", date(2026, 1, 5))[0] == date(2025, 12, 20)
    assert parse_date_phrase("Feb 29", TODAY)[0] == date(2024, 2, 29)


def test_first_phrase_wins_and_later_one_stays_as_words():
    assert parse_date_phrase("Oct 3 vs Oct 4", TODAY) == (date(2026, 10, 3), "vs Oct 4")
    assert parse_date_phrase("3 Oct 4", TODAY) == (date(2026, 10, 3), "4")


@pytest.mark.parametrize("text", [
    "May", "march madness", "Monday standup", "10/3", "3.10", "3", "2026",
    "Feb 30", "31 Sept", "2026-13-01", "2026-02-30", "octopus 3", "todays plan",
    "", "   ", "Oct", "Oct 32",
])
def test_not_dates(text):
    assert parse_date_phrase(text, TODAY) == (None, text)


def test_title_with_time_after_date():
    assert parse_date_phrase("Meeting — Oct 3, 7:58 PM", TODAY) == (
        date(2026, 10, 3), "Meeting — , 7:58 PM")
```

The last test pins the year group: `", 7"` must not be read as a year.

- [ ] **Step 2: Run the tests to check they fail**

Run: `.venv/bin/python -m pytest tests/test_search_dates.py -q`
Expected: collection error, `ModuleNotFoundError: speakeasy.search_dates`.

- [ ] **Step 3: Implement**

```python
# speakeasy/search_dates.py
"""Find one date phrase in a search query ("Oct 3", "3rd October 2026",
"2026-10-03", "today", "yesterday") so search can narrow to that local day.
Pure: the caller passes today. Bare months, weekdays and numeric slash
dates are deliberately not dates — they collide with real words and
titles ("May", "Monday standup") or are ambiguous (10/3)."""

import re
from datetime import date, timedelta

_MONTH_NUMBERS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sept": 9, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
# Longest first, so "september" wins over "sep" and "sept" over "sep".
_MONTH = "(?P<month>" + "|".join(sorted(_MONTH_NUMBERS, key=len, reverse=True)) + r")\.?"
_DAY = r"(?P<day>\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?:,?\s+(?P<year>(?:19|2\d)\d\d)(?!\w))?"
_PATTERNS = [
    re.compile(rf"(?<!\w){_MONTH}\s+{_DAY}(?!\w){_YEAR}", re.I),
    re.compile(rf"(?<!\w){_DAY}\s+{_MONTH}(?!\w){_YEAR}", re.I),
    re.compile(r"(?<!\w)(?P<iso>(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2}))(?!\w)"),
    re.compile(r"(?<!\w)(?P<rel>today|yesterday)(?!\w)", re.I),
]


def _most_recent(month: int, day: int, today: date) -> date | None:
    # Eight years back always reaches a leap year for 29 February.
    for year in range(today.year, today.year - 9, -1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate <= today:
            return candidate
    return None


def _resolve(m: re.Match, today: date) -> date | None:
    groups = m.groupdict()
    if groups.get("rel"):
        return today if groups["rel"].lower() == "today" else today - timedelta(days=1)
    try:
        if groups.get("iso"):
            return date(int(groups["y"]), int(groups["m"]), int(groups["d"]))
        month = _MONTH_NUMBERS[groups["month"].lower()]
        day = int(groups["day"])
        if groups.get("year"):
            return date(int(groups["year"]), month, day)
    except ValueError:
        return None
    return _most_recent(month, day, today)


def parse_date_phrase(text: str, today: date) -> tuple[date | None, str]:
    best = None  # (start, end, date)
    for pattern in _PATTERNS:
        for m in pattern.finditer(text):
            day = _resolve(m, today)
            if day is not None:
                if best is None or m.start() < best[0]:
                    best = (m.start(), m.end(), day)
                break
    if best is None:
        return None, text
    start, end, day = best
    rest = " ".join((text[:start] + " " + text[end:]).split())
    return day, rest
```

- [ ] **Step 4: Run the tests to check they pass**

Run: `.venv/bin/python -m pytest tests/test_search_dates.py -q`
Expected: all pass. If `"3 Oct 4"` fails, check that the day-month pattern's match at index 0 beats the month-day match `"Oct 4"` at index 2: the earliest start wins.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/search_dates.py tests/test_search_dates.py
git commit -m "Search: parse one date phrase from a query"
```

---

### Task 2: Title and date matching in `MeetingLibrary.search`

**Files:**
- Modify: `speakeasy/meeting_library.py` (imports; `SearchHit.kind` comment at ~291; `search` at ~1186; new helpers next to `fts_query` at ~39)
- Test: `tests/test_meeting_search.py`

**Interfaces:**
- Consumes: `parse_date_phrase` (Task 1).
- Produces:
  - `SearchHit.kind` may be `"meeting"`, with `speaker`/`start_seconds`/`end_seconds`/`segment_index` set to `None`, `also_speakers=[]` and `score=0.0`, and `snippet` the title with `HIT_OPEN`/`HIT_CLOSE` around matched words.
  - `mark_title(title: str, words: list[str]) -> str`.
  - `_today() -> date`, a module-level function that tests monkeypatch.
  - `search()` keeps its signature.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_meeting_search.py`)

```python
from datetime import date

from speakeasy import meeting_library
from speakeasy.meeting_library import mark_title


def _titled(lib, day, title, text="nothing relevant", hour=13):
    return lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0, 5, text)], duration_seconds=600, title=title,
        started_at=datetime(2026, 9, day, hour, 0, tzinfo=EDT)))


@pytest.fixture
def sept_25(monkeypatch):
    monkeypatch.setattr(meeting_library, "_today", lambda: date(2026, 9, 25))


def test_mark_title_highlights_case_insensitively_and_merges_overlaps():
    o, c = HIT_OPEN, HIT_CLOSE
    assert mark_title("Test Meeting", ["meet"]) == f"Test {o}Meet{c}ing"
    assert mark_title("Stand-up", ["stand", "and-up"]) == f"{o}Stand-up{c}"
    assert mark_title("C++ review", ["c++"]) == f"{o}C++{c} review"
    assert mark_title("Plain", []) == "Plain"


def test_title_hits_come_first_newest_first(library_path):
    lib = MeetingLibrary()
    old = _titled(lib, 20, "Test Meeting")
    new = _titled(lib, 24, "Another test meeting")
    body = _titled(lib, 22, "Unrelated", text="this meeting ran long")
    hits = lib.search("meeting")
    assert [(h.meeting_id, h.kind) for h in hits[:2]] == [(new, "meeting"), (old, "meeting")]
    assert (body, "transcript") in [(h.meeting_id, h.kind) for h in hits[2:]]
    t = hits[0]
    assert t.speaker is None and t.start_seconds is None and t.segment_index is None
    assert t.also_speakers == [] and t.score == 0.0
    assert t.snippet == f"Another test {HIT_OPEN}meeting{HIT_CLOSE}"


def test_every_title_word_must_appear(library_path):
    lib = MeetingLibrary()
    both = _titled(lib, 24, "Budget review Q4")
    _titled(lib, 23, "Budget sync")
    assert [h.meeting_id for h in lib.search("review budget") if h.kind == "meeting"] == [both]


def test_meeting_can_be_title_and_content_hit(library_path):
    lib = MeetingLibrary()
    mid = _titled(lib, 24, "Cognos review", text="cognos reporting is massive")
    assert [(h.meeting_id, h.kind) for h in lib.search("cognos")] == [
        (mid, "meeting"), (mid, "transcript")]


def test_date_only_lists_day_earliest_first(library_path, sept_25):
    lib = MeetingLibrary()
    late = _titled(lib, 24, "Retro", hour=16)
    early = _titled(lib, 24, "Stand-up", hour=9)
    _titled(lib, 23, "Other day")
    hits = lib.search("Sep 24")
    assert [(h.meeting_id, h.kind) for h in hits] == [(early, "meeting"), (late, "meeting")]
    assert hits[0].snippet == "Stand-up"


def test_date_only_with_no_meeting_that_day_is_empty(library_path, sept_25):
    lib = MeetingLibrary()
    _titled(lib, 24, "Retro")
    assert lib.search("Sep 21") == []


def test_default_title_found_by_word_and_by_date(library_path, sept_25):
    lib = MeetingLibrary()
    mid = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0, 5, "hello")], duration_seconds=600,
        started_at=datetime(2026, 9, 24, 13, 58, tzinfo=EDT)))
    assert lib.search("meeting")[0].meeting_id == mid
    by_date = lib.search("Sep 24")
    assert [(h.meeting_id, h.kind) for h in by_date] == [(mid, "meeting")]


def test_date_narrows_words(library_path, sept_25):
    lib = MeetingLibrary()
    on_day = _titled(lib, 24, "Standup", text="cognos")
    _titled(lib, 23, "Standup", text="cognos")
    assert {h.meeting_id for h in lib.search("standup Sep 24")} == {on_day}
    assert {h.meeting_id for h in lib.search("cognos 24 September")} == {on_day}
    assert lib.search("standup Sep 22") == []


def test_punctuation_left_by_date_is_ignored(library_path, sept_25):
    lib = MeetingLibrary()
    mid = _titled(lib, 24, "Standup")
    assert [h.meeting_id for h in lib.search("Sep 24, standup")] == [mid]


def test_date_and_explicit_range_intersect(library_path, sept_25):
    lib = MeetingLibrary()
    mid = _titled(lib, 24, "Standup")
    assert [h.meeting_id for h in lib.search("Sep 24", from_date="2026-09-20")] == [mid]
    assert lib.search("Sep 24", from_date="2026-09-25") == []
    assert lib.search("Sep 24", to_date="2026-09-23") == []


def test_today_uses_the_local_clock(library_path, monkeypatch):
    lib = MeetingLibrary()
    mid = _titled(lib, 24, "Standup")
    monkeypatch.setattr(meeting_library, "_today", lambda: date(2026, 9, 24))
    assert [h.meeting_id for h in lib.search("today")] == [mid]
    monkeypatch.setattr(meeting_library, "_today", lambda: date(2026, 9, 25))
    assert [h.meeting_id for h in lib.search("yesterday")] == [mid]
    assert lib.search("today") == []


def test_title_special_characters_match_literally(library_path):
    lib = MeetingLibrary()
    a = _titled(lib, 24, "C++ review")
    b = _titled(lib, 23, "100% done")
    c = _titled(lib, 22, "a_b sync")
    _titled(lib, 21, "axb sync")
    assert [h.meeting_id for h in lib.search("c++") if h.kind == "meeting"] == [a]
    assert [h.meeting_id for h in lib.search("100%") if h.kind == "meeting"] == [b]
    assert [h.meeting_id for h in lib.search("a_b") if h.kind == "meeting"] == [c]


def test_title_hits_respect_filters_and_limit(library_path):
    lib = MeetingLibrary()
    ids = [_titled(lib, d, "Weekly sync") for d in (20, 21, 22, 23)]
    lib.save_notes(ids[0], tags=["DMT"])
    assert [h.meeting_id for h in lib.search("weekly", tag="dmt")] == [ids[0]]
    assert len(lib.search("weekly", limit=2)) == 2
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `.venv/bin/python -m pytest tests/test_meeting_search.py -q`
Expected: `ImportError: cannot import name 'mark_title'`.

- [ ] **Step 3: Implement**

At the top of `speakeasy/meeting_library.py`, add `date` to the existing `datetime` import and import the parser:

```python
from speakeasy.search_dates import parse_date_phrase
```

Below `fts_query`, add:

```python
def _today() -> date:
    """The local calendar day a date phrase like "Oct 3" is resolved against
    (a function so tests can pin it)."""
    return date.today()


def mark_title(title: str, words: list[str]) -> str:
    """Wrap every case-insensitive occurrence of each word in HIT_OPEN/
    HIT_CLOSE, merging overlaps, for a title hit's snippet."""
    spans = sorted(m.span() for w in words if w
                   for m in re.finditer(re.escape(w), title, re.I))
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    out, pos = [], 0
    for start, end in merged:
        out += [title[pos:start], HIT_OPEN, title[start:end], HIT_CLOSE]
        pos = end
    return "".join(out) + title[pos:]
```

Change the `SearchHit.kind` comment to `# "meeting", "transcript", "notes" or "user_notes"`.

Replace `search` with:

```python
    def search(self, query: str, *, from_date=None, to_date=None, tag=None,
               person=None, limit=10) -> list[SearchHit]:
        limit = max(1, min(int(limit), 50))
        # A falsy query (None, "") must short-circuit before fts_query ever
        # sees it: str(None) is the literal text "None", which would
        # otherwise become a real FTS match for any segment containing that
        # word.
        if not query:
            return []
        # One date phrase narrows to that local day, ANDed with any explicit
        # range; the words left over search titles and contents within it.
        # Leftover punctuation (", " from "Oct 3, standup") is not a word.
        day, rest = parse_date_phrase(str(query), _today())
        words = [w for w in rest.split() if re.search(r"\w", w)]
        if day is not None:
            iso = day.isoformat()
            from_date = max(from_date, iso) if from_date else iso
            to_date = min(to_date, iso) if to_date else iso
        where, params = meeting_filters(from_date, to_date, tag, person)
        with self._transaction() as conn:
            if not words:
                if day is None:
                    return []
                return self._meeting_hits(conn, where, params, [], "ASC", limit)
            title_where, title_params = meeting_filters(
                from_date, to_date, tag, person, title=" ".join(words))
            titled = self._meeting_hits(conn, title_where, title_params, words, "DESC", limit)
            hits = []
            for any_term in (False, True):
                match = fts_query(" ".join(words), any_term=any_term)
                if match is None:
                    break
                hits = self._search(conn, match, where, params, limit * 4)
                if hits:
                    break
        # bm25 scores from the transcript and notes FTS tables are not comparable
        # (different table sizes and document lengths), so order by rank within
        # each kind and interleave, transcript first on ties.
        rank = {}
        for kind in ("transcript", "notes", "user_notes"):
            of_kind = sorted((h for h in hits if h.kind == kind), key=lambda h: h.score)
            rank.update((id(h), i) for i, h in enumerate(of_kind))
        hits.sort(key=lambda h: (rank[id(h)], {"transcript": 0, "notes": 1, "user_notes": 2}[h.kind]))
        return (titled + collapse_echoes(hits))[:limit]

    def _meeting_hits(self, conn, where, params, words, order, limit) -> list[SearchHit]:
        # Meeting-level hits: a title match (words highlighted) or, for a
        # date-only query, every meeting that day (earliest first).
        return [
            SearchHit(r["id"], r["title"], r["started_at"], r["tz_offset_minutes"],
                      "meeting", None, None, None, None, mark_title(r["title"], words),
                      [], 0.0)
            for r in conn.execute(
                "SELECT m.id, m.title, m.started_at, m.tz_offset_minutes FROM meetings m"
                f" WHERE {where} ORDER BY m.started_at {order}, m.id LIMIT ?",
                (*params, limit))
        ]
```

`order` is only ever the literal `"ASC"` or `"DESC"` from this file, never user input.

- [ ] **Step 4: Run the tests to check they pass**

Run: `.venv/bin/python -m pytest tests/test_meeting_search.py tests/test_search_dates.py -q`
Expected: all pass. Then run the full suite (timeout 600000 ms): `.venv/bin/python -m pytest -q`. Any failure elsewhere means an existing query now also title-matches a seeded title. Read the test and decide whether the new behaviour is correct. Update the assertion only if the spec says the new result is correct, and list every such change in the report.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/meeting_library.py tests/test_meeting_search.py
git commit -m "Search matches meeting titles and narrows to a date phrase"
```

---

### Task 3: Claude (MCP) description and the bridge payload

**Files:**
- Modify: `speakeasy/mcp_tools.py:361-368`, `packaging/mcpb/manifest.json:28-29`
- Test: `tests/test_mcp_tools.py` (`test_title_lookup_is_described` at ~76; new test), `tests/test_meetings_bridge.py` (new test)

**Interfaces:**
- Consumes: `kind="meeting"` hits from Task 2.
- Produces: the MCP result `{"kind": "meeting", "at": None, "start_seconds": None, "speaker": None, "snippet": "<title with **bold** words>"}`. The bridge result `{"kind": "meeting", "seconds": None, "segmentIndex": None, "parts": [...]}`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_mcp_tools.py`, replace the last two lines of `test_title_lookup_is_described` with:

```python
    search = tools["search_meetings"].definition()
    assert "title" not in search["inputSchema"]["properties"]
    assert "Search meetings by title, date and content" in search["description"]
    assert "a date alone lists that day's meetings" in search["description"]
```

and add:

```python
def test_search_meetings_title_and_date_hits(lib, tools):
    mid = _seed(lib, title="Budget review")
    hits = tools["search_meetings"].run({"query": "review"})["results"]
    assert hits[0] == {
        "meeting_id": mid, "title": "Budget review", "meeting_start": hits[0]["meeting_start"],
        "kind": "meeting", "speaker": None, "also_speakers": [], "at": None,
        "start_seconds": None, "snippet": "Budget **review**"}
    day = tools["search_meetings"].run({"query": "2026-09-24"})["results"]
    assert [(h["meeting_id"], h["kind"]) for h in day] == [(mid, "meeting")]
```

`_seed` saves 10:00 PDT on 24 September, which is 24 September in any US or European local zone, so the ISO-date query is deterministic.

In `tests/test_meetings_bridge.py`, add (using the file's existing `_setup`, whose meeting is titled "1:1 Alex"):

```python
def test_search_reports_title_hits(library_path):
    lib, mid, bridge, d = _setup(library_path)
    res = bridge.search_payload({"query": "alex"})
    assert res[0]["meetingId"] == mid and res[0]["kind"] == "meeting"
    assert res[0]["seconds"] is None and res[0]["segmentIndex"] is None
    assert res[0]["parts"] == [{"text": "1:1 ", "hit": False}, {"text": "Alex", "hit": True}]
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py tests/test_meetings_bridge.py -q`
Expected: `test_title_lookup_is_described` fails on the description. The two payload tests should already pass, which proves the pass-through. If `snippet_parts` emits an empty leading part, adjust the expected `parts` to what `snippet_parts` produces for a snippet that *ends* in a hit, and note it.

- [ ] **Step 3: Implement**

In `speakeasy/mcp_tools.py`, replace the `search_meetings` description string with:

```python
         "Search meetings by title, date and content: transcripts, summaries and the "
         "user's own notes (kind user_notes). A date in the query (e.g. 'Oct 3', "
         "'3 October 2026', '2026-10-03', 'today', 'yesterday') limits results to that "
         "day; a date alone lists that day's meetings. Title matches come first as kind "
         "meeting; content matches return ranked snippets (matches in **bold**) with the "
         "meeting id and time offset.",
```

In `packaging/mcpb/manifest.json`, set the `search_meetings` `"description"` to the identical single-line string:

`Search meetings by title, date and content: transcripts, summaries and the user's own notes (kind user_notes). A date in the query (e.g. 'Oct 3', '3 October 2026', '2026-10-03', 'today', 'yesterday') limits results to that day; a date alone lists that day's meetings. Title matches come first as kind meeting; content matches return ranked snippets (matches in **bold**) with the meeting id and time offset.`

- [ ] **Step 4: Run the tests to check they pass**

Run: `.venv/bin/python -m pytest tests/test_mcp_tools.py tests/test_meetings_bridge.py tests/test_mcpb.py tests/test_mcp_server.py -q`
Expected: all pass. `test_mcp_mode_imports_nothing_heavy` must still pass, because `search_dates` is import-light.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/mcp_tools.py packaging/mcpb/manifest.json tests/test_mcp_tools.py tests/test_meetings_bridge.py
git commit -m "MCP search_meetings describes title and date matching"
```

---

### Task 4: Title hits in the Meetings search results

**Files:**
- Modify: `frontend/src/mock/meetings.ts:72-83` (type) and `:496` (`MOCK_RESULTS`)
- Modify: `frontend/src/meetings/SearchResults.tsx`

**Interfaces:**
- Consumes: the bridge result from Task 3.
- Produces: `SearchResult.kind: 'meeting' | 'transcript' | 'notes' | 'user_notes'`.

- [ ] **Step 1: Widen the type and add a mock title hit**

In `frontend/src/mock/meetings.ts`, change `kind` to:

```ts
  kind: 'meeting' | 'transcript' | 'notes' | 'user_notes';
```

Make the first entry of `MOCK_RESULTS`:

```ts
  {
    meetingId: standupDetail.id,
    title: standupDetail.title,
    dayLabel: standupDetail.dayLabel,
    time: standupDetail.time,
    kind: 'meeting',
    speaker: null,
    alsoSpeakers: [],
    seconds: null,
    segmentIndex: null,
    parts: [{ text: standupDetail.title, hit: false }],
  },
```

- [ ] **Step 2: Render it**

In `SearchResults.tsx`, render the title span as:

```tsx
            <span className={styles.title}>
              {result.kind === 'meeting'
                ? result.parts.map((part, j) =>
                    part.hit ? (
                      <mark key={j} className={styles.mark}>
                        {part.text}
                      </mark>
                    ) : (
                      <span key={j}>{part.text}</span>
                    ),
                  )
                : result.title}
            </span>
```

Wrap the speaker-line ternary and the `.snippet` div so that neither renders for `kind === 'meeting'`:

```tsx
          {result.kind !== 'meeting' && (
            <>
              {/* existing speaker-line ternary, unchanged */}
              {/* existing snippet div, unchanged */}
            </>
          )}
```

Move the existing JSX inside the fragment exactly as it is. Don't rewrite it.

- [ ] **Step 3: Build and check**

Run: `npm --prefix frontend run build` and `npm --prefix frontend test`
Expected: both succeed (tsc has no errors; the offline check passes).

Then start the `frontend` preview from `.claude/launch.json` (or `npm --prefix frontend run dev`) and open `/meetings.html?state=search`. The first row shows "Stand-up" with no second line; the transcript rows look as before. Take a screenshot.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/mock/meetings.ts frontend/src/meetings/SearchResults.tsx
git commit -m "Meetings search shows title hits"
```

---

### Task 5: Light-mode floating sidebar

**Files:**
- Modify: `frontend/src/styles/tokens.css` (dark `:root` and the light block)
- Modify: `frontend/src/meetings/App.tsx:886-888`, `frontend/src/meetings/App.module.css`
- Modify: `frontend/src/meetings/Sidebar.module.css`, `frontend/src/meetings/MeetingList.module.css:36-38`
- Test: `tests/test_frontend_tokens.py`

**Interfaces:**
- Produces: tokens `--window-canvas`, `--sidebar-bg`, `--sidebar-inset`, `--sidebar-radius`, `--sidebar-shadow`, `--sidebar-divider`, `--sidebar-opaque`, `--row-active-bg`, `--row-active-shadow`, `--search-fill`, `--search-border`, `--sidebar-divider-contrast`, `--sidebar-shadow-contrast`.

- [ ] **Step 0: Take a "before" screenshot.** Before changing any CSS, open `/meetings.html` in the dev preview with the color scheme emulated as dark, and save a screenshot to the scratchpad.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_frontend_tokens.py`)

```python
SIDEBAR_DARK = {
    "--window-canvas": "transparent",
    "--sidebar-bg": "var(--glass-sidebar)",
    "--sidebar-inset": "0px",
    "--sidebar-radius": "0px",
    "--sidebar-shadow": "none",
    "--sidebar-divider": "var(--hairline-lo)",
    "--sidebar-opaque": "var(--surface-opaque)",
    "--row-active-bg": "var(--selection)",
    "--row-active-shadow": "none",
    "--search-fill": "var(--glass-fill-2)",
    "--search-border": "var(--hairline)",
    "--sidebar-divider-contrast": "var(--hairline)",
    "--sidebar-shadow-contrast": "none",
}


def test_sidebar_tokens_keep_dark_mode_unchanged():
    dark, _ = parse_tokens()
    assert {k: dark.get(k) for k in SIDEBAR_DARK} == SIDEBAR_DARK


def test_light_sidebar_panel_values_and_contrast():
    _, light = parse_tokens()
    assert light["--window-canvas"].upper() == "#ECEEED"
    assert light["--sidebar-bg"] == "rgba(255,255,255,0.62)"
    assert (light["--sidebar-inset"], light["--sidebar-radius"]) == ("6px", "11px")
    assert light["--row-active-bg"].upper() == "#FFFFFF"
    panel = "#%02X%02X%02X" % tuple(round(c) for c in _over(
        _rgba(light["--sidebar-bg"]), _rgba(light["--window-canvas"])[:3]))
    for bg in (panel, light["--row-active-bg"], light["--window-canvas"]):
        for tok, floor in (("--text-hi", 4.5), ("--text", 4.5), ("--text-mid", 4.5), ("--text-lo", 3.0)):
            ratio = contrast(light[tok], bg)
            assert ratio >= floor, f"light {tok} on {bg}: {ratio:.2f}"
    assert contrast(light["--titlebar-text"], light["--window-canvas"]) >= 4.5
```

The expected ratios are text-mid 5.24 and text-lo 3.94 on the panel (≈ `#F8F9F8`), and text-lo 3.86 on the canvas.

- [ ] **Step 2: Run the tests to check they fail**

Run: `.venv/bin/python -m pytest tests/test_frontend_tokens.py -q`
Expected: the two new tests fail with a `KeyError` or a dict mismatch.

- [ ] **Step 3: Add the tokens**

In `tokens.css`, dark `:root`, after `--hairline-lo`:

```css
  /* Meetings sidebar. Dark values are today's look; light floats an inset
     frosted panel on an opaque canvas (spec 2026-10-03). */
  --window-canvas: transparent;
  --sidebar-bg: var(--glass-sidebar);
  --sidebar-inset: 0px;
  --sidebar-radius: 0px;
  --sidebar-shadow: none;
  --sidebar-divider: var(--hairline-lo);
  --sidebar-opaque: var(--surface-opaque);
  --row-active-bg: var(--selection);
  --row-active-shadow: none;
  --search-fill: var(--glass-fill-2);
  --search-border: var(--hairline);
  --sidebar-divider-contrast: var(--hairline);
  --sidebar-shadow-contrast: none;
```

In the light block, after `--hairline-lo`:

```css
    --window-canvas: #ECEEED;
    --sidebar-bg: rgba(255,255,255,0.62);
    --sidebar-inset: 6px;
    --sidebar-radius: 11px;
    --sidebar-shadow: 0 0 0 0.5px rgba(0,0,0,0.08), 0 4px 14px -6px rgba(20,30,25,0.18);
    --sidebar-divider: transparent;
    --sidebar-opaque: #F7F7F6;
    --row-active-bg: #FFFFFF;
    --row-active-shadow: 0 0 0 0.5px rgba(0,0,0,0.08), 0 1px 3px rgba(0,0,0,0.06);
    --search-fill: rgba(0,0,0,0.045);
    --search-border: transparent;
    --sidebar-divider-contrast: transparent;
    --sidebar-shadow-contrast: 0 0 0 0.5px rgba(0,0,0,0.12), 0 4px 14px -6px rgba(20,30,25,0.18);
```

(`rgba(0,0,0,0.12)` is the light `--hairline` value, written out in full.)

- [ ] **Step 4: The canvas wrapper**

In `App.tsx`, wrap the title bar and the split:

```tsx
    <GlassPanel width={1040} height={660}>
      <div className={styles.window}>
        <TitleBar title="Meetings" />
        <div className={styles.split} ref={containerRef}>
          {/* …unchanged… */}
        </div>
      </div>
```

`GlassPanel`'s only children are `<TitleBar>` and `.split` (sheets render inside `.split`), so close the new `</div>` immediately before `</GlassPanel>` and re-indent. Nothing else moves.

In `App.module.css`, add:

```css
.window {
  height: 100%;
  background: var(--window-canvas);
}
```

- [ ] **Step 5: Sidebar and selected-row styles**

In `Sidebar.module.css`, `.sidebar`, replace `height: 100%;`, `background:` and `border-right:` with:

```css
  align-self: stretch;
  margin: var(--sidebar-inset) 0 var(--sidebar-inset) var(--sidebar-inset);
  border-radius: var(--sidebar-radius);
  box-shadow: var(--sidebar-shadow);
  background: var(--sidebar-bg);
  border-right: 0.5px solid var(--sidebar-divider);
```

Set `.searchInput` `border:` to `0.5px solid var(--search-border)` and `background:` to `var(--search-fill)`. Keep the `:focus` rule.

Change `.rowActive` to:

```css
.rowActive {
  background: var(--row-active-bg);
  box-shadow: var(--row-active-shadow);
  color: var(--text-hi);
}
```

In the `prefers-contrast: more` block, replace `.sidebar { border-right-color: var(--hairline); }` with:

```css
  .sidebar {
    border-right-color: var(--sidebar-divider-contrast);
    box-shadow: var(--sidebar-shadow-contrast);
  }
```

In the `prefers-reduced-transparency: reduce` block, use `background: var(--sidebar-opaque);`.

In `MeetingList.module.css`, change `.rowActive` to:

```css
.rowActive {
  background: var(--row-active-bg);
  box-shadow: var(--row-active-shadow);
}
```

- [ ] **Step 6: Run the tests and build**

Run: `.venv/bin/python -m pytest tests/test_frontend_tokens.py -q` and `npm --prefix frontend run build`
Expected: all pass, including `test_css_modules_use_tokens_only`.

Mutation check: set the dark `--sidebar-inset` to `6px`. `test_sidebar_tokens_keep_dark_mode_unchanged` must fail. Revert it.

- [ ] **Step 7: Look at it in the dev preview**

Open `/meetings.html` in the preview with the color scheme emulated as light, then dark, and screenshot both.
- **Light:** an inset, rounded, frosted panel on a light grey canvas, and the selected meeting as a white chip.
- **Dark:** identical to the "before" dark screenshot from Step 0.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/styles/tokens.css frontend/src/meetings/App.tsx frontend/src/meetings/App.module.css frontend/src/meetings/Sidebar.module.css frontend/src/meetings/MeetingList.module.css tests/test_frontend_tokens.py
git commit -m "Light-mode Meetings sidebar: floating frosted panel"
```

---

### Task 6: Install, look at it in the real app, and update the docs

**Files:**
- Modify: `docs/superpowers/plans/2026-10-03-recording-pill.md` (Follow-ups: mark both items done, linking this plan)
- Modify: `README.md`, in the section that describes Meetings search, if it says titles aren't searched (`grep -n -i "search" README.md`)

- [ ] **Step 1: Full suite** (timeout 600000 ms): `.venv/bin/python -m pytest -q`. Expected: everything passes.
- [ ] **Step 2: Install:** `scripts/build_app.sh --install`. This stops Claude Desktop's Speakeasy MCP connector. Tell the user to toggle it back on (see the memory note).
- [ ] **Step 3: On-screen check, done by the controller, not a subagent.** Follow the installed-app UI-check memory notes. Use a temporary HOME seeded with a meeting saved today with the default title, plus "Stand-up" and "Retro" on another day.
  - Searching `meeting` puts the default-titled meeting first, with "Meeting" highlighted in the title and no second line.
  - Searching today's date as `Oct 3` (use the actual day) lists that day's meetings, earliest first.
  - Searching `standup Oct 3` returns nothing when no stand-up was that day.
  - **Light appearance** (Settings › Appearance › Light): the sidebar is the floating panel, the selected meeting is a white chip, and no wallpaper shows through.
  - **Dark appearance:** the sidebar looks as before.
  - Screenshot each state and send the screenshots to the user.
- [ ] **Step 4: Docs.** Mark the two follow-ups done in the recording-pill plan. Fix the README if needed. Commit:

```bash
git add docs README.md
git commit -m "Docs: search by title and date; light sidebar follow-ups done"
```

## Progress

- [x] Task 1 · [x] Task 2 · [x] Task 3 · [x] Task 4 · [x] Task 5 · [x] Task 6 (3 Oct 2026, branch `feature/search-light-sidebar`)

Done = tests and installed build checked; the Meetings window's on-screen look in the
installed app is still the user's check (screen access to Speakeasy was declined before).
- Full suite: 1254 passed. Each task had an Opus review with mutation checks; the final branch review found no must-fix items.
- Dev preview (mock data): the title hit renders with no second line and opens on the Summary tab. Dark computed styles match the pre-change baseline. Light shows the inset frosted panel with a white chip.
- Installed `Speakeasy --mcp` with a temporary HOME: `meeting` returns the default-titled meeting with **Meeting** bold, `Oct 3` lists today's meeting, `standup Oct 3` returns nothing, and `stand-up` and `Retro` return title hits.

### Changes from the plan made during execution
- Task 4 also widened `JumpTarget.kind` (MeetingDetail.tsx) to include `'meeting'`, which tsc needs. A title hit opens the meeting on its default tab with no jump (early return). Without that return, a title hit would have forced the Transcript tab.
- `SearchResults.tsx` uses a local `renderParts` helper for both the title and the snippet.
- Extra tests: the day/year boundaries of the date parser, punctuation-only queries return `[]`, and `a.b` matches as a literal.

### Loose ends
- **Known limitation:** title matching is by substring, and default titles are "Meeting — Oct 3, 7:58 PM". So "meeting", "pm" or "oct" title-match almost every default-titled meeting and can fill the whole limit (10 in MCP, 50 in the app), which crowds out content hits. Spec-consistent. A possible later change is to cap title hits at `limit // 2`.
- The title highlight uses `re.I` but the SQL filter uses `casefold`, so "Straße" vs "strasse" can match without a highlight. Cosmetic.
- Flaky test: `tests/test_meeting_recorder.py::test_gap_fill_queue_full_is_retried_on_the_next_block` failed twice in full-suite runs (FlakyQueue thread error) and passed alone. It predates this branch.
- The light `--sidebar-opaque` (reduced transparency) isn't included in the contrast test. It is lighter than the canvas, which passes.
- No frontend unit test covers the title-hit default-tab behaviour. Only the preview check covers it.
