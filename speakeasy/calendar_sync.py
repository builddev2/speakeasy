"""Calendar.app → the library's calendar cache, through EventKit.

Only Speakeasy talks to EventKit, never the MCP server: a child process of
Claude would put the Calendars permission prompt on Claude. Every EventKit
call runs on one `calendar` thread that never touches audio or the model.
What is copied is deliberately narrow: titles, times, the user's own RSVP and
attendee names/emails. Event notes, location and URLs are never read; they
routinely hold dial-in codes and passwords."""

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

from . import settings
from .meeting_library import EventPerson, MeetingLibrary, SyncedEvent

SYNC_BACK = timedelta(days=90)
SYNC_AHEAD = timedelta(days=14)

# EventKit enum values (EKTypes.h), mirrored so event_from_ek stays testable
# with plain fakes; test_constants_match_eventkit checks them.
AUTH_NOT_DETERMINED, AUTH_RESTRICTED, AUTH_DENIED, AUTH_FULL, AUTH_WRITE_ONLY = 0, 1, 2, 3, 4
PARTICIPANT_TYPE_ROOM, PARTICIPANT_TYPE_RESOURCE, PARTICIPANT_TYPE_GROUP = 2, 3, 4
STATUS_DECLINED = 3
EVENT_STATUS_CANCELED = 3
CALENDAR_TYPE_SUBSCRIPTION, CALENDAR_TYPE_BIRTHDAY = 3, 4


@dataclass(frozen=True)
class CalendarInfo:
    id: str
    title: str
    account: str
    kind: int

    @property
    def default_enabled(self) -> bool:
        # Holiday (subscribed) and birthday calendars are not meetings.
        return self.kind not in (CALENDAR_TYPE_SUBSCRIPTION, CALENDAR_TYPE_BIRTHDAY)


def included_ids(calendars, choices: dict) -> set[str]:
    return {c.id for c in calendars if choices.get(c.id, c.default_enabled)}


def _utc(nsdate) -> datetime:
    return datetime.fromtimestamp(float(nsdate.timeIntervalSince1970()), tz=timezone.utc)


def _email(participant) -> str | None:
    url = participant.URL()
    text = str(url.absoluteString()) if url is not None else ""
    if not text.lower().startswith("mailto:"):
        return None
    return unquote(text[7:]).strip().lower() or None


def _identity(participant) -> str:
    return _email(participant) or (participant.name() or "").strip().lower()


def event_from_ek(ek, calendar_title: str) -> SyncedEvent | None:
    """A plain SyncedEvent, or None for a cancelled or unidentifiable event.
    Reads only the fields named here, never notes/location/URL."""
    if ek.status() == EVENT_STATUS_CANCELED:
        return None
    ident = ek.calendarItemExternalIdentifier() or ek.eventIdentifier()
    if not ident:
        return None
    start, end = _utc(ek.startDate()), _utc(ek.endDate())
    organizer = ek.organizer()
    participants = list(ek.attendees() or [])
    if organizer is not None and all(_identity(p) != _identity(organizer) for p in participants):
        participants.append(organizer)
    declined_self = unknown = False
    count = 0
    people: dict[str, EventPerson] = {}
    for p in participants:
        if p.isCurrentUser():
            declined_self = declined_self or p.participantStatus() == STATUS_DECLINED
            continue
        kind = p.participantType()
        if kind in (PARTICIPANT_TYPE_ROOM, PARTICIPANT_TYPE_RESOURCE):
            continue
        if kind == PARTICIPANT_TYPE_GROUP:
            unknown = True   # a mailing list: we can't tell how many will talk
            continue
        if p.participantStatus() == STATUS_DECLINED:
            continue
        email = _email(p)
        name = (p.name() or "").strip() or email
        if not name:
            unknown = True   # an address we can't name (X500, urn:): still a voice
            continue
        count += 1
        role = ("organizer" if organizer is not None and _identity(p) == _identity(organizer)
                else "attendee")
        people[_identity(p)] = EventPerson(name, email, role)
    ordered = sorted(people.values(), key=lambda p: (p.role != "organizer", p.name.lower()))
    return SyncedEvent(
        key=f"{ident}@{start.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        calendar_name=calendar_title,
        title=(ek.title() or "").strip() or "Untitled event",
        start=start, end=end, all_day=bool(ek.isAllDay()), declined=declined_self,
        other_attendees=None if unknown or not count else count,
        people=tuple(ordered),
    )


class EventKitStore:
    """The only code that touches an EKEventStore. Build and use it on the
    calendar thread only."""

    def __init__(self) -> None:
        import EventKit

        self._ek = EventKit
        self._store = EventKit.EKEventStore.alloc().init()
        self._observer = None

    @staticmethod
    def authorization() -> int:
        import EventKit

        return int(EventKit.EKEventStore.authorizationStatusForEntityType_(
            EventKit.EKEntityTypeEvent))

    def request_access(self, done) -> None:
        # macOS 14+; the completion arrives on an arbitrary queue.
        self._store.requestFullAccessToEventsWithCompletion_(
            lambda granted, error: done(bool(granted)))

    def _ek_calendars(self):
        return list(self._store.calendarsForEntityType_(self._ek.EKEntityTypeEvent) or [])

    def calendars(self) -> list[CalendarInfo]:
        return [CalendarInfo(str(c.calendarIdentifier()), str(c.title()),
                             str(c.source().title()) if c.source() is not None else "Other",
                             int(c.type()))
                for c in self._ek_calendars()]

    def events(self, start: datetime, end: datetime, ids: set[str]) -> list[SyncedEvent]:
        from Foundation import NSDate

        cals = [c for c in self._ek_calendars() if str(c.calendarIdentifier()) in ids]
        if not cals:
            return []
        predicate = self._store.predicateForEventsWithStartDate_endDate_calendars_(
            NSDate.dateWithTimeIntervalSince1970_(start.timestamp()),
            NSDate.dateWithTimeIntervalSince1970_(end.timestamp()), cals)
        out = []
        for ek in self._store.eventsMatchingPredicate_(predicate) or []:
            event = event_from_ek(ek, str(ek.calendar().title()))
            if event is not None:
                out.append(event)
        return out

    def observe_changes(self, callback) -> None:
        from Foundation import NSNotificationCenter

        # The block runs on the posting thread; callback only queues a sync.
        self._observer = NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
            self._ek.EKEventStoreChangedNotification, self._store, None,
            lambda note: callback())

    def stop_observing(self) -> None:
        from Foundation import NSNotificationCenter

        if self._observer is not None:
            NSNotificationCenter.defaultCenter().removeObserver_(self._observer)
            self._observer = None


class CalendarSync:
    """Owns the `calendar` executor. Public methods are safe from any thread."""

    def __init__(self, *, library=None, store_factory=EventKitStore,
                 authorization=EventKitStore.authorization,
                 now=lambda: datetime.now(timezone.utc),
                 read_settings=settings.get_meeting_settings,
                 on_synced=lambda: None) -> None:
        self._library = library if library is not None else MeetingLibrary()
        self._store_factory = store_factory
        self._authorization = authorization
        self._now = now
        self._read_settings = read_settings
        self.on_synced = on_synced
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="calendar")
        self._lock = threading.Lock()
        self._pending = False
        self._store = None
        self.calendars: list[CalendarInfo] = []

    def access(self) -> str:
        # EKEventStore.authorizationStatusForEntityType_ is a thread-safe class
        # method that never prompts and builds no store, so it may be called
        # from the main thread (the one exception to "EventKit only on the
        # calendar thread").
        status = self._authorization()
        if status == AUTH_FULL:
            return "connected"
        return "unconnected" if status == AUTH_NOT_DETERMINED else "denied"

    def tick(self) -> None:
        """Launch, wake and timer entry point. Never prompts.

        Syncs whenever access has been decided, including "denied": a revoke
        must reach `sync_now`, which clears the cache and calendars. That path
        never builds a store or prompts unless access is connected. Only
        "unconnected" (never asked) does nothing; this never calls
        request_access.
        """
        if self.access() != "unconnected":
            self.request_sync()

    def request_sync(self) -> None:
        with self._lock:
            if self._pending:
                return
            self._pending = True
        try:
            self._executor.submit(self._run_sync)
        except RuntimeError:   # shut down (quit): may be called from an ObjC block
            with self._lock:
                self._pending = False

    def request_access(self) -> None:
        try:
            self._executor.submit(self._request_access)
        except RuntimeError:
            pass

    def _request_access(self) -> None:
        if self.access() != "unconnected":
            # Already decided: macOS won't ask again; the page offers
            # Privacy Settings instead.
            self.request_sync()
            return
        self._get_store().request_access(self._after_access)

    def _after_access(self, granted: bool) -> None:
        # Any thread. A store created before the grant can keep answering
        # "no calendars", so start from a fresh one.
        # One job, so a sync queued earlier can't run on the pre-grant store
        # with the drop landing after it.
        try:
            self._executor.submit(self._drop_store_and_sync)
        except RuntimeError:
            pass

    def _drop_store_and_sync(self) -> None:
        self._drop_store()
        self._run_sync()

    def _drop_store(self) -> None:
        store, self._store = self._store, None
        if store is not None:
            store.stop_observing()

    def _get_store(self):
        if self._store is None:
            self._store = self._store_factory()
            self._store.observe_changes(self.request_sync)
        return self._store

    def _run_sync(self) -> None:
        with self._lock:
            self._pending = False   # a change during this sync queues another
        try:
            self.sync_now()
        except Exception as err:   # never kill the executor; log the type only
            print(f"  → calendar sync failed: {type(err).__name__}")
        self.on_synced()

    def sync_now(self) -> int:
        """Calendar thread only. Returns the number of events cached."""
        if self.access() != "connected":
            # Access withdrawn: stop serving events (MCP included).
            self.calendars = []
            self._library.clear_calendar_cache()
            return 0
        store = self._get_store()
        self.calendars = store.calendars()
        ids = included_ids(self.calendars, self._read_settings().get("calendar_choices", {}))
        now = self._now()
        start, end = now - SYNC_BACK, now + SYNC_AHEAD
        events = store.events(start, end, ids)
        self._library.replace_calendar_window(events, start, end)
        return len(events)

    def shutdown(self, wait: bool = False, restart: bool = False) -> None:
        """`restart=True` (tests) drains the queue, then accepts work again."""
        self._executor.shutdown(wait=wait, cancel_futures=not wait)
        self._drop_store()
        if restart:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="calendar")
