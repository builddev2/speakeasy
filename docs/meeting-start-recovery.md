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
