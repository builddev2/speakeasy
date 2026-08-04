"""Feature-gated client for the bundled Core Audio process-tap helper.

The helper is a separate process so a wedged tap teardown cannot hold the
microphone HAL lock inside Speakeasy. Its realtime callback only copies into a
bounded queue; a helper writer queue drains that data to the temporary WAV.
Only non-content JSON status lines cross stdout.
"""

from __future__ import annotations

import json
import platform
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import config, settings

_START_TIMEOUT_SECONDS = 30.0
_STOP_TIMEOUT_SECONDS = 0.75
_EVENT_QUEUE_MAX = 32


class SystemAudioUnavailable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SystemTrackResult:
    path: Path | None
    first_buffer_ns: int | None
    status: str
    dropped_frames: int = 0
    nonzero_signal: bool = False
    writer_failed: bool = False
    writer_lagged: bool = False
    helper_exited: bool = False
    helper_exit_reason: str | None = None
    capture_scope: str = "global"


def helper_path() -> Path:
    return settings.system_audio_helper_path()


def capability() -> str:
    version = platform.mac_ver()[0]
    try:
        parts = tuple(int(part) for part in version.split(".")[:2])
    except ValueError:
        return "unsupported_platform"
    if parts < (14, 2):
        return "requires_macos_14_2"
    if not helper_path().is_file():
        return "helper_missing"
    return "available"


def eligible_process_ids() -> set[int]:
    if capability() != "available":
        return set()
    try:
        result = subprocess.run(
            [str(helper_path()), "--list-pids"],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(event, dict) and event.get("event") == "eligible_processes":
            values = event.get("pids")
            if isinstance(values, list):
                return {
                    value
                    for value in values
                    if isinstance(value, int) and value > 0
                }
    return set()


def _public_reason(reason: str) -> str:
    if reason == "requires_macos_14_2":
        return reason
    if reason in {
        "invalid_arguments",
        "invalid_selected_pid",
        "selected_app_unavailable",
        "unsupported_format",
    }:
        return reason
    if reason.startswith(("tap_create_", "aggregate_create_", "device_start_")):
        return "permission_denied_or_unavailable"
    return "start_failed"


class SystemAudioRecorder:
    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._events: queue.Queue[dict] = queue.Queue(_EVENT_QUEUE_MAX)
        self._reader: threading.Thread | None = None
        self._path: Path | None = None
        self._first_buffer_ns: int | None = None
        self._dropped_frames = 0
        self._nonzero_signal = False
        self._writer_failed = False
        self._writer_lagged = False
        self._helper_exited = False
        self._helper_exit_reason: str | None = None
        self._stopping = False
        self.capture_scope = "global"
        self.status = capability()

    @property
    def first_buffer_ns(self) -> int | None:
        return self._first_buffer_ns

    @property
    def nonzero_signal(self) -> bool:
        return self._nonzero_signal

    @property
    def writer_failed(self) -> bool:
        return self._writer_failed

    @property
    def writer_lagged(self) -> bool:
        return self._writer_lagged

    @property
    def dropped_frames(self) -> int:
        return self._dropped_frames

    @property
    def helper_exited(self) -> bool:
        return self._helper_exited

    @property
    def helper_exit_reason(self) -> str | None:
        return self._helper_exit_reason

    def start(self, path: Path, *, process_id: int | None = None) -> None:
        available = capability()
        self.status = available
        if available != "available":
            raise SystemAudioUnavailable(available)
        if process_id is not None and (
            not isinstance(process_id, int)
            or isinstance(process_id, bool)
            or process_id <= 0
        ):
            raise SystemAudioUnavailable("invalid_selected_pid")
        self.capture_scope = "selected" if process_id is not None else "global"
        self._path = path
        self._first_buffer_ns = None
        self._dropped_frames = 0
        self._nonzero_signal = False
        self._writer_failed = False
        self._writer_lagged = False
        self._helper_exited = False
        self._helper_exit_reason = None
        self._stopping = False
        self._events = queue.Queue(_EVENT_QUEUE_MAX)
        try:
            command = [
                str(helper_path()),
                str(path),
                str(config.MEETING_MAX_SECONDS),
            ]
            if process_id is not None:
                command.extend(["--pid", str(process_id)])
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            self._reader = threading.Thread(
                target=self._read_events, name="system-audio-status", daemon=True
            )
            self._reader.start()
            deadline = time.monotonic() + _START_TIMEOUT_SECONDS
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise queue.Empty
                event = self._events.get(timeout=remaining)
                if event.get("event") in {"ready", "error", "helper_exit"}:
                    break
        except (OSError, queue.Empty) as exc:
            self.force_close()
            self._path = None
            raise SystemAudioUnavailable("start_failed") from exc
        if event.get("event") != "ready":
            reason = _public_reason(str(event.get("reason", "start_failed")))
            self.force_close()
            self._path = None
            raise SystemAudioUnavailable(reason)
        self.status = "capturing"

    def _read_events(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            try:
                event = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("event")
            if kind == "first_buffer":
                value = event.get("host_time_ns")
                if isinstance(value, int) and value >= 0:
                    self._first_buffer_ns = value
            elif kind == "nonzero_signal":
                self._nonzero_signal = True
            elif kind == "writer_error":
                self._writer_failed = True
            elif kind == "writer_lag":
                self._writer_lagged = True
            elif kind == "error":
                self._helper_exit_reason = _public_reason(
                    str(event.get("reason", "start_failed"))
                )
                if self.status == "capturing":
                    self.status = self._helper_exit_reason
            elif kind == "stopped":
                value = event.get("dropped_frames")
                if isinstance(value, int) and value >= 0:
                    self._dropped_frames = value
            try:
                self._events.put_nowait(event)
            except queue.Full:
                pass
        self._helper_exited = True
        if self._helper_exit_reason is None:
            self._helper_exit_reason = (
                "requested_stop" if self._stopping else "unexpected_exit"
            )
        if not self._stopping and self.status == "capturing":
            self.status = "helper_exited"
        try:
            self._events.put_nowait(
                {"event": "helper_exit", "returncode": process.poll()}
            )
        except queue.Full:
            pass

    def stop(self) -> SystemTrackResult:
        process = self._process
        if process is None:
            return self.take_result()
        if process.poll() is not None:
            process.wait()
            if self._reader is not None:
                self._reader.join(timeout=0.25)
            self._reader = None
            if self._helper_exit_reason not in {
                None,
                "requested_stop",
                "unexpected_exit",
            }:
                self.status = self._helper_exit_reason
            elif self.status == "capturing":
                self.status = "helper_exited"
            self._process = None
            return self.take_result()
        self._stopping = True
        process.terminate()
        try:
            process.wait(timeout=_STOP_TIMEOUT_SECONDS)
            status = (
                "captured"
                if process.returncode == 0
                else (
                    self.status
                    if self.status not in {"available", "capturing"}
                    else "stop_failed"
                )
            )
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                pass
            status = "teardown_timeout"
        if self._reader is not None:
            self._reader.join(timeout=0.25)
        self._process = None
        self._reader = None
        self._helper_exited = True
        if status == "captured":
            self._helper_exit_reason = "requested_stop"
        self.status = status
        return self.take_result()

    def force_close(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            self.status = "forced_close"
            self._stopping = True
            self._helper_exited = True
            self._helper_exit_reason = "forced_close"
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    pass
        self._reader = None

    def take_result(self) -> SystemTrackResult:
        path, self._path = self._path, None
        return SystemTrackResult(
            path=path,
            first_buffer_ns=self._first_buffer_ns,
            status=self.status,
            dropped_frames=self._dropped_frames,
            nonzero_signal=self._nonzero_signal,
            writer_failed=self._writer_failed,
            writer_lagged=self._writer_lagged,
            helper_exited=self._helper_exited,
            helper_exit_reason=self._helper_exit_reason,
            capture_scope=self.capture_scope,
        )

    def discard(self) -> None:
        self.force_close()
        path, self._path = self._path, None
        if path is not None:
            path.unlink(missing_ok=True)
