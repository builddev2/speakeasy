import json
import types

import pytest

from speakeasy import model_verify


class P:
    def __init__(self, shape):
        self.shape = shape


class FakeModel:
    def __init__(self, shapes, text="the quick brown fox jumps over the lazy dog"):
        self._shapes = shapes
        self.text = text

    def parameters(self):
        return {"layer": {k: P(v) for k, v in self._shapes.items()}}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    d = tmp_path / "m"
    d.mkdir()
    (d / "config.json").write_text(json.dumps({"a": 1}))
    state = {"loaded": FakeModel({"w": (2, 3)}), "fresh": FakeModel({"w": (2, 3)}),
             "say": 0, "text": "The quick brown fox jumps over the lazy dog."}
    monkeypatch.setattr(model_verify, "_load_model", lambda p: state["loaded"])
    monkeypatch.setattr(model_verify, "from_config", lambda cfg: state["fresh"])

    def say(path):
        state["say"] += 1

    monkeypatch.setattr(model_verify, "_say", say)
    monkeypatch.setattr(model_verify, "_transcribe", lambda m, p: state["text"])
    return d, state


def test_pass(setup, capsys):
    d, _ = setup
    assert model_verify.main([str(d)]) == 0


def test_seven_of_nine_passes(setup):
    d, st = setup
    st["text"] = "The quick brown fox jumps over a lazy cat"
    assert model_verify.main([str(d)]) == 0


def test_six_of_nine_fails(setup, capsys):
    d, st = setup
    st["text"] = "quick brown fox jumps over some thing else"
    assert model_verify.main([str(d)]) == 1
    assert "transcription" in capsys.readouterr().err


def test_shape_mismatch(setup, capsys):
    d, st = setup
    st["loaded"] = FakeModel({"w": (2, 4)})
    assert model_verify.main([str(d)]) == 1
    cap = capsys.readouterr()
    assert "shape" in cap.err and "shape" in cap.out


def test_missing_parameter(setup):
    d, st = setup
    st["loaded"] = FakeModel({})
    assert model_verify.main([str(d)]) == 1


def test_load_error_is_reported(setup, monkeypatch, capsys):
    d, _ = setup

    def boom(p):
        raise RuntimeError("bad weights")

    monkeypatch.setattr(model_verify, "_load_model", boom)
    assert model_verify.main([str(d)]) == 1
    assert "bad weights" in capsys.readouterr().err


def test_usage(capsys):
    assert model_verify.main([]) == 1


def test_main_dispatches_verify_model(monkeypatch):
    import sys
    from speakeasy import __main__ as entry
    seen = {}
    monkeypatch.setattr(model_verify, "main", lambda argv: seen.update(argv=argv) or 0)
    monkeypatch.setattr(sys, "argv", ["speakeasy", "--verify-model", "/x"])
    with pytest.raises(SystemExit) as e:
        entry.main()
    assert e.value.code == 0 and seen["argv"] == ["/x"]
