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


def test_canary_requires_explicit_opt_in_and_batch_override_wins():
    from speakeasy.latency_protocol import streaming_canary_enabled
    assert not streaming_canary_enabled(canary=False, batch=False)
    assert streaming_canary_enabled(canary=True, batch=False)
    assert not streaming_canary_enabled(canary=True, batch=True)
