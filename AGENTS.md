# AGENTS.md

Guidance for Codex working in this repo. The `README.md` documents the
product and architecture in full — this file is the short list of things that
are easy to get wrong.

## What it is

Speakeasy is a local, offline hold-to-talk dictation app for macOS (Apple
Silicon): hold Right ⌘, speak, release → transcribe on-device (Parakeet on MLX)
→ paste at the cursor. It runs as a menu-bar app (`python -m speakeasy`) with a
terminal front end (`--cli` / `--train`). Both drive the same `DictationEngine`.

It also transcribes whole **meetings**: "Start Meeting" in the menu bar
records (up to three hours) to spooled temporary WAVs; "End Meeting" runs
chunked transcription + speaker diarization (sherpa-onnx, on-device) and saves
a speaker-labelled transcript. Only the transcript is persisted — audio is
deleted the moment processing ends (success, cancel, error, or crash-recovery
sweep at next launch). Meetings persist to a SQLite library at
`settings.library_path()` via `MeetingLibrary` (`meeting_library.py`,
`meeting_store.py`); legacy per-meeting JSON is imported once, by
`meeting_import.py`, and the original files are archived to
`meetings/legacy-json/`. Never write meeting JSON again. See `engine.py`
(`begin_meeting`/`end_meeting`/`_process_meeting`), `meeting_recorder.py`,
`meetings.py`, `diarizer.py`.
- Every `MeetingLibrary` call opens its own short-lived SQLite connection —
  never share one across threads or processes (WAL plus a busy timeout make
  that safe for the app and, later, the MCP server writing concurrently).
- Full-text search and the calendar cache are derived, not master data.
  `--rebuild-index` rebuilds the search indexes from the meetings/segments/
  notes tables, but only clears the calendar cache — it does not rebuild it;
  the phase-3 calendar sync refills it from Calendar.app.
- The capture-health whitelist (`meetings.filter_capture_health`) still
  applies at save — only the fixed privacy-safe schema reaches the database,
  never raw provenance.
- The known-source ("You") mic track has no diarization turns to break it up,
  so it is split on real pauses (>1.5 s) and capped at 60 s per segment
  (`config.KNOWN_SPEAKER_MAX_GAP_SECONDS` /
  `KNOWN_SPEAKER_MAX_SEGMENT_SECONDS`, `meetings.known_speaker_segments`) —
  otherwise a whole meeting would land as one segment timestamped 00:00:00.
- Diarized (remote) segments use the same 1.5 s pause rule
  (`config.DIARIZED_MAX_GAP_SECONDS`) under the 60 s cap, and in two-track
  meetings also split where a "You" segment starts (`align_speakers(...,
  break_at=...)`, times converted to system-track time with the track
  offsets) so replies are not listed after the remote speech they answered.
On macOS 14.2+, meetings capture microphone and outgoing system audio as
separate temporary tracks through a bundled Core Audio process-tap helper.
Global system audio is the default; the engine option to select one eligible
application process still exists (a browser selection means the browser, not one tab) but no UI currently sets it. The
mic is the known local user (`You`); only the system track is diarized, then
both transcripts are shifted by their first-buffer offsets and merged. Mic
sentences that echo system-track words within the bleed window are removed
before segmenting (`meetings.remove_mic_echo`). A
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
- **`--mcp` stdout is JSON-RPC only.** `mcp_server.main()` keeps a private dup
  of fd 1 for the protocol, then `dup2`s stderr onto fd 1 and sets
  `sys.stdout = sys.stderr`. Nothing may print to the real stdout (a stray line
  corrupts the stream); `test_stray_output_in_a_tool_never_reaches_stdout`
  guards it.
- **MCP tools never return capture fields** (capture health, device or app
  names, PIDs): they describe the user's machine, not the meeting. Writes
  are `save_notes` (only the fields given, `updated_by` "claude"; its tags are
  source `claude` and never displace `user` tags or re-add suppressed ones),
  `tag_meetings` (source `user`) and `manage_tags`. Every tag name resolves
  through `tag_names.tag_slug` then `tag_aliases`; never insert into `tags`
  directly. `pending_summaries` is read-only. The only other summary-queue
  writer is the app (`MeetingLibrary.request_summary` via
  `meetings.requestSummary`); `save_notes` with a non-empty summary clears a
  request.
- **`--mcp` stays import-light** (no AppKit, MLX, sherpa-onnx or UI modules);
  `test_mcp_mode_imports_nothing_heavy` in `tests/test_mcp_server.py` enforces it.
- **Calendar privacy.** EventKit is used only on the `calendar` thread
  (`calendar_sync.py`); the engine and MCP server read the cached
  `calendar_events` table. Event notes, location, URLs and structured
  locations are never read or stored (`SyncedEvent` has no field for them).
  Calendar access is never requested at launch, wake or on a timer; only the
  user's Connect Calendar click may show the macOS prompt, and revoked access
  clears the cache.
- **Record prompts never record.** Every `begin_meeting`/`end_meeting` from
  `record_prompt_controller.py` follows a banner click. `call_detect.py`
  reduces the helper's input PIDs to one boolean (is a process other than
  Speakeasy and its children using the mic) and nothing logs, stores or shows
  them. The banner panel is native, borderless and non-activating
  (`orderFrontRegardless()`); never make it key.
- **Test isolation.** Tests never touch the real App Support folder: the
  autouse `isolated_home` fixture in `tests/conftest.py` redirects it. Never
  bypass it, and never create an `EKEventStore` in a test.

## Threading model (load-bearing — don't violate)

The AppKit run loop owns the **main thread**. The two executors and capture
contexts are intentionally bounded and single-purpose:

- **`worker`** (1 thread) — loads *and* runs the Parakeet model. MLX pins GPU
  arrays to their creating thread, so the model must only ever be touched here.
  During a meeting it consumes completed microphone overlap chunks from a
  bounded writer-owned handoff. `_process_meeting` queues behind that job,
  reuses its result, finishes system-track ASR, then performs diarization,
  alignment, and save. A failed/overflowed handoff falls back to the complete
  spool. Diarization (diarize → speaker merge → voice identification) runs
  in a spawned child process per meeting (`speakeasy/diarization_process.py`,
  `engine.diarization_runner`); the worker job blocks on it, so the pipeline
  stays sequential. The child reads the spool itself and exits, which returns
  onnxruntime's arenas and freed buffers to macOS; keeping the models in-process
  held 300–800 MB after meetings (docs/model-memory.md). A failed child
  (`DiarizationFailed`) saves the transcript with one speaker label and records
  `diarization_status`/`diarization_failure` in capture health; cancel
  terminates the child (measured ≤0.08 s). A normal quit kills the child from
  `Engine.shutdown()` via `ChildDiarizationRunner.terminate_active()`, before
  the hard exit that skips other cleanup. The parent-death watchdog in the
  child is only the backstop for crashes and force-quits. It is a Python
  thread, so it cannot run while sherpa-onnx's segmentation holds the GIL: the
  child can then linger until the first progress callback (~58 s into a
  29.3-min meeting), then exits within ~0.4 s.
  `Transcriber.__init__` also caps MLX's process-wide buffer cache
  (`config.MLX_CACHE_LIMIT_BYTES`, 256 MiB) before loading; without it the
  idle footprint grows by GBs after meetings. Don't remove it
  (docs/model-memory.md).
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
- **`calendar`** (1 thread, `calendar_sync.CalendarSync`) — the only place
  EventKit is used. Syncs Calendar.app into `calendar_events` at launch (only
  if access is already granted), after wake, on `EKEventStoreChangedNotification`
  and every 5 minutes. Never touches audio or the model. The engine never calls
  EventKit: it reads the cached table.
- **`call-probe`** (1 daemon thread, `call_detect.CallProbe`) — every 5 s while
  wanted, runs the system-audio helper's `--list-input-pids` (1 s timeout) and
  `pgrep -P`, and hands a bool to the main thread. It never opens audio and
  never touches the model, executors or UI. `call_detect.MicWatch` keeps the
  ignored PIDs (apps already on the mic at the first answer, or behind a
  waved-off offer) inside `call_detect.py`; the controller only sends
  `ignore_current()` (waved-off offer) and `forgive_waved()` (recording starts;
  waved-off apps count again, the first-answer baseline stays ignored).
- **`ax-warmup`** (1 thread, `ax_warmup.AccessibilityWarmer`) — on every app
  activation, one focus query; if the app answers NoValue and
  `AXEnhancedUserInterface` is settable and off, writes it once (Chromium
  returns NotImplemented yet applies it; trust the read-back). Fresh Electron
  apps need ~2 s after that write to answer focus, longer than key-down
  retries. AX metadata only, 0.1 s timeouts; never main, `worker` or `control`.
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

The MCP server (`--mcp`) is a separate process with its own short-lived SQLite
connections. The Meetings window learns of its writes by polling
`MeetingsBridge.poll_changed()` every 10 s while visible: a `LibraryWatcher`
checks `PRAGMA data_version` on one main-thread-only connection.

UI objects are main-thread only; engine callbacks hop threads via
`performSelectorOnMainThread` (see `ui/overlay.py`, `ui/menubar.py`).
The recording pill's drawer and the menu poll a fixed, privacy-safe capture-health schema: buffer and
nonzero-signal state, dropped-frame counts, writer lag/failure, helper exit,
outcome/fallback, mode, scope, output-route category, and mic echo removal
count. Never add transcript text, audio, app or
device names, PIDs, window titles, or other user content to it. `meetings.py`
whitelists the persisted keys; keep that boundary fixed when adding status UI.

## Build / run / test

```bash
.venv/bin/python -m speakeasy            # run the menu-bar app from source
.venv/bin/python -m speakeasy --cli      # terminal front end
.venv/bin/python -m pytest               # tests (pure logic + recorder/hotkey/engine units)
npm --prefix frontend test           # frontend pure-helper tests (node --test, no deps)
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
  Engine meeting tests inject `InProcessDiarizationRunner(fake_diarizer)` as
  `engine.diarization_runner`; child-process tests spawn stub targets from
  `tests/diarization_child_stubs.py` and never load sherpa-onnx.
- `build_app.sh` also runs `npm --prefix packaging/mcpb ci`, stages
  `packaging/mcpb/` and packs it with the pinned, build-time-only
  `@anthropic-ai/mcpb` (2.1.2) into
  `Speakeasy.app/Contents/Resources/Speakeasy.mcpb`; the Connect Claude sheet
  opens that file. It holds only `manifest.json` and `server/speakeasy-mcp`.
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
  to avoid a Dock-icon flash on launch. There is no separate Dock window:
  the status item menu (with its Next-up card) is the primary interface, and
  the Meetings window is the Dock-reachable window, opened on launch and on a
  Dock click via `open_meetings(None)`. Recordings show the native
  non-activating floating pill (`ui/pill_panel.py` view,
  `ui/pill_controller.py` controller, pure rules in `ui/pill_model.py`); the
  pill's Cancel has a Discard/Keep confirm.
  `AppDelegate.applicationShouldTerminateAfterLastWindowClosed_` returns
  `False` so closing the window doesn't quit the background service, and
  `applicationShouldHandleReopen_hasVisibleWindows_` reopens Meetings on a Dock
  click.
- **The three windows (meetings, training, and microphone check) are web-rendered** (`ui/webwindow.py` hosts the built
  `frontend/` pages in transparent WKWebViews over the usual glass windows;
  `ui/webbridge.py` is the pure-logic bridge half, tested without WebKit).
  All webview calls are main-thread only — engine callbacks keep hopping via
  `performSelectorOnMainThread`, then `evaluateJavaScript`. Building the app
  (and running from source) needs `npm --prefix frontend run build` first;
  node/npm is a build-time-only dependency, nothing web ships beyond the
  static files in `Resources/frontend/`.
- **Appearance is app-level.** `speakeasy/ui/appearance.py` applies the
  `appearance` setting (`system`/`light`/`dark`) with `NSApp.setAppearance_`;
  `WebWindow` must not force an appearance. Pages follow `prefers-color-scheme`
  and cache no theme in JS.
- **Frontend CSS is tokens-only.** No literal colours in
  `frontend/src/**/*.module.css`; use `var(--…)` from `tokens.css`
  (`tests/test_frontend_tokens.py` enforces it, with contrast checks in both
  modes). Pure helpers are tested with `node --test` on `.ts` files.
- **Meeting notes (notepad).** Schema v5: `user_notes` (`id` PK, `meeting_id`
  UNIQUE, Markdown, FTS-indexed via `user_notes_fts`; `notes_fts` indexes Claude's summaries) and the single-row `note_draft`
  for the "Recording now" notepad. `MeetingLibrary.save_meeting` adopts a draft
  whose start is within meeting start − 10 min .. start + duration, shifting its
  time stamps. Limit 200,000 characters (`Notes are too long to save`); the
  editor autosaves 500 ms after the last change and retries every 5 s. Claude
  can read notes through MCP (`get_meeting`, search) but nothing lets it write
  them; `save_notes` is unchanged. Never edit a shipped migration.
- **TipTap is pinned exactly (3.31.4) and bundled.** `@tiptap/core`, `pm`,
  `react`, `starter-kit` and `extension-list` have no `^`/`~` in
  `frontend/package.json`. The frontend build ends with
  `frontend/scripts/check-offline.mjs`, which fails the build if the bundle
  loads a remote script, stylesheet, CSS `url()` or dynamic import (URLs inside
  ordinary strings are not checked); keep the app fully offline.
- Match the surrounding code's comment density and style; comments here explain
  *why* a non-obvious constraint exists (threading, TCC, CoreAudio), not what
  the next line does.

## Microphone diagnostics

The button-driven check uses the existing worker/control executors and pauses
normal capture. Batch finalization and silence trimming stay enabled. Saved
checks can add only missing short takes without rewriting prior evidence.
Planned batch handoffs have null live WER. Failure reports retain only operation,
safe category/code, and source locations; never add exception messages or locals.
Failed attempts survive retries and keep acceptance unmet. See
`docs/real-mic-correctness.md` for current device evidence and limitations.

## Recurring insertion regression: mandatory enhancement check

Read [the incident history](docs/insertion-focus-regression.md) before modifying
dictation, focus, AX security checks or insertion. This regression recurred after
an enhancement: TextEdit worked while Teams and Codex failed. Do not infer web
editor compatibility from native TextEdit or mocked AX acknowledgement alone.

- AXSubrole is optional. Accept successful nil, kAXErrorNoValue (-25212), and
  kAXErrorAttributeUnsupported (-25205) as absence of a subrole. Chromium normal
  editors can return nil. Do not restore the overly strict missing-value check.
- Still reject AXSecureTextField and genuine role/subrole inspection failures
  (including timeout, permission failure and invalid element). Missing AXRole
  remains a failure. Optional absence is not an authorization failure.
- Preserve app-owned focus lookup, bounded lazy accessibility activation, and
  foreground/field identity checks. Never bind to a later arbitrary focus to
  hide an unavailable-target outcome or delay microphone onset waiting for AX.
- A fresh Codex/Teams answers focus only ~2 s after `AXEnhancedUserInterface`
  is written, and does not implement `AXManualAccessibility`. Keep the
  activation-time warm-up (`speakeasy/ax_warmup.py`); do not "fix" a first-take
  miss by lengthening key-down retries or relaxing the acceptance bound.
- DOM/web editors use the existing single clipboard/Cmd+V path even if
  AXSelectedText advertises writability. Preserve clipboard ownership and do
  not retry ambiguous delivery. Native AX acknowledgement is not visible proof.
- Run native/web missing-subrole and security-failure regressions in
  tests/test_injector_reliability.py, then the full suite. Before declaring an
  insertion-affecting enhancement validated, obtain visible single-insertion
  checks in TextEdit, Codex and Teams on the actual installed build. Do not send
  test messages. Retain explicit pending status when live checks are unavailable.

The user confirmed the corrected installed build works after this recurrence.
That confirmation does not close the separate wake-recovery or accuracy gates.

## Wake recovery release status — 19 September 2026

The installed mitigation passed the user's approximately ten-second sleep/wake
check. Two logged recovery events reached Ready in 2393.2 ms and 667.0 ms, each
on its first attempt without errors. These are not two independently confirmed
test cycles. The full suite passed 388 tests. Longer-sleep reliability and an
explicit post-wake dictation check remain unverified; the two-second target was
not met by both events. See [cause, mitigation and retained evidence](docs/microphone-recovery-recurrence.md).
Historical failures and earlier build-specific acceptance statements remain
unchanged. Do not restore the shared cold-start/warm-command timeout: helper
launch has one absolute four-second budget, warm commands retain 1.5 seconds,
and a launch timeout does not trigger an immediate second cold launch.
