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
