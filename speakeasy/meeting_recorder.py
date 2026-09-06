"""Long-form meeting capture: mic → bounded queue → writer thread → spool WAV.

Unlike the dictation Recorder (which keeps a take in RAM for seconds), a
meeting runs for hours, so audio spools straight to an int16 WAV in
settings.spool_dir() and RAM stays flat regardless of length. The PortAudio
callback must never block or touch the disk — it only copies bytes into a
bounded queue that a dedicated writer thread drains; if the writer ever
stalls (disk hiccup), frames are dropped rather than letting the queue grow
without bound.

The input stream is opened in shared mode (CoreAudio's default — PortAudio
never requests exclusive access), so a live Zoom/Teams call and a meeting
recording can use the microphone at the same time; neither blocks the other.

Spool files are the app's only unpersisted-audio window: the engine sweeps
spool_dir() clean at every launch, and an atexit hook unlinks the active
spool, so a crash or force-quit mid-meeting never leaves audio behind.
"""

import atexit
import queue
import threading
import time as monotonic_time
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd

from . import config, settings
from .coreaudio import RecorderBusy, teardown
from .meeting_stream import MeetingASRSession
from .system_audio import SystemAudioRecorder, SystemAudioUnavailable

# ~30 s of audio at 16 kHz int16 in ~1 KiB blocks. The exact block size varies
# by host buffer; this is generous enough that only a genuinely stuck writer
# ever drops frames.
_QUEUE_MAX_BLOCKS = 1024
_WRITER_LAG_BLOCKS = _QUEUE_MAX_BLOCKS * 3 // 4
_FORCE_CLOSE_WRITER_TIMEOUT_SECONDS = 0.25

_active_spools: set[Path] = set()
_active_lock = threading.Lock()


@dataclass(frozen=True)
class MeetingCaptureHealth:
    mic_first_buffer: bool = False
    system_first_buffer: bool = False
    system_nonzero_signal: bool = False
    mic_dropped_frames: int = 0
    system_dropped_frames: int = 0
    mic_writer_failed: bool = False
    system_writer_failed: bool = False
    mic_writer_lagged: bool = False
    system_writer_lagged: bool = False
    helper_exited: bool = False
    helper_exit_reason: str | None = None
    capture_outcome: str = "recording"
    fallback_reason: str | None = None
    capture_mode: str = "mic_only"
    capture_scope: str = "mic_only"

    def to_dict(self) -> dict[str, bool | int | str | None]:
        return {
            "mic_first_buffer": self.mic_first_buffer,
            "system_first_buffer": self.system_first_buffer,
            "system_nonzero_signal": self.system_nonzero_signal,
            "mic_dropped_frames": self.mic_dropped_frames,
            "system_dropped_frames": self.system_dropped_frames,
            "mic_writer_failed": self.mic_writer_failed,
            "system_writer_failed": self.system_writer_failed,
            "mic_writer_lagged": self.mic_writer_lagged,
            "system_writer_lagged": self.system_writer_lagged,
            "helper_exited": self.helper_exited,
            "helper_exit_reason": self.helper_exit_reason,
            "capture_outcome": self.capture_outcome,
            "fallback_reason": self.fallback_reason,
            "capture_mode": self.capture_mode,
            "capture_scope": self.capture_scope,
        }


@dataclass(frozen=True)
class MeetingRecording:
    mic_path: Path | None
    system_path: Path | None = None
    mic_start_ns: int | None = None
    system_start_ns: int | None = None
    capture_mode: str = "mic_only"
    system_audio_status: str = "unavailable"
    health: MeetingCaptureHealth | None = None
    capture_scope: str = "mic_only"

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(path for path in (self.mic_path, self.system_path) if path)

    @property
    def track_offsets_seconds(self) -> dict[str, float]:
        starts = [
            value
            for path, value in (
                (self.mic_path, self.mic_start_ns),
                (self.system_path, self.system_start_ns),
            )
            if path is not None and value is not None
        ]
        if not starts:
            return {"mic": 0.0, **({"system": 0.0} if self.system_path else {})}
        origin = min(starts)
        offsets = {}
        if self.mic_path is not None:
            offsets["mic"] = max(
                0.0,
                (
                    (self.mic_start_ns if self.mic_start_ns is not None else origin)
                    - origin
                )
                / 1e9,
            )
        if self.system_path is not None:
            offsets["system"] = max(
                0.0,
                (
                    (
                        self.system_start_ns
                        if self.system_start_ns is not None
                        else origin
                    )
                    - origin
                )
                / 1e9,
            )
        return offsets


def _cleanup_active_spools() -> None:
    with _active_lock:
        for path in _active_spools:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


atexit.register(_cleanup_active_spools)


def sweep_spool_dir() -> None:
    """Delete every file in spool_dir(). Called once per launch: anything
    still there is an orphan from a crash or force-quit and must go —
    meeting audio is never persisted."""
    try:
        for path in settings.spool_dir().iterdir():
            try:
                path.unlink()
                print(f"  Swept orphaned spool file: {path.name}")
            except OSError:
                pass
    except OSError:
        pass


class MeetingRecorder:
    def __init__(self) -> None:
        self._stream: sd.InputStream | None = None
        self._queue: queue.Queue[bytes | None] = queue.Queue(_QUEUE_MAX_BLOCKS)
        self._writer: threading.Thread | None = None
        self._wav: wave.Wave_write | None = None
        self._path: Path | None = None
        self._recording = False
        self._level = 0.0
        self._frames = 0
        self._dropped = 0
        self._first_buffer_ns: int | None = None
        self._writer_failed = False
        self._writer_lagged = False
        self._pretranscription: MeetingASRSession | None = None

    def configure_pretranscription(
        self, session: MeetingASRSession | None
    ) -> None:
        if self._recording:
            raise RecorderBusy()
        self._pretranscription = session

    @property
    def level(self) -> float:
        """RMS of the most recent block (0.0 when not recording)."""
        return self._level

    @property
    def elapsed_seconds(self) -> float:
        return self._frames / config.SAMPLE_RATE

    @property
    def first_buffer_ns(self) -> int | None:
        return self._first_buffer_ns

    @property
    def dropped_frames(self) -> int:
        return self._dropped

    @property
    def writer_failed(self) -> bool:
        return self._writer_failed

    @property
    def writer_lagged(self) -> bool:
        return self._writer_lagged

    def start(self) -> None:
        if self._recording:
            return
        if self._writer is not None:
            if self._writer.is_alive():
                raise RecorderBusy()
            self._writer = None
        if teardown.in_flight:
            # A CoreAudio teardown — a previous meeting's or a dictation take's,
            # the HAL mutex doesn't care which — is still unwinding. Opening now
            # would deadlock against it and freeze the control thread, so refuse
            # before anything is allocated (no spool file, no writer thread).
            raise RecorderBusy()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._path = settings.spool_dir() / f"meeting-{stamp}-mic.wav"
        with _active_lock:
            _active_spools.add(self._path)
        self._wav = wave.open(str(self._path), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(config.SAMPLE_RATE)
        self._queue = queue.Queue(_QUEUE_MAX_BLOCKS)
        self._frames = 0
        self._dropped = 0
        self._writer_failed = False
        self._writer_lagged = False
        self._level = 0.0
        self._first_buffer_ns = None
        self._writer = threading.Thread(
            target=self._drain, name="meeting-writer", daemon=True
        )
        self._recording = True
        self._writer.start()
        # Own stream, separate from the dictation Recorder's prewarmed one —
        # int16 (half the disk), and its lifetime is the meeting's, not the
        # app's. Shared-mode input: coexists with Zoom/Teams on the same mic.
        try:
            self._stream = sd.InputStream(
                samplerate=config.SAMPLE_RATE,
                channels=1,
                dtype="int16",
                callback=self._on_audio,
            )
            self._stream.start()
        except Exception:
            self._recording = False
            self._finish_writer()
            path, self._path = self._path, None
            if path is not None:
                path.unlink(missing_ok=True)
                release_spool(path)
            raise

    def _on_audio(self, indata: np.ndarray, frames: int, time, status) -> None:
        if not self._recording:
            return
        if self._frames >= config.MEETING_MAX_SECONDS * config.SAMPLE_RATE:
            return  # spool cap: an abandoned recording must not fill the disk
        try:
            self._queue.put_nowait(bytes(indata))
        except queue.Full:
            self._dropped += frames
            self._writer_lagged = True
            return
        if self._queue.qsize() >= _WRITER_LAG_BLOCKS:
            self._writer_lagged = True
        if self._first_buffer_ns is None:
            observed = monotonic_time.monotonic_ns()
            adc_time = getattr(time, "inputBufferAdcTime", None)
            current_time = getattr(time, "currentTime", None)
            if isinstance(adc_time, (int, float)) and isinstance(
                current_time, (int, float)
            ):
                observed -= max(0, int((current_time - adc_time) * 1e9))
            self._first_buffer_ns = observed
        self._frames += frames
        block = indata[:, 0].astype(np.float32) / 32768.0
        self._level = float(np.sqrt(np.mean(block**2)))

    def _drain(self) -> None:
        try:
            while True:
                block = self._queue.get()
                if block is None:
                    return
                try:
                    self._wav.writeframes(block)
                except Exception:
                    # Disk full/unwritable: keep draining so stop() doesn't
                    # hang; the shortfall shows up as a shorter WAV.
                    self._writer_failed = True
                    if self._pretranscription is not None:
                        self._pretranscription.cancel()
                else:
                    if self._pretranscription is not None:
                        self._pretranscription.add_pcm(block)
        finally:
            wav, self._wav = self._wav, None
            if wav is not None:
                try:
                    wav.close()
                except OSError:
                    self._writer_failed = True
                    if self._pretranscription is not None:
                        self._pretranscription.cancel()
            pretranscription, self._pretranscription = (
                self._pretranscription,
                None,
            )
            if pretranscription is not None:
                pretranscription.finish()

    def _signal_writer_stop(self) -> None:
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            # Make room without blocking the control path; dropping one final
            # block is safer than waiting on a stalled disk writer.
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(None)

    def stop(self) -> Path | None:
        """Stop capture, finish the WAV (header fix-up happens on close),
        and return the spool path. The caller owns the file from here."""
        if not self._recording:
            return None
        self._recording = False
        self._level = 0.0
        if self._stream is not None:
            # abort(), not stop(): same wedged-device rationale as
            # Recorder.stop — there is nothing to drain that we can't afford
            # to lose (at most the final host buffer).
            #
            # Mark the teardown in flight across both CoreAudio calls. If either
            # wedges (the watchdog in engine._stop_meeting_recorder_guarded then
            # abandons this thread mid-HAL), the marker stays raised and the next
            # open — the dictation stream re-arming after the meeting, or another
            # meeting — refuses instead of deadlocking on the HAL mutex.
            with teardown.in_progress():
                try:
                    self._stream.abort()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
        self._finish_writer()
        if self._dropped:
            print(f"  → meeting writer dropped {self._dropped} frames")
        path, self._path = self._path, None
        return path

    def _finish_writer(self) -> None:
        writer = self._writer
        if writer is None:
            return
        self._signal_writer_stop()
        writer.join(timeout=10.0)
        if not writer.is_alive():
            self._writer = None

    def force_close(self) -> None:
        """Abandon a wedged stream (watchdog path — same contract as
        Recorder.force_close): drop the reference without touching the stuck
        call, ask the writer to finalize the WAV, and keep the path so the
        caller can still process or discard what made it to disk."""
        self._recording = False
        self._level = 0.0
        self._stream = None
        writer = self._writer
        if writer is not None:
            self._signal_writer_stop()
            writer.join(timeout=_FORCE_CLOSE_WRITER_TIMEOUT_SECONDS)
            if not writer.is_alive():
                self._writer = None
            elif self._pretranscription is not None:
                self._pretranscription.cancel()

    def take_path(self) -> Path | None:
        """The current spool path (after force_close), surrendering ownership."""
        path, self._path = self._path, None
        return path

    def discard(self) -> None:
        """Delete the spool without processing it (cancel/quit paths)."""
        path, self._path = self._path, None
        if path is not None:
            path.unlink(missing_ok=True)
            release_spool(path)


def release_spool(path: Path) -> None:
    """Forget a spool path once it is deleted (pairs with the atexit hook)."""
    with _active_lock:
        _active_spools.discard(path)


class MeetingCaptureRecorder:
    """Own the independent mic and system tracks for one meeting."""

    def __init__(
        self,
        mic: MeetingRecorder | None = None,
        system: SystemAudioRecorder | None = None,
    ) -> None:
        self.mic = mic or MeetingRecorder()
        self.system = system or SystemAudioRecorder()
        self.capture_mode = "mic_only"
        self.system_audio_status = self.system.status
        self._system_active = False
        self._system_path: Path | None = None
        self._last_recording: MeetingRecording | None = None
        self._final_health: MeetingCaptureHealth | None = None
        self.capture_scope = "mic_only"
        self._requested_scope = "global"

    @property
    def level(self) -> float:
        return self.mic.level

    @property
    def elapsed_seconds(self) -> float:
        return self.mic.elapsed_seconds

    def configure_pretranscription(
        self, session: MeetingASRSession | None
    ) -> None:
        self.mic.configure_pretranscription(session)

    @property
    def health(self) -> MeetingCaptureHealth:
        if self._final_health is not None:
            return self._final_health
        fallback_reason = (
            None if self.capture_mode == "mic_and_system" else self.system_audio_status
        )
        return MeetingCaptureHealth(
            mic_first_buffer=self.mic.first_buffer_ns is not None,
            system_first_buffer=self.system.first_buffer_ns is not None,
            system_nonzero_signal=self.system.nonzero_signal,
            mic_dropped_frames=self.mic.dropped_frames,
            system_dropped_frames=self.system.dropped_frames,
            mic_writer_failed=self.mic.writer_failed,
            system_writer_failed=self.system.writer_failed,
            mic_writer_lagged=self.mic.writer_lagged,
            system_writer_lagged=self.system.writer_lagged,
            helper_exited=self.system.helper_exited,
            helper_exit_reason=self.system.helper_exit_reason,
            capture_outcome="recording",
            fallback_reason=fallback_reason,
            capture_mode=self.capture_mode,
            capture_scope=self.capture_scope,
        )

    def start(self, *, system_audio_pid: int | None = None) -> None:
        self._final_health = None
        self._requested_scope = (
            "selected" if system_audio_pid is not None else "global"
        )
        self.mic.start()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        system_path = settings.spool_dir() / f"meeting-{stamp}-system.wav"
        self._system_path = system_path
        with _active_lock:
            _active_spools.add(system_path)
        try:
            self.system.start(system_path, process_id=system_audio_pid)
        except SystemAudioUnavailable as exc:
            system_path.unlink(missing_ok=True)
            release_spool(system_path)
            self._system_path = None
            self.capture_mode = "mic_only"
            self.capture_scope = (
                "selected_app_unavailable"
                if self._requested_scope == "selected"
                else "mic_only"
            )
            self.system_audio_status = exc.reason
            self._system_active = False
            return
        except Exception:
            system_path.unlink(missing_ok=True)
            release_spool(system_path)
            self._system_path = None
            self.capture_mode = "mic_only"
            self.capture_scope = (
                "selected_app_unavailable"
                if self._requested_scope == "selected"
                else "mic_only"
            )
            self.system_audio_status = "start_failed"
            self._system_active = False
            return
        self.capture_mode = "mic_and_system"
        self.capture_scope = self._requested_scope
        self.system_audio_status = "capturing"
        self._system_active = True

    def stop(self) -> MeetingRecording | None:
        mic_path = self.mic.stop()
        system_result = self.system.stop() if self._system_active else None
        self._system_active = False
        self._system_path = None
        if mic_path is None and (system_result is None or system_result.path is None):
            return None
        system_path = system_result.path if system_result else None
        system_start = system_result.first_buffer_ns if system_result else None
        system_status = (
            system_result.status if system_result else self.system_audio_status
        )
        if system_result and system_status == "captured":
            if system_start is None:
                system_status = "no_system_audio"
            elif not system_result.nonzero_signal:
                system_status = "silent_system_audio"
            elif system_result.writer_failed:
                system_status = "system_writer_failed"
        mode = (
            "mic_and_system"
            if system_path is not None
            and system_start is not None
            and system_status == "captured"
            else "mic_only"
        )
        fallback_reason = None if mode == "mic_and_system" else system_status
        capture_scope = (
            self._requested_scope
            if mode == "mic_and_system"
            else (
                "selected_app_unavailable"
                if self._requested_scope == "selected"
                else "mic_only"
            )
        )
        self._final_health = MeetingCaptureHealth(
            mic_first_buffer=self.mic.first_buffer_ns is not None,
            system_first_buffer=system_start is not None,
            system_nonzero_signal=(
                system_result.nonzero_signal if system_result else False
            ),
            mic_dropped_frames=self.mic.dropped_frames,
            system_dropped_frames=(
                system_result.dropped_frames if system_result else 0
            ),
            mic_writer_failed=self.mic.writer_failed,
            system_writer_failed=(
                system_result.writer_failed if system_result else False
            ),
            mic_writer_lagged=self.mic.writer_lagged,
            system_writer_lagged=(
                system_result.writer_lagged if system_result else False
            ),
            helper_exited=(system_result.helper_exited if system_result else False),
            helper_exit_reason=(
                system_result.helper_exit_reason if system_result else None
            ),
            capture_outcome="captured" if mode == "mic_and_system" else "fallback",
            fallback_reason=fallback_reason,
            capture_mode=mode,
            capture_scope=capture_scope,
        )
        recording = MeetingRecording(
            mic_path=mic_path,
            system_path=system_path,
            mic_start_ns=self.mic.first_buffer_ns,
            system_start_ns=system_start,
            capture_mode=mode,
            system_audio_status=system_status,
            health=self._final_health,
            capture_scope=capture_scope,
        )
        self._last_recording = recording
        self.capture_mode = mode
        self.capture_scope = capture_scope
        self.system_audio_status = system_status
        return recording

    def force_close(self) -> None:
        self.mic.force_close()
        self.system.force_close()
        self._system_active = False

    def take_recording(self) -> MeetingRecording | None:
        if self._last_recording is not None:
            recording, self._last_recording = self._last_recording, None
            return recording
        mic_path = self.mic.take_path()
        system_result = self.system.take_result()
        self._system_path = None
        if mic_path is None and system_result.path is None:
            return None
        system_status = system_result.status
        if system_status == "captured" and system_result.first_buffer_ns is None:
            system_status = "no_system_audio"
        mode = (
            "mic_and_system"
            if system_result.path
            and system_result.first_buffer_ns is not None
            and system_status == "captured"
            else "mic_only"
        )
        capture_scope = (
            self._requested_scope
            if mode == "mic_and_system"
            else (
                "selected_app_unavailable"
                if self._requested_scope == "selected"
                else "mic_only"
            )
        )
        self._final_health = MeetingCaptureHealth(
            mic_first_buffer=self.mic.first_buffer_ns is not None,
            system_first_buffer=system_result.first_buffer_ns is not None,
            system_nonzero_signal=system_result.nonzero_signal,
            mic_dropped_frames=self.mic.dropped_frames,
            system_dropped_frames=system_result.dropped_frames,
            mic_writer_failed=self.mic.writer_failed,
            system_writer_failed=system_result.writer_failed,
            mic_writer_lagged=self.mic.writer_lagged,
            system_writer_lagged=system_result.writer_lagged,
            helper_exited=system_result.helper_exited,
            helper_exit_reason=system_result.helper_exit_reason,
            capture_outcome="forced_close",
            fallback_reason=None if mode == "mic_and_system" else system_status,
            capture_mode=mode,
            capture_scope=capture_scope,
        )
        return MeetingRecording(
            mic_path=mic_path,
            system_path=system_result.path,
            mic_start_ns=self.mic.first_buffer_ns,
            system_start_ns=system_result.first_buffer_ns,
            capture_mode=mode,
            system_audio_status=system_status,
            health=self._final_health,
            capture_scope=capture_scope,
        )

    def discard(self) -> None:
        system_path, self._system_path = self._system_path, None
        self.mic.discard()
        self.system.discard()
        if system_path is not None:
            system_path.unlink(missing_ok=True)
            release_spool(system_path)
        if self._last_recording is not None:
            for path in self._last_recording.paths:
                path.unlink(missing_ok=True)
                release_spool(path)
            self._last_recording = None
