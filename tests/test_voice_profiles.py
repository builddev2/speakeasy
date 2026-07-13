import json

import numpy as np
import pytest

from speakeasy import config
from speakeasy.voice_profiles import VoiceProfileStore
from speakeasy.meetings import DiarizationTurn


class Stream:
    def accept_waveform(self, sample_rate, samples):
        assert sample_rate == config.SAMPLE_RATE
        self.samples = samples

    def input_finished(self):
        pass


class Extractor:
    dim = 2

    def create_stream(self):
        return Stream()

    def is_ready(self, stream):
        return len(stream.samples) >= 4

    def compute(self, stream):
        return [float(stream.samples.mean()), 1.0]


def test_enroll_persists_embedding_not_audio(voice_profiles_dir):
    store = VoiceProfileStore(Extractor())
    store.enroll("Alice", np.ones(8, dtype=np.float32))
    data = json.loads((voice_profiles_dir / "Alice.json").read_text())
    assert data["embedding"] == [1.0, 1.0]
    assert not list(voice_profiles_dir.glob("*.wav"))
    assert store.names() == ["Alice"]
    store.delete("Alice")
    assert store.names() == []


def test_short_enrollment_is_rejected(voice_profiles_dir):
    store = VoiceProfileStore(Extractor())
    with pytest.raises(ValueError, match="too short"):
        store.enroll("Alice", np.ones(2, dtype=np.float32))


def test_identify_matches_expected_enrolled_profile(voice_profiles_dir):
    store = VoiceProfileStore(Extractor())
    store.enroll("Alice", np.ones(8, dtype=np.float32))
    turns = [DiarizationTurn(0, 1, 4)]
    matched = store.identify(np.ones(config.SAMPLE_RATE, dtype=np.float32), turns, ["Alice"])
    assert matched[0].profile_id == "Alice"
    assert matched[0].confidence == pytest.approx(1.0)
