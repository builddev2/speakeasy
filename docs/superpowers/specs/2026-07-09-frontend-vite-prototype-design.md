# Speakeasy frontend prototype — React/Vite/TypeScript (phase 1 of 2)

> **Status: implemented; historical phase-1 design record.** The standalone
> mock prototype described below was subsequently embedded into the native app
> and wired to real data. Current production behavior and dependency versions
> live in `README.md`, `AGENTS.md`, `frontend/`, and `speakeasy/ui/`; the body
> below intentionally retains the original phase boundary and decisions.
> Later backend work added isolated microphone capture and worker-owned live
> dictation streaming/fallback; it is intentionally outside this UI snapshot.

## Context

Speakeasy's UI is currently 100% native PyObjC/AppKit (`speakeasy/ui/glass.py`,
`menubar.py`, `meetings_window.py`, `training_window.py`, `main_window.py`).
The visual design has drifted from the intended look. Two design references
were provided:

- `~/Downloads/speakeasy-reference.html` — a clean, annotated static HTML/CSS
  spec of all 4 window states (design tokens, exact measurements, colors).
- `~/Downloads/Speakeasy Dock (standalone).html` — a Claude Artifact export
  of the same design, which additionally reveals the intended configurable
  props (`operatorName`, `readyMessage`, `meetingElapsed`, `showSpeakerColors`)
  and the mock transcript data shape.

This is a two-phase project:

1. **This spec** — build a standalone React + Vite + TypeScript prototype
   that pixel-matches the reference design, running in a normal browser via
   `npm run dev` with mock data. No Python/PyObjC changes at all.
2. **A later spec** — embed the built prototype into the native app shell
   (e.g. pywebview/WKWebView), replace the existing PyObjC windows, wire real
   `DictationEngine`/meeting data, and update `scripts/build_app.sh`
   packaging. Out of scope here; noted only so phase-1 structural decisions
   don't have to be redone.

## Goals

- Pixel-match `speakeasy-reference.html`'s 4 window states: dock idle, dock
  recording, meetings (transcript browser), training (voice training).
- Plain CSS (CSS Modules), no Tailwind, no component libraries — custom
  glassmorphism per the reference tokens.
- Structure the app so phase 2 can embed each window with minimal rework.

## Non-goals

- No integration with the Python engine, no real audio/transcription data.
- No automated test suite (visual prototype; verification is manual/visual
  against the two reference files).
- No router, no backend, no persistence.

## Architecture

New top-level `frontend/` folder, sibling to `speakeasy/`, fully independent
of the Python app:

```
frontend/
  index.html              # dev-only picker linking to the 3 windows
  dock.html                → src/dock/main.tsx
  meetings.html             → src/meetings/main.tsx
  training.html             → src/training/main.tsx
  src/
    dock/       (App.tsx, App.module.css, main.tsx)
    meetings/   (App.tsx, App.module.css, main.tsx)
    training/   (App.tsx, App.module.css, main.tsx)
    components/  (GlassPanel, TitleBar, AppIdentity, StatusDot,
                   PrimaryButton, GlassButton, ActionButton, Waveform)
    styles/tokens.css   # :root custom properties, verbatim from the reference
    mock/        (meetings.ts — seed meetings + transcripts)
  package.json, vite.config.ts, tsconfig.json
```

**Desktop backdrop:** every window sits over a fixed, full-viewport backdrop
(`#0c1310` base + 4 soft radial color blobs — coral, blue, purple, green —
per the reference's `.desktop-bg`) so the glass panels have something behind
them to blur; without it `backdrop-filter` has nothing to blur and the
translucency won't read. This lives in `styles/tokens.css` as global
`body`/`#root` rules (not a component), applied to all three window entries.
The dev-only `index.html` picker does not need it.

**Why multi-page instead of a single-page app with a router:** phase 2 embeds
this as three separate native windows (dock panel, meetings, training), each
loaded independently by its own pywebview/WKWebView instance. A Vite
multi-page build (`build.rollupOptions.input` = the three HTML entries)
produces three independent bundles that map 1:1 onto those windows, so phase
2 just points each native window at its already-existing entry file instead
of extracting views out of a router.

Tooling: Vite + React + TypeScript, npm. Plain CSS Modules per component
(`*.module.css`) plus one global `styles/tokens.css` for shared custom
properties (colors, radii, shadows, speaker palette) — every component reads
these vars, no hardcoded hex outside this file.

## Components

Shared, presentational-only (props in, JSX out — no fetch, no engine calls):

- `GlassPanel` — frosted window chrome: `border-radius: 16px`,
  `backdrop-filter: blur(40px) saturate(180%)`, the panel gradient and
  box-shadow stack from the reference. Takes width/height + children.
- `TitleBar` — traffic lights (11px, `#ff5f57`/`#febc2e`/`#28c840`) + optional
  title text; `plain` variant (no title, no bottom border) for the dock panel.
- `AppIdentity` — skull icon (💀 in a 34px dark rounded square, desaturated)
  + wordmark + status-line slot.
- `StatusDot` — green/rec/amber dot; `rec` variant pulses
  (`@keyframes recdot`).
- `PrimaryButton` — full-width, 46px, 12px radius, coral gradient
  (`rgba(235,152,93,0.92)` → `rgba(214,120,68,0.80)`) by default; `danger`
  prop swaps to the red gradient (`rgba(255,90,78,0.92)` →
  `rgba(226,59,50,0.80)`) for End Meeting.
- `GlassButton` — secondary frosted button (`rgba(255,255,255,0.08)` bg,
  0.5px `rgba(255,255,255,0.14)` border), used as the dock's Meetings/Train
  Profile pair.
- `ActionButton` — 34px meetings action-row buttons; `strong` (Copy) and
  `danger` (Delete) variants.
- `Waveform` — the signature 26-bar rainbow hero: hue stepped 0→300° across
  bars, blurred rainbow bloom layer behind, `@keyframes pill`/`bloom` —
  ported directly from the reference file's bar-generation logic (same hue
  formula, same per-bar animation duration/delay jitter).

## Windows

- **`dock`** (360×300): local `useState<'idle' | 'recording'>`, toggled via a
  dev-only keyboard shortcut (`R`) — a preview aid only; phase 2 drives this
  from the real engine state, not part of the shipped design. Idle: identity
  block, green `Ready — {operatorName}` status, full-width `PrimaryButton`
  "Begin Meeting", `GlassButton` pair (Meetings / Train Profile), "Quit
  Speakeasy" link bottom-right. Recording: rec status dot pulsing +
  `· mm:ss` mono timer (ticks via `setInterval`, cosmetic only in this
  phase), `Waveform` hero, amber "Dictation paused" pill, red `PrimaryButton`
  "End Meeting", the same secondary pair dimmed to 40% opacity and
  non-interactive.
- **`meetings`** (720×480): local `selectedMeetingId` state, defaulting to
  the first mock meeting. Left sidebar (232px) lists meetings (title +
  "X min · Y speakers" subtitle), active item gets the coral-tinted
  background/border. Right pane: title, meta row (date · duration ·
  speakers), scrollable mono transcript panel (`rgba(0,0,0,0.30)` bg, bottom
  fade-out gradient), action row (Copy / Export / Rename / right-aligned
  red-tinted Delete). Copy/Export/Rename/Delete are no-op stubs
  (`console.log`) — no file I/O in this phase.
- **`training`** (640×440): static per the reference. Left sidebar (222px):
  3 checked sessions (coral check icon), 1 "current" session (bullet + gold
  star, subtly highlighted row), 1 truncated/dimmed session. Right pane:
  dim intro line, balanced-wrap headline, bottom input row — a real
  controlled `<input>` styled with the coral focus ring (`1.5px #E8955A`
  border + `0 0 0 3.5px rgba(235,152,93,0.25)` glow, not system blue) so
  focus is genuinely interactive, plus a coral `Practice` button (no-op
  stub).

Configurable props threaded top-of-component (so phase 2 can pass real
values in without restructuring): `operatorName` (default `"Jason"`),
`readyMessage` (default `"Ready"`), and for meetings, `colorCodeSpeakers:
boolean` (default `true`) — toggles per-speaker transcript hues vs. a flat
dim color for all speaker labels.

## Mock data

`src/mock/meetings.ts` exports 3 seed meetings (title, subtitle, duration,
speaker count) with a transcript per meeting shaped
`{ time: string, speaker: string, text: string }[]`. The first meeting
("Meeting — Jul 8, 7:43 PM") gets the 10 reference lines verbatim; the other
two get 2–3 shorter invented lines each. Speaker color assignment cycles the
7-color palette (`#6fd8b0, #F0B34A, #C08CF2, #6EA8FF, #7ED97E, #F0895A,
#DE8FB4`) by speaker index, same `pal[(n-1) % pal.length]` formula as the
reference.

`index.html` is a plain, unstyled dev-only page linking to `/dock.html`,
`/meetings.html`, `/training.html` — a local nav convenience for `npm run
dev`, not part of the shipped design and not something phase 2 embeds.

## Testing / verification

No automated test suite — this is a visual prototype with no logic worth
unit-testing yet. Verification is manual: run `npm run dev`, open each of the
three entries, and compare side-by-side against `speakeasy-reference.html`'s
four window states (colors, spacing, radii, shadows, animation).
