# Reliability work and evidence

Baseline: master/origin/master/HEAD matched 18c2a62 before edits. Baseline host
suite: 339 passed in 6.73s. Sandbox collection cannot access Metal.

## Latency protocol and canary

`--dictation-streaming-canary` opts in for one launch. The default remains batch;
`--dictation-batch-mode` overrides the canary. Diagnostic results cannot enable
it. Existing frame parity, bounded queues, same-worker fallback, long-take
handoff, and stream-error circuit breaker remain in use.

Run `.venv/bin/python scripts/bench_temperature.py WAV REFERENCE --output JSONL`.
Use `--mode streaming` for depth-two replay, `--after diagnostic` or `--after
meeting` for model transitions, and `--idle-seconds 1800` for actual idle. This
script uses one sequential model owner and never records or inserts. The meeting
transition exercises ASR, not the complete diarization/engine pipeline. Every
cold sample requires a fresh process. Use at least 30 attempts per build,
duration, mode and temperature group; do not pool cold with warm. No accelerated
idle equivalent is asserted. References and audio are input-only; output contains
numeric metrics and safe enums. Streaming replay deliberately bypasses the long
take cutoff for comparison, as diagnostics do; production does not.

Initial synthetic host-Metal probe (single 2.9-second synthesized sentence,
unchanged 18c2a62 inference implementation with uncommitted benchmark additions):

| Mode | Warm n | Total inference p50 ms | p95 ms | WER |
|---|---:|---:|---:|---:|
| Batch | 30 | 85.80 | 90.13 | 0% |
| Depth-two replay | 30 | 572.90 | 587.41 | 0% |

First-take observations: batch 114.31 ms; replay 564.77 ms (one each, insufficient
for conclusions). These are NOT release-to-paste measurements and cannot meet
that acceptance criterion. No real microphone or installed app validation was
performed. The initial sandbox-generated WAV had zero frames; its model error
was excluded and the benchmark now rejects too-short fixtures before model load.

The 6.4-second occurrence was not reproduced. Shape compilation, residency,
idle wake and worker contention remain hypotheses. No speculative warmup,
padding or keep-warm change was made. Medium/long, 30 independent cold starts,
30-minute idle, complete meeting/diagnostic transitions, end-to-end delivery,
and the formal 100-real-microphone gate remain unvalidated. The 37-take release
evidence is historical and does not satisfy that gate.

## Final text insertion

The menu exposes Copy Last Dictation and Paste Last Dictation (may duplicate).
Only the latest final and existing correction source remain in memory, for 60
seconds, replaced on a newer final and cleared on profile change/shutdown/expiry.
There is no audio retention or persistent history. No new global shortcut was
added. Explicit paste shares the existing worker; it never retries automatically.

At key-down the engine retains an AX element identity. Delivery rejects changed
focus, inaccessible focus, and secure fields. For standard writable AX text
controls it writes AXSelectedText without reading values or selections. Successful
AX API return is an acknowledgement, not proof of visible application behavior.
An ambiguous AX failure never falls through to Cmd+V. Incompatible accessible
targets receive one clipboard/Quartz dispatch with an unconfirmed outcome.

Clipboard representations are materialized at delivery time; empty strings,
empty pasteboards and available non-text data are preserved. Restoration is
conditional on the pasteboard change count, so a later user copy wins. Promised
or unavailable representations can block insertion; lossless support is limited
to materializable representations. Clipboard operations are not OS-atomic against
another process changing them during the write itself. Delayed target handling
beyond the existing settle interval remains unverified. Recovery can duplicate an
already-delivered but unacknowledged paste; the menu labels that risk.

Mocked 1000-attempt matrix: 200 AX acknowledgements, 200 secure rejections,
200 changed-focus rejections, 400 unconfirmed fallback dispatches. All matched
expected outcomes; no automatic duplicate dispatch. These are harness outcomes,
not a 99.9% real-app delivery claim. Real AppKit, browser/contenteditable,
Electron, Terminal/editor, permission-revocation and delayed target checks remain
required on a consented host. Focus/clipboard metadata and final strings never
enter the standard telemetry schema.
