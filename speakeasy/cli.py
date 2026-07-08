"""Terminal front end: profile picker menus + the classic run loop.

This is the original `python -m speakeasy` experience, now driving the
shared DictationEngine. The menu bar app (ui/menubar.py) replaces it as the
default; this stays for development and for guided training via --train.
"""

import signal
import time

from . import config
from .engine import DictationEngine, play_sound
from .profiles import Profile, list_profiles, load_profiles


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
            try:
                return Profile.load(preselected), force_train
            except (OSError, ValueError) as err:
                print(f"Profile '{preselected}' couldn't be read: {err}")
        else:
            print(f"Profile '{preselected}' not found.")

    profiles = load_profiles()
    if not profiles:
        return _first_run_menu(force_train)
    return _returning_menu(profiles, force_train)


def run(profile_name: str | None, train: bool) -> None:
    # Log in before the model loads so the menu appears instantly.
    profile, train_requested = select_profile(profile_name, force_train=train)
    engine = DictationEngine()
    engine.set_profile(profile)

    print(f"Loading model {config.MODEL_ID} (first run downloads ~2.3 GB)...")
    engine.load_model()

    if train_requested and profile is not None:
        from .training import run_training

        engine.control.submit(engine.recorder.prewarm).result()
        run_training(
            profile,
            engine.recorder,
            engine.transcriber,
            engine.worker,
            engine.control,
            play_sound,
        )

    app = None
    if config.OVERLAY_ENABLED:
        from AppKit import (
            NSApplication,
            NSApplicationActivationPolicyAccessory,
        )

        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        from .ui.overlay import Overlay

        engine.overlay = Overlay()

    engine.start()
    hotkey_name = config.hotkey_name()
    profile_tag = f" [profile: {profile.name}]" if profile is not None else ""
    print(
        f"Ready{profile_tag}. Hold [{hotkey_name}] and speak; "
        "release to insert text. Ctrl-C to quit."
    )

    if app is not None:
        # AppKit run loop owns the main thread; Ctrl-C terminates via the
        # signal handler (the overlay's heartbeat timer lets it fire).
        def _sigint(signum, frame):
            print("\nShutting down.")
            engine.shutdown()
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
            engine.shutdown()
