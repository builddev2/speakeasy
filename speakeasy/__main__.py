"""Speakeasy: hold Right Command, speak, release — text appears at your cursor."""

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(prog="speakeasy", description=__doc__)
    parser.add_argument(
        "--cli",
        action="store_true",
        help="run the terminal flow instead of the menu bar app",
    )
    parser.add_argument(
        "--profile", metavar="NAME", help="use this profile (skips the picker)"
    )
    parser.add_argument(
        "--train",
        action="store_true",
        help="run a guided training session for a profile, then dictate (terminal)",
    )
    parser.add_argument(
        "--enroll-voice", nargs=2, metavar=("NAME", "WAV"),
        help="store a local speaker embedding from a 16 kHz mono WAV",
    )
    parser.add_argument("--delete-voice", metavar="NAME")
    parser.add_argument("--list-voices", action="store_true")
    parser.add_argument(
        "--import-vocabulary", nargs=2, metavar=("PROFILE", "FILE"),
        help="add one vocabulary term per line to a profile",
    )
    args = parser.parse_args()

    if args.enroll_voice or args.delete_voice or args.list_voices:
        from .voice_profiles import VoiceProfileStore

        store = VoiceProfileStore()
        if args.enroll_voice:
            from .transcriber import read_wav_mono_f32

            name, wav = args.enroll_voice
            store.enroll(name, read_wav_mono_f32(Path(wav)))
            print(f"Enrolled local voice profile: {name}")
        elif args.delete_voice:
            store.delete(args.delete_voice)
            print(f"Deleted local voice profile: {args.delete_voice}")
        else:
            print("\n".join(store.names()))
        return
    if args.import_vocabulary:
        from .profiles import Profile

        name, source = args.import_vocabulary
        profile = Profile.load(name)
        added = profile.import_vocabulary(Path(source).read_text(encoding="utf-8").splitlines())
        print(f"Added {added} vocabulary term(s) to {name}.")
        return

    # The menu bar app is the default; --train is a terminal experience until
    # the native training window lands, and it implies --cli.
    if args.cli or args.train:
        from .cli import run

        run(args.profile, args.train)
    else:
        from .ui.menubar import run_app

        run_app(args.profile)


if __name__ == "__main__":
    main()
