"""Drop-in replacement for the one librosa function Speakeasy needs.

parakeet-mlx imports librosa solely to call ``librosa.filters.mel`` once at
model construction (building the mel filterbank matrix). librosa itself drags
in numba, llvmlite, scipy and friends — hundreds of megabytes that are fragile
to freeze into a standalone .app bundle. This module is a pure-numpy port of
that single function, and ``install()`` registers it as a stub ``librosa``
package so ``import librosa`` inside parakeet-mlx resolves to it.

In the dev venv the real librosa is installed and this module is never
activated; the packaged app excludes librosa and activates the shim. The
parity test in tests/test_mel_shim.py asserts the two produce identical
filterbanks for Parakeet's parameters.

Ported from librosa (https://github.com/librosa/librosa), ISC license,
Copyright (c) 2013--2023, librosa development team.
"""

import sys
import types

import numpy as np

# Slaney mel scale constants (librosa's default, htk=False).
_F_SP = 200.0 / 3
_MIN_LOG_HZ = 1000.0
_MIN_LOG_MEL = _MIN_LOG_HZ / _F_SP
_LOGSTEP = np.log(6.4) / 27.0


def _hz_to_mel(frequencies: np.ndarray) -> np.ndarray:
    frequencies = np.asanyarray(frequencies, dtype=np.float64)
    mels = frequencies / _F_SP
    log_t = frequencies >= _MIN_LOG_HZ
    mels[log_t] = _MIN_LOG_MEL + np.log(frequencies[log_t] / _MIN_LOG_HZ) / _LOGSTEP
    return mels


def _mel_to_hz(mels: np.ndarray) -> np.ndarray:
    mels = np.asanyarray(mels, dtype=np.float64)
    freqs = _F_SP * mels
    log_t = mels >= _MIN_LOG_MEL
    freqs[log_t] = _MIN_LOG_HZ * np.exp(_LOGSTEP * (mels[log_t] - _MIN_LOG_MEL))
    return freqs


def mel(
    *,
    sr: float,
    n_fft: int,
    n_mels: int = 128,
    fmin: float = 0.0,
    fmax: float | None = None,
    htk: bool = False,
    norm: str | None = "slaney",
    dtype=np.float32,
):
    """Mel filterbank matrix, shape (n_mels, 1 + n_fft // 2).

    Matches librosa.filters.mel for the Slaney scale + Slaney normalization
    (librosa's defaults, and the only form parakeet-mlx uses). Other modes
    were deliberately not ported — fail loudly rather than differ silently.
    """
    if htk:
        raise NotImplementedError("mel shim only implements the Slaney scale (htk=False)")
    if norm != "slaney":
        raise NotImplementedError("mel shim only implements norm='slaney'")
    if fmax is None:
        fmax = float(sr) / 2

    fftfreqs = np.fft.rfftfreq(n=n_fft, d=1.0 / sr)
    # n_mels + 2 band edges, equally spaced on the mel scale.
    min_mel, max_mel = _hz_to_mel(np.array([fmin, fmax]))
    mel_f = _mel_to_hz(np.linspace(min_mel, max_mel, n_mels + 2))

    fdiff = np.diff(mel_f)
    ramps = np.subtract.outer(mel_f, fftfreqs)

    weights = np.zeros((n_mels, 1 + n_fft // 2), dtype=dtype)
    for i in range(n_mels):
        lower = -ramps[i] / fdiff[i]
        upper = ramps[i + 2] / fdiff[i + 1]
        weights[i] = np.maximum(0, np.minimum(lower, upper))

    # Slaney-style area normalization: each triangle integrates to ~equal energy.
    enorm = 2.0 / (mel_f[2 : n_mels + 2] - mel_f[:n_mels])
    weights *= enorm[:, np.newaxis]
    return weights


def install() -> None:
    """Register stub librosa modules so `import librosa` resolves to this shim.

    No-op if the real librosa is importable or a stub is already installed.
    """
    if "librosa" in sys.modules:
        return
    librosa_mod = types.ModuleType("librosa")
    filters_mod = types.ModuleType("librosa.filters")
    filters_mod.mel = mel
    librosa_mod.filters = filters_mod
    sys.modules["librosa"] = librosa_mod
    sys.modules["librosa.filters"] = filters_mod
