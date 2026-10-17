# Size and Slop Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the app bundle by about 1.2 GB, stop shipping mock data in the production frontend, and simplify the three places the 16 October review flagged as the hardest to read — without changing any transcript or any behaviour a user can see.

**Architecture:** Five independent tasks. Task 1 converts the speech model to bf16 at build time (parakeet-mlx already casts fp32→bf16 on every launch, so the result is bit-identical). Task 2 loads a local model directory directly instead of going through `from_pretrained`'s hub lookup. Task 3 moves the frontend's real types out of `mock/` and gates the fixtures behind `import.meta.env.DEV` so Rollup drops them. Tasks 4 and 5 are refactors of `engine._process_meeting` and `transcriber._model_operation`, covered by the existing suites.

**Tech Stack:** Python 3.11, MLX / parakeet-mlx 0.5.2, PyInstaller, React + Vite + TypeScript.

**Spec:** No separate spec. This plan implements items 1–5 of the code review in the session of 16 October 2026; the evidence is copied into "Review findings" below so the plan is standalone.

## Review findings (the evidence behind each task)

1. `dist/Speakeasy.app/Contents/Resources/model/model.safetensors` is **2.47 GB, all 697 tensors F32**. `parakeet_mlx.utils.from_pretrained` loads it and then casts every parameter to `mx.bfloat16` (its default `dtype`). Storing bf16 on disk gives the same in-memory model at about 1.24 GB, and removes the transient fp32 copy at launch.
2. `from_pretrained(path)` first calls `hf_hub_download(path, ...)`, which raises (we set `HF_HUB_OFFLINE=1`) and only then falls back to the local directory. For the bundled model, that hub lookup is pointless.
3. `frontend/src/mock/meetings.ts` (747 lines) holds both the production **types** (19 interfaces/types, `SPEAKER_PALETTE`, `speakerColor`) and the **fixtures**. Fixture titles such as `Weekly 1:1 — Alex` are in the shipped `meetings-*.js` (527,142 bytes), because `App.tsx` uses `const isMock = !bridge.embedded`, which the bundler can't evaluate at build time.
4. `engine._process_meeting` is about 290 lines, has 29 `if timing is not None` checks, and has two `start / try / finally / finish` blocks per stage. The dual-track and single-track branches are inline.
5. `transcriber._model_operation` uses `getattr(self, "_x", default)` six times, because `tests/test_dual_track_meeting.py:393` builds `Transcriber.__new__(Transcriber)` without `__init__`. It also branches on `function.__name__`, and has lines about 200 characters long.

## Global Constraints

- Speakeasy must stay fully offline: no new network calls and no new dependencies.
- Transcripts must not change. Task 2 must give the same transcript and token timings on a real clip.
- Pure refactors (Tasks 4 and 5) change no tests except the patch points named here. The existing suites are the safety net.
- Tests run HOME-isolated, as the existing conftest does. Never run app code against the real `~/Library/Application Support` data.
- Full suite: `.venv/bin/python -B -m pytest -q` with a Bash timeout of 600000 ms. Frontend: `cd frontend && npm test && npm run build`.
- Work on branch `cleanup-size-and-slop` in a worktree, never on master.
- "Done" means the rebuilt app is launched and checked (Task 6), not only that the tests pass.

## Review Focus

- **(Task 1 skipped)** Stale conversion cache: if the HF snapshot changes, the build must not reuse an old bf16 file. The cache is keyed by snapshot hash (Task 1, test `test_convert_skips_when_up_to_date` plus the hash-in-path rule).
- **Dev checkout with only a HF id and no cache:** first-run download must still work. `_load_model` falls back to `from_pretrained` for anything that isn't a directory (Task 2, `test_load_model_hf_id_uses_from_pretrained`).
- **A bundled directory that is still fp32** (an old build, or a skipped conversion): it must still load as bf16 (Task 2, `test_load_model_casts_non_bf16_directory`).
- **Browser mock mode during development** (`npm run dev`, `meetings.html?mock=…`) must still show fixtures after the DEV gate (Task 3, step 7, checked in the browser pane).
- **A meeting that fails or is cancelled mid-stage** must still record the finish time of that stage and emit the timing line (Task 4, `test_stage_finishes_on_exception`).

---

### Task 1: Ship the speech model as bf16 — SKIPPED (user decision, 16 Oct 2026)

> Not being done. The bundle stays fp32 (2.47 GB) and is cast to bf16 at launch as today. Later tasks must not depend on a bf16 directory. Items are executed one at a time, each in its own session and branch.


**Files:**
- Create: `scripts/convert_model_bf16.py`
- Create: `tests/test_convert_model_bf16.py`
- Modify: `scripts/build_app.sh:92-94` (the "Bundling model" step) and its bundle sanity checks near line 139
- Modify: `packaging/Speakeasy.spec:14` docstring ("The 2.3 GB model" → "The 1.2 GB bf16 model")

**Interfaces:**
- Produces: `convert(src_dir: Path, dest_dir: Path) -> bool` (returns False when dest is already up to date), `check(dest_dir: Path) -> None` (raises `SystemExit` with a message unless every tensor is BF16), `verify(src_dir: Path, dest_dir: Path) -> None` (raises unless every dest tensor `mx.array_equal`s `src.astype(mx.bfloat16)` and the key sets match). CLI: `convert_model_bf16.py convert SRC DEST | check DEST | verify SRC DEST`.
- Output layout: `DEST/config.json` (copied byte-for-byte) and `DEST/model.safetensors` (bf16), written to a temp name and then `os.replace`d. Up to date means both files exist and `DEST/.source` contains the source snapshot's directory name (its hash).

- [ ] **Step 1: Write the failing tests** in `tests/test_convert_model_bf16.py`, using a tiny synthetic snapshot in `tmp_path` (`mx.save_safetensors` with three F32 tensors of shapes (4,), (2,3), (1,) and a `config.json` of `{"a": 1}`):
  - `test_convert_writes_bf16_with_same_keys_and_values`: after `convert`, `mx.load(dest/"model.safetensors")` has the same keys, every `.dtype == mx.bfloat16`, and every array `mx.array_equal`s `src[k].astype(mx.bfloat16)`; `config.json` bytes are equal.
  - `test_convert_skips_when_up_to_date`: a second `convert` returns `False` and leaves the safetensors `st_mtime_ns` unchanged.
  - `test_check_rejects_f32`: `check` on the source directory raises `SystemExit`.
  - `test_verify_detects_mismatch`: overwrite one dest tensor with zeros; `verify` raises `SystemExit`.
- [ ] **Step 2: Run** `.venv/bin/python -B -m pytest tests/test_convert_model_bf16.py -q`. Expected: FAIL (module not found).
- [ ] **Step 3: Implement `scripts/convert_model_bf16.py`.** Use `mx.load` / `mx.save_safetensors`; load the module in tests with `importlib.util.spec_from_file_location`. Read the safetensors header (8-byte little-endian length + JSON) for `check`, so `check` never loads 1.2 GB.
- [ ] **Step 4: Run the tests again.** Expected: PASS.
- [ ] **Step 5: Wire it into `build_app.sh`.** Before bundling, run `.venv/bin/python scripts/convert_model_bf16.py convert "$SNAP" "models/parakeet-bf16/$(basename "$SNAP")"` (`models/` is already gitignored). Then rsync that directory's two files into `Contents/Resources/model/` instead of `$SNAP`'s. In the sanity-check section, add `.venv/bin/python scripts/convert_model_bf16.py check "$APP/Contents/Resources/model" || exit 1`.
- [ ] **Step 6: Prove bit-identity on the real model.** Run `.venv/bin/python scripts/convert_model_bf16.py verify "$SNAP" models/parakeet-bf16/<hash>` with a timeout of 600000 ms. Expected: exit 0. Record `du -sh` of the new safetensors (expect about 1.2 GB).
- [ ] **Step 7: Commit** `feat(build): bundle the speech model as bf16 (2.47 GB → ~1.24 GB)`.

### Task 2: Load a local model directory without the hub lookup

> **Moved (16 Oct 2026):** done in `2026-10-16-model-loader-and-update-check.md` (Task A, f663b99), merged to master with that branch.

**Files:**
- Modify: `speakeasy/transcriber.py` (imports near line 55; `Transcriber.__init__` line 111)
- Modify: `tests/test_transcriber_warmup.py:11,40` (change the patch target from `speakeasy.transcriber.from_pretrained` to `speakeasy.transcriber._load_model`)
- Create: `tests/test_transcriber_load.py`

**Interfaces:**
- Produces: `_load_model(source: str) -> BaseParakeet` in `speakeasy/transcriber.py`. If `Path(source).is_dir()`, it reads `config.json`, calls `parakeet_mlx.utils.from_config(config)` and `model.load_weights(str(dir / "model.safetensors"))`, then casts to `mx.bfloat16` **only if** some parameter is not already bf16 (the same `tree_flatten` / `astype` / `tree_unflatten` / `model.update` pattern as `from_pretrained`). Otherwise it returns `from_pretrained(source)`. `Transcriber.__init__` calls `_load_model(source)`.

- [ ] **Step 1: Write the failing tests** in `tests/test_transcriber_load.py`, patching `speakeasy.transcriber.from_config` with a fake model that records `load_weights` paths and whose `parameters()` returns `{"w": mx.zeros((2,), dtype=...)}`:
  - `test_load_model_directory_skips_hub`: patch `from_pretrained` to raise `AssertionError`; `_load_model(str(tmp_dir))` returns the fake; `load_weights` was called with `str(tmp_dir / "model.safetensors")`.
  - `test_load_model_bf16_directory_is_not_recast`: params already bf16, so `update` is never called.
  - `test_load_model_casts_non_bf16_directory`: params F32, so `update` is called once and every value it gets is bf16.
  - `test_load_model_hf_id_uses_from_pretrained`: `_load_model("mlx-community/x")` returns whatever the patched `from_pretrained` returns.
- [ ] **Step 2: Run** `.venv/bin/python -B -m pytest tests/test_transcriber_load.py -q`. Expected: FAIL (`_load_model` missing).
- [ ] **Step 3: Implement `_load_model`** and switch `__init__` to it. Update the two patch targets in `test_transcriber_warmup.py`.
- [ ] **Step 4: Run** `.venv/bin/python -B -m pytest tests/test_transcriber_load.py tests/test_transcriber_warmup.py -q`. Expected: PASS.
- [ ] **Step 5: Real-model parity check** (scratchpad, temp HOME, timeout 600000 ms). Generate a clip with `say -o clip.wav --data-format=LEI16@16000 "Speakeasy should transcribe this sentence identically after the change."`. In one script, transcribe it with (a) `from_pretrained(<fp32 snapshot dir>)` and (b) `_load_model(<the same fp32 snapshot dir>)` (Task 1 skipped), each through `model.transcribe(path)`. Assert `text` is equal and every token's `(text, start, end)` is equal. Expected: identical. Paste the two texts into the commit message body.
- [ ] **Step 6: Commit** `refactor(transcriber): load bundled model directory directly`.

### Task 3: Keep mock fixtures out of the production frontend

**Files:**
- Create: `frontend/src/meetings/types.ts`: every `export interface` / `export type` currently in `frontend/src/mock/meetings.ts` lines 8–110, plus `ClaudeSetupInfo` (line 681)
- Create: `frontend/src/meetings/speakerColor.ts`: `SPEAKER_PALETTE` and `speakerColor` (lines 112–~130)
- Modify: `frontend/src/mock/meetings.ts` (import the types from `../meetings/types` and keep only `MOCK_*` data); `frontend/src/mock/actionItems.ts` likewise if it declares types
- Modify: every importer of `../mock/meetings` that imports only types or `speakerColor`: `ConnectClaudeSheet.tsx, LibraryBanner.tsx, MeetingDetail.tsx, MeetingList.tsx, RecordingNotes.tsx, SearchResults.tsx, SettingsSheet.tsx, Sidebar.tsx, TodayView.tsx, agendaSplit.ts, draftApi.ts, listSections.ts, navigation.ts, recordingState.ts, transcriptTurns.ts`, and `frontend/tests/{agendaSplit,listSections,recordingState,transcriptTurns}.test.ts`
- Modify: `frontend/src/meetings/App.tsx:143` → `const isMock = import.meta.env.DEV && !bridge.embedded;`. Gate the mock branches in `actionItemsApi.ts` and `draftApi.ts` the same way (`if (bridge.embedded || !import.meta.env.DEV) return bridge.call(...)`).
- Create: `frontend/scripts/check-no-mocks.mjs` exporting `findMockFixtures(dir: string): string[]`; add it to the `build` script after `check-offline.mjs`
- Create: `frontend/tests/checkNoMocks.test.ts`

**Interfaces:**
- Produces: `findMockFixtures(dir)` returns `"<file>: <marker>"` for every `.js` file under `dir` containing any of the markers `'Weekly 1:1 — Alex'` or `'Alex, Priya, Sam'`. Run as main on `dist/`, it exits 1 when the list isn't empty.
- Invariant: after this task, `grep -rn "from '../mock/" frontend/src` matches only `App.tsx`, `actionItemsApi.ts` and `draftApi.ts`.

- [ ] **Step 1: Write the failing test** `frontend/tests/checkNoMocks.test.ts`: write a temp dir with `a.js` containing `Weekly 1:1 — Alex` and `b.js` without it; assert `findMockFixtures(dir)` has length 1 and ends with `Weekly 1:1 — Alex`.
- [ ] **Step 2: Run** `cd frontend && npm test`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement `check-no-mocks.mjs`** (same file-walk shape as `check-offline.mjs`) and append `&& node scripts/check-no-mocks.mjs` to `build`.
- [ ] **Step 4: Run** `npm test` (expected: PASS), then `npm run build`. Expected: build **fails**, listing the meetings bundle. This proves the check sees today's leak.
- [ ] **Step 5: Move the types and `speakerColor`, update the importers, and add the DEV gates.**
- [ ] **Step 6: Run** `npm test && npm run build`. Expected: PASS. Record the new `meetings-*.js` size against 527,142 bytes.
- [ ] **Step 7: Check that dev mock mode still works.** Add a `frontend-dev` entry to `.claude/launch.json` (`npm --prefix frontend run dev`, port 5173), then `preview_start` it and open `meetings.html?mock=default`. Expected: the fixture meeting list renders (read_page shows `Weekly 1:1 — Alex`). Stop the server.
- [ ] **Step 8: Commit** `refactor(frontend): real types out of mock/, fixtures dev-only`.

### Task 4: Untangle `engine._process_meeting`

**Files:**
- Modify: `speakeasy/engine.py:1222-1512`
- Create: `tests/test_engine_stage.py`

**Interfaces:**
- Produces, at module level in `engine.py`:
  - `_stage(timing: MeetingTiming | None, phase: str)`, a `contextlib.contextmanager`: a no-op when `timing is None`; otherwise `timing.start(phase)`, yield, and `timing.finish(phase)` in `finally`.
  - `@dataclass class _TrackResult: segments: list[MeetingSegment]; capture_mode: str; system_status: str | None; health: dict`
- Produces, on `DictationEngine`: `_dual_track_segments(self, job, recording, offsets, *, mic_frames, precomputed_mic, speaker_cap, report_progress) -> _TrackResult` and `_single_track_segments(self, job, recording, offsets, *, mic_frames, precomputed_mic, speaker_cap, report_progress) -> _TrackResult`. Each one moves its existing branch body verbatim, except for `_stage`, and `health` = `{**diarization_health, **echo_health}`.
- `_process_meeting` keeps its signature, its `try / except MeetingCancelled / except Exception / finally` structure and every print and progress string, character for character.

- [ ] **Step 1: Write the failing tests** in `tests/test_engine_stage.py`:
  - `test_stage_none_is_noop`: `with _stage(None, "mic_asr"): pass` raises nothing.
  - `test_stage_records_start_and_finish`: with `MeetingTiming(clock_ns=iter(range(10)).__next__)`, events contain `mic_asr_started` and `mic_asr_finished`.
  - `test_stage_finishes_on_exception`: a `ValueError` raised inside still records `mic_asr_finished` and propagates.
- [ ] **Step 2: Run** `.venv/bin/python -B -m pytest tests/test_engine_stage.py -q`. Expected: FAIL (import error).
- [ ] **Step 3: Add `_stage` and `_TrackResult`.** Extract the two branch methods, and replace each `if timing is not None: timing.start(x)` / `try` / `finally: if timing is not None: timing.finish(x)` with `with _stage(timing, x):`. Shrink the 12-line comment in `except Exception` to two lines: why no source line or message is printed (both can carry transcript text or paths), and that `extract_tb` avoids `linecache`.
- [ ] **Step 4: Run** `.venv/bin/python -B -m pytest tests/test_engine_stage.py tests/test_engine_meeting.py tests/test_dual_track_meeting.py tests/test_meeting_benchmark.py -q` (timeout 600000). Expected: PASS. Then `grep -c "if timing is not None" speakeasy/engine.py`: expect ≤ 6 (only attribute assignments and the final emit).
- [ ] **Step 5: Commit** `refactor(engine): split meeting pipeline by capture mode; _stage timing helper`.

### Task 5: Tidy `transcriber._model_operation`

**Files:**
- Modify: `speakeasy/transcriber.py:65-93` and the `Transcriber` class body

**Interfaces:**
- Class-level defaults on `Transcriber` (so `Transcriber.__new__` objects in tests still work): `_last_operation_finished_ns: int | None = None`, `_previous_operation: str | None = None`, `_warmup_complete: bool = False`, `_model_load_ms: float | None = None`, `_model_warmup_ms: float | None = None`.
- `_model_operation(operation: str, *, takes_session: bool = False)`. `transcribe_stream` is decorated with `takes_session=True`, which replaces the `function.__name__ == "transcribe_stream"` check.
- The context dict keys and values, and the `_previous_operation` rule, are unchanged. Extract `_temperature(previous_operation, idle_seconds) -> str` returning `"first_after_load"` / `"idle"` (≥ 1800 s) / `"warm"`, and split the long lines; no line over 100 characters.

- [ ] **Step 1: Write the failing test** in `tests/test_transcriber_load.py`: `test_temperature_rules` asserts `_temperature("warmup", 5) == "first_after_load"`, `_temperature("dictation", 1800) == "idle"`, `_temperature("dictation", 1799.9) == "warm"`, `_temperature(None, None) == "warm"`.
- [ ] **Step 2: Run it.** Expected: FAIL (import error).
- [ ] **Step 3: Implement** the class defaults, `takes_session`, `_temperature` and the line splits; remove every `getattr(self, "_…", …)` in `transcriber.py`.
- [ ] **Step 4: Run** `.venv/bin/python -B -m pytest tests/test_transcriber_load.py tests/test_transcriber_warmup.py tests/test_dual_track_meeting.py tests/test_dictation_benchmark.py tests/test_engine_streaming.py -q` (timeout 600000). Expected: PASS. Then `grep -c 'getattr(self, "_' speakeasy/transcriber.py`: expect 0.
- [ ] **Step 5: Commit** `refactor(transcriber): plain attributes and named temperature rule`.

### Task 6: Full check, build and launch

- [ ] **Step 1:** Run `.venv/bin/python -B -m pytest -q` with timeout 600000. Expected: all pass (the last recorded count is 388 or more).
- [ ] **Step 2:** Run `scripts/build_app.sh` (timeout 600000; run it in the background if needed). Expected: build succeeds; bundle size unchanged at about 2.6 GB (Task 1 skipped).
- [ ] **Step 3:** Launch the built app with the `run` skill (temp HOME, as in the installed-app UI checks memory). Check: the menu bar reaches Ready; `Speakeasy-latency` log `model_load_ms` against the last pre-change value; one dictation into TextEdit. Per CLAUDE.md, insertion is unchanged by this plan, so no Codex/Teams claim is made. Open the Meetings window: it renders with no mock meetings.
- [ ] **Step 4:** Update this plan's checkboxes, and record before/after numbers (bundle size, `meetings-*.js` bytes, `model_load_ms`) under "Results" below.

## Results

_(filled in by Task 6)_

## Not in this plan (other review findings, for later)

- `meeting_store.connect()` runs the lease, mkdir, WAL check and `migrate()` on every short-lived connection; a per-process "already migrated" flag per path would skip most of that.
- `frontend/src/meetings/App.tsx` is 1,501 lines with 47 `useState`; split it by view.
- Recording state is polled every 2 s via `recording.get`; the bridge already has `emit`, so push it instead.
- `ui/pill_panel.py` and `ui/record_prompt_panel.py` share about 10 lines of panel setup.
- Agent-context size: `AGENTS.md` (414 lines) plus the dated release-status sections in `CLAUDE.md` and `README.md` load into every session; move the dated evidence into `docs/`.
- The bundle still carries `hf_xet`, `libssl`/`libcrypto` and `certifi` because `parakeet_mlx.utils` imports `huggingface_hub`; excluding them needs a shim like `_mel_shim`.
