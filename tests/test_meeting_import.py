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
    seen = []
    report = import_json_meetings(progress=lambda d, t: seen.append((d, t)))
    assert report.imported == 1
    assert report.skipped == [(bad.name, "TypeError")]
    assert bad.exists()
    # progress must still be driven to completion when a file is skipped,
    # not just when every file succeeds.
    assert seen[-1] == (2, 2)


def test_bad_profile_id_type_is_skipped_not_fatal(meetings_dir):
    # profile_id is the one legacy field Meeting.load passes through
    # without casting. A hand-edited file that slips a dict in there must
    # not blow up the whole import -- it should be skipped and reported
    # exactly like a file that fails to parse, and every good file around
    # it still imports and archives normally.
    good = _write(meetings_dir, "20260924-134023-aaaa")
    bad = _write(meetings_dir, "20260924-140000-cccc", segments=[
        {"speaker": "You", "start": 0.0, "end": 5.0, "text": "hi",
         "profile_id": {"a": 1}},
    ])
    report = import_json_meetings()
    assert report.imported == 1
    assert report.skipped == [(bad.name, "TypeError")]
    assert MeetingLibrary().get_meeting("20260924-134023-aaaa") is not None
    assert (meetings_dir / "legacy-json" / good.name).exists()
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


def test_no_half_import_across_several_meetings(meetings_dir, monkeypatch):
    # Verification failure on ONE meeting in a multi-meeting batch must roll
    # back every meeting in that batch, not just the offending one -- each
    # meeting is not committed separately.
    a = _write(meetings_dir, "20260924-134023-aaaa")
    b = _write(meetings_dir, "20260924-140000-bbbb", created="2026-09-24T14:00:00")
    real_convert = meeting_import._convert

    def lossy_convert(legacy):
        new = real_convert(legacy)
        if legacy.meeting_id == "20260924-140000-bbbb":
            new.segments = [
                MeetingSegment(s.speaker, s.start, s.end, s.text[:-1])
                for s in new.segments
            ]
        return new

    monkeypatch.setattr(meeting_import, "_convert", lossy_convert)
    with pytest.raises(ImportVerificationError):
        import_json_meetings()
    assert MeetingLibrary().count_meetings() == 0
    assert a.exists() and b.exists()


def test_speaker_set_mismatch_rolls_back(meetings_dir, monkeypatch):
    path = _write(meetings_dir, "20260924-134023-aaaa")

    def renaming(segment, **kw):
        return [MeetingSegment("Someone Else", segment.start, segment.end, segment.text)]

    monkeypatch.setattr(meeting_import, "split_long_segment", renaming)
    with pytest.raises(ImportVerificationError):
        import_json_meetings()
    assert MeetingLibrary().count_meetings() == 0
    assert path.exists()


def test_archiving_never_overwrites_an_existing_archive(meetings_dir):
    path = _write(meetings_dir, "20260924-134023-aaaa")
    import_json_meetings()
    archive_dir = meetings_dir / "legacy-json"
    original_contents = (archive_dir / path.name).read_text()
    # A different file lands back under the SAME name (e.g. a hand-restored
    # backup) and gets picked up on a later run. It's already present in
    # the library, so it is only archived, never re-inserted -- but
    # archiving it must not clobber the meeting already archived there.
    dup = _write(meetings_dir, "20260924-134023-aaaa", title="Restored copy")
    report = import_json_meetings()
    assert report.already_present == 1
    assert not dup.exists()
    assert (archive_dir / path.name).read_text() == original_contents
    siblings = sorted(archive_dir.glob("20260924-134023-aaaa*.json"))
    assert len(siblings) == 2
    contents = {p.read_text() for p in siblings}
    assert original_contents in contents
    assert len(contents) == 2  # neither copy was lost


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
