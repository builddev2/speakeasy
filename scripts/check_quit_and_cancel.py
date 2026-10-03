"""Real-model check of quit and Discard during speaker identification.

Runs the production meeting pipeline (real Transcriber, real
ChildDiarizationRunner and sherpa-onnx models) on a synthetic spoken meeting
made with macOS `say`, in a temporary HOME. Never touches live capture or the
real ~/Library/Application Support/Speakeasy.

Three runs on the same audio:
  keep     no cancel: processing completes and the meeting is saved
           (the pill's Keep button makes no backend call, so this is its
           backend outcome)
  discard  the pill's Discard path is run while the
           diarization child is working: nothing is saved
  quit     in a separate process, Engine.shutdown() (the body every quit
           path runs) is called while the diarization child is working, and
           the process then exits at once, as NSApplication.terminate_ does:
           the child must already be dead when shutdown() returns

Usage: .venv/bin/python scripts/check_quit_and_cancel.py [--minutes 5]
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

LINES = [
    "The Moon is about three hundred and eighty four thousand kilometres away.",
    "Light from the Sun takes a little over eight minutes to reach the Earth.",
    "More than one thousand Earths could fit inside Jupiter.",
    "A day on Venus is longer than its year.",
    "Mars has the tallest volcano in the solar system, Olympus Mons.",
    "A teaspoon of neutron star would weigh billions of tonnes.",
]
VOICES = ["Daniel", "Samantha", "Karen"]


def make_meeting(folder: Path, minutes: float) -> Path:
    """Alternating voices, 16 kHz mono int16, looped to the requested length."""
    folder.mkdir(exist_ok=True)
    import wave
    clips = []
    for i, (voice, line) in enumerate(zip(VOICES * 2, LINES)):
        path = folder / f"clip{i}.wav"
        subprocess.run(["say", "-v", voice, "-o", str(path), "--file-format=WAVE",
                        "--data-format=LEI16@16000", line], check=True)
        with wave.open(str(path)) as clip:
            assert (clip.getnchannels(), clip.getsampwidth(), clip.getframerate()) == (1, 2, 16000)
            clips.append(clip.readframes(clip.getnframes()) + b"\0\0" * 8000)
    block = b"".join(clips)
    target = int(minutes * 60 * 16000 * 2)
    source = folder / "source.wav"
    with wave.open(str(source), "wb") as out:
        out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        out.writeframes((block * (target // len(block) + 1))[:target])
    return source


def build_engine(root: Path):
    import threading as _threading
    from speakeasy.engine import DictationEngine, MeetingOptions
    from speakeasy.diarization_process import ChildDiarizationRunner
    from speakeasy.meeting_library import MeetingLibrary
    from speakeasy.engine import State
    from speakeasy.transcriber import Transcriber

    engine = object.__new__(DictationEngine)
    engine.transcriber = Transcriber()
    engine.diarization_runner = ChildDiarizationRunner()
    engine.library = MeetingLibrary()
    engine._meeting_options = MeetingOptions()
    engine._meeting_cancel = _threading.Event()
    engine.profile = None
    engine._user_paused = True
    engine.meeting_processing_error = None
    engine.meeting_event = None
    engine._meeting_active = True
    engine.state = State.MEETING_PROCESSING
    engine.progress = []
    engine.on_meeting_progress = engine.progress.append
    engine.on_meeting_saved = lambda _: None
    engine._set_state = lambda state: setattr(engine, "state", state)
    engine._idle_state = lambda: State.READY
    # shutdown() also stops dictation, the mic and the executors; none of
    # those exist in this headless run, so they are inert stand-ins.
    inert = SimpleNamespace(stop=lambda: None, shutdown=lambda **_: None,
                            force_close=lambda: None, resume=lambda: None,
                            discard=lambda: None)
    engine._shutting_down = False
    engine._diagnostic_cancel = None
    engine._clear_last_dictation = lambda: None
    engine._cancel_dictation_stream = lambda: None
    engine._listener = inert
    engine.recorder = inert
    engine.worker = inert
    engine.control = inert
    engine.meeting_recorder = inert
    engine._meeting_asr_session = None
    return engine


def cancel_handler(engine):
    """The real pill Discard path, bound to engine."""
    from speakeasy.ui import pill_controller
    pill_controller._make_panel = lambda target: SimpleNamespace(
        render=lambda v: None, show=lambda o: None, hide=lambda: None,
        frame_origin=lambda: None, set_drawer=lambda rows: None,
        is_drawer_visible=lambda: False, event_menu=lambda items: None)
    pill = pill_controller.PillController.alloc().initWithEngine_opener_(engine, lambda view: None)

    def handler(params, respond):
        pill.machine.phase = "confirm_discard"
        pill.pillDiscard_(None)
        respond(True)
    return handler


def alive(pid: int) -> bool:
    out = subprocess.run(["ps", "-o", "pid=", "-p", str(pid)], capture_output=True, text=True)
    return bool(out.stdout.strip())


def run(mode: str, source: Path, root: Path, quit_delay: float = 0.0) -> dict:
    import shutil
    from concurrent.futures import Future
    from speakeasy.meeting_recorder import MeetingRecording
    from speakeasy.meeting_stream import MeetingASRResult, MeetingASRStatus
    from speakeasy.meeting_benchmark import MeetingTiming

    engine = build_engine(root)
    before = engine.library.count_meetings()
    spool = root / f"{mode}-mic.wav"
    shutil.copy(source, spool)
    # Like a real stop: the live mic transcript is already done, so the
    # pipeline goes almost straight to speaker identification.
    future = Future()
    future.set_result(MeetingASRResult(
        MeetingASRStatus.COMPLETE, transcript=engine.transcriber.transcribe_long_wav(spool)))
    timing = MeetingTiming()
    timing.start("stop")
    timing.finish("stop")
    records = []
    timing.emit = lambda status: records.append(timing.record(status))
    recording = MeetingRecording(mic_path=spool, capture_mode="mic_only")
    handler = cancel_handler(engine) if mode == "discard" else None

    started = time.monotonic()
    worker = threading.Thread(target=engine._process_meeting, args=(recording, timing, future))
    worker.start()
    result = {"mode": mode}
    if mode != "keep":
        runner = engine.diarization_runner
        while worker.is_alive():
            process = runner._active
            if process is not None and process.is_alive() and any(
                    p.startswith("Identifying speakers") for p in engine.progress):
                break
            time.sleep(0.02)
        else:
            raise SystemExit(f"{mode}: processing ended before speaker identification")
        # With this audio the child reports progress every ~1.3 s, so its
        # parent watchdog would also end it soon after a hard exit. The quit
        # check therefore asserts what only terminate_active() provides: the
        # child is already dead when shutdown() returns.
        time.sleep(quit_delay if mode == "quit" else 2.0)
        pid = process.pid
        result["child_pid"] = pid
        result["progress_at_action"] = engine.progress[-1]
        t0 = time.monotonic()
        if mode == "discard":
            responses = []
            handler({}, lambda *a, **k: responses.append(a))
            result["handler_response"] = responses
        else:
            # Quit: shutdown() and then a hard exit, like every quit path in
            # the app. The worker thread never runs again.
            print(f"CHILD {pid} {result['progress_at_action']}", flush=True)
            engine.shutdown()
            took = time.monotonic() - t0
            print(f"SHUTDOWN {took:.3f} {'alive' if alive(pid) else 'dead'}", flush=True)
            print("EXIT", flush=True)
            os._exit(0)
        while alive(pid) and time.monotonic() - t0 < 120:
            time.sleep(0.02)
        result["child_gone_s"] = round(time.monotonic() - t0, 3)
        worker.join(120)
        result["worker_done_s"] = round(time.monotonic() - t0, 3)
    else:
        worker.join()
    result["worker_alive"] = worker.is_alive()
    result["total_s"] = round(time.monotonic() - started, 1)
    result["status"] = records[0]["status"] if records else None
    result["meetings_saved"] = engine.library.count_meetings() - before
    result["final_state"] = engine.state.name
    result["spool_deleted"] = not spool.exists()
    result["last_progress"] = engine.progress[-1] if engine.progress else None
    return result


def quit_in_subprocess(source: Path, root: Path, delay: float) -> dict:
    """Run the quit mode in its own process and time the child's death."""
    from speakeasy.meeting_library import MeetingLibrary
    before = MeetingLibrary().count_meetings()
    app = subprocess.Popen([sys.executable, __file__, "--quit-run", str(source), str(root),
                            "--quit-delay", str(delay)],
                           stdout=subprocess.PIPE, text=True)
    pid = None
    for line in app.stdout:
        if line.startswith("CHILD "):
            pid = int(line.split()[1])
            progress = line.split(None, 2)[2].strip()
        if line.startswith("SHUTDOWN "):
            _, took, state = line.split()
        if line.startswith("EXIT"):
            break
    t0 = time.monotonic()
    assert pid is not None, "quit run never reached speaker identification"
    while alive(pid) and time.monotonic() - t0 < 120:
        time.sleep(0.02)
    result = {"mode": "quit", "child_pid": pid, "progress_at_action": progress,
              "shutdown_s": float(took), "child_at_shutdown_return": state,
              "child_gone_s": round(time.monotonic() - t0, 3)}
    app.wait(30)
    result["app_exit_code"] = app.returncode
    result["meetings_saved"] = MeetingLibrary().count_meetings() - before
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--minutes", type=float, default=5)
    parser.add_argument("--quit-delay", type=float, default=2.0,
                        help="seconds into speaker identification to quit")
    parser.add_argument("--quit-run", nargs=2, type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--modes", nargs="+", default=["keep", "discard", "quit"])
    args = parser.parse_args()
    if args.quit_run:  # the "app" process of the quit mode; env already set
        run("quit", *args.quit_run, quit_delay=args.quit_delay)
        raise SystemExit("quit run returned instead of exiting")
    with tempfile.TemporaryDirectory(prefix="speakeasy-quit-check-") as folder:
        root = Path(folder)
        real_support = Path.home() / "Library" / "Application Support" / "Speakeasy"
        # The ASR model cache stays where it is (read only); app data moves.
        os.environ.setdefault("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
        os.environ["HOME"] = str(root / "home")
        os.environ["HF_HUB_OFFLINE"] = "1"
        from speakeasy import settings
        support = settings.app_support_dir()
        assert support.is_relative_to(root) and support != real_support, support
        print(f"temp HOME: {os.environ['HOME']}", flush=True)
        source = make_meeting(root, args.minutes)
        print(f"synthetic meeting: {args.minutes} min, voices {', '.join(VOICES)}", flush=True)
        ok = True
        for mode in args.modes:
            if mode == "quit":
                result = quit_in_subprocess(source, root, args.quit_delay)
                print(result, flush=True)
                checks = [result["child_at_shutdown_return"] == "dead", result["shutdown_s"] <= 1.5,
                          result["child_gone_s"] <= 1.5, result["app_exit_code"] == 0,
                          result["meetings_saved"] == 0]
            else:
                result = run(mode, source, root)
                print(result, flush=True)
                checks = [result["status"] == ("success" if mode == "keep" else "cancelled"),
                          result["meetings_saved"] == (1 if mode == "keep" else 0),
                          result["final_state"] == "READY",
                          result["spool_deleted"],
                          not result["worker_alive"]]
            ok &= all(checks)
            print(f"  {mode}: {'PASS' if all(checks) else 'FAIL'}", flush=True)
        print("ALL PASS" if ok else "SOME FAILED")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
