"""SQL for action items. Every function takes an open connection inside the
caller's transaction; MeetingLibrary wraps each one (spec §5)."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from . import action_items as ai


class ActionItemNotFound(LookupError):
    pass


@dataclass
class ActionItem:
    id: int
    meeting_id: str | None
    meeting_title: str | None
    meeting_started_at: str | None
    meeting_tz_offset_minutes: int | None
    task: str
    owner: str
    priority: str
    status: str
    completed_at: str | None
    due_date: str | None
    due_phrase: str
    due_override: str | None
    due_cleared: bool
    effective_due: str | None
    due_source: str | None
    notes: str
    source: str
    user_touched: bool
    tags: list[str]
    meeting_tags: list[str]
    position: int
    created_at: str
    updated_at: str
    updated_by: str
    deleted_at: str | None


_UPDATABLE = {"task", "owner", "priority", "status", "notes", "due_override",
              "due_cleared", "deleted_at", "completed_at"}
_SELECT = ("SELECT a.*, m.title AS meeting_title, m.started_at AS meeting_started_at,"
           " m.tz_offset_minutes AS meeting_tz FROM action_items a"
           " LEFT JOIN meetings m ON m.id = a.meeting_id")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _names(conn, sql, params=()) -> dict:
    out: dict = {}
    for key, name in conn.execute(sql, params):
        out.setdefault(key, []).append(name)
    return out


def _rows_to_items(conn, rows) -> list[ActionItem]:
    if not rows:
        return []
    own = _names(conn, "SELECT at.item_id, t.name FROM action_item_tags at"
                       " JOIN tags t ON t.id = at.tag_id ORDER BY t.name COLLATE NOCASE")
    meeting = _names(conn, "SELECT mt.meeting_id, t.name FROM meeting_tags mt"
                           " JOIN tags t ON t.id = mt.tag_id ORDER BY t.name COLLATE NOCASE")
    items = []
    for r in rows:
        due, source = ai.effective_due(r["due_date"], r["due_override"], r["due_cleared"])
        items.append(ActionItem(
            id=r["id"], meeting_id=r["meeting_id"], meeting_title=r["meeting_title"],
            meeting_started_at=r["meeting_started_at"], meeting_tz_offset_minutes=r["meeting_tz"],
            task=r["task"], owner=r["owner"], priority=r["priority"], status=r["status"],
            completed_at=r["completed_at"], due_date=r["due_date"], due_phrase=r["due_phrase"],
            due_override=r["due_override"], due_cleared=bool(r["due_cleared"]),
            effective_due=due, due_source=source, notes=r["notes"], source=r["source"],
            user_touched=bool(r["user_touched"]), tags=own.get(r["id"], []),
            meeting_tags=meeting.get(r["meeting_id"], []), position=r["position"],
            created_at=r["created_at"], updated_at=r["updated_at"],
            updated_by=r["updated_by"], deleted_at=r["deleted_at"]))
    return items


def list_items(conn, *, meeting_id=None, include_deleted=False) -> list[ActionItem]:
    where, params = [], []
    if meeting_id is not None:
        where.append("a.meeting_id = ?")
        params.append(meeting_id)
    if not include_deleted:
        where.append("a.deleted_at IS NULL")
    sql = _SELECT + (" WHERE " + " AND ".join(where) if where else "") + \
        " ORDER BY a.meeting_id IS NULL, m.started_at DESC, a.position, a.id"
    return _rows_to_items(conn, conn.execute(sql, params).fetchall())


def get_item(conn, item_id: int) -> ActionItem:
    rows = conn.execute(_SELECT + " WHERE a.id = ?", (item_id,)).fetchall()
    if not rows:
        raise ActionItemNotFound(item_id)
    return _rows_to_items(conn, rows)[0]


def insert_item(conn, inp: ai.ItemInput, *, meeting_id, source, updated_by, position=0,
                notes="", due_override=None, touched=False) -> int:
    now = _now()
    return conn.execute(
        "INSERT INTO action_items (meeting_id, task, owner, priority, due_date, due_phrase,"
        " due_override, notes, source, user_touched, position, created_at, updated_at, updated_by)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (meeting_id, inp.task, inp.owner, inp.priority, inp.due_date, inp.due_phrase,
         due_override, notes, source, int(touched), position, now, now, updated_by)).lastrowid


def set_own_tags(conn, item_id: int, names, resolve) -> None:
    conn.execute("DELETE FROM action_item_tags WHERE item_id = ?", (item_id,))
    for name in names:
        conn.execute("INSERT OR IGNORE INTO action_item_tags (item_id, tag_id) VALUES (?, ?)",
                     (item_id, resolve(conn, name)))


def update_item(conn, item_id: int, sets: dict, *, updated_by, tags=None, resolve=None) -> None:
    row = conn.execute("SELECT meeting_id, status FROM action_items WHERE id = ?",
                       (item_id,)).fetchone()
    if row is None:
        raise ActionItemNotFound(item_id)
    sets = dict(sets)
    if not set(sets) <= _UPDATABLE:  # column names come from code, never from callers
        raise ValueError(f"Cannot update columns: {sorted(set(sets) - _UPDATABLE)}")
    if "status" in sets and sets["status"] != row["status"]:
        sets["completed_at"] = _now() if sets["status"] == "done" else None
    sets.update(user_touched=1, updated_at=_now(), updated_by=updated_by)
    cols = ", ".join(f"{k} = ?" for k in sets)
    conn.execute(f"UPDATE action_items SET {cols} WHERE id = ?", (*sets.values(), item_id))
    if tags is not None:
        set_own_tags(conn, item_id, tags, resolve)
    if row["meeting_id"]:
        sync_mirror(conn, row["meeting_id"])


def sync_mirror(conn, meeting_id: str) -> None:
    """notes.action_items_json/_text follow the items (search + old readers).
    Only an existing notes row is updated: creating one would make an
    unsummarised meeting look summarised."""
    rows = conn.execute(
        "SELECT owner, task, due_phrase, due_date, due_override, due_cleared FROM action_items"
        " WHERE meeting_id = ? AND deleted_at IS NULL ORDER BY position, id",
        (meeting_id,)).fetchall()
    lines = [ai.format_mirror(r["owner"], r["task"], r["due_phrase"],
                              ai.effective_due(r["due_date"], r["due_override"], r["due_cleared"])[0])
             for r in rows]
    conn.execute("UPDATE notes SET action_items_json = ?, action_items_text = ? WHERE meeting_id = ?",
                 (json.dumps(lines), "\n".join(lines), meeting_id))


def delete_untouched_for_meeting(conn, meeting_id: str) -> None:
    conn.execute("DELETE FROM action_items WHERE meeting_id = ? AND source = 'summary'"
                 " AND user_touched = 0 AND status = 'open'", (meeting_id,))
