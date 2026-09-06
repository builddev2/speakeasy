# AGENTS.md

Guidance for Codex working in this repo. The `README.md` documents the
product and architecture in full — this file is the short list of things that
are easy to get wrong.

## What it is

Speakeasy is a local, offline hold-to-talk dictation app for macOS (Apple
Silicon): hold Right ⌘, speak, release → transcribe on-device (Parakeet on MLX)
→ paste at the cursor. It runs as a menu-bar app (`python -m speakeasy`) with a
terminal front end (`--cli` / `--train`). Both drive the same `DictationEngine`.

It also transcribes whole **meetings**: "Begin Meeting" in the menu bar
records (up to three hours) to spooled temporary WAVs; "End Meeting" runs
chunked transcription + speaker diarization (sherpa-onnx, on-device) and saves
a speaker-labelled transcript. Only the transcript is persisted — audio is
deleted the moment processing ends (success, cancel, error, or crash-recovery
sweep at next launch). See `engine.py` (`begin_meeting`/`end_meeting`/
`_process_meeting`), `meeting_recorder.py`, `meetings.py`, `diarizer.py`.
On macOS 14.2+, meetings capture microphone and outgoing system audio as
separate temporary tracks through a bundled Core Audio process-tap helper.
Global system audio is the default; the Dock can instead select one eligible
application process (a browser selection means the browser, not one tab). The
mic is the known local user (`You`); only the system track is diarized, then
both transcripts are shifted by their first-buffer offsets and merged. A
selected process that exits never widens silently to global capture. macOS
14.0–14.1 or denied, silent, failed, or unavailable system capture falls back
visibly to the original mic-only diarization mode. See README's
[Meeting transcription](README.md#meeting-transcription) section.

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

The AppKit run loop owns the **main thread**. The two executors and capture
contexts are intentionally bounded and single-purpose:

- **`worker`** (1 thread) — loads *and* runs the Parakeet model. MLX pins GPU
  arrays to their creating thread, so the model must only ever be touched here.
  During a meeting it consumes completed microphone overlap chunks from a
  bounded writer-owned handoff. `_process_meeting` queues behind that job,
  reuses its result, finishes system-track ASR, then performs diarization,
  alignment, and save. A failed/overflowed handoff falls back to the complete
  spool. Only the track selected for diarization is loaded fully, and that
  array is released immediately afterwards. Diarization is CPU/onnxruntime
  with no pinning rule, but it stays on `worker` to keep the pipeline
  sequential rather than adding an executor.
- **`control`** (1 thread) — recorder `start()`/`stop()`, including
  `MeetingRecorder`. They must never run on the event-tap thread. Recorder
  stop runs under a timeout watchdog
  (`engine._stop_recorder_guarded`, and its meeting counterpart
  `_stop_meeting_recorder_guarded`). Immediate dictation owns CoreAudio in a
  prewarmed helper process; on timeout `force_close()` kills it so the next take
  can create a fresh helper. Its realtime callback only copies and
  `put_nowait`s into a bounded queue; the collector coalesces two-second blocks
  before the IPC/worker boundaries, whose capacities are expressed in seconds
  of audio. Optional streaming blocks may drop but complete batch audio is
  retained separately, and capture-queue overflow must fail the take. Takes of
  15 seconds or longer close the streaming context and use that complete audio
  for a batch final on the same worker; no draft is ever published. Meeting
  capture remains in-process and uses the process-wide
  `coreaudio.teardown` guard; never clear that marker from force-close.
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
  After each write, the writer alone builds 120-second chunks with the same
  15-second overlap as post-processing and offers them nonblockingly to a
  two-chunk ASR queue. Overflow invalidates that optimization and the complete
  spool is transcribed after stop.
  The writer thread exclusively owns WAV closure; `force_close()` signals it
  and uses a bounded join, never closing the file concurrently with a write.
- **system-audio helper process** — owns the macOS 14.2+ Core Audio process
  tap and private aggregate device. Its realtime callback only copies into a
  bounded queue; its separate writer queue downmixes/resamples to 16 kHz mono
  PCM16 and writes the temporary system WAV. Only bounded, non-content health
  events cross stdout. Global and selected-process capture share this path;
  selected-process disappearance is explicit and never falls back to global.
  Keeping tap teardown out of the main process prevents a wedged system stop
  from holding Speakeasy's microphone HAL lock. The control executor still
  serializes helper start/stop, and its stop is bounded/killed.
- Meeting cancellation is a polled `threading.Event`
  (`engine._meeting_cancel`), not a thread — `cancel_meeting_processing()`
  just sets it, and the worker checks it between transcription chunks and
  pipeline phases. No extra executor needed.

UI objects are main-thread only; engine callbacks hop threads via
`performSelectorOnMainThread` (see `ui/overlay.py`, `ui/menubar.py`).
The Dock and menu poll a fixed, privacy-safe capture-health schema: buffer and
nonzero-signal state, dropped-frame counts, writer lag/failure, helper exit,
outcome/fallback, mode, and scope. Never add transcript text, audio, app or
device names, PIDs, window titles, or other user content to it. `meetings.py`
whitelists the persisted keys; keep that boundary fixed when adding status UI.

## Build / run / test

```bash
.venv/bin/python -m speakeasy            # run the menu-bar app from source
.venv/bin/python -m speakeasy --cli      # terminal front end
.venv/bin/python -m pytest               # tests (pure logic + recorder/hotkey/engine units)
scripts/fetch_diarization_models.sh      # one-time: download + checksum the 2 diarization ONNX models
scripts/build_system_audio_helper.sh     # build the macOS 14.2+ process-tap helper
scripts/build_app.sh                     # build dist/Speakeasy.app (PyInstaller + bundled model)
scripts/build_app.sh --install           # build AND update /Applications in place
```

- Tests must not touch the real mic, model, or sherpa-onnx — mock the stream /
  recorder / helper / transcriber / diarizer (see `tests/test_recorder.py`,
  `tests/test_coreaudio_guard.py`, `tests/test_meeting_recorder.py`,
  `tests/test_system_audio.py`, `tests/test_engine_meeting.py`, and
  `tests/test_webbridge.py`). `speakeasy/diarizer.py` imports
  `sherpa_onnx` lazily inside `__init__` specifically so the module (and
  anything importing it, like `engine.py`) stays importable in tests without
  the dependency installed.
- Use `.venv/bin/python`, not the system python (Quartz/pyobjc etc. live there).
- `build_app.sh` fails fast if `models/diarization/*.onnx` is missing — run
  the fetch script first on a fresh checkout.

## Permissions / TCC gotcha

The app needs Microphone, Accessibility, and Input Monitoring, plus optional
Screen & System Audio Recording for headphone-compatible meetings. macOS keys the Accessibility /
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
  called at the top of `Transcriber.transcribe`). Long-form meeting methods
  (`transcribe_long` / `transcribe_long_wav`) are deliberately *not* trimmed —
  it would shift transcript timestamps out of sync with the diarization turns
  `meetings.align_speakers` maps them onto.
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
