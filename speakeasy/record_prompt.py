"""What the record banner shows. Pure and main-thread only: the controller
feeds it calendar ticks, call observations, engine states and clicks, then
draws `banner`. Speakeasy never records by itself — every path that starts or
stops a recording begins with a click the controller reports here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from . import calendar_match
from .meeting_library import CalendarEvent

OFFER_TIMEOUT = timedelta(minutes=5)
# Brief mic use (another dictation app, a voice memo) is not a call.
CALL_START = timedelta(seconds=10)
CALL_END = timedelta(seconds=60)

READY = "ready"
RECORDING = "meeting_recording"
PROCESSING = "meeting_processing"


@dataclass(frozen=True)
class Offer:
    kind: str                          # "calendar" or "call"
    events: tuple[CalendarEvent, ...]  # first is shown; the rest go in "N more"
    shown_at: datetime


@dataclass(frozen=True)
class CallEnded:
    pass


@dataclass(frozen=True)
class BannerText:
    title: str
    subtitle: str
    primary: str
    secondary: str
    more: tuple[str, ...] = ()


def _with_people(events) -> list[CalendarEvent]:
    # Personal blocks ("pickup", "Outside office hours") invite nobody else.
    return [e for e in events if e.people]


def _when(event: CalendarEvent, now: datetime) -> str:
    minutes = round((calendar_match.event_start(event) - now).total_seconds() / 60)
    if minutes >= 1:
        return f"Starts in {minutes} min"
    if minutes <= -1:
        return f"Started {-minutes} min ago"
    return "Starting now"


def banner_text(banner, now: datetime) -> BannerText:
    if isinstance(banner, CallEnded):
        return BannerText("Call ended", "Stop recording?", "Stop", "Keep Recording")
    if banner.kind == "call":
        if banner.events:
            return BannerText(banner.events[0].title, "Call in progress", "Record", "Not Now")
        return BannerText("Record this call?", "Another app is using the microphone",
                          "Record", "Not Now")
    first = banner.events[0]
    return BannerText(first.title, f"{_when(first, now)} · {len(first.people)} invited",
                      "Record", "Not Now", tuple(e.title for e in banner.events[1:]))


class RecordPromptCoordinator:
    def __init__(self, prompted: set[str]) -> None:
        self.prompted = set(prompted)
        self.banner: Offer | CallEnded | None = None
        self._state: str | None = None
        self._using_since: datetime | None = None
        self._idle_since: datetime | None = None
        self._call_offered = False      # this call was offered (or recorded) already
        self._heard_call = False        # another app used the mic during this recording
        self._ended_dismissed = False   # Keep Recording, until the mic is used again

    # -- inputs ---------------------------------------------------------------

    def engine_state(self, state: str) -> None:
        if state != RECORDING:
            self._heard_call = False
            self._ended_dismissed = False
            if isinstance(self.banner, CallEnded):
                self.banner = None
        if state in (RECORDING, PROCESSING) and isinstance(self.banner, Offer):
            self._close_offer()   # started by hand (menu, Dock, Today view)
        self._state = state

    def calendar_tick(self, events, now: datetime, *, offer_enabled: bool) -> None:
        self._expire(now)
        if not offer_enabled:
            if isinstance(self.banner, Offer) and self.banner.kind == "calendar":
                self.banner = None
            return
        if self._state != READY or self.banner is not None:
            return
        candidates = _with_people(calendar_match.prompt_candidates(events, now, self.prompted))
        if candidates:
            candidates.sort(key=lambda e: (
                abs((calendar_match.event_start(e) - now).total_seconds()), e.event_key))
            self.banner = Offer("calendar", tuple(candidates), now)

    def call_observed(self, using: bool, now: datetime, events, *,
                      detect_enabled: bool) -> None:
        if using:
            self._idle_since = None
            if self._using_since is None:
                self._using_since = now
        else:
            self._using_since = None
            if self._idle_since is None:
                self._idle_since = now
        call_over = self._idle_since is not None and now - self._idle_since >= CALL_END
        if call_over:
            self._call_offered = False
        if not detect_enabled:
            self.calls_disabled()
            return
        self._expire(now)
        if self._state == RECORDING:
            self._watch_recording(using, now, call_over)
            return
        if isinstance(self.banner, Offer) and self.banner.kind == "call" and call_over:
            self.banner = None   # the call ended before anyone answered
            return
        if (self._state != READY or self.banner is not None or self._call_offered
                or self._using_since is None or now - self._using_since < CALL_START):
            return
        self._call_offered = True
        event = calendar_match.pick_event(_with_people(events), now)
        if event is not None and event.event_key in self.prompted:
            return   # the calendar banner already asked about this meeting
        self.banner = Offer("call", (event,) if event is not None else (), now)

    def calls_disabled(self) -> None:
        """Call detection was turned off: drop call banners (nothing is marked)."""
        if isinstance(self.banner, CallEnded) or (
                isinstance(self.banner, Offer) and self.banner.kind == "call"):
            self.banner = None

    # -- clicks ---------------------------------------------------------------

    def record(self, index: int = 0) -> str | None:
        """The event key to link (None: let the engine match), and close."""
        offer = self.banner
        if not isinstance(offer, Offer):
            raise RuntimeError("no record offer is showing")
        key = offer.events[index].event_key if offer.events else None
        self._close_offer()
        return key

    def not_now(self) -> None:
        if isinstance(self.banner, Offer):
            self._close_offer()

    def stop(self) -> None:
        if isinstance(self.banner, CallEnded):
            self.banner = None

    def keep(self) -> None:
        if isinstance(self.banner, CallEnded):
            self.banner = None
            self._ended_dismissed = True

    # -- internals --------------------------------------------------------------

    def _watch_recording(self, using: bool, now: datetime, call_over: bool) -> None:
        if using:
            if now - self._using_since >= CALL_START:   # a blip is not a call
                self._heard_call = True
                self._call_offered = True    # recorded, so not offered again after
            self._ended_dismissed = False
            if isinstance(self.banner, CallEnded):
                self.banner = None
            return
        if (self._heard_call and call_over and self.banner is None
                and not self._ended_dismissed):
            self.banner = CallEnded()

    def _expire(self, now: datetime) -> None:
        if isinstance(self.banner, Offer) and now - self.banner.shown_at >= OFFER_TIMEOUT:
            self._close_offer()

    def _close_offer(self) -> None:
        self.prompted.update(e.event_key for e in self.banner.events)
        self.banner = None
