# Diarization Child Process Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run speaker diarization (diarize → tidy → voice identification) in a spawned child process per meeting. The app's CPU heap then stops growing after meetings, and a failed diarization saves the transcript with one speaker label instead of losing the meeting.

**Architecture:** A new `speakeasy/diarization_process.py` holds:
- the pure pipeline `diarize_meeting_audio` (moved out of `Engine._diarize_track`);
- `ChildDiarizationRunner`, used by the app;
- `InProcessDiarizationRunner`, used by tests and as the benchmark reference.

The engine calls an injectable `diarization_runner` with the spool WAV path and never loads the diarization track itself. The child talks to the parent over a one-way `multiprocessing` Pipe (spawn context), the same pattern `MicrophoneHelper` already runs in the installed app.

**Tech Stack:** Python 3.11 (`.venv/bin/python`), sherpa-onnx 1.13.4 (child only), multiprocessing spawn, pytest.

**Spec:** `docs/superpowers/specs/2026-09-30-diarization-subprocess-design.md`. Read it first; it has all the measurements.

## Global Constraints

- Read `AGENTS.md` before starting. Offline only: no network, no new dependencies.
- Tests must never import or run sherpa-onnx, the model, or the mic. Child-process tests use stub targets from `tests/diarization_child_stubs.py`.
- Never run app code against the real `~/Library/Application Support/Speakeasy`. The autouse `isolated_home` fixture covers tests; the benchmark script sets `HOME` to a temp dir and asserts it *before* importing `speakeasy`.
- Only exception **type names** cross the pipe (`("error", "ValueError")`), never message text. Messages can carry paths or transcript text.
- `speakeasy/diarization_process.py` and `speakeasy/wav_io.py` must not import `mlx`, `parakeet_mlx` or `sherpa_onnx` at module level. `sherpa_onnx` is imported only inside the child's work function and `InProcessDiarizationRunner`.
- `config.DIARIZATION_CHILD_LAUNCH_TIMEOUT_SECONDS = 20.0`; poll interval 0.1 s; terminate grace 2.0 s; no overall time limit.
- `DiarizationFailed.reason` is one of `launch_timeout`, `launch_failed`, `child_exited`, `child_error:<TypeName>`.
- New `capture_health` keys: `diarization_status` (`"ok"` / `"failed"`) and `diarization_failure` (the reason).
- Keep both timing stage names `diarization` and `voice_identification`.
- Run tests with `.venv/bin/python -m pytest`. The full suite took 16.5 s (743 tests, 30 Sep) here; give the Bash call `timeout: 600000` and pass `-p no:cacheprovider`. Mutation runs: `python -B` and clear `__pycache__` (see memory notes).
- Work on branch `diarization-subprocess`, never on `master`.

## Review Focus

These are the five input classes the spec implies and ordinary tests are least likely to hit, most likely first. Each has a test in the task named.

1. **Cancel pressed while the child is still starting** (before `ready`). The meeting must cancel within ~1 s, not wait out the 20 s launch budget. Test: Task 2, `test_cancel_before_ready_is_prompt`.
2. **A 2-hour meeting sends thousands of turns.** The whole list must arrive intact through the pipe. Test: Task 2, `test_large_turn_list_arrives_intact` (20,000 turns).
3. **The child dies mid-run after reporting progress** (crash, OOM kill). This must be `child_exited`, not a hang, and the transcript is still saved. Tests: Task 2, `test_child_exit_after_progress_is_child_exited`; Task 3, `test_diarization_failure_saves_transcript_with_one_label`.
4. **An exception whose message holds a path or transcript words.** Only the type name may reach the parent, the log or `capture_health`. Test: Task 2, `test_child_error_sends_only_type_name`.
5. **Back-to-back meetings with different speaker counts.** Each meeting gets a fresh child, with nothing cached from the previous count (today's code re-used a Diarizer keyed by count). Test: Task 2, `test_each_call_gets_a_fresh_child`.

---

### Task 1: Light WAV reader + pure diarization pipeline

**Files:**
- Create: `speakeasy/wav_io.py`
- Modify: `speakeasy/transcriber.py:64-110` (move `read_wav_mono_f32` out; re-export it)
- Create: `speakeasy/diarization_process.py` (first half)
- Test: `tests/test_diarization_process.py`

**Interfaces:**
- Produces:
  - `speakeasy.wav_io.read_wav_mono_f32(path: Path) -> np.ndarray`. The same function, moved; `speakeasy.transcriber.read_wav_mono_f32` still works through a re-export.
  - `speakeasy.diarization_process`:
    - `class DiarizationFailed(Exception)` with `.reason: str`
    - `turn_to_wire(turn) -> tuple` and `turn_from_wire(values) -> DiarizationTurn`
    - `diarize_meeting_audio(audio, diarizer, embed, *, expected_count, max_speakers, voice_profile_names, progress, cancelled, on_phase=lambda phase: None) -> list`
    - `class InProcessDiarizationRunner(diarizer=None, embed=None)`, called as `runner(wav_path, *, expected_count, max_speakers, voice_profile_names, progress, cancel, on_phase=lambda phase: None) -> list`

- [ ] **Step 0: Branch**

```bash
git checkout -b diarization-subprocess
```

- [ ] **Step 1: Write the failing tests** in `tests/test_diarization_process.py`

```python
"""diarization_process: the pure pipeline and the runners (no sherpa-onnx)."""

import subprocess
import sys
import threading
import wave

import numpy as np
import pytest

from speakeasy import config, meetings
from speakeasy.diarization_process import (
    DiarizationFailed,
    InProcessDiarizationRunner,
    diarize_meeting_audio,
    turn_from_wire,
    turn_to_wire,
)


class SplitRemoteDiarizer:
    """One real remote voice split into a big and a small cluster."""
    def diarize(self, samples, progress=lambda f: None):
        progress(1.0)
        return [(0.0, 0.8, 4), (1.0, 1.8, 9)]


def _pipeline(audio, diarizer, **overrides):
    kwargs = dict(
        expected_count=None, max_speakers=None, voice_profile_names=(),
        progress=lambda f: None, cancelled=lambda: False,
    )
    kwargs.update(overrides)
    embed = kwargs.pop("embed", lambda samples: np.array([1.0, 0.0]))
    return diarize_meeting_audio(audio, diarizer, embed, **kwargs)


def _audio(seconds=2):
    return np.zeros(config.SAMPLE_RATE * seconds, dtype=np.float32)


def test_cap_merges_speakers_when_count_unknown():
    turns = _pipeline(_audio(), SplitRemoteDiarizer(), max_speakers=1)
    # Equal talk (0.8 s each): the tie goes to the lower cluster id.
    assert {t.speaker for t in turns} == {4}


def test_manual_count_returns_diarizer_turns_unchanged():
    turns = _pipeline(_audio(), SplitRemoteDiarizer(), expected_count=2, max_speakers=1)
    assert turns == [(0.0, 0.8, 4), (1.0, 1.8, 9)]


def test_identify_runs_only_with_names(monkeypatch):
    from speakeasy import voice_profiles
    calls = []

    class FakeProfiles:
        def identify(self, audio, turns, names, *, cancelled):
            calls.append(names)
            return [meetings.DiarizationTurn(0.0, 0.8, 4, 0.9, False, "Alice")]

    monkeypatch.setattr(voice_profiles, "VoiceProfileStore", FakeProfiles)
    _pipeline(_audio(), SplitRemoteDiarizer(), expected_count=2)
    assert calls == []
    turns = _pipeline(_audio(), SplitRemoteDiarizer(), expected_count=2,
                      voice_profile_names=("Alice",))
    assert calls == [["Alice"]]
    assert turns[0].profile_id == "Alice"


def test_phase_is_reported_before_identification_even_without_names():
    phases = []
    _pipeline(_audio(), SplitRemoteDiarizer(), expected_count=2, on_phase=phases.append)
    assert phases == ["voice_identification"]


def test_empty_audio_raises_value_error():
    with pytest.raises(ValueError):
        _pipeline(np.empty(0, dtype=np.float32), SplitRemoteDiarizer())


def test_wire_round_trip_keeps_every_field():
    turn = meetings.DiarizationTurn(1.25, 2.5, 3, 0.75, True, "Alice")
    assert turn_from_wire(turn_to_wire(turn)) == turn
    assert turn_from_wire(turn_to_wire((0.0, 1.0, 2))) == meetings.DiarizationTurn(0.0, 1.0, 2)


def test_diarization_failed_carries_reason():
    assert DiarizationFailed("child_exited").reason == "child_exited"


def test_in_process_runner_reads_the_wav_itself(tmp_path):
    path = tmp_path / "track.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(config.SAMPLE_RATE)
        out.writeframes(np.full(config.SAMPLE_RATE * 2, 100, dtype=np.int16).tobytes())
    seen = []

    class Recording(SplitRemoteDiarizer):
        def diarize(self, samples, progress=lambda f: None):
            seen.append(len(samples))
            return super().diarize(samples, progress)

    runner = InProcessDiarizationRunner(Recording())
    turns = runner(path, expected_count=2, max_speakers=None, voice_profile_names=(),
                   progress=lambda f: None, cancel=threading.Event())
    assert seen == [config.SAMPLE_RATE * 2]
    assert turns == [(0.0, 0.8, 4), (1.0, 1.8, 9)]


def test_child_side_modules_do_not_import_mlx_or_sherpa():
    code = (
        "import sys\n"
        "import speakeasy.diarization_process, speakeasy.wav_io\n"
        "import speakeasy.speaker_merge, speakeasy.voice_profiles, speakeasy.diarizer\n"
        "heavy = [m for m in ('mlx', 'parakeet_mlx', 'sherpa_onnx') if m in sys.modules]\n"
        "assert not heavy, heavy\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_transcriber_still_exports_read_wav_mono_f32():
    from speakeasy import transcriber, wav_io
    assert transcriber.read_wav_mono_f32 is wav_io.read_wav_mono_f32
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_diarization_process.py -q -p no:cacheprovider`
Expected: collection error, `ModuleNotFoundError: No module named 'speakeasy.diarization_process'`.

- [ ] **Step 3: Create `speakeasy/wav_io.py`**

Move `read_wav_mono_f32` from `speakeasy/transcriber.py` **verbatim**: the whole function, from `def read_wav_mono_f32` to its final `return`, including the resampling tail. The module header:

```python
"""PCM16 WAV → 16 kHz mono float32, with no model imports.

Lives apart from transcriber.py (which imports MLX at module level) so the
diarization child process can read a meeting spool without loading the
speech-model stack.
"""

import wave
from pathlib import Path

import numpy as np

from . import config

# (paste def read_wav_mono_f32(...) here, unchanged)
```

In `speakeasy/transcriber.py`, delete the function body and add this, next to the other `from .` imports (after `from .meeting_stream import ...`):

```python
from .wav_io import read_wav_mono_f32  # noqa: F401  (re-export; engine and scripts import it here)
```

If `transcriber.py` then no longer uses `wave` anywhere, leave the import alone; `transcribe_long_wav` still uses it.

- [ ] **Step 4: Create `speakeasy/diarization_process.py`** (first half; Task 2 appends the child runner)

```python
"""Speaker diarization for one meeting, run in a child process by the app.

Why a child: sherpa-onnx's onnxruntime arenas grow to the longest meeting so
far and never shrink (302 MB after a 3.2-min meeting, 804 MB after 29.3 min),
and macOS's large-malloc cache keeps even freed blocks in the footprint, so
deleting the models in-process returns almost nothing. A child that exits
returns all of it for ~0.7 s per meeting. Measurements:
docs/superpowers/specs/2026-09-30-diarization-subprocess-design.md.

This module must stay import-light (no mlx, parakeet_mlx or sherpa_onnx at
module level): the spawned child imports it first.
"""

import threading
from collections.abc import Callable
from pathlib import Path

from .meetings import DiarizationTurn
from .wav_io import read_wav_mono_f32


class DiarizationFailed(Exception):
    """Diarization could not produce turns; the meeting is saved unlabelled.

    `reason` is a privacy-safe code: launch_timeout, launch_failed,
    child_exited or child_error:<ExceptionTypeName>.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def turn_to_wire(turn) -> tuple:
    if not isinstance(turn, DiarizationTurn):
        turn = DiarizationTurn(float(turn[0]), float(turn[1]), int(turn[2]))
    return (
        float(turn.start), float(turn.end), int(turn.speaker),
        turn.confidence, bool(turn.overlap), turn.profile_id,
    )


def turn_from_wire(values) -> DiarizationTurn:
    start, end, speaker, confidence, overlap, profile_id = values
    return DiarizationTurn(start, end, speaker, confidence, overlap, profile_id)


def diarize_meeting_audio(
    audio,
    diarizer,
    embed: Callable,
    *,
    expected_count: int | None,
    max_speakers: int | None,
    voice_profile_names,
    progress: Callable[[float], None],
    cancelled: Callable[[], bool],
    on_phase: Callable[[str], None] = lambda phase: None,
) -> list:
    """diarize → tidy (only when the count is unknown) → identify (only with names)."""
    if not len(audio):
        raise ValueError("Meeting track has no readable audio")
    turns = diarizer.diarize(audio, progress=progress)
    if expected_count is None:
        # The user's own count is exact and wins. Otherwise fold the
        # over-split clusters (and apply the calendar cap) before naming
        # voices, so profile matching sees whole voices.
        from . import speaker_merge

        turns = speaker_merge.tidy_speakers(
            audio, turns, embed, max_speakers=max_speakers, cancelled=cancelled)
    on_phase("voice_identification")
    if voice_profile_names:
        from .voice_profiles import VoiceProfileStore

        turns = VoiceProfileStore().identify(
            audio, turns, list(voice_profile_names), cancelled=cancelled)
    return turns


class InProcessDiarizationRunner:
    """Runs the pipeline in this process.

    Tests inject fakes through it, and the memory benchmark uses it as the
    reference for the child's turns. The app uses ChildDiarizationRunner.
    """

    def __init__(self, diarizer=None, embed: Callable | None = None) -> None:
        self.diarizer = diarizer
        self.embed = embed

    def __call__(
        self,
        wav_path,
        *,
        expected_count: int | None,
        max_speakers: int | None,
        voice_profile_names,
        progress: Callable[[float], None],
        cancel: threading.Event,
        on_phase: Callable[[str], None] = lambda phase: None,
    ) -> list:
        audio = read_wav_mono_f32(Path(wav_path))
        diarizer = self.diarizer
        if diarizer is None:
            from .diarizer import Diarizer

            diarizer = Diarizer(expected_count)
        embed = self.embed
        if embed is None:
            # Built on first use only: tidy_speakers often needs no
            # embeddings, and tests replace VoiceProfileStore with fakes
            # that only implement identify().
            store = []

            def embed(samples):
                if not store:
                    from .voice_profiles import VoiceProfileStore

                    store.append(VoiceProfileStore())
                return store[0].embed(samples)
        return diarize_meeting_audio(
            audio, diarizer, embed,
            expected_count=expected_count, max_speakers=max_speakers,
            voice_profile_names=voice_profile_names, progress=progress,
            cancelled=cancel.is_set, on_phase=on_phase,
        )
```

- [ ] **Step 5: Run the new tests, then the transcriber/dual-track tests**

Run: `.venv/bin/python -m pytest tests/test_diarization_process.py tests/test_dual_track_meeting.py tests/test_transcriber_warmup.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add speakeasy/wav_io.py speakeasy/transcriber.py speakeasy/diarization_process.py tests/test_diarization_process.py
git commit -m "Extract diarization pipeline and an MLX-free WAV reader"
```

---

### Task 2: Child process runner

**Files:**
- Modify: `speakeasy/diarization_process.py` (append)
- Modify: `speakeasy/config.py` (after `DIARIZATION_MIN_OFF`, ~line 120)
- Create: `tests/diarization_child_stubs.py`
- Test: `tests/test_diarization_child.py`

**Interfaces:**
- Consumes: `DiarizationFailed`, `turn_to_wire`, `turn_from_wire`, `diarize_meeting_audio`, `read_wav_mono_f32` (Task 1).
- Produces:
  - `run_child(conn, wav_path: str, expected_count, max_speakers, voice_profile_names: tuple)` is the real child target.
  - `serve(conn, work)` is the child-side protocol. `work(ready, progress, on_phase) -> list[turns]`, where `ready()` sends `("ready",)`.
  - `class ChildDiarizationRunner(target=None, *, launch_timeout=config.DIARIZATION_CHILD_LAUNCH_TIMEOUT_SECONDS, poll_seconds=0.1, terminate_grace=2.0)`. It is called exactly like `InProcessDiarizationRunner`. It raises `DiarizationFailed(reason)`, or `MeetingCancelled` when `cancel` is set.
  - The target is called as `target(conn, wav_path_str, expected_count, max_speakers, voice_profile_names_tuple)`.

- [ ] **Step 1: Add the config constant** to `speakeasy/config.py`, directly after `DIARIZATION_MIN_OFF = 0.5`:

```python

# Diarization runs in a spawned child per meeting (memory; see
# docs/model-memory.md). Budget for the child to import sherpa-onnx and say
# "ready" (measured ~0.2 s in dev). No overall limit: a 2-hour meeting
# legitimately takes ~12 minutes.
DIARIZATION_CHILD_LAUNCH_TIMEOUT_SECONDS = 20.0
```

- [ ] **Step 2: Write the stub targets** in `tests/diarization_child_stubs.py`

These are top-level functions so the spawn context can pickle them by name. The spawned child gets the parent's `sys.path`, which pytest's rootdir import mode puts `tests/` on. None of them import sherpa-onnx.

```python
"""Spawn targets for tests/test_diarization_child.py (no sherpa-onnx)."""

import os
import time

from speakeasy import meetings
from speakeasy.diarization_process import serve


def stub_turns(conn, wav_path, expected_count, max_speakers, names):
    def work(ready, progress, on_phase):
        ready()
        progress(0.5)
        on_phase("voice_identification")
        return [meetings.DiarizationTurn(0.0, 1.0, 3, 0.9, True, "Alice"),
                (1.0, 2.0, expected_count if expected_count is not None else 7)]
    serve(conn, work)


def stub_never_ready(conn, wav_path, expected_count, max_speakers, names):
    def work(ready, progress, on_phase):
        time.sleep(30)
        return []
    serve(conn, work)


def stub_exit_after_progress(conn, wav_path, expected_count, max_speakers, names):
    def work(ready, progress, on_phase):
        ready()
        progress(0.25)
        time.sleep(0.2)
        os._exit(3)
    serve(conn, work)


def stub_error_with_secret(conn, wav_path, expected_count, max_speakers, names):
    def work(ready, progress, on_phase):
        ready()
        raise ValueError("/Users/someone/secret-meeting.wav: the merger closes Friday")
    serve(conn, work)


def stub_slow(conn, wav_path, expected_count, max_speakers, names):
    def work(ready, progress, on_phase):
        ready()
        time.sleep(30)
        return []
    serve(conn, work)


def stub_many_turns(conn, wav_path, expected_count, max_speakers, names):
    def work(ready, progress, on_phase):
        ready()
        return [(i * 0.5, i * 0.5 + 0.4, i % 6) for i in range(20_000)]
    serve(conn, work)
```

- [ ] **Step 3: Write the failing tests** in `tests/test_diarization_child.py`

```python
"""ChildDiarizationRunner with real spawned children running stub targets."""

import threading
import time

import pytest

import diarization_child_stubs as stubs
from speakeasy import meetings
from speakeasy.diarization_process import ChildDiarizationRunner, DiarizationFailed
from speakeasy.transcriber import MeetingCancelled


def _call(runner, *, cancel=None, expected_count=None, progress=None, on_phase=None):
    return runner(
        "/nonexistent/track.wav",
        expected_count=expected_count, max_speakers=None, voice_profile_names=(),
        progress=progress or (lambda f: None),
        cancel=cancel or threading.Event(),
        on_phase=on_phase or (lambda phase: None),
    )


def test_turns_progress_and_phase_arrive():
    progress, phases = [], []
    turns = _call(ChildDiarizationRunner(stubs.stub_turns), progress=progress.append,
                  on_phase=phases.append)
    assert turns == [meetings.DiarizationTurn(0.0, 1.0, 3, 0.9, True, "Alice"),
                     meetings.DiarizationTurn(1.0, 2.0, 7)]
    assert progress == [0.5]
    assert phases == ["voice_identification"]


def test_each_call_gets_a_fresh_child():
    runner = ChildDiarizationRunner(stubs.stub_turns)
    assert _call(runner, expected_count=2)[1].speaker == 2
    assert _call(runner, expected_count=None)[1].speaker == 7


def test_launch_timeout():
    runner = ChildDiarizationRunner(stubs.stub_never_ready, launch_timeout=1.0)
    started = time.monotonic()
    with pytest.raises(DiarizationFailed) as failure:
        _call(runner)
    assert failure.value.reason == "launch_timeout"
    assert time.monotonic() - started < 5.0


def test_child_exit_after_progress_is_child_exited():
    progress = []
    with pytest.raises(DiarizationFailed) as failure:
        _call(ChildDiarizationRunner(stubs.stub_exit_after_progress),
              progress=progress.append)
    assert failure.value.reason == "child_exited"
    assert progress == [0.25]


def test_child_error_sends_only_type_name(capsys):
    with pytest.raises(DiarizationFailed) as failure:
        _call(ChildDiarizationRunner(stubs.stub_error_with_secret))
    assert failure.value.reason == "child_error:ValueError"
    assert "secret" not in str(failure.value)
    captured = capsys.readouterr()
    assert "secret" not in captured.out + captured.err


def test_cancel_while_running_is_prompt():
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(MeetingCancelled):
        _call(ChildDiarizationRunner(stubs.stub_slow), cancel=cancel)
    assert time.monotonic() - started < 3.0


def test_cancel_before_ready_is_prompt():
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(MeetingCancelled):
        _call(ChildDiarizationRunner(stubs.stub_never_ready), cancel=cancel)
    assert time.monotonic() - started < 3.0


def test_large_turn_list_arrives_intact():
    turns = _call(ChildDiarizationRunner(stubs.stub_many_turns))
    assert len(turns) == 20_000
    assert turns[-1] == meetings.DiarizationTurn(19_999 * 0.5, 19_999 * 0.5 + 0.4, 19_999 % 6)


def test_no_child_left_running():
    import multiprocessing
    _call(ChildDiarizationRunner(stubs.stub_turns))
    cancel = threading.Event(); cancel.set()
    with pytest.raises(MeetingCancelled):
        _call(ChildDiarizationRunner(stubs.stub_slow), cancel=cancel)
    assert [p for p in multiprocessing.active_children()
            if p.name == "speakeasy-diarization"] == []
```

- [ ] **Step 4: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_diarization_child.py -q -p no:cacheprovider`
Expected: ImportError, as `serve` and `ChildDiarizationRunner` don't exist yet.

- [ ] **Step 5: Append the child side and runner** to `speakeasy/diarization_process.py`

Add `import multiprocessing` and `import time` to the imports at the top, and `from . import config` to the `from .` imports. Then append:

```python
def serve(conn, work) -> None:
    """Child side of the protocol. Only exception type names cross the pipe."""
    last = [-1.0]

    def progress(fraction: float) -> None:
        # sherpa reports per chunk; forward at most every 1 % (and the end).
        if fraction >= 1.0 or fraction - last[0] >= 0.01:
            last[0] = fraction
            conn.send(("progress", float(fraction)))

    try:
        turns = work(
            lambda: conn.send(("ready",)),
            progress,
            lambda phase: conn.send(("phase", phase)),
        )
        conn.send(("turns", [turn_to_wire(turn) for turn in turns]))
    except Exception as error:
        try:
            conn.send(("error", type(error).__name__))
        except (OSError, ValueError):
            pass
    finally:
        conn.close()


def run_child(conn, wav_path, expected_count, max_speakers, voice_profile_names) -> None:
    """Spawn target: the whole diarization stage for one track."""

    def work(ready, progress, on_phase):
        import sherpa_onnx  # noqa: F401  (import cost counts toward the launch budget)

        from .diarizer import Diarizer
        from .voice_profiles import VoiceProfileStore

        ready()
        audio = read_wav_mono_f32(Path(wav_path))
        return diarize_meeting_audio(
            audio, Diarizer(expected_count), VoiceProfileStore().embed,
            expected_count=expected_count, max_speakers=max_speakers,
            voice_profile_names=voice_profile_names, progress=progress,
            cancelled=lambda: False,  # the parent cancels by terminating us
            on_phase=on_phase,
        )

    serve(conn, work)


class ChildDiarizationRunner:
    """Parent side: one spawned child per call, always reaped."""

    def __init__(
        self,
        target=None,
        *,
        launch_timeout: float = config.DIARIZATION_CHILD_LAUNCH_TIMEOUT_SECONDS,
        poll_seconds: float = 0.1,
        terminate_grace: float = 2.0,
    ) -> None:
        self._target = target or run_child
        self._launch_timeout = launch_timeout
        self._poll_seconds = poll_seconds
        self._terminate_grace = terminate_grace

    def __call__(
        self,
        wav_path,
        *,
        expected_count: int | None,
        max_speakers: int | None,
        voice_profile_names,
        progress: Callable[[float], None],
        cancel: threading.Event,
        on_phase: Callable[[str], None] = lambda phase: None,
    ) -> list:
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(
            target=self._target,
            args=(sender, str(wav_path), expected_count, max_speakers,
                  tuple(voice_profile_names or ())),
            name="speakeasy-diarization",
            daemon=True,
        )
        finished = False
        try:
            try:
                process.start()
            except Exception:
                raise DiarizationFailed("launch_failed") from None
            sender.close()
            ready = False
            deadline = time.monotonic() + self._launch_timeout
            while True:
                if cancel.is_set():
                    from .transcriber import MeetingCancelled

                    raise MeetingCancelled
                if receiver.poll(self._poll_seconds):
                    try:
                        message = receiver.recv()
                    except (EOFError, OSError):
                        raise DiarizationFailed("child_exited") from None
                    kind = message[0]
                    if kind == "ready":
                        ready = True
                    elif kind == "progress":
                        progress(float(message[1]))
                    elif kind == "phase":
                        on_phase(str(message[1]))
                    elif kind == "turns":
                        finished = True
                        return [turn_from_wire(values) for values in message[1]]
                    elif kind == "error":
                        raise DiarizationFailed(f"child_error:{message[1]}")
                    continue
                if not ready and time.monotonic() > deadline:
                    raise DiarizationFailed("launch_timeout")
                if not process.is_alive() and not receiver.poll(0):
                    raise DiarizationFailed("child_exited")
        finally:
            self._reap(process, graceful=finished)
            receiver.close()
            if not sender.closed:
                sender.close()

    def _reap(self, process, *, graceful: bool) -> None:
        if process.pid is None:
            return
        if graceful:
            process.join(self._terminate_grace)
        if process.is_alive():
            process.terminate()
            process.join(self._terminate_grace)
        if process.is_alive():
            process.kill()
            process.join(self._terminate_grace)
```

Note: `multiprocessing.connection.Connection.closed` exists; `sender.close()` is idempotent-guarded because `process.start()` may have failed before the first close.

- [ ] **Step 6: Run the child tests**

Run: `.venv/bin/python -m pytest tests/test_diarization_child.py tests/test_diarization_process.py -q -p no:cacheprovider`
Expected: all PASS. If the spawned child cannot import `diarization_child_stubs`, fix it by adding the tests directory to the child's path. Do this in the test module with `monkeypatch.setenv("PYTHONPATH", ...)` or a module-level `sys.path` check; do **not** move the stubs into `speakeasy/`.

- [ ] **Step 7: Commit**

```bash
git add speakeasy/diarization_process.py speakeasy/config.py tests/diarization_child_stubs.py tests/test_diarization_child.py
git commit -m "Add per-meeting diarization child runner with cancel and launch budget"
```

---

### Task 3: Engine uses the runner; failed diarization saves the transcript

**Files:**
- Modify: `speakeasy/engine.py`
  - `__init__` (~lines 97, 100, 127): remove `self.diarizer`, `self.speaker_embedder`, `self._diarizer_speaker_count`; add `self.diarization_runner`
  - `_diarize_track` and `_speaker_embedding` (~lines 1058-1108): replace both
  - `_process_meeting` diarization calls (~lines 1188-1203 and 1248-1263) and `capture_health=` (~line 1299)
- Modify: `speakeasy/meetings.py:33` (`_CAPTURE_HEALTH_KEYS`)
- Modify: `scripts/bench_meeting_completion.py:59-60`
- Modify: `tests/test_engine_meeting.py`

**Interfaces:**
- Consumes: `ChildDiarizationRunner`, `InProcessDiarizationRunner`, `DiarizationFailed` (Tasks 1–2).
- Produces:
  - `DictationEngine.diarization_runner`, which is a `ChildDiarizationRunner` by default and injectable.
  - `DictationEngine._diarize_track(path, progress, timing=None, *, max_speakers=None) -> list`, which raises `DiarizationFailed`.
  - `DictationEngine._diarize_or_fallback(path, progress, timing, *, max_speakers) -> tuple[list, dict]`.

- [ ] **Step 1: Mechanically switch the existing tests to the runner seam** in `tests/test_engine_meeting.py`

1. Add `from speakeasy.diarization_process import ChildDiarizationRunner, DiarizationFailed, InProcessDiarizationRunner` to the imports.
2. In `_engine`, replace `engine.diarizer = FakeDiarizer()` with `engine.diarization_runner = InProcessDiarizationRunner(FakeDiarizer())`.
3. Replace every other `engine.diarizer = X` with `engine.diarization_runner = InProcessDiarizationRunner(X)`. For the two that bind a variable first, e.g. `diarizer = FakeRemoteDiarizer(); engine.diarizer = diarizer`, keep the variable and wrap it.
4. Delete every `engine._diarizer_speaker_count = 2` line.
5. In `_mic_only_voices`, replace the `engine.diarizer = ...` and `engine.speaker_embedder = ...` lines with:
   ```python
   vectors = iter([np.array([1.0, 0.0]), np.array([0.9, 0.1]), np.array([0.0, 1.0])])
   engine.diarization_runner = InProcessDiarizationRunner(
       ThreeVoiceDiarizer(), embed=lambda audio: next(vectors))
   ```
   Move the existing `# Fixed embeddings: voices 0 and 1 alike, voice 2 different.` comment with it.
6. Delete `test_diarize_track_caps_speakers` and `test_manual_speaker_count_skips_merging`. They now live in `tests/test_diarization_process.py` (Task 1). Keep `SplitRemoteDiarizer` only if something else uses it.
7. In `test_dual_track_streams_both_transcripts_before_loading_system_for_diarization`, change the expected `calls` to:
   ```python
   # The diarization track is read by the runner (a child in the app), never
   # loaded into the engine's process.
   assert calls == [
       ("transcribe", recorder.mic_path),
       ("transcribe", recorder.system_path),
   ]
   ```
   Rename the test to `test_dual_track_streams_both_transcripts_and_never_loads_system_in_engine`.
8. Then grep: `grep -n "\.diarizer\b\|speaker_embedder\|_diarizer_speaker_count" tests/ speakeasy/ scripts/`. After Step 4, the only hits allowed are the `Diarizer` class itself and `evaluate.py`.

- [ ] **Step 2: Add the new failing engine tests** (append to `tests/test_engine_meeting.py`)

```python
class FailingRunner:
    def __init__(self, reason="child_exited"):
        self.reason = reason
        self.calls = 0

    def __call__(self, wav_path, **kwargs):
        self.calls += 1
        raise DiarizationFailed(self.reason)


def test_default_runner_is_a_child_process(spool_dir):
    engine = DictationEngine()
    assert isinstance(engine.diarization_runner, ChildDiarizationRunner)
    assert not hasattr(engine, "diarizer")
    assert not hasattr(engine, "speaker_embedder")
    engine.shutdown()


def test_diarization_failure_saves_transcript_with_one_label(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    engine.meeting_recorder = FakeDualMeetingRecorder(spool_dir)
    engine.transcriber = FakeDualTranscriber()
    runner = FailingRunner("child_exited")
    engine.diarization_runner = runner
    saved = []
    engine.on_meeting_saved = saved.append

    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)

    assert runner.calls == 1
    stored = MeetingLibrary().get_meeting(saved[0])
    assert [s.speaker for s in stored.segments] == ["You", "Speaker 1"]
    assert "remote one" in stored.segments[1].text
    assert stored.capture_health["diarization_status"] == "failed"
    assert stored.capture_health["diarization_failure"] == "child_exited"
    assert engine.meeting_processing_error is None
    assert not list(spool_dir.iterdir())
    engine.shutdown()


def test_mic_only_diarization_failure_saves_transcript(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    engine.diarization_runner = FailingRunner("child_error:ValueError")
    saved = []
    engine.on_meeting_saved = saved.append

    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)

    stored = MeetingLibrary().get_meeting(saved[0])
    assert [(s.speaker, s.text) for s in stored.segments] == [("Speaker 1", "clod says hello")]
    assert stored.capture_health["diarization_failure"] == "child_error:ValueError"
    engine.shutdown()


def test_successful_diarization_records_ok(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    saved = []
    engine.on_meeting_saved = saved.append
    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    stored = MeetingLibrary().get_meeting(saved[0])
    assert stored.capture_health["diarization_status"] == "ok"
    assert "diarization_failure" not in stored.capture_health
    engine.shutdown()


def test_cancel_from_runner_saves_nothing(meetings_dir, spool_dir):
    engine = _engine(spool_dir)

    def cancelling_runner(wav_path, **kwargs):
        engine.cancel_meeting_processing()
        raise MeetingCancelled

    engine.diarization_runner = cancelling_runner
    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    assert MeetingLibrary().count_meetings() == 0
    assert not list(spool_dir.iterdir())
    engine.shutdown()


def test_runner_receives_path_and_meeting_options(meetings_dir, spool_dir):
    engine = _engine(spool_dir)
    seen = {}

    def recording_runner(wav_path, **kwargs):
        seen["path_exists"] = wav_path.exists()
        seen.update(kwargs)
        kwargs["on_phase"]("voice_identification")
        return [(0.0, 1.0, 0)]

    engine.diarization_runner = recording_runner
    engine.begin_meeting(MeetingOptions(expected_speaker_count=3,
                                        expected_voice_profile_names=("Alice",)))
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    assert seen["path_exists"] is True
    assert seen["expected_count"] == 3
    assert tuple(seen["voice_profile_names"]) == ("Alice",)
    assert seen["cancel"] is engine._meeting_cancel
    engine.shutdown()


def test_timing_keeps_both_diarization_stages(
    meetings_dir, spool_dir, isolate_meeting_latency_log
):
    engine = _engine(spool_dir)
    engine.begin_meeting()
    assert _wait_for(lambda: engine.state is State.MEETING_RECORDING)
    engine.end_meeting()
    assert _wait_for(lambda: engine.state is State.READY)
    record = meeting_benchmark.read_records(isolate_meeting_latency_log)[0]
    assert record["diarization_ms"] is not None
    assert record["voice_identification_ms"] is not None
    engine.shutdown()
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_engine_meeting.py -q -p no:cacheprovider`
Expected: failures such as `test_default_runner_is_a_child_process` (no attribute `diarization_runner`), the fallback tests (meeting not saved), and the mechanically-updated tests (the runner isn't used yet).

- [ ] **Step 4: Implement in `speakeasy/engine.py`**

1. Imports: add `from .diarization_process import ChildDiarizationRunner, DiarizationFailed`.
2. `__init__`:
   - Delete `self.diarizer = None  # built lazily on the first meeting (loads ONNX)`, the `self.speaker_embedder = None` line (with any comment directly above it that only describes it), and `self._diarizer_speaker_count: int | None = None`.
   - In place of the first, add:
     ```python
     # Diarization runs in a spawned child per meeting so onnxruntime's
     # arenas and freed buffers leave with it (docs/model-memory.md).
     self.diarization_runner = ChildDiarizationRunner()
     ```
3. Replace the whole `_diarize_track` method and the `_speaker_embedding` method with:

```python
    def _diarize_track(
        self,
        path: Path,
        progress,
        timing: MeetingTiming | None = None,
        *,
        max_speakers: int | None = None,
    ):
        """Speaker turns for one spooled track, computed by the runner (a
        child process in the app). Raises DiarizationFailed or
        MeetingCancelled."""
        open_phase = ["diarization"]
        if timing is not None:
            timing.start("diarization")

        def on_phase(name: str) -> None:
            if name == "voice_identification" and open_phase[0] == "diarization":
                if timing is not None:
                    timing.finish("diarization")
                    timing.start("voice_identification")
                open_phase[0] = name

        try:
            return self.diarization_runner(
                path,
                expected_count=self._meeting_options.expected_speaker_count,
                max_speakers=max_speakers,
                voice_profile_names=tuple(
                    self._meeting_options.expected_voice_profile_names or ()),
                progress=progress,
                cancel=self._meeting_cancel,
                on_phase=on_phase,
            )
        finally:
            if timing is not None:
                timing.finish(open_phase[0])

    def _diarize_or_fallback(self, path: Path, progress, timing, *, max_speakers):
        """Turns plus capture-health fields. A failed diarization keeps the
        words: every sentence then aligns to one speaker label."""
        try:
            turns = self._diarize_track(path, progress, timing, max_speakers=max_speakers)
        except DiarizationFailed as failure:
            print(f"  → diarization failed ({failure.reason}); saving with one speaker label")
            return [], {"diarization_status": "failed",
                        "diarization_failure": failure.reason}
        return turns, {"diarization_status": "ok"}
```

4. In `_process_meeting`, dual-track branch: replace the block from `system_audio = self._read_meeting_track(recording.system_path)` through `del system_audio` with:

```python
                turns, diarization_health = self._diarize_or_fallback(
                    recording.system_path,
                    lambda f: report_progress(
                        f"Identifying remote speakers… {int(f * 100)}% of stage"
                    ),
                    timing,
                    max_speakers=speaker_cap,
                )
```

5. Mic-only branch: replace the block from `audio = self._read_meeting_track(audio_path)` through `del audio` with:

```python
                turns, diarization_health = self._diarize_or_fallback(
                    audio_path,
                    lambda f: report_progress(
                        f"Identifying speakers… {int(f * 100)}% of stage"
                    ),
                    timing,
                    max_speakers=speaker_cap,
                )
```

6. `NewMeeting(... capture_health=...)`: change it to

```python
                capture_health={
                    **(recording.health.to_dict() if recording.health else {}),
                    **diarization_health,
                },
```

7. Update the `_process_meeting` docstring only if it says diarization happens in-process. Keep "The spool WAV dies in the finally". The child reads the spool before that `finally` runs, because the runner call blocks.

- [ ] **Step 5: Whitelist the two health keys** in `speakeasy/meetings.py` `_CAPTURE_HEALTH_KEYS`: add `"diarization_status",` and `"diarization_failure",` after `"capture_scope",`.

- [ ] **Step 6: Update `scripts/bench_meeting_completion.py`**: replace

```python
        engine.diarizer = None
        engine._diarizer_speaker_count = None
```
with
```python
        from speakeasy.diarization_process import ChildDiarizationRunner
        engine.diarization_runner = ChildDiarizationRunner()
```

- [ ] **Step 7: Run the engine tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_engine_meeting.py tests/test_diarization_process.py tests/test_diarization_child.py -q -p no:cacheprovider`
Expected: all PASS.

Run (Bash `timeout: 600000`): `.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: all PASS. The count should be the baseline (743) minus 2 moved tests plus the new ones. That includes `test_mcp_mode_imports_nothing_heavy`.

- [ ] **Step 8: Commit**

```bash
git add speakeasy/engine.py speakeasy/meetings.py scripts/bench_meeting_completion.py tests/test_engine_meeting.py
git commit -m "Diarize meetings in a child process; save unlabelled transcript on failure"
```

---

### Task 4: Memory benchmark, measured results, docs

**Files:**
- Create: `scripts/measure_diarization_memory.py`
- Modify: `docs/model-memory.md` (append section)
- Modify: `AGENTS.md` (threading-model paragraph ~lines 88-101; test guidance ~lines 184-190)

**Interfaces:**
- Consumes: `ChildDiarizationRunner`, `InProcessDiarizationRunner` (Tasks 1–2).

- [ ] **Step 1: Write `scripts/measure_diarization_memory.py`**

```python
"""Parent-process memory with diarization in a child, on synthetic meetings.

Synthetic audio only (macOS `say`, four voices); HOME is redirected to a temp
dir and asserted BEFORE speakeasy is imported, so the real App Support folder
is never touched. Bundled dev models (models/diarization) only.

Usage: .venv/bin/python -B scripts/measure_diarization_memory.py
Runs ~8 minutes. Prints PASS/FAIL against the spec's acceptance numbers:
parent growth <= 5 MB after each meeting, child overhead <= 1.5 s, child
turns == in-process turns.
"""
import os
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="speakeasy-diar-bench-home-")
os.environ["HOME"] = HOME
assert str(Path.home()) == HOME

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from speakeasy import settings  # noqa: E402
from speakeasy.diarization_process import (  # noqa: E402
    ChildDiarizationRunner,
    InProcessDiarizationRunner,
)

assert str(settings.app_support_dir()).startswith(HOME)

VOICES = ["Samantha", "Daniel", "Karen", "Fred"]
LINES = [
    "Let's go through the budget for the next quarter before we decide anything.",
    "I think the telescope schedule is the bigger risk, not the money.",
    "Jupiter's moon Europa probably has a salty ocean under its ice shell.",
    "Can we agree to send the draft to the whole team by Friday afternoon?",
    "The spectrometer results came back and the redshift is about zero point three.",
    "I'd rather test it twice than explain a wrong number to the review board.",
]


def footprint_mb() -> float:
    out = subprocess.run(["footprint", "-p", str(os.getpid())],
                         capture_output=True, text=True).stdout
    number, unit = re.search(r"Footprint: ([\d.]+) ([KMG]?B)", out).groups()
    return float(number) * {"B": 1 / 2**20, "KB": 1 / 1024, "MB": 1, "GB": 1024}[unit]


def make_meeting(folder: Path, minutes: float) -> Path:
    clips = []
    for i, line in enumerate(LINES):
        for voice in VOICES:
            aiff, wav = folder / f"{voice}_{i}.aiff", folder / f"{voice}_{i}.wav"
            if not wav.exists():
                subprocess.run(["say", "-v", voice, "-o", str(aiff), line], check=True)
                subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                                str(aiff), str(wav)], check=True)
            with wave.open(str(wav)) as source:
                clips.append(source.readframes(source.getnframes()))
    target = int(minutes * 60 * 16000) * 2
    gap = b"\0" * (int(0.4 * 16000) * 2)
    out = folder / f"meeting_{minutes}min.wav"
    with wave.open(str(out), "wb") as dest:
        dest.setnchannels(1); dest.setsampwidth(2); dest.setframerate(16000)
        written, k = 0, 0
        while written < target:
            block = (clips[k % len(clips)] + gap)[: target - written]
            dest.writeframes(block)
            written += len(block)
            k += 7  # rotate voices so each speaker keeps coming back
    return out


def run(runner, path):
    started = time.perf_counter()
    turns = runner(path, expected_count=None, max_speakers=None, voice_profile_names=(),
                   progress=lambda f: None, cancel=threading.Event())
    return turns, time.perf_counter() - started


def main() -> None:
    folder = Path(tempfile.mkdtemp(prefix="speakeasy-diar-bench-audio-"))
    short, long_ = make_meeting(folder, 3.2), make_meeting(folder, 29.3)
    import numpy  # noqa: F401  (baseline includes what the app always has)
    baseline = footprint_mb()
    print(f"parent baseline: {baseline:.0f} MB")
    child = ChildDiarizationRunner()
    child_turns, child_short_times, growth = {}, [], []
    for n, path in enumerate((short, short, long_, short, short), start=1):
        turns, seconds = run(child, path)
        child_turns[path] = turns
        if path == short:
            child_short_times.append(seconds)
        now = footprint_mb()
        growth.append(now - baseline)
        speakers = len({t.speaker for t in turns})
        print(f"meeting {n} ({path.stem}): {seconds:.1f} s wall, {speakers} speakers, "
              f"parent {now:.0f} MB ({now - baseline:+.0f} MB)")
    reference = InProcessDiarizationRunner()
    in_short_times = []
    for _ in range(2):
        in_turns_short, seconds = run(reference, short)
        in_short_times.append(seconds)
    in_turns_long, in_long_seconds = run(reference, long_)
    overhead = statistics.median(child_short_times) - statistics.median(in_short_times)
    print(f"3.2 min: child median {statistics.median(child_short_times):.1f} s, "
          f"in-process median {statistics.median(in_short_times):.1f} s, "
          f"overhead {overhead:.2f} s; 29.3 min in-process {in_long_seconds:.1f} s")
    same_short = child_turns[short] == in_turns_short
    same_long = child_turns[long_] == in_turns_long
    checks = {
        "parent growth <= 5 MB after every meeting": max(growth) <= 5,
        "child overhead <= 1.5 s (3.2 min, medians)": overhead <= 1.5,
        "child turns == in-process turns (3.2 min)": same_short,
        "child turns == in-process turns (29.3 min)": same_long,
    }
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it** (Bash `timeout: 600000`, or `run_in_background` and wait; it takes ~8 min)

Run: `.venv/bin/python -B scripts/measure_diarization_memory.py`
Expected: four `PASS` lines. Save the full output. **If a check fails, do not loosen the threshold.** Report the numbers to the controller.
- If turns differ, print the first differing turn pair and check whether they differ only in float noise.
- If they do, report it as a spec question, not a fix.

After the run, check that the real library was not touched. This command should show only the installed app's own timestamp changes, if it is running:

```bash
ls -la ~/Library/Application\ Support/Speakeasy/
```

- [ ] **Step 3: Append to `docs/model-memory.md`**

Add a section titled `## Post-meeting CPU heap and the diarization child (30 September 2026)`. Use prose plus tables, in the same style as the existing sections. Include:
- the problem numbers from the spec (Malloc Large 8.6 → 301 MB, Small 260 → 384 MB, 2,314 MB total);
- the spec's keep / release / no-cache / child table, verbatim;
- the macOS large-cache finding: a 400 MB numpy array freed still counted, `MallocLargeCache=0` returned it, and the switch is undocumented;
- that sherpa-onnx exposes no arena setting;
- the child-overhead measurement;
- **this benchmark's actual output, pasted**, plus the command to recheck;
- a closing line: "Installed-app check after this change: pending (step 4 of the plan)."

- [ ] **Step 4: Update `AGENTS.md`**

In the `worker` bullet of the threading model, replace the sentence that starts "Only the track selected for diarization is loaded fully" through "rather than adding an executor." with:

```
  spool. Diarization (diarize → speaker merge → voice identification) runs
  in a spawned child process per meeting (`speakeasy/diarization_process.py`,
  `engine.diarization_runner`); the worker job blocks on it, so the pipeline
  stays sequential. The child reads the spool itself and exits, which returns
  onnxruntime's arenas and freed buffers to macOS; keeping the models in-process
  held 300–800 MB after meetings (docs/model-memory.md). A failed child
  (`DiarizationFailed`) saves the transcript with one speaker label and records
  `diarization_status`/`diarization_failure` in capture health; cancel
  terminates the child.
```

Make sure the sentences before and after still read correctly. In the test-guidance bullet that says "mock the stream / recorder / helper / transcriber / diarizer", add after the existing `speakeasy/diarizer.py` sentence:

```
  Engine meeting tests inject `InProcessDiarizationRunner(fake_diarizer)` as
  `engine.diarization_runner`; child-process tests spawn stub targets from
  `tests/diarization_child_stubs.py` and never load sherpa-onnx.
```

- [ ] **Step 5: Full suite, then commit** (Bash `timeout: 600000`)

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: all PASS.

```bash
git add scripts/measure_diarization_memory.py docs/model-memory.md AGENTS.md
git commit -m "Add diarization memory benchmark and record results"
```

---

## After the tasks (controller)

1. Final whole-branch review on opus, with mutations. At minimum:
   - make `_reap` skip `terminate`;
   - send the exception message instead of the type name;
   - drop `diarization_health` from `capture_health`;
   - make `_diarize_or_fallback` re-raise;
   - remove the cancel check before `ready`.
   The suite must catch each one.
2. Record the task status below, commit the plan.
3. **Stop before step 4.** Step 4 is the installed-app check with the user: `scripts/build_app.sh --install`, then 20 dictations plus one mic-only meeting. Pass/fail is:
   - footprint ≤ 2,000 MB (record Malloc Large and Small);
   - a cancel during "Identifying speakers" stops within about 1 s;
   - dictation inserts in TextEdit, Codex and Teams.
   Merging waits on that.

## Status

- [x] Task 1 (f9529c7) — [x] Task 2 (88fbaa4, 8a4be2a) — [x] Task 3 (fb9977d, 1c3727c) — [x] Task 4 (bb58a75) — [x] Final review + fix wave (eff7c71) — [x] Installed-app check (step 4, 2b9504c; results in docs/model-memory.md) — [x] Merge to master

Merged to master on 30 September 2026; branch and worktree removed.
Full suite at eff7c71: 772 passed. Benchmark at bb58a75: 4/4 PASS (parent 31 MB baseline, 31 MB after meeting 1, 26 MB after meetings 2–5 incl. 29.3 min; child turns identical to in-process).

### Changes agreed during execution (beyond the plan text)

- Worked in a git worktree instead of `git checkout -b` (user's global rule).
- Final review found the child is orphaned when the app quits mid-diarization: quit is `NSApp.terminate_`, a hard C exit, so neither `_reap` nor multiprocessing's atexit cleanup runs (reproduced). Fix: a parent-death watchdog thread in `serve` (`_PARENT_POLL_SECONDS = 0.5`, `os._exit(0)` when `getppid()` changes), with test `test_child_exits_when_parent_dies_abruptly`. The alternative of terminating from `engine.shutdown()` was not added, because the watchdog also covers crashes and SIGKILL.
- The pre-existing flaky `test_meeting_happy_path_records_content_free_phase_timing` was fixed in passing: the engine reaches READY before `timing.emit()`, so the test now waits for the record.

### Open items (TODO)

1. **Done: installed-app check (step 4).** 1,977 MB after 15 dictations + a 3.1-min mic-only meeting (target ≤ 2,000 MB; was 2,314 MB). Malloc Large 38 MB (was 301). Dictation inserted in TextEdit, Codex and Teams. The user stopped before the UI cancel test (the only Cancel is the menu-bar "Cancel Processing"). It was replaced by a real-model dev probe: cancel ≤ 0.08 s at every point. Details in docs/model-memory.md.
2. **Done: watchdog latency.** Behaviour fixed on 30 Sep 2026: a normal quit now kills the child from `Engine.shutdown()` (commits 4ebdbbd, 1f2b7f0; see `docs/superpowers/plans/2026-09-30-quit-kills-child-and-dock-cancel.md`). The watchdog remains as the backstop for crashes and force-quits. Original finding: The dev probe shows sherpa-onnx segmentation holds the GIL with no progress callbacks (~58 s on a 29.3-min meeting). A child orphaned then lingers until the first callback, then exits in ~0.4 s. AGENTS.md and the `serve` docstring are corrected. Follow-up (now done): terminate the child from the app's quit path (`engine.shutdown`), so a normal quit does not leave it running for up to a minute or more. Planned in `2026-09-30-quit-kills-child-and-dock-cancel.md`.
3. **Malloc Small growth.** In the step-4 check it rose 106 MB (226 to 332 MB) with no diarizer in the parent, so it comes from dictation/meeting transcription. Not broken down; open.
4. **Deferred minors from the task reviews:**
   - the in-process runner's cancel wiring is untested;
   - `FakeProfiles` ignores its turns argument;
   - the drain-guard branch cannot be tested with real spawns;
   - the <3 s and <5 s time bounds in the child tests;
   - `serve` catches only `Exception`;
   - `_diarize_or_fallback` has no type hints;
   - the benchmark's median-of-two includes the cold sherpa import.
   The final review triaged all of these as leave.
5. **Done: Cancel in the main window** (30 Sep 2026, commit 4f4c41a; see `docs/superpowers/plans/2026-09-30-quit-kills-child-and-dock-cancel.md`). Original finding: No Cancel in the main window. Cancelling meeting processing exists only as "Cancel Processing" in the menu-bar icon's menu, shown only while processing. The user looked for it in the main window and could not find it. UI follow-up; not part of this branch. Planned (two-step inline confirm) in `2026-09-30-quit-kills-child-and-dock-cancel.md`.
