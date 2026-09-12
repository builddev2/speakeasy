"""Clipboard restoration must not outrun the target app's paste handler."""

from speakeasy import engine as engine_module
from speakeasy.engine import DictationEngine, State


class _Transcriber:
    def transcribe(self, audio):
        return "fresh dictation"


class _Listener:
    def __init__(self, order=None):
        self.order = order

    def pause(self):
        if self.order is not None:
            self.order.append("pause")

    def resume(self):
        if self.order is not None:
            self.order.append("resume")


def test_slow_target_reads_dictation_before_clipboard_is_restored(monkeypatch):
    clock = 0.0
    clipboard = {"text": "previous clipboard"}
    paste_due = {"at": None}
    pasted = []

    def insert_text(text):
        clipboard["text"] = text
        paste_due["at"] = clock + 0.25

    def sleep(seconds):
        nonlocal clock
        clock += seconds
        if paste_due["at"] is not None and clock >= paste_due["at"]:
            pasted.append(clipboard["text"])
            paste_due["at"] = None

    monkeypatch.setattr(engine_module.injector, "read_clipboard", lambda: clipboard["text"])
    monkeypatch.setattr(engine_module.injector, "insert_text", insert_text)
    monkeypatch.setattr(
        engine_module.injector,
        "restore_clipboard",
        lambda previous: clipboard.update(text=previous),
    )
    monkeypatch.setattr(engine_module.time, "sleep", sleep)

    engine = DictationEngine.__new__(DictationEngine)
    engine.transcriber = _Transcriber()
    engine.profile = None
    engine.overlay = None
    engine._listener = _Listener()
    engine._user_paused = False
    engine.on_state_changed = lambda state: None
    engine.state = State.TRANSCRIBING
    engine.last_dictation_heard = None
    engine.last_dictation_text = None

    engine._transcribe_and_paste(None)
    if paste_due["at"] is not None:
        sleep(paste_due["at"] - clock)

    assert pasted == ["fresh dictation"]
    assert clipboard["text"] == "previous clipboard"


def test_finalizer_restores_after_insertion_error_and_resumes_first(monkeypatch):
    order = []

    def insert_text(text):
        order.append(("insert", text))
        raise RuntimeError("target failed")

    monkeypatch.setattr(engine_module.injector, "insert_text", insert_text)
    monkeypatch.setattr(
        engine_module.injector,
        "restore_clipboard",
        lambda previous: order.append(("restore", previous)),
    )
    monkeypatch.setattr(
        engine_module.time, "sleep", lambda seconds: order.append("settle")
    )

    engine = DictationEngine.__new__(DictationEngine)
    engine.transcriber = _Transcriber()
    engine.profile = None
    engine.overlay = None
    engine._listener = _Listener(order)
    engine._user_paused = False
    engine.on_state_changed = lambda state: order.append("idle")
    engine.state = State.TRANSCRIBING
    engine.last_dictation_heard = None
    engine.last_dictation_text = None

    engine._finalize_dictation("final", "old")

    assert order == [
        "pause",
        ("insert", "final"),
        "resume",
        ("restore", "old"),
        "idle",
    ]


def test_user_paused_during_final_insert_is_not_resumed(monkeypatch):
    order = []
    monkeypatch.setattr(
        engine_module.injector,
        "insert_text",
        lambda text: order.append(("insert", text)),
    )
    monkeypatch.setattr(
        engine_module.injector,
        "restore_clipboard",
        lambda previous: order.append(("restore", previous)),
    )
    monkeypatch.setattr(
        engine_module.time, "sleep", lambda seconds: order.append("settle")
    )

    engine = DictationEngine.__new__(DictationEngine)
    engine.transcriber = _Transcriber()
    engine.profile = None
    engine.overlay = None
    engine._listener = _Listener(order)
    engine._user_paused = True
    engine.on_state_changed = lambda state: order.append("idle")
    engine.state = State.TRANSCRIBING
    engine.last_dictation_heard = None
    engine.last_dictation_text = None

    engine._finalize_dictation("final", "old")

    assert order == [
        "pause",
        ("insert", "final"),
        "settle",
        ("restore", "old"),
        "idle",
    ]


def test_profile_error_restores_without_inserting(monkeypatch):
    order = []

    class Profile:
        def apply(self, text):
            order.append(("profile", text))
            raise RuntimeError("profile failed")

    monkeypatch.setattr(
        engine_module.injector,
        "insert_text",
        lambda text: order.append(("insert", text)),
    )
    monkeypatch.setattr(
        engine_module.injector,
        "restore_clipboard",
        lambda previous: order.append(("restore", previous)),
    )

    engine = DictationEngine.__new__(DictationEngine)
    engine.transcriber = _Transcriber()
    engine.profile = Profile()
    engine.overlay = None
    engine._listener = _Listener(order)
    engine._user_paused = False
    engine.on_state_changed = lambda state: order.append("idle")
    engine.state = State.TRANSCRIBING
    engine.last_dictation_heard = None
    engine.last_dictation_text = None

    engine._finalize_dictation("raw final", "old")

    assert order == [
        ("profile", "raw final"),
        ("restore", "old"),
        "idle",
    ]
    assert engine.last_dictation_heard == "raw final"
    assert engine.last_dictation_text is None


def test_stale_final_never_profiles_retains_or_inserts(monkeypatch):
    engine = DictationEngine.__new__(DictationEngine)
    engine._dictation_generation = 2
    engine.last_dictation_text = "new final"
    engine.last_dictation_heard = "new raw"
    calls = []
    monkeypatch.setattr(engine, "_restore_after_dictation", lambda *a, **k: None)
    monkeypatch.setattr(engine_module.injector, "insert_text", lambda *a, **k: calls.append(a))
    engine._finalize_dictation("old final", None, generation=1)
    assert engine.last_dictation_heard == "new raw"
    assert engine.last_dictation_text == "new final"
    assert not calls


def test_recovery_ttl_replacement_and_shutdown_clear(monkeypatch):
    import threading
    engine = DictationEngine.__new__(DictationEngine)
    engine._insertion_lock = threading.RLock()
    engine._last_dictation_timer = None
    engine.last_dictation_heard = "raw"
    engine._retain_last_dictation("first")
    first = engine._last_dictation_timer
    engine._retain_last_dictation("second")
    assert first.finished.is_set()
    monkeypatch.setattr(engine_module.threading, "current_thread", lambda: first)
    engine._expire_last_dictation()
    assert engine.last_dictation_text == "second"
    current = engine._last_dictation_timer
    monkeypatch.setattr(engine_module.threading, "current_thread", lambda: current)
    engine._expire_last_dictation()
    assert engine.last_dictation_text is None
    assert engine.last_dictation_heard is None


def test_1000_stale_generations_never_overwrite_latest_final(monkeypatch):
    engine = DictationEngine.__new__(DictationEngine)
    engine._dictation_generation = 1001
    engine.last_dictation_text = "latest"
    engine.last_dictation_heard = "latest raw"
    monkeypatch.setattr(engine_module.injector, "insert_text",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    for generation in range(1000):
        engine._finalize_dictation("stale", None, generation=generation)
    assert engine.last_dictation_text == "latest"
    assert engine.last_dictation_heard == "latest raw"


def test_post_dispatch_settle_does_not_block_next_capture_lock(monkeypatch):
    import threading
    engine = DictationEngine.__new__(DictationEngine)
    engine.profile = None
    engine.overlay = None
    engine._listener = _Listener()
    engine._user_paused = False
    engine.on_state_changed = lambda state: None
    engine.transcriber = object()
    monkeypatch.setattr(engine_module.injector, "insert_text", lambda text: None)
    monkeypatch.setattr(engine_module.injector, "restore_clipboard", lambda previous: None)
    def settle(seconds):
        acquired = []
        def next_capture():
            lock = engine._delivery_lock()
            if lock.acquire(timeout=.1):
                acquired.append(True)
                lock.release()
        thread = threading.Thread(target=next_capture)
        thread.start()
        thread.join(.2)
        assert acquired == [True]
    monkeypatch.setattr(engine_module.time, "sleep", settle)
    engine._finalize_dictation("final", None)
    engine._clear_last_dictation()
