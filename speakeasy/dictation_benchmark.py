"""Take-scoped timing for the immediate dictation pipeline."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field


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

    def _duration(self, field_name: str) -> str:
        start_name, finish_name = _DURATIONS[field_name]
        start = self.events.get(start_name)
        finish = self.events.get(finish_name)
        if start is None or finish is None:
            return "na"
        return f"{(finish - start) / 1_000_000:.1f}"

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

    def emit(self, status: str) -> bool:
        """Print once. Returns whether this call emitted the summary."""
        if self._emitted:
            return False
        self._emitted = True
        print(self.format_summary(status))
        return True
