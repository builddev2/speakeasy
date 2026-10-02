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


def test_tag_meetings_validates_before_writing(lib, library_path):
    a = _meeting(lib)
    with pytest.raises(ValueError, match="No meeting with id 20200101-000000-abcd"):
        lib.tag_meetings([a, "20200101-000000-abcd"], add=["Ops"])
    assert lib.meeting_tags(a) == [] and lib.list_tags() == []
    with pytest.raises(ValueError, match="both add and remove"):
        lib.tag_meetings([a], add=["Ops"], remove=["ops"])
    assert _sql(library_path, "SELECT COUNT(*) FROM tags") == [(0,)]
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


def test_save_notes_reuses_existing_tag_for_any_spelling(lib):
    a, b = _meeting(lib, 0), _meeting(lib, 1)
    assert lib.save_notes(a, tags=["Project X", "Budget"]).created_tags == ["Project X", "Budget"]
    notes = lib.save_notes(b, tags=["project-x"])
    assert notes.created_tags == [] and notes.suppressed_tags == []
    assert lib.meeting_tags(b) == ["Project X"]
    assert lib.list_tags() == [("Project X", 2), ("Budget", 1)]


def test_resaving_claude_tags_never_removes_user_tags(lib):
    a = _meeting(lib)
    lib.tag_meetings([a], add=["Mine"])
    lib.save_notes(a, tags=["Theirs"])
    assert lib.meeting_tag_details(a) == [("Mine", "user"), ("Theirs", "claude")]
    lib.save_notes(a, tags=["Other"])
    assert lib.meeting_tag_details(a) == [("Mine", "user"), ("Other", "claude")]
    lib.save_notes(a, tags=[])
    assert lib.meeting_tag_details(a) == [("Mine", "user")]


def test_claude_naming_a_user_tag_keeps_it_user(lib):
    a = _meeting(lib)
    lib.tag_meetings([a], add=["Project X"])
    notes = lib.save_notes(a, tags=["project x"])
    assert notes.created_tags == []
    assert lib.meeting_tag_details(a) == [("Project X", "user")]
    lib.save_notes(a, tags=[])
    assert lib.meeting_tag_details(a) == [("Project X", "user")]


def test_save_notes_skips_suppressed_tags_per_meeting(lib):
    a, b = _meeting(lib, 0), _meeting(lib, 1)
    lib.save_notes(a, tags=["Ops"])
    lib.tag_meetings([a], remove=["Ops"])
    notes = lib.save_notes(a, tags=["OPS", "Budget"])
    assert notes.suppressed_tags == ["Ops"]
    assert lib.meeting_tags(a) == ["Budget"]
    assert lib.save_notes(b, tags=["Ops"]).suppressed_tags == []
    assert lib.meeting_tags(b) == ["Ops"]


def test_new_tag_cap_allows_three_and_rejects_four_atomically(lib):
    a, b = _meeting(lib, 0), _meeting(lib, 1)
    assert lib.save_notes(a, tags=["a1", "a2", "a3"]).created_tags == ["a1", "a2", "a3"]
    with pytest.raises(ValueError, match=r"Would create 4 new tags \(n1, n2, n3, n4\); at most 3 per call"):
        lib.save_notes(b, summary="S", tags=["a1", "n1", "n2", "n3", "n4"])
    assert lib.get_meeting(b).notes is None          # the summary was not saved either
    assert lib.meeting_tags(b) == []
    assert {n for n, _ in lib.list_tags()} == {"a1", "a2", "a3"}


def test_twenty_tag_limit_counts_user_and_claude_tags(lib):
    a = _meeting(lib)
    lib.tag_meetings([a], add=[f"u{i}" for i in range(18)])
    with pytest.raises(ValueError, match="At most 20 tags per meeting"):
        lib.save_notes(a, tags=["n1", "n2", "n3"])
    assert len(lib.meeting_tags(a)) == 18
    lib.save_notes(a, tags=["n1", "n2"])
    assert len(lib.meeting_tags(a)) == 20


def test_rename_keeps_old_name_resolving(lib):
    a = _meeting(lib)
    lib.tag_meetings([a], add=["Proj"])
    assert lib.rename_tag("proj", "Project Phoenix") == TagInfo("Project Phoenix", "", ["Proj"], 1)
    assert [m.meeting_id for m in lib.list_meetings(tag="Proj")] == [a]
    assert lib.save_notes(a, tags=["proj"]).created_tags == []
    assert lib.meeting_tags(a) == ["Project Phoenix"]


def test_rename_case_only_changes_display_without_alias(lib):
    a = _meeting(lib)
    lib.tag_meetings([a], add=["budget"])
    assert lib.rename_tag("budget", "Budget") == TagInfo("Budget", "", [], 1)


def test_rename_back_to_an_own_alias(lib):
    a = _meeting(lib)
    lib.tag_meetings([a], add=["Proj"])
    lib.rename_tag("Proj", "Phoenix")
    assert lib.rename_tag("Phoenix", "Proj") == TagInfo("Proj", "", ["Phoenix"], 1)


def test_rename_onto_another_tag_or_its_alias_says_merge(lib):
    a = _meeting(lib)
    lib.tag_meetings([a], add=["Ops", "Budget"])
    with pytest.raises(ValueError, match="already exists; use merge"):
        lib.rename_tag("Ops", "budget")
    lib.rename_tag("Budget", "Finance")          # "Budget" is now an alias of Finance
    with pytest.raises(ValueError, match="already exists; use merge"):
        lib.rename_tag("Ops", "BUDGET")
    with pytest.raises(ValueError, match="No tag named"):
        lib.rename_tag("missing", "Whatever")


def test_merge_moves_links_user_wins_and_leaves_alias(lib):
    a, b, c = _meeting(lib, 0), _meeting(lib, 1), _meeting(lib, 2)
    lib.tag_meetings([a], add=["proj-x"])         # user link on the source
    lib.save_notes(a, tags=["Project X"])         # claude link on the target
    lib.save_notes(b, tags=["proj-x"])            # source only, from Claude
    lib.tag_meetings([c], add=["Project X"])      # target only, from the user
    assert lib.merge_tags("proj-x", "project x") == TagInfo("Project X", "", ["proj-x"], 3)
    assert lib.meeting_tag_details(a) == [("Project X", "user")]
    assert lib.meeting_tag_details(b) == [("Project X", "claude")]
    assert lib.meeting_tag_details(c) == [("Project X", "user")]
    assert len(lib.list_meetings(tag="projx")) == 3


def test_merge_link_beats_suppression_and_moves_suppressions(lib, library_path):
    a, b = _meeting(lib, 0), _meeting(lib, 1)
    lib.tag_meetings([a], add=["Target", "Source"])
    lib.tag_meetings([a], remove=["Source"])      # a: Target linked, Source suppressed
    lib.tag_meetings([b], add=["Source"])
    lib.tag_meetings([b], remove=["Source"])      # b: Source suppressed only
    lib.merge_tags("Source", "Target")
    assert lib.meeting_tags(a) == ["Target"]
    assert _sql(library_path, "SELECT meeting_id FROM tag_suppressions") == [(b,)]
    assert lib.save_notes(b, tags=["Target"]).suppressed_tags == ["Target"]
    assert lib.save_notes(a, tags=["Target"]).suppressed_tags == []


def test_merge_repoints_aliases_and_rejects_self_and_missing(lib):
    a = _meeting(lib)
    lib.tag_meetings([a], add=["A", "C"])
    lib.rename_tag("A", "B")                      # alias A -> B
    assert lib.merge_tags("B", "C") == TagInfo("C", "", ["A", "B"], 1)
    with pytest.raises(ValueError, match="into itself"):
        lib.merge_tags("C", "a")
    with pytest.raises(ValueError, match="No tag named"):
        lib.merge_tags("missing", "C")


def test_delete_tag_removes_links_aliases_and_suppressions(lib, library_path):
    a, b = _meeting(lib, 0), _meeting(lib, 1)
    lib.tag_meetings([a, b], add=["Ops"])
    lib.tag_meetings([b], remove=["Ops"])
    lib.rename_tag("Ops", "Operations")
    assert lib.delete_tag("ops") == "Operations"
    for table in ("tags", "meeting_tags", "tag_aliases", "tag_suppressions"):
        assert _sql(library_path, f"SELECT COUNT(*) FROM {table}") == [(0,)], table
    assert lib.save_notes(b, tags=["Ops"]).created_tags == ["Ops"]


def test_describe_tag(lib):
    a = _meeting(lib)
    lib.tag_meetings([a], add=["Ops"])
    assert lib.describe_tag("ops", "  Running   the shop ").description == "Running the shop"
    with pytest.raises(ValueError, match="at most 200 characters"):
        lib.describe_tag("Ops", "x" * 201)
    assert lib.describe_tag("Ops", "x" * 200).description == "x" * 200
    assert lib.describe_tag("Ops", "").description == ""
