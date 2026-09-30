# Quit Kills Diarization Child + Main-Window Cancel Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close open items 2 and 5 of `docs/superpowers/plans/2026-09-30-diarization-subprocess.md`:
- **Item 2:** a normal app quit kills the diarization child straight away. Today it can linger for about 58 s, until sherpa-onnx releases the GIL and the child's parent watchdog runs.
- **Item 5:** the main window (dock) gets a Cancel button while a meeting is processing, with a two-step inline confirm. The user approved this on 30 Sep 2026.

**Why the child survives a quit today:**
- `Engine.shutdown()` (`speakeasy/engine.py:202`) sets `_meeting_cancel`, but only the runner loop on the worker thread acts on it, polling every 0.1 s.
- Every quit path calls `shutdown()` and then `NSApplication.terminate_`, which is a hard C exit: `quitApp_` in `ui/menubar.py`, `_quit` in `ui/main_window.py`, and the SIGINT/SIGTERM handler.
- The worker thread never runs again, so the child is orphaned (see the comment at `speakeasy/diarization_process.py:26`).
- The fix is to kill the child directly from inside `shutdown()`.

**Tech Stack:** Python 3.11 (`.venv/bin/python`), multiprocessing spawn, pytest; React/TypeScript dock (`frontend/src/dock/App.tsx`, Vite). The frontend has no test runner.

## Global Constraints

- Read `AGENTS.md` before starting. Offline only; no new dependencies.
- Tests never import sherpa-onnx, the model or the mic. Child-process tests use stub targets in `tests/diarization_child_stubs.py` (add new stubs there).
- Never run app code against the real `~/Library/Application Support/Speakeasy`; the autouse `isolated_home` fixture covers tests.
- Run tests with `.venv/bin/python -m pytest -p no:cacheprovider`, giving the Bash call `timeout: 600000`. The full suite took about 17 s (743+ tests). Mutation runs: `python -B` and clear `__pycache__` first.
- Branch `quit-kills-diarization-child` in a worktree; never on `master`.
- UI follows the existing glass style in `frontend/src/dock/App.module.css`.

## Task 1: `ChildDiarizationRunner.terminate_active()` and shutdown wiring

**Files:** `speakeasy/diarization_process.py`, `speakeasy/engine.py`, `tests/test_diarization_process.py`, `tests/diarization_child_stubs.py`, an engine test file (`tests/test_engine_meeting.py` or similar).

- [ ] **Runner:** track the live process.
  - Add `self._active = None` and `self._active_lock = threading.Lock()`.
  - After `process.start()` succeeds, set `_active = process` under the lock.
  - In the outer `finally`, clear it under the lock **before** `_reap`.
- [ ] **Runner:** add `terminate_active(self, grace: float = 0.5) -> None`.
  - Read `_active` under the lock. If it is `None` or not alive, return.
  - Otherwise call `process.terminate()` and `process.join(grace)`; if the process is still alive, call `process.kill()` and `process.join(grace)`.
  - Swallow `OSError`/`ValueError`: it runs on the quit path and must never raise.
  - Worst case is about 1 s. The child installs no SIGTERM handler, so the default action kills it even while sherpa holds the GIL.
- [ ] **Runner:** make a cancelled run always end in `MeetingCancelled`.
  - In both `child_exited` branches (EOF on `recv`, and "not alive and nothing to read"), raise `MeetingCancelled` instead of `DiarizationFailed("child_exited")` when `cancel.is_set()`.
  - Without this, a shutdown or cancel can be reported as a diarization failure and drop into the single-speaker fallback.
- [ ] **Engine:** in `shutdown()`, straight after `self._meeting_cancel.set()`, add:
  ```python
  terminate = getattr(self.diarization_runner, "terminate_active", None)
  if terminate is not None:
      terminate()
  ```
  The `getattr` keeps fake runners working. It must be safe to repeat: a menu quit calls `shutdown()` twice (`quitApp_` and then `applicationWillTerminate_`).
- [ ] **Stub:** add a child target that sends `("ready",)` and then blocks indefinitely, for example `while True: time.sleep(0.05)`.
- [ ] **Runner tests:**
  1. Run the runner in a thread with the blocking stub. Wait for ready: poll `runner._active` until the process is alive and `ready` has been received (use a `progress`/`on_phase` hook or a short sleep after `_active` is set). Then set `cancel` and call `terminate_active()`. The child must be dead within 1.0 s of the call, and the runner thread must raise `MeetingCancelled` and finish within 3 s.
  2. With no run active, `terminate_active()` returns without error.
  3. Called twice during an active run, it does not raise.
  4. Race: the runner thread's `_reap` and `terminate_active()` run together, with no exception and no hang (join within 5 s).
  5. With `cancel` **not** set, killing the child (via `terminate_active()`) still raises `DiarizationFailed("child_exited")`. This keeps the existing reason for real crashes.
- [ ] **Engine test:** a fake runner records its calls. Calling `shutdown()` calls `terminate_active` at least once, and calling `shutdown()` twice does not raise.
- [ ] **Mutations the reviewer must confirm are caught:**
  - `terminate_active` returns at once (no-op);
  - `_active` is never set;
  - `_active` is cleared before `start()`;
  - the `cancel.is_set()` → `MeetingCancelled` branch is removed;
  - the engine call is removed.
- [ ] Commit.

## Task 2: Dock Cancel button with two-step confirm

**Files:** `speakeasy/ui/main_window.py`, `frontend/src/dock/App.tsx`, `frontend/src/dock/App.module.css`, `tests/test_main_window_events.py`, then rebuild `frontend/dist`. Follow the existing build step in AGENTS.md or `package.json`, and commit `dist` only if the repo already tracks it.

- [ ] **Bridge:** register `dispatcher.register("app.cancelProcessing", self._cancel_processing)`. The handler calls `self.engine.cancel_meeting_processing()` and then `respond(True)`.
- [ ] **Dock:** when `app.mode === 'meeting_processing'`, show a **Cancel** button next to the progress text.
  - First click: the control changes inline to the text **"Discard meeting?"** with two buttons, **Discard** and **Keep**.
  - **Keep** goes back to the single Cancel button.
  - **Discard** calls `bridge.call('app.cancelProcessing')` and then shows a disabled **"Cancelling…"** button. The engine pushes the progress text "Cancelling… waiting for the current stage".
  - The confirm state resets whenever `mode` leaves `meeting_processing`.
  - In mock mode (`!bridge.embedded`) the button just logs; it must not crash.
  - Tooltip on Cancel, as on the menu item: "Discards this meeting. Cancellation is checked between transcription chunks and speaker-identification passes."
- [ ] **Style:** use the existing glass button styles. Discard uses the existing destructive/red style if there is one; otherwise use the amber/red token already used for errors.
- [ ] **Bridge test:** the handler is registered, calls `cancel_meeting_processing` once, and responds `True`. Mutation: drop the engine call → the test fails.
- [ ] Run `npm run build` (or the repo's frontend build command) and the full Python suite.
- [ ] Commit.

## Task 3: Docs, installed-app check, finish

- [ ] **AGENTS.md:** update the watchdog paragraph, the `serve` docstring, and the comment at `diarization_process.py:26`. A normal quit now kills the child from `Engine.shutdown()`; the parent-death watchdog is only the backstop for crashes and force-quits, where it still waits for the first callback (about 58 s on a 29.3-min meeting).
- [ ] **Old plan:** mark items 2 and 5 done in `2026-09-30-diarization-subprocess.md`, with a pointer to this plan.
- [ ] **Installed-app check, quit** (done by the user or the main session, not a subagent on real data):
  1. Start processing a meeting of 5 min or more.
  2. During speaker identification, quit from the menu.
  3. Within about 1 s, run `ps -axo pid,command | grep multiprocessing.spawn | grep -v grep`. Expected: nothing.
  4. Record the result here.
- [ ] **Installed-app check, Cancel:**
  1. Record a short meeting and end it.
  2. In the main window, press Cancel and then Keep: processing continues.
  3. Press Cancel and then Discard: no meeting is saved, and the app returns to Ready.
  4. Record the result.
- [ ] Say which was done: tests only, or the app launched and checked. Then merge and clean up per the user's CLAUDE.md.

## Status

- 30 Sep 2026: plan approved; two-step confirm chosen for item 5. Not started.
