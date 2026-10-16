import logging

import pytest

from speakeasy import model_source, model_update, settings
from speakeasy.model_update import CheckResult
from speakeasy.ui.menu_model import UPDATE_ITEM_TITLE, update_item_visible

WEEK = 7 * 86400


@pytest.fixture(autouse=True)
def _identity(monkeypatch):
    ident = {"model_id": "x", "revision": "a" * 40,
             "files": {"config.json": "c" * 40, "model.safetensors": "d" * 64},
             "origin": "bundled"}
    monkeypatch.setattr(model_source, "active_identity", lambda: ident)


class Runner:
    def __init__(self):
        self.calls = 0

    def run(self, argv, *, timeout):
        self.calls += 1
        return 6, b""   # curl "could not resolve host" -> offline

    def start(self, argv):
        raise AssertionError("no downloads")


def test_off_by_default_never_calls_runner():
    assert settings.get_model_update_settings() == {"auto_check": False, "last_check": None}
    r = Runner()
    assert model_update.maybe_auto_check(10 * WEEK, runner=r) is None
    assert r.calls == 0
    assert settings.get_model_update_settings()["last_check"] is None


def test_six_days_does_not_trigger_seven_does():
    settings.set_model_update_settings(auto_check=True, last_check=1000.0)
    r = Runner()
    assert model_update.maybe_auto_check(1000.0 + 6 * 86400, runner=r) is None
    assert r.calls == 0
    result = model_update.maybe_auto_check(1000.0 + WEEK, runner=r)
    assert result is not None and r.calls > 0


def test_none_last_check_is_due_and_offline_is_stored_and_logs_one_line(caplog):
    settings.set_model_update_settings(auto_check=True)
    r = Runner()
    with caplog.at_level(logging.DEBUG):
        result = model_update.maybe_auto_check(5000.0, runner=r)
    assert result.state == "offline"
    assert settings.get_model_update_settings()["last_check"] == 5000.0
    assert len(caplog.records) == 1
    # next launch within a week: no retry
    assert model_update.maybe_auto_check(5000.0 + 86400, runner=r) is None


def test_setter_validation():
    with pytest.raises(ValueError, match="Automatic check must be on or off."):
        settings.set_model_update_settings(auto_check=1.0)
    with pytest.raises(ValueError):
        settings.set_model_update_settings(auto_check=1)
    out = settings.set_model_update_settings(auto_check=True, last_check=12)
    assert out == {"auto_check": True, "last_check": 12.0}
    assert isinstance(out["last_check"], float)
    with pytest.raises(ValueError):
        settings.set_model_update_settings(last_check=True)
    assert settings.set_model_update_settings(last_check=1.5)["last_check"] == 1.5


def test_menu_item_label_and_visibility():
    assert UPDATE_ITEM_TITLE == "Speech model update available…"
    assert update_item_visible(True) is True
    assert update_item_visible(False) is False


def test_available_state_flag():
    from speakeasy.ui.menu_model import update_available_after
    avail = CheckResult("available", "b" * 40, "a" * 40, 1, "m")
    off = CheckResult("offline", None, None, None, "m")
    assert update_available_after(avail) is True
    assert update_available_after(off) is False
    assert update_available_after(None) is False
