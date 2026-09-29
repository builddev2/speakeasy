"""One-time import of legacy JSON meetings into the SQLite library.

The JSON files stay untouched until the whole batch has been inserted and
verified in one transaction; only then are they moved, unchanged, to
meetings/legacy-json/. Files that fail to parse stay where they are and are
reported. Re-running is safe: meetings already in the library are not
inserted again, only archived.

Legacy `created` stamps were taken when processing *finished*, so the start
is estimated as created − duration and every imported meeting is marked
timestamps_approximate.
"""

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import meetings, settings
from .meeting_library import (
    ImportVerificationError, MeetingLibrary, NewMeeting, nonspace_len,
)
from .meetings import MeetingSegment

__all__ = ["ImportVerificationError", "ImportReport", "LEGACY_DIR_NAME",
           "legacy_files", "split_long_segment", "import_json_meetings"]

LEGACY_DIR_NAME = "legacy-json"


@dataclass
class ImportReport:
    imported: int = 0
    already_present: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)


def legacy_files():
    return sorted(
        p for p in settings.meetings_dir().glob("*.json")
        if meetings._ID_RE.fullmatch(p.stem)
    )


def split_long_segment(segment, *, threshold_seconds=120.0, target_seconds=60.0):
    duration = segment.end - segment.start
    text = segment.text.strip()
    if duration <= threshold_seconds or not text:
        return [segment]
    budget = max(1, int(len(text) * target_seconds / duration))
    pieces, current = [], []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        parts = sentence.split() if len(sentence) > budget else [sentence]
        for part in parts:
            if current and len(" ".join(current + [part])) > budget:
                pieces.append(" ".join(current))
                current = []
            current.append(part)
    if current:
        pieces.append(" ".join(current))
    per_char = duration / sum(len(p) for p in pieces)
    result, cursor = [], segment.start
    for i, piece in enumerate(pieces):
        end = segment.end if i == len(pieces) - 1 else cursor + len(piece) * per_char
        result.append(MeetingSegment(
            speaker=segment.speaker, start=cursor, end=end, text=piece,
            confidence=segment.confidence, overlap=segment.overlap,
            profile_id=segment.profile_id, cluster_id=segment.cluster_id))
        cursor = end
    return result


def _validate_segment(seg: MeetingSegment) -> None:
    """Meeting.load casts speaker/text/start/end/confidence/overlap, but not
    profile_id -- a hand-edited file can slip a dict or list through as
    profile_id, and it would only fail deep inside sqlite3's INSERT (which
    runs after every file has already been queued), aborting the whole
    batch instead of just the one bad file. Check the segment-level fields
    the DB insert needs here (see _convert for the top-level `title` check),
    inside the per-file try in import_json_meetings, so a bad value is
    skipped and reported the same way a malformed file is."""
    if not isinstance(seg.speaker, str):
        raise TypeError(f"segment speaker must be str, got {type(seg.speaker).__name__}")
    if not isinstance(seg.text, str):
        raise TypeError(f"segment text must be str, got {type(seg.text).__name__}")
    for name, value in (("start", seg.start), ("end", seg.end)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"segment {name} must be a number, got {type(value).__name__}")
    if seg.confidence is not None and (
        isinstance(seg.confidence, bool) or not isinstance(seg.confidence, (int, float))
    ):
        raise TypeError(
            f"segment confidence must be a number or None, got {type(seg.confidence).__name__}")
    if not isinstance(seg.overlap, (bool, int)):
        raise TypeError(f"segment overlap must be bool or int, got {type(seg.overlap).__name__}")
    if seg.profile_id is not None and not isinstance(seg.profile_id, str):
        raise TypeError(
            f"segment profile_id must be str or None, got {type(seg.profile_id).__name__}")


def _archive_target(archive_dir: Path, name: str) -> Path:
    """Never silently clobber an existing archived original: a re-run can
    find a file already archived under this name (a hand-restored copy, or
    another meeting that happens to share a legacy filename), and
    os.replace would otherwise destroy whatever is already there. Pick a
    fresh, still-unique name instead of overwriting."""
    target = archive_dir / name
    if not target.exists():
        return target
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 1
    while (archive_dir / f"{stem}.{n}{suffix}").exists():
        n += 1
    return archive_dir / f"{stem}.{n}{suffix}"


def _convert(legacy) -> NewMeeting:
    for seg in legacy.segments:
        _validate_segment(seg)
    # Meeting.load passes a truthy top-level `title` through unchanged
    # (falling back to the meeting id only when it's falsy), so a
    # hand-edited file with e.g. "title": ["x"] or "title": 2024 reaches
    # here uncast. Left unchecked, that only fails deep inside _insert's
    # `(new.title or "").strip()` with a raw AttributeError, after the
    # whole batch has already been queued -- aborting every file, not just
    # this one. Catch it here instead, inside the per-file try.
    if not isinstance(legacy.title, str):
        raise TypeError(f"title must be str, got {type(legacy.title).__name__}")
    if legacy.created:
        finished = datetime.fromisoformat(legacy.created)
    else:
        finished = datetime.strptime(legacy.meeting_id[:15], "%Y%m%d-%H%M%S")
    # The old app (meetings.Meeting.new) stamped both `created` and the
    # default title when processing *finished*, using the same strftime
    # format. If the title still matches that computed default exactly, it
    # was never renamed, so regenerate it (title=None) from the *estimated
    # start* below instead of importing the stale end-time title verbatim.
    # Any other title (including a user rename) is kept unchanged.
    old_default = f"Meeting — {finished.strftime('%b %-d, %-I:%M %p')}"
    title = None if legacy.title == old_default else legacy.title
    if finished.tzinfo is None:
        finished = finished.astimezone()  # the Mac's zone on that date (DST-aware)
    segments = [
        piece for seg in legacy.segments for piece in split_long_segment(seg)
    ]
    return NewMeeting(
        segments=segments,
        duration_seconds=legacy.duration_seconds,
        started_at=finished - timedelta(seconds=legacy.duration_seconds),
        title=title,
        meeting_id=legacy.meeting_id,
        capture_mode=legacy.capture_mode,
        system_audio_status=legacy.system_audio_status,
        capture_scope=legacy.capture_scope,
        track_offsets_seconds=legacy.track_offsets_seconds,
        capture_health=legacy.capture_health,
        source="imported_json",
        timestamps_approximate=True,
    )


def import_json_meetings(library=None, progress=None) -> ImportReport:
    library = library or MeetingLibrary()
    progress = progress or (lambda done, total: None)
    files = legacy_files()
    present = library.meeting_ids()
    report = ImportReport()
    batch, expected, to_archive = [], {}, []
    for done, path in enumerate(files, 1):
        try:
            legacy = meetings.Meeting.load(path.stem)
            if legacy.meeting_id in present:
                report.already_present += 1
            else:
                batch.append(_convert(legacy))
                # Verification (below) never compares titles: only content
                # (non-whitespace char count) and the speaker set, both of
                # which the title regeneration above does not touch.
                expected[legacy.meeting_id] = (
                    sum(nonspace_len(s.text) for s in legacy.segments),
                    frozenset(s.speaker for s in legacy.segments),
                )
            to_archive.append(path)
        except Exception as err:  # hand-edited JSON can fail in any way
            report.skipped.append((path.name, type(err).__name__))
        progress(done, len(files))
    library.import_meetings(batch, expected)
    report.imported = len(batch)
    archive = settings.meetings_dir() / LEGACY_DIR_NAME
    archive.mkdir(exist_ok=True)
    for path in to_archive:
        os.replace(path, _archive_target(archive, path.name))
    return report
