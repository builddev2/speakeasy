# Six-recording device review — 13 September 2026

Reviewed the completed private `/tmp/speakeasy-quick-6.json` report from source
commit `a65466b0a4b995f63a25fe34300520cb257e1556`. All six references match the
planned corpus. The [content-free derived metrics](reliability-evidence/quick-6-device-summary.json)
include the source SHA-256; the private source report remains unchanged and is
not committed. No new recording, installation or runtime setting change occurred.

## Result and decision

All six completed with no reported failed attempt, nonfinite audio, clipping,
dropped streaming frames or frame-integrity mismatch. Five takes have zero WER
in every scored mode. Across all six, weighted batch WER is 1.17%; both replay
depths have 0.58% WER. These are reference comparisons, not audible-speech review.

| Take | Actual seconds | Batch WER | Batch inference ms | Live outcome / ms after release |
|---|---:|---:|---:|---|
| 1 | 9.664 | 0.00% | 166.4 | complete / 545.5 |
| 2 | 5.344 | 0.00% | 111.7 | complete / 461.6 |
| 3 | 9.312 | 0.00% | 435.5 | complete / 537.1 |
| 4 | 12.384 | 11.11% | 227.5 | complete / 606.5 |
| 5 | 27.328 | 0.00% | 446.9 | batch_handoff / 157.1 |
| 6 | 36.160 | 0.00% | 599.3 | batch_handoff / 155.2 |

Take 4 has a one-word substitution shared by untrimmed batch, replay and live
streaming; trimmed batch has one additional singular/plural substitution.
This isolates an output difference associated with the trimmed comparison but
does not prove that trimming cut off speech. The audio is not retained for an
audible check, and runs are ordered, not repeated or randomized. No speculative
trimming or model change is justified from this single observation.

The first two recordings were intended to be short but lasted 9.664 and 5.344
seconds. Actual coverage is four medium and two long, with no under-five-second
take. Keep this gap explicit; do not relabel takes or require a repeat session.

The two long live outcomes are planned batch handoffs. Their approximately
155–157 ms is time to the handoff decision, **not time to a final transcript**.
The other four live finals arrived 462–607 ms after release. Batch comparison
times exclude recorder stop, queueing and insertion, and follow live processing.
Neither measurement establishes real release-to-paste latency or a reliable p95.

The legacy 30-prompt summary's unmet coverage is expected for this reduced check;
it is not a request for 24 more recordings. This run shows no catastrophic
capture/stream corruption, but does not establish broad production acceptance.

## Next targeted step: application insertion

Proceed to a disposable document in the user's primary dictation application,
using fixed test text and the branch's insertion code first. This requires no
additional recordings. Check one normal insertion, one blocked focus change,
clipboard preservation and one explicit recovery action without an automatic
retry. A successful AX call alone is insufficient: inspect the resulting document
for exactly one insertion and correct cursor placement. Never test in an email
composer, shared document or other surface that could send/publish text.

Select the user's primary app before operating it. The installed Speakeasy build
is older, so it cannot validate the new code. Do not replace it as part of this
check. Keep batch default and the historical accuracy gate unchanged. If the
application check exposes a problem, investigate that problem before requesting
more speech. Real OS wake/device recovery and short-duration coverage remain
separate, unverified checks.

## Follow-through boundary — 19 September 2026

These six-take results remain the completed accuracy evidence and have not been
rewritten or pooled with synthetic inference runs. No repeat bulk reading session
is requested. The later brief helper-recovery capture has no reference/WER and
is not another accuracy take. See [current implementation and open application
checks](reliability-followthrough.md).
