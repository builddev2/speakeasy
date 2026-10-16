import json
import os

import pytest

from speakeasy import model_store, settings, transcriber


@pytest.fixture(autouse=True)
def support(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_support_dir", lambda: tmp_path)
    return tmp_path


def _rev(name="rev-aaa", size=5, sizes_size=None, config='{"a": 1}'):
    d = model_store.speech_models_dir() / name
    d.mkdir(parents=True)
    if config is not None:
        (d / "config.json").write_text(config)
    (d / "model.safetensors").write_bytes(b"x" * size)
    sizes = {"config.json": 1, "model.safetensors": size if sizes_size is None else sizes_size}
    (d / "source.json").write_text(json.dumps({"model_id": "m", "revision": "aaa", "files": {}, "sizes": sizes}))
    return d


def _activate(name="rev-aaa", status="pending", previous=None):
    model_store.write_active({"revision": name[4:], "dir": name, "status": status, "previous": previous})


def test_no_update_uses_bundled():
    assert model_store.update_dir_if_valid() is None
    assert settings.model_path() == settings.bundled_model_path()


def test_valid_pending_and_confirmed_update_preferred():
    d = _rev()
    _activate(status="pending")
    assert model_store.update_dir_if_valid() == d
    assert settings.model_path() == str(d)
    _activate(status="confirmed")
    assert settings.model_path() == str(d)


def test_float_size_from_json_compares_numerically():
    d = _rev(size=5)
    (d / "source.json").write_text('{"sizes": {"model.safetensors": 5.0}}')
    _activate()
    assert model_store.update_dir_if_valid() == d


@pytest.mark.parametrize("breakage", ["size", "no_config", "bad_config", "no_source", "no_sizes", "bad_status", "no_weights"])
def test_invalid_update_falls_back_to_bundled(breakage):
    d = _rev(sizes_size=9 if breakage == "size" else None,
             config=None if breakage == "no_config" else "{nope" if breakage == "bad_config" else '{}')
    if breakage == "no_source":
        (d / "source.json").unlink()
    if breakage == "no_sizes":
        (d / "source.json").write_text('{"files": {}}')
    if breakage == "no_weights":
        (d / "model.safetensors").unlink()
    _activate(status="bogus" if breakage == "bad_status" else "pending")
    assert model_store.update_dir_if_valid() is None
    assert settings.model_path() == settings.bundled_model_path()


def test_bad_active_json_is_none():
    model_store.speech_models_dir().mkdir(parents=True)
    (model_store.speech_models_dir() / "active.json").write_text("{trunc")
    assert model_store.read_active() is None


def test_dir_traversal_in_pointer_rejected(support):
    _rev()
    model_store.write_active({"dir": "../speech/rev-aaa", "status": "pending", "previous": None})
    assert model_store.update_dir_if_valid() is None


def test_confirm_deletes_previous():
    _rev("rev-old")
    _rev("rev-new")
    _activate("rev-new", previous="rev-old")
    model_store.confirm_loaded()
    active = model_store.read_active()
    assert active["status"] == "confirmed" and active["previous"] is None
    assert not (model_store.speech_models_dir() / "rev-old").exists()
    assert (model_store.speech_models_dir() / "rev-new").exists()


def test_write_active_atomic_crash_keeps_old(monkeypatch):
    _activate("rev-old", status="confirmed")
    def boom(*a):
        raise OSError("crash")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        model_store.write_active({"dir": "rev-new", "status": "pending"})
    assert model_store.read_active()["dir"] == "rev-old"


def test_mark_failed_and_cleanup():
    root = model_store.speech_models_dir()
    _rev("rev-aaa")
    _activate("rev-aaa")
    model_store.mark_failed()
    assert model_store.read_active() is None
    assert (root / "failed-aaa").is_dir() and not (root / "rev-aaa").exists()
    (root / ".staging-zzz").mkdir()
    _rev("rev-keep"); _rev("rev-prev"); _rev("rev-stray")
    _activate("rev-keep", previous="rev-prev")
    model_store.cleanup_leftovers()
    assert sorted(p.name for p in root.iterdir()) == ["active.json", "rev-keep", "rev-prev"]


def test_use_bundled_removes_everything():
    _rev("rev-a"); _rev("rev-b")
    _activate("rev-a")
    model_store.use_bundled()
    root = model_store.speech_models_dir()
    assert model_store.read_active() is None
    assert not list(root.glob("rev-*"))


def _stub_transcriber(monkeypatch, loads, fail_on=None):
    def fake_load(source):
        loads.append(source)
        if source == fail_on:
            raise RuntimeError("corrupt")
        return object()
    monkeypatch.setattr(transcriber, "_load_model", fake_load)
    monkeypatch.setattr(transcriber.Transcriber, "transcribe", lambda self, a: None)
    monkeypatch.setattr(transcriber.Transcriber, "transcribe_stream",
                        lambda self, s: type("R", (), {"status": transcriber.StreamStatus.COMPLETE})())


def test_transcriber_corrupt_update_falls_back_and_marks_failed(monkeypatch):
    d = _rev()
    _activate()
    loads = []
    _stub_transcriber(monkeypatch, loads, fail_on=str(d))
    transcriber.Transcriber()
    assert loads == [str(d), settings.bundled_model_path()]
    assert model_store.read_active() is None
    assert (model_store.speech_models_dir() / "failed-aaa").is_dir()


def test_transcriber_good_update_confirms(monkeypatch):
    d = _rev()
    _activate()
    loads = []
    _stub_transcriber(monkeypatch, loads)
    transcriber.Transcriber()
    assert loads == [str(d)]
    assert model_store.read_active()["status"] == "confirmed"


def test_transcriber_bundled_does_not_touch_store(monkeypatch):
    loads = []
    _stub_transcriber(monkeypatch, loads)
    transcriber.Transcriber()
    assert loads == [settings.bundled_model_path()]


def test_transcriber_survives_confirm_failure(monkeypatch):
    d = _rev()
    _activate()
    loads = []
    _stub_transcriber(monkeypatch, loads)
    def boom(*a):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    transcriber.Transcriber()
    assert loads == [str(d)]


def test_cleanup_skips_staging_in_use():
    root = model_store.speech_models_dir()
    busy, idle = root / ".staging-busy", root / ".staging-idle"
    busy.mkdir(parents=True)
    idle.mkdir()
    model_store.mark_staging_in_use(busy)
    try:
        model_store.cleanup_leftovers()
        assert busy.is_dir() and not idle.exists()
    finally:
        model_store.release_staging(busy)
    model_store.cleanup_leftovers()
    assert not busy.exists()


def test_mark_failed_ignores_hostile_revision(tmp_path):
    d = _rev("rev-aaa")
    model_store.write_active({"revision": "../../evil", "dir": "rev-aaa",
                              "status": "pending", "previous": None})
    model_store.mark_failed()
    root = model_store.speech_models_dir()
    assert not d.exists()
    assert [p.name for p in root.iterdir() if p.name.startswith("failed-")] == ["failed-rev-aaa"]
    assert not (tmp_path / "evil").exists() and not (tmp_path / "models" / "evil").exists()


def test_loading_pointer_is_not_valid_for_use():
    _rev()
    _activate(status="loading")
    assert model_store.update_dir_if_valid() is None
    assert settings.model_path() == settings.bundled_model_path()


def test_loading_pointer_at_launch_uses_bundled_and_marks_failed(monkeypatch):
    # The previous launch died while loading this update.
    _rev()
    _activate(status="loading")
    loads = []
    _stub_transcriber(monkeypatch, loads)
    transcriber.Transcriber()
    assert loads == [settings.bundled_model_path()]
    assert model_store.read_active() is None
    assert (model_store.speech_models_dir() / "failed-aaa").is_dir()


def test_successful_load_goes_pending_loading_confirmed(monkeypatch):
    d = _rev()
    _activate(status="pending")
    seen = []

    def fake_load(source):
        seen.append(model_store.read_active()["status"])
        return object()

    _stub_transcriber(monkeypatch, [])
    monkeypatch.setattr(transcriber, "_load_model", fake_load)
    transcriber.Transcriber()
    assert seen == ["loading"]
    assert model_store.read_active()["status"] == "confirmed"
    assert model_store.update_dir_if_valid() == d


def test_confirmed_update_is_not_marked_loading(monkeypatch):
    _rev()
    _activate(status="confirmed")
    seen = []

    def fake_load(source):
        seen.append(model_store.read_active()["status"])
        return object()

    _stub_transcriber(monkeypatch, [])
    monkeypatch.setattr(transcriber, "_load_model", fake_load)
    transcriber.Transcriber()
    assert seen == ["confirmed"]


def test_failure_writing_loading_does_not_block_launch(monkeypatch):
    d = _rev()
    _activate(status="pending")
    loads = []
    _stub_transcriber(monkeypatch, loads)
    real = model_store.write_active

    def write(data):
        if data.get("status") == "loading":
            raise OSError("read-only")
        real(data)

    monkeypatch.setattr(model_store, "write_active", write)
    transcriber.Transcriber()
    assert loads == [str(d)]
    assert model_store.read_active()["status"] == "confirmed"


def test_manage_update_false_does_no_bookkeeping(monkeypatch):
    d = _rev()
    _activate(status="pending")
    loads = []
    _stub_transcriber(monkeypatch, loads)
    calls = []
    for name in ("confirm_loaded", "mark_failed", "recover_interrupted_load", "mark_loading"):
        monkeypatch.setattr(model_store, name, lambda *a, _n=name: calls.append(_n), raising=False)
    transcriber.Transcriber(manage_update=False)
    assert loads == [str(d)]
    assert calls == []
    assert model_store.read_active()["status"] == "pending"


def test_manage_update_false_failure_just_raises(monkeypatch):
    d = _rev()
    _activate(status="pending")
    _stub_transcriber(monkeypatch, [], fail_on=str(d))
    with pytest.raises(RuntimeError):
        transcriber.Transcriber(manage_update=False)
    assert model_store.read_active()["status"] == "pending"
    assert d.is_dir()


def test_diagnostic_call_sites_do_not_manage_the_update():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "speakeasy"
    assert "Transcriber(manage_update=False)" in (root / "evaluate.py").read_text()
    assert "Transcriber(manage_update=False)" in (root / "inference_probe.py").read_text()
