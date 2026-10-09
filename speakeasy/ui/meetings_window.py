"""Meetings library browser — WKWebView hosting frontend meetings.html."""

import objc
import sqlite3
import threading
import tempfile
from datetime import datetime
from pathlib import Path
from Foundation import NSObject

from speakeasy import library_backup, meeting_export, meetings, settings
from speakeasy.meeting_library import utc_iso
from speakeasy.ui import login_item
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
        self._engine = engine
        self._calendar = services.calendar_sync

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
                    "title": event.title if event is not None else None,
                    "mode": state,
                    "micFailure": getattr(engine.recorder, "last_failure", None),
                    "startError": engine.meeting_start_error,
                    "processingError": engine.meeting_processing_error}

        # copyText and copy both live on MeetingsBridge itself (pure-Python,
        # unit-tested); only the clipboard write is injected here.
        self._bridge = MeetingsBridge(
            set_clipboard=injector.set_clipboard, open_path=self._open_path,
            calendar=services.calendar_sync,
            begin_meeting=engine.begin_meeting if engine is not None else None,
            retry_microphone=engine.retry_microphone if engine is not None else None,
            recording_event_key=recording_event_key,
            recording_info=recording_info,
            login_status=login_item.status, set_login=login_item.set_enabled,
            open_url=lambda url: NSWorkspace.sharedWorkspace().openURL_(
                NSURL.URLWithString_(url)),
        )
        self._poll_timer = None
        self._bulk_export_busy = False
        self._last_export = None
        self._last_backup = None
        self._maintenance_busy = False
        self._restore_temp = None
        self._restore_candidate = None
        self._restore_expected_count = None
        dispatcher = BridgeDispatcher(guard=lambda method: not self._maintenance_busy)
        self._bridge.register(dispatcher)
        dispatcher.register("meetings.export", self._export)
        dispatcher.register("library.exportAll", self._export_all)
        dispatcher.register("library.revealExport", self._reveal_export)
        dispatcher.register("library.backup", self._backup)
        dispatcher.register("library.revealBackup", self._reveal_backup)
        dispatcher.register("library.restore", self._restore)
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

    def show(self, view=None):
        if view is not None:
            self._bridge.set_navigation(view)
        if not self._maintenance_busy:
            self._bridge.poll_changed()  # baseline; the emit below re-lists anyway
            self._web.emit("meetings.changed")  # page re-lists (old reload()-on-show)
        if view is not None:
            self._web.emit("meetings.navigate")
        self._web.show()
        if self._poll_timer is None:
            from Foundation import NSTimer
            # MCP writes come from another process (Claude's), so nothing in
            # this app hears about them; poll a cheap marker while visible.
            self._poll_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                10.0, self, "pollLibrary:", None, True)

    def pollLibrary_(self, timer):
        if not self._maintenance_busy and self._web.window.isVisible() and self._bridge.poll_changed():
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

    @objc.python_method
    def _export_all(self, params, respond):
        from AppKit import NSModalResponseOK, NSOpenPanel

        if self._bulk_export_busy:
            respond(error="export_busy")
            return
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(False)
        panel.setCanCreateDirectories_(True)
        panel.setPrompt_("Choose Folder")

        def completion(response):
            if response != NSModalResponseOK:
                respond({"status": "cancelled"})
                return
            self._bulk_export_busy = True
            parent = str(panel.URL().path())

            def run():
                try:
                    path, count = meeting_export.export_snapshot(parent, self._bridge.library)
                    result = ({"status": "complete", "count": count, "path": str(path)}, None)
                except Exception as exc:
                    print(f"Meeting export failed: {type(exc).__name__}")
                    result = (None, "export_failed")
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    b"bulkExportFinished:", (respond, *result), False)

            threading.Thread(target=run, name="library-export", daemon=True).start()

        panel.beginSheetModalForWindow_completionHandler_(self._web.window, completion)

    def bulkExportFinished_(self, payload):
        respond, result, error = payload
        self._bulk_export_busy = False
        self._last_export = result["path"] if result is not None else None
        respond(result, error=error)

    @objc.python_method
    def _reveal_export(self, params, respond):
        if self._last_export is None:
            respond(error="no_export")
            return
        self._open_path(self._last_export)
        respond(True)

    @objc.python_method
    def _backup(self, params, respond):
        from AppKit import NSModalResponseOK, NSSavePanel

        if self._bulk_export_busy or self._maintenance_busy:
            respond(error="library_busy")
            return
        panel = NSSavePanel.savePanel()
        panel.setAllowedContentTypes_([
            objc.lookUpClass("UTType").typeWithFilenameExtension_("speakeasy-library")])
        panel.setNameFieldStringValue_(
            f"Speakeasy library {datetime.now():%Y-%m-%d}"
            f"{library_backup.EXTENSION}")

        def completion(response):
            if response != NSModalResponseOK:
                respond({"status": "cancelled"})
                return
            path = str(panel.URL().path())
            self._bulk_export_busy = True

            def run():
                try:
                    count = library_backup.backup_library(path)
                    result = ({"status": "complete", "count": count, "path": path}, None)
                except Exception as exc:
                    print(f"Library backup failed: {type(exc).__name__}")
                    result = (None, "backup_failed")
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    b"libraryBackupFinished:", (respond, *result), False)

            threading.Thread(target=run, name="library-backup", daemon=True).start()

        panel.beginSheetModalForWindow_completionHandler_(self._web.window, completion)

    def libraryBackupFinished_(self, payload):
        respond, result, error = payload
        self._bulk_export_busy = False
        if result is not None:
            self._last_backup = result["path"]
        respond(result, error=error)

    @objc.python_method
    def _reveal_backup(self, params, respond):
        if self._last_backup is None:
            respond(error="no_backup")
            return
        self._open_path(self._last_backup)
        respond(True)

    @objc.python_method
    def _restore(self, params, respond):
        from AppKit import NSModalResponseOK, NSOpenPanel

        if self._bulk_export_busy or self._maintenance_busy:
            respond(error="library_busy")
            return
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(False)
        panel.setAllowedContentTypes_([
            objc.lookUpClass("UTType").typeWithFilenameExtension_("speakeasy-library")])

        def completion(response):
            if response != NSModalResponseOK:
                respond({"status": "cancelled"})
                return
            path = str(panel.URL().path())
            self._bulk_export_busy = True
            self._restore_temp = tempfile.TemporaryDirectory(prefix="speakeasy-restore-ui-")
            self._restore_candidate = Path(self._restore_temp.name) / "candidate.sqlite"

            def check():
                try:
                    count = library_backup.prepare_backup(path, self._restore_candidate)
                    result = (respond, path, count, None)
                except Exception as exc:
                    print(f"Library restore validation failed: {type(exc).__name__}")
                    result = (respond, path, None, "invalid_backup")
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    b"libraryRestoreChecked:", result, False)

            threading.Thread(target=check, name="library-restore-check", daemon=True).start()

        panel.beginSheetModalForWindow_completionHandler_(self._web.window, completion)

    def libraryRestoreChecked_(self, payload):
        from AppKit import NSAlert, NSAlertFirstButtonReturn

        respond, path, count, error = payload
        if error is not None:
            self._bulk_export_busy = False
            self._clear_restore_candidate()
            respond(error=error)
            return
        if self._engine is None or self._engine.state.value != "ready":
            self._bulk_export_busy = False
            self._clear_restore_candidate()
            respond(error="recording_in_progress")
            return
        try:
            current = self._bridge.library.count_meetings()
        except Exception as exc:
            print(f"Current library check failed: {type(exc).__name__}")
            self._bulk_export_busy = False
            self._clear_restore_candidate()
            respond(error="current_unhealthy")
            return
        self._restore_expected_count = count
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Restore meeting library?")
        alert.setInformativeText_(
            f"The backup has {count} meetings and will replace the current library "
            f"of {current} meetings. A verified recovery copy will be saved in "
            f"{settings.library_path().parent} before replacement.")
        alert.addButtonWithTitle_("Restore")
        alert.addButtonWithTitle_("Cancel")

        def decided(choice):
            if choice != NSAlertFirstButtonReturn:
                self._bulk_export_busy = False
                self._clear_restore_candidate()
                respond({"status": "cancelled"})
                return
            self._start_restore(path, respond)

        alert.beginSheetModalForWindow_completionHandler_(self._web.window, decided)

    @objc.python_method
    def _start_restore(self, path, respond):
        try:
            was_paused = self._engine.begin_library_maintenance()
        except RuntimeError:
            self._bulk_export_busy = False
            self._clear_restore_candidate()
            respond(error="recording_in_progress")
            return
        self._maintenance_busy = True
        self._bridge.stop_polling()

        def run():
            calendar_attempted = False
            fatal = False
            try:
                self._engine.control.submit(lambda: None).result(timeout=30)
                if not self._engine.control.submit(
                        self._engine.library_maintenance_idle).result(timeout=30):
                    raise RuntimeError("Engine did not become idle.")
                self._engine.worker.submit(lambda: None).result(timeout=30)
                if self._calendar is not None:
                    calendar_attempted = True
                    self._calendar.pause_for_maintenance()
                count, recovery = library_backup.restore_prepared(
                    self._restore_candidate, expected_count=self._restore_expected_count)
                result = ({"status": "complete", "count": count,
                           "recovery": str(recovery)}, None)
            except library_backup.RestoreIndeterminate as exc:
                fatal = True
                result = ({"status": "needs_recovery", "recovery": str(exc.recovery),
                           "markerPersisted": exc.marker_persisted}, None)
            except BlockingIOError:
                result = (None, "library_in_use")
            except sqlite3.OperationalError as exc:
                result = (None, "library_in_use" if "locked" in str(exc).lower()
                          else "restore_failed")
            except library_backup.InvalidLibrary:
                result = (None, "current_unhealthy")
            except Exception as exc:
                print(f"Library restore failed: {type(exc).__name__}")
                result = (None, "restore_failed")
            finally:
                if calendar_attempted and not fatal:
                    try:
                        self._calendar.resume_after_maintenance()
                    except Exception as exc:
                        print(f"Calendar resume failed: {type(exc).__name__}")
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"libraryRestoreFinished:", (respond, was_paused, fatal, *result), False)

        threading.Thread(target=run, name="library-restore", daemon=True).start()

    def libraryRestoreFinished_(self, payload):
        respond, was_paused, fatal, result, error = payload
        self._bulk_export_busy = False
        self._clear_restore_candidate()
        if not fatal:
            self._engine.end_library_maintenance(was_paused)
            self._maintenance_busy = False
            self._bridge.poll_changed()
        if result is not None and not fatal:
            self._web.emit("meetings.changed")
            self._web.emit("calendar.changed")
        respond(result, error=error)

    @objc.python_method
    def _clear_restore_candidate(self):
        if self._restore_temp is not None:
            self._restore_temp.cleanup()
        self._restore_temp = None
        self._restore_candidate = None
        self._restore_expected_count = None
