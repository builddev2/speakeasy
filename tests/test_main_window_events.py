from speakeasy.ui import services
from speakeasy.ui.main_window import MainWindowController


def _call(monkeypatch, sync):
    monkeypatch.setattr(services, "calendar_sync", sync)
    out = {}
    MainWindowController._meeting_events(
        MainWindowController.alloc(), {}, lambda *a, **k: out.update(args=a, kwargs=k))
    return out


class _Sync:
    def __init__(self, access):
        self._access = access

    def access(self):
        return self._access


def test_meeting_events_errors_without_calendar(monkeypatch):
    out = _call(monkeypatch, None)
    assert out["kwargs"] == {"error": "calendar_unavailable"}


def test_meeting_events_errors_when_not_connected(monkeypatch):
    out = _call(monkeypatch, _Sync("denied"))
    assert out["kwargs"] == {"error": "calendar_unavailable"}


class _Dispatcher:
    def __init__(self):
        self.handlers = {}

    def register(self, name, handler):
        self.handlers[name] = handler


def test_cancel_processing_is_registered_and_cancels_engine(monkeypatch):
    from speakeasy.ui import main_window

    dispatcher = _Dispatcher()
    monkeypatch.setattr(main_window, "BridgeDispatcher", lambda: dispatcher)
    monkeypatch.setattr(main_window, "WebWindow", lambda *a, **k: type(
        "W", (), {"window": type("Win", (), {
            "setDelegate_": lambda self, d: None,
            "setFrameAutosaveName_": lambda self, n: None})()})())

    class _Engine:
        calls = 0

        def cancel_meeting_processing(self):
            self.calls += 1

    engine = _Engine()
    controller = MainWindowController.alloc().initWithEngine_(engine)
    handler = dispatcher.handlers["app.cancelProcessing"]
    out = []
    handler({}, lambda *a, **k: out.append((a, k)))
    assert engine.calls == 1
    assert out == [((True,), {})]
