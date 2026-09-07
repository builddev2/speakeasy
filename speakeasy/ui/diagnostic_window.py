"""Local microphone check in a glass window, with explicit consent."""

import objc
from AppKit import NSWorkspace
from Foundation import NSObject

from ..diagnostic_run import DiagnosticRun
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
        self._web.show()

    @objc.python_method
    def _start(self, params, reply):
        if self._owns_engine or self.engine.state is not State.READY:
            reply(error="Wait until dictation and other sessions are idle.")
            return
        self._owns_engine = True
        self.run = DiagnosticRun(self.engine, self._emit)
        self.engine._diagnostic_cancel = self.run.cancel
        self.engine.pause()
        self._payload = {"phase": "processing", "total": 30}
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
        if self.run is not None and self.run.report_path:
            NSWorkspace.sharedWorkspace().selectFile_inFileViewerRootedAtPath_(self.run.report_path, "")
        reply(True)

    def windowWillClose_(self, notification):
        if self._owns_engine:
            self.run.cancel()
