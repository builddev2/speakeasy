"""Exercise the diagnostic worker/recorder lifecycle with no real audio/model."""

from concurrent.futures import ThreadPoolExecutor
import json
import threading

import numpy as np
import pytest

from speakeasy import dictation_diagnostic as diagnostic
from speakeasy.dictation_benchmark import DictationTiming
from speakeasy.dictation_stream import run_stream
from test_dictation_stream import _Model, _Stream


def test_capture_comparison_uses_same_audio_and_worker_without_telemetry(tmp_path, monkeypatch, capsys):
    audio = np.arange(16123, dtype=np.float32) / 20000
    model_threads = []
    controls = []

    class Recorder:
        stream_delivery_complete = True
        stream_dropped_frames = 0

        def prewarm(self):
            controls.append(threading.get_ident())

        def start(self, chunk_queue):
            controls.append(threading.get_ident())
            self.session = chunk_queue
            chunk_queue.put_nowait(audio[:16000])

        def stop(self):
            controls.append(threading.get_ident())
            self.session.put_nowait(audio[16000:])
            return audio

        def force_close(self):
            pass

    class Transcriber:
        def transcribe_stream(self, session):
            model_threads.append(threading.get_ident())
            return run_stream(_Model(_Stream([])), session, to_device=np.asarray)

    def compare(transcriber, saved, reference):
        model_threads.append(threading.get_ident())
        assert np.array_equal(saved, audio)
        assert reference == "private reference"
        return {"batch": {"text": "private result"}}

    monkeypatch.setattr("speakeasy.recorder.Recorder", Recorder)
    monkeypatch.setattr(diagnostic, "compare", compare)
    monkeypatch.setattr(diagnostic.settings, "app_support_dir", lambda: tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    monkeypatch.setattr(DictationTiming, "emit", lambda *a, **k: pytest.fail("telemetry called"))
    with ThreadPoolExecutor(max_workers=1) as worker, ThreadPoolExecutor(max_workers=1) as control:
        report = diagnostic.record_take(Transcriber(), worker, control, "private reference")
    assert report["live"]["received_frames"] == len(audio)
    assert report["live"]["integrity_matches"]
    assert len(set(model_threads)) == len(set(controls)) == 1
    assert model_threads[0] != controls[0]
    assert not list(tmp_path.rglob("*.wav"))
    assert "private" not in capsys.readouterr().out


def test_failed_attempt_is_recorded_and_cannot_pass_gate(tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    answers = iter(["RECORD", "private reference"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(diagnostic.settings, "app_support_dir", lambda: tmp_path)
    monkeypatch.setattr("speakeasy.transcriber.Transcriber", lambda: object())

    def fail(*args, **kwargs):
        raise RuntimeError("private failure detail")

    monkeypatch.setattr(diagnostic, "record_take", fail)
    with pytest.raises(RuntimeError):
        diagnostic.main(["--output", str(output)])
    report = json.loads(output.read_text())
    assert report["failed_attempt"] == {"attempt": 1, "type": "RuntimeError"}
    assert not report["summary"]["numerical_gate_pass"]
    assert "private failure detail" not in output.read_text()
