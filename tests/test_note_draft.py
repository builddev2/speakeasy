from datetime import datetime, timedelta, timezone

import pytest

from speakeasy.meeting_library import MeetingLibrary, MeetingNotFound, NewMeeting, utc_iso
from speakeasy.meetings import MeetingSegment

EDT = timezone(timedelta(hours=-4))
START = datetime(2026, 10, 2, 9, 0, 0, tzinfo=EDT)


def _new(started=START):
    return NewMeeting(segments=[MeetingSegment("You", 0.0, 4.0, "hello")],
                      duration_seconds=600.0, started_at=started)


def test_draft_round_trip_and_discard(library_path):
    lib = MeetingLibrary()
    assert lib.get_draft() is None
    lib.set_draft("hello", [(0, 3.0)], utc_iso(START))
    d = lib.get_draft()
    assert (d.markdown, d.stamps, d.started_at) == ("hello", [(0, 3.0)], utc_iso(START))
    lib.set_draft("hello again", [], utc_iso(START))
    assert lib.get_draft().markdown == "hello again"
    lib.discard_draft()
    assert lib.get_draft() is None


def test_draft_refuses_bad_input(library_path):
    lib = MeetingLibrary()
    with pytest.raises(ValueError, match="Notes are too long to save"):
        lib.set_draft("x" * 200_001, [], utc_iso(START))
    with pytest.raises(ValueError, match="Invalid note stamps"):
        lib.set_draft("x", [(0, -1)], utc_iso(START))


def test_save_meeting_adopts_a_draft_from_this_recording(library_path):
    lib = MeetingLibrary()
    lib.set_draft("first\n- second", [(0, 30.0), (1, 95.5)], utc_iso(START))
    mid = lib.save_meeting(_new())
    notes = lib.get_user_notes(mid)
    assert (notes.markdown, notes.stamps) == ("first\n- second", [(0, 30.0), (1, 95.5)])
    assert lib.get_draft() is None


def test_adoption_shifts_stamps_by_the_draft_start(library_path):
    lib = MeetingLibrary()
    lib.set_draft("late start", [(0, 10.0)], utc_iso(START + timedelta(seconds=20)))
    mid = lib.save_meeting(_new())
    assert lib.get_user_notes(mid).stamps == [(0, 30.0)]


def test_stamps_before_the_meeting_clamp_to_zero(library_path):
    lib = MeetingLibrary()
    lib.set_draft("early", [(0, 10.0)], utc_iso(START - timedelta(minutes=5)))
    mid = lib.save_meeting(_new())
    assert lib.get_user_notes(mid).stamps == [(0, 0.0)]


@pytest.mark.parametrize("offset", [timedelta(minutes=-11), timedelta(seconds=601)])
def test_draft_outside_window_is_left_alone(library_path, offset):
    lib = MeetingLibrary()
    lib.set_draft("other recording", [], utc_iso(START + offset))
    mid = lib.save_meeting(_new())
    assert lib.get_user_notes(mid) is None
    assert lib.get_draft().markdown == "other recording"


def test_blank_draft_is_cleared_without_notes(library_path):
    lib = MeetingLibrary()
    lib.set_draft("   ", [], utc_iso(START))
    mid = lib.save_meeting(_new())
    assert lib.get_user_notes(mid) is None and lib.get_draft() is None


def test_finish_draft_overwrites_with_latest_text(library_path):
    lib = MeetingLibrary()
    lib.set_draft("A", [(0, 30.0)], utc_iso(START))
    mid = lib.save_meeting(_new())
    lib.set_draft("stray", [], utc_iso(START))          # a debounced save that lost the race
    lib.finish_draft(mid, "A and B", [(0, 30.0), (1, 40.0)], utc_iso(START))
    notes = lib.get_user_notes(mid)
    assert (notes.markdown, notes.stamps) == ("A and B", [(0, 30.0), (1, 40.0)])
    assert lib.get_draft() is None


def test_finish_draft_for_a_missing_meeting_keeps_the_draft(library_path):
    lib = MeetingLibrary()
    lib.set_draft("keep me", [], utc_iso(START))
    with pytest.raises(MeetingNotFound):
        lib.finish_draft("20260101-000000-abcd", "keep me", [], utc_iso(START))
    assert lib.get_draft().markdown == "keep me"
