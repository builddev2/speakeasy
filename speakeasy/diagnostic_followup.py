"""Read prior evidence without modifying it; plan only missing short takes."""

import copy
import json
import os
import tempfile

from . import settings
from .dictation_diagnostic import summarize

_SHORT_REFERENCES = (
    ("Please close the door.", "ordinary"),
    ("Please check the database.", "technical"),
    ("Send the notes today.", "ordinary"),
    ("Please review the code.", "technical"),
    ("Move the meeting forward.", "ordinary"),
    ("Keep the audio offline.", "technical"),
    ("Bring the report tomorrow.", "ordinary"),
    ("Please restart the application.", "technical"),
    ("Leave the lights on.", "ordinary"),
    ("Please validate the release.", "technical"),
)


def normalize_report(report):
    """Legacy live batch handoffs never had an ASR final to score."""
    result = copy.deepcopy(report)
    for take in result["takes"]:
        take.setdefault("build_commit", result["build_commit"])
        if take["live"].get("status") == "batch_required":
            take["live"]["wer"] = None
            take["live"]["outcome"] = "batch_handoff"
    result["summary"] = summarize(result["takes"])
    failures = result.get("failed_attempts", int(result.get("status") == "error"))
    result["failed_attempts"] = failures
    if result.get("status") != "complete":
        result["summary"]["numerical_gate_pass"] = False
    if failures:
        result["summary"]["numerical_gate_pass"] = False
        result["summary"]["failure_reasons"].append(f"{failures} recording/comparison failure(s) need review.")
        details = result.get("failure_details", [])
        for detail in details:
            result["summary"]["failure_reasons"].append(
                f"Prompt {detail['prompt_index']}: {detail['operation'].replace('_', ' ')} ({detail['type']}).")
            if detail.get("reason", "unclassified") != "unclassified":
                result["summary"]["failure_reasons"].append(detail["reason"].replace('_', ' ').capitalize() + ".")
        if failures > len(details):
            result["summary"]["failure_reasons"].append("An earlier failure has no saved cause; it cannot be diagnosed from this report.")
    return result


def latest_report():
    folder = settings.app_support_dir() / "diagnostic-reports"
    for path in sorted(folder.glob("microphone-*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            report = json.loads(path.read_text())
            if report.get("source") != "consented_real_microphone" or not report.get("takes"):
                continue
            return path, normalize_report(report)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None, None


def short_followup(report):
    if report is None or len(report["takes"]) < 30:
        return []
    counts = report["summary"]["duration_counts"]
    if counts["medium"] < 10 or counts["long"] < 10:
        return []
    missing = max(0, 10 - counts["short"])
    prompts = []
    for index, (reference, vocabulary) in enumerate(_SHORT_REFERENCES[:missing]):
        prompts.append({"reference": reference, "vocabulary": vocabulary,
                        "condition": "quiet" if index < (missing + 1) // 2 else "moderate_noise",
                        "expected_duration_group": "short"})
    return prompts


def save_reviewed_report(report):
    folder = settings.app_support_dir() / "diagnostic-reports"
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    reviewed = normalize_report(report)
    reviewed["reviewed_by_build"] = settings.build_commit()
    fd, path = tempfile.mkstemp(prefix="reviewed-microphone-", suffix=".json", dir=folder)
    with os.fdopen(fd, "w") as target:
        json.dump(reviewed, target, indent=2, allow_nan=False)
    return path
