"""Diagnostic privacy and file lifetime without a microphone or model."""

import json
import stat
import time

import numpy as np
import pytest

from speakeasy import config, dictation_diagnostic as diagnostic


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic.settings, "app_support_dir", lambda: tmp_path)
    return tmp_path


@pytest.mark.parametrize("fail", [False, True])
def test_exact_wav_is_private_and_deleted_on_success_or_error(root, fail):
    audio = np.array([-0.75, 0, 0.123456789, 1], dtype=np.float32)
    try:
        with diagnostic.temporary_wav(audio) as path:
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
            assert path.read_bytes()[:4] == b"RIFF"
            assert np.array_equal(np.frombuffer(path.read_bytes(), dtype="<f4", offset=44), audio)
            if fail:
                raise RuntimeError("comparison failed")
    except RuntimeError:
        pass
    assert not path.exists()


def test_expiry_removes_wav_even_while_comparison_is_running(root, monkeypatch):
    monkeypatch.setattr(diagnostic, "TEMP_TTL_SECONDS", 0.01)
    with diagnostic.temporary_wav(np.ones(10, dtype=np.float32)) as path:
        deadline = time.monotonic() + 1
        while path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not path.exists()


def test_crash_cleanup_only_removes_owned_temporary_wavs(root):
    folder = root / "dictation-diagnostic-temp"
    folder.mkdir()
    (folder / "take-orphan.wav").write_bytes(b"private")
    (root / "report.json").write_text("private report")
    diagnostic.sweep_temporary_audio()
    assert not (folder / "take-orphan.wav").exists()
    assert (root / "report.json").exists()


def test_no_consent_never_creates_report_or_loads_model(root, monkeypatch):
    output = root / "report.json"
    monkeypatch.setattr("sys.argv", ["diagnostic", "--output", str(output)])
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    diagnostic.main()
    assert not output.exists()


def test_production_streaming_is_disabled_until_real_device_gate():
    assert config.DICTATION_STREAMING_ENABLED is False


def test_gate_uses_word_weighting_and_never_substitutes_small_corpus():
    def row(reference, text, wer):
        mode = {"text": text, "wer": wer}
        return {"reference": reference, "batch": mode, "stream_depth_1": mode,
                "stream_depth_2": mode, "duration_group": "short",
                "live": {"integrity_matches": True, "delivery_complete": True,
                         "stream_dropped_frames": 0}}
    summary = diagnostic.summarize([row("one", "wrong", 1),
                                    row("two three four", "two three four", 0)])
    assert summary["weighted_wer"]["batch"] == .25
    assert summary["numerical_gate_pass"] is False
    assert summary["catastrophic_candidates"] == 1
    assert summary["gate"].startswith("unmet")
