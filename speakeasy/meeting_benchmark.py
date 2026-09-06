"""Content-free phase telemetry for meeting post-processing."""

import json
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import settings

FIELDS = (
    "build_commit",
    "status",
    "capture_mode",
    "mic_frames",
    "system_frames",
    "stop_ms",
    "mic_asr_ms",
    "system_asr_ms",
    "diarization_ms",
    "voice_identification_ms",
    "alignment_ms",
    "save_ms",
    "stop_to_final_ms",
)
_STATUSES = {"success", "cancelled", "error", "no_audio"}
_CAPTURE_MODES = {"mic_only", "mic_and_system"}
_PHASES = {
    "stop",
    "mic_asr",
    "system_asr",
    "diarization",
    "voice_identification",
    "alignment",
    "save",
}
_WRITE_LOCK = threading.Lock()


def log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "Speakeasy-meeting-latency.jsonl"


@dataclass
class MeetingTiming:
    clock_ns: Callable[[], int] = time.perf_counter_ns
    build_commit: str = field(default_factory=settings.build_commit)
    capture_mode: str | None = None
    mic_frames: int = 0
    system_frames: int = 0
    events: dict[str, int] = field(default_factory=dict)
    _emitted: bool = False

    def mark(self, event: str) -> None:
        self.events.setdefault(event, self.clock_ns())

    def start(self, phase: str) -> None:
        if phase not in _PHASES:
            raise ValueError("unknown meeting timing phase")
        self.mark(f"{phase}_started")

    def finish(self, phase: str) -> None:
        if phase not in _PHASES:
            raise ValueError("unknown meeting timing phase")
        self.mark(f"{phase}_finished")

    def _duration(self, phase: str) -> float | None:
        start = self.events.get(f"{phase}_started")
        finish = self.events.get(f"{phase}_finished")
        if start is None or finish is None:
            return None
        return round((finish - start) / 1_000_000, 1)

    def record(self, status: str) -> dict:
        stop_start = self.events.get("stop_started")
        final = self.events.get("pipeline_finished")
        stop_to_final = (
            round((final - stop_start) / 1_000_000, 1)
            if stop_start is not None and final is not None
            else None
        )
        values = {
            "build_commit": self.build_commit,
            "status": status,
            "capture_mode": self.capture_mode,
            "mic_frames": self.mic_frames,
            "system_frames": self.system_frames,
            **{f"{phase}_ms": self._duration(phase) for phase in _PHASES},
            "stop_to_final_ms": stop_to_final,
        }
        return {name: values[name] for name in FIELDS}

    def emit(self, status: str, *, path: Path | None = None) -> bool:
        if self._emitted:
            return False
        self._emitted = True
        record = self.record(status)
        destination = path or log_path()
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with _WRITE_LOCK, destination.open("a", encoding="utf-8") as output:
                output.write(json.dumps(record, separators=(",", ":")) + "\n")
        except OSError as error:
            print(f"  → meeting timing log unavailable: {type(error).__name__}")
        return True


def _valid(value: object) -> dict | None:
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        return None
    if (
        value["status"] not in _STATUSES
        or value["capture_mode"] not in _CAPTURE_MODES
    ):
        return None
    for name in ("mic_frames", "system_frames"):
        if (
            not isinstance(value[name], int)
            or isinstance(value[name], bool)
            or value[name] < 0
        ):
            return None
    for name in FIELDS[5:]:
        item = value[name]
        if item is not None and (
            not isinstance(item, (int, float)) or isinstance(item, bool) or not math.isfinite(item) or item < 0
        ):
            return None
    return {name: value[name] for name in FIELDS}


def read_records(path: Path | None = None) -> list[dict]:
    try:
        lines = (path or log_path()).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            record = _valid(json.loads(line))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if record is not None:
            records.append(record)
    return records
