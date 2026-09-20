# Candidate release checks

A candidate is not accepted merely because its tests or signature pass.
Use a clean commit and `scripts/build_app.sh --release` (no installation).
The default build remains an explicitly labelled development build and permits
tracked/untracked edits. Release mode rejects both, refuses ad-hoc fallback,
and embeds revision, configuration, dependency versions and asset hashes.

Run `.venv/bin/python -m pytest -q` with host Metal access, then:

```
.venv/bin/python scripts/verify_release.py dist/Speakeasy.app --output /private/tmp/speakeasy-release.json
```

Keep that content-free manifest with test results. Its OS scenarios start pending
and acceptance is false. Do not change acceptance until each required scenario
has actual evidence on that exact bundle. AX acknowledgement is not visible
verification; clipboard dispatch is unconfirmed delivery. Never retry ambiguous
delivery automatically. Install only on an explicit release instruction.

## Visible delivery

Use disposable TextEdit text, `tests/fixtures/insertion.html` in a browser,
an available Electron editor, and a terminal **editor buffer**, never a shell
prompt. Replace SELECT in `LEFT SELECT RIGHT` by dictating “blue lantern”.
Observe exactly `LEFT blue lantern RIGHT`, once, with the caret after lantern.
The browser fixture checks actual text and saved selection, and has no network
or submit action. Automation must use permitted computer-use tooling.
For Codex and Teams, use empty unsent composers and user confirmation where
app automation is restricted. Never send test content.

Repeat after a rapid second take. Test focus change during inference, copying
unrelated text during inference/settle, a non-text clipboard, explicit Copy and
Paste Last Dictation, and expiry after 60 seconds. Check that a secure field is
refused. Record visible results separately from mocked permission/timeout,
lazy AX, missing-subrole, stale-generation and onset regressions in the suite.
Do not revoke permissions or change input devices without coordination.

## Lifecycle

Record startup Ready, a coordinated sleep/wake then a complete dictation,
input switching, diagnostics-to-dictation, meeting-start failure recovery,
and a user-initiated completed-meeting-to-dictation transition. Keep fault
simulation distinct from OS observations. Never interrupt or start a live
meeting for these checks without user initiation.

## Offline inference comparison

Both executables accept the same early-exit diagnostic entrypoint:

```
.venv/bin/python -m speakeasy --inference-probe INPUT.wav REFERENCE.txt --output /private/tmp/source.jsonl
./dist/Speakeasy.app/Contents/MacOS/Speakeasy --inference-probe INPUT.wav REFERENCE.txt --output /private/tmp/packaged.jsonl
```

Use identical synthetic or consented mono PCM16 16 kHz inputs and one process
at a time. Alternate source/package order between fixtures/runs. Default 31
pairs provides at least 30 warm observations per variant; first-after-load is
separate. `--idle-seconds` waits real time before each attempt. No long idle
campaign is implied. `--treatment refresh` measures a finite one-second-silence
refresh and its added compute wall time; it does not measure energy or prove
capture-time overlap. Reports are private (0600), output is exclusive-create,
and normal app startup, microphone, hotkeys and insertion are bypassed.
These are inference results, never release-to-dispatch acceptance.
