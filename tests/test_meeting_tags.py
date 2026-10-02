from datetime import datetime, timedelta, timezone

import pytest

from speakeasy import meeting_store
from speakeasy.meeting_library import MeetingLibrary, NewMeeting, TagInfo
from speakeasy.meetings import MeetingSegment

EDT = timezone(timedelta(hours=-4))


@pytest.fixture
def lib(library_path):
    return MeetingLibrary()


def _meeting(lib, minute=0):
    return lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0.0, 4.0, "hello there")],
        duration_seconds=60.0,
        started_at=datetime(2026, 9, 24, 13, minute, 0, tzinfo=EDT)))


def _sql(path, query, params=()):
    conn = meeting_store.connect(path)
    try:
        with conn:
            return [tuple(r) for r in conn.execute(query, params)]
    finally:
        conn.close()


def test_tag_meetings_adds_user_tags_across_meetings(lib):
    a, b = _meeting(lib, 0), _meeting(lib, 1)
    update = lib.tag_meetings([a, b], add=["Project X", "Ops"])
    assert update.created_tags == ["Project X", "Ops"]
    assert update.not_found == []
    assert update.meetings == [(a, ["Ops", "Project X"]), (b, ["Ops", "Project X"])]
    assert lib.meeting_tag_details(a) == [("Ops", "user"), ("Project X", "user")]


def test_tag_meetings_upgrades_claude_link_and_reuses_spelling(lib):
    a = _meeting(lib)
    lib.save_notes(a, tags=["Project X"])
    assert lib.meeting_tag_details(a) == [("Project X", "claude")]
    update = lib.tag_meetings([a], add=["project-x"])
    assert update.created_tags == []
    assert lib.meeting_tag_details(a) == [("Project X", "user")]


def test_tag_meetings_remove_unlinks_and_suppresses_even_unlinked(lib, library_path):
    a, b = _meeting(lib, 0), _meeting(lib, 1)
    lib.tag_meetings([a], add=["Ops"])
    update = lib.tag_meetings([a, b], remove=["ops", "Nope"])
    assert update.not_found == ["Nope"]
    assert lib.meeting_tags(a) == [] and lib.meeting_tags(b) == []
    # Ops is now on no meeting, but orphan cleanup must keep it: deleting it
    # would cascade the suppressions away and let Claude re-add it.
    assert _sql(library_path,
                "SELECT ts.meeting_id FROM tag_suppressions ts JOIN tags t"
                " ON t.id = ts.tag_id WHERE t.slug = 'ops' ORDER BY ts.meeting_id") == [(a,), (b,)]


def test_tag_meetings_add_clears_suppression(lib, library_path):
    a = _meeting(lib)
    lib.tag_meetings([a], add=["Ops"])
    lib.tag_meetings([a], remove=["Ops"])
    lib.tag_meetings([a], add=["OPS"])
    assert lib.meeting_tag_details(a) == [("Ops", "user")]
    assert _sql(library_path, "SELECT COUNT(*) FROM tag_suppressions") == [(0,)]


def test_tag_meetings_validates_before_writing(lib):
    a = _meeting(lib)
    with pytest.raises(ValueError, match="No meeting with id 20200101-000000-abcd"):
        lib.tag_meetings([a, "20200101-000000-abcd"], add=["Ops"])
    assert lib.meeting_tags(a) == [] and lib.list_tags() == []
    with pytest.raises(ValueError, match="both add and remove"):
        lib.tag_meetings([a], add=["Ops"], remove=["ops"])
    assert lib.list_tags() == []
    with pytest.raises(ValueError, match="between 1 and 100"):
        lib.tag_meetings([], add=["Ops"])
    with pytest.raises(ValueError, match="between 1 and 100"):
        lib.tag_meetings([a] + [f"20200101-0000{i:02d}-abcd" for i in range(100)], add=["x"])
    with pytest.raises(ValueError, match="at least one tag"):
        lib.tag_meetings([a])


def test_tag_meetings_enforces_twenty_per_meeting(lib):
    a = _meeting(lib)
    lib.tag_meetings([a], add=[f"t{i}" for i in range(20)])
    with pytest.raises(ValueError, match="At most 20 tags per meeting"):
        lib.tag_meetings([a], add=["one more"])
    assert len(lib.meeting_tags(a)) == 20


def test_tag_filter_matches_variant_spelling_and_alias(lib, library_path):
    a, b = _meeting(lib, 0), _meeting(lib, 1)
    lib.tag_meetings([a], add=["Project X"])
    lib.tag_meetings([b], add=["Ops"])
    assert [m.meeting_id for m in lib.list_meetings(tag="project-x")] == [a]
    assert [m.meeting_id for m in lib.list_meetings(tag="Project X")] == [a]
    _sql(library_path, "INSERT INTO tag_aliases (slug, name, tag_id)"
                       " SELECT 'operations', 'Operations', id FROM tags WHERE slug = 'ops'")
    assert [m.meeting_id for m in lib.list_meetings(tag="Operations")] == [b]
    assert [h.meeting_id for h in lib.search("hello", tag="operations")] == [b]
    assert lib.list_meetings(tag="!!!") == []


def test_tag_catalog_lists_used_tags_with_description_and_aliases(lib, library_path):
    a, b = _meeting(lib, 0), _meeting(lib, 1)
    lib.tag_meetings([a, b], add=["Ops"])
    lib.tag_meetings([a], add=["Budget"])
    _sql(library_path, "UPDATE tags SET description = 'Run the shop' WHERE slug = 'ops'")
    _sql(library_path, "INSERT INTO tag_aliases (slug, name, tag_id)"
                       " SELECT 'operations', 'Operations', id FROM tags WHERE slug = 'ops'")
    assert lib.tag_catalog() == [TagInfo("Ops", "Run the shop", ["Operations"], 2),
                                 TagInfo("Budget", "", [], 1)]
    assert lib.list_tags() == [("Ops", 2), ("Budget", 1)]


def test_orphan_cleanup_keeps_tags_that_still_mean_something(lib, library_path):
    a = _meeting(lib)
    lib.save_notes(a, tags=["Plain", "Described", "Aliased"])
    _sql(library_path, "UPDATE tags SET description = 'kept' WHERE slug = 'described'")
    _sql(library_path, "INSERT INTO tag_aliases (slug, name, tag_id)"
                       " SELECT 'other', 'Other', id FROM tags WHERE slug = 'aliased'")
    lib.save_notes(a, tags=[])
    assert {r[0] for r in _sql(library_path, "SELECT name FROM tags")} == {"Described", "Aliased"}


def test_new_tags_get_slug_and_created_at(lib, library_path):
    a = _meeting(lib)
    lib.save_notes(a, tags=["Q3 Planning"])
    [(slug, created_at)] = _sql(library_path, "SELECT slug, created_at FROM tags")
    assert slug == "q3planning" and created_at.endswith("Z")


def test_writers_resolve_through_aliases(lib, library_path):
    a = _meeting(lib)
    lib.tag_meetings([a], add=["Ops"])
    _sql(library_path, "INSERT INTO tag_aliases (slug, name, tag_id)"
                       " SELECT 'operations', 'Operations', id FROM tags WHERE slug = 'ops'")
    update = lib.tag_meetings([a], add=["Operations"])
    assert update.created_tags == []
    assert lib.meeting_tag_details(a) == [("Ops", "user")]
    assert _sql(library_path, "SELECT COUNT(*) FROM tags WHERE slug = 'operations'") == [(0,)]
    update = lib.tag_meetings([a], remove=["operations"])
    assert update.not_found == []
    assert lib.meeting_tags(a) == []
    assert _sql(library_path, "SELECT COUNT(*) FROM tag_suppressions ts JOIN tags t"
                              " ON t.id = ts.tag_id WHERE t.slug = 'ops'") == [(1,)]
