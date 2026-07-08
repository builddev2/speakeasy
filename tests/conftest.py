"""Shared fixtures. Tests touch only a temp profiles dir — no mic, no model."""

import pytest

from speakeasy import profiles, settings


@pytest.fixture
def profiles_dir(tmp_path, monkeypatch):
    """Point the profiles dir at a throwaway directory for the test."""
    monkeypatch.setattr(settings, "profiles_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def make_profile(profiles_dir):
    """Factory for a saved Profile backed by the temp profiles dir."""

    def _make(name="tester", **kwargs):
        p = profiles.Profile(name, **kwargs)
        p.save()
        return p

    return _make
