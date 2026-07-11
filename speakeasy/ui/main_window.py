"""Dock-reachable main window — WKWebView hosting frontend dock.html.

Same public surface as the old native implementation: menubar.py wires
engine callbacks to the engineStateChanged_/meetingProgress_/meetingSaved_
selectors on the main thread, and AppDelegate calls show() on launch and
Dock reopen.
"""

import objc
from Foundation import NSObject

from speakeasy.engine import State
from speakeasy.ui.webbridge import BridgeDispatcher
from speakeasy.ui.webwindow import WebWindow


class MainWindowController(NSObject):
    def initWithEngine_(self, engine):
        self = objc.super(MainWindowController, self).init()
        if self is None:
            return None
        self.engine = engine
        self.meetings_window = None
        self.training_window = None
        self._progress_text = None
        # Set by menubar.py after both controllers exist: when present, this
        # is the single owner of meetings/training windows and _open_window
        # delegates to it instead of building a second, competing instance
        # (two TrainingWindowControllers means two engine.pause() calls and
        # two stacked hotkey event taps — see CLAUDE.md's threading model).
        self.window_owner = None

        dispatcher = BridgeDispatcher()
        dispatcher.register("app.getState", self._get_state)
        dispatcher.register("app.beginMeeting", self._begin_meeting)
        dispatcher.register("app.endMeeting", self._end_meeting)
        dispatcher.register("app.openWindow", self._open_window)
        dispatcher.register("app.quit", self._quit)
        self._web = WebWindow("Speakeasy", 360, 300, "dock", dispatcher)
        self._web.window.setDelegate_(self)
        return self

    # -- lifecycle -----------------------------------------------------------

    def show(self):
        self._push_state()
        self._web.show()

    def windowWillClose_(self, notification):
        pass  # closing hides the window; the engine keeps running

    # -- engine callbacks (arrive on main thread via performSelector) ---------

    def engineStateChanged_(self, state_value):
        if State(str(state_value)) is not State.MEETING_PROCESSING:
            self._progress_text = None
        self._push_state()

    def meetingProgress_(self, text):
        self._progress_text = str(text)
        self._push_state()

    def meetingSaved_(self, meeting_id):
        self._push_state()
        # With window_owner set, self.meetings_window stays None forever and
        # this is a no-op — the owner (StatusItemController) forwards to its
        # own cached meetings window instead, which is now the authoritative
        # one. Kept for the no-owner fallback path.
        if self.meetings_window is not None:
            self.meetings_window.meetingSaved_(meeting_id)

    # -- bridge handlers (main thread) ----------------------------------------

    @objc.python_method
    def _state_payload(self):
        state = self.engine.state
        elapsed = 0.0
        recorder = getattr(self.engine, "meeting_recorder", None)
        if state is State.MEETING_RECORDING and recorder is not None:
            elapsed = float(recorder.elapsed_seconds)
        profile = self.engine.profile
        return {
            "mode": state.value,
            "profileName": profile.name if profile else "Guest",
            "elapsedSeconds": elapsed,
            "progressText": self._progress_text,
        }

    @objc.python_method
    def _push_state(self):
        self._web.emit("state", self._state_payload())

    @objc.python_method
    def _get_state(self, params, respond):
        respond(self._state_payload())

    @objc.python_method
    def _begin_meeting(self, params, respond):
        self.engine.begin_meeting()  # submits to control internally
        respond(True)

    @objc.python_method
    def _end_meeting(self, params, respond):
        self.engine.end_meeting()
        respond(True)

    @objc.python_method
    def _open_window(self, params, respond):
        name = params.get("name")
        if self.window_owner is not None:
            # StatusItemController is the single owner of meetings/training
            # windows once set (see menubar.py); delegate rather than
            # building a second, competing instance here.
            if name == "meetings":
                self.window_owner.openMeetings_(None)
            elif name == "training":
                self.window_owner.openTraining_(None)
            respond(True)
            return
        # Fallback for when no owner is set (e.g. tests instantiating this
        # controller standalone): keep the old local-cache behavior.
        if name == "meetings":
            if self.meetings_window is None:
                from speakeasy.ui.meetings_window import MeetingsWindowController

                self.meetings_window = MeetingsWindowController.alloc().init()
            self.meetings_window.show()
        elif name == "training":
            # Matches the old openTraining_ guard exactly: profile is the
            # only hard gate. (engine.transcriber is None only dims the
            # native button; it was never a hard block on opening.)
            if self.engine.profile is not None:
                if self.training_window is None:
                    from speakeasy.ui.training_window import TrainingWindowController

                    self.training_window = TrainingWindowController.alloc().initWithEngine_(
                        self.engine
                    )
                self.training_window.showForProfile_(self.engine.profile)
        respond(True)

    @objc.python_method
    def _quit(self, params, respond):
        respond(True)
        from AppKit import NSApplication

        print("\nShutting down.")
        self.engine.shutdown()
        NSApplication.sharedApplication().terminate_(None)
