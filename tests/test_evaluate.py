import pytest

from speakeasy.evaluate import diarization_error_rate, word_error_rate
from speakeasy.meetings import DiarizationTurn


def test_word_error_rate():
    assert word_error_rate("hello world", "hello there") == pytest.approx(0.5)


def test_diarization_error_ignores_anonymous_label_permutation():
    reference = [DiarizationTurn(0, 1, 0), DiarizationTurn(1, 2, 1)]
    hypothesis = [DiarizationTurn(0, 1, 9), DiarizationTurn(1, 2, 4)]
    assert diarization_error_rate(reference, hypothesis, 2) == 0


def test_diarization_error_penalizes_extra_hypothesis_speaker():
    reference = [DiarizationTurn(0, 1, 0)]
    hypothesis = [DiarizationTurn(0, 1, 9), DiarizationTurn(0, 1, 4)]
    assert diarization_error_rate(reference, hypothesis, 1) == pytest.approx(1.0)
