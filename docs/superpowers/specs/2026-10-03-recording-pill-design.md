# Recording pill, menu-bar Next-up and Today home — design

Date: 2026-10-03. Status: agreed with the user in chat (sections 1–2 approved;
the user then asked for no further section reviews and delegated the remaining
decisions, which are marked *(delegated)*). Phase 3 of the Speakeasy UI Review
artifact (2 Oct 2026), minus "Claude-written text" provenance. The Quiet Library
UI rules (`docs/superpowers/specs/2026-10-02-quiet-library-design.md`) bind this
work. Plan: `docs/superpowers/plans/2026-10-03-recording-pill.md`.

## Problem

The Dock window (`ui/main_window.py` + `frontend/src/dock/`) is a full window
for a running meeting: five controls compete, two are dimmed but still drawn,
the rainbow waveform is decorative, and during a recording its **Meetings**
button is disabled, so reaching the notepad means going through the menu bar.
The user wants the app to look clean, modern and Apple-like, and wants Today
not to show the whole of a long day.

## Decisions (user, 3 Oct 2026)

| Topic | Decision |
|---|---|
| Scope | Recording pill + capture drawer + menu-bar Next-up together; the Dock window is removed. Claude provenance is a separate later spec. |
| Launch / Dock-icon click | Opens the **Meetings window on Today**. Speakeasy keeps its Dock icon (`NSApplicationActivationPolicyRegular`). |
| Today on busy days | **Focus layout:** a Now / Up next card, then the next 3 events; earlier events fold into one line; "Show all N events" expands. |
| Pill | Dot, title, timer, audio indicator, **Notes**, Stop. Click the pill body for the capture drawer. Floats top centre, draggable, never takes focus. |
| After Stop | The pill stays: "Processing · <engine progress>" with Cancel (Discard/Keep), then "Saved · Open" for 6 s. |
| Menu bar | A **native NSMenu** with a custom Next-up card row at the top. |
| Meeting options | System-audio app picker and Remote speakers are removed from the UI (user never uses them): every meeting records mic + all system audio, speakers Auto. **Identify enrolled voices** becomes a Settings › Recording switch, default off. |
| Pill/drawer technology | **Native AppKit panels** (like `record_prompt_panel.py`), not a web view. |
| Look | Clean, modern, Apple-like: system font, native HUD/vibrancy materials, colour only for state (red recording, amber warning/processing, coral only for Record), no decoration that carries no information. |

## Where everything goes (approved section 1)

| Dock window had | New home |
|---|---|
| Opens on launch / Dock-icon click | Meetings window, Today |
| Begin Meeting | Today card **Record** (current/next event); **Start Meeting** button in Today's header (unscheduled); menu Next-up card and **Start Meeting**; the existing record-prompt banner |
| Status, timer, End Meeting | Pill |
| Link to calendar event | Capture drawer |
| Capture health text and warnings | Capture drawer row (with its fix); menu status line |
| Dictation paused | Capture drawer |
| Processing progress, Cancel + Discard/Keep | Pill after Stop |
| Microphone failed / Retry Microphone | Menu status line + **Retry Microphone** item; banner at the top of Today |
| "Meeting could not start / finish" | Banner at the top of Today; menu status line |
| Profile name, Train Profile, Check Microphone | Menu (Profile submenu stays; **Train My Voice…**, **Check Microphone…**) |
| System audio picker, Remote speakers | Removed from the UI (engine options remain, unused by the UI) |
| Identify enrolled voices | Settings › Recording switch |
| Start at Login (menu) | Settings › General switch |
| Quit | Menu, ⌘Q |

Unchanged: the engine, recorder, dictation and insertion code; the
meeting-start, microphone-recovery and insertion rules in `AGENTS.md`; the
capture-health privacy boundary (only the existing whitelisted fields: no app or
device names, PIDs, window titles or transcript text); closing Meetings never
quits the app.

## The pill (approved section 2)

Native non-activating `NSPanel`, about 360 × 36 pt, capsule shape, HUD material
(`NSVisualEffectView`, `.hudWindow`), adapts to light/dark. Level and collection
behaviour as the record-prompt banner: status-window level, joins all Spaces,
full-screen auxiliary, never key/main. Appears whenever a recording starts
(any start path). Default position: top centre of the screen with the mouse,
just below the menu bar. Draggable by its background; the position is saved in
`settings.json` (`"pill_origin"`, screen-relative) and reset to the default if
it is no longer fully on a connected screen.

**Recording:** `● <title> <mm:ss | h:mm:ss> ⦿ [Notes] (■)`
- Title: linked event or "Untitled meeting", truncated with "…".
- Timer: from `meeting_recorder.elapsed_seconds`, refreshed once a second.
- ⦿ audio indicator, from the existing capture health (no new fields):
  grey = waiting for first buffer; green = system and mic buffers arriving with
  non-zero signal; amber = no signal yet after 10 s, capture lag, writer
  failure or helper exit, or mic-only fallback. The first change to amber in a
  recording opens the drawer once.
- **Notes** opens the Meetings window on the Recording now page.
- **■** ends the meeting (`engine.end_meeting()`), one click.

**Processing:** `◐ Processing · <engine progress text> [Notes] [Cancel]`.
Cancel → `Discard meeting? [Discard] [Keep]` (Discard calls
`engine.cancel_meeting_processing()`, then the pill shows "Cancelling…" until the
state leaves processing). Saved → `✓ Saved · [Open]` for 6 s, then hides; Open
opens Meetings on that meeting. A processing error or cancel → hide (cancel) or
`Couldn't finish · [Open Meetings] ×` until closed (error).

**Capture drawer:** a second native panel under the pill; toggled by clicking
the pill's title/timer area; ✕ closes. Rows: **Event** (linked title or "Link to
event…"; click shows a native menu of today's events, ✓ on the linked one,
"Unlink" when linked — same data as `calendar_payloads.day_chips`); **Microphone**
(You · working / no signal); **System audio** (All apps · working / waiting /
unavailable, with the fix text the Dock showed for each problem); **Dictation**
("Paused until the meeting ends"); footer "Audio stays on this Mac and is
deleted once the transcript is saved."

## Today home (delegated)

The Meetings window already opens on Today when Calendar is connected. Change
its body to the focus layout:

1. **Banner slot** (only when needed): microphone failed (Retry Microphone),
   meeting could not start, meeting could not finish. Quiet Library banner style.
2. **Hero card**: if recording, "Recording · <title> · <timer>" with
   **Open notes**; else the event in progress (started, not ended, not
   recorded) or the next event today, with "Now" / "Starts in N min" /
   "at 2:30 PM", people count and a coral **Record** button (recorded events
   show "Recorded ✓" and open the meeting). No events left: "Nothing else
   today" with **Start Meeting**.
3. **Next**: the next 3 later events today (time range, title, people,
   Record). 
4. **Earlier today · N meetings, M recorded** collapsed line; click expands
   the earlier rows in place.
5. **Show all N events** when more than 3 later events exist; expands in
   place; "Show fewer" collapses.
6. Upcoming days keep their existing collapsed sections below.

Header: "Today" title and a **Start Meeting** button (records with defaults; no
calendar link). Without Calendar the existing Connect Calendar / Privacy
Settings states stay and the header still offers Start Meeting. The now-line is
dropped (the hero card replaces it). Splitting the agenda (current/next, later,
earlier) is a pure function with unit tests.

## Menu bar (delegated details)

Native `NSMenu`, same status item. Order:

1. Status line (Ready — <profile> / Recording · 12:03 / Processing · … /
   microphone failure text) and the event line (unchanged behaviour).
2. **Next-up card**: a custom `NSView` menu row: title, "Now" / "Starts in N
   min · 2 people", coral **Record**. Hidden when Calendar isn't connected, no
   event remains today, or a meeting is recording/processing.
3. **Start Meeting** / **End Meeting** (existing toggle), **Cancel Processing**
   (existing), **Retry Microphone** (visible only when the mic failed).
4. **Open Meetings** (⌘O when the menu is open), **Meeting Notes** (visible
   while recording/processing; opens Recording now).
5. Separator; **Profile ▸**, **Train My Voice…**, **Check Microphone…**,
   **Correct / Copy / Paste Last Dictation** (unchanged).
6. Separator; **Settings…** (opens Meetings › Settings), **Quit Speakeasy** ⌘Q.

"Start at Login" leaves the menu for Settings › General.

## Start paths and defaults

One helper, `meeting_options.default_options(calendar_event_key=None)`,
builds every UI start's `MeetingOptions`: all system audio, speakers Auto, and
`expected_voice_profile_names` = all enrolled voices when the
`identify_voices` setting is on (else empty). Used by the menu, the Next-up
card, Today (Record and Start Meeting) and the record-prompt banner. The engine
is not changed.

## Opening Meetings on a page

`MeetingsWindowController.show(view=None)` gains `view in {"recording", "settings",
("meeting", id)}`. The controller stores the request, shows the window, and
emits `meetings.navigate` with it; the page also asks `meetings.takeNavigation` on
load (covers a window created by this call). The page then selects Recording
now (only while recording/processing), opens the Settings sheet, or selects that meeting. `openMeetings_` in
`menubar.py` remains the single owner of the window.

## Removed

`speakeasy/ui/main_window.py`, `frontend/src/dock/`, the `dock.html` Vite
entry, the `dock` window size, the decorative rainbow `Waveform` component if
nothing else uses it, `app.listCaptureApplications`/`capture_application_options`
UI wiring if unused elsewhere, and their tests. `AppDelegate` shows Meetings on
launch and on Dock reopen. `build_app.sh`'s `dock.html` check becomes
`meetings.html`. README and AGENTS.md updated (Dock window → pill, menu, Today).

## Testing

- Pure-logic units (no AppKit): pill state model (state + health + progress →
  what the pill shows), agenda split, Next-up selection, default options,
  pill-position clamping, navigation request hand-off. Mutation-checked by the
  Opus reviewers.
- Bridge tests for new/moved methods (begin/end/cancel/retry/link/settings).
- Frontend node tests for the agenda split and Today rendering states.
- Data-level run of the built app with a temp HOME (start/stop via the menu
  path, pill state, notes navigation request).
- **On screen**: the pill, drawer, menu card and Today must be looked at. The
  user has declined screen control before; the plan ends with a short list of
  checks for the user, and the merge waits on their answer (or an explicit
  waiver, recorded in the plan).

## Out of scope

Claude provenance on summaries; global keyboard shortcuts; changing the
system-audio source mid-meeting; re-adding the per-app picker or speaker count;
Training and Microphone Check window redesigns; any engine/recorder change.
