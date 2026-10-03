"""Main-thread glue for the recording pill: follows engine state (never the
start path), draws pill_model's view on PillPanel and turns clicks into
engine calls or Meetings navigation."""

import objc
from Foundation import NSObject, NSTimer

from .. import settings
from .pill_model import (PillMachine, audio_indicator, clamp_origin, default_origin,
                         drawer_rows, pill_view)

SIZE = (360.0, 36.0)
SAVED_SECONDS = 6.0


def _make_panel(target):
    from .pill_panel import PillPanel
    return PillPanel.alloc().initWithTarget_(target)


def _screens():
    from AppKit import NSScreen
    return [(s.visibleFrame().origin.x, s.visibleFrame().origin.y,
             s.visibleFrame().size.width, s.visibleFrame().size.height)
            for s in NSScreen.screens()]


def _mouse_screen():
    from AppKit import NSEvent, NSMouseInRect, NSScreen
    point = NSEvent.mouseLocation()
    for s in NSScreen.screens():
        if NSMouseInRect(point, s.frame(), False):
            f = s.visibleFrame()
            return (f.origin.x, f.origin.y, f.size.width, f.size.height)
    return _screens()[0]


def _today_events():
    """Today's calendar chips, or None when Calendar isn't connected."""
    from datetime import datetime

    from ..meeting_library import MeetingLibrary
    from . import calendar_payloads, services
    sync = services.calendar_sync
    if sync is None or sync.access() != "connected":
        return None
    now = datetime.now().astimezone()
    day = now.date().isoformat()
    return calendar_payloads.day_chips(MeetingLibrary().calendar_events_between(day, day), now.tzinfo)


def _start_timer(target, seconds, selector, repeats):
    return NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        seconds, target, selector, None, repeats)


class PillController(NSObject):
    def initWithEngine_opener_(self, engine, opener):
        self = objc.super(PillController, self).init()
        if self is None:
            return None
        self.engine, self.opener = engine, opener
        self.machine = PillMachine()
        self.panel = _make_panel(self)
        self._progress = None
        self._ticker = None
        self._saved_timer = None
        self._shown = False
        self._drawer_open = False
        self._auto_opened = False
        return self

    # -- engine callbacks (main thread) ----------------------------------
    def engineStateChanged_(self, value):
        was = self.machine.phase
        self.machine.on_state(str(value), self.engine.meeting_processing_error)
        if self.machine.phase == "recording" and was != "recording":
            self._auto_opened = self._drawer_open = False
            self._progress = None
        self._render()

    def meetingProgress_(self, text):
        self._progress = str(text)
        self._render()

    def meetingSaved_(self, meeting_id):
        self.machine.on_saved(str(meeting_id))
        self._render()

    def tick_(self, timer):
        if self.machine.phase == "recording" and not self._auto_opened:
            rec, health = self._recorder()
            if audio_indicator(health, rec.get("capture_mode", ""),
                               rec.get("elapsed", 0.0)) == "warning":
                self._auto_opened = self._drawer_open = True
        self._render()

    def savedExpired_(self, timer):
        self._saved_timer = None
        self.machine.expire_saved()
        self._render()

    # -- buttons ---------------------------------------------------------
    def pillNotes_(self, sender):
        self.opener("recording")
        self._render()

    def pillStop_(self, sender):
        self.engine.end_meeting()
        self._render()

    def pillCancel_(self, sender):
        self.machine.cancel()
        self._render()

    def pillKeep_(self, sender):
        self.machine.keep()
        self._render()

    def pillDiscard_(self, sender):
        self.machine.discard()
        self.engine.cancel_meeting_processing()
        self._render()

    def pillOpen_(self, sender):
        meeting_id = self.machine.saved_id
        self.opener({"meeting": meeting_id})
        self.machine.close()
        self._render()

    def pillOpenMeetings_(self, sender):
        self.opener(None)
        self._render()

    def pillClose_(self, sender):
        self.machine.close()
        self._render()

    def pillBodyClicked_(self, sender):
        if self.machine.phase == "recording":
            self._drawer_open = not self._drawer_open
        self._render()

    def drawerClose_(self, sender):
        self._drawer_open = False
        self._render()

    def pillMovedTo_(self, pair):
        try:
            settings.set_pill_origin(float(pair[0]), float(pair[1]))
        except (ValueError, TypeError, IndexError):
            pass

    def drawerEventClicked_(self, sender):
        chips = _today_events()
        if not chips:
            return
        event = getattr(self.engine, "meeting_event", None)
        linked = getattr(event, "event_key", None) if event else None
        items = [(f"{c['time']} · {c['title']}", c["key"], c["key"] == linked) for c in chips]
        if linked:
            items.append(("Unlink", None, False))
        self.panel.event_menu(items)

    def drawerEventChosen_(self, item):
        self.engine.link_meeting_event(item.representedObject())
        self._render()

    @objc.python_method
    def shutdown(self):
        self._stop_ticker()
        self._stop_saved_timer()
        self.panel.hide()
        self._shown = False

    # -- internals -------------------------------------------------------
    @objc.python_method
    def _recorder(self):
        rec = getattr(self.engine, "meeting_recorder", None)
        health = getattr(rec, "health", None)
        return ({"elapsed": getattr(rec, "elapsed_seconds", 0.0) or 0.0,
                 "capture_mode": getattr(rec, "capture_mode", "") or "",
                 "status": getattr(rec, "system_audio_status", "") or ""},
                health.to_dict() if health else {})

    @objc.python_method
    def _origin(self):
        saved = settings.get_pill_origin()
        if saved and clamp_origin(saved, SIZE, _screens()):
            return saved
        return default_origin(_mouse_screen(), SIZE)

    @objc.python_method
    def _stop_ticker(self):
        if self._ticker is not None:
            self._ticker.invalidate()
            self._ticker = None

    @objc.python_method
    def _stop_saved_timer(self):
        if self._saved_timer is not None:
            self._saved_timer.invalidate()
            self._saved_timer = None

    @objc.python_method
    def _render(self):
        phase = self.machine.phase
        if phase == "hidden":
            self._stop_ticker()
            self._stop_saved_timer()
            self.panel.hide()
            self._shown = False
            return
        rec, health = self._recorder()
        event = getattr(self.engine, "meeting_event", None)
        title = getattr(event, "title", None) if event else None
        view = pill_view(self.machine, title=title, elapsed=rec["elapsed"], health=health,
                         capture_mode=rec["capture_mode"], progress=self._progress)
        self.panel.render(view)
        if not self._shown:
            self.panel.show(self._origin())
            self._shown = True
        if phase in ("recording", "processing"):
            if self._ticker is None:
                self._ticker = _start_timer(self, 1.0, b"tick:", True)
        else:
            self._stop_ticker()
        if phase == "saved":
            if self._saved_timer is None:
                self._saved_timer = _start_timer(self, SAVED_SECONDS, b"savedExpired:", False)
        else:
            self._stop_saved_timer()
        if phase == "recording" and self._drawer_open:
            self.panel.set_drawer(drawer_rows(health, rec["capture_mode"], rec["status"],
                                              rec["elapsed"], title))
        else:
            self.panel.set_drawer(None)
