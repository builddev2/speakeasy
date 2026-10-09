import json
import sqlite3

from speakeasy import library_backup, meeting_store
from speakeasy.meeting_library import MeetingLibrary
from tests.test_library_backup import _meeting


def _v6_with_notes(path, items):
    """A v6 library holding one meeting whose notes carry legacy strings."""
    lib = MeetingLibrary(path)
    mid = _meeting(lib, "Legacy")
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO notes (meeting_id, summary, action_items_json, action_items_text,"
                 " updated_at, updated_by) VALUES (?, 's', ?, '', '2026-10-01T10:00:00Z', 'claude')",
                 (mid, json.dumps(items)))
    conn.execute("DROP TABLE action_item_tags")
    conn.execute("DROP TABLE action_items")
    conn.execute("PRAGMA user_version = 6")
    conn.commit()
    conn.close()
    return mid


def test_fresh_library_is_v7_with_tables(library_path):
    conn = meeting_store.connect(library_path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 7 == meeting_store.SCHEMA_VERSION
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"action_items", "action_item_tags"} <= names


def test_v6_items_import_as_open_summary_items(library_path):
    mid = _v6_with_notes(library_path, ["Jason — Send budget (Fri)", "Unassigned — Book room", "  ", "Plain task"])
    conn = meeting_store.connect(library_path)
    rows = conn.execute("SELECT task, owner, due_phrase, due_date, status, source, user_touched,"
                        " position, created_at, updated_by FROM action_items WHERE meeting_id = ?"
                        " ORDER BY position", (mid,)).fetchall()
    assert [tuple(r) for r in rows] == [
        ("Send budget", "Jason", "Fri", None, "open", "summary", 0, 0, "2026-10-01T10:00:00Z", "claude"),
        ("Book room", "", "", None, "open", "summary", 0, 1, "2026-10-01T10:00:00Z", "claude"),
        ("Plain task", "", "", None, "open", "summary", 0, 2, "2026-10-01T10:00:00Z", "claude"),
    ]


def test_migration_is_idempotent_on_reopen(library_path):
    _v6_with_notes(library_path, ["A task"])
    meeting_store.connect(library_path).close()
    # Tables kept, version rewound: a second run must not import again.
    raw = sqlite3.connect(library_path)
    raw.execute("PRAGMA user_version = 6")
    raw.commit()
    raw.close()
    conn = meeting_store.connect(library_path)
    assert conn.execute("SELECT COUNT(*) FROM action_items").fetchone()[0] == 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 7


def test_v6_backup_restores_and_imports_items(tmp_path, library_path):
    mid = _v6_with_notes(library_path, ["Jason — Send budget (Fri)"])
    backup = tmp_path / "old.speakeasy-library"
    src = sqlite3.connect(library_path)
    out = sqlite3.connect(backup)
    src.backup(out)
    out.execute("PRAGMA journal_mode = DELETE")  # a standalone, non-WAL file
    version = out.execute("PRAGMA user_version").fetchone()[0]
    out.commit()
    out.close()
    src.close()
    assert version == 6
    meeting_store.connect(library_path).close()  # the live library is upgraded as usual
    conn = meeting_store.connect(library_path)
    conn.execute("DELETE FROM action_items")
    conn.commit()
    conn.close()
    library_backup.restore_library(backup, library_path)
    conn = meeting_store.connect(library_path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
    row = conn.execute("SELECT task, owner, due_phrase FROM action_items WHERE meeting_id = ?",
                       (mid,)).fetchone()
    assert tuple(row) == ("Send budget", "Jason", "Fri")


def test_orphan_cleanup_keeps_item_only_tags(library_path):
    lib = MeetingLibrary(library_path)
    mid = _meeting(lib, "Tagged")
    lib.save_notes(mid, tags=["Budget"])
    conn = meeting_store.connect(library_path)
    tag_id = conn.execute("SELECT id FROM tags WHERE name = 'Budget'").fetchone()[0]
    conn.execute("INSERT INTO action_items (meeting_id, task, source, created_at, updated_at, updated_by)"
                 " VALUES (NULL, 'Keep tag', 'manual', 'x', 'x', 'user')")
    conn.execute("INSERT INTO action_item_tags (item_id, tag_id) VALUES (last_insert_rowid(), ?)", (tag_id,))
    conn.commit()
    conn.close()
    lib.delete(mid)  # last meeting using Budget
    conn = meeting_store.connect(library_path)
    assert conn.execute("SELECT COUNT(*) FROM tags WHERE name = 'Budget'").fetchone()[0] == 1
