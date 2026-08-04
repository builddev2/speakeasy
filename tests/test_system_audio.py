"""System-audio helper lifecycle, with the process fully mocked."""

import json
import subprocess

import pytest

from speakeasy import system_audio


class FakeProcess:
    def __init__(self, events, *, timeout=False, returncode=None):
        self.stdout = iter(json.dumps(event) + "\n" for event in events)
        self.returncode = returncode
        self.timeout = timeout
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        if self.timeout:
            raise subprocess.TimeoutExpired("helper", timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

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


def test_eligible_process_ids_are_read_from_helper_without_names(
    monkeypatch, tmp_path
):
    _available(monkeypatch, tmp_path)
    result = type(
        "Result",
        (),
        {
            "stdout": json.dumps(
                {"event": "eligible_processes", "pids": [101, 202, -1, "303"]}
            )
        },
    )()
    monkeypatch.setattr(system_audio.subprocess, "run", lambda *a, **k: result)

    assert system_audio.eligible_process_ids() == {101, 202}


def test_helper_reports_first_buffer_and_clean_stop(monkeypatch, tmp_path):
    _available(monkeypatch, tmp_path)
    process = FakeProcess(
        [
            {"event": "ready"},
            {"event": "first_buffer", "host_time_ns": 1234},
            {"event": "nonzero_signal"},
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
    assert result.nonzero_signal is True
    assert result.status == "captured"


def test_helper_reports_writer_failure_and_lag(monkeypatch, tmp_path):
    _available(monkeypatch, tmp_path)
    process = FakeProcess(
        [
            {"event": "ready"},
            {"event": "first_buffer", "host_time_ns": 1234},
            {"event": "nonzero_signal"},
            {"event": "writer_lag"},
            {"event": "writer_error"},
            {"event": "stopped", "frames": 10, "dropped_frames": 4},
        ]
    )
    monkeypatch.setattr(system_audio.subprocess, "Popen", lambda *a, **k: process)
    recorder = system_audio.SystemAudioRecorder()

    recorder.start(tmp_path / "system.wav")
    result = recorder.stop()

    assert result.nonzero_signal is True
    assert result.writer_lagged is True
    assert result.writer_failed is True
    assert result.dropped_frames == 4


def test_first_buffer_may_arrive_before_ready(monkeypatch, tmp_path):
    _available(monkeypatch, tmp_path)
    process = FakeProcess(
        [
            {"event": "first_buffer", "host_time_ns": 1234},
            {"event": "ready"},
            {"event": "stopped", "frames": 10, "dropped_frames": 0},
        ]
    )
    monkeypatch.setattr(system_audio.subprocess, "Popen", lambda *a, **k: process)
    recorder = system_audio.SystemAudioRecorder()

    recorder.start(tmp_path / "system.wav")
    result = recorder.stop()

    assert result.first_buffer_ns == 1234
    assert result.status == "captured"


def test_selected_process_id_is_the_only_helper_scope(monkeypatch, tmp_path):
    _available(monkeypatch, tmp_path)
    process = FakeProcess(
        [
            {"event": "ready"},
            {"event": "first_buffer", "host_time_ns": 1234},
            {"event": "nonzero_signal"},
        ]
    )
    commands = []

    def popen(command, **kwargs):
        commands.append(command)
        return process

    monkeypatch.setattr(system_audio.subprocess, "Popen", popen)
    recorder = system_audio.SystemAudioRecorder()

    recorder.start(tmp_path / "system.wav", process_id=4242)
    result = recorder.stop()

    assert commands == [[
        str(system_audio.helper_path()),
        str(tmp_path / "system.wav"),
        str(system_audio.config.MEETING_MAX_SECONDS),
        "--pid",
        "4242",
    ]]
    assert result.capture_scope == "selected"


def test_selected_process_disappearance_is_not_global_fallback(
    monkeypatch, tmp_path
):
    _available(monkeypatch, tmp_path)
    process = FakeProcess(
        [
            {"event": "ready"},
            {"event": "error", "reason": "selected_app_unavailable"},
        ],
        returncode=6,
    )
    monkeypatch.setattr(system_audio.subprocess, "Popen", lambda *a, **k: process)
    recorder = system_audio.SystemAudioRecorder()

    recorder.start(tmp_path / "system.wav", process_id=4242)
    result = recorder.stop()

    assert result.status == "selected_app_unavailable"
    assert result.capture_scope == "selected"
    assert result.helper_exit_reason == "selected_app_unavailable"


def test_selected_process_resolution_failure_is_explicit(monkeypatch, tmp_path):
    _available(monkeypatch, tmp_path)
    process = FakeProcess(
        [{"event": "error", "reason": "selected_app_unavailable"}],
        returncode=4,
    )
    monkeypatch.setattr(system_audio.subprocess, "Popen", lambda *a, **k: process)
    recorder = system_audio.SystemAudioRecorder()

    with pytest.raises(system_audio.SystemAudioUnavailable) as raised:
        recorder.start(tmp_path / "system.wav", process_id=4242)

    assert raised.value.reason == "selected_app_unavailable"


def test_helper_exit_before_ready_fails_start_immediately(monkeypatch, tmp_path):
    _available(monkeypatch, tmp_path)
    process = FakeProcess([], returncode=4)
    monkeypatch.setattr(system_audio.subprocess, "Popen", lambda *a, **k: process)
    recorder = system_audio.SystemAudioRecorder()

    with pytest.raises(system_audio.SystemAudioUnavailable) as raised:
        recorder.start(tmp_path / "system.wav")

    assert raised.value.reason == "start_failed"
    assert recorder.status == "forced_close"


def test_unexpected_helper_exit_after_ready_is_visible(monkeypatch, tmp_path):
    _available(monkeypatch, tmp_path)
    process = FakeProcess([{"event": "ready"}], returncode=7)
    monkeypatch.setattr(system_audio.subprocess, "Popen", lambda *a, **k: process)
    recorder = system_audio.SystemAudioRecorder()

    recorder.start(tmp_path / "system.wav")
    result = recorder.stop()

    assert result.status == "helper_exited"
    assert result.helper_exited is True
    assert result.helper_exit_reason == "unexpected_exit"


def test_permission_denial_is_normalized_without_osstatus(monkeypatch, tmp_path):
    _available(monkeypatch, tmp_path)
    process = FakeProcess([{"event": "error", "reason": "tap_create_1886547824"}])
    monkeypatch.setattr(system_audio.subprocess, "Popen", lambda *a, **k: process)
    recorder = system_audio.SystemAudioRecorder()

    with pytest.raises(system_audio.SystemAudioUnavailable) as raised:
        recorder.start(tmp_path / "system.wav")
    assert raised.value.reason == "permission_denied_or_unavailable"


def test_selected_process_permission_denial_does_not_retry_global(
    monkeypatch, tmp_path
):
    _available(monkeypatch, tmp_path)
    process = FakeProcess([{"event": "error", "reason": "tap_create_1886547824"}])
    commands = []

    def popen(command, **kwargs):
        commands.append(command)
        return process

    monkeypatch.setattr(system_audio.subprocess, "Popen", popen)
    recorder = system_audio.SystemAudioRecorder()

    with pytest.raises(system_audio.SystemAudioUnavailable) as raised:
        recorder.start(tmp_path / "system.wav", process_id=4242)

    assert raised.value.reason == "permission_denied_or_unavailable"
    assert len(commands) == 1
    assert commands[0][-2:] == ["--pid", "4242"]


def test_wedged_helper_is_killed_and_reported(monkeypatch, tmp_path):
    _available(monkeypatch, tmp_path)
    process = FakeProcess([{"event": "ready"}], timeout=True)
    monkeypatch.setattr(system_audio.subprocess, "Popen", lambda *a, **k: process)
    recorder = system_audio.SystemAudioRecorder()
    recorder.start(tmp_path / "system.wav")

    result = recorder.stop()

    assert process.killed
    assert result.status == "teardown_timeout"
