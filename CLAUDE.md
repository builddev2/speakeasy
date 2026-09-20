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

## Wake recovery release status — 19 September 2026

The installed mitigation passed the user's approximately ten-second sleep/wake
check. Two logged recovery events reached Ready in 2393.2 ms and 667.0 ms, each
on its first attempt without errors. These are not two independently confirmed
test cycles. The full suite passed 388 tests. Longer-sleep reliability and an
explicit post-wake dictation check remain unverified; the two-second target was
not met by both events. See [cause, mitigation and retained evidence](docs/microphone-recovery-recurrence.md).
Historical failures and earlier build-specific acceptance statements remain
unchanged. Do not restore the shared cold-start/warm-command timeout: helper
launch has one absolute four-second budget, warm commands retain 1.5 seconds,
and a launch timeout does not trigger an immediate second cold launch.
