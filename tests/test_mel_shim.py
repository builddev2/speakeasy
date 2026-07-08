"""Parity test: the mel shim must reproduce librosa.filters.mel exactly.

Runs in the dev venv, where the real librosa is installed (it is a declared
dependency of parakeet-mlx). The packaged app ships only the shim, so this
test is what makes that substitution provably safe.
"""

import numpy as np
import pytest

from speakeasy import _mel_shim

librosa = pytest.importorskip("librosa")

# Parakeet-tdt-0.6b-v2's actual preprocessor parameters, plus variations to
# guard the general shape of the port.
PARAM_SETS = [
    dict(sr=16_000, n_fft=512, n_mels=128, fmin=0, fmax=8_000.0),  # Parakeet
    dict(sr=16_000, n_fft=400, n_mels=80, fmin=0, fmax=8_000.0),
    dict(sr=22_050, n_fft=1024, n_mels=64, fmin=0, fmax=11_025.0),
]


@pytest.mark.parametrize("params", PARAM_SETS)
def test_matches_librosa(params):
    ours = _mel_shim.mel(norm="slaney", **params)
    theirs = librosa.filters.mel(norm="slaney", **params)
    assert ours.shape == theirs.shape
    assert ours.dtype == theirs.dtype
    np.testing.assert_allclose(ours, theirs, atol=1e-8)


def test_fmax_defaults_to_nyquist():
    ours = _mel_shim.mel(sr=16_000, n_fft=512, n_mels=128)
    theirs = librosa.filters.mel(sr=16_000, n_fft=512, n_mels=128)
    np.testing.assert_allclose(ours, theirs, atol=1e-8)


def test_unported_modes_fail_loudly():
    with pytest.raises(NotImplementedError):
        _mel_shim.mel(sr=16_000, n_fft=512, htk=True)
    with pytest.raises(NotImplementedError):
        _mel_shim.mel(sr=16_000, n_fft=512, norm=None)


def test_install_is_noop_when_librosa_present():
    # Real librosa is imported (module-level above), so install() must not
    # replace it with the stub.
    _mel_shim.install()
    import librosa as check

    assert check is librosa
