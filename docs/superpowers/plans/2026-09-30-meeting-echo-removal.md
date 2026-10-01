# Meeting Mic-Echo Removal + Output-Route Health Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In dual-track meetings, stop the far end's speech from being transcribed twice when it plays through the Mac speakers and bleeds into the mic. Also record which kind of output device was in use, so future cases can be diagnosed.

**Architecture:** A pure function in `meetings.py` compares each mic ASR sentence's timed words with the system track's timed words on the shared meeting timeline. A mic sentence is dropped only when (a) ≥80% of its words match system words in order and (b) the median time lag between the matched words is consistent with acoustic bleed (−0.25 s…+0.75 s). The engine applies this before `known_speaker_segments`, prints content-free calibration lines, and stores a removal count in capture health. Separately, the Swift system-audio helper reports the default output device's *category* (never its name) at start and at stop. That category flows through `SystemTrackResult` into `MeetingCaptureHealth`.

**Tech Stack:** Python 3 (stdlib `difflib`, `bisect`, `statistics`), pytest, Swift/CoreAudio (`native/SystemAudioCapture.swift`).

**Spec:** This plan's Background section. The design was agreed in chat on 2026-09-30: the user approved items 1 (per-sentence removal) and 2 (output route); existing meetings are left unchanged.

## Background (diagnosis, 2026-09-30)

Read-only analysis of `library.sqlite` compared mic ("You") and system segments by time (±3 s) and word-bigram overlap:

| Meeting | Length | Mic words that are system echo |
|---|---|---|
| 20260928-111034-213a (test) | 73 s | 74% |
| 20260928-115150-c139 | 1056 s | 20% |
| 20260929-223110-3b95 | 1761 s | 27%: 13 echo segments, 7 mixed, 43 genuinely the user's own speech |

- The cause is speaker bleed. `merge_tracks` deliberately keeps duplicates, and only search de-duplicates (`meeting_library.collapse_echoes`).
- Removal must be per **sentence**, before segments are built. Stored mic segments merge up to 60 s of sentences, and 7 of 20 echo-bearing segments in 3b95 also contain the user's own words.
- Capture health records no output device, so headphone use could not be checked. In 3b95 the echo stops for the last ~35 segments. That is consistent with a switch to headphones mid-meeting, but this is not proven.
- Not in scope, noted only: 3b95 also had `mic_dropped_frames=738300`, `system_dropped_frames=185344` with both writers lagging.
- Existing meetings are **not** rewritten. Their audio is gone and only coarse segments remain, so cleaning them would lose the user's own words.

## Global Constraints

- Fully offline; no new dependencies (stdlib only in Python, CoreAudio/Foundation only in Swift).
- Capture health stays privacy-safe: no transcript text, audio, app or device **names**, PIDs. Only fixed category strings, bools and ints. New keys must be added to `meetings._CAPTURE_HEALTH_KEYS`.
- MCP tools never return capture fields; no MCP change.
- Log lines (stdout → `~/Library/Logs/Speakeasy.log` in the installed app) contain numbers only, never words from the transcript.
- Tests never touch the real App Support folder (autouse `isolated_home` fixture in `tests/conftest.py`). Subagents must never run app code against the real library (memory: subagents-real-data-sandbox).
- Mutation checks: run with `.venv/bin/python -B` and clear `__pycache__` first (memory: mutation-review-gotchas).
- Run tests with `.venv/bin/python -m pytest`. The full suite takes a few minutes: give the Bash call `timeout: 600000`.
- Work on branch `claude/vibrant-goldstine-4d33ff` (this worktree). Never on `master`.
- Initial thresholds (config): `MEETING_ECHO_MIN_COVERAGE = 0.8`, `MEETING_ECHO_MAX_LEAD_SECONDS = 0.25`, `MEETING_ECHO_MAX_LAG_SECONDS = 0.75`, `MEETING_ECHO_WINDOW_SECONDS = 1.0`. Task 5 may change them only on calibration evidence.
- Output-route categories (exact strings): `built_in_speakers`, `built_in_headphones`, `bluetooth`, `usb`, `display`, `airplay`, `virtual`, `other`, `unknown`.

## Review Focus

1. **A deliberate repeat is not echo.** If the user repeats the remote's phrase after them ("so forty hours"), the mic sentence must be kept. A repeat that starts after the remote finishes falls mostly outside the 1 s search window, so its coverage stays low. The lag gate covers a repeat that overlaps the end of the remote's sentence. Tests: `test_repeat_after_remote_is_kept`, `test_mic_leading_system_beyond_gate_is_kept` (Task 1).
2. **Talking over bleed.** A mic sentence that mixes the user's words with echo must be kept (coverage < 0.8). Test: `test_mixed_sentence_is_kept` (Task 1).
3. **Track offsets.** Both tracks must be compared on the shifted timeline, or a ~0.25 s first-buffer offset turns real echo into "lead" and stops removal. Test: `test_offsets_are_applied_before_comparing` (Task 1); engine test uses offset 0.5 s (Task 2).
4. **Real token shape.** parakeet tokens mark a word start with a leading space; sub-word tokens without one join the previous word. A tokenless sentence must not crash. Tests: `test_subword_tokens_join_into_words`, `test_sentence_without_tokens_uses_text` (Task 1).
5. **Unknown or garbage route values from the helper** must become `"unknown"`, never pass an arbitrary string (which could be a device name) into health. Test: `test_output_route_outside_known_set_is_unknown` (Task 3).

---

### Task 1: Echo detector (pure logic)

**Files:**
- Modify: `speakeasy/config.py` (after `KNOWN_SPEAKER_MAX_SEGMENT_SECONDS`, ~line 198)
- Modify: `speakeasy/meetings.py` (imports at top; new code after `known_speaker_segments`, ~line 427)
- Create: `tests/test_meeting_echo.py`

**Interfaces:**
- Produces: `meetings.EchoCandidate` (frozen dataclass: `words: int`, `coverage: float`, `lag_seconds: float`, `removed: bool`).
- Produces: `meetings.remove_mic_echo(mic_sentences, system_sentences, *, mic_offset: float = 0.0, system_offset: float = 0.0) -> tuple[list, list[EchoCandidate]]`. Returns the kept mic sentence objects (the same objects, in order) and one candidate per mic sentence that had at least one matched word.

- [ ] **Step 1: Write the failing tests** in `tests/test_meeting_echo.py`:

```python
"""remove_mic_echo: drop mic sentences that are system audio replayed
through the speakers, keep everything the user actually said.

Sentences are duck-typed like parakeet_mlx AlignedSentence: .start/.end/
.text/.tokens, where a token whose text starts with a space begins a word.
"""

from dataclasses import dataclass, field

from speakeasy import config
from speakeasy.meetings import remove_mic_echo


@dataclass
class Token:
    start: float
    end: float
    text: str

    @property
    def duration(self):
        return self.end - self.start


@dataclass
class Sentence:
    start: float
    end: float
    text: str
    tokens: list = field(default_factory=list)


def sentence(start, end, text):
    words = text.split()
    step = (end - start) / max(len(words), 1)
    tokens = [
        Token(start + i * step, start + (i + 1) * step, w if i == 0 else f" {w}")
        for i, w in enumerate(words)
    ]
    return Sentence(start, end, text, tokens)


REMOTE = "we should ship the estimate by friday afternoon"


def test_bleed_copy_of_system_sentence_is_removed():
    system = [sentence(10.0, 13.0, REMOTE)]
    mic = [sentence(10.08, 13.08, REMOTE)]
    kept, candidates = remove_mic_echo(mic, system)
    assert kept == []
    assert len(candidates) == 1 and candidates[0].removed
    assert candidates[0].coverage == 1.0
    assert abs(candidates[0].lag_seconds - 0.08) < 1e-6


def test_imperfect_echo_transcription_still_removed():
    # Mic ASR of bleed is quieter/reverberant: one word differs out of eight.
    system = [sentence(10.0, 13.0, REMOTE)]
    mic = [sentence(10.1, 13.1, "we should ship the estimate by friday afternoons")]
    kept, candidates = remove_mic_echo(mic, system)
    assert kept == []
    assert candidates[0].coverage == 0.88


def test_own_speech_is_kept():
    system = [sentence(10.0, 13.0, REMOTE)]
    mine = sentence(14.0, 16.0, "sounds good I will draft it tonight")
    kept, candidates = remove_mic_echo([mine], system)
    assert kept == [mine]
    assert candidates == []


def test_repeat_after_remote_is_kept():
    # User repeats the remote's last words after they finish: same words,
    # but ~1.5 s later — a human repeat, not acoustic bleed.
    system = [sentence(10.0, 11.5, "forty hours total")]
    repeat = sentence(11.5, 13.0, "forty hours total")
    kept, candidates = remove_mic_echo([repeat], system)
    assert kept == [repeat]
    assert all(not c.removed for c in candidates)


def test_mixed_sentence_is_kept():
    # User talks over the bleed: half the mic words are theirs.
    system = [sentence(10.0, 13.0, REMOTE)]
    mixed = sentence(10.05, 13.05, "we should ship hang on let me check my calendar")
    kept, candidates = remove_mic_echo([mixed], system)
    assert kept == [mixed]
    assert candidates and not candidates[0].removed
    assert candidates[0].coverage < config.MEETING_ECHO_MIN_COVERAGE


def test_offsets_are_applied_before_comparing():
    # Track-local times differ by the first-buffer offset; on the shared
    # timeline the mic copy trails the system speech by 0.05 s.
    system = [sentence(9.75, 12.75, REMOTE)]          # + 0.25 offset → 10.0
    mic = [sentence(10.05, 13.05, REMOTE)]            # + 0.0 offset
    kept, _ = remove_mic_echo(mic, system, mic_offset=0.0, system_offset=0.25)
    assert kept == []
    kept, candidates = remove_mic_echo(mic, system)   # offsets ignored → lag 0.30
    assert kept == []                                 # still within the gate
    far = [sentence(8.0, 11.0, REMOTE)]               # mic trails by 2.05 s
    kept, _ = remove_mic_echo(mic, far, system_offset=0.0)
    assert kept == mic


def test_mic_leading_system_beyond_gate_is_kept():
    system = [sentence(10.5, 13.5, REMOTE)]
    mic = [sentence(10.0, 13.0, REMOTE)]              # mic 0.5 s *before* system
    kept, candidates = remove_mic_echo(mic, system)
    assert kept == mic
    assert candidates[0].lag_seconds == -0.5


def test_short_sentence_needs_every_word():
    system = [sentence(5.0, 5.6, "okay yeah")]
    assert remove_mic_echo([sentence(5.05, 5.65, "okay yeah")], system)[0] == []
    partial = sentence(5.05, 5.65, "okay sure")
    assert remove_mic_echo([partial], system)[0] == [partial]


def test_subword_tokens_join_into_words():
    def subword(start, pieces):
        tokens = [Token(start + i * 0.1, start + (i + 1) * 0.1, p)
                  for i, p in enumerate(pieces)]
        return Sentence(start, start + 0.1 * len(pieces), "".join(pieces), tokens)
    system = [subword(3.0, ["est", "im", "ate", " by", " fri", "day"])]
    mic = [subword(3.05, ["est", "im", "ate", " by", " fri", "day"])]
    kept, candidates = remove_mic_echo(mic, system)
    assert kept == []
    assert candidates[0].words == 3


def test_sentence_without_tokens_uses_text():
    system = [Sentence(4.0, 5.0, "ship it friday")]
    mic = [Sentence(4.1, 5.1, "ship it friday")]
    kept, _ = remove_mic_echo(mic, system)
    assert kept == []


def test_empty_inputs():
    mine = sentence(0.0, 1.0, "hello there")
    assert remove_mic_echo([], [sentence(0.0, 1.0, "x y")]) == ([], [])
    assert remove_mic_echo([mine], []) == ([mine], [])
    assert remove_mic_echo(None, None) == ([], [])


def test_thresholds_read_from_config_at_call_time(monkeypatch):
    system = [sentence(10.0, 13.0, REMOTE)]
    mic = [sentence(10.08, 13.08, REMOTE)]
    monkeypatch.setattr(config, "MEETING_ECHO_MAX_LAG_SECONDS", 0.05)
    assert remove_mic_echo(mic, system)[0] == mic
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_meeting_echo.py -q`
Expected: collection error `ImportError: cannot import name 'remove_mic_echo'`.

- [ ] **Step 3: Add config constants** to `speakeasy/config.py` directly after `KNOWN_SPEAKER_MAX_SEGMENT_SECONDS = 60.0`:

```python

# Speaker bleed: with system capture active, far-end audio played through the
# Mac speakers is also heard by the mic and was transcribed twice. A mic
# sentence is dropped as echo only when most of its words match system-track
# words in order AND those words arrive at about the same moment — acoustic
# bleed lags by tens of ms, while a person repeating a phrase speaks after the
# other side finishes. Calibrated on a speaker-mode test recording (plan
# 2026-09-30-meeting-echo-removal, Task 5).
MEETING_ECHO_MIN_COVERAGE = 0.8      # share of mic words matched in order
MEETING_ECHO_MAX_LEAD_SECONDS = 0.25  # mic word may precede system word by
MEETING_ECHO_MAX_LAG_SECONDS = 0.75   # mic word may trail system word by
MEETING_ECHO_WINDOW_SECONDS = 1.0     # system words searched around a sentence
```

- [ ] **Step 4: Implement** in `speakeasy/meetings.py`. Change the imports to:

```python
import json
import re
import statistics
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, replace
from datetime import datetime
from difflib import SequenceMatcher
```

and add after `known_speaker_segments`:

```python
_ECHO_WORD_RE = re.compile(r"[a-z0-9']+")


@dataclass(frozen=True)
class EchoCandidate:
    """Content-free match statistics for one mic sentence (for calibration
    logs); never carries transcript text."""
    words: int
    coverage: float
    lag_seconds: float
    removed: bool


def _timed_words(sentence, offset: float) -> list[tuple[str, float]]:
    """Normalized (word, start) pairs on the shared meeting timeline.

    parakeet tokens are sub-word pieces; a piece whose text starts with a
    space begins a new word."""
    tokens = getattr(sentence, "tokens", None) or []
    if not tokens:
        start = float(sentence.start) + offset
        return [(w, start) for w in _ECHO_WORD_RE.findall(sentence.text.lower())]
    pieces: list[list] = []
    for token in tokens:
        if not pieces or token.text[:1].isspace():
            pieces.append([token.text, float(token.start) + offset])
        else:
            pieces[-1][0] += token.text
    return [
        (w, start)
        for text, start in pieces
        for w in _ECHO_WORD_RE.findall(text.lower())
    ]


def remove_mic_echo(
    mic_sentences, system_sentences, *, mic_offset: float = 0.0,
    system_offset: float = 0.0,
) -> tuple[list, list[EchoCandidate]]:
    """Drop mic sentences that are the system track replayed through the
    speakers (speaker-mode bleed), keeping everything else in order.

    A sentence is echo only if >= MEETING_ECHO_MIN_COVERAGE of its words
    match nearby system words in order and the median mic-minus-system lag
    of those matches lies within [-MAX_LEAD, +MAX_LAG]. Text alone cannot
    tell echo from a deliberate repeat; arrival time can.
    """
    system_words = sorted(
        (w for s in system_sentences or [] for w in _timed_words(s, system_offset)),
        key=lambda item: item[1],
    )
    starts = [t for _, t in system_words]
    window = config.MEETING_ECHO_WINDOW_SECONDS
    kept, candidates = [], []
    for sentence in mic_sentences or []:
        mic_words = _timed_words(sentence, mic_offset)
        if not mic_words or not system_words:
            kept.append(sentence)
            continue
        lo = bisect_left(starts, mic_words[0][1] - window)
        hi = bisect_right(starts, mic_words[-1][1] + window)
        near = system_words[lo:hi]
        matcher = SequenceMatcher(
            None, [w for w, _ in mic_words], [w for w, _ in near], autojunk=False
        )
        lags = [
            mic_words[block.a + k][1] - near[block.b + k][1]
            for block in matcher.get_matching_blocks()
            for k in range(block.size)
        ]
        if not lags:
            kept.append(sentence)
            continue
        coverage = len(lags) / len(mic_words)
        lag = statistics.median(lags)
        removed = (
            coverage >= config.MEETING_ECHO_MIN_COVERAGE
            and -config.MEETING_ECHO_MAX_LEAD_SECONDS
            <= lag
            <= config.MEETING_ECHO_MAX_LAG_SECONDS
        )
        candidates.append(
            EchoCandidate(len(mic_words), round(coverage, 2), round(lag, 2), removed)
        )
        if not removed:
            kept.append(sentence)
    return kept, candidates
```

- [ ] **Step 5: Run the tests.** Run: `.venv/bin/python -m pytest tests/test_meeting_echo.py -q`. Expected: all pass. If a test's arithmetic is off (e.g. rounding of `coverage`/`lag_seconds`), fix the **test's expected value** only after confirming by hand that the implementation matches the documented rule; do not loosen the rule.

- [ ] **Step 6: Commit**

```bash
git add speakeasy/config.py speakeasy/meetings.py tests/test_meeting_echo.py
git commit -m "Meetings: detect mic sentences that are speaker bleed of system audio"
```

---

### Task 2: Wire echo removal into dual-track processing

**Files:**
- Modify: `speakeasy/engine.py` (dual-track branch ~lines 1153–1213; `capture_health=` dict ~line 1276)
- Modify: `speakeasy/meetings.py` (`_CAPTURE_HEALTH_KEYS`, ~line 33)
- Test: `tests/test_engine_meeting.py`

**Interfaces:**
- Consumes: `meetings.remove_mic_echo`, `meetings.EchoCandidate` (Task 1).
- Produces: capture-health key `mic_echo_sentences_removed: int`, present only for `capture_mode == "mic_and_system"` meetings.

- [ ] **Step 1: Write the failing test.** Append to `tests/test_engine_meeting.py`, next to `test_dual_track_meeting_labels_you_and_diarizes_only_remote_track`:

```python
def _spaced_sentence(start, end, text):
    # Real parakeet tokens mark word starts with a leading space.
    sentence = Sentence(start, end, text)
    for i, token in enumerate(sentence.tokens):
        token.text = token.text if i == 0 else " " + token.text
    return sentence


class EchoDualTranscriber:
    """Mic track = one own sentence + a bleed copy of the remote sentence.
    The recorder fake puts system at offset 0.5 s, so the system sentence at
    track-local 0.0 lands at 0.5 on the meeting timeline; the mic copy
    starts at 0.55 (50 ms acoustic lag)."""

    def transcribe_long(self, audio, *, progress, cancel=None):
        progress(1.0)
        result = type("Result", (), {})()
        if float(np.mean(audio)) > 0.001:
            result.sentences = [
                _spaced_sentence(0.55, 1.35, "remote one is speaking now"),
                _spaced_sentence(3.0, 3.8, "local reply here"),
            ]
        else:
            result.sentences = [
                _spaced_sentence(0.0, 0.8, "remote one is speaking now"),
                _spaced_sentence(1.0, 1.8, "remote two"),
            ]
        return result


def test_dual_track_drops_mic_echo_of_system_speech(meetings_dir, spool_dir, capsys):
    engine = _engine(spool_dir)
    engine.meeting_recorder = FakeDualMeetingRecorder(spool_dir)
    engine.transcriber = EchoDualTranscriber()
    engine.diarization_runner = InProcessDiarizationRunner(FakeRemoteDiarizer())
    saved = []
    engine.on_meeting_saved = saved.append

    engine.begin_meeting(MeetingOptions(expected_speaker_count=2))
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)

    stored = MeetingLibrary().get_meeting(saved[0])
    you = [s for s in stored.segments if s.speaker == "You"]
    assert [s.text for s in you] == ["local reply here"]
    assert [s.speaker for s in stored.segments].count("You") == 1
    assert stored.capture_health["mic_echo_sentences_removed"] == 1
    out = capsys.readouterr().out
    assert "mic echo: removed 1 of 2 mic sentences" in out
    assert "remote one" not in out and "local reply" not in out
    engine.shutdown()
```

Also extend `test_dual_track_meeting_labels_you_and_diarizes_only_remote_track` with:

```python
    assert stored.capture_health["mic_echo_sentences_removed"] == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_engine_meeting.py -q -k "dual_track"`
Expected: the new test fails (the echo "You" segment is still present / KeyError on `mic_echo_sentences_removed`).

- [ ] **Step 3: Implement.** In `speakeasy/meetings.py`, add `"mic_echo_sentences_removed",` to `_CAPTURE_HEALTH_KEYS` (after `"diarization_failure",`).

In `speakeasy/engine.py`, before `if dual_track:` (~line 1153), add `echo_health: dict = {}`. Inside the dual-track `try:` of the alignment block, replace

```python
                    local_segments = meetings.shift_segments(
                        meetings.known_speaker_segments(mic_sentences),
                        offsets.get("mic", 0.0),
                    )
```

with

```python
                    # Speaker-mode bleed puts the far end on the mic too;
                    # drop those sentences before they become "You" segments.
                    mic_sentences, echo_candidates = meetings.remove_mic_echo(
                        mic_sentences,
                        system_result.sentences,
                        mic_offset=offsets.get("mic", 0.0),
                        system_offset=offsets.get("system", 0.0),
                    )
                    removed = sum(c.removed for c in echo_candidates)
                    for c in echo_candidates:
                        if c.coverage >= 0.3:
                            print(
                                f"  → echo candidate: words={c.words} "
                                f"coverage={c.coverage:.2f} lag={c.lag_seconds:+.2f}s "
                                f"{'removed' if c.removed else 'kept'}"
                            )
                    print(
                        f"  → mic echo: removed {removed} of "
                        f"{len(mic_sentences) + removed} mic sentences"
                    )
                    echo_health = {"mic_echo_sentences_removed": removed}
                    local_segments = meetings.shift_segments(
                        meetings.known_speaker_segments(mic_sentences),
                        offsets.get("mic", 0.0),
                    )
```

Note: `len(mic_sentences)` counts sentences with empty text too; that is fine for a log line.

In the `capture_health={...}` dict of `NewMeeting`, add `**echo_health,` after `**diarization_health,`.

- [ ] **Step 4: Run the tests.** Run: `.venv/bin/python -m pytest tests/test_engine_meeting.py tests/test_meeting_echo.py tests/test_dual_track_meeting.py -q`. Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add speakeasy/engine.py speakeasy/meetings.py tests/test_engine_meeting.py
git commit -m "Dual-track meetings: drop mic sentences that echo system audio"
```

---

### Task 3: Record the output-route category

**Files:**
- Modify: `native/SystemAudioCapture.swift` (new `outputRoute()` near `audioProcessObject`, ~line 117; `"ready"` emit ~line 535; `"stopped"` emit ~line 450)
- Modify: `speakeasy/system_audio.py` (`SystemTrackResult`, `SystemAudioRecorder.__init__`, `_read_events`, `take_result`)
- Modify: `speakeasy/meeting_recorder.py` (`MeetingCaptureHealth` + `to_dict`; the three `MeetingCaptureHealth(` construction sites ~lines 456, 554, 630)
- Modify: `speakeasy/meetings.py` (`_CAPTURE_HEALTH_KEYS`)
- Test: `tests/test_system_audio.py`, `tests/test_dual_track_meeting.py`

**Interfaces:**
- Produces: `system_audio.OUTPUT_ROUTES: frozenset[str]` (the category set in Global Constraints).
- Produces: `SystemTrackResult.output_route_start: str | None = None`, `SystemTrackResult.output_route_stop: str | None = None`.
- Produces: `MeetingCaptureHealth.output_route_start: str | None = None`, `.output_route_stop: str | None = None`. Both are in `to_dict()` and whitelisted.
- Helper protocol: `{"event":"ready","output_route":"<category>"}` and `{"event":"stopped",...,"output_route":"<category>"}`.

- [ ] **Step 1: Write failing Python tests.** Append to `tests/test_system_audio.py` (it already has `FakeProcess` and `_available`):

```python
def _run_helper(monkeypatch, tmp_path, events):
    _available(monkeypatch, tmp_path)
    process = FakeProcess(events)
    monkeypatch.setattr(system_audio.subprocess, "Popen", lambda *a, **k: process)
    recorder = system_audio.SystemAudioRecorder()
    recorder.start(tmp_path / "system.wav")
    return recorder.stop()


def test_helper_reports_output_route_at_start_and_stop(monkeypatch, tmp_path):
    result = _run_helper(monkeypatch, tmp_path, [
        {"event": "ready", "output_route": "built_in_speakers"},
        {"event": "first_buffer", "host_time_ns": 5},
        {"event": "nonzero_signal"},
        {"event": "stopped", "frames": 10, "dropped_frames": 0,
         "output_route": "bluetooth"},
    ])
    assert result.output_route_start == "built_in_speakers"
    assert result.output_route_stop == "bluetooth"


def test_output_route_outside_known_set_is_unknown(monkeypatch, tmp_path):
    result = _run_helper(monkeypatch, tmp_path, [
        {"event": "ready", "output_route": "Someone's AirPods Pro"},  # a NAME
        {"event": "first_buffer", "host_time_ns": 5},
        {"event": "stopped", "frames": 10, "dropped_frames": 0,
         "output_route": 7},
    ])
    assert result.output_route_start == "unknown"
    assert result.output_route_stop == "unknown"
```

A ready event with no `output_route` key (older helper) must leave both fields `None`. Add `assert result.output_route_start is None` and `assert result.output_route_stop is None` to the existing `test_helper_reports_first_buffer_and_clean_stop`.

In `tests/test_dual_track_meeting.py`:
- give `FakeSystem.__init__` keyword args `output_route_start="built_in_speakers"` and `output_route_stop="built_in_speakers"`, stored as `self.output_route_start` / `self.output_route_stop`;
- in `FakeSystem.stop()` (line ~79) and `take_result()` (line ~93), add `output_route_start=self.output_route_start, output_route_stop=self.output_route_stop` to the `SystemTrackResult(...)` call;
- give `FakeSystem` the same attributes the live `health` property reads (they are plain instance attributes here);
- add `"output_route_start"` and `"output_route_stop"` to the expected set in `test_writer_health_and_dropped_frames_are_reported_without_content`;
- add a test:

```python
def test_output_route_reaches_capture_health(spool_dir):
    recorder = MeetingCaptureRecorder(
        mic=FakeMic(spool_dir / "mic.wav"),
        system=FakeSystem(output_route_start="built_in_speakers",
                          output_route_stop="bluetooth"),
    )
    recorder.start()
    recording = recorder.stop()
    assert recording.health.output_route_start == "built_in_speakers"
    assert recording.health.output_route_stop == "bluetooth"
    assert recording.health.to_dict()["output_route_stop"] == "bluetooth"
```

Append to `tests/test_meeting_echo.py` (no existing test covers `filter_capture_health` directly):

```python
from speakeasy.meetings import filter_capture_health


def test_new_health_keys_pass_the_whitelist():
    health = {
        "output_route_start": "built_in_speakers",
        "output_route_stop": "bluetooth",
        "mic_echo_sentences_removed": 3,
        "output_device_name": "MacBook Pro Speakers",  # never whitelisted
    }
    assert filter_capture_health(health) == {
        "output_route_start": "built_in_speakers",
        "output_route_stop": "bluetooth",
        "mic_echo_sentences_removed": 3,
    }
```

This test fails until Task 3 adds the route keys. (`mic_echo_sentences_removed` is added in Task 2.)

- [ ] **Step 2: Run to verify failure.** Run: `.venv/bin/python -m pytest tests/test_system_audio.py tests/test_dual_track_meeting.py -q`. Expected: AttributeError / missing-key failures.

- [ ] **Step 3: Python implementation.**

`speakeasy/system_audio.py`: near the top:

```python
# Content-free output-device categories the helper may report. Anything else
# (including a device name) is stored as "unknown" so it can never reach
# capture health.
OUTPUT_ROUTES = frozenset({
    "built_in_speakers", "built_in_headphones", "bluetooth", "usb",
    "display", "airplay", "virtual", "other", "unknown",
})


def _route(value) -> str:
    return value if isinstance(value, str) and value in OUTPUT_ROUTES else "unknown"
```

- `SystemTrackResult`: add `output_route_start: str | None = None` and `output_route_stop: str | None = None` after `capture_scope`.
- `__init__`: `self._output_route_start: str | None = None` and `self._output_route_stop: str | None = None`.
- `_read_events`: add branches

```python
            elif kind == "ready":
                if "output_route" in event:
                    self._output_route_start = _route(event.get("output_route"))
```

  and inside the existing `elif kind == "stopped":` branch:

```python
                if "output_route" in event:
                    self._output_route_stop = _route(event.get("output_route"))
```

- `take_result`: pass `output_route_start=self._output_route_start, output_route_stop=self._output_route_stop`.

`speakeasy/meeting_recorder.py`: add fields `output_route_start: str | None = None` and `output_route_stop: str | None = None` to `MeetingCaptureHealth` (after `capture_scope`) and to `to_dict()`. In the three construction sites:
- the live `health` property: `output_route_start=getattr(self.system, "output_route_start", None)`. Expose a read-only property `output_route_start` on `SystemAudioRecorder` returning `self._output_route_start`, matching how `nonzero_signal` is exposed. Use `getattr` only if other fakes lack it; prefer adding the attribute to every fake.
- the normal-stop site: `output_route_start=(system_result.output_route_start if system_result else None)` and the same for `_stop`.
- the forced-close site: `output_route_start=system_result.output_route_start, output_route_stop=system_result.output_route_stop`.

`speakeasy/meetings.py`: add `"output_route_start",` and `"output_route_stop",` to `_CAPTURE_HEALTH_KEYS`.

- [ ] **Step 4: Swift implementation** in `native/SystemAudioCapture.swift`, after `audioProcessObject`:

```swift
/// Category of the current default output device — never its name, which
/// would identify the user's hardware in capture health.
private func outputRoute() -> String {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDefaultOutputDevice,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var device = AudioObjectID(kAudioObjectUnknown)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(
        AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &device
    ) == noErr, device != kAudioObjectUnknown else { return "unknown" }
    var transport: UInt32 = 0
    address.mSelector = kAudioDevicePropertyTransportType
    size = UInt32(MemoryLayout<UInt32>.size)
    guard AudioObjectGetPropertyData(device, &address, 0, nil, &size, &transport) == noErr
    else { return "unknown" }
    switch transport {
    case kAudioDeviceTransportTypeBuiltIn:
        var source: UInt32 = 0
        var sourceAddress = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyDataSource,
            mScope: kAudioObjectPropertyScopeOutput,
            mElement: kAudioObjectPropertyElementMain
        )
        size = UInt32(MemoryLayout<UInt32>.size)
        if AudioObjectGetPropertyData(device, &sourceAddress, 0, nil, &size, &source) == noErr,
           source == 0x6864_706E /* 'hdpn' */ {
            return "built_in_headphones"
        }
        return "built_in_speakers"
    case kAudioDeviceTransportTypeBluetooth, kAudioDeviceTransportTypeBluetoothLE:
        return "bluetooth"
    case kAudioDeviceTransportTypeUSB:
        return "usb"
    case kAudioDeviceTransportTypeHDMI, kAudioDeviceTransportTypeDisplayPort:
        return "display"
    case kAudioDeviceTransportTypeAirPlay:
        return "airplay"
    case kAudioDeviceTransportTypeVirtual, kAudioDeviceTransportTypeAggregate,
         kAudioDeviceTransportTypeAutoAggregate:
        return "virtual"
    default:
        return "other"
    }
}
```

Change `emit(["event": "ready"])` (in the capture branch, not the process-list branch) to `emit(["event": "ready", "output_route": outputRoute()])`. Add `"output_route": outputRoute(),` to the `"stopped"` dictionary in `stop()`.

- [ ] **Step 5: Build the helper and run the tests.**

Run: `scripts/build_system_audio_helper.sh` → expected `Built build/native/SpeakeasySystemAudioCapture ...`, no errors.
Run: `.venv/bin/python -m pytest -q` (timeout 600000) → expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add native/SystemAudioCapture.swift speakeasy/system_audio.py speakeasy/meeting_recorder.py speakeasy/meetings.py tests/
git commit -m "Capture health: record output-route category at meeting start and stop"
```

---

### Task 4: Docs

**Files:** `README.md` (~lines 282–295, "Fallback and speaker-mode limitation"), `speakeasy/meetings.py` (`merge_tracks` docstring), `AGENTS.md` (capture-health schema sentence at the end of the threading section; the dual-track paragraph in "What it is"), `docs/superpowers/specs/2026-09-26-meeting-library-mcp-design.md` (~line 61).

- [ ] **Step 1:** README: replace "With system capture active, speaker playback can still bleed into the microphone and appear twice. Speakeasy preserves both segments rather than risk deleting a real repeated phrase or overlapping speech;" with text saying that mic sentences which match system-track words *and* arrive within the bleed delay window are dropped before the transcript is saved. Say also that a deliberate repeat (spoken after the other person finishes) and speech mixed with bleed are kept, so some duplication can remain, and that headphones still give the cleanest separation. Mention that capture health records the output-route category (not the device name) at start and stop.
- [ ] **Step 2:** `merge_tracks` docstring: keep "merge does no text cleanup". Add that speaker-mode echo is removed earlier, per sentence, by `remove_mic_echo` using arrival time.
- [ ] **Step 3:** AGENTS.md: add "output-route category" and "mic echo removal count" to the fixed capture-health schema list. Add one line to the dual-track paragraph: "mic sentences that echo system-track words within the bleed window are removed before segmenting (`meetings.remove_mic_echo`)."
- [ ] **Step 4:** Spec note: append "Since 2026-09-30, new recordings drop bleed per sentence at processing time (plan 2026-09-30-meeting-echo-removal); search collapse remains for older meetings."
- [ ] **Step 5:** Commit: `git commit -am "Docs: speaker-bleed removal and output-route health"`.

---

### Task 5: Install, calibrate on a real recording, look at it

This needs the user and the installed app; it cannot be done by a subagent.

- [ ] **Step 1:** Warn the user that `--install` stops Claude Desktop's Speakeasy MCP connector (toggle it to recover). Then run `npm --prefix frontend run build` and `scripts/build_app.sh --install` (timeout 600000).
- [ ] **Step 2:** Ask the user to record two short (~2 min) meetings with system capture on:
  (a) **speakers:** play a talk video or call through the Mac speakers; talk over it a few times and, once, repeat a phrase right after the video says it;
  (b) **headphones:** same, with headphones.
- [ ] **Step 3:** Read `~/Library/Logs/Speakeasy.log` lines starting `→ echo candidate` / `→ mic echo` (numbers only). Re-run the read-only probe (mode=ro, numbers only, no text) on the two new meeting ids. Check:
  - `output_route_start/stop` match what the user used;
  - (a) removed > 0, and the probe shows mic echo ≪ 74%;
  - (b) removed ≈ 0;
  - the deliberate repeat is kept (its candidate line shows `kept` with lag > 0.75 s).
- [ ] **Step 4:** Adjust thresholds only if the lag/coverage distribution shows a clear gap the defaults miss. If so, record the observed distribution and the new values in this plan, update `config.py` and the tests, rerun the suite, and commit.
- [ ] **Step 5:** Open both meetings in the Meetings window with the user and confirm there are no duplicated passages in (a). Report which checks were tests only and which were launched and looked at.

## Status

- [ ] Task 1  - [ ] Task 2  - [ ] Task 3  - [ ] Task 4  - [ ] Task 5

## Out of scope / follow-ups

- Dropped frames and writer lag in 20260929-223110-3b95 (738k mic / 185k system frames): separate investigation.
- Rewriting stored meetings: rejected (their audio is gone; it would be lossy).
- Route changes in the middle of a meeting are only seen as a start/stop difference; a CoreAudio listener could log every change if that turns out to matter.
