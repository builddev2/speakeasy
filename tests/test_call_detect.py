import os
import threading

from speakeasy import call_detect


def test_other_app_using_mic_ignores_speakeasy_processes():
    assert call_detect.other_app_using_mic(
        input_ids=lambda: {10, 11}, own_ids=lambda: {10, 11}) is False
    assert call_detect.other_app_using_mic(
        input_ids=lambda: {10, 12}, own_ids=lambda: {10, 11}) is True
    assert call_detect.other_app_using_mic(
        input_ids=lambda: set(), own_ids=lambda: {10}) is False
    assert call_detect.other_app_using_mic(
        input_ids=lambda: None, own_ids=lambda: {10}) is None


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
