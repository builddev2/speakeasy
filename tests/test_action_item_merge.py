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
