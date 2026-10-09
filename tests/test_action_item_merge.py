import pytest

from speakeasy.meeting_library import MeetingLibrary
from tests.test_library_backup import _meeting


@pytest.fixture
def lib(library_path):
    return MeetingLibrary(library_path)


def tasks(lib, mid):
    return [i.task for i in lib.list_action_items(meeting_id=mid)]


def test_untouched_items_are_replaced(lib):
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=["Old one", "Old two"])
    lib.save_notes(mid, action_items=["New one"])
    assert tasks(lib, mid) == ["New one"]


def test_touched_done_manual_items_are_kept_and_not_duplicated(lib):
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=["Send the budget draft", "Book room", "Email Siam"])
    items = {i.task: i for i in lib.list_action_items(meeting_id=mid)}
    lib.update_action_item(items["Send the budget draft"].id, updated_by="user", notes="n")
    lib.update_action_item(items["Book room"].id, updated_by="user", status="done")
    lib.create_action_item(task="My own thing", meeting_id=mid)
    lib.save_notes(mid, action_items=["Send budget draft", "Book the room", "Brand new", "Email Siam"])
    assert sorted(tasks(lib, mid)) == sorted(
        ["Send the budget draft", "Book room", "My own thing", "Brand new", "Email Siam"])


def test_deleted_item_suppresses_readd(lib):
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=["Wrong item"])
    lib.delete_action_item(lib.list_action_items(meeting_id=mid)[0].id, updated_by="user")
    lib.save_notes(mid, action_items=["Wrong item", "Right item"])
    assert tasks(lib, mid) == ["Right item"]


def test_structured_items_and_order(lib):
    mid = _meeting(lib, "M")
    notes = lib.save_notes(mid, summary="s", action_items=[
        {"task": "B", "owner": "Jason", "due_date": "2026-10-16", "due_phrase": "Fri",
         "priority": "high", "tags": ["Budget"]},
        "Siam — A"])
    items = lib.list_action_items(meeting_id=mid)
    assert [(i.task, i.owner, i.priority, i.tags, i.position) for i in items] == [
        ("B", "Jason", "high", ["Budget"], 0), ("A", "Siam", "normal", [], 1)]
    assert notes.action_items == ["Jason — B (Fri)", "Siam — A"]


def test_limits_and_bad_items_save_nothing(lib):
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=["Keep"])
    with pytest.raises(ValueError, match="50"):
        lib.save_notes(mid, action_items=[f"t{i}" for i in range(51)])
    with pytest.raises(ValueError, match="priority"):
        lib.save_notes(mid, summary="changed", action_items=[{"task": "x", "priority": "urgent"}])
    assert tasks(lib, mid) == ["Keep"]
    assert lib.get_meeting(mid, with_segments=False).notes.summary == "s"


def test_blank_strings_are_dropped(lib):
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=["  ", "Real"])
    assert tasks(lib, mid) == ["Real"]


def test_omitting_action_items_leaves_items_alone(lib):
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=["Keep"])
    lib.save_notes(mid, summary="new summary")
    assert tasks(lib, mid) == ["Keep"]


def _tag_count(lib, name):
    with lib._transaction() as conn:
        return conn.execute("SELECT COUNT(*) FROM tags WHERE name = ?", (name,)).fetchone()[0]


def test_resummarise_drops_orphan_tags_but_keeps_tags_of_kept_items(lib):
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=[
        {"task": "B", "tags": ["Budgetzz"]}, {"task": "Keeper", "tags": ["Keepzz"]}])
    keeper = next(i for i in lib.list_action_items(meeting_id=mid) if i.task == "Keeper")
    lib.update_action_item(keeper.id, updated_by="user", notes="n")
    lib.save_notes(mid, action_items=["C"])
    assert _tag_count(lib, "Budgetzz") == 0
    assert _tag_count(lib, "Keepzz") == 1


def test_similarity_threshold_is_pinned(lib):
    from speakeasy import action_items as ai
    assert ai.SIMILAR_TASK_RATIO == 0.85
    assert ai.similar("Draft budget proposal", "Draft budget proposals now") >= 0.85
    assert ai.similar("Draft budget proposal", "Draft budget plan for Q4") < 0.85
    mid = _meeting(lib, "M")
    lib.create_action_item(task="Draft budget proposal", meeting_id=mid)
    lib.save_notes(mid, summary="s", action_items=[
        "Draft budget proposals now", "Draft budget plan for Q4"])
    assert sorted(tasks(lib, mid)) == ["Draft budget plan for Q4", "Draft budget proposal"]


def test_similarity_equal_to_threshold_suppresses(lib, monkeypatch):
    from speakeasy import action_item_store, action_items as ai
    monkeypatch.setattr(action_item_store.ai, "similar", lambda a, b: ai.SIMILAR_TASK_RATIO)
    mid = _meeting(lib, "M")
    lib.create_action_item(task="Alpha", meeting_id=mid)
    lib.save_notes(mid, summary="s", action_items=["Beta"])
    assert tasks(lib, mid) == ["Alpha"]
