# Handoff: Meeting library, calendar and Claude MCP

> **Superseded (28 Sep 2026):** Phase 1 is implemented on branch `meeting-library-phase1`. The current status, decisions and open items are in the plan's "Execution notes". This file is kept as the original planning handoff.

Session date: 26–27 September 2026. This file is enough to resume in a fresh chat.

## Read these first (in order)

1. `docs/superpowers/plans/2026-09-27-meeting-library-mcp.md`: the implementation plan. Phase 1 is fully detailed (Tasks 1–12); phases 2–4 are scoped outlines.
2. `docs/superpowers/specs/2026-09-26-meeting-library-mcp-design.md`: the approved design spec, including the UI/UX section.
3. `AGENTS.md`: repo rules (offline, threading, tests, insertion regression).

## Status

- Spec: **approved**. Plan: written, pending the user's final read. The execution method is already chosen (subagent-driven, per `~/.claude/CLAUDE.md`).
- **No code written yet.** Spec, plan and this handoff are on disk on `master`, **uncommitted**. Commit them on the new worktree branch first.
- Nothing is running (no background shells, monitors or subagents).
- Baseline: 393 tests pass in ~7 s (`.venv/bin/python -m pytest -q`; use timeout 300000).

## What the user wants

A local Granola-style tool:
- record meetings (only ever started by a click)
- see the work calendar (Exchange/M365 synced into macOS Calendar.app)
- keep every transcript in a searchable library
- query it from **Claude Desktop and Claude Code** through a local MCP server, using the Claude subscription, not API credits

It was inspired by a coworker's Firebase-hosted version; Speakeasy stays fully offline instead.

## Decisions (do not re-litigate)

| Topic | Decision |
|---|---|
| Privacy | Speakeasy makes no network calls. Claude reads meetings only through a local stdio MCP server that the user's Claude client launches. The user accepted that text Claude reads goes to Anthropic. |
| Storage | **SQLite is the master copy** (WAL + FTS5, stdlib). Legacy JSON is imported once, verified, and archived to `meetings/legacy-json/`. The Markdown export is on demand only. |
| Obsidian | **Not used.** Only its lessons are adopted: plain-text export, rebuildable caches, linked people and tags, summaries before transcripts. |
| Calendar | EventKit (`pyobjc-framework-EventKit==12.2.1`, phase 3). Only the app touches EventKit; the MCP server reads the cached table. Event notes, location and URLs are never stored. |
| MCP | `Speakeasy --mcp`, stdlib JSON-RPC over stdio. Plus a **Claude Desktop Extension (`.mcpb`)** for one-click install (user said yes). Claude Code uses `claude mcp add ... --scope user`. |
| v1 scope | Library + search + MCP; calendar in the app; record prompt (non-activating banner, never auto-records). |
| UI | Apple HIG, modelled on Calendar/Mail/Notes, in the existing dark glass tokens; no new frontend deps. **All screens are mocked first and screenshots approved by the user before wiring** (plan Task 9, Step 6 is a hard stop). |
| Build order | Phase 1 library + UI → 2 MCP + `.mcpb` → 3 calendar → 4 record prompt. One session per phase. |

## Code-review findings the plan fixes

1. `Meeting.new` stamps `created`/title at **processing end**, not recording start (`engine.py` `_process_meeting`). Fixed in Task 7; imported meetings get start = created − duration, marked approximate.
2. `known_speaker_segments` merges the mic track without limit (median longest segment ~15 min, max ~2.5 h). Task 5 splits at 1.5 s pauses and a 60 s cap; the import splits old segments over 120 s.
3. `Meeting.save` drops unknown keys, and there is a two-writer race. Fixed by SQLite transactions.
4. `list_meetings` parses every transcript on the main thread. Fixed by indexed queries.
5. `Meeting.load` lets `TypeError` escape. The importer skips and reports bad files.
6. Speaker relabel uses `window.prompt/confirm`, but WKWebView has no `WKUIDelegate`, so the click likely does nothing. Replaced by a popover (still to confirm in the running app).

## Refinements agreed while planning

- The UTC offset is stored in minutes (`tz_offset_minutes`), not an IANA zone name (Python 3.11).
- No `track` column on segments.
- Echo collapse uses overlapping time ranges with 5 s slack plus snippet similarity ≥ 0.8.
- The `.mcpb` manifest field names in the plan are from memory: verify them against the current MCPB spec at the start of phase 2.
- Phase 2's first step is a throwaway spike: confirm the frozen PyInstaller bundle passes stdio for `--mcp`; otherwise add a console executable.

## User's working rules that apply

- Branch in a worktree before executing; never implement on `master`. Commit, and push only when asked; never force-push.
- Sonnet implements, Opus reviews using mutation checks. Keep subagent reports short.
- "Done" for app work = launched and looked at (the `run` skill), not just tests passing. Say which was done.
- At each phase boundary, run the `/clear` checkpoint from `~/.claude/CLAUDE.md` and end with `Safe to /clear: yes|no`.
- Use they/them for people not yet named with pronouns. The user's coworker is referred to only as "a coworker".

## Prompt to paste into the fresh session

```
Execute docs/superpowers/plans/2026-09-27-meeting-library-mcp.md, Phase 1 only, subagent-driven. Read docs/superpowers/plans/2026-09-27-meeting-library-mcp-handoff.md first for decisions and context, and the spec at docs/superpowers/specs/2026-09-26-meeting-library-mcp-design.md. Create a worktree branch first and commit the spec, plan and handoff there. Stop at Task 9 Step 6 for my design approval.
```
