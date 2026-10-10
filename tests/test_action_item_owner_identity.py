import sqlite3

import pytest

from speakeasy import action_item_store
from speakeasy.meeting_library import MeetingLibrary
from tests.test_library_backup import _meeting

JASON = lambda: {"name": "Jason", "aliases": []}  # noqa: E731


@pytest.fixture
def lib(library_path):
    return MeetingLibrary(library_path, identity=JASON)


def test_save_notes_normalises_owner(lib):
    mid = _meeting(lib, "Sync")
    lib.save_notes(mid, summary="s", action_items=[{"task": "Ship", "owner": "me"}])
    assert lib.list_action_items()[0].owner == "Jason"


def test_create_normalises_owner(lib):
    assert lib.create_action_item(task="t", owner="You").owner == "Jason"


def test_update_normalises_owner(lib):
    item = lib.create_action_item(task="t", owner="Priya")
    assert lib.update_action_item(item.id, updated_by="user", owner="mine").owner == "Jason"


def test_normalize_self_owners_rewrites_old_rows(library_path):
    plain = MeetingLibrary(library_path, identity=lambda: {"name": "", "aliases": []})
    mid = _meeting(plain, "Sync")
    plain.save_notes(mid, summary="s", action_items=[
        {"task": "A", "owner": "You"}, {"task": "B", "owner": "Refayet & me"},
        {"task": "C", "owner": "Priya"}])
    assert plain.normalize_self_owners() == 0
    assert [i.owner for i in plain.list_action_items()] == ["You", "Refayet & me", "Priya"]
    lib = MeetingLibrary(library_path, identity=JASON)
    before = {i.task: i for i in lib.list_action_items()}
    assert lib.normalize_self_owners() == 2
    after = {i.task: i for i in lib.list_action_items()}
    assert [after[t].owner for t in "ABC"] == ["Jason", "Refayet & Jason", "Priya"]
    for t in "ABC":
        assert after[t].user_touched is False
        assert (after[t].updated_by, after[t].updated_at) == (before[t].updated_by, before[t].updated_at)
    conn = sqlite3.connect(library_path)
    text, = conn.execute("SELECT action_items_text FROM notes WHERE meeting_id = ?", (mid,)).fetchone()
    touched = [r[0] for r in conn.execute("SELECT user_touched FROM action_items")]
    conn.close()
    assert "Jason" in text and "You" not in text and "me" not in text.split()
    assert touched == [0, 0, 0]
    assert lib.normalize_self_owners() == 0


def test_normalize_includes_soft_deleted(library_path):
    plain = MeetingLibrary(library_path, identity=lambda: {"name": "", "aliases": []})
    item = plain.create_action_item(task="t", owner="me")
    plain.delete_action_item(item.id, updated_by="user")
    lib = MeetingLibrary(library_path, identity=JASON)
    assert lib.normalize_self_owners() == 1
    assert lib.list_action_items(include_deleted=True)[0].owner == "Jason"


def test_no_identity_name_changes_nothing(library_path):
    lib = MeetingLibrary(library_path, identity=lambda: {"name": "", "aliases": []})
    item = lib.create_action_item(task="t", owner="me")
    assert item.owner == "me"
    assert lib.normalize_self_owners() == 0


def test_backfill_due_dates(library_path, monkeypatch):
    lib = MeetingLibrary(library_path)
    mid = _meeting(lib, "Sync")  # Wednesday 2026-09-30
    lib.save_notes(mid, summary="s", action_items=[
        {"task": "A", "due_phrase": "by Friday"}, {"task": "B", "due_phrase": "whenever"},
        {"task": "C", "due_phrase": "Fri", "due_date": "2026-10-09"}])
    manual = lib.create_action_item(task="M", meeting_id=mid)
    conn = sqlite3.connect(library_path)
    conn.execute("UPDATE action_items SET due_phrase = 'by Friday' WHERE id = ?", (manual.id,))
    conn.commit()
    conn.close()
    lib.save_notes(mid, summary="s", action_items=[
        {"task": "A", "due_phrase": "by Friday"}, {"task": "B", "due_phrase": "whenever"},
        {"task": "C", "due_phrase": "Fri", "due_date": "2026-10-09"},
        {"task": "T", "due_phrase": "by Friday"}])
    t = {i.task: i for i in lib.list_action_items()}["T"]
    lib.update_action_item(t.id, updated_by="user", status="done")
    before = {i.task: i for i in lib.list_action_items()}
    synced = []
    real = action_item_store.sync_mirror
    monkeypatch.setattr(action_item_store, "sync_mirror", lambda conn, m: (synced.append(m), real(conn, m)))
    assert lib.backfill_due_dates() == 1
    assert synced == [mid]
    after = {i.task: i for i in lib.list_action_items()}
    assert after["A"].due_date == "2026-10-02"
    assert after["B"].due_date is None and after["M"].due_date is None
    assert after["C"].due_date == "2026-10-09" and after["T"].due_date is None
    assert (after["A"].updated_by, after["A"].updated_at, after["A"].user_touched) == \
        (before["A"].updated_by, before["A"].updated_at, before["A"].user_touched)
    assert lib.backfill_due_dates() == 0
