# Next step: six-recording microphone check

This replaces the requested 100-recording session as the next step. Budget about
five minutes, including processing; actual time depends on the device. Record in
a quiet room using your normal microphone. No noise, wake, or device-switching
setup is required for this first check.

There are two short, two medium and two long prompts, with ordinary and technical
language in each pair. References are unchanged selections from the existing
30-prompt corpus (original IDs 1, 2, 6, 7, 11, 12). Each recording is compared by
the existing diagnostic pipeline; separate recordings for each inference mode
are unnecessary.

## Run when ready

Quit other Speakeasy instances. In an interactive Terminal, run these commands
from this worktree. The absolute Python path uses the existing project environment;
no virtualenv symlink or installation is needed.

```sh
cd '/Users/jchiu/.codex/worktrees/30fe/Speakeasy 06JUL26'
'/Users/jchiu/Documents/00_Personal_Projects/Coding - General/Speakeasy 06JUL26/.venv/bin/python' -m speakeasy \
  --dictation-diagnostic /tmp/speakeasy-quick-6.json \
  --diagnostic-corpus docs/real-mic-quick-6-corpus.json --diagnostic-sounds
```

Use a new output filename if it exists. Read the disclosure, then type `RECORD`
and press Return to consent to six takes. For each prompt below, press Return to
start, read naturally, then press Return to stop. Short takes should be under five
seconds, medium five to under fifteen, and long at least fifteen. Do not change
references to match recognition. If a take fails, stop and retain the report;
do not restart the whole session or overwrite its evidence.

## How we use the result

Review all six takes individually for capture failures, empty/garbled output,
word errors, frame integrity and inference time. Confirm actual duration coverage
before interpreting the comparison. Long takes intentionally use batch handoff;
unscored live WER is not a failure. A wrong duration needs at most a targeted
replacement after review, not a complete repeat.

If these six work, the next decision is a small real-application insertion/recovery
check. If one fails, investigate that failure first and request only the specific
retest needed. No additional bulk recording session is required to take that next
step. Six takes cannot establish reliable p95 latency or broad production accuracy.

The existing numerical summary remains labelled `30_prompt_screen` and will show
incomplete coverage; that is expected, not failure of this six-take diagnostic.
This session has no automatic pass or streaming-enable action. Batch stays default.
The historical 100-take protocol remains a reference, not the current user task.

Temporary audio follows the existing deletion lifecycle. The private JSON retains
references and transcripts; standard telemetry remains content-free. Nothing is
pasted by this diagnostic. No recording starts until you explicitly consent.

## Prompts

### 1. Short — ordinary

Please review the proposal today.

### 2. Short — technical

The database migration finished successfully.

### 3. Medium — ordinary

Please review the proposal today. I will check the details this afternoon and share any changes with the team.

### 4. Medium — technical

The database migration finished successfully. Compare the complete recording with the delivered samples before applying any vocabulary corrections.

### 5. Long — ordinary

Please review the proposal today. I will check the details this afternoon and share any changes with the team. We need enough time to review the document before making a final decision. Please bring the notes from our last discussion so that everyone has the same information. If the schedule changes, let me know before I leave the office this evening.

### 6. Long — technical

The database migration finished successfully. Compare the complete recording with the delivered samples before applying any vocabulary corrections. We should keep the validation evidence separate from production telemetry and review the failure cases individually. The release remains blocked until the team verifies the customer workflow on the actual device. The acceptance criteria must include accurate results and clear ownership of each unresolved issue.

