"""Shared fixtures. Tests touch only temp dirs — no mic, no model."""

import pytest

from speakeasy import profiles, settings


@pytest.fixture
def profiles_dir(tmp_path, monkeypatch):
    """Point the profiles dir at a throwaway directory for the test."""
    monkeypatch.setattr(settings, "profiles_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def meetings_dir(tmp_path, monkeypatch):
    """Point the meetings dir at a throwaway directory for the test."""
    d = tmp_path / "meetings"
    d.mkdir()
    monkeypatch.setattr(settings, "meetings_dir", lambda: d)
    return d


@pytest.fixture
def spool_dir(tmp_path, monkeypatch):
    """Point the meeting-audio spool dir at a throwaway directory."""
    d = tmp_path / "spool"
    d.mkdir()
    monkeypatch.setattr(settings, "spool_dir", lambda: d)
    return d


@pytest.fixture
def voice_profiles_dir(tmp_path, monkeypatch):
    d = tmp_path / "voice_profiles"
    d.mkdir()
    monkeypatch.setattr(settings, "voice_profiles_dir", lambda: d)
    return d


@pytest.fixture
def make_profile(profiles_dir):
    """Factory for a saved Profile backed by the temp profiles dir."""

    def _make(name="tester", **kwargs):
        p = profiles.Profile(name, **kwargs)
        p.save()
        return p

    return _make


@pytest.fixture(autouse=True)
def insertion_target(monkeypatch):
    """Unit tests use a stable incompatible target; never inspect host focus."""
    import ApplicationServices as ax
    from speakeasy import injector
    monkeypatch.setattr(injector._transaction, "snapshot", None, raising=False)
    target = object()
    monkeypatch.setattr(injector, "focused_target", lambda: target)
    monkeypatch.setattr(injector, "_attribute", lambda element, name: "AXGroup")
    monkeypatch.setattr(ax, "AXUIElementIsAttributeSettable", lambda *args: (0, False))
    monkeypatch.setattr(ax, "AXUIElementCopyAttributeNames", lambda *args: (0, []))


@pytest.fixture(autouse=True)
def microphone_authorization(monkeypatch):
    from speakeasy import recorder
    monkeypatch.setattr(recorder, "_permission_blocked", lambda: False)
