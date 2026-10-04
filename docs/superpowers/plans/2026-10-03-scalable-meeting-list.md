# Scalable Meeting List, Anchored Today Header, MCP Recall — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:**
- Anchor the Today header to the pane edges.
- Replace the flat, 500-capped meeting list with collapsible time sections.
- Make the MCP tools fit "why and when did I decide X?" recall from Claude Desktop.

**Architecture:**
- **Part 1** is CSS only (`TodayView.module.css`).
- **Part 2:**
  - the bridge sends every meeting plus a local `startDate`;
  - a new pure module `listSections.ts` builds the sections and the visible-row and
    open-path helpers;
  - `MeetingList.tsx` renders them, with open state kept in localStorage.
- **Part 3** changes `mcp_tools.py` and `meeting_library.py`:
  - compact list rows;
  - search grouped by meeting, with an offset;
  - `pending_summaries` over a date range;
  - updated descriptions, mirrored in `packaging/mcpb/manifest.json`.

**Tech Stack:** Python 3 (pytest, SQLite FTS5); React + TypeScript (Vite, `node --test` for pure modules); WKWebView host.

**Spec:** `docs/superpowers/specs/2026-10-03-scalable-meeting-list-design.md`

## Global Constraints

- Fully offline: no new dependencies, no network calls.
- Python: use `.venv/bin/python`. Run tests with
  `.venv/bin/python -B -m pytest <path> -q`, with a timeout of at least 600000 ms for the
  full suite (about 400 tests).
- Frontend tests: `cd frontend && npm test` (only pure modules live in `frontend/tests/`).
  Type check and build: `cd frontend && npm run build`.
- Never run app code against real `~/Library/Application Support` data. Tests use the
  `library_path` fixture, which already isolates storage.
- Weeks start on **Monday**.
- Every MCP tool description change must also go into `packaging/mcpb/manifest.json`, or
  `tests/test_mcpb.py::test_manifest_tools_match_tools_list_exactly` fails. Regenerate it with:
  ```bash
  .venv/bin/python -c "import json,pathlib,tempfile,os;os.environ['HOME']=tempfile.mkdtemp();from speakeasy.mcp_tools import build_tools;p=pathlib.Path('packaging/mcpb/manifest.json');m=json.loads(p.read_text());t=build_tools(None);m['tools']=[{'name':x.name,'description':x.description} for x in t.values()];p.write_text(json.dumps(m,indent=2,ensure_ascii=False)+'\n')"
  ```
  (`build_tools` only stores the library, so `None` is fine for descriptions.) Afterwards,
  run `git diff packaging/mcpb/manifest.json`: only description text may change.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Work on branch `scalable-meeting-list` in a worktree, never on `master`.

## Review Focus

1. **A selected meeting inside a collapsed section** (opened from search, from Today's
   "Recorded ✓" row, or after a delete moves the selection) must open its section and scroll
   into view. *Pinned by `sectionPathFor` tests (Task 3); checked live in Task 9.*
2. **Midnight or week rollover while the window is open:** sections must regroup without a
   reload. `today` comes from `nowMs`, which ticks every 30 s. *Pinned by `buildSections`
   tests with different `today` values (Task 3).*
3. **A filtered list (Tag or Person)** shows every section open and never overwrites the
   user's stored open state. *Pinned by `visibleRows(..., expandAll=true)` (Task 3); checked
   live in Task 9.*
4. **Corrupt or old localStorage** (non-JSON, non-object, or keys for sections that no longer
   exist) must not crash the list or hide rows. *Pinned by `parseOpenState` tests (Task 3).*
5. **MCP paging past the end** (an offset beyond the results) returns an empty list with
   `next_offset: null`, never an error. *Pinned in Tasks 6 and 7.*

---

### Task 1: Anchor the Today header and left-align its content

**Files:**
- Modify: `frontend/src/meetings/TodayView.module.css`

**Interfaces:** none (CSS only).

- [ ] **Step 1: Header spans the pane.** In `.header`, delete `max-width: 760px;` and
  `margin: 0 auto;`. Keep `width: 100%`, `justify-content: space-between` and the padding
  `18px 24px 6px`. Add right after the `.title` rule:

```css
.header > :last-child:not(.title) {
  flex-shrink: 0;
}
```

- [ ] **Step 2: Left-align content on the title's 24 px edge.** Make these exact edits:
  - `.agenda`: `margin: 0 auto;` → `margin: 0;`
  - `.banner`: `margin: 6px auto;` → `margin: 6px 24px;`
  - `.hero`: `margin: 8px auto;` → `margin: 8px 24px;`
  - `.quiet`: `margin: 8px auto;` → `margin: 8px 24px;`
  - `.foldRow, .earlier`: `margin: 0 auto;` → `margin: 0;`
  - `.upcoming`: add `width: 100%; max-width: 760px;`

- [ ] **Step 3: Build.** Run `cd frontend && npm run build`. Expected: no errors, and
  `check-offline` passes.

- [ ] **Step 4: Look at it.** Start the mock dev server via `preview_start` (Vite;
  `frontend/meetings.html`). Check at widths 900 and 2000:
  - "Today" sits 24 px from the pane's left edge;
  - Start Meeting's right edge is 24 px from the pane's right edge;
  - Upcoming rows start on the same left edge as the hero.

  Take a screenshot at each width.

- [ ] **Step 5: Commit.**

```bash
git add frontend/src/meetings/TodayView.module.css
git commit -m "Today: anchor header to pane edges, left-align content"
```

---

### Task 2: Bridge sends every meeting plus `startDate`

**Files:**
- Modify: `speakeasy/meeting_library.py` (`list_meetings`, around line 760)
- Modify: `speakeasy/ui/meetings_bridge.py:301-323`
- Modify: `frontend/src/mock/meetings.ts` (`MeetingMeta`, mock metas)
- Test: `tests/test_meetings_bridge.py`, `tests/test_meeting_library.py`

**Interfaces:**
- Produces:
  - `MeetingLibrary.list_meetings(..., limit=None)`: `None` means no limit; integers still
    clamp to 1..500.
  - Every bridge meta (list and detail) gains `"startDate": "YYYY-MM-DD"`, the local day.
  - TS: `MeetingMeta.startDate: string`.

- [ ] **Step 1: Failing tests.** Append to `tests/test_meetings_bridge.py`:

```python
def test_list_returns_every_meeting_past_500(library_path):
    lib = MeetingLibrary()
    start = datetime(2025, 1, 1, 9, 0, tzinfo=TZ)
    for i in range(501):
        lib.save_meeting(NewMeeting(
            segments=[MeetingSegment("You", 0, 5, "hi")], duration_seconds=300,
            started_at=start + timedelta(hours=i), title=f"M{i}"))
    bridge = MeetingsBridge(library=lib, now=NOW)
    assert len(bridge.list_payload({})) == 501


def test_meta_start_date_is_local_day(library_path):
    lib = MeetingLibrary()
    # 23:30 at UTC-4 is already the next day in UTC: startDate must stay local.
    lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0, 5, "late")], duration_seconds=300,
        started_at=datetime(2026, 9, 23, 23, 30, tzinfo=TZ), title="Late"))
    bridge = MeetingsBridge(library=lib, now=NOW)
    [meta] = bridge.list_payload({})
    assert meta["startDate"] == "2026-09-23"
    assert bridge.get_payload({"id": meta["id"]})["startDate"] == "2026-09-23"
```

  Check how other tests in that file build the bridge (they may use a `now=` keyword or a
  helper). Match that exactly; if the constructor keyword differs, use the existing pattern.

  Append to `tests/test_meeting_library.py`:

```python
def test_list_meetings_none_limit_is_uncapped(library_path):
    lib = MeetingLibrary()
    base = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    for i in range(502):
        lib.save_meeting(NewMeeting(
            segments=[MeetingSegment("You", 0, 5, "hi")], duration_seconds=300,
            started_at=base + timedelta(hours=i), title=f"M{i}"))
    assert len(lib.list_meetings(limit=None)) == 502
    assert len(lib.list_meetings(limit=10_000)) == 500   # integers still clamp
    assert len(lib.list_meetings()) == 100               # default unchanged
```

  Add any missing imports at the top of the file (`timedelta`, `timezone`, `NewMeeting`,
  `MeetingSegment`).

- [ ] **Step 2: Run them; they fail.**
  Run `.venv/bin/python -B -m pytest tests/test_meetings_bridge.py tests/test_meeting_library.py -q -k "past_500 or start_date or none_limit"`.
  Expected: 3 failures (500 rows; KeyError `startDate`; `int(None)` TypeError).

- [ ] **Step 3: Implement.** In `MeetingLibrary.list_meetings`, replace
  `limit = max(1, min(int(limit), 500))` with:

```python
        # None = every meeting (the Meetings window groups them itself);
        # SQLite treats LIMIT -1 as no limit.
        limit = -1 if limit is None else max(1, min(int(limit), 500))
```

  In `MeetingsBridge._meta_fields`, add to the returned dict, right after `"dayLabel"`:

```python
            "startDate": meeting_local_start.date().isoformat(),
```

  In `list_payload`, change `limit=500` to `limit=None`.

- [ ] **Step 4: TS type and mock data.** In `frontend/src/mock/meetings.ts`:
  - add `startDate: string;` after `dayLabel: string;` in `MeetingMeta`;
  - give each mock meta a `startDate` consistent with `MOCK_NOW_MS` (Tue 29 Sep 2026), so
    the mock shows every section kind:

    | meta                 | `startDate`  |
    |----------------------|--------------|
    | `standupMeta`        | `2026-09-29` |
    | `oneOnOneMeta`       | `2026-09-29` |
    | `designReviewMeta`   | `2026-09-28` |
    | `sprintPlanningMeta` | `2026-09-28` |
    | `customerCallMeta`   | `2026-09-24` |
    | `retroMeta`          | `2026-09-22` |
    | `q3KickoffMeta`      | `2026-08-04` |
    | `onboardingSamMeta`  | `2025-11-12` |

  Fix any other object literals typed `MeetingMeta` that `tsc` flags (search results build
  their own shape, so check `npm run build`).

- [ ] **Step 5: Run the tests and build.**
  Run `.venv/bin/python -B -m pytest tests/test_meetings_bridge.py tests/test_meeting_library.py -q`,
  then `cd frontend && npm run build`. Expected: all pass.

- [ ] **Step 6: Commit.**

```bash
git add speakeasy/meeting_library.py speakeasy/ui/meetings_bridge.py frontend/src/mock/meetings.ts tests/test_meetings_bridge.py tests/test_meeting_library.py
git commit -m "Meetings window: list every meeting (no 500 cap), add local startDate"
```

---

### Task 3: `listSections.ts`, the pure section model

**Files:**
- Create: `frontend/src/meetings/listSections.ts`
- Test: `frontend/tests/listSections.test.ts`

**Interfaces:**
- Consumes: `MeetingMeta.startDate` (Task 2).
- Produces (all exported):
  - `interface RowGroup { label: string | null; rows: MeetingMeta[] }`
  - `interface Section { key: string; label: string; count: number; defaultOpen: boolean; groups: RowGroup[]; children: Section[] }`.
    A year section has `children` (its months) and empty `groups`; every other section has
    `groups` and empty `children`.
  - `type OpenState = Record<string, boolean>`
  - `localIsoDate(ms: number): string`
  - `buildSections(metas: MeetingMeta[], today: string): Section[]`
  - `isOpen(section: Section, state: OpenState, expandAll: boolean): boolean`
  - `visibleRows(sections: Section[], state: OpenState, expandAll: boolean): MeetingMeta[]`
  - `sectionPathFor(sections: Section[], id: string): string[]`
  - `parseOpenState(raw: string | null): OpenState`

- [ ] **Step 1: Write the failing tests** in `frontend/tests/listSections.test.ts`:

```ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildSections,
  isOpen,
  localIsoDate,
  parseOpenState,
  sectionPathFor,
  visibleRows,
} from '../src/meetings/listSections.ts';
import type { MeetingMeta } from '../src/mock/meetings.ts';

function meta(id: string, startDate: string): MeetingMeta {
  return {
    id, title: id, dayLabel: '', startDate, time: '9:00 AM', duration: '30 min',
    subtitle: '', speakerCount: 1, hasSummary: false, approximate: false, tags: [], people: [],
  };
}
// Newest first, as the bridge sends them.
function metas(...dates: string[]): MeetingMeta[] {
  return dates.map((d, i) => meta(`m${i}`, d));
}
const keys = (s: ReturnType<typeof buildSections>) => s.map((x) => x.key);

// Saturday 3 Oct 2026: this week = Mon 28 Sep..; last week = 21–27 Sep.
const SAT = '2026-10-03';

test('recent sections, Monday week start, labels and counts', () => {
  const s = buildSections(
    metas('2026-10-03', '2026-10-02', '2026-10-01', '2026-09-28', '2026-09-27', '2026-09-21', '2026-09-20'),
    SAT,
  );
  assert.deepEqual(keys(s), ['today', 'yesterday', 'this-week', 'last-week', 'm-2026-09']);
  assert.deepEqual(s.map((x) => x.label), ['Today', 'Yesterday', 'This week', 'Last week', 'September']);
  assert.deepEqual(s.map((x) => x.count), [1, 1, 2, 2, 1]);
  assert.deepEqual(s.map((x) => x.defaultOpen), [true, true, true, true, false]);
  assert.deepEqual(s[2].groups.map((g) => g.label), ['Thursday', 'Monday']);
  assert.deepEqual(s[3].groups.map((g) => g.label), ['Sunday 27 Sep', 'Monday 21 Sep']);
  assert.deepEqual(s[0].groups.map((g) => g.label), [null]);
});

test('today is a Monday: yesterday (Sunday) wins over last week; no this-week section', () => {
  const s = buildSections(metas('2026-09-28', '2026-09-27', '2026-09-26'), '2026-09-28');
  assert.deepEqual(keys(s), ['today', 'yesterday', 'last-week']);
  assert.equal(s[2].count, 1);
});

test('today is a Sunday: this week runs Monday..Friday', () => {
  const s = buildSections(metas('2026-10-02', '2026-09-28', '2026-09-27'), '2026-10-04');
  assert.deepEqual(keys(s), ['this-week', 'last-week']);
  assert.equal(s[0].count, 2);
});

test('older months this year: collapsed, grouped by week, Monday may be in the previous month', () => {
  const s = buildSections(metas('2026-09-02', '2026-09-01', '2026-08-31', '2026-08-04'), SAT);
  assert.deepEqual(keys(s), ['m-2026-09', 'm-2026-08']);
  assert.deepEqual(s[0].groups.map((g) => g.label), ['Week of 31 Aug']);
  assert.equal(s[0].count, 2);
  assert.deepEqual(s[1].groups.map((g) => g.label), ['Week of 31 Aug', 'Week of 3 Aug']);
});

test('earlier years: collapsed year with collapsed month children', () => {
  const s = buildSections(metas('2026-01-05', '2025-12-15', '2025-11-12', '2024-03-01'), SAT);
  assert.deepEqual(keys(s), ['m-2026-01', 'y-2025', 'y-2024']);
  const y = s[1];
  assert.equal(y.label, '2025');
  assert.equal(y.count, 2);
  assert.equal(y.defaultOpen, false);
  assert.deepEqual(y.groups, []);
  assert.deepEqual(y.children.map((c) => [c.key, c.label, c.count, c.defaultOpen]), [
    ['m-2025-12', 'December 2025', 1, false],
    ['m-2025-11', 'November 2025', 1, false],
  ]);
});

test('New Year: last week can span years; older December goes to the year section', () => {
  // Fri 2 Jan 2026: this week = Mon 29 Dec 2025..; last week = 22–28 Dec 2025.
  const s = buildSections(metas('2026-01-01', '2025-12-29', '2025-12-22', '2025-12-21'), '2026-01-02');
  assert.deepEqual(keys(s), ['yesterday', 'this-week', 'last-week', 'y-2025']);
  assert.deepEqual(s[1].groups.map((g) => g.label), ['Monday']);
  assert.deepEqual(s[3].children.map((c) => c.key), ['m-2025-12']);
});

test('future-dated meetings fall into today; empty input gives no sections', () => {
  assert.deepEqual(keys(buildSections(metas('2026-10-05'), SAT)), ['today']);
  assert.deepEqual(buildSections([], SAT), []);
});

test('visibleRows respects collapsed sections, year months and expandAll', () => {
  const s = buildSections(metas('2026-10-03', '2026-08-04', '2025-11-12'), SAT);
  assert.deepEqual(visibleRows(s, {}, false).map((m) => m.id), ['m0']);
  assert.deepEqual(visibleRows(s, { 'm-2026-08': true }, false).map((m) => m.id), ['m0', 'm1']);
  // An open year with a closed month still hides that month's rows.
  assert.deepEqual(visibleRows(s, { 'y-2025': true }, false).map((m) => m.id), ['m0']);
  assert.deepEqual(visibleRows(s, { 'y-2025': true, 'm-2025-11': true }, false).map((m) => m.id), ['m0', 'm2']);
  // An open month inside a closed year stays hidden.
  assert.deepEqual(visibleRows(s, { 'm-2025-11': true }, false).map((m) => m.id), ['m0']);
  assert.deepEqual(visibleRows(s, { today: false }, true).map((m) => m.id), ['m0', 'm1', 'm2']);
  assert.equal(isOpen(s[0], { today: false }, false), false);
  assert.equal(isOpen(s[0], {}, false), true);
});

test('sectionPathFor gives the keys to open', () => {
  const s = buildSections(metas('2026-10-03', '2026-08-04', '2025-11-12'), SAT);
  assert.deepEqual(sectionPathFor(s, 'm0'), ['today']);
  assert.deepEqual(sectionPathFor(s, 'm1'), ['m-2026-08']);
  assert.deepEqual(sectionPathFor(s, 'm2'), ['y-2025', 'm-2025-11']);
  assert.deepEqual(sectionPathFor(s, 'missing'), []);
});

test('parseOpenState tolerates junk', () => {
  assert.deepEqual(parseOpenState(null), {});
  assert.deepEqual(parseOpenState('not json'), {});
  assert.deepEqual(parseOpenState('[1,2]'), {});
  assert.deepEqual(parseOpenState('"x"'), {});
  assert.deepEqual(parseOpenState('{"a":true,"b":"yes","c":false}'), { a: true, c: false });
});

test('localIsoDate uses local calendar fields', () => {
  assert.equal(localIsoDate(new Date(2026, 0, 2, 23, 59).getTime()), '2026-01-02');
});
```

- [ ] **Step 2: Run; it fails.** Run `cd frontend && npm test`. Expected: the import of
  `listSections.ts` fails (module not found).

- [ ] **Step 3: Implement** `frontend/src/meetings/listSections.ts`:

```ts
import type { MeetingMeta } from '../mock/meetings';

/** A run of rows under one subheading (null: no subheading). */
export interface RowGroup {
  label: string | null;
  rows: MeetingMeta[];
}

/** A collapsible list section. Year sections hold month `children` and no
 *  `groups`; every other section holds `groups` and no `children`. */
export interface Section {
  key: string;
  label: string;
  count: number;
  defaultOpen: boolean;
  groups: RowGroup[];
  children: Section[];
}

/** Section key -> open, as the user last left it. */
export type OpenState = Record<string, boolean>;

const DAY_MS = 86_400_000;
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

/** 'YYYY-MM-DD' of `ms` in the local time zone. */
export function localIsoDate(ms: number): string {
  const d = new Date(ms);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// Calendar arithmetic on whole days (UTC midnights), so DST never shifts a day.
function dayNumber(iso: string): number {
  const [y, m, d] = iso.split('-').map(Number);
  return Math.round(Date.UTC(y, m - 1, d) / DAY_MS);
}

function parts(day: number): { y: number; m: number; d: number; weekday: number } {
  const date = new Date(day * DAY_MS);
  return { y: date.getUTCFullYear(), m: date.getUTCMonth(), d: date.getUTCDate(), weekday: (date.getUTCDay() + 6) % 7 };
}

function weekOfLabel(day: number): string {
  const monday = parts(day - parts(day).weekday);
  return `Week of ${monday.d} ${MONTHS_SHORT[monday.m]}`;
}

function newSection(key: string, label: string, defaultOpen: boolean): Section {
  return { key, label, count: 0, defaultOpen, groups: [], children: [] };
}

function addRow(section: Section, label: string | null, row: MeetingMeta) {
  section.count += 1;
  const last = section.groups[section.groups.length - 1];
  if (last && last.label === label) last.rows.push(row);
  else section.groups.push({ label, rows: [row] });
}

/** Group `metas` (newest first) into time sections relative to `today` ('YYYY-MM-DD'). */
export function buildSections(metas: MeetingMeta[], today: string): Section[] {
  const t = dayNumber(today);
  const { y: thisYear } = parts(t);
  const thisMonday = t - parts(t).weekday;
  const lastMonday = thisMonday - 7;
  const sections: Section[] = [];
  const byKey = new Map<string, Section>();

  function section(key: string, label: string, defaultOpen: boolean, parent?: Section): Section {
    let s = byKey.get(key);
    if (!s) {
      s = newSection(key, label, defaultOpen);
      byKey.set(key, s);
      (parent ? parent.children : sections).push(s);
    }
    return s;
  }

  for (const row of metas) {
    const n = dayNumber(row.startDate);
    const p = parts(n);
    if (n >= t) addRow(section('today', 'Today', true), null, row);
    else if (n === t - 1) addRow(section('yesterday', 'Yesterday', true), null, row);
    else if (n >= thisMonday) addRow(section('this-week', 'This week', true), WEEKDAYS[p.weekday], row);
    else if (n >= lastMonday)
      addRow(section('last-week', 'Last week', true), `${WEEKDAYS[p.weekday]} ${p.d} ${MONTHS_SHORT[p.m]}`, row);
    else if (p.y === thisYear)
      addRow(section(`m-${p.y}-${pad(p.m + 1)}`, MONTHS[p.m], false), weekOfLabel(n), row);
    else {
      const year = section(`y-${p.y}`, String(p.y), false);
      year.count += 1;
      addRow(section(`m-${p.y}-${pad(p.m + 1)}`, `${MONTHS[p.m]} ${p.y}`, false, year), weekOfLabel(n), row);
    }
  }
  return sections;
}

export function isOpen(section: Section, state: OpenState, expandAll: boolean): boolean {
  return expandAll || (state[section.key] ?? section.defaultOpen);
}

/** Rows a person can see, in display order (keyboard navigation walks these). */
export function visibleRows(sections: Section[], state: OpenState, expandAll: boolean): MeetingMeta[] {
  const out: MeetingMeta[] = [];
  for (const s of sections) {
    if (!isOpen(s, state, expandAll)) continue;
    for (const g of s.groups) out.push(...g.rows);
    out.push(...visibleRows(s.children, state, expandAll));
  }
  return out;
}

/** Keys that must be open for meeting `id` to be visible ([] if absent). */
export function sectionPathFor(sections: Section[], id: string): string[] {
  for (const s of sections) {
    if (s.groups.some((g) => g.rows.some((r) => r.id === id))) return [s.key];
    const inner = sectionPathFor(s.children, id);
    if (inner.length) return [s.key, ...inner];
  }
  return [];
}

/** Stored open state; anything malformed is ignored rather than trusted. */
export function parseOpenState(raw: string | null): OpenState {
  if (!raw) return {};
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
    const out: OpenState = {};
    for (const [k, v] of Object.entries(value)) if (typeof v === 'boolean') out[k] = v;
    return out;
  } catch {
    return {};
  }
}
```

- [ ] **Step 4: Run; it passes.** Run `cd frontend && npm test`. Expected: all tests pass,
  including the 11 new ones.

- [ ] **Step 5: Commit.**

```bash
git add frontend/src/meetings/listSections.ts frontend/tests/listSections.test.ts
git commit -m "Meeting list: pure time-section model (Monday weeks, months, years)"
```

---

### Task 4: `MeetingList` renders sections; App wiring

**Files:**
- Modify: `frontend/src/meetings/MeetingList.tsx` (full rewrite of rendering and keyboard handling)
- Modify: `frontend/src/meetings/MeetingList.module.css`
- Modify: `frontend/src/meetings/App.tsx` (the `<MeetingList …>` call, around line 924)

**Interfaces:**
- Consumes: everything exported by `listSections.ts` (Task 3).
- Produces: `MeetingList` takes two new required props, `today: string` and
  `expandAll: boolean`.

- [ ] **Step 1: Rewrite `MeetingList.tsx`.** Keep `rowId`, `isEditableTarget`, the ⌘F and ⌘⌫
  handling, the listbox ARIA and the row markup exactly as they are today. Replace
  `groupByDay` and the rendering with:

```tsx
import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import type { MeetingMeta } from '../mock/meetings';
import { buildSections, isOpen, parseOpenState, sectionPathFor, visibleRows } from './listSections';
import type { OpenState, Section } from './listSections';
import styles from './MeetingList.module.css';

const STORAGE_KEY = 'meetingList.sections';

function readState(): OpenState {
  try {
    return parseOpenState(window.localStorage.getItem(STORAGE_KEY));
  } catch {
    return {};
  }
}

function writeState(state: OpenState) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* storage unavailable: the state just isn't remembered */
  }
}

interface MeetingListProps {
  metas: MeetingMeta[];
  selectedId: string | null;
  /** Local 'YYYY-MM-DD'; sections regroup when it changes. */
  today: string;
  /** A Tag/Person filter is active: show every section open, store nothing. */
  expandAll: boolean;
  onSelect: (id: string) => void;
  onRequestSearchFocus?: () => void;
  onRequestDelete?: () => void;
}
```

  Inside the component:

```tsx
  const listRef = useRef<HTMLDivElement>(null);
  const rowRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
  const pendingScroll = useRef(false);
  const [openState, setOpenState] = useState<OpenState>(readState);
  const sections = useMemo(() => buildSections(metas, today), [metas, today]);
  const rows = useMemo(() => visibleRows(sections, openState, expandAll), [sections, openState, expandAll]);

  function setOpen(keys: string[], open: boolean) {
    setOpenState((prev) => {
      const next = { ...prev };
      for (const k of keys) next[k] = open;
      writeState(next);
      return next;
    });
  }

  // A selection made elsewhere (search, Today, after a delete) may sit in a
  // collapsed section: open its path, then scroll once the row exists.
  useEffect(() => {
    if (!selectedId) return;
    pendingScroll.current = true;
    if (expandAll) return;
    const closed = sectionPathFor(sections, selectedId).filter((key) => {
      const s = findSection(sections, key);
      return s !== null && !isOpen(s, openState, false);
    });
    if (closed.length) setOpen(closed, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only on a new selection
  }, [selectedId]);

  useEffect(() => {
    if (!pendingScroll.current || !selectedId) return;
    const el = rowRefs.current.get(selectedId);
    if (el) {
      el.scrollIntoView({ block: 'nearest' });
      pendingScroll.current = false;
    }
  }, [selectedId, rows]);
```

  Add this module-level helper next to `readState`:

```tsx
function findSection(sections: Section[], key: string): Section | null {
  for (const s of sections) {
    if (s.key === key) return s;
    const inner = findSection(s.children, key);
    if (inner) return inner;
  }
  return null;
}
```

  In `onKeyDown`, replace every use of `metas` with `rows` (the visible rows). Keep the
  existing logic otherwise:
  - empty → return;
  - ArrowDown → next, or the first row if nothing is selected;
  - ArrowUp → previous, or the first row.

  Rendering. The existing row button JSX moves into `renderRow(meta)` unchanged:

```tsx
  function renderSection(s: Section, depth: number) {
    const open = isOpen(s, openState, expandAll);
    return (
      <div key={s.key} className={styles.section}>
        <button
          type="button"
          className={depth === 0 ? styles.sectionHeader : `${styles.sectionHeader} ${styles.sectionHeaderNested}`}
          aria-expanded={open}
          disabled={expandAll}
          onClick={() => setOpen([s.key], !open)}
        >
          <span className={open ? `${styles.chevron} ${styles.chevronOpen}` : styles.chevron} aria-hidden="true">›</span>
          <span className={styles.sectionLabel}>{s.label}</span>
          <span className={styles.sectionCount}>{s.count}</span>
        </button>
        {open && (
          <>
            {s.groups.map((g, i) => (
              <Fragment key={g.label ?? `g${i}`}>
                {g.label && (
                  <div className={styles.dayHeader} role="presentation">
                    {g.label}
                  </div>
                )}
                {g.rows.map(renderRow)}
              </Fragment>
            ))}
            {s.children.map((c) => renderSection(c, depth + 1))}
          </>
        )}
      </div>
    );
  }
```

  The listbox `div` (same attributes as today) renders `{sections.map((s) => renderSection(s, 0))}`.
  `aria-activedescendant` is only set when the selected row is visible:
  `rows.some((r) => r.id === selectedId)`.

- [ ] **Step 2: CSS.** Append to `MeetingList.module.css`. `.dayHeader` stays as the inner
  subheading; change its padding to `6px 18px 2px`.

```css
.section {
  display: flex;
  flex-direction: column;
}

.sectionHeader {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 8px 18px 6px;
  border: none;
  background: var(--sidebar-bg);
  text-align: left;
  cursor: pointer;
  font: 600 11px/1.4 var(--sans);
  color: var(--text-mid);
}

/* Months inside a year scroll with their year header, not pinned over it. */
.sectionHeaderNested {
  position: static;
  padding-left: 30px;
}

.sectionHeader:disabled {
  cursor: default;
}

.sectionHeader:hover:not(:disabled) {
  color: var(--text-hi);
}

.sectionHeader:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}

.sectionLabel {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sectionCount {
  flex-shrink: 0;
  font: 400 11px/1 var(--sans);
  font-variant-numeric: tabular-nums;
  color: var(--text-lo);
}

.chevron {
  display: inline-block;
  width: 10px;
  font-size: 13px;
  line-height: 1;
  transition: transform 0.12s ease;
}

.chevronOpen {
  transform: rotate(90deg);
}

@media (prefers-reduced-motion: reduce) {
  .chevron {
    transition: none;
  }
}

@media (prefers-reduced-transparency: reduce) {
  .sectionHeader {
    background: var(--surface-opaque);
  }
}
```

  Check that `--sidebar-bg` is opaque enough for rows not to show through the pinned header
  in **both** themes. In light mode the sidebar is a frosted floating panel (commit
  85bd647); if the variable is translucent, use the same backdrop-filter the sidebar uses.
  Verify by looking at it in Task 9.

- [ ] **Step 3: Wire App.** At the `<MeetingList` call in `App.tsx`, add:

```tsx
                today={localIsoDate(isMock ? MOCK_NOW_MS : nowMs)}
                expandAll={filter.type !== 'all'}
```

  and add `import { localIsoDate } from './listSections';`. `MOCK_NOW_MS` and `nowMs` are
  already in scope (used near line 950); confirm before adding imports.

- [ ] **Step 4: Build and test.** Run `cd frontend && npm run build && npm test`. Expected:
  no type errors; all tests pass.

- [ ] **Step 5: Look at it in mock mode** (`preview_start`, `meetings.html`). Expected:
  - Today (2), Yesterday (2), Last week (2) open (Thu 24 and Tue 22 Sep fall in last week,
    since mock today is Tue 29 Sep);
  - `August` (1) collapsed;
  - `2025` (1) collapsed.

  Then check behaviour:
  - clicking `2025` and then `November 2025` shows the row;
  - ArrowDown from the last visible row doesn't jump into a collapsed section;
  - reloading keeps the open state.

  Take a screenshot.

- [ ] **Step 6: Commit.**

```bash
git add frontend/src/meetings/MeetingList.tsx frontend/src/meetings/MeetingList.module.css frontend/src/meetings/App.tsx
git commit -m "Meeting list: collapsible time sections with pinned headers and remembered state"
```

---

### Task 5: MCP M1, compact `list_meetings` rows

**Files:**
- Modify: `speakeasy/mcp_tools.py` (`list_meetings` and its description)
- Modify: `packaging/mcpb/manifest.json` (regenerate; see Global Constraints)
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Produces: each `list_meetings` row has `named_speakers: list[str]` and
  `speaker_count: int` instead of `speakers`. `get_meeting` is unchanged.

- [ ] **Step 1: Failing test.** In `test_list_meetings_shape_and_paging`, replace
  `assert m["speakers"] == ["You", "Speaker 1"]` with:

```python
    assert "speakers" not in m
    assert m["named_speakers"] == ["You"] and m["speaker_count"] == 2
```

  Then append:

```python
def test_list_meetings_drops_anonymous_speaker_labels(lib, tools):
    lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("Speaker 12", 0, 2, "a"), MeetingSegment("Priya", 2, 4, "b"),
                  MeetingSegment("speaker 3", 4, 6, "c"), MeetingSegment("Speaker Phone", 6, 8, "d"),
                  MeetingSegment("You", 8, 9, "e")],
        duration_seconds=600, title="Big", started_at=datetime(2026, 9, 20, 9, tzinfo=PDT)))
    [m] = tools["list_meetings"].run({})["meetings"]
    assert m["named_speakers"] == ["Priya", "Speaker Phone", "You"]
    assert m["speaker_count"] == 5


def test_list_meetings_description_mentions_named_speakers(tools):
    d = tools["list_meetings"].definition()["description"]
    assert "named speakers" in d and "get_meeting" in d
```

- [ ] **Step 2: Run; it fails.** Run `.venv/bin/python -B -m pytest tests/test_mcp_tools.py -q -k list_meetings`.

- [ ] **Step 3: Implement.** In `mcp_tools.py`, add near `_DATE_RE`:

```python
# Diarization's placeholder labels ("Speaker 12"); same pattern as
# voice_profiles._ANONYMOUS_RE. Listing them made a 100-row page ~74k chars.
_ANONYMOUS_SPEAKER_RE = re.compile(r"Speaker [1-9][0-9]*", re.IGNORECASE)
```

  In `list_meetings`, replace `"speakers": m.speakers,` with:

```python
                "named_speakers": [s for s in m.speakers
                                   if not _ANONYMOUS_SPEAKER_RE.fullmatch(s)],
                "speaker_count": m.speaker_count,
```

  The `named_speakers` order is first appearance, as `m.speakers` is today. The test's
  expected order `["Priya", "Speaker Phone", "You"]` follows segment order; if the library
  orders speakers differently, adjust the test to the library's documented order, not the
  code to the test.

  Replace the `list_meetings` description with:

  > "List saved meetings, newest first, with date, duration, named speakers (unnamed 'Speaker N' labels are only counted in speaker_count), people, tags and whether a summary exists. No transcript text. Filter by title words to find a meeting by name; use get_meeting for full detail."

- [ ] **Step 4: Regenerate the manifest** (command in Global Constraints). Then run
  `.venv/bin/python -B -m pytest tests/test_mcp_tools.py tests/test_mcpb.py tests/test_mcp_server.py -q`.
  Expected: all pass. If `test_mcp_server.py` asserts on `speakers` in a list result,
  update it the same way.

- [ ] **Step 5: Commit.**

```bash
git add speakeasy/mcp_tools.py packaging/mcpb/manifest.json tests/test_mcp_tools.py tests/test_mcp_server.py
git commit -m "MCP list_meetings: named speakers + count instead of every Speaker N label"
```

---

### Task 6: MCP M2, search by meeting with paging

**Files:**
- Modify: `speakeasy/meeting_library.py` (`search` around line 1211; new `MeetingHits`, `_ranked_hits`, `search_grouped`)
- Modify: `speakeasy/mcp_tools.py` (`search_meetings` handler, schema, description)
- Modify: `packaging/mcpb/manifest.json` (regenerate)
- Test: `tests/test_meeting_search.py`, `tests/test_mcp_tools.py`

**Interfaces:**
- Produces:
  - `MeetingLibrary.search(query, *, from_date=None, to_date=None, tag=None, person=None, limit=10, offset=0) -> list[SearchHit]`:
    `limit` clamps 1..100 (was 50), `offset` clamps 0..1000. With `offset=0` and
    `limit ≤ 50`, results are identical to today.
  - `@dataclass MeetingHits(meeting_id: str, title: str, started_at: str, tz_offset_minutes: int, hit_count: int, first_seconds: float | None, last_seconds: float | None, kinds: list[str], best: list[SearchHit])`.
  - `MeetingLibrary.search_grouped(query, *, from_date=None, to_date=None, tag=None, person=None, limit=10, offset=0) -> list[MeetingHits]`.
  - MCP `search_meetings` accepts `by_meeting: bool` and `offset: int`, and returns
    `{"results": [...], "offset": int, "next_offset": int | None}`.

- [ ] **Step 1: Failing library tests.** Append to `tests/test_meeting_search.py`, reusing
  its `_save(lib, day, *segments)` helper (check its signature at the top of the file and
  match it):

```python
def test_search_offset_pages_without_overlap(library_path):
    lib = MeetingLibrary()
    for day in range(1, 13):
        _save(lib, day, ("You", 0, 5, "standup notes"))
    first = lib.search("standup", limit=5)
    second = lib.search("standup", limit=5, offset=5)
    assert len(first) == 5 and len(second) == 5
    assert not {h.meeting_id for h in first} & {h.meeting_id for h in second}
    assert lib.search("standup", limit=5, offset=500) == []
    assert lib.search("standup", limit=5, offset=0) == lib.search("standup", limit=5)


def test_search_grouped_one_row_per_meeting(library_path):
    lib = MeetingLibrary()
    busy = _save(lib, 3, ("You", 10, 15, "the API design"), ("Speaker 1", 600, 605, "API versioning"),
                 ("You", 1200, 1205, "API again"))
    quiet = _save(lib, 4, ("You", 30, 35, "one API mention"))
    groups = lib.search_grouped("API", limit=10)
    assert [g.meeting_id for g in groups][:2] in ([busy, quiet], [quiet, busy])
    g = next(g for g in groups if g.meeting_id == busy)
    assert g.hit_count == 3
    assert g.first_seconds == 10 and g.last_seconds == 1200
    assert g.kinds == ["transcript"]
    assert len(g.best) == 2 and all(h.meeting_id == busy for h in g.best)
    assert lib.search_grouped("API", limit=1, offset=1)[0].meeting_id == groups[1].meeting_id
    assert lib.search_grouped("API", offset=50) == []


def test_search_grouped_counts_title_hits_as_kind_meeting(library_path):
    lib = MeetingLibrary()
    mid = _save(lib, 5, ("You", 0, 5, "nothing relevant"), title="API roadmap")
    [g] = lib.search_grouped("roadmap")
    assert g.meeting_id == mid and g.kinds == ["meeting"]
    assert g.hit_count == 1 and g.first_seconds is None and g.last_seconds is None
```

`_save(lib, day, *segments, **kw)` forwards keywords to `NewMeeting`, so `title=` works.

- [ ] **Step 2: Run; they fail** (`offset` is an unexpected keyword; `search_grouped` is
  missing). Run `.venv/bin/python -B -m pytest tests/test_meeting_search.py -q`.

- [ ] **Step 3: Implement in `meeting_library.py`.** Add after the `SearchHit` dataclass:

```python
@dataclass
class MeetingHits:
    """search_grouped: one meeting's matches, ranked by its best passage."""
    meeting_id: str
    title: str
    started_at: str
    tz_offset_minutes: int
    hit_count: int
    first_seconds: float | None   # earliest/latest transcript hit; None if none
    last_seconds: float | None
    kinds: list[str]              # in order first matched
    best: list[SearchHit]         # top 2 passages, rank order


GROUPED_FETCH = 2000   # passages per source considered by search_grouped
```

  Refactor `search` so the body that builds the ranked list becomes `_ranked_hits`.
  Behaviour must be unchanged when `offset == 0`.

```python
    def search(self, query: str, *, from_date=None, to_date=None, tag=None,
               person=None, limit=10, offset=0) -> list[SearchHit]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, min(int(offset), 1000))
        need = offset + limit
        ranked = self._ranked_hits(query, from_date, to_date, tag, person,
                                   title_limit=need, fetch=need * 4)
        return ranked[offset:need]

    def search_grouped(self, query: str, *, from_date=None, to_date=None, tag=None,
                       person=None, limit=10, offset=0) -> list[MeetingHits]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, min(int(offset), 1000))
        groups: dict[str, MeetingHits] = {}
        for h in self._ranked_hits(query, from_date, to_date, tag, person,
                                   title_limit=GROUPED_FETCH, fetch=GROUPED_FETCH):
            g = groups.get(h.meeting_id)
            if g is None:
                g = groups[h.meeting_id] = MeetingHits(
                    h.meeting_id, h.title, h.started_at, h.tz_offset_minutes,
                    0, None, None, [], [])
            g.hit_count += 1
            if h.kind not in g.kinds:
                g.kinds.append(h.kind)
            if len(g.best) < 2:
                g.best.append(h)
            if h.start_seconds is not None:
                g.first_seconds = h.start_seconds if g.first_seconds is None else min(g.first_seconds, h.start_seconds)
                g.last_seconds = h.start_seconds if g.last_seconds is None else max(g.last_seconds, h.start_seconds)
        return list(groups.values())[offset:offset + limit]

    def _ranked_hits(self, query, from_date, to_date, tag, person, *,
                     title_limit, fetch) -> list[SearchHit]:
        # (the old body of search(), from the falsy-query guard to the final
        #  return, with these substitutions:)
        #   _meeting_hits(conn, where, params, [], "ASC", limit)
        #       -> _meeting_hits(conn, where, params, [], "ASC", title_limit)
        #   titled = self._meeting_hits(..., words, "DESC", limit)
        #       -> ... "DESC", title_limit)
        #   self._search(conn, match, where, params, limit * 4)
        #       -> self._search(conn, match, where, params, fetch)
        #   return (titled + collapse_echoes(hits))[:limit]
        #       -> return titled + collapse_echoes(hits)
```

  The comment block above is an instruction: move the existing code there with exactly
  those four substitutions. Keep every existing comment in that code.

- [ ] **Step 4: Run the library tests.** Run
  `.venv/bin/python -B -m pytest tests/test_meeting_search.py tests/test_meeting_library.py -q`.
  Expected: all pass, including the existing `test_limit_is_clamped` (28 < 100).

- [ ] **Step 5: Failing MCP tests.** Append to `tests/test_mcp_tools.py`:

```python
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


def test_search_meetings_rejects_non_boolean_by_meeting(tools):
    with pytest.raises(ToolError):
        tools["search_meetings"].run({"query": "x", "by_meeting": "yes"})
```

  `_seed`'s two segments both contain "budget", at 0 s and 5 s, so `hit_count == 2`. If
  echo collapsing merges them, check the segment texts ("Let's review the budget" and "The
  cafe budget is fine" differ enough that they won't merge).

- [ ] **Step 6: Implement in `mcp_tools.py`.** Add a coercion helper next to `_str_list`:

```python
def _bool(args, key, default=False):
    value = args.get(key, default)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ToolError(f"{key} must be true or false.")
    return value
```

  Replace the `search_meetings` handler body:

```python
    def _snippet(h):
        return {"kind": h.kind, "speaker": h.speaker, "at": _hms(h.start_seconds),
                "start_seconds": h.start_seconds, "snippet": _markdown(h.snippet)}

    def search_meetings(args):
        query = _text(args, "query", required=True, max_len=500)
        start, end = _range(args)
        limit = _int(args, "limit", 10, 1, 50)
        offset = _int(args, "offset", 0, 0, 1000)
        filters = dict(from_date=start, to_date=end, tag=_text(args, "tag"),
                       person=_text(args, "person"), limit=limit + 1, offset=offset)
        if _bool(args, "by_meeting"):
            rows = library.search_grouped(query, **filters)
            results = [{
                "meeting_id": g.meeting_id, "title": g.title,
                "meeting_start": _start(g.started_at, g.tz_offset_minutes),
                "hit_count": g.hit_count, "first_at": _hms(g.first_seconds),
                "last_at": _hms(g.last_seconds), "kinds": g.kinds,
                "snippets": [_snippet(h) for h in g.best],
            } for g in rows[:limit]]
        else:
            rows = library.search(query, **filters)
            results = [{
                "meeting_id": h.meeting_id, "title": h.title,
                "meeting_start": _start(h.started_at, h.tz_offset_minutes),
                "kind": h.kind, "speaker": h.speaker, "also_speakers": h.also_speakers,
                "at": _hms(h.start_seconds), "start_seconds": h.start_seconds,
                "snippet": _markdown(h.snippet),
            } for h in rows[:limit]]
        return {"results": results, "offset": offset,
                "next_offset": offset + limit if len(rows) > limit else None}
```

  `_snippet` must be defined inside `build_tools`, before `search_meetings`. Add to the
  schema properties:

```python
          "by_meeting": {"type": "boolean", "default": False,
                         "description": "One result per meeting (hit count, first/last "
                                        "time, best 2 snippets) instead of per passage."},
          "offset": {"type": "integer", "minimum": 0, "maximum": 1000, "default": 0},
```

  Leave the description text for Task 8; only the schema changes here.

- [ ] **Step 7: Run.** Run `.venv/bin/python -B -m pytest tests/test_mcp_tools.py tests/test_mcp_server.py tests/test_mcpb.py tests/test_meeting_search.py tests/test_meetings_bridge.py -q`.
  Expected: all pass. The manifest only mirrors name and description, which are unchanged,
  so `test_mcpb` passes without regeneration.

- [ ] **Step 8: Commit.**

```bash
git add speakeasy/meeting_library.py speakeasy/mcp_tools.py tests/test_meeting_search.py tests/test_mcp_tools.py
git commit -m "MCP search_meetings: by_meeting grouping and offset paging"
```

---

### Task 7: MCP M3, `pending_summaries` over a date range

**Files:**
- Modify: `speakeasy/meeting_library.py` (`_PENDING_SQL` area around line 99; `pending_summaries` around line 902)
- Modify: `speakeasy/mcp_tools.py` (`pending_summaries` handler, schema, description)
- Modify: `packaging/mcpb/manifest.json` (regenerate)
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Produces:
  - `MeetingLibrary.pending_summaries(limit=5, now=None, from_date=None, to_date=None)`.
    With either date set, the "recent" window is replaced by that range; explicit requests
    still come first.
  - MCP `pending_summaries` accepts `from`/`to`.

- [ ] **Step 1: Failing tests.** Append to `tests/test_mcp_tools.py`:

```python
def test_pending_summaries_catches_up_a_past_range(lib, tools):
    aug = [_seed(lib, day=d, title=f"A{d}") for d in (2, 3)]   # 2026-09-02/03, outside 7 days
    done = _seed(lib, day=4, title="Done")
    tools["save_notes"].run({"id": done, "summary": "TL;DR: x"})
    short = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0, 4, "hi")], duration_seconds=60, title="Short",
        started_at=datetime(2026, 9, 3, 12, 0, tzinfo=PDT)))
    requested = _seed(lib, day=10, title="Asked")
    lib.request_summary(requested)
    out = tools["pending_summaries"].run({"from": "2026-09-01", "to": "2026-09-05"})
    assert [m["id"] for m in out["meetings"]] == [requested, *aug]
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
```

  This assumes the test's `datetime.now()` is after 2026-09-17, so 2–4 Sep is outside the
  7-day window; `_seed`'s September dates already rely on this in existing tests. If the
  existing tests pin `now` differently, follow their pattern.

- [ ] **Step 2: Run; they fail.** Run `.venv/bin/python -B -m pytest tests/test_mcp_tools.py -q -k pending_summaries`.

- [ ] **Step 3: Implement in the library.** Below `_PENDING_SQL`, add:

```python
# pending_summaries(from_date/to_date): the same rule over a chosen range of
# local days instead of the last PENDING_WINDOW_DAYS (catching up older meetings).
_PENDING_RANGE_SQL = (
    "(r.meeting_id IS NOT NULL OR (m.started_at >= ? AND m.started_at < ?"
    " AND m.duration_seconds >= ?"
    " AND NOT EXISTS (SELECT 1 FROM notes n WHERE n.meeting_id = m.id"
    " AND n.summary <> '')))")
```

  Change `pending_summaries`:

```python
    def pending_summaries(self, limit=5, now=None, from_date=None,
                          to_date=None) -> list[tuple[str, bool]]:
        """[(meeting_id, requested)], explicit requests first (oldest request
        first), then unsummarised meetings oldest first: recent ones, or those
        in [from_date, to_date] (local days) when either is given."""
        limit = max(1, min(int(limit), 10))
        if from_date or to_date:
            lower, upper = local_day_bounds(from_date, to_date)
            sql, params = _PENDING_RANGE_SQL, [lower or "", upper or "9999", PENDING_MIN_SECONDS]
        else:
            sql, params = _PENDING_SQL, self._pending_params(now)
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT m.id, r.meeting_id IS NOT NULL AS requested FROM meetings m"
                " LEFT JOIN summary_requests r ON r.meeting_id = m.id"
                f" WHERE {sql}"
                " ORDER BY requested DESC, r.requested_at, r.rowid, m.started_at, m.id"
                " LIMIT ?", (*params, limit)).fetchall()
        return [(r["id"], bool(r["requested"])) for r in rows]
```

- [ ] **Step 4: Implement in MCP.** In the `pending_summaries` handler, change the first
  lines to:

```python
        limit = _int(args, "limit", 5, 1, 10)
        start, end = _range(args)
        meetings = []
        for meeting_id, requested in library.pending_summaries(
                limit=limit, from_date=start, to_date=end):
```

  Schema: add `"from": _FILTERS["from"], "to": _FILTERS["to"]` to its properties. Replace
  the description with:

  > "Meetings waiting for a summary (new ones from the last 7 days, plus any the user asked to summarise or redo), with the summary format to use. To catch up older meetings when the user asks (e.g. 'summarise everything from August'), pass from/to: unsummarised meetings in that range come back oldest first; call again until none are left. For each: read the whole transcript with get_transcript, then save the summary, action items and tags with save_notes."

- [ ] **Step 5: Regenerate the manifest; run the tests.** Run
  `.venv/bin/python -B -m pytest tests/test_mcp_tools.py tests/test_mcpb.py tests/test_meeting_library.py -q`.
  Expected: all pass, including the existing `test_meeting_library.py:546` (limit 50 → 10).

- [ ] **Step 6: Commit.**

```bash
git add speakeasy/meeting_library.py speakeasy/mcp_tools.py packaging/mcpb/manifest.json tests/test_mcp_tools.py
git commit -m "MCP pending_summaries: optional from/to to catch up older meetings"
```

---

### Task 8: MCP M4, search guidance in the description

**Files:**
- Modify: `speakeasy/mcp_tools.py` (`search_meetings` description)
- Modify: `packaging/mcpb/manifest.json` (regenerate)
- Test: `tests/test_mcp_tools.py`

- [ ] **Step 1: Failing test.**

```python
def test_search_description_teaches_recall_strategy(tools):
    d = tools["search_meetings"].definition()["description"]
    for phrase in ("one or two distinctive terms", "synonyms", "by_meeting", "from/to",
                   "get_meeting", "get_transcript", "next_offset"):
        assert phrase in d, phrase
```

- [ ] **Step 2: Run; it fails.**

- [ ] **Step 3: Replace the `search_meetings` description** with:

  > "Search meetings by title, date and content: transcripts, summaries and the user's own notes (kind user_notes). A date in the query (e.g. 'Oct 3', '3 October 2026', '2026-10-03', 'today', 'yesterday') limits results to that day; a date alone lists that day's meetings. Title matches come first as kind meeting; content matches return ranked snippets (matches in **bold**) with the meeting id and time offset. Every word must appear in the same passage, so search one or two distinctive terms at a time and try synonyms (e.g. API, endpoint, REST, GraphQL) as separate searches. To answer 'when and why did we decide X': search with by_meeting=true to see which meetings discussed it and when, narrow with from/to, then read get_meeting (summary Decisions, the user's notes) and get_transcript around start_seconds. Pass next_offset as offset for more."

- [ ] **Step 4: Regenerate the manifest; run** `.venv/bin/python -B -m pytest tests/test_mcp_tools.py tests/test_mcpb.py -q`.
  Expected: all pass.

- [ ] **Step 5: Commit.**

```bash
git add speakeasy/mcp_tools.py packaging/mcpb/manifest.json tests/test_mcp_tools.py
git commit -m "MCP search_meetings: describe the recall strategy (terms, synonyms, by_meeting)"
```

---

### Task 9: Full suite, real-app look, real-MCP probe, docs

**Files:**
- Modify: this plan (tick boxes; record results under "Results")
- Modify: `docs/superpowers/specs/2026-10-03-scalable-meeting-list-design.md`. Note the
  signature change: `visibleRows(sections, state, expandAll)` replaced `openKeys`. Also note
  that grouped search fetches 2,000 passages per source.

- [ ] **Step 1: Full suites.** Run `.venv/bin/python -B -m pytest -q` (timeout 600000 ms),
  then `cd frontend && npm run build && npm test`. Expected: all pass. Record the counts.

- [ ] **Step 2: Real app with seeded data.**
  - Build and launch the installed app with a temporary HOME, following the memory notes
    "Installed-app UI checks" and "Worktree session guards". Seed more than 600 meetings
    spanning 2025–2026 into that temp library with a short script in the scratchpad (never
    the real library).
  - Note: `build_app.sh --install` stops Claude Desktop's Speakeasy connector. Tell the user
    to toggle it back on afterwards.
  - Check:
    - Today header anchored at narrow and wide sizes;
    - list sections and counts;
    - pinned headers in light and dark mode;
    - a search hit in 2025 opens `2025 › <month>` and scrolls to it;
    - a Tag filter shows everything open;
    - the open state survives a relaunch.
  - Take screenshots.

- [ ] **Step 3: Real MCP probe (read-only).** After installing, with the connector re-enabled,
  run through Claude Code's `speakeasy` MCP tools:
  - `list_meetings` with limit 100 → record the character count (target < 25,000);
  - `search_meetings` with `{"query": "API", "by_meeting": true, "limit": 20}` → record the
    count and size;
  - `pending_summaries` with `{"from": "2026-08-01", "to": "2026-08-31"}` → only check that
    it lists meetings. Don't save notes.

- [ ] **Step 4: Results and TODO.** Add a `## Results` section to this plan with the test
  counts, sizes and screenshot notes. Make sure the TODO below is still accurate. Commit:

```bash
git add docs/
git commit -m "Docs: scalable meeting list results"
```

## TODO (outside this plan)

- Speaker separation produced 144 speakers in one meeting. Investigate over-splitting
  (`diarization`).
- Meaning-based search, if keyword search plus the described strategy proves insufficient.
