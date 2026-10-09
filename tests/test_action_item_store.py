import pytest

from speakeasy.action_item_store import ActionItemNotFound
from speakeasy.meeting_library import MeetingLibrary
from tests.test_library_backup import _meeting


@pytest.fixture
def lib(library_path):
    return MeetingLibrary(library_path)


def test_create_manual_item_without_meeting(lib):
    item = lib.create_action_item(task="Call the bank", owner="Jason", due="2026-10-20",
                                  priority="high", notes="ask about fees", tags=["Money"])
    assert (item.task, item.owner, item.priority, item.status, item.source) == \
        ("Call the bank", "Jason", "high", "open", "manual")
    assert (item.effective_due, item.due_source, item.due_override) == ("2026-10-20", "user", "2026-10-20")
    assert item.tags == ["Money"] and item.meeting_id is None and item.user_touched
    assert lib.list_action_items()[0].id == item.id


def test_create_on_missing_meeting_raises(lib):
    from speakeasy.meeting_library import MeetingNotFound
    with pytest.raises(MeetingNotFound):
        lib.create_action_item(task="x", meeting_id="20260101-000000-dead")


def test_str_tags_rejected(lib):
    with pytest.raises(ValueError):
        lib.create_action_item(task="x", tags="Money")
    item = lib.create_action_item(task="x")
    with pytest.raises(ValueError):
        lib.update_action_item(item.id, updated_by="user", tags="Money")
    with pytest.raises(ValueError):
        lib.bulk_update_action_items([item.id], updated_by="user", add_tags="Money")
    assert lib.create_action_item(task="y", tags=("A",)).tags == ["A"]


def test_invalid_status_rejected(lib):
    item = lib.create_action_item(task="x")
    with pytest.raises(ValueError, match="open or done"):
        lib.update_action_item(item.id, updated_by="user", status="finished")
    with pytest.raises(ValueError, match="open or done"):
        lib.bulk_update_action_items([item.id], updated_by="user", status="finished")


def test_timestamps_are_utc_z_with_microseconds(lib):
    item = lib.create_action_item(task="x")
    for stamp in (item.created_at, item.updated_at):
        assert stamp.endswith("Z") and "." in stamp and "+00:00" not in stamp


def test_update_status_stamps_and_clears_completed_at(lib):
    item = lib.create_action_item(task="Do it")
    done = lib.update_action_item(item.id, updated_by="user", status="done")
    assert done.status == "done" and done.completed_at
    reopened = lib.update_action_item(item.id, updated_by="user", status="open")
    assert reopened.completed_at is None


def test_due_semantics(lib, library_path):
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=[{"task": "Ship", "due_date": "2026-10-16",
                                                    "due_phrase": "by Friday"}])
    item = lib.list_action_items(meeting_id=mid)[0]
    assert (item.effective_due, item.due_source) == ("2026-10-16", "claude")
    item = lib.update_action_item(item.id, updated_by="user", due="2026-10-19")
    assert (item.effective_due, item.due_source) == ("2026-10-19", "user")
    item = lib.update_action_item(item.id, updated_by="user", due=None)
    assert (item.effective_due, item.due_source, item.due_cleared) == (None, "user", True)
    item = lib.update_action_item(item.id, updated_by="user", due_reset=True)
    assert (item.effective_due, item.due_source, item.due_cleared) == ("2026-10-16", "claude", False)


def test_update_marks_touched_and_rewrites_mirror(lib):
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=["Jason — Send it (Fri)"])
    item = lib.list_action_items(meeting_id=mid)[0]
    assert not item.user_touched
    item = lib.update_action_item(item.id, updated_by="claude", task="Send it today", notes="n")
    assert item.user_touched and item.updated_by == "claude"
    assert lib.get_meeting(mid, with_segments=False).notes.action_items == ["Jason — Send it today (Fri)"]


def test_tags_replace_and_orphans_dropped(lib, library_path):
    import sqlite3
    item = lib.create_action_item(task="t", tags=["A", "B"])
    item = lib.update_action_item(item.id, updated_by="user", tags=["b", "C"])
    assert item.tags == ["B", "C"]                       # existing tag keeps its display name
    conn = sqlite3.connect(library_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM tags WHERE name='A'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tags WHERE name='B'").fetchone()[0] == 1
    finally:
        conn.close()


def test_meeting_tags_are_reported(lib):
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", tags=["Budget"], action_items=["Do"])
    assert lib.list_action_items(meeting_id=mid)[0].meeting_tags == ["Budget"]


def test_soft_delete_and_restore(lib):
    item = lib.create_action_item(task="t")
    lib.delete_action_item(item.id, updated_by="user")
    assert lib.list_action_items() == []
    assert lib.list_action_items(include_deleted=True)[0].deleted_at
    assert lib.restore_action_item(item.id, updated_by="user").deleted_at is None


def test_not_found(lib):
    for call in (lambda: lib.get_action_item(999), lambda: lib.update_action_item(999, updated_by="user", notes="x"),
                 lambda: lib.delete_action_item(999, updated_by="user")):
        with pytest.raises(ActionItemNotFound):
            call()


def test_bulk_update(lib):
    a, b = lib.create_action_item(task="a"), lib.create_action_item(task="b", tags=["X"])
    items = lib.bulk_update_action_items([a.id, float(b.id)], updated_by="user", status="done",
                                         add_tags=["Y"], remove_tags=["X"])
    assert [(i.status, i.tags) for i in items] == [("done", ["Y"]), ("done", ["Y"])]
    lib.bulk_update_action_items([a.id], updated_by="user", delete=True)
    assert [i.id for i in lib.list_action_items()] == [b.id]
    with pytest.raises(ValueError, match="500"):
        lib.bulk_update_action_items(list(range(1, 502)), updated_by="user", status="done")


def test_delete_meeting_keeps_touched_items(lib):
    mid = _meeting(lib, "Gone")
    lib.save_notes(mid, summary="s", action_items=["Untouched", "Touched", "Done"])
    items = {i.task: i for i in lib.list_action_items(meeting_id=mid)}
    lib.update_action_item(items["Touched"].id, updated_by="user", notes="mine")
    lib.update_action_item(items["Done"].id, updated_by="user", status="done")
    manual = lib.create_action_item(task="Manual", meeting_id=mid)
    lib.delete(mid)
    left = {i.task: i for i in lib.list_action_items()}
    assert set(left) == {"Touched", "Done", "Manual"}
    assert all(i.meeting_id is None for i in left.values())
    assert manual.id in {i.id for i in left.values()}


def test_get_meeting_includes_items(lib):
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=["A", "B"])
    assert [i.task for i in lib.get_meeting(mid, with_segments=False).action_items] == ["A", "B"]


def test_delete_meeting_keeps_touched_items_without_save_notes(lib):
    """Same rule as above, seeding summary items through the store directly."""
    from speakeasy import action_item_store as store, action_items as ai
    mid = _meeting(lib, "Gone")
    with lib._transaction() as conn:
        for pos, task in enumerate(("Untouched", "Touched", "Done")):
            store.insert_item(conn, ai.ItemInput(task=task), meeting_id=mid, source="summary",
                              updated_by="claude", position=pos)
    items = {i.task: i for i in lib.list_action_items(meeting_id=mid)}
    lib.update_action_item(items["Touched"].id, updated_by="user", notes="mine")
    lib.update_action_item(items["Done"].id, updated_by="user", status="done")
    manual = lib.create_action_item(task="Manual", meeting_id=mid)
    lib.delete(mid)
    left = {i.task: i for i in lib.list_action_items()}
    assert set(left) == {"Touched", "Done", "Manual"}
    assert all(i.meeting_id is None for i in left.values())
    assert manual.id in {i.id for i in left.values()}
