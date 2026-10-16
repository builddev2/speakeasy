"""Child-process check of a downloaded speech model (`--verify-model <dir>`).

Loads the model, compares every parameter shape with a fresh model built from
its config, then transcribes a spoken test sentence. Exit 0 passes; otherwise
exit 1 with the reason on stderr and stdout. Offline: no network, and it never
touches App Support.
"""

import json
import re
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import mlx.core as mx
import numpy as np
from mlx.utils import tree_flatten
from parakeet_mlx.audio import get_logmel
from parakeet_mlx.utils import from_config

from .transcriber import _load_model

SENTENCE = "The quick brown fox jumps over the lazy dog."
SAY = "/usr/bin/say"
_NEEDED = 7


def _say(path) -> None:
    subprocess.run([SAY, "-o", str(path), "--data-format=LEI16@16000", SENTENCE],
                   check=True, capture_output=True, timeout=60)


def _read_wav(path) -> np.ndarray:
    """The `say` output (LEI16@16000, mono) as float32 in [-1, 1)."""
    with wave.open(str(path)) as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(
                f"test audio must be 16-bit mono 16000 Hz, got {w.getsampwidth() * 8}-bit "
                f"{w.getnchannels()} ch {w.getframerate()} Hz")
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _transcribe(model, path) -> str:
    # In memory, as Transcriber.transcribe does: model.transcribe(path) shells
    # out to ffmpeg, which the packaged app does not ship.
    mel = get_logmel(mx.array(_read_wav(path)), model.preprocessor_config)
    return model.generate(mel)[0].text


def _words(text) -> list[str]:
    return re.sub(r"[^\w\s]", "", text.lower()).split()


def _shapes(model) -> dict:
    return {k: tuple(v.shape) for k, v in tree_flatten(model.parameters())}


def _fail(reason) -> int:
    reason = " ".join(str(reason).split())[:200]
    print(reason, file=sys.stderr)
    print(reason, flush=True)
    return 1


def _check(directory) -> int:
    with open(directory / "config.json") as f:
        cfg = json.load(f)
    model = _load_model(str(directory))
    got, want = _shapes(model), _shapes(from_config(cfg))
    for name, shape in want.items():
        if name not in got:
            return _fail(f"parameter {name} is missing")
        if got[name] != shape:
            return _fail(f"shape mismatch in {name}")
    extra = set(got) - set(want)
    if extra:
        return _fail(f"unexpected parameter {sorted(extra)[0]}")
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "check.wav"
        _say(wav)
        heard = set(_words(_transcribe(model, wav)))
    hits = sum(1 for w in _words(SENTENCE) if w in heard)
    if hits < _NEEDED:
        return _fail(f"transcription matched {hits} of 9 words")
    return 0


def main(argv) -> int:
    if len(argv) != 1:
        return _fail("usage: --verify-model <dir>")
    try:
        return _check(Path(argv[0]))
    except Exception as exc:
        return _fail(f"{type(exc).__name__}: {exc}")
