"""The meeting library: the only API for stored meetings.

Called by the engine's save path, the JSON importer, the CLI, the Meetings
window bridge and (phase 2) the MCP server. Pure Python over meeting_store
with no AppKit import, so a future local web server can call it unchanged.
Times cross this boundary as UTC ISO strings ending in 'Z' plus the local
UTC offset captured at recording start.
"""

import json
import math
import re
import secrets
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

from . import meeting_store
from .meetings import MeetingSegment, _ID_RE, filter_capture_health
from .search_dates import parse_date_phrase
from .tag_names import (MAX_DESCRIPTION_CHARS, MAX_NEW_TAGS_PER_SAVE,
                        MAX_TAGS_PER_MEETING, clean_tag_name, clean_tag_names, tag_slug)

_MAX_PAGE_CHARS = 60_000
_MIN_PAGE_CHARS = 1_000

# Snippet highlight markers (SQLite's snippet() char() arguments); consumers
# convert these into their own markup before display.
HIT_OPEN, HIT_CLOSE = "\x02", "\x03"
_MAX_QUERY_TERMS = 12
# Echoes across mic + system-audio tracks land within a few seconds of each
# other but are rarely frame-identical, so a time-overlap check alone would
# both miss real echoes and merge unrelated back-to-back remarks.
_ECHO_SLACK_SECONDS = 5.0
_ECHO_MIN_RATIO = 0.8


def fts_query(text: str, *, any_term: bool = False) -> str | None:
    """Turn free text into a safe FTS5 query: every word becomes a quoted
    string, so FTS operators and punctuation in user or Claude input can
    never raise a syntax error or change the query's meaning."""
    terms = re.findall(r"\w+", str(text))[:_MAX_QUERY_TERMS]
    if not terms:
        return None
    return (" OR " if any_term else " ").join(f'"{t}"' for t in terms)


def _today() -> date:
    """The local calendar day a date phrase like "Oct 3" is resolved against
    (a function so tests can pin it)."""
    return date.today()


def mark_title(title: str, words: list[str]) -> str:
    """Wrap every case-insensitive occurrence of each word in HIT_OPEN/
    HIT_CLOSE, merging overlaps, for a title hit's snippet."""
    spans = sorted(m.span() for w in words if w
                   for m in re.finditer(re.escape(w), title, re.I))
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    out, pos = [], 0
    for start, end in merged:
        out += [title[pos:start], HIT_OPEN, title[start:end], HIT_CLOSE]
        pos = end
    return "".join(out) + title[pos:]


class MeetingNotFound(KeyError):
    pass


class ImportVerificationError(RuntimeError):
    pass


def nonspace_len(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_iso() -> str:
    return utc_iso(datetime.now(timezone.utc))


# A meeting is waiting for a summary when the user asked (summary_requests)
# or it is recent, long enough to matter and still has no summary. The
# window keeps the pre-feature backlog from being summarised automatically.
PENDING_WINDOW_DAYS = 7
PENDING_MIN_SECONDS = 120
_PENDING_SQL = (
    "(r.meeting_id IS NOT NULL OR (m.started_at >= ? AND m.duration_seconds >= ?"
    " AND NOT EXISTS (SELECT 1 FROM notes n WHERE n.meeting_id = m.id"
    " AND n.summary <> '')))")


def local_start(started_at: str, tz_offset_minutes: int) -> datetime:
    utc = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    return utc.astimezone(timezone(timedelta(minutes=tz_offset_minutes)))


def local_day_bounds(from_date, to_date):
    """'YYYY-MM-DD' local dates (inclusive) -> UTC ISO [lower, upper)."""
    lower = upper = None
    if from_date:
        lower = utc_iso(datetime.fromisoformat(from_date).astimezone())
    if to_date:
        upper = utc_iso(
            (datetime.fromisoformat(to_date) + timedelta(days=1)).astimezone()
        )
    return lower, upper


def _check_id(meeting_id: str) -> None:
    # The id reaches SQL only as a bound parameter, but it is also a
    # filename in exports and legacy JSON; keep the one accepted shape.
    if not _ID_RE.fullmatch(str(meeting_id)):
        raise ValueError(f"Invalid meeting id: {meeting_id!r}")


@dataclass(frozen=True)
class EventPerson:
    name: str
    email: str | None
    role: str  # "attendee" | "organizer"


@dataclass(frozen=True)
class SyncedEvent:
    """One Calendar.app occurrence as calendar_sync hands it over. It has no
    notes, location or URL fields on purpose: those often hold dial-in codes
    and passwords, and are never stored."""
    key: str
    calendar_name: str
    title: str
    start: datetime
    end: datetime
    all_day: bool
    declined: bool
    other_attendees: int | None
    people: tuple[EventPerson, ...] = ()


@dataclass
class NewMeeting:
    segments: list[MeetingSegment]
    duration_seconds: float
    started_at: datetime
    title: str | None = None
    meeting_id: str | None = None
    capture_mode: str = "mic_only"
    system_audio_status: str = "unavailable"
    capture_scope: str = "mic_only"
    track_offsets_seconds: dict = field(default_factory=lambda: {"mic": 0.0})
    capture_health: dict = field(default_factory=dict)
    calendar_event_id: str | None = None
    source: str = "recorded"
    timestamps_approximate: bool = False
    people: list[EventPerson] = field(default_factory=list)


@dataclass
class Notes:
    summary: str
    action_items: list[str]
    updated_at: str
    updated_by: str
    # Filled only on the Notes that save_notes returns: what its tags did.
    created_tags: list[str] = field(default_factory=list)
    suppressed_tags: list[str] = field(default_factory=list)


@dataclass
class MeetingSummary:
    meeting_id: str
    title: str
    started_at: str
    tz_offset_minutes: int
    duration_seconds: float
    speaker_count: int
    has_summary: bool
    timestamps_approximate: bool
    tags: list[str]
    people: list[str]
    # Distinct segment speakers, in order of first appearance. Carried on
    # the summary (not just the count) so the Meetings window bridge can
    # build a "You, Speaker 1"-style subtitle for a listed meeting without
    # a second per-row query for its full transcript.
    speakers: list[str] = field(default_factory=list)

    @property
    def local_start(self) -> datetime:
        return local_start(self.started_at, self.tz_offset_minutes)


USER_NOTES_MAX_CHARS = 200_000

# Line markers the notepad's Markdown uses (see frontend/src/meetings/notesMarkdown.ts).
NOTES_LINE_PREFIX = re.compile(r"^\s*(?:#{1,2} |[-*] \[[ xX]\] |[-*] |\d+\. )")
_MD_INLINE = re.compile(r"\\(.)|\*+")


def notes_plain_text(markdown: str) -> str:
    """The notepad's Markdown as plain lines for search: list/heading markers
    and emphasis removed, backslash escapes resolved, blank lines dropped."""
    out = []
    for line in markdown.splitlines():
        if not line.startswith("\\"):
            line = NOTES_LINE_PREFIX.sub("", line)
        line = _MD_INLINE.sub(lambda m: m.group(1) or "", line).strip()
        if line:
            out.append(line)
    return "\n".join(out)


def _check_stamps(stamps) -> list[tuple[int, float]]:
    """[(line, seconds)] with a non-negative int line and number; else ValueError."""
    out = []
    for item in stamps or []:
        try:
            line, seconds = item
        except (TypeError, ValueError):
            raise ValueError("Invalid note stamps") from None
        # WKWebView hands every JS number over as a float (line 0 arrives as 0.0).
        if isinstance(line, float) and math.isfinite(line) and line.is_integer():
            line = int(line)
        if (isinstance(line, bool) or not isinstance(line, int) or line < 0
                or isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                or not math.isfinite(seconds) or seconds < 0):
            raise ValueError("Invalid note stamps")
        out.append((line, round(float(seconds), 1)))
    return out


@dataclass
class UserNotes:
    markdown: str
    stamps: list[tuple[int, float]]
    updated_at: str


DRAFT_EARLY_WINDOW = timedelta(minutes=10)


@dataclass
class NoteDraft:
    markdown: str
    stamps: list[tuple[int, float]]   # seconds since started_at
    started_at: str                   # UTC ISO, the recording's start
    updated_at: str


def _adopt_stamps(stamps, drafted: datetime, meeting_start: datetime) -> list[tuple[int, float]]:
    """Re-base draft stamps (seconds since the draft's start) onto the meeting."""
    shift = (drafted - meeting_start).total_seconds()
    return [(line, max(0.0, round(s + shift, 1))) for line, s in stamps]


@dataclass
class StoredMeeting:
    meeting_id: str
    title: str
    started_at: str
    tz_offset_minutes: int
    duration_seconds: float
    segments: list[MeetingSegment]
    capture_mode: str
    system_audio_status: str
    capture_scope: str
    track_offsets_seconds: dict
    capture_health: dict
    calendar_event_id: str | None
    source: str
    timestamps_approximate: bool
    notes: Notes | None
    tags: list[str]
    people: list[str]
    speakers: list[str] = field(default_factory=list)
    segment_count: int = 0
    user_notes: UserNotes | None = None

    @property
    def local_start(self) -> datetime:
        return local_start(self.started_at, self.tz_offset_minutes)

    @property
    def created(self) -> str:
        # Duck-types the legacy Meeting for meetings.render_txt/render_md.
        return self.local_start.replace(tzinfo=None).isoformat(timespec="seconds")


@dataclass
class TranscriptPage:
    segments: list[tuple[int, MeetingSegment]]
    next_cursor: int | None
    title: str = ""
    timestamps_approximate: bool = False


@dataclass
class SearchHit:
    meeting_id: str
    title: str
    started_at: str
    tz_offset_minutes: int
    kind: str  # "meeting", "transcript", "notes" or "user_notes"
    speaker: str | None
    start_seconds: float | None
    end_seconds: float | None
    segment_index: int | None
    snippet: str
    also_speakers: list[str]
    score: float


@dataclass
class MeetingHits:
    """search_grouped: one meeting's matches, ranked by its best passage."""
    meeting_id: str
    title: str
    started_at: str
    tz_offset_minutes: int
    hit_count: int
    first_seconds: float | None   # earliest/latest transcript hit; None if none
    last_seconds: float | None
    kinds: list[str]              # in order first matched
    best: list[SearchHit]         # top 2 passages, rank order


GROUPED_FETCH = 2000   # passages per source considered by search_grouped


@dataclass
class CalendarEvent:
    event_key: str
    calendar_name: str
    title: str
    start_utc: str
    end_utc: str
    all_day: bool
    declined: bool
    meeting_ids: list[str]
    other_attendees: int | None = None
    people: list[EventPerson] = field(default_factory=list)


@dataclass
class TagInfo:
    name: str
    description: str
    aliases: list[str]   # other spellings that resolve to this tag
    count: int           # meetings using it


@dataclass
class TagUpdate:
    meetings: list[tuple[str, list[str]]]  # (meeting id, its tags afterwards)
    created_tags: list[str]
    not_found: list[str]                   # remove names that matched no tag


def _plain(snippet: str) -> str:
    return snippet.replace(HIT_OPEN, "").replace(HIT_CLOSE, "").lower()


def collapse_echoes(hits: list[SearchHit]) -> list[SearchHit]:
    """Merge a passage that both tracks transcribed (in-room speaker mode)
    into one result. Stored transcripts are never changed; only results."""
    kept: list[SearchHit] = []
    for hit in hits:
        twin = None
        if hit.kind == "transcript":
            for k in kept:
                if (k.kind == "transcript" and k.meeting_id == hit.meeting_id
                        and k.start_seconds - _ECHO_SLACK_SECONDS <= hit.end_seconds
                        and hit.start_seconds - _ECHO_SLACK_SECONDS <= k.end_seconds
                        and SequenceMatcher(None, _plain(k.snippet),
                                            _plain(hit.snippet)).ratio() >= _ECHO_MIN_RATIO):
                    twin = k
                    break
        if twin is None:
            kept.append(hit)
        elif hit.speaker != twin.speaker and hit.speaker not in twin.also_speakers:
            twin.also_speakers.append(hit.speaker)
    return kept


def _segment(row) -> MeetingSegment:
    return MeetingSegment(
        speaker=row["speaker"],
        start=row["start_seconds"],
        end=row["end_seconds"],
        text=row["text"],
        confidence=row["confidence"],
        overlap=bool(row["overlap"]),
        profile_id=row["profile_id"],
        cluster_id=row["cluster_id"],
    )


def meeting_filters(from_date=None, to_date=None, tag=None, person=None, title=None):
    """SQL WHERE fragments over alias `m` plus parameters; shared by list
    and search so both filter identically. `title`: every whitespace-separated
    word must appear in the title, any order, case-folded."""
    clauses, params = [], []
    lower, upper = local_day_bounds(from_date, to_date)
    if lower:
        clauses.append("m.started_at >= ?")
        params.append(lower)
    if upper:
        clauses.append("m.started_at < ?")
        params.append(upper)
    if tag:
        # Any spelling of the tag, or an alias left by a rename/merge.
        slug = tag_slug(tag)
        clauses.append(
            "EXISTS (SELECT 1 FROM meeting_tags mt JOIN tags t ON t.id = mt.tag_id"
            " WHERE mt.meeting_id = m.id AND (t.slug = ? OR t.id IN"
            " (SELECT tag_id FROM tag_aliases WHERE slug = ?)))"
        )
        params += [slug, slug]
    if person:
        clauses.append(
            "EXISTS (SELECT 1 FROM meeting_people mp JOIN people p"
            " ON p.id = mp.person_id WHERE mp.meeting_id = m.id AND"
            " (p.display_name = ? COLLATE NOCASE OR p.email = ?))"
        )
        params += [person.strip(), person.strip()]
    for word in (title or "").split():
        # instr, not LIKE: '%' and '_' in a title are ordinary characters.
        clauses.append("instr(casefold(m.title), ?) > 0")
        params.append(word.casefold())
    return (" AND ".join(clauses) or "1"), params


class MeetingLibrary:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @contextmanager
    def _transaction(self):
        conn = meeting_store.connect(self._path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # -- writes -----------------------------------------------------------

    def save_meeting(self, new: NewMeeting) -> str:
        with self._transaction() as conn:
            meeting_id = self._insert(conn, new)
            self._adopt_draft(conn, meeting_id, new)
            return meeting_id

    def _adopt_draft(self, conn, meeting_id: str, new: NewMeeting) -> None:
        """Attach the notes typed during this recording (engine.py stays untouched:
        the library is the one place every saved recording passes through)."""
        row = conn.execute("SELECT * FROM note_draft WHERE id = 1").fetchone()
        if row is None:
            return
        drafted = datetime.fromisoformat(row["started_at"])
        start = new.started_at
        if not (start - DRAFT_EARLY_WINDOW <= drafted
                <= start + timedelta(seconds=float(new.duration_seconds))):
            return
        stamps = _adopt_stamps([tuple(s) for s in json.loads(row["stamps_json"])], drafted, start)
        self._write_user_notes(conn, meeting_id, row["markdown"], stamps)
        conn.execute("DELETE FROM note_draft WHERE id = 1")

    def get_draft(self) -> NoteDraft | None:
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM note_draft WHERE id = 1").fetchone()
        if row is None:
            return None
        return NoteDraft(row["markdown"], [tuple(s) for s in json.loads(row["stamps_json"])],
                         row["started_at"], row["updated_at"])

    def set_draft(self, markdown: str, stamps, started_at: str) -> NoteDraft:
        markdown = str(markdown)
        if len(markdown) > USER_NOTES_MAX_CHARS:
            raise ValueError("Notes are too long to save")
        checked = _check_stamps(stamps)
        drafted = utc_iso(datetime.fromisoformat(str(started_at)))
        now = _now_iso()
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO note_draft (id, markdown, stamps_json, started_at, updated_at)"
                " VALUES (1, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET"
                " markdown = excluded.markdown, stamps_json = excluded.stamps_json,"
                " started_at = excluded.started_at, updated_at = excluded.updated_at",
                (markdown, json.dumps([list(s) for s in checked]), drafted, now))
        return NoteDraft(markdown, checked, drafted, now)

    def discard_draft(self) -> None:
        with self._transaction() as conn:
            conn.execute("DELETE FROM note_draft WHERE id = 1")

    def finish_draft(self, meeting_id: str, markdown: str, stamps, started_at: str) -> UserNotes | None:
        """The page's last word when the meeting it was drafting is saved:
        overwrite that meeting's notes with the editor's text and drop the draft."""
        _check_id(meeting_id)
        with self._transaction() as conn:
            m = conn.execute("SELECT started_at FROM meetings WHERE id = ?",
                             (meeting_id,)).fetchone()
            if m is None:
                raise MeetingNotFound(meeting_id)
            shifted = _adopt_stamps(_check_stamps(stamps),
                                    datetime.fromisoformat(str(started_at)),
                                    datetime.fromisoformat(m["started_at"]))
            self._touch(conn, meeting_id)
            notes = self._write_user_notes(conn, meeting_id, markdown, shifted)
            conn.execute("DELETE FROM note_draft WHERE id = 1")
            return notes

    def _insert(self, conn, new: NewMeeting) -> str:
        if new.started_at.tzinfo is None:
            raise ValueError("NewMeeting.started_at must be timezone-aware")
        start = new.started_at
        offset = int(start.utcoffset().total_seconds() // 60)
        meeting_id = new.meeting_id or (
            f"{start.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        )
        _check_id(meeting_id)
        title = (new.title or "").strip() or (
            f"Meeting — {start.strftime('%b %-d, %-I:%M %p')}"
        )
        health = filter_capture_health(new.capture_health)
        now = _now_iso()
        conn.execute(
            "INSERT INTO meetings (id, title, started_at, tz_offset_minutes,"
            " duration_seconds, capture_mode, system_audio_status, capture_scope,"
            " track_offsets_json, capture_health_json, calendar_event_id, source,"
            " timestamps_approximate, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                meeting_id, title, utc_iso(start), offset,
                float(new.duration_seconds), new.capture_mode,
                new.system_audio_status, new.capture_scope,
                json.dumps({k: round(float(v), 6)
                            for k, v in new.track_offsets_seconds.items()
                            if k in {"mic", "system"}}),
                json.dumps(health), new.calendar_event_id, new.source,
                int(new.timestamps_approximate), now, now,
            ),
        )
        conn.executemany(
            "INSERT INTO segments (meeting_id, idx, speaker, start_seconds,"
            " end_seconds, text, confidence, overlap, profile_id, cluster_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (meeting_id, i, s.speaker, round(s.start, 2), round(s.end, 2),
                 s.text, s.confidence, int(s.overlap), s.profile_id, s.cluster_id)
                for i, s in enumerate(new.segments)
            ],
        )
        for person in new.people:
            conn.execute(
                "INSERT OR IGNORE INTO meeting_people (meeting_id, person_id, role)"
                " VALUES (?, ?, ?)",
                (meeting_id, self._person_id(conn, person.name, person.email), person.role))
        return meeting_id

    def import_meetings(self, batch, expected) -> None:
        """Insert a batch in one transaction and verify it against what the
        caller read from the source files (non-whitespace character count and
        speaker set per meeting) before committing. Any mismatch raises and
        the whole import rolls back, so a splitting/import bug can never
        silently drop or garble a legacy meeting's transcript."""
        if set(expected) != {n.meeting_id for n in batch}:
            raise ImportVerificationError("batch and expectations differ")
        with self._transaction() as conn:
            for new in batch:
                self._insert(conn, new)
            for meeting_id, (chars, speakers) in expected.items():
                rows = conn.execute(
                    "SELECT speaker, text FROM segments WHERE meeting_id = ?",
                    (meeting_id,)).fetchall()
                got = (sum(nonspace_len(r["text"]) for r in rows),
                       frozenset(r["speaker"] for r in rows))
                if got != (chars, speakers):
                    raise ImportVerificationError(f"content mismatch in {meeting_id}")

    def _touch(self, conn, meeting_id: str) -> None:
        cur = conn.execute(
            "UPDATE meetings SET updated_at = ? WHERE id = ?",
            (_now_iso(), meeting_id),
        )
        if cur.rowcount == 0:
            raise MeetingNotFound(meeting_id)

    def rename(self, meeting_id: str, title: str) -> None:
        _check_id(meeting_id)
        title = title.strip()
        if not title:
            raise ValueError("Meeting title can't be empty.")
        with self._transaction() as conn:
            self._touch(conn, meeting_id)
            conn.execute("UPDATE meetings SET title = ? WHERE id = ?", (title, meeting_id))

    def imported_title_fixes(self) -> list[tuple[str, str, str]]:
        """Imported titles ending in the recording's *end* time (the old app
        stamped titles when processing finished), with that time replaced by
        the start. Preview only; apply_title_fixes writes."""
        def stamp(d: datetime) -> str:
            return d.strftime("%b %-d, %-I:%M %p")

        fixes = []
        with self._transaction() as conn:
            for r in conn.execute(
                    "SELECT id, title, started_at, tz_offset_minutes, duration_seconds"
                    " FROM meetings WHERE source = 'imported_json' ORDER BY started_at"):
                start = local_start(r["started_at"], r["tz_offset_minutes"])
                end = start + timedelta(seconds=r["duration_seconds"])
                suffix = " — " + stamp(end)
                if stamp(end) != stamp(start) and r["title"].endswith(suffix):
                    fixes.append((r["id"], r["title"],
                                  r["title"][: -len(suffix)] + " — " + stamp(start)))
        return fixes

    def apply_title_fixes(self, fixes) -> int:
        """Rename only where the title still equals the previewed one, so a
        title the user edited after the preview is never overwritten."""
        changed = 0
        with self._transaction() as conn:
            for meeting_id, old, new in fixes:
                cur = conn.execute("UPDATE meetings SET title = ? WHERE id = ? AND title = ?",
                                   (new, meeting_id, old))
                if cur.rowcount:
                    self._touch(conn, meeting_id)
                    changed += 1
        return changed

    def relabel_speaker(self, meeting_id: str, segment_index: int, label: str,
                        *, all_matching: bool = False) -> None:
        """Relabel one segment, or every segment sharing its current label."""
        _check_id(meeting_id)
        label = label.strip()
        if not label:
            raise ValueError("Speaker label can't be empty.")
        with self._transaction() as conn:
            self._touch(conn, meeting_id)
            row = conn.execute(
                "SELECT speaker FROM segments WHERE meeting_id = ? AND idx = ?",
                (meeting_id, int(segment_index)),
            ).fetchone()
            if row is None:
                raise IndexError("Invalid meeting segment index.")
            where = "speaker = ?" if all_matching else "idx = ?"
            conn.execute(
                "UPDATE segments SET speaker = ?, profile_id = NULL,"
                f" confidence = NULL WHERE meeting_id = ? AND {where}",
                (label, meeting_id,
                 row["speaker"] if all_matching else int(segment_index)),
            )

    def delete(self, meeting_id: str) -> None:
        _check_id(meeting_id)
        with self._transaction() as conn:
            if conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,)).rowcount == 0:
                raise MeetingNotFound(meeting_id)
            # Orphaned tags would linger in list_tags() (unless they still
            # carry a description, alias or suppression); people stay (shared
            # with calendar events).
            self._drop_orphan_tags(conn)

    # -- reads ------------------------------------------------------------

    def count_meetings(self) -> int:
        with self._transaction() as conn:
            return conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]

    def meeting_ids(self) -> set[str]:
        with self._transaction() as conn:
            return {r[0] for r in conn.execute("SELECT id FROM meetings")}

    def _tags(self, conn, meeting_id):
        return [r[0] for r in conn.execute(
            "SELECT t.name FROM meeting_tags mt JOIN tags t ON t.id = mt.tag_id"
            " WHERE mt.meeting_id = ? ORDER BY t.name COLLATE NOCASE", (meeting_id,))]

    def _people(self, conn, meeting_id):
        return [r[0] for r in conn.execute(
            "SELECT p.display_name FROM meeting_people mp JOIN people p"
            " ON p.id = mp.person_id WHERE mp.meeting_id = ?"
            " ORDER BY mp.role DESC, p.display_name COLLATE NOCASE", (meeting_id,))]

    def _notes(self, conn, meeting_id):
        row = conn.execute("SELECT * FROM notes WHERE meeting_id = ?", (meeting_id,)).fetchone()
        if row is None:
            return None
        return Notes(row["summary"], json.loads(row["action_items_json"]),
                     row["updated_at"], row["updated_by"])

    def get_meeting(self, meeting_id: str, *, with_segments: bool = True) -> StoredMeeting:
        _check_id(meeting_id)
        with self._transaction() as conn:
            m = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            if m is None:
                raise MeetingNotFound(meeting_id)
            speakers = [r[0] for r in conn.execute(
                "SELECT speaker FROM segments WHERE meeting_id = ? GROUP BY speaker"
                " ORDER BY MIN(idx)", (meeting_id,))]
            segment_count = conn.execute(
                "SELECT COUNT(*) FROM segments WHERE meeting_id = ?", (meeting_id,)).fetchone()[0]
            segments = [_segment(r) for r in conn.execute(
                "SELECT * FROM segments WHERE meeting_id = ? ORDER BY idx",
                (meeting_id,))] if with_segments else []
            return StoredMeeting(
                meeting_id=m["id"], title=m["title"], started_at=m["started_at"],
                tz_offset_minutes=m["tz_offset_minutes"],
                duration_seconds=m["duration_seconds"], segments=segments,
                capture_mode=m["capture_mode"],
                system_audio_status=m["system_audio_status"],
                capture_scope=m["capture_scope"],
                track_offsets_seconds=json.loads(m["track_offsets_json"]),
                capture_health=json.loads(m["capture_health_json"]),
                calendar_event_id=m["calendar_event_id"], source=m["source"],
                timestamps_approximate=bool(m["timestamps_approximate"]),
                notes=self._notes(conn, meeting_id),
                tags=self._tags(conn, meeting_id),
                people=self._people(conn, meeting_id),
                speakers=speakers, segment_count=segment_count,
                user_notes=self._user_notes(conn, meeting_id),
            )

    def _user_notes(self, conn, meeting_id):
        row = conn.execute("SELECT * FROM user_notes WHERE meeting_id = ?",
                           (meeting_id,)).fetchone()
        if row is None:
            return None
        return UserNotes(row["markdown"], [tuple(s) for s in json.loads(row["stamps_json"])],
                         row["updated_at"])

    def get_user_notes(self, meeting_id: str) -> UserNotes | None:
        _check_id(meeting_id)
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)).fetchone() is None:
                raise MeetingNotFound(meeting_id)
            return self._user_notes(conn, meeting_id)

    def set_user_notes(self, meeting_id: str, markdown: str, stamps) -> UserNotes | None:
        _check_id(meeting_id)
        with self._transaction() as conn:
            self._touch(conn, meeting_id)
            return self._write_user_notes(conn, meeting_id, markdown, stamps)

    def _write_user_notes(self, conn, meeting_id, markdown, stamps) -> UserNotes | None:
        markdown = str(markdown)
        if len(markdown) > USER_NOTES_MAX_CHARS:
            raise ValueError("Notes are too long to save")
        checked = _check_stamps(stamps)
        if not markdown.strip():
            conn.execute("DELETE FROM user_notes WHERE meeting_id = ?", (meeting_id,))
            return None
        now = _now_iso()
        conn.execute(
            "INSERT INTO user_notes (meeting_id, markdown, text, stamps_json, updated_at)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT(meeting_id) DO UPDATE SET"
            " markdown = excluded.markdown, text = excluded.text,"
            " stamps_json = excluded.stamps_json, updated_at = excluded.updated_at",
            (meeting_id, markdown, notes_plain_text(markdown),
             json.dumps([list(s) for s in checked]), now))
        return UserNotes(markdown, checked, now)

    def list_meetings(self, *, from_date=None, to_date=None, tag=None,
                      person=None, title=None, limit=100, offset=0) -> list[MeetingSummary]:
        where, params = meeting_filters(from_date, to_date, tag, person, title)
        # None = every meeting (the Meetings window groups them itself);
        # SQLite treats LIMIT -1 as no limit.
        limit = -1 if limit is None else max(1, min(int(limit), 500))
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT m.*, (SELECT COUNT(DISTINCT speaker) FROM segments s"
                " WHERE s.meeting_id = m.id) AS speaker_count,"
                " EXISTS (SELECT 1 FROM notes n WHERE n.meeting_id = m.id"
                " AND n.summary <> '') AS has_summary,"
                # Distinct speakers in order of first appearance, unit-separator
                # joined (char(31): never appears in a speaker label) — lets
                # callers build a subtitle without a second per-row query.
                " (SELECT group_concat(speaker, char(31)) FROM ("
                "   SELECT speaker, MIN(idx) AS first_idx FROM segments"
                "   WHERE meeting_id = m.id GROUP BY speaker ORDER BY first_idx"
                " )) AS speakers_blob,"
                # Tags and people ride along for the same reason: one SELECT
                # however many rows come back. Order matches _tags/_people.
                " (SELECT group_concat(name, char(31)) FROM ("
                "   SELECT t.name FROM meeting_tags mt JOIN tags t ON t.id = mt.tag_id"
                "   WHERE mt.meeting_id = m.id ORDER BY t.name COLLATE NOCASE"
                " )) AS tags_blob,"
                " (SELECT group_concat(display_name, char(31)) FROM ("
                "   SELECT p.display_name FROM meeting_people mp"
                "   JOIN people p ON p.id = mp.person_id WHERE mp.meeting_id = m.id"
                "   ORDER BY mp.role DESC, p.display_name COLLATE NOCASE"
                " )) AS people_blob"
                f" FROM meetings m WHERE {where}"
                " ORDER BY m.started_at DESC, m.id DESC LIMIT ? OFFSET ?",
                (*params, limit, max(0, int(offset))),
            ).fetchall()
            return [
                MeetingSummary(
                    meeting_id=r["id"], title=r["title"], started_at=r["started_at"],
                    tz_offset_minutes=r["tz_offset_minutes"],
                    duration_seconds=r["duration_seconds"],
                    speaker_count=r["speaker_count"],
                    has_summary=bool(r["has_summary"]),
                    timestamps_approximate=bool(r["timestamps_approximate"]),
                    tags=r["tags_blob"].split("\x1f") if r["tags_blob"] else [],
                    people=r["people_blob"].split("\x1f") if r["people_blob"] else [],
                    speakers=(r["speakers_blob"].split("\x1f")
                              if r["speakers_blob"] else []),
                )
                for r in rows
            ]

    def transcript_page(self, meeting_id: str, *, start_seconds=None,
                        end_seconds=None, cursor=0, max_chars=20_000) -> TranscriptPage:
        _check_id(meeting_id)
        max_chars = max(_MIN_PAGE_CHARS, min(int(max_chars), _MAX_PAGE_CHARS))
        clauses, params = ["meeting_id = ?", "idx >= ?"], [meeting_id, max(0, int(cursor))]
        if start_seconds is not None:
            clauses.append("end_seconds >= ?")
            params.append(float(start_seconds))
        if end_seconds is not None:
            clauses.append("start_seconds <= ?")
            params.append(float(end_seconds))
        with self._transaction() as conn:
            meta = conn.execute(
                "SELECT title, timestamps_approximate FROM meetings WHERE id = ?",
                (meeting_id,)).fetchone()
            if meta is None:
                raise MeetingNotFound(meeting_id)
            title, approximate = meta["title"], bool(meta["timestamps_approximate"])
            page, used = [], 0
            # Iterate the cursor lazily and stop once the budget is spent, so a
            # page of a long meeting reads a page of rows, not the whole tail.
            for row in conn.execute(
                    f"SELECT * FROM segments WHERE {' AND '.join(clauses)} ORDER BY idx",
                    params):
                # Progress guard: always admit at least one segment before
                # checking the budget, so a single oversized segment still
                # advances the cursor instead of stalling the pager forever.
                if page and used + len(row["text"]) > max_chars:
                    return TranscriptPage(page, row["idx"], title, approximate)
                page.append((row["idx"], _segment(row)))
                used += len(row["text"])
        return TranscriptPage(page, None, title, approximate)

    # -- notes, tags, people ---------------------------------------------

    def save_notes(self, meeting_id: str, *, summary=None, action_items=None,
                   tags=None, updated_by="claude") -> Notes:
        """Replace only the fields given; the rest keep their stored value."""
        _check_id(meeting_id)
        if summary is not None:
            summary = str(summary).strip()
            if len(summary) > 20_000:
                raise ValueError("Summary is longer than 20,000 characters.")
        if action_items is not None:
            action_items = [str(a).strip() for a in action_items if str(a).strip()]
            if len(action_items) > 50 or any(len(a) > 500 for a in action_items):
                raise ValueError("At most 50 action items of 500 characters each.")
        if tags is not None:
            tags = clean_tag_names(tags)
            if len(tags) > MAX_TAGS_PER_MEETING:
                raise ValueError(f"At most {MAX_TAGS_PER_MEETING} tags per meeting.")
        new_summary = summary
        with self._transaction() as conn:
            self._touch(conn, meeting_id)
            created, suppressed = ([], []) if tags is None else \
                self._set_claude_tags(conn, meeting_id, tags)
            current = self._notes(conn, meeting_id) or Notes("", [], "", updated_by)
            summary = current.summary if summary is None else summary
            action_items = current.action_items if action_items is None else action_items
            now = _now_iso()
            conn.execute(
                "INSERT INTO notes (meeting_id, summary, action_items_json,"
                " action_items_text, updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?)"
                # Upsert, not INSERT OR REPLACE: REPLACE deletes without firing
                # the FTS delete trigger, leaving stale search entries.
                " ON CONFLICT(meeting_id) DO UPDATE SET summary = excluded.summary,"
                " action_items_json = excluded.action_items_json,"
                " action_items_text = excluded.action_items_text,"
                " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                (meeting_id, summary, json.dumps(action_items),
                 "\n".join(action_items), now, updated_by),
            )
            if new_summary:  # a written summary answers any pending request
                conn.execute("DELETE FROM summary_requests WHERE meeting_id = ?",
                             (meeting_id,))
            notes = self._notes(conn, meeting_id)
            notes.created_tags, notes.suppressed_tags = created, suppressed
            return notes

    # -- summaries ----------------------------------------------------------

    def _pending_params(self, now):
        now = now or datetime.now(timezone.utc)
        return [utc_iso(now - timedelta(days=PENDING_WINDOW_DAYS)), PENDING_MIN_SECONDS]

    def request_summary(self, meeting_id: str) -> None:
        _check_id(meeting_id)
        with self._transaction() as conn:
            self._touch(conn, meeting_id)
            conn.execute(
                "INSERT INTO summary_requests (meeting_id, requested_at) VALUES (?, ?)"
                " ON CONFLICT(meeting_id) DO NOTHING",
                (meeting_id, datetime.now(timezone.utc).isoformat(timespec="microseconds")))

    def pending_summaries(self, limit=5, now=None) -> list[tuple[str, bool]]:
        """[(meeting_id, requested)], explicit requests first (oldest request
        first), then recent unsummarised meetings oldest first."""
        limit = max(1, min(int(limit), 10))
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT m.id, r.meeting_id IS NOT NULL AS requested FROM meetings m"
                " LEFT JOIN summary_requests r ON r.meeting_id = m.id"
                f" WHERE {_PENDING_SQL}"
                " ORDER BY requested DESC, r.requested_at, r.rowid, m.started_at, m.id"
                " LIMIT ?", (*self._pending_params(now), limit)).fetchall()
        return [(r["id"], bool(r["requested"])) for r in rows]

    def summary_pending(self, meeting_id: str, now=None) -> bool:
        _check_id(meeting_id)
        with self._transaction() as conn:
            return conn.execute(
                "SELECT 1 FROM meetings m LEFT JOIN summary_requests r"
                f" ON r.meeting_id = m.id WHERE m.id = ? AND {_PENDING_SQL}",
                (meeting_id, *self._pending_params(now))).fetchone() is not None

    def _set_claude_tags(self, conn, meeting_id, names) -> tuple[list[str], list[str]]:
        """Make `names` this meeting's Claude-suggested tags. User tags are
        never removed (and a requested user tag stays 'user'); tags the
        user removed here are skipped; at most MAX_NEW_TAGS_PER_SAVE tags
        are created. The new-tag cap raises before any write; the 20-per-meeting
        limit raises after the writes and relies on the caller's transaction
        rolling back.
        Returns (created names, suppressed names)."""
        found, new = [], []
        for name in names:
            tag_id = self._find_tag(conn, name)
            if tag_id is None:
                new.append(name)
            elif tag_id not in found:
                found.append(tag_id)
        if len(new) > MAX_NEW_TAGS_PER_SAVE:
            raise ValueError(
                f"Would create {len(new)} new tags ({', '.join(new)}); at most "
                f"{MAX_NEW_TAGS_PER_SAVE} per call. Reuse tags from list_tags or drop some.")
        blocked = {r[0] for r in conn.execute(
            "SELECT tag_id FROM tag_suppressions WHERE meeting_id = ?", (meeting_id,))}
        suppressed = [self._tag_name(conn, t) for t in found if t in blocked]
        wanted = [t for t in found if t not in blocked]
        wanted += [self._create_tag(conn, name) for name in new]
        keep = f" AND tag_id NOT IN ({','.join('?' * len(wanted))})" if wanted else ""
        conn.execute(
            f"DELETE FROM meeting_tags WHERE meeting_id = ? AND source = 'claude'{keep}",
            (meeting_id, *wanted))
        now = _now_iso()
        for tag_id in wanted:
            # OR IGNORE: an existing link keeps its source and added_at.
            conn.execute(
                "INSERT OR IGNORE INTO meeting_tags (meeting_id, tag_id, source, added_at)"
                " VALUES (?, ?, 'claude', ?)", (meeting_id, tag_id, now))
        if self._tag_count(conn, meeting_id) > MAX_TAGS_PER_MEETING:
            raise ValueError(f"At most {MAX_TAGS_PER_MEETING} tags per meeting.")
        self._drop_orphan_tags(conn)
        return new, suppressed

    def _person_id(self, conn, name: str, email: str | None) -> int:
        # Controller ruling: normalise the email once, here, and use the
        # normalised value for both the lookup and the insert. The email
        # column is UNIQUE COLLATE NOCASE, so a case-only difference is
        # already handled by SQLite; but a padded email (" Refayet@x.com")
        # is not — NOCASE does not trim whitespace — so without stripping
        # here the lookup misses the existing row and the insert then
        # raises IntegrityError on the UNIQUE constraint.
        email = str(email).strip().lower() if email else None
        email = email or None
        name = name.strip() or (email or "Unknown")
        if email:
            row = conn.execute("SELECT id FROM people WHERE email = ?", (email,)).fetchone()
            if row:
                return row["id"]
            return conn.execute(
                "INSERT INTO people (display_name, email) VALUES (?, ?)",
                (name, email)).lastrowid
        row = conn.execute(
            "SELECT id FROM people WHERE email IS NULL AND display_name = ?"
            " COLLATE NOCASE", (name,)).fetchone()
        if row:
            return row["id"]
        return conn.execute(
            "INSERT INTO people (display_name) VALUES (?)", (name,)).lastrowid

    def link_people(self, meeting_id: str, people) -> None:
        """Replace a meeting's people with [(name, email|None, role)]."""
        _check_id(meeting_id)
        with self._transaction() as conn:
            self._touch(conn, meeting_id)
            conn.execute("DELETE FROM meeting_people WHERE meeting_id = ?", (meeting_id,))
            for name, email, role in people:
                pid = self._person_id(conn, name, email)
                conn.execute(
                    "INSERT OR IGNORE INTO meeting_people (meeting_id, person_id, role)"
                    " VALUES (?, ?, ?)", (meeting_id, pid, role))

    def list_tags(self) -> list[tuple[str, int]]:
        with self._transaction() as conn:
            return [(r[0], r[1]) for r in conn.execute(
                "SELECT t.name, COUNT(*) FROM tags t JOIN meeting_tags mt"
                " ON mt.tag_id = t.id GROUP BY t.id"
                " ORDER BY COUNT(*) DESC, t.name COLLATE NOCASE")]

    # -- tags -----------------------------------------------------------------

    @staticmethod
    def _find_tag(conn, name) -> int | None:
        """The tag a name means: by slug, then by alias. Never creates."""
        slug = tag_slug(name)
        row = conn.execute("SELECT id FROM tags WHERE slug = ?", (slug,)).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT tag_id FROM tag_aliases WHERE slug = ?", (slug,)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _create_tag(conn, name) -> int:
        return conn.execute(
            "INSERT INTO tags (name, slug, created_at) VALUES (?, ?, ?)",
            (name, tag_slug(name), _now_iso())).lastrowid

    def _require_tag(self, conn, name) -> int:
        tag_id = self._find_tag(conn, clean_tag_name(name))
        if tag_id is None:
            raise ValueError(f"No tag named {name!r}.")
        return tag_id

    @staticmethod
    def _tag_name(conn, tag_id) -> str:
        return conn.execute("SELECT name FROM tags WHERE id = ?", (tag_id,)).fetchone()[0]

    @staticmethod
    def _tag_count(conn, meeting_id) -> int:
        return conn.execute(
            "SELECT COUNT(*) FROM meeting_tags WHERE meeting_id = ?", (meeting_id,)).fetchone()[0]

    @staticmethod
    def _tag_info(conn, tag_id) -> TagInfo:
        name, description = conn.execute(
            "SELECT name, description FROM tags WHERE id = ?", (tag_id,)).fetchone()
        aliases = [r[0] for r in conn.execute(
            "SELECT name FROM tag_aliases WHERE tag_id = ? ORDER BY name COLLATE NOCASE",
            (tag_id,))]
        count = conn.execute(
            "SELECT COUNT(*) FROM meeting_tags WHERE tag_id = ?", (tag_id,)).fetchone()[0]
        return TagInfo(name, description, aliases, count)

    @staticmethod
    def _drop_orphan_tags(conn) -> None:
        # A tag no meeting uses is clutter, unless it still means something:
        # a description, an alias, or a user's "not this tag on this meeting"
        # (deleting the tag would cascade that suppression away).
        conn.execute(
            "DELETE FROM tags WHERE description = ''"
            " AND id NOT IN (SELECT tag_id FROM meeting_tags)"
            " AND id NOT IN (SELECT tag_id FROM tag_aliases)"
            " AND id NOT IN (SELECT tag_id FROM tag_suppressions)")

    def tag_catalog(self) -> list[TagInfo]:
        """Tags in use, most used first, with descriptions and aliases."""
        with self._transaction() as conn:
            ids = [r[0] for r in conn.execute(
                "SELECT t.id FROM tags t JOIN meeting_tags mt ON mt.tag_id = t.id"
                " GROUP BY t.id ORDER BY COUNT(*) DESC, t.name COLLATE NOCASE")]
            return [self._tag_info(conn, tag_id) for tag_id in ids]

    def meeting_tag_details(self, meeting_id: str) -> list[tuple[str, str]]:
        """[(tag name, 'user' | 'claude')] for one meeting."""
        _check_id(meeting_id)
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)).fetchone() is None:
                raise MeetingNotFound(meeting_id)
            return [(r[0], r[1]) for r in conn.execute(
                "SELECT t.name, mt.source FROM meeting_tags mt JOIN tags t ON t.id = mt.tag_id"
                " WHERE mt.meeting_id = ? ORDER BY t.name COLLATE NOCASE", (meeting_id,))]

    def tag_meetings(self, meeting_ids, *, add=(), remove=()) -> TagUpdate:
        """The user's explicit tagging. Added tags become source 'user'
        (kept when Claude re-saves notes); removed tags are unlinked and
        suppressed so save_notes will not add them back. Everything is
        checked before the first write; one transaction for all meetings."""
        ids = list(dict.fromkeys(str(i) for i in meeting_ids))
        if not 1 <= len(ids) <= 100:
            raise ValueError("Give between 1 and 100 meeting ids.")
        for meeting_id in ids:
            _check_id(meeting_id)
        add_names, remove_names = clean_tag_names(add), clean_tag_names(remove)
        if not add_names and not remove_names:
            raise ValueError("Give at least one tag to add or remove.")
        with self._transaction() as conn:
            missing = [i for i in ids if conn.execute(
                "SELECT 1 FROM meetings WHERE id = ?", (i,)).fetchone() is None]
            if missing:
                raise ValueError(f"No meeting with id {', '.join(missing)}.")
            add_ids, created = [], []
            for name in add_names:
                tag_id = self._find_tag(conn, name)
                if tag_id is None:
                    tag_id = self._create_tag(conn, name)
                    created.append(name)
                if tag_id not in add_ids:
                    add_ids.append(tag_id)
            remove_ids, not_found = [], []
            for name in remove_names:
                tag_id = self._find_tag(conn, name)
                if tag_id is None:
                    not_found.append(name)
                elif tag_id not in remove_ids:
                    remove_ids.append(tag_id)
            both = sorted(self._tag_name(conn, t) for t in set(add_ids) & set(remove_ids))
            if both:
                raise ValueError(f"{', '.join(both)} is in both add and remove.")
            now = _now_iso()
            for meeting_id in ids:
                self._touch(conn, meeting_id)
                for tag_id in add_ids:
                    conn.execute(
                        "INSERT INTO meeting_tags (meeting_id, tag_id, source, added_at)"
                        " VALUES (?, ?, 'user', ?) ON CONFLICT(meeting_id, tag_id)"
                        " DO UPDATE SET source = 'user'", (meeting_id, tag_id, now))
                    conn.execute(
                        "DELETE FROM tag_suppressions WHERE meeting_id = ? AND tag_id = ?",
                        (meeting_id, tag_id))
                for tag_id in remove_ids:
                    conn.execute(
                        "DELETE FROM meeting_tags WHERE meeting_id = ? AND tag_id = ?",
                        (meeting_id, tag_id))
                    conn.execute(
                        "INSERT OR IGNORE INTO tag_suppressions (meeting_id, tag_id, created_at)"
                        " VALUES (?, ?, ?)", (meeting_id, tag_id, now))
                if self._tag_count(conn, meeting_id) > MAX_TAGS_PER_MEETING:
                    raise ValueError(f"At most {MAX_TAGS_PER_MEETING} tags per meeting.")
            self._drop_orphan_tags(conn)
            return TagUpdate([(i, self._tags(conn, i)) for i in ids], created, not_found)

    def rename_tag(self, tag: str, name: str) -> TagInfo:
        """Change a tag's display name everywhere; the old spelling stays
        as an alias so it keeps resolving. Refuses a name another tag (or
        another tag's alias) already has: that is a merge."""
        new = clean_tag_name(name)
        slug = tag_slug(new)
        with self._transaction() as conn:
            tag_id = self._require_tag(conn, tag)
            owner = self._find_tag(conn, new)
            if owner is not None and owner != tag_id:
                raise ValueError(f"A tag named {new!r} already exists; use merge.")
            old_name, old_slug = conn.execute(
                "SELECT name, slug FROM tags WHERE id = ?", (tag_id,)).fetchone()
            # Renaming back to one of its own aliases: the alias becomes the name.
            conn.execute("DELETE FROM tag_aliases WHERE slug = ?", (slug,))
            conn.execute("UPDATE tags SET name = ?, slug = ? WHERE id = ?", (new, slug, tag_id))
            if slug != old_slug:
                conn.execute("INSERT INTO tag_aliases (slug, name, tag_id) VALUES (?, ?, ?)",
                             (old_slug, old_name, tag_id))
            return self._tag_info(conn, tag_id)

    def merge_tags(self, tag: str, into: str) -> TagInfo:
        """Fold `tag` into `into`: links (a 'user' link wins), suppressions
        and aliases move; the merged name becomes an alias of the target."""
        with self._transaction() as conn:
            source, target = self._require_tag(conn, tag), self._require_tag(conn, into)
            if source == target:
                raise ValueError("A tag cannot be merged into itself.")
            conn.execute(
                "UPDATE meeting_tags SET source = 'user' WHERE tag_id = ? AND meeting_id IN"
                " (SELECT meeting_id FROM meeting_tags WHERE tag_id = ? AND source = 'user')",
                (target, source))
            conn.execute(
                "INSERT OR IGNORE INTO meeting_tags (meeting_id, tag_id, source, added_at)"
                " SELECT meeting_id, ?, source, added_at FROM meeting_tags WHERE tag_id = ?",
                (target, source))
            conn.execute(
                "INSERT OR IGNORE INTO tag_suppressions (meeting_id, tag_id, created_at)"
                " SELECT meeting_id, ?, created_at FROM tag_suppressions WHERE tag_id = ?",
                (target, source))
            # Where the merged tag is on a meeting, the link beats a suppression.
            conn.execute(
                "DELETE FROM tag_suppressions WHERE tag_id = ? AND meeting_id IN"
                " (SELECT meeting_id FROM meeting_tags WHERE tag_id = ?)", (target, target))
            conn.execute("UPDATE tag_aliases SET tag_id = ? WHERE tag_id = ?", (target, source))
            name, slug = conn.execute(
                "SELECT name, slug FROM tags WHERE id = ?", (source,)).fetchone()
            conn.execute("DELETE FROM tags WHERE id = ?", (source,))  # cascades its links
            conn.execute("INSERT INTO tag_aliases (slug, name, tag_id) VALUES (?, ?, ?)",
                         (slug, name, target))
            return self._tag_info(conn, target)

    def delete_tag(self, tag: str) -> str:
        """Remove a tag from every meeting, with its aliases and
        suppressions (all cascade). Returns the deleted display name."""
        with self._transaction() as conn:
            tag_id = self._require_tag(conn, tag)
            name = self._tag_name(conn, tag_id)
            conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
            return name

    def describe_tag(self, tag: str, description: str) -> TagInfo:
        text = " ".join(str(description).split())
        if len(text) > MAX_DESCRIPTION_CHARS:
            raise ValueError(f"Tag descriptions are at most {MAX_DESCRIPTION_CHARS} characters.")
        with self._transaction() as conn:
            tag_id = self._require_tag(conn, tag)
            conn.execute("UPDATE tags SET description = ? WHERE id = ?", (text, tag_id))
            return self._tag_info(conn, tag_id)

    # -- search -------------------------------------------------------------

    def search(self, query: str, *, from_date=None, to_date=None, tag=None,
               person=None, limit=10, offset=0) -> list[SearchHit]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, min(int(offset), 1000))
        need = offset + limit
        ranked = self._ranked_hits(query, from_date, to_date, tag, person,
                                   title_limit=need, fetch=need * 4)
        return ranked[offset:need]

    def search_grouped(self, query: str, *, from_date=None, to_date=None, tag=None,
                       person=None, limit=10, offset=0) -> list[MeetingHits]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, min(int(offset), 1000))
        groups: dict[str, MeetingHits] = {}
        for h in self._ranked_hits(query, from_date, to_date, tag, person,
                                   title_limit=GROUPED_FETCH, fetch=GROUPED_FETCH):
            g = groups.get(h.meeting_id)
            if g is None:
                g = groups[h.meeting_id] = MeetingHits(
                    h.meeting_id, h.title, h.started_at, h.tz_offset_minutes,
                    0, None, None, [], [])
            g.hit_count += 1
            if h.kind not in g.kinds:
                g.kinds.append(h.kind)
            if len(g.best) < 2:
                g.best.append(h)
            if h.start_seconds is not None:
                g.first_seconds = h.start_seconds if g.first_seconds is None else min(g.first_seconds, h.start_seconds)
                g.last_seconds = h.start_seconds if g.last_seconds is None else max(g.last_seconds, h.start_seconds)
        return list(groups.values())[offset:offset + limit]

    def _ranked_hits(self, query, from_date, to_date, tag, person, *,
                     title_limit, fetch) -> list[SearchHit]:
        # A falsy query (None, "") must short-circuit before fts_query ever
        # sees it: str(None) is the literal text "None", which would
        # otherwise become a real FTS match for any segment containing that
        # word.
        if not query:
            return []
        # One date phrase narrows to that local day, ANDed with any explicit
        # range; the words left over search titles and contents within it.
        # Leftover punctuation (", " from "Oct 3, standup") is not a word.
        day, rest = parse_date_phrase(str(query), _today())
        words = [w for w in rest.split() if re.search(r"\w", w)]
        if day is not None:
            iso = day.isoformat()
            from_date = max(from_date, iso) if from_date else iso
            to_date = min(to_date, iso) if to_date else iso
        where, params = meeting_filters(from_date, to_date, tag, person)
        with self._transaction() as conn:
            if not words:
                if day is None:
                    return []
                return self._meeting_hits(conn, where, params, [], "ASC", title_limit)
            title_where, title_params = meeting_filters(
                from_date, to_date, tag, person, title=" ".join(words))
            titled = self._meeting_hits(conn, title_where, title_params, words, "DESC", title_limit)
            hits = []
            for any_term in (False, True):
                match = fts_query(" ".join(words), any_term=any_term)
                if match is None:
                    break
                hits = self._search(conn, match, where, params, fetch)
                if hits:
                    break
        # bm25 scores from the transcript and notes FTS tables are not comparable
        # (different table sizes and document lengths), so order by rank within
        # each kind and interleave, transcript first on ties.
        rank = {}
        for kind in ("transcript", "notes", "user_notes"):
            of_kind = sorted((h for h in hits if h.kind == kind), key=lambda h: h.score)
            rank.update((id(h), i) for i, h in enumerate(of_kind))
        hits.sort(key=lambda h: (rank[id(h)], {"transcript": 0, "notes": 1, "user_notes": 2}[h.kind]))
        return titled + collapse_echoes(hits)

    def _meeting_hits(self, conn, where, params, words, order, limit) -> list[SearchHit]:
        # Meeting-level hits: a title match (words highlighted) or, for a
        # date-only query, every meeting that day (earliest first).
        return [
            SearchHit(r["id"], r["title"], r["started_at"], r["tz_offset_minutes"],
                      "meeting", None, None, None, None, mark_title(r["title"], words),
                      [], 0.0)
            for r in conn.execute(
                "SELECT m.id, m.title, m.started_at, m.tz_offset_minutes FROM meetings m"
                f" WHERE {where} ORDER BY m.started_at {order}, m.id LIMIT ?",
                (*params, limit))
        ]

    def _search(self, conn, match, where, params, fetch) -> list[SearchHit]:
        # Controller ruling: the spec's ~30-word snippet wins over the
        # plan's 24 (snippet() token count is the 6th argument).
        snippet = "snippet({t}, -1, char(2), char(3), '…', 30)"
        hits = [
            SearchHit(r["id"], r["title"], r["started_at"], r["tz_offset_minutes"],
                      "transcript", r["speaker"], r["start_seconds"],
                      r["end_seconds"], r["idx"], r["snip"], [], r["score"])
            for r in conn.execute(
                "SELECT m.id, m.title, m.started_at, m.tz_offset_minutes, s.speaker,"
                " s.start_seconds, s.end_seconds, s.idx,"
                f" {snippet.format(t='segments_fts')} AS snip,"
                " bm25(segments_fts) AS score FROM segments_fts"
                " JOIN segments s ON s.id = segments_fts.rowid"
                " JOIN meetings m ON m.id = s.meeting_id"
                f" WHERE segments_fts MATCH ? AND {where} ORDER BY score LIMIT ?",
                (match, *params, fetch))
        ]
        hits += [
            SearchHit(r["id"], r["title"], r["started_at"], r["tz_offset_minutes"],
                      "notes", None, None, None, None, r["snip"], [], r["score"])
            for r in conn.execute(
                "SELECT m.id, m.title, m.started_at, m.tz_offset_minutes,"
                f" {snippet.format(t='notes_fts')} AS snip,"
                " bm25(notes_fts) AS score FROM notes_fts"
                " JOIN notes n ON n.id = notes_fts.rowid"
                " JOIN meetings m ON m.id = n.meeting_id"
                f" WHERE notes_fts MATCH ? AND {where} ORDER BY score LIMIT ?",
                (match, *params, fetch))
        ]
        hits += [
            SearchHit(r["id"], r["title"], r["started_at"], r["tz_offset_minutes"],
                      "user_notes", None, None, None, None, r["snip"], [], r["score"])
            for r in conn.execute(
                "SELECT m.id, m.title, m.started_at, m.tz_offset_minutes,"
                f" {snippet.format(t='user_notes_fts')} AS snip,"
                " bm25(user_notes_fts) AS score FROM user_notes_fts"
                " JOIN user_notes u ON u.id = user_notes_fts.rowid"
                " JOIN meetings m ON m.id = u.meeting_id"
                f" WHERE user_notes_fts MATCH ? AND {where} ORDER BY score LIMIT ?",
                (match, *params, fetch))
        ]
        return hits

    def list_people(self, query=None) -> list[tuple[str, int]]:
        where, params = "1", []
        if query:
            where = "(p.display_name LIKE ? OR p.email LIKE ?)"
            params = [f"%{query.strip()}%"] * 2
        with self._transaction() as conn:
            return [(r[0], r[1]) for r in conn.execute(
                "SELECT p.display_name, COUNT(*) FROM people p JOIN meeting_people mp"
                f" ON mp.person_id = p.id WHERE {where} GROUP BY p.id"
                " ORDER BY COUNT(*) DESC, p.display_name COLLATE NOCASE", params)]

    # -- MCP reads (phase 2) -------------------------------------------------

    def meeting_tags(self, meeting_id: str) -> list[str]:
        _check_id(meeting_id)
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)).fetchone() is None:
                raise MeetingNotFound(meeting_id)
            return self._tags(conn, meeting_id)

    def _calendar_event(self, conn, row) -> CalendarEvent:
        return CalendarEvent(
            event_key=row["event_key"], calendar_name=row["calendar_name"],
            title=row["title"], start_utc=row["start_utc"], end_utc=row["end_utc"],
            all_day=bool(row["all_day"]), declined=bool(row["declined"]),
            meeting_ids=[r[0] for r in conn.execute(
                "SELECT id FROM meetings WHERE calendar_event_id = ?"
                " ORDER BY started_at, id", (row["event_key"],))],
            other_attendees=row["other_attendees"],
            people=[EventPerson(r[0], r[1], r[2]) for r in conn.execute(
                "SELECT p.display_name, p.email, cep.role FROM calendar_event_people cep"
                " JOIN people p ON p.id = cep.person_id WHERE cep.event_key = ?"
                " ORDER BY cep.role DESC, p.display_name COLLATE NOCASE",
                (row["event_key"],))],
        )

    @staticmethod
    def _drop_orphan_people(conn) -> None:
        # People arrive with calendar events; once neither a meeting nor a
        # cached event refers to them they are just stale contact data.
        conn.execute(
            "DELETE FROM people WHERE id NOT IN (SELECT person_id FROM meeting_people)"
            " AND id NOT IN (SELECT person_id FROM calendar_event_people)")

    def replace_calendar_window(self, events, window_start: datetime,
                                window_end: datetime) -> None:
        """Make the cached events starting in [window_start, window_end)
        exactly `events`. One transaction, so a reader never sees a half-synced
        window; events outside it (older history) are left alone."""
        lo, hi = utc_iso(window_start), utc_iso(window_end)
        now = _now_iso()
        with self._transaction() as conn:
            conn.execute("DELETE FROM calendar_events WHERE start_utc >= ? AND start_utc < ?",
                         (lo, hi))
            for e in events:
                if e.start.tzinfo is None or e.end.tzinfo is None:
                    raise ValueError("Calendar event times must be timezone-aware.")
                conn.execute(
                    "INSERT OR REPLACE INTO calendar_events (event_key, calendar_name,"
                    " title, start_utc, end_utc, all_day, declined, other_attendees,"
                    " synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (e.key, e.calendar_name, e.title, utc_iso(e.start), utc_iso(e.end),
                     int(e.all_day), int(e.declined), e.other_attendees, now))
                # Explicit: REPLACE's implicit delete is not relied on to
                # cascade to the people rows.
                conn.execute("DELETE FROM calendar_event_people WHERE event_key = ?", (e.key,))
                for p in e.people:
                    conn.execute(
                        "INSERT OR IGNORE INTO calendar_event_people (event_key, person_id,"
                        " role) VALUES (?, ?, ?)",
                        (e.key, self._person_id(conn, p.name, p.email), p.role))
            self._drop_orphan_people(conn)

    def clear_calendar_cache(self) -> None:
        with self._transaction() as conn:
            conn.execute("DELETE FROM calendar_events")
            self._drop_orphan_people(conn)

    def calendar_events_overlapping(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        """Cached events with start < `end` and end > `start` (UTC instants)."""
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM calendar_events WHERE start_utc < ? AND end_utc > ?"
                " ORDER BY start_utc, event_key", (utc_iso(end), utc_iso(start))).fetchall()
            return [self._calendar_event(conn, r) for r in rows]

    def link_event(self, meeting_id: str, event_key: str | None) -> None:
        """Link a meeting to a cached event (taking its title and people), or
        unlink it. Unlinking keeps the title and people: the user may have
        renamed it since, and the spec keeps them when an event disappears."""
        _check_id(meeting_id)
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)).fetchone() is None:
                raise MeetingNotFound(meeting_id)
            if event_key is None:
                conn.execute("UPDATE meetings SET calendar_event_id = NULL WHERE id = ?",
                             (meeting_id,))
                self._touch(conn, meeting_id)
                return
            row = conn.execute("SELECT * FROM calendar_events WHERE event_key = ?",
                               (event_key,)).fetchone()
            if row is None:
                raise ValueError("That calendar event is no longer in the calendar.")
            event = self._calendar_event(conn, row)
            conn.execute("UPDATE meetings SET calendar_event_id = ?, title = ? WHERE id = ?",
                         (event_key, event.title, meeting_id))
            conn.execute("DELETE FROM meeting_people WHERE meeting_id = ?", (meeting_id,))
            for p in event.people:
                conn.execute(
                    "INSERT OR IGNORE INTO meeting_people (meeting_id, person_id, role)"
                    " VALUES (?, ?, ?)",
                    (meeting_id, self._person_id(conn, p.name, p.email), p.role))
            self._touch(conn, meeting_id)

    def calendar_events_between(self, from_date: str, to_date: str) -> list[CalendarEvent]:
        """Cached Calendar.app events overlapping local days [from, to]."""
        lower, upper = local_day_bounds(from_date, to_date)
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM calendar_events WHERE start_utc < ? AND end_utc > ?"
                " ORDER BY start_utc, event_key", (upper, lower)).fetchall()
            return [self._calendar_event(conn, r) for r in rows]

    def calendar_event(self, event_key: str | None) -> CalendarEvent | None:
        if not event_key:
            return None
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM calendar_events WHERE event_key = ?",
                               (event_key,)).fetchone()
            return self._calendar_event(conn, row) if row else None


class LibraryWatcher:
    """Notices commits made by any other connection, e.g. Claude saving
    notes through the MCP server (a separate process nothing in the app
    hears from). PRAGMA data_version changes exactly when another
    connection commits; updated_at would not do, since its one-second
    resolution hides a second change within the same second.

    The one exception to "every call opens its own connection": the
    watcher keeps one, because data_version is only meaningful across
    calls on the same connection. It is used only by the thread that made
    it (sqlite3's check_same_thread enforces this) and sits idle outside
    a transaction, so it never holds back WAL checkpoints.
    """

    def __init__(self, library: MeetingLibrary) -> None:
        self._path = library._path
        self._conn = None
        self._version = None

    def changed(self) -> bool:
        if self._conn is None:
            self._conn = meeting_store.connect(self._path)
        version = self._conn.execute("PRAGMA data_version").fetchone()[0]
        changed = self._version is not None and version != self._version
        self._version = version
        return changed

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
        self._conn, self._version = None, None
