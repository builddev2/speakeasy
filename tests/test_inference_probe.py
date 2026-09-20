import wave
import pytest
from speakeasy.inference_probe import validate_fixture


def test_empty_fixture_rejected_before_model_import(tmp_path):
    wav, reference = tmp_path / 'empty.wav', tmp_path / 'ref.txt'
    reference.write_text('reference')
    with wave.open(str(wav), 'wb') as output:
        output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
    with pytest.raises(ValueError, match='empty'):
        validate_fixture(wav, reference)
