# CLAUDE.md

Guidance for Claude Code working in this repo. The `README.md` documents the
product and architecture in full — this file is the short list of things that
are easy to get wrong.

## What it is

Speakeasy is a local, offline hold-to-talk dictation app for macOS (Apple
Silicon): hold Right ⌘, speak, release → transcribe on-device (Parakeet on MLX)
→ paste at the cursor. It runs as a menu-bar app (`python -m speakeasy`) with a
terminal front end (`--cli` / `--train`). Both drive the same `DictationEngine`.

It also transcribes whole **meetings**: "Begin Meeting" in the menu bar
records (up to a couple of hours) to a spooled temp WAV; "End Meeting" runs
chunked transcription + speaker diarization (sherpa-onnx, on-device) and saves
a speaker-labelled transcript. Only the transcript is persisted — audio is
deleted the moment processing ends (success, cancel, error, or crash-recovery
sweep at next launch). See `engine.py` (`begin_meeting`/`end_meeting`/
`_process_meeting`), `meeting_recorder.py`, `meetings.py`, `diarizer.py`.
**Known limitation:** capture is mic-only — on a Zoom/Teams call, remote
participants are only transcribed if played through speakers, not headphones
(see README's [Meeting transcription](README.md#meeting-transcription)
section). A future enhancement could add system-audio capture via
ScreenCaptureKit (macOS 13+, needs the Screen Recording permission).

## Hard constraints

- **Fully offline, always.** No network calls at runtime, ever. Prefer removing
  external dependencies over adding them; the model and every dep are bundled.
  Dependencies are pinned exactly in `requirements.txt` (supply-chain safety) —
  don't unpin or add casually. The two diarization ONNX models are downloaded
  once at dev/build time (`scripts/fetch_diarization_models.sh`, checksum
  verified) and bundled into the app — never fetched at runtime, same pattern
  as the speech model.
- **Apple Silicon only** (MLX/Metal).

## Threading model (load-bearing — don't violate)

The AppKit run loop owns the **main thread**. Three worker contexts, each
single-purpose:

- **`worker`** (1 thread) — loads *and* runs the Parakeet model. MLX pins GPU
  arrays to their creating thread, so the model must only ever be touched here.
  The whole meeting-processing pipeline (`_process_meeting`: chunked
  transcription → diarization → alignment → save) also runs as one job here —
  diarization is CPU/onnxruntime with no pinning rule, but it stays on
  `worker` to keep the pipeline strictly sequential rather than adding a
  fourth executor.
- **`control`** (1 thread) — recorder `start()`/`stop()`, including
  `MeetingRecorder`. CoreAudio's stop deadlocks against the HAL mutex if
  called on the event-tap thread, so it must never run there. This thread is
  a single serialization point: if a recorder call blocks, the whole hotkey
  pipeline freezes — recorder stop runs under a timeout watchdog
  (`engine._stop_recorder_guarded`, and its meeting counterpart
  `_stop_meeting_recorder_guarded`) for exactly this reason.
- **hotkey tap thread** — the raw Quartz `CGEventTap`. Callbacks must return
  instantly; they only `submit()` to `control`. No Text Input Source calls from
  here (a listen-only tap avoids the TSM main-queue assertion that SIGTRAPs the
  process — this is why pynput was removed; do not reintroduce it). The tap is
  torn down entirely (`_listener.stop()`) for the duration of a meeting —
  dictation and meeting recording never run concurrently — and rebuilt when
  `_process_meeting` finishes, in its `finally`.
- **`meeting-writer`** (1 thread per meeting, spawned by `MeetingRecorder`) —
  drains a bounded queue fed by the mic callback into the spool WAV. The
  audio callback itself must never touch disk or block; it only
  `put_nowait`s into the queue and drops frames if the writer falls behind.
- Meeting cancellation is a polled `threading.Event`
  (`engine._meeting_cancel`), not a thread — `cancel_meeting_processing()`
  just sets it, and the worker checks it between transcription chunks and
  pipeline phases. No extra executor needed.

UI objects are main-thread only; engine callbacks hop threads via
`performSelectorOnMainThread` (see `ui/overlay.py`, `ui/menubar.py`).

## Build / run / test

```bash
.venv/bin/python -m speakeasy            # run the menu-bar app from source
.venv/bin/python -m speakeasy --cli      # terminal front end
.venv/bin/python -m pytest               # tests (pure logic + recorder/hotkey/engine units)
scripts/fetch_diarization_models.sh      # one-time: download + checksum the 2 diarization ONNX models
scripts/build_app.sh                     # build dist/Speakeasy.app (PyInstaller + bundled model)
scripts/build_app.sh --install           # build AND update /Applications in place
```

- Tests must not touch the real mic, model, or sherpa-onnx — mock the stream /
  recorder / transcriber / diarizer (see `tests/test_recorder.py`,
  `tests/test_engine_watchdog.py`, `tests/test_meeting_recorder.py`,
  `tests/test_engine_meeting.py`). `speakeasy/diarizer.py` imports
  `sherpa_onnx` lazily inside `__init__` specifically so the module (and
  anything importing it, like `engine.py`) stays importable in tests without
  the dependency installed.
- Use `.venv/bin/python`, not the system python (Quartz/pyobjc etc. live there).
- `build_app.sh` fails fast if `models/diarization/*.onnx` is missing — run
  the fetch script first on a fresh checkout.

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
- **The menu-bar status glyph is a custom template `NSImage`** — a skull at rest
  (`_skull_image()` in `ui/menubar.py`), `mic.fill`/`waveform` while active.
  macOS has no `skull` SF Symbol, so it's drawn in code; keep any new menu-bar
  icon a *template* so it tints to the bar's light/dark/accent like the SF
  Symbols do.
- Match the surrounding code's comment density and style; comments here explain
  *why* a non-obvious constraint exists (threading, TCC, CoreAudio), not what
  the next line does.
