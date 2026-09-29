"""Shared fixtures. Tests touch only temp dirs — no mic, no model."""

import pytest

from speakeasy import profiles, settings


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    """Safety net: no test may touch the real home or App Support folder.

    A test once built Profile("t") without a temp fixture and add_word() wrote
    profiles/t.json into the real ~/Library/Application Support/Speakeasy.
    HOME is redirected so Path.home() / expanduser("~") resolve to a throwaway
    dir, and settings.app_support_dir is patched too in case HOME was already
    read. Specific fixtures below (profiles_dir, library_path, ...) still
    override individual dirs; repo-relative paths such as models/ are unaffected.
    """
    home = tmp_path_factory.mktemp("home")
    support = home / "Library" / "Application Support" / "Speakeasy"
    support.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(settings, "app_support_dir", lambda: support)
    return home


@pytest.fixture
def profiles_dir(tmp_path, monkeypatch):
    """Point the profiles dir at a throwaway directory for the test."""
    monkeypatch.setattr(settings, "profiles_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def library_path(tmp_path, monkeypatch):
    """Point the SQLite meeting library at a throwaway file."""
    path = tmp_path / "library.sqlite"
    monkeypatch.setattr(settings, "library_path", lambda: path)
    return path


@pytest.fixture
def meetings_dir(tmp_path, monkeypatch, library_path):
    """Point the meetings dir (legacy JSON) at a throwaway directory."""
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
    monkeypatch.setattr(injector, "focused_target", lambda diagnostics=None: target)
    monkeypatch.setattr(injector, "_attribute", lambda element, name: (0, "AXGroup"))
    monkeypatch.setattr(ax, "AXUIElementIsAttributeSettable", lambda *args: (0, False))
    monkeypatch.setattr(ax, "AXUIElementCopyAttributeNames", lambda *args: (0, []))


@pytest.fixture(autouse=True)
def microphone_authorization(monkeypatch):
    from speakeasy import recorder
    monkeypatch.setattr(recorder, "_permission_blocked", lambda: False)
    monkeypatch.setattr(recorder, "_log_recovery", lambda record: None)
