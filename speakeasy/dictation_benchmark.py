"""Take-scoped timing and offline summaries for immediate dictation."""

import json
import math
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import config


_FIELDS = (
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
)

_DURATIONS = {
    "release_to_control_ms": ("release_received", "control_stop_started"),
    "control_to_recorder_stop_ms": (
        "control_stop_started",
        "recorder_stop_started",
    ),
    "recorder_stop_ms": ("recorder_stop_started", "recorder_stop_finished"),
    "worker_queue_ms": ("worker_submitted", "worker_started"),
    "clipboard_read_ms": ("clipboard_read_started", "clipboard_read_finished"),
    "trim_ms": ("trim_started", "trim_finished"),
    "mel_ms": ("mel_started", "mel_finished"),
    "inference_ms": ("inference_started", "inference_finished"),
    "profile_ms": ("profile_started", "profile_finished"),
    "insertion_to_dispatch_ms": ("insertion_started", "paste_dispatched"),
    "release_to_paste_ms": ("release_received", "paste_dispatched"),
    "paste_settle_ms": ("paste_dispatched", "paste_settle_finished"),
    "restore_ms": ("clipboard_restore_started", "clipboard_restore_finished"),
    "cleanup_ms": ("cleanup_started", "pipeline_finished"),
    "release_to_idle_ms": ("release_received", "pipeline_finished"),
}

_COMPLETION_STATUSES = {
    "empty_transcription",
    "insertion_exception",
    "pipeline_exception",
    "recorder_busy",
    "recorder_stop_timeout",
    "recording_too_short",
    "success",
    "transcription_exception",
}
_RECORDER_STOP_OUTCOMES = {"normal", "timeout"}
_SUMMARY_PHASES = (
    "recorder_stop_ms",
    "worker_queue_ms",
    "clipboard_read_ms",
    "trim_ms",
    "mel_ms",
    "inference_ms",
    "profile_ms",
    "insertion_to_dispatch_ms",
)
_LOG_MAX_BYTES = 5 * 1024 * 1024
_MIN_CONCLUSION_RECORDS = 20
_MIN_GROUP_RECORDS = 5
_WRITE_LOCK = threading.Lock()


def latency_log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "Speakeasy-dictation-latency.jsonl"


def _rotated_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".1")


def _append_record(record: dict, path: Path | None = None) -> None:
    path = path or latency_log_path()
    line = json.dumps(record, separators=(",", ":"), sort_keys=False) + "\n"
    with _WRITE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            size = 0
        if size + len(line.encode("utf-8")) > _LOG_MAX_BYTES:
            path.replace(_rotated_path(path))
        with path.open("a", encoding="utf-8") as destination:
            destination.write(line)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires at least one value")
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _duration_bucket(samples_before: int) -> str:
    seconds = samples_before / config.SAMPLE_RATE
    if seconds <= 5:
        return "short"
    if seconds <= 15:
        return "medium"
    return "long"


def _valid_record(value: object) -> dict | None:
    if not isinstance(value, dict) or not set(_FIELDS).issubset(value):
        return None
    take = value.get("take")
    status = value.get("status")
    if (
        not isinstance(take, int)
        or isinstance(take, bool)
        or not isinstance(status, str)
        or status not in _COMPLETION_STATUSES
    ):
        return None
    record = {"take": take, "status": status}
    outcome = value.get("recorder_stop_outcome")
    if outcome is not None and outcome not in _RECORDER_STOP_OUTCOMES:
        return None
    record["recorder_stop_outcome"] = outcome
    profile_active = value.get("profile_active")
    if profile_active is not None and not isinstance(profile_active, bool):
        return None
    record["profile_active"] = profile_active
    for name in ("samples_before", "samples_after"):
        sample_count = value.get(name)
        if sample_count is not None and (
            not isinstance(sample_count, int)
            or isinstance(sample_count, bool)
            or sample_count < 0
        ):
            return None
        record[name] = sample_count
    for name in _DURATIONS:
        duration = value.get(name)
        if duration is not None and (
            not isinstance(duration, (int, float))
            or isinstance(duration, bool)
            or not math.isfinite(duration)
            or duration < 0
        ):
            return None
        record[name] = float(duration) if duration is not None else None
    return record


def read_records(path: Path | None = None) -> list[dict]:
    path = path or latency_log_path()
    records = []
    for source in (_rotated_path(path), path):
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for line in lines:
            try:
                record = _valid_record(json.loads(line))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if record is not None:
                records.append(record)
    return records


def _format_ms(value: float) -> str:
    return f"{value:.1f} ms"


def _metric_line(label: str, values: list[float], *, include_max: bool = False) -> str:
    if not values:
        return f"  {label}: no measurements"
    summary = (
        f"n={len(values)}, p50={_format_ms(_percentile(values, 0.50))}, "
        f"p95={_format_ms(_percentile(values, 0.95))}"
    )
    if include_max:
        summary += f", max={_format_ms(max(values))}"
    return f"  {label}: {summary}"


def format_latency_summary(records: list[dict]) -> str:
    lines = [f"Valid timing records: {len(records)}"]
    statuses = Counter(record["status"] for record in records)
    if statuses:
        lines.append(
            "Completion status: "
            + ", ".join(f"{name}={statuses[name]}" for name in sorted(statuses))
        )
    else:
        lines.append("Completion status: none")

    successful = [
        record
        for record in records
        if record["status"] == "success"
        and record.get("release_to_paste_ms") is not None
    ]
    lines.append("Release to paste (successful takes):")
    lines.append(
        _metric_line(
            "release_to_paste_ms",
            [record["release_to_paste_ms"] for record in successful],
            include_max=True,
        )
    )
    lines.append("Measured phases (successful takes):")
    for name in _SUMMARY_PHASES:
        lines.append(
            _metric_line(
                name,
                [record[name] for record in successful if record.get(name) is not None],
            )
        )

    lines.append("Recording duration groups (from samples_before at 16 kHz):")
    for bucket in ("short", "medium", "long"):
        grouped = [
            record
            for record in successful
            if record.get("samples_before") is not None
            and _duration_bucket(record["samples_before"]) == bucket
        ]
        latencies = [record["release_to_paste_ms"] for record in grouped]
        lines.append(_metric_line(bucket, latencies, include_max=True))
        if len(grouped) < _MIN_GROUP_RECORDS:
            lines.append(
                f"    dominant phase: insufficient data "
                f"({len(grouped)}/{_MIN_GROUP_RECORDS} successful takes)"
            )
            continue
        medians = {}
        for name in _SUMMARY_PHASES:
            values = [record[name] for record in grouped if record.get(name) is not None]
            if values:
                medians[name] = _percentile(values, 0.50)
        if medians:
            dominant = max(medians, key=medians.get)
            lines.append(
                f"    dominant measured pre-paste phase: {dominant} "
                f"(p50 {_format_ms(medians[dominant])})"
            )
        else:
            lines.append("    dominant phase: no measured phases")

    if len(successful) < _MIN_CONCLUSION_RECORDS:
        lines.append(
            f"Caveat: only {len(successful)} successful release-to-paste records; "
            f"collect at least {_MIN_CONCLUSION_RECORDS} before drawing a bottleneck "
            "conclusion."
        )
    else:
        lines.append(
            f"Evidence check: {len(successful)} successful release-to-paste records "
            "are available; review duration-group coverage before drawing a conclusion."
        )
    return "\n".join(lines)


def print_latency_summary(path: Path | None = None) -> None:
    path = path or latency_log_path()
    print(f"Dictation latency log: {path}")
    print(format_latency_summary(read_records(path)))


@dataclass
class DictationTiming:
    """Absolute monotonic timestamps and safe summary fields for one take."""

    take: int
    clock_ns: Callable[[], int] = time.perf_counter_ns
    events: dict[str, int] = field(default_factory=dict)
    recorder_stop_outcome: str | None = None
    profile_active: bool | None = None
    samples_before: int | None = None
    samples_after: int | None = None
    _emitted: bool = False

    def mark(self, event: str, timestamp_ns: int | None = None) -> None:
        self.events.setdefault(
            event, self.clock_ns() if timestamp_ns is None else timestamp_ns
        )

    def _duration_value(self, field_name: str) -> float | None:
        start_name, finish_name = _DURATIONS[field_name]
        start = self.events.get(start_name)
        finish = self.events.get(finish_name)
        if start is None or finish is None:
            return None
        return round((finish - start) / 1_000_000, 1)

    def _duration(self, field_name: str) -> str:
        value = self._duration_value(field_name)
        return "na" if value is None else f"{value:.1f}"

    def record(self, status: str) -> dict:
        safe_status = status if status in _COMPLETION_STATUSES else "pipeline_exception"
        outcome = (
            self.recorder_stop_outcome
            if self.recorder_stop_outcome in _RECORDER_STOP_OUTCOMES
            else None
        )
        values = {
            "take": self.take,
            "status": safe_status,
            "recorder_stop_outcome": outcome,
            "profile_active": self.profile_active,
            "samples_before": self.samples_before,
            "samples_after": self.samples_after,
        }
        values.update({name: self._duration_value(name) for name in _DURATIONS})
        return {name: values[name] for name in _FIELDS}

    def format_summary(self, status: str) -> str:
        values = {
            "take": str(self.take),
            "status": status,
            "recorder_stop_outcome": self.recorder_stop_outcome or "na",
            "profile_active": (
                "na"
                if self.profile_active is None
                else str(self.profile_active).lower()
            ),
            "samples_before": (
                "na" if self.samples_before is None else str(self.samples_before)
            ),
            "samples_after": (
                "na" if self.samples_after is None else str(self.samples_after)
            ),
        }
        values.update({name: self._duration(name) for name in _DURATIONS})
        return "DICTATION_BENCH " + " ".join(
            f"{name}={values[name]}" for name in _FIELDS
        )

    def emit(self, status: str, *, path: Path | None = None) -> bool:
        """Print and persist once without letting file errors escape."""
        if self._emitted:
            return False
        self._emitted = True
        print(self.format_summary(status))
        try:
            _append_record(self.record(status), path)
        except Exception as error:
            print(f"  → dictation timing log unavailable: {type(error).__name__}")
        return True
