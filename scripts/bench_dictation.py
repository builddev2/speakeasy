#!/usr/bin/env python
"""Dev benchmark: dictation latency (trim on vs off) + fuzzy-snap preview.

Not a test — it loads the real Parakeet model, so run it by hand:

    .venv/bin/python scripts/bench_dictation.py path/to/wavs [profile_name]

`path/to/wavs` holds 16 kHz mono WAV clips (dev-local, uncommitted). Prints,
per clip: samples trimmed, transcribe latency with trimming enabled vs
disabled, and the transcript. If a profile name is given, also prints the
transcript after Profile.apply() so vocab snaps are visible.
"""

import sys
import time
from pathlib import Path

from speakeasy import config
from speakeasy.profiles import Profile
from speakeasy.transcriber import Transcriber, read_wav_mono_f32


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    wav_dir = Path(sys.argv[1])
    profile = Profile.load(sys.argv[2]) if len(sys.argv) > 2 else None

    t = Transcriber()  # warms the model
    for wav in sorted(wav_dir.glob("*.wav")):
        audio = read_wav_mono_f32(wav)
        for enabled in (True, False):
            config.DICTATION_TRIM_ENABLED = enabled
            started = time.perf_counter()
            text = t.transcribe(audio)
            elapsed = time.perf_counter() - started
            tag = "trim" if enabled else "raw "
            print(f"{wav.name:24s} [{tag}] {elapsed:5.2f}s  {text!r}")
            if enabled and profile is not None:
                print(f"{'':24s} [snap] {profile.apply(text)!r}")
    config.DICTATION_TRIM_ENABLED = True


if __name__ == "__main__":
    main()
