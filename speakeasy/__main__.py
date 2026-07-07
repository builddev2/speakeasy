"""Speakeasy: hold Right Command, speak, release — text appears at your cursor."""

import signal
import subprocess
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import config, injector
from .hotkey import HotkeyListener
from .recorder import Recorder
from .transcriber import Transcriber


def _play(sound: str) -> None:
    if config.SOUNDS_ENABLED:
        subprocess.Popen(
            ["afplay", sound],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def main() -> None:
    recorder = Recorder()
    # Single worker so transcriptions run off the hotkey thread, in order.
    # The model is both loaded and used on this one thread because MLX pins
    # arrays to their creating thread (see Transcriber docstring).
    worker = ThreadPoolExecutor(max_workers=1)
    # Recorder start/stop runs here, never on the event-tap callback thread:
    # CoreAudio's AudioOutputUnitStop deadlocks against the HAL mutex when
    # called from inside a CGEventTap callback while an AppKit run loop is
    # active, and a blocked tap callback gets the tap disabled by macOS.
    control = ThreadPoolExecutor(max_workers=1)

    print(f"Loading model {config.MODEL_ID} (first run downloads ~600 MB)...")
    transcriber = worker.submit(Transcriber).result()

    overlay = None
    if config.OVERLAY_ENABLED:
        from AppKit import (
            NSApplication,
            NSApplicationActivationPolicyAccessory,
        )

        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        from .overlay import Overlay

        overlay = Overlay()

    def transcribe_and_paste(audio) -> None:
        try:
            started = time.perf_counter()
            text = transcriber.transcribe(audio)
            elapsed = time.perf_counter() - started
            if text:
                print(f"  → {text!r}  ({elapsed:.2f}s)")
                injector.insert_text(text)
            else:
                print("  → (no speech detected)")
        except Exception:
            traceback.print_exc()
        finally:
            if overlay:
                overlay.hide()

    def _start_recording() -> None:
        recorder.start()
        if overlay:
            overlay.show_recording(lambda: recorder.level)
        _play(config.SOUND_START)
        print("● recording...")

    def _stop_recording() -> None:
        audio = recorder.stop()
        _play(config.SOUND_STOP)
        duration = len(audio) / config.SAMPLE_RATE
        if duration < config.MIN_DURATION_SECONDS:
            print("  → (too short, ignored)")
            if overlay:
                overlay.hide()
            return
        if overlay:
            overlay.show_transcribing()
        worker.submit(transcribe_and_paste, audio)

    # Hotkey callbacks run on the event-tap thread and must return instantly.
    def on_hold_start() -> None:
        control.submit(_start_recording)

    def on_hold_end() -> None:
        control.submit(_stop_recording)

    listener = HotkeyListener(on_hold_start, on_hold_end)
    listener.start()
    hotkey_name = str(config.HOTKEY).removeprefix("Key.")
    print(f"Ready. Hold [{hotkey_name}] and speak; release to insert text. Ctrl-C to quit.")

    if overlay:
        # AppKit run loop owns the main thread; Ctrl-C terminates via the
        # signal handler (the overlay's heartbeat timer lets it fire).
        def _sigint(signum, frame):
            print("\nShutting down.")
            listener.stop()
            worker.shutdown(wait=False)
            control.shutdown(wait=False)
            app.terminate_(None)

        signal.signal(signal.SIGINT, _sigint)
        signal.signal(signal.SIGTERM, _sigint)
        app.run()
    else:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down.")
        finally:
            listener.stop()
            worker.shutdown(wait=False)
            control.shutdown(wait=False)


if __name__ == "__main__":
    main()
