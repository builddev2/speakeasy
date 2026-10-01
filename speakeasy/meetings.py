"""Meeting transcripts: legacy per-meeting JSON parsing, plus pure alignment
and rendering logic shared with MeetingLibrary.

The live store is meeting_library.py (SQLite). Meeting here is kept only so
meeting_import.py can parse the old hand-editable per-meeting JSON files:

    {
      "id": "20260708-143212-a3f9",
      "title": "Meeting — Jul 8, 2:32 PM",
      "created": "2026-07-08T14:32:12",
      "duration_seconds": 1832.5,
      "segments": [
        {"speaker": "Speaker 1", "start": 0.4, "end": 12.9, "text": "..."}
      ]
    }

This module is pure logic (stdlib only) so the alignment algorithm and the
legacy parser are testable without the model, sherpa-onnx, or AppKit. The
alignment inputs are duck-typed: `sentences` only needs .start/.end/.tokens
(tokens need .start/.end/.duration/.text), matching parakeet_mlx's
AlignedSentence without importing it.
"""

import json
import re
import statistics
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, replace
from datetime import datetime
from difflib import SequenceMatcher

from . import config, settings

# Ids double as legacy filenames; validated on load.
_ID_RE = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{4}")
_CAPTURE_HEALTH_KEYS = {
    "mic_first_buffer",
    "system_first_buffer",
    "system_nonzero_signal",
    "mic_dropped_frames",
    "system_dropped_frames",
    "mic_writer_failed",
    "system_writer_failed",
    "mic_writer_lagged",
    "system_writer_lagged",
    "helper_exited",
    "helper_exit_reason",
    "capture_outcome",
    "fallback_reason",
    "capture_mode",
    "capture_scope",
    "diarization_status",
    "diarization_failure",
}

# A diarization turn further than this from a token is considered unrelated;
# the token then inherits its predecessor's speaker instead (mid-sentence
# continuity beats a distant turn).
_MAX_TURN_DISTANCE_SECONDS = 2.0


def filter_capture_health(d: dict | None) -> dict:
    """Keep only the privacy-safe whitelisted capture-health fields.

    Shared by Meeting and MeetingLibrary so the whitelist logic lives in
    exactly one place; meeting/app names, device names and transcripts must
    never leak into diagnostics.
    """
    return {
        str(key): value
        for key, value in (d or {}).items()
        if key in _CAPTURE_HEALTH_KEYS
        and (value is None or isinstance(value, (bool, int, str)))
    }


@dataclass
class MeetingSegment:
    speaker: str
    start: float
    end: float
    text: str
    confidence: float | None = None
    overlap: bool = False
    profile_id: str | None = None
    cluster_id: int | None = None


@dataclass(frozen=True)
class DiarizationTurn:
    start: float
    end: float
    speaker: int
    confidence: float | None = None
    overlap: bool = False
    profile_id: str | None = None


def flag_overlaps(turns):
    """Return `turns` with `overlap` set exactly where two different voices
    are concurrent. Precondition: `turns` is sorted by start."""
    overlapping = set()
    for left, first in enumerate(turns):
        for right in range(left + 1, len(turns)):
            second = turns[right]
            if second.start >= first.end:
                break
            if first.speaker != second.speaker and second.end > first.start:
                overlapping.update((left, right))
    return [
        replace(turn, overlap=index in overlapping)
        for index, turn in enumerate(turns)
    ]


class Meeting:
    def __init__(
        self,
        meeting_id: str,
        title: str,
        created: str,
        duration_seconds: float,
        segments: list[MeetingSegment],
        capture_mode: str = "mic_only",
        system_audio_status: str = "legacy_unknown",
        track_offsets_seconds: dict[str, float] | None = None,
        capture_health: dict | None = None,
        capture_scope: str = "mic_only",
    ) -> None:
        self.meeting_id = meeting_id
        self.title = title
        self.created = created
        self.duration_seconds = duration_seconds
        self.segments = segments
        self.capture_mode = capture_mode
        self.system_audio_status = system_audio_status
        self.track_offsets_seconds = track_offsets_seconds or {"mic": 0.0}
        self.capture_health = filter_capture_health(capture_health)
        self.capture_scope = capture_scope

    # -- storage --------------------------------------------------------
    # Meeting is now kept only for meeting_import.py (Meeting.load parses
    # the legacy hand-editable JSON files); MeetingLibrary/meeting_library.py
    # is the live store. new/save/rename/delete/relabel_speaker are gone —
    # nothing still writes this format.

    @property
    def path(self):
        return settings.meetings_dir() / f"{self.meeting_id}.json"

    @classmethod
    def load(cls, meeting_id: str) -> "Meeting":
        # Same defense as Profile.load: the id becomes a filesystem path and
        # must not be trusted blindly.
        if not _ID_RE.fullmatch(meeting_id):
            raise ValueError(f"Invalid meeting id: {meeting_id!r}")
        path = settings.meetings_dir() / f"{meeting_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        raw_offsets = data.get("track_offsets_seconds", {"mic": 0.0})
        if not isinstance(raw_offsets, dict):
            raw_offsets = {"mic": 0.0}
        segments = [
            MeetingSegment(
                speaker=str(s["speaker"]),
                start=float(s["start"]),
                end=float(s["end"]),
                text=str(s["text"]),
                confidence=(
                    float(s["confidence"]) if s.get("confidence") is not None else None
                ),
                overlap=bool(s.get("overlap", False)),
                profile_id=s.get("profile_id"),
                cluster_id=(
                    int(s["cluster_id"]) if s.get("cluster_id") is not None else None
                ),
            )
            for s in data.get("segments", [])
        ]
        return cls(
            meeting_id=meeting_id,
            title=data.get("title") or meeting_id,
            created=data.get("created") or "",
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            segments=segments,
            capture_mode=str(data.get("capture_mode", "mic_only")),
            system_audio_status=str(
                data.get("system_audio_status", "legacy_unknown")
            ),
            track_offsets_seconds={
                str(name): float(offset)
                for name, offset in raw_offsets.items()
                if name in {"mic", "system"}
            },
            capture_health=(
                data.get("capture_health")
                if isinstance(data.get("capture_health"), dict)
                else {}
            ),
            capture_scope=str(data.get("capture_scope", "mic_only")),
        )


# -- rendering ------------------------------------------------------------


def _timestamp(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _duration_text(seconds: float) -> str:
    m = int(seconds) // 60
    if m >= 60:
        return f"{m // 60} h {m % 60} min"
    return f"{m} min" if m else f"{int(seconds)} s"


def render_txt(meeting: Meeting) -> str:
    lines = [
        meeting.title,
        f"{meeting.created}  ·  {_duration_text(meeting.duration_seconds)}",
        "",
    ]
    for seg in meeting.segments:
        lines.append(f"[{_timestamp(seg.start)}] {seg.speaker}: {seg.text}")
    return "\n".join(lines) + "\n"


def render_md(meeting: Meeting) -> str:
    lines = [
        f"# {meeting.title}",
        "",
        f"*{meeting.created}  ·  {_duration_text(meeting.duration_seconds)}*",
        "",
    ]
    for seg in meeting.segments:
        lines.append(f"**{seg.speaker}** [{_timestamp(seg.start)}]: {seg.text}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


# -- speaker/text alignment -------------------------------------------------


def align_speakers(sentences, turns) -> list[MeetingSegment]:
    """Attribute transcribed sentences to diarization speakers.

    `sentences`: time-ordered objects with .start/.end/.tokens (tokens carry
    .start/.end/.duration/.text) — parakeet_mlx AlignedSentence-shaped.
    `turns`: [(start, end, speaker_int)] from the diarizer, any order.

    Tokens vote for the turn containing their midpoint; a token in no turn
    falls to the nearest turn unless it is further than
    _MAX_TURN_DISTANCE_SECONDS away, in which case it inherits its
    predecessor's speaker (the diarizer missed a beat mid-utterance more
    often than the room actually changed speakers). Each sentence goes to
    its duration-majority speaker — sentence-level voting absorbs the jitter
    tokens pick up at turn boundaries. Consecutive same-speaker sentences
    merge into one segment, and speakers are numbered by first appearance.
    """
    if not sentences:
        return []
    turns = [
        turn
        if isinstance(turn, DiarizationTurn)
        else DiarizationTurn(float(turn[0]), float(turn[1]), int(turn[2]))
        for turn in turns
    ]
    turns.sort(key=lambda t: t.start)

    def token_speaker(token, previous: int | None) -> int | None:
        mid = (token.start + token.end) / 2
        nearest, nearest_distance = None, None
        for turn in turns:
            if turn.start <= mid <= turn.end:
                return turn.speaker
            distance = turn.start - mid if mid < turn.start else mid - turn.end
            if nearest_distance is None or distance < nearest_distance:
                nearest, nearest_distance = turn.speaker, distance
        if nearest is None:
            return previous
        if nearest_distance > _MAX_TURN_DISTANCE_SECONDS and previous is not None:
            return previous
        return nearest

    # Split at sustained in-sentence speaker changes, while absorbing short
    # boundary jitter with the sentence's duration-majority speaker.
    attributed: list[tuple[object, list[object], int]] = []
    previous: int | None = None
    for sentence in sentences:
        weights: dict[int, float] = {}
        token_speakers: list[tuple[object, int]] = []
        for token in sentence.tokens:
            speaker = token_speaker(token, previous)
            if speaker is None:
                continue
            previous = speaker
            token_speakers.append((token, speaker))
            weights[speaker] = weights.get(speaker, 0.0) + max(token.duration, 1e-3)
        if weights:
            winner = max(weights.items(), key=lambda kv: kv[1])[0]
        else:
            # No turns at all (diarization found nothing): single speaker.
            winner = attributed[-1][2] if attributed else 0
        if not token_speakers:
            attributed.append((sentence, [], winner))
            continue
        runs: list[list[tuple[object, int]]] = []
        for item in token_speakers:
            if not runs or runs[-1][-1][1] != item[1]:
                runs.append([item])
            else:
                runs[-1].append(item)
        normalized_runs: list[tuple[list[object], int]] = []
        for run in runs:
            duration = sum(max(token.duration, 1e-3) for token, _ in run)
            speaker = run[0][1]
            if speaker != winner and (
                len(run) < 2 or duration < config.DIARIZATION_SPLIT_MIN_SECONDS
            ):
                speaker = winner
            tokens = [token for token, _ in run]
            if normalized_runs and normalized_runs[-1][1] == speaker:
                normalized_runs[-1][0].extend(tokens)
            else:
                normalized_runs.append((tokens, speaker))
        if len(normalized_runs) == 1:
            attributed.append((sentence, [], normalized_runs[0][1]))
        else:
            attributed.extend(
                (sentence, tokens, speaker) for tokens, speaker in normalized_runs
            )

    # Merge consecutive same-speaker sentences; label by first appearance.
    labels: dict[int, str] = {}
    profile_ids: dict[int, str | None] = {}
    profile_labels = {turn.profile_id for turn in turns if turn.profile_id}
    segments: list[MeetingSegment] = []
    for sentence, tokens, speaker in attributed:
        if speaker not in labels:
            profile = next(
                (t.profile_id for t in turns if t.speaker == speaker and t.profile_id),
                None,
            )
            if profile:
                labels[speaker] = profile
            else:
                number = len(labels) + 1
                label = f"Speaker {number}"
                while label in profile_labels or label in labels.values():
                    number += 1
                    label = f"Speaker {number}"
                labels[speaker] = label
            profile_ids[speaker] = profile
        text = (
            "".join(token.text for token in tokens).strip()
            if tokens
            else sentence.text.strip()
        )
        if not text:
            continue
        related = [t for t in turns if t.speaker == speaker]
        confidence_values = [t.confidence for t in related if t.confidence is not None]
        confidence = (
            sum(confidence_values) / len(confidence_values)
            if confidence_values
            else None
        )
        start = tokens[0].start if tokens else sentence.start
        end = tokens[-1].end if tokens else sentence.end
        overlap = any(
            t.overlap and t.start < end and t.end > start
            for t in related
        )
        if (
            segments
            and segments[-1].speaker == labels[speaker]
            and segments[-1].overlap == overlap
            and end - segments[-1].start <= config.DIARIZED_MAX_SEGMENT_SECONDS
        ):
            segments[-1].text = (segments[-1].text + " " + text).strip()
            segments[-1].end = end
        else:
            segments.append(
                MeetingSegment(
                    speaker=labels[speaker],
                    start=start,
                    end=end,
                    text=text,
                    confidence=confidence,
                    overlap=overlap,
                    profile_id=profile_ids[speaker],
                    cluster_id=speaker,
                )
            )
    return segments


def known_speaker_segments(
    sentences,
    speaker: str = "You",
    *,
    max_gap_seconds: float | None = None,
    max_segment_seconds: float | None = None,
) -> list[MeetingSegment]:
    """Convert one known-source ASR track without running diarization.

    Consecutive sentences merge unless the pause between them exceeds
    max_gap_seconds or the merged segment would exceed max_segment_seconds.
    """
    gap = config.KNOWN_SPEAKER_MAX_GAP_SECONDS if max_gap_seconds is None else max_gap_seconds
    cap = (config.KNOWN_SPEAKER_MAX_SEGMENT_SECONDS
           if max_segment_seconds is None else max_segment_seconds)
    segments = []
    for sentence in sentences or []:
        text = sentence.text.strip()
        if not text:
            continue
        start, end = float(sentence.start), float(sentence.end)
        last = segments[-1] if segments else None
        if (last is not None and last.speaker == speaker
                and start - last.end <= gap and end - last.start <= cap):
            last.text = (last.text + " " + text).strip()
            last.end = end
        else:
            segments.append(MeetingSegment(speaker=speaker, start=start, end=end, text=text))
    return segments


_ECHO_WORD_RE = re.compile(r"[a-z0-9']+")


@dataclass(frozen=True)
class EchoCandidate:
    """Content-free match statistics for one mic sentence (for calibration
    logs); never carries transcript text."""
    words: int
    coverage: float
    lag_seconds: float
    removed: bool


def _timed_words(sentence, offset: float) -> list[tuple[str, float]]:
    """Normalized (word, start) pairs on the shared meeting timeline.

    parakeet tokens are sub-word pieces; a piece whose text starts with a
    space begins a new word."""
    tokens = getattr(sentence, "tokens", None) or []
    if not tokens:
        start = float(sentence.start) + offset
        return [(w, start) for w in _ECHO_WORD_RE.findall(sentence.text.lower())]
    pieces: list[list] = []
    for token in tokens:
        if not pieces or token.text[:1].isspace():
            pieces.append([token.text, float(token.start) + offset])
        else:
            pieces[-1][0] += token.text
    return [
        (w, start)
        for text, start in pieces
        for w in _ECHO_WORD_RE.findall(text.lower())
    ]


def remove_mic_echo(
    mic_sentences, system_sentences, *, mic_offset: float = 0.0,
    system_offset: float = 0.0,
) -> tuple[list, list[EchoCandidate]]:
    """Drop mic sentences that are the system track replayed through the
    speakers (speaker-mode bleed), keeping everything else in order.

    A sentence is echo only if >= MEETING_ECHO_MIN_COVERAGE of its words
    match nearby system words in order and the median mic-minus-system lag
    of those matches lies within [-MAX_LEAD, +MAX_LAG]. Text alone cannot
    tell echo from a deliberate repeat; arrival time can.
    """
    system_words = sorted(
        (w for s in system_sentences or [] for w in _timed_words(s, system_offset)),
        key=lambda item: item[1],
    )
    starts = [t for _, t in system_words]
    window = config.MEETING_ECHO_WINDOW_SECONDS
    kept, candidates = [], []
    for sentence in mic_sentences or []:
        mic_words = _timed_words(sentence, mic_offset)
        if not mic_words or not system_words:
            kept.append(sentence)
            continue
        lo = bisect_left(starts, mic_words[0][1] - window)
        hi = bisect_right(starts, mic_words[-1][1] + window)
        near = system_words[lo:hi]
        matcher = SequenceMatcher(
            None, [w for w, _ in mic_words], [w for w, _ in near], autojunk=False
        )
        lags = [
            mic_words[block.a + k][1] - near[block.b + k][1]
            for block in matcher.get_matching_blocks()
            for k in range(block.size)
        ]
        if not lags:
            kept.append(sentence)
            continue
        coverage = len(lags) / len(mic_words)
        lag = statistics.median(lags)
        removed = (
            coverage >= config.MEETING_ECHO_MIN_COVERAGE
            and -config.MEETING_ECHO_MAX_LEAD_SECONDS
            <= lag
            <= config.MEETING_ECHO_MAX_LAG_SECONDS
        )
        candidates.append(
            EchoCandidate(len(mic_words), round(coverage, 2), round(lag, 2), removed)
        )
        if not removed:
            kept.append(sentence)
    return kept, candidates


def shift_segments(
    segments: list[MeetingSegment], offset_seconds: float
) -> list[MeetingSegment]:
    for segment in segments:
        segment.start = max(0.0, segment.start + offset_seconds)
        segment.end = max(segment.start, segment.end + offset_seconds)
    return segments


def merge_tracks(*tracks: list[MeetingSegment]) -> list[MeetingSegment]:
    """Chronologically merge independent tracks without transcript cleanup.

    Deliberately do not remove similar text across tracks: repeated phrases and
    real overlap are valid meeting content, while speaker-mode echo cannot be
    distinguished reliably from text alone.
    """
    indexed = [
        (segment.start, segment.end, track_index, segment_index, segment)
        for track_index, track in enumerate(tracks)
        for segment_index, segment in enumerate(track)
    ]
    indexed.sort(key=lambda item: item[:4])
    return [item[-1] for item in indexed]
