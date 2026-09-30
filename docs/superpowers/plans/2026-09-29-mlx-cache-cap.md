# MLX Buffer-Cache Cap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop Speakeasy's memory footprint growing to multiple GB by capping MLX's
Metal buffer cache, with no change to transcription output or speed.

**Architecture:** One call, `mx.set_cache_limit(config.MLX_CACHE_LIMIT_BYTES)`, made in
`Transcriber.__init__` on the worker thread before the model loads. The cap is
process-wide, so every later MLX operation (dictation, streaming, meetings,
diagnostics) is covered. A committed measurement script and a doc record the
before/after numbers so anyone can recheck them.

**Tech Stack:** Python 3.11, mlx 0.31.2, parakeet-mlx 0.5.2, pytest.

**Spec:** This plan. The spec is the measurement in "Background" below, agreed in chat on
29 September 2026.

## Background (measured 29 Sep 2026, bundled model, mlx 0.31.2)

The user asked for the model to move to 16-bit. It is already 16-bit:
`parakeet_mlx.from_pretrained` defaults to `dtype=mx.bfloat16` and converts the 32-bit
file (`Contents/Resources/model/model.safetensors`, 2.3 GB) while it loads.
The live app measured 2,615 MB footprint: 2,189 MB "IOAccelerator (graphics)" = 1,218 MB
bf16 weights + MLX's **buffer cache** (freed Metal buffers MLX keeps for reuse; no
limit by default).

Standalone benchmark: 8 macOS `say` sentences, each clean / 10 dB SNR / 0 dB SNR white
noise, plus one 2.8-minute clip in 120 s chunks with 15 s overlap:

| Variant | Weights | Footprint after 24 clips | After 2.8 min audio | Short-clip time (8 clips) | Long clip |
|---|---|---|---|---|---|
| fp32 (no cast) | 2,396 MB | 3,452 MB | 7,757 MB | 1.16 s | 4.32 s |
| bf16 (today's app) | 1,218 MB | 2,792 MB | **7,085 MB** | 1.36 s | 5.11 s |
| fp16 | 1,218 MB | 2,791 MB | 7,085 MB | 1.38 s | 4.68 s |
| **bf16 + 256 MB cache cap** | 1,218 MB | **1,629 MB** | **1,598 MB** | 1.38 s | 3.89 s |

Accuracy (word error rate vs the script; clean errors are only formatting such as
"3:30", "300,000", "kilometers", so true clean WER ≈ 0):

| Set | fp32 | bf16 | bf16 + cap | fp16 |
|---|---|---|---|---|
| clean | 5.7% | 5.7% | 5.7% | 5.7% |
| 10 dB noise | 5.7% | 5.7% | 5.7% | 5.7% |
| 0 dB noise | 17.1% | 16.4% | 16.4% | 17.1% |
| long 2.8 min | 5.7% | 5.7% | 5.7% | 5.7% |

The cap changed **no transcript** (all 25 identical to uncapped bf16). bf16 vs fp32 differed
on one 0 dB clip only. Timing differences are within run-to-run noise
(single runs; not repeated). Conclusion: keep bf16, add the cap. fp16 offers nothing
over bf16.

## Global Constraints

- Offline only; no new dependencies (AGENTS.md).
- MLX work stays on the `worker` thread; `set_cache_limit` is called from
  `Transcriber.__init__`, which already runs there.
- `--mcp` stays import-light: do not import MLX from `config.py` or anything `--mcp` loads.
- Tests must not load the real model (mock `from_pretrained`, as `tests/test_transcriber_warmup.py` does).
- Cap value: `MLX_CACHE_LIMIT_BYTES = 256 * 1024 * 1024` (256 MiB).
- Full suite: `.venv/bin/python -m pytest` with a Bash timeout of 600000 ms (388+ tests).
- Mutation checks: run with `python -B` and clear `__pycache__` first (memory note).

## Review Focus

1. **Cap set after load** — if the call moves below `from_pretrained`, the load's leftover
   ~560 MB stays cached until the next free; test asserts ordering.
2. **Cap silently dropped in a refactor** — test asserts the exact byte value is passed.
3. **Meeting peak is not capped** — the cap limits *idle cache*, not the transient
   working set (active peak 3,082 MB during a 120 s chunk). Expected; documented, not a bug.
4. **Diagnostic/probe paths** (`dictation_diagnostic.py`, `inference_probe.py`) share the
   process and get the cap for free; no separate test needed.
5. **Installed app differs from venv** — PyInstaller bundle must show the drop too;
   Task 3 measures the installed app, not only the venv.

---

### Task 1: Cap MLX's buffer cache at model load

**Files:**
- Modify: `speakeasy/config.py` (after `MEETING_OVERLAP_SECONDS`, ~line 95)
- Modify: `speakeasy/transcriber.py:144-148` (`Transcriber.__init__`)
- Test: `tests/test_transcriber_warmup.py`
- Modify: `AGENTS.md` (Threading model section, the `worker` bullet)

**Interfaces:**
- Produces: `config.MLX_CACHE_LIMIT_BYTES: int` (268435456).

- [x] **Step 1: Write the failing test** (append to `tests/test_transcriber_warmup.py`)

```python
def test_constructor_caps_mlx_cache_before_loading_model(monkeypatch):
    events = []
    monkeypatch.setattr(
        "speakeasy.transcriber.mx.set_cache_limit",
        lambda limit: events.append(("cap", limit)) or 0,
    )
    monkeypatch.setattr(
        "speakeasy.transcriber.from_pretrained",
        lambda source: events.append(("load",)) or object(),
    )
    monkeypatch.setattr(Transcriber, "transcribe", lambda self, audio: "")
    monkeypatch.setattr(
        Transcriber,
        "transcribe_stream",
        lambda self, session: StreamResult(StreamStatus.COMPLETE, text=""),
    )

    Transcriber()

    assert config.MLX_CACHE_LIMIT_BYTES == 256 * 1024 * 1024
    assert events[:2] == [("cap", config.MLX_CACHE_LIMIT_BYTES), ("load",)]
```

- [x] **Step 2: Run it and confirm it fails**

Run: `.venv/bin/python -B -m pytest tests/test_transcriber_warmup.py -v`
Expected: FAIL — `AttributeError: module 'speakeasy.config' has no attribute 'MLX_CACHE_LIMIT_BYTES'`.

- [x] **Step 3: Add the config constant** (`speakeasy/config.py`, after `MEETING_OVERLAP_SECONDS`)

```python
# --- Model memory --------------------------------------------------------------
# MLX keeps freed Metal buffers in a reuse cache with no limit by default; after
# one 2.8-minute transcription it held ~5.7 GB on top of the 1.2 GB bf16 model.
# 256 MiB keeps the footprint ~1.6 GB with identical transcripts and no
# measurable slowdown (docs/model-memory.md).
MLX_CACHE_LIMIT_BYTES = 256 * 1024 * 1024
```

- [x] **Step 4: Set the cap in `Transcriber.__init__`** (`speakeasy/transcriber.py`)

Replace:

```python
        source = settings.model_path()
        print(f"Loading speech model from {source}")
        started = time.perf_counter_ns()
```

with:

```python
        source = settings.model_path()
        # Process-wide; set before loading so the fp32→bf16 cast's freed
        # buffers are trimmed too. See config.MLX_CACHE_LIMIT_BYTES.
        mx.set_cache_limit(config.MLX_CACHE_LIMIT_BYTES)
        print(f"Loading speech model from {source}")
        started = time.perf_counter_ns()
```

- [x] **Step 5: Run the file's tests; confirm both pass**

Run: `.venv/bin/python -B -m pytest tests/test_transcriber_warmup.py -v`
Expected: 2 passed.

- [x] **Step 6: Mutation check** (clear `__pycache__` first; `-B` on every run)
  1. Move the `mx.set_cache_limit` line below `self._model = from_pretrained(source)` → test must FAIL.
  2. Change the constant to `512 * 1024 * 1024` → test must FAIL.
  3. Delete the `set_cache_limit` line → test must FAIL.
  Restore after each; record the three results in the commit message.

- [x] **Step 7: Document in AGENTS.md** — append to the `worker` bullet in "Threading model":

```markdown
  `Transcriber.__init__` also caps MLX's process-wide buffer cache
  (`config.MLX_CACHE_LIMIT_BYTES`, 256 MiB) before loading; without it the
  idle footprint grows by GBs after meetings. Don't remove it
  (docs/model-memory.md).
```

- [x] **Step 8: Full suite**

Run: `.venv/bin/python -m pytest` (Bash timeout 600000)
Expected: all pass (389 or more).

- [x] **Step 9: Commit**

```bash
git add speakeasy/config.py speakeasy/transcriber.py tests/test_transcriber_warmup.py AGENTS.md
git commit -m "Cap MLX's buffer cache at 256 MiB so idle memory stays ~1.6 GB"
```

---

### Task 2: Committed memory benchmark and results doc

**Files:**
- Create: `scripts/measure_model_memory.py`
- Create: `docs/model-memory.md`

**Interfaces:**
- Consumes: `config.MLX_CACHE_LIMIT_BYTES` (Task 1).
- Produces: `scripts/measure_model_memory.py [--no-cap]` printing one line per stage.

The script uses only synthetic `say` audio in a temp directory and the bundled model;
it never reads `~/Library/Application Support/Speakeasy`.

- [x] **Step 1: Write `scripts/measure_model_memory.py`**

```python
"""Measure the speech model's memory with and without the MLX cache cap.

Synthetic audio only (macOS `say`), bundled model only; never reads user data.
Usage: .venv/bin/python -B scripts/measure_model_memory.py [--no-cap]
"""
import os
import re
import subprocess
import sys
import tempfile
import time
import wave

import mlx.core as mx
from parakeet_mlx import from_pretrained

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from speakeasy import config  # noqa: E402

MODEL = "/Applications/Speakeasy.app/Contents/Resources/model"
SENTENCES = [
    "The quick brown fox jumps over the lazy dog while the committee reviews the quarterly budget.",
    "Jupiter is the largest planet in our solar system, and its great red spot is a storm bigger than Earth.",
    "Neutron stars are so dense that a teaspoon of their material would weigh about a billion tonnes on Earth.",
    "Remember to buy milk, eggs, bread and some fresh strawberries on the way home tonight.",
]


def footprint_mb() -> str:
    out = subprocess.run(["footprint", "-p", str(os.getpid())],
                         capture_output=True, text=True).stdout
    return re.search(r"Footprint: ([\d.]+ [KMG]B)", out).group(1)


def report(stage: str) -> None:
    mb = lambda b: round(b / 2**20)
    print(f"{stage:18s} weights+live={mb(mx.get_active_memory())} MB "
          f"cache={mb(mx.get_cache_memory())} MB footprint={footprint_mb()}")


def make_audio(folder: str) -> tuple[list[str], str]:
    clips = []
    for i, text in enumerate(SENTENCES):
        aiff, wav = f"{folder}/s{i}.aiff", f"{folder}/s{i}.wav"
        subprocess.run(["say", "-o", aiff, text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                        aiff, wav], check=True)
        clips.append(wav)
    long = f"{folder}/long.wav"
    frames = b"".join(wave.open(c).readframes(10**8) + b"\0" * 32000
                      for c in clips * 8)
    with wave.open(long, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(frames)
    return clips, long


def main() -> None:
    if "--no-cap" not in sys.argv:
        mx.set_cache_limit(config.MLX_CACHE_LIMIT_BYTES)
    print("cache cap:", "off" if "--no-cap" in sys.argv else
          f"{config.MLX_CACHE_LIMIT_BYTES // 2**20} MiB")
    with tempfile.TemporaryDirectory() as folder:
        clips, long = make_audio(folder)
        model = from_pretrained(MODEL)
        report("after load")
        for clip in clips:
            model.transcribe(clip)
        report("after 4 clips")
        started = time.perf_counter()
        model.transcribe(long, chunk_duration=config.MEETING_CHUNK_SECONDS,
                         overlap_duration=config.MEETING_OVERLAP_SECONDS)
        report(f"after long ({time.perf_counter() - started:.1f}s)")


if __name__ == "__main__":
    main()
```

- [x] **Step 2: Run both modes and keep the output**

Run: `.venv/bin/python -B scripts/measure_model_memory.py --no-cap` then without `--no-cap` (Bash timeout 300000 each).
Expected: uncapped footprint after long ≥ 4 GB; capped ≤ 2 GB. If capped is above
2 GB, stop and report — do not tune the number without the user.

- [x] **Step 3: Write `docs/model-memory.md`** containing: the two Background tables
  from this plan (copied verbatim), the Step 2 output of both runs with the date, the
  sentence "The model is already bfloat16 in memory; `from_pretrained` casts the 32-bit
  file at load", the caveat that the cap does not limit the transient working set
  during a 120 s meeting chunk (active peak ~3.1 GB), and a "How to recheck" line with
  the Step 2 commands.

- [x] **Step 4: Commit**

```bash
git add scripts/measure_model_memory.py docs/model-memory.md
git commit -m "Add model memory benchmark and record cache-cap results"
```

---

### Task 3: Installed-app check (controller, not a subagent)

Runs the real installed app, so the controller does it with the user; subagents must
not run app code on real App Support data.

- [x] **Step 1: Build and install**

Run: `scripts/build_app.sh --install` (Bash timeout 600000).

- [x] **Step 2: Quit and relaunch Speakeasy; after it shows Ready, measure idle**

Run: `footprint -p $(pgrep -x Speakeasy | head -1) | sed -n 2,8p`
Expected: total ≤ 1,800 MB (was 2,615 MB); IOAccelerator ≤ 1,500 MB.

- [x] **Step 3: User dictates 5 times into TextEdit, then Codex, then Teams**
  (CLAUDE.md insertion rule: all three must still insert). Measure again.
  Expected: total ≤ 1,900 MB; every dictation inserted correctly.

- [x] **Step 4: User records or imports one meeting of ≥ 3 minutes; after processing ends, measure again.**
  Expected: total ≤ 2,000 MB (uncapped would be ~7 GB); transcript looks normal.

- [x] **Step 5: Append the three measurements (date, build commit, numbers) to
  `docs/model-memory.md` under "Installed app", and commit**

```bash
git add docs/model-memory.md
git commit -m "Record installed-app memory check for the MLX cache cap"
```

## Out of scope (possible follow-up)

- Ship the model file pre-converted to bf16: bundle 2.3 GB → ~1.2 GB and slightly faster
  load, with identical weights (the in-memory cast is the same). Runtime memory with the
  cap would barely change, so it's a disk and install-size win only.
- Duplicate Claude connectors ("speakeasy" and "Speakeasy Meetings") start two MCP
  processes per Claude session, ~17 MB each; that's Claude-side config, not app code.

## Status (30 September 2026)

- Task 1 done (6aee378), Task 2 done (3b1e4d3, final-review fixes 7d72ebf), Task 3 done (e7f2461).
- Installed-app result: idle 1,601 MB (target met); after a meeting 2,314 MB (target ≤ 2,000 MB **missed**).
  MLX/GPU part held at weights + ~256 MiB; the excess is ~420 MB CPU heap after the meeting.
- **Open follow-up:** find what holds the post-meeting CPU heap (suspect: the sherpa-onnx diarizer kept
  in `engine.diarizer`, `speakeasy/engine.py:1067-1071`), and whether it plateaus or grows per meeting.
- Deferred and left by ruling: existing warm-up test calls the real `mx.set_cache_limit`; the config
  block sits between meeting constants.
- Not done (optional): ship the model file pre-converted to bf16 (disk/install size only).
