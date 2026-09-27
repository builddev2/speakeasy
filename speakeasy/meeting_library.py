"""The meeting library: the only API for stored meetings.

Called by the engine's save path, the JSON importer, the CLI, the Meetings
window bridge and (phase 2) the MCP server. Pure Python over meeting_store
with no AppKit import, so a future local web server can call it unchanged.
Times cross this boundary as UTC ISO strings ending in 'Z' plus the local
UTC offset captured at recording start.
"""

import json
import re
import secrets
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

from . import meeting_store
from .meetings import MeetingSegment, _ID_RE, filter_capture_health

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


def _normalise_tags(tags) -> list[str]:
    seen, result = set(), []
    for raw in tags:
        name = " ".join(str(raw).split())
        if not name or name.lower() in seen:
            continue
        if len(name) > 40:
            raise ValueError("Tags are at most 40 characters.")
        seen.add(name.lower())
        result.append(name)
    if len(result) > 20:
        raise ValueError("At most 20 tags per meeting.")
    return result


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


@dataclass
class Notes:
    summary: str
    action_items: list[str]
    updated_at: str
    updated_by: str


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

    @property
    def local_start(self) -> datetime:
        return local_start(self.started_at, self.tz_offset_minutes)


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


@dataclass
class SearchHit:
    meeting_id: str
    title: str
    started_at: str
    tz_offset_minutes: int
    kind: str  # "transcript" or "notes"
    speaker: str | None
    start_seconds: float | None
    end_seconds: float | None
    segment_index: int | None
    snippet: str
    also_speakers: list[str]
    score: float


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


def meeting_filters(from_date=None, to_date=None, tag=None, person=None):
    """SQL WHERE fragments over alias `m` plus parameters; shared by list
    and search so both filter identically."""
    clauses, params = [], []
    lower, upper = local_day_bounds(from_date, to_date)
    if lower:
        clauses.append("m.started_at >= ?")
        params.append(lower)
    if upper:
        clauses.append("m.started_at < ?")
        params.append(upper)
    if tag:
        clauses.append(
            "EXISTS (SELECT 1 FROM meeting_tags mt JOIN tags t ON t.id = mt.tag_id"
            " WHERE mt.meeting_id = m.id AND t.name = ?)"
        )
        params.append(tag.strip())
    if person:
        clauses.append(
            "EXISTS (SELECT 1 FROM meeting_people mp JOIN people p"
            " ON p.id = mp.person_id WHERE mp.meeting_id = m.id AND"
            " (p.display_name = ? COLLATE NOCASE OR p.email = ?))"
        )
        params += [person.strip(), person.strip()]
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
            return self._insert(conn, new)

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
            # Orphaned tags would linger in list_tags(); people stay (shared
            # with calendar events).
            conn.execute(
                "DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM meeting_tags)"
            )

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

    def get_meeting(self, meeting_id: str) -> StoredMeeting:
        _check_id(meeting_id)
        with self._transaction() as conn:
            m = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            if m is None:
                raise MeetingNotFound(meeting_id)
            segments = [_segment(r) for r in conn.execute(
                "SELECT * FROM segments WHERE meeting_id = ? ORDER BY idx", (meeting_id,))]
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
            )

    def list_meetings(self, *, from_date=None, to_date=None, tag=None,
                      person=None, limit=100, offset=0) -> list[MeetingSummary]:
        where, params = meeting_filters(from_date, to_date, tag, person)
        limit = max(1, min(int(limit), 500))
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT m.*, (SELECT COUNT(DISTINCT speaker) FROM segments s"
                " WHERE s.meeting_id = m.id) AS speaker_count,"
                " EXISTS (SELECT 1 FROM notes n WHERE n.meeting_id = m.id"
                " AND n.summary <> '') AS has_summary"
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
                    tags=self._tags(conn, r["id"]),
                    people=self._people(conn, r["id"]),
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
            if conn.execute("SELECT 1 FROM meetings WHERE id = ?", (meeting_id,)).fetchone() is None:
                raise MeetingNotFound(meeting_id)
            rows = conn.execute(
                f"SELECT * FROM segments WHERE {' AND '.join(clauses)} ORDER BY idx",
                params,
            ).fetchall()
        page, used = [], 0
        for i, row in enumerate(rows):
            # Progress guard: always admit at least one segment before
            # checking the budget, so a single oversized segment still
            # advances the cursor instead of stalling the pager forever.
            if page and used + len(row["text"]) > max_chars:
                return TranscriptPage(page, rows[i]["idx"])
            page.append((row["idx"], _segment(row)))
            used += len(row["text"])
        return TranscriptPage(page, None)

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
            tags = _normalise_tags(tags)
        with self._transaction() as conn:
            self._touch(conn, meeting_id)
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
            if tags is not None:
                conn.execute("DELETE FROM meeting_tags WHERE meeting_id = ?", (meeting_id,))
                for name in tags:
                    conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
                    conn.execute(
                        "INSERT INTO meeting_tags (meeting_id, tag_id)"
                        " SELECT ?, id FROM tags WHERE name = ?", (meeting_id, name))
                conn.execute(
                    "DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM meeting_tags)")
            return self._notes(conn, meeting_id)

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

    # -- search -------------------------------------------------------------

    def search(self, query: str, *, from_date=None, to_date=None, tag=None,
               person=None, limit=10) -> list[SearchHit]:
        limit = max(1, min(int(limit), 50))
        # A falsy query (None, "") must short-circuit before fts_query ever
        # sees it: str(None) is the literal text "None", which would
        # otherwise become a real FTS match for any segment containing that
        # word.
        if not query:
            return []
        where, params = meeting_filters(from_date, to_date, tag, person)
        with self._transaction() as conn:
            for any_term in (False, True):
                match = fts_query(query, any_term=any_term)
                if match is None:
                    return []
                hits = self._search(conn, match, where, params, limit * 4)
                if hits:
                    break
        hits.sort(key=lambda h: h.score)
        return collapse_echoes(hits)[:limit]

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
