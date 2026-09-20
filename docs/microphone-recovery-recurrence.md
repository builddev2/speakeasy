# Recurring microphone failure — 15 September 2026

The user twice reported the generic microphone failure after a normal restart
had restored dictation. Both observations were on installed build `20a25fa`.
The app remained responsive internally, with no dictation capture helper process.
A short native stack sample showed the main run loop waiting for events and no
sampled CoreAudio stop/open stack. This does not establish the original cause.

The recurring process had both stdout and stderr directed to `/dev/null`.
Recovery emitted `MIC_RECOVERY` only with print, so GUI launches discarded the
very evidence needed to distinguish wake, missing input, permission denial,
helper timeout and other launch failures. No permission reset or system audio
service restart was performed. Stack samples stay local in temporary storage;
no audio or transcripts were collected.

## Correction

- Persist the existing content-free recovery record in
  `~/Library/Logs/Speakeasy-microphone-recovery.jsonl`, with the existing bounded
  5 MiB rotation (one rotated predecessor). Add wall-clock time and allowlisted
  triggering/final failure codes; never exception messages, device names, PIDs,
  audio or transcripts. A log I/O failure cannot prevent recovery.
- Distinguish a helper response timeout from a helper exit.
- When failed, replace Begin Meeting with Retry Microphone. This queues the
  existing finite recovery on the existing control executor; duplicate clicks
  cannot queue extra attempts. It does not start a recording or reload the model.
- Show specific permission/device/timeout guidance when known; otherwise show
  microphone-unavailable guidance, without implying permission was revoked.
- Do not reopen while a prior recorder stop remains pending. The main-process
  CoreAudio teardown guard remains authoritative and is never reset.

These changes fix lost diagnostics and provide an explicit recovery route. They
are not proof that the original microphone failure has been prevented. No
speculative wake delay, timeout increase or infinite background retry was added.
A future occurrence must be correlated with the persisted recovery record.

Validation: 375 tests passed in 6.46 seconds on the final source;
41 focused recorder/helper/watchdog tests passed in 2.35 seconds. Frontend
TypeScript and Vite build passed (59 modules). Automated recovery uses fake
helpers; actual wake/input-change reliability remains unverified.

## Follow-through — 19 September 2026

Installed `40dbd21` logs still contain intermittent wake failures: four of five
wake records exhausted helper timeouts (3310.1–4369.3 ms); one reached Ready in
1055.6 ms. Manual retries reached Ready in 398.0 and 501.7 ms. These are separated
from old builds and from the development experiment.

`1d0d92f` rejects holds as soon as wake recovery is queued and coalesces repeated
wake notifications. It also explicitly allowlists failure metadata. One isolated
real helper-exit experiment on that source recovered in 272.3 ms, captured the
next short take, and left zero test helpers after shutdown. This is not a wake
fix or general two-second acceptance result. No deadlines or OS services were
changed. See [the full evidence](reliability-followthrough.md).

## Enhancement regression reminder

Before changing dictation, focus, accessibility, security checks, or paste behavior,
read the [recurring insertion defect and prevention rules](insertion-focus-regression.md).
The user confirmed the corrected installed build works in Codex and Teams after
the optional-subrole fix; TextEdit was confirmed earlier. Historical observations
above remain scoped to their original builds. Microphone wake recovery and the
100-take accuracy gate remain unresolved.

## Wake startup budget correction — 19 September 2026

The next repair is on `codex/wake-recovery`, based on `ba9f0c1`. Existing wake
logs on `cf49a6a` include failed two-attempt recoveries lasting 4185.8 and
5348.7 ms. A launch-failure recovery on the installed insertion correction also
succeeded on retry. These locate failure before helper readiness, not inside a
particular CoreAudio call.

The code used the same 1.5-second timeout for cold process startup/import/device
opening and warm commands. Recovery killed a timed-out starter and immediately
repeated cold startup. The mitigation gives a single launch one absolute
four-second deadline, including startup and all progress messages; progress
cannot extend it. A timeout exhausts recovery instead of triggering an immediate
second cold launch. Other failures retain at most two attempts. Warm start
commands retain their 1.5-second deadline. Cleanup remains bounded and can add
its existing termination time to the launch deadline; four seconds is not a
whole-operation wall-clock guarantee.

Content-free `failure_stage` is allowlisted to `process_start`, `device_query`,
and `stream_open`. The last child milestone narrows a timeout; `process_start`
includes module imports before the child can report, and does not prove which
import blocked. No names, exception strings, audio, or text enter these records.
No new thread, background retry loop, system-service restart, or permission
change was introduced. Insertion code is unchanged.

Validation: 388 tests pass, including startup beyond the old deadline, fixed
absolute deadline despite progress, unchanged warm-command timeout, bounded
cleanup and explicit manual retry. Three real source-helper launches opened
stopped streams and shut down in 267, 260 and 307 ms; none recorded audio. A
separate cross-runtime packaged-helper probe produced resource-tracker errors
and is excluded as acceptance evidence. These awake checks do not reproduce wake
contention. The four-second choice is a bounded mitigation, not a measured wake
percentile or proof of root cause. It does not meet the prior two-second target
in the worst case.

The user will test sleep/wake later. Acceptance remains pending: after wake,
confirm Ready, dictate one short sentence into a disposable editor, and verify
one complete insertion. If recovery fails, preserve the new stage record before
manual retry. Do not call the wake issue resolved from unit tests alone.

The test bundle was built, signed with Speakeasy Dev, installed in place, and
passed deep/strict signature verification. Installed provenance is
`ba9f0c11787b062bdf7194b5c7484f995d691715-dirty` (this uncommitted wake correction).
This identifies the installed test bundle before the user-confirmed short
sleep/wake check below. The source is now being finalized for the authorized
merge to master; the installed bundle retains this original provenance label.

### First installed wake confirmation

The user reports putting the computer to sleep for approximately ten seconds,
waking it, and that it appears to work. The installed build above has two new
`sleep_wake` records: Ready after 2393.2 ms and 667.0 ms, each with one attempt,
no failure code and no failure stage. These are two logged recovery events, not
evidence that the user performed two distinct test cycles. This supports the
short-sleep recovery mitigation; it does not establish overnight reliability,
an exact root cause, or the prior two-second target (one event exceeded it).
The user did not explicitly describe a post-wake dictated sentence, so that
specific end-to-end assertion remains unconfirmed. Earlier failures are retained.

## Wake recovery release status — 19 September 2026

The installed mitigation passed the user's approximately ten-second sleep/wake
check. Two logged recovery events reached Ready in 2393.2 ms and 667.0 ms, each
on its first attempt without errors. These are not two independently confirmed
test cycles. The full suite passed 388 tests. Longer-sleep reliability and an
explicit post-wake dictation check remain unverified; the two-second target was
not met by both events. See [cause, mitigation and retained evidence](microphone-recovery-recurrence.md).
Historical failures and earlier build-specific acceptance statements remain
unchanged. Do not restore the shared cold-start/warm-command timeout: helper
launch has one absolute four-second budget, warm commands retain 1.5 seconds,
and a launch timeout does not trigger an immediate second cold launch.

### Retained release evidence

[Recovery log snapshot](reliability-evidence/wake-recovery/installed-recovery.jsonl)
contains the two preceding failed wake records and the two successful records on
the mitigation build. Original system logs remain untouched.
[Release summary](reliability-evidence/wake-recovery/summary.json) separates
user observation, logged events, test results, and outstanding acceptance.
