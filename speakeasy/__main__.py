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
    parser.add_argument(
        "--dictation-latency-summary",
        action="store_true",
        help="summarize the local privacy-safe dictation timing log",
    )
    parser.add_argument(
        "--dictation-latency-compare",
        nargs=2,
        metavar=("BATCH_JSONL", "STREAMING_JSONL"),
        help="compare separate privacy-safe batch and streaming timing logs",
    )
    parser.add_argument(
        "--dictation-latency-log",
        metavar="JSONL",
        help="write this run's privacy-safe dictation timing to a separate log",
    )
    parser.add_argument(
        "--dictation-batch-mode",
        action="store_true",
        help="use authoritative batch dictation (currently the safe default)",
    )
    parser.add_argument("--dictation-diagnostic", metavar="REPORT_JSON",
                        help="consented one-shot microphone comparison; requires a terminal")
    parser.add_argument("--diagnostic-corpus", metavar="CORPUS_JSON")
    parser.add_argument("--diagnostic-sounds", action="store_true")
    args = parser.parse_args()

    from .dictation_diagnostic import sweep_temporary_audio

    sweep_temporary_audio()

    if args.dictation_diagnostic:
        import sys
        from .dictation_diagnostic import main as diagnostic_main

        if not sys.stdin or not sys.stdin.isatty() or not sys.stdout.isatty():
            parser.error("microphone diagnostics require an interactive terminal")
        diagnostic_args = ["--output", args.dictation_diagnostic]
        if args.diagnostic_corpus:
            diagnostic_args += ["--corpus", args.diagnostic_corpus]
        if args.diagnostic_sounds:
            diagnostic_args += ["--sounds"]
        diagnostic_main(diagnostic_args)
        return

    if args.dictation_latency_log:
        from .dictation_benchmark import set_latency_log_path

        set_latency_log_path(Path(args.dictation_latency_log).expanduser())
    if args.dictation_latency_compare:
        from .dictation_benchmark import print_comparative_summary

        batch, streaming = args.dictation_latency_compare
        print_comparative_summary(Path(batch), Path(streaming))
        return
    if args.dictation_latency_summary:
        from .dictation_benchmark import print_latency_summary

        print_latency_summary()
        return
    if args.dictation_batch_mode:
        from . import config

        config.DICTATION_STREAMING_ENABLED = False
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
