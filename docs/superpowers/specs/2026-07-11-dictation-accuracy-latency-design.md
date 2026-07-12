# Dictation latency & accuracy pass

**Date:** 2026-07-11
**Status:** Approved design, pending implementation
**Scope:** Reduce dictation latency and improve dictation + meeting accuracy,
without changing the speech model or adding runtime dependencies.

## Goal

Three user-prioritized targets:

1. **Dictation latency** — time from releasing Right ⌘ to text at the cursor.
2. **Dictation accuracy** — correctness of short dictations.
3. **Meeting accuracy** — transcript quality for meetings.

Meeting *processing time* is explicitly **out of scope**.

## Findings that shaped the design

These came from reading `parakeet-mlx` 0.5.2 internals and the current
pipeline; they narrow what's worth doing.

- **The model is fixed and unbiasable.** `parakeet-mlx` exposes no
  hotword/biasing hook and no beam search (greedy TDT decoding). Any
  vocabulary win must be **post-processing**, not model-level.
- **Waveform normalization is a no-op for accuracy.** The feature extractor
  applies **per-feature mean/variance normalization** on the log-mel
  (`audio.py`, `normalize == "per_feature"`). A constant input gain becomes a
  constant log-domain offset that mean-subtraction erases. Peak/gain
  normalization was therefore **dropped** from scope.
- **Silence trimming helps accuracy, not just latency.** Those per-feature
  stats are computed over the clip's time axis. A short utterance buried in
  lead/tail silence has its mean/std dominated by silence, so the speech
  frames get standardized against the wrong baseline. Trimming tightens the
  stats *and* cuts mel frames fed to the encoder.
- **Model quantization was rejected.** It would cut latency but degrade
  accuracy, against the stated priority. The model stays bf16.
- **`vocabulary` is currently dead for accuracy.** It's stored per-profile and
  only feeds the "protected words" guard in `add_correction`; it never
  influences a transcript.

## Components

Two components. Both are pure logic, unit-testable without mic/model/onnx (the
way `profiles.py` is tested today). No new runtime dependencies — offline
constraint holds; `difflib` is stdlib and the phonetic key is vendored.

### Component 1 — Silence trimming (dictation path only)

A pure function, new module `speakeasy/preprocess.py`:

```
trim_silence(audio: np.ndarray, sample_rate: int) -> np.ndarray
```

Algorithm:

1. Frame the clip at ~25 ms windows, 10 ms hop; compute per-frame RMS.
2. Threshold = `max(peak_rms * RATIO, ABSOLUTE_FLOOR)`. The absolute floor
   ensures an all-silence clip does not "trim to its own noise."
3. Find the first and last frames above threshold; keep that span plus an
   **80 ms margin** on each side (consonant onsets/offsets are low-energy;
   the margin prevents clipping leading "s"/"t" sounds).
4. **If no frame exceeds threshold, return the input unchanged.** Never return
   an empty array — the existing `MIN_DURATION_SECONDS` drop and "no speech
   detected" paths continue to own that case.

**Call site:** top of `Transcriber.transcribe()` (worker thread, pure NumPy,
runs before `get_logmel`). `transcribe_long()` (meetings) does **not** call it:

- Trimming interior/edge silence of a 2 h recording is marginal.
- Meeting chunks are 120 s, so per-chunk norm stats are not silence-dominated —
  the accuracy rationale for dictation doesn't transfer.
- Trimming would shift absolute transcript timestamps out of sync with the
  diarization turns they're aligned against (`meetings.align_speakers`).

**Default:** enabled. `DICTATION_TRIM_ENABLED = True` in `config.py`; the flag
exists to disable, not to opt in. Conservative margins make onset-clipping
unlikely.

**Config knobs (config.py):**

- `DICTATION_TRIM_ENABLED = True`
- `TRIM_THRESHOLD_RATIO` — fraction of peak RMS (e.g. `0.06`, ≈ −24 dB).
- `TRIM_ABSOLUTE_FLOOR` — RMS floor below which a clip counts as silent.
- `TRIM_MARGIN_SECONDS = 0.08`

### Component 2 — Activate the `vocabulary` list (dictation + meetings)

Make the stored per-profile `vocabulary` influence transcripts via a
**conservative fuzzy post-correction** in `Profile.apply()`. Because the
meeting pipeline already calls `profile.apply()` on every segment
(`engine._process_meeting`), this improves dictation and meetings from one
change.

**Matching — both gates must pass (high precision).** A transcript word token
snaps to a vocabulary word only when **both** hold:

1. **Phonetic key match** — a small vendored Metaphone-style key (no
   dependency, stays offline). Catches sound-alikes ("clod" → "Claude").
2. **Tight edit-distance ratio** — `difflib.SequenceMatcher` ratio above a
   high threshold (e.g. ≥ 0.8). Catches near-spellings ("kubernetis" →
   "Kubernetes") and prevents phonetic collisions from firing on unrelated
   words.

Requiring both keeps false rewrites rare, which matters most when a user's
vocabulary contains ordinary-looking words.

**Guardrails:**

- Never touch a token that already equals a vocabulary word or an existing
  correction target — reuse the existing "protected" set concept.
- Preserve the vocabulary word's own casing ("Claude", "Vercel") when snapping.
- Preserve surrounding punctuation and spacing (tokenize on word boundaries,
  replace whole word tokens, rejoin).

**Layering:** exact `corrections` regex runs first (precise, learned from
training), then vocabulary fuzzy-snap runs on the remaining word tokens.

**Performance:** phonetic keys for the vocabulary are precomputed in
`Profile._rebuild()`. `apply()` stays O(number of transcript tokens) with no
perceptible latency (matching the current claim in `profiles.py`).

**v1 scope:** single-token vocabulary words only. Multi-word entries
("Edge Runtime") are a **documented follow-up** — sliding-window phrase
matching multiplies false-snap risk and isn't worth it in v1.

## Testing

- `tests/test_preprocess.py` — silence trim: all-silence clip returned
  unchanged; leading/trailing silence removed; margin preserved; a clip with no
  silence is (near) unchanged; interior silence between words is **not**
  removed (only outer edges).
- `tests/test_profiles.py` (extend) — vocabulary fuzzy-snap: sound-alike snaps;
  near-spelling snaps; unrelated word left alone; protected words untouched;
  casing preserved; punctuation/spacing preserved; corrections still win and
  run first.
- No test touches the real mic, model, or sherpa-onnx (repo rule).

## Benchmark harness (last, per request)

Built **after** both components land, to quantify rather than gate:

- A small script (e.g. `scripts/bench_dictation.py`) that runs a fixed set of
  recorded WAV fixtures through `Transcriber.transcribe()` with trimming on vs
  off, reporting per-clip latency and the transcript, plus the vocabulary
  fuzzy-snap decisions. Fixtures are dev-local, not committed audio.
- Purpose: confirm the trim's latency delta and eyeball accuracy on the
  fuzzy-snap changes before merge.

## Out of scope

- Model quantization (rejected — accuracy cost).
- Waveform peak/gain normalization (no-op under per-feature normalization).
- Meeting processing-time parallelization (GPU transcription ∥ CPU
  diarization) — deprioritized by the user.
- Multi-word vocabulary phrase matching (v1 is single-token).
- Any new runtime dependency (offline + supply-chain constraints).
