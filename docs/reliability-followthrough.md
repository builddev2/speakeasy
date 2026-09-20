# Reliability follow-through — 18–19 September 2026

## Status and provenance

**Partial completion; not a production acceptance claim.** Capture/insertion
and queued-wake defects were corrected. The recurring long inference stall and
OS wake timeouts remain unexplained. The application delivery matrix is open.

Work is on `codex/reliability-followthrough`, based on verified `6b5144e`,
including the newer microphone-retry and meeting-start fixes. No older worktree
was edited. At the pre-install evidence snapshot, the installed app identified `40dbd21`;
local master and origin/master remain `18c2a62`. After explicit authorization,
the tested `cf49a6a` bundle was installed in place and relaunched to Ready.
Deep/strict signature verification passed and installed provenance matches the
local bundle. No permission prompt was observed. No merge, push, TCC reset,
service restart, or input-device change was performed.

Executable source and final local signed bundle: `cf49a6a`.
`dist/Speakeasy.app` passed the build script's bundle checks and deep/strict
signature verification with **Speakeasy Dev**. It is approximately 2.6 GB.
Frontend TypeScript/Vite passed (59 modules, 430 ms Vite build).
Baseline: **377 passed in 7.82 s**. Final full host suite: **386 passed in 5.99 s**.
The initial sandbox collection failed for unavailable Metal; the host run is the
verification evidence. Microphone/model unit tests remain mocked.

## 1. Latency — instrumented and investigated, correction still open

Fresh installed-build observations are separated from all older builds in
[installed-observations.json](reliability-evidence/followthrough/installed-observations.json).
The one `40dbd21` dictation captured 1.856 seconds, trimmed to 1.565 seconds,
and took **5281.4 ms release-to-dispatch**: inference 5068.2 ms, recorder stop
111.6 ms, worker queue 2.3 ms, insertion-to-dispatch 66.4 ms. Its outcome was
`dispatched_unconfirmed`. This confirms a continuing stall, not visible delivery,
a percentile, or its cause.

Ordinary take telemetry now includes mode, first-after-load/warm/idle state,
actual elapsed model idle seconds, previous model operation, warmup completion,
model-load/warmup durations, and input duration group. Timings use monotonic
`perf_counter_ns`; the helper maps the first input-buffer ADC time to that clock
using callback observation minus PortAudio input age. Key-down-to-first-buffer
therefore measures onset separately from release-to-control, stop, queue,
preprocessing, inference, dispatch and readiness.

`release_to_dispatch_ms` is explicitly an API acknowledgement or paste dispatch,
never visible insertion. `release_to_ready_ms` ends when the engine reaches
Ready; it remains null for failed/stale takes that do not reach Ready.
The legacy release-to-paste/idle fields remain for historical compatibility.
Idle means time since the last model operation finished, including any intervening
non-model work, not proof that the whole machine was idle or asleep.
Previous-operation labels describe model work, not completion of a whole meeting
or diagnostic UI lifecycle. Streaming remains an explicit canary; context may
be absent if its worker never began before release. No text or identities were
added to telemetry.

The ordinary summary now separates build, mode, temperature, previous operation
and duration. It shows ranges, rather than p50/p95, below 30 observations in a
cohort. Historical missing metadata stays unknown.

### Controlled experiment

`scripts/bench_phase_evaluation.py` compares unchanged lazy mel with explicitly
evaluated mel on the same synthetic short utterance, reversing treatment order
on alternate pairs. It records source revision/dirty state, actual idle, WER,
load average, process peak RSS, and MLX allocation. These are host inference
measurements, not release-to-dispatch or physical residency measurements. Audio
and reference are not committed. The fixture checksum is in the observation
manifest. One synthesized utterance is not a microphone-accuracy corpus.

The original dirty-source exploratory runs remain separately labelled in
`phase-warm.jsonl` and `phase-idle60.jsonl`; they are not pooled with the clean
runs. `clean-phase-warm.jsonl` was collected at `1d0d92f`. Its first baseline
observation is first-after-load; do not treat all 30 baseline records as a warm
cohort. Final warm cohorts are reported in `summary.json` and `final-warm32.jsonl`.

At clean `cf49a6a`, excluding the first-after-load baseline: unchanged batch had
**31 warm attempts, p50 81.82 / p95 90.12 ms**; evaluated mel had **32 warm
attempts, p50 82.26 / p95 89.02 ms**. All had 0% WER and no empty result on this
one fixture. The small differences do not support an application change.
The separate first-after-load baseline was 102.3 ms (n=1; no percentile).

On clean `cf49a6a`, one baseline after **60.005 seconds physical idle** took
**773.9 ms**, including **722.9 ms encoder plus deferred mel** and **42.3 ms
decoder wall time**. One evaluated-mel trial after another physical 60-second
idle took **979.1 ms**, including 828.2 ms encoder work and 50.4 ms decoder time.
The pinned Parakeet implementation evaluates encoder outputs before calling
`decode`; the harness timestamps that boundary without changing production code.
These single observations narrow where the short-idle cost appears, but cannot
attribute it to compilation, GPU wake, scheduling, or memory residency, and do
not explain the installed 5-second stall. Explicit evaluation did not demonstrate
a useful improvement, so it was **not added to production**. No keep-warm loop,
padding, cache, new model worker, or deadline change was added.

A small real-idle reproduction was completed; **30-minute idle, sleep/wake ASR,
first-after-launch distributions, and full meeting/diagnostic transitions remain
unmeasured on this source**. No 15-hour run was launched. For another small
reproduction, use a consented PCM16 WAV and fixed reference:

```sh
.venv/bin/python scripts/bench_phase_evaluation.py FIXTURE.wav REFERENCE.txt --output idle60.jsonl --pairs 1 --idle-seconds 60
```

For one baseline after an actual 30-minute wait, the existing
`bench_temperature.py ... --attempts 1 --idle-seconds 1800` is available; it is
one first-after-load/idle observation, not a distribution. Thirty independent
30-minute waits require at least 15 hours per treatment and need a planned run.
Next causal experiment: compare a finite worker-owned refresh during the
capture interval against baseline, on the same audio and actual idle intervals,
measuring both refresh cost and post-release work before changing the app.
The onset changes here do not establish an inference-speed improvement.

**Targets:** warm short release-to-dispatch p50 ≤500 ms / p95 ≤800 ms and
first-after-launch/real-30-minute-idle p95 ≤1.5 s are **not established**.

## 2. Insertion and onset — source fixes verified; application matrix open

`6044daf` preserves AX error status. A supported role is required. Unsupported
subrole (`kAXErrorAttributeUnsupported`) is distinct from timeout, permission,
cannot-complete and no-value failures. Successful-but-null subrole and secure
subrole also block insertion before clipboard access. A regression failed on
the old implementation and passes now. Native/search-field and DOM metadata
retain their appropriate one-write/one-paste behavior.

`f2378aa` submits bounded target resolution to the existing single worker while
control starts authoritative capture immediately. It accepts the identity only
when resolution finished no later than the first captured buffer. A delayed
AX query or busy worker therefore cannot bind to a later-selected field; the
final text is retained for explicit Copy/Paste Last Dictation for 60 seconds.
The tradeoff is deliberate: lazy accessibility, model loading or rapid takes
can block automatic insertion rather than delay capture. This needs real-app
usability validation. The implementation adds no executor, does not query AX
on the hotkey callback, and cancels queued stale target work on replacement or
shutdown. Existing final-generation, focus, clipboard and ambiguous-write
protections remain.

The concurrent regression holds AX resolution blocked while capture reaches
Recording, then verifies late target rejection. A pre-buffer target is retained.
First-buffer timestamps cross the helper protocol on stop; complete helper audio
remains authoritative. Existing 1000-attempt delivery and stale-generation tests,
clipboard representations/change-count tests, TTL, secure focus, lazy activation,
settle-time focus changes and streaming fallback regressions still pass.
These tests do not prove a real-app delivery percentage.

An automated native check created a disposable TextEdit document (`Untitled 2`,
containing only `LEFT. RIGHT`). CUA could edit that fixture, but the independent
product delivery probe did not observe TextEdit frontmost and safely aborted.
There was no product insertion or visible delivery evidence. The save prompt
was cancelled and the disposable document was left open. No alternate method
was used to force focus or bypass the tool boundary.

**Still unverified on the new build:** exactly one insertion/cursor placement in
TextEdit, browser textarea/contenteditable, Electron editor, Terminal/editor,
Teams and Codex; real secure-field refusal and real focus/clipboard races.
AX acknowledgement and unconfirmed paste dispatch retain their distinct labels;
ambiguous writes are never automatically repeated.

## 3. Device recovery — one actual scenario passed; wake/input gates open

`1d0d92f` sets Recovering when wake work is queued, so holds cannot queue behind
it before control starts. Repeated notifications coalesce into one finite job.
The regression also verifies no helper opens if shutdown occurs before the
queued job executes. Trigger/final failure metrics are explicitly allowlisted.
Existing permission/device, finite retry, stop ownership, shutdown-race,
1000-cycle helper ownership/control soaks and post-meeting-start regressions pass.
The newer user retry and persistent diagnostics were preserved, not reimplemented.

The installed `40dbd21` snapshot contains five wake recoveries: four timeout
failures at **3310.1–4369.3 ms**, and one success at **1055.6 ms**. Two manual
retries succeeded in **398.0 and 501.7 ms**. These existing OS observations show
intermittency; they do not validate the new queued-wake change. The earlier
`21eb7f3` observations remain in the historical documents.

A separate real-device experiment used the worktree's own helper after the
installed UI showed Ready and existing microphone authorization was verified.
It ran on one control executor, killed only its own helper, detected the exit,
and recovered in **272.3 ms** with one launch. The next 0.2-second capture
returned **3072 frames**, its first buffer arrived **29.1 ms** after start was
requested, and **zero test helpers** survived shutdown. Audio stayed in memory,
was not transcribed or saved, and diagnostics went to an isolated evidence file.
[Raw result](reliability-evidence/followthrough/device-recovery.json) and
[exact experiment harness](reliability-evidence/followthrough/device_recovery_harness.py)
identify source `1d0d92f`. This meets the two-second target for **one helper-exit
re-arm**, not a wake/input-switch distribution or installed microphone-to-text
acceptance.

**Open:** coordinated sleep/wake, default-input switch, next ordinary take after
both, and installed post-meeting recovery. Permission revocation and destructive
scenarios remain simulated. Two 1.5-second launch deadlines plus termination can
still exceed two seconds; those limits were not speculatively shortened.

## Minimal remaining validation

Installation was subsequently authorized and completed in place for `cf49a6a`.
Startup to Ready is verified; this is not insertion or accuracy acceptance.
TextEdit was confirmed, followed by user confirmation of the installed
optional-subrole correction in response to the Codex/Teams check request.
Check one short dictation in each unresolved disposable
app surface, confirming exactly one copy at the cursor. In Teams/Codex use an
empty composer and **do not send**. In Terminal use an editor buffer, never a
shell prompt or Return. A blocked take should leave Copy Last Dictation available;
do not retry an ambiguous delivered take automatically.

Coordinate one sleep/wake and one default-input switch when no meeting is active,
then verify Ready and the next short take. Reuse the completed six-take accuracy
evidence; no new bulk reading session is requested. Historical 100-take accuracy
acceptance remains unmet, and streaming stays off by default.

## Enhancement regression reminder

Before changing dictation, focus, accessibility, security checks, or paste behavior,
read the [recurring insertion defect and prevention rules](insertion-focus-regression.md).
The user confirmed the corrected installed build works in Codex and Teams after
the optional-subrole fix; TextEdit was confirmed earlier. Historical observations
above remain scoped to their original builds. Microphone wake recovery and the
100-take accuracy gate remain unresolved.
