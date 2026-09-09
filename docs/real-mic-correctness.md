# Correctness investigation — updated 8 September 2026

Real-device acceptance remains **UNMET**, although the saved recordings now
meet the short-screen accuracy and coverage thresholds. Normal dictation uses
authoritative batch results with silence trimming; streaming remains disabled.

## Latest installed-device evidence

The user completed 30 recordings on build `8356a30`, then seven short recordings
on `3f48639`. The combined report contains 37 takes (910 reference words):

| Measure | Batch | Depth-two streaming |
|---|---:|---:|
| Overall word accuracy | 98.79% | 98.79% |
| Short word accuracy | 97.78% | 95.56% |
| Medium word accuracy | 97.90% | 98.74% |
| Long word accuracy | 99.20% | 99.04% |

Coverage is 10 short, 17 medium, 10 long; 21 quiet/16 moderate-noise and
19 ordinary/18 technical. All seven added takes were under five seconds and
transcribed with zero word errors. Saved takes have zero recorded integrity
failures or catastrophic candidates. Ten long live streams deliberately handed
off to batch and are unscored, not 100%-WER failures.

The first follow-up stopped after four takes with a recording/comparison error.
The retry retained those takes and added three, preserving the original report
and per-take build provenance. The report retains one failed attempt, so its
numerical gate is false. Its cause was not recorded and cannot be reconstructed;
no Speakeasy crash report was found during review. Manual acoustic/condition
review and the broader production gate remain outstanding. Repeated references
on retry and this single-user sample limit generalization.

Build `0af7379` adds operation, safe exception category, known helper failure code,
and source file/line evidence for future failures. Messages, locals, audio and
transcripts are excluded from failure details. Cleanup preserves the original
exception. Older failures remain explicitly undiagnosed; a retry cannot erase
them or automatically pass acceptance. No extra reading is requested now.

Reports remain local in the private diagnostic-reports folder; content-bearing
reports and audio are not checked into Git. The original garbled utterance has
no retained audio, so these results do not establish its acoustic root cause.

## Confirmed defects and regression evidence

Starting worktree, canonical master, and installed provenance were all
`a79dc586abdf20c515b13e9b02d121ca1d794954`. The worktree was clean.

Running the original source against controlled inputs reproduced two defects:

- A stream receiving 15,999 frames while the authoritative recording contained
  16,000 returned `complete`. No frame or sample-content parity check existed.
  The corrected worker checks ordered float32 sample digests and frame counts
  after draining/finalization. Missing, extra or reordered samples invalidate
  the final and trigger existing same-worker batch recovery. Digests stay in
  memory and are not added to telemetry.
- A callback marked with PortAudio `input_overflow` reported zero dropped
  frames. Sounddevice's installed documentation says this flag denotes audio
  discarded before samples reach the callback. The helper now invalidates the
  take through its existing dropped-frame error path. The counter marks the
  affected callback length; PortAudio does not supply the actual lost count.
  This prevents publication of an incomplete batch as well as a partial stream.

Neither reproduction establishes which mechanism caused the user's garbled
utterance. No original audio exists for that utterance, so a same-audio acoustic
root-cause claim would be unsupported.

## Controlled same-audio model results

Thirty existing Samantha synthesized WAVs: ten phrases at short duration,
repeated four times for medium and ten times for long. This is synthetic replay,
not thirty speakers or live captures. The prior 100-take soak repeated only ten
short synthetic fixtures ten times, with the authoritative array enqueued before
streaming started. It bypassed the microphone, helper IPC, live release and UI
sound paths.

New comparisons load Parakeet once on the host and sequentially run the exact
same float32 samples through normal batch, untrimmed batch, depth-one streaming,
and depth-two streaming. Diagnostic replay retains two-second model chunking
and the current final partial-chunk behavior; long replay bypasses the normal
15-second batch cutoff for comparison only.

| Group | Samples | Batch WER | Depth 1 WER | Depth 2 WER | Batch p50 | Depth 2 p50 |
|---|---:|---:|---:|---:|---:|---:|
| Short, 1.9–2.7 s | 10 | 0.00% | 1.59% | 1.59% | 105.8 ms | 555.0 ms |
| Medium, 8.2–11.3 s | 10 | 1.59% | 1.59% | 1.59% | 224.6 ms | 1655.8 ms |
| Long, 20.7–28.5 s | 10 | 1.75% | 11.75% | 1.59% | 513.0 ms | 5791.2 ms |

Word-weighted overall: batch 15/945 errors (1.59%); depth one 79/945
(8.36%); depth two 15/945 (1.59%). Untrimmed batch had the same WER; its p50
was 85.2, 207.2 and 475.2 ms by group. These sequential, warmed replay timings
measure total inference, not release-to-paste. They do not justify changing
trimming or cache depth for real microphone audio.

Content-bearing synthetic evidence is local only:
`/private/tmp/speakeasy-correctness-replay.json`. No audio/reference/transcript
was added to standard telemetry. No new dependency or runtime network call was
introduced.

## Tests and remaining device work

- Original focused capture/stream baseline: 27 passed.
- New focused pure-logic/privacy suite: 39 passed.
- Full suite: `.venv/bin/python -m pytest -q` — **323 passed in 4.72 s** on
  the host with Metal access. The initial sandbox run could not collect nine
  MLX-dependent modules because Metal was unavailable; that was an environment
  limitation, subsequently resolved by host execution.
- Regressions cover delayed tails, sample parity, same-worker batch recovery,
  no draft publication, upstream overflow, bounded/nonblocking callback behavior,
  consent, exact WAV roundtrip, private permissions, deletion on success/error,
  expiry, orphan cleanup, failed-attempt accounting and no diagnostic telemetry.

The current app provides 30 prewritten references: ten per duration;
15 quiet/15 moderate-noise; 15 ordinary/15 technical. This user-requested shorter
screen does not establish equivalence to the original 100-take acceptance gate. Actual recording durations
are measured, so reading outside a requested range can require additional takes.
The latest real-device evidence is summarized above. Manual catastrophic-error and condition review remains required even
if numerical checks pass. The safe default cannot auto-enable itself.

Open Speakeasy from the Dock, choose **Check Microphone**, and then **Begin
30-prompt check**. Read the displayed reference and use Start recording / Stop &
compare. All quiet prompts precede the noisy prompts. Allow about 12–18 minutes.
Completed results persist in the private diagnostic-reports folder; cancel and
window-close unblock the session and restore normal dictation after cleanup.
Model and recorder work use the engine's existing worker/control executors.

The original 323-test result above records the preceding CLI implementation.
The final release verification includes 339 tests covering the in-app workflow,
follow-up preservation, error reporting, and cleanup.
The in-app version additionally tests prompt balance, button sequencing,
cancellation, shared engine resources and saved-report permissions.

## Scope and privacy

Only explicitly consented diagnostics retain a WAV temporarily (0600 file,
0700 folder), delete it after comparison/error, enforce 15-minute in-process
expiry and sweep crash leftovers on the next launch. The requested private JSON
retains content until the user deletes it. Normal failed dictations retain no
audio. A process crash prevents timer expiry until the next launch; this is
stated in the disclosure rather than presented as a guaranteed wall-clock erase.

Retry Last Failed Dictation was deliberately omitted. The current keyboard
injection has no target-app acknowledgement, and the audio has no retention
owner spanning insertion completion. A safe retry would require a new explicit
retention lifecycle and control for ambiguous/duplicate insertion; silently
retaining every take would violate this task's privacy boundary.

Meeting processing, normal telemetry and final-only profile/clipboard publication
remain unchanged. The diagnostic UI adds saved results, missing-short follow-ups,
and explicit failure reasons. The authorized release uses an in-place signed
installation from the merged `master` revision; installed provenance and origin
parity are verified during release.
