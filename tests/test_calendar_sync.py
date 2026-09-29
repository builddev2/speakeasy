"""calendar_sync with a fake EventKit store; no EKEventStore is ever built."""
import threading
from datetime import datetime, timedelta, timezone

import pytest

from speakeasy import calendar_sync as cs
from speakeasy import meeting_store
from speakeasy.meeting_library import MeetingLibrary

UTC = timezone.utc
NOW = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)
SECRET = "SECRET-DIAL-IN 555-0100 pin 4242"


class Date:
    def __init__(self, dt): self._dt = dt
    def timeIntervalSince1970(self): return self._dt.timestamp()


class URL:
    def __init__(self, s): self._s = s
    def absoluteString(self): return self._s


class P:
    def __init__(self, name, email=None, *, me=False, status=2, kind=1):
        self._n, self._e, self._me, self._st, self._k = name, email, me, status, kind
    def name(self): return self._n
    def URL(self): return URL(f"mailto:{self._e}") if self._e else None
    def isCurrentUser(self): return self._me
    def participantStatus(self): return self._st
    def participantType(self): return self._k


class Ev:
    def __init__(self, title, start, minutes=30, *, attendees=(), organizer=None,
                 ext="EXT1", all_day=False, status=1):
        self._t, self._s, self._m = title, start, minutes
        self._a, self._o, self._x, self._ad, self._st = list(attendees), organizer, ext, all_day, status
    def title(self): return self._t
    def startDate(self): return Date(self._s)
    def endDate(self): return Date(self._s + timedelta(minutes=self._m))
    def isAllDay(self): return self._ad
    def status(self): return self._st
    def calendarItemExternalIdentifier(self): return self._x
    def eventIdentifier(self): return "LOCAL-" + self._x
    def organizer(self): return self._o
    def attendees(self): return self._a
    # Privacy: these must never be read.
    def notes(self): raise AssertionError(SECRET)
    def location(self): raise AssertionError(SECRET)
    def structuredLocation(self): raise AssertionError(SECRET)
    def URL(self): raise AssertionError(SECRET)


ME = P("Jason", "jason@example.com", me=True)
REF = P("Refayet K", "Refayet@Example.com")


def test_event_from_ek_counts_other_people_only():
    ev = Ev("1:1", NOW, attendees=[ME, REF, P("Room 4", kind=cs.PARTICIPANT_TYPE_ROOM),
                                   P("Declined D", "d@example.com", status=cs.STATUS_DECLINED)],
            organizer=REF)
    out = cs.event_from_ek(ev, "Work")
    assert out.other_attendees == 1
    assert [(p.name, p.role) for p in out.people] == [("Refayet K", "organizer")]
    assert out.declined is False
    assert out.key == "EXT1@2026-09-29T16:00:00Z"


def test_group_invite_means_unknown_count():
    ev = Ev("All hands", NOW, attendees=[ME, REF, P("Eng DL", kind=cs.PARTICIPANT_TYPE_GROUP)])
    assert cs.event_from_ek(ev, "Work").other_attendees is None


def test_no_invitees_means_unknown_count():
    assert cs.event_from_ek(Ev("Focus", NOW), "Work").other_attendees is None


def test_self_declined_and_cancelled():
    me_no = P("Jason", "jason@example.com", me=True, status=cs.STATUS_DECLINED)
    assert cs.event_from_ek(Ev("x", NOW, attendees=[me_no, REF]), "Work").declined is True
    assert cs.event_from_ek(Ev("x", NOW, status=cs.EVENT_STATUS_CANCELED), "Work") is None


def test_untitled_and_organizer_not_in_attendees():
    out = cs.event_from_ek(Ev("  ", NOW, attendees=[ME], organizer=REF), "Work")
    assert out.title == "Untitled event"
    assert [p.name for p in out.people] == ["Refayet K"] and out.other_attendees == 1


def test_unnameable_participant_makes_count_unknown():
    x500 = P(None, None)
    x500._e = None
    ev = Ev("x", NOW, attendees=[ME, REF, x500])
    assert cs.event_from_ek(ev, "Work").other_attendees is None


def test_same_name_without_email_counts_each_person():
    ev = Ev("x", NOW, attendees=[ME, P("Sam"), P("Sam")])
    out = cs.event_from_ek(ev, "Work")
    assert out.other_attendees == 2


def test_organizer_also_attending_is_one_person():
    ev = Ev("x", NOW, attendees=[ME, REF], organizer=REF)
    assert cs.event_from_ek(ev, "Work").other_attendees == 1


class FakeStore:
    def __init__(self, calendars, events_by_cal):
        self._cals, self._events = calendars, events_by_cal
        self.requested = []
        self.observer = None
    def calendars(self): return list(self._cals)
    def events(self, start, end, ids):
        return [e for cal_id in sorted(ids)
                for ek in self._events.get(cal_id, [])
                if (e := cs.event_from_ek(ek, cal_id))]
    def request_access(self, done): self.requested.append(done)
    def observe_changes(self, callback): self.observer = callback
    def stop_observing(self): self.observer = None


WORK = cs.CalendarInfo("work", "Calendar", "Exchange", 2)
BDAY = cs.CalendarInfo("bday", "Birthdays", "Other", cs.CALENDAR_TYPE_BIRTHDAY)


def make_sync(library_path, store, status=cs.AUTH_FULL, choices=None):
    return cs.CalendarSync(
        library=MeetingLibrary(library_path), store_factory=lambda: store,
        authorization=lambda: status, now=lambda: NOW,
        read_settings=lambda: {"offer_to_record": True, "calendar_choices": choices or {}})


def test_sync_inserts_updates_and_deletes(library_path):
    store = FakeStore([WORK, BDAY], {"work": [Ev("1:1", NOW, attendees=[ME, REF])],
                                     "bday": [Ev("Mum", NOW, ext="B1", all_day=True)]})
    sync = make_sync(library_path, store)
    assert sync.sync_now() == 1   # birthdays off by default
    lib = MeetingLibrary(library_path)
    assert [e.title for e in lib.calendar_events_between("2026-09-29", "2026-09-29")] == ["1:1"]
    store._events["work"] = [Ev("1:1 moved", NOW + timedelta(hours=1), ext="EXT2")]
    sync.sync_now()
    assert [e.title for e in lib.calendar_events_between("2026-09-29", "2026-09-29")] == ["1:1 moved"]


def test_unticked_calendar_is_dropped(library_path):
    store = FakeStore([WORK], {"work": [Ev("1:1", NOW)]})
    make_sync(library_path, store).sync_now()
    make_sync(library_path, store, choices={"work": False}).sync_now()
    assert MeetingLibrary(library_path).calendar_events_between("2026-09-29", "2026-09-29") == []


def test_private_fields_never_reach_the_database(library_path):
    store = FakeStore([WORK], {"work": [Ev("1:1", NOW, attendees=[ME, REF])]})
    make_sync(library_path, store).sync_now()
    conn = meeting_store.connect(library_path)
    try:
        dump = "\n".join(conn.iterdump())
    finally:
        conn.close()
    assert "SECRET" not in dump and "1:1" in dump


@pytest.mark.parametrize("status", [cs.AUTH_DENIED, cs.AUTH_RESTRICTED, cs.AUTH_NOT_DETERMINED])
def test_without_access_the_cache_is_cleared_and_no_store_is_built(library_path, status):
    store = FakeStore([WORK], {"work": [Ev("1:1", NOW)]})
    make_sync(library_path, store).sync_now()
    built = []
    sync = cs.CalendarSync(
        library=MeetingLibrary(library_path),
        store_factory=lambda: built.append(1) or store, authorization=lambda: status,
        now=lambda: NOW, read_settings=lambda: {"calendar_choices": {}})
    assert sync.sync_now() == 0 and built == [] and sync.calendars == []
    assert MeetingLibrary(library_path).calendar_events_between("2026-09-29", "2026-09-29") == []


def test_access_states():
    for status, name in [(cs.AUTH_FULL, "connected"), (cs.AUTH_NOT_DETERMINED, "unconnected"),
                         (cs.AUTH_DENIED, "denied"), (cs.AUTH_RESTRICTED, "denied"),
                         (cs.AUTH_WRITE_ONLY, "denied")]:
        assert cs.CalendarSync(authorization=lambda s=status: s, library=object()).access() == name


def test_request_sync_coalesces(library_path):
    gate, calls = threading.Event(), []
    store = FakeStore([WORK], {})
    original = store.events
    def slow(start, end, ids):
        calls.append(1)
        gate.wait(5)
        return original(start, end, ids)
    store.events = slow
    done = threading.Event()
    sync = make_sync(library_path, store)
    sync.on_synced = done.set
    for _ in range(5):
        sync.request_sync()
    gate.set()
    sync.shutdown(wait=True)
    assert 1 <= len(calls) <= 2


def test_request_access_asks_once_then_syncs(library_path):
    status = {"v": cs.AUTH_NOT_DETERMINED}
    store = FakeStore([WORK], {"work": [Ev("1:1", NOW)]})
    sync = cs.CalendarSync(library=MeetingLibrary(library_path), store_factory=lambda: store,
                           authorization=lambda: status["v"], now=lambda: NOW,
                           read_settings=lambda: {"calendar_choices": {}})
    synced = threading.Event()
    sync.on_synced = synced.set
    sync.request_access()
    sync.shutdown(wait=True, restart=True)
    assert len(store.requested) == 1
    status["v"] = cs.AUTH_FULL
    store.requested[0](True)
    assert synced.wait(5)
    assert MeetingLibrary(library_path).calendar_events_between("2026-09-29", "2026-09-29")


def test_constants_match_eventkit():
    EventKit = pytest.importorskip("EventKit")   # constants only; no store
    assert cs.AUTH_FULL == EventKit.EKAuthorizationStatusFullAccess
    assert cs.AUTH_DENIED == EventKit.EKAuthorizationStatusDenied
    assert cs.AUTH_NOT_DETERMINED == EventKit.EKAuthorizationStatusNotDetermined
    assert cs.AUTH_RESTRICTED == EventKit.EKAuthorizationStatusRestricted
    assert cs.AUTH_WRITE_ONLY == EventKit.EKAuthorizationStatusWriteOnly
    assert cs.STATUS_DECLINED == EventKit.EKParticipantStatusDeclined
    assert cs.PARTICIPANT_TYPE_ROOM == EventKit.EKParticipantTypeRoom
    assert cs.PARTICIPANT_TYPE_RESOURCE == EventKit.EKParticipantTypeResource
    assert cs.PARTICIPANT_TYPE_GROUP == EventKit.EKParticipantTypeGroup
    assert cs.EVENT_STATUS_CANCELED == EventKit.EKEventStatusCanceled
    assert cs.CALENDAR_TYPE_SUBSCRIPTION == EventKit.EKCalendarTypeSubscription
    assert cs.CALENDAR_TYPE_BIRTHDAY == EventKit.EKCalendarTypeBirthday


def test_grant_while_sync_queued_resyncs_on_a_fresh_store(library_path):
    gate = threading.Event()
    stores = []

    def factory():
        st = FakeStore([WORK], {"work": [Ev("1:1", NOW)]})
        stores.append(st)
        if len(stores) == 1:
            st.events = lambda s, e, i: (gate.wait(5), [])[1]
        return st

    sync = cs.CalendarSync(library=MeetingLibrary(library_path), store_factory=factory,
                           authorization=lambda: cs.AUTH_FULL, now=lambda: NOW,
                           read_settings=lambda: {"calendar_choices": {}})
    sync.request_sync()          # running, blocked on the first (stale) store
    sync.request_sync()          # queued behind it
    sync._after_access(True)
    gate.set()
    sync.shutdown(wait=True)
    assert len(stores) == 2
    assert MeetingLibrary(library_path).calendar_events_between("2026-09-29", "2026-09-29")


def test_requests_after_shutdown_do_not_raise_or_stick(library_path):
    sync = make_sync(library_path, FakeStore([WORK], {}))
    sync.shutdown(wait=True)
    sync.request_sync()
    sync.request_access()
    sync._after_access(True)
    assert sync._pending is False


def test_shutdown_stops_observing(library_path):
    store = FakeStore([WORK], {})
    sync = make_sync(library_path, store)
    sync.sync_now()
    assert store.observer is not None
    sync.shutdown(wait=True)
    assert store.observer is None
