# Record prompts: calendar and call detection — design

Date: 2026-10-01. Status: approved in chat (decisions below); plan
`docs/superpowers/plans/2026-10-01-record-prompts.md`.

Supersedes "Phase 4: Record prompt" in
`docs/superpowers/plans/2026-09-27-meeting-library-mcp.md` and the
"Record prompt" sections of `2026-09-26-meeting-library-mcp-design.md`
where they differ (native panel instead of the web page; call detection and
"call ended" added).

## Problem

Recording only starts when the user clicks Begin Meeting. From 24 Sep to
2 Oct 2026 the calendar held about 100 entries; 3 were linked to a
recording, and 7 of the 10 recordings in that window had no calendar event
(e.g. the two unscheduled "Project Black" calls on 1 Oct). One recording
ran to the 3-hour cap. Meetings that are never recorded cannot be
summarised, searched or followed up.

## Decisions (user, 1 Oct 2026)

| Topic | Decision |
|---|---|
| Triggers | Calendar event start **and** call detection (another app starts using the microphone). |
| Call-detection toggle | Separate setting, "Offer to record calls in other apps", default on. |
| Consent | Prompt only. Speakeasy never starts recording by itself; every recording starts from a click. |
| Call ends | Banner "Call ended — Stop recording?" with **Stop** and **Keep Recording**. No countdown; it waits for a click. |

## Behaviour

### Calendar offer
- Checked every 30 s from the cached `calendar_events` table (never
  EventKit) while "Offer to record calendar meetings" is on and the engine
  is `ready`.
- Eligible: `calendar_match.prompt_candidates` (not all-day, not declined,
  `start − 2 min ≤ now ≤ start + 5 min`, not prompted before) **and** at
  least one other named person (`event.people` non-empty). This skips
  personal blocks seen in real data ("pickup", "Outside office hours",
  "Meeting delay block").
- Several eligible events: the nearest start is shown; the others are in a
  "N more" menu whose items record that event instead.
- **Record** → `engine.begin_meeting(MeetingOptions(calendar_event_key=…))`;
  the banner reads "Recording" for 2 s, then hides.
- **Not Now**, 5 minutes without action, or the user starting a meeting by
  other means → hidden; every event in that offer is marked prompted.
- Prompted keys persist for the local day (`settings.json`
  `record_prompted`), so a relaunch does not re-ask.

### Call offer
- A background `call-probe` thread asks the bundled helper every 5 s which
  processes have Core Audio input running (`--list-input-pids`), removes
  Speakeasy and its child processes, and reduces the answer to one
  boolean. PIDs and app names are never logged, stored or shown.
- Another app using the mic for ≥ 10 s while the engine is `ready` and no
  banner shows → "Record this call?". If a calendar event matches now
  (`pick_event`, events with people only) its title is shown and Record
  links it; if that event was already prompted, nothing is shown.
- One offer per call: not offered again until the mic has been free for
  ≥ 60 s. A mic session that was being recorded counts as handled.
- The offer hides when the call ends (mic free ≥ 60 s), after 5 minutes,
  on Not Now, or when a meeting starts.

### Call ended
- Only while recording, only if another app used the mic during this
  recording, and only when call detection is on.
- Mic free ≥ 60 s → "Call ended — Stop recording?" **Stop** calls
  `engine.end_meeting()`; **Keep Recording** hides it until the mic is used
  again and then freed again. Mic use resuming hides it by itself.

### Banner
- Native AppKit, not a web view: a borderless, non-activating `NSPanel`
  (`NSWindowStyleMaskNonactivatingPanel`, `NSStatusWindowLevel`), 360 × 92 pt,
  top-right under the menu bar, glass background, `ClickyButton`s so the
  first click acts. It never takes keyboard focus from the meeting app.
  The unused web mock (`frontend/prompt.html`, `frontend/src/prompt/`) is
  removed.
- Fades in and out; one banner at a time.

### Settings
- Meetings › Settings gains "Offer to record calls in other apps" under the
  existing "Offer to record calendar meetings". Turning it off stops
  probing and hides call banners.

## Constraints kept
- Offline: the probe is a local Core Audio property read in the existing
  bundled helper; no network, no new dependency.
- Threading: EventKit stays on `calendar`; the probe thread never opens an
  audio stream and never touches the model or UI objects; all banner logic
  and UI run on the main thread.
- Privacy: only a boolean leaves `call_detect.py`. Capture-health schema
  unchanged.
- macOS < 14.2 or helper missing: call detection is silently unavailable;
  calendar offers still work.

## Out of scope
- Automatic recording, countdown auto-stop, linking old recordings to
  events, typed notes during meetings.
