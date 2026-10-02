# Meeting tags: canonical slugs, aliases, provenance — design

Date: 2026-10-01. Scope: items 1–4 of the tag review (no app UI; the tag
editor and automatic rule tagging are phase 2).

## Goal

Tags stay consistent as the library grows, and no writer can silently wipe
another's tags. Success means:

- "Project X", "project-x" and "ProjectX" are one tag, enforced by the
  database, and filtering by any spelling finds it.
- Tags the user asked for are never removed by Claude re-summarising, and a
  tag the user removed is not re-added by Claude.
- Adding or removing one tag never needs a read-modify-write across calls.
- Existing tags can be renamed, merged, deleted and described over MCP.
- Claude is steered to reuse existing tags rather than invent new ones.

## Current state (before)

- `tags(id, name UNIQUE COLLATE NOCASE)` and `meeting_tags(meeting_id,
  tag_id)` (`speakeasy/meeting_store.py`, schema v2).
- The only writer is `MeetingLibrary.save_notes(tags=…)`, reached through
  MCP `save_notes`; it replaces the meeting's whole tag list, then deletes
  orphaned tags.
- `_normalise_tags` collapses whitespace, de-duplicates case-insensitively,
  caps 40 chars per tag and 20 tags per meeting.
- The library holds one tag (`test`) today, so the migration risk is low.

## 1. Canonical form

`tag_slug(name: str) -> str`, in `meeting_library.py` and registered as a
deterministic SQLite function in `meeting_store.connect()` (like
`casefold`):

1. `unicodedata.normalize("NFKD", name)`, drop combining marks
   (diacritics), then `casefold()`.
2. Keep only characters where `str.isalnum()` is true; drop everything else
   (spaces, hyphens, underscores, punctuation, emoji).
3. No stemming: "plan" and "plans" are different slugs.

An empty slug (e.g. "!!!", "—") is rejected with
`ValueError("Tag must contain a letter or digit.")`.

Display names keep today's rules: whitespace collapsed, at most 40
characters. Resolving a name to an existing tag never changes that tag's
display name; only `manage_tags rename` does.

**Resolution** `_resolve_tag(conn, name, *, create) -> (tag_id, created)`:
look up `tags.slug`, then `tag_aliases.slug`; if neither matches and
`create` is true, insert a new tag with the display name. Every writer and
the tag filter go through it.

## 2. Schema v3

```sql
ALTER TABLE tags ADD COLUMN slug TEXT;            -- backfilled, then:
CREATE UNIQUE INDEX tags_slug ON tags(slug);
ALTER TABLE tags ADD COLUMN description TEXT NOT NULL DEFAULT '';
ALTER TABLE tags ADD COLUMN created_at TEXT;      -- NULL for pre-v3 tags

CREATE TABLE tag_aliases (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,                           -- spelling, for display
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE
);

ALTER TABLE meeting_tags ADD COLUMN source TEXT NOT NULL DEFAULT 'claude'
    CHECK (source IN ('user', 'claude'));
ALTER TABLE meeting_tags ADD COLUMN added_at TEXT; -- NULL for pre-v3 links

CREATE TABLE tag_suppressions (
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (meeting_id, tag_id)
);
```

`tags.name` keeps its `UNIQUE COLLATE NOCASE` constraint (two names that
differ only in case already share a slug, so it never conflicts). A slug is
never in both `tags.slug` and `tag_aliases.slug`; the writers enforce this
because SQLite cannot express it as a constraint.

**Migration.** `_MIGRATIONS` entries may be a callable as well as a script;
v3 is `_migrate_v3(conn)` in Python, because the backfill needs `tag_slug`
and collision merging. It runs inside `BEGIN IMMEDIATE`; once inside the
lock it checks `PRAGMA table_info(tags)` for `slug`, and if that column
exists (another connection already migrated) it only sets
`user_version = 3` and commits. Otherwise it:

1. Adds the columns and tables above (except the unique index).
2. Sets `slug = tag_slug(name)` for every tag.
3. Merges tags whose slugs collide into a survivor chosen by most meeting
   links, ties to the lowest id. Their meeting links are combined (using
   `INSERT OR IGNORE`), then the other tags are deleted. A tag whose name
   gives an empty slug gets slug `tag<id>`, so no data is lost.
4. Creates the unique index, sets `user_version = 3`, and commits. Any
   error rolls the whole transaction back.

`SCHEMA_VERSION = 3`. Builds before v3 refuse to open a v3 library (the
existing guard). The app and MCP server ship in one bundle, but installing
stops the Claude Desktop MCP connector, so it must be toggled afterwards.

**Orphan cleanup** (it currently runs after `save_notes` and meeting
delete) deletes a tag no meeting uses only if it has no description and no
aliases. `list_tags` still hides tags no meeting uses.

## 3. Writes

All writes are single transactions in `MeetingLibrary`. Per-meeting limit
of 20 tags (all sources together) and the 40-character name limit apply to
every writer.

### `save_notes(tags=[…])`: Claude's own suggestions (source `claude`)

- Resolve each name (creating it if needed), then drop duplicates by tag
  id, keeping the first.
- Replace only this meeting's `source = 'claude'` links with the requested
  set. `user` links are never deleted. If a requested tag is already a
  `user` link, it stays `user`.
- A requested tag that has a suppression for this meeting is skipped and
  reported back.
- **New-tag cap:** if a call would create more than 3 new tags, nothing is
  written and the call raises
  `ValueError("Would create N new tags (a, b, c, d); at most 3 per call. Reuse tags from list_tags or drop some.")`.
- MCP response adds `created_tags: [names]` and `suppressed: [names]` to
  today's fields.
- `summary` and `action_items` behave exactly as today.

### New `tag_meetings(ids, add=[], remove=[])`: the user's explicit request (source `user`)

- `ids`: 1–100 meeting ids. All are validated and must exist before
  anything is written, otherwise `ValueError` naming the unknown ids.
- `add`: resolve, creating the tag if needed (no new-tag cap). Upsert the
  link as `source = 'user'`, upgrading a `claude` link, and delete any
  suppression for that meeting and tag.
- `remove`: resolve without creating. Delete the link whatever its source,
  and insert a suppression, even if the meeting had no such link, so Claude
  cannot add it later. A name that resolves to no tag is reported in
  `not_found` and otherwise ignored.
- A name in both `add` and `remove` (after resolving) is a `ValueError`.
- Returns `{"meetings": [{"id", "tags"}], "created_tags", "not_found"}`.

### New `manage_tags(action, tag, …)`

`tag` is resolved without creating; if nothing matches, `ValueError("No tag named …")`.

- `rename` (`name`): if the new slug belongs to another tag or another
  tag's alias, `ValueError("… already exists; use merge.")`. Otherwise
  update `name` and `slug`. If the slug changed, add the old slug as an
  alias, and remove any alias that equals the new slug.
- `merge` (`into`): move meeting links to the target, with `user` winning
  if both tags were on a meeting. Move suppressions (`INSERT OR IGNORE`),
  repoint the source tag's aliases, add the source's slug as an alias of
  the target, and delete the source. Merging a tag into itself is an error.
- `delete`: delete the tag; links, aliases and suppressions cascade. It
  adds no suppression.
- `describe` (`description`, at most 200 chars, empty string clears).
- Returns the resulting tag (`name`, `description`, `aliases`, `count`), or
  `{"deleted": name}`.

## 4. Reads and steering

- `meeting_filters(tag=…)` matches by
  `t.slug = tag_slug(?) OR t.id IN (SELECT tag_id FROM tag_aliases WHERE slug = tag_slug(?))`.
- `MeetingLibrary.list_tags()` keeps its `[(name, count)]` shape for the
  app sidebar. New `tag_catalog()` returns
  `[{name, count, description, aliases: [names]}]`, and MCP `list_tags`
  uses it.
- `meeting_tags(id)` and `StoredMeeting.tags` are unchanged (a list of
  names). MCP `get_meeting` adds `tags_detail: [{name, source}]`.
- Tool descriptions:
  - `save_notes`: "…tags are your suggestions: call list_tags first and
    reuse existing tags (any spelling of an existing tag or alias matches
    it); create a new tag only for a genuinely new topic, at most 3 per
    call. Replacing tags never removes ones the user added."
  - `tag_meetings`: "Add or remove tags on meetings because the user asked
    to. Removed tags will not be re-added by save_notes."
  - `manage_tags`: "Rename, merge, delete or describe a tag across all
    meetings, when the user asks to tidy tags."
  - `list_tags`: mention descriptions and aliases.
- Every tool and description change is mirrored in
  `packaging/mcpb/manifest.json` (`test_mcpb` enforces this).
  `tag_meetings` and `manage_tags` are write tools (`read_only = False`).
- The app's "Summarise and tag" prompt (`MeetingDetail.tsx`) adds: "Reuse
  my existing tags where they fit."

## Error handling

Library validation raises `ValueError` with a message that tells the
caller what to do next. The MCP layer turns it into a `ToolError` as it
does today. Every write either commits fully or not at all.

## Testing

- `tag_slug`: the spelling variants of one tag, diacritics, empty slugs,
  and that "plan" and "plans" stay distinct.
- Resolution through slug and alias; display names unchanged by
  resolving.
- `save_notes` provenance: `user` links survive, a `claude` link to a
  `user` tag stays `user`, suppressed tags skipped and reported, new-tag
  cap at exactly 3 (3 passes, 4 fails and writes nothing), and the
  20-per-meeting limit across sources.
- `tag_meetings`: upgrades, clearing suppressions, suppression without an
  existing link, unknown ids rejected before any write, a name in both
  add and remove, `not_found`.
- `manage_tags`: a rename collision, the old name resolving after rename,
  merge combining links (user wins) plus moving suppressions and aliases,
  delete cascading, describe limits.
- Filters match an alias and a variant spelling.
- Migration from a real v2 file with colliding names (`Project X`,
  `project-x`), plus an empty-slug name: links combined, survivor rule,
  version 3. Two connections migrating one v2 file concurrently.
- Orphan cleanup keeps tags that have a description or alias.
- MCP tool tests and the manifest mirror test.
- Tests are HOME-isolated; the opus reviewer verifies by mutation.

## Out of scope (phase 2)

App tag editor and tag manager UI, automatic rule tagging (calendar series,
attendees, title patterns), multi-tag filters, sidebar search.
