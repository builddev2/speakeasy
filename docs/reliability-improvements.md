# Speakeasy reliability changes and verification

## Scope and provenance

The fresh worktree, master, origin/master and installed release all matched
`18c2a62dbe571dbd202b42cf96832f7210ec4f5d` before implementation. Work is on
`codex/latency-paste-mic-recovery`. No merge, push, installation, branch/worktree
deletion, microphone recording, or system permission change was performed.

Production streaming remains off. **The next step is now a six-recording diagnostic**
([instructions and prompts](real-mic-quick-6-reading-sheet.md)), replacing the
100-recording request. The historical formal gate remains unmet; neither this
small check nor the existing 30-prompt screen establishes production acceptance.

## Demonstrated causes and changes

- Clipboard restoration previously ignored empty strings and non-text data.
  The fallback now materializes all available representations and snapshots again
  at delivery time, preserving copies made during inference. Restoration checks
  change count so a later user copy wins.
- An older final could overwrite the latest retained text. Generation/profile
  changes are serialized with final retention and dispatch. A separate 1000-stale-
  generation regression verifies that older finals neither replace nor insert.
- Focus could change during clipboard settling. Focus and clipboard ownership are
  now rechecked immediately before Cmd+V. The generation lock is released before
  the 500-ms post-dispatch wait so the next capture is not delayed by that wait.
- Stop errors discarded helpers without immediately prewarming replacements.
  The existing control executor now performs bounded recovery. A prior stop must
  relinquish ownership before a new helper opens; repeated calls cannot create
  additional abandoned stop threads.
- Shutdown could terminate a helper before its in-progress launch completed,
  leaving a subsequently launched process alive. A launch/shutdown lock fixes
  this demonstrated race without blocking realtime callbacks or level reads.
- A prewarmed stream retained its previous default input. The helper now checks
  the PortAudio-reported transient input identity before capture and reopens on a change. Identity never
  crosses IPC or enters telemetry. Wake notifications queue recovery on control.
- The historical 6.4-second **batch** inference was not reproduced or explained.
  Graph shape, residency, idle/wake and queue contention remain hypotheses.
  No speculative padding, duration warmup, or continuous keep-warm was added.

Changed implementation: `__main__.py`, `latency_protocol.py`,
`scripts/bench_temperature.py`, `injector.py`, `dictation_benchmark.py`,
`engine.py`, `recorder.py`, `microphone_helper.py`, `ui/menubar.py`, and the Dock's
state labels. Focused tests accompany each demonstrated regression.

## Latency and accuracy evidence

[Clean-source raw metrics and grouped summary](reliability-evidence/clean/summary.json)
contain 366 synthetic host-Metal inference attempts on **three synthesized
fixtures**, not 366 independent utterances. Every record identifies clean source
commit `13ae18d3d085dbf736b8ade469fa9346b61491bf`. All had 0% WER against their
fixed references; none was empty and no streaming parity check failed. These
establish neither microphone accuracy nor real-app delivery. No synthetic audio
or reference text is committed.

Each table entry has 30 attempts except first-short, which has 31 independent
fresh processes per mode. Values are **total inference p50 / p95 in ms**, not
release-to-paste. First-take rows follow model warmup, not a cold machine.
Streaming replay bypasses the long-take cutoff only for comparison, as diagnostics
do; production retains the cutoff.

| Group | Batch | Depth-two replay |
|---|---:|---:|
| Warm short, 2.464 s | 144.79 / 163.17 | 850.86 / 878.36 |
| Warm medium, 11.803 s | 351.28 / 375.56 | 3038.52 / 7961.68 |
| Warm long, 26.101 s | 499.30 / 2832.19 | 9697.61 / 13833.79 |
| First short after load, 31 fresh processes/mode | 106.90 / 143.56 | 849.89 / 956.82 |
| Short after diagnostic comparison | 144.20 / 166.40 | 575.77 / 607.44 |
| Short after meeting ASR | 90.68 / 102.37 | 562.22 / 579.76 |
| First after 30-minute idle | Unmeasured | Unmeasured |

The 31 fresh-process short runs measured model load plus warmup separately:
batch-run p50/p95 **1977.22 / 2409.89 ms**; streaming-run **2189.35 / 3378.01 ms**.
Do not add these to release latency without measuring capture/queue overlap.
Medium/long first-take observations have only one sample each and support no
cold-start conclusion. The meeting transition exercises ASR, not the complete
engine/diarization pipeline; diagnostic transition similarly exercises comparison,
not UI close/re-arm. Real idle is not replaced by an accelerated fake clock.

Long batch tail latency and the substantial variation between groups remain
unexplained. Runs were sequential but host load, thermal state and OS residency
were not controlled; mode order was not randomized. These are observations, not
causal evidence of an improvement or regression. The earlier 366 exploratory
records and [their summary](reliability-evidence/summary.json) are retained
unchanged outside `clean/`. They had uncommitted work and mixed HEADs; they are
not pooled with the clean-source run. The first sandbox-generated fixture had
zero frames and caused an invalid-input model error; it was excluded. Empty or
too-short fixtures and empty references are now rejected before model loading.

Inspection of installed pinned Parakeet-MLX 0.5.2 found no explicit `mx.compile`
call; MLX 0.31.2 activation functions use shapeless compilation. This does not
establish compilation as the outlier cause. Mel construction is lazy: the
reported inference phase includes evaluation of mel/encoder work and is not pure
decoder time. MLX active allocation remained approximately 1218 MiB within the
warm runs; peak allocation varied by fixture/mode (approximately 2107–3826 MiB).
Allocator statistics do not establish total process RSS or OS/Metal residency.

The requested release-to-paste p50/p95 targets are **not established**. The long
replay cost supports retaining the existing long-take batch handoff, but does
not explain the old batch outlier. No new real-device accuracy evidence was
collected. The supplied release evidence (37 takes, 98.79% batch/depth-two
accuracy) remains historical and below the formal gate.

### Reproduction

Run from the worktree using its configured `.venv/bin/python` and local,
consented PCM16 WAV/fixed UTF-8 reference files:

```sh
.venv/bin/python scripts/bench_temperature.py FIXTURE.wav REFERENCE.txt --output batch.jsonl --attempts 31
.venv/bin/python scripts/bench_temperature.py FIXTURE.wav REFERENCE.txt --output streaming.jsonl --attempts 31 --mode streaming
.venv/bin/python scripts/bench_temperature.py FIXTURE.wav REFERENCE.txt --output after-diagnostic.jsonl --attempts 30 --after diagnostic
.venv/bin/python scripts/bench_temperature.py FIXTURE.wav REFERENCE.txt --output after-meeting.jsonl --attempts 30 --after meeting
.venv/bin/python scripts/bench_temperature.py FIXTURE.wav REFERENCE.txt --output idle.jsonl --attempts 30 --idle-seconds 1800
```

Repeat the first command with `--attempts 1` in 30 separate processes and unique
output paths for a cold group. Run each mode sequentially; never share a model
across threads. The script captures no microphone input and performs no insertion.
Thirty actual 30-minute idle intervals require at least 15 hours per group;
no equivalent idle condition has been validated. Outputs are exclusive-created,
mode 0600, and contain only safe numeric metrics/enums and revision metadata.

`--dictation-streaming-canary` opts in for one app launch. Batch mode overrides
it and the next normal launch returns to batch. Diagnostic results cannot enable
it. Existing frame parity, bounded queues, same-worker fallback, long-take handoff
and stream-error circuit breaker remain in use.

## Insertion outcomes and recovery

Standard writable AX text controls receive an AXSelectedText write without
reading values or selected text. Successful API return is `ax_acknowledged`,
not proof of visible application behavior. AX messaging uses a 100-ms timeout.
An ambiguous write never falls through to a second insertion. Secure fields,
missing/inaccessible focus, and changed focus block delivery. Incompatible
accessible targets receive one clipboard/Quartz dispatch, explicitly unconfirmed.
Blocked/ambiguous outcomes are not logged as successful dictation delivery;
only allowlisted outcome enums are added to standard telemetry.

The menu exposes Copy Last Dictation and Paste Last Dictation (may duplicate).
Only the latest final and the existing correction source remain in memory for
60 seconds, cleared on replacement, profile change, expiry and shutdown. A single
cancellable expiry timer retains no text in its callback. There is no audio
retention, persistent history, or new global shortcut. Explicit paste uses the
existing worker and is available only while idle/failed, not during an active
take. The menu disables recovery after expiry.

| Automated matrix | Result | Host-app validation |
|---|---|---|
| Standard AX editable control | API acknowledgement; no clipboard dispatch | AppKit unverified |
| Browser/contenteditable, Electron-like, Terminal/editor fallback | One unconfirmed dispatch | All unverified |
| Secure field / missing permission or focus | Blocked, no automatic insertion | Real permission/secure targets unverified |
| Focus changes during inference or clipboard settle | Blocked | Real focus switching unverified |
| Clipboard copied during inference / after dispatch | Latest copy preserved | Real non-text/promised providers unverified |
| Delayed paste | Existing 250-ms simulated handler reads final before restoration | Handlers beyond 500 ms unverified |
| AX ambiguous error | No fallback or automatic retry | Real ambiguous errors unverified |
| Stale generation | 1000 rejected, latest final unchanged | Unit concurrency evidence |

The 1000-attempt mocked delivery matrix produced exactly 200 AX acknowledgements,
200 secure rejections, 200 focus-change rejections and 400 unconfirmed dispatches.
All expected outcomes matched, with zero duplicate automatic dispatches. This is
not a 99.9% real-app delivery claim. Clipboard restoration is lossless only for
materializable representations. Promised/unavailable data can block fallback.
The OS clipboard is not atomic against another process changing it during a
write itself. Explicit recovery may duplicate an already-delivered paste.

## Microphone recovery and remaining device checks

Ready, recording, stopping, restarting, permission_blocked, device_unavailable
and failed are explicit recorder states. The existing control executor attempts
at most two replacement launches with one 50-ms backoff. Native authorization
status is queried without prompting/resetting permission; denied/restricted
permission stops retry immediately. A missing default input also stops retry.
Other driver failures remain generic, with an input/permission remedy.

The Dock/menu show recovery and failure. Holds beginning during recovery are
rejected as a pair rather than queued for unexpected later capture. Wake recovery
respects shutdown, training, diagnostics and meeting ownership. PortAudio-reported input changes
are checked before the next take, not midway through an active recording.
Recovery telemetry contains only revision, safe reason/state enums, attempt count
and duration. The CoreAudio teardown guard is never reset.

1000 serialized control cycles with 100 injected helper errors had no busy
cascade, no leaked helpers and no extra surviving threads: cycle p50/p95/max
**0.04 / 0.25 / 0.46 ms**. These are mocked timings, not OS re-arm measurements.
A separate 1000-cycle recorder ownership soak passed. The existing 1.5-second
command deadline means two failed launches plus termination can exceed two
seconds. Actual OS re-arm within two seconds is **not established**.

Real sleep/wake, default-input changes, permission revocation, unexpected helper
exit and overflow still require controlled device validation. Simulations cover
wake, input change, finite failure, stop timeout, helper exit, overflow, shutdown
races, rejected hotkey holds and repeated control cycles. No coreaudiod restart,
TCC reset, microphone recording, or installed-app modification was used.

## Verification and acceptance boundaries

- Baseline: `.venv/bin/python -m pytest -q` on host: **339 passed in 6.73s**.
- Latest full suite: **366 passed in 6.01s**.
- Focused insertion/stream/timing run: **63 passed in 0.64s**.
- `tests/test_recorder.py tests/test_microphone_helper.py tests/test_dictation_diagnostic.py`: **34 passed in 1.16s**.
- Control soak command: `.venv/bin/python -m pytest -q -s tests/test_engine_watchdog.py::test_1000_control_recovery_cycles_have_no_busy_cascade`.
- `npm --prefix frontend run build`: TypeScript check passed; Vite built **59 modules in 448 ms**.
- Sandbox MLX collection failed because Metal was unavailable; host results above are the product test evidence.

Next human step: six quiet-room recordings, followed by individual review. Do not
request another bulk session before investigating those results.

Outstanding evidence for the original production acceptance criteria: real end-to-end latency including
30-minute idle, the complete meeting/diagnostic-to-dictation transitions, the real
application insertion matrix, OS recovery timing and the formal 100-take consented
microphone/reference gate. The original 100-prompt corpus from `0e286aa` is
preserved separately in `real-mic-formal-100-corpus.json`, with exact instructions
in [the historical formal reading sheet](real-mic-formal-100-reading-sheet.md);
that session is not required for the next step. Its 34 short,
34 medium and 32 long prompts are evenly balanced across conditions/vocabulary.
The current UI screen and legacy 30-prompt numerical summary remain unchanged;
100-take acceptance requires separate manual review and never auto-enables
streaming. The changes are not installed, so checks of the current installed app
cannot validate the new insertion/recovery behavior.

Offline runtime, one MLX worker, one control executor, bounded audio queues,
authoritative complete audio, same-worker fallback, final-only insertion and
diagnostic privacy/audio deletion boundaries are preserved. No model, meeting
pipeline, language, package-size, or cloud feature was changed.

## Local implementation commits

- `332be16`: build-scoped inference protocol and explicit streaming canary.
- `9f9e78e`: guarded final insertion and expiring explicit text recovery.
- `13ae18d`: bounded microphone recovery and completed reliability guards.

The clean benchmark and latest suite validate the third commit. Subsequent
evidence-only changes do not alter executable source. The temporary dependency
virtualenv link used for verification is removed after completion; configure a
project environment before running the reproduction or microphone commands.
