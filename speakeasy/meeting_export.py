"""Plain-text way out of the SQLite library (`--export-meetings`): one
Markdown file per meeting, for backups and troubleshooting."""

import re
from pathlib import Path

from .meeting_library import MeetingLibrary
from .meetings import render_md

_UNSAFE = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


def safe_filename(title: str, fallback: str) -> str:
    name = _UNSAFE.sub("-", title).strip(" .")
    name = re.sub(r"\s+", " ", name)[:120].strip(" .")
    return name or fallback


def render_export_md(stored) -> str:
    body = render_md(stored)
    title_line, _, rest = body.partition("\n")
    extra = []
    if stored.notes and stored.notes.summary:
        extra += ["## Summary", "", stored.notes.summary, ""]
    if stored.notes and stored.notes.action_items:
        extra += ["## Action items", ""] + [f"- [ ] {a}" for a in stored.notes.action_items] + [""]
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
    while batch := library.list_meetings(limit=500, offset=offset):
        for summary in batch:
            stored = library.get_meeting(summary.meeting_id)
            name = safe_filename(stored.title, stored.meeting_id)
            day = stored.local_start.strftime("%Y-%m-%d")
            path = folder / f"{day} {name}.md"
            # Two meetings can share a title on the same day; a numeric
            # suffix keeps the second export from silently overwriting the
            # first instead of losing a meeting's transcript.
            suffix = 2
            while path.exists():
                path = folder / f"{day} {name} ({suffix}).md"
                suffix += 1
            path.write_text(render_export_md(stored), encoding="utf-8")
            count += 1
        offset += len(batch)
    return count
