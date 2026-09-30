import numpy as np

from speakeasy import config
from speakeasy.dictation_stream import StreamResult, StreamStatus
from speakeasy.transcriber import Transcriber


def test_constructor_warms_batch_and_complete_stream_lifecycle(monkeypatch):
    calls = []
    model = object()
    monkeypatch.setattr("speakeasy.transcriber.from_pretrained", lambda source: model)
    monkeypatch.setattr(
        Transcriber,
        "transcribe",
        lambda self, audio: calls.append(("batch", len(audio))) or "",
    )

    def warm_stream(self, session):
        calls.append(("stream", session.finished, len(session.audio)))
        return StreamResult(StreamStatus.COMPLETE, text="")

    monkeypatch.setattr(Transcriber, "transcribe_stream", warm_stream)

    transcriber = Transcriber()

    assert transcriber._model is model
    assert calls == [
        ("batch", config.SAMPLE_RATE),
        ("stream", True, config.SAMPLE_RATE),
    ]


def test_constructor_caps_mlx_cache_before_loading_model(monkeypatch):
    events = []
    monkeypatch.setattr(
        "speakeasy.transcriber.mx.set_cache_limit",
        lambda limit: events.append(("cap", limit)) or 0,
    )
    monkeypatch.setattr(
        "speakeasy.transcriber.from_pretrained",
        lambda source: events.append(("load",)) or object(),
    )
    monkeypatch.setattr(Transcriber, "transcribe", lambda self, audio: "")
    monkeypatch.setattr(
        Transcriber,
        "transcribe_stream",
        lambda self, session: StreamResult(StreamStatus.COMPLETE, text=""),
    )

    Transcriber()

    assert config.MLX_CACHE_LIMIT_BYTES == 256 * 1024 * 1024
    assert events[:2] == [("cap", config.MLX_CACHE_LIMIT_BYTES), ("load",)]
