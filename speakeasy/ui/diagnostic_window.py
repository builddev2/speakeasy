"""Local microphone check in a glass window, with explicit consent."""

import objc
from AppKit import NSWorkspace
from Foundation import NSObject

from ..diagnostic_run import DiagnosticRun
from ..diagnostic_followup import latest_report, short_followup, save_reviewed_report, normalize_report
from ..engine import State
from .webbridge import BridgeDispatcher
from .webwindow import WebWindow


class DiagnosticWindowController(NSObject):
    def initWithEngine_(self, engine):
        self = objc.super(DiagnosticWindowController, self).init()
        if self is None:
            return None
        self.engine = engine
        self.run = None
        self._owns_engine = False
        self._previous_report = None
        self._previous_path = None
        self._reviewed_path = None
        self._payload = {"phase": "consent", "total": 30}
        dispatcher = BridgeDispatcher()
        dispatcher.register("diagnostic.state", lambda params, reply: reply(self._payload))
        dispatcher.register("diagnostic.start", self._start)
        dispatcher.register("diagnostic.next", self._next)
        dispatcher.register("diagnostic.cancel", self._cancel)
        dispatcher.register("diagnostic.report", self._report)
        self._web = WebWindow("Microphone Check", 660, 600, "diagnostic", dispatcher)
        self._web.window.setDelegate_(self)
        return self

    def show(self):
        if not self._owns_engine:
            self._load_previous()
        self._web.show()

    @objc.python_method
    def _load_previous(self, phase="results"):
        self._reviewed_path = None
        self._previous_path, self._previous_report = latest_report()
        if self._previous_report is not None:
            self._payload = {"phase": phase, "total": len(self._previous_report["takes"]),
                             "summary": self._previous_report["summary"],
                             "reportPath": str(self._previous_path),
                             "followupCount": len(short_followup(self._previous_report))}
            self._web.emit("diagnostic.state", self._payload)

    @objc.python_method
    def _start(self, params, reply):
        if self._owns_engine or self.engine.state is not State.READY:
            reply(error="Wait until dictation and other sessions are idle.")
            return
        followup = bool(params.get("followup"))
        if followup and not short_followup(self._previous_report):
            reply(error="No short-recording follow-up is available for the saved report.")
            return
        self.run = DiagnosticRun(
            self.engine, self._emit,
            base_report=self._previous_report if followup else None,
            parent_report=str(self._previous_path) if followup else None)
        self._owns_engine = True
        self._previous_report = None
        self._reviewed_path = None
        self.engine._diagnostic_cancel = self.run.cancel
        self.engine.pause()
        self._payload = {"phase": "processing", "total": len(self.run.prompts)}
        self._web.emit("diagnostic.state", self._payload)
        self.run.start()
        reply(True)

    @objc.python_method
    def _emit(self, payload):
        self.performSelectorOnMainThread_withObject_waitUntilDone_(b"updated:", payload, False)

    def updated_(self, payload):
        self._payload = dict(payload)
        if self._payload["phase"] in ("complete", "cancelled", "error") and self._owns_engine:
            self._owns_engine = False
            self.engine._diagnostic_cancel = None
            if not self.engine._shutting_down:
                self.engine.resume()
            if self._payload["phase"] != "error":
                self._load_previous(self._payload["phase"])
            else:
                self._previous_report = {
                    "status": "error", "source": "consented_real_microphone",
                    "build_commit": self.run.failure_details[-1]["build_commit"],
                    "takes": self.run.takes, "failed_attempts": self.run.failed_attempts,
                    "failure_details": self.run.failure_details,
                    "parent_report": self.run.parent_report}
                self._previous_path = self.run.report_path
                self._previous_report = normalize_report(self._previous_report)
                self._payload["followupCount"] = len(short_followup(self._previous_report))
        self._web.emit("diagnostic.state", self._payload)

    @objc.python_method
    def _next(self, params, reply):
        if self.run is not None:
            self.run.advance()
        reply(True)

    @objc.python_method
    def _cancel(self, params, reply):
        if self.run is not None and self._owns_engine:
            self.run.cancel()
            self._payload = {**self._payload, "phase": "cancelling"}
            self._web.emit("diagnostic.state", self._payload)
        reply(True)

    @objc.python_method
    def _report(self, params, reply):
        if self._previous_report is not None and self._reviewed_path is None:
            self._reviewed_path = save_reviewed_report(self._previous_report)
        path = self._reviewed_path
        if path:
            NSWorkspace.sharedWorkspace().selectFile_inFileViewerRootedAtPath_(path, "")
        reply(True)

    def windowWillClose_(self, notification):
        if self._owns_engine:
            self.run.cancel()
