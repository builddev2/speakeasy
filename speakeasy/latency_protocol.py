"""Content-free grouping for controlled, build-scoped inference experiments."""

from collections import defaultdict

from .dictation_benchmark import _percentile


def classify(last_finished, now, previous_operation, idle_seconds):
    if last_finished is None:
        return "cold"
    if previous_operation in {"diagnostic", "meeting"}:
        return f"after_{previous_operation}"
    return "idle" if now - last_finished >= idle_seconds else "warm"


def summarize(records):
    groups = defaultdict(list)
    keys = ("build_commit", "mode", "temperature", "duration_group")
    for record in records:
        groups[tuple(record[key] for key in keys)].append(record["elapsed_ms"])
    return [dict(zip(keys, key), attempts=len(values), sufficient=len(values) >= 30,
                 p50_ms=round(_percentile(values, .5), 2),
                 p95_ms=round(_percentile(values, .95), 2))
            for key, values in sorted(groups.items())]
