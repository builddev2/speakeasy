# Correctness investigation — 6 September 2026

Real-device acceptance is **UNMET**. No new microphone recordings have been
collected. The cause of the reported garbled microphone transcript is not yet
established. Normal dictation now uses authoritative batch audio for every
duration; this is a containment policy, not proof that batch meets acceptance.

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
The real-device corpus currently has **zero takes**. WER and live latency are
unmeasured. Manual catastrophic-error and condition review remains required even
if numerical checks pass. The safe default cannot auto-enable itself.

Open Speakeasy from the Dock, choose **Check Microphone**, and then **Begin
30-prompt check**. Read the displayed reference and use Start recording / Stop &
compare. All quiet prompts precede the noisy prompts. Allow about 12–18 minutes.
Completed results persist in the private diagnostic-reports folder; cancel and
window-close unblock the session and restore normal dictation after cleanup.
Model and recorder work use the engine's existing worker/control executors.

The original 323-test result above records the preceding CLI implementation.
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

Meeting processing, UI layout, normal telemetry schema and final-only profile /
clipboard publication remain unchanged. No broader latency or meeting-progress
optimization was performed. Installation/provenance is reported separately after
building this committed revision; no push is authorized or performed.
