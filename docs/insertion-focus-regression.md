# Insertion focus regression — 14 September 2026

## Current follow-up — 19 September 2026

The user confirms TextEdit works on installed `cf49a6a`, while Codex and Teams
do not. Recent records show one acknowledgement and two blocks: unavailable
focus and unknown/secure field. Records cannot identify the destination app.

The subrole check incorrectly rejected `kAXErrorNoValue` and successful nil
values. Chromium's AXSubrole implementation returns nil for ordinary fields
and explicitly returns AXSecureTextField for protected text fields:
https://chromium.googlesource.com/chromium/src/+/HEAD/ui/accessibility/platform/ax_platform_node_cocoa.mm
The correction accepts absent optional subroles while retaining role-query,
permission, timeout, secure-field, target-identity and clipboard protections.
Regression coverage exercises both native and web delivery for these values;
all 386 tests pass. This does not resolve or explain every unavailable-focus
block: late target resolution still deliberately retains the final text.
The user subsequently reported “It is working now” in response to the requested
Codex/Teams check. This is user-confirmed installed delivery, not an independently
observed app-by-app cursor/clipboard/security matrix.

The corrected bundle was installed in place and its deep/strict signature
verified. Build provenance is `fca95322e5dc3c0de4c7cd35553ee7e5f90ce5dc-dirty`
(the uncommitted subrole correction above). Before quitting the old build, the
UI showed microphone failure; retained recovery logs show two failed wake
recoveries with helper timeouts. Restart for installation is not evidence that
the recurring microphone fault has been diagnosed or repaired.

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

## Web-editor compatibility follow-up

The user confirmed TextEdit insertion works on `0e7ca3d`, while Teams and Codex
still fail. Seven records for that build contain five AX acknowledgements and
two unavailable-focus blocks. Logs deliberately omit target app identity, so an
individual record cannot be attributed to Teams or Codex from the log alone.

After inspecting Teams with the UI tool, a read-only direct probe returned its
AXTextArea, writable AXSelectedText, and AXEnhancedUserInterface=true. The field
also advertises AXDOMIdentifier and AXDOMClassList. No prior flag measurement
was captured immediately before inspection, so activation is a supported causal
hypothesis, not a demonstrated before/after experiment. No Teams message was
sent and no draft text was inserted. Codex UI automation remains restricted by
the computer-use tool; no alternative route was used to control it.

The follow-up change enables writable AXManualAccessibility and
AXEnhancedUserInterface attributes only when the focused-element query fails.
It waits 50 ms and retries the query once after successful activation, checking
that the frontmost application is still the same. Unsupported attributes are
not written. The existing 100-ms per-call messaging bound remains; multiple calls
mean the whole lookup is not bounded to 100 ms. Accessibility initialization can
add latency to the first take. No field/window identity is fabricated and there
is no paste when focus remains unavailable.

This uses the activation mechanism described by
[Electron's accessibility documentation](https://github.com/electron/electron/blob/main/docs/tutorial/accessibility.md)
and [Chromium's accessibility design](https://www.chromium.org/developers/design-documents/accessibility/).
These sources describe framework behavior, not verified Codex or Teams behavior.

A field advertising AXDOMIdentifier now uses the existing single clipboard/Cmd+V
path even when AXSelectedText claims to be writable. Direct AX writes may not
notify a web editor's input handlers; the user's report and exposed metadata
justify using its normal paste path, but silent AX failure was not reproduced
with a controlled write. Unknown attribute metadata also uses the normal paste
path, provided existing role/secure/focus checks pass. Native text controls retain
direct AX writes. No selection, DOM attribute value or transcript is read to
classify a field. Clipboard restoration and ambiguous-write no-retry behavior
remain unchanged. Paste dispatch is still unconfirmed until visibly checked.

Validation: 20 focused insertion/engine tests passed in 0.85 seconds; all 371
tests passed in 7.55 seconds. New regressions cover lazy activation, exhausted
retry, foreground switch during activation, unsupported flags, and web-editor
single dispatch without an AX write. The final Teams/Codex end-to-end check is
still pending after installation; do not report both apps as fixed yet.

## Follow-through — 19 September 2026

`6044daf` now distinguishes unsupported subrole from failed or indeterminate
security inspection, blocking the latter before clipboard access. `f2378aa`
starts capture independently of AX resolution and accepts a target only if it
resolved before the first audio buffer; late resolution retains text for explicit
recovery. This can conservatively block cold/lazy or rapid-take insertion.
The full host suite now passes 386 tests; application-visible delivery on the
new local build remains unverified. A guarded TextEdit probe aborted because it
could not observe the disposable target as frontmost. See the
[full evidence and remaining checks](reliability-followthrough.md).

## Root cause, recurrence and durable prevention

The earlier enhancement exposed two compatibility problems: system-wide AX focus
lookup failed where app-owned lookup succeeded, and web editors needed bounded
lazy accessibility activation plus normal clipboard paste instead of trusting
AXSelectedText writability. The `20a25fa` repair was user-confirmed previously.

This recurrence followed the stricter `6044daf` security check: it classified
optional missing AXSubrole metadata (NoValue or successful nil) as unsafe. Normal
Chromium editors can omit the subrole, so valid insertion was rejected before
clipboard access. Tests mirrored that wrong assumption by explicitly expecting
NoValue to be blocked and never covering nil success. The correction accepts
optional absence and expands both native and web regression cases. Real query
failures, password fields and focus changes still block delivery.

The installed log also had an unavailable-target outcome. Its exact cause was
not established, and the subrole correction must not be described as diagnosing
that outcome. Privacy-safe records do not identify which destination produced
each event. All 386 tests pass; the user reports the corrected build works.

Future enhancements must follow the mandatory section in [AGENTS.md](../AGENTS.md).
Keep this incident visible from CLAUDE.md; do not replace the live three-app gate
with TextEdit-only testing or an AX acknowledgement. Preserve the separate
microphone timeout evidence and historical acceptance limits.

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

## Target discard and fresh-Electron lookup — 29 September 2026

Installed build c81273e logged the new target fields:

| Take | App | key_down→first_buffer (ms) | target_resolve_ms | target_status | first AX error | AX enabled |
|---|---|---|---|---|---|---|
| 1st | Codex (fresh launch) | 174.3 | 77.2 | missing | -25212 (NoValue) | False, so no retry |
| 2nd | Codex | 16.3 | 6.5 | accepted | 0 | |
| 1st | Teams (fresh launch) | 11.5 | 15.3 | late | 0 | |
| 2nd | Teams | 18.4 | 15.2 | accepted | 0 | |

Two causes:

1. The `f2378aa` rule accepted the key-down target only if it resolved before
   the first audio buffer. That buffer arrives 5-20 ms after key-down, the same
   order as an AX lookup (Teams about 15 ms), so correct targets were discarded
   at random (first Teams take: AX succeeded, status "late").
2. In a freshly launched Electron app the first AXFocusedUIElement query returns
   NoValue. The single retry ran only if `_enable_accessibility` returned True;
   for Codex it returned False, so there was no retry (first Codex take: "missing").

Fix: a target is accepted when it resolved at or before
`max(first_buffer, key_down + DICTATION_TARGET_GRACE_SECONDS)` (0.3 s); with no
key-down time the old first-buffer rule applies, and with no first buffer the
grace bound alone applies. "late" now means past that bound. `focused_target`
retries AXFocusedUIElement up to `DICTATION_TARGET_RETRY_ATTEMPTS` (4) times,
sleeping `DICTATION_TARGET_RETRY_INTERVAL_SECONDS` (0.04 s) before each, whether
or not accessibility enabling reported success, re-checking the frontmost app
after each attempt (change: no target, `target_app_switched`). It never
substitutes an application or window element. New diagnostic and latency field
`target_retry_count`. Unchanged: secure-field and subrole rules, the
`current != target` check in deliver_final (focus_changed), clipboard
ownership, fail-closed on app switch.

### Live check and follow-ups — 29–30 September 2026

Installed build `14541f3` (712 tests). On 29 September the user checked
dictation in TextEdit, Codex and Teams, including the first take after
relaunching Codex and Teams. All inserted.

Neither race occurred in that run. Every take had `target_retry_count` 0 and
every lookup finished before the first buffer. So the run shows the fix does
not regress. It does not yet show the fix working live.

Residual risk from the fix: a non-secure field that gains focus within 300 ms of
key-down, and is still focused at release, receives the text. The old rule held
it for recovery. Password fields stay blocked.

Follow-ups made on 30 September (D1):

- Retries at key-down keep the fixed 4-attempt cap. A deadline was tried and
  removed: the acceptance bound depends on the first buffer, which is known only
  at stop. Four of 138 logged takes had a first buffer later than 300 ms, and a
  deadline would have cut their retries.
- Delivery-time lookups keep 4 attempts.
- The recovery paste lookup (AppKit main thread) makes no retries (ruling R22).
  It keeps the click-time field. Worst case is about 0.5 s of AX timeouts.
- Worst-case lookup: about 1.06 s for an unresponsive app (0.1 s first query
  + up to 4 x 0.1 s enable calls + 4 x (0.04 + 0.1) s retries), and about 0.2 s for a fresh Electron app.

Status: **pending**. It stays pending until a take with `target_retry_count > 0`,
or one whose lookup finished between the first buffer and key-down + 300 ms, is
seen inserting correctly in Codex or Teams on an installed build.

### Installed check on `4ddb41d` — 30 September 2026

The user checked TextEdit, then the first and second takes after relaunching Codex and Teams. TextEdit and both second takes inserted. **Both first takes after a relaunch failed** with `permission_or_focus_unavailable`.

| Take | App | key_down→first_buffer (ms) | target_resolve_ms | status | first / last AX error | retries | AX enabled |
|---|---|---|---|---|---|---|---|
| 1st | Codex (fresh launch) | 57.6 | 316.5 | missing | -25212 / -25212 | 4 | False |
| 2nd | Codex | 32.9 | 3.2 | accepted | 0 | 0 | |
| 1st | Teams (fresh launch) | 202.9 | 321.9 | missing | -25212 / -25212 | 4 | False |
| 2nd | Teams | 46.2 | 18.9 | accepted | 0 | 0 | |
| — | TextEdit | 47.3 | 31.6 | accepted | 0 | 0 | |

The key-down lookup is the same as in `14541f3`; only the recovery paste changed. So this is not a regression from the 30 Sep follow-ups. The 29 Sep pass came from a run in which no retry was needed.

The R21 retries (4 × 40 ms) did not help. Every query in the fresh apps answered NoValue for about 320 ms. `target_ax_enabled` was False in both takes, so `_enable_accessibility` wrote nothing. It writes `AXManualAccessibility` and `AXEnhancedUserInterface` only when `AXUIElementIsAttributeSettable` reports them as settable.

Hypothesis, not yet tested: Electron accepts a write to `AXManualAccessibility` even though it does not report the attribute as settable. If so, the fresh app never gets asked to build its tree, and the tree only appears later by some other route.

Next step: a read-only probe of a freshly launched Codex or Teams. It should record what the settable check returns for each attribute, whether a direct write succeeds, and how long until `AXFocusedUIElement` answers. Do this before changing the rule that unsupported attributes are not written. **Status: the first-take failure is open.**
