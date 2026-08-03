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
from dataclasses import dataclass
from pathlib import Path

from . import config, settings

_START_TIMEOUT_SECONDS = 30.0
_STOP_TIMEOUT_SECONDS = 0.75


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


def _public_reason(reason: str) -> str:
    if reason == "requires_macos_14_2":
        return reason
    if reason in {"invalid_arguments", "unsupported_format"}:
        return reason
    if reason.startswith(("tap_create_", "aggregate_create_", "device_start_")):
        return "permission_denied_or_unavailable"
    return "start_failed"


class SystemAudioRecorder:
    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._events: queue.Queue[dict] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._path: Path | None = None
        self._first_buffer_ns: int | None = None
        self._dropped_frames = 0
        self.status = capability()

    @property
    def first_buffer_ns(self) -> int | None:
        return self._first_buffer_ns

    def start(self, path: Path) -> None:
        available = capability()
        self.status = available
        if available != "available":
            raise SystemAudioUnavailable(available)
        self._path = path
        self._first_buffer_ns = None
        self._dropped_frames = 0
        self._events = queue.Queue()
        try:
            self._process = subprocess.Popen(
                [str(helper_path()), str(path), str(config.MEETING_MAX_SECONDS)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            self._reader = threading.Thread(
                target=self._read_events, name="system-audio-status", daemon=True
            )
            self._reader.start()
            event = self._events.get(timeout=_START_TIMEOUT_SECONDS)
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
            elif kind == "stopped":
                value = event.get("dropped_frames")
                if isinstance(value, int) and value >= 0:
                    self._dropped_frames = value
            self._events.put(event)

    def stop(self) -> SystemTrackResult:
        process = self._process
        if process is None:
            return self.take_result()
        if process.poll() is not None:
            process.wait()
            if self._reader is not None:
                self._reader.join(timeout=0.25)
            self._reader = None
            self.status = "stop_failed"
            self._process = None
            return self.take_result()
        process.terminate()
        try:
            process.wait(timeout=_STOP_TIMEOUT_SECONDS)
            status = "captured" if process.returncode == 0 else "stop_failed"
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
        self.status = status
        return self.take_result()

    def force_close(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
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
        )

    def discard(self) -> None:
        self.force_close()
        path, self._path = self._path, None
        if path is not None:
            path.unlink(missing_ok=True)
