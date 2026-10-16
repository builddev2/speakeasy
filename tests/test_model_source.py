import json
import sys

import pytest

from speakeasy import config, model_source, model_store

REV = "a" * 40
CFG = "b" * 40
ST = "c" * 64


def _fake_cache(tmp_path, monkeypatch, revs=(REV,), symlinks=True):
    hub = tmp_path / "hub"
    base = hub / ("models--" + config.MODEL_ID.replace("/", "--"))
    blobs = base / "blobs"
    blobs.mkdir(parents=True)
    (blobs / CFG).write_text("{}")
    (blobs / ST).write_text("x")
    for rev in revs:
        snap = base / "snapshots" / rev
        snap.mkdir(parents=True)
        for name, blob in (("config.json", CFG), ("model.safetensors", ST)):
            if symlinks:
                (snap / name).symlink_to(f"../../blobs/{blob}")
            else:
                (snap / name).write_text("plain")
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    monkeypatch.delenv("HF_HOME", raising=False)


def _frozen(tmp_path, monkeypatch, source=None):
    exe = tmp_path / "App.app" / "Contents" / "MacOS" / "Speakeasy"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    model = tmp_path / "App.app" / "Contents" / "Resources" / "model"
    model.mkdir(parents=True)
    if source is not None:
        (model / ".source").write_text(source)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))


def _identity(rev=REV):
    return {"model_id": config.MODEL_ID, "revision": rev,
            "files": {"config.json": CFG, "model.safetensors": ST}}


def test_dev_identity_from_cache(tmp_path, monkeypatch):
    _fake_cache(tmp_path, monkeypatch)
    assert model_source.bundled_identity() == _identity()


def test_dev_picks_first_sorted_snapshot(tmp_path, monkeypatch):
    _fake_cache(tmp_path, monkeypatch, revs=("f" * 40, "1" * 40))
    assert model_source.bundled_identity()["revision"] == "1" * 40


def test_dev_missing_cache_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "nope"))
    assert model_source.bundled_identity() is None


def test_dev_non_symlink_is_none(tmp_path, monkeypatch):
    _fake_cache(tmp_path, monkeypatch, symlinks=False)
    assert model_source.bundled_identity() is None


def test_frozen_reads_source_file(tmp_path, monkeypatch):
    _frozen(tmp_path, monkeypatch, json.dumps(_identity()))
    assert model_source.bundled_identity() == _identity()


@pytest.mark.parametrize("text", [None, "not json", "[1]"])
def test_frozen_missing_or_bad_source_is_none(tmp_path, monkeypatch, text):
    _frozen(tmp_path, monkeypatch, text)
    assert model_source.bundled_identity() is None


def test_active_is_bundled_without_update(tmp_path, monkeypatch):
    _fake_cache(tmp_path, monkeypatch)
    assert model_source.active_identity() == {**_identity(), "origin": "bundled"}


def test_active_none_when_nothing_known(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "nope"))
    assert model_source.active_identity() is None


def test_active_uses_update_source(tmp_path, monkeypatch):
    _fake_cache(tmp_path, monkeypatch)
    upd = tmp_path / "rev-upd"
    upd.mkdir()
    new = _identity("d" * 40)
    (upd / "source.json").write_text(json.dumps(new))
    monkeypatch.setattr(model_store, "update_dir_if_valid", lambda: upd)
    assert model_source.active_identity() == {**new, "origin": "update"}


def test_active_falls_back_if_update_source_unreadable(tmp_path, monkeypatch):
    _fake_cache(tmp_path, monkeypatch)
    upd = tmp_path / "rev-upd"
    upd.mkdir()
    (upd / "source.json").write_text("garbage")
    monkeypatch.setattr(model_store, "update_dir_if_valid", lambda: upd)
    assert model_source.active_identity() == {**_identity(), "origin": "bundled"}
