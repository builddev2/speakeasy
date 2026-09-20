# Meeting start feedback — 16 September 2026

The user reported that Begin Meeting did nothing. The installed app showed Ready,
a default system-audio selection and no observed blocked CoreAudio stack. No
recording was started during investigation. Old meeting-start exceptions went
only to discarded stdout/stderr, so the actual prior error cannot be recovered.

A reproduced code defect existed before the recorder-start exception handler:
listener/recorder/pretranscription preparation could throw after setting
`_meeting_active` without clearing it. Later Begin Meeting calls would silently
return while the UI stayed Ready. The exception boundary now covers preparation,
clears the active flag and cancels queued pretranscription on failure, resumes
hotkeys when appropriate, and pushes an error state to the Dock. This is a tested
failure path, not proof that the user's earlier failure had this exact cause.

Recorder busy/start failures are also visible. Safe reason enums and build/time
metadata persist in `~/Library/Logs/Speakeasy-meeting-start.jsonl` with bounded
rotation. No exception messages, transcripts, audio, or device identities are
logged. Invalid remote-speaker counts and rejected bridge calls now show feedback
instead of an unhandled promise rejection. Blank remote-speaker count remains Auto.

Validation: 24 focused meeting tests passed in 1.99 seconds; full suite 377 passed
in 7.28 seconds. TypeScript and Vite build passed. Tests use fake capture; the
user must explicitly start the next real meeting to validate the installed path.

Separately, new microphone-recovery telemetry establishes that an earlier failed
recovery followed sleep/wake and exhausted two helper-timeout attempts (4018.8 ms).
A later manual retry reached Ready in 414.9 ms. That identifies the failing stage,
not why the helper timed out or proof that wake reliability has been fixed.

Diagnostic provenance: the first focused test run used the real startup-log path
before its fixture was redirected to a temporary directory. Consequently, entries
from the development run stamped `21eb7f3` must not be used as live meeting-start
evidence. Automatic approval review rejected deleting that file because it could
contain real diagnostics; it was left intact. Use installed `40dbd21` or later
records for follow-up, and preserve uncertainty for any mixed earlier entries.

## Subsequent verification — 19 September 2026

The preparation/start failure fixes and their provenance caveat above are
preserved in the new worktree. Its full host suite passes 386 tests, including
post-meeting-start failure regressions. Installed provenance subsequently reached `cf49a6a` after an authorized in-place
update and verified startup to Ready; a full real meeting-to-dictation
transition remains unverified. See [follow-through](reliability-followthrough.md).

## Enhancement regression reminder

Before changing dictation, focus, accessibility, security checks, or paste behavior,
read the [recurring insertion defect and prevention rules](insertion-focus-regression.md).
The user confirmed the corrected installed build works in Codex and Teams after
the optional-subrole fix; TextEdit was confirmed earlier. Historical observations
above remain scoped to their original builds. Microphone wake recovery and the
100-take accuracy gate remain unresolved.
