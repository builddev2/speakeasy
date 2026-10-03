# Meeting notepad — design

Date: 2026-10-02. Status: proposed, agreed section by section in chat with the
user. Follows the Quiet Library refresh
(`docs/superpowers/specs/2026-10-02-quiet-library-design.md`), whose UI rules
bind this work. Plan: `docs/superpowers/plans/2026-10-02-meeting-notepad.md`
(written after this spec is approved).

## Problem

Speakeasy records and transcribes meetings, and Claude writes a summary, but
the user has nowhere to write their own notes: not while the meeting is
happening, and not afterwards. What they jot down is usually what mattered, and
Claude's summary cannot use it.

## Decisions (user, 2 Oct 2026)

| Topic | Decision |
|---|---|
| Purpose | Both: one notes document per meeting, editable any time; lines started while recording also carry a time stamp that links into the transcript. |
| Where to type while recording | The Meetings window (a "Recording now" page). The Dock is unchanged. |
| Format | Rich text: bold, italic, headings (two levels), bullet and numbered lists, checklists. |
| Editor | TipTap (ProseMirror), installed from npm and bundled by Vite; works offline. Notes are stored as Markdown. |
| Ownership | The notes are the user's. Claude can read them (MCP) and should build its summary on them, but cannot change them. |
| Consistency | The notepad follows the Quiet Library UI rules exactly (see *UI consistency*). |

## What the user sees

- **Notes tab.** The detail toolbar's segmented control becomes
  Summary · Transcript · Notes. Notes shows the user's rich-text page in the
  same reading column as the summary. Default tab: Summary when a summary
  exists; otherwise Notes when the user has notes; otherwise Transcript.
- **Editing.** Bold, italic, Heading 1, Heading 2, bullet list, numbered list,
  checklist. Shortcuts: ⌘B, ⌘I, ⌘Z / ⇧⌘Z, ⌘⌥1 / ⌘⌥2 (headings), ⌘⇧8 (bullets),
  ⌘⇧7 (numbers), ⌘⇧9 (checklist). Markdown typing: `# `, `## `, `- `, `1. `,
  `[ ] `, `**bold**`, `*italic*`. A small formatting bar sits at the top of the
  Notes tab (icon buttons, same style as Copy/Export). Pasting keeps only these
  formats; anything else becomes plain text.
- **Autosave.** Saves 500 ms after typing stops and when the user switches
  meeting, tab or closes the window. The toolbar shows "Saved" (or "Saving…")
  in `--text-lo`; a failed save shows "Couldn't save — retrying" and retries
  every 5 s, keeping the text in the editor.
- **Recording now.** While a meeting records, a row "Recording now" with a red
  dot (`--rec`) and elapsed time sits at the top of the sidebar, above Today.
  Selecting it shows the notepad full width of the reading column, titled with
  the linked calendar event's title or "Meeting in progress". It has no Record
  or Stop controls (those stay in the Dock and banner).
- **Time stamps.** A line (paragraph, heading or list item) started while
  recording gets a stamp in the left gutter showing elapsed time (`12:40`), in
  the transcript's gutter style. Stamps are set when the line is created and
  never move. After the meeting is saved, clicking a stamp switches to the
  Transcript tab and jumps to the segment at or just before that time, with the
  existing jump highlight. Lines written after the meeting have no stamp.
- **When recording stops,** the Recording now row is replaced by the saved
  meeting, which is selected with the Notes tab open; the user can keep typing
  while the transcript is still processing.
- **Unsaved recording.** If a recording is cancelled or discarded, the notes
  are kept as a draft. The next Recording now page opens with them and a
  one-line notice "Notes from a recording that wasn't saved" with a Discard
  button.
- **Search.** ⌘K search also matches the user's notes; result rows show
  "Notes" as the source, like "Summary" and transcript hits do.
- **Copy / Export** add a "## My notes" section (Markdown, stamps as
  `[12:40]` prefixes) after the summary and before the transcript.

## UI consistency (binding)

The notepad must look and behave like the rest of the Quiet Library window:

- Colours only from `tokens.css` tokens; no literal colours (the existing lint
  covers new CSS modules). Light and dark both defined.
- Coral (`--accent`) only for Record actions and the focus ring. The
  formatting bar's active state (e.g. Bold on) uses `--selection` with
  `--text-hi`; checklist boxes use the neutral switch styling
  (`--text-mid` checked fill, `--hairline` border), never coral. The
  Recording now dot uses `--rec`.
- Typography: body `400 14.5px/1.6 var(--sans)` (same as transcript turns),
  headings in `var(--serif)` at 20 px and 16.5 px semibold, text colour
  `--text`; gutter stamps `400 11.5px` tabular nums in `--text-lo`, same
  52 px gutter as transcript turns.
- Layout: the editor sits in `.bodyInner` (640 px, 720 px at ≥ 1400 px) on
  `--surface-content`, no card or border around the editor; `.body` is the
  single scroller. The formatting bar is sticky at the top of the scroller
  like the find bar.
- Toolbar: Notes is the third segment of the existing segmented control
  (neutral `--selection` for the active segment); the formatting bar's icon
  buttons reuse `.iconButton` (28 × 26, 16 px stroke icons, `currentColor`).
- Sidebar: the Recording now row uses the existing row component and
  `--selection` when selected; the time never truncates, the title ellipsizes.
- Fluid: nothing clips from the Meetings minimum (820 × 520) to 2560 × 1440 in
  either appearance; `frontend/scripts/overflow-check.js` returns `[]`.
- Copy strings, verbatim: "Notes", "Recording now", "Meeting in progress",
  "Saved", "Saving…", "Couldn't save — retrying", "Notes from a recording that
  wasn't saved", "Discard", "My notes".

## Storage

- Library schema version 5 (migration in `meeting_store.py`):
  - `user_notes(meeting_id TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE
    CASCADE, markdown TEXT NOT NULL, text TEXT NOT NULL, stamps_json TEXT NOT
    NULL DEFAULT '[]', updated_at TEXT NOT NULL)`. `text` is a plain-text copy
    for search. `stamps_json` is a list of `[line_index, seconds]` where
    `line_index` counts lines in document order, 0-based, where a line is a paragraph, a heading or one list/checklist item (nested items count too).
  - `user_notes_fts` (FTS5 over `text`, same tokenizer and trigger pattern as
    `notes_fts`).
  - `note_draft(id INTEGER PRIMARY KEY CHECK (id = 1), markdown TEXT NOT NULL,
    stamps_json TEXT NOT NULL DEFAULT '[]', started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL)`. Draft stamps are `[line_index, utc_iso]`.
  - Separate from `notes` (Claude's summary, action items) so neither can
    overwrite the other.
- Limit: 200,000 characters of Markdown; longer input is refused with an error
  the page shows ("Notes are too long to save").
- `MeetingLibrary` API: `get_user_notes(id)`, `set_user_notes(id, markdown,
  stamps)`, `get_draft()`, `set_draft(markdown, stamps)`, `discard_draft()`.

## Draft hand-off (no `engine.py` change)

`engine.py` is still being changed in the Codex `latency-cancellation`
worktree, so this work does not edit it (nor `menubar.py`).

- `MeetingLibrary.save_meeting(new)` adopts the draft in the same transaction:
  if a draft exists and its `started_at` lies between `new.started_at − 10 min`
  and `new.started_at + duration`, its Markdown becomes the meeting's
  `user_notes`, each stamp becomes `max(0, stamp − new.started_at)` seconds,
  and the draft row is deleted. Otherwise the draft is left alone.
- Cancelled or discarded recordings never call `save_meeting`, so the draft
  stays for the next recording.
- The Meetings window learns that recording is on from the engine it already
  holds (`services.engine`: its state, `meeting_event` and the recording start time it already keeps; if that is only a private attribute, read it defensively and fall back to the time the page first saw recording on), exposed to the page as
  a `recording.get` bridge call returning `{recording: bool, startedAt, title}`
  and pushed on the existing status event. Reading the start time uses the
  engine's existing attributes; the notepad never calls engine methods.

## Bridge

`notes.user.get {id}` → `{markdown, stamps}`; `notes.user.set {id, markdown,
stamps}` → `{updatedAt}`; `notes.draft.get` → `{markdown, stamps, startedAt}
| null`; `notes.draft.set {markdown, stamps}`; `notes.draft.discard`;
`recording.get`. `meetings.get` gains `hasUserNotes` (for the default tab) and
search results gain source `"notes"`.

## Claude (MCP), search, export

- `get_meeting` returns `user_notes: {markdown, stamps: [{line, at: "h:mm:ss"}],
  updated_at} | null`. `save_notes` is unchanged and cannot write user notes.
- Tool descriptions (`get_meeting`, `pending_summaries`) tell Claude: read
  `user_notes` first; treat what the user wrote as what mattered; do not copy
  it back verbatim; keep the user's checklist items theirs and do not duplicate
  them as action items. Description changes are mirrored in
  `packaging/mcpb/manifest.json` (enforced by `test_mcpb`).
- `search_meetings` and the app's search include `user_notes_fts` hits with
  source `"notes"`.
- Markdown export and Copy add "## My notes" after the summary.

## Offline and dependencies

TipTap packages are pinned to exact versions in `frontend/package.json` /
`package-lock.json` and bundled by Vite; nothing loads from the network at run
time. A test scans the built bundle for `http://` / `https://` script, style or
font loads and fails if any appear. This is the frontend's first editor
dependency; no others are added.

## Testing

- Node (`node --test`): Markdown ↔ editor document conversion round-trips for
  every supported format; stamps stay attached to their line through
  conversion; unsupported pasted formats become plain text.
- Python: v5 migration from v4 (existing data kept); `set_user_notes` /
  `get_user_notes`; length limit; delete cascades; FTS search hits with source
  `"notes"`; draft adopted inside the window, ignored outside it, kept after a
  cancel; stamp conversion; MCP `get_meeting` fields; manifest mirror; export
  section.
- UI (mock + dev server, both appearances): Notes tab, formatting bar, autosave
  states, Recording now row, stamps and jump-to-transcript, unsaved-draft
  notice; overflow check `[]` at 820 × 520 and 2560 × 1440.
- Real app (temp HOME): type during a short recording, stop, confirm the notes
  and stamps arrive on the saved meeting.

## Execution roles (user requirement)

- All coding, including fixes after review: **Sonnet 5.5**
  (`claude-sonnet-5-5`, Agent `model: "sonnet"`).
- All evaluation: every per-task review, mutation check, visual/overflow check
  and the final whole-branch review: **Opus 5.5** (`claude-opus-5-5`, Agent
  `model: "opus"`).
- The controller (Opus) plans, dispatches and records; it writes no feature
  code. Every dispatch sets `model` explicitly.

## Out of scope

Recording pill and capture drawer; menu-bar popover; "written by Claude"
provenance; typing notes in the Dock; sharing notes; images or links in notes;
Claude editing user notes.
