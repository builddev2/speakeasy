"""Local speaker embeddings for optional meeting-speaker identification."""

import json
import os
import re
from dataclasses import replace

import numpy as np

from . import config, settings
from .meetings import DiarizationTurn

_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,39}")
_SCHEMA = 1


class VoiceProfileStore:
    """Extract, persist, and match speaker embeddings entirely on-device."""

    def __init__(self, extractor=None) -> None:
        self._extractor = extractor

    def _get_extractor(self):
        if self._extractor is None:
            import sherpa_onnx

            model = settings.diarization_model_dir() / "embedding.onnx"
            self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
                sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                    model=str(model), num_threads=2
                )
            )
        return self._extractor

    def _embedding(self, samples: np.ndarray) -> list[float]:
        extractor = self._get_extractor()
        stream = extractor.create_stream()
        stream.accept_waveform(config.SAMPLE_RATE, np.asarray(samples, dtype=np.float32))
        stream.input_finished()
        if not extractor.is_ready(stream):
            raise ValueError("Voice sample is too short for enrollment.")
        return [float(value) for value in extractor.compute(stream)]

    def enroll(self, name: str, samples: np.ndarray) -> None:
        name = name.strip()
        if not _NAME_RE.fullmatch(name):
            raise ValueError("Voice profile names use letters, digits, spaces, - and _.")
        embedding = self._embedding(samples)
        path = settings.voice_profiles_dir() / f"{name}.json"
        data = {
            "schema": _SCHEMA,
            "name": name,
            "model": "nemo_en_titanet_small",
            "embedding": embedding,
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def delete(self, name: str) -> None:
        if not _NAME_RE.fullmatch(name):
            raise ValueError(f"Invalid voice profile name: {name!r}")
        (settings.voice_profiles_dir() / f"{name}.json").unlink(missing_ok=True)

    def names(self) -> list[str]:
        return sorted(path.stem for path in settings.voice_profiles_dir().glob("*.json"))

    def _load(self, names: list[str] | None) -> dict[str, list[float]]:
        selected = set(names) if names else None
        result = {}
        for path in settings.voice_profiles_dir().glob("*.json"):
            if selected is not None and path.stem not in selected:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("schema") == _SCHEMA and isinstance(data.get("embedding"), list):
                    result[str(data.get("name") or path.stem)] = data["embedding"]
            except (OSError, ValueError):
                continue
        return result

    def identify(
        self,
        samples: np.ndarray,
        turns: list[DiarizationTurn],
        expected_names: list[str] | None = None,
    ) -> list[DiarizationTurn]:
        enrolled = self._load(expected_names)
        if not enrolled:
            return turns
        identified = []
        for speaker in sorted({turn.speaker for turn in turns}):
            chunks = [
                samples[int(turn.start * config.SAMPLE_RATE):int(turn.end * config.SAMPLE_RATE)]
                for turn in turns
                if turn.speaker == speaker and turn.end > turn.start
            ]
            if not chunks:
                continue
            try:
                embedding = self._embedding(np.concatenate(chunks))
            except ValueError:
                continue
            query = np.asarray(embedding, dtype=np.float32)
            query_norm = float(np.linalg.norm(query))
            scores = {}
            if query_norm:
                for candidate, stored in enrolled.items():
                    vector = np.asarray(stored, dtype=np.float32)
                    denominator = query_norm * float(np.linalg.norm(vector))
                    if denominator:
                        scores[candidate] = float(np.dot(query, vector) / denominator)
            name, score = max(scores.items(), key=lambda item: item[1], default=("", None))
            if score is None or score < config.SPEAKER_MATCH_THRESHOLD:
                name, score = "", None
            for turn in turns:
                if turn.speaker == speaker:
                    identified.append(replace(turn, profile_id=name or None, confidence=score))
        by_speaker = {turn.speaker for turn in identified}
        identified.extend(turn for turn in turns if turn.speaker not in by_speaker)
        return sorted(identified, key=lambda turn: turn.start)
