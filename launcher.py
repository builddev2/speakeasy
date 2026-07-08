"""PyInstaller entry point for the standalone Speakeasy.app.

A windowed (LSUIElement) app launched by the PyInstaller bootloader has no
console attached, so sys.stdout / sys.stderr are None. Any real write past
that — a traceback, argparse --help/error — raises and kills the process
(exit 255) with nothing shown. Before importing speakeasy, point both
streams at a log file so failures are recoverable instead of silent.
"""

import sys


def _redirect_streams_to_log():
    if sys.stdout is not None and sys.stderr is not None:
        return
    import os

    log_dir = os.path.expanduser("~/Library/Logs")
    os.makedirs(log_dir, exist_ok=True)
    log = open(
        os.path.join(log_dir, "Speakeasy.log"),
        "a",
        buffering=1,
        encoding="utf-8",
    )
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


_redirect_streams_to_log()

from speakeasy.__main__ import main

if __name__ == "__main__":
    main()
