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
