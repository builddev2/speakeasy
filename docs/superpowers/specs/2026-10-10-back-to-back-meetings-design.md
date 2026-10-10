# Back-to-back meetings: background processing — design

**Date:** 10 October 2026 · **Status:** draft for review

## Problem

With back-to-back meetings, the user cannot start recording the next meeting
while Speakeasy is still processing the previous one. The start of the next
meeting goes unrecorded.

### Why it happens today

- `DictationEngine._begin_meeting` returns silently unless
  `state is State.READY` and `_meeting_active` is false (`engine.py:911`).
  `_meeting_active` is only cleared in the `finally` of `_process_meeting`.
- Every surface follows the same rule:
  - the menu's Start Meeting item (`menubar.py:346`, enabled only in READY);
  - the record banner (`record_prompt.py`: `calendar_tick` and
    `call_observed` need `READY`; `record_prompt_controller._engine_ready`);
  - the Meetings page "Recording now" row.
- Processing reads per-meeting values stored on the engine itself:
  `_meeting_cancel`, `_meeting_options` (speaker count, voice profiles),
  `meeting_event`, `_meeting_started_at` and `meeting_processing_error`. A
  second recording would overwrite them while the first is still being
  processed.

### How long the block lasts (measured)

From `~/Library/Logs/Speakeasy-meeting-latency.jsonl`, the last 10 meetings
(all `mic_and_system`):

| Meeting length | Stop → saved | System ASR | Diarization |
|---|---|---|---|
| 9.9 min | 53 s | 10 s | 41 s |
| 31.6 min | 162 s | 32 s | 128 s |
| 46.4 min | 219 s | 49 s | 169 s |
| 61.8 min | 354 s | 69 s | 283 s |
| 74.5 min | 503 s | 84 s | 417 s |

- Processing takes about **13–17 % of meeting length**, and roughly **80 %**
  of that is diarization (spawned child process).
- Mic ASR is already ~0 s, because it is done live during the meeting.
- After an hour-long meeting, the first **6–8 minutes** of the next meeting
  are lost.

## Goal and success criteria

1. The user can start a new recording (menu, pill flow, banner, Today view)
   **at any time no recording is active**, including while earlier meetings
   are still processing.
2. Every stopped meeting is still processed and saved exactly as today, in
   stop order:
   - same transcript;
   - same diarization;
   - same audio deletion when processing ends.
3. Notes typed during a recording always end up on **that** meeting, never on
   a neighbour.
4. The new recording's capture quality is not degraded by concurrent
   processing. In a live overlap check, its capture health shows **0 dropped
   frames** and no writer lag/failure.
5. Nothing new is persisted. Audio still lives only in the spool until its own
   job ends, and is swept at the next launch after a crash.

Out of scope:

- Making diarization faster. That shortens the wait but does not remove it,
  so it is a separate follow-up.
- Dictation during background processing.

## Decisions

| # | Decision | Reason |
|---|---|---|
| D1 | Processing becomes a **FIFO queue of self-contained jobs** on the existing single `worker` thread | The worker already runs jobs one at a time in order, so the pipeline stays sequential and no new executor is needed. MLX stays pinned to one thread (AGENTS.md threading model). |
| D2 | **Dictation stays off** until no recording is active **and** no job is pending | Dictation needs the same worker, so it would wait minutes behind a job. This matches today's behaviour (the tap is off during processing). |
| D3 | A new recording **skips live mic pre-transcription** if any job is pending | The live job would wait behind processing, then overflow the two-chunk handoff and fall back to the spool anyway. Skipping it is predictable. Cost: the mic track is transcribed from the spool afterwards, which is about 1.3 min per hour of meeting (`system_asr` scale). |
| D4 | **Cancel** (menu "Cancel Processing", pill Discard) applies to the job that is currently running. Queued jobs are untouched | This keeps today's meaning of cancel. Queues longer than one are rare. |
| D5 | On stop, notes are **parked** under that recording's start time, and each job adopts its own parked notes | Fixes the cross-meeting notes bug (see Notes below). |
| D6 | **Quit with pending jobs asks first**: "N meetings are still processing. Quitting discards them." Choices are Quit / Cancel | Quitting already discards the job being processed. A queue makes the loss bigger. |
| D7 | No cap on queue length | Each recording's spool is already capped. Two one-hour tracks are about 230 MB of temporary disk. |

## Design

### 1. Engine: job object and derived state (`engine.py`)

A new dataclass, `MeetingJob` (fields set once, never mutated), is created in `_end_meeting`. It
holds everything `_process_meeting` reads:

- `recording`
- `timing`
- `pretranscription` (Future or None)
- `options: MeetingOptions`
- `event`: the calendar event held at stop
- `started_at`
- `cancel`: a fresh `threading.Event` for each job

`_process_meeting(job)` and the helpers it calls (`_saved_meeting_event`,
`_diarize_track`, `_diarize_or_fallback`, `_transcribe_meeting_track`) take
the job, or its fields, as parameters. They must not read `self.meeting_event`,
`self._meeting_options` or `self._meeting_cancel`.

Engine bookkeeping replaces `_meeting_active`:

- `_recording_active: bool`: the recording side, owned by the `control`
  thread.
- `_jobs: collections.deque[MeetingJob]`: pending and running jobs, guarded
  by a `threading.Lock`. A job is appended on `control` in `_end_meeting` and
  removed on `worker` in `_process_meeting`'s `finally`. The head is the job
  currently running.

The state is derived in one place, which every transition calls:

```
_meeting_state() -> State:
    MEETING_RECORDING   if _recording_active
    MEETING_PROCESSING  elif _jobs
    _idle_state()       otherwise
```

Rules:

- `_begin_meeting` is allowed when the state is `READY` or `MEETING_PROCESSING`,
  `not _recording_active`, there is no library maintenance and no dictation
  stream.
- In `MEETING_PROCESSING` the model is already loaded.
- `_process_meeting`'s `finally` removes its job, then sets
  `_meeting_state()`. It resumes the hotkey listener **only if** there is no
  recording and no pending job (D2), and the user has not paused.
- `_end_meeting` sets `_recording_active = False`, appends the job and sets
  `MEETING_PROCESSING`. It does not resume the listener.
- `cancel_meeting_processing()` sets `cancel` on the head job (D4).
  `shutdown()` sets every job's `cancel`.
- `meeting_processing_error` stays a single "last job's error" field for the
  UI. It is cleared when a job starts, not when a recording begins.
- New read-only property `pending_meeting_jobs -> int`, used by the UI.
- `meeting_event` and `_meeting_started_at` remain the **current recording's**
  values for the UI (pill title, notepad).
- Per-job timing records gain one privacy-safe field,
  `concurrent_recording: bool`: true if a recording was active at any point
  while the job ran. This lets the logs show overlap.

Existing invariants that must hold:

- Spool deletion and `release_spool` stay in each job's `finally`.
- The cancel-check points stay the same.
- The failed-diarization fallback stays the same.
- If quit happens with queued jobs, `worker.shutdown(wait=False)` never runs
  them. Their spools are removed by the `atexit` hook (still listed in
  `_active_spools`) and, failing that, by the launch sweep.

### 2. Live pre-transcription (D3)

`_begin_meeting` only configures the `MeetingASRSession` and submits
`transcribe_meeting_stream` when `_jobs` is empty. Otherwise the recording
runs without it, and its job transcribes the mic spool with
`transcribe_long_wav`. This path already exists for overflow.

### 3. Notes: parked drafts (D5)

**Bug today if overlap were simply allowed.** The note draft is one row (`note_draft`, id 1). Two things go wrong:

- **A's notes are lost.** B's notepad loads A's leftover draft and autosaves
  over it with B's `started_at`. `save_meeting(A)` then rejects it because it
  falls outside A's window.
- **B's notes land on A.** When A is saved while B's notepad is open, the
  page's `onMeetingSaved` (`App.tsx:304`) calls `notes.draft.finish(A, <B's
  notes>)` and navigates away from B.

Fix:

- **Schema v8.** It appends a new migration and never edits a shipped one.
  It adds `parked_notes(started_at TEXT PRIMARY KEY, markdown TEXT, stamps_json
  TEXT, parked_at TEXT)`.
- **`MeetingLibrary.park_draft(started_at)`.** It runs in one transaction:
  if `note_draft.started_at` equals `started_at` (normalised UTC ISO), the
  row moves to `parked_notes`.
- **The engine calls it in `_end_meeting`**, after the job is created, with
  the recording's `started_at`. A failure is logged as
  `notes_park_failed` (no content) and never blocks processing.
- **`set_draft(markdown, stamps, started_at)`** writes to the
  `parked_notes` row instead if one exists for that `started_at`. A late
  autosave (fewer than 500 ms of keystrokes after stop) therefore follows its
  meeting instead of creating a stray draft.
- **`save_meeting(new)` adopts in this order:**
  1. a parked row whose `started_at` equals `new.started_at` (exact match);
  2. otherwise, today's window rule against `note_draft`. This fallback is
     needed for imports and tests.
  The adopted row is deleted.
- **`finish_draft(meeting_id, …, started_at)`** first checks that
  `started_at` matches the meeting's `started_at`. On a mismatch it raises
  `DraftMismatch`, and the bridge returns a safe error code. The parked row
  is deleted once it is written.
- **`meetings.saved` bridge payload** gains `startedAt`. The page's
  `onMeetingSaved` only hands notes over when it matches
  `recordingStartedAtRef`. Otherwise it only refreshes the list and stays on
  the current recording.
- **Search.** Parked notes are not searchable until saved, the same as drafts
  today.
- **Backup/export.** `parked_notes` is transient like `note_draft`. Backup
  coverage tests must say so explicitly, in the same way they treat
  `note_draft`.

### 4. UI surfaces

| Surface | Change |
|---|---|
| `menu_model.menu_flags` | Takes `pending` (int). `cancel` becomes `pending > 0`. `meeting_notes` is unchanged. |
| Menu Start Meeting (`menubar.py:340-346`) | Enabled when the state is `READY` or `MEETING_PROCESSING`. |
| Menu status line | While recording with jobs pending, the usual recording text plus " · processing 1 meeting" (or "N meetings"). The processing-only state keeps today's progress text. |
| `PillMachine` | `on_saved` is ignored while the phase is `recording`. A job finishing during a recording must not replace the recording pill. When a recording stops while another job runs, the pill shows `processing` as today. The progress text is for the running job. |
| Pill Discard | Unchanged call. By D4 it cancels the running job. When that job is not the one just stopped, the confirm text reads "Discard the meeting being processed?" |
| Record banner (`record_prompt.py`) | `calendar_tick` and `call_observed` treat `PROCESSING` like `READY` for offers. `engine_state(PROCESSING)` no longer closes an open Offer; only `RECORDING` does. |
| Record banner controller | `_engine_ready` returns true for `ready` and `meeting_processing`. `_PROBED_STATES` adds `meeting_processing`. |
| `meetings_window.recording_info` | Adds `pendingProcessing: int`. `processing` stays `state == meeting_processing`. |
| Frontend | `RecordingInfo` gains `pendingProcessing`. The Today view and the recording row show "Processing N earlier meeting(s)" next to the live recording. Mock states are added for recording+processing. |
| Quit (`menubar.quitApp_`) | If `pending_meeting_jobs > 0` or a recording is active, a native `NSAlert` appears with Quit / Cancel (D6). The existing quit path is otherwise unchanged. |

### 5. Things that do not change

- Pipeline order, diarization child, echo removal and alignment.
- The capture-health whitelist, MCP tools and their descriptions.
- Calendar privacy rules, and "record prompts never record" (every begin still
  follows a click).
- The wake/mic-recovery exclusion of meeting states (`engine.py:182`).

## Risks and how we check them

| Risk | Check |
|---|---|
| Diarization child (CPU-heavy) starves the new recording's writer or realtime callback | **Live overlap eval** (Opus-run, from Terminal.app per memory "Live capture checks"). Record a meeting for at least 10 min with system audio playing, stop it, then start a second recording immediately and run it for at least 5 min while job 1 diarizes. Pass: job 2's capture health shows 0 dropped frames and no writer lag/failure, both meetings save, and both timing records show the correct `concurrent_recording`. Run in temp HOME per the memory notes. |
| Shared-field leak between jobs | Engine unit tests run two overlapping meetings with different options, events and speaker counts, and assert each saved meeting has its own. A **mutation** that reads `self._meeting_options` in `_diarize_track` must make a test fail. |
| Hotkey resumes mid-recording | Test: job A finishes while B is recording, and the listener is not resumed. |
| Notes cross-over | Library tests for park/adopt/late-autosave/finish mismatch, plus a frontend helper test for the `onMeetingSaved` match. |
| Cancel hits the wrong job | Test: cancel while A runs and B is queued. A is discarded and B is saved. |
| Quit loses queued audio on disk | Test: shutdown with queued jobs. Spools are in `_active_spools`, so the `atexit` cleanup removes them. |

## Delivery (user requirement)

- **Opus:** writes the implementation plan, solves hard problems (blockers,
  repeated failures, concurrency design questions), and runs all evals:
  - per-task review verified by **mutation**;
  - the final whole-branch review;
  - the live overlap eval.
- **Sonnet:** writes the code, as task implementers given exact briefs.
- Work happens on a branch or worktree, never on `master`. "Done" means the
  installed app was built, launched and the overlap scenario looked at, not
  only tests passing.
