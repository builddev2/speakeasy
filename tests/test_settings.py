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


def test_meeting_settings_defaults_and_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    assert settings.get_meeting_settings() == {
        "offer_to_record": True, "detect_calls": True, "calendar_choices": {}}
    settings.set_meeting_settings(offer_to_record=False, calendar_choices={"cal-1": False})
    settings.set_meeting_settings(calendar_choices={"cal-2": True})
    assert settings.get_meeting_settings() == {
        "offer_to_record": False, "detect_calls": True,
        "calendar_choices": {"cal-1": False, "cal-2": True}}


def test_meeting_settings_reject_bad_values(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    import pytest
    with pytest.raises(ValueError):
        settings.set_meeting_settings(offer_to_record="yes")
    with pytest.raises(ValueError):
        settings.set_meeting_settings(detect_calls="no")
    with pytest.raises(ValueError):
        settings.set_meeting_settings(calendar_choices={"cal": "on"})


def test_meeting_settings_ignore_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text('{"meetings": {"offer_to_record": 1,'
                                            ' "calendar_choices": {"a": 1, "b": false}}}')
    assert settings.get_meeting_settings() == {"offer_to_record": True, "detect_calls": True,
                                               "calendar_choices": {"b": False}}


def test_detect_calls_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    assert settings.get_meeting_settings()["detect_calls"] is True
    settings.set_meeting_settings(detect_calls=False)
    assert settings.get_meeting_settings()["detect_calls"] is False
    assert settings.get_meeting_settings()["offer_to_record"] is True


def test_prompted_events_are_kept_for_the_day_only(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    assert settings.get_prompted_events("2026-10-02") == set()
    settings.set_prompted_events("2026-10-02", {"a@2026-10-02T15:00:00Z"})
    assert settings.get_prompted_events("2026-10-02") == {"a@2026-10-02T15:00:00Z"}
    assert settings.get_prompted_events("2026-10-03") == set()
    assert settings.get_meeting_settings()["offer_to_record"] is True


def test_set_prompted_events_keeps_other_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    settings.set_meeting_settings(offer_to_record=False)
    settings.set_prompted_events("2026-10-02", {"a"})
    assert settings.get_meeting_settings()["offer_to_record"] is False


def test_prompted_events_keep_the_latest_500(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    keys = {f"e{i}@2026-10-02T{i // 60:02d}:{i % 60:02d}:00Z" for i in range(600)}
    settings.set_prompted_events("2026-10-02", keys)
    kept = settings.get_prompted_events("2026-10-02")
    assert len(kept) == 500
    assert "e599@2026-10-02T09:59:00Z" in kept and "e0@2026-10-02T00:00:00Z" not in kept


def test_prompted_events_ignore_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text(
        '{"record_prompted": {"day": "2026-10-02", "keys": ["a", 3, null]}}')
    assert settings.get_prompted_events("2026-10-02") == {"a"}


import math


def test_identify_voices_defaults_off_and_round_trips():
    assert settings.get_identify_voices() is False
    assert settings.set_identify_voices(True) is True
    assert settings.get_identify_voices() is True


def test_identify_voices_rejects_non_bool():
    with pytest.raises(ValueError, match="Identify voices must be on or off."):
        settings.set_identify_voices("yes")


def test_pill_origin_round_trips_and_rejects_bad_values():
    assert settings.get_pill_origin() is None
    settings.set_pill_origin(120.5, 800.0)
    assert settings.get_pill_origin() == (120.5, 800.0)
    for bad in ((math.nan, 1.0), (1.0, math.inf), ("1", 2.0), (True, 2.0)):
        with pytest.raises(ValueError, match="Pill position must be two numbers."):
            settings.set_pill_origin(*bad)


def test_corrupt_pill_origin_reads_as_none():
    data = settings._read()
    data["pill_origin"] = ["x", 3]
    settings._write(data)
    assert settings.get_pill_origin() is None


def test_identity_round_trip_and_validation():
    from speakeasy import settings
    assert settings.get_identity() == {"name": "", "aliases": []}
    assert settings.set_identity(name=" Jason ", aliases=["JC", "jc"]) == {"name": "Jason", "aliases": ["JC"]}
    assert settings.set_identity(aliases=["Jas"]) == {"name": "Jason", "aliases": ["Jas"]}
    assert settings.get_identity() == {"name": "Jason", "aliases": ["Jas"]}
    import pytest
    with pytest.raises(ValueError):
        settings.set_identity(name="x" * 81)


def test_identity_ignores_corrupt_stored_value():
    from speakeasy import settings
    settings._write({"identity": {"name": 5, "aliases": "nope"}})
    assert settings.get_identity() == {"name": "", "aliases": []}
    settings._write({"identity": {"name": "x" * 81, "aliases": []}})
    assert settings.get_identity() == {"name": "", "aliases": []}
