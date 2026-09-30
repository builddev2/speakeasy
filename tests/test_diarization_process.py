"""diarization_process: the pure pipeline and the runners (no sherpa-onnx)."""

import subprocess
import sys
import threading
import wave

import numpy as np
import pytest

from speakeasy import config, meetings
from speakeasy.diarization_process import (
    DiarizationFailed,
    InProcessDiarizationRunner,
    diarize_meeting_audio,
    turn_from_wire,
    turn_to_wire,
)


class SplitRemoteDiarizer:
    """One real remote voice split into a big and a small cluster."""
    def diarize(self, samples, progress=lambda f: None):
        progress(1.0)
        return [(0.0, 0.8, 4), (1.0, 1.8, 9)]


def _pipeline(audio, diarizer, **overrides):
    kwargs = dict(
        expected_count=None, max_speakers=None, voice_profile_names=(),
        progress=lambda f: None, cancelled=lambda: False,
    )
    kwargs.update(overrides)
    embed = kwargs.pop("embed", lambda samples: np.array([1.0, 0.0]))
    return diarize_meeting_audio(audio, diarizer, embed, **kwargs)


def _audio(seconds=2):
    return np.zeros(config.SAMPLE_RATE * seconds, dtype=np.float32)


def test_cap_merges_speakers_when_count_unknown():
    turns = _pipeline(_audio(), SplitRemoteDiarizer(), max_speakers=1)
    # Equal talk (0.8 s each): the tie goes to the lower cluster id.
    assert {t.speaker for t in turns} == {4}


def test_manual_count_returns_diarizer_turns_unchanged():
    turns = _pipeline(_audio(), SplitRemoteDiarizer(), expected_count=2, max_speakers=1)
    assert turns == [(0.0, 0.8, 4), (1.0, 1.8, 9)]


def test_identify_runs_only_with_names(monkeypatch):
    from speakeasy import voice_profiles
    calls = []

    class FakeProfiles:
        def identify(self, audio, turns, names, *, cancelled):
            calls.append(names)
            return [meetings.DiarizationTurn(0.0, 0.8, 4, 0.9, False, "Alice")]

    monkeypatch.setattr(voice_profiles, "VoiceProfileStore", FakeProfiles)
    _pipeline(_audio(), SplitRemoteDiarizer(), expected_count=2)
    assert calls == []
    turns = _pipeline(_audio(), SplitRemoteDiarizer(), expected_count=2,
                      voice_profile_names=("Alice",))
    assert calls == [["Alice"]]
    assert turns[0].profile_id == "Alice"


def test_phase_is_reported_before_identification_even_without_names():
    phases = []
    _pipeline(_audio(), SplitRemoteDiarizer(), expected_count=2, on_phase=phases.append)
    assert phases == ["voice_identification"]


def test_empty_audio_raises_value_error():
    with pytest.raises(ValueError):
        _pipeline(np.empty(0, dtype=np.float32), SplitRemoteDiarizer())


def test_wire_round_trip_keeps_every_field():
    turn = meetings.DiarizationTurn(1.25, 2.5, 3, 0.75, True, "Alice")
    assert turn_from_wire(turn_to_wire(turn)) == turn
    assert turn_from_wire(turn_to_wire((0.0, 1.0, 2))) == meetings.DiarizationTurn(0.0, 1.0, 2)


def test_diarization_failed_carries_reason():
    assert DiarizationFailed("child_exited").reason == "child_exited"


def test_in_process_runner_reads_the_wav_itself(tmp_path):
    path = tmp_path / "track.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(config.SAMPLE_RATE)
        out.writeframes(np.full(config.SAMPLE_RATE * 2, 100, dtype=np.int16).tobytes())
    seen = []

    class Recording(SplitRemoteDiarizer):
        def diarize(self, samples, progress=lambda f: None):
            seen.append(len(samples))
            return super().diarize(samples, progress)

    runner = InProcessDiarizationRunner(Recording())
    turns = runner(path, expected_count=2, max_speakers=None, voice_profile_names=(),
                   progress=lambda f: None, cancel=threading.Event())
    assert seen == [config.SAMPLE_RATE * 2]
    assert turns == [(0.0, 0.8, 4), (1.0, 1.8, 9)]


def test_child_side_modules_do_not_import_mlx_or_sherpa():
    code = (
        "import sys\n"
        "import speakeasy.diarization_process, speakeasy.wav_io\n"
        "import speakeasy.speaker_merge, speakeasy.voice_profiles, speakeasy.diarizer\n"
        "heavy = [m for m in ('mlx', 'parakeet_mlx', 'sherpa_onnx') if m in sys.modules]\n"
        "assert not heavy, heavy\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_transcriber_still_exports_read_wav_mono_f32():
    from speakeasy import transcriber, wav_io
    assert transcriber.read_wav_mono_f32 is wav_io.read_wav_mono_f32
