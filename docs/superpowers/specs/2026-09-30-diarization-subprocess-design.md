# Diarization in a per-meeting child process — design

Date: 30 September 2026. Status: approved in chat (option A, failure option 2).

## Problem

After the MLX cache cap, the installed app measured 1,601 MB idle but 2,314 MB after
20 dictations and one 3.2-minute mic-only meeting (docs/model-memory.md, "Installed
app"). GPU memory was fine (1,470 MB). The growth was CPU heap: "Malloc Large"
8.6 → 301 MB, "Malloc Small" 260 → 384 MB. Target was ≤ 2,000 MB after a meeting.

## Measurements (30 Sep 2026, synthetic audio, temp HOME)

Method: `footprint -p` inside a fresh process per experiment; synthetic `say`
meetings with four voices (Samantha, Daniel, Karen, Fred), 3.2 min and 29.3 min, 16 kHz.
The synthetic 3.2-min meeting diarizes in 19.0–20.4 s; the real 3.2-min meeting took
17.8 s, so the workload is comparable. Single runs unless repeats are listed.

### 1. Diarizer + embedder retention: plateau at the high-water mark, not a leak

Five meetings in one process (3.2, 3.2, 29.3, 3.2, 3.2 min), diarize + `tidy_speakers`:

| After meeting | Keep models (today) | Release each time | Release + `MallocLargeCache=0` | Child process (parent footprint) |
|---|---|---|---|---|
| baseline | 53 MB | 52 | 44 | 26 |
| 1 (3.2 min) | 302 | 302 | 54 | 26 |
| 2 (3.2 min) | 312 | 309 | 54 | 26 |
| 3 (29.3 min) | 804 | 356 | 59 | 25 |
| 4 (3.2 min) | 807 | 432 | 59 | 25 |
| 5 (3.2 min) | 723 | 438 | 59 | 25 |

- Diarizer alone: construct +89 MB, first 3.2-min diarize +145 MB, then flat at
  310 MB across five identical runs.
- Speaker-embedding extractor (`engine.speaker_embedder`, `VoiceProfileStore.embed`,
  used by `tidy_speakers` and voice identification): load + one 10 s embed +174 MB,
  flat at ~255 MB above baseline after that.
- With the large cache off and the models kept, 230 MB (3.2 min) and 438 MB (29.3 min)
  remain. That is live onnxruntime arena memory, sized by the longest meeting so far.

### 2. macOS keeps freed large blocks in the footprint

On this OS (Darwin 27), libmalloc's large-allocation cache keeps freed blocks dirty
and they count in `footprint`. Plain numpy, one process:

| | default | `MallocLargeCache=0` |
|---|---|---|
| allocate then free 12 / 107 / 400 MB | footprint stays +12 / +119 / +519 MB | back to 17 MB each time |

- `malloc_zone_pressure_relief(NULL, 0)` returned 0 MB every time.
- So deleting the Diarizer returned 0 MB (310 → 309 MB), and the float32 meeting
  track that `_process_meeting` "releases immediately" still counts (29.3 min =
  107 MB).
- `MallocLargeCache` is not in `man malloc`: an undocumented switch.

### 3. Other retained buffers after meeting ASR (`transcribe_long_wav`)

- CPU heap +18 MB Malloc Large, +2 MB Small after transcribing both tracks.
- GPU stays at the cap (1,445–1,484 MB).
- Speed with the large cache off was unchanged: 36 short `transcribe()` calls median
  108 vs 109 ms, 29.3-min transcription 35.2 vs 35.3 s.

### 4. Reload and process costs

| What | Time |
|---|---|
| `Diarizer()` construct | 151–205 ms cold, 69–78 ms warm |
| Embedding extractor load + 10 s embed | 144–206 ms |
| Spawn + import sherpa_onnx / diarizer | 195–208 ms |
| Whole child meeting overhead (spawn, import, pipe) vs work inside child | 0.67–0.69 s (3.2 min: 20.1–20.9 s wall; 29.3 min: 182.0 s wall) |

### 5. Arena settings

sherpa-onnx 1.13.4 calls `EnableCpuMemArena` internally. Its Python configs expose
only `model`, `num_threads`, `provider`, `debug`. Changing the arena needs a rebuilt
sherpa-onnx, which is ruled out (pinned dependency).

### Unexplained remainder

The installed app's "Malloc Small" rose 124 MB; the synthetic runs account for about
45 MB of it. It may come from the 20 dictations. Not measured; the installed-app check
below will show what remains.

## Options considered

- **A. Per-meeting child process (chosen).** The parent does not grow at all. Cost
  about 0.7 s per meeting.
- **B. Release models + `MallocLargeCache=0` via Info.plist `LSEnvironment`.**
  54–59 MB retained, no speed cost. Rejected: undocumented switch; applies only when
  LaunchServices launches the app; changes the allocator for the whole process.
- **C. Release only.** 0 MB saved after short meetings, 250–390 MB still retained.
  Rejected.
- **D. onnxruntime arena config.** Not exposed. Rejected.

## Design

### New module `speakeasy/diarization_process.py`

- `diarize_meeting_audio(audio, diarizer, embed, *, expected_count, max_speakers,
  voice_profile_names, progress, cancelled) -> list[DiarizationTurn]`
  - Pure logic, moved out of `Engine._diarize_track` unchanged.
  - Steps: `diarizer.diarize` → `speaker_merge.tidy_speakers` (only when
    `expected_count is None`) → `VoiceProfileStore.identify` (only when names are
    given).
- `run_child(conn, wav_path, expected_count, max_speakers, voice_profile_names)` is the
  child entry point:
  1. Import sherpa_onnx and the diarizer, then send `("ready",)`.
  2. Read the WAV with `read_wav_mono_f32`.
  3. Build `Diarizer(expected_count)` and a `VoiceProfileStore`, then call
     `diarize_meeting_audio`.
  4. Send progress as `("progress", fraction)`.
  5. Finish with `("turns", [...])`, or on an exception
     `("error", type(error).__name__)`. Never send message text (privacy).
  - Each turn is sent as a tuple of every `DiarizationTurn` field (`start, end,
    speaker, confidence, overlap, profile_id`) and rebuilt exactly in the parent.
    `profile_id` carries identified voice names, so it must survive the round trip.
- `ChildDiarizationRunner`, the parent side:
  - `__call__(wav_path, *, expected_count, max_speakers, voice_profile_names, progress,
    cancel: threading.Event) -> list[DiarizationTurn]`
  - Uses `multiprocessing.get_context("spawn")`, a `Pipe`, `daemon=True`, and
    `name="speakeasy-diarization"`. This is the same pattern as `MicrophoneHelper`,
    which already runs in the installed app.
  - Polls the pipe every 0.1 s.
  - Launch budget: `config.DIARIZATION_CHILD_LAUNCH_TIMEOUT_SECONDS = 20.0` to reach
    `ready`. There is no overall time limit, because a 2-hour meeting takes about
    12 minutes.
  - Cancel: `terminate()`, then `join(2.0)`, then `kill()`, then raise
    `MeetingCancelled`. That makes cancel work during diarization; today it doesn't.
  - Any failure raises `DiarizationFailed(reason)`, where reason is one of
    `launch_timeout`, `launch_failed`, `child_exited`, or `child_error:<TypeName>`.
  - The child is always reaped in a `finally`.

### Engine changes (`speakeasy/engine.py`)

- Remove `self.diarizer`, `self.speaker_embedder`, `self._diarizer_speaker_count` and
  `_speaker_embedding`. Add `self.diarization_runner = ChildDiarizationRunner()`,
  injectable for tests.
- `_diarize_track(path, progress, timing, *, max_speakers)` takes the track path, not an
  audio array. The parent no longer calls `_read_meeting_track` for the diarization
  track, in either the dual-track or the mic-only branch.
  - The existing "no readable audio" check uses the frame counts already computed
    in `_process_meeting`.
  - A track that disappears before the child reads it becomes a child `error`.
- Timing: `MeetingTiming` marks events on the parent's clock, and both stage names
  stay.
  - `diarization` starts just before the child is launched, so it now includes the
    ~0.7 s spawn and load.
  - The child sends `("phase", "voice_identification")` immediately before the
    identify step, always, as today's code marks that stage even with no names. On
    receiving it, the parent calls `timing.finish("diarization")` and
    `timing.start("voice_identification")`.
  - On `turns`, `error` or cancel, the parent finishes whichever stage is open. This
    matches today's `finally` semantics.

### Failure fallback (behaviour change, chosen option 2)

On `DiarizationFailed`, the meeting is still saved:

- Transcribed sentences are aligned with an empty turn list, so every system/mic-only
  sentence gets a single label ("Speaker 1"). The mic track in dual mode stays "You".
- `capture_health` records `diarization_status: "failed"` and
  `diarization_failure: <reason>`. Both keys are added to `_CAPTURE_HEALTH_KEYS` in
  `meetings.py`; values are privacy-safe codes.
- On success, `diarization_status: "ok"`.
- `MeetingCancelled` is not a failure; it keeps today's cancel behaviour.
- Other exceptions (ASR, save) keep today's `processing_failed` behaviour.

### `--mcp` import rule

`diarization_process.py` imports sherpa_onnx only inside the child function, and
nothing on the `--mcp` path imports the module.
`test_mcp_mode_imports_nothing_heavy` must still pass.

## Testing

- **Unit tests for `diarize_meeting_audio`** with fakes: calls tidy only without a
  count, calls identify only with names, keeps the manual count exact.
- **Existing engine meeting tests** switch from `engine.diarizer = Fake…` to an
  in-process test runner wrapping the same fakes. Their assertions stay unchanged.
- **New engine tests:**
  - `DiarizationFailed` saves the transcript with one remote label and the two
    `capture_health` fields.
  - Cancel raised from the runner saves nothing and cleans the spools.
  - The diarization track is not read into the parent.
- **Runner tests with a real spawned child**, using a stub target that needs no
  sherpa-onnx:
  - ready → turns
  - progress forwarding
  - launch timeout, with a child that never sends ready
  - child exits without a result
  - child error sends only the type name
  - cancel terminates within 2.5 s
- **Suite:** full suite with an explicit Bash timeout above its runtime.
- **Mutation check** by the reviewer.

## Acceptance (real, checkable numbers)

1. `scripts/measure_diarization_memory.py` is committed. It uses synthetic `say` audio
   with a temp HOME asserted before importing speakeasy, and reuses the probe method
   above. Over five meetings (3.2, 3.2, 29.3, 3.2, 3.2 min):
   - parent footprint grows ≤ 5 MB per meeting (measured 0–1 MB);
   - child overhead is ≤ 1.5 s per meeting (measured 0.68 s);
   - child turns equal in-process turns for both synthetic meetings.
2. Installed app: repeat the 30 Sep sequence, 20 dictations plus one mic-only meeting.
   - Footprint after the meeting must be ≤ 2,000 MB (was 2,314 MB). Record Malloc Large
     and Small too.
   - One cancel during "Identifying speakers" must stop within about 1 s.
   - Dictation still inserts in TextEdit, Codex and Teams (AGENTS.md insertion rule).
3. Docs:
   - `docs/model-memory.md` gets the measurements and the large-cache fact.
   - AGENTS.md's threading-model paragraph now says diarization runs in a per-meeting
     child process launched from the worker job.
