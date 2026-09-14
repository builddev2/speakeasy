# Insertion focus regression — 14 September 2026

The user reported no insertion in any application after installing `9d2d4ca`.
Five recent records for that build show successful recorder stop and completed
inference followed by `insertion_blocked` / `permission_or_focus_unavailable`.
No paste dispatch was recorded. One inference also took 7.8 seconds; that is a
separate unresolved latency observation, not the cause of the focus rejection.

A host diagnostic had Accessibility trust, but the system-wide focused-element
query returned `-25204` (cannot complete), at both 100 ms and 500 ms timeouts.
An application-specific query against an empty TextEdit document returned success
and an AXTextArea. These probes were separate observations, not a simultaneous
comparison with TextEdit verified frontmost. They support replacing the failing
system-wide lookup, but do not establish that the installed app's permission
state or all target applications are correct.

The lookup now creates an AX application element for the frontmost application's
transient PID and reads its focused element. It checks the frontmost PID again
before accepting the result. Neither PID nor field content is logged. Missing
focus, query failure or an application switch still fails closed. Existing secure
field, target-identity, clipboard ownership and ambiguous-write protections remain.
No permission reset, broad fallback or automatic retry was introduced.

Regression tests exercise application-owned lookup without using the system proxy,
query failure, missing element and a frontmost-application change. Full suite:
368 passed in 7.42 seconds. Live installed-app insertion remains to be confirmed
by the user after this correction is installed. No additional recording corpus
is required; one short ordinary dictation is sufficient for the immediate check.
