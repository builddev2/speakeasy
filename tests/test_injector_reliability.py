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
    monkeypatch.setattr(injector, "focused_target", lambda: target)
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
    monkeypatch.setattr(injector, "focused_target", lambda: None)
    monkeypatch.setattr(injector, "insert_text", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    assert injector.deliver_final("final", None) == "permission_or_focus_unavailable"


def test_focus_change_during_clipboard_settle_never_posts(monkeypatch):
    pb = Pasteboard({"text": "previous"})
    target = object()
    focus = [target]
    posts = []
    monkeypatch.setattr(injector, "_pasteboard", lambda: pb)
    monkeypatch.setattr(injector, "_make_item", lambda values: Item(dict(values)))
    monkeypatch.setattr(injector, "focused_target", lambda: focus[0])
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
        foreground = iter([app(123), after])
        workspace = SimpleNamespace(frontmostApplication=lambda: next(foreground))
        monkeypatch.setattr(injector, "NSWorkspace", SimpleNamespace(sharedWorkspace=lambda: workspace))
        monkeypatch.setattr(ax, "AXUIElementCopyAttributeValue", lambda *args: (error, element))
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


def test_lazy_accessibility_retry_is_bounded_and_checks_foreground(monkeypatch):
    import ApplicationServices as ax
    from types import SimpleNamespace
    owner, target = object(), object()
    app = SimpleNamespace(processIdentifier=lambda: 123)
    other = SimpleNamespace(processIdentifier=lambda: 456)
    monkeypatch.setattr(ax, "AXUIElementCreateApplication", lambda pid: owner)
    monkeypatch.setattr(ax, "AXUIElementSetMessagingTimeout", lambda *args: 0)
    monkeypatch.setattr(ax, "AXUIElementIsAttributeSettable", lambda e, name, _: (0, name == "AXEnhancedUserInterface"))
    for retry_result, final_app, expected in [((0, target), app, target),
                                            ((-25212, None), app, None),
                                            ((0, target), other, None)]:
        foreground = iter([app, app, final_app])
        workspace = SimpleNamespace(frontmostApplication=lambda: next(foreground))
        monkeypatch.setattr(injector, "NSWorkspace", SimpleNamespace(sharedWorkspace=lambda: workspace))
        replies = iter([(-25212, None), retry_result])
        queries, writes, sleeps = [], [], []
        def copy(element, name, _):
            queries.append(name)
            return next(replies)
        monkeypatch.setattr(ax, "AXUIElementCopyAttributeValue", copy)
        monkeypatch.setattr(ax, "AXUIElementSetAttributeValue", lambda *args: writes.append(args) or 0)
        monkeypatch.setattr(injector.time, "sleep", sleeps.append)
        assert query_focused_target() is expected
        assert queries == ["AXFocusedUIElement", "AXFocusedUIElement"]
        assert writes == [(owner, "AXEnhancedUserInterface", True)]
        assert sleeps == [.05]


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
    for error in (-25204, -25211, -25202, -25212):
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
        for subrole in ((-25205, None), (0, "AXStandardWindow"), (0, "")):
            monkeypatch.setattr(ax, "AXUIElementCopyAttributeValue", lambda e, n, out: (0, "AXTextArea") if n == "AXRole" else subrole)
            assert injector.deliver_final("final", target) == ("dispatched_unconfirmed" if web else "ax_acknowledged")
    assert len(writes) == len(pastes) == 3
