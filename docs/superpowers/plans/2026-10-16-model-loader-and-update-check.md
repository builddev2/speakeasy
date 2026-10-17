# Model loader and speech-model update check — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.
> Implementers: Sonnet subagents. Reviewers: Opus subagents that verify by **mutation**
> (`python -B`, clear `__pycache__`, HOME-isolated tests). Blockers, failing evals or hard
> design problems go back to the Opus controller to diagnose; never let an implementer loop.

**Goal:** (A) load a local model directory without the Hugging Face hub lookup
(Task 2 of `2026-10-16-size-and-slop-cleanup.md`, brought into this plan). (B) add a Settings
option that checks for a newer speech model and, if one exists, offers to download it. The
update is used from the next launch.

**Scope:** Only Task 2 from the cleanup plan. **Task 1 is skipped**: the model stays fp32 in
the bundle and is cast to bf16 at launch. **Tasks 3, 4 and 5 are not touched.** Mark Task 2 in the
cleanup plan as "moved to 2026-10-16-model-loader-and-update-check.md" when this branch merges.

**Tech stack:** Python 3.11, MLX / parakeet-mlx 0.5.2, PyInstaller, React + Vite + TypeScript,
and `/usr/bin/curl` for the network (see "Decisions").

## Decisions

These are the user's answers from 16 October 2026:

1. **The offline rule gets a narrow exception.** Only the model update check may use the
   network. It contacts huggingface.co, plus the download hosts that huggingface.co redirects to,
   and only for `config.MODEL_ID`. It runs only when the user presses the button or has turned on
   the opt-in automatic check. Launch, dictation and meetings never wait on it, and never fail
   because it failed. A test enforces that only `speakeasy/model_update.py` starts network
   processes (Task B7).
2. **The automatic check is opt-in and off by default.** When it is on, it runs at most once
   every 7 days, in the background after Ready. It only **checks**. It never downloads on its
   own.
3. **"Newer" means the model files changed, not just the commit.** The version shown is the HF
   commit hash for `config.MODEL_ID` on `main`. An update is offered only when the identity of
   `config.json` or `model.safetensors` differs from the current model's. A commit that changes
   only the README is reported as "Up to date (the repository changed, but the model files are
   the same)".
4. **Rollback:** the previous **update** folder is kept until the new one loads once, then it
   is deleted. The bundled model always stays in the read-only `.app`. Settings offers **"Use
   bundled model"**, which removes the update so the bundled model is used from the next launch.

## Calls I made (the user can veto these while reading)

- **The network goes through `/usr/bin/curl`, run as a subprocess.** There is no new dependency.
  The frozen Python's `ssl` default CA path points at the build machine's Homebrew OpenSSL, and
  there is no bundled `certifi`, so `urllib` cannot be trusted in the `.app`. System curl uses the
  macOS trust store. It also lets cancel be "kill the process". `huggingface_hub` is not used,
  because `HF_HUB_OFFLINE=1` is fixed at import time in the frozen app (see
  `transcriber.py:24-42`).
- **File identity uses Hugging Face's own hashes:** the LFS `sha256` for `model.safetensors`
  and the git blob SHA-1 for `config.json`. Both come from one API call. In a dev checkout they
  come free from the HF cache: snapshot files are symlinks to `blobs/<identity>`. The download
  is verified against the same values.
- **The verification load runs in a child process** (`Speakeasy --verify-model <dir>`, the same
  pattern as `--inference-probe` in `__main__.py`). This means a second 2.5 GB fp32 load never
  shares the app's MLX worker thread or memory.
- **No hot reload.** The prompt and the finished state say "takes effect the next time
  Speakeasy starts".
- **No resume for a broken download.** A failed or cancelled download deletes its staging
  folder, and the next attempt starts again from the beginning.
- **The update applies in dev runs too.** `settings.model_path()` prefers a confirmed update
  in both modes, because dev and installed share App Support.

## Global constraints

- Work on branch `model-update-check` in a worktree, never on master.
- Full suite: `.venv/bin/python -B -m pytest -q`, with a **Bash timeout of 600000 ms** (put this
  in every implementer brief). Frontend: `cd frontend && npm test && npm run build`.
- Tests run HOME-isolated (the session-wide temp HOME in conftest). **No subagent runs app code
  against real App Support data.**
- **Network tests use a fake hub.** `model_update` takes an injectable `runner` that stands in
  for curl; there are no real HTTP calls in tests. The controller (Opus) makes at most **one**
  real Hugging Face check, and only with the user's OK.
- **Bridge numbers arrive as floats.** Never validate with `isinstance(x, int)`.
- Tool-description changes would also need `packaging/mcpb/manifest.json`. None are planned.
- Copy has to be checkable and plain. Sizes are shown in GB to one decimal place
  (2470000000 bytes → "2.5 GB").

## On-disk layout

```
~/Library/Application Support/Speakeasy/models/speech/
  active.json              # pointer, replaced atomically with os.replace
  rev-<sha40>/             # a verified update: config.json, model.safetensors, source.json
  .staging-<sha40>/        # an in-progress download (deleted at launch if found)
```

`active.json` looks like this:
`{"revision": "<sha40>", "dir": "rev-<sha40>", "status": "pending" | "confirmed", "previous": "rev-<old>" | null}`

`source.json` is the same shape as the bundled `.source`:
`{"model_id": "mlx-community/parakeet-tdt-0.6b-v2", "revision": "<sha40>", "files": {"config.json": "<git-sha1>", "model.safetensors": "<sha256>"}}`

---

## Task A: `_load_model` (the cleanup plan's Task 2, as written)

**Files:** modify `speakeasy/transcriber.py` (import at line 55; `Transcriber.__init__` at line 111).
Modify `tests/test_transcriber_warmup.py:11,40`: patch `speakeasy.transcriber._load_model`
instead of `from_pretrained`. Create `tests/test_transcriber_load.py`.

**Interface:** `_load_model(source: str) -> BaseParakeet`. If `Path(source).is_dir()`, it reads
`config.json` and calls `parakeet_mlx.utils.from_config(config)` and
`model.load_weights(str(dir / "model.safetensors"))`. Then it casts to `mx.bfloat16` **only if**
a parameter is not already bf16, using the `tree_flatten` / `astype` / `tree_unflatten` /
`model.update` pattern from `from_pretrained`. That cast always happens for now, because the
bundle is fp32. For anything else it returns `from_pretrained(source)`.

- [ ] A1. Write failing tests. Patch `speakeasy.transcriber.from_config` with a fake that records
  `load_weights` paths and whose `parameters()` returns `{"w": mx.zeros((2,), dtype=…)}`:
  `test_load_model_directory_skips_hub` (`from_pretrained` patched to raise `AssertionError`),
  `test_load_model_bf16_directory_is_not_recast` (`update` is never called),
  `test_load_model_casts_non_bf16_directory` (`update` is called once, with every value bf16),
  `test_load_model_hf_id_uses_from_pretrained`.
- [ ] A2. Run them and confirm they FAIL. Then implement and switch `__init__` over. Run
  `tests/test_transcriber_load.py tests/test_transcriber_warmup.py` and confirm they PASS.
- [ ] A3. **Real-model parity check** (controller or implementer, scratchpad, temp HOME,
  timeout 600000 ms). Make the clip with
  `say -o clip.wav --data-format=LEI16@16000 "Speakeasy should transcribe this sentence identically after the change."`.
  In one script, run (a) `from_pretrained(<fp32 snapshot dir>)` and (b) `_load_model(<same dir>)`,
  each through `model.transcribe(path)`. The snapshot dir is
  `~/.cache/huggingface/hub/models--mlx-community--parakeet-tdt-0.6b-v2/snapshots/8ae155301e23d820d82aa60d24817c900e69e487`.
  Assert that `text` is equal and that every token's `(text, start, end)` is equal. Paste both
  texts into the commit body.
- [ ] A4. Commit `refactor(transcriber): load bundled model directory directly`.

## Task B1: Record the bundled model's identity

**Files:** `scripts/build_app.sh` (after line 94), `speakeasy/model_source.py` (new),
`tests/test_model_source.py` (new).

- [ ] Add to `build_app.sh`. `REV=$(basename "$SNAP")`. Set `CFG_ID` and `ST_ID` to the
  basenames of `readlink "$SNAP/config.json"` and `readlink "$SNAP/model.safetensors"`; the cache
  symlinks point at `../../blobs/<id>`. If either is not a symlink, fail the build with a clear
  error. Write `Resources/model/.source` as the JSON above, with `printf`, before codesign. Add a
  sanity check that `.source` exists and `REV` is 40 hex characters.
- [ ] `model_source.py`:
  - `bundled_identity() -> dict | None`. Frozen: read `Resources/model/.source`. Dev: take the
    first snapshot dir under the HF cache for `config.MODEL_ID` (the same path logic as
    `transcriber._is_cached`); its name is the revision, and the readlink basenames are the file
    ids. Return None if none of this is available.
  - `active_identity() -> dict`. Returns the confirmed or pending update's `source.json` if one
    is in use, otherwise `bundled_identity()`, with an added key
    `"origin": "update" | "bundled"`.
- [ ] Tests: a fake frozen layout and a fake HF cache with symlinks (tmp_path). A missing cache
  gives None. A non-symlink file in dev gives None, not a crash.

## Task B2: `settings.model_path()` prefers a valid update

**Files:** `speakeasy/settings.py:270`, `speakeasy/model_store.py` (new),
`tests/test_model_store.py` (new).

- [ ] `model_store.py` (pure filesystem, no network):
  - `speech_models_dir() = app_support_dir() / "models" / "speech"`.
  - `read_active() -> dict | None`. Returns None if the file is missing or the JSON is bad.
  - `write_active(data)`. Writes a temp file in the same directory, fsyncs it, then
    `os.replace`.
  - `update_dir_if_valid() -> Path | None`. Returns the active dir only if it exists, has
    `config.json` (valid JSON) and `model.safetensors` whose **size** matches the size in
    `source.json`, and its status is `pending` or `confirmed`. Size only, so launch stays fast; a
    full hash belongs to download time.
  - `confirm_loaded()`. Sets the status to `confirmed`. If `previous` is set, it deletes that
    folder (`shutil.rmtree`, ignoring errors) and sets `previous` to null.
  - `mark_failed()`. Removes `active.json` and renames the dir to `failed-<sha40>`; it is deleted
    on the next cleanup.
  - `cleanup_leftovers()`. Deletes `.staging-*` and `failed-*`, and any `rev-*` that is neither
    the active dir nor `previous`.
  - `use_bundled()`. Removes `active.json` and deletes every `rev-*` (the button in Task B6).
- [ ] `settings.model_path()`: return `str(update_dir)` if `update_dir_if_valid()` gives one,
  otherwise the current behaviour.
- [ ] `Transcriber.__init__`: if the source is the update dir and `_load_model` raises, log it,
  call `mark_failed()` and load the bundled source. If loading the update succeeds, call
  `confirm_loaded()`. Call `cleanup_leftovers()` once at startup, off the hot path (after Ready).
- [ ] Tests: no update → bundled. Valid pending → update. Size mismatch, missing config or bad
  JSON → bundled. A corrupt update makes `Transcriber` fall back and mark it failed (fake
  `_load_model` raising for that path). Confirming deletes `previous`. `write_active` is atomic,
  so a reader never sees a partial file (simulate a crash before the replace: the old pointer
  survives).

## Task B3: The checker (network, curl, fake hub in tests)

**Files:** `speakeasy/model_update.py` (new), `tests/test_model_update_check.py` (new).

- [ ] `Runner` protocol: `run(argv: list[str], *, timeout: float) -> (returncode, stdout_bytes)`,
  plus `start(argv) -> Popen-like` for downloads. The default uses `subprocess`. Tests inject a
  fake hub.
- [ ] `check(runner=default) -> CheckResult`. Run
  `/usr/bin/curl -sS -f -L --max-time 15 --connect-timeout 5 https://huggingface.co/api/models/mlx-community/parakeet-tdt-0.6b-v2/revision/main?blobs=true`.
  Parse `sha`, and from `siblings` take `config.json` → `blobId` and `model.safetensors` →
  `lfs.sha256` / `lfs.size` (or `size`). Compare with `model_source.active_identity()`.
  `CheckResult(state, remote_revision, current_revision, download_bytes, message)`, where the
  state is one of:
  - `up_to_date` — "Speech model is up to date (revision abc1234)."
  - `same_files` — "Up to date. The repository changed (abc1234 → def5678), but the model files are the same."
  - `available` — "A newer speech model is available (revision def5678, 2.5 GB download)."
  - `offline` — curl exit codes 6, 7 and 28 (no DNS, cannot connect, timeout): "Couldn't reach Hugging Face. Check your internet connection and try again. Speakeasy works offline as usual."
  - `error` — any other exit code, bad JSON or a missing field: "Couldn't check for a model update (<short reason>). Speakeasy works offline as usual."
  - `unknown_current` — `active_identity()` is None: "Can't tell which model is installed, so no update can be offered."
  Short revisions are the first 7 characters.
- [ ] Never raise to the caller. Never log the full response.
- [ ] Tests with a fake hub covering every state, plus: `--max-time` is present; the URL is
  exactly the one above; no other host appears in argv.

## Task B4: Download, verify and swap

**Files:** `speakeasy/model_update.py`, `speakeasy/__main__.py` (`--verify-model`),
`speakeasy/model_verify.py` (new), `tests/test_model_update_download.py` (new).

- [ ] `Downloader(result, runner, on_state)` runs on its own `threading.Thread(daemon=True)`. Only
  one may be active at a time; a second start raises `ValueError("A model download is already in progress.")`.
  1. **Disk:** if `shutil.disk_usage(speech_models_dir()).free < download_bytes + 1_000_000_000`,
     fail with "Not enough free disk space: the update needs about 3.5 GB free (2.5 GB download plus room to verify)."
     The real numbers are computed; the example is for 2.5 GB.
  2. **Fetch** into `.staging-<sha>/`, one file at a time:
     `/usr/bin/curl -sS -f -L --connect-timeout 10 --speed-limit 1024 --speed-time 60 -o <file> https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v2/resolve/<sha>/<name>`.
     Progress = bytes on disk / expected size, polled every 0.5 s and sent through
     `on_state("downloading", fraction)`.
  3. **Cancel:** `cancel()` terminates curl (then kills it after 2 s), deletes staging and
     reports `cancelled`: "Download cancelled. Your current model is unchanged."
  4. **Hash:** SHA-256 of `model.safetensors` in streamed 8 MiB chunks, and the git blob SHA-1 of
     `config.json` (`sha1(b"blob %d\0" % len + data)`). A mismatch fails with "The download was damaged (checksum mismatch). Your current model is unchanged."
  5. **Verify in a child:** `on_state("verifying")`. Run
     `[sys.executable, "--verify-model", dir]` when frozen, or `[sys.executable, "-m", "speakeasy", "--verify-model", dir]`
     in dev, with a 300 s timeout. `model_verify.main` runs `_load_model(dir)`, checks that every
     parameter shape matches `from_config(config)`'s, makes a clip with
     `/usr/bin/say -o <tmp>/check.wav --data-format=LEI16@16000 "The quick brown fox jumps over the lazy dog."`,
     transcribes it, and passes if at least 7 of the 9 lowercased, punctuation-stripped words
     appear. Exit 0 means pass; otherwise exit 1 with a reason on stderr. A failure is reported
     as "The new model didn't pass its check (<reason>). Your current model is unchanged."
  6. **Swap:** write `source.json` and rename staging to `rev-<sha>` (`os.replace`). Then
     `write_active({revision, dir, status: "pending", previous: <old active dir or null>})`.
     Report `ready`: "Update ready. It will be used the next time Speakeasy starts."
  - Any failure deletes staging. The current `active.json` is never touched before step 6.
- [ ] Tests (fake runner that writes files into the `-o` path; tmp HOME): happy path to
  `ready` with the pointer pending and previous recorded; cancel mid-download leaves no staging
  and the pointer unchanged; hash mismatch; verify child exit 1; low disk; second start raises;
  curl exit 28 mid-download gives an "offline" message.
  `model_verify` gets a unit test with a faked transcriber. The real verify runs once in Task B8.

## Task B5: Settings storage and the opt-in automatic check

**Files:** `speakeasy/settings.py`, `speakeasy/model_update.py`, the engine or app start hook
after Ready, `speakeasy/ui/menu_model.py` and `menubar.py`, plus tests.

- [ ] `settings.get_model_update_settings() -> {"auto_check": bool, "last_check": float | None}`,
  with `auto_check` defaulting to **False**. `set_model_update_settings(auto_check=None, last_check=None)`
  accepts a bool for `auto_check` and any number for `last_check` (floats are fine).
- [ ] After Ready, start a daemon thread **only if** `auto_check` is on and
  `now - last_check >= 7 * 86400`. It runs `check()`, stores `last_check`, and if the state is
  `available`, sets an in-memory flag that adds the menu-bar item
  **"Speech model update available…"**, which opens Settings. It does not download.
- [ ] Tests: off by default means the thread never starts (assert the runner is never called);
  6 days doesn't trigger, 7 days does; `offline` changes nothing visible and logs one line.

## Task B6: Bridge and the Settings sheet

**Files:** `speakeasy/ui/meetings_bridge.py` (beside `settings_get_payload` at line 264),
`frontend/src/meetings/SettingsSheet.tsx`, its CSS module and types, bridge tests, frontend tests.

- [ ] Bridge methods, registered the way the other `settings.*` ones are:
  - `modelUpdate.status` → `{current: {revision, origin}, autoCheck, state, message, remote: {revision, bytes} | null, progress: number | null, previousRevertible: bool}`.
  - `modelUpdate.check` → runs `check()` on a background thread. The result comes back as the
    `modelUpdate.changed` event. It never blocks the main thread.
  - `modelUpdate.download`, `modelUpdate.cancel`, `modelUpdate.useBundled`, and
    `modelUpdate.setAutoCheck {value: bool}` (reject anything that isn't a bool with
    "Automatic check must be on or off.").
  - Events: `modelUpdate.changed`, emitted via the window's `emit`, hopped to the main thread the
    same way existing background events are. Progress events are throttled to at most 4 a second.
- [ ] A **"Speech model"** section in the sheet, after "General", in the existing glass styles
  (`styles.sectionTitle`, `fieldRow`, `Switch`). Reuse the existing classes; add no new visual
  language.
  - The row "Current model: Bundled · revision 8ae1553" (or "Update · revision def5678").
  - The button **Check for update**, with a spinner while checking.
  - The switch **Check automatically (weekly)**, off by default. Its hint: "Contacts Hugging Face once a week. Never downloads without asking."
  - The `available` state shows an inline prompt card:
    > **A newer speech model is available.** Revision def5678 · 2.5 GB download.
    > Transcripts may differ slightly from the current model, including for meetings you
    > reprocess. The update is checked before use and takes effect the next time Speakeasy starts.
    > [Download update] [Not now]
  - `downloading` shows a progress bar with "1.2 of 2.5 GB" and **Cancel**. `verifying` shows
    "Checking the new model…". `ready` shows the ready message.
  - Errors and offline show the message in the existing muted error style. Dictation is never
    mentioned as affected.
  - **Use bundled model** appears only when `origin == "update"`. It asks for confirmation:
    "Switch back to the bundled model from the next launch? The downloaded update (2.5 GB) will be deleted."
- [ ] Mock-mode fixtures for each state, so `npm run dev` can show them. Frontend tests cover
  each state's text and that Cancel calls `modelUpdate.cancel`.
- [ ] Bridge tests: float-valued params (`progress` and size arithmetic stay correct with
  floats); `setAutoCheck(1.0)` is rejected; `check` returns at once and the result arrives as an
  event.

## Task B7: Guard the offline rule and update the docs

- [ ] `tests/test_offline_guard.py`: AST-scan `speakeasy/**/*.py`, failing if `urllib.request`,
  `http.client`, `socket`, `requests`, `httpx` or the string `"/usr/bin/curl"` appears outside
  `speakeasy/model_update.py`. Also assert that importing `speakeasy.transcriber`,
  `speakeasy.engine` and `speakeasy.dictation_stream` does not import `speakeasy.model_update`.
- [ ] Reword the AGENTS.md hard constraint: "Fully offline, always — with one exception: the
  user-started (or opt-in weekly) speech-model update check in `speakeasy/model_update.py`, which
  contacts huggingface.co for `config.MODEL_ID` only. Launch, dictation and meetings never wait on
  it. `tests/test_offline_guard.py` enforces this." Update the memory note
  `offline-operation-required.md` to match.
- [ ] README: a short "Updating the speech model" section with the transcript-parity note.

## Task B8: Launch and check (the definition of done)

The controller (Opus) does this with the user, after the full suite and the frontend build pass.

- [ ] `.venv/bin/python -B -m pytest -q` (timeout 600000 ms), `cd frontend && npm test && npm run build`.
- [ ] `scripts/build_app.sh --install`. Remember that this stops Claude Desktop's Speakeasy MCP
  server (memory note); toggle the connector afterwards. Confirm
  `Resources/model/.source` has revision `8ae155301e23d820d82aa60d24817c900e69e487`.
- [ ] Launch the installed app and confirm **Ready** is reached. Then do **one dictation into
  TextEdit**.
- [ ] **One real check online, with the user's OK.** Settings → Check for update. The expected
  result is `up_to_date` or `same_files`, unless HF has really changed. Record the message.
- [ ] **Wi-Fi off:** Check for update shows the offline message within about 15 s. The app
  stays responsive, and a dictation still works.
- [ ] Optional, only if the user wants the 2.5 GB download: do a real download → ready →
  relaunch → Ready → dictation, then Use bundled model → relaunch.
- [ ] Record what was run, and what was only tested, in this plan's Results section.

## Review focus (for the Opus reviewers)

- Kill the app mid-download, or mid-swap: the next launch still reaches Ready on the previous
  model.
- A corrupt update (truncated safetensors) gives the bundled model, not a crash loop.
- Offline: no code path on launch, dictation or meetings waits on `model_update`.
- Mutations to try: remove the size check in `update_dir_if_valid`; skip the hash compare; let
  `write_active` write in place; flip the `auto_check` default; drop `--max-time`. The suite must
  catch each one.

## Results

### Implementation (16 Oct 2026, branch `model-update-check`, subagent-driven)

Tasks A, B1–B7 are done. Each one was reviewed by mutation testing (Opus), and so was a final whole-branch review. Order run: A, B2, B1, B3–B7 (B1 needs B2's store).
Last tested state: full suite 1752 passed (74 s); frontend 83 tests passed; `npm run build` OK.

The final review found one **critical** defect, now fixed (24e8a5d). The verify child transcribed through
parakeet's `load_audio`, which needs ffmpeg, and ffmpeg is absent from the `.app`. Every real update would have
failed verification. The child now reads the WAV itself and transcribes in memory.

Rulings made during execution (beyond the plan):
- An update's `source.json` also stores `"sizes"`. Without it, the launch size check had nothing to compare against.
- `settings.bundled_model_path()` added; the Transcriber falls back to it.
- The auto-check hook lives in the menubar layer, not the engine. This keeps the offline guard satisfiable.
- `CheckResult` carries `remote_files` / `remote_sizes`, which the downloader uses.
- Security: a revision from the network must be 40 lowercase hex characters before it is used in any path or URL.
  curl is restricted to https (`--proto =https --proto-redir =https`).
- `settings._write` is now atomic (mkstemp + fsync + os.replace, keeping the file mode). The check thread writes
  `last_check` off the main thread.
- Pointer status `loading` guards against a crash loop. A launch that finds `loading` marks the update failed and
  uses the bundled model. Cost: a Quit during the first load of a new update discards it.
- Diagnostic `Transcriber(manage_update=False)` (evaluate, inference_probe, scripts) never touches the pointer.
- Settings: Cancel shows only while downloading (verify can't be cancelled). The Current-model row adds
  "(from next launch)" while the pointer is pending.

### B8 launch and check (16–17 Oct 2026)

- Suite: 1753 passed. Frontend: 83 tests passed; build OK.
- `scripts/build_app.sh --install` built and installed the app from the worktree.
  `Resources/model/.source` has revision `8ae155301e23d820d82aa60d24817c900e69e487`, config blob `8955c58…` and safetensors sha256 `b958c37…`.
- **The installed-app check found a second defect that tests had missed.** The frozen `--verify-model` crashed with
  `No module named 'librosa'`, because model_verify imported parakeet_mlx before transcriber installed the shim.
  Fixed in 49d82e9 (test-first). After that, the frozen `--verify-model` on the bundled model ran with PATH without ffmpeg and a temp HOME, and **passed**.
  The run took 3.1 s warm. The first run straight after install took 496 s, under load average ~100 while macOS scanned the new 2.6 GB bundle.
  Dev phase timings: load 1.3 s, `say` 1.9 s, transcribe 2.1 s. The transcript was exactly "The quick brown fox jumps over the lazy dog."
- The installed app launched. Settings → Speech model shows "Bundled · revision 8ae1553", with the weekly check off by default.
- **The one real check (user's OK):** Check for update showed "Checking…", then "Speech model is up to date (revision 8ae1553)."
- **Dictation into TextEdit:** worked (the user did this).
- **Wi-Fi off (the user did this, because Claude is offline too):** the offline message appeared, and dictation still worked while
  offline. The time to the message was not reported.
- **Not done:**
  - the optional real 2.5 GB download → ready → relaunch → Use bundled cycle
  - checking that the "Speech model update available…" menu item appears and then hides; this needs a real `available` result
  - toggling the Claude Desktop connector after install (the user's step)

### Follow-ups (deferred findings, not blocking)
- Use `"$MODEL_ID"` in build_app.sh's `.source` printf, and pin it to `config.MODEL_ID` with a test.
- If `confirm_loaded` fails after a good load, the pointer stays `loading` and the next launch discards a good
  update. A normal Quit during a pending load also discards it; it could reset to `pending` in applicationWillTerminate.
- An update is confirmed before the warmup transcribe runs. `mark_failed` over a confirmed update falls back to
  bundled, not to the previous update.
- After cancelled/failed, the UI has no Download button; the user must press Check again. The 'ready' mock shows Bundled.
- Controller emitter exceptions are swallowed without a log. A late "checking" snapshot could hide the menu item after a fast "available".
- Test gaps:
  - mixed-dtype cast; zero/negative size; reason text in error states; last_check on `error`
  - diagnostic call sites (2 of 7 pinned); menubar listener wiring; TSX event dispatch
- Offline guard does not see `importlib`/`__import__` strings or a bare `"curl"` argv.
