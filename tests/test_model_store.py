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
