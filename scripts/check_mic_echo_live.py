"""Live speaker-bleed calibration check for dual-track meetings.

Runs the production pipeline (real Engine, MeetingCaptureRecorder with the
real mic and the system-audio helper, real Transcriber and diarization child)
in a temporary HOME. The "remote" voice is a pre-rendered WAV played by
`afplay`, whose PID is the meeting's selected system-audio process; the
user's "own" voice is `say` at timed moments, a different process, so it
reaches the mic through the speakers but not the system track. Speaker bleed
of the remote voice therefore appears on the mic and should be removed, while
the own utterances should survive as "You".

Own utterances: two sentences in gaps, a deliberate repeat of the last four
words of a remote sentence, a talk-over during a remote sentence, and a short
"okay yeah". Pass --no-own to play the remote voice alone.

Uses the speakers and microphone; do not change volume or output device.
Usage: .venv/bin/python scripts/check_mic_echo_live.py [--no-own]
"""
import argparse
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Temp HOME before any speakeasy import; the ASR model cache stays readable.
_REAL_HOME = Path.home()
os.environ.setdefault("HF_HOME", str(_REAL_HOME / ".cache" / "huggingface"))
_ROOT = Path(tempfile.mkdtemp(prefix="speakeasy-echo-live-"))
os.environ["HOME"] = str(_ROOT / "home")
os.environ["HF_HUB_OFFLINE"] = "1"
assert Path.home() == _ROOT / "home" and Path.home().is_relative_to(_ROOT)

REMOTE_VOICE, OWN_VOICE = "Daniel", "Samantha"
LEAD_SECONDS = 3.0
REMOTE = [
    "The Moon is about three hundred and eighty four thousand kilometres away.",
    "Light from the Sun takes a little over eight minutes to reach the Earth.",
    "More than one thousand Earths could fit inside the planet Jupiter.",
    "A day on Venus is longer than a whole year on Venus.",
    "Neptune takes about one hundred sixty five years to orbit the Sun.",
    "A teaspoon of neutron star would weigh billions of tonnes.",
    "Saturn is the least dense planet and would float in a big enough bath.",
    "The nearest star beyond the Sun is more than four light years away.",
]
GAPS = [6.0, 5.0, 6.0, 5.0, 5.0, 5.0, 5.0, 12.0]  # silence after each sentence;
# the last is long so the meeting ends while afplay still plays (an exited
# selected process would end the system capture and force the mic-only fallback)
TAIL_SECONDS = 4.0
OWN1 = "My favourite breakfast is toast with butter and strawberry jam."
OWN2 = "Remember to buy more apples and oranges on the way home tonight."
TALK_OVER = "Actually I think we should reschedule the garden party for Friday."
SHORT = "okay yeah"
REPEAT_WORDS = 4


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def render(path: Path, voice: str, text: str) -> float:
    subprocess.run(["say", "-v", voice, "-o", str(path), "--file-format=WAVE",
                    "--data-format=LEI16@16000", text], check=True)
    with wave.open(str(path)) as clip:
        assert (clip.getnchannels(), clip.getsampwidth(), clip.getframerate()) == (1, 2, 16000)
        return clip.getnframes() / 16000


def build_remote(folder: Path):
    """One WAV: lead silence, then each sentence + gap. Returns the path and
    each sentence's (start, end) in seconds from the file start."""
    spans, blocks, cursor = [], [b"\0\0" * int(LEAD_SECONDS * 16000)], LEAD_SECONDS
    for index, (text, gap) in enumerate(zip(REMOTE, GAPS)):
        clip = folder / f"remote{index}.wav"
        duration = render(clip, REMOTE_VOICE, text)
        with wave.open(str(clip)) as handle:
            blocks.append(handle.readframes(handle.getnframes()))
        blocks.append(b"\0\0" * int(gap * 16000))
        spans.append((cursor, cursor + duration))
        cursor += duration + gap
    path = folder / "remote.wav"
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        out.writeframes(b"".join(blocks))
    return path, spans, cursor


def own_plan(spans):
    """(file-time seconds, label, text) for each own utterance."""
    last_words = " ".join(REMOTE[4].rstrip(".").split()[-REPEAT_WORDS:])
    return [
        (spans[0][1] + 1.0, "own1", OWN1),
        (spans[2][1] + 1.0, "own2", OWN2),
        (spans[3][1] + 2.0, "short", SHORT),
        (spans[4][1] + 0.7, "repeat", last_words),
        (spans[6][0] + 1.5, "talkover", TALK_OVER),
    ]


class Tee(io.TextIOBase):
    def __init__(self, real):
        self.real, self.buffer_text, self.lock = real, [], threading.Lock()

    def write(self, text):
        with self.lock:
            self.buffer_text.append(text)
        return self.real.write(text)

    def flush(self):
        self.real.flush()

    def captured(self):
        with self.lock:
            return "".join(self.buffer_text)


def build_engine():
    from types import SimpleNamespace
    from speakeasy import engine as engine_module
    from speakeasy.engine import DictationEngine, State

    engine_module.play_sound = lambda sound: None    # no chimes into the mic
    engine = DictationEngine()
    inert = SimpleNamespace(stop=lambda: None, shutdown=lambda **_: None,
                            force_close=lambda: None, resume=lambda: None,
                            start=lambda: None, discard=lambda: None,
                            pause=lambda: None, state="idle")
    engine._listener = inert
    engine.recorder = inert
    engine.on_meeting_progress = lambda text: None
    # Built on the worker thread, as in the app: MLX streams are per-thread.
    engine.worker.submit(engine._create_transcriber).result()
    assert engine.state is State.READY, engine.state
    return engine


def wait_for(predicate, timeout, what):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise SystemExit(f"timed out waiting for {what}")


def overlap(reference: list[str], candidate: list[str]) -> float:
    if not reference:
        return 0.0
    pool = list(candidate)
    hit = 0
    for word in reference:
        if word in pool:
            pool.remove(word)
            hit += 1
    return hit / len(reference)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-own", action="store_true", help="remote voice only")
    args = parser.parse_args()
    from speakeasy import settings
    support = settings.app_support_dir()
    real_support = _REAL_HOME / "Library" / "Application Support" / "Speakeasy"
    assert support.is_relative_to(_ROOT) and support != real_support, support
    print(f"temp HOME: {os.environ['HOME']}", flush=True)
    helper = Path(__file__).resolve().parent.parent / "build" / "native" / "SpeakeasySystemAudioCapture"
    if not helper.exists():
        raise SystemExit("system-audio helper missing: scripts/build_system_audio_helper.sh")

    remote_path, spans, remote_end = build_remote(_ROOT)
    plan = [] if args.no_own else own_plan(spans)
    from speakeasy.engine import MeetingOptions, State
    tee = Tee(sys.stdout)
    sys.stdout = tee
    try:
        engine = build_engine()
        player = subprocess.Popen(["afplay", str(remote_path)])
        file_t0 = time.monotonic()                       # afplay spawn ~ file time 0
        # The helper only accepts a PID that is already an audio process.
        from speakeasy import system_audio
        wait_for(lambda: player.pid in system_audio.eligible_process_ids(), 2.5,
                 "afplay to become an eligible audio process")
        engine.begin_meeting(MeetingOptions(system_audio_pid=player.pid))
        wait_for(lambda: engine.state is State.MEETING_RECORDING
                 or engine.meeting_start_error, 30, "meeting start")
        if engine.meeting_start_error:
            player.kill()
            raise SystemExit(f"BLOCKED: meeting start failed: {engine.meeting_start_error}")
        meeting_t0 = time.monotonic()
        shift = meeting_t0 - file_t0       # meeting time = file time - shift
        planned = [(t - shift, label, text) for t, label, text in plan]
        say_procs = []
        for at, label, text in sorted(planned):
            delay = meeting_t0 + at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            say_procs.append(subprocess.Popen(["say", "-v", OWN_VOICE, text]))
        remaining = file_t0 + spans[-1][1] + TAIL_SECONDS - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        assert player.poll() is None, "afplay ended before the meeting did"
        for proc in say_procs:
            proc.wait(30)
        engine.end_meeting()
        wait_for(lambda: engine.state is State.MEETING_PROCESSING, 60, "processing start")
        player.kill()
        wait_for(lambda: engine.state is State.READY, 900, "READY")
    finally:
        sys.stdout = tee.real
    log = tee.captured()
    echo_lines = [l for l in log.splitlines() if "echo candidate" in l or "mic echo" in l]
    print("\n=== planned (seconds from meeting start) ===")
    print(f"audio-file shift vs meeting start: {shift:.2f}s")
    for index, (start, end) in enumerate(spans):
        print(f"remote {index + 1}: {start - shift:6.1f} - {end - shift:6.1f}  {REMOTE[index]}")
    for at, label, text in sorted(planned):
        print(f"own {label}: starts {at:6.1f}  {text}")
    print("\n=== engine echo lines ===")
    print("\n".join(echo_lines) or "(none)")

    ids = sorted(engine.library.meeting_ids())
    assert len(ids) == 1, ids
    meeting = engine.library.get_meeting(ids[0])
    print("\n=== capture_health ===")
    print(meeting.capture_health)
    print("track_offsets_seconds:", meeting.track_offsets_seconds)
    print(f"capture_mode={meeting.capture_mode} scope={meeting.capture_scope}"
          f" system_audio_status={meeting.system_audio_status}")
    print("\n=== segments (start end speaker text) ===")
    for s in meeting.segments:
        print(f"{s.start:7.2f} {s.end:7.2f} {s.speaker}: {s.text}")

    you = [s for s in meeting.segments if s.speaker == "You"]
    print("\n=== remote sentences duplicated as You (word overlap >= 0.6) ===")
    duplicated = 0
    for index, text in enumerate(REMOTE):
        best = max((overlap(words(text), words(s.text)) for s in you), default=0.0)
        dup = best >= 0.6
        duplicated += dup
        print(f"remote {index + 1}: best You overlap {best:.2f} -> {'DUPLICATE' if dup else 'clean'}")
    print(f"remote sentences with a You duplicate: {duplicated} of {len(REMOTE)}")
    print("\n=== own utterances surviving as You (overlap >= 0.6, any You segment) ===")
    for at, label, text in sorted(planned):
        best = max((overlap(words(text), words(s.text)) for s in you), default=0.0)
        print(f"{label}: best You overlap {best:.2f} -> {'SURVIVED' if best >= 0.6 else 'LOST'}")
    print(f"\ntemp HOME kept until now: {_ROOT}")
    engine.shutdown()


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(_ROOT, ignore_errors=True)
        print("temp HOME deleted", flush=True)
