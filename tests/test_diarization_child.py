"""ChildDiarizationRunner with real spawned children running stub targets."""

import threading
import time

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
