"""Meetings library browser — WKWebView hosting frontend meetings.html."""

import objc
from Foundation import NSObject

from speakeasy import meetings
from speakeasy.meeting_library import MeetingNotFound
from speakeasy.ui.meetings_bridge import MeetingsBridge
from speakeasy.ui.webbridge import BridgeDispatcher
from speakeasy.ui.webwindow import WebWindow


class MeetingsWindowController(NSObject):
    def init(self):
        self = objc.super(MeetingsWindowController, self).init()
        if self is None:
            return None
        from speakeasy import injector

        # copyText lives on MeetingsBridge itself (pure-Python, unit-tested);
        # only the clipboard write is injected here.
        self._bridge = MeetingsBridge(set_clipboard=injector.set_clipboard)
        dispatcher = BridgeDispatcher()
        self._bridge.register(dispatcher)
        dispatcher.register("meetings.copy", self._copy)
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

    # -- Copy/Export: not part of MeetingsBridge (they touch the clipboard
    #    and AppKit save panels, so they stay here with the rest of the
    #    ObjC window glue) --------------------------------------------------

    @objc.python_method
    def _copy(self, params, respond):
        from speakeasy import injector

        try:
            meeting = self._bridge.library.get_meeting(str(params.get("id", "")))
        except MeetingNotFound:
            respond(error="not_found")
            return
        injector.set_clipboard(meetings.render_txt(meeting))
        respond(True)

    @objc.python_method
    def _export(self, params, respond):
        from AppKit import NSModalResponseOK, NSSavePanel

        try:
            meeting = self._bridge.library.get_meeting(str(params.get("id", "")))
        except MeetingNotFound:
            respond(error="not_found")
            return
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
