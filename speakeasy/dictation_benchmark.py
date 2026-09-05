"""Take-scoped timing and offline summaries for immediate dictation."""

import json
import math
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import config, settings


_FIELDS = (
    "build_commit",
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
    "stream_context_ms",
    "stream_first_chunk_ms",
    "stream_add_audio_ms",
    "stream_provisional_ms",
    "stream_final_flush_ms",
    "stream_queue_high_water",
    "stream_queue_capacity",
    "stream_overflowed",
    "fallback_reason",
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

_STREAM_DURATION_FIELDS = {
    "stream_context_ms",
    "stream_first_chunk_ms",
    "stream_add_audio_ms",
    "stream_provisional_ms",
    "stream_final_flush_ms",
}
_FALLBACK_REASONS = {"stream_error", "stream_overflow"}

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
    "recorder_stop_error",
    "recording_too_short",
    "success",
    "success_fallback_queue_overflow",
    "success_fallback_stream_error",
    "success_streaming",
    "transcription_exception",
}
_SUCCESS_STATUSES = {
    "success",
    "success_fallback_queue_overflow",
    "success_fallback_stream_error",
    "success_streaming",
}
_RECORDER_STOP_OUTCOMES = {"error", "normal", "timeout"}
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
_LOG_PATH_OVERRIDE: Path | None = None


def latency_log_path() -> Path:
    return _LOG_PATH_OVERRIDE or (
        Path.home() / "Library" / "Logs" / "Speakeasy-dictation-latency.jsonl"
    )


def set_latency_log_path(path: Path) -> None:
    global _LOG_PATH_OVERRIDE
    _LOG_PATH_OVERRIDE = path


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
    legacy_fields = set(_FIELDS) - {
        "build_commit",
        *_STREAM_DURATION_FIELDS,
        "stream_queue_high_water",
        "stream_queue_capacity",
        "stream_overflowed",
        "fallback_reason",
    }
    if not isinstance(value, dict) or not legacy_fields.issubset(value):
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
    build_commit = value.get("build_commit", "unknown")
    if not isinstance(build_commit, str) or not build_commit:
        return None
    record = {"build_commit": build_commit, "take": take, "status": status}
    outcome = value.get("recorder_stop_outcome")
    if outcome is not None and outcome not in _RECORDER_STOP_OUTCOMES:
        return None
    record["recorder_stop_outcome"] = outcome
    profile_active = value.get("profile_active")
    if profile_active is not None and not isinstance(profile_active, bool):
        return None
    record["profile_active"] = profile_active
    stream_overflowed = value.get("stream_overflowed")
    if stream_overflowed is not None and not isinstance(stream_overflowed, bool):
        return None
    record["stream_overflowed"] = stream_overflowed
    fallback_reason = value.get("fallback_reason")
    if fallback_reason is not None and fallback_reason not in _FALLBACK_REASONS:
        return None
    record["fallback_reason"] = fallback_reason
    for name in ("stream_queue_high_water", "stream_queue_capacity"):
        count = value.get(name)
        if count is not None and (
            not isinstance(count, int) or isinstance(count, bool) or count < 0
        ):
            return None
        record[name] = count
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
    for name in _STREAM_DURATION_FIELDS:
        duration = value.get(name)
        if duration is not None and (
            not isinstance(duration, (int, float))
            or isinstance(duration, bool)
            or not math.isfinite(duration)
            or duration < 0
        ):
            return None
        record[name] = float(duration) if duration is not None else None
    return {name: record[name] for name in _FIELDS}


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
        if record["status"] in _SUCCESS_STATUSES
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
    if successful:
        pure_streaming = sum(
            record["status"] == "success_streaming" for record in successful
        )
        lines.append(
            "Streaming coverage (successful takes): "
            f"{pure_streaming}/{len(successful)} "
            f"({pure_streaming / len(successful) * 100:.1f}%)"
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


def _comparison_mode(records: list[dict]) -> dict:
    successful = [
        record for record in records if record["status"] in _SUCCESS_STATUSES
    ]
    measured = [
        record
        for record in successful
        if record.get("release_to_paste_ms") is not None
    ]
    grouped = {
        bucket: [
            record
            for record in measured
            if record.get("samples_before") is not None
            and _duration_bucket(record["samples_before"]) == bucket
        ]
        for bucket in ("short", "medium", "long")
    }
    attempt_groups = {
        bucket: [
            record
            for record in records
            if record.get("samples_before") is not None
            and _duration_bucket(record["samples_before"]) == bucket
        ]
        for bucket in ("short", "medium", "long")
    }
    return {
        "attempts": len(records),
        "successful": len(successful),
        "measured": measured,
        "groups": grouped,
        "attempt_groups": attempt_groups,
        "timeouts_busy": sum(
            record["status"] in {"recorder_busy", "recorder_stop_timeout"}
            for record in records
        ),
        "pure_streaming": sum(
            record["status"] == "success_streaming" for record in successful
        ),
        "overflow_fallbacks": sum(
            record["status"] == "success_fallback_queue_overflow"
            for record in successful
        ),
        "stream_error_fallbacks": sum(
            record["status"] == "success_fallback_stream_error"
            for record in successful
        ),
    }


def _improvement(batch_value: float, streaming_value: float) -> float:
    if batch_value == 0:
        return 0.0 if streaming_value == 0 else -math.inf
    return (batch_value - streaming_value) / batch_value * 100


def format_comparative_summary(
    batch_records: list[dict], streaming_records: list[dict]
) -> str:
    """Compare separate batch and streaming logs without joining take content."""
    batch = _comparison_mode(batch_records)
    streaming = _comparison_mode(streaming_records)
    lines = ["Dictation latency comparison (batch vs streaming)"]
    for label, mode in (("Batch", batch), ("Streaming", streaming)):
        reliability = (
            mode["successful"] / mode["attempts"] * 100 if mode["attempts"] else 0.0
        )
        groups = ", ".join(
            f"{name}={len(mode['attempt_groups'][name])}"
            for name in ("short", "medium", "long")
        )
        lines.append(
            f"{label}: attempts={mode['attempts']}, successful={mode['successful']}, "
            f"reliability={reliability:.1f}%, {groups}"
        )
    streaming_successes = streaming["successful"]
    coverage = (
        streaming["pure_streaming"] / streaming_successes * 100
        if streaming_successes
        else 0.0
    )
    lines.append(
        "Streaming outcomes: "
        f"pure={streaming['pure_streaming']}/{streaming_successes} ({coverage:.1f}%), "
        f"queue_overflow_fallback={streaming['overflow_fallbacks']}, "
        f"stream_error_fallback={streaming['stream_error_fallbacks']}"
    )

    prerequisites = []
    for label, mode in (("batch", batch), ("streaming", streaming)):
        if mode["attempts"] < 30:
            prerequisites.append(f"{label} attempts {mode['attempts']}/30")
        if mode["successful"] < 29:
            prerequisites.append(f"{label} successful {mode['successful']}/29")
        if len(mode["measured"]) < 29:
            prerequisites.append(
                f"{label} measured successful {len(mode['measured'])}/29"
            )
        for bucket in ("short", "medium", "long"):
            count = len(mode["attempt_groups"][bucket])
            if count < 10:
                prerequisites.append(f"{label} {bucket} {count}/10")
    if prerequisites:
        lines.append("Result: INSUFFICIENT DATA")
        lines.append("Missing: " + "; ".join(prerequisites))
        return "\n".join(lines)

    checks: list[tuple[str, bool, str]] = []
    batch_latency = [record["release_to_paste_ms"] for record in batch["measured"]]
    stream_latency = [
        record["release_to_paste_ms"] for record in streaming["measured"]
    ]
    for percentile, threshold in ((0.50, 30.0), (0.95, 25.0)):
        batch_value = _percentile(batch_latency, percentile)
        stream_value = _percentile(stream_latency, percentile)
        improvement = _improvement(batch_value, stream_value)
        checks.append(
            (
                f"overall p{int(percentile * 100)} improvement >= {threshold:.0f}%",
                improvement >= threshold,
                f"{improvement:.1f}% ({batch_value:.1f} -> {stream_value:.1f} ms)",
            )
        )
    for bucket in ("medium", "long"):
        batch_value = _percentile(
            [record["release_to_paste_ms"] for record in batch["groups"][bucket]],
            0.50,
        )
        stream_value = _percentile(
            [record["release_to_paste_ms"] for record in streaming["groups"][bucket]],
            0.50,
        )
        improvement = _improvement(batch_value, stream_value)
        checks.append(
            (
                f"{bucket} p50 improvement >= 25%",
                improvement >= 25.0,
                f"{improvement:.1f}% ({batch_value:.1f} -> {stream_value:.1f} ms)",
            )
        )
    for bucket in ("short", "medium", "long"):
        batch_value = _percentile(
            [record["release_to_paste_ms"] for record in batch["groups"][bucket]],
            0.95,
        )
        stream_value = _percentile(
            [record["release_to_paste_ms"] for record in streaming["groups"][bucket]],
            0.95,
        )
        regression = -_improvement(batch_value, stream_value)
        checks.append(
            (
                f"{bucket} p95 regression <= 5%",
                regression <= 5.0,
                f"{regression:.1f}% ({batch_value:.1f} -> {stream_value:.1f} ms)",
            )
        )
    for label, mode in (("batch", batch), ("streaming", streaming)):
        reliability = mode["successful"] / mode["attempts"] * 100
        checks.extend(
            [
                (
                    f"{label} reliability >= 95%",
                    reliability >= 95.0,
                    f"{reliability:.1f}%",
                ),
                (
                    f"{label} timeout/busy outcomes = 0",
                    mode["timeouts_busy"] == 0,
                    str(mode["timeouts_busy"]),
                ),
            ]
        )
    checks.extend(
        [
            (
                "pure streaming coverage >= 95%",
                coverage >= 95.0,
                f"{coverage:.1f}%",
            ),
            (
                "queue overflow fallbacks = 0",
                streaming["overflow_fallbacks"] == 0,
                str(streaming["overflow_fallbacks"]),
            ),
            (
                "stream error fallbacks <= 1",
                streaming["stream_error_fallbacks"] <= 1,
                str(streaming["stream_error_fallbacks"]),
            ),
        ]
    )
    for name, passed, value in checks:
        lines.append(f"  {'PASS' if passed else 'FAIL'}: {name} [{value}]")
    lines.append("Result: " + ("PASS" if all(item[1] for item in checks) else "FAIL"))
    return "\n".join(lines)


def print_comparative_summary(batch_path: Path, streaming_path: Path) -> None:
    print(f"Batch dictation latency log: {batch_path}")
    print(f"Streaming dictation latency log: {streaming_path}")
    print(
        format_comparative_summary(
            read_records(batch_path), read_records(streaming_path)
        )
    )


def print_latency_summary(path: Path | None = None) -> None:
    path = path or latency_log_path()
    print(f"Dictation latency log: {path}")
    print(format_latency_summary(read_records(path)))


@dataclass
class DictationTiming:
    """Absolute monotonic timestamps and safe summary fields for one take."""

    take: int
    build_commit: str = field(default_factory=settings.build_commit)
    clock_ns: Callable[[], int] = time.perf_counter_ns
    events: dict[str, int] = field(default_factory=dict)
    recorder_stop_outcome: str | None = None
    profile_active: bool | None = None
    samples_before: int | None = None
    samples_after: int | None = None
    stream_context_ms: float | None = None
    stream_first_chunk_ms: float | None = None
    stream_add_audio_ms: float | None = None
    stream_provisional_ms: float | None = None
    stream_final_flush_ms: float | None = None
    stream_queue_high_water: int | None = None
    stream_queue_capacity: int | None = None
    stream_overflowed: bool | None = None
    fallback_reason: str | None = None
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
            "build_commit": self.build_commit,
            "take": self.take,
            "status": safe_status,
            "recorder_stop_outcome": outcome,
            "profile_active": self.profile_active,
            "samples_before": self.samples_before,
            "samples_after": self.samples_after,
            "stream_context_ms": self.stream_context_ms,
            "stream_first_chunk_ms": self.stream_first_chunk_ms,
            "stream_add_audio_ms": self.stream_add_audio_ms,
            "stream_provisional_ms": self.stream_provisional_ms,
            "stream_final_flush_ms": self.stream_final_flush_ms,
            "stream_queue_high_water": self.stream_queue_high_water,
            "stream_queue_capacity": self.stream_queue_capacity,
            "stream_overflowed": self.stream_overflowed,
            "fallback_reason": self.fallback_reason,
        }
        values.update({name: self._duration_value(name) for name in _DURATIONS})
        return {name: values[name] for name in _FIELDS}

    def format_summary(self, status: str) -> str:
        values = {
            "build_commit": self.build_commit,
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
            "stream_context_ms": self._safe_metric(self.stream_context_ms),
            "stream_first_chunk_ms": self._safe_metric(self.stream_first_chunk_ms),
            "stream_add_audio_ms": self._safe_metric(self.stream_add_audio_ms),
            "stream_provisional_ms": self._safe_metric(self.stream_provisional_ms),
            "stream_final_flush_ms": self._safe_metric(self.stream_final_flush_ms),
            "stream_queue_high_water": self._safe_metric(self.stream_queue_high_water),
            "stream_queue_capacity": self._safe_metric(self.stream_queue_capacity),
            "stream_overflowed": (
                "na"
                if self.stream_overflowed is None
                else str(self.stream_overflowed).lower()
            ),
            "fallback_reason": self.fallback_reason or "na",
        }
        values.update({name: self._duration(name) for name in _DURATIONS})
        return "DICTATION_BENCH " + " ".join(
            f"{name}={values[name]}" for name in _FIELDS
        )

    @staticmethod
    def _safe_metric(value) -> str:
        if value is None:
            return "na"
        return f"{value:.1f}" if isinstance(value, float) else str(value)

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
