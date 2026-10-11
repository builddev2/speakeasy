# Back-to-Back Meetings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user start recording a new meeting while earlier meetings are still being processed, with every meeting still saved correctly and its notes kept with it.

**Architecture:** Each stopped meeting becomes a self-contained `MeetingJob`, processed in stop order on the existing single `worker` thread. The engine's state is derived under one lock from two things: whether a recording is active, and the job queue. Notes typed during a recording are **parked** under that recording's start time when it stops, and adopted only by that meeting. The UI surfaces (menu, pill, banner, Meetings page, quit) treat "processing" as "free to start".

**Tech Stack:** Python 3 + SQLite (stdlib), pytest; React 18 + TypeScript + Vite, with `node --test` for frontend helpers; PyObjC for AppKit. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-10-back-to-back-meetings-design.md`. Read it before any task. It holds the measured numbers and the reasons behind each decision (D1–D7).

## Model routing and escalation (user requirement)

| Role | Model | When |
|---|---|---|
| Controller (the executing session) | **Opus** | Runs the plan, writes briefs, resolves questions, merges |
| Implementer per task | **Sonnet** (`model: "sonnet"`) | Every coding task (1–7). The brief contains the task text verbatim plus Global Constraints and the Review Focus lines that task owns |
| Task reviewer | **Opus** (`model: "opus"`) | After every task: spec compliance plus code quality, verified **by mutation** (break the code, confirm a test fails, restore). Each task lists the mutations the reviewer must try at minimum |
| Hard problems | **Opus** | When an implementer reports BLOCKED or NEEDS_CONTEXT, when the same test fails after two Sonnet attempts, or when reviewer and implementer disagree. Concurrency in Task 3 (lock ordering, listener resume races) is pre-flagged as hard: if the Sonnet result shows any flakiness, Opus diagnoses it with superpowers:systematic-debugging, and the fix may go back to Sonnet with the diagnosis |
| Design question not answered here | **Opus** decides if it's technical and records the ruling in this plan. **Ask the user** if it changes behaviour they will see |
| Evals: whole-branch review and live overlap eval (Task 8) | **Opus** | Mutation-verified review against the Review Focus list, then build, install and the live two-meeting overlap check |

Brief rules for every subagent:

- Keep reports short: a verdict, the files changed and the test result.
- Never run app code against the real `~/Library/Application Support/Speakeasy`.
  Tests are HOME-isolated by the autouse `isolated_home` fixture; never bypass it.
- Give any full-suite Bash call `timeout: 600000`. The Python suite takes
  several minutes (1,285+ tests on 9 Oct).
- Mutation runs use this command:

  ```
  PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider <file>
  ```

  Afterwards run:

  ```
  find speakeasy tests -name __pycache__ -type d -prune -exec rm -rf {} +
  ```

- Use `.venv/bin/python`, never the system python.

## Global Constraints

- Offline always: no network calls, and no new runtime or npm dependencies.
- **Threading (AGENTS.md):**
  - MLX and model calls run only on `worker`; recorder start/stop only on `control`.
  - Processing stays one job at a time on `worker`. No new executor.
  - UI objects are main-thread only.
- Audio is never persisted. Each job deletes its own spools in its `finally`
  and calls `meeting_recorder.release_spool(path)`. A crash or quit leaves
  cleanup to the `atexit` hook and the launch sweep.
- Schema version becomes **8**. Only append to `_MIGRATIONS`; never edit an existing entry.
- Bridge errors reach JS as `Error(message)`. A new library error must subclass
  `ValueError` and stringify to its code (`draft_mismatch`).
- **Privacy:** timing records and capture health stay content-free. The only new
  timing field is `concurrent_recording: bool`. Never log notes text, titles or paths.
- Every `begin_meeting` still follows a user click; nothing auto-records.
- Frontend CSS is tokens-only (`var(--…)`); no literal colours.
- **Copy (exact strings):**

  | Where | Text |
  |---|---|
  | Menu status suffix | `" · processing 1 meeting"` / `" · processing {n} meetings"` |
  | Today view | `Processing 1 earlier meeting` / `Processing {n} earlier meetings` |
  | Pill confirm detail, when `pending > 1` | `{pending - 1} more will still be saved` |
  | Quit alert title | `Quit and discard unsaved meetings?` |
  | Quit alert detail, processing only | `1 meeting is still processing. Quitting discards it.` / `{n} meetings are still processing. Quitting discards them.` |
  | Quit alert detail, recording active | `The current recording` prepended to the above: `The current recording and 1 processing meeting will be discarded.` / `…and {n} processing meetings will be discarded.`; with no jobs, `The current recording will be discarded.` |
  | Quit buttons | `Quit` (first), `Cancel` |

- **Commands:**
  - Python tests: `.venv/bin/python -m pytest <files> -q`
  - Frontend tests: `npm --prefix frontend test`
  - Frontend build: `npm --prefix frontend run build`

## Review Focus

1. **Stop then start within the same second.** Today spool names are stamped
   to the second, so the new recording would truncate the queued meeting's
   audio. Both meetings must save their own audio. Pinned in Task 1:
   `test_spool_names_unique_within_one_second`.
2. **Job A finishes while B is recording.** The dictation hotkey must stay off,
   the state must stay `MEETING_RECORDING`, the pill must stay on recording,
   and the menu status must not be replaced by A's progress. Pinned in Task 3
   (`test_job_finishing_during_recording_keeps_recording_state_and_hotkey_off`)
   and Task 7 (`test_saved_is_ignored_while_recording`,
   `test_progress_during_recording_is_not_status_text`).
3. **Notes typed for B while A is processing.** A's notes must stay on A and
   B's on B, including a late autosave of A's notes after the stop. Pinned in
   Task 4 (`test_parked_notes_follow_their_meeting`,
   `test_late_autosave_goes_to_parked_row`) and Task 6
   (`shouldHandOverNotes` tests).
4. **Cancel while A runs and B is queued.** A is discarded and B is saved.
   Pinned in Task 3: `test_cancel_hits_running_job_only`.
5. **Different options per meeting.** Expected speaker count, voice profiles
   and calendar event must not leak from B into A's processing. Pinned in
   Task 2: `test_job_uses_its_own_options_and_event`.

## Pre-flight rulings (controller, 2026-10-10)

- **R1.** `parked_notes` is user data, not derived data: backups copy it. Add
  it to `MASTER` in `tests/test_library_backup.py`. This supersedes the spec's
  "transient like `note_draft`" line, because `note_draft` is already in
  `MASTER`.
- **R2.** `_meeting_active` becomes a read-only property
  (`_recording_active or bool(_jobs)`), so the existing call sites at
  `engine.py:199, 216, 316, 688–726` keep their meaning: "a meeting is
  recording or processing".
- **R3.** `meeting_processing_error` is cleared when a job **starts**, not at
  `begin_meeting`, so a failure in A is not wiped by starting B.
- **R4.** All state publication goes through `_publish_meeting_state()` under
  `self._meeting_lock` (an `RLock`). The listener resume decision is made and
  executed inside the lock. `_begin_meeting` sets `_recording_active = True`
  under the lock **before** calling `self._listener.stop()`.

---

### Task 0: Preconditions and worktree (controller, no subagent)

- [ ] **Step 1:** Run `git status`. The working tree may hold the user's
  unrelated uncommitted change to
  `docs/superpowers/plans/2026-10-07-library-export-recovery.md` and an
  untracked `.claude/`. Do not commit, stash or touch them.
- [ ] **Step 2:** Create the worktree with superpowers:using-git-worktrees,
  on branch `back-to-back-meetings` from `master`. Symlink `.venv` and run
  `npm --prefix frontend ci` if `frontend/node_modules` is missing (see memory
  "Worktree session guards").
- [ ] **Step 3:** Run the full baseline with `timeout: 600000`:
  `.venv/bin/python -m pytest -q` and `npm --prefix frontend test`. Record the
  pass counts in Progress below. If anything fails on master, stop and tell
  the user.

---

### Task 1: Unique spool file names

**Files:**
- Modify: `speakeasy/meeting_recorder.py:291` (mic stamp) and `:604` (system stamp)
- Test: `tests/test_meeting_recorder.py`

**Interfaces:**
- Produces: `meeting_recorder._spool_stamp() -> str`, unique per call within the process.

- [ ] **Step 1: Write the failing test** (append to `tests/test_meeting_recorder.py`)

```python
def test_spool_names_unique_within_one_second(monkeypatch):
    from datetime import datetime as real_datetime
    from speakeasy import meeting_recorder

    class Frozen(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 10, 10, 9, 0, 0)

    monkeypatch.setattr(meeting_recorder, "datetime", Frozen)
    stamps = {meeting_recorder._spool_stamp() for _ in range(50)}
    assert len(stamps) == 50
    assert all(s.startswith("20261010-090000-") for s in stamps)
```

- [ ] **Step 2: Run it to verify it fails**

  Run: `.venv/bin/python -m pytest tests/test_meeting_recorder.py::test_spool_names_unique_within_one_second -q`

  Expected: FAIL, `AttributeError: ... has no attribute '_spool_stamp'`.

- [ ] **Step 3: Implement.** Add this near `_active_spools` in `speakeasy/meeting_recorder.py`:

```python
_spool_counter = itertools.count(1)


def _spool_stamp() -> str:
    # A new recording may start while the previous one's spools are still
    # queued for processing; a per-second stamp alone would let the new
    # writer truncate them.
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{next(_spool_counter)}"
```

  Add `import itertools` to the imports. Replace both
  `stamp = datetime.now().strftime("%Y%m%d-%H%M%S")` lines (`:291` and
  `:604`) with `stamp = _spool_stamp()`.

  In `MeetingCaptureRecorder.start`, the system stamp must equal the mic
  stamp so the pair stays recognisable. Do this:
  1. Read the mic path from `self.mic`, after `self.mic.start()` has set it.
  2. Derive the system path:
     `system_path = self.mic._path.with_name(self.mic._path.name.replace("-mic.wav", "-system.wav"))`.
  3. If `self.mic._path` is not available there, call `_spool_stamp()` once
     instead.

- [ ] **Step 4: Run the meeting recorder tests**

  Run: `.venv/bin/python -m pytest tests/test_meeting_recorder.py tests/test_dual_track_meeting.py tests/test_system_audio.py -q`

  Expected: PASS. If a test asserts an exact spool name, update it to the
  new `YYYYmmdd-HHMMSS-N` pattern.

- [ ] **Step 5: Commit**

  ```bash
  git add speakeasy/meeting_recorder.py tests/test_meeting_recorder.py
  git commit -m "Meetings: unique spool names so a queued meeting's audio is never truncated"
  ```

**Reviewer mutations:**
- Drop the counter suffix; the test must fail.
- Give mic and system different stamps; a dual-track test must notice, or
  the reviewer adds one.

---

### Task 2: `MeetingJob` (per-job fields, no behaviour change yet)

**Files:**
- Modify: `speakeasy/engine.py`
  - new dataclass near `State`;
  - `__init__` (`:113–124`);
  - `cancel_meeting_processing` (`:886`);
  - `_end_meeting` (`:968–1000`);
  - `_saved_meeting_event` (`:875`);
  - `_transcribe_meeting_track` (`:1061`);
  - `_diarize_track` (`:1084`);
  - `_diarize_or_fallback` (`:1119`);
  - `_process_meeting` (`:1131–1410`);
  - `shutdown` (`:224–247`).
- Test: `tests/test_engine_meeting.py`, `tests/test_engine_streaming_fallback.py`

**Interfaces:**
- Produces:

```python
@dataclasses.dataclass(eq=False)
class MeetingJob:
    """Everything one stopped meeting's processing reads. Set once at stop;
    the worker never reads per-meeting values from the engine itself, so a
    new recording can start while this job waits or runs."""
    recording: MeetingRecording
    options: MeetingOptions
    event: object | None              # CalendarEvent held at stop
    timing: MeetingTiming | None = None
    pretranscription: Future | None = None
    cancel: threading.Event = dataclasses.field(default_factory=threading.Event)
    concurrent_recording: bool = False
```

- `DictationEngine._new_job(recording, timing=None, pretranscription=None) -> MeetingJob`
  takes a snapshot of `self._meeting_options` and `self.meeting_event`,
  appends the job to `self._jobs` under `self._meeting_lock`, and returns it.
- `DictationEngine._process_meeting(job: MeetingJob) -> None`
- `DictationEngine._jobs: collections.deque[MeetingJob]`
- `DictationEngine._meeting_lock: threading.RLock`
- `DictationEngine._recording_active: bool`
- `DictationEngine._meeting_active` becomes a property (R2).
- `DictationEngine.pending_meeting_jobs` is an `int` property.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_engine_meeting.py`)

```python
class GatedSpyRunner:
    """Records each call's options and cancel event; blocks until released."""

    def __init__(self):
        self.calls = []
        self.gate = threading.Event()
        self.entered = threading.Event()

    def __call__(self, wav_path, *, expected_count, max_speakers, voice_profile_names,
                 progress, cancel, on_phase=lambda phase: None):
        self.calls.append({"path": wav_path, "expected_count": expected_count,
                           "voices": tuple(voice_profile_names), "cancel": cancel})
        self.entered.set()
        self.gate.wait(timeout=5.0)
        if cancel.is_set():
            raise MeetingCancelled
        progress(1.0)
        return [(0.0, 1.0, 0)]


def test_job_uses_its_own_options_and_event(meetings_dir, spool_dir):
    _seed_events([("ev-a", 3, "Alpha", 1)])
    engine = _engine(spool_dir)
    runner = GatedSpyRunner()
    runner.gate.set()
    engine.diarization_runner = runner
    saved = _record(engine, MeetingOptions(calendar_event_key="ev-a", expected_speaker_count=3))
    engine.end_meeting()
    assert _wait_for(lambda: saved)
    # Changing the engine's current values after stop must not affect the job.
    assert runner.calls[0]["expected_count"] == 3
    assert MeetingLibrary().get_meeting(saved[0]).title == "Alpha"
    assert engine.pending_meeting_jobs == 0
    engine.shutdown()


def test_job_snapshot_is_independent_of_engine_fields(meetings_dir, spool_dir):
    _seed_events([("ev-a", 3, "Alpha", 1), ("ev-b", 1, "Beta", 1)])
    engine = _engine(spool_dir)
    engine._meeting_options = MeetingOptions(expected_speaker_count=4)
    engine.meeting_event = MeetingLibrary().calendar_event("ev-a")
    job = engine._new_job(MeetingRecording(mic_path=None))
    engine._meeting_options = MeetingOptions(expected_speaker_count=2)
    engine.meeting_event = MeetingLibrary().calendar_event("ev-b")
    assert job.options.expected_speaker_count == 4
    assert job.event.title == "Alpha"
    assert job.cancel is not engine._new_job(MeetingRecording(mic_path=None)).cancel
    engine.shutdown()
```

  Update the existing tests that touch the old fields:

  | Location | Old | New |
  |---|---|---|
  | `:914` and `:997` | `engine._process_meeting(MeetingRecording(...))` | `engine._process_meeting(engine._new_job(MeetingRecording(...)))` (same arguments inside) |
  | `:1491` | `assert seen["cancel"] is engine._meeting_cancel` | `assert isinstance(seen["cancel"], threading.Event)` and `assert seen["cancel"] is not getattr(engine, "_meeting_cancel", None)` |
  | `tests/test_engine_streaming_fallback.py:307` | `engine._meeting_active is False` | Leave as is: the property keeps it valid |

- [ ] **Step 2: Run to verify the new tests fail**

  Run: `.venv/bin/python -m pytest tests/test_engine_meeting.py -q -k "own_options or snapshot_is_independent"`

  Expected: FAIL, `AttributeError: '_new_job'` or `'pending_meeting_jobs'`.

- [ ] **Step 3: Implement.** In `speakeasy/engine.py`:

  **3a. Imports and the dataclass.** Add `import collections` and define the
  `MeetingJob` dataclass above under `State`. `MeetingTiming` is already
  imported (used in `_end_meeting`).

  **3b. `__init__`.** Replace these lines:

  ```
  self._meeting_active = False
  self._meeting_cancel = threading.Event()
  ```

  with:

  ```python
  # Recording side (control thread) and the processing queue (appended on
  # control at stop, removed on worker when the job ends). State, listener
  # resume and both of these change only under _meeting_lock.
  self._recording_active = False
  self._jobs: collections.deque[MeetingJob] = collections.deque()
  self._meeting_lock = threading.RLock()
  ```

  **3c. Properties.** Add them after `__init__`:

  ```python
  @property
  def _meeting_active(self) -> bool:
      return self._recording_active or bool(self._jobs)

  @property
  def pending_meeting_jobs(self) -> int:
      with self._meeting_lock:
          return len(self._jobs)

  def _new_job(self, recording, timing=None, pretranscription=None) -> MeetingJob:
      job = MeetingJob(recording=recording, options=self._meeting_options,
                       event=self.meeting_event, timing=timing,
                       pretranscription=pretranscription)
      with self._meeting_lock:
          self._jobs.append(job)
      return job
  ```

  **3d. Recording flags.** Replace every write of `self._meeting_active = True`
  with `self._recording_active = True`. Replace every write of
  `self._meeting_active = False` in `_meeting_start_failed` and in the
  no-recording branch of `_end_meeting` with `self._recording_active = False`.

  **3e. `_end_meeting`.** Set `self._recording_active = False` right after
  `recording = self._stop_meeting_recorder_guarded()` returns, before the
  `if recording is None` check, and delete the duplicate in that branch.

  Replace `self._meeting_cancel.clear()` and the `worker.submit` call with:

  ```python
  job = self._new_job(recording, timing, pretranscription)
  self._set_state(State.MEETING_PROCESSING)
  timing.capture_mode = recording.capture_mode
  timing.start("backlog")
  self.on_meeting_progress("Finishing captured audio…")
  self.worker.submit(self._process_meeting, job)
  ```

  **3f. `cancel_meeting_processing`.**

  ```python
  def cancel_meeting_processing(self) -> None:
      """Abort the job being processed now (the queue's head) and discard
      that meeting. Queued jobs are untouched. Just sets its event — the
      worker polls it between chunks and phases."""
      with self._meeting_lock:
          head = self._jobs[0] if self._jobs else None
      if head is None:
          return
      head.cancel.set()
      if self.state is State.MEETING_PROCESSING:
          self.on_meeting_progress("Cancelling… waiting for the current stage")
  ```

  **3g. `shutdown`.** Replace `self._meeting_cancel.set()` with:

  ```python
  with self._meeting_lock:
      jobs = list(self._jobs)
  for job in jobs:
      job.cancel.set()
  ```

  Change `if self._meeting_active:` (force-close the recorder) to
  `if self._recording_active:`.

  **3h. `_saved_meeting_event(self, held)`.** Take the held event as a
  parameter instead of reading `self.meeting_event`.

  **3i. `_transcribe_meeting_track(self, path, *, progress, cancel, precomputed=None)`.**
  Pass `cancel=cancel` to `transcribe_long_wav` and `transcribe_long`.

  **3j. `_diarize_track(self, path, progress, timing=None, *, max_speakers=None, job)`.**
  Use `job.options.expected_speaker_count`,
  `job.options.expected_voice_profile_names` and `cancel=job.cancel`.

  **3k. `_diarize_or_fallback(self, path, progress, timing, *, max_speakers, job)`.**
  Pass `job=job` through.

  **3l. `_process_meeting(self, job: MeetingJob)`.** At the top:

  ```python
  recording, timing, pretranscription = job.recording, job.timing, job.pretranscription
  cancel = job.cancel
  self.meeting_processing_error = None
  ```

  Then make these replacements in the body:
  - every `self._meeting_cancel` with `cancel`;
  - `self._saved_meeting_event()` with `self._saved_meeting_event(job.event)`;
  - add `cancel=cancel` to each `_transcribe_meeting_track` call;
  - add `job=job` to each `_diarize_or_fallback` call.

  In `finally`, replace these lines:

  ```
  self._meeting_active = False
  self.meeting_event = None
  if not self._user_paused:
      self._listener.resume()
  self._set_state(self._idle_state())
  ```

  with:

  ```python
  with self._meeting_lock:
      self._jobs.remove(job)
      if not self._meeting_active:
          self.meeting_event = None
          if not self._user_paused:
              self._listener.resume()
      self._set_state(self._idle_state() if not self._jobs else State.MEETING_PROCESSING)
  ```

  Task 3 replaces this with `_publish_meeting_state()`.

  **3m. `_begin_meeting`.** Remove `self.meeting_processing_error = None`
  (R3). Keep `self.meeting_start_error = None`. The guard
  `self.state is not State.READY or self._meeting_active` stays as it is in
  this task.

- [ ] **Step 4: Run the engine suites**

  Run: `.venv/bin/python -m pytest tests/test_engine_meeting.py tests/test_engine_streaming_fallback.py tests/test_engine*.py -q`

  Expected: all PASS.

- [ ] **Step 5: Commit**

  ```bash
  git add speakeasy/engine.py tests/test_engine_meeting.py tests/test_engine_streaming_fallback.py
  git commit -m "Engine: meeting processing reads a per-job snapshot (MeetingJob)"
  ```

**Reviewer mutations:**
- `_diarize_track` reads `self._meeting_options` again: `test_job_uses_its_own_options_and_event` or the snapshot test must fail.
- `_new_job` reuses one shared `threading.Event`: the snapshot test must fail.
- Drop `self._jobs.remove(job)`: a test asserting `pending_meeting_jobs == 0` must fail.

---

### Task 3: Overlap — start while processing (HARD: Opus-flagged)

**Files:**
- Modify: `speakeasy/engine.py`
  - `_begin_meeting` (`:910–966`);
  - `_meeting_start_failed` (`:894`);
  - `_end_meeting`;
  - `_process_meeting` `finally`;
  - new `_meeting_state` and `_publish_meeting_state`.
- Modify: `speakeasy/meeting_benchmark.py`: `FIELDS`, the `MeetingTiming` field, `record`, `_valid`
- Test: `tests/test_engine_meeting.py`, `tests/test_meeting_benchmark.py`

**Interfaces:**
- Consumes: Task 2's `MeetingJob`, `_jobs`, `_meeting_lock`, `_recording_active`, `_new_job`.
- Produces:
  - `DictationEngine._meeting_state() -> State`
  - `DictationEngine._publish_meeting_state() -> None`
  - `MeetingTiming.concurrent_recording: bool`
  - the `"concurrent_recording"` timing field

- [ ] **Step 1: Write the failing engine tests** (append to `tests/test_engine_meeting.py`)

```python
class UniqueSpoolRecorder(FakeMeetingRecorder):
    """Each start writes a new spool, like the real recorder (Task 1)."""

    def __init__(self, spool_dir):
        super().__init__(spool_dir)
        self.count = 0

    def start(self, *, system_audio_pid=None):
        self.count += 1
        self._path = self._spool_dir / f"meeting-test-{self.count}.wav"
        with wave.open(str(self._path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(config.SAMPLE_RATE)
            w.writeframes(np.ones(config.SAMPLE_RATE, dtype=np.int16).tobytes())


def _overlap_engine(spool_dir):
    engine = _engine(spool_dir)
    engine.meeting_recorder = UniqueSpoolRecorder(spool_dir)
    runner = GatedSpyRunner()
    engine.diarization_runner = runner
    saved = []
    engine.on_meeting_saved = saved.append
    return engine, runner, saved


def _start(engine):
    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)


def test_can_start_recording_while_previous_meeting_processes(meetings_dir, spool_dir):
    engine, runner, saved = _overlap_engine(spool_dir)
    _start(engine)
    engine.end_meeting()
    assert runner.entered.wait(5.0)                      # A is diarizing
    assert engine.state is State.MEETING_PROCESSING
    _start(engine)                                       # B starts during A
    assert engine.pending_meeting_jobs == 1
    runner.gate.set()
    assert _wait_for(lambda: len(saved) == 1)            # A saved during B
    assert engine.state is State.MEETING_RECORDING
    engine.end_meeting()
    assert _wait_for(lambda: len(saved) == 2 and engine.state is State.READY)
    assert len(set(saved)) == 2
    assert list(spool_dir.iterdir()) == []
    engine.shutdown()


def test_job_finishing_during_recording_keeps_recording_state_and_hotkey_off(meetings_dir, spool_dir):
    engine, runner, saved = _overlap_engine(spool_dir)
    states = []
    engine.on_state_changed = states.append
    _start(engine)
    engine.end_meeting()
    assert runner.entered.wait(5.0)
    _start(engine)
    states.clear()
    runner.gate.set()
    assert _wait_for(lambda: len(saved) == 1)
    time.sleep(0.05)
    assert engine._listener.running is False
    assert State.READY not in states
    assert engine.state is State.MEETING_RECORDING
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    assert engine._listener.running is True
    engine.shutdown()


def test_hotkey_stays_off_until_queue_drains(meetings_dir, spool_dir):
    engine, runner, saved = _overlap_engine(spool_dir)
    _start(engine)
    engine.end_meeting()
    assert runner.entered.wait(5.0)
    _start(engine)
    engine.end_meeting()                                 # A running, B queued
    assert engine.pending_meeting_jobs == 2
    runner.entered.clear()
    runner.gate.set()
    assert _wait_for(lambda: len(saved) == 2)
    assert _wait_for(lambda: engine.state is State.READY)
    assert engine._listener.running is True
    engine.shutdown()


def test_cancel_hits_running_job_only(meetings_dir, spool_dir):
    engine, runner, saved = _overlap_engine(spool_dir)
    _start(engine)
    engine.end_meeting()
    assert runner.entered.wait(5.0)
    _start(engine)
    engine.end_meeting()
    engine.cancel_meeting_processing()                   # cancels A only
    runner.gate.set()
    assert _wait_for(lambda: engine.state is State.READY)
    assert len(saved) == 1
    assert runner.calls[0]["cancel"].is_set()
    assert not runner.calls[1]["cancel"].is_set()
    engine.shutdown()


class OptionalPretranscribingRecorder(UniqueSpoolRecorder):
    """Supports live pre-transcription, but works when the engine skips it."""

    def __init__(self, spool_dir):
        super().__init__(spool_dir)
        self.session = None
        self.configured = 0

    def configure_pretranscription(self, session):
        self.session = session
        self.configured += 1

    def stop(self):
        if self.session is not None:
            self.session.finish()
            self.session = None
        return super().stop()


def test_live_pretranscription_skipped_while_jobs_pending(meetings_dir, spool_dir):
    engine, runner, saved = _overlap_engine(spool_dir)
    transcriber = PretranscribingFakeTranscriber()
    engine.transcriber = transcriber
    recorder = OptionalPretranscribingRecorder(spool_dir)
    engine.meeting_recorder = recorder
    _start(engine)
    assert recorder.configured == 1                       # A: worker idle
    engine.end_meeting()
    assert runner.entered.wait(5.0)
    _start(engine)
    assert recorder.configured == 1                       # B: skipped, A pending
    runner.gate.set()
    engine.end_meeting()
    assert _wait_for(lambda: len(saved) == 2 and engine.state is State.READY)
    engine.shutdown()


def test_start_failure_during_processing_returns_to_processing(meetings_dir, spool_dir):
    engine, runner, saved = _overlap_engine(spool_dir)
    _start(engine)
    engine.end_meeting()
    assert runner.entered.wait(5.0)

    def boom(**kwargs):
        raise RuntimeError("no mic")

    engine.meeting_recorder.start = boom
    engine.begin_meeting()
    assert _wait_for(lambda: engine.meeting_start_error == "microphone_start_failed")
    assert engine.state is State.MEETING_PROCESSING
    assert engine._listener.running is False
    runner.gate.set()
    assert _wait_for(lambda: engine.state is State.READY)
    assert engine._listener.running is True
    engine.shutdown()


def test_timing_marks_concurrent_recording(meetings_dir, spool_dir, isolate_meeting_latency_log):
    engine, runner, saved = _overlap_engine(spool_dir)
    _start(engine)
    engine.end_meeting()
    assert runner.entered.wait(5.0)
    _start(engine)
    runner.gate.set()
    assert _wait_for(lambda: len(saved) == 1)
    engine.end_meeting()
    assert _wait_for(lambda: len(saved) == 2 and engine.state is State.READY)
    assert _wait_for(lambda: len(meeting_benchmark.read_records(isolate_meeting_latency_log)) == 2)
    records = meeting_benchmark.read_records(isolate_meeting_latency_log)
    assert [r["concurrent_recording"] for r in records] == [True, False]
    engine.shutdown()


def test_shutdown_with_queued_jobs_cancels_all(meetings_dir, spool_dir):
    engine, runner, saved = _overlap_engine(spool_dir)
    _start(engine)
    engine.end_meeting()
    assert runner.entered.wait(5.0)
    _start(engine)
    engine.end_meeting()
    with engine._meeting_lock:
        jobs = list(engine._jobs)
    engine.shutdown()
    assert all(job.cancel.is_set() for job in jobs)
    runner.gate.set()
```

- [ ] **Step 2: Write the failing benchmark test** (append to `tests/test_meeting_benchmark.py`)

```python
def test_concurrent_recording_field_round_trips_and_old_rows_default_false(tmp_path):
    from speakeasy import meeting_benchmark as mb
    path = tmp_path / "t.jsonl"
    timing = mb.MeetingTiming(capture_mode="mic_only")
    timing.concurrent_recording = True
    timing.mark("pipeline_finished")
    timing.emit("success", path=path)
    old = {k: None for k in mb.FIELDS if k.endswith("_ms")}
    old.update(build_commit="x", status="success", capture_mode="mic_only",
               mic_frames=1, system_frames=0)
    with path.open("a") as f:
        f.write(json.dumps(old) + "\n")
    records = mb.read_records(path)
    assert [r["concurrent_recording"] for r in records] == [True, False]
    bad = dict(old, concurrent_recording="yes")
    with path.open("a") as f:
        f.write(json.dumps(bad) + "\n")
    assert len(mb.read_records(path)) == 2
```

  Add `import json` at the top of that file if it is missing.

- [ ] **Step 3: Run to verify they fail**

  Run: `.venv/bin/python -m pytest tests/test_engine_meeting.py tests/test_meeting_benchmark.py -q -k "while_previous or finishing_during or until_queue or running_job_only or pretranscription_skipped or start_failure_during or concurrent"`

  Expected: FAIL. `begin_meeting` is refused in `MEETING_PROCESSING`, and the
  `concurrent_recording` key is missing.

- [ ] **Step 4: Implement `meeting_benchmark.py`**

  - Append `"concurrent_recording"` to the end of `FIELDS`.
  - Add the field `concurrent_recording: bool = False` to `MeetingTiming`
    (before `_emitted`).
  - In `record()`, add `"concurrent_recording": self.concurrent_recording` to `values`.
  - In `_valid`, after the `backlog_ms` tolerance, add:

  ```python
  if isinstance(value, dict) and "concurrent_recording" not in value:
      value = dict(value, concurrent_recording=False)
  ```

  Do this before the `set(value) != set(FIELDS)` check. The existing
  `backlog_ms` check compares against `set(FIELDS) - {"backlog_ms"}`, so make
  it `set(FIELDS) - {"backlog_ms", "concurrent_recording"}` and apply the
  `concurrent_recording` default afterwards, so a legacy row missing both
  fields still loads.

  Then add `if not isinstance(value["concurrent_recording"], bool): return None`.
  Change the numeric loop `for name in FIELDS[5:]:` to iterate over
  `[n for n in FIELDS if n.endswith("_ms")]`.

- [ ] **Step 5: Implement the engine** (`speakeasy/engine.py`)

  **5a. State helpers.** Add them after the `_new_job` helper:

  ```python
  def _meeting_state(self) -> State:
      if self._recording_active:
          return State.MEETING_RECORDING
      if self._jobs:
          return State.MEETING_PROCESSING
      return self._idle_state()

  def _publish_meeting_state(self) -> None:
      """The one place meeting transitions set state and re-arm dictation.
      Decided and applied under _meeting_lock so a job finishing on worker
      can never publish READY (or resume the hotkey) over a recording that
      control just started."""
      with self._meeting_lock:
          if not self._meeting_active:
              self.meeting_event = None
              if not self._user_paused and not self._shutting_down:
                  self._listener.resume()
          self._set_state(self._meeting_state())
  ```

  **5b. Top of `_begin_meeting`.** Replace the guard and
  `self._meeting_active = True` with:

  ```python
  with self._meeting_lock:
      if (self._library_maintenance or self._recording_active
              or self.state not in (State.READY, State.MEETING_PROCESSING)):
          return
      with self._dictation_stream_lock:
          if self._dictation_stream is not None:
              return
      self._recording_active = True
      worker_busy = bool(self._jobs)
      for job in self._jobs:
          job.concurrent_recording = True
  ```

  Keep the rest of the setup outside the lock (listener stop, recorder stop,
  start).

  Wrap the pretranscription block with `if not worker_busy:` (D3). The live
  job would only wait behind processing and then overflow.

  **5c. `_meeting_start_failed`.**

  ```python
  def _meeting_start_failed(self, reason):
      from .dictation_benchmark import _append_record
      self.meeting_start_error = reason
      self._cancel_meeting_pretranscription()
      with self._meeting_lock:
          self._recording_active = False
      self._publish_meeting_state()
      try:
          _append_record({"build_commit": settings.build_commit(),
                          "timestamp_seconds": round(time.time(), 3), "reason": reason},
                         Path.home() / "Library/Logs/Speakeasy-meeting-start.jsonl")
      except OSError:
          pass
  ```

  **5d. `_end_meeting`.**
  - Set `self._recording_active = False` under `self._meeting_lock`.
  - In the `recording is None` branch, replace the listener resume and
    `_set_state` with `self._publish_meeting_state()`.
  - In the job branch, replace `self._set_state(State.MEETING_PROCESSING)`
    with `self._publish_meeting_state()`. The job is appended first, so this
    publishes `MEETING_PROCESSING`.

  **5e. `_process_meeting` `finally`.** Replace the Task 2 block with:

  ```python
  with self._meeting_lock:
      self._jobs.remove(job)
  self._publish_meeting_state()
  if timing is not None:
      timing.concurrent_recording = job.concurrent_recording
      timing.mark("pipeline_finished")
      timing.emit(status)
  ```

  `_publish_meeting_state` takes the RLock itself. A recording that starts
  between the two blocks is seen by `_publish_meeting_state`, which then
  publishes `MEETING_RECORDING`.

  **5f. Recording state.** `_link_meeting_event` and `_end_meeting` keep
  checking `self.state is State.MEETING_RECORDING`. Leave those as they are.
  `_end_meeting`'s guard becomes
  `if not self._recording_active or self.state is not State.MEETING_RECORDING: return`.

- [ ] **Step 6: Run the new tests 20 times to shake out races**

  Run: `for i in $(seq 20); do .venv/bin/python -m pytest tests/test_engine_meeting.py -q -k "while_previous or finishing_during or until_queue or running_job_only or pretranscription_skipped or start_failure_during or concurrent or shutdown_with_queued" -p no:randomly || break; done`

  Expected: 20 clean passes. **Any flake means escalate to Opus** (see Model routing).

- [ ] **Step 7: Run the engine, benchmark and AGENTS-adjacent suites**

  Run: `.venv/bin/python -m pytest tests/test_engine*.py tests/test_meeting_benchmark.py tests/test_meeting_recorder.py -q`

  Expected: PASS.

- [ ] **Step 8: Commit**

  ```bash
  git add speakeasy/engine.py speakeasy/meeting_benchmark.py tests/test_engine_meeting.py tests/test_meeting_benchmark.py
  git commit -m "Engine: start a meeting while earlier ones process (FIFO job queue)"
  ```

**Reviewer mutations (minimum):**
- In `_publish_meeting_state`, resume the listener when `not self._recording_active` (ignoring jobs): `test_hotkey_stays_off_until_queue_drains` must fail.
- Move `self._recording_active = True` after `self._listener.stop()` and outside the lock: argue whether any test catches it. If none does, add one that drives `_publish_meeting_state` from a second thread at the moment between the two.
- Drop `if not worker_busy`: `test_live_pretranscription_skipped_while_jobs_pending` must fail.
- `cancel_meeting_processing` sets every job's cancel: `test_cancel_hits_running_job_only` must fail.
- Remove `READY` from the `_begin_meeting` guard: the existing happy-path tests must fail.

---

### Task 4: Parked notes (schema v8) and engine park hook

**Files:**
- Modify: `speakeasy/meeting_store.py`: append the v8 migration and set `SCHEMA_VERSION = 8`.
- Modify: `speakeasy/meeting_library.py`:
  - new `DraftMismatch`;
  - `park_draft`;
  - `set_draft` routing;
  - `_adopt_draft` order;
  - `finish_draft` check.
- Modify: `speakeasy/engine.py`: `_end_meeting` calls `self.library.park_draft(...)`.
- Test: `tests/test_user_notes.py`, `tests/test_action_item_schema.py` (version literals), `tests/test_library_backup.py` (`MASTER`), `tests/test_engine_meeting.py`

**Interfaces:**
- Produces:
  - `MeetingLibrary.park_draft(started_at: datetime) -> bool`: true if a draft was parked.
  - `class DraftMismatch(ValueError)`, with `str(DraftMismatch()) == "draft_mismatch"`.

- [ ] **Step 1: Write the failing library tests** (append to `tests/test_user_notes.py`)

```python
from datetime import datetime, timedelta, timezone

from speakeasy.meeting_library import DraftMismatch, MeetingLibrary, NewMeeting, utc_iso
from speakeasy.meetings import MeetingSegment

A = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)
B = A + timedelta(minutes=61)


def _new(start, minutes=60):
    return NewMeeting(segments=[MeetingSegment("You", 1, 2, "hi")],
                      duration_seconds=minutes * 60, started_at=start)


def test_parked_notes_follow_their_meeting():
    lib = MeetingLibrary()
    lib.set_draft("A notes", [], utc_iso(A))
    assert lib.park_draft(A) is True
    assert lib.get_draft() is None                    # slot is free for B
    lib.set_draft("B notes", [], utc_iso(B))
    a_id = lib.save_meeting(_new(A))                  # A saves while B records
    assert lib.get_user_notes(a_id).markdown == "A notes"
    assert lib.get_draft().markdown == "B notes"      # B's draft untouched
    b_id = lib.save_meeting(_new(B, 30))
    assert lib.get_user_notes(b_id).markdown == "B notes"


def test_late_autosave_goes_to_parked_row():
    lib = MeetingLibrary()
    lib.set_draft("A", [], utc_iso(A))
    lib.park_draft(A)
    lib.set_draft("A plus late words", [], utc_iso(A))    # editor's last save
    assert lib.get_draft() is None
    a_id = lib.save_meeting(_new(A))
    assert lib.get_user_notes(a_id).markdown == "A plus late words"


def test_park_ignores_other_recordings_draft():
    lib = MeetingLibrary()
    lib.set_draft("B notes", [], utc_iso(B))
    assert lib.park_draft(A) is False
    assert lib.get_draft().markdown == "B notes"


def test_finish_draft_rejects_another_recordings_notes():
    lib = MeetingLibrary()
    a_id = lib.save_meeting(_new(A))
    with pytest.raises(DraftMismatch) as err:
        lib.finish_draft(a_id, "B notes", [], utc_iso(B))
    assert str(err.value) == "draft_mismatch"


def test_window_fallback_still_adopts_unparked_draft():
    lib = MeetingLibrary()
    lib.set_draft("typed", [], utc_iso(A + timedelta(minutes=5)))
    a_id = lib.save_meeting(_new(A))
    assert lib.get_user_notes(a_id).markdown == "typed"
```

  Merge these imports into the file's existing ones (add `import pytest` if it
  is missing). `MeetingLibrary.get_user_notes(meeting_id)` is the existing
  reader (`meeting_library.py:779`).

  Update the version and backup tests:
  - `tests/test_action_item_schema.py:27`: `== 7 == meeting_store.SCHEMA_VERSION` becomes `== meeting_store.SCHEMA_VERSION`.
  - `tests/test_action_item_schema.py:55` and `:77`: `== 7` becomes `>= 7`. These tests migrate to v7 then on; the latest version is now 8.
  - `tests/test_library_backup.py`: add `"parked_notes"` to `MASTER` (R1). In
    `test_backup_restore_preserves_master_data_and_rebuilds_derived`, add one
    `parked_notes` row via `lib.set_draft(...)` and then `lib.park_draft(...)`.

- [ ] **Step 2: Write the failing engine test** (append to `tests/test_engine_meeting.py`)

```python
def test_end_meeting_parks_that_recordings_draft(meetings_dir, spool_dir):
    from speakeasy.meeting_library import utc_iso
    engine, runner, saved = _overlap_engine(spool_dir)
    _start(engine)
    engine.library.set_draft("A notes", [], utc_iso(engine._meeting_started_at))
    engine.end_meeting()
    assert runner.entered.wait(5.0)
    assert engine.library.get_draft() is None
    runner.gate.set()
    assert _wait_for(lambda: saved)
    assert MeetingLibrary().get_user_notes(saved[0]).markdown == "A notes"
    engine.shutdown()
```

- [ ] **Step 3: Run to verify they fail**

  Run: `.venv/bin/python -m pytest tests/test_user_notes.py tests/test_engine_meeting.py -q -k "parked or late_autosave or park_ignores or finish_draft_rejects or window_fallback or parks_that"`

  Expected: FAIL (ImportError for `DraftMismatch`).

- [ ] **Step 4: Implement the v8 migration** (`speakeasy/meeting_store.py`)

  ```python
  # v8: notes parked at stop under their recording's start, so a recording
  # that starts while the previous meeting is still processing gets a clean
  # draft slot (spec 2026-10-10-back-to-back-meetings-design.md §3).
  _SCHEMA_V8 = """
  BEGIN IMMEDIATE;
  CREATE TABLE IF NOT EXISTS parked_notes (
      started_at TEXT PRIMARY KEY,
      markdown TEXT NOT NULL,
      stamps_json TEXT NOT NULL DEFAULT '[]',
      parked_at TEXT NOT NULL
  );
  PRAGMA user_version = 8;
  COMMIT;
  """
  ```

  Append `(8, _SCHEMA_V8)` to `_MIGRATIONS` and set `SCHEMA_VERSION = 8`.
  Check `library_backup.py`'s `rebuild_derived` and table handling: if it
  lists tables explicitly, add `parked_notes` as master data.

- [ ] **Step 5: Implement the library** (`speakeasy/meeting_library.py`)

  ```python
  class DraftMismatch(ValueError):
      """The editor's notes belong to a different recording than this meeting."""

      def __init__(self):
          super().__init__("draft_mismatch")
  ```

  ```python
  def park_draft(self, started_at: datetime) -> bool:
      """Move the draft typed during the recording that started at
      `started_at` aside, so the next recording starts with an empty slot."""
      key = utc_iso(started_at)
      with self._transaction() as conn:
          row = conn.execute("SELECT * FROM note_draft WHERE id = 1").fetchone()
          if row is None or row["started_at"] != key:
              return False
          conn.execute(
              "INSERT INTO parked_notes (started_at, markdown, stamps_json, parked_at)"
              " VALUES (?, ?, ?, ?) ON CONFLICT(started_at) DO UPDATE SET"
              " markdown = excluded.markdown, stamps_json = excluded.stamps_json,"
              " parked_at = excluded.parked_at",
              (key, row["markdown"], row["stamps_json"], _now_iso()))
          conn.execute("DELETE FROM note_draft WHERE id = 1")
          return True
  ```

  In `set_draft`, inside the transaction and before the `note_draft` upsert:

  ```python
  parked = conn.execute("SELECT 1 FROM parked_notes WHERE started_at = ?",
                        (drafted,)).fetchone()
  if parked is not None:
      conn.execute("UPDATE parked_notes SET markdown = ?, stamps_json = ? WHERE started_at = ?",
                   (markdown, json.dumps([list(s) for s in checked]), drafted))
      return NoteDraft(markdown, checked, drafted, now)
  ```

  `_adopt_draft(conn, meeting_id, new)` first tries the parked row:

  ```python
  key = utc_iso(new.started_at)
  parked = conn.execute("SELECT * FROM parked_notes WHERE started_at = ?", (key,)).fetchone()
  if parked is not None:
      stamps = [tuple(s) for s in json.loads(parked["stamps_json"])]
      self._write_user_notes(conn, meeting_id, parked["markdown"], stamps)
      conn.execute("DELETE FROM parked_notes WHERE started_at = ?", (key,))
      return
  ```

  After that, the existing `note_draft` window rule runs unchanged. The
  parked stamps are already relative to that recording's start, so they are
  not shifted.

  `finish_draft`: after loading `m`, add
  `if utc_iso(datetime.fromisoformat(str(started_at))) != m["started_at"]: raise DraftMismatch()`.
  Before the existing `DELETE FROM note_draft`, also run
  `DELETE FROM parked_notes WHERE started_at = ?` with `m["started_at"]`.

- [ ] **Step 6: Add the engine hook** (`speakeasy/engine.py`, `_end_meeting`)

  Right after `job = self._new_job(...)` and **before** `self.worker.submit(...)`.
  The job must not be able to save before its notes are parked:

  ```python
  if recording.started_at is not None:
      try:
          self.library.park_draft(recording.started_at)
      except (sqlite3.Error, OSError):
          print("  → notes_park_failed")
  ```

- [ ] **Step 7: Run the tests**

  Run: `.venv/bin/python -m pytest tests/test_user_notes.py tests/test_action_item_schema.py tests/test_library_backup.py tests/test_meeting_store.py tests/test_engine_meeting.py tests/test_meetings_bridge.py -q`

  Expected: PASS.

- [ ] **Step 8: Commit**

  ```bash
  git add speakeasy/meeting_store.py speakeasy/meeting_library.py speakeasy/engine.py tests/test_user_notes.py tests/test_action_item_schema.py tests/test_library_backup.py tests/test_engine_meeting.py
  git commit -m "Notes: park each recording's draft at stop (schema v8)"
  ```

**Reviewer mutations:**
- `_adopt_draft` skips the parked lookup: `test_parked_notes_follow_their_meeting` must fail.
- `set_draft` ignores parked rows: `test_late_autosave_goes_to_parked_row` must fail.
- Remove the `finish_draft` check: the mismatch test must fail.
- `park_draft` without the `started_at` equality check: `test_park_ignores_other_recordings_draft` must fail.

---

### Task 5: Bridge and Meetings-window payloads

**Files:**
- Modify: `speakeasy/ui/meetings_window.py` (`recording_info` at `:53–69`, `meetingSaved_` at `:145`)
- Modify: `speakeasy/ui/meetings_bridge.py`: `_NOT_RECORDING` at `:70` gains `"pendingProcessing": 0`.
- Test: `tests/test_meetings_bridge.py`

**Interfaces:**
- Consumes:
  - `engine.pending_meeting_jobs` (Task 2);
  - `DraftMismatch` (Task 4), which reaches JS as `Error("draft_mismatch")` through `_wrap`'s `ValueError` branch.
- Produces:
  - the `recording.get` payload gains `pendingProcessing: int`;
  - the `meetings.saved` event payload becomes `{"id": str, "startedAt": str | None}`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_meetings_bridge.py`, following its existing bridge construction helpers)

```python
def test_not_recording_payload_has_pending_zero(tmp_path):
    assert make_bridge(tmp_path).recording_payload({})["pendingProcessing"] == 0


def test_draft_finish_mismatch_returns_error_code(tmp_path):
    from datetime import datetime, timezone
    bridge = make_bridge(tmp_path)
    from speakeasy.meeting_library import NewMeeting, utc_iso
    from speakeasy.meetings import MeetingSegment
    start = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)
    mid = bridge.library.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 1, 2, "x")], duration_seconds=60, started_at=start))
    responses = []
    handler = bridge._wrap(bridge.draft_finish_payload)
    handler({"id": mid, "markdown": "B", "stamps": [],
             "startedAt": "2026-10-10T10:01:00Z"},
            lambda value=None, error=None: responses.append(error))
    assert responses == ["draft_mismatch"]
```

  `make_bridge(tmp_path)` is the file's existing helper (`:23`).

- [ ] **Step 2: Run them to verify they fail**, then implement:

  - `_NOT_RECORDING` gains `"pendingProcessing": 0`.
  - `recording_info()` in `meetings_window.py` adds
    `"pendingProcessing": int(getattr(engine, "pending_meeting_jobs", 0))`.
  - `meetingSaved_` becomes:

  ```python
  def meetingSaved_(self, meeting_id):
      started_at = None
      try:
          started_at = self._bridge.library.get_meeting(
              str(meeting_id), with_segments=False).started_at
      except Exception:
          pass   # the page falls back to the server-side draft_mismatch check
      self._web.emit("meetings.changed")
      self._web.emit("meetings.saved", {"id": str(meeting_id), "startedAt": started_at})
  ```

- [ ] **Step 3: Run the bridge tests**

  Run: `.venv/bin/python -m pytest tests/test_meetings_bridge.py tests/test_meetings_bridge_actions.py tests/test_webbridge.py -q`

  Expected: PASS.

- [ ] **Step 4: Commit**

  ```bash
  git add speakeasy/ui/meetings_window.py speakeasy/ui/meetings_bridge.py tests/test_meetings_bridge.py
  git commit -m "Bridge: pending job count and saved meeting start time for the notes hand-over"
  ```

**Reviewer mutations:**
- Drop `pendingProcessing` from `_NOT_RECORDING`: the test must fail.
- Make `DraftMismatch` not subclass `ValueError`: the error-code test must fail.

---

### Task 6: Frontend — notes hand-over, start button, processing note

**Files:**
- Modify: `frontend/src/mock/meetings.ts` (`RecordingInfo` gets `pendingProcessing?: number`)
- Modify: `frontend/src/meetings/recordingState.ts`: add `shouldHandOverNotes`, `canStartRecording`, `processingNote`.
- Modify: `frontend/src/meetings/App.tsx`:
  - `onMeetingSaved` at `:304–322`;
  - the `meetings.saved` listener at `:328`;
  - the `RecordingNotes` render, which gets `key`.
- Modify: `frontend/src/meetings/TodayView.tsx` (`:84–106`, `:183–197`)
- Test: `frontend/tests/recordingState.test.ts`

**Interfaces:**
- Consumes: the `meetings.saved` payload `{id, startedAt}` and `pendingProcessing` (Task 5).
- Produces:
  - `shouldHandOverNotes(recordingView: boolean, savedStartedAt: string | null | undefined, recordingStartedAt: string | null): boolean`
  - `canStartRecording(info: RecordingInfo): boolean`
  - `processingNote(info: RecordingInfo): string | null`

- [ ] **Step 1: Write the failing tests** (append to `frontend/tests/recordingState.test.ts`; extend its import line)

```ts
import { canStartRecording, processingNote, shouldHandOverNotes } from '../src/meetings/recordingState.ts';

test('hands notes over only to the meeting they were typed for', () => {
  assert.equal(shouldHandOverNotes(true, '2026-10-10T09:00:00Z', '2026-10-10T09:00:00Z'), true);
  assert.equal(shouldHandOverNotes(true, '2026-10-10T09:00:00Z', '2026-10-10T10:01:00Z'), false);
  assert.equal(shouldHandOverNotes(true, '2026-10-10T09:00:00Z', '2026-10-10T09:00:00.000Z'), true);
  assert.equal(shouldHandOverNotes(false, '2026-10-10T09:00:00Z', '2026-10-10T09:00:00Z'), false);
  // Unknown start on either side: keep today's behaviour; the server check is the backstop.
  assert.equal(shouldHandOverNotes(true, null, '2026-10-10T09:00:00Z'), true);
  assert.equal(shouldHandOverNotes(true, '2026-10-10T09:00:00Z', null), true);
});

test('start is allowed whenever nothing is recording', () => {
  assert.equal(canStartRecording(info({})), true);
  assert.equal(canStartRecording(info({ processing: true, pendingProcessing: 1 })), true);
  assert.equal(canStartRecording(info({ recording: true })), false);
});

test('processing note counts earlier meetings while recording', () => {
  assert.equal(processingNote(info({ recording: true, pendingProcessing: 0 })), null);
  assert.equal(processingNote(info({ recording: true, pendingProcessing: 1 })), 'Processing 1 earlier meeting');
  assert.equal(processingNote(info({ recording: true, pendingProcessing: 3 })), 'Processing 3 earlier meetings');
  assert.equal(processingNote(info({ processing: true, pendingProcessing: 1 })), null);
});
```

- [ ] **Step 2: Run them to verify they fail**

  Run: `npm --prefix frontend test`

  Expected: FAIL (the functions are not exported).

- [ ] **Step 3: Implement the helpers** (`recordingState.ts`)

```ts
/** A saved meeting takes the open recording notes only if they were typed for it. */
export function shouldHandOverNotes(
  recordingView: boolean, savedStartedAt: string | null | undefined, recordingStartedAt: string | null,
): boolean {
  if (!recordingView) return false;
  if (!savedStartedAt || !recordingStartedAt) return true;
  return Date.parse(savedStartedAt) === Date.parse(recordingStartedAt);
}

/** Earlier meetings may still be processing; only a live recording blocks a new one. */
export function canStartRecording(info: RecordingInfo): boolean {
  return !info.recording;
}

export function processingNote(info: RecordingInfo): string | null {
  const n = info.pendingProcessing ?? 0;
  if (!info.recording || n < 1) return null;
  return n === 1 ? 'Processing 1 earlier meeting' : `Processing ${n} earlier meetings`;
}
```

  Add `pendingProcessing?: number;` to `RecordingInfo` in `frontend/src/mock/meetings.ts`.

- [ ] **Step 4: Wire up `App.tsx`**

  - `onMeetingSaved(id: string, savedStartedAt?: string | null)`. Its first line becomes:

  ```ts
  if (!shouldHandOverNotes(recordingViewRef.current, savedStartedAt, recordingStartedAtRef.current)) {
    if (embedded) void refreshList(filterParams(filterRef.current), true);
    return; // another recording's notes are open, or save_meeting already adopted them
  }
  ```

  - In its `.catch`, when `err instanceof Error && err.message === 'draft_mismatch'`,
    refresh the list and **do not** call `open()`. Use a flag checked in
    `.finally`.
  - The listener reads `startedAt` from the payload:
    `const p = payload as { id?: string; startedAt?: string | null } | null; if (p?.id) onMeetingSavedRef.current(p.id, p.startedAt);`
  - Render the recording notes as
    `<RecordingNotes key={recordingStartedAt ?? ''} … />`, so a new
    recording remounts the editor. An unmounted editor's late save keeps its
    own `startedAtRef`, and the server routes it to the parked row.
  - Check `NotesEditor`: if it drops a pending debounced save on unmount
    (no flush in its effect cleanup), add a cleanup that calls the flush
    function it exposes through `flushRef`. This keeps the last ≤500 ms of
    typing.

- [ ] **Step 5: Wire up `TodayView.tsx`**

  - The header shows Start Meeting when `canStartRecording(recording)` (was `!recordingActive`).
  - Pass `recording.recording` as `splitAgenda`'s third argument, and show
    the agenda sections when `!recording.recording`. The calendar rows' Record
    buttons then work during processing.
  - The hero block is unchanged. Inside it, under the title, render
    `processingNote(recording)` when it is non-null, in a `<div className={styles.heroNote}>`.
  - Add `.heroNote` to `TodayView.module.css`, copying the existing
    secondary-text rule (tokens only, e.g. `color: var(--text-lo)`).
  - Keep the processing-only hero (`Processing · title`) as it is.

- [ ] **Step 6: Add a mock state.** In `App.tsx`'s mock setup (around `:165`),
  add `'today-recording-processing'`:
  `{ recording: true, processing: false, pendingProcessing: 1, … }`.
  This lets the dev preview show both.

- [ ] **Step 7: Run tests and build**

  Run: `npm --prefix frontend test && npm --prefix frontend run build`

  Expected: PASS, and the build ends with the offline check passing.

- [ ] **Step 8: Commit**

  ```bash
  git add frontend
  git commit -m "Meetings page: start during processing, notes hand-over only to their own meeting"
  ```

**Reviewer mutations:**
- `shouldHandOverNotes` returns `recordingView`: a test must fail.
- `canStartRecording` returns `!(recording || processing)`: a test must fail.

The reviewer also previews the mock state in the dev server (memory
"Worktree session guards") and screenshots it.

---

### Task 7: Menu, pill, record banner, quit

**Files:**
- Modify: `speakeasy/ui/menu_model.py`: `menu_flags(state, pending=0)`, `start_enabled`, `processing_suffix`, `quit_warning`.
- Modify: `speakeasy/ui/menubar.py`:
  - `_sync_meeting_items` (`:336–346`);
  - `engineStateChanged_` (`:295–333`);
  - `meetingProgress_` (`:348`);
  - `menuWillOpen_` (`:481`);
  - `quitApp_` (`:574`).
- Modify: `speakeasy/ui/pill_model.py`: `PillMachine.on_saved`, `expire_saved`; `pill_view(..., pending=1)`.
- Modify: `speakeasy/ui/pill_controller.py`: `savedExpired_`, `_render`.
- Modify: `speakeasy/record_prompt.py`: `engine_state`, `calendar_tick`, `call_observed`.
- Modify: `speakeasy/ui/record_prompt_controller.py`: `_PROBED_STATES` (`:23`), `_engine_ready` (`:165`).
- Test: `tests/test_menu_model.py`, `tests/test_pill_model.py`, `tests/test_pill_controller.py`, `tests/test_record_prompt.py`, `tests/test_record_prompt_controller.py`

**Interfaces:**
- Consumes: `engine.pending_meeting_jobs`, `engine._recording_active`.
- Produces:

```python
def menu_flags(state: str, pending: int = 0) -> dict      # cancel = pending > 0
def start_enabled(state: str) -> bool                     # ready or meeting_processing
def processing_suffix(pending: int) -> str                # "" / " · processing 1 meeting" / " · processing N meetings"
def quit_warning(recording: bool, pending: int) -> str | None
```

- [ ] **Step 1: Write the failing pure tests**

  Add to `tests/test_menu_model.py`. Update the existing parametrized
  `meeting_processing` case to call `menu_flags(state, 1)` for that row (keep
  `cancel=True`). Add a `meeting_recording` row with `pending=1` expecting
  `cancel=True`.

```python
from speakeasy.ui.menu_model import processing_suffix, quit_warning, start_enabled


def test_start_enabled_during_processing():
    assert start_enabled("ready") and start_enabled("meeting_processing")
    assert not start_enabled("meeting_recording") and not start_enabled("loading")


def test_processing_suffix():
    assert processing_suffix(0) == ""
    assert processing_suffix(1) == " · processing 1 meeting"
    assert processing_suffix(2) == " · processing 2 meetings"


def test_quit_warning():
    assert quit_warning(False, 0) is None
    assert quit_warning(False, 1) == "1 meeting is still processing. Quitting discards it."
    assert quit_warning(False, 2) == "2 meetings are still processing. Quitting discards them."
    assert quit_warning(True, 0) == "The current recording will be discarded."
    assert quit_warning(True, 1) == "The current recording and 1 processing meeting will be discarded."
    assert quit_warning(True, 3) == "The current recording and 3 processing meetings will be discarded."
```

  Add to `tests/test_pill_model.py` (use the file's `m(...)` helper):

```python
def test_saved_is_ignored_while_recording():
    machine = m(("on_state", "meeting_recording", None), ("on_saved", "m-a"))
    assert machine.phase == "recording" and machine.saved_id is None


def test_saved_expiry_returns_to_processing_when_more_queued():
    machine = m(("on_state", "meeting_processing", None), ("on_saved", "m-a"))
    machine.expire_saved(still_processing=True)
    assert machine.phase == "processing"
    machine.on_saved("m-b")
    machine.expire_saved(still_processing=False)
    assert machine.phase == "hidden"


def test_confirm_detail_mentions_queued_meetings():
    machine = m(("on_state", "meeting_processing", None), ("cancel",))
    view = pill_view(machine, title=None, elapsed=0, health=None, capture_mode="", progress=None, pending=3)
    assert (view.title, view.detail) == ("Discard meeting?", "2 more will still be saved")
    view1 = pill_view(machine, title=None, elapsed=0, health=None, capture_mode="", progress=None, pending=1)
    assert view1.detail == ""
```

  `PillView` fields are `phase, title, detail, indicator, buttons` (`pill_model.py:81`).

  Add to `tests/test_record_prompt.py`:

```python
def test_calendar_offer_shows_while_previous_meeting_processes():
    c = coord("meeting_processing")
    c.calendar_tick([ev("next", start=at(1))], at(0), offer_enabled=True)
    assert isinstance(c.banner, rp.Offer)


def test_processing_does_not_close_an_open_offer():
    c = coord("ready")
    c.calendar_tick([ev("next", start=at(1))], at(0), offer_enabled=True)
    c.engine_state("meeting_processing")
    assert isinstance(c.banner, rp.Offer)
    c.engine_state("meeting_recording")
    assert c.banner is None
```

  Change `tests/test_record_prompt_controller.py:167–168`: after
  `engineStateChanged_("meeting_processing")`, assert
  `c.probe_wanted is True`, because calls are now offered while processing.

- [ ] **Step 2: Run them to verify they fail**

  Run: `.venv/bin/python -m pytest tests/test_menu_model.py tests/test_pill_model.py tests/test_record_prompt.py tests/test_record_prompt_controller.py -q`

- [ ] **Step 3: Implement the pure modules**

  `menu_model.py`:

```python
def menu_flags(state: str, pending: int = 0) -> dict:
    return {"next_up": state == "ready",
            "meeting_notes": state in ("meeting_recording", "meeting_processing"),
            "retry_mic": state == "mic_failed",
            "cancel": pending > 0}


def start_enabled(state: str) -> bool:
    # Earlier meetings may still be processing; only a recording blocks a new one.
    return state in ("ready", "meeting_processing")


def processing_suffix(pending: int) -> str:
    if pending < 1:
        return ""
    return " · processing 1 meeting" if pending == 1 else f" · processing {pending} meetings"


def quit_warning(recording: bool, pending: int) -> str | None:
    if recording:
        if pending < 1:
            return "The current recording will be discarded."
        noun = "processing meeting" if pending == 1 else "processing meetings"
        return f"The current recording and {pending} {noun} will be discarded."
    if pending < 1:
        return None
    if pending == 1:
        return "1 meeting is still processing. Quitting discards it."
    return f"{pending} meetings are still processing. Quitting discards them."
```

  `pill_model.py`:
  - `on_saved`: `if self.phase == "recording": return` as the first line.
    A queued job finishing must not take over the recording pill.
  - `expire_saved(self, still_processing=False)`: when the phase is
    `"saved"`, set `self.phase = "processing" if still_processing else "hidden"`.
    Keep any other existing branches.
  - `pill_view(..., progress, pending=1)`: the `confirm_discard` entry becomes
    `("Discard meeting?", f"{pending - 1} more will still be saved" if pending > 1 else "")`.

  `record_prompt.py`:
  - `engine_state`: change the offer-closing condition from
    `state in (RECORDING, PROCESSING)` to `state == RECORDING`.
  - `calendar_tick` and `call_observed`: replace both `self._state != READY`
    checks with `self._state not in (READY, PROCESSING)`.

- [ ] **Step 4: Implement the AppKit glue**

  `record_prompt_controller.py`:
  - `_PROBED_STATES = {"ready", "meeting_recording", "meeting_processing"}`.
  - `_engine_ready` returns `self.engine.state.value in ("ready", "meeting_processing")`,
    and its comment becomes "begin_meeting is refused while recording".

  `pill_controller.py`:
  - `savedExpired_` calls
    `self.machine.expire_saved(still_processing=self.engine.state.value == "meeting_processing")`.
  - `_render` passes `pending=int(getattr(self.engine, "pending_meeting_jobs", 1) or 1)` to `pill_view`.

  `menubar.py`:
  - `_sync_meeting_items`: in the else branch, use `setEnabled_(start_enabled(state.value))`.
  - `engineStateChanged_`: for `MEETING_RECORDING`, use
    `_meeting_status_text(self.engine.meeting_recorder) + processing_suffix(self.engine.pending_meeting_jobs)`.
    Apply the same suffix wherever the recording clock tick re-titles the
    status line (find the timer that updates `End Meeting (clock)` around
    `:285–292`, and the status line next to it).
  - `menu_flags(...)` calls at `:333` and `:481` pass `self.engine.pending_meeting_jobs`.
  - `meetingProgress_`: `if self.engine.state is State.MEETING_RECORDING: return`.
    A queued job's progress must not replace the recording status.
  - `quitApp_`:

```python
def quitApp_(self, sender):
    warning = quit_warning(self.engine.state is State.MEETING_RECORDING,
                           self.engine.pending_meeting_jobs)
    if warning is not None:
        from AppKit import NSAlert, NSAlertFirstButtonReturn
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Quit and discard unsaved meetings?")
        alert.setInformativeText_(warning)
        alert.addButtonWithTitle_("Quit")
        alert.addButtonWithTitle_("Cancel")
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if alert.runModal() != NSAlertFirstButtonReturn:
            return
    print("\nShutting down.")
    self.engine.shutdown()
    NSApplication.sharedApplication().terminate_(None)
```

  Check that Cmd+Q and the main-menu Quit (`menubar.py:770`) both route
  through `quitApp_`. If either calls `terminate_` directly, route it through
  `quitApp_`.

- [ ] **Step 5: Add a pill-controller test** (`tests/test_pill_controller.py`, using its `setup`/`go` helpers)

```python
def test_progress_during_recording_is_not_status_text(setup):
    go(setup, "meeting_recording")
    setup.controller.meetingSaved_("m-a")
    assert setup.controller.machine.phase == "recording"
```

  Name the test after the pill behaviour if the fake setup cannot reach the
  menubar. The menubar's `meetingProgress_` guard is checked by the Task 8
  reviewer during the live eval.

- [ ] **Step 6: Run the UI suites**

  Run: `.venv/bin/python -m pytest tests/test_menu_model.py tests/test_menubar_menu.py tests/test_main_menu.py tests/test_pill_model.py tests/test_pill_controller.py tests/test_pill_screens.py tests/test_record_prompt.py tests/test_record_prompt_controller.py -q`

  Expected: PASS.

- [ ] **Step 7: Commit**

  ```bash
  git add speakeasy/ui speakeasy/record_prompt.py tests
  git commit -m "UI: start, banner and calls during processing; pill and menu keep the live recording; quit confirm"
  ```

**Reviewer mutations:**
- Remove the `on_saved` recording guard: `test_saved_is_ignored_while_recording` must fail.
- Restore `PROCESSING` in the offer-closing condition: `test_processing_does_not_close_an_open_offer` must fail.
- `start_enabled` returns only `ready`: a test must fail.

---

### Task 8: Docs, whole-branch review and live eval (Opus)

- [ ] **Step 1 (Sonnet): update AGENTS.md.** Edit the `worker` bullet in
  "Threading model" with these points:
  - Processing is a FIFO of `MeetingJob`s.
  - A new recording may start while jobs are pending.
  - Live mic pre-transcription is skipped when the worker is busy.
  - Dictation resumes only when no recording is active and no job is pending.
  - State and resume are decided under `_meeting_lock` in `_publish_meeting_state`.

  Edit the "hotkey tap thread" bullet the same way: "rebuilt when the last
  pending job finishes". Add a sentence on `parked_notes` to the "Meeting
  notes (notepad)" bullet: schema v8, parked at stop, adopted by exact start.
  Then commit.
- [ ] **Step 2 (Opus): run the full suite** with `timeout: 600000`:
  `.venv/bin/python -m pytest -q` and `npm --prefix frontend test`. Compare
  the counts with the Task 0 baseline.
- [ ] **Step 3 (Opus): whole-branch review** with
  superpowers:requesting-code-review against the spec and the Review Focus
  list. Mutation-verify every Review Focus item: break the code, confirm a
  test fails, restore.
- [ ] **Step 4 (Opus): build and install.** Run
  `npm --prefix frontend run build` then `scripts/build_app.sh --install`.
  This stops Desktop's Speakeasy MCP connector (memory "Install kills MCP
  connector"); tell the user to toggle it.
- [ ] **Step 5 (Opus + user): live overlap eval.** It runs from Terminal.app
  (memory "Live capture checks"); a Claude-session capture gets silent
  system audio.
  1. Play audio with speech, record meeting A for **at least 10 min**, then stop.
  2. Immediately start meeting B and talk for **at least 5 min** while A
     processes. Confirm the menu shows "Recording … · processing 1 meeting"
     and the pill stays on recording when A saves.
  3. Stop B.

  Pass criteria:
  - Both meetings appear with their own titles and notes.
  - B's capture health (pill drawer or saved meeting) shows **0 dropped
    frames** and no writer lag/failure.
  - The two newest rows of `Speakeasy-meeting-latency.jsonl` show
    `concurrent_recording` true for A and false for B.
  - The dictation hotkey works after both are saved.

  Record the numbers in Progress.
- [ ] **Step 6 (controller):** update this plan's Progress, then use
  superpowers:finishing-a-development-branch. "Merge and clean up" follows
  the user's CLAUDE.md definition.

---

## Progress

- [x] Task 0 — worktree `.worktrees/back-to-back-meetings` (branch `back-to-back-meetings`); baseline 1501 Python / 72 frontend passed
- [x] Task 1 — spool names (5cc789f..31659ed)
- [x] Task 2 — MeetingJob (31659ed..3422f9c)
- [x] Task 3 — overlap (3422f9c..089c58b; + 783193d `silent_sounds` test fixture)
- [x] Task 4 — parked notes v8, incl. unpark on no-save/shutdown (783193d..55fe382)
- [x] Task 5 — bridge payloads (55fe382..bf98e9a)
- [x] Task 6 — frontend (bf98e9a..d44ce59); mock state previewed dark + light
- [x] Task 7 — menu/pill/banner/quit (d44ce59..42e755b)
- [ ] Task 8 — Step 1 AGENTS.md (7870f4d) done; Step 2 full suite 1567 passed / frontend 75 done; Step 3 whole-branch review + fix wave (3406cf8) done, re-review clean; **Step 4 build/install and Step 5 live overlap eval with the user: NOT DONE**; Step 6 finish branch: not done.

### Controller rulings made during execution (full list in the final session message)

- `concurrent_recording` is true for every job pending (queued or running) when a recording begins.
- `_end_meeting` keeps `_recording_active` True through the recorder stop and clears it in the same locked block that appends the job.
- Parked notes of a job that saves nothing (cancel/failure) or never runs (shutdown) are unparked back into `note_draft` when that slot is empty; park/unpark catch `Exception` (best effort, logged as `notes_park_failed` / `notes_unpark_failed`).
- `pending_meeting_jobs` is a lock-free read for main-thread UI.
- `meeting_processing_error` is kept until it has been published in an idle state (refines R3).
- The pill returns from "Cancelling…" to processing when the pending count drops after a discard.
- Tests: autouse `silent_sounds` fixture in `tests/conftest.py`. Looped engine tests spawned hundreds of `afplay` processes and hung the Mac twice on 10 Oct.

### Live-eval checklist (Task 8 Step 5, in addition to the plan's pass criteria)

1. Menu status while B records with A queued reads "<recording text> · processing 1 meeting" and is never replaced by A's percentage. When A saves during B, the pill stays on recording and the hotkey stays off.
2. Pill → Notes within ~1 s of starting B while A processes: the notes land on B.
3. With B's notepad open, A saves: the page stays on B, and A has only its own notes.
4. "Cancel Processing" while recording B discards A only.
5. Pill Discard with 2 jobs shows "1 more will still be saved", and the pill returns to processing for the next job.
6. Calendar or call banner during processing: Record starts B.
7. Quit alert copy for: recording only; recording + 1 job; processing only. Cancel keeps the app running.
8. `Speakeasy-meeting-latency.jsonl`: `concurrent_recording` is true for A and false for B. B's capture health shows 0 dropped frames.

### TODO / open items

- Follow-up (separate spec): make diarization faster. It is 80 % of
  processing time, so this would shorten the queue but is not needed for
  this feature.
- Dictation during background processing stays off by decision D2. Revisit
  only if the user asks.
- Ask the user: should Dock Quit and logout also show the quit alert (`applicationShouldTerminate_`)? Today they bypass it, which is pre-existing, but with a queue they can discard more.
- Ask the user: should "Cancel Processing" be retitled, or confirm, while a recording is active? It cancels the earlier meeting, not the recording.
- Parked notes can get stuck when the draft slot is occupied, and after a crash (no launch-time reaper). The rows are kept in the DB and backups; they are not lost.
- Pre-existing flaky test `test_gap_fill_queue_full_is_retried_on_the_next_block` (test swaps the queue while the writer runs). Spun off as a separate task.
- Deferred minors from per-task reviews: see the session's final message. All were triaged "OK to defer" by the whole-branch review.
