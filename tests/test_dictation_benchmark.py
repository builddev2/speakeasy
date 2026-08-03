"""Phase-level dictation timing without real audio, MLX, or macOS APIs."""

from itertools import count

import numpy as np
import pytest

from speakeasy import engine as engine_module
from speakeasy import injector, transcriber as transcriber_module
from speakeasy.dictation_benchmark import DictationTiming
from speakeasy.engine import DictationEngine, State, _RecorderStopResult


class Clock:
    def __init__(self):
        self.ns = 0

    def __call__(self):
        return self.ns

    def advance(self, milliseconds):
        self.ns += int(milliseconds * 1_000_000)


class Listener:
    def pause(self):
        pass

    def resume(self):
        pass


class Overlay:
    def __init__(self, clock):
        self.clock = clock
        self.hidden = 0

    def hide(self):
        self.hidden += 1
        self.clock.advance(1)


def make_engine(clock, transcriber):
    engine = DictationEngine.__new__(DictationEngine)
    engine.transcriber = transcriber
    engine.profile = None
    engine.overlay = Overlay(clock)
    engine._listener = Listener()
    engine._user_paused = False
    engine.on_state_changed = lambda state: clock.advance(1)
    engine.state = State.TRANSCRIBING
    engine.last_dictation_heard = None
    engine.last_dictation_text = None
    return engine


def benchmark_lines(capsys):
    return [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("DICTATION_BENCH ")
    ]


def field(line, name):
    return dict(part.split("=", 1) for part in line.split()[1:])[name]


def test_release_callback_only_captures_time_and_submits_control_work():
    submitted = []

    class Control:
        def submit(self, *args):
            submitted.append(args)

    engine = DictationEngine.__new__(DictationEngine)
    engine._dictation_clock_ns = lambda: 123_456_789
    engine.control = Control()

    engine._on_hold_end()

    assert submitted == [(engine._stop_recording, 123_456_789)]


def test_success_reports_all_phases_and_original_release_time(monkeypatch, capsys):
    clock = Clock()
    order = []
    clipboard = {"text": "private clipboard"}

    class TimedTranscriber:
        def transcribe(self, audio, *, timing):
            assert order == ["clipboard_read"]
            order.append("transcription")
            timing.samples_before = 48_231
            timing.mark("trim_started")
            clock.advance(1.1)
            timing.samples_after = 39_120
            timing.mark("trim_finished")
            timing.mark("mel_started")
            clock.advance(18.6)
            timing.mark("mel_finished")
            timing.mark("inference_started")
            clock.advance(412.7)
            timing.mark("inference_finished")
            return "private transcript"

    class Profile:
        name = "private profile"

        def apply(self, text):
            clock.advance(0.1)
            return text

    def read_clipboard():
        clock.advance(3.2)
        order.append("clipboard_read")
        return clipboard["text"]

    def insert_text(text, *, timing):
        clipboard["text"] = text
        clock.advance(7.4)
        timing.mark("paste_dispatched")

    def restore_clipboard(previous):
        clock.advance(1.8)
        clipboard["text"] = previous

    monkeypatch.setattr(engine_module.injector, "read_clipboard", read_clipboard)
    monkeypatch.setattr(engine_module.injector, "insert_text", insert_text)
    monkeypatch.setattr(engine_module.injector, "restore_clipboard", restore_clipboard)
    monkeypatch.setattr(
        engine_module.time, "sleep", lambda seconds: clock.advance(seconds * 1000)
    )

    engine = make_engine(clock, TimedTranscriber())
    engine.profile = Profile()
    timing = DictationTiming(17, clock_ns=clock)
    timing.mark("release_received")
    clock.advance(0.4)
    timing.mark("control_stop_started")
    timing.mark("recorder_stop_started")
    clock.advance(12.8)
    timing.mark("recorder_stop_finished")
    timing.recorder_stop_outcome = "normal"
    timing.mark("worker_submitted")
    clock.advance(0.2)

    engine._transcribe_and_paste(np.ones(48_231, dtype=np.float32), timing)

    lines = benchmark_lines(capsys)
    assert len(lines) == 1
    line = lines[0]
    assert field(line, "status") == "success"
    assert field(line, "release_to_control_ms") == "0.4"
    assert field(line, "recorder_stop_ms") == "12.8"
    assert field(line, "worker_queue_ms") == "0.2"
    assert field(line, "clipboard_read_ms") == "3.2"
    assert field(line, "trim_ms") == "1.1"
    assert field(line, "mel_ms") == "18.6"
    assert field(line, "inference_ms") == "412.7"
    assert field(line, "profile_active") == "true"
    assert field(line, "profile_ms") == "0.1"
    assert field(line, "insertion_to_dispatch_ms") == "7.4"
    assert field(line, "release_to_paste_ms") != field(
        line, "insertion_to_dispatch_ms"
    )
    assert field(line, "paste_settle_ms") == "500.0"
    assert field(line, "restore_ms") == "1.8"
    assert field(line, "cleanup_ms") == "2.0"
    assert field(line, "samples_before") == "48231"
    assert field(line, "samples_after") == "39120"
    assert clipboard["text"] == "private clipboard"
    assert order == ["clipboard_read", "transcription"]
    assert "private transcript" not in line
    assert "private clipboard" not in line
    assert "private profile" not in line


def test_summary_has_fixed_field_order_and_emits_once(capsys):
    timing = DictationTiming(3, clock_ns=lambda: 0)
    timing.mark("release_received")
    timing.mark("pipeline_finished")

    assert timing.emit("recording_too_short") is True
    assert timing.emit("success") is False

    line = benchmark_lines(capsys)[0]
    names = [part.split("=", 1)[0] for part in line.split()[1:]]
    assert names == [
        "take",
        "status",
        "recorder_stop_outcome",
        "release_to_control_ms",
        "control_to_recorder_stop_ms",
        "recorder_stop_ms",
        "worker_queue_ms",
        "clipboard_read_ms",
        "trim_ms",
        "mel_ms",
        "inference_ms",
        "profile_active",
        "profile_ms",
        "insertion_to_dispatch_ms",
        "release_to_paste_ms",
        "paste_settle_ms",
        "restore_ms",
        "cleanup_ms",
        "release_to_idle_ms",
        "samples_before",
        "samples_after",
    ]
    assert field(line, "trim_ms") == "na"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("", "empty_transcription"), (RuntimeError("inference"), "transcription_exception")],
)
def test_empty_and_transcription_exception_restore_and_emit_once(
    monkeypatch, capsys, text, expected
):
    clock = Clock()
    restored = []

    class Result:
        def transcribe(self, audio, *, timing):
            if isinstance(text, Exception):
                raise text
            return text

    monkeypatch.setattr(engine_module.injector, "read_clipboard", lambda: "old")
    monkeypatch.setattr(
        engine_module.injector, "restore_clipboard", lambda value: restored.append(value)
    )
    engine = make_engine(clock, Result())
    timing = DictationTiming(1, clock_ns=clock)
    timing.mark("release_received")

    engine._transcribe_and_paste(None, timing)

    line = benchmark_lines(capsys)[0]
    assert field(line, "status") == expected
    assert field(line, "insertion_to_dispatch_ms") == "na"
    assert restored == ["old"]
    assert engine.overlay.hidden == 1


def test_insertion_exception_restores_clipboard_and_emits(monkeypatch, capsys):
    clock = Clock()
    restored = []

    class Result:
        def transcribe(self, audio, *, timing):
            return "private transcript"

    monkeypatch.setattr(engine_module.injector, "read_clipboard", lambda: "old")

    def fail_insert(text, *, timing):
        raise RuntimeError("paste failed")

    monkeypatch.setattr(engine_module.injector, "insert_text", fail_insert)
    monkeypatch.setattr(
        engine_module.injector, "restore_clipboard", lambda value: restored.append(value)
    )
    engine = make_engine(clock, Result())
    timing = DictationTiming(1, clock_ns=clock)
    timing.mark("release_received")

    engine._transcribe_and_paste(None, timing)

    line = benchmark_lines(capsys)[0]
    assert field(line, "status") == "insertion_exception"
    assert field(line, "release_to_paste_ms") == "na"
    assert restored == ["old"]
    assert "private transcript" not in line


@pytest.mark.parametrize(
    ("outcome", "busy", "expected"),
    [
        ("normal", False, "recording_too_short"),
        ("normal", True, "recorder_busy"),
        ("timeout", False, "recorder_stop_timeout"),
    ],
)
def test_control_early_outcomes_emit_without_worker_phases(
    monkeypatch, capsys, outcome, busy, expected
):
    clock = Clock()
    engine = make_engine(clock, object())
    engine._dictation_take_ids = count(1)
    engine._dictation_clock_ns = clock
    engine._recorder_busy = busy

    class Recorder:
        @staticmethod
        def duration_seconds(audio):
            return 0.0

    engine.recorder = Recorder()

    def stopped(timing):
        clock.advance(4)
        timing.mark("recorder_stop_finished")
        return _RecorderStopResult(np.empty(0, dtype=np.float32), outcome)

    monkeypatch.setattr(engine, "_stop_recorder_guarded", stopped)
    monkeypatch.setattr(engine_module, "play_sound", lambda sound: None)

    engine._stop_recording(release_received=0)

    line = benchmark_lines(capsys)[0]
    assert field(line, "status") == expected
    assert field(line, "recorder_stop_outcome") == outcome
    assert field(line, "worker_queue_ms") == "na"
    assert field(line, "inference_ms") == "na"


def test_transcriber_marks_trim_mel_and_inference(monkeypatch):
    clock = Clock()
    timing = DictationTiming(1, clock_ns=clock)
    instance = transcriber_module.Transcriber.__new__(transcriber_module.Transcriber)

    class Result:
        text = "synthetic"

    class Model:
        preprocessor_config = object()

        def generate(self, mel):
            clock.advance(3)
            return [Result()]

    instance._model = Model()

    def trim(audio, sample_rate):
        clock.advance(1)
        return audio[2:]

    def mel(audio, config):
        clock.advance(2)
        return "mel"

    monkeypatch.setattr(transcriber_module.preprocess, "trim_silence", trim)
    monkeypatch.setattr(transcriber_module.mx, "array", lambda audio: audio)
    monkeypatch.setattr(transcriber_module, "get_logmel", mel)

    assert instance.transcribe(np.ones(10), timing=timing) == "synthetic"
    assert timing.samples_before == 10
    assert timing.samples_after == 8
    assert timing._duration("trim_ms") == "1.0"
    assert timing._duration("mel_ms") == "2.0"
    assert timing._duration("inference_ms") == "3.0"


def test_injector_marks_dispatch_only_after_post(monkeypatch):
    clock = Clock()
    timing = DictationTiming(1, clock_ns=clock)
    order = []
    monkeypatch.setattr(injector, "_set_clipboard", lambda text: order.append("set"))
    monkeypatch.setattr(injector.time, "sleep", lambda seconds: order.append("settle"))

    def post():
        order.append("post")
        assert "paste_dispatched" not in timing.events

    monkeypatch.setattr(injector, "_post_cmd_v", post)

    injector.insert_text("synthetic", timing=timing)

    assert order == ["set", "settle", "post"]
    assert timing.events["paste_dispatched"] == 0
