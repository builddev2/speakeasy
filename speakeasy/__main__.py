"""Speakeasy: hold Right Command, speak, release — text appears at your cursor."""

import argparse


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
    args = parser.parse_args()

    # The menu bar app is the default; --train is a terminal experience until
    # the native training window lands, and it implies --cli.
    if args.cli or args.train:
        from .cli import run

        run(args.profile, args.train)
    else:
        from .cli import run  # TODO(menubar): switch default to ui.menubar.run_app

        run(args.profile, False)


if __name__ == "__main__":
    main()
