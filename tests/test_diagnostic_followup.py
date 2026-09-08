"""Follow-up evidence is appended to a copy, never rewritten in place."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

from speakeasy import diagnostic_followup, diagnostic_run, config
from speakeasy.dictation_diagnostic import summarize


def base_report():
    takes = []
    for index in range(30):
        group = 'short' if index < 3 else 'medium' if index < 20 else 'long'
        takes.append({'reference': 'Please check the report.', 'duration_group': group,
                      'condition': 'quiet' if index < 15 else 'moderate_noise',
                      'vocabulary': 'ordinary' if index % 2 else 'technical',
                      'batch': {'text': 'Please check the report.', 'wer': 0},
                      'stream_depth_1': {'text': 'Please check the report.', 'wer': 0},
                      'stream_depth_2': {'text': 'Please check the report.', 'wer': 0},
                      'live': {'status': 'batch_required' if group == 'long' else 'complete',
                               'wer': 1 if group == 'long' else 0, 'text': None,
                               'integrity_matches': True, 'delivery_complete': True,
                               'stream_dropped_frames': 0}})
    return {'source': 'consented_real_microphone', 'status': 'complete',
            'build_commit': 'old-build', 'takes': takes}


def test_batch_and_trimming_remain_safe_defaults():
    assert config.DICTATION_STREAMING_ENABLED is False
    assert config.DICTATION_TRIM_ENABLED is True


def test_old_handoffs_are_unscored_in_memory_without_changing_original():
    source = base_report()
    before = copy.deepcopy(source)
    normalized = diagnostic_followup.normalize_report(source)
    assert source == before
    assert normalized['takes'][-1]['live']['wer'] is None
    assert normalized['takes'][-1]['live']['outcome'] == 'batch_handoff'
    assert normalized['summary']['batch_handoffs'] == 10
    assert normalized['summary']['missing_duration_counts'] == {'short': 7, 'medium': 0, 'long': 0}
    assert 'Short recordings: 3/10; 7 more needed.' in normalized['summary']['failure_reasons']
    assert not normalized['summary']['numerical_gate_pass']


def test_followup_only_requests_missing_short_takes():
    prompts = diagnostic_followup.short_followup(diagnostic_followup.normalize_report(base_report()))
    assert len(prompts) == 7
    assert all(len(p['reference'].split()) == 4 for p in prompts)
    assert {p['condition'] for p in prompts} == {'quiet', 'moderate_noise'}
    assert {p['vocabulary'] for p in prompts} == {'ordinary', 'technical'}
    assert diagnostic_followup.short_followup(None) == []


def test_followup_preserves_source_file_and_merges_once(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic_run.settings, 'app_support_dir', lambda: tmp_path)
    source = base_report()
    folder = tmp_path / 'diagnostic-reports'
    folder.mkdir()
    path = folder / 'microphone-original.json'
    original = json.dumps(source)
    path.write_text(original)
    engine = SimpleNamespace(transcriber=None, worker=None, control=None, recorder=None)
    run = diagnostic_run.DiagnosticRun(engine, lambda payload: None, base_report=source, parent_report=str(path))

    def take(*args, **kwargs):
        result = copy.deepcopy(source['takes'][0])
        reference = args[3]
        result['audio'] = {'seconds': 3}
        for mode in ('batch', 'stream_depth_1', 'stream_depth_2'):
            result[mode] = {'text': reference, 'wer': 0}
        return result

    monkeypatch.setattr(diagnostic_run, 'record_take', take)
    run._run()
    combined = json.loads(Path(run.report_path).read_text())
    assert path.read_text() == original
    assert len(source['takes']) == 30
    assert len(combined['takes']) == 37
    assert combined['parent_report'] == str(path)
    assert combined['takes'][0]['build_commit'] == 'old-build'
    assert combined['summary']['duration_counts'] == {'short': 10, 'medium': 17, 'long': 10}
    assert combined['summary']['numerical_gate_pass']
    assert combined['takes'][29]['live']['wer'] is None
    latest_path, latest = diagnostic_followup.latest_report()
    assert str(latest_path) == run.report_path
    assert diagnostic_followup.short_followup(latest) == []
    combined['status'] = 'cancelled'
    assert not diagnostic_followup.normalize_report(combined)['summary']['numerical_gate_pass']


def test_failed_prior_attempt_cannot_be_hidden_by_followup():
    source = base_report()
    source['status'] = 'error'
    report = diagnostic_followup.normalize_report(source)
    assert report['failed_attempts'] == 1
    assert not report['summary']['numerical_gate_pass']


def test_invalid_new_report_does_not_hide_previous_valid_one(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic_followup.settings, 'app_support_dir', lambda: tmp_path)
    folder = tmp_path / 'diagnostic-reports'
    folder.mkdir()
    original = folder / 'microphone-old.json'
    original.write_text(json.dumps(base_report()))
    (folder / 'microphone-new.json').write_text('{')
    assert diagnostic_followup.latest_report()[0] == original


def test_reviewed_export_has_correct_scores_and_private_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic_followup.settings, 'app_support_dir', lambda: tmp_path)
    original = base_report()
    path = Path(diagnostic_followup.save_reviewed_report(original))
    reviewed = json.loads(path.read_text())
    assert path.name.startswith('reviewed-microphone-')
    assert reviewed['takes'][-1]['live']['wer'] is None
    assert original['takes'][-1]['live']['wer'] == 1
    assert path.stat().st_mode & 0o777 == 0o600
    assert diagnostic_followup.latest_report() == (None, None)
