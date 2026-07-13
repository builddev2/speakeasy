"""Local speaker embeddings for optional meeting-speaker identification."""

import json
import os
import re
from collections.abc import Callable
from dataclasses import replace

import numpy as np

from . import config, settings
from .meetings import DiarizationTurn

_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,39}")
_ANONYMOUS_RE = re.compile(r"Speaker [1-9][0-9]*", re.IGNORECASE)
_SCHEMA = 1
_MODEL = "nemo_en_titanet_small"


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
        stream.accept_waveform(
            config.SAMPLE_RATE, np.asarray(samples, dtype=np.float32)
        )
        stream.input_finished()
        if not extractor.is_ready(stream):
            raise ValueError("Voice sample is too short for enrollment.")
        embedding = np.asarray(extractor.compute(stream), dtype=np.float32)
        if (
            embedding.ndim != 1
            or not embedding.size
            or not np.all(np.isfinite(embedding))
        ):
            raise ValueError("Speaker embedding model returned an invalid result.")
        return [float(value) for value in embedding]

    def enroll(self, name: str, samples: np.ndarray) -> None:
        name = name.strip()
        if not _NAME_RE.fullmatch(name):
            raise ValueError(
                "Voice profile names use letters, digits, spaces, - and _."
            )
        if _ANONYMOUS_RE.fullmatch(name):
            raise ValueError(
                "Voice profile names can't use the reserved Speaker N labels."
            )
        if any(
            existing.casefold() == name.casefold() and existing != name
            for existing in self.names()
        ):
            raise ValueError(
                "Voice profile names must be unique ignoring capitalization."
            )
        embedding = self._embedding(samples)
        path = settings.voice_profiles_dir() / f"{name}.json"
        data = {
            "schema": _SCHEMA,
            "name": name,
            "model": _MODEL,
            "embedding": embedding,
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    def delete(self, name: str) -> None:
        name = name.strip()
        if not _NAME_RE.fullmatch(name):
            raise ValueError(f"Invalid voice profile name: {name!r}")
        (settings.voice_profiles_dir() / f"{name}.json").unlink(missing_ok=True)

    def names(self) -> list[str]:
        return sorted(self._load(None))

    def _load(self, names: list[str] | None) -> dict[str, list[float]]:
        selected = None if names is None else set(names)
        result = {}
        for path in settings.voice_profiles_dir().glob("*.json"):
            if selected is not None and path.stem not in selected:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                name = data.get("name")
                vector = np.asarray(data.get("embedding"), dtype=np.float32)
                if (
                    data.get("schema") == _SCHEMA
                    and data.get("model") == _MODEL
                    and name == path.stem
                    and isinstance(name, str)
                    and _NAME_RE.fullmatch(name)
                    and vector.ndim == 1
                    and vector.size > 0
                    and np.all(np.isfinite(vector))
                ):
                    result[name] = [float(value) for value in vector]
            except (OSError, TypeError, ValueError):
                continue
        return result

    def identify(
        self,
        samples: np.ndarray,
        turns: list[DiarizationTurn],
        expected_names: list[str] | None = None,
        *,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> list[DiarizationTurn]:
        enrolled = self._load(expected_names)
        if not enrolled:
            return turns
        proposals: list[tuple[float, int, str]] = []
        for speaker in sorted({turn.speaker for turn in turns}):
            if cancelled():
                return turns
            chunks = [
                samples[
                    max(0, int(turn.start * config.SAMPLE_RATE)):
                    min(len(samples), int(turn.end * config.SAMPLE_RATE))
                ]
                for turn in turns
                if turn.speaker == speaker and turn.end > turn.start
            ]
            chunks = [chunk for chunk in chunks if len(chunk)]
            if not chunks:
                continue
            try:
                embedding = self._embedding(np.concatenate(chunks))
            except (ImportError, OSError, RuntimeError, TypeError, ValueError) as err:
                print(
                    f"  → speaker identification skipped for cluster {speaker}: {err}"
                )
                continue
            if cancelled():
                return turns
            query = np.asarray(embedding, dtype=np.float32)
            query_norm = float(np.linalg.norm(query))
            scores = {}
            if query_norm:
                for candidate, stored in enrolled.items():
                    vector = np.asarray(stored, dtype=np.float32)
                    if vector.shape != query.shape:
                        continue
                    denominator = query_norm * float(np.linalg.norm(vector))
                    if denominator:
                        scores[candidate] = float(np.dot(query, vector) / denominator)
            ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
            if not ranked or ranked[0][1] < config.SPEAKER_MATCH_THRESHOLD:
                continue
            runner_up = ranked[1][1] if len(ranked) > 1 else -1.0
            if ranked[0][1] - runner_up < config.SPEAKER_MATCH_MARGIN:
                continue
            proposals.append((ranked[0][1], speaker, ranked[0][0]))

        assignments: dict[int, tuple[str, float]] = {}
        used_names: set[str] = set()
        for score, speaker, name in sorted(
            proposals, key=lambda item: (-item[0], item[1], item[2])
        ):
            if name in used_names:
                continue
            assignments[speaker] = (name, score)
            used_names.add(name)
        return [
            replace(
                turn,
                profile_id=assignments[turn.speaker][0],
                confidence=assignments[turn.speaker][1],
            )
            if turn.speaker in assignments
            else turn
            for turn in turns
        ]
