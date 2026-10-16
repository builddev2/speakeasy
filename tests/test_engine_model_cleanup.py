import threading

from speakeasy import engine as engine_mod, model_store


def _engine(monkeypatch, states):
    eng = object.__new__(engine_mod.DictationEngine)
    eng.transcriber = None
    monkeypatch.setattr(engine_mod, "Transcriber", lambda: object())
    monkeypatch.setattr(eng, "_set_state", lambda s: states.append(s), raising=False)
    return eng


def test_cleanup_runs_after_ready_off_thread(monkeypatch):
    states, seen = [], {}
    done = threading.Event()

    def cleanup():
        seen["states"] = list(states)
        seen["thread"] = threading.current_thread()
        done.set()

    monkeypatch.setattr(model_store, "cleanup_leftovers", cleanup)
    eng = _engine(monkeypatch, states)
    try:
        eng._create_transcriber()
        assert done.wait(5)
    finally:
        done.set()
    assert seen["states"] == [engine_mod.State.READY]
    assert seen["thread"] is not threading.current_thread()
    assert seen["thread"].daemon


def test_cleanup_failure_does_not_escape(monkeypatch):
    states = []
    done = threading.Event()

    def cleanup():
        done.set()
        raise RuntimeError("disk")

    monkeypatch.setattr(model_store, "cleanup_leftovers", cleanup)
    eng = _engine(monkeypatch, states)
    eng._create_transcriber()
    assert done.wait(5)
    assert states == [engine_mod.State.READY]
    # also direct: the wrapper itself swallows
    engine_mod._cleanup_model_leftovers()
