import json

import mlx.core as mx
import pytest

from speakeasy import transcriber


class FakeModel:
    def __init__(self, dtype):
        self.dtype = dtype
        self.loaded = []
        self.updates = []

    def load_weights(self, path):
        self.loaded.append(path)

    def parameters(self):
        return {"w": mx.zeros((2,), dtype=self.dtype)}

    def update(self, tree):
        self.updates.append(tree)


def _bundle(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"target": "x"}))
    (tmp_path / "model.safetensors").write_bytes(b"")
    return tmp_path


def _no_hub(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("hub lookup")

    monkeypatch.setattr("speakeasy.transcriber.from_pretrained", boom)


def test_load_model_directory_skips_hub(monkeypatch, tmp_path):
    _no_hub(monkeypatch)
    model = FakeModel(mx.float32)
    configs = []
    monkeypatch.setattr(
        "speakeasy.transcriber.from_config", lambda c: configs.append(c) or model
    )
    out = transcriber._load_model(str(_bundle(tmp_path)))
    assert out is model
    assert configs == [{"target": "x"}]
    assert model.loaded == [str(tmp_path / "model.safetensors")]


def test_load_model_bf16_directory_is_not_recast(monkeypatch, tmp_path):
    _no_hub(monkeypatch)
    model = FakeModel(mx.bfloat16)
    monkeypatch.setattr("speakeasy.transcriber.from_config", lambda c: model)
    transcriber._load_model(str(_bundle(tmp_path)))
    assert model.updates == []


def test_load_model_casts_non_bf16_directory(monkeypatch, tmp_path):
    _no_hub(monkeypatch)
    model = FakeModel(mx.float32)
    monkeypatch.setattr("speakeasy.transcriber.from_config", lambda c: model)
    transcriber._load_model(str(_bundle(tmp_path)))
    assert len(model.updates) == 1
    assert model.updates[0]["w"].dtype == mx.bfloat16


def test_load_model_hf_id_uses_from_pretrained(monkeypatch):
    sentinel = object()
    seen = []
    monkeypatch.setattr(
        "speakeasy.transcriber.from_pretrained",
        lambda s: seen.append(s) or sentinel,
    )
    assert transcriber._load_model("org/name") is sentinel
    assert seen == ["org/name"]
