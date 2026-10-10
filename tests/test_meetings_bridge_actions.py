# tests/test_meetings_bridge_actions.py
import pytest

from speakeasy import settings
from speakeasy.meeting_library import MeetingLibrary
from speakeasy.ui.meetings_bridge import MeetingsBridge
from tests.test_library_backup import _meeting


@pytest.fixture
def env(library_path):
    lib = MeetingLibrary(library_path)
    return lib, MeetingsBridge(library=lib)


def test_list_payload_shape(env):
    lib, bridge = env
    settings.set_identity(name="Jason", aliases=[])
    mid = _meeting(lib, "Sync")
    lib.save_notes(mid, summary="s", tags=["Budget"], action_items=[
        {"task": "Ship", "owner": "Jason", "due_date": "2026-10-16", "due_phrase": "Fri"}])
    out = bridge.actions_list_payload({})
    assert out["identitySet"] is True and len(out["today"]) == 10
    item = out["items"][0]
    assert set(item) == {"id", "meetingId", "meetingTitle", "meetingDate", "task", "owner", "mine",
                         "priority", "status", "completedAt", "due", "dueSource", "duePhrase",
                         "claudeDue", "notes", "tags", "meetingTags", "source", "createdAt", "updatedAt"}
    assert (item["mine"], item["due"], item["dueSource"], item["claudeDue"], item["meetingTags"]) == \
        (True, "2026-10-16", "claude", "2026-10-16", ["Budget"])


def test_update_accepts_float_id_rejects_bool(env):
    lib, bridge = env
    item = lib.create_action_item(task="t")
    out = bridge.actions_update_payload({"id": float(item.id), "status": "done", "dueReset": False})
    assert out["status"] == "done"
    with pytest.raises(ValueError):
        bridge.actions_update_payload({"id": True, "status": "done"})


def test_update_due_semantics(env):
    lib, bridge = env
    item = lib.create_action_item(task="t")
    assert bridge.actions_update_payload({"id": item.id, "due": "2026-10-20"})["due"] == "2026-10-20"
    assert bridge.actions_update_payload({"id": item.id, "due": None})["due"] is None
    assert bridge.actions_update_payload({"id": item.id, "dueReset": True})["dueSource"] is None


def test_create_delete_restore_bulk(env):
    lib, bridge = env
    created = bridge.actions_create_payload({"task": "New", "owner": "", "tags": ["X"], "priority": "low"})
    assert created["source"] == "manual" and created["tags"] == ["X"]
    assert bridge.actions_delete_payload({"id": float(created["id"])}) is True
    assert bridge.actions_list_payload({})["items"] == []
    assert bridge.actions_restore_payload({"id": created["id"]})["id"] == created["id"]
    out = bridge.actions_bulk_payload({"ids": [float(created["id"])], "status": "done", "addTags": ["Y"]})
    assert out["items"][0]["status"] == "done" and out["items"][0]["tags"] == ["X", "Y"]


def test_not_found_maps_to_error(env):
    _, bridge = env
    responses = []
    handler = MeetingsBridge._wrap(bridge.actions_update_payload)
    handler({"id": 999.0, "notes": "x"}, lambda result=None, error=None: responses.append(error))
    assert responses == ["not_found"]


def test_settings_identity_round_trip(env):
    _, bridge = env
    out = bridge.settings_set_payload({"userName": "Jason", "userAliases": ["JC"]})
    assert (out["userName"], out["userAliases"]) == ("Jason", ["JC"])


def test_meeting_detail_sends_item_payloads(env):
    lib, bridge = env
    mid = _meeting(lib, "M")
    lib.save_notes(mid, summary="s", action_items=["A"])
    detail = bridge.get_payload({"id": mid})
    assert detail["actionItems"][0]["task"] == "A" and "id" in detail["actionItems"][0]


def test_bridge_mutations_are_attributed_to_user(env):
    lib, bridge = env
    created = bridge.actions_create_payload({"task": "New"})
    assert lib.get_action_item(created["id"]).updated_by == "user"

    def claude_touch(item_id):
        lib.update_action_item(item_id, updated_by="claude", notes="c")
        assert lib.get_action_item(item_id).updated_by == "claude"

    claude_touch(created["id"])
    bridge.actions_update_payload({"id": float(created["id"]), "notes": "u"})
    assert lib.get_action_item(created["id"]).updated_by == "user"

    claude_touch(created["id"])
    bridge.actions_delete_payload({"id": float(created["id"])})
    assert lib.get_action_item(created["id"]).updated_by == "user"

    lib.restore_action_item(created["id"], updated_by="claude")
    assert lib.get_action_item(created["id"]).updated_by == "claude"
    bridge.actions_bulk_payload({"ids": [float(created["id"])], "status": "done"})
    assert lib.get_action_item(created["id"]).updated_by == "user"

    lib.delete_action_item(created["id"], updated_by="claude")
    bridge.actions_restore_payload({"id": created["id"]})
    assert lib.get_action_item(created["id"]).updated_by == "user"


def test_saving_user_name_rewrites_self_owners(env):
    lib, bridge = env
    item = lib.create_action_item(task="t", owner="You")
    assert item.owner == "You"
    bridge.settings_set_payload({"userName": "Jason"})
    assert lib.get_action_item(item.id).owner == "Jason"


def test_first_actions_list_rewrites_existing_self_owners(env):
    lib, bridge = env
    item = lib.create_action_item(task="t", owner="me")
    settings.set_identity(name="Jason", aliases=[])
    out = bridge.actions_list_payload({})
    assert out["items"][0]["owner"] == "Jason"
    assert lib.get_action_item(item.id).owner == "Jason"


def test_actions_list_survives_cleanup_failure(env, monkeypatch):
    lib, bridge = env
    lib.create_action_item(task="t")
    monkeypatch.setattr(lib, "normalize_self_owners", lambda: 1 / 0)
    assert len(bridge.actions_list_payload({})["items"]) == 1


def test_settings_save_survives_owner_cleanup_failure(env, monkeypatch):
    lib, bridge = env
    monkeypatch.setattr(lib, "normalize_self_owners", lambda: 1 / 0)
    before = bridge.settings_get_payload({})["detectCalls"]
    out = bridge.settings_set_payload({"userName": "Jason", "detectCalls": not before})
    assert out["detectCalls"] is (not before)
    assert settings.get_identity()["name"] == "Jason"


def test_first_actions_list_backfills_due_dates(env):
    lib, bridge = env
    mid = _meeting(lib, "Sync")
    lib.save_notes(mid, summary="s", action_items=[{"task": "A", "due_phrase": "by Friday"}])
    out = bridge.actions_list_payload({})
    assert out["items"][0]["due"] == "2026-10-02"


def test_actions_list_survives_backfill_failure(env, monkeypatch):
    lib, bridge = env
    lib.create_action_item(task="t")
    monkeypatch.setattr(lib, "backfill_due_dates", lambda: 1 / 0)
    assert len(bridge.actions_list_payload({})["items"]) == 1
