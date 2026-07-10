"""Local speech-to-text via Parakeet on Apple MLX."""

import os
import sys
import threading
import wave
from collections.abc import Callable
from pathlib import Path

from . import config, settings


class MeetingCancelled(Exception):
    """Raised inside transcribe_long when the user cancels processing."""


def _is_cached(model_id: str) -> bool:
    """Check the on-disk HF cache directly, without importing huggingface_hub.

    Avoids the ordering trap where HF_HUB_OFFLINE must be set *before*
    huggingface_hub is first imported (it's baked into a module constant at
    import time), so we can't decide by asking the library itself here.
    """
    cache_home = os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface"))
    hub_dir = Path(os.environ.get("HF_HUB_CACHE", os.path.join(cache_home, "hub")))
    snapshots = hub_dir / f"models--{model_id.replace('/', '--')}" / "snapshots"
    return snapshots.is_dir() and any(snapshots.iterdir())


# Skip the "check for a newer model" network call when we already have the
# model — cached in dev, bundled inside the .app when frozen — so startup is
# instant and offline-safe. On a genuinely first dev run (no cache yet),
# leave this unset so the model can still download.
if getattr(sys, "frozen", False) or _is_cached(config.MODEL_ID):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

# The packaged app excludes librosa (and its heavy numba/scipy tree); the
# shim provides the one librosa function parakeet-mlx actually calls.
try:
    import librosa  # noqa: F401
except ImportError:
    from . import _mel_shim

    _mel_shim.install()

import mlx.core as mx
import numpy as np
from parakeet_mlx import from_pretrained
from parakeet_mlx.alignment import (
    merge_longest_common_subsequence,
    merge_longest_contiguous,
    sentences_to_result,
    tokens_to_sentences,
)
from parakeet_mlx.audio import get_logmel


def read_wav_mono_f32(path: Path) -> np.ndarray:
    """Read a 16 kHz mono int16 WAV (a meeting spool) as float32 in [-1, 1]."""
    with wave.open(str(path)) as w:
        if w.getframerate() != config.SAMPLE_RATE or w.getnchannels() != 1:
            raise ValueError(
                f"Expected {config.SAMPLE_RATE} Hz mono spool, got "
                f"{w.getframerate()} Hz / {w.getnchannels()} ch: {path}"
            )
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return data.astype(np.float32) / 32768.0


class Transcriber:
    """Wraps the Parakeet MLX model.

    MLX arrays are pinned to the thread that creates them, so this class must
    be constructed *and* have transcribe() called on the same thread. The app
    does both on a single dedicated worker thread.
    """

    def __init__(self) -> None:
        source = settings.model_path()
        print(f"Loading speech model from {source}")
        self._model = from_pretrained(source)
        # First call triggers MLX graph compilation (~1s); pay it now with
        # a second of silence rather than on the user's first dictation.
        self.transcribe(np.zeros(config.SAMPLE_RATE, dtype=np.float32))

    def transcribe(self, audio: np.ndarray) -> str:
        # Feed the buffer to the model in-memory — the temp-WAV + ffmpeg
        # round-trip of model.transcribe(path) costs ~100 ms per dictation.
        mel = get_logmel(mx.array(audio), self._model.preprocessor_config)
        result = self._model.generate(mel)[0]
        return result.text.strip()

    def transcribe_long(
        self,
        audio: np.ndarray,
        *,
        progress: Callable[[float], None] = lambda fraction: None,
        cancel: threading.Event | None = None,
    ):
        """Chunked long-form transcription with timestamps (meetings).

        This is BaseParakeet.transcribe(path, chunk_duration=...) minus its
        ffmpeg dependency (absent from the packaged app) plus per-chunk
        cancellation, which the library's chunk_callback can't provide (its
        return value is ignored). Returns an AlignedResult whose sentence and
        token times are absolute within the recording — that's what the
        diarization turns are aligned against. Same thread rule as
        transcribe(): worker only.
        """
        sample_rate = config.SAMPLE_RATE
        chunk_samples = int(config.MEETING_CHUNK_SECONDS * sample_rate)
        overlap_samples = int(config.MEETING_OVERLAP_SECONDS * sample_rate)
        hop = self._model.preprocessor_config.hop_length

        all_tokens = []
        total = len(audio)
        for start in range(0, total, chunk_samples - overlap_samples):
            if cancel is not None and cancel.is_set():
                raise MeetingCancelled
            end = min(start + chunk_samples, total)
            if end - start < hop:
                break  # prevent zero-length log mel (same guard as upstream)
            mel = get_logmel(
                mx.array(audio[start:end]), self._model.preprocessor_config
            )
            chunk_result = self._model.generate(mel)[0]
            offset = start / sample_rate
            for sentence in chunk_result.sentences:
                for token in sentence.tokens:
                    token.start += offset
                    token.end = token.start + token.duration
            if not all_tokens:
                all_tokens = chunk_result.tokens
            else:
                try:
                    all_tokens = merge_longest_contiguous(
                        all_tokens,
                        chunk_result.tokens,
                        overlap_duration=config.MEETING_OVERLAP_SECONDS,
                    )
                except RuntimeError:
                    all_tokens = merge_longest_common_subsequence(
                        all_tokens,
                        chunk_result.tokens,
                        overlap_duration=config.MEETING_OVERLAP_SECONDS,
                    )
            progress(end / total)
        return sentences_to_result(tokens_to_sentences(all_tokens))
