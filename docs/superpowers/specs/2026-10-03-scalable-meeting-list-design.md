# Scalable meeting list, anchored Today header, MCP recall review — design

Date: 2026-10-03. Status: approved in chat, awaiting written-spec review.

## Intent

The user records roughly 100 meetings a quarter (the latest 100 span 9 Jul – 3 Oct 2026).

- **Browse by time** in the Meetings window, mostly within the current year, without
  scrolling forever. Tags and People filters already cover browsing by subject and person.
- **Deep recall goes through the MCP server from Claude Desktop**, for example: "Why did I
  choose this API direction, when, and what were the considerations?" The MCP tools must
  support that end to end.
- **Window layout:** the Today title stays anchored left and Start Meeting stays in the
  right corner as the window resizes.

Out of scope:
- meaning-based search (would need an offline embedding model);
- speaker-separation over-splitting (see TODO);
- any new network dependency (the app must stay fully offline).

## Part 1 — Today header and content alignment

Cause: `.header` in `frontend/src/meetings/TodayView.module.css` has `max-width: 760px;
margin: 0 auto`, so the header centres in wide windows. `.upcoming` has no cap and runs
full width, so the two disagree.

Change:

- `.header`:
  - full pane width (`max-width: none; margin: 0`), padding `18px 24px 6px`;
  - `justify-content: space-between`, so the title sits at the left edge and Start Meeting in
    the right corner.
- Content blocks (`.banner`, `.hero`, `.quiet`, `.agenda`, `.foldRow`, `.earlier`,
  `.upcoming`):
  - left-aligned on the same 24px left edge as the title (`margin-left: 0`, not `auto`);
  - keep a 760px reading cap (712px for the inset cards, as today), now measured from the
    left edge;
  - `.upcoming` gets the same cap.
- Check at the narrowest window: the title and button must not overlap (button `flex-shrink: 0`).

## Part 2 — Sectioned meeting list

### Data (bridge)

- `MeetingsBridge.list_payload` (`speakeasy/ui/meetings_bridge.py`): remove `limit=500` and
  return every meeting.
  - Today the window silently drops everything past the 500 newest. At ~400/year that limit
    is reached within about a year.
  - Payload is about 300 bytes per meeting, so 3,000 meetings ≈ 1 MB. That is acceptable;
    rendering is the cost, and collapsed sections render nothing.
- Each meta gains `startDate: "YYYY-MM-DD"`: the meeting's local calendar day.
  - `dayLabel` stays (search results and other callers use it).
  - Mirror the field in `frontend/src/mock/meetings.ts` (`MeetingMeta`) and the mock data.

### Section model: `frontend/src/meetings/listSections.ts` (new, pure)

`buildSections(metas, today: string /*YYYY-MM-DD*/) -> Section[]`

The input is sorted newest first, as the bridge sends it. Weeks start on Monday.

| Section key       | Label            | Contains (local day d)                                 | Default | Inner grouping              |
|-------------------|------------------|--------------------------------------------------------|---------|-----------------------------|
| `today`           | Today            | d = today                                              | open    | none                        |
| `yesterday`       | Yesterday        | d = today − 1                                          | open    | none                        |
| `this-week`       | This week        | Monday of this week ≤ d < yesterday                    | open    | day (`Wednesday`)           |
| `last-week`       | Last week        | the previous Monday–Sunday, excluding days above       | open    | day (`Thursday 25 Sep`)     |
| `m-YYYY-MM`       | `August` (year omitted for the current year) | rest of that month, current year | collapsed | week (`Week of 4 Aug`) |
| `y-YYYY`          | `2025`           | every meeting in an earlier year                       | collapsed | month (collapsible `m-2025-08`, label `August 2025`) |

Rules:
- A meeting belongs to the first section that matches, top to bottom.
- Empty sections are omitted.
- The current month's remainder (days older than last week but still in this month) is its
  own `m-YYYY-MM` section.
- Every section header shows a count of the meetings it contains.
- A meeting dated after today (clock skew) goes into `today`.
- `Week of <Monday>`: the Monday may fall in the previous month. The label shows that real
  Monday, but the meeting stays in the month of its own day.

`visibleRows(sections, openKeys: Set<string>) -> MeetingMeta[]`
: rows in display order, excluding rows inside collapsed sections or collapsed year-months.

`sectionPathFor(sections, id) -> string[]`
: the keys that must be open for a row to be visible (for example `['y-2025', 'm-2025-08']`).

### MeetingList rendering

- Section headers:
  - a disclosure button (`aria-expanded`) with chevron, label and count;
  - pinned to the top while scrolling (`position: sticky; top: 0`, with the sidebar background
    so rows don't show through);
  - inner group headers (day/week) are plain text and not pinned.
- Collapsed sections render no rows.
- Open state:
  - starts from the defaults above;
  - user toggles are stored in localStorage under `meetingList.sections` (a JSON map of
    key → open), using the same try/catch pattern as `Sidebar.readOpen`;
  - a stored key for a section that no longer exists is ignored.
- When a Tag or Person filter is active (the filtered list is short), every section and
  year-month is shown open. Stored state is neither read nor written while filtered.
- **Selection:** when `selectedId` changes to a row that isn't visible (from search, Today,
  deep link or navigation), open every key in `sectionPathFor` (and store that state), then
  `scrollIntoView` as today.
- **Keyboard:**
  - ArrowUp/ArrowDown move through `visibleRows`, not all metas;
  - ⌘F and ⌘⌫ behave as now;
  - Left/Right arrow are not added (YAGNI).
- The listbox semantics stay as they are: header buttons sit outside the option flow.

### Deletion and live updates

`buildSections` is recomputed from `metas` on every render (memoised on metas plus today's
date). Today's date is read once per minute from the clock App already ticks, so sections roll
over at midnight.

## Part 3 — MCP tools for recall

Evidence comes from read-only probes of the real library on 2026-10-03.

### M1 — compact `list_meetings` rows

Problem: a 100-row page was 74,301 characters (about 745 per meeting, about 500 of them
anonymous `Speaker N` labels; one meeting has 144). Claude Desktop rejects a result that size.

Change (`speakeasy/mcp_tools.py`, `list_meetings`):
- Replace `speakers` with:
  - `named_speakers`: speakers that are not a bare `Speaker <n>` label, so it keeps `You` and
    real names;
  - `speaker_count`: the number of distinct speakers.
- `get_meeting` keeps the full `speakers` list.
- The description says so.
- Target: a 100-row page of the real library under 25,000 characters, measured as part of
  acceptance.

### M2 — search by meeting, with paging

Problem: in the top 50 passages for "API", 7 came from one meeting and 6 from another. There
is no offset, so nothing past 50 is reachable, and Claude cannot list "which meetings
discussed X".

Change (`search_meetings`):

New argument `by_meeting: boolean` (default false). When true, return one row per meeting,
ranked by its best passage (each source's rank, interleaved the same way as now):

```
{"meeting_id", "title", "meeting_start", "hit_count",
 "first_at", "last_at",            # hh:mm:ss of the earliest/latest transcript hit, or null
 "kinds": [...],                   # which of meeting/transcript/notes/user_notes matched
 "snippets": [ {kind, speaker, at, start_seconds, snippet} ]  # best 2
}
```

- Hit counts consider up to 2,000 passages per query, so they stay cheap.
- `limit` keeps its 1–50 range and counts meetings in this mode.

New argument `offset` (0–1000, default 0), available in both modes. The response gains
`next_offset` (null at the end), as `list_meetings` has.

Library: `MeetingLibrary.search` gains `offset` and `by_meeting`, or a sibling method
`search_meetings_grouped`; the implementer may choose. The existing callers (the window's
search, `limit=50`) are unchanged.

### M3 — summary catch-up for older meetings

Problem: only 7 of the latest 100 meetings have summaries, and summaries carry the
`## Decisions` section that best answers "why/when did I decide". `pending_summaries` only
offers the last 7 days.

Change:
- `pending_summaries` accepts optional `from`/`to` (local days, as elsewhere).
- With a range, it returns unsummarised meetings in that range (with the same minimum
  duration as now), oldest first, after any explicit requests.
- Without a range, behaviour is unchanged.
- The description tells Claude it can catch up a period this way when the user asks, for
  example "summarise everything from August".
- The `limit` cap of 10 per call stays; Claude calls again until the list is empty.

### M4 — search guidance in the description

Problem: all words must occur in one passage. When that matches anything, the any-word
fallback never runs: "API decision" found 3 passages. Word stems also differ ("decide" does
not match "decision").

Change: the `search_meetings` description adds guidance to:
- search with one or two distinctive terms;
- try synonyms (for example REST, endpoint, GraphQL);
- use `by_meeting` to find which meetings and when;
- narrow with `from`/`to`;
- then read `get_meeting` (summary Decisions) and `get_transcript` around `start_seconds`.

### Description mirror

Every changed tool description and schema must also be updated in
`packaging/mcpb/manifest.json`; `tests/test_mcpb.py` enforces this.

## Testing

Frontend (`node --test`, pure modules only):
- `frontend/tests/listSections.test.ts` covers:
  - Monday week start, including when today is a Monday and when it is a Sunday;
  - yesterday on a Monday;
  - the last-week boundary;
  - the current month's remainder;
  - a week whose Monday is in the previous month;
  - New Year (today = 2 Jan; December goes into `y-<last year>`);
  - empty sections omitted;
  - counts;
  - future-dated meetings;
  - `visibleRows` with collapsed sections and year-months;
  - `sectionPathFor`.

Python:
- `tests/test_meetings_bridge.py`: more than 500 meetings all returned; `startDate` is the
  local day.
- `tests/test_mcp_tools.py` / `tests/test_meeting_library_mcp.py`:
  - `named_speakers`/`speaker_count`;
  - `by_meeting` grouping, counts, first/last, two snippets, ranking;
  - `offset` and `next_offset` in both modes;
  - `pending_summaries` with and without a range;
  - requested meetings first.
- `tests/test_mcpb.py` passes.

Process and acceptance:
- Reviewers verify by mutation (break the code; the suite must notice).
- Run with `python -B` and clear `__pycache__`, with HOME isolated (see memory).
- Launch the real app with a temporary HOME, look at the Today header at narrow and wide
  sizes, and look at the list with seeded data spanning more than one year.
- Re-run the API question through the real MCP server and report result sizes.

## TODO (not in this work)

- Speaker separation produced 144 speakers in one meeting (anonymous labels dominate
  `list_meetings`). Investigate over-splitting separately.
- Meaning-based (embedding) search, if keyword search plus Claude's multi-query strategy
  proves insufficient.
