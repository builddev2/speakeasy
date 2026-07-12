"""Meetings transcript browser — WKWebView hosting frontend meetings.html."""

from datetime import datetime

import objc
from Foundation import NSObject

from speakeasy import meetings
from speakeasy.ui.webbridge import BridgeDispatcher, segments_to_lines
from speakeasy.ui.webwindow import WebWindow


def _meeting_meta(meeting) -> dict:
    created = meeting.created
    if isinstance(created, str):
        created = datetime.fromisoformat(created)
    minutes = max(1, round(meeting.duration_seconds / 60))
    speaker_count = len({segment.speaker for segment in meeting.segments})
    speakers = "speaker" if speaker_count == 1 else "speakers"
    return {
        "id": meeting.meeting_id,
        "title": meeting.title,
        "subtitle": f"{minutes} min · {speaker_count} {speakers}",
        "date": created.strftime("%b %-d, %Y · %H:%M"),
        "duration": f"{minutes} min",
        "speakerCount": speaker_count,
    }


class MeetingsWindowController(NSObject):
    def init(self):
        self = objc.super(MeetingsWindowController, self).init()
        if self is None:
            return None
        dispatcher = BridgeDispatcher()
        dispatcher.register("meetings.list", self._list)
        dispatcher.register("meetings.get", self._get)
        dispatcher.register("meetings.rename", self._rename)
        dispatcher.register("meetings.delete", self._delete)
        dispatcher.register("meetings.copy", self._copy)
        dispatcher.register("meetings.export", self._export)
        self._web = WebWindow("Meetings", 720, 480, "meetings", dispatcher)
        self._web.window.setDelegate_(self)
        return self

    def show(self):
        self._web.emit("meetings.changed")  # page re-lists (old reload()-on-show)
        self._web.show()

    def windowWillClose_(self, notification):
        pass

    def meetingSaved_(self, meeting_id):
        self._web.emit("meetings.changed")

    # -- bridge handlers (main thread; store I/O is small local JSON,
    #    same main-thread pattern the old native window used) ----------------

    @objc.python_method
    def _list(self, params, respond):
        respond([_meeting_meta(m) for m in meetings.list_meetings()])

    @objc.python_method
    def _load(self, params):
        return meetings.Meeting.load(str(params.get("id", "")))

    @objc.python_method
    def _get(self, params, respond):
        meeting = self._load(params)
        detail = _meeting_meta(meeting)
        detail["lines"] = segments_to_lines(meeting.segments)
        respond(detail)

    @objc.python_method
    def _rename(self, params, respond):
        meeting = self._load(params)
        meeting.rename(str(params.get("title", "")))
        self._list({}, respond)

    @objc.python_method
    def _delete(self, params, respond):
        self._load(params).delete()
        self._list({}, respond)

    @objc.python_method
    def _copy(self, params, respond):
        from speakeasy import injector

        injector.set_clipboard(meetings.render_txt(self._load(params)))
        respond(True)

    @objc.python_method
    def _export(self, params, respond):
        from AppKit import NSModalResponseOK, NSSavePanel

        meeting = self._load(params)
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
