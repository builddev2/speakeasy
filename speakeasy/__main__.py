"""Speakeasy: hold Right Command, speak, release — text appears at your cursor."""

import argparse
import signal
import subprocess
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import config, injector
from .hotkey import HotkeyListener
from .profiles import Profile, list_profiles
from .recorder import Recorder
from .transcriber import Transcriber


def _play(sound: str) -> None:
    if config.SOUNDS_ENABLED:
        subprocess.Popen(
            ["afplay", sound],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _ask(prompt: str, default: str = "") -> str:
    """input() that returns `default` on empty input or a closed stdin."""
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        return default
    return answer or default


def _summary(profile: Profile) -> str:
    """'jason  (12 words, 8 corrections)' — clauses dropped when zero."""
    v, c = len(profile.vocabulary), len(profile.corrections)
    parts = []
    if v:
        parts.append(f"{v} word{'s' if v != 1 else ''}")
    if c:
        parts.append(f"{c} correction{'s' if c != 1 else ''}")
    return f"{profile.name}  ({', '.join(parts)})" if parts else profile.name


def _create_profile() -> Profile | None:
    while True:
        name = input("Name for the new profile (blank to cancel): ").strip()
        if not name:
            return None
        try:
            return Profile.create(name)
        except ValueError as err:
            print(f"  {err}")


def _ask_train_now() -> bool:
    return _ask("Train it now? [Y/n]: ", "y") not in ("n", "no")


def _pick_to_train(profiles: list[Profile]) -> Profile:
    """Choose which existing profile to train (auto-selects the only one)."""
    if len(profiles) == 1:
        return profiles[0]
    while True:
        print("\nWhich profile to train?")
        for i, p in enumerate(profiles, start=1):
            print(f"  {i}. {_summary(p)}")
        choice = _ask("Choose [1]: ", "1")
        if choice.isdigit() and 1 <= int(choice) <= len(profiles):
            return profiles[int(choice) - 1]
        print("  Enter a number from the list.")


def _first_run_menu(train_mode: bool) -> tuple[Profile | None, bool]:
    print("\nWelcome to Speakeasy — no profiles yet.")
    print("A profile learns how you say names and jargon for sharper transcription.\n")
    print("  n. Create your first profile")
    if not train_mode:
        print("  g. Continue as guest (no corrections)")
    while True:
        choice = _ask("Choose [n]: ", "n")
        if choice == "g" and not train_mode:
            return None, False
        if choice == "n":
            profile = _create_profile()
            if profile is not None:
                return profile, train_mode or _ask_train_now()
            continue
        print("  Enter 'n'" + ("." if train_mode else " or 'g'."))


def _returning_menu(
    profiles: list[Profile], train_mode: bool
) -> tuple[Profile | None, bool]:
    header = "Which profile to train?" if train_mode else "Speakeasy — choose a profile:"
    while True:
        print(f"\n{header}\n")
        for i, p in enumerate(profiles, start=1):
            print(f"  {i}. {_summary(p)}")
        print("\n  n. New profile")
        if not train_mode:
            print("  t. Train a profile")
            print("  g. Guest (no corrections)")
        choice = _ask("Choose [1]: ", "1")
        if choice.isdigit() and 1 <= int(choice) <= len(profiles):
            return profiles[int(choice) - 1], train_mode
        if choice == "n":
            profile = _create_profile()
            if profile is not None:
                return profile, train_mode or _ask_train_now()
            continue
        if choice == "t" and not train_mode:
            return _pick_to_train(profiles), True
        if choice == "g" and not train_mode:
            return None, False
        print("  Enter a number from the list, or one of the letters shown.")


def select_profile(
    preselected: str | None, force_train: bool
) -> tuple[Profile | None, bool]:
    """Startup login: returns (active profile or None for guest, train_requested).

    Renders before the model loads, so it appears instantly. `force_train`
    (from --train) hides the guest/train options and treats every profile
    selection as train-and-dictate — you can't train guest.
    """
    if preselected:
        if preselected in list_profiles():
            return Profile.load(preselected), force_train
        print(f"Profile '{preselected}' not found.")

    profiles = [Profile.load(name) for name in list_profiles()]
    if not profiles:
        return _first_run_menu(force_train)
    return _returning_menu(profiles, force_train)


def main() -> None:
    parser = argparse.ArgumentParser(prog="speakeasy", description=__doc__)
    parser.add_argument(
        "--profile", metavar="NAME", help="use this profile (skips the picker)"
    )
    parser.add_argument(
        "--train",
        action="store_true",
        help="run a guided training session for a profile, then dictate",
    )
    args = parser.parse_args()

    # Log in before the model loads so the menu appears instantly.
    profile, train_requested = select_profile(args.profile, force_train=args.train)
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

    if train_requested and profile is not None:
        from .training import run_training

        control.submit(recorder.prewarm).result()
        run_training(profile, recorder, transcriber, worker, control, _play)

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
            # Save the clipboard now so the read overlaps with the GPU work.
            previous = injector.read_clipboard()
            started = time.perf_counter()
            text = transcriber.transcribe(audio)
            if profile is not None:
                text = profile.apply(text)
            elapsed = time.perf_counter() - started
            if text:
                print(f"  → {text!r}  ({elapsed:.2f}s)")
                # Drop the hotkey tap while we synthesize Cmd+V: a live pynput
                # listener in this process duplicates the injected paste.
                listener.pause()
                try:
                    injector.insert_text(text)
                finally:
                    listener.resume()
                # Let the target app consume the paste before restoring the
                # clipboard — after resume(), so it's off the hotkey-dead window.
                time.sleep(config.PASTE_SETTLE_SECONDS)
                injector.restore_clipboard(previous)
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
        duration = len(audio) / config.SAMPLE_RATE
        if duration < config.MIN_DURATION_SECONDS:
            _play(config.SOUND_STOP)
            print("  → (too short, ignored)")
            if overlay:
                overlay.hide()
            return
        # Kick off transcription before the sound: Popen costs ~10-30 ms.
        worker.submit(transcribe_and_paste, audio)
        if overlay:
            overlay.show_transcribing()
        _play(config.SOUND_STOP)

    # Hotkey callbacks run on the event-tap thread and must return instantly.
    def on_hold_start() -> None:
        control.submit(_start_recording)

    def on_hold_end() -> None:
        control.submit(_stop_recording)

    # Open the mic stream (stopped) now so the first keypress doesn't pay
    # the ~100 ms CoreAudio open, which can clip the first syllable.
    control.submit(recorder.prewarm)

    listener = HotkeyListener(on_hold_start, on_hold_end)
    listener.start()
    hotkey_name = str(config.HOTKEY).removeprefix("Key.")
    profile_tag = f" [profile: {profile.name}]" if profile is not None else ""
    print(
        f"Ready{profile_tag}. Hold [{hotkey_name}] and speak; "
        "release to insert text. Ctrl-C to quit."
    )

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
