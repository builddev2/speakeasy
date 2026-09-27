import json
import re
from datetime import datetime

import pytest

from speakeasy import meeting_import
from speakeasy.meeting_import import (
    ImportVerificationError, import_json_meetings, legacy_files, split_long_segment,
)
from speakeasy.meeting_library import MeetingLibrary
from speakeasy.meetings import MeetingSegment


def _nonspace(text):
    return re.sub(r"\s", "", text)


def _write(meetings_dir, meeting_id, created="2026-09-24T13:40:23", duration=1380.0,
           segments=None, **extra):
    data = {"id": meeting_id, "title": f"Meeting {meeting_id}", "created": created,
            "duration_seconds": duration,
            "segments": segments if segments is not None else [
                {"speaker": "You", "start": 0.0, "end": 5.0, "text": "hello"},
                {"speaker": "Speaker 1", "start": 5.0, "end": 9.0, "text": "hi"}],
            **extra}
    path = meetings_dir / f"{meeting_id}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_short_segment_is_unchanged():
    seg = MeetingSegment("You", 0.0, 100.0, "short. text.")
    assert split_long_segment(seg) == [seg]


def test_long_segment_splits_at_sentences_with_interpolated_times():
    text = " ".join(f"Sentence number {i} is here." for i in range(200))
    seg = MeetingSegment("You", 10.0, 910.0, text, confidence=0.5, cluster_id=3)
    pieces = split_long_segment(seg)
    assert len(pieces) >= 14
    assert pieces[0].start == 10.0 and pieces[-1].end == 910.0
    assert all(a.end == pytest.approx(b.start) for a, b in zip(pieces, pieces[1:]))
    assert all(p.end - p.start <= 66.0 for p in pieces)
    assert _nonspace("".join(p.text for p in pieces)) == _nonspace(text)
    assert " ".join(p.text for p in pieces) == text
    assert all(p.text.endswith(".") for p in pieces[:-1])        # sentence boundaries
    assert {(p.speaker, p.confidence, p.cluster_id) for p in pieces} == {("You", 0.5, 3)}


def test_long_segment_without_punctuation_splits_by_words():
    text = " ".join(["word"] * 3000)
    pieces = split_long_segment(MeetingSegment("You", 0.0, 600.0, text))
    assert len(pieces) >= 10
    assert _nonspace("".join(p.text for p in pieces)) == _nonspace(text)


def test_import_happy_path(meetings_dir):
    a = _write(meetings_dir, "20260924-134023-aaaa")
    _write(meetings_dir, "20260923-100000-bbbb", created="2026-09-23T10:00:00",
           duration=60.0, capture_mode="mic_and_system")
    (meetings_dir / "notes.json").write_text("{}")             # not a meeting id
    seen = []
    report = import_json_meetings(progress=lambda d, t: seen.append((d, t)))
    assert (report.imported, report.already_present, report.skipped) == (2, 0, [])
    assert seen == [(1, 2), (2, 2)]
    lib = MeetingLibrary()
    m = lib.get_meeting("20260924-134023-aaaa")
    assert m.local_start.replace(tzinfo=None) == datetime(2026, 9, 24, 13, 17, 23)
    assert m.timestamps_approximate and m.source == "imported_json"
    assert m.title == "Meeting 20260924-134023-aaaa"
    assert not a.exists()
    assert (meetings_dir / "legacy-json" / a.name).exists()
    assert (meetings_dir / "notes.json").exists()
    assert legacy_files() == []


def test_malformed_file_is_skipped_reported_and_left(meetings_dir):
    _write(meetings_dir, "20260924-134023-aaaa")
    bad = _write(meetings_dir, "20260924-140000-cccc", segments=["not a dict"])
    report = import_json_meetings()
    assert report.imported == 1
    assert report.skipped == [(bad.name, "TypeError")]
    assert bad.exists()


def test_verification_failure_rolls_back_and_moves_nothing(meetings_dir, monkeypatch):
    path = _write(meetings_dir, "20260924-134023-aaaa")
    def lossy(segment, **kw):
        return [MeetingSegment(segment.speaker, segment.start, segment.end, segment.text[:-1])]
    monkeypatch.setattr(meeting_import, "split_long_segment", lossy)
    with pytest.raises(ImportVerificationError):
        import_json_meetings()
    assert MeetingLibrary().count_meetings() == 0
    assert path.exists()


def test_rerun_after_crash_between_commit_and_archive(meetings_dir):
    path = _write(meetings_dir, "20260924-134023-aaaa")
    import_json_meetings()
    (meetings_dir / "legacy-json" / path.name).rename(path)   # simulate un-archived file
    report = import_json_meetings()
    assert (report.imported, report.already_present) == (0, 1)
    assert MeetingLibrary().count_meetings() == 1
    assert not path.exists()


def test_default_legacy_title_is_regenerated_from_estimated_start(meetings_dir):
    # The old app stamped `created` and the default title when processing
    # ended, not when recording started. If we imported the literal default
    # title, it would carry the wrong (end) time. When the title is exactly
    # the old default computed from `created`, re-derive it from the
    # estimated start (created - duration) instead of keeping it verbatim.
    created = "2026-07-08T19:43:00"
    old_default = "Meeting — Jul 8, 7:43 PM"
    _write(meetings_dir, "20260708-194300-dddd", created=created, duration=1380.0,
           title=old_default)
    import_json_meetings()
    m = MeetingLibrary().get_meeting("20260708-194300-dddd")
    assert m.title == "Meeting — Jul 8, 7:20 PM"


def test_renamed_legacy_title_is_kept(meetings_dir):
    created = "2026-07-08T19:43:00"
    _write(meetings_dir, "20260708-194300-eeee", created=created, duration=1380.0,
           title="Sprint planning")
    import_json_meetings()
    m = MeetingLibrary().get_meeting("20260708-194300-eeee")
    assert m.title == "Sprint planning"
