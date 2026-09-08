"""Button-driven diagnostic orchestration; ASR stays on the engine worker."""

import json
import os
import tempfile
import threading

from . import settings
from .diagnostic_content import PROMPTS
from .diagnostic_followup import normalize_report, short_followup
from .dictation_diagnostic import record_take, summarize
from .dictation_stream import StreamingSession


class DiagnosticRun:
    def __init__(self, engine, emit, *, base_report=None, parent_report=None):
        self.engine = engine
        self.emit = emit
        self.cancelled = threading.Event()
        self.next_button = threading.Event()
        self.session = None
        self.phase = "consent"
        self.index = 0
        self.base_report = normalize_report(base_report) if base_report is not None else None
        self.prompts = short_followup(self.base_report) if self.base_report else PROMPTS
        if not self.prompts:
            raise ValueError("No missing short recordings in this report")
        self.takes = self.base_report["takes"] if self.base_report else []
        self.parent_report = parent_report
        self.failed_attempts = self.base_report["failed_attempts"] if self.base_report else 0
        self.report_path = None
        self.thread = None

    def start(self):
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self._run, name="diagnostic-session", daemon=True)
        self.thread.start()

    def advance(self):
        if self.phase in ("prompt", "recording"):
            self.next_button.set()

    def cancel(self):
        self.cancelled.set()
        self.next_button.set()
        if self.session is not None:
            self.session.cancel()

    def _publish(self, phase, **extra):
        self.phase = phase
        self.emit({"phase": phase, "index": self.index + 1, "total": len(self.prompts),
                   "prompt": self.prompts[self.index], **extra})

    def _wait_button(self, message):
        self.next_button.clear()
        if self.cancelled.is_set():
            raise InterruptedError()
        self._publish("recording" if message.startswith("RECORDING") else "prompt")
        self.next_button.wait()
        if self.cancelled.is_set():
            raise InterruptedError()

    def _save(self, target, status):
        summary = summarize(self.takes)
        if status != "complete" or self.failed_attempts:
            summary["numerical_gate_pass"] = False
        if self.failed_attempts:
            summary["failure_reasons"].append(f"{self.failed_attempts} recording/comparison failure(s) need review.")
        target.seek(0)
        json.dump({"source": "consented_real_microphone", "status": status,
                   "build_commit": settings.build_commit(), "takes": self.takes,
                   "parent_report": self.parent_report, "failed_attempts": self.failed_attempts,
                   "summary": summary}, target, indent=2, allow_nan=False)
        target.truncate()
        target.flush()
        os.fsync(target.fileno())
        return summary

    def _run(self):
        status = "cancelled"
        summary = None
        try:
            folder = settings.app_support_dir() / "diagnostic-reports"
            folder.mkdir(mode=0o700, exist_ok=True)
            folder.chmod(0o700)
            fd, self.report_path = tempfile.mkstemp(prefix="microphone-", suffix=".json", dir=folder)
            with os.fdopen(fd, "w") as target:
                try:
                    for self.index, entry in enumerate(self.prompts):
                        if self.cancelled.is_set():
                            raise InterruptedError()
                        self.session = StreamingSession()
                        result = record_take(
                            self.engine.transcriber, self.engine.worker, self.engine.control,
                            entry["reference"], sounds=True, prompt=self._wait_button,
                            recorder=self.engine.recorder, session=self.session,
                            processing=lambda: self._publish("processing"),
                            is_cancelled=self.cancelled.is_set)
                        if self.cancelled.is_set():
                            raise InterruptedError()
                        seconds = result["audio"]["seconds"]
                        result.update(build_commit=settings.build_commit(),
                                      reference=entry["reference"], condition=entry["condition"],
                                      vocabulary=entry["vocabulary"],
                                      duration_group="short" if seconds < 5 else "medium" if seconds < 15 else "long")
                        self.takes.append(result)
                        self._save(target, "in_progress")
                    status = "complete"
                except InterruptedError:
                    status = "cancelled"
                except Exception:
                    self.failed_attempts += 1
                    status = "error"
                finally:
                    summary = self._save(target, status)
        except Exception:
            status = "error"
        finally:
            self._publish(status, summary=summary, reportPath=self.report_path)
