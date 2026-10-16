"""ModelUpdateController + its bridge methods. No network, no real downloads:
check and the downloader are injected fakes; threads are joined."""

import threading

import pytest

from speakeasy import model_source, model_store, settings
from speakeasy.model_update import CheckResult
from speakeasy.ui.meetings_bridge import MeetingsBridge
from speakeasy.ui.model_update_controller import ModelUpdateController
from speakeasy.ui.webbridge import BridgeDispatcher

REV_A = "a" * 40
REV_B = "b" * 40


@pytest.fixture(autouse=True)
def _identity(monkeypatch):
    ident = {"revision": REV_A, "origin": "bundled"}
    monkeypatch.setattr(model_source, "active_identity", lambda: ident)
    monkeypatch.setattr(model_store, "read_active", lambda: None)


def available(total=2_500_000_000.0):
    return CheckResult("available", REV_B, REV_A, total, "A newer speech model is available.")


class FakeDownloader:
    instances = []

    def __init__(self, result, runner=None, on_state=None):
        self.result, self.on_state = result, on_state
        self.started = self.cancelled = False
        FakeDownloader.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def make(check=None, clock=None):
    FakeDownloader.instances = []
    events = []
    c = ModelUpdateController(check=check or (lambda: available()),
                              downloader_factory=FakeDownloader,
                              clock=clock or Clock(),
                              emitter=lambda p: events.append(p))
    return c, events


def test_idle_status_shape():
    c, _ = make()
    s = c.status()
    assert s == {"current": {"revision": REV_A[:7], "origin": "bundled", "pending": False},
                 "autoCheck": False, "state": "idle", "message": "", "remote": None,
                 "progress": None, "progressText": None, "previousRevertible": False,
                 "updateSize": None}


def test_check_returns_at_once_and_result_arrives_as_event():
    gate = threading.Event()

    def slow_check():
        gate.wait(5)
        return available()

    c, events = make(check=slow_check)
    s = c.check()
    assert s["state"] == "checking"
    assert events[-1]["state"] == "checking"
    gate.set()
    c.join()
    assert events[-1]["state"] == "available"
    assert events[-1]["remote"] == {"revision": REV_B[:7], "bytes": 2_500_000_000.0,
                                    "sizeText": "2.5 GB"}
    assert c.status()["state"] == "available"


def test_check_while_busy_is_refused():
    gate = threading.Event()
    c, _ = make(check=lambda: (gate.wait(5), available())[1])
    c.check()
    with pytest.raises(ValueError, match="already"):
        c.check()
    gate.set()
    c.join()


def test_accept_check_result_from_menubar_shows_prompt():
    c, events = make()
    c.accept_check_result(available())
    assert c.status()["state"] == "available"
    assert events[-1]["state"] == "available"


def test_download_without_available_result_errors():
    c, _ = make()
    with pytest.raises(ValueError, match="No update is available"):
        c.download()
    c.accept_check_result(CheckResult("up_to_date", REV_A, REV_A, None, "Up to date."))
    with pytest.raises(ValueError, match="No update is available"):
        c.download()


def test_download_progress_text_uses_floats_and_cancel():
    c, events = make()
    c.accept_check_result(available(2_500_000_000.0))
    s = c.download()
    d = FakeDownloader.instances[0]
    assert d.started and s["state"] == "downloading" and s["progress"] == 0.0
    d.on_state("downloading", 0.5, "")
    st = c.status()
    assert st["progress"] == 0.5 and st["progressText"] == "1.2 of 2.5 GB"
    with pytest.raises(ValueError, match="already in progress"):
        c.download()
    c.cancel()
    assert d.cancelled
    d.on_state("cancelled", 0.5, "Download cancelled.")
    assert c.status()["state"] == "cancelled"
    assert events[-1]["state"] == "cancelled"
    # the available result survives a cancel, so the user can retry
    assert c.download()["state"] == "downloading"


def test_verifying_cannot_be_cancelled_and_ready_clears_remote_result():
    c, _ = make()
    c.accept_check_result(available())
    c.download()
    d = FakeDownloader.instances[0]
    d.on_state("verifying", 1.0, "")
    assert c.status()["message"] == "Checking the new model…"
    c.cancel()
    assert not d.cancelled
    d.on_state("ready", 1.0, "Ready. It takes effect the next time Speakeasy starts.")
    assert c.status()["state"] == "ready"
    with pytest.raises(ValueError, match="No update is available"):
        c.download()


def test_progress_events_throttled_to_four_per_second():
    clock = Clock()
    c, events = make(clock=clock)
    c.accept_check_result(available())
    c.download()
    d = FakeDownloader.instances[0]
    events.clear()
    for i in range(40):          # 40 callbacks in 1.0 s of fake time
        clock.t += 0.025
        d.on_state("downloading", i / 40, "")
    prog = [e for e in events if e["state"] == "downloading"]
    assert 3 <= len(prog) <= 4
    d.on_state("verifying", 1.0, "")   # state changes always go out
    assert events[-1]["state"] == "verifying"
    assert c.status()["progress"] is None


def test_failed_and_offline_states_carry_message():
    c, events = make()
    c.accept_check_result(available())
    c.download()
    FakeDownloader.instances[0].on_state("offline", 0.1, "You appear to be offline.")
    assert events[-1]["state"] == "offline" and events[-1]["message"] == "You appear to be offline."


def test_use_bundled_calls_store_and_pending_flag(monkeypatch):
    called = []
    monkeypatch.setattr(model_store, "use_bundled", lambda: called.append(1))
    ident = {"revision": REV_B, "origin": "update", "sizes": {"config.json": 1_000.0, "model.safetensors": 2_469_999_000.0}}
    monkeypatch.setattr(model_source, "active_identity", lambda: ident)
    c, _ = make()
    s = c.status()
    assert s["previousRevertible"] is True and s["updateSize"] == "2.5 GB"
    s = c.use_bundled()
    assert called == [1]
    assert s["current"]["pending"] is True


def test_pending_from_store(monkeypatch):
    monkeypatch.setattr(model_store, "read_active", lambda: {"status": "pending"})
    c, _ = make()
    assert c.status()["current"]["pending"] is True


def test_set_auto_check_accepts_only_real_bool():
    c, _ = make()
    assert c.set_auto_check(True)["autoCheck"] is True
    assert settings.get_model_update_settings()["auto_check"] is True
    for bad in (1.0, 1, 0.0, "true", None):
        with pytest.raises(ValueError, match="Automatic check must be on or off."):
            c.set_auto_check(bad)
    assert settings.get_model_update_settings()["auto_check"] is True


# -- bridge ------------------------------------------------------------------

def _call(d, method, params=None):
    out = []
    d.dispatch({"id": 1.0, "method": method, "params": params or {}}, out.append)
    return out[-1]


def bridge_with(tmp_path, controller):
    from speakeasy.meeting_library import MeetingLibrary
    b = MeetingsBridge(library=MeetingLibrary(tmp_path / "l.sqlite"), model_update=controller)
    d = BridgeDispatcher()
    b.register(d)
    return d


def test_bridge_methods(tmp_path):
    c, events = make()
    d = bridge_with(tmp_path, c)
    assert '"state": "idle"' in _call(d, "modelUpdate.status")
    assert '"state": "checking"' in _call(d, "modelUpdate.check")
    c.join()
    assert events[-1]["state"] == "available"
    assert '"state": "downloading"' in _call(d, "modelUpdate.download")
    assert "_resolve" in _call(d, "modelUpdate.cancel")
    assert "_reject" in _call(d, "modelUpdate.download") and "already in progress" in _call(d, "modelUpdate.download")


def test_bridge_set_auto_check_rejects_float(tmp_path):
    c, _ = make()
    d = bridge_with(tmp_path, c)
    assert "Automatic check must be on or off." in _call(d, "modelUpdate.setAutoCheck", {"value": 1.0})
    assert "_resolve" in _call(d, "modelUpdate.setAutoCheck", {"value": True})
    assert "Automatic check must be on or off." in _call(d, "modelUpdate.setAutoCheck", {})


def test_bridge_download_without_result_is_error(tmp_path):
    c, _ = make()
    d = bridge_with(tmp_path, c)
    out = _call(d, "modelUpdate.download")
    assert "_reject" in out and "No update is available" in out


def test_bridge_use_bundled(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(model_store, "use_bundled", lambda: called.append(1))
    c, _ = make()
    d = bridge_with(tmp_path, c)
    assert "_resolve" in _call(d, "modelUpdate.useBundled")
    assert called == [1]
