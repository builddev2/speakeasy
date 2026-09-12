from speakeasy import injector


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
        monkeypatch.setattr(injector, "_attribute", lambda e, n: role if n == "AXRole" else
                            ("AXSecureTextField" if case == 1 else None))
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
    monkeypatch.setattr(injector, "_attribute", lambda e, n: "AXTextArea" if n == "AXRole" else None)
    monkeypatch.setattr(ax, "AXUIElementIsAttributeSettable", lambda *a: (0, True))
    monkeypatch.setattr(ax, "AXUIElementSetAttributeValue", lambda *a: -25202)
    monkeypatch.setattr(injector, "insert_text", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    assert injector.deliver_final("final", target) == "delivery_unknown"
