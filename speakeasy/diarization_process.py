"""Speaker diarization for one meeting, run in a child process by the app.

Why a child: sherpa-onnx's onnxruntime arenas grow to the longest meeting so
far and never shrink (302 MB after a 3.2-min meeting, 804 MB after 29.3 min),
and macOS's large-malloc cache keeps even freed blocks in the footprint, so
deleting the models in-process returns almost nothing. A child that exits
returns all of it for ~0.7 s per meeting. Measurements:
docs/superpowers/specs/2026-09-30-diarization-subprocess-design.md.

This module must stay import-light (no mlx, parakeet_mlx or sherpa_onnx at
module level): the spawned child imports it first.
"""

import threading
from collections.abc import Callable
from pathlib import Path

from .meetings import DiarizationTurn
from .wav_io import read_wav_mono_f32


class DiarizationFailed(Exception):
    """Diarization could not produce turns; the meeting is saved unlabelled.

    `reason` is a privacy-safe code: launch_timeout, launch_failed,
    child_exited or child_error:<ExceptionTypeName>.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def turn_to_wire(turn) -> tuple:
    if not isinstance(turn, DiarizationTurn):
        turn = DiarizationTurn(float(turn[0]), float(turn[1]), int(turn[2]))
    return (
        float(turn.start), float(turn.end), int(turn.speaker),
        turn.confidence, bool(turn.overlap), turn.profile_id,
    )


def turn_from_wire(values) -> DiarizationTurn:
    start, end, speaker, confidence, overlap, profile_id = values
    return DiarizationTurn(start, end, speaker, confidence, overlap, profile_id)


def diarize_meeting_audio(
    audio,
    diarizer,
    embed: Callable,
    *,
    expected_count: int | None,
    max_speakers: int | None,
    voice_profile_names,
    progress: Callable[[float], None],
    cancelled: Callable[[], bool],
    on_phase: Callable[[str], None] = lambda phase: None,
) -> list:
    """diarize → tidy (only when the count is unknown) → identify (only with names)."""
    if not len(audio):
        raise ValueError("Meeting track has no readable audio")
    turns = diarizer.diarize(audio, progress=progress)
    if expected_count is None:
        # The user's own count is exact and wins. Otherwise fold the
        # over-split clusters (and apply the calendar cap) before naming
        # voices, so profile matching sees whole voices.
        from . import speaker_merge

        turns = speaker_merge.tidy_speakers(
            audio, turns, embed, max_speakers=max_speakers, cancelled=cancelled)
    on_phase("voice_identification")
    if voice_profile_names:
        from .voice_profiles import VoiceProfileStore

        turns = VoiceProfileStore().identify(
            audio, turns, list(voice_profile_names), cancelled=cancelled)
    return turns


class InProcessDiarizationRunner:
    """Runs the pipeline in this process.

    Tests inject fakes through it, and the memory benchmark uses it as the
    reference for the child's turns. The app uses ChildDiarizationRunner.
    """

    def __init__(self, diarizer=None, embed: Callable | None = None) -> None:
        self.diarizer = diarizer
        self.embed = embed

    def __call__(
        self,
        wav_path,
        *,
        expected_count: int | None,
        max_speakers: int | None,
        voice_profile_names,
        progress: Callable[[float], None],
        cancel: threading.Event,
        on_phase: Callable[[str], None] = lambda phase: None,
    ) -> list:
        audio = read_wav_mono_f32(Path(wav_path))
        diarizer = self.diarizer
        if diarizer is None:
            from .diarizer import Diarizer

            diarizer = Diarizer(expected_count)
        embed = self.embed
        if embed is None:
            # Built on first use only: tidy_speakers often needs no
            # embeddings, and tests replace VoiceProfileStore with fakes
            # that only implement identify().
            store = []

            def embed(samples):
                if not store:
                    from .voice_profiles import VoiceProfileStore

                    store.append(VoiceProfileStore())
                return store[0].embed(samples)
        return diarize_meeting_audio(
            audio, diarizer, embed,
            expected_count=expected_count, max_speakers=max_speakers,
            voice_profile_names=voice_profile_names, progress=progress,
            cancelled=cancel.is_set, on_phase=on_phase,
        )
