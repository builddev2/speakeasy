from speakeasy.latency_protocol import classify, summarize


def test_temperature_tracks_transitions_and_real_idle():
    assert classify(None, 2, "dictation", 1800) == "cold"
    assert classify(1, 2, "dictation", 1800) == "warm"
    assert classify(1, 1801, "dictation", 1800) == "idle"
    assert classify(1, 2, "diagnostic", 1800) == "after_diagnostic"
    assert classify(1, 2, "meeting", 1800) == "after_meeting"


def test_summary_never_pools_builds_or_underfilled_groups():
    rows = [dict(build_commit="a", mode="batch", temperature="warm",
                 duration_group="short", elapsed_ms=i) for i in range(30)]
    rows.append(dict(rows[0], build_commit="b"))
    result = summarize(rows)
    assert result[0]["attempts"] == 30
    assert result[0]["sufficient"] is True
    assert result[0]["p95_ms"] == 27.55
    assert result[1]["sufficient"] is False


def test_canary_requires_explicit_launch_opt_in_and_batch_override_wins(monkeypatch):
    import sys
    from types import SimpleNamespace
    from speakeasy import config, dictation_diagnostic
    from speakeasy.__main__ import main
    observed = []
    monkeypatch.setattr(config, "DICTATION_STREAMING_ENABLED", False)
    monkeypatch.setattr(dictation_diagnostic, "sweep_temporary_audio", lambda: None)
    monkeypatch.setitem(sys.modules, "speakeasy.ui.menubar", SimpleNamespace(
        run_app=lambda profile: observed.append(config.DICTATION_STREAMING_ENABLED)))
    for flags in ([], ["--dictation-streaming-canary"],
                  ["--dictation-streaming-canary", "--dictation-batch-mode"], []):
        monkeypatch.setattr(sys, "argv", ["speakeasy", *flags])
        main()
    assert observed == [False, True, False, False]


def test_formal_session_preserves_original_100_prompt_coverage():
    import json
    from collections import Counter
    from pathlib import Path
    corpus = Path(__file__).resolve().parents[1] / 'docs/real-mic-formal-100-corpus.json'
    rows = json.loads(corpus.read_text())
    assert len(rows) == 100
    assert len({row['id'] for row in rows}) == 100
    assert Counter(row['expected_duration_group'] for row in rows) == {
        'short': 34, 'medium': 34, 'long': 32}
    assert Counter(row['condition'] for row in rows) == {'quiet': 50, 'moderate_noise': 50}
    assert Counter(row['vocabulary'] for row in rows) == {'ordinary': 50, 'technical': 50}
