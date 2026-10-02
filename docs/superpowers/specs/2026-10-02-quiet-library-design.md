# Quiet library: Meetings UI refresh, light/dark, record banner — design

Date: 2026-10-02. Status: proposed from the UI review
(<https://claude.ai/artifact/UeZzqUHKXVULJJW7NeoaaG>) and the user's follow-ups in
chat (light and dark mode required; the record banner is part of the refresh and
gets an in-app way to turn it off). Plan:
`docs/superpowers/plans/2026-10-02-quiet-library.md`.

## Problem

The Meetings window works but is loud: a saturated coral fill marks the selected
row, the summary sits in a card on glass on glass, the transcript is monospace at
about 30 characters a line with "9:00:03 AM" on every line, and three columns
truncate titles. Every web window is forced to dark (`WebWindow` sets
`NSAppearanceNameDarkAqua`). The selected row and banner Record button put 85 %
white on #E8955A, about 2.1:1 contrast (4.5:1 is the minimum for small text).
The record banner has no way to switch itself off; you must find Meetings ›
Settings.

## Decisions

| Topic | Decision |
|---|---|
| Scope of this spec | Phases 1–2 of the review plus light/dark and the banner refresh. The recording pill, menu-bar popover, notepad and "written by Claude" provenance are a later spec. |
| Appearance | Follows macOS by default. Settings › Appearance: System / Light / Dark, stored as `settings.json` `"appearance"` (`"system"`, `"light"`, `"dark"`; anything else reads as `"system"`). Applied once at app level with `NSApp.setAppearance_`; `WebWindow` stops forcing DarkAqua. Change applies live to every open window and the banner. |
| Colour | One token set in `frontend/src/styles/tokens.css`, defined for dark (default) and redefined under `@media (prefers-color-scheme: light)`. No literal colours in any `*.module.css`. Values are pinned in the plan and checked by a contrast test (≥ 4.5:1 for body and secondary text and button labels, ≥ 3:1 for tertiary text). |
| Accent | Coral only for Record. Selection and the active tab use a neutral `--selection` fill. Button text on coral and on recording red uses `--on-accent` / `--on-rec` (white in light, near-black in dark). |
| Layout | Two panes: the sidebar holds Search (⌘K), Today, All meetings, collapsible Tags and People, and the meeting list grouped by day; the detail pane is one column (max 640 px) on an opaque content surface with no inner card. |
| Actions | Copy, Export and ⋯ move to a toolbar at the top of the detail pane, beside the Summary / Transcript segmented control. |
| Transcript | Proportional font; consecutive lines from one speaker form one turn; elapsed time (`0:03`, `1:02:15`) in a gutter with the wall-clock time as a tooltip. Each segment keeps its own element so find, jump-to-result and speaker rename still target one segment. |
| Time ranges | "9:00–9:12 AM" when both ends share AM/PM; "11:30 AM–12:15 PM" otherwise. |
| Today | The window opens on Today when Calendar is connected; otherwise All meetings. |
| Record banner | Stays native (`record_prompt_panel.py`, 360 × 92 pt, non-activating). Gains a ⋯ button top-right. Its menu has one item for the banner's kind: "Turn off calendar prompts" (calendar offer) or "Turn off call prompts" (call offer, call ended). Choosing it writes the setting, hides the banner, and shows "Calendar prompts are off" / "Call prompts are off" with "Turn back on in Meetings › Settings" and an **Undo** button for 5 s. Undo restores the setting only. Turning prompts off never stops or starts a recording. |
| Banner colours | Record uses dynamic coral (#B8551F light, #E8955A dark) and Stop (call ended) uses dynamic recording red (#D33A2F light, #FF5A4E dark), with label colour white in light and #1D1F1E in dark. |
| Settings sheet | A "Record prompts" group holds the existing two switches ("Offer to record calendar meetings", "Offer to record calls in other apps"); an "Appearance" group holds the System / Light / Dark control. |

## Conflicts checked (record-prompts work, merged 8d55a13)

- The banner is native AppKit; `frontend/prompt.html` was deleted. The refresh is
  done in PyObjC, not React.
- Call detection privacy: only a boolean leaves `call_detect.py`; app names and
  PIDs are never shown or stored. So the banner never names the calling app and
  there is no per-app mute. (The review mockup's "Zoom" examples are withdrawn.)
- Call-ended prompts stay tied to `detect_calls`; there is no third switch.
- The "N more" button (bottom-left) stays; ⋯ sits top-right.
- Open record-prompts checks 2, 4 and 6 are the user's live checks. This work
  must not change banner timing, focus behaviour or coordinator rules, and the
  final task re-runs the panel/controller suites.
- The uncommitted Codex worktree `codex/latency-cancellation` (base bb0ede4,
  251 commits behind) edits `speakeasy/ui/menubar.py` and engine files. This plan
  does not touch `menubar.py` or the engine.

## Correction to the review

The review said "92 meetings upgraded" stays in the list header. It does not:
`useLibraryBannerDismissed` fades it after 4 s once the page is visible. No
change is planned for it.

## Constraints kept

- Offline; no new runtime or build dependencies. Frontend unit tests use Node's
  built-in runner (`node --test`, Node ≥ 23 strips TypeScript types).
- Glass for navigation (sidebar, toolbar, banner, sheets' backdrop), solid for
  content (summary, transcript).
- The banner never takes focus (`makeKeyAndOrderFront` /
  `activateIgnoringOtherApps` stay out of the banner modules).
- Dictation insertion code is untouched.

## Out of scope

Recording pill, menu-bar popover replacing the Dock, meeting notepad, Claude
provenance on summaries, Dock and Training layout changes (they only get tokens
so they work in light mode).
