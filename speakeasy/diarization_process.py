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

import multiprocessing
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from . import config
from .meetings import DiarizationTurn
from .wav_io import read_wav_mono_f32


# App quit is a hard C exit: no cleanup runs in the parent, so the child polls.
_PARENT_POLL_SECONDS = 0.5


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


def _watch_parent() -> None:
    parent = os.getppid()

    def watch() -> None:
        while True:
            time.sleep(_PARENT_POLL_SECONDS)
            if os.getppid() != parent:
                os._exit(0)

    threading.Thread(target=watch, name="speakeasy-diarization-watchdog",
                     daemon=True).start()


def serve(conn, work) -> None:
    """Child side of the protocol. Only exception type names cross the pipe.

    Exits after the parent dies (see _PARENT_POLL_SECONDS): within ~0.5 s once
    Python code runs, but not during sherpa-onnx's segmentation, which holds the
    GIL with no progress callbacks (~58 s on a 29.3-min meeting).
    """
    _watch_parent()
    last = [-1.0]

    def progress(fraction: float) -> None:
        # sherpa reports per chunk; forward at most every 1 % (and the end).
        if fraction >= 1.0 or fraction - last[0] >= 0.01:
            last[0] = fraction
            conn.send(("progress", float(fraction)))

    try:
        turns = work(
            lambda: conn.send(("ready",)),
            progress,
            lambda phase: conn.send(("phase", phase)),
        )
        conn.send(("turns", [turn_to_wire(turn) for turn in turns]))
    except Exception as error:
        try:
            conn.send(("error", type(error).__name__))
        except (OSError, ValueError):
            pass
    finally:
        conn.close()


def run_child(conn, wav_path, expected_count, max_speakers, voice_profile_names) -> None:
    """Spawn target: the whole diarization stage for one track."""

    def work(ready, progress, on_phase):
        import sherpa_onnx  # noqa: F401  (import cost counts toward the launch budget)

        from .diarizer import Diarizer
        from .voice_profiles import VoiceProfileStore

        ready()
        audio = read_wav_mono_f32(Path(wav_path))
        return diarize_meeting_audio(
            audio, Diarizer(expected_count), VoiceProfileStore().embed,
            expected_count=expected_count, max_speakers=max_speakers,
            voice_profile_names=voice_profile_names, progress=progress,
            cancelled=lambda: False,  # the parent cancels by terminating us
            on_phase=on_phase,
        )

    serve(conn, work)


class ChildDiarizationRunner:
    """Parent side: one spawned child per call, always reaped."""

    def __init__(
        self,
        target=None,
        *,
        launch_timeout: float = config.DIARIZATION_CHILD_LAUNCH_TIMEOUT_SECONDS,
        poll_seconds: float = 0.1,
        terminate_grace: float = 2.0,
    ) -> None:
        self._target = target or run_child
        self._launch_timeout = launch_timeout
        self._poll_seconds = poll_seconds
        self._terminate_grace = terminate_grace

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
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(
            target=self._target,
            args=(sender, str(wav_path), expected_count, max_speakers,
                  tuple(voice_profile_names or ())),
            name="speakeasy-diarization",
            daemon=True,
        )
        finished = False
        try:
            try:
                process.start()
            except Exception:
                raise DiarizationFailed("launch_failed") from None
            sender.close()
            ready = False
            deadline = time.monotonic() + self._launch_timeout
            while True:
                if cancel.is_set():
                    from .transcriber import MeetingCancelled

                    raise MeetingCancelled
                if receiver.poll(self._poll_seconds):
                    try:
                        message = receiver.recv()
                    except (EOFError, OSError):
                        raise DiarizationFailed("child_exited") from None
                    kind = message[0]
                    if kind == "ready":
                        ready = True
                    elif kind == "progress":
                        progress(float(message[1]))
                    elif kind == "phase":
                        on_phase(str(message[1]))
                    elif kind == "turns":
                        finished = True
                        return [turn_from_wire(values) for values in message[1]]
                    elif kind == "error":
                        raise DiarizationFailed(f"child_error:{message[1]}")
                    continue
                if not ready and time.monotonic() > deadline:
                    raise DiarizationFailed("launch_timeout")
                if not process.is_alive() and not receiver.poll(0):
                    raise DiarizationFailed("child_exited")
        finally:
            self._reap(process, graceful=finished)
            receiver.close()
            if not sender.closed:
                sender.close()

    def _reap(self, process, *, graceful: bool) -> None:
        if process.pid is None:
            return
        if graceful:
            process.join(self._terminate_grace)
        if process.is_alive():
            process.terminate()
            process.join(self._terminate_grace)
        if process.is_alive():
            process.kill()
            process.join(self._terminate_grace)
