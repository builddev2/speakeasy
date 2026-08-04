# Speakeasy native embedding — phase 2 of the frontend redesign

> **Status: implemented; historical phase-2 design record.** All three windows
> are now local WKWebView pages hosted by the native shell. The current bridge
> also exposes speaker relabeling and privacy-safe global/selected-app capture
> state added after this design. Use `README.md`, `AGENTS.md`, and current source
> for the operational contract; retain the body below as the approved snapshot.

## Context

Phase 1 (`docs/superpowers/specs/2026-07-09-frontend-vite-prototype-design.md`,
PR #4) built a standalone React + Vite + TypeScript prototype of the three
redesigned windows under `frontend/`, running in a browser with mock data.
This spec covers phase 2: embedding that frontend into the native macOS app,
replacing the PyObjC window implementations, and wiring real engine data.

Decisions made during brainstorming (2026-07-09, all confirmed by Jason):

1. **Prerequisite sequencing:** merge `meeting-transcription` (including the
   currently-uncommitted edits in the main checkout) to `master` first, then
   merge phase 1's PR #4, then branch phase 2 from the updated `master`.
2. **Scope:** the three windows only. `menubar.py` (status item + NSMenu),
   `overlay.py` (dictation waveform strip), and `permissions.py` stay native.
3. **Glass treatment:** real vibrancy hybrid — transparent WKWebView layered
   over a native `NSVisualEffectView` (behind-window blur). The page drops
   its fake desktop backdrop and `backdrop-filter` in embedded mode; the
   GlassPanel's semi-transparent gradient, hairline border, and inner
   highlight composite over the real native blur.
4. **Window chrome:** native traffic lights (existing `glass.py` style —
   hidden title, full-size content view). The page hides its drawn
   traffic-light row in embedded mode.
5. **Embedding technology:** raw `WKWebView` via PyObjC's WebKit framework
   binding. No pywebview (third-party window manager that would fight our
   NSVisualEffectView layering and threading rules), no app-shell rewrite.

## Goals

- `main_window.py`, `meetings_window.py`, and `training_window.py` render
  the phase-1 design via embedded WKWebViews, wired to real engine data.
- The dock panel reflects live engine state (idle/recording/meeting) and its
  actions (Begin/End Meeting, open Meetings/Training, Quit) drive the engine.
- The meetings window lists real transcripts from the meetings store, with
  working Copy / Export / Rename / Delete.
- The training window reaches feature parity with the current native
  training window.
- Fully offline at runtime, unchanged threading model, packaging updated so
  the built `.app` contains the frontend and needs no node/npm/network.

## Non-goals

- No changes to the dictation pipeline, engine, hotkey handling, overlay,
  menu-bar item, or permissions flow beyond the bridge hooks described here.
- No new design surfaces beyond the three windows (except the small set of
  training-flow states noted below).
- No web-based overlay; no replacement of native menus.

## Architecture

### Window host: `speakeasy/ui/webwindow.py`

One reusable host class, `WebWindow(title, width, height, page)`:

- NSWindow styled per the existing `glass.py` conventions: titled +
  closable + miniaturizable + full-size content view, hidden title, native
  traffic lights over edge-to-edge content.
- `NSVisualEffectView` (behind-window blending, active state) fills the
  content view — this supplies the real blur.
- A transparent `WKWebView` sits above it: window non-opaque where needed,
  webview background drawing disabled so the page's semi-transparent
  surfaces composite over the native blur.
- Content loads with `loadFileURL_allowingReadAccessToURL_`, granting read
  access at the `frontend/dist/` directory level so code-split JS/CSS chunks
  resolve. No server, no network.
- Python→JS calls made before the page finishes loading are queued and
  flushed on `didFinishNavigation` (a `webView:didFinishNavigation:`
  delegate) — otherwise early engine-state pushes would be silently lost.

The three window modules become thin instantiations of `WebWindow`
(dock 360×300, meetings 720×480, training 640×440). The `AppDelegate`
behaviors documented in CLAUDE.md — `applicationShouldTerminateAfterLastWindowClosed_`
returning False, Dock-click reopen — are unchanged.

### JS ↔ Python bridge

- **JS → Python:** a single `WKScriptMessageHandler` named `speakeasy`
  receives `{id, method, params}`. A Python-side dispatcher routes `method`
  to handlers. WKScriptMessageHandler delivers on the main thread; handlers
  that touch the engine submit to the appropriate executor per the threading
  model in CLAUDE.md (main thread never blocks on `worker`/`control`).
  Results post back to the page tagged with the same `id`.
- **Python → JS:** engine callbacks hop to the main thread exactly as today
  (`performSelectorOnMainThread`), then `evaluateJavaScript` invokes
  `speakeasyBridge.emit(event, payload)` in the page.
- **Frontend wrapper:** `frontend/src/bridge.ts` exposes
  `bridge.call(method, params) → Promise` and `bridge.on(event, handler)`.
  When `window.webkit.messageHandlers.speakeasy` is absent (plain browser),
  it falls back to the existing mock data — `npm run dev` remains a working
  design preview permanently.

### Bridge API surface

| Method / event | Direction | Purpose |
| --- | --- | --- |
| `app.getState()` | JS→Py | `{mode, profileName, readyMessage, meetingStartedAt}`; `mode ∈ idle, dictating, transcribing, meeting_recording, meeting_processing` |
| `app.beginMeeting()` / `app.endMeeting()` | JS→Py | drive the existing engine meeting API |
| `app.openWindow(name)` | JS→Py | open `meetings` / `training` windows |
| `app.quit()` | JS→Py | terminate the app |
| `state` (event) | Py→JS | pushed on every engine state transition; dock derives its ticking timer locally from `meetingStartedAt` |
| `meetings.list()` | JS→Py | `[{id, title, date, durationMin, speakerCount}]` from the meetings store |
| `meetings.get(id)` | JS→Py | meta + `lines: [{time, speakerNumber, speakerLabel, text}]` |
| `meetings.rename(id, name)` / `meetings.delete(id)` | JS→Py | store mutations (delete confirms in-page first) |
| `meetings.export(id)` | JS→Py | native `NSSavePanel` |
| `meetings.copy(id)` | JS→Py | `NSPasteboard` (more reliable than the webview clipboard API) |
| `training.listSessions()` | JS→Py | sessions + completion state from the profile store |
| `training.practice(...)` and session flow | JS→Py | see training parity below |
| `meetings.changed` (event) | Py→JS | pushed when a new transcript is saved while the window is open |

`TranscriptLine` gains a structured `speakerNumber: number` field (the
phase-1 review flagged the regex round-trip through the display string;
fixed here before real diarizer data lands).

### Embedded mode (frontend changes)

The page detects embedding by the presence of the native message handler.
When embedded it: hides the drawn traffic-light row; removes the fake
desktop backdrop and panel `backdrop-filter`; stretches the GlassPanel to
fill the viewport (the native window is the panel — rounded corners and
shadow come from AppKit). `vite.config.ts` sets `base: './'` so built asset
URLs are relative and work from `file://`. In a plain browser nothing
changes.

The dock panel's dev-only R-key toggle is removed in embedded mode; state
comes exclusively from `state` events.

### Training window parity

The phase-1 prototype only drew the training window's empty/prompt state.
Feature parity with the current native `training_window.py` requires a small
number of additional page states (phrase display, recording-in-progress,
result/feedback). These are the one place phase 2 adds UI beyond the
approved prototype; the implementation plan defines them concretely after
reading `training_window.py`, following the established design language
(tokens, component library) — no new visual vocabulary.

## Packaging & build

- `scripts/build_app.sh` gains a frontend step before PyInstaller:
  `npm ci && npm run build` in `frontend/`, failing fast if npm is missing
  (mirrors the fail-fast on missing diarization models). Build-time
  dependency acquisition is consistent with the existing pattern (model and
  ONNX files are fetched at build time, never at runtime);
  `package-lock.json` pins the npm tree.
- `packaging/Speakeasy.spec` bundles `frontend/dist/` into
  `Resources/frontend/`.
- `webwindow.py` resolves the dist path bundled-vs-from-source the same way
  existing bundled resources are resolved.
- `requirements.txt` gains an exactly-pinned `pyobjc-framework-WebKit` only
  if the existing pyobjc pins don't already provide it (verify during
  planning).

## Threading

No changes to the model in CLAUDE.md. New rules the bridge must obey:

- All WKWebView interaction (creation, `evaluateJavaScript`, load calls) is
  main-thread only.
- Bridge handlers run on the main thread and must return immediately;
  engine work is submitted to the existing executors, with results posted
  back to the page asynchronously via the queued-eval mechanism.
- Engine → UI pushes use the existing `performSelectorOnMainThread` hop.

## Testing

- Unit tests for the bridge dispatcher: method routing, unknown-method
  error, promise-id round-trip, pre-load call queuing/flush — with the
  webview mocked (repo rule: tests never touch real mic/model/webview).
- Frontend type-checks and builds via `npm run build`; the mock fallback in
  `bridge.ts` keeps the browser preview verifiable.
- Manual verification from source (`python -m speakeasy`): all three
  windows open, real translucency shows the desktop behind, begin/end
  meeting round-trips to a saved transcript, meetings actions work, training
  flow works end to end.
- Manual verification of the built bundle (`build_app.sh --install`): same
  checks, plus hotkey/permissions unaffected (the TCC gotcha in CLAUDE.md
  makes bundle-level verification non-optional).

## Risks

- **WKWebView transparency** APIs differ across macOS versions
  (`drawsBackground` KVC vs `underPageBackgroundColor`); the plan pins one
  approach and verifies on the target OS (Darwin 25 / macOS 15+).
- **Early `evaluateJavaScript` loss** — mitigated by the queued-flush design.
- **`file://` asset resolution** for code-split chunks — mitigated by
  directory-level read access + relative `base`; verified in the plan with a
  built bundle, not just dev.
- **Old window code removal**: `main_window.py`, `meetings_window.py`,
  `training_window.py` are replaced wholesale; `glass.py` remains (menu-bar
  adjacent UI and shared window styling still use it).
