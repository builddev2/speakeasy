# Meeting search by title and date; light-mode floating sidebar — design

Date: 3 October 2026. Source: the Follow-ups section of
[the recording-pill plan](../plans/2026-10-03-recording-pill.md), brainstormed in
chat with the user (the sidebar direction was picked from mockups, option C).

## Problem

1. **Search ignores titles and dates.** Searching "meeting" or "Oct 3" finds
   nothing, even for "Test Meeting — Oct 3, 7:58 PM". `MeetingLibrary.search`
   queries only the transcript, summary and user-notes FTS tables.
2. **The light-mode Meetings sidebar looks bad** ("gray side bar looks really
   bad", user). In light mode the sidebar is `rgba(0,0,0,0.025)` over the
   native `UnderWindowBackground` blur, so it reads as muddy grey that changes
   with the wallpaper.

## Decisions

| Topic | Decision |
|---|---|
| Scope of search change | In `MeetingLibrary.search`, so both the Meetings window and Claude's `search_meetings` MCP tool get it. |
| Date in a query | A date narrows results to that local day; the remaining words search within it. No fallback to other days. |
| Date-only query | Lists every meeting that day, earliest first. |
| Ranking | Title hits first (newest first), then content hits ranked exactly as today. |
| Sidebar | Option C, "floating frosted panel", **light mode only**. Dark mode unchanged. |
| Style | Minimal, clean, modern (user). |

## 1. Search

### 1.1 Date phrase parsing

A new pure function `parse_date_phrase(text, today) -> (date | None, rest_text)`
in a new module `speakeasy/search_dates.py` (pure, no I/O; `today` injected so
tests are deterministic). It finds at most **one** date phrase, removes it,
and returns the remaining text with whitespace collapsed. The first phrase in
the text wins; any later date phrase stays as ordinary words.

Recognised (case-insensitive; word boundaries on both sides):

| Form | Examples |
|---|---|
| Month day | `Oct 3`, `October 3`, `oct 3rd`, `Sept 3`, `Oct. 3` |
| Day month | `3 Oct`, `3rd October`, `03 oct` |
| Either, with a year | `Oct 3 2026`, `Oct 3, 2026`, `3 Oct 2026`, `3rd October, 2026` |
| ISO | `2026-10-03` |
| Relative | `today`, `yesterday` |

- Month names: full English names and the three-letter abbreviations, plus
  `sept`, each optionally followed by `.`. Day ordinal suffixes `st`, `nd`,
  `rd`, `th` are accepted (no check that the suffix agrees with the number).
- Year: four digits, 1900–2999. With no year, the phrase means the **most recent
  such day that is not after `today`**: `Oct 3` on 2026-10-03 is 2026-10-03;
  `Dec 20` on 2026-01-05 is 2025-12-20. `Feb 29` with no year means the most
  recent valid 29 February.
- An impossible date (`Feb 30`, `2026-13-01`) is **not** a date phrase; the
  text is left untouched.
- Deliberately **not** dates (they collide with real words and titles): a bare
  month (`May`, `March`), weekday names (`Monday standup`), numeric slash or
  dot forms (`10/3`, `3.10`), and a bare number.

`today` is the local date (`date.today()`) passed in by the caller.

### 1.2 Matching and ordering in `MeetingLibrary.search`

`search(query, *, from_date, to_date, tag, person, limit)` keeps its signature.

1. If `query` is falsy, return `[]` (unchanged).
2. `day, words = parse_date_phrase(query, today)`. If `day` is set, the
   filters get an extra local-day bound `[day, day+1)` that is ANDed with any
   `from_date`/`to_date` (both apply; an empty intersection gives no results).
3. **Date only** (`day` set and `words` has no `\w` characters): return the
   meetings on that day matching the other filters, ordered by `started_at`
   ascending, as `kind="meeting"` hits with `snippet` = the plain title, up to
   `limit`.
4. **Words** (with or without a day):
   - **Title hits:** meetings whose title contains every whitespace-separated
     word of `words` (case-folded substring, via the existing
     `meeting_filters(title=words)`), ordered by `started_at` descending, one
     hit per meeting, `kind="meeting"`. The snippet is the title with each
     case-insensitive occurrence of each word wrapped in `HIT_OPEN`/`HIT_CLOSE`
     (overlapping occurrences merged).
   - **Content hits:** the existing FTS search over `words` (AND, then the OR
     fallback, then the per-kind interleave and `collapse_echoes`), with the
     day bound added to its filters. Unchanged otherwise.
   - Result: title hits, then content hits, truncated to `limit`. A meeting can
     appear as both a title hit and content hits.
5. If `words` produces no FTS terms (punctuation only) and there is no day,
   return `[]` (unchanged behaviour).

`SearchHit.kind` gains `"meeting"`: `speaker`, `start_seconds`,
`end_seconds`, `segment_index` are `None`, `also_speakers` is `[]`, `score` is
`0.0`.

### 1.3 Meetings window

- `MeetingsBridge.search_payload` is unchanged apart from passing `kind`
  through (it already does).
- `SearchResults.tsx`: for `kind === 'meeting'`, the title line renders
  `result.parts` (the highlighted title) instead of `result.title`, and there
  is no speaker line or snippet line. The day · time label stays.
- The frontend mock (`src/mock/meetings.ts`) gains one `kind: 'meeting'`
  result so the dev view shows it.

### 1.4 Claude (MCP)

- `search_meetings` output is unchanged in shape; `kind` may now be
  `"meeting"`, with `at`/`start_seconds` null and `snippet` the title with
  matches in `**bold**`.
- The tool description becomes: "Search meetings by title, date and content:
  transcripts, summaries and the user's own notes (kind user_notes). A date in
  the query (e.g. 'Oct 3', '3 October 2026', '2026-10-03', 'today',
  'yesterday') limits results to that day; a date alone lists that day's
  meetings. Title matches come first as kind meeting; content matches return
  ranked snippets (matches in **bold**) with the meeting id and time offset."
- The same text goes into `packaging/mcpb/manifest.json` (`test_mcpb`
  enforces the mirror).

## 2. Light-mode sidebar: floating frosted panel

Light mode only; dark mode must render exactly as today.

### 2.1 Tokens (`frontend/src/styles/tokens.css`)

New tokens, defined in the dark `:root` block with values that reproduce the
current look, and redefined in the light block:

| Token | Dark (= today) | Light |
|---|---|---|
| `--window-canvas` | `transparent` | `#ECEEED` |
| `--sidebar-bg` | `var(--glass-sidebar)` | `rgba(255,255,255,0.62)` |
| `--sidebar-inset` | `0px` | `6px` |
| `--sidebar-radius` | `0px` | `11px` |
| `--sidebar-shadow` | `none` | `0 0 0 0.5px rgba(0,0,0,0.08), 0 4px 14px -6px rgba(20,30,25,0.18)` |
| `--sidebar-divider` | `var(--hairline-lo)` | `transparent` |
| `--sidebar-opaque` | `var(--surface-opaque)` | `#F7F7F6` |
| `--row-active-bg` | `var(--selection)` | `#FFFFFF` |
| `--row-active-shadow` | `none` | `0 0 0 0.5px rgba(0,0,0,0.08), 0 1px 3px rgba(0,0,0,0.06)` |
| `--search-fill` | `var(--glass-fill-2)` | `rgba(0,0,0,0.045)` |
| `--search-border` | `var(--hairline)` | `transparent` |
| `--sidebar-divider-contrast` | `var(--hairline)` | `transparent` |
| `--sidebar-shadow-contrast` | `none` | `0 0 0 0.5px var(--hairline), 0 4px 14px -6px rgba(20,30,25,0.18)` |

### 2.2 Styles

- `GlassPanel` is shared with other windows, so it is not changed. Inside it,
  the Meetings `App.tsx` wraps `<TitleBar>` and the `.split` in a new
  `<div className={styles.window}>` (`height: 100%`, `background:
  var(--window-canvas)`). In dark mode the canvas is transparent, so nothing
  changes. In light mode the opaque canvas covers the title bar and the area
  around the sidebar, which hides the native blur and the wallpaper. The detail
  pane is already opaque (`--surface-content`).
- `.sidebar` uses `--sidebar-bg`, `margin: var(--sidebar-inset) 0
  var(--sidebar-inset) var(--sidebar-inset)`, `border-radius:
  var(--sidebar-radius)`, `box-shadow: var(--sidebar-shadow)`, and
  `border-right: 0.5px solid var(--sidebar-divider)`. Its height becomes
  `calc(100% - 2 * var(--sidebar-inset))`; the width clamp is unchanged.
- `.rowActive` uses `--row-active-bg` and `--row-active-shadow`. Hover stays
  `--glass-fill-2`.
- `.searchInput` uses `--search-fill` and `--search-border`; focus keeps
  `border-color: var(--accent)`.
- No literal colours in any `*.module.css` (Quiet Library rule).
- `prefers-reduced-transparency: reduce`: the sidebar uses `--sidebar-opaque`
  (dark: unchanged `--surface-opaque`).
- `prefers-contrast: more`: the existing rule that switches the sidebar's
  right border to `--hairline` now sets `border-right-color:
  var(--sidebar-divider-contrast)` and `box-shadow:
  var(--sidebar-shadow-contrast)`. Dark: `var(--hairline)` and `none` (as
  today). Light: `transparent` and `0 0 0 0.5px var(--hairline), 0 4px 14px
  -6px rgba(20,30,25,0.18)`.
- The panel sits below the 34 px title bar, which keeps the traffic lights and
  the "Meetings" title on the canvas. The light frost is visual only: the canvas behind the
  panel is opaque, so there is no live blur in light mode.

### 2.3 Contrast

The existing contrast test (`tests/test_frontend_tokens.py`) is extended to the panel's effective light
background (`rgba(255,255,255,0.62)` over `#ECEEED`, ≈ `#F7F8F7`) and the white
active chip: ≥ 4.5:1 for `--text-hi`, `--text`, `--text-mid`; ≥ 3:1 for
`--text-lo`. If a value fails, the plan adjusts the token, not the threshold.

## 3. Testing and done

- **Python (test-first):**
  - `parse_date_phrase`: table of every recognised form, year inference across
    a year boundary, Feb 29, impossible dates, and the not-a-date forms;
  - library search: title hit ordering before content hits; date-only listing
    ordered earliest first; date and words together; date ANDed with
    `from_date`/`to_date`; empty result when the day has no match; highlight
    markers in title snippets;
  - MCP: `kind: "meeting"` with null offsets and bold title; description
    mirror.
- **Frontend:** `npm run build` plus `tests/test_frontend_tokens.py` (tokens and contrast); the dark token
  values equal today's.
- **Done** = installed app launched and looked at in light and dark: "meeting"
  and "Oct 3" find "Test Meeting — Oct 3"; the light sidebar matches option C;
  dark is unchanged. Tests alone are not done.

## Out of scope

Weekday and month-range queries, numeric dates, fuzzy title matching
("standup" vs "Stand-up"), and any change to the dark sidebar or to the Today
view's layout.
