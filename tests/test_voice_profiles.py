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
    path = voice_profiles_dir / "Alice.json"
    data = json.loads(path.read_text())
    assert data["embedding"] == [1.0, 1.0]
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(voice_profiles_dir.glob("*.wav"))
    assert store.names() == ["Alice"]
    store.delete("Alice")
    assert store.names() == []


def test_short_enrollment_is_rejected(voice_profiles_dir):
    store = VoiceProfileStore(Extractor())
    with pytest.raises(ValueError, match="too short"):
        store.enroll("Alice", np.ones(2, dtype=np.float32))


def test_reserved_anonymous_label_is_rejected(voice_profiles_dir):
    with pytest.raises(ValueError, match="reserved"):
        VoiceProfileStore(Extractor()).enroll(
            "Speaker 1", np.ones(8, dtype=np.float32)
        )


def test_existing_reserved_name_remains_loadable(voice_profiles_dir):
    (voice_profiles_dir / "Speaker 1.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "name": "Speaker 1",
                "model": "nemo_en_titanet_small",
                "embedding": [1.0, 1.0],
            }
        )
    )
    assert VoiceProfileStore(Extractor()).names() == ["Speaker 1"]


def test_identify_matches_expected_enrolled_profile(voice_profiles_dir):
    store = VoiceProfileStore(Extractor())
    store.enroll("Alice", np.ones(8, dtype=np.float32))
    turns = [DiarizationTurn(0, 1, 4)]
    matched = store.identify(
        np.ones(config.SAMPLE_RATE, dtype=np.float32), turns, ["Alice"]
    )
    assert matched[0].profile_id == "Alice"
    assert matched[0].confidence == pytest.approx(1.0)


def test_identify_never_assigns_one_profile_to_two_clusters(voice_profiles_dir):
    store = VoiceProfileStore(Extractor())
    store.enroll("Alice", np.ones(8, dtype=np.float32))
    turns = [DiarizationTurn(0, 1, 1), DiarizationTurn(1, 2, 2)]
    matched = store.identify(
        np.ones(2 * config.SAMPLE_RATE, dtype=np.float32), turns, ["Alice"]
    )
    assert [turn.profile_id for turn in matched] == ["Alice", None]


def test_ambiguous_match_stays_anonymous(voice_profiles_dir):
    store = VoiceProfileStore(Extractor())
    store.enroll("Alice", np.ones(8, dtype=np.float32))
    store.enroll("Alicia", np.full(8, 0.9, dtype=np.float32))
    turns = [DiarizationTurn(0, 1, 1)]
    matched = store.identify(
        np.ones(config.SAMPLE_RATE, dtype=np.float32), turns, ["Alice", "Alicia"]
    )
    assert matched[0].profile_id is None
    assert matched[0].confidence is None


def test_invalid_profile_is_not_listed_or_used(voice_profiles_dir):
    (voice_profiles_dir / "Broken.json").write_text(
        json.dumps({"schema": 99, "name": "Broken", "embedding": [1, 1]})
    )
    store = VoiceProfileStore(Extractor())
    assert store.names() == []
    turns = [DiarizationTurn(0, 1, 1)]
    assert store.identify(np.ones(config.SAMPLE_RATE), turns, ["Broken"]) == turns


def test_non_object_profile_json_is_ignored(voice_profiles_dir):
    (voice_profiles_dir / "Broken.json").write_text("[]")
    assert VoiceProfileStore(Extractor()).names() == []


def test_identification_can_stop_between_clusters(voice_profiles_dir):
    store = VoiceProfileStore(Extractor())
    store.enroll("Alice", np.ones(8, dtype=np.float32))
    turns = [DiarizationTurn(0, 1, 1), DiarizationTurn(1, 2, 2)]
    assert store.identify(
        np.ones(2 * config.SAMPLE_RATE), turns, ["Alice"], cancelled=lambda: True
    ) == turns


def test_identification_failure_keeps_anonymous_turns(voice_profiles_dir):
    VoiceProfileStore(Extractor()).enroll("Alice", np.ones(8, dtype=np.float32))

    class FailingExtractor(Extractor):
        def compute(self, stream):
            raise RuntimeError("ONNX failure")

    turns = [DiarizationTurn(0, 1, 1)]
    assert VoiceProfileStore(FailingExtractor()).identify(
        np.ones(config.SAMPLE_RATE), turns, ["Alice"]
    ) == turns
