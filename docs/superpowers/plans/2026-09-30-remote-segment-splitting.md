# Remote Segment Splitting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop remote (diarized) speech from merging into blocks of up to 60 s that are listed before the "You" replies made during them, so two-way meetings read in true order.

**Architecture:** `meetings.align_speakers` merges consecutive same-speaker pieces with no gap limit. Two independent rules are added to that merge: (A) a new segment starts after a pause longer than `config.DIARIZED_MAX_GAP_SECONDS` (1.5 s, the same rule the mic track uses), and (B) an optional `break_at` list of times (on the system track's own timeline) forces a new segment when a time falls between the current segment's start and the next piece's start. In the two-track path, `engine.py` passes the "You" segment starts, converted with the track offsets, as `break_at`.

**Tech Stack:** Python 3, pytest. No new dependencies; fully offline.

**Spec:** No separate spec. The design was agreed in chat on 2026-09-30 (option C = A + B) and is recorded in this section and under "Background". The evidence and cause are in `docs/superpowers/plans/2026-09-30-meeting-echo-removal.md`, in the sections headed "Speaker 1 merging".

## Background (why)

- On the system track, consecutive sentences with the same label and the same overlap flag merge until the segment would pass `DIARIZED_MAX_SEGMENT_SECONDS` (60 s). There is no gap limit (`speakeasy/meetings.py` ~380-387).
- `merge_tracks` sorts by start time, so a 60 s remote block is listed before every "You" reply made during it.
- Measured in the stored meetings: 3b95 (29 min) had 56 of 64 "You" segments starting inside a remote segment, with a median remote length of 57.0 s. c139 (18 min) had 15 of 29.
- In the synthetic live run (meeting 20260930-194946-6824), remote sentences 1-7 (2.68-61.00 s, about 5 s apart) became a single segment.
- Why both rules:
  - (A) covers every path that uses `align_speakers`: two-track, the mic-only fallback (`engine.py` ~1289) and `evaluate.py`. It handles normal turn-taking, where the remote gap is at least as long as the reply.
  - (B) catches quick turn-takes, where the remote pause holding a short "yes" is under 1.5 s.
- Known limit: talk-over in the middle of a sentence cannot be split at sentence level. The remote sentence that contains the interjection is still listed first. This is accepted.

## Global Constraints

- Fully offline; no new dependencies.
- New config: `DIARIZED_MAX_GAP_SECONDS = 1.5` in `speakeasy/config.py`, next to `DIARIZED_MAX_SEGMENT_SECONDS`.
- `DIARIZED_MAX_SEGMENT_SECONDS` (60.0), `KNOWN_SPEAKER_MAX_GAP_SECONDS` (1.5) and `KNOWN_SPEAKER_MAX_SEGMENT_SECONDS` (60.0) are unchanged.
- Echo-removal thresholds and `remove_mic_echo` are unchanged.
- Stored meetings are not migrated. There is no reprocess path, and the meeting audio is temporary.
- The `align_speakers` signature stays backward compatible. `break_at` is keyword-only and defaults to `None` (no breaks).
- `merge_tracks` is unchanged.
- No transcript text in logs, tests' printed output or the plan, matching the existing privacy rules in AGENTS.md.
- Tests:
  - Run with `.venv/bin/python -B -m pytest` and clear `__pycache__` before mutation runs (memory: mutation-review gotchas).
  - The full suite takes a few minutes, so give the Bash call `timeout: 600000`.
  - Tests are HOME-isolated; never run app code against the real `~/Library/Application Support` data.
- Work on a branch/worktree, never on master.

## Review Focus

1. **Offset sign in `break_at`.** "You" starts are on the shared timeline. `align_speakers` works in system-track time, so the break is `you.start - offsets["system"]`. A wrong sign silently disables (B). This is pinned by the Task 2 engine test, which only passes with the subtraction.
2. **A break exactly at a segment's start must not split.** If a "You" segment starts at the same instant as a remote segment, nothing can be reordered, so there must be no empty or pointless split. The lower bound is strict, and Task 1 has a test for it.
3. **A break inside the previous piece (talk-over) still splits before the next piece.** The You start falls in (segment.start, next piece start], so the rest of the remote speech is listed after the reply. Task 1 has a test for it.
4. **The gap rule must not split a sentence's own in-sentence speaker runs wrongly.** Runs from one sentence are contiguous, so their gap is about 0 and they never trigger (A). The existing `test_sustained_speaker_change_splits_sentence` and `test_split_sentence_preserves_subword_spacing` must still pass unchanged.
5. **Unsorted or empty `break_at`.** `[]` and `None` behave the same as before. Unsorted input is sorted inside the function. Task 1 has a test for it.

---

### Task 1: Gap split and `break_at` in `align_speakers`

**Files:**
- Modify: `speakeasy/config.py` (after `DIARIZED_MAX_SEGMENT_SECONDS`, ~line 152)
- Modify: `speakeasy/meetings.py:250` (`align_speakers` signature, docstring, merge condition ~380-387)
- Test: `tests/test_meeting_alignment.py`
- Modify (doc): `AGENTS.md` ~line 35 (the bullet on the known-source track)

**Interfaces:**
- Produces: `align_speakers(sentences, turns, *, break_at: Iterable[float] | None = None) -> list[MeetingSegment]`. `break_at` holds times in the **same timeline as `sentences`** (system-track time, before `shift_segments`).
- Produces: `config.DIARIZED_MAX_GAP_SECONDS = 1.5`.

- [x] **Step 1: Write the failing tests** (append to `tests/test_meeting_alignment.py`; it already defines `sentence(start, end, text)` and imports `align_speakers`)

```python
def test_same_speaker_splits_after_pause_longer_than_gap():
    # 1.4 s pause merges; 1.6 s pause starts a new segment (limit 1.5 s).
    sentences = [
        sentence(0.0, 2.0, "First."),
        sentence(3.4, 5.0, "Second."),
        sentence(6.6, 8.0, "Third."),
    ]
    segments = align_speakers(sentences, [(0.0, 8.0, 1)])
    assert [(s.speaker, s.start, s.end) for s in segments] == [
        ("Speaker 1", 0.0, 5.0),
        ("Speaker 1", 6.6, 8.0),
    ]


def test_break_at_splits_short_pause_where_local_speaker_starts():
    # 0.5 s pause would merge, but "You" started at 2.2 s inside it.
    sentences = [sentence(0.0, 2.0, "Question?"), sentence(2.5, 4.0, "Go on.")]
    segments = align_speakers(sentences, [(0.0, 4.0, 1)], break_at=[2.2])
    assert [(s.start, s.end) for s in segments] == [(0.0, 2.0), (2.5, 4.0)]


def test_break_at_inside_previous_sentence_splits_before_next():
    # Talk-over: "You" started at 1.0 s, mid-sentence. The current sentence
    # stays whole; the next one starts a new segment.
    sentences = [sentence(0.0, 2.0, "Long point."), sentence(2.3, 4.0, "More.")]
    segments = align_speakers(sentences, [(0.0, 4.0, 1)], break_at=[1.0])
    assert [(s.start, s.end) for s in segments] == [(0.0, 2.0), (2.3, 4.0)]


def test_break_at_equal_to_segment_start_does_not_split():
    sentences = [sentence(0.0, 2.0, "One."), sentence(2.3, 4.0, "Two.")]
    segments = align_speakers(sentences, [(0.0, 4.0, 1)], break_at=[0.0])
    assert [(s.start, s.end) for s in segments] == [(0.0, 4.0)]


def test_break_at_outside_segment_does_not_split():
    sentences = [sentence(5.0, 7.0, "One."), sentence(7.3, 9.0, "Two.")]
    segments = align_speakers(
        sentences, [(5.0, 9.0, 1)], break_at=[12.0, -0.4, 3.0]
    )
    assert [(s.start, s.end) for s in segments] == [(5.0, 9.0)]


def test_break_at_empty_or_unsorted_is_handled():
    sentences = [
        sentence(0.0, 1.0, "A."),
        sentence(1.2, 2.0, "B."),
        sentence(2.2, 3.0, "C."),
    ]
    turns = [(0.0, 3.0, 1)]
    assert len(align_speakers(sentences, turns, break_at=[])) == 1
    segments = align_speakers(sentences, turns, break_at=[2.1, 1.1])
    assert [(s.start, s.end) for s in segments] == [(0.0, 1.0), (1.2, 2.0), (2.2, 3.0)]
```

- [x] **Step 2: Update the one existing test that now conflicts.** In `test_token_in_distant_gap_inherits_previous_speaker`, the two sentences are 3 s apart (0-2 s and 5-6 s), so they are now two segments. The test is about attribution, not merging. Replace its two asserts with:

```python
    # Both attributed to Speaker 1; the 3 s pause now separates segments.
    assert [s.speaker for s in segments] == ["Speaker 1", "Speaker 1"]
```

- [x] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -B -m pytest tests/test_meeting_alignment.py -v`
Expected: the gap test FAILs (one merged segment), and the `break_at` tests FAIL with `TypeError: ... unexpected keyword argument 'break_at'`.

- [x] **Step 4: Add the config constant** to `speakeasy/config.py`, directly below `DIARIZED_MAX_SEGMENT_SECONDS = 60.0`:

```python
# Like the mic track, a remote pause longer than this starts a new segment.
# Without it, remote turns merged up to the 60 s cap and were listed before
# the "You" replies made during them (3b95: 56 of 64 replies).
DIARIZED_MAX_GAP_SECONDS = 1.5
```

- [x] **Step 5: Implement in `speakeasy/meetings.py`**

Signature (line 250):

```python
def align_speakers(sentences, turns, *, break_at=None) -> list[MeetingSegment]:
```

At the end of the docstring, replace the sentence "Consecutive same-speaker sentences merge into one segment, and speakers are numbered by first appearance." with:

```
    Consecutive same-speaker sentences merge into one segment unless the
    pause exceeds config.DIARIZED_MAX_GAP_SECONDS, the segment would exceed
    config.DIARIZED_MAX_SEGMENT_SECONDS, or a `break_at` time (same
    timeline as `sentences`; the two-track path passes "You" starts) falls
    after the segment's start and at or before the next piece's start.
    Speakers are numbered by first appearance.
```

Just before the `# Merge consecutive same-speaker sentences` loop, add:

```python
    breaks = sorted(break_at or ())
```

Replace the merge condition (currently ~380-387) with:

```python
        last = segments[-1] if segments else None
        if (
            last is not None
            and last.speaker == labels[speaker]
            and last.overlap == overlap
            and start - last.end <= config.DIARIZED_MAX_GAP_SECONDS
            and end - last.start <= config.DIARIZED_MAX_SEGMENT_SECONDS
            and bisect.bisect_right(breaks, start) == bisect.bisect_right(breaks, last.start)
        ):
            last.text = (last.text + " " + text).strip()
            last.end = end
        else:
```

(The `bisect` line means "no break b with last.start < b <= start".) Add `import bisect` to the imports at the top of `meetings.py` in alphabetical order.

- [x] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -B -m pytest tests/test_meeting_alignment.py tests/test_meetings.py tests/test_dual_track_meeting.py -v`
Expected: all PASS, including the unchanged `test_diarised_segments_split_at_sixty_seconds` (its gaps are 1 s).

- [x] **Step 7: Update AGENTS.md.** After the bullet that ends "otherwise a whole meeting would land as one segment timestamped 00:00:00.", add:

```markdown
- Diarized (remote) segments use the same 1.5 s pause rule
  (`config.DIARIZED_MAX_GAP_SECONDS`) under the 60 s cap, and in two-track
  meetings also split where a "You" segment starts (`align_speakers(...,
  break_at=...)`, times converted to system-track time with the track
  offsets) so replies are not listed after the remote speech they answered.
```

- [x] **Step 8: Commit**

```bash
git add speakeasy/config.py speakeasy/meetings.py tests/test_meeting_alignment.py AGENTS.md
git commit -m "Split diarized segments on 1.5 s pauses and at break_at times"
```

### Task 2: Pass "You" starts as `break_at` in the two-track path

**Files:**
- Modify: `speakeasy/engine.py` ~1233-1242 (the `local_segments` / `remote_segments` block)
- Test: `tests/test_engine_meeting.py`

**Interfaces:**
- Consumes: `meetings.align_speakers(sentences, turns, *, break_at=None)` from Task 1, which takes times in system-track time.
- Consumes: the existing `offsets` dict (`recording.track_offsets_seconds`, keys `"mic"` and `"system"`). Segments returned by `shift_segments` are on the shared timeline.

- [x] **Step 1: Write the failing test** (append to `tests/test_engine_meeting.py`; `Sentence`, `FakeDualMeetingRecorder`, `InProcessDiarizationRunner`, `MeetingOptions`, `MeetingLibrary`, `_engine` and `_wait_for` already exist there). The recorder fake sets offsets to mic 0.0 and system 0.5.

```python
class QuickReplyTranscriber:
    """One remote voice with a 0.4 s pause (under the 1.5 s gap rule) in
    which the local user replies. System track-local 0.0-0.8 and 1.2-2.0
    land at 0.5-1.3 and 1.7-2.5; the reply is at 1.35 on the mic (offset
    0), which is 0.85 in system time. Only the offset-converted break
    splits the remote speech; an unconverted 1.35 would miss (0.0, 1.2]."""

    def transcribe_long(self, audio, *, progress, cancel=None):
        progress(1.0)
        result = type("Result", (), {})()
        if float(np.mean(audio)) > 0.001:
            result.sentences = [Sentence(1.35, 1.6, "quick yes")]
        else:
            result.sentences = [
                Sentence(0.0, 0.8, "remote asks"),
                Sentence(1.2, 2.0, "remote continues"),
            ]
        return result


class OneRemoteVoiceDiarizer:
    def diarize(self, samples, progress=lambda f: None):
        progress(1.0)
        return [(0.0, 2.0, 4)]


def test_dual_track_splits_remote_speech_where_you_reply(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    engine.meeting_recorder = FakeDualMeetingRecorder(spool_dir)
    engine.transcriber = QuickReplyTranscriber()
    engine.diarization_runner = InProcessDiarizationRunner(OneRemoteVoiceDiarizer())
    saved = []
    engine.on_meeting_saved = saved.append

    engine.begin_meeting(MeetingOptions(expected_speaker_count=2))
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)

    stored = MeetingLibrary().get_meeting(saved[0])
    assert [(s.speaker, round(s.start, 2)) for s in stored.segments] == [
        ("Speaker 1", 0.5),
        ("You", 1.35),
        ("Speaker 1", 1.7),
    ]
    engine.shutdown()
```

- [x] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -B -m pytest tests/test_engine_meeting.py::test_dual_track_splits_remote_speech_where_you_reply -v`
Expected: FAIL. The stored order is `[("Speaker 1", 0.5), ("You", 1.35)]`, because the remote speech merged.

- [x] **Step 3: Implement in `speakeasy/engine.py`.** Replace the `remote_segments = ...` assignment (the `local_segments` assignment above it stays as it is) with:

```python
                    # "You" starts, in system-track time, end remote
                    # segments so replies are not listed after them.
                    system_offset = offsets.get("system", 0.0)
                    remote_segments = meetings.shift_segments(
                        meetings.align_speakers(
                            system_result.sentences,
                            turns,
                            break_at=[s.start - system_offset for s in local_segments],
                        ),
                        system_offset,
                    )
```

Do not change the mic-only fallback call (~1289) or `evaluate.py`. They get the gap rule from Task 1, and they have no separate "You" track.

- [x] **Step 4: Run the test to verify it passes, then run the full suite**

Run: `.venv/bin/python -B -m pytest tests/test_engine_meeting.py -v`
Expected: all PASS, including the existing dual-track and echo tests (their "You" starts fall outside any same-speaker merge).

Run (Bash `timeout: 600000`): `.venv/bin/python -B -m pytest -q`
Expected: all pass (810 + 7 new = 817).

- [x] **Step 5: Commit**

```bash
git add speakeasy/engine.py tests/test_engine_meeting.py
git commit -m "Break remote segments where a You segment starts in two-track meetings"
```

### Task 3: Mutation review, install and live check

Reviewer (opus): verify by mutation, then record the results below. Each mutation must make at least one test fail. Clear `__pycache__` and use `python -B` for every run.

- [x] **Step 1: Mutations**
  1. Remove `and start - last.end <= config.DIARIZED_MAX_GAP_SECONDS`. Expect `test_same_speaker_splits_after_pause_longer_than_gap` to fail.
  2. Set `DIARIZED_MAX_GAP_SECONDS` to 1.3. Expect the 1.4 s-merges half of the same test to fail.
  3. Replace the `bisect` condition with `True`. Expect the `break_at` tests and the engine test to fail.
  4. Use `bisect_left` for both calls (which makes the bound inclusive at `last.start`). Expect `test_break_at_equal_to_segment_start_does_not_split` to fail.
  5. Remove `sorted(...)` (`breaks = list(break_at or ())`). Expect `test_break_at_empty_or_unsorted_is_handled` to fail.
  6. In `engine.py`, drop `- system_offset`. Expect `test_dual_track_splits_remote_speech_where_you_reply` to fail.
  7. In `engine.py`, use `+ system_offset`. Expect the same engine test to fail.

- [x] **Step 2: Install.** Run `./build_app.sh --install` from the merged/branch commit. Afterwards, toggle the Claude Desktop Speakeasy Meetings connector off and on (memory: install kills MCP connector).

- [x] **Step 3: Live check (user, from Terminal.app).** Run `scripts/check_mic_echo_live.py` in this worktree, the same way as the 2026-09-30 run. Expected:
  - Remote sentences 1-8 come out as separate Speaker 1 segments, about 5 s apart, interleaved with own1, own2, the short reply, the repeat and the talk-over in start order.
  - The talk-over reply may still follow the remote sentence it interrupts (the known limit).
  - Echo results unchanged: 0 of 8 remote sentences duplicated as "You", 5 of 5 own utterances kept, lags about +0.05 s.

- [x] **Step 4: First real meeting after install.** Re-run the numbers-only sqlite probe used for 3b95/c139 (remote segment count, median and max length, and the number of "You" starts inside a remote segment). Expect "You starts inside a remote segment" to drop from most to only talk-over cases, and the median remote length to fall well below 57 s. Record the numbers here, without text.

## Results

Executed 2026-09-30, subagent-driven, on branch `claude/remote-segment-splitting`
(worktree `.claude/worktrees/remote-segment-splitting`).

- Task 1: 7c4df21. Uses the existing `from bisect import bisect_left, bisect_right`
  in `meetings.py` instead of adding `import bisect` (ruling: matches file idiom).
- Task 2: d04675c.
- Full suite: 824 passed (baseline was 817, not the 810 the plan assumed; +7 new).
- Task 3 Step 1 mutations (final opus review): all 7 caught. Extra: `<=` → `<` on
  the gap check survives (see TODO); break against `last.end`, gap measured from
  `end`, and `break_at=None` in the engine were all caught.
- Step 2: merged to master (9c0aa8c, 824 passed on the merge), pushed, installed
  with `scripts/build_app.sh --install` and relaunched (process running). Steps 3-4
  (live check from Terminal.app, first real meeting probe): not yet done.
- Step 3 live check (user, Terminal.app, 2026-10-01, meeting 20261001-002557-ea4c,
  temp HOME, installed build 9c0aa8c): **splitting passed; own speech 4 of 5.**
  - Segment order: all 8 remote sentences are separate Speaker 1 segments, about
    5 s apart (2.50, 12.58, 21.22, 30.82, 38.90, 48.02, 56.42, 65.22 s), interleaved
    in start order with own1 (8.88), own2 (26.96), "okay yeah" (36.88) and the
    repeat (44.80). Before this change remote 1-7 were one 2.8-61.3 s segment.
  - Echo: 8 candidates removed (8 of 12 mic sentences), coverage 0.92-1.00, lag
    +0.06 s (one +0.02 s). 0 of 8 remote sentences duplicated as "You".
  - **Talk-over LOST** (best You overlap 0.09). Expected 5 of 5. It is absent from
    the mic sentences entirely: 12 mic sentences = 8 echo + 4 own, and the
    remote-7 echo candidate has 13 words, the length of remote 7 alone, so the
    talk-over was not removed as part of it. This change only touches
    `diarized_segments`/`align_speakers` `break_at` on the system track, after
    mic transcription and echo removal, so it cannot drop a mic sentence. Likely
    cause: the ASR did not transcribe Samantha over Daniel's bleed in this run
    (the 2026-09-30 run kept it, with 16 mic sentences). Not proven; the audio
    was deleted with the temp HOME.
  - Capture clean: `mic_and_system`, `selected`, built-in speakers, no dropped
    frames, no writer lag, `diarization_status` ok. Offsets mic 0.0 s, system 0.103 s.
- Step 4 (2026-10-01, meeting 20261001-090143-7cbb "SSO follow up", 34.7 min, user + 3
  attendees, built-in speakers, no dropped frames, `diarization_status` ok,
  151 mic echo sentences removed). Probe, numbers only:

  | Meeting | Remote segs | Median | Max | ≥30 s | "You" starts inside remote |
  |---|---|---|---|---|---|
  | 3b95 (before) | 28 | 56.8 s | 222.8 s | 25 | 56 of 64 |
  | 7cbb (after) | 194 | 5.6 s | 58.7 s | 3 | 36 of 61 |

  - Splitting works: median 56.8 → 5.6 s, ≥30 s segments 25 → 3, max under the 60 s cap.
  - "Inside" is still 36 of 61, but 24 of the 36 start within 1.0 s of the remote
    segment's end (turn-boundary overlap); only 11 have the remote continuing >1 s.
    Splitting is between sentences, so a You start inside one remote sentence can't split.
  - **New problem, not this plan: residual echo in real meetings.** Classifying You
    segments with ≥4 content words by share of words also in remote speech within ±3 s:
    23 echo (≥0.8, 581 s), 8 mixed (0.4-0.8, 178 s), 12 own (<0.4, 192 s), plus 18 short.
    Control: the same measure against remote speech shifted ±120 s gives 0 of 43 at ≥0.6
    (mean 0.10-0.13) vs 30 of 43 unshifted (mean 0.65), so it is real duplication, not
    shared topic words. 22 of 23 echo segments start after 20 min (5-min bins:
    0,1,0,0,10,5,7). Trigram-estimated echo lag stays about 0 s (within ±2.4 s) all meeting,
    so not drift. 13 of the 36 "inside" cases are echo. One You segment is 105.8 s
    (single long ASR sentence over the 60 s cap, known limit). 5 remote speaker labels
    for 3 attendees (Speaker 3: 9 segs/17 s; Speaker 2: 29 segs/75 s). Audio not kept,
    so per-sentence echo decisions can't be recovered.
- Earlier note: no real meeting recorded since the install (23:56, 30 Sep);
  newest in the library is still 3b95 (re-checked 2026-10-01 by `created_at`).
  Probe written down below and re-validated on the old meetings: 3b95 28 / 56.8 s /
  222.8 s / 25 / 56 of 64; c139 37 / 6.2 s / 189.8 s / 6 / 15 of 29. These match
  the earlier table, except that the 3b95 median is 56.8 s here (the mean of the two
  middle values of 28) against 57.0 s recorded before.

  Probe (read-only, numbers only; columns: remote count, median, max, count ≥30 s,
  "You" starts strictly inside a remote segment):
  ```bash
  DB=~/Library/Application\ Support/Speakeasy/library.sqlite; M=<meeting id>
  sqlite3 -readonly "$DB" "
  with r as (select start_seconds s, end_seconds e, end_seconds-start_seconds d from segments where meeting_id='$M' and speaker!='You'),
  y as (select start_seconds s from segments where meeting_id='$M' and speaker='You'),
  o as (select d, row_number() over (order by d) rn, count(*) over () n from r)
  select (select count(*) from r), (select round(avg(d),1) from o where rn in ((n+1)/2,(n+2)/2)),
   (select round(max(d),1) from r), (select count(*) from r where d>=30),
   (select count(*) from y where exists (select 1 from r where y.s>r.s and y.s<r.e)) || ' of ' || (select count(*) from y);"
  ```

### TODO / deferred
- ~~Re-run the live check to see whether the talk-over loss repeats~~ Done 2026-10-01
  (meeting 20261001-102054-cacc, `--keep`): **all passed.** 8 remote sentences as
  separate Speaker 1 segments in start order with all 5 own utterances; talk-over
  SURVIVED (overlap 1.00, listed at 58.48 s after remote 7 at 55.56 s); 0 of 8
  duplicates; 8 of 13 mic sentences removed, coverage 0.92-1.00, lag +0.04 s; capture
  clean, system offset 0.116 s. The earlier loss did not repeat (one-off ASR miss,
  not reproduced). Synthetic echo removal works; the real-meeting residual echo
  (7cbb) is therefore specific to real calls (several voices, longer sentences).
- **Residual echo in real meetings (7cbb, see Step 4):** new systematic-debugging
  session under the echo-removal plan. Echo late in the meeting survived although
  aligned in time; check coverage vs `MEETING_ECHO_MIN_COVERAGE` 0.8, the
  ±0.25/0.75 s lead/lag and 1.0 s window against long mic sentences. Needs a meeting's
  audio kept, e.g. a live check with `--keep DIR` (fa550cd) or a debug retention option.
  - **Debug session 2026-10-01, Phase 1 (stored segments only, numbers only):**
    - Lag measured precisely where a You segment begins with the same 4 words as a
      remote segment within ±8 s (start-to-start; tokens are 0.08 s apart): +0.04 s at
      526 s; then −0.20 to −0.32 s for all 17 matches from 1310 s to 2053 s (values
      −0.20/−0.28 alternate, one −0.32). Those segments have 0.71-1.00 word coverage.
      **The mic copy arrives 0.2-0.3 s before the system copy, so echo fails the
      0.25 s lead limit, not the coverage test.** This explains why echo stops being
      removed partway through (22 of 23 echo segments after 20 min).
    - Shape: looks like a step (flat −0.2..−0.3 from 1310 s to 2053 s) more than a
      steady ramp (150 ppm would add another −0.11 s across that range). Not proven,
      because there are no matches between 526 s and 1310 s.
    - Other meetings: d2cc (2026-10-01, 12 min, speakers) +0.03 s at 203 s and 715 s.
      Older meetings have too few matches to say anything.
    - Sign: mic early compared with system means the mic track lost samples or the
      system track gained samples after start. Track alignment is a single start
      offset (`MeetingRecording.track_offsets_seconds`). After that, both WAVs are
      treated as continuous.
    - Leading suspect (unverified): **the mic callback ignores PortAudio `status`**
      (`meeting_recorder.py` `_on_audio`). Input overflows / CoreAudio input glitches
      (for example Teams reconfiguring the mic) lose samples silently.
      `mic_dropped_frames` only counts writer-queue overflow, so health reported 0.
      The system helper also writes per callback and uses host time only for the
      first buffer (`SystemAudioCapture.swift` `write`), so a skipped or repeated
      IO cycle there would not be seen either. Its sign (lost system samples) would
      give mic-late, the opposite of what we measured.
    - Next (Phase 1 step 4, instrumentation, no fix): record per track the timeline
      error = (callback time − first callback time) − frames written / rate, its
      min/max and when the largest step happened, plus a mic `input_overflow` count,
      in `capture_health_json`. Run one real speaker-mode meeting and read the numbers.
      Do not widen `MEETING_ECHO_MAX_LEAD_SECONDS` before the cause is known: real
      timeline slips would also misplace "You" segments in the transcript order.
    - **Step 4 instrumentation done (branch `residual-echo-timeline-instrumentation`,
      2026-10-01; not merged; installed 2026-10-01 from 374623d, helper sha1 ab403fe8 matches the tested build).** `speakeasy/capture_timeline.py`
      computes error = (buffer time − first buffer time) − frames before / rate;
      positive = samples missing. Summaries use 30 s window minima (callback delay
      is only ever positive), so `series_ms` shows step vs ramp. New
      `capture_health_json` keys: `mic_input_overflows` (PortAudio
      `status.input_overflow` count), `mic_timeline` (PortAudio
      `inputBufferAdcTime`), `mic_arrival_timeline` (callback monotonic time),
      `system_timeline` (tap packet `mHostTime`) and `system_sample_timeline`
      (tap device `mSampleTime`; moves without frames on a skipped IO cycle). The
      helper emits a `timeline` event every 5 s of tap input. Each summary has
      `error_ms` (last window), `min_ms`, `max_ms`, `step_ms`/`step_at_s` (largest
      window-to-window change), `max_jump_ms`/`max_jump_at_s` (largest
      buffer-to-buffer change; system: per 5 s sample), `max_jitter_ms`,
      `series_ms`. Mic ADC time and helper host time are both host-clock seconds.
    - Smoke checks (no meeting): helper 10 s run, host and sample-time error 0
      (5.002667 s elapsed vs 240128/48000 frames). Mic 5 s probe: ADC and arrival
      series all 0 ms, jitter ≤ 1 ms, 0 status flags. 836 tests pass. Mutations
      (sign flip, window max, overflow not counted, ADC not observed, sample time
      dropped, rate guard, whitelist entry) each fail a test. The whitelist
      mutation first exposed that `filter_capture_health` dropped dict values, so
      numbers-only dicts are now allowed for `*_timeline` keys.
    - **Reading the numbers after a real speaker-mode meeting:** expected mic-early
      ≈ `mic_timeline.error_ms − system_timeline.error_ms` (Phase 1 measured
      +200..+320 ms after 1310 s). Mic step with `mic_input_overflows` > 0 → input
      overflow; mic step with 0 overflows → loss PortAudio does not flag; flat mic
      and negative system step → system gained samples; both flat → the lead is
      not a capture-timeline slip (look at ASR token timing / alignment instead).
    - **Result, meeting 4717 (2026-10-01 12:01, 46 min, solo Teams meeting with
      mic on, a recorded stand-up recap played through built-in speakers, global
      capture):**
      - `mic_timeline`: one step of **+258 ms at stream position 435.5 s**
        (buffer-to-buffer); end error +229 ms. `mic_arrival_timeline` agrees
        (+256 ms at 435.4 s). `mic_input_overflows` = 0, `mic_dropped_frames` = 0.
      - Ramp besides the step: −5 ms over the first 420 s, 253 → 229 ms over the
        remaining 2280 s, about −11 ppm (≈ 40 ms per hour). Small next to the step.
      - `system_timeline` and `system_sample_timeline`: 0 ms throughout (552
        samples).
      - Echo lag (You segment starting with the same 4 words as a remote segment
        within ±8 s): −0.13, −0.21, −0.21 s at 1007, 2103, 2750 s, all after the
        step. Same sign and size as the mic step. No matches before it.
      - macOS log (`/usr/bin/log show`, "skipping cycle"): exactly one line during
        the meeting, `HALC_ProxyIOContext::IOWorkLoop: skipping cycle due to
        overload` in the Speakeasy process at 12:09:03.310 = 436 s after start.
        The system helper is a separate process, so this is the mic IO context.
      - Same log for earlier meetings: 7cbb had two, at 1065 s and 1314 s after
        start (Phase 1 placed its step between 526 s and 1310 s). d2cc (+0.03 s, no
        lag) had none.
    - **Root cause of the echo lead (established):** Core Audio skips the mic IO
      cycle when Speakeasy's in-process IO callback overruns. That input is lost,
      PortAudio does not flag it (0 overflows), and the spool WAV stays
      continuous, so every later mic sample sits ~0.25 s early on the shared
      timeline. Echo then fails the 0.25 s lead limit.
    - **Why the callback overruns: hypothesis, not proven.** sounddevice's callback
      needs the GIL, so any Python thread holding it can stall the HAL IO thread.
      2 of the 3 overloads fall on mic ASR chunk boundaries (120 s + n × 105 s:
      435 s, n = 3; 1065 s, n = 9); 1314 s does not. Earlier chunk handoffs in the
      same meetings did not overload.
    - Fix options (not chosen; decide in a new plan):
      1. Timeline repair: when consecutive mic ADC times jump by more than the
         block length plus a tolerance, write the missing duration as silence to
         the spool (and the ASR chunker). The audio is already lost, but the track
         stays on the shared timeline whatever caused the overload.
      2. Remove the GIL from the realtime path: move meeting mic capture
         out of the Python callback (for example into the helper process, as
         dictation already does). Bigger change; prevents the loss itself.
      Keep the timeline instrumentation either way, to verify.
    - **Option 1 chosen 2026-10-01; design approved (gap threshold 20 ms).**
      Branch `residual-echo-timeline-instrumentation`. Status: **implemented
      (91df833 + review fixes eee81b7), 852 tests pass, installed 2026-10-01 from
      eee81b7; awaiting a real meeting.** Opus mutation review: 18 mutants, 15
      caught; the 2 that mattered (take_recording health site, exact threshold)
      now have tests. Review fixes: fill clamped to the spool cap, zeros written
      in ≤ 1 s pieces, ADC must be finite.
      - Known, accepted: a writer-queue drop of a block is now also gap-filled, so
        the same loss shows in both `mic_dropped_frames` and `mic_gap_fill_ms`.
      - Open follow-up: a very large ADC jump (for example a clock break over
        sleep/wake mid-meeting) is filled up to the spool cap. If the system track
        paused too, that would misalign mic against system. Consider a maximum
        plausible gap (fill only below it; count larger ones separately) once a
        real case is seen.
      - Detection in `MeetingRecorder._on_audio`, ADC clock only:
        `gap = (adc − first_adc) − frames_written / SAMPLE_RATE`, where
        frames_written includes inserted silence. If `gap ≥ 0.020` s, queue
        `round(gap × SAMPLE_RATE)` (an `int`) ahead of the block. Negative gaps
        never act. No ADC time (None / non-number / ≤ 0) → no repair. Arrival
        time is never used (a late-but-complete callback would look like a gap).
      - Queue full when queuing the fill → nothing counted; the gap is retried on
        the next block because frames_written did not move. The block itself
        follows the existing drop path.
      - Writer (`_drain`): an `int` item → write that many zero int16 frames to
        the WAV and pass the same zeros to `pretranscription.add_pcm`, so ASR
        chunk positions stay aligned. The callback never allocates the zeros.
      - Inserted frames count toward `_frames` (elapsed time and spool cap) and
        toward the timeline frames, so `mic_timeline` measures the repaired track
        (should stay within ±20 ms).
      - Health: `mic_gap_fills` (int count) and `mic_gap_fill_ms` (int total) in
        `MeetingCaptureHealth`, all three construction sites, `to_dict`, and the
        `meetings.py` whitelist.
      - Tests first: 258 ms ADC jump → 4128 zero frames before the next block (WAV
        length and content); ASR chunk start frames include them; jump < 20 ms,
        negative jump, missing ADC → nothing inserted; queue full → retried next
        block; health fields reach `capture_health_json`. Mutation review.
      - Out of scope: dictation recorder, system track, echo lead limit (stays
        0.25 s), cause of the overload (option 2).
      - Then: rebuild + install, one solo Teams speaker-mode meeting of 25+ min;
        expect `mic_gap_fill_ms` > 0 when an overload is logged, `mic_timeline`
        within ±20 ms, echo lag ≈ 0.
- Remote diarization over-splits: 5 labels for 3 attendees in 7cbb.
- Optional: a test pinning that an exactly-1.5 s remote pause merges (same `<=`
  rule as the mic track); currently unpinned.
- Optional: engine test with a nonzero mic offset (low risk: local segments are
  already on the shared timeline).
- `evaluate.py` attributed WER now counts a "Speaker N" label per extra split, so
  attributed-WER numbers from before this change are not comparable. Plain WER is
  unaffected.
