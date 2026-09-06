"""Bounded overlap chunking for during-capture meeting ASR."""

import numpy as np

from speakeasy import config
from speakeasy.meeting_stream import MeetingASRSession


def _pcm(frames, value=1):
    return np.full(frames, value, dtype=np.int16).tobytes()


def test_overlap_chunks_match_offline_long_form_boundaries(monkeypatch):
    monkeypatch.setattr(config, "SAMPLE_RATE", 10)
    monkeypatch.setattr(config, "MEETING_CHUNK_SECONDS", 4.0)
    monkeypatch.setattr(config, "MEETING_OVERLAP_SECONDS", 1.0)
    session = MeetingASRSession(max_chunks=3)

    session.add_pcm(_pcm(40, 1))
    session.add_pcm(_pcm(30, 2))
    session.add_pcm(_pcm(5, 3))
    session.finish()

    chunks = [session.get(timeout=0) for _ in range(3)]
    assert [(start, len(pcm) // 2) for start, pcm in chunks] == [
        (0, 40),
        (30, 40),
        (60, 15),
    ]
    assert session.finished is True
    assert session.overflowed is False


def test_queue_overflow_invalidates_pretranscription_without_growing(monkeypatch):
    monkeypatch.setattr(config, "SAMPLE_RATE", 10)
    monkeypatch.setattr(config, "MEETING_CHUNK_SECONDS", 4.0)
    monkeypatch.setattr(config, "MEETING_OVERLAP_SECONDS", 1.0)
    session = MeetingASRSession(max_chunks=1)

    session.add_pcm(_pcm(70))
    session.finish()

    assert session.overflowed is True
    assert session.finished is True
    assert session.get(timeout=0)[0] == 0
    assert session.empty is True


def test_cancel_discards_pending_tail_and_stops_accepting_audio(monkeypatch):
    monkeypatch.setattr(config, "SAMPLE_RATE", 10)
    monkeypatch.setattr(config, "MEETING_CHUNK_SECONDS", 4.0)
    monkeypatch.setattr(config, "MEETING_OVERLAP_SECONDS", 1.0)
    session = MeetingASRSession()

    session.add_pcm(_pcm(20))
    session.cancel()
    session.add_pcm(_pcm(40))
    session.finish()

    assert session.cancelled is True
    assert session.empty is True
