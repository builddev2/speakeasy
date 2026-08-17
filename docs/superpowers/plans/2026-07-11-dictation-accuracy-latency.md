# Dictation Latency & Accuracy Pass — Implementation Plan

> **Historical implementation record — completed.** Silence trimming,
> phonetic/fuzzy vocabulary correction, their tests, and the benchmark harness
> are implemented. Long-form meeting ASR has since gained the streaming
> `transcribe_long_wav()` path while retaining the shared array API described
> below. Preserve this plan's checkboxes/code as its execution snapshot; use
> `README.md`, `AGENTS.md`, and current source for present behavior.
> Immediate dictation has since gained a dedicated microphone helper and a
> bounded Parakeet streaming fast path. It pastes only the final result and
> batch-falls back from complete captured audio on the same MLX worker. The
> original trim benchmark below is not the current release-to-paste comparison;
> see `speakeasy/dictation_benchmark.py` and the README commands instead.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut dictation latency and improve dictation + meeting accuracy by trimming silence before inference and activating the currently-unused per-profile vocabulary via a conservative two-gate fuzzy correction.

**Architecture:** Two independent, pure-logic components. (1) `preprocess.trim_silence()` runs at the top of `Transcriber.transcribe()` on the worker thread — fewer mel frames and tighter per-feature normalization stats. (2) `Profile.apply()` gains a second pass that snaps transcript word tokens to vocabulary words when they clear both a phonetic-key gate (`phonetics.phonetic_key`) and a tight `difflib` edit-ratio gate. The meeting pipeline already calls `Profile.apply()`, so component 2 improves meetings for free. No model changes.

**Tech Stack:** Python 3.11, NumPy, MLX/parakeet-mlx (unchanged), stdlib `difflib` + `re`. Tests are pure pytest — no mic, model, or sherpa-onnx.

## Global Constraints

- **Fully offline, always** — no network calls at runtime; no new runtime dependencies. `difflib` is stdlib; the phonetic key is vendored pure Python.
- **Apple Silicon only** (MLX) — unchanged; not touched by this work.
- **Threading:** the model is loaded and used only on the `worker` thread. `trim_silence()` is pure NumPy and runs inside `transcribe()`, i.e. already on `worker` — introduce no new threads.
- **Tests must not touch the real mic, model, or sherpa-onnx** — all tasks here are pure-logic and satisfy this by construction.
- **Use `.venv/bin/python`**, not system python.
- Run the full suite with `.venv/bin/python -m pytest`.

---

### Task 1: Silence trimming (`preprocess.trim_silence`)

**Files:**
- Create: `speakeasy/preprocess.py`
- Modify: `speakeasy/config.py` (append trim knobs)
- Test: `tests/test_preprocess.py`

**Interfaces:**
- Consumes: `config.SAMPLE_RATE` (existing, `16_000`).
- Produces: `preprocess.trim_silence(audio: np.ndarray, sample_rate: int) -> np.ndarray` — returns a mono float32 array; trims leading/trailing silence with an 80 ms margin; returns the input **unchanged** if no frame clears the threshold. Also `config.DICTATION_TRIM_ENABLED: bool`, `config.TRIM_THRESHOLD_RATIO: float`, `config.TRIM_ABSOLUTE_FLOOR: float`, `config.TRIM_MARGIN_SECONDS: float`.

- [ ] **Step 1: Add config knobs**

Append to `speakeasy/config.py` (after the `RECORDER_STOP_TIMEOUT_SECONDS` block, before the sounds section):

```python
# --- Dictation silence trimming ---------------------------------------------
# Trim leading/trailing silence from a dictation clip before inference. Fewer
# mel frames = faster; it also tightens Parakeet's per-feature normalization
# (mean/std are taken over the clip's time axis, so lead/tail silence skews the
# stats for a short utterance). Meetings are NOT trimmed — see transcriber.py.
DICTATION_TRIM_ENABLED = True
# A frame counts as speech when its RMS exceeds max(peak_rms * ratio, floor).
TRIM_THRESHOLD_RATIO = 0.06     # ~ -24 dB below the loudest frame
TRIM_ABSOLUTE_FLOOR = 0.005     # RMS below this is treated as silence outright
TRIM_MARGIN_SECONDS = 0.08      # keep 80 ms each side so onsets aren't clipped
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_preprocess.py`:

```python
"""Unit tests for silence trimming (preprocess.py) — pure, no hardware."""

import numpy as np

from speakeasy import config
from speakeasy.preprocess import trim_silence

SR = config.SAMPLE_RATE


def _tone(seconds: float, amp: float = 0.5, freq: float = 220.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.float32)


def test_all_silence_returned_unchanged():
    audio = _silence(1.0)
    out = trim_silence(audio, SR)
    assert np.array_equal(out, audio)


def test_leading_and_trailing_silence_removed():
    audio = np.concatenate([_silence(0.5), _tone(0.5), _silence(0.5)])
    out = trim_silence(audio, SR)
    # 0.5 s speech + up to 2 * 80 ms margin, well under the 1.5 s original.
    assert len(out) < len(audio)
    assert len(out) <= int((0.5 + 2 * config.TRIM_MARGIN_SECONDS) * SR) + SR // 100


def test_margin_preserves_onset():
    # Speech starts at 0.5 s; trimmed clip must retain samples before the onset.
    audio = np.concatenate([_silence(0.5), _tone(0.3)])
    out = trim_silence(audio, SR)
    # Some leading samples survive (the 80 ms margin), so it's not cut flush.
    assert len(out) >= int((0.3 + config.TRIM_MARGIN_SECONDS) * SR) - SR // 100


def test_no_silence_is_near_unchanged():
    audio = _tone(0.6)
    out = trim_silence(audio, SR)
    assert abs(len(out) - len(audio)) <= int(2 * config.TRIM_MARGIN_SECONDS * SR) + SR // 100


def test_interior_silence_between_words_is_kept():
    # word - gap - word: the gap is interior, only outer edges get trimmed.
    audio = np.concatenate([_tone(0.3), _silence(0.4), _tone(0.3)])
    out = trim_silence(audio, SR)
    assert len(out) >= int(1.0 * SR) - int(2 * config.TRIM_MARGIN_SECONDS * SR)


def test_empty_input_returns_empty():
    out = trim_silence(np.empty(0, dtype=np.float32), SR)
    assert len(out) == 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_preprocess.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'speakeasy.preprocess'`.

- [ ] **Step 4: Implement `trim_silence`**

Create `speakeasy/preprocess.py`:

```python
"""Audio preprocessing for the dictation path (pure NumPy, no hardware).

trim_silence() removes leading/trailing silence from a held-to-talk clip
before it reaches the model. Two payoffs: fewer mel frames through the encoder
(latency), and tighter per-feature normalization — Parakeet standardizes each
mel bin over the clip's time axis, so lead/tail silence skews the mean/std for
a short utterance. Interior silence is left alone; only the outer edges move.

Only dictation calls this. Meetings must not: trimming would shift transcript
timestamps out of sync with the diarization turns they're aligned against.
"""

import numpy as np

from . import config

# 25 ms analysis frames, 10 ms hop — standard short-time energy framing.
_FRAME_SECONDS = 0.025
_HOP_SECONDS = 0.010


def trim_silence(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    """Return `audio` with leading/trailing silence trimmed (80 ms margin).

    Returns the input unchanged when trimming is disabled, the clip is too
    short to frame, or no frame clears the speech threshold (e.g. pure
    silence) — never returns an empty array for a non-empty input.
    """
    if not config.DICTATION_TRIM_ENABLED or len(audio) == 0:
        return audio

    frame = int(_FRAME_SECONDS * sample_rate)
    hop = int(_HOP_SECONDS * sample_rate)
    if len(audio) < frame:
        return audio  # too short to analyse; leave it alone

    # Per-frame RMS via a strided view over the signal.
    starts = np.arange(0, len(audio) - frame + 1, hop)
    frames = np.stack([audio[s : s + frame] for s in starts])
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))

    threshold = max(rms.max() * config.TRIM_THRESHOLD_RATIO, config.TRIM_ABSOLUTE_FLOOR)
    voiced = np.nonzero(rms >= threshold)[0]
    if len(voiced) == 0:
        return audio  # all silence / below floor — let downstream drop it

    margin = int(config.TRIM_MARGIN_SECONDS * sample_rate)
    start = max(0, starts[voiced[0]] - margin)
    end = min(len(audio), starts[voiced[-1]] + frame + margin)
    return audio[start:end]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_preprocess.py -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Commit**

```bash
git add speakeasy/preprocess.py speakeasy/config.py tests/test_preprocess.py
git commit -m "Add silence trimming for the dictation path

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Wire trimming into `Transcriber.transcribe()`

**Files:**
- Modify: `speakeasy/transcriber.py` (import + one call in `transcribe`)

**Interfaces:**
- Consumes: `preprocess.trim_silence` (Task 1).
- Produces: no new API. `transcribe()` trims before `get_logmel`; `transcribe_long()` is deliberately left untouched.

**Note on testing:** `Transcriber` loads the real MLX model in `__init__`, so it is not unit-tested in this repo (consistent with the existing test suite, which has no `test_transcriber.py`). This wiring is verified by the benchmark harness (Task 5) and manual dictation. Keep the change to the single documented call.

- [ ] **Step 1: Add the import**

In `speakeasy/transcriber.py`, extend the local package import near the top (currently `from . import config, settings`):

```python
from . import config, preprocess, settings
```

- [ ] **Step 2: Call `trim_silence` at the top of `transcribe`**

Change `Transcriber.transcribe` (currently):

```python
    def transcribe(self, audio: np.ndarray) -> str:
        # Feed the buffer to the model in-memory — the temp-WAV + ffmpeg
        # round-trip of model.transcribe(path) costs ~100 ms per dictation.
        mel = get_logmel(mx.array(audio), self._model.preprocessor_config)
```

to:

```python
    def transcribe(self, audio: np.ndarray) -> str:
        # Drop lead/tail silence first: fewer mel frames (latency) and a
        # tighter per-feature norm (accuracy). Meetings intentionally skip
        # this — see transcribe_long — to keep timestamps aligned with
        # diarization.
        audio = preprocess.trim_silence(audio, config.SAMPLE_RATE)
        # Feed the buffer to the model in-memory — the temp-WAV + ffmpeg
        # round-trip of model.transcribe(path) costs ~100 ms per dictation.
        mel = get_logmel(mx.array(audio), self._model.preprocessor_config)
```

- [ ] **Step 3: Verify the suite still imports and passes**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — no test regressions. (This confirms the import wiring; runtime behavior is exercised in Task 5.)

- [ ] **Step 4: Commit**

```bash
git add speakeasy/transcriber.py
git commit -m "Trim dictation silence before inference in transcribe()

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Phonetic key (`phonetics.phonetic_key`)

**Files:**
- Create: `speakeasy/phonetics.py`
- Test: `tests/test_phonetics.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `phonetics.phonetic_key(word: str) -> str` — a Soundex-style consonant-group key (first letter preserved, vowels dropped, consecutive equal groups collapsed, no length truncation). Empty string for a word with no letters.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_phonetics.py`:

```python
"""Unit tests for the phonetic key (phonetics.py) — pure, no hardware."""

from speakeasy.phonetics import phonetic_key


def test_sound_alikes_collide():
    assert phonetic_key("clod") == phonetic_key("Claude") == "C43"


def test_near_spellings_collide():
    assert phonetic_key("kubernetis") == phonetic_key("Kubernetes") == "K16532"


def test_anthropic_misspelling_collides():
    assert phonetic_key("anthropik") == phonetic_key("Anthropic") == "A53612"


def test_unrelated_words_differ():
    assert phonetic_key("cat") != phonetic_key("Claude")


def test_double_letters_collapse():
    assert phonetic_key("ll") == phonetic_key("l") == "L"


def test_no_letters_is_empty():
    assert phonetic_key("123!") == ""
    assert phonetic_key("") == ""


def test_case_insensitive():
    assert phonetic_key("CLAUDE") == phonetic_key("claude")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_phonetics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'speakeasy.phonetics'`.

- [ ] **Step 3: Implement `phonetic_key`**

Create `speakeasy/phonetics.py`:

```python
"""A small vendored phonetic key for fuzzy vocabulary matching (offline, pure).

Soundex-style consonant grouping, but without Soundex's fixed 4-char width —
longer words keep more signal, which matters for technical vocabulary. The
first letter is preserved; vowels drop out and act as separators; consecutive
identical consonant groups collapse. Sound-alikes and near-spellings map to the
same key ("clod"/"Claude" -> "C43"), which gates the fuzzy snap in profiles.py.
"""

# Soundex digit groups for consonants; vowels and h/w/y map to "" (separators).
_CODES = {
    **dict.fromkeys("bfpv", "1"),
    **dict.fromkeys("cgjkqsxz", "2"),
    **dict.fromkeys("dt", "3"),
    "l": "4",
    **dict.fromkeys("mn", "5"),
    "r": "6",
}


def phonetic_key(word: str) -> str:
    """Return a Soundex-style phonetic key, or "" if the word has no letters."""
    letters = [c for c in word.lower() if c.isalpha()]
    if not letters:
        return ""
    key = letters[0].upper()
    prev = _CODES.get(letters[0], "")
    for c in letters[1:]:
        code = _CODES.get(c, "")
        if code and code != prev:
            key += code
        prev = code  # vowels reset prev to "" so they separate equal groups
    return key
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_phonetics.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add speakeasy/phonetics.py tests/test_phonetics.py
git commit -m "Add vendored phonetic key for fuzzy vocabulary matching

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Activate `vocabulary` via two-gate fuzzy snap in `Profile`

**Files:**
- Modify: `speakeasy/config.py` (append fuzzy knobs)
- Modify: `speakeasy/profiles.py` (`__init__`/`_rebuild`/`apply` + import)
- Test: `tests/test_profiles.py` (extend)

**Interfaces:**
- Consumes: `phonetics.phonetic_key` (Task 3); existing `Profile.vocabulary`, `Profile.corrections`, `normalize()`.
- Produces: `Profile.apply()` now runs a second pass that snaps single word tokens to a vocabulary word when both gates pass. New config: `config.FUZZY_VOCAB_ENABLED: bool`, `config.FUZZY_MIN_RATIO: float`, `config.FUZZY_MIN_TOKEN_LEN: int`. New internal state built in `_rebuild()`: `self._vocab_index: dict[str, list[str]]` (phonetic key -> vocab words) and `self._protected: set[str]` (normalized vocab words + correction targets).

- [ ] **Step 1: Add config knobs**

Append to `speakeasy/config.py` (after the trim block from Task 1):

```python
# --- Fuzzy vocabulary correction --------------------------------------------
# The per-profile `vocabulary` list snaps near-miss transcript words onto the
# intended word, but only when BOTH gates agree (high precision): a phonetic
# key match AND a tight edit-distance ratio. Catches model near-spellings of
# words the user added but never trained ("kubernetis" -> "Kubernetes"); pure
# homophones with little letter overlap ("clod"->"Claude") do NOT snap and are
# left to the exact `corrections` layer.
FUZZY_VOCAB_ENABLED = True
FUZZY_MIN_RATIO = 0.8        # difflib SequenceMatcher ratio floor to snap
FUZZY_MIN_TOKEN_LEN = 3      # ignore very short tokens (too collision-prone)
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_profiles.py`:

```python
# -- fuzzy vocabulary snapping ----------------------------------------------


def test_fuzzy_snaps_near_spelling_to_vocab_word():
    p = Profile("t", vocabulary=["Kubernetes"])
    assert p.apply("we deployed kubernetis today") == "we deployed Kubernetes today"


def test_fuzzy_snaps_misspelled_proper_noun():
    p = Profile("t", vocabulary=["Anthropic"])
    assert p.apply("i work at anthropik") == "i work at Anthropic"


def test_fuzzy_exact_vocab_word_left_as_written():
    # An exact (case-insensitive) vocab match is protected: a common-word
    # homograph like the fruit "apple" is NOT clobbered to the vocab "Apple".
    p = Profile("t", vocabulary=["Apple"])
    assert p.apply("i ate an apple") == "i ate an apple"


def test_fuzzy_leaves_unrelated_word_alone():
    p = Profile("t", vocabulary=["Claude"])
    assert p.apply("the cat sat") == "the cat sat"


def test_fuzzy_low_overlap_homophone_does_not_snap():
    # Passes the phonetic gate but fails the ratio gate — left for corrections.
    p = Profile("t", vocabulary=["Claude"])
    assert p.apply("a big clod of dirt") == "a big clod of dirt"


def test_fuzzy_preserves_surrounding_punctuation():
    p = Profile("t", vocabulary=["Kubernetes"])
    assert p.apply("(kubernetis).") == "(Kubernetes)."


def test_fuzzy_ignores_short_tokens():
    p = Profile("t", vocabulary=["Go"])
    # "go" is below FUZZY_MIN_TOKEN_LEN, so it is never touched.
    assert p.apply("we go now") == "we go now"


def test_corrections_run_before_fuzzy():
    # Exact correction wins; fuzzy does not re-touch the produced word.
    p = Profile("t", vocabulary=["Claude"], corrections={"clod": "Claude"})
    assert p.apply("hey clod") == "hey Claude"


def test_fuzzy_does_not_touch_correction_targets():
    # "Claude" is a correction target and a vocab word; a correct occurrence
    # is left exactly as written (no spurious re-snap / casing churn).
    p = Profile("t", vocabulary=["Claude"], corrections={"clawed": "Claude"})
    assert p.apply("Claude is here") == "Claude is here"


def test_fuzzy_disabled_is_noop(monkeypatch):
    from speakeasy import config
    monkeypatch.setattr(config, "FUZZY_VOCAB_ENABLED", False)
    p = Profile("t", vocabulary=["Kubernetes"])
    assert p.apply("kubernetis") == "kubernetis"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_profiles.py -k fuzzy -v`
Expected: FAIL — the fuzzy pass does not exist yet (e.g. `kubernetis` is returned unchanged).

- [ ] **Step 4: Implement the fuzzy pass in `profiles.py`**

In `speakeasy/profiles.py`, update the imports at the top:

```python
import json
import os
import re
from datetime import datetime
from difflib import SequenceMatcher

from . import config, settings
from .phonetics import phonetic_key
```

Add a module-level token pattern near the existing `_PUNCT_RE` (after line ~29):

```python
# Word tokens for the fuzzy pass: letters and apostrophes, matched in isolation
# so surrounding punctuation/whitespace is preserved by re.sub.
_WORD_RE = re.compile(r"[A-Za-z']+")
```

Replace `_rebuild` (currently only builds the corrections regex) so it also
builds the vocabulary index and the protected set:

```python
    def _rebuild(self) -> None:
        # Exact corrections regex (longest-first so phrases beat sub-words).
        if self.corrections:
            keys = sorted(self.corrections, key=len, reverse=True)
            alternation = "|".join(re.escape(k) for k in keys)
            self._pattern = re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)
        else:
            self._pattern = None
        # Fuzzy vocabulary index: phonetic key -> intended words. Only
        # single-token vocab entries participate (v1); multi-word phrases are a
        # documented follow-up. Words never re-snapped: vocab words themselves
        # (matched case-insensitively) and correction targets.
        self._vocab_index: dict[str, list[str]] = {}
        for word in self.vocabulary:
            if " " in word.strip():
                continue
            self._vocab_index.setdefault(phonetic_key(word), []).append(word)
        self._protected = {normalize(w) for w in self.vocabulary}
        self._protected |= {normalize(v) for v in self.corrections.values()}
```

Add a helper and extend `apply`:

```python
    def _snap_token(self, token: str) -> str:
        """Snap one word token to a vocab word when both gates pass, else return
        it unchanged."""
        low = token.lower()
        if len(low) < config.FUZZY_MIN_TOKEN_LEN or normalize(token) in self._protected:
            return token
        candidates = self._vocab_index.get(phonetic_key(token))
        if not candidates:
            return token
        best, best_ratio = None, config.FUZZY_MIN_RATIO
        for word in candidates:
            ratio = SequenceMatcher(None, low, word.lower()).ratio()
            if ratio >= best_ratio:
                best, best_ratio = word, ratio
        return best if best is not None else token

    def apply(self, text: str) -> str:
        """Rewrite learned misrecognitions, then snap vocabulary near-misses."""
        if self._pattern is not None:
            text = self._pattern.sub(
                lambda m: self.corrections[normalize(m.group(0))], text
            )
        if config.FUZZY_VOCAB_ENABLED and self._vocab_index:
            text = _WORD_RE.sub(lambda m: self._snap_token(m.group(0)), text)
        return text
```

Note: the old `apply` body (the corrections-only version) is fully replaced by
the block above; `_rebuild`'s old corrections-only body is likewise replaced.

- [ ] **Step 5: Run the fuzzy tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_profiles.py -k fuzzy -v`
Expected: PASS (10 tests).

- [ ] **Step 6: Run the full profiles suite (guard against regressions)**

Run: `.venv/bin/python -m pytest tests/test_profiles.py -v`
Expected: PASS — existing correction/normalize tests still green.

- [ ] **Step 7: Commit**

```bash
git add speakeasy/config.py speakeasy/profiles.py tests/test_profiles.py
git commit -m "Activate profile vocabulary via two-gate fuzzy correction

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Benchmark harness (last)

**Files:**
- Create: `scripts/bench_dictation.py`

**Interfaces:**
- Consumes: `Transcriber`, `preprocess.trim_silence`, `Profile`. Reads dev-local WAV fixtures from a directory argument (not committed).
- Produces: a dev-only script; prints per-clip latency (trim on vs off) and transcripts, plus vocab fuzzy-snap decisions. Not imported by the app; no test.

**Note:** This runs the real model, so it is a manual dev tool, not part of `pytest`. It exists to quantify the trim's latency delta and eyeball the fuzzy-snap changes before merge.

- [ ] **Step 1: Write the script**

Create `scripts/bench_dictation.py`:

```python
#!/usr/bin/env python
"""Dev benchmark: dictation latency (trim on vs off) + fuzzy-snap preview.

Not a test — it loads the real Parakeet model, so run it by hand:

    .venv/bin/python scripts/bench_dictation.py path/to/wavs [profile_name]

`path/to/wavs` holds 16 kHz mono WAV clips (dev-local, uncommitted). Prints,
per clip: samples trimmed, transcribe latency with trimming enabled vs
disabled, and the transcript. If a profile name is given, also prints the
transcript after Profile.apply() so vocab snaps are visible.
"""

import sys
import time
from pathlib import Path

from speakeasy import config
from speakeasy.profiles import Profile
from speakeasy.transcriber import Transcriber, read_wav_mono_f32


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    wav_dir = Path(sys.argv[1])
    profile = Profile.load(sys.argv[2]) if len(sys.argv) > 2 else None

    t = Transcriber()  # warms the model
    for wav in sorted(wav_dir.glob("*.wav")):
        audio = read_wav_mono_f32(wav)
        for enabled in (True, False):
            config.DICTATION_TRIM_ENABLED = enabled
            started = time.perf_counter()
            text = t.transcribe(audio)
            elapsed = time.perf_counter() - started
            tag = "trim" if enabled else "raw "
            print(f"{wav.name:24s} [{tag}] {elapsed:5.2f}s  {text!r}")
            if enabled and profile is not None:
                print(f"{'':24s} [snap] {profile.apply(text)!r}")
    config.DICTATION_TRIM_ENABLED = True


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-check it parses and shows usage**

Run: `.venv/bin/python scripts/bench_dictation.py`
Expected: prints the docstring/usage and exits with code 2 (does not load the model). A full run against real WAVs is a manual dev step.

- [ ] **Step 3: Commit**

```bash
git add scripts/bench_dictation.py
git commit -m "Add dev benchmark for dictation trim latency + fuzzy snaps

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] **Run the full suite:** `.venv/bin/python -m pytest -q` — all green, including the new `test_preprocess.py`, `test_phonetics.py`, and the extended `test_profiles.py`.
- [ ] **Manual dictation smoke test:** run `.venv/bin/python -m speakeasy`, dictate a few phrases including a leading pause; confirm text still appears correctly and onsets aren't clipped.
- [ ] **Manual fuzzy check:** with a profile whose `vocabulary` includes a technical word, dictate a sentence where the model tends to mis-spell it; confirm the snap fires (and that ordinary words are untouched).
