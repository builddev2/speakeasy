import pytest

from speakeasy import settings
from speakeasy.mcp_tools import ToolError, build_tools
from speakeasy.meeting_library import MeetingLibrary
from tests.test_library_backup import _meeting


@pytest.fixture
def env(library_path):
    lib = MeetingLibrary(library_path)
    return lib, build_tools(lib)


def run(tools, name, **args):
    return tools[name].run(args)


def test_tool_flags(env):
    _, tools = env
    assert tools["list_action_items"].read_only is True
    for name in ("create_action_item", "update_action_item", "delete_action_item"):
        assert tools[name].read_only is False


def test_save_notes_accepts_objects_and_get_meeting_returns_ids(env):
    lib, tools = env
    mid = _meeting(lib, "M")
    run(tools, "save_notes", id=mid, summary="s",
        action_items=[{"task": "Ship", "owner": "Jason", "due_date": "2026-10-16", "due_phrase": "Fri"}, "Siam — Review"])
    items = run(tools, "get_meeting", id=mid)["notes"]["action_items"]
    assert [(i["task"], i["owner"], i["due"], i["status"], i["priority"]) for i in items] == [
        ("Ship", "Jason", "2026-10-16", "open", "normal"), ("Review", "Siam", None, "open", "normal")]
    assert all(isinstance(i["id"], int) for i in items)


def test_list_filters(env):
    lib, tools = env
    settings.set_identity(name="Jason", aliases=[])
    mid = _meeting(lib, "Budget sync")
    lib.save_notes(mid, summary="s", tags=["Budget"], action_items=[
        {"task": "Mine overdue", "owner": "Jason", "due_date": "2000-01-03"},
        {"task": "Theirs", "owner": "Siam", "priority": "high"}])
    done = lib.create_action_item(task="Done one", owner="Jason")
    lib.update_action_item(done.id, updated_by="user", status="done")
    out = run(tools, "list_action_items")
    assert out["identity_set"] is True and {i["task"] for i in out["items"]} == {"Mine overdue", "Theirs"}
    assert [i["task"] for i in run(tools, "list_action_items", mine=True)["items"]] == ["Mine overdue"]
    assert [i["task"] for i in run(tools, "list_action_items", overdue=True)["items"]] == ["Mine overdue"]
    assert [i["task"] for i in run(tools, "list_action_items", status="done")["items"]] == ["Done one"]
    assert len(run(tools, "list_action_items", tag="budget")["items"]) == 2
    assert [i["task"] for i in run(tools, "list_action_items", query="THEIR")["items"]] == ["Theirs"]
    assert [i["task"] for i in run(tools, "list_action_items", sort="priority")["items"]][0] == "Theirs"
    first = run(tools, "list_action_items", limit=1)
    assert len(first["items"]) == 1 and first["next_offset"] == 1
    item = run(tools, "list_action_items", query="Mine")["items"][0]
    assert item["mine"] is True and item["meeting"]["title"] == "Budget sync"
    assert item["tags"] == ["Budget"] and item["due_source"] == "claude"


def test_owner_filter_for_the_user_matches_every_spelling(env):
    lib, tools = env
    settings.set_identity(name="Jason", aliases=[])
    lib.create_action_item(task="A", owner="Jason")
    lib.create_action_item(task="B", owner="You")
    lib.create_action_item(task="C", owner="Siam")
    for who in ("Jason", "you", "Me"):
        got = run(tools, "list_action_items", owner=who)["items"]
        assert {i["task"] for i in got} == {"A", "B"}, who
    assert [i["task"] for i in run(tools, "list_action_items", owner="siam")["items"]] == ["C"]


def test_create_update_delete_round_trip(env):
    lib, tools = env
    created = run(tools, "create_action_item", task="Call bank", due="2026-10-20", tags=["Money"])
    assert created["due"] == "2026-10-20" and created["due_source"] == "user"
    updated = run(tools, "update_action_item", id=str(created["id"]), status="done", notes="called")
    assert updated["status"] == "done" and updated["notes"] == "called"
    assert updated["due"] == "2026-10-20" and updated["tags"] == ["Money"]
    cleared = run(tools, "update_action_item", id=created["id"], due="")
    assert cleared["due"] is None
    assert lib.get_action_item(created["id"]).updated_by == "claude"
    assert run(tools, "delete_action_item", id=created["id"]) == {"deleted": created["id"]}
    assert run(tools, "list_action_items", status="all")["items"] == []
    restored = run(tools, "delete_action_item", id=created["id"], undo=True)
    assert restored["id"] == created["id"]


@pytest.mark.parametrize("name, args, message", [
    ("update_action_item", {"id": 999, "notes": "x"}, "not found"),
    ("update_action_item", {"id": 1}, "Give at least one"),
    ("create_action_item", {"task": ""}, "task"),
    ("list_action_items", {"status": "maybe"}, "status"),
    ("list_action_items", {"sort": "random"}, "sort"),
    ("update_action_item", {"id": "abc", "notes": "x"}, "id"),
])
def test_errors_are_tool_errors(env, name, args, message):
    _, tools = env
    with pytest.raises(ToolError, match=message):
        run(tools, name, **args)


def test_pending_summaries_instructions_name_the_user(env):
    _, tools = env
    settings.set_identity(name="Jason", aliases=[])
    assert 'owner "Jason"' in run(tools, "pending_summaries")["instructions"]
