from speakeasy import injector
# Keep the real function before the autouse fixture replaces host focus access.
from speakeasy.injector import focused_target as query_focused_target
from speakeasy.injector import _attribute as query_attribute


class Item:
    def __init__(self, values):
        self.values = values
    def types(self):
        return list(self.values)
    def dataForType_(self, kind):
        return self.values[kind]


class Pasteboard:
    def __init__(self, values):
        self.items = [Item(values)] if values is not None else []
        self.version = 0
    def pasteboardItems(self):
        return self.items
    def changeCount(self):
        return self.version
    def clearContents(self):
        self.items = []
        self.version += 1
    def writeObjects_(self, items):
        self.items = items
        self.version += 1
        return True
    def setString_forType_(self, value, kind):
        self.items = [Item({kind: value})]
        self.version += 1
        return True


def test_snapshot_preserves_empty_nontext_and_absent_clipboard(monkeypatch):
    for values in (None, {"text": ""}, {"image": b"image", "custom": b"bytes"}):
        pb = Pasteboard(values)
        monkeypatch.setattr(injector, "_pasteboard", lambda: pb)
        monkeypatch.setattr(injector, "_make_item", lambda values: Item(dict(values)))
        saved = injector.read_clipboard()
        injector._set_clipboard("final")
        saved.restore_count = pb.changeCount()
        injector.restore_clipboard(saved)
        assert [item.values for item in pb.items] == ([] if values is None else [values])


def test_restore_does_not_overwrite_newer_clipboard(monkeypatch):
    pb = Pasteboard({"image": b"old"})
    monkeypatch.setattr(injector, "_pasteboard", lambda: pb)
    saved = injector.read_clipboard()
    saved.restore_count = pb.changeCount()
    pb.setString_forType_("new copy", "text")
    injector.restore_clipboard(saved)
    assert pb.items[0].values == {"text": "new copy"}


def test_delivery_matrix_and_1000_attempt_soak(monkeypatch):
    import ApplicationServices as ax
    target = object()
    monkeypatch.setattr(injector, "focused_target", lambda **kwargs: target)
    posts = []
    writes = []
    monkeypatch.setattr(injector, "insert_text", lambda text, **k: posts.append(text))
    monkeypatch.setattr(ax, "AXUIElementSetAttributeValue", lambda *args: writes.append(1) or 0)
    for attempt in range(1000):
        case = attempt % 5
        role = "AXTextField" if case in (0, 1, 4) else "AXGroup"
        monkeypatch.setattr(injector, "_attribute", lambda e, n: (0, role) if n == "AXRole" else
                            ((0, "AXSecureTextField") if case == 1 else (-25205, None)))
        monkeypatch.setattr(ax, "AXUIElementIsAttributeSettable", lambda *a: (0, case == 0))
        outcome = injector.deliver_final("final", object() if case == 3 else target)
        assert outcome == ["ax_acknowledged", "secure_or_unknown_field",
                           "dispatched_unconfirmed", "focus_changed",
                           "dispatched_unconfirmed"][case]
    assert len(posts) == 400
    assert len(writes) == 200


def test_ambiguous_ax_write_never_falls_back(monkeypatch):
    import ApplicationServices as ax
    target = injector.focused_target()
    monkeypatch.setattr(injector, "_attribute", lambda e, n: (0, "AXTextArea") if n == "AXRole" else (-25205, None))
    monkeypatch.setattr(ax, "AXUIElementIsAttributeSettable", lambda *a: (0, True))
    monkeypatch.setattr(ax, "AXUIElementSetAttributeValue", lambda *a: -25202)
    monkeypatch.setattr(injector, "insert_text", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    assert injector.deliver_final("final", target) == "delivery_unknown"


def test_copy_during_inference_is_the_clipboard_restored(monkeypatch):
    pb = Pasteboard({"text": "before inference"})
    monkeypatch.setattr(injector, "_pasteboard", lambda: pb)
    monkeypatch.setattr(injector, "_make_item", lambda values: Item(dict(values)))
    monkeypatch.setattr(injector.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(injector, "_post_cmd_v", lambda: None)
    previous = injector.read_clipboard()
    pb.setString_forType_("copied during inference", "text")
    injector.insert_text("final")
    injector.restore_clipboard(previous)
    assert pb.items[0].values == {"text": "copied during inference"}


def test_permission_or_missing_focus_never_inserts(monkeypatch):
    monkeypatch.setattr(injector, "focused_target", lambda **kwargs: None)
    monkeypatch.setattr(injector, "insert_text", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    assert injector.deliver_final("final", None) == "permission_or_focus_unavailable"


def test_focus_change_during_clipboard_settle_never_posts(monkeypatch):
    pb = Pasteboard({"text": "previous"})
    target = object()
    focus = [target]
    posts = []
    monkeypatch.setattr(injector, "_pasteboard", lambda: pb)
    monkeypatch.setattr(injector, "_make_item", lambda values: Item(dict(values)))
    monkeypatch.setattr(injector, "focused_target", lambda **kwargs: focus[0])
    monkeypatch.setattr(injector, "_post_cmd_v", lambda: posts.append(1))
    monkeypatch.setattr(injector.time, "sleep", lambda seconds: focus.__setitem__(0, object()))
    assert injector.deliver_final("final", target) == "focus_changed"
    injector.restore_clipboard(None)
    assert posts == []
    assert pb.items[0].values == {"text": "previous"}


def test_focus_queries_owning_application_not_system_proxy(monkeypatch):
    import ApplicationServices as ax
    from types import SimpleNamespace
    target = object()
    owner = object()
    app = SimpleNamespace(processIdentifier=lambda: 123)
    workspace = SimpleNamespace(frontmostApplication=lambda: app)
    monkeypatch.setattr(injector, "NSWorkspace", SimpleNamespace(sharedWorkspace=lambda: workspace))
    monkeypatch.setattr(ax, "AXUIElementCreateSystemWide", lambda: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(ax, "AXUIElementCreateApplication", lambda pid: owner if pid == 123 else None)
    monkeypatch.setattr(ax, "AXUIElementSetMessagingTimeout", lambda *args: 0)
    calls = []
    def copy(element, name, result):
        calls.append((element, name))
        return 0, target
    monkeypatch.setattr(ax, "AXUIElementCopyAttributeValue", copy)
    assert query_focused_target() is target
    assert calls == [(owner, "AXFocusedUIElement")]


def test_focus_query_fails_closed_on_switch_or_unavailable_field(monkeypatch):
    import ApplicationServices as ax
    from types import SimpleNamespace
    app = lambda pid: SimpleNamespace(processIdentifier=lambda: pid)
    monkeypatch.setattr(ax, "AXUIElementCreateApplication", lambda pid: object())
    monkeypatch.setattr(ax, "AXUIElementSetMessagingTimeout", lambda *args: 0)
    for error, element, after in [(0, object(), app(456)), (-25204, None, app(123)),
                                   (0, None, app(123)), (0, object(), None)]:
        # Unavailable fields are retried (app unchanged), so keep it frontmost.
        foreground = iter([app(123), after] + [app(123)] * 8)
        workspace = SimpleNamespace(frontmostApplication=lambda: next(foreground))
        monkeypatch.setattr(injector, "NSWorkspace", SimpleNamespace(sharedWorkspace=lambda: workspace))
        monkeypatch.setattr(ax, "AXUIElementCopyAttributeValue", lambda *args: (error, element))
        monkeypatch.setattr(ax, "AXUIElementIsAttributeSettable", lambda *args: (-25205, False))
        monkeypatch.setattr(injector.time, "sleep", lambda seconds: None)
        assert query_focused_target() is None


def test_web_editor_uses_one_paste_despite_writable_selected_text(monkeypatch):
    import ApplicationServices as ax
    target = injector.focused_target()
    monkeypatch.setattr(injector, "_attribute", lambda e, n: (0, "AXTextArea") if n == "AXRole" else (-25205, None))
    monkeypatch.setattr(ax, "AXUIElementIsAttributeSettable", lambda *args: (0, True))
    monkeypatch.setattr(ax, "AXUIElementCopyAttributeNames", lambda *args: (0, ["AXDOMIdentifier"]))
    monkeypatch.setattr(ax, "AXUIElementSetAttributeValue", lambda *args: (_ for _ in ()).throw(AssertionError()))
    pasted = []
    monkeypatch.setattr(injector, "insert_text", lambda text, **kwargs: pasted.append(text))
    assert injector.deliver_final("final", target) == "dispatched_unconfirmed"
    assert pasted == ["final"]


def test_unsupported_accessibility_activation_does_not_write_or_retry(monkeypatch):
    import ApplicationServices as ax
    monkeypatch.setattr(ax, "AXUIElementIsAttributeSettable", lambda *args: (-25205, False))
    monkeypatch.setattr(ax, "AXUIElementSetAttributeValue", lambda *args: (_ for _ in ()).throw(AssertionError()))
    assert not injector._enable_accessibility(object())


def test_security_inspection_errors_block_before_clipboard(monkeypatch):
    import ApplicationServices as ax
    # Exercise the real status-preserving query, not the shared metadata fake.
    monkeypatch.setattr(injector, "_attribute", query_attribute)
    target = injector.focused_target()
    monkeypatch.setattr(injector, "insert_text", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    for error in (-25204, -25211, -25202):
        monkeypatch.setattr(ax, "AXUIElementCopyAttributeValue",
                            lambda e, n, out: (0, "AXTextArea") if n == "AXRole" else (error, None))
        assert injector.deliver_final("final", target) == "secure_or_unknown_field"


def test_supported_and_unsupported_subroles_on_native_and_web_fields(monkeypatch):
    import ApplicationServices as ax
    monkeypatch.setattr(injector, "_attribute", query_attribute)
    target = injector.focused_target()
    writes, pastes = [], []
    monkeypatch.setattr(ax, "AXUIElementIsAttributeSettable", lambda *a: (0, True))
    monkeypatch.setattr(ax, "AXUIElementSetAttributeValue", lambda *a: writes.append(1) or 0)
    monkeypatch.setattr(injector, "insert_text", lambda *a, **k: pastes.append(1))
    for web in (False, True):
        monkeypatch.setattr(ax, "AXUIElementCopyAttributeNames", lambda *a: (0, ["AXDOMIdentifier"] if web else []))
        for subrole in ((-25205, None), (-25212, None), (0, None),
                        (0, "AXSearchField"), (0, "")):
            monkeypatch.setattr(ax, "AXUIElementCopyAttributeValue", lambda e, n, out: (0, "AXTextArea") if n == "AXRole" else subrole)
            assert injector.deliver_final("final", target) == ("dispatched_unconfirmed" if web else "ax_acknowledged")
    assert len(writes) == len(pastes) == 5


def _fake_frontmost(monkeypatch, apps, bundle="com.example.Electron"):
    import ApplicationServices as ax
    from types import SimpleNamespace
    foreground = iter(apps)
    workspace = SimpleNamespace(frontmostApplication=lambda: next(foreground))
    monkeypatch.setattr(injector, "NSWorkspace", SimpleNamespace(sharedWorkspace=lambda: workspace))
    monkeypatch.setattr(ax, "AXUIElementCreateApplication", lambda pid: object())
    monkeypatch.setattr(ax, "AXUIElementSetMessagingTimeout", lambda *args: 0)


def _app(pid=123, bundle="com.example.Electron"):
    from types import SimpleNamespace
    return SimpleNamespace(processIdentifier=lambda: pid,
                           bundleIdentifier=lambda: bundle)


def test_diagnostics_record_direct_success_without_enabling(monkeypatch):
    import ApplicationServices as ax
    target = object()
    _fake_frontmost(monkeypatch, [_app(), _app()])
    monkeypatch.setattr(ax, "AXUIElementCopyAttributeValue", lambda *a: (0, target))
    diagnostics = {}
    assert query_focused_target(diagnostics) is target
    assert diagnostics == {
        "target_first_ax_error": 0, "target_ax_enabled": False,
        "target_retry_ax_error": None, "target_retry_count": 0,
        "target_app_switched": False, "target_app": "com.example.Electron",
        "target_deadline_stop": False,
    }


def test_diagnostics_record_enable_and_retry_error_codes(monkeypatch):
    import ApplicationServices as ax
    _fake_frontmost(monkeypatch, [_app()] * 8)
    monkeypatch.setattr(ax, "AXUIElementIsAttributeSettable",
                        lambda e, name, _: (0, name == "AXManualAccessibility"))
    monkeypatch.setattr(ax, "AXUIElementSetAttributeValue", lambda *a: 0)
    monkeypatch.setattr(injector.time, "sleep", lambda seconds: None)
    replies = iter([(-25204, None)] + [(-25212, None)] * 4)
    monkeypatch.setattr(ax, "AXUIElementCopyAttributeValue", lambda *a: next(replies))
    diagnostics = {}
    assert query_focused_target(diagnostics) is None
    assert diagnostics["target_first_ax_error"] == -25204
    assert diagnostics["target_ax_enabled"] is True
    assert diagnostics["target_retry_ax_error"] == -25212
    assert diagnostics["target_retry_count"] == 4
    assert diagnostics["target_app_switched"] is False


def _retry_setup(monkeypatch, replies, apps, enable=False):
    """Fake AX: `replies` answers successive AXFocusedUIElement queries."""
    import ApplicationServices as ax
    _fake_frontmost(monkeypatch, apps)
    monkeypatch.setattr(ax, "AXUIElementIsAttributeSettable",
                        lambda e, name, _: (0, enable and name == "AXManualAccessibility"))
    monkeypatch.setattr(ax, "AXUIElementSetAttributeValue", lambda *a: 0)
    sleeps, queries = [], []
    monkeypatch.setattr(injector.time, "sleep", sleeps.append)
    iterator = iter(replies)

    def copy(element, name, _):
        queries.append(name)
        return next(iterator)
    monkeypatch.setattr(ax, "AXUIElementCopyAttributeValue", copy)
    return sleeps, queries


def test_retry_runs_even_when_accessibility_enable_returns_false(monkeypatch):
    # Codex fresh launch: first query NoValue, enable reports False; the
    # accessibility tree appears by the second attempt.
    target = object()
    sleeps, queries = _retry_setup(
        monkeypatch, [(-25212, None), (-25212, None), (0, target)],
        [_app()] * 6, enable=False)
    diagnostics = {}
    assert query_focused_target(diagnostics) is target
    assert diagnostics["target_ax_enabled"] is False
    assert diagnostics["target_first_ax_error"] == -25212
    assert diagnostics["target_retry_ax_error"] == 0
    assert diagnostics["target_retry_count"] == 2
    assert sleeps == [.04, .04]
    assert len(queries) == 3


def test_retry_succeeds_on_first_attempt_records_count_one(monkeypatch):
    target = object()
    sleeps, _ = _retry_setup(
        monkeypatch, [(-25212, None), (0, target)], [_app()] * 4)
    diagnostics = {}
    assert query_focused_target(diagnostics) is target
    assert diagnostics["target_retry_count"] == 1
    assert sleeps == [.04]


def test_all_retry_attempts_failing_returns_none_with_count_four(monkeypatch):
    sleeps, queries = _retry_setup(
        monkeypatch, [(-25212, None)] * 5, [_app()] * 8)
    diagnostics = {}
    assert query_focused_target(diagnostics) is None
    assert diagnostics["target_retry_count"] == 4
    assert diagnostics["target_retry_ax_error"] == -25212
    assert sleeps == [.04] * 4
    assert len(queries) == 5
    assert diagnostics["target_app_switched"] is False


def test_retry_stops_and_fails_closed_when_frontmost_app_changes(monkeypatch):
    # initial app, post-first check, attempt 1 ok, then the app changes.
    sleeps, queries = _retry_setup(
        monkeypatch, [(-25212, None), (-25212, None), (0, object())],
        [_app(), _app(), _app(), _app(456)])
    diagnostics = {}
    assert query_focused_target(diagnostics) is None
    assert diagnostics["target_app_switched"] is True
    assert diagnostics["target_retry_count"] == 2
    assert len(queries) == 3


def test_retry_success_is_discarded_if_app_changed_during_that_attempt(monkeypatch):
    _retry_setup(monkeypatch, [(-25212, None), (0, object())],
                 [_app(), _app(), _app(456)])
    diagnostics = {}
    assert query_focused_target(diagnostics) is None
    assert diagnostics["target_app_switched"] is True
    assert diagnostics["target_retry_count"] == 1


def test_diagnostics_record_app_switch_and_no_enable(monkeypatch):
    import ApplicationServices as ax
    _fake_frontmost(monkeypatch, [_app(), _app(456)])
    monkeypatch.setattr(ax, "AXUIElementCopyAttributeValue", lambda *a: (-25204, None))
    monkeypatch.setattr(ax, "AXUIElementIsAttributeSettable",
                        lambda *a: (_ for _ in ()).throw(AssertionError()))
    diagnostics = {}
    assert query_focused_target(diagnostics) is None
    assert diagnostics["target_app_switched"] is True
    assert diagnostics["target_ax_enabled"] is False
    assert diagnostics["target_retry_ax_error"] is None


def _clock(values):
    iterator = iter(values)
    return lambda: next(iterator)


def test_deadline_stops_retries_before_second_sleep(monkeypatch):
    # A target would be returned on attempt 2, but the deadline cuts it off.
    sleeps, queries = _retry_setup(
        monkeypatch, [(-25212, None), (-25212, None), (0, object())],
        [_app()] * 6)
    diagnostics = {}
    result = query_focused_target(
        diagnostics, deadline_ns=1_000_000_000,
        clock_ns=_clock([0, 999_000_000]))
    assert result is None
    assert diagnostics["target_deadline_stop"] is True
    assert diagnostics["target_retry_count"] == 1
    assert sleeps == [.04]
    assert len(queries) == 2


def test_deadline_already_past_makes_no_retry(monkeypatch):
    sleeps, queries = _retry_setup(
        monkeypatch, [(-25212, None), (0, object())], [_app()] * 4)
    diagnostics = {}
    result = query_focused_target(
        diagnostics, deadline_ns=100, clock_ns=lambda: 1_000)
    assert result is None
    assert diagnostics["target_deadline_stop"] is True
    assert diagnostics["target_retry_count"] == 0
    assert sleeps == []
    assert len(queries) == 1


def test_no_deadline_keeps_four_attempts_and_no_stop_flag(monkeypatch):
    sleeps, queries = _retry_setup(
        monkeypatch, [(-25212, None)] * 5, [_app()] * 8)
    diagnostics = {}
    assert query_focused_target(diagnostics) is None
    assert diagnostics["target_deadline_stop"] is False
    assert diagnostics["target_retry_count"] == 4
    assert sleeps == [.04] * 4


def test_retry_attempts_zero_queries_once_but_still_enables(monkeypatch):
    import ApplicationServices as ax
    enabled = []
    sleeps, queries = _retry_setup(
        monkeypatch, [(-25212, None), (0, object())], [_app()] * 4)
    monkeypatch.setattr(injector, "_enable_accessibility",
                        lambda owner: enabled.append(1) or False)
    diagnostics = {}
    assert query_focused_target(diagnostics, retry_attempts=0) is None
    assert len(queries) == 1
    assert sleeps == []
    assert enabled == [1]
    assert diagnostics["target_retry_count"] == 0


def test_delivery_time_lookups_keep_default_retries_and_no_deadline(monkeypatch):
    calls = []
    target = object()
    monkeypatch.setattr(injector, "focused_target",
                        lambda diagnostics=None, **kwargs: calls.append((diagnostics, kwargs)) or target)
    # insert_text
    pb = Pasteboard({"text": "before"})
    monkeypatch.setattr(injector, "_pasteboard", lambda: pb)
    monkeypatch.setattr(injector, "_make_item", lambda values: Item(dict(values)))
    monkeypatch.setattr(injector.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(injector, "_post_cmd_v", lambda: None)
    injector.insert_text("final", target=target)
    injector.restore_clipboard(None)
    assert calls == [(None, {})]
    # deliver_final
    calls.clear()
    monkeypatch.setattr(injector, "insert_text", lambda *a, **k: None)
    monkeypatch.setattr(injector, "_attribute", lambda e, n: (0, "AXGroup"))
    injector.deliver_final("final", target)
    assert calls == [(None, {})]


def test_deadline_boundary_equal_still_retries(monkeypatch):
    # clock + interval == deadline is not past it: strict comparison.
    target = object()
    sleeps, queries = _retry_setup(
        monkeypatch, [(-25212, None), (0, target)], [_app()] * 4)
    diagnostics = {}
    result = query_focused_target(
        diagnostics, deadline_ns=1_040_000_000, clock_ns=lambda: 1_000_000_000)
    assert result is target
    assert diagnostics["target_deadline_stop"] is False
    assert sleeps == [.04]


def test_deadline_stop_works_without_diagnostics(monkeypatch):
    sleeps, queries = _retry_setup(
        monkeypatch, [(-25212, None), (0, object())], [_app()] * 4)
    assert query_focused_target(deadline_ns=100, clock_ns=lambda: 1_000) is None
    assert sleeps == []
    assert len(queries) == 1


def _settable_setup(monkeypatch, replies, settable):
    """Fake AX with an `owner` sentinel; records settable checks and writes."""
    import ApplicationServices as ax
    owner = object()
    _fake_frontmost(monkeypatch, [_app()] * 6)
    monkeypatch.setattr(ax, "AXUIElementCreateApplication", lambda pid: owner)
    checks, writes = [], []

    def is_settable(element, name, _):
        checks.append((element, name))
        return settable[name]
    monkeypatch.setattr(ax, "AXUIElementIsAttributeSettable", is_settable)
    monkeypatch.setattr(ax, "AXUIElementSetAttributeValue",
                        lambda element, name, value: writes.append((element, name, value)) or 0)
    monkeypatch.setattr(injector.time, "sleep", lambda seconds: None)
    iterator = iter(replies)
    monkeypatch.setattr(ax, "AXUIElementCopyAttributeValue", lambda *a: next(iterator))
    return owner, checks, writes


def test_failed_lookup_enables_accessibility_on_owner_app_element(monkeypatch):
    target = object()
    both = {"AXManualAccessibility": (0, True), "AXEnhancedUserInterface": (0, True)}
    owner, _, writes = _settable_setup(
        monkeypatch, [(-25212, None), (0, target)], both)
    assert query_focused_target() is target
    assert [(w[0] is owner, w[1], w[2]) for w in writes] == [
        (True, "AXManualAccessibility", True), (True, "AXEnhancedUserInterface", True)]
    assert len(writes) == 2

    unsettable = {"AXManualAccessibility": (-25205, False),
                  "AXEnhancedUserInterface": (0, False)}
    _, checks, writes = _settable_setup(
        monkeypatch, [(-25212, None), (0, target)], unsettable)
    assert query_focused_target() is target
    assert len(checks) == 2
    assert writes == []

    _, checks, writes = _settable_setup(monkeypatch, [(0, target)], both)
    assert query_focused_target() is target
    assert checks == [] and writes == []


def test_callable_deadline_none_means_keep_retrying(monkeypatch):
    target = object()
    sleeps, queries = _retry_setup(
        monkeypatch, [(-25212, None), (-25212, None), (0, target)], [_app()] * 6)
    diagnostics = {}
    result = query_focused_target(
        diagnostics, deadline_ns=lambda: None, clock_ns=lambda: 10**15)
    assert result is target
    assert diagnostics["target_deadline_stop"] is False
    assert sleeps == [.04, .04]


def test_callable_deadline_none_still_bounded_by_attempt_cap(monkeypatch):
    sleeps, queries = _retry_setup(
        monkeypatch, [(-25212, None)] * 5, [_app()] * 8)
    assert query_focused_target(deadline_ns=lambda: None) is None
    assert sleeps == [.04] * 4


def test_callable_deadline_int_uses_strict_rule_and_is_reevaluated(monkeypatch):
    target = object()
    values = iter([None, 1_040_000_000, 1_039_999_999])
    sleeps, queries = _retry_setup(
        monkeypatch, [(-25212, None), (-25212, None), (-25212, None), (0, target)],
        [_app()] * 8)
    diagnostics = {}
    result = query_focused_target(
        diagnostics, deadline_ns=lambda: next(values),
        clock_ns=lambda: 1_000_000_000)
    assert result is None
    assert diagnostics["target_deadline_stop"] is True
    assert diagnostics["target_retry_count"] == 2
    assert sleeps == [.04, .04]
