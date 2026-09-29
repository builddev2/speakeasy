"""Meetings library browser — WKWebView hosting frontend meetings.html."""

import objc
from Foundation import NSObject

from speakeasy import meetings
from speakeasy.ui.meetings_bridge import MeetingsBridge
from speakeasy.ui.webbridge import BridgeDispatcher
from speakeasy.ui.webwindow import WebWindow


class MeetingsWindowController(NSObject):
    def init(self):
        self = objc.super(MeetingsWindowController, self).init()
        if self is None:
            return None
        from speakeasy import injector

        # copyText and copy both live on MeetingsBridge itself (pure-Python,
        # unit-tested); only the clipboard write is injected here.
        self._bridge = MeetingsBridge(set_clipboard=injector.set_clipboard)
        dispatcher = BridgeDispatcher()
        self._bridge.register(dispatcher)
        dispatcher.register("meetings.export", self._export)
        self._web = WebWindow(
            "Meetings", 1040, 660, "meetings", dispatcher,
            resizable=True, min_size=(820, 520),
        )
        self._web.window.setDelegate_(self)
        return self

    def show(self):
        self._web.emit("meetings.changed")  # page re-lists (old reload()-on-show)
        self._web.show()

    def windowWillClose_(self, notification):
        pass

    def meetingSaved_(self, meeting_id):
        self._web.emit("meetings.changed")

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
                    render = meetings.render_md if path.endswith(".md") else meetings.render_txt
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(render(meeting))
            except OSError as exc:
                print(f"Meeting export failed: {exc}")
            finally:
                respond(True)

        panel.beginSheetModalForWindow_completionHandler_(self._web.window, completion)
