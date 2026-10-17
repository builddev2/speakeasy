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
    # scores exactly 7 of 9: 'the' appears twice in the sentence and is absent here
    st["text"] = "quick brown fox jumps over lazy dog"
    assert model_verify._NEEDED == 7
    assert model_verify.main([str(d)]) == 0


def test_threshold_is_exactly_seven(setup, monkeypatch):
    d, st = setup
    st["text"] = "quick brown fox jumps over lazy dog"
    monkeypatch.setattr(model_verify, "_NEEDED", 8)
    assert model_verify.main([str(d)]) == 1


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


def _write_wav(path, rate=16000, channels=1, samples=(0, 16384, -16384, 32767)):
    import wave
    import struct
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % len(samples), *samples))


class RecordingModel:
    preprocessor_config = object()

    def __init__(self):
        self.args = []

    def generate(self, mel):
        self.args.append(mel)
        return [types.SimpleNamespace(text="hello")]

    def transcribe(self, *a, **k):
        raise AssertionError("must not use the path-based transcribe")


def test_transcribe_passes_mx_array_never_a_path(tmp_path, monkeypatch):
    import shutil
    import mlx.core as mx
    wav = tmp_path / "a.wav"
    _write_wav(wav)
    seen = {}

    def fake_logmel(audio, config):
        seen["audio"], seen["config"] = audio, config
        return audio

    def no_which(*a, **k):
        raise AssertionError("ffmpeg must not be consulted")

    monkeypatch.setattr(shutil, "which", no_which)
    monkeypatch.setattr(model_verify, "get_logmel", fake_logmel)
    model = RecordingModel()
    assert model_verify._transcribe(model, wav) == "hello"
    assert len(model.args) == 1 and isinstance(model.args[0], mx.array)
    assert not isinstance(model.args[0], (str, bytes))
    assert seen["config"] is model.preprocessor_config
    assert seen["audio"].dtype == mx.float32
    assert [round(v, 4) for v in seen["audio"].tolist()] == [0.0, 0.5, -0.5, 1.0]


def test_read_wav_converts_int16_to_float32(tmp_path):
    wav = tmp_path / "a.wav"
    _write_wav(wav)
    audio = model_verify._read_wav(wav)
    assert audio.dtype.name == "float32"
    assert audio.tolist() == pytest.approx([0.0, 0.5, -0.5, 32767 / 32768])


@pytest.mark.parametrize("rate,channels", [(44100, 1), (16000, 2)])
def test_read_wav_rejects_wrong_format(tmp_path, rate, channels):
    wav = tmp_path / "a.wav"
    _write_wav(wav, rate=rate, channels=channels, samples=(0, 0, 0, 0))
    with pytest.raises(ValueError, match="16000"):
        model_verify._read_wav(wav)


def test_imports_without_librosa(tmp_path):
    """The packaged app has no librosa: model_verify must install the shim first."""
    import os
    import subprocess
    import sys

    code = (
        "import sys\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'librosa' or name.startswith('librosa.'):\n"
        "            raise ModuleNotFoundError(\"No module named 'librosa'\")\n"
        "sys.meta_path.insert(0, Block())\n"
        "import speakeasy.model_verify\n"
    )
    env = {**os.environ, "HOME": str(tmp_path), "HF_HUB_OFFLINE": "1"}
    r = subprocess.run([sys.executable, "-B", "-c", code], env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
