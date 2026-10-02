import os
import threading

from speakeasy import call_detect


def _watch(answers, own=frozenset({10})):
    seq = iter(answers)
    return call_detect.MicWatch(input_ids=lambda: next(seq), own_ids=lambda: set(own))


def test_other_app_using_mic_ignores_speakeasy_processes():
    w = _watch([set(), {10, 11}, {10, 12}], own={10, 11})
    assert [w(), w(), w()] == [False, False, True]


def test_apps_already_using_the_mic_are_ignored_until_they_release_it():
    w = _watch([{20}, {20}, {20, 30}, {20}, set(), {20}])
    assert [w(), w(), w(), w(), w(), w()] == [False, False, True, False, False, True]


def test_unknown_answers_pass_through_and_do_not_set_the_baseline():
    w = _watch([None, {20}, {20, 30}])
    assert [w(), w(), w()] == [None, False, True]


def test_waved_off_apps_are_ignored_until_they_release_the_mic():
    w = _watch([set(), {30}, {30}, {30, 40}, set(), {30}])
    assert [w(), w()] == [False, True]
    w.ignore_current()
    assert [w(), w(), w(), w()] == [False, True, False, True]


def test_probe_ignore_current_reaches_its_watch():
    p = call_detect.CallProbe(on_result=lambda v: None, wanted=lambda: False)
    assert isinstance(p.watch, call_detect.MicWatch)
    p.ignore_current()
    assert p.watch._ignore_requested
    q = call_detect.CallProbe(on_result=lambda v: None, wanted=lambda: False,
                              probe=lambda: True)
    assert q.watch is None
    q.ignore_current()        # no watch: nothing to do, no error


def test_own_process_ids_include_children(monkeypatch):
    result = type("R", (), {"stdout": "201\n202\nnot-a-pid\n"})()
    seen = []

    def run(args, **kwargs):
        seen.append(args)
        return result

    monkeypatch.setattr(call_detect.subprocess, "run", run)
    assert call_detect.own_process_ids() == {os.getpid(), 201, 202}
    assert seen[0] == ["/usr/bin/pgrep", "-P", str(os.getpid())]


def test_own_process_ids_survive_pgrep_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("no pgrep")

    monkeypatch.setattr(call_detect.subprocess, "run", boom)
    assert call_detect.own_process_ids() == {os.getpid()}


def test_probe_reports_only_while_wanted_and_stops():
    wanted = threading.Event()
    got = threading.Event()
    calls, results = [], []

    def probe():
        calls.append(1)
        return True

    def on_result(value):
        results.append(value)
        got.set()

    p = call_detect.CallProbe(on_result=on_result, wanted=wanted.is_set,
                              probe=probe, interval=0.01)
    p.start()
    assert not got.wait(0.1)
    assert calls == []
    wanted.set()
    assert got.wait(1.0)
    assert results[0] is True
    p.stop()
    p._thread.join(1.0)
    assert not p._thread.is_alive()


def test_probe_never_reports_unknown():
    calls = []

    def probe():
        calls.append(1)
        return None

    results = []
    p = call_detect.CallProbe(on_result=results.append, wanted=lambda: True,
                              probe=probe, interval=0.01)
    p.start()
    threading.Event().wait(0.1)
    p.stop()
    p._thread.join(1.0)
    assert calls and results == []


def test_poll_interval_is_five_seconds():
    assert call_detect.POLL_SECONDS == 5.0
    p = call_detect.CallProbe(on_result=lambda v: None, wanted=lambda: True)
    assert p._interval == 5.0
