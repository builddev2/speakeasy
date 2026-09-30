"""ChildDiarizationRunner with real spawned children running stub targets."""

import multiprocessing
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

import diarization_child_stubs as stubs
from speakeasy import meetings
from speakeasy.diarization_process import ChildDiarizationRunner, DiarizationFailed
from speakeasy.transcriber import MeetingCancelled


def _call(runner, *, cancel=None, expected_count=None, progress=None, on_phase=None):
    return runner(
        "/nonexistent/track.wav",
        expected_count=expected_count, max_speakers=None, voice_profile_names=(),
        progress=progress or (lambda f: None),
        cancel=cancel or threading.Event(),
        on_phase=on_phase or (lambda phase: None),
    )


def test_turns_progress_and_phase_arrive():
    progress, phases = [], []
    turns = _call(ChildDiarizationRunner(stubs.stub_turns), progress=progress.append,
                  on_phase=phases.append)
    assert turns == [meetings.DiarizationTurn(0.0, 1.0, 3, 0.9, True, "Alice"),
                     meetings.DiarizationTurn(1.0, 2.0, 7)]
    assert progress == [0.5]
    assert phases == ["voice_identification"]


def test_each_call_gets_a_fresh_child():
    runner = ChildDiarizationRunner(stubs.stub_turns)
    assert _call(runner, expected_count=2)[1].speaker == 2
    assert _call(runner, expected_count=None)[1].speaker == 7


def test_launch_timeout():
    runner = ChildDiarizationRunner(stubs.stub_never_ready, launch_timeout=1.0)
    started = time.monotonic()
    with pytest.raises(DiarizationFailed) as failure:
        _call(runner)
    assert failure.value.reason == "launch_timeout"
    assert time.monotonic() - started < 5.0


def test_child_exit_after_progress_is_child_exited():
    progress = []
    with pytest.raises(DiarizationFailed) as failure:
        _call(ChildDiarizationRunner(stubs.stub_exit_after_progress),
              progress=progress.append)
    assert failure.value.reason == "child_exited"
    assert progress == [0.25]


def test_child_error_sends_only_type_name(capsys):
    with pytest.raises(DiarizationFailed) as failure:
        _call(ChildDiarizationRunner(stubs.stub_error_with_secret))
    assert failure.value.reason == "child_error:ValueError"
    assert "secret" not in str(failure.value)
    captured = capsys.readouterr()
    assert "secret" not in captured.out + captured.err


def test_cancel_while_running_is_prompt():
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(MeetingCancelled):
        _call(ChildDiarizationRunner(stubs.stub_slow), cancel=cancel)
    assert time.monotonic() - started < 3.0


def test_cancel_before_ready_is_prompt():
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(MeetingCancelled):
        _call(ChildDiarizationRunner(stubs.stub_never_ready), cancel=cancel)
    assert time.monotonic() - started < 3.0


def test_large_turn_list_arrives_intact():
    turns = _call(ChildDiarizationRunner(stubs.stub_many_turns))
    assert len(turns) == 20_000
    assert turns[-1] == meetings.DiarizationTurn(19_999 * 0.5, 19_999 * 0.5 + 0.4, 19_999 % 6)


def test_no_child_left_running():
    import multiprocessing
    _call(ChildDiarizationRunner(stubs.stub_turns))
    cancel = threading.Event(); cancel.set()
    with pytest.raises(MeetingCancelled):
        _call(ChildDiarizationRunner(stubs.stub_slow), cancel=cancel)
    assert [p for p in multiprocessing.active_children()
            if p.name == "speakeasy-diarization"] == []


def test_launch_deadline_stops_after_ready():
    runner = ChildDiarizationRunner(stubs.stub_ready_then_slow, launch_timeout=1.0)
    turns = _call(runner)
    assert turns == [meetings.DiarizationTurn(0.0, 1.0, 1)]


def test_progress_is_throttled():
    progress = []
    assert _call(ChildDiarizationRunner(stubs.stub_many_progress),
                 progress=progress.append) == []
    assert 2 <= len(progress) <= 101
    assert progress[-1] == 1.0


def test_child_exits_when_parent_dies_abruptly(tmp_path):
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "parent.py"
    script.write_text(textwrap.dedent(f"""
        import os, threading, time
        import diarization_child_stubs as stubs
        from speakeasy.diarization_process import ChildDiarizationRunner

        if __name__ == "__main__":
            runner = ChildDiarizationRunner(stubs.stub_record_pid_then_slow)
            threading.Thread(target=lambda: runner(
                {str(pid_file)!r}, expected_count=None, max_speakers=None,
                voice_profile_names=(), progress=lambda f: None,
                cancel=threading.Event()), daemon=True).start()
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if os.path.exists({str(pid_file)!r}) and os.path.getsize({str(pid_file)!r}):
                    break
                time.sleep(0.05)
            os._exit(0)  # hard exit, like NSApp.terminate_: no cleanup runs
    """))
    tests_dir = Path(__file__).resolve().parent
    root = tests_dir.parent
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(root), str(tests_dir)]))
    parent = subprocess.Popen([sys.executable, str(script)], env=env)
    child_pid = None
    try:
        parent.wait(timeout=30)
        child_pid = int(pid_file.read_text())
        gone = False
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                gone = True
                break
            time.sleep(0.1)
        assert gone, "child outlived its parent"
    finally:
        if parent.poll() is None:
            parent.kill()
        if child_pid is not None:
            try:
                os.kill(child_pid, 9)
            except ProcessLookupError:
                pass


def _start_blocking_run(runner, cancel):
    """Run the runner on a thread against the block-forever stub; wait until ready."""
    outcome = []

    def run():
        try:
            _call(runner, cancel=cancel)
            outcome.append(None)
        except BaseException as error:  # noqa: BLE001 - recorded for assertions
            outcome.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        process = runner._active
        if process is not None and process.is_alive():
            break
        time.sleep(0.02)
    else:
        raise AssertionError("child never became active")
    time.sleep(1.0)  # let the child import and send ready
    return thread, process, outcome


def test_terminate_active_kills_child_and_cancelled_run_raises_cancelled():
    runner = ChildDiarizationRunner(stubs.stub_ready_then_block)
    cancel = threading.Event()
    thread, process, outcome = _start_blocking_run(runner, cancel)
    cancel.set()
    started = time.monotonic()
    runner.terminate_active()
    assert not process.is_alive()
    assert time.monotonic() - started < 1.0
    thread.join(3.0)
    assert not thread.is_alive()
    assert isinstance(outcome[0], MeetingCancelled)


def test_terminate_active_without_a_run_is_a_noop():
    ChildDiarizationRunner(stubs.stub_turns).terminate_active()


def test_terminate_active_twice_does_not_raise():
    runner = ChildDiarizationRunner(stubs.stub_ready_then_block)
    cancel = threading.Event()
    thread, process, outcome = _start_blocking_run(runner, cancel)
    cancel.set()
    runner.terminate_active()
    runner.terminate_active()
    thread.join(3.0)
    assert not thread.is_alive()
    assert isinstance(outcome[0], MeetingCancelled)


def test_terminate_active_races_with_reap():
    runner = ChildDiarizationRunner(stubs.stub_ready_then_block)
    cancel = threading.Event()
    thread, process, outcome = _start_blocking_run(runner, cancel)
    errors = []

    def hammer():
        try:
            while thread.is_alive():
                runner.terminate_active(grace=0.2)
                time.sleep(0.005)
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    # Cancel first: the runner leaves through the top-of-loop check and runs
    # _reap while the hammer thread is calling terminate_active.
    cancel.set()
    killer = threading.Thread(target=hammer, daemon=True)
    killer.start()
    thread.join(5.0)
    killer.join(5.0)
    assert not thread.is_alive() and not killer.is_alive()
    assert not errors
    assert isinstance(outcome[0], MeetingCancelled)


def test_killed_child_without_cancel_is_still_child_exited():
    runner = ChildDiarizationRunner(stubs.stub_ready_then_block)
    thread, process, outcome = _start_blocking_run(runner, threading.Event())
    runner.terminate_active()
    thread.join(5.0)
    assert not thread.is_alive()
    assert isinstance(outcome[0], DiarizationFailed)
    assert outcome[0].reason == "child_exited"


def _dead_child_unreadable_pipe(monkeypatch, cancel, *, set_cancel):
    """Make the 'child dead and nothing to read' branch reachable: the pipe
    looks empty (a real dead child's pipe reports EOF as readable)."""
    import multiprocessing

    real = multiprocessing.get_context("spawn")
    made = []

    class Receiver:
        def __init__(self, inner):
            self._inner = inner

        def poll(self, timeout=0.0):
            made[0].join(10.0)  # the child has exited by the time we answer
            if set_cancel:
                cancel.set()
            return False

        def recv(self):
            return self._inner.recv()

        def close(self):
            self._inner.close()

        @property
        def closed(self):
            return self._inner.closed

    class Context:
        def Pipe(self, duplex=True):
            receiver, sender = real.Pipe(duplex=duplex)
            return Receiver(receiver), sender

        def Process(self, *args, **kwargs):
            process = real.Process(*args, **kwargs)
            made.append(process)
            return process

    monkeypatch.setattr(multiprocessing, "get_context", lambda method=None: Context())


def test_dead_child_with_empty_pipe_and_cancel_raises_cancelled(monkeypatch):
    cancel = threading.Event()
    _dead_child_unreadable_pipe(monkeypatch, cancel, set_cancel=True)
    runner = ChildDiarizationRunner(stubs.stub_exit_after_progress, launch_timeout=60.0)
    with pytest.raises(MeetingCancelled):
        _call(runner, cancel=cancel)


def test_dead_child_with_empty_pipe_without_cancel_is_child_exited(monkeypatch):
    cancel = threading.Event()
    _dead_child_unreadable_pipe(monkeypatch, cancel, set_cancel=False)
    runner = ChildDiarizationRunner(stubs.stub_exit_after_progress, launch_timeout=60.0)
    with pytest.raises(DiarizationFailed) as failure:
        _call(runner, cancel=cancel)
    assert failure.value.reason == "child_exited"


def test_cancel_already_set_never_starts_a_child(monkeypatch):
    starts = []
    real_get_context = multiprocessing.get_context

    class _Ctx:
        def __init__(self, inner):
            self._inner = inner

        def Pipe(self, *args, **kwargs):
            return self._inner.Pipe(*args, **kwargs)

        def Process(self, *args, **kwargs):
            process = self._inner.Process(*args, **kwargs)
            process.start = lambda: starts.append(True)
            return process

    monkeypatch.setattr(
        multiprocessing, "get_context", lambda method=None: _Ctx(real_get_context(method))
    )
    runner = ChildDiarizationRunner(stubs.stub_turns)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(MeetingCancelled):
        _call(runner, cancel=cancel)
    assert starts == []
    assert runner._active is None
