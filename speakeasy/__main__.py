"""Speakeasy: hold Right Option, speak, release — text appears at your cursor."""

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

    print(f"Loading model {config.MODEL_ID} (first run downloads ~600 MB)...")
    transcriber = worker.submit(Transcriber).result()

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

    def on_hold_start() -> None:
        recorder.start()
        _play(config.SOUND_START)
        print("● recording...")

    def on_hold_end() -> None:
        audio = recorder.stop()
        _play(config.SOUND_STOP)
        duration = len(audio) / config.SAMPLE_RATE
        if duration < config.MIN_DURATION_SECONDS:
            print("  → (too short, ignored)")
            return
        worker.submit(transcribe_and_paste, audio)

    listener = HotkeyListener(on_hold_start, on_hold_end)
    listener.start()
    hotkey_name = str(config.HOTKEY).removeprefix("Key.")
    print(f"Ready. Hold [{hotkey_name}] and speak; release to insert text. Ctrl-C to quit.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        listener.stop()
        worker.shutdown(wait=False)


if __name__ == "__main__":
    main()
