"""Audio preprocessing for the dictation path (pure NumPy, no hardware).

trim_silence() removes leading/trailing silence from a held-to-talk clip
before it reaches the model. Two payoffs: fewer mel frames through the encoder
(latency), and tighter per-feature normalization — Parakeet standardizes each
mel bin over the clip's time axis, so lead/tail silence skews the mean/std for
a short utterance. Interior silence is left alone; only the outer edges move.

Only dictation calls this. Meetings must not: trimming would shift transcript
timestamps out of sync with the diarization turns they're aligned against.
"""

import numpy as np

from . import config

# 25 ms analysis frames, 10 ms hop — standard short-time energy framing.
_FRAME_SECONDS = 0.025
_HOP_SECONDS = 0.010


def trim_silence(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    """Return `audio` with leading/trailing silence trimmed (80 ms margin).

    Returns the input unchanged when trimming is disabled, the clip is too
    short to frame, or no frame clears the speech threshold (e.g. pure
    silence) — never returns an empty array for a non-empty input.
    """
    if not config.DICTATION_TRIM_ENABLED or len(audio) == 0:
        return audio

    frame = int(_FRAME_SECONDS * sample_rate)
    hop = int(_HOP_SECONDS * sample_rate)
    if len(audio) < frame:
        return audio  # too short to analyse; leave it alone

    # Per-frame RMS via a strided view over the signal.
    starts = np.arange(0, len(audio) - frame + 1, hop)
    frames = np.stack([audio[s : s + frame] for s in starts])
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))

    threshold = max(rms.max() * config.TRIM_THRESHOLD_RATIO, config.TRIM_ABSOLUTE_FLOOR)
    voiced = np.nonzero(rms >= threshold)[0]
    if len(voiced) == 0:
        return audio  # all silence / below floor — let downstream drop it

    margin = int(config.TRIM_MARGIN_SECONDS * sample_rate)
    start = max(0, starts[voiced[0]] - margin)
    end = min(len(audio), starts[voiced[-1]] + frame + margin)
    return audio[start:end]
