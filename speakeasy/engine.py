"""DictationEngine: the dictation pipeline behind every front end.

Extraction of the original __main__ wiring, UI-independent so the terminal
flow (cli.py) and the menu bar app (ui/menubar.py) drive the same object.

Threading rules (unchanged from the original, load-bearing):
- The model is loaded AND used on the single `worker` thread — MLX pins its
  GPU arrays to the thread that created them.
- Recorder start/stop runs on the single `control` thread, never on the
  hotkey event-tap thread: CoreAudio's AudioOutputUnitStop deadlocks against
  the HAL mutex when called inside a CGEventTap callback, and a blocked tap
  callback gets the tap disabled by macOS.
- Hotkey callbacks return instantly; they only submit to `control`.

Meeting transcription follows the same rules: the meeting recorder starts and
stops on `control`, and the whole processing pipeline (chunked transcription →
diarization → alignment → save) runs as one job on `worker` — transcription
because of the MLX rule, diarization (CPU/onnxruntime, no pinning) to keep the
pipeline strictly sequential. Audio spools to disk and is deleted in the job's
`finally`, whatever happens; cancellation is a polled Event, so it needs no
extra thread.
"""

import subprocess
import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from enum import Enum
from pathlib import Path

import numpy as np

from . import config, injector, meeting_recorder, meetings
from .hotkey import HotkeyListener
from .meeting_recorder import MeetingRecorder
from .profiles import Profile
from .recorder import Recorder, RecorderBusy
from .transcriber import MeetingCancelled, Transcriber, read_wav_mono_f32


def play_sound(sound: str) -> None:
    if config.SOUNDS_ENABLED:
        subprocess.Popen(
            ["afplay", sound],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


class State(Enum):
    LOADING = "loading"
    READY = "ready"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    PAUSED = "paused"
    MEETING_RECORDING = "meeting_recording"
    MEETING_PROCESSING = "meeting_processing"


class DictationEngine:
    """Hold-to-talk dictation: record → transcribe → paste at the cursor.

    Front ends observe `on_state_changed(state)` — fired from worker/control
    threads, so subscribers marshal to their own thread (same contract as
    Overlay) — and may attach a duck-typed `overlay` before start().
    """

    def __init__(self) -> None:
        self.recorder = Recorder()
        self.meeting_recorder = MeetingRecorder()
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.control = ThreadPoolExecutor(max_workers=1)
        self.transcriber: Transcriber | None = None
        self.diarizer = None  # built lazily on the first meeting (loads ONNX)
        self.profile: Profile | None = None
        self.overlay = None
        self.state = State.LOADING
        self.on_state_changed: Callable[[State], None] = lambda state: None
        # Meeting observers, fired from the worker thread (same marshaling
        # contract as on_state_changed): a preformatted progress line for the
        # status item, and the saved meeting's id.
        self.on_meeting_progress: Callable[[str], None] = lambda text: None
        self.on_meeting_saved: Callable[[str], None] = lambda meeting_id: None
        self._listener = HotkeyListener(self._on_hold_start, self._on_hold_end)
        self._model_future: Future | None = None
        self._user_paused = False
        self._meeting_active = False
        self._meeting_cancel = threading.Event()

    # -- lifecycle ------------------------------------------------------

    def load_model(self) -> None:
        """Load the model, blocking (terminal path prints around this)."""
        self._ensure_model_submitted().result()

    def start(self) -> None:
        """Arm the hotkey and prewarm the mic.

        Kicks off a model load if none has happened yet; a recording made
        while loading simply queues behind the load on the worker thread
        and transcribes the moment the model is up.
        """
        self._ensure_model_submitted()
        # Open the mic stream (stopped) now so the first keypress doesn't pay
        # the ~100 ms CoreAudio open, which can clip the first syllable.
        self.control.submit(self.recorder.prewarm)
        # A crash or force-quit mid-meeting leaves its audio spool behind;
        # sweep it now — meeting audio is never persisted.
        self.control.submit(meeting_recorder.sweep_spool_dir)
        self._listener.start()

    def set_profile(self, profile: Profile | None) -> None:
        self.profile = profile

    @property
    def can_train(self) -> bool:
        """Training needs a loaded model, a named (non-Guest) profile, and no
        meeting in flight — training and meetings both want the hotkey and
        the mic. Single source of truth for the menu item and dock button."""
        return (
            self.transcriber is not None
            and self.profile is not None
            and self.state not in (State.MEETING_RECORDING, State.MEETING_PROCESSING)
        )

    def pause(self) -> None:
        """Suspend dictation, e.g. while the training window owns the hotkey."""
        self._user_paused = True
        self._listener.stop()
        # Discard any hold that was in flight — its release will never arrive.
        self.control.submit(self.recorder.stop)
        self._set_state(State.PAUSED)

    def resume(self) -> None:
        self._user_paused = False
        if self._meeting_active:
            # The training window closed mid-meeting: dictation stays off
            # until the meeting pipeline re-arms it in its finally block.
            return
        self._listener.resume()
        self._set_state(self._idle_state())

    def shutdown(self) -> None:
        self._listener.stop()
        # Quit mid-meeting: stop the mic and delete the spool (best-effort —
        # the atexit hook and next launch's sweep are the backstops).
        self._meeting_cancel.set()
        if self._meeting_active:
            self.meeting_recorder.force_close()
            self.meeting_recorder.discard()
        self.worker.shutdown(wait=False)
        self.control.shutdown(wait=False)

    def _ensure_model_submitted(self) -> Future:
        if self._model_future is None:
            self._model_future = self.worker.submit(self._create_transcriber)
        return self._model_future

    def _create_transcriber(self) -> None:
        self.transcriber = Transcriber()
        self._set_state(State.READY)

    def _idle_state(self) -> State:
        return State.READY if self.transcriber is not None else State.LOADING

    def _set_state(self, state: State) -> None:
        self.state = state
        self.on_state_changed(state)

    # -- recording flow ---------------------------------------------------

    # Hotkey callbacks run on the event-tap thread and must return instantly.
    def _on_hold_start(self) -> None:
        self.control.submit(self._start_recording)

    def _on_hold_end(self) -> None:
        self.control.submit(self._stop_recording)

    def _start_recording(self) -> None:
        try:
            self.recorder.start()
        except RecorderBusy:
            # A prior stop is still unwinding in CoreAudio; opening now would
            # deadlock. Drop this take and stay idle — the hotkey stays live,
            # and the next press works once the stop clears (or after relaunch
            # if the mic is genuinely wedged).
            print("  → (mic busy — a previous stop is still releasing; try again)")
            if self.overlay:
                self.overlay.hide()
            self._set_state(self._idle_state())
            return
        if self.overlay:
            self.overlay.show_recording(lambda: self.recorder.level)
        play_sound(config.SOUND_START)
        self._set_state(State.RECORDING)
        print("● recording...")

    def _stop_recording(self) -> None:
        audio = self._stop_recorder_guarded()
        if self.recorder.duration_seconds(audio) < config.MIN_DURATION_SECONDS:
            play_sound(config.SOUND_STOP)
            print("  → (too short, ignored)")
            if self.overlay:
                self.overlay.hide()
            self._set_state(self._idle_state())
            return
        # Kick off transcription before the sound: Popen costs ~10-30 ms.
        self.worker.submit(self._transcribe_and_paste, audio)
        if self.overlay:
            self.overlay.show_transcribing()
        play_sound(config.SOUND_STOP)
        self._set_state(State.TRANSCRIBING)

    def _stop_recorder_guarded(self) -> np.ndarray:
        """Stop the recorder without letting a wedged CoreAudio call freeze
        the control thread (and, since every hotkey press funnels through it,
        the whole dictation pipeline).

        stop() runs on a throwaway thread; if it hasn't returned within the
        timeout the audio device has hung, so the stream is abandoned — the mic
        is released and the next recording rebuilds it — and this take is
        dropped rather than blocking forever.
        """
        result: list[np.ndarray] = []
        done = threading.Event()

        def run() -> None:
            try:
                result.append(self.recorder.stop())
            finally:
                done.set()

        threading.Thread(target=run, name="recorder-stop", daemon=True).start()
        if done.wait(config.RECORDER_STOP_TIMEOUT_SECONDS) and result:
            return result[0]
        print("  → (recorder stop timed out; releasing the mic)")
        self.recorder.force_close()
        return np.empty(0, dtype=np.float32)

    # -- meeting flow -------------------------------------------------------

    def begin_meeting(self) -> None:
        """Start a meeting recording. Only meaningful from READY — the menu
        item is disabled otherwise, and _begin_meeting re-checks on control."""
        self.control.submit(self._begin_meeting)

    def end_meeting(self) -> None:
        self.control.submit(self._end_meeting)

    def cancel_meeting_processing(self) -> None:
        """Abort processing and discard the meeting. Just sets the event —
        the worker polls it between transcription chunks and phases, so no
        executor hop is needed (worker is busy processing anyway)."""
        self._meeting_cancel.set()

    def _begin_meeting(self) -> None:
        if self.state is not State.READY or self._meeting_active:
            return
        self._meeting_active = True
        # Dictation off for the duration: tear the tap down entirely (the
        # meeting recorder owns the session) and discard any hold in flight —
        # its release will never arrive.
        self._listener.stop()
        self.recorder.stop()
        try:
            self.meeting_recorder.start()
        except Exception:
            traceback.print_exc()
            self._meeting_active = False
            self._listener.resume()
            self._set_state(self._idle_state())
            return
        play_sound(config.SOUND_MEETING_START)
        self._set_state(State.MEETING_RECORDING)
        print("● meeting recording...")

    def _end_meeting(self) -> None:
        if not self._meeting_active or self.state is not State.MEETING_RECORDING:
            return
        wav_path = self._stop_meeting_recorder_guarded()
        play_sound(config.SOUND_MEETING_END)
        if wav_path is None:
            # Nothing made it to disk; back to dictation.
            self._meeting_active = False
            if not self._user_paused:
                self._listener.resume()
            self._set_state(self._idle_state())
            return
        self._meeting_cancel.clear()
        self._set_state(State.MEETING_PROCESSING)
        self.worker.submit(self._process_meeting, wav_path)

    def _stop_meeting_recorder_guarded(self) -> Path | None:
        """meeting_recorder.stop() under the same watchdog as the dictation
        recorder: a wedged CoreAudio stop must not freeze the control thread.
        On timeout the stream is abandoned but the spool is kept — everything
        up to the wedge is already on disk and still worth transcribing."""
        result: list[Path | None] = []
        done = threading.Event()

        def run() -> None:
            try:
                result.append(self.meeting_recorder.stop())
            finally:
                done.set()

        threading.Thread(target=run, name="meeting-stop", daemon=True).start()
        if done.wait(config.RECORDER_STOP_TIMEOUT_SECONDS) and result:
            return result[0]
        print("  → (meeting recorder stop timed out; releasing the mic)")
        self.meeting_recorder.force_close()
        return self.meeting_recorder.take_path()

    def _process_meeting(self, wav_path: Path) -> None:
        """The whole post-meeting pipeline, one worker job: transcribe →
        diarize → align → save transcript. The spool WAV dies in the finally
        no matter how this exits — audio is never persisted."""
        try:
            self.on_meeting_progress("Transcribing meeting… 0%")
            audio = read_wav_mono_f32(wav_path)
            duration = len(audio) / config.SAMPLE_RATE
            result = self.transcriber.transcribe_long(
                audio,
                progress=lambda f: self.on_meeting_progress(
                    f"Transcribing meeting… {int(f * 70)}%"
                ),
                cancel=self._meeting_cancel,
            )
            if self._meeting_cancel.is_set():
                raise MeetingCancelled
            if self.diarizer is None:
                from .diarizer import Diarizer

                self.diarizer = Diarizer()
            turns = self.diarizer.diarize(
                audio,
                progress=lambda f: self.on_meeting_progress(
                    f"Identifying speakers… {70 + int(f * 28)}%"
                ),
            )
            del audio
            if self._meeting_cancel.is_set():
                raise MeetingCancelled
            segments = meetings.align_speakers(result.sentences, turns)
            if self.profile is not None:
                for segment in segments:
                    segment.text = self.profile.apply(segment.text)
            meeting = meetings.Meeting.new(segments, duration_seconds=duration)
            meeting.save()
            print(f"  → meeting saved: {meeting.title} ({len(segments)} segments)")
            self.on_meeting_saved(meeting.meeting_id)
        except MeetingCancelled:
            print("  → meeting processing cancelled; nothing saved")
        except Exception:
            traceback.print_exc()
        finally:
            wav_path.unlink(missing_ok=True)
            meeting_recorder.release_spool(wav_path)
            self._meeting_active = False
            if not self._user_paused:
                self._listener.resume()
            self._set_state(self._idle_state())

    def _transcribe_and_paste(self, audio) -> None:
        previous = None
        try:
            # Save the clipboard now so the read overlaps with the GPU work.
            previous = injector.read_clipboard()
            started = time.perf_counter()
            text = self.transcriber.transcribe(audio)
            if self.profile is not None:
                text = self.profile.apply(text)
            elapsed = time.perf_counter() - started
            if text:
                print(f"  → {text!r}  ({elapsed:.2f}s)")
                # Deaf-en the hotkey tap while we synthesize Cmd+V so the
                # injected keystroke can never re-trigger recording.
                self._listener.pause()
                try:
                    injector.insert_text(text)
                finally:
                    # Don't re-arm the hotkey if the user paused dictation
                    # (training window open) while we were transcribing.
                    if not self._user_paused:
                        self._listener.resume()
                # Let the target app consume the paste before restoring the
                # clipboard — after resume(), so it's off the hotkey-dead window.
                time.sleep(config.PASTE_SETTLE_SECONDS)
            else:
                print("  → (no speech detected)")
        except Exception:
            traceback.print_exc()
        finally:
            # Always restore, even if transcription or the paste itself
            # raised — otherwise the dictated text is stranded on the
            # clipboard and the user's prior clipboard is lost.
            injector.restore_clipboard(previous)
            if self.overlay:
                self.overlay.hide()
            self._set_state(self._idle_state())
