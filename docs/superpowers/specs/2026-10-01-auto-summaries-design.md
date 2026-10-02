# Automatic meeting summaries + legible summary layout — design

Date: 2026-10-01. Status: awaiting approval.

**Depends on:** `docs/superpowers/plans/2026-10-01-meeting-tags.md` (bc50812).
Execute this only after that plan is merged to master; see "Coordination
with the meeting-tags plan" below.

## Problem

1. **Unreadable layout.** `MeetingDetail.tsx` renders `detail.summary` in one
   `<p>`, so Claude's headings, bullets and line breaks collapse into a wall
   of text (seen on meeting `20261001-165512-f167`, whose stored summary has
   sections and `- ` bullets).
2. **Wordy content.** Neither the "Copy prompt" text nor `save_notes`
   says what shape a summary should have, so Claude writes long prose,
   speaker-mapping notes and transcript-error notes.
3. **Manual workflow.** The user must copy a prompt and paste it into Claude
   for every meeting.

## Constraint

Speakeasy makes **no network calls at runtime, ever** (AGENTS.md). The app
therefore never calls Claude. Summaries are written by Claude, outside the
app, through the existing local MCP connector. (User decision 2026-10-01:
"Claude task picks up", over an on-device model or an in-app API call.)

## Design

### A. Summary format (single source: `speakeasy/summary_format.py`)

`SUMMARY_INSTRUCTIONS` (a module constant) is the format, returned to Claude
by the new MCP tool and referenced by the `save_notes` description:

```
TL;DR: 1–2 sentences: what the meeting was for and where it landed.

## Decisions
- One line each. Write "- None" if nothing was decided.

## Key points
- At most 6, one line each: only what matters later.

## Open questions
- Unresolved issues, risks, and ideas parked for later.
```

Rules in the same text: about 150 words in the summary; no speaker-mapping
or transcript-error notes; plain names, no "Speaker 3"; action items go in
`action_items` as `Owner — task (due)` (owner "Unassigned" if none), not in
the summary; parked ideas go under Open questions; tags: call `list_tags`
first and reuse existing tags (any spelling or alias matches); create a new
tag only for a genuinely new topic, at most 3 new per meeting (save_notes
rejects more and saves nothing, so on that error drop new tags and save
again); 3–8 tags in total; meetings under 2 minutes
with almost no content get `TL;DR: Too short to summarise.` and no sections.

### B. Legible layout (parse in Python, render in React)

`summary_format.parse_summary(text) -> list[dict]` turns stored text into
blocks the bridge sends as `detail.summaryBlocks`:

- `{"kind": "tldr", "text": …}` — a first line starting `TL;DR:`
  (case-insensitive), prefix removed.
- `{"kind": "heading", "text": …}` — a line starting `#`/`##`/`###`, **or**
  an ALL-CAPS line of ≤ 60 chars with at least 3 letters and no lowercase
  letters (e.g. `CORE CONCEPT (consensus)` counts once the parenthesised
  part is ignored for the caps test), so existing summaries improve too.
  A trailing `:` is removed.
- `{"kind": "bullets", "items": [...]}` — consecutive lines starting `- `,
  `* ` or `• `; a non-bullet line that directly follows a bullet (no blank
  line) is appended to that bullet (wrapped text).
- `{"kind": "para", "text": …}` — any other run of non-blank lines, joined
  with spaces.
- `**bold**` markers are stripped from all text (no inline markup is rendered).

React renders each block; no HTML is ever injected (`dangerouslySetInnerHTML`
is not used). Spacing (`MeetingDetail.module.css`):

| Element | Value |
|---|---|
| TL;DR block | 14px/1.55, weight 500, `var(--text)`, 12px padding, 10px radius, subtle tinted background, margin-bottom 20px |
| Heading | 11px uppercase, 0.06em tracking, weight 600, `var(--text-lo)`; margin-top 22px (0 if first), margin-bottom 8px |
| Bullet list | gap 6px between items; 13px/1.5; dot marker like action items |
| Paragraph | 13px/1.55, margin 0 0 12px |
| Action items heading | margin-top 24px (was 16px) |

`summary` (raw text) stays in the payload for Copy/Export.

### C. Automatic pick-up queue

**Schema v4:** new table, appended to `_MIGRATIONS` after the tags plan's
`(3, _migrate_v3)` as a plain idempotent script `(4, _SCHEMA_V4)`;
`SCHEMA_VERSION = 4`:

```sql
CREATE TABLE IF NOT EXISTS summary_requests (
    meeting_id TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
    requested_at TEXT NOT NULL
);
```

**A meeting is pending when** it has no summary and started within the
last 7 days and lasted ≥ 2 minutes (automatic), **or** it has a row in
`summary_requests` (explicit; any age/length, and even if it already has a
summary — that is "Redo summary"). Older unsummarised meetings are not
picked up automatically, so the existing ~100-meeting backlog does not
spend usage; the user summarises those with the button.

**Library** (`MeetingLibrary`):
- `request_summary(meeting_id)` — upsert the row (raises `MeetingNotFound`).
- `pending_summaries(limit=5, now=None) -> list[MeetingSummary-like]` —
  explicit requests first (oldest request first), then automatic ones
  (oldest meeting first).
- `save_notes(..., summary=…)` with a non-empty summary deletes the
  meeting's `summary_requests` row in the same transaction.
- `summary_status(meeting_id) -> "queued" | None` for the bridge.

**MCP tool** `pending_summaries` (read-only):
`{"limit": 1–10, default 5}` → `{"meetings": [{id, title, start,
duration_minutes, speakers, tags, has_summary, requested}], "instructions":
SUMMARY_INSTRUCTIONS}`. No tag list is duplicated here: the instructions
tell Claude to call `list_tags` (which after the tags plan returns names,
counts, descriptions and aliases). Description: "Meetings
waiting for a summary (new ones from the last 7 days, plus any the user
asked to (re)summarise), with the summary format to use. Summarise each with
get_transcript, then save_notes." Mirrored in `packaging/mcpb/manifest.json`. Tool order becomes the tags
plan's list plus `pending_summaries` last: `list_meetings, get_meeting,
search_meetings, get_transcript, get_calendar, list_tags, list_people,
save_notes, tag_meetings, manage_tags, pending_summaries`.

`save_notes` keeps the tags plan's description and appends one sentence:
" Write summaries in the format pending_summaries returns." Its `summary`
property gains the description: "Use the format from
pending_summaries: TL;DR line, then ## Decisions, ## Key points, ## Open
questions."

**Bridge/UI:**
- New bridge method `meetings.requestSummary {id}` → calls
  `request_summary`, returns the refreshed detail payload.
- Detail payload gains `summaryQueued: bool`.
- No-summary state: text "No summary yet." plus button **Summarise**
  (replaces "Copy prompt"). When queued: "Queued — Claude will summarise
  this the next time it checks (every 30 minutes while the Claude app is
  open)." and the button is disabled.
- `···` menu gains **Redo summary** when a summary exists; when queued, a
  small "Queued for a new summary" note shows above the summary.
- Meetings in the automatic window show the same "Queued" text without the
  user pressing anything (`summaryQueued` is true for anything
  `pending_summaries` would return, not just explicit rows).

### D. The Claude scheduled task (user-side setup, not app code)

Created with the Claude Desktop scheduled-tasks tool after the app change is
installed: task id `speakeasy-auto-summaries`, cron `*/30 * * * *` (local
time; also runs on next launch if Claude was closed). Prompt, self-contained:

> Using the Speakeasy Meetings connector: call pending_summaries. If it
> returns no meetings, stop and reply "Nothing to summarise." For each
> meeting: read the full transcript with get_transcript (follow next_cursor
> until done), write the summary, action items and tags exactly as the
> returned instructions say, and save them with save_notes. Do not read or
> change any other meeting. Reply with one line per meeting saved.

`notifyOnCompletion: false`. The runs use the user's Claude plan usage; at
most 5 meetings per run bounds it.

## Coordination with the meeting-tags plan

Checked against session "Meeting tag process review" (spec dc15f14, plan
bc50812). No design conflicts; these overlaps are resolved by **running
that plan first** and building this one on top:

| Overlap | Resolution |
|---|---|
| Schema version: tags plan takes v3 (Python migration) | This takes v4, appended after it |
| `MeetingLibrary.save_notes` rewritten for tag provenance | This only adds one `DELETE FROM summary_requests` in the same transaction, after the tags plan's version |
| `save_notes` caps new tags at 3 and raises without saving | Instructions say reuse tags, ≤ 3 new, retry without new tags on that error |
| `list_tags` output changes shape | `pending_summaries` does not return tags; Claude calls `list_tags` |
| MCP tool list/order test and manifest | `pending_summaries` appended after `manage_tags`; read-only |
| `save_notes` description rewritten | One sentence appended to the tags plan's string, mirrored |
| `MeetingDetail.tsx:592` "Copy prompt" text edited by the tags plan | This removes the Copy prompt button (replaced by Summarise); the tags plan's edit is simply superseded |

Behaviour fit: "Redo summary" re-runs `save_notes(tags=…)`, which under the
tags plan replaces only Claude's own tags, never the user's, and skips tags
the user removed, so automatic re-summarising cannot undo the user's tag
choices. The tags plan's phase 2 (tag editor UI, rule tagging) does not
touch summaries.

## Out of scope

- Any network call from the app; triggering Claude from the app instantly
  (the button queues; the next 30-minute run picks it up).
- Inline markdown beyond headings/bullets/TL;DR; editing summaries in the app.
- Tag canonicalisation (the meeting-tags spec).

## Testing

- `tests/test_summary_format.py`: parse of the new format; parse of the
  stored f167-style legacy text (ALL-CAPS headings, `- ` bullets, prose
  first paragraph); wrapped bullet continuation; `**` stripping; empty text.
- `tests/test_meeting_library.py`: request/pending ordering, 7-day and
  2-minute window, explicit request overrides window, save_notes clears the
  request, delete cascades, migration from v3 (post-tags) on a populated DB.
- `tests/test_mcp_tools.py` + manifest test: new tool name/order/schema,
  description mirror.
- `tests/test_meetings_bridge.py`: `summaryBlocks`, `summaryQueued`,
  `meetings.requestSummary`.
- Reviewer mutations: break the ALL-CAPS heading rule, the 7-day window,
  and the clear-on-save; the suite must fail each time.
- Done = built, installed, and looked at in the real app (temp-HOME launch
  per memory): f167's summary shows separated sections; Summarise queues.
  The scheduled task then summarises one real meeting end to end.
