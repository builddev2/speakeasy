"""Local speech-to-text via Parakeet on Apple MLX."""

import os
from pathlib import Path

from . import config


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
# model cached, so startup is instant and offline-safe. On a genuinely first
# run (no cache yet), leave this unset so the model can still download.
if _is_cached(config.MODEL_ID):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

import mlx.core as mx
import numpy as np
from parakeet_mlx import from_pretrained
from parakeet_mlx.audio import get_logmel


class Transcriber:
    """Wraps the Parakeet MLX model.

    MLX arrays are pinned to the thread that creates them, so this class must
    be constructed *and* have transcribe() called on the same thread. The app
    does both on a single dedicated worker thread.
    """

    def __init__(self) -> None:
        self._model = from_pretrained(config.MODEL_ID)
        # First call triggers MLX graph compilation (~1s); pay it now with
        # a second of silence rather than on the user's first dictation.
        self.transcribe(np.zeros(config.SAMPLE_RATE, dtype=np.float32))

    def transcribe(self, audio: np.ndarray) -> str:
        # Feed the buffer to the model in-memory — the temp-WAV + ffmpeg
        # round-trip of model.transcribe(path) costs ~100 ms per dictation.
        mel = get_logmel(mx.array(audio), self._model.preprocessor_config)
        result = self._model.generate(mel)[0]
        return result.text.strip()
