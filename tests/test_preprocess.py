"""Unit tests for silence trimming (preprocess.py) — pure, no hardware."""

import numpy as np

from speakeasy import config
from speakeasy.preprocess import trim_silence

SR = config.SAMPLE_RATE


def _tone(seconds: float, amp: float = 0.5, freq: float = 220.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.float32)


def test_all_silence_returned_unchanged():
    audio = _silence(1.0)
    out = trim_silence(audio, SR)
    assert np.array_equal(out, audio)


def test_leading_and_trailing_silence_removed():
    audio = np.concatenate([_silence(0.5), _tone(0.5), _silence(0.5)])
    out = trim_silence(audio, SR)
    # 0.5 s speech + up to 2 * 80 ms margin, well under the 1.5 s original.
    assert len(out) < len(audio)
    # 0.5 s speech + 80 ms margin each side + one 25 ms analysis frame (the
    # tail margin is measured past the end of the last voiced frame).
    assert len(out) <= int((0.5 + 2 * config.TRIM_MARGIN_SECONDS + 0.025) * SR) + SR // 100


def test_margin_preserves_onset():
    # Speech starts at 0.5 s; trimmed clip must retain samples before the onset.
    audio = np.concatenate([_silence(0.5), _tone(0.3)])
    out = trim_silence(audio, SR)
    # Some leading samples survive (the 80 ms margin), so it's not cut flush.
    assert len(out) >= int((0.3 + config.TRIM_MARGIN_SECONDS) * SR) - SR // 100


def test_no_silence_is_near_unchanged():
    audio = _tone(0.6)
    out = trim_silence(audio, SR)
    assert abs(len(out) - len(audio)) <= int(2 * config.TRIM_MARGIN_SECONDS * SR) + SR // 100


def test_interior_silence_between_words_is_kept():
    # word - gap - word: the gap is interior, only outer edges get trimmed.
    audio = np.concatenate([_tone(0.3), _silence(0.4), _tone(0.3)])
    out = trim_silence(audio, SR)
    assert len(out) >= int(1.0 * SR) - int(2 * config.TRIM_MARGIN_SECONDS * SR)


def test_empty_input_returns_empty():
    out = trim_silence(np.empty(0, dtype=np.float32), SR)
    assert len(out) == 0


def test_disabled_returns_input_unchanged(monkeypatch):
    monkeypatch.setattr(config, "DICTATION_TRIM_ENABLED", False)
    audio = np.concatenate([_silence(0.5), _tone(0.5), _silence(0.5)])
    out = trim_silence(audio, SR)
    assert np.array_equal(out, audio)


def test_too_short_to_frame_returned_unchanged():
    audio = _tone(0.01)  # 160 samples < one 400-sample frame
    out = trim_silence(audio, SR)
    assert np.array_equal(out, audio)
