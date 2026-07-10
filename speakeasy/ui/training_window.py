"""Voice-training window — WKWebView hosting frontend training.html.

Lifecycle is identical to the old native window: opening pauses dictation
and arms a _TakeRecorder; closing tears it down and resumes. Session and
practice flows run on a background thread driving a TrainingUI adapter
whose callbacks hop to the main thread and become bridge events.
"""

import threading

import objc
from Foundation import NSObject

from .. import training, training_content
from ..engine import play_sound
from .webbridge import BridgeDispatcher
from .webwindow import WebWindow

_W, _H = 640, 440


class _Cancelled(Exception):
    """Raised on the session thread when the window closes mid-session."""


class _WebTrainingUI(training.TrainingUI):
    """TrainingUI adapter: marshals callbacks to the main thread, then emits
    bridge events. controller is the TrainingWindowController (NSObject).

    Strings below are lifted verbatim from the old native _WindowUI adapter.
    """

    def __init__(self, controller):
        self._c = controller

    def _feedback(self, line):
        self._c.performSelectorOnMainThread_withObject_waitUntilDone_(
            b"uiFeedback:", line, False
        )

    def show_prompt(self, text, hotkey_name):
        self._c.performSelectorOnMainThread_withObject_waitUntilDone_(
            b"uiPrompt:",
            {
                "text": text,
                "status": f"Hold [{hotkey_name}], read the line aloud, release when done.",
            },
            False,
        )

    def nothing_heard(self):
        self._feedback("Nothing heard — try again.")

    def heard_correctly(self):
        self._feedback("✓ Heard it correctly")

    def correction_saved(self, heard_span, target):
        self._feedback(f'✗ Heard “{heard_span}” — saved correction → “{target}”')

    def correction_conflict(self, heard_span, target):
        self._feedback(f'✗ Heard “{heard_span}” — not saved (would conflict)')

    def session_started(self, name):
        pass  # the sidebar selection already shows it

    def session_finished(self, name, saved):
        self._c.performSelectorOnMainThread_withObject_waitUntilDone_(
            b"uiDone:", f"Session complete — {saved} correction(s) learned.", False
        )


class TrainingWindowController(NSObject):
    def initWithEngine_(self, engine):
        self = objc.super(TrainingWindowController, self).init()
        if self is None:
            return None
        self.engine = engine
        self.profile = None
        self._taker = None
        self._busy = False
        self._cancelled = False
        dispatcher = BridgeDispatcher()
        dispatcher.register("training.listSessions", self._list_sessions)
        dispatcher.register("training.startSession", self._start_session)
        dispatcher.register("training.practice", self._practice)
        self._web = WebWindow("Training", _W, _H, "training", dispatcher)
        self._web.window.setDelegate_(self)
        return self

    # -- open / close ---------------------------------------------------------

    def showForProfile_(self, profile):
        if profile is None or self.engine.transcriber is None:
            return
        self.profile = profile
        if self._taker is None:
            # The window owns the hotkey while open; dictation is suspended.
            self.engine.pause()
            self._taker = training._TakeRecorder(
                self.engine.recorder,
                self.engine.transcriber,
                self.engine.worker,
                self.engine.control,
                play_sound,
            )
            self._taker.__enter__()
        self._cancelled = False
        self._push_sessions()
        self._web.show()

    def windowWillClose_(self, notification):
        self._cancelled = True
        if self._taker is not None:
            self._taker.unblock()  # free a session thread waiting on a take
            self._taker.__exit__(None, None, None)
            self._taker = None
        self.engine.resume()
        self._busy = False

    # -- main-thread UI event relays -------------------------------------------

    def uiPrompt_(self, payload):
        self._web.emit("training.prompt", dict(payload))

    def uiFeedback_(self, line):
        self._web.emit("training.feedback", {"line": str(line)})

    def uiDone_(self, message):
        self._busy = False
        self._web.emit("training.done", {"message": str(message)})
        self._push_sessions()

    # -- bridge handlers --------------------------------------------------------

    @objc.python_method
    def _sessions_payload(self):
        done = set(self.profile.sessions_done) if self.profile else set()
        suggested = training._suggested_index(self.profile) if self.profile else None
        sessions = []
        for index, session in enumerate(training_content.SESSIONS):
            sessions.append(
                {
                    "name": session["name"],
                    "done": session["name"] in done,
                    "suggested": index == suggested,
                }
            )
        return {
            "sessions": sessions,
            "profileName": self.profile.name if self.profile else "Guest",
        }

    @objc.python_method
    def _push_sessions(self):
        self._web.emit("training.sessions", self._sessions_payload())

    @objc.python_method
    def _list_sessions(self, params, respond):
        respond(self._sessions_payload())

    @objc.python_method
    def _record(self, taker):
        # LIFT: matches the old _start_run's record() closure exactly — the
        # taker is captured once per run so a late-arriving take after the
        # window closes still raises instead of touching a torn-down taker.
        heard = taker.record()
        if self._cancelled:
            raise _Cancelled()
        return heard

    @objc.python_method
    def _hotkey_name(self):
        from .. import config

        return config.hotkey_name()

    @objc.python_method
    def _run(self, work, announce_done):
        # Not in the brief verbatim: the old window always re-enabled its
        # controls in _start_run's finally regardless of which flow ran,
        # but only _run_session's ui.session_finished ever fired a "done"
        # message. _train_prompt (practice) never calls it, so without an
        # explicit announce here the web UI's busy flag would never clear
        # after a practice run — announce_done makes that flow call the
        # same session_finished relay _run_session already gets for free.
        taker = self._taker
        ui = _WebTrainingUI(self)

        def record():
            return self._record(taker)

        def run():
            try:
                saved = work(ui, record)
                if announce_done:
                    ui.session_finished(None, saved)
            except _Cancelled:
                pass

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

    @objc.python_method
    def _start_session(self, params, respond):
        name = str(params.get("name", ""))
        session = next(
            (s for s in training_content.SESSIONS if s["name"] == name), None
        )
        if session is None or self._busy or self._taker is None:
            respond(error="cannot start session")
            return
        self._busy = True
        self._run(
            lambda ui, record: training._run_session(
                session, self.profile, record, self._hotkey_name(), ui
            ),
            announce_done=False,
        )
        respond(True)

    @objc.python_method
    def _practice(self, params, respond):
        term = str(params.get("text", "")).strip()
        if term == "" or self._busy or self._taker is None:
            respond(error="cannot start practice")
            return
        self._busy = True
        self.profile.add_word(term)
        self._run(
            lambda ui, record: training._train_prompt(
                term, [term], self.profile, record, self._hotkey_name(), ui
            ),
            announce_done=True,
        )
        respond(True)
