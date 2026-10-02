"""Main-thread glue for the record banner. It feeds RecordPromptCoordinator
calendar ticks (30 s, cached table only), call-probe answers and engine
states, draws its banner on RecordPromptPanel, and turns clicks into
begin_meeting/end_meeting — the only way this feature records anything."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import objc
from Foundation import NSObject, NSTimer

from .. import settings
from ..call_detect import CallProbe
from ..meeting_options import MeetingOptions
from ..record_prompt import CallEnded, Offer, RecordPromptCoordinator, banner_text

TICK_SECONDS = 30.0
# Wide enough for both prompt_candidates (start −2…+5 min) and pick_event
# (start −10 min … end).
_EVENT_WINDOW = timedelta(minutes=15)
_PROBED_STATES = {"ready", "meeting_recording"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


def _make_panel(target):
    from .record_prompt_panel import RecordPromptPanel

    return RecordPromptPanel.alloc().initWithTarget_(target)


def _make_probe(controller):
    return CallProbe(
        on_result=lambda using: controller.performSelectorOnMainThread_withObject_waitUntilDone_(
            b"callObserved:", using, False),
        wanted=lambda: controller.probe_wanted)


class RecordPromptController(NSObject):
    def initWithEngine_(self, engine):
        self = objc.super(RecordPromptController, self).init()
        if self is None:
            return None
        self.engine = engine
        self.coordinator = RecordPromptCoordinator(settings.get_prompted_events(_today()))
        self._saved_prompted = set(self.coordinator.prompted)
        self._shown = None
        self._shown_text = None
        self.probe_wanted = False
        self.panel = _make_panel(self)
        self.probe = _make_probe(self)
        self.probe.start()
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            TICK_SECONDS, self, b"tick:", None, True)
        self.engineStateChanged_(engine.state.value)
        return self

    # -- inputs (main thread) ---------------------------------------------------

    def engineStateChanged_(self, state_name):
        state = str(state_name)
        self.coordinator.engine_state(state)
        if state == "meeting_recording":
            self.probe.forgive_waved()
        self._update_probe(state, settings.get_meeting_settings())
        self._render()

    def tick_(self, timer):
        now = _now()
        prefs = settings.get_meeting_settings()
        self.coordinator.calendar_tick(self._events_near(now), now,
                                       offer_enabled=prefs["offer_to_record"])
        if not prefs["detect_calls"]:
            self.coordinator.calls_disabled()
        self._update_probe(self.engine.state.value, prefs)
        self._render()

    def callObserved_(self, using):
        now = _now()
        self.coordinator.call_observed(
            bool(using), now, self._events_near(now),
            detect_enabled=settings.get_meeting_settings()["detect_calls"])
        self._render()

    # -- clicks (from the panel) ---------------------------------------------------

    def bannerPrimary_(self, sender):
        banner = self.coordinator.banner
        if isinstance(banner, Offer):
            if self._engine_ready():
                self._record(0)
        elif isinstance(banner, CallEnded):
            self.coordinator.stop()
            self.engine.end_meeting()
            self._render()

    def bannerSecondary_(self, sender):
        banner = self.coordinator.banner
        if isinstance(banner, Offer):
            self.coordinator.not_now()
        elif isinstance(banner, CallEnded):
            self.coordinator.keep()
        self._render()

    def bannerMore_(self, item):
        banner = self.coordinator.banner
        if isinstance(banner, Offer) and 0 < int(item.tag()) < len(banner.events):
            if self._engine_ready():
                self._record(int(item.tag()))

    @objc.python_method
    def shutdown(self):
        self._timer.invalidate()
        self.probe.stop()
        self.panel.hide()

    # -- internals ---------------------------------------------------------------

    @objc.python_method
    def _engine_ready(self):
        # begin_meeting does nothing unless READY; don't claim "Recording".
        return self.engine.state.value == "ready"

    @objc.python_method
    def _record(self, index):
        key = self.coordinator.record(index)
        self.engine.begin_meeting(MeetingOptions(calendar_event_key=key))
        self._persist()
        # The panel hides itself after the confirmation; nothing to re-draw.
        self._shown = None
        self._shown_text = None
        self.panel.show_confirmation("Recording")

    @objc.python_method
    def _update_probe(self, state, prefs):
        self.probe_wanted = bool(prefs["detect_calls"]) and state in _PROBED_STATES

    @objc.python_method
    def _events_near(self, now):
        try:
            return self.engine.library.calendar_events_overlapping(
                now - _EVENT_WINDOW, now + _EVENT_WINDOW)
        except (sqlite3.Error, ValueError):
            return []

    @objc.python_method
    def _persist(self):
        if self.coordinator.prompted != self._saved_prompted:
            settings.set_prompted_events(_today(), self.coordinator.prompted)
            self._saved_prompted = set(self.coordinator.prompted)

    @objc.python_method
    def _render(self):
        self._persist()
        if self.coordinator.take_call_waved_off():
            self.probe.ignore_current()
        banner = self.coordinator.banner
        if banner is None:
            if self._shown is not None:
                self.panel.hide()
            self._shown = None
            self._shown_text = None
            return
        text = banner_text(banner, _now())
        if banner is self._shown and text == self._shown_text:
            return
        self._shown = banner
        self._shown_text = text
        self.panel.show(text.title, text.subtitle, text.primary, text.secondary, list(text.more))
