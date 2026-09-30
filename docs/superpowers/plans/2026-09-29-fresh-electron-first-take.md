# Fresh-Electron First Take Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the first dictation after relaunching Codex or Teams insert text. Do it by switching on the app's accessibility tree when the app comes to the front, not at key-down.

**Architecture:** A new `speakeasy/ax_warmup.py` owns one `ax-warmup` thread. Every time an app is activated (`NSWorkspaceDidActivateApplicationNotification`), it asks once for the focused element. If the app answers NoValue and `AXEnhancedUserInterface` is settable and off, it writes it once. The key-down lookup, the acceptance bound and the delivery checks stay the same. Separately, `_enable_accessibility` is fixed so that a write that reads back `true` counts as success.

**Tech Stack:** Python 3, PyObjC (ApplicationServices, AppKit), pytest with fake AX modules.

**Spec:** The evidence is in `docs/insertion-focus-regression.md`, section "Fresh-launch probe: cause found — 29 September 2026". The rules are in `AGENTS.md`, section "Recurring insertion regression: mandatory enhancement check". The user chose the scope on 29 Sep 2026: warm **any** app that needs it, not a fixed list.

## Background (read first)

Probe of a fresh Codex (`scripts/ax_fresh_probe.py`):
- `AXManualAccessibility`: unsupported (-25205) for settable, read and write.
- `AXEnhancedUserInterface`: settable, value false. Writing `true` returns **-25208 (NotImplemented)** after 3.9 ms, but reading it back gives `true`.
- `AXFocusedUIElement` stays NoValue indefinitely without that write. It answers **about 2154 ms after** the write, with role `AXWebArea`.
- Key-down retries last only about 320 ms. AGENTS.md forbids waiting longer at key-down or binding to a later focus, so the tree has to be built before key-down.

## Global Constraints

- Offline only. Read no content: no AX value, title or selection. Only error codes, booleans and roles.
- Never run AX calls on the AppKit main thread, `worker` (MLX) or `control` (recorder). Warm-up has its own single `ax-warmup` thread.
- Never write `AXManualAccessibility` from warm-up; Codex doesn't implement it. Warm-up writes only `AXEnhancedUserInterface`, at most once per activation, and only if its current value reads as not `true`.
- Every AX call on the warm-up path uses a messaging timeout of `0.1` s.
- Don't change `DICTATION_TARGET_GRACE_SECONDS` (0.3), `DICTATION_TARGET_RETRY_ATTEMPTS` (4), `DICTATION_TARGET_RETRY_INTERVAL_SECONDS` (0.04), the acceptance bound in `engine.py`, `deliver_final`, the subrole/secure-field rules or clipboard handling.
- New config: `AX_WARMUP_ATTEMPTS = 5` and `AX_WARMUP_RETRY_INTERVAL_SECONDS = 0.5`. A busy app (-25204) is retried within one activation, which covers roughly the first 2.5 s of a launch.
- Never activate Speakeasy's own process (skip `os.getpid()`).
- Tests must not touch real AX: inject a fake `ax` module. Run the full suite with an explicit Bash timeout of 600000 ms.
- Before running mutations, read memory `mutation-review-gotchas`: use `python -B` and clear `__pycache__`.

## Review Focus

1. **A native app that answers focus must never be written to.** If `AXFocusedUIElement` answers (err 0), there must be no settable check and no write. Test: `test_answering_app_is_never_written`.
2. **An app that is still launching** answers -25204 at first. Warm-up must retry (up to 5 attempts, 0.5 s apart) and not give up after one try. Test: `test_busy_app_is_retried_then_enabled`.
3. **Activations arriving in bursts** (switching apps back and forth quickly) must not queue up repeated work for the same process. A process already queued or running is skipped. Test: `test_duplicate_activation_is_not_queued_twice`.
4. **Quitting Speakeasy** during a warm-up must not hang or raise. `shutdown()` stops new work, and the retry loop ends once shutdown is set. Test: `test_shutdown_stops_retry_loop_and_ignores_new_activations`.
5. **A write that returns an error but takes effect** (Chromium returns -25208 and reads back `true`) must count as enabled, both in warm-up and in `_enable_accessibility`. Tests: `test_not_implemented_write_that_reads_back_true_counts_as_enabled` and `test_enable_accessibility_counts_read_back_true`.

---

### Task 1 (user, before any code): Probe a fresh Teams

This is a gate. The Teams evidence so far comes only from the log, so check that Teams behaves like Codex before building on it.

- [ ] **Step 1:** Ask the user to quit Teams, then run this in Terminal.app (which has Accessibility permission):

```bash
.venv/bin/python scripts/ax_fresh_probe.py --mode write --attr AXEnhancedUserInterface --bundle com.microsoft.teams2
```

- [ ] **Step 2:** Record the result in `docs/insertion-focus-regression.md` under "Fresh-launch probe", as an extra table row.
  - **Continue** if `AXEnhancedUserInterface` is settable and focus answers within the 8 s window.
  - **Stop and re-plan with the user** if it is not settable, or focus never answers.
- [ ] **Step 3:** Commit: `git commit -am "Record fresh Teams probe"`

### Task 2: Count a write that reads back true as enabled in `_enable_accessibility`

**Files:**
- Modify: `speakeasy/injector.py` (`_enable_accessibility`, and the comment in `focused_target` about enabling)
- Test: `tests/test_injector_reliability.py`

**Interfaces:**
- Produces: `_enable_accessibility(owner) -> bool`. Its signature is unchanged; it now returns True when a settable attribute's write returns 0 **or** reading it back gives `true`.

- [ ] **Step 1: Write the failing test** (add after `test_unsupported_accessibility_activation_does_not_write_or_retry`):

```python
def test_enable_accessibility_counts_read_back_true(monkeypatch):
    # Fresh Codex, probe 29 Sep 2026: the AXEnhancedUserInterface write returns
    # NotImplemented (-25208) yet the attribute reads back True and the tree
    # is built about 2 s later.
    import ApplicationServices as ax
    monkeypatch.setattr(ax, "AXUIElementIsAttributeSettable",
                        lambda e, name, _: (0, name == "AXEnhancedUserInterface"))
    writes = []
    monkeypatch.setattr(ax, "AXUIElementSetAttributeValue",
                        lambda e, name, value: writes.append(name) or -25208)
    monkeypatch.setattr(ax, "AXUIElementCopyAttributeValue",
                        lambda e, name, _: (0, True) if name == "AXEnhancedUserInterface" else (-25205, None))
    assert injector._enable_accessibility(object()) is True
    assert writes == ["AXEnhancedUserInterface"]


def test_enable_accessibility_failed_write_that_reads_back_false_is_not_enabled(monkeypatch):
    import ApplicationServices as ax
    monkeypatch.setattr(ax, "AXUIElementIsAttributeSettable",
                        lambda e, name, _: (0, name == "AXEnhancedUserInterface"))
    monkeypatch.setattr(ax, "AXUIElementSetAttributeValue", lambda *a: -25208)
    monkeypatch.setattr(ax, "AXUIElementCopyAttributeValue", lambda *a: (0, False))
    assert injector._enable_accessibility(object()) is False
```

- [ ] **Step 2: Run the tests to verify the first fails**

Run: `.venv/bin/python -m pytest tests/test_injector_reliability.py -k enable_accessibility -v`
Expected: `test_enable_accessibility_counts_read_back_true` FAILS (returns False). The other two pass.

- [ ] **Step 3: Implement**

```python
def _enable_accessibility(owner):
    import ApplicationServices as ax
    enabled = False
    for name in ("AXManualAccessibility", "AXEnhancedUserInterface"):
        error, writable = ax.AXUIElementIsAttributeSettable(owner, name, None)
        if error == 0 and writable:
            # Chromium applies AXEnhancedUserInterface but returns
            # NotImplemented (-25208): trust the read-back, not the status.
            result = ax.AXUIElementSetAttributeValue(owner, name, True)
            read_error, value = ax.AXUIElementCopyAttributeValue(owner, name, None)
            if result == 0 or (read_error == 0 and value is True):
                enabled = True
    return enabled
```

In `focused_target`, replace the comment above the retry loop with:

```python
        # Fresh Electron apps answer NoValue until their accessibility tree is
        # built, about 2 s after AXEnhancedUserInterface is written (probe,
        # 29 Sep 2026) -- longer than these retries. ax_warmup writes it when
        # the app is activated so the tree is normally ready by key-down.
        # Never substitute an application/window for an unidentified field.
```

In `test_retry_runs_even_when_accessibility_enable_returns_false`, change the comment to `# An app whose tree appears during the retries (e.g. already warming).`. The old comment claimed Codex's tree appears by the second attempt, which the probe disproved.

Note: `value is True` works because PyObjC returns a Python bool for a CFBoolean. If the tree test fakes return `1`, use `bool(value)` instead and keep the `(0, False)` test failing correctly.

- [ ] **Step 4: Run the file's tests**

Run: `.venv/bin/python -m pytest tests/test_injector_reliability.py -v`
Expected: all pass. The existing fakes in `_retry_setup` return a single reply iterator for every `AXUIElementCopyAttributeValue` call. If an `enable=True` test now fails because the read-back uses up a focus reply, route the fake by name: return `(0, True)` for `AXManualAccessibility`/`AXEnhancedUserInterface` and `next(iterator)` otherwise. **Do not change the expected values.**

- [ ] **Step 5: Commit**

```bash
git add speakeasy/injector.py tests/test_injector_reliability.py
git commit -m "Count an accessibility write that reads back true as enabled"
```

### Task 3: `AccessibilityWarmer`

**Files:**
- Create: `speakeasy/ax_warmup.py`
- Modify: `speakeasy/config.py` (add the two constants after `DICTATION_TARGET_RETRY_INTERVAL_SECONDS`)
- Test: `tests/test_ax_warmup.py`

**Interfaces:**
- Produces:
  - `class AccessibilityWarmer(ax_module=None, *, own_pid=None, sleep=time.sleep, attempts=None, interval=None, executor=None)`
  - `.app_activated(pid: int) -> None`. It can be called from any thread, returns immediately, and skips `own_pid`, `pid <= 0`, and any process already queued or running.
  - `.warm(pid: int) -> str`. It runs synchronously (the thread body, and the entry point for tests) and returns one of `"focus_answers" | "enabled" | "already_enabled" | "unsupported" | "busy" | "failed" | "shutdown"`.
  - `.shutdown() -> None`
  - `activated_pid(notification) -> int | None`. Reads `userInfo()[NSWorkspaceApplicationKey].processIdentifier()` and returns None on any missing piece.

- [ ] **Step 1: Add the config constants**

```python
# A freshly launched Chromium/Electron app (Codex, Teams) builds its
# accessibility tree only after AXEnhancedUserInterface is written, and takes
# about 2 s to do so (probe, 29 Sep 2026) -- far longer than the key-down
# retries. ax_warmup writes it when the app is activated. An app still
# launching answers CannotComplete; retry that this many times, this far apart
# (covers about the first 2.5 s of a launch).
AX_WARMUP_ATTEMPTS = 5
AX_WARMUP_RETRY_INTERVAL_SECONDS = 0.5
```

- [ ] **Step 2: Write the failing tests** in `tests/test_ax_warmup.py`:

```python
from concurrent.futures import Future

from speakeasy import ax_warmup
from speakeasy.ax_warmup import AccessibilityWarmer

EUI = "AXEnhancedUserInterface"


class FakeAX:
    """Fake ApplicationServices: `focus` answers successive focus queries."""

    def __init__(self, focus, settable=True, eui=False, write_result=-25208,
                 applies=True):
        self.focus = list(focus)
        self.settable = settable
        self.eui = eui
        self.write_result = write_result
        self.applies = applies
        self.calls = []

    def AXUIElementCreateApplication(self, pid):
        return ("app", pid)

    def AXUIElementSetMessagingTimeout(self, element, seconds):
        self.calls.append(("timeout", seconds))
        return 0

    def AXUIElementCopyAttributeValue(self, element, name, _):
        self.calls.append(("copy", name))
        if name == "AXFocusedUIElement":
            return self.focus.pop(0)
        if name == EUI:
            return (0, self.eui)
        return (-25205, None)

    def AXUIElementIsAttributeSettable(self, element, name, _):
        self.calls.append(("settable", name))
        return (0, self.settable and name == EUI)

    def AXUIElementSetAttributeValue(self, element, name, value):
        self.calls.append(("set", name, value))
        if self.applies:
            self.eui = True
        return self.write_result


def warmer(fake, **kw):
    kw.setdefault("sleep", lambda s: None)
    return AccessibilityWarmer(fake, own_pid=1, **kw)


def writes(fake):
    return [c for c in fake.calls if c[0] == "set"]


def test_answering_app_is_never_written():
    fake = FakeAX([(0, object())])
    assert warmer(fake).warm(42) == "focus_answers"
    assert writes(fake) == []
    assert not any(c[0] == "settable" for c in fake.calls)


def test_not_implemented_write_that_reads_back_true_counts_as_enabled():
    fake = FakeAX([(-25212, None)])
    assert warmer(fake).warm(42) == "enabled"
    assert writes(fake) == [("set", EUI, True)]


def test_write_that_does_not_apply_is_failed():
    fake = FakeAX([(-25212, None)], applies=False)
    assert warmer(fake).warm(42) == "failed"


def test_already_enabled_is_not_rewritten():
    fake = FakeAX([(-25212, None)], eui=True)
    assert warmer(fake).warm(42) == "already_enabled"
    assert writes(fake) == []


def test_unsettable_app_is_not_written():
    fake = FakeAX([(-25212, None)], settable=False)
    assert warmer(fake).warm(42) == "unsupported"
    assert writes(fake) == []


def test_manual_accessibility_is_never_touched():
    fake = FakeAX([(-25212, None)])
    warmer(fake).warm(42)
    assert not any(len(c) > 1 and c[1] == "AXManualAccessibility" for c in fake.calls)


def test_busy_app_is_retried_then_enabled():
    sleeps = []
    fake = FakeAX([(-25204, None), (-25204, None), (-25212, None)])
    assert warmer(fake, sleep=sleeps.append).warm(42) == "enabled"
    assert sleeps == [0.5, 0.5]


def test_busy_app_gives_up_after_five_attempts():
    sleeps = []
    fake = FakeAX([(-25204, None)] * 5)
    assert warmer(fake, sleep=sleeps.append).warm(42) == "busy"
    assert len(sleeps) == 4
    assert writes(fake) == []


def test_other_errors_fail_without_write():
    for error in (-25202, -25211, -25200):
        fake = FakeAX([(error, None)])
        assert warmer(fake).warm(42) == "failed"
        assert writes(fake) == []


def test_every_ax_call_uses_a_tenth_second_timeout():
    fake = FakeAX([(-25212, None)])
    warmer(fake).warm(42)
    assert ("timeout", 0.1) in fake.calls
    assert all(c[1] == 0.1 for c in fake.calls if c[0] == "timeout")


class RecordingExecutor:
    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args):
        self.jobs.append((fn, args))
        return Future()

    def shutdown(self, wait=False, cancel_futures=False):
        pass


def test_own_process_and_invalid_pids_are_skipped():
    ex = RecordingExecutor()
    w = warmer(FakeAX([]), executor=ex)
    w.app_activated(1)
    w.app_activated(0)
    w.app_activated(-5)
    assert ex.jobs == []


def test_duplicate_activation_is_not_queued_twice():
    ex = RecordingExecutor()
    w = warmer(FakeAX([(-25212, None)]), executor=ex)
    w.app_activated(42)
    w.app_activated(42)
    assert len(ex.jobs) == 1
    fn, args = ex.jobs[0]
    fn(*args)            # job finishes: 42 is no longer in flight
    w.app_activated(42)
    assert len(ex.jobs) == 2


def test_shutdown_stops_retry_loop_and_ignores_new_activations():
    ex = RecordingExecutor()
    fake = FakeAX([(-25204, None)] * 5)
    holder = {}

    def sleep(_):
        holder["w"].shutdown()

    w = holder["w"] = warmer(fake, sleep=sleep, executor=ex)
    assert w.warm(42) == "shutdown"
    assert len([c for c in fake.calls if c == ("copy", "AXFocusedUIElement")]) == 1
    w.app_activated(43)
    assert ex.jobs == []


def test_activated_pid_reads_notification_and_tolerates_gaps():
    from types import SimpleNamespace
    app = SimpleNamespace(processIdentifier=lambda: 77)
    note = SimpleNamespace(userInfo=lambda: {ax_warmup.NSWorkspaceApplicationKey: app})
    assert ax_warmup.activated_pid(note) == 77
    assert ax_warmup.activated_pid(SimpleNamespace(userInfo=lambda: None)) is None
    assert ax_warmup.activated_pid(SimpleNamespace(userInfo=lambda: {})) is None
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ax_warmup.py -v`
Expected: collection error, `ModuleNotFoundError: speakeasy.ax_warmup`.

- [ ] **Step 4: Implement `speakeasy/ax_warmup.py`**

```python
"""Switch on a fresh Chromium/Electron app's accessibility tree before dictation.

A freshly launched Codex answers AXFocusedUIElement with NoValue indefinitely
until AXEnhancedUserInterface is written. The write returns NotImplemented yet
reads back True, and focus answers about 2 s later (probe, 29 Sep 2026; see
docs/insertion-focus-regression.md). Key-down retries last about 0.3 s and
must not wait longer, so the write happens when the app is activated instead.

Only AX metadata (error codes, one boolean) is read -- never values. All work
runs on one `ax-warmup` thread: never the main thread, worker or control.
AXManualAccessibility is not written: Codex does not implement it.
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from AppKit import NSWorkspaceApplicationKey

from . import config

_NO_VALUE = -25212
_CANNOT_COMPLETE = -25204
_ATTRIBUTE = "AXEnhancedUserInterface"


def activated_pid(notification):
    try:
        info = notification.userInfo()
        app = info[NSWorkspaceApplicationKey] if info else None
        return int(app.processIdentifier()) if app is not None else None
    except Exception:
        return None


class AccessibilityWarmer:
    def __init__(self, ax_module=None, *, own_pid=None, sleep=time.sleep,
                 attempts=None, interval=None, executor=None):
        if ax_module is None:
            import ApplicationServices as ax_module
        self._ax = ax_module
        self._own_pid = os.getpid() if own_pid is None else own_pid
        self._sleep = sleep
        self._attempts = config.AX_WARMUP_ATTEMPTS if attempts is None else attempts
        self._interval = (config.AX_WARMUP_RETRY_INTERVAL_SECONDS
                          if interval is None else interval)
        self._executor = executor or ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="ax-warmup")
        self._lock = threading.Lock()
        self._in_flight: set[int] = set()
        self._stopped = False

    def app_activated(self, pid) -> None:
        if pid is None or pid <= 0 or pid == self._own_pid:
            return
        with self._lock:
            if self._stopped or pid in self._in_flight:
                return
            self._in_flight.add(pid)
        try:
            self._executor.submit(self._run, pid)
        except RuntimeError:  # executor already shut down
            with self._lock:
                self._in_flight.discard(pid)

    def _run(self, pid):
        try:
            return self.warm(pid)
        except Exception:
            return "failed"
        finally:
            with self._lock:
                self._in_flight.discard(pid)

    def warm(self, pid) -> str:
        outcome = "busy"
        for attempt in range(self._attempts):
            if attempt:
                self._sleep(self._interval)
            if self._stopped:
                return "shutdown"
            outcome = self._try(pid)
            if outcome != "busy":
                break
        return outcome

    def _try(self, pid) -> str:
        ax = self._ax
        owner = ax.AXUIElementCreateApplication(pid)
        ax.AXUIElementSetMessagingTimeout(owner, .1)
        error, element = ax.AXUIElementCopyAttributeValue(
            owner, "AXFocusedUIElement", None)
        if error == 0 and element is not None:
            return "focus_answers"
        if error == _CANNOT_COMPLETE:
            return "busy"
        if error != _NO_VALUE:
            return "failed"
        error, settable = ax.AXUIElementIsAttributeSettable(owner, _ATTRIBUTE, None)
        if error != 0 or not settable:
            return "unsupported"
        error, value = ax.AXUIElementCopyAttributeValue(owner, _ATTRIBUTE, None)
        if error == 0 and value is True:
            return "already_enabled"
        # Chromium applies the value but answers NotImplemented: trust the read-back.
        ax.AXUIElementSetAttributeValue(owner, _ATTRIBUTE, True)
        error, value = ax.AXUIElementCopyAttributeValue(owner, _ATTRIBUTE, None)
        return "enabled" if error == 0 and value is True else "failed"

    def shutdown(self) -> None:
        with self._lock:
            self._stopped = True
        self._executor.shutdown(wait=False, cancel_futures=True)
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_ax_warmup.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add speakeasy/ax_warmup.py speakeasy/config.py tests/test_ax_warmup.py
git commit -m "Add AccessibilityWarmer: enable a fresh app's AX tree on activation"
```

### Task 4: Wire the warmer into the menu-bar app, and update the docs

**Files:**
- Modify: `speakeasy/ui/menubar.py` (import, `applicationDidFinishLaunching_`, a new `appDidActivate_`, `applicationWillTerminate_`)
- Modify: `AGENTS.md` (Threading model: add an `ax-warmup` bullet after `calendar`; Recurring insertion section: add one bullet)
- Modify: `docs/insertion-focus-regression.md` (status line of "Fresh-launch probe")

**Interfaces:**
- Consumes: `AccessibilityWarmer`, `activated_pid` from Task 3.

- [ ] **Step 1: Wire it up.** In the `AppKit` import list add `NSWorkspaceDidActivateApplicationNotification`. In `applicationDidFinishLaunching_`, right after the `systemDidWake:` observer:

```python
        from ..ax_warmup import AccessibilityWarmer

        # Fresh Electron apps build their AX tree ~2 s after being asked;
        # ask on activation so the first dictation finds its field.
        self.ax_warmer = AccessibilityWarmer()
        NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
            self, b"appDidActivate:", NSWorkspaceDidActivateApplicationNotification, None,
        )
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is not None:
            self.ax_warmer.app_activated(front.processIdentifier())
```

Add the method next to `systemDidWake_`:

```python
    def appDidActivate_(self, notification):
        warmer = getattr(self, "ax_warmer", None)
        if warmer is not None:
            from ..ax_warmup import activated_pid
            warmer.app_activated(activated_pid(notification))
```

In `applicationWillTerminate_`, before `self.engine.shutdown()`:

```python
        if getattr(self, "ax_warmer", None) is not None:
            self.ax_warmer.shutdown()
```

Also add `self.ax_warmer = None` in `init` next to `self.calendar_sync = None`.

- [ ] **Step 2: AGENTS.md.** Threading model, after the `calendar` bullet:

```markdown
- **`ax-warmup`** (1 thread, `ax_warmup.AccessibilityWarmer`) — on every app
  activation, one focus query; if the app answers NoValue and
  `AXEnhancedUserInterface` is settable and off, writes it once (Chromium
  returns NotImplemented yet applies it; trust the read-back). Fresh Electron
  apps need ~2 s after that write to answer focus, longer than key-down
  retries. AX metadata only, 0.1 s timeouts; never main, `worker` or `control`.
```

In "Recurring insertion regression", add this bullet after the "Preserve app-owned focus lookup" bullet:

```markdown
- A fresh Codex/Teams answers focus only ~2 s after `AXEnhancedUserInterface`
  is written, and does not implement `AXManualAccessibility`. Keep the
  activation-time warm-up (`speakeasy/ax_warmup.py`); do not "fix" a first-take
  miss by lengthening key-down retries or relaxing the acceptance bound.
```

- [ ] **Step 3: Regression doc.** Change the status line of "Fresh-launch probe" to: `**Status: fix implemented on branch fix-fresh-electron-first-take (activation warm-up); pending the installed three-app check.**`

- [ ] **Step 4: Full suite**

Run (Bash timeout 600000): `.venv/bin/python -m pytest -q`
Expected: all pass (721 before this branch, plus the new tests).

- [ ] **Step 5: Commit**

```bash
git add speakeasy/ui/menubar.py AGENTS.md docs/insertion-focus-regression.md
git commit -m "Warm accessibility on app activation; document the ax-warmup thread"
```

### Task 5: Review (opus, by mutation)

- [ ] A fresh Opus reviewer checks the whole branch against this plan and AGENTS.md's insertion rules. They should break each of the following, confirm the suite fails, and then restore it (use `python -B` and clear `__pycache__`):
  - remove the `focus_answers` early return;
  - accept `result == 0` only (drop the read-back) in both places;
  - drop the `busy` retry;
  - drop the in-flight dedupe;
  - drop the `_stopped` check in `warm`;
  - skip the own-pid check.
- [ ] Any mutation the suite doesn't notice gets a test before merge.

### Task 6 (user + me): Installed check — the acceptance gate

- [ ] `scripts/build_app.sh --install` (install in place; this preserves the TCC grants). Relaunch Speakeasy.
- [ ] The user checks, on the installed build, without sending any messages:
  1. TextEdit take.
  2. Quit and relaunch Codex. Click into the composer and dictate the **first take**, then a second take.
  3. The same for Teams.
- [ ] Read the dictation log: the first take in each fresh app should show `target_first_ax_error` 0 (or a successful retry) and `target_status` `accepted`. Record the table in `docs/insertion-focus-regression.md`.
- [ ] Known limit to state in the doc: a take started within ~2.2 s of the app first activating can still miss, because the tree isn't built yet.
- [ ] Only after all three apps insert, set the status to **fixed on `<commit>`**, update memory `dictation-target-timing`, and mark this plan done.

## Status

- 29 Sep 2026: cause found (probe). Plan written on branch `fix-fresh-electron-first-take`. Task 1 (Teams probe) not yet run. No code changed.
