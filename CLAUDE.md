# CLAUDE.md

Claude Code must read and follow [AGENTS.md](AGENTS.md) before working in this
repository. `AGENTS.md` is the single canonical agent guide for the current
product architecture, offline/privacy constraints, Core Audio threading model,
build and test commands, permissions, and repository conventions.

This file intentionally does not duplicate that guidance. Keeping one source
of truth prevents the system-audio, meeting-memory, UI, and teardown rules from
drifting between agent tools as the implementation changes.

For the microphone check, follow the diagnostic evidence/privacy rules in that
guide and consult [current device results](docs/real-mic-correctness.md); a
completed recording session does not itself establish production acceptance.

## Remember before every dictation enhancement

The TextEdit-works / Codex-and-Teams-fail defect has happened twice. Read the
[recurring insertion rules in AGENTS.md](AGENTS.md#recurring-insertion-regression-mandatory-enhancement-check)
and [cause, resolution and evidence](docs/insertion-focus-regression.md).
Do not treat an absent optional AXSubrole as a failed security inspection; keep
real failures and password fields blocked. Preserve normal paste for web fields
and require actual Codex/Teams checks alongside TextEdit before claiming success.
