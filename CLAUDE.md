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
  `_stop_meeting_recorder_guarded`) for exactly this reason. But the watchdog
  only frees `control`; the abandoned `stop()` keeps running inside the HAL,
  and **opening a new stream while it runs deadlocks both on the HAL mutex**
  (a real hang we hit: a long take's stop wedged, the watchdog moved on, and
  the next `start()` opened a second stream straight into the deadlock). So
  every CoreAudio teardown is marked in flight on the **process-wide** guard in
  `speakeasy/coreaudio.py` (`teardown.in_progress()`), and every open consults
  it first: while a teardown is in flight, `Recorder.prewarm()` refuses
  silently and `Recorder.start()` / `MeetingRecorder.start()` raise
  `RecorderBusy`, so the engine drops that take (or that meeting) and stays
  idle. The mic self-recovers when the wedged stop finally returns (the marker
  clears), or stays unavailable until relaunch if it never does — either way
  the pipeline never freezes.
  **The guard is process-wide because the HAL mutex is** (per process/device),
  and the two recorders are separate objects on the same device: a wedged
  `MeetingRecorder.stop()` deadlocks a dictation `Recorder.start()` just as
  readily as one of its own, and vice versa. A per-instance flag would miss
  exactly those cross-recorder pairs. Dictation and meetings being mutually
  exclusive does *not* close this — that only prevents concurrent *recording*,
  while the watchdog by design leaves a teardown unwinding in the HAL long
  after the meeting is "over" and dictation has re-armed. Never open, reopen, or
  start a CoreAudio input stream without checking `coreaudio.teardown.in_flight`
  first; never clear the marker from a `force_close()` path (the abandoned stop
  is still in there — that is the whole point).
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
- **Dictation audio is silence-trimmed before inference** (`preprocess.py`,
  called at the top of `Transcriber.transcribe`). Meetings (`transcribe_long`)
  are deliberately *not* trimmed — it would shift transcript timestamps out of
  sync with the diarization turns `meetings.align_speakers` maps them onto.
  `trim_silence` never returns an empty array for non-empty input (all-silence
  clips pass through unchanged, so the model warmup still sees its full second).
- **Profiles correct two ways** (`profiles.py` `apply()`): exact learned
  `corrections` (regex) run first, then a `vocabulary` fuzzy snap gated on
  *both* a phonetic key (`phonetics.py`) and a `difflib` ratio — high-precision
  by design, so a real word isn't rewritten to a look-alike. Anything that
  mutates a profile's `vocabulary`/`corrections` must call `_rebuild()`
  afterwards (as `add_word`/`add_correction` do) or the fuzzy index and the
  protected-word set go stale until the next reload.
- **The menu-bar status glyph is a custom template `NSImage`** — a skull at rest
  (`_skull_image()` in `ui/menubar.py`), `mic.fill`/`waveform` while active.
  macOS has no `skull` SF Symbol, so it's drawn in code; keep any new menu-bar
  icon a *template* so it tints to the bar's light/dark/accent like the SF
  Symbols do.
- **The app runs `NSApplicationActivationPolicyRegular`** (normal Dock icon +
  app switcher), not `LSUIElement`/Accessory — set both at runtime
  (`run_app()` in `ui/menubar.py`) and in the packaging spec's `info_plist`,
  to avoid a Dock-icon flash on launch. The status item is still the primary
  interface; `ui/main_window.py` is a Dock-reachable fallback with the same
  core actions, for when the status item is hidden by menu-bar overflow.
  `AppDelegate.applicationShouldTerminateAfterLastWindowClosed_` returns
  `False` so closing that window doesn't quit the background service, and
  `applicationShouldHandleReopen_hasVisibleWindows_` reopens it on a Dock
  click.
- **The three windows are web-rendered** (`ui/webwindow.py` hosts the built
  `frontend/` pages in transparent WKWebViews over the usual glass windows;
  `ui/webbridge.py` is the pure-logic bridge half, tested without WebKit).
  All webview calls are main-thread only — engine callbacks keep hopping via
  `performSelectorOnMainThread`, then `evaluateJavaScript`. Building the app
  (and running from source) needs `npm --prefix frontend run build` first;
  node/npm is a build-time-only dependency, nothing web ships beyond the
  static files in `Resources/frontend/`.
- Match the surrounding code's comment density and style; comments here explain
  *why* a non-obvious constraint exists (threading, TCC, CoreAudio), not what
  the next line does.
