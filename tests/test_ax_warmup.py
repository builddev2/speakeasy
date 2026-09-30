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


def test_run_swallows_ax_exception_and_clears_in_flight():
    class Exploding(FakeAX):
        def AXUIElementCopyAttributeValue(self, element, name, _):
            raise RuntimeError("boom")

    ex = RecordingExecutor()
    w = warmer(Exploding([]), executor=ex)
    assert w._run(42) == "failed"
    w.app_activated(42)
    assert len(ex.jobs) == 1


def test_read_back_error_is_failed_even_if_value_true():
    class ErrorAfterWrite(FakeAX):
        def AXUIElementCopyAttributeValue(self, element, name, _):
            if name == EUI and self.eui:
                return (-25204, True)
            return super().AXUIElementCopyAttributeValue(element, name, _)

    fake = ErrorAfterWrite([(-25212, None)])
    assert warmer(fake).warm(42) == "failed"
    assert writes(fake) == [("set", EUI, True)]


def test_run_logs_outcome_without_ax_content(capsys):
    w = warmer(FakeAX([(-25212, None)]))
    assert w._run(42) == "enabled"
    assert capsys.readouterr().out == "  → ax warm-up pid 42: enabled\n"
    w = warmer(FakeAX([(0, object())]))
    assert w._run(42) == "focus_answers"
    assert capsys.readouterr().out == ""


def test_run_logs_exception_type_only(capsys):
    class Exploding(FakeAX):
        def AXUIElementCopyAttributeValue(self, element, name, _):
            raise RuntimeError("secret title")

    assert warmer(Exploding([]))._run(7) == "failed"
    assert capsys.readouterr().out == "  → ax warm-up pid 7: failed (RuntimeError)\n"
