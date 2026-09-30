"""Spawn targets for tests/test_diarization_child.py (no sherpa-onnx)."""

import os
import time

from speakeasy import meetings
from speakeasy.diarization_process import serve


def stub_turns(conn, wav_path, expected_count, max_speakers, names):
    def work(ready, progress, on_phase):
        ready()
        progress(0.5)
        on_phase("voice_identification")
        return [meetings.DiarizationTurn(0.0, 1.0, 3, 0.9, True, "Alice"),
                (1.0, 2.0, expected_count if expected_count is not None else 7)]
    serve(conn, work)


def stub_never_ready(conn, wav_path, expected_count, max_speakers, names):
    def work(ready, progress, on_phase):
        time.sleep(30)
        return []
    serve(conn, work)


def stub_exit_after_progress(conn, wav_path, expected_count, max_speakers, names):
    def work(ready, progress, on_phase):
        ready()
        progress(0.25)
        time.sleep(0.2)
        os._exit(3)
    serve(conn, work)


def stub_error_with_secret(conn, wav_path, expected_count, max_speakers, names):
    def work(ready, progress, on_phase):
        ready()
        raise ValueError("/Users/someone/secret-meeting.wav: the merger closes Friday")
    serve(conn, work)


def stub_slow(conn, wav_path, expected_count, max_speakers, names):
    def work(ready, progress, on_phase):
        ready()
        time.sleep(30)
        return []
    serve(conn, work)


def stub_many_turns(conn, wav_path, expected_count, max_speakers, names):
    def work(ready, progress, on_phase):
        ready()
        return [(i * 0.5, i * 0.5 + 0.4, i % 6) for i in range(20_000)]
    serve(conn, work)


def stub_ready_then_slow(conn, wav_path, expected_count, max_speakers, names):
    def work(ready, progress, on_phase):
        ready()
        time.sleep(1.5)
        return [(0.0, 1.0, 1)]
    serve(conn, work)


def stub_many_progress(conn, wav_path, expected_count, max_speakers, names):
    def work(ready, progress, on_phase):
        ready()
        for i in range(1, 1000):
            progress(i * 0.001)
        progress(1.0)
        return []
    serve(conn, work)
