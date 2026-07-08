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

Future meeting recording plugs in here: long-form audio can be submitted to
the same `worker` executor (keeping the one-model-thread rule) with results
going to a file sink instead of the paste path.
"""

import subprocess
import time
import traceback
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from enum import Enum

from . import config, injector
from .hotkey import HotkeyListener
from .profiles import Profile
from .recorder import Recorder
from .transcriber import Transcriber


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


class DictationEngine:
    """Hold-to-talk dictation: record → transcribe → paste at the cursor.

    Front ends observe `on_state_changed(state)` — fired from worker/control
    threads, so subscribers marshal to their own thread (same contract as
    Overlay) — and may attach a duck-typed `overlay` before start().
    """

    def __init__(self) -> None:
        self.recorder = Recorder()
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.control = ThreadPoolExecutor(max_workers=1)
        self.transcriber: Transcriber | None = None
        self.profile: Profile | None = None
        self.overlay = None
        self.state = State.LOADING
        self.on_state_changed: Callable[[State], None] = lambda state: None
        self._listener = HotkeyListener(self._on_hold_start, self._on_hold_end)
        self._model_future: Future | None = None

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
        self._listener.start()

    def set_profile(self, profile: Profile | None) -> None:
        self.profile = profile

    def pause(self) -> None:
        """Suspend dictation, e.g. while the training window owns the hotkey."""
        self._listener.stop()
        self._set_state(State.PAUSED)

    def resume(self) -> None:
        self._listener.resume()
        self._set_state(self._idle_state())

    def shutdown(self) -> None:
        self._listener.stop()
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
        self.recorder.start()
        if self.overlay:
            self.overlay.show_recording(lambda: self.recorder.level)
        play_sound(config.SOUND_START)
        self._set_state(State.RECORDING)
        print("● recording...")

    def _stop_recording(self) -> None:
        audio = self.recorder.stop()
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

    def _transcribe_and_paste(self, audio) -> None:
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
                # Drop the hotkey tap while we synthesize Cmd+V: a live pynput
                # listener in this process duplicates the injected paste.
                self._listener.pause()
                try:
                    injector.insert_text(text)
                finally:
                    self._listener.resume()
                # Let the target app consume the paste before restoring the
                # clipboard — after resume(), so it's off the hotkey-dead window.
                time.sleep(config.PASTE_SETTLE_SECONDS)
                injector.restore_clipboard(previous)
            else:
                print("  → (no speech detected)")
        except Exception:
            traceback.print_exc()
        finally:
            if self.overlay:
                self.overlay.hide()
            self._set_state(self._idle_state())
