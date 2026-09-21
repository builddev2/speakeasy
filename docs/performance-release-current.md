# Current performance and release evidence — 20 September 2026

This is the current summary; linked incident documents retain historical results.
Installed build `fb1015e8784e13cbe2788a2f0d118a0afda17794` passed signed asset
verification and reached Ready. The user confirmed TextEdit and Codex work and
the static is gone after removing ordinary dictation start/stop cues. The final
pre-merge suite passed **393 tests in 8.95 s**. The user authorized merge to origin
and branch cleanup on 20 September. Full OS acceptance remains incomplete: the
additional device, lifecycle and performance checks below are still open.

## Implemented

- A headless source/frozen inference entrypoint runs identical private fixtures,
  rejects empty/truncated inputs before model import, and exits before app/UI,
  hotkey, capture, insertion or diagnostic cleanup. It records model/configuration,
  versions, dtype, filterbank/output parity digests, load/warmup, lazy mel/encoder
  and decoder timing, actual idle and explicitly defined memory statistics.
- Clean release mode rejects tracked and untracked edits, requires the configured
  signing identity, records exact revision/configuration/dependencies and hashes
  of model/frontend/native assets, and checks that source stayed clean during build.
  Development builds remain available and explicitly labelled. The release verifier
  checks hashes/signature and emits a manifest with acceptance false and OS cases
  pending. The browser fixture checks real text and caret after a manual insertion;
  it does not automate Speakeasy or claim delivery. Other surface checks use the
  existing regression suite and documented disposable-app procedure.
- Meeting processing failure now persists visibly in Dock/menu instead of silently
  returning to Ready. A failed spool deletion no longer skips cleanup of the other
  track or state restoration. Stage-local progress replaces arbitrary whole-job
  percentages, save has its own status, cancellation is explicit, and queued backlog
  has its own timing. Old timing records remain readable.

The failure regression first failed on the missing error state, then passed with
cleanup/state regressions. Source baseline: **388 passed in 7.27 s**. Implementation:
**393 passed in 7.13 s**, including the final cancellation regression. Frontend TypeScript/Vite build passed. The sandbox initially
lacked Metal; host tests use the original pinned virtualenv with this worktree's source.

## Inference experiment

Measured clean revision `dfdc7c20ce545ce6fa0196384e8cd0e8e9f6781a` in source and its
signed packaged candidate. Apple M1 Pro, 16 GiB RAM, MLX 0.31.2, Parakeet-MLX 0.5.2,
NumPy 2.4.6; unchanged v2 model, bfloat16 parameters. Source-cache and bundled
model weights/configuration SHA-256 hashes match. One model process ran at a time.
Three fixtures derive from one synthetic utterance: 2.52, 10.08 and 25.20 seconds.
They are not three independent speakers or a microphone corpus. Source/package
order alternated by fixture; lazy/evaluated-mel order alternated within each run.
Host scheduling and thermal state were not controlled.

| Fixture | Source baseline p50 / p95 ms | Packaged baseline p50 / p95 ms |
|---|---:|---:|
| Short | 94.91 / 100.00 | 96.16 / 124.57 |
| Medium | 256.21 / 298.86 | 257.36 / 1316.39 |
| Long | 596.99 / 614.48 | 597.75 / 629.64 |

Each baseline distribution has **30 warm attempts**, with one first-after-load
observation reported separately per run. Each evaluated-mel treatment has **31 warm
attempts**. Across 372 comparable attempts, filterbank and output hashes match
between runtimes and variants for each fixture; none was empty. Measured WER is
12.5% throughout against the unchanged fixed references. There is no demonstrated
accuracy improvement or degradation from mel evaluation. Explicit evaluation
provided no consistent speed benefit and was not adopted.

The packaged medium run contains a burst at pairs 6–8 with 1.2–2.6-second
outliers across both variants and both encoder/decoder work. These are retained,
not removed from p95. Similar medians do not rule out tail latency. The reversed-order medium repeat (30 warm baseline attempts per runtime)
measured source 233.29 / 240.40 ms and packaged 239.51 / 252.32 ms. No similar
burst occurred. Both runs are retained separately. These results
neither establish a persistent packaging penalty nor explain the historical
5–8-second installed stalls. No inference optimization is shipped on this evidence.

All values exclude recorder stop, worker queue and insertion. Warm short
release-to-dispatch p50 ≤500 ms / p95 ≤800 ms is **not established**. Independent
first-launch and actual 30-minute-idle p95 ≤1.5 s is **not established**. Actual 60-second waits produced baseline 827.93 ms and evaluated-mel 1065.28 ms
(n=1 each); encoder/deferred work was 757.37 and 937.56 ms. These are not
30-minute-idle observations or percentile evidence. No 15-hour
idle campaign or permanent GPU refresh loop was run. Streaming remains a canary;
the historical 100-real-take accuracy gate remains unmet.

## Meeting experiment

Accelerated prerecorded processing uses the production `_process_meeting` path.
Both tracks repeat synthetic speech in 20-second blocks, with two-second offset
speech placement, silence and overlap. Microphone ASR is precomputed before the
simulated stop; system ASR, diarization, alignment and save run after it. This is
a simplified low-speech-density workload, not representative multi-speaker meeting
acceptance. Capture stop, live backlog, drops and live queue reliability are not
measured. Stop-to-final includes final spool cleanup; it is not a real end-meeting
button latency measurement. Each size has one observation.

| Duration | System ASR s | Diarization s | Stop-to-final s |
|---|---:|---:|---:|
| 15 min exploratory | 16.08 | 43.59 | 59.96 |
| 30 min | 37.03 | 82.07 | 119.58 |
| 60 min | 70.02 | 164.08 | 234.80 |

The 15-minute exploratory run had uncommitted implementation and is not pooled
with clean trials. The 30-minute run overlapped compilation of the candidate;
keep that contention caveat. Diarization is the dominant observed stage.

A separate clean 15-minute **2,1,1,2 thread** experiment (n=2 per setting) found
39.41–43.83 s diarization at the existing two threads versus 60.77–64.47 s at one.
All four speaker-turn and final-segment digests were identical, including text,
speaker assignments, timestamps and overlap flags. One thread was rejected as
slower; production keeps two. This tiny single-voice synthetic sample is not
speaker-quality acceptance. No system-track pretranscription or quality tradeoff
was introduced. The **30% post-stop improvement gate is unmet**.

## Candidate and remaining release checks

The performance experiment revision is `dfdc7c20ce545ce6fa0196384e8cd0e8e9f6781a`.
Final follow-up also keeps cancellation visible when later stage callbacks arrive
and labels probe phase records as batch. The final candidate revision is embedded
in `dist/Speakeasy.app/Contents/Resources/build-commit.txt`; its verification receipt
is `dist/release-verification.json`. Candidate verification is not OS acceptance.
The earlier installed provenance was
`ba9f0c11787b062bdf7194b5c7484f995d691715-dirty`; it was subsequently replaced
in place by the verified `fb1015e` build. Documentation-only release records and
the merge commit may follow that installed revision without changing runtime code.

The clean candidate build passed asset verification and `codesign --verify --deep
--strict` with Speakeasy Dev. See [release procedure](release-checks.md) and the
[content-free evidence summary](reliability-evidence/performance-release/summary.json). Required application/device cases remain pending.
The browser-use tool explicitly blocked the local fixture URL; no alternate route
was used to bypass that policy. Native/Electron/terminal fixtures are documented
manual scenarios, not newly completed automated OS integration results.

Installation was authorized and completed. User confirmation covers ordinary
TextEdit and Codex dictation on `fb1015e`; earlier Codex/Terminal confirmation
applies to `de619e8`. Exact selection/caret and the remaining browser, Electron,
terminal-editor and Teams scenarios have not all been established. Coordinate
wake/input switching and next dictation, diagnostic-to-dictation and a user-started
meeting-to-dictation transition. No bulk recording corpus is requested.

Historical evidence: [insertion recurrence](insertion-focus-regression.md),
[wake mitigation](microphone-recovery-recurrence.md),
[meeting-start repair](meeting-start-recovery.md),
[real-mic accuracy limitations](real-mic-correctness.md), and
[prior follow-through](reliability-followthrough.md). Their earlier pass counts,
installed labels and acceptance statements apply to those historical builds.
