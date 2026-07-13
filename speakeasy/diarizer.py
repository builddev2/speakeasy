"""Speaker diarization via sherpa-onnx (CPU, fully offline).

Two small ONNX models — a pyannote segmentation model and a speaker-embedding
model — turn a meeting recording into (start, end, speaker) turns that
meetings.align_speakers() maps onto the transcript. Both models are bundled
into the .app (Contents/Resources/diarization); a dev checkout fetches them
once with scripts/fetch_diarization_models.sh. Runtime never touches the
network.

onnxruntime is not MLX: it has no thread-pinning rule, but the engine still
runs diarization inside the meeting-processing job on the worker thread so
the pipeline stays strictly sequential. Thread counts are capped at 2 so a
2-hour diarization pass can't starve other apps.

sherpa_onnx is imported lazily (inside __init__) so importing this module —
and everything that imports it, like the engine — works in tests without the
dependency installed.
"""

from collections.abc import Callable
from dataclasses import replace

import numpy as np

from . import config, meetings, settings


class Diarizer:
    def __init__(self, expected_speaker_count: int | None = None) -> None:
        import sherpa_onnx

        model_dir = settings.diarization_model_dir()
        segmentation = model_dir / "segmentation.onnx"
        embedding = model_dir / "embedding.onnx"
        if not (segmentation.is_file() and embedding.is_file()):
            raise FileNotFoundError(
                f"Diarization models missing from {model_dir} — run "
                "scripts/fetch_diarization_models.sh (dev) or rebuild the app."
            )
        self._sd = sherpa_onnx.OfflineSpeakerDiarization(
            sherpa_onnx.OfflineSpeakerDiarizationConfig(
                segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                    pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                        model=str(segmentation)
                    ),
                    num_threads=2,
                ),
                embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                    model=str(embedding), num_threads=2
                ),
                clustering=sherpa_onnx.FastClusteringConfig(
                    num_clusters=expected_speaker_count or -1,
                    threshold=config.DIARIZATION_THRESHOLD,
                ),
                min_duration_on=config.DIARIZATION_MIN_ON,
                min_duration_off=config.DIARIZATION_MIN_OFF,
            )
        )
        if self._sd.sample_rate != config.SAMPLE_RATE:
            raise ValueError(
                f"Diarization models expect {self._sd.sample_rate} Hz, "
                f"app records at {config.SAMPLE_RATE} Hz"
            )

    def diarize(
        self,
        samples: np.ndarray,
        progress: Callable[[float], None] = lambda fraction: None,
    ) -> list[meetings.DiarizationTurn]:
        """Speaker turns for a mono float32 recording, sorted by start.

        The callback reports progress only — its abort return value is
        ignored by sherpa-onnx 1.13.4's binding, so a cancel during this
        phase takes effect when process() returns.
        """

        def on_progress(done: int, total: int) -> int:
            if total > 0:
                progress(done / total)
            return 0

        result = self._sd.process(samples, callback=on_progress)
        turns = [
            meetings.DiarizationTurn(segment.start, segment.end, segment.speaker)
            for segment in result.sort_by_start_time()
        ]
        # sherpa returns concurrent segments for overlapping voices. Preserve
        # that information instead of silently forcing overlap onto one label.
        overlapping = set()
        for left, first in enumerate(turns):
            for right in range(left + 1, len(turns)):
                second = turns[right]
                if second.start >= first.end:
                    break
                if first.speaker != second.speaker and second.end > first.start:
                    overlapping.update((left, right))
        return [
            replace(turn, overlap=True) if index in overlapping else turn
            for index, turn in enumerate(turns)
        ]
