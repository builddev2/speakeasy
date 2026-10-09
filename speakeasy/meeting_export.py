"""Plain-text way out of the SQLite library (`--export-meetings`): one
Markdown file per meeting for reading and troubleshooting."""

import re
import shutil
import sqlite3
import tempfile
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from . import library_lease, meeting_store
from .meeting_library import NOTES_LINE_PREFIX, MeetingLibrary, MeetingNotFound
from .meetings import render_md

_UNSAFE = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


def _collision_key(name: str) -> str:
    # macOS's default filesystem (APFS) is case-insensitive and normalises
    # Unicode when comparing filenames, so "Standup"/"standup" or NFC/NFD
    # "café" are the SAME file on disk even though they differ as Python
    # strings. Compare candidates on a normalised, case-folded key so the
    # disambiguation loop below catches these collisions too, not just
    # byte-for-byte duplicates.
    return unicodedata.normalize("NFC", name).casefold()


def safe_filename(title: str, fallback: str) -> str:
    name = _UNSAFE.sub("-", title).strip(" .")
    name = re.sub(r"\s+", " ", name)[:120].strip(" .")
    return name or fallback


def format_elapsed(seconds: float) -> str:
    """0:03, 12:40, 1:02:15 — matches the page's formatElapsed."""
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, ss = divmod(rem, 60)
    return f"{h}:{m:02d}:{ss:02d}" if h else f"{m}:{ss:02d}"


def notes_section(stored) -> list[str]:
    """'## My notes' with each stamped line prefixed '[12:40] ' after its list marker."""
    notes = getattr(stored, "user_notes", None)
    if not notes or not notes.markdown.strip():
        return []
    stamps = dict(notes.stamps)
    lines = []
    for i, line in enumerate(notes.markdown.split("\n")):
        if i in stamps:
            m = NOTES_LINE_PREFIX.match(line)
            cut = m.end() if m else 0
            line = f"{line[:cut]}[{format_elapsed(stamps[i])}] {line[cut:]}"
        lines.append(line)
    return ["## My notes", "", *lines, ""]


def _due_text(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d:%a} {d.day} {d:%b %Y}"


def format_item_md(item) -> list[str]:
    parts = [f"- [{'x' if item.status == 'done' else ' '}] {item.task}"]
    if item.owner:
        parts.append(item.owner)
    head = " — ".join(parts)
    extras = []
    if item.effective_due:
        due = f"due {_due_text(item.effective_due)}"
        extras.append(f'{due} ("{item.due_phrase}")' if item.due_phrase else due)
    if item.priority != "normal":
        extras.append(item.priority)
    lines = [" · ".join([head, *extras])]
    if item.notes:
        first, *rest = item.notes.split("\n")
        lines += [f"  Notes: {first}", *[f"  {r}" for r in rest]]
    return lines


def _item_with_source(item) -> list[str]:
    lines = format_item_md(item)
    if item.meeting_id and item.meeting_title and item.meeting_started_at:
        started = datetime.fromisoformat(item.meeting_started_at.replace("Z", "+00:00"))
        local = started.astimezone(timezone(timedelta(minutes=item.meeting_tz_offset_minutes or 0)))
        lines.insert(1, f"  From: {item.meeting_title} ({local:%Y-%m-%d})")
    return lines


def _write_action_items_file(folder: Path, library) -> None:
    items = library.list_action_items()
    if not items:
        return
    lines = ["# Action items", ""]
    for heading, done in (("Open", False), ("Completed", True)):
        group = [i for i in items if (i.status == "done") == done]
        lines += [f"## {heading}", ""]
        for item in group:
            lines += _item_with_source(item)
        lines.append("")
    (folder / "Action items.md").write_text("\n".join(lines), encoding="utf-8")


def render_export_md(stored) -> str:
    body = render_md(stored)
    title_line, _, rest = body.partition("\n")
    extra = []
    if stored.notes and stored.notes.summary:
        extra += ["## Summary", "", stored.notes.summary, ""]
    if getattr(stored, "action_items", None):
        extra += ["## Action items", ""]
        for item in stored.action_items:
            extra += format_item_md(item)
        extra.append("")
    elif stored.notes and stored.notes.action_items:
        extra += ["## Action items", ""] + [f"- [ ] {a}" for a in stored.notes.action_items] + [""]
    extra += notes_section(stored)
    if stored.tags:
        extra += [f"Tags: {', '.join(stored.tags)}", ""]
    if stored.people:
        extra += [f"People: {', '.join(stored.people)}", ""]
    if stored.timestamps_approximate:
        extra += ["_Timestamps are approximate (imported meeting)._", ""]
    if extra:
        extra += ["## Transcript", ""]
    return "\n".join([title_line, *([""] + extra if extra else []), rest.lstrip("\n")])


def export_all(folder: Path, library=None) -> int:
    library = library or MeetingLibrary()
    folder = Path(folder).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    count = 0
    offset = 0
    # Collision keys for names already written *this run*, not files already
    # on disk: re-running an export into the same folder must overwrite each
    # meeting's own file in place, not pile up "(2)", "(3)", ... copies of
    # everything every time.
    used_keys = set()
    while batch := library.list_meetings(limit=500, offset=offset):
        for summary in batch:
            try:
                stored = library.get_meeting(summary.meeting_id)
            except MeetingNotFound:
                # Deleted between the list() page and this get(); skip it
                # rather than crashing the rest of the export.
                continue
            name = safe_filename(stored.title, stored.meeting_id)
            day = stored.local_start.strftime("%Y-%m-%d")
            base = f"{day} {name}"
            # Two meetings can share a title on the same day; a numeric
            # suffix keeps the second export from silently overwriting the
            # first instead of losing a meeting's transcript.
            candidate = base
            suffix = 2
            while _collision_key(candidate) in used_keys:
                candidate = f"{base} ({suffix})"
                suffix += 1
            used_keys.add(_collision_key(candidate))
            (folder / f"{candidate}.md").write_text(render_export_md(stored), encoding="utf-8")
            count += 1
        offset += len(batch)
    _write_action_items_file(folder, library)
    return count


def export_snapshot(parent: Path, library=None) -> tuple[Path, int]:
    """Publish a complete Markdown export from one consistent library view."""
    library = library or MeetingLibrary()
    parent = Path(parent)
    if not parent.is_dir():
        raise NotADirectoryError(parent)
    stem = f"Speakeasy meetings {datetime.now().astimezone():%Y-%m-%d %H-%M-%S} {uuid4().hex[:8]}"
    destination = parent / stem
    suffix = 2
    while destination.exists():
        destination = parent / f"{stem} ({suffix})"
        suffix += 1
    staging = Path(tempfile.mkdtemp(prefix=".Speakeasy-export-", dir=parent))
    try:
        with tempfile.TemporaryDirectory(prefix="speakeasy-snapshot-") as temp:
            snapshot = Path(temp) / "library.sqlite3"
            source = meeting_store.connect(library._path)
            try:
                target = sqlite3.connect(snapshot)
                try:
                    source.backup(target)
                finally:
                    target.close()
            finally:
                source.close()
            try:
                count = export_all(staging, MeetingLibrary(snapshot))
            finally:
                library_lease.release_private(snapshot)
        # A concurrent export can pick the same name; never replace its output.
        while destination.exists():
            destination = parent / f"{stem} ({suffix})"
            suffix += 1
        staging.rename(destination)
        return destination, count
    except BaseException:
        shutil.rmtree(staging)
        raise
