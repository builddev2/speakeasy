"""Button-driven sessions do not need hardware or another model executor."""

import json
import threading
from types import SimpleNamespace

from speakeasy import diagnostic_run
from speakeasy.diagnostic_content import PROMPTS
from speakeasy.dictation_diagnostic import summarize


def test_short_corpus_balances_30_actual_recordings():
    assert len(PROMPTS) == 30
    for name in ('short', 'medium', 'long'):
        assert sum(p['expected_duration_group'] == name for p in PROMPTS) == 10
    for name in ('quiet', 'moderate_noise'):
        assert sum(p['condition'] == name for p in PROMPTS) == 15
    assert sum(p['vocabulary'] == 'technical' for p in PROMPTS) == 15
    assert all(p['condition'] == 'quiet' for p in PROMPTS[:15])


def test_cancel_unblocks_prompt_without_recording():
    run = diagnostic_run.DiagnosticRun(None, lambda payload: ready.set())
    ready = threading.Event()
    stopped = threading.Event()

    def wait():
        try:
            run._wait_button('Reference')
        except InterruptedError:
            stopped.set()

    thread = threading.Thread(target=wait)
    thread.start()
    assert ready.wait(1)
    run.cancel()
    thread.join(1)
    assert stopped.is_set()


def test_recording_button_only_advances_waiting_phases():
    run = diagnostic_run.DiagnosticRun(None, lambda payload: None)
    run.advance()
    assert not run.next_button.is_set()
    run.phase = 'recording'
    run.advance()
    assert run.next_button.is_set()


def test_session_uses_existing_engine_resources_and_saves_private_report(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic_run.settings, 'app_support_dir', lambda: tmp_path)
    engine = SimpleNamespace(transcriber=object(), worker=object(), control=object(), recorder=object())
    updates = []
    run = diagnostic_run.DiagnosticRun(engine, updates.append)

    def take(transcriber, worker, control, reference, **kwargs):
        assert (transcriber, worker, control) == (engine.transcriber, engine.worker, engine.control)
        assert kwargs['recorder'] is engine.recorder
        mode = {'text': reference, 'wer': 0}
        group = PROMPTS[len(run.takes)]['expected_duration_group']
        return {'audio': {'seconds': {'short': 3, 'medium': 10, 'long': 20}[group]},
                'batch': mode, 'stream_depth_1': mode, 'stream_depth_2': mode,
                'live': {'integrity_matches': True, 'delivery_complete': True, 'stream_dropped_frames': 0}}

    monkeypatch.setattr(diagnostic_run, 'record_take', take)
    run._run()
    report = json.loads(open(run.report_path).read())
    assert report['status'] == 'complete'
    assert report['summary']['numerical_gate_pass']
    assert report['summary']['protocol'] == '30_prompt_screen'
    assert report['summary']['gate'].startswith('unmet')
    assert len(report['takes']) == 30
    assert updates[-1]['phase'] == 'complete'
    from pathlib import Path
    assert Path(run.report_path).stat().st_mode & 0o777 == 0o600


def test_capture_failure_never_passes_or_leaks_error_text(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic_run.settings, 'app_support_dir', lambda: tmp_path)
    engine = SimpleNamespace(transcriber=None, worker=None, control=None, recorder=None)
    run = diagnostic_run.DiagnosticRun(engine, lambda payload: None)

    def fail(*args, **kwargs):
        raise RuntimeError('private content')

    monkeypatch.setattr(diagnostic_run, 'record_take', fail)
    run._run()
    text = open(run.report_path).read()
    report = json.loads(text)
    assert report['status'] == 'error'
    assert not report['summary']['numerical_gate_pass']
    assert 'private content' not in text
