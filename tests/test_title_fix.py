from datetime import datetime, timedelta, timezone

from speakeasy.meeting_library import MeetingLibrary, NewMeeting
from speakeasy.meetings import MeetingSegment

PDT = timezone(timedelta(hours=-7))


def _imported(lib, title, start, minutes, mid):
    return lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0, 1, "a")], duration_seconds=minutes * 60,
        started_at=start, title=title, meeting_id=mid, source="imported_json",
        timestamps_approximate=True))


def test_end_time_suffix_becomes_start_time(library_path):
    lib = MeetingLibrary(library_path)
    start = datetime(2026, 9, 2, 13, 9, tzinfo=PDT)
    _imported(lib, "1on1 Jim Meeting — Sep 2, 2:07 PM", start, 58, "20260902-140700-aaaa")
    fixes = lib.imported_title_fixes()
    assert fixes == [("20260902-140700-aaaa", "1on1 Jim Meeting — Sep 2, 2:07 PM",
                      "1on1 Jim Meeting — Sep 2, 1:09 PM")]
    assert lib.apply_title_fixes(fixes) == 1
    assert lib.get_meeting("20260902-140700-aaaa").title.endswith("1:09 PM")
    assert lib.imported_title_fixes() == []          # idempotent


def test_other_titles_are_left_alone(library_path):
    lib = MeetingLibrary(library_path)
    start = datetime(2026, 9, 2, 13, 9, tzinfo=PDT)
    _imported(lib, "Board prep", start, 58, "20260902-140700-bbbb")
    _imported(lib, "Sync — Sep 2, 1:09 PM", start, 58, "20260902-140700-cccc")   # already start
    lib.save_meeting(NewMeeting(segments=[MeetingSegment("You", 0, 1, "a")],
                                duration_seconds=3480, started_at=start,
                                title="Recorded — Sep 2, 2:07 PM"))           # not imported
    assert lib.imported_title_fixes() == []


def test_apply_skips_titles_changed_since_preview(library_path):
    lib = MeetingLibrary(library_path)
    start = datetime(2026, 9, 2, 13, 9, tzinfo=PDT)
    _imported(lib, "X — Sep 2, 2:07 PM", start, 58, "20260902-140700-dddd")
    fixes = lib.imported_title_fixes()
    lib.rename("20260902-140700-dddd", "Renamed by user")
    assert lib.apply_title_fixes(fixes) == 0
    assert lib.get_meeting("20260902-140700-dddd").title == "Renamed by user"
