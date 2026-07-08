"""settings.py: paths, legacy-profile migration, settings.json round-trip."""

import pytest

from speakeasy import config, settings


@pytest.fixture
def support_dir(tmp_path, monkeypatch):
    """Point the App Support dir at a throwaway directory."""
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    return tmp_path


def test_migrates_legacy_profiles_nondestructively(support_dir, tmp_path, monkeypatch):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "jason.json").write_text('{"name": "jason"}')
    monkeypatch.setattr(config, "LEGACY_PROFILES_DIR", legacy)

    d = settings.profiles_dir()
    assert (d / "jason.json").is_file()
    assert (legacy / "jason.json").is_file()  # original left in place

    # Migration runs only when the dir is first created — later edits stick.
    (d / "jason.json").write_text('{"name": "jason", "vocabulary": ["edited"]}')
    settings.profiles_dir()
    assert "edited" in (d / "jason.json").read_text()


def test_profiles_dir_without_legacy(support_dir, monkeypatch):
    monkeypatch.setattr(config, "LEGACY_PROFILES_DIR", support_dir / "nonexistent")
    d = settings.profiles_dir()
    assert d.is_dir()
    assert list(d.iterdir()) == []


def test_last_profile_roundtrip(support_dir):
    assert settings.get_last_profile() is None
    settings.set_last_profile("jason")
    assert settings.get_last_profile() == "jason"
    settings.set_last_profile(None)
    assert settings.get_last_profile() is None


def test_corrupt_settings_json_is_ignored(support_dir):
    (support_dir / "settings.json").write_text("{not json")
    assert settings.get_last_profile() is None
    settings.set_last_profile("jason")  # recovers by rewriting
    assert settings.get_last_profile() == "jason"


def test_model_path_in_dev_is_model_id():
    assert settings.model_path() == config.MODEL_ID
