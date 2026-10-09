# Action items: tracked, editable, filterable — design

Date: 2026-10-09. Status: approved in chat (2026-10-09); written for hand-off.

**Depends on:** `codex/library-export-recovery` merged to master (it sets
schema v6 and the backup/restore code this feature extends). This feature is
schema v7.

## Goal

Action items stop being a read-only bullet list under each summary and become
a tracked to-do list across all meetings. Success means the user can:

- open an **Action items** view from the sidebar and see every item, with a
  link to the meeting it came from;
- tick items done, add notes to each item, and override the due date Claude
  set (or clear it, or reset to Claude's);
- filter by tags (meeting tags plus the item's own), owner (mine by default),
  priority, status and text, and sort/group by due date, priority, meeting,
  owner or tag;
- add, edit and delete items by hand, with undo, and bulk-complete or bulk-tag;
- ask Claude (MCP) to list, create, update, complete and delete items;
- re-summarise a meeting without losing any of the above.

## User decisions (2026-10-09, verbatim choices)

| Question | Choice |
|---|---|
| Default scope | **Mine by default**; store everyone's, one click shows everyone's or one person's |
| Tags | **Inherit + own**: item shows its meeting's tags and can have extra tags of its own |
| Re-summarise | **Keep edited, replace untouched**; no duplicates of kept items |
| Extras | Manual add/edit/delete; overdue & due-soon views; Claude can read/complete; full MCP CRUD; priority + search + export |
| Due dates | **Claude resolves the phrase to a date and the app shows the phrase**; user override always wins and is marked |
| Identity | **Name + aliases in Settings** |
| Placement | **Sidebar row under Today**, full-width view in the detail pane; meeting Summary tab shows the same interactive rows |
| Existing items | **Import all as open** |

## Constraints

- **Offline, always** (AGENTS.md, memory `offline-operation-required`). The app
  never calls an LLM. Items are written by Claude through the local MCP
  server, or by the user in the app. No new runtime dependencies; the
  similarity check uses the standard library's `difflib`.
- **WKWebView numbers arrive as floats.** Every bridge handler that takes an
  item id or other integer accepts integral floats (`1.0`) and rejects bools.
  Bridge tests send floats.
- **MCP tool descriptions are mirrored** in `packaging/mcpb/manifest.json`
  (`tests/test_mcpb.py` enforces an exact match).
- Tests never touch the real library (`tests/conftest.py` autouse
  `isolated_home`). Real-app checks use a temp `HOME`.

## Current state (before)

- `notes.action_items_json` (JSON list of strings) and `notes.action_items_text`
  (newline-joined, indexed by `notes_fts`) in `speakeasy/meeting_store.py`.
- `MeetingLibrary.save_notes(action_items=[...])` replaces the whole list
  (max 50, 500 chars each).
- Claude is told to write items as `"Owner — task (due)"`, owner `Unassigned`
  when nobody took it (`speakeasy/summary_format.py`).
- `MeetingDetail.tsx` renders `detail.actionItems: string[]` as dots.
- `meeting_export.render_export_md` writes `- [ ] <string>`.

## Design

### 1. Data — schema v7 (`speakeasy/meeting_store.py`)

```sql
CREATE TABLE action_items (
    id INTEGER PRIMARY KEY,
    meeting_id TEXT REFERENCES meetings(id) ON DELETE SET NULL,
    task TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('high','normal','low')),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done')),
    completed_at TEXT,
    due_date TEXT,                         -- Claude's date, YYYY-MM-DD
    due_phrase TEXT NOT NULL DEFAULT '',   -- what was said: "by Friday"
    due_override TEXT,                     -- the user's date, YYYY-MM-DD
    due_cleared INTEGER NOT NULL DEFAULT 0, -- the user chose "no date"
    notes TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL CHECK (source IN ('summary','manual')),
    user_touched INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL CHECK (updated_by IN ('claude','user')),
    deleted_at TEXT
);
CREATE INDEX action_items_meeting ON action_items(meeting_id);
CREATE TABLE action_item_tags (
    item_id INTEGER NOT NULL REFERENCES action_items(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, tag_id)
);
```

- **Effective due date** = `NULL` if `due_cleared`, else `due_override`, else
  `due_date`. `due_source` is `user` when an override or clear is set,
  `claude` when only `due_date` is set, otherwise none.
- `source = 'summary'` means written by `save_notes`; `'manual'` means
  created by the user in the app or by Claude through `create_action_item`.
- `user_touched = 1` after any edit through the app or the item MCP tools
  (Claude edits at the user's request count as the user's).
- **Delete is soft** (`deleted_at`). A deleted summary item also stops
  `save_notes` re-adding it (see §2).
- **No FTS table.** Item text search is a case-insensitive substring match in
  Python over a few thousand rows; nothing derived to rebuild on restore.
  Library-wide search still finds item text through the `notes` mirror below.
- **Mirror.** `notes.action_items_json`/`action_items_text` are rewritten from
  the meeting's non-deleted items after every item change, formatted
  `"Owner — task (due)"` (owner omitted when empty; due is the phrase, else the
  effective date). Old readers, `notes_fts` and `Notes.action_items` keep working.
- **Tag clean-up.** `_drop_orphan_tags` also keeps tags referenced by
  `action_item_tags`.
- **Meeting deletion.** `MeetingLibrary.delete` hard-deletes that meeting's
  untouched summary items first; touched, done and manual items survive with
  `meeting_id = NULL` and show "Meeting deleted".
- **Migration** `_migrate_v7(conn)` (Python, modelled on `_migrate_v3`'s race
  handling) creates the tables and imports every `notes.action_items_json`
  string with `parse_legacy` (§4): `source='summary'`, `status='open'`,
  `user_touched=0`, `updated_by='claude'`, `created_at = updated_at =
  notes.updated_at`, `position` = list index. Then `user_version = 7`. A v6
  build refuses a v7 library (existing fence).
- **Backup/restore.** The new tables are master data. `rebuild_derived` needs
  no change. `tests/test_library_backup.py::MASTER` gains both tables. A v6
  backup restored into a v7 build migrates and imports.

### 2. Re-summarise merge (`save_notes`)

`save_notes(action_items=[...])` accepts strings (parsed with `parse_legacy`)
or objects `{task, owner?, due_date?, due_phrase?, priority?, tags?}`.
Max 50 per call. Owner `Unassigned` (any case) is stored as `''`.

In one transaction:

1. **Kept** = the meeting's items where `source='manual'` OR `user_touched=1`
   OR `status='done'` OR `deleted_at IS NOT NULL`.
2. Hard-delete the meeting's other items (untouched, open, live summary items).
3. For each incoming item in order: skip it if `similar(task, kept.task) >= 0.85`
   for any kept item; otherwise insert it (`source='summary'`, `position` = index).
4. Rewrite the mirror.

`similar(a, b)` = `difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()`,
where `norm` casefolds, splits on every non-alphanumeric run, and drops the
filler words a, an, the, to, of, for, with, and, on, in, at. Without that,
"Book the room" vs "Book room" scores 0.82 and would duplicate a kept item. The 0.85 threshold is a named constant `SIMILAR_TASK_RATIO`.

### 3. Identity ("mine")

`settings.json` key `identity = {"name": str, "aliases": [str]}`. The name can
be up to 80 characters; there can be up to 10 aliases of up to 40 characters
each. Blank entries are dropped and duplicates removed case-insensitively.

`is_mine(owner, identity)`:

1. Split `owner` on `,`, `&`, `/` and the word `and`.
2. Each part is compared against every candidate (the name and each alias),
   all casefolded.
3. A part matches when it equals a candidate, or when its first word equals
   the candidate's first word.
4. `is_mine` is false when the identity name is empty.

The first-word rule means another person with the same first name also
counts as "mine". That is accepted and documented in the Settings help text.

The bridge computes `mine` per item, so the frontend never re-implements
matching.

When the name is set, `pending_summaries` instructions gain the line:
`The user is <name> (also <aliases>). Write the user's own items with owner "<name>".`

The due-date rule is added for everyone:
`Resolve relative due dates ("by Friday", "next week", "end of month") to a
YYYY-MM-DD due_date counted from the meeting's start date, and put the words
said in due_phrase. Leave due_date out when no time was given. "Next week"
means that week's Friday; "end of month" means the month's last day.`

### 4. Pure helpers (`speakeasy/action_items.py`)

- `parse_legacy(text) -> ItemInput`:
  - The owner is the text before the first ` — ` or ` – ` (em or en dash
    with spaces; a plain hyphen is not used, so "Follow up - urgent" stays
    one task), but only when that part is at most 40 characters and at most
    4 words.
  - If parsing leaves an empty task, the whole original text becomes the task.
  - A trailing `(…)` of up to 80 characters becomes `due_phrase` and is
    removed from the task.
  - `Unassigned` becomes `''`.
  - `due_date` is always `None`.
- `validate_input(raw) -> ItemInput`:
  - `str` input goes through `parse_legacy`; `dict` input is checked field by
    field.
  - Limits:
    - task: 1–500 characters after strip
    - owner: ≤ 80 characters
    - due_phrase: ≤ 80 characters
    - priority: `high`/`normal`/`low`
    - due_date: a real date, years 2000–2100
    - tags: through `clean_tag_names`, at most 10
  - Any violation raises `ValueError` with a message safe to show Claude.
- `similar`, `norm`, `is_mine`, `clean_identity`, `format_mirror(item)`,
  `effective_due(item)`, `due_bucket(due, today)` (Python twin of the frontend's,
  used by the MCP `overdue` filter).

### 5. Library API (`speakeasy/action_item_store.py` + thin `MeetingLibrary` methods)

SQL lives in `action_item_store.py` as functions taking `conn`.
`MeetingLibrary` wraps each in `self._transaction()`. `ActionItem` is a
dataclass with:

- **Identity and meeting:** `id`, `meeting_id`, `meeting_title`,
  `meeting_started_at`, `meeting_tz_offset_minutes`
- **Content:** `task`, `owner`, `priority`, `status`, `completed_at`, `notes`
- **Due date:** `due_date`, `due_phrase`, `due_override`, `due_cleared`,
  `effective_due`, `due_source`
- **Provenance:** `source`, `user_touched`, `updated_by`
- **Tags:** `tags` (own), `meeting_tags`
- **Ordering and times:** `position`, `created_at`, `updated_at`, `deleted_at`

Methods on `MeetingLibrary`:

- `list_action_items(*, meeting_id=None, include_deleted=False) -> list[ActionItem]`
- `get_action_item(item_id) -> ActionItem` (raises `ActionItemNotFound`)
- `create_action_item(*, task, owner='', meeting_id=None, due=None, priority='normal', notes='', tags=(), updated_by='user') -> ActionItem`
- `update_action_item(item_id, *, updated_by, task=UNSET, owner=UNSET, priority=UNSET, status=UNSET, notes=UNSET, due=UNSET, due_reset=False, tags=UNSET) -> ActionItem`.
  The `due` argument works like this:
  - `UNSET` leaves the date unchanged.
  - A `YYYY-MM-DD` string sets the override.
  - `None` or `''` clears it to "no date".
  - `due_reset=True` drops the override and the clear, returning to Claude's date.
- `delete_action_item(item_id, *, updated_by)`, `restore_action_item(item_id, *, updated_by) -> ActionItem`
- `bulk_update_action_items(ids, *, updated_by, status=UNSET, add_tags=(), remove_tags=(), delete=False) -> list[ActionItem]` (max 500 ids)

Every write sets `updated_at`, `updated_by` and `user_touched=1`, then
rewrites that meeting's mirror. Setting status to `done` stamps
`completed_at`; setting it back to `open` clears it. Notes can be up to
5,000 characters.

### 6. MCP (`speakeasy/mcp_tools.py`, mirrored in `packaging/mcpb/manifest.json`)

- `list_action_items` (read-only) takes these filters:
  - `status`: open, done or all (default open)
  - `mine`, `owner`, `tag`, `meeting_id`, `due_from`, `due_to`, `overdue`, `query`
  - `sort`: due, priority, meeting or created
  - `limit` (1–100, default 50) and `offset`

  It returns each item with:
  - `id`, `task`, `owner`, `mine`, `priority`, `status`
  - `due`, `due_source`, `due_phrase`, `notes`
  - `tags` (own plus meeting tags), `meeting` (id, title, start), `completed_at`

  It also returns `identity_set` and `next_offset`.
- `create_action_item`, `update_action_item` (`due: ""` clears; `reset_due: true`
  returns to Claude's date; `tags` replaces the item's own tags) and
  `delete_action_item` (`undo: true` restores). These three are not read-only,
  and their writes are `updated_by='claude'` and touched.
- `save_notes.action_items` accepts strings or objects (schema `anyOf`).
- `get_meeting.notes.action_items` becomes a list of
  `{id, task, owner, status, due, priority}`, so Claude can reference ids.
- `pending_summaries.instructions` = `summary_instructions(identity)`.

### 7. Bridge (`speakeasy/ui/meetings_bridge.py`)

Methods (payload shapes mirror `frontend/src/mock/actionItems.ts`):

- `actions.list` → `{items: ActionItemPayload[], identitySet: bool, today: "YYYY-MM-DD"}` (non-deleted only)
- `actions.create`, `actions.update`, `actions.restore` → `ActionItemPayload`
- `actions.delete` → `true`
- `actions.bulk` → `{items}`
- `settings.meetings.get/set` gain `userName` and `userAliases`
- `meetings.get` sends `actionItems: ActionItemPayload[]` (that meeting's items)

`ActionItemPayload` fields:

- **Identity and meeting:** `id`, `meetingId`, `meetingTitle`, `meetingDate`
  (local `YYYY-MM-DD`)
- **Content:** `task`, `owner`, `mine`, `priority`, `status`, `completedAt`, `notes`
- **Due date:** `due` (effective), `dueSource`, `duePhrase`, `claudeDue`
- **Tags:** `tags` (own), `meetingTags`
- **Provenance and times:** `source`, `createdAt`, `updatedAt`

### 8. Frontend

**Sidebar row.** An "Action items" row sits directly under Today and is shown
even when Calendar is off.

- The count is the open items that are mine. If no name is set, it counts
  everyone's open items.
- A red badge with the overdue count appears when that count is above 0.

**`ActionItemsView`** fills the detail pane, like Today.

- **Toolbar:**
  - an owner switch: Mine / Everyone / a person
  - a multi-select tag filter (matches ANY selected tag, over own plus
    meeting tags)
  - priority chips
  - a status switch: Open / Completed / All (default Open)
  - a search field, matching task, notes, owner and meeting title
  - Group by: Due / Meeting / Owner / Tag / None
  - Sort: Due / Priority / Meeting date / Created
- **Due groups**, Monday-start weeks: Overdue, Today, This week (to
  Sunday), Later, No date, then Completed (collapsed, newest first) when the
  status is All.
- **Empty states:**
  - "Set your name in Settings to see your items" with an Open Settings
    button, when Mine is selected and no name is set (the list then shows
    everyone's items)
  - "Nothing due — nice." when the filtered list is empty
- **Adding items:** "+ Add item" opens an inline row with task, owner
  (defaulting to the user's name), due date and priority.
- **Bulk mode:** checkboxes on each row, with Complete, Reopen, Add tag and
  Delete actions.
- **Undo:** deleting shows a 6-second undo toast that calls `actions.restore`.
- **Persistence:** filter, group and sort are kept in `localStorage` under
  `actions.view.v1`, with every read and write wrapped in try/catch.

**`ActionItemRow`**, shared by the view and `MeetingDetail`:

- a checkbox, the task, an owner chip ("Unassigned" when empty), a priority mark
- the due date as `Fri 16 Oct`, plus `"by Friday"` in muted text when there
  is a phrase, and an `edited` mark when the due date is the user's
- tag chips (meeting tags muted, own tags solid)
- the meeting link (`meetingTitle · meetingDate`), which opens the meeting;
  "Meeting deleted" when `meetingId` is null and the source was a summary
- a click on the row expands an editor for task, owner, priority, due date
  (with No date and Reset to Claude's), own tags and notes. Text fields save
  on blur or Enter; the checkbox and selects save immediately.

**Code layout:**

- Pure logic lives in `frontend/src/meetings/actionItems.ts`: `dueBucket`,
  `filterItems`, `sortItems`, `groupItems`, `sidebarCounts` and
  `formatDue`, tested in `frontend/tests/actionItems.test.ts` with
  `node --test`.
- Bridge calls live in `frontend/src/meetings/actionItemsApi.ts`, with an
  in-memory mock fallback (as in `draftApi.ts`).
- Mock data lives in `frontend/src/mock/actionItems.ts`. The mock state
  `?state=actions` opens the view.
- The style follows the existing glass look and CSS tokens. No new npm
  dependencies.

### 9. Export (`speakeasy/meeting_export.py`)

Per meeting, the `## Action items` section is built from the meeting's
non-deleted items:

```
- [x] Send budget draft — Jason · due Fri 16 Oct 2026 ("by Friday") · high
  Notes: first line
  second line
```

The owner, due date and priority parts are each omitted when empty or
`normal`. `export_all` also writes `Action items.md`, listing every
non-deleted item grouped Open/Completed, with the meeting title and date per
item. Manual items without a meeting appear only there. The export's
verified count stays the number of meetings exported.

## Error handling

- Library writes raise `ValueError` (bad input), `ActionItemNotFound` or
  `MeetingNotFound`.
  - The bridge maps them to `error` strings (`not_found` for both not-found
    cases).
  - MCP maps them to `ToolError` messages that tell Claude how to fix the call.
- The frontend applies an edit optimistically. When the bridge call fails, it
  rolls the row back and shows a quiet inline "Couldn't save — try again"
  under the row.
- A refresh triggered by `meetings.changed` (e.g. Claude edited through MCP)
  replaces the list but keeps any open editor's unsaved text.

## Testing

- **Python:**
  - the migration (fresh v7; v6 library with items imported, including
    `Unassigned`, a missing dash and a trailing phrase; idempotent re-run)
  - every `parse_legacy` and `validate_input` rule
  - merge rules: kept categories, the similarity skip, order, the 50 cap and
    deleted-as-suppression
  - CRUD and due semantics
  - the mirror and the orphan-tag fix
  - meeting deletion
  - `is_mine`
  - MCP tools, including the manifest mirror
  - bridge payloads with float ids
  - export
  - backup MASTER
- **Frontend:** every pure function in `actionItems.ts`, including week
  boundaries on Sunday and Monday and the ANY tag match.
- **Reviewers** verify by mutation (memory `mutation-review-gotchas`: run with
  `python -B`, clear `__pycache__`).
- **Real app:** a temp-`HOME` launch of the built app with seeded meetings.
  Open Action items, tick an item, add a note, override the due date, filter
  by tag, and confirm the meeting link and the Summary-tab rows. Then pipe
  JSON-RPC to `Speakeasy --mcp` under the temp `HOME` for list, update and
  delete.

## Out of scope (follow-ups)

- Reminders and notifications.
- Recurring items.
- Calendar or Reminders.app sync.
- Drag-to-reorder (`position` is stored for it).
- Linking an owner to a People record.
- Per-item history.
