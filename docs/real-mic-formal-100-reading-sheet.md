# Formal 100-take microphone session

**Historical protocol, not the current next step.** Use the
[six-recording check](real-mic-quick-6-reading-sheet.md) instead.

Original corpus preserved from commit `0e286aa`. This is a separate session; the
in-app 30-prompt screen and its acceptance calculation are unchanged. Completing
the recordings does not automatically enable streaming or establish a pass.

Quit other Speakeasy instances. From this branch with the project virtualenv
configured, run the command below in an interactive Terminal. The disclosure
appears first; recording begins only after you type `RECORD` and press Return.
For each take, read its fixed prompt below; press Return to start and again to
stop. Do not change a reference to match recognition. Short takes must be under
5 seconds, medium 5 to under 15, and long at least 15. Follow each prompt’s listed condition
(50 quiet and 50 moderate-noise takes). Never record others without consent.

```sh
.venv/bin/python -m speakeasy --dictation-diagnostic /tmp/speakeasy-formal-100.json \
  --diagnostic-corpus docs/real-mic-formal-100-corpus.json --diagnostic-sounds
```

Use a new output path if it already exists. Reports retain failed attempts and
references/transcripts privately; temporary audio is deleted by the existing
diagnostic lifecycle. The legacy numerical summary is still labelled as a
30-prompt screen: formal review must separately confirm 100 completed takes,
coverage, WER <=5% overall and per duration, streaming within one percentage
point of batch, no empty audible result, catastrophic transcript or frame loss.
Keep the gate closed pending that review.

## 1 — short, quiet, ordinary

Please review the proposal today.

## 2 — short, quiet, technical

The database migration finished successfully.

## 3 — short, quiet, ordinary

The customer approved the revised plan.

## 4 — short, quiet, technical

Keep the microphone audio fully offline.

## 5 — short, quiet, ordinary

Schedule a follow up meeting tomorrow.

## 6 — short, quiet, technical

The application must preserve the clipboard.

## 7 — short, quiet, ordinary

Send the summary before lunch.

## 8 — short, quiet, technical

Validate the renewal forecast before planning.

## 9 — short, quiet, ordinary

Capture the decision and its owner.

## 10 — short, quiet, technical

The engineering team reviewed the release.

## 11 — short, quiet, ordinary

Please move our appointment to Friday.

## 12 — short, quiet, technical

The worker processes one request at a time.

## 13 — short, quiet, ordinary

The next meeting starts after lunch.

## 14 — short, quiet, technical

Check the sample rate before transcription.

## 15 — short, quiet, ordinary

Leave the package beside the door.

## 16 — short, quiet, technical

The product backlog needs an accountable owner.

## 17 — short, quiet, ordinary

We should confirm the delivery date.

## 18 — short, moderate_noise, technical

The deployment requires a regression check.

## 19 — short, moderate_noise, ordinary

The team needs a clear answer.

## 20 — short, moderate_noise, technical

Our pipeline must detect missing frames.

## 21 — short, moderate_noise, ordinary

Please review the proposal today.

## 22 — short, moderate_noise, technical

The database migration finished successfully.

## 23 — short, moderate_noise, ordinary

The customer approved the revised plan.

## 24 — short, moderate_noise, technical

Keep the microphone audio fully offline.

## 25 — short, moderate_noise, ordinary

Schedule a follow up meeting tomorrow.

## 26 — short, moderate_noise, technical

The application must preserve the clipboard.

## 27 — short, moderate_noise, ordinary

Send the summary before lunch.

## 28 — short, moderate_noise, technical

Validate the renewal forecast before planning.

## 29 — short, moderate_noise, ordinary

Capture the decision and its owner.

## 30 — short, moderate_noise, technical

The engineering team reviewed the release.

## 31 — short, moderate_noise, ordinary

Please move our appointment to Friday.

## 32 — short, moderate_noise, technical

The worker processes one request at a time.

## 33 — short, moderate_noise, ordinary

The next meeting starts after lunch.

## 34 — short, moderate_noise, technical

Check the sample rate before transcription.

## 35 — medium, quiet, ordinary

Please review the proposal today. I will check the details this afternoon and share any changes with the team.

## 36 — medium, quiet, technical

The database migration finished successfully. Compare the complete recording with the delivered samples before applying any vocabulary corrections.

## 37 — medium, quiet, ordinary

The customer approved the revised plan. Please bring the notes from our last discussion so that everyone has the same information.

## 38 — medium, quiet, technical

Keep the microphone audio fully offline. The release remains blocked until the team verifies the customer workflow on the actual device.

## 39 — medium, quiet, ordinary

Schedule a follow up meeting tomorrow. I will check the details this afternoon and share any changes with the team.

## 40 — medium, quiet, technical

The application must preserve the clipboard. Compare the complete recording with the delivered samples before applying any vocabulary corrections.

## 41 — medium, quiet, ordinary

Send the summary before lunch. Please bring the notes from our last discussion so that everyone has the same information.

## 42 — medium, quiet, technical

Validate the renewal forecast before planning. The release remains blocked until the team verifies the customer workflow on the actual device.

## 43 — medium, quiet, ordinary

Capture the decision and its owner. I will check the details this afternoon and share any changes with the team.

## 44 — medium, quiet, technical

The engineering team reviewed the release. Compare the complete recording with the delivered samples before applying any vocabulary corrections.

## 45 — medium, quiet, ordinary

Please move our appointment to Friday. Please bring the notes from our last discussion so that everyone has the same information.

## 46 — medium, quiet, technical

The worker processes one request at a time. The release remains blocked until the team verifies the customer workflow on the actual device.

## 47 — medium, quiet, ordinary

The next meeting starts after lunch. I will check the details this afternoon and share any changes with the team.

## 48 — medium, quiet, technical

Check the sample rate before transcription. Compare the complete recording with the delivered samples before applying any vocabulary corrections.

## 49 — medium, quiet, ordinary

Leave the package beside the door. Please bring the notes from our last discussion so that everyone has the same information.

## 50 — medium, quiet, technical

The product backlog needs an accountable owner. The release remains blocked until the team verifies the customer workflow on the actual device.

## 51 — medium, quiet, ordinary

We should confirm the delivery date. I will check the details this afternoon and share any changes with the team.

## 52 — medium, moderate_noise, technical

The deployment requires a regression check. Compare the complete recording with the delivered samples before applying any vocabulary corrections.

## 53 — medium, moderate_noise, ordinary

The team needs a clear answer. Please bring the notes from our last discussion so that everyone has the same information.

## 54 — medium, moderate_noise, technical

Our pipeline must detect missing frames. The release remains blocked until the team verifies the customer workflow on the actual device.

## 55 — medium, moderate_noise, ordinary

Please review the proposal today. I will check the details this afternoon and share any changes with the team.

## 56 — medium, moderate_noise, technical

The database migration finished successfully. Compare the complete recording with the delivered samples before applying any vocabulary corrections.

## 57 — medium, moderate_noise, ordinary

The customer approved the revised plan. Please bring the notes from our last discussion so that everyone has the same information.

## 58 — medium, moderate_noise, technical

Keep the microphone audio fully offline. The release remains blocked until the team verifies the customer workflow on the actual device.

## 59 — medium, moderate_noise, ordinary

Schedule a follow up meeting tomorrow. I will check the details this afternoon and share any changes with the team.

## 60 — medium, moderate_noise, technical

The application must preserve the clipboard. Compare the complete recording with the delivered samples before applying any vocabulary corrections.

## 61 — medium, moderate_noise, ordinary

Send the summary before lunch. Please bring the notes from our last discussion so that everyone has the same information.

## 62 — medium, moderate_noise, technical

Validate the renewal forecast before planning. The release remains blocked until the team verifies the customer workflow on the actual device.

## 63 — medium, moderate_noise, ordinary

Capture the decision and its owner. I will check the details this afternoon and share any changes with the team.

## 64 — medium, moderate_noise, technical

The engineering team reviewed the release. Compare the complete recording with the delivered samples before applying any vocabulary corrections.

## 65 — medium, moderate_noise, ordinary

Please move our appointment to Friday. Please bring the notes from our last discussion so that everyone has the same information.

## 66 — medium, moderate_noise, technical

The worker processes one request at a time. The release remains blocked until the team verifies the customer workflow on the actual device.

## 67 — medium, moderate_noise, ordinary

The next meeting starts after lunch. I will check the details this afternoon and share any changes with the team.

## 68 — medium, moderate_noise, technical

Check the sample rate before transcription. Compare the complete recording with the delivered samples before applying any vocabulary corrections.

## 69 — long, quiet, ordinary

Please review the proposal today. I will check the details this afternoon and share any changes with the team. We need enough time to review the document before making a final decision. Please bring the notes from our last discussion so that everyone has the same information. If the schedule changes, let me know before I leave the office this evening.

## 70 — long, quiet, technical

The database migration finished successfully. Compare the complete recording with the delivered samples before applying any vocabulary corrections. We should keep the validation evidence separate from production telemetry and review the failure cases individually. The release remains blocked until the team verifies the customer workflow on the actual device. The acceptance criteria must include accurate results and clear ownership of each unresolved issue.

## 71 — long, quiet, ordinary

The customer approved the revised plan. Please bring the notes from our last discussion so that everyone has the same information. If the schedule changes, let me know before I leave the office this evening. I will check the details this afternoon and share any changes with the team. We need enough time to review the document before making a final decision.

## 72 — long, quiet, technical

Keep the microphone audio fully offline. The release remains blocked until the team verifies the customer workflow on the actual device. The acceptance criteria must include accurate results and clear ownership of each unresolved issue. Compare the complete recording with the delivered samples before applying any vocabulary corrections. We should keep the validation evidence separate from production telemetry and review the failure cases individually.

## 73 — long, quiet, ordinary

Schedule a follow up meeting tomorrow. I will check the details this afternoon and share any changes with the team. We need enough time to review the document before making a final decision. Please bring the notes from our last discussion so that everyone has the same information. If the schedule changes, let me know before I leave the office this evening.

## 74 — long, quiet, technical

The application must preserve the clipboard. Compare the complete recording with the delivered samples before applying any vocabulary corrections. We should keep the validation evidence separate from production telemetry and review the failure cases individually. The release remains blocked until the team verifies the customer workflow on the actual device. The acceptance criteria must include accurate results and clear ownership of each unresolved issue.

## 75 — long, quiet, ordinary

Send the summary before lunch. Please bring the notes from our last discussion so that everyone has the same information. If the schedule changes, let me know before I leave the office this evening. I will check the details this afternoon and share any changes with the team. We need enough time to review the document before making a final decision.

## 76 — long, quiet, technical

Validate the renewal forecast before planning. The release remains blocked until the team verifies the customer workflow on the actual device. The acceptance criteria must include accurate results and clear ownership of each unresolved issue. Compare the complete recording with the delivered samples before applying any vocabulary corrections. We should keep the validation evidence separate from production telemetry and review the failure cases individually.

## 77 — long, quiet, ordinary

Capture the decision and its owner. I will check the details this afternoon and share any changes with the team. We need enough time to review the document before making a final decision. Please bring the notes from our last discussion so that everyone has the same information. If the schedule changes, let me know before I leave the office this evening.

## 78 — long, quiet, technical

The engineering team reviewed the release. Compare the complete recording with the delivered samples before applying any vocabulary corrections. We should keep the validation evidence separate from production telemetry and review the failure cases individually. The release remains blocked until the team verifies the customer workflow on the actual device. The acceptance criteria must include accurate results and clear ownership of each unresolved issue.

## 79 — long, quiet, ordinary

Please move our appointment to Friday. Please bring the notes from our last discussion so that everyone has the same information. If the schedule changes, let me know before I leave the office this evening. I will check the details this afternoon and share any changes with the team. We need enough time to review the document before making a final decision.

## 80 — long, quiet, technical

The worker processes one request at a time. The release remains blocked until the team verifies the customer workflow on the actual device. The acceptance criteria must include accurate results and clear ownership of each unresolved issue. Compare the complete recording with the delivered samples before applying any vocabulary corrections. We should keep the validation evidence separate from production telemetry and review the failure cases individually.

## 81 — long, quiet, ordinary

The next meeting starts after lunch. I will check the details this afternoon and share any changes with the team. We need enough time to review the document before making a final decision. Please bring the notes from our last discussion so that everyone has the same information. If the schedule changes, let me know before I leave the office this evening.

## 82 — long, quiet, technical

Check the sample rate before transcription. Compare the complete recording with the delivered samples before applying any vocabulary corrections. We should keep the validation evidence separate from production telemetry and review the failure cases individually. The release remains blocked until the team verifies the customer workflow on the actual device. The acceptance criteria must include accurate results and clear ownership of each unresolved issue.

## 83 — long, quiet, ordinary

Leave the package beside the door. Please bring the notes from our last discussion so that everyone has the same information. If the schedule changes, let me know before I leave the office this evening. I will check the details this afternoon and share any changes with the team. We need enough time to review the document before making a final decision.

## 84 — long, quiet, technical

The product backlog needs an accountable owner. The release remains blocked until the team verifies the customer workflow on the actual device. The acceptance criteria must include accurate results and clear ownership of each unresolved issue. Compare the complete recording with the delivered samples before applying any vocabulary corrections. We should keep the validation evidence separate from production telemetry and review the failure cases individually.

## 85 — long, moderate_noise, ordinary

We should confirm the delivery date. I will check the details this afternoon and share any changes with the team. We need enough time to review the document before making a final decision. Please bring the notes from our last discussion so that everyone has the same information. If the schedule changes, let me know before I leave the office this evening.

## 86 — long, moderate_noise, technical

The deployment requires a regression check. Compare the complete recording with the delivered samples before applying any vocabulary corrections. We should keep the validation evidence separate from production telemetry and review the failure cases individually. The release remains blocked until the team verifies the customer workflow on the actual device. The acceptance criteria must include accurate results and clear ownership of each unresolved issue.

## 87 — long, moderate_noise, ordinary

The team needs a clear answer. Please bring the notes from our last discussion so that everyone has the same information. If the schedule changes, let me know before I leave the office this evening. I will check the details this afternoon and share any changes with the team. We need enough time to review the document before making a final decision.

## 88 — long, moderate_noise, technical

Our pipeline must detect missing frames. The release remains blocked until the team verifies the customer workflow on the actual device. The acceptance criteria must include accurate results and clear ownership of each unresolved issue. Compare the complete recording with the delivered samples before applying any vocabulary corrections. We should keep the validation evidence separate from production telemetry and review the failure cases individually.

## 89 — long, moderate_noise, ordinary

Please review the proposal today. I will check the details this afternoon and share any changes with the team. We need enough time to review the document before making a final decision. Please bring the notes from our last discussion so that everyone has the same information. If the schedule changes, let me know before I leave the office this evening.

## 90 — long, moderate_noise, technical

The database migration finished successfully. Compare the complete recording with the delivered samples before applying any vocabulary corrections. We should keep the validation evidence separate from production telemetry and review the failure cases individually. The release remains blocked until the team verifies the customer workflow on the actual device. The acceptance criteria must include accurate results and clear ownership of each unresolved issue.

## 91 — long, moderate_noise, ordinary

The customer approved the revised plan. Please bring the notes from our last discussion so that everyone has the same information. If the schedule changes, let me know before I leave the office this evening. I will check the details this afternoon and share any changes with the team. We need enough time to review the document before making a final decision.

## 92 — long, moderate_noise, technical

Keep the microphone audio fully offline. The release remains blocked until the team verifies the customer workflow on the actual device. The acceptance criteria must include accurate results and clear ownership of each unresolved issue. Compare the complete recording with the delivered samples before applying any vocabulary corrections. We should keep the validation evidence separate from production telemetry and review the failure cases individually.

## 93 — long, moderate_noise, ordinary

Schedule a follow up meeting tomorrow. I will check the details this afternoon and share any changes with the team. We need enough time to review the document before making a final decision. Please bring the notes from our last discussion so that everyone has the same information. If the schedule changes, let me know before I leave the office this evening.

## 94 — long, moderate_noise, technical

The application must preserve the clipboard. Compare the complete recording with the delivered samples before applying any vocabulary corrections. We should keep the validation evidence separate from production telemetry and review the failure cases individually. The release remains blocked until the team verifies the customer workflow on the actual device. The acceptance criteria must include accurate results and clear ownership of each unresolved issue.

## 95 — long, moderate_noise, ordinary

Send the summary before lunch. Please bring the notes from our last discussion so that everyone has the same information. If the schedule changes, let me know before I leave the office this evening. I will check the details this afternoon and share any changes with the team. We need enough time to review the document before making a final decision.

## 96 — long, moderate_noise, technical

Validate the renewal forecast before planning. The release remains blocked until the team verifies the customer workflow on the actual device. The acceptance criteria must include accurate results and clear ownership of each unresolved issue. Compare the complete recording with the delivered samples before applying any vocabulary corrections. We should keep the validation evidence separate from production telemetry and review the failure cases individually.

## 97 — long, moderate_noise, ordinary

Capture the decision and its owner. I will check the details this afternoon and share any changes with the team. We need enough time to review the document before making a final decision. Please bring the notes from our last discussion so that everyone has the same information. If the schedule changes, let me know before I leave the office this evening.

## 98 — long, moderate_noise, technical

The engineering team reviewed the release. Compare the complete recording with the delivered samples before applying any vocabulary corrections. We should keep the validation evidence separate from production telemetry and review the failure cases individually. The release remains blocked until the team verifies the customer workflow on the actual device. The acceptance criteria must include accurate results and clear ownership of each unresolved issue.

## 99 — long, moderate_noise, ordinary

Please move our appointment to Friday. Please bring the notes from our last discussion so that everyone has the same information. If the schedule changes, let me know before I leave the office this evening. I will check the details this afternoon and share any changes with the team. We need enough time to review the document before making a final decision.

## 100 — long, moderate_noise, technical

The worker processes one request at a time. The release remains blocked until the team verifies the customer workflow on the actual device. The acceptance criteria must include accurate results and clear ownership of each unresolved issue. Compare the complete recording with the delivered samples before applying any vocabulary corrections. We should keep the validation evidence separate from production telemetry and review the failure cases individually.

## Enhancement regression reminder

Before changing dictation, focus, accessibility, security checks, or paste behavior,
read the [recurring insertion defect and prevention rules](insertion-focus-regression.md).
The user confirmed the corrected installed build works in Codex and Teams after
the optional-subrole fix; TextEdit was confirmed earlier. Historical observations
above remain scoped to their original builds. Microphone wake recovery and the
100-take accuracy gate remain unresolved.

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
