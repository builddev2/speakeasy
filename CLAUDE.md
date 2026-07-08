# CLAUDE.md

Guidance for Claude Code working in this repo. The `README.md` documents the
product and architecture in full — this file is the short list of things that
are easy to get wrong.

## What it is

Speakeasy is a local, offline hold-to-talk dictation app for macOS (Apple
Silicon): hold Right ⌘, speak, release → transcribe on-device (Parakeet on MLX)
→ paste at the cursor. It runs as a menu-bar app (`python -m speakeasy`) with a
terminal front end (`--cli` / `--train`). Both drive the same `DictationEngine`.

## Hard constraints

- **Fully offline, always.** No network calls at runtime, ever. Prefer removing
  external dependencies over adding them; the model and every dep are bundled.
  Dependencies are pinned exactly in `requirements.txt` (supply-chain safety) —
  don't unpin or add casually.
- **Apple Silicon only** (MLX/Metal).

## Threading model (load-bearing — don't violate)

The AppKit run loop owns the **main thread**. Three worker contexts, each
single-purpose:

- **`worker`** (1 thread) — loads *and* runs the Parakeet model. MLX pins GPU
  arrays to their creating thread, so the model must only ever be touched here.
- **`control`** (1 thread) — recorder `start()`/`stop()`. CoreAudio's stop
  deadlocks against the HAL mutex if called on the event-tap thread, so it must
  never run there. This thread is a single serialization point: if a recorder
  call blocks, the whole hotkey pipeline freezes — recorder stop runs under a
  timeout watchdog (`engine._stop_recorder_guarded`) for exactly this reason.
- **hotkey tap thread** — the raw Quartz `CGEventTap`. Callbacks must return
  instantly; they only `submit()` to `control`. No Text Input Source calls from
  here (a listen-only tap avoids the TSM main-queue assertion that SIGTRAPs the
  process — this is why pynput was removed; do not reintroduce it).

UI objects are main-thread only; engine callbacks hop threads via
`performSelectorOnMainThread` (see `ui/overlay.py`, `ui/menubar.py`).

## Build / run / test

```bash
.venv/bin/python -m speakeasy            # run the menu-bar app from source
.venv/bin/python -m speakeasy --cli      # terminal front end
.venv/bin/python -m pytest               # tests (pure logic + recorder/hotkey/engine units)
scripts/build_app.sh                     # build dist/Speakeasy.app (PyInstaller + bundled model)
scripts/build_app.sh --install           # build AND update /Applications in place
```

- Tests must not touch the real mic or model — mock the stream / recorder (see
  `tests/test_recorder.py`, `tests/test_engine_watchdog.py`).
- Use `.venv/bin/python`, not the system python (Quartz/pyobjc etc. live there).

## Permissions / TCC gotcha

The app needs Microphone, Accessibility, and Input Monitoring, granted to the
`.app` (or to Terminal when run from source). macOS keys the Accessibility /
Input Monitoring grants on the code signature. **Replacing the whole
`/Applications/Speakeasy.app` (e.g. `rm -rf` + `mv`) can drop the grant and
force a re-prompt; a dead Right ⌘ hotkey + a re-prompt is this, not a code
bug.** Install in place instead (`build_app.sh --install`). To recover a stale
grant: `tccutil reset ListenEvent com.jasonchiu.speakeasy` and
`tccutil reset Accessibility com.jasonchiu.speakeasy`, then relaunch and
re-approve. A stable self-signed `Speakeasy Dev` cert keeps grants across
rebuilds.

## Conventions

- **Branch before executing a plan**; don't commit/push unless asked. Main
  branch is `master`.
- **UI follows Apple glassmorphism** (translucent "glass" styling — see
  `ui/glass.py`, `ui/overlay.py`).
- Match the surrounding code's comment density and style; comments here explain
  *why* a non-obvious constraint exists (threading, TCC, CoreAudio), not what
  the next line does.
