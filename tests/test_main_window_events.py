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
