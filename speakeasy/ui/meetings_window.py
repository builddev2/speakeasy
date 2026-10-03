"""Meetings library browser — WKWebView hosting frontend meetings.html."""

import objc
from Foundation import NSObject

from speakeasy import meeting_export, meetings
from speakeasy.meeting_library import utc_iso
from speakeasy.ui.meetings_bridge import MeetingsBridge
from speakeasy.ui.webbridge import BridgeDispatcher
from speakeasy.ui.webwindow import WebWindow

_MIN_SIZE = (820, 520)
_SCREEN_FRACTION = 0.9
# Restores the user's last size/position across launches (NSUserDefaults).
_AUTOSAVE_NAME = "SpeakeasyMeetingsWindow"


def default_content_size(visible_width, visible_height):
    """First-launch size: most of the screen, never below the minimum."""
    return (
        max(_MIN_SIZE[0], round(visible_width * _SCREEN_FRACTION)),
        max(_MIN_SIZE[1], round(visible_height * _SCREEN_FRACTION)),
    )


class MeetingsWindowController(NSObject):
    def init(self):
        self = objc.super(MeetingsWindowController, self).init()
        if self is None:
            return None
        from AppKit import NSWorkspace
        from Foundation import NSURL

        from speakeasy import injector
        from speakeasy.ui import services

        engine = services.engine

        def recording_event_key():
            # Read once: the control thread can clear meeting_event between
            # two reads, and the bridge doesn't catch AttributeError.
            event = engine.meeting_event if engine is not None else None
            return event.event_key if event is not None else None

        def recording_info():
            # Read-only view of the engine for the notepad; engine.py is not
            # changed. _meeting_started_at is private, so read it defensively;
            # the page falls back to the time it first saw recording on.
            if engine is None:
                return None
            state = getattr(engine.state, "value", "")
            event = engine.meeting_event
            started = getattr(engine, "_meeting_started_at", None)
            return {"recording": state == "meeting_recording",
                    "processing": state == "meeting_processing",
                    "startedAt": utc_iso(started) if started is not None else None,
                    "title": event.title if event is not None else None}

        # copyText and copy both live on MeetingsBridge itself (pure-Python,
        # unit-tested); only the clipboard write is injected here.
        self._bridge = MeetingsBridge(
            set_clipboard=injector.set_clipboard, open_path=self._open_path,
            calendar=services.calendar_sync,
            begin_meeting=engine.begin_meeting if engine is not None else None,
            recording_event_key=recording_event_key,
            recording_info=recording_info,
            open_url=lambda url: NSWorkspace.sharedWorkspace().openURL_(
                NSURL.URLWithString_(url)),
        )
        self._poll_timer = None
        dispatcher = BridgeDispatcher()
        self._bridge.register(dispatcher)
        dispatcher.register("meetings.export", self._export)
        from AppKit import NSScreen

        visible = NSScreen.mainScreen().visibleFrame().size
        width, height = default_content_size(visible.width, visible.height)
        self._web = WebWindow(
            "Meetings", width, height, "meetings", dispatcher,
            resizable=True, min_size=_MIN_SIZE,
        )
        # Overrides the centred default frame when a saved one exists.
        self._web.window.setFrameAutosaveName_(_AUTOSAVE_NAME)
        self._web.window.setDelegate_(self)
        return self

    def show(self):
        self._bridge.poll_changed()  # baseline; the emit below re-lists anyway
        self._web.emit("meetings.changed")  # page re-lists (old reload()-on-show)
        self._web.show()
        if self._poll_timer is None:
            from Foundation import NSTimer
            # MCP writes come from another process (Claude's), so nothing in
            # this app hears about them; poll a cheap marker while visible.
            self._poll_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                10.0, self, "pollLibrary:", None, True)

    def pollLibrary_(self, timer):
        if self._web.window.isVisible() and self._bridge.poll_changed():
            self._web.emit("meetings.changed")

    def windowWillClose_(self, notification):
        if self._poll_timer is not None:
            self._poll_timer.invalidate()
            self._poll_timer = None
        self._bridge.stop_polling()

    @objc.python_method
    def _open_path(self, path):
        from AppKit import NSWorkspace
        from Foundation import NSURL
        NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(path)))

    def meetingSaved_(self, meeting_id):
        self._web.emit("meetings.changed")
        self._web.emit("meetings.saved", {"id": str(meeting_id)})

    def calendarChanged_(self, _):
        self._web.emit("calendar.changed")

    def libraryStatus_(self, payload):
        # apply_status_json (MeetingsBridge, tested there) decides what to
        # parse/store and which events to emit; this stays thin ObjC glue.
        for event, data in self._bridge.apply_status_json(str(payload)):
            self._web.emit(event, data)

    # -- Export: not part of MeetingsBridge (it drives an AppKit save panel
    #    and responds asynchronously from the panel's completion handler, so
    #    it can't be registered through `register()`/`_wrap` like the rest).
    #    meetings.copy has no such constraint and is a plain MeetingsBridge
    #    method instead — see meetings_bridge.py. --------------------------

    @objc.python_method
    def _export(self, params, respond):
        from AppKit import NSModalResponseOK, NSSavePanel

        # Runs the not_found lookup through the same `_wrap` conversion
        # every other handler gets (tested in test_meetings_bridge.py),
        # rather than a bespoke try/except duplicating that logic here.
        lookup: dict = {}

        def capture_lookup(result=None, error=None):
            lookup["result"] = result
            lookup["error"] = error

        MeetingsBridge._wrap(self._bridge.export_meeting)(params, capture_lookup)
        if lookup.get("error") is not None:
            respond(error=lookup["error"])
            return
        meeting = lookup["result"]
        panel = NSSavePanel.savePanel()
        # UTType via lookUpClass: AppKit already loads the system framework,
        # and the pyobjc UniformTypeIdentifiers wrapper isn't a pinned dep.
        UTType = objc.lookUpClass("UTType")
        panel.setAllowedContentTypes_(
            [
                UTType.typeWithFilenameExtension_("txt"),
                UTType.typeWithFilenameExtension_("md"),
            ]
        )
        panel.setNameFieldStringValue_(f"{meeting.title}.txt")

        def completion(response):
            try:
                if response == NSModalResponseOK:
                    path = str(panel.URL().path())
                    render = (meeting_export.render_export_md if path.endswith(".md")
                              else meetings.render_txt)
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(render(meeting))
            except OSError as exc:
                print(f"Meeting export failed: {exc}")
            finally:
                respond(True)

        panel.beginSheetModalForWindow_completionHandler_(self._web.window, completion)
