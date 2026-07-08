"""Shared fixtures. Tests touch only a temp profiles dir — no mic, no model."""

import pytest

from speakeasy import config, profiles


@pytest.fixture
def profiles_dir(tmp_path, monkeypatch):
    """Point PROFILES_DIR at a throwaway directory for the duration of a test."""
    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def make_profile(profiles_dir):
    """Factory for a saved Profile backed by the temp profiles dir."""

    def _make(name="tester", **kwargs):
        p = profiles.Profile(name, **kwargs)
        p.save()
        return p

    return _make
