"""Local speech-to-text via Parakeet on Apple MLX."""

import tempfile
import wave
from pathlib import Path

import numpy as np
from parakeet_mlx import from_pretrained

from . import config


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
        # parakeet-mlx's transcribe API takes a file path, so round-trip
        # the buffer through a temporary 16-bit WAV.
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            path = Path(f.name)
        try:
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(config.SAMPLE_RATE)
                w.writeframes(pcm.tobytes())
            result = self._model.transcribe(path)
            return result.text.strip()
        finally:
            path.unlink(missing_ok=True)
