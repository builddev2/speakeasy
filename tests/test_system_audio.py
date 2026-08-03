"""System-audio helper lifecycle, with the process fully mocked."""

import json
import subprocess

import pytest

from speakeasy import system_audio


class FakeProcess:
    def __init__(self, events, *, timeout=False):
        self.stdout = iter(json.dumps(event) + "\n" for event in events)
        self.returncode = None
        self.timeout = timeout
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout):
        if self.timeout:
            raise subprocess.TimeoutExpired("helper", timeout)
        self.returncode = 0
        return 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    def poll(self):
        return self.returncode


def _available(monkeypatch, tmp_path):
    helper = tmp_path / "helper"
    helper.write_bytes(b"binary")
    monkeypatch.setattr(system_audio, "helper_path", lambda: helper)
    monkeypatch.setattr(system_audio.platform, "mac_ver", lambda: ("14.2", (), ""))


def test_capability_feature_gates_macos_14_0(monkeypatch, tmp_path):
    monkeypatch.setattr(system_audio.platform, "mac_ver", lambda: ("14.1", (), ""))
    monkeypatch.setattr(system_audio, "helper_path", lambda: tmp_path / "missing")
    assert system_audio.capability() == "requires_macos_14_2"


def test_helper_reports_first_buffer_and_clean_stop(monkeypatch, tmp_path):
    _available(monkeypatch, tmp_path)
    process = FakeProcess(
        [
            {"event": "ready"},
            {"event": "first_buffer", "host_time_ns": 1234},
            {"event": "stopped", "frames": 10, "dropped_frames": 3},
        ]
    )
    monkeypatch.setattr(system_audio.subprocess, "Popen", lambda *a, **k: process)
    recorder = system_audio.SystemAudioRecorder()
    path = tmp_path / "system.wav"

    recorder.start(path)
    result = recorder.stop()

    assert process.terminated
    assert result.path == path
    assert result.first_buffer_ns == 1234
    assert result.dropped_frames == 3
    assert result.status == "captured"


def test_permission_denial_is_normalized_without_osstatus(monkeypatch, tmp_path):
    _available(monkeypatch, tmp_path)
    process = FakeProcess([{"event": "error", "reason": "tap_create_1886547824"}])
    monkeypatch.setattr(system_audio.subprocess, "Popen", lambda *a, **k: process)
    recorder = system_audio.SystemAudioRecorder()

    with pytest.raises(system_audio.SystemAudioUnavailable) as raised:
        recorder.start(tmp_path / "system.wav")
    assert raised.value.reason == "permission_denied_or_unavailable"


def test_wedged_helper_is_killed_and_reported(monkeypatch, tmp_path):
    _available(monkeypatch, tmp_path)
    process = FakeProcess([{"event": "ready"}], timeout=True)
    monkeypatch.setattr(system_audio.subprocess, "Popen", lambda *a, **k: process)
    recorder = system_audio.SystemAudioRecorder()
    recorder.start(tmp_path / "system.wav")

    result = recorder.stop()

    assert process.killed
    assert result.status == "teardown_timeout"
