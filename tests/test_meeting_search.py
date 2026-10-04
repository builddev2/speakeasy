from datetime import date, datetime, timedelta, timezone

import pytest

from speakeasy import meeting_library
from speakeasy.meeting_library import (
    HIT_CLOSE, HIT_OPEN, MeetingLibrary, NewMeeting, SearchHit,
    collapse_echoes, fts_query, mark_title,
)
from speakeasy.meetings import MeetingSegment

EDT = timezone(timedelta(hours=-4))


def _save(lib, day, *segs, **kw):
    return lib.save_meeting(NewMeeting(
        segments=[MeetingSegment(sp, s, e, t) for sp, s, e, t in segs],
        duration_seconds=600, started_at=datetime(2026, 9, day, 13, 0, tzinfo=EDT), **kw))


def test_fts_query_quotes_every_term():
    assert fts_query('C++ "unbalanced AND NEAR( -x') == '"C" "unbalanced" "AND" "NEAR" "x"'
    assert fts_query("rbac cognos", any_term=True) == '"rbac" OR "cognos"'
    assert fts_query("  ?!  ") is None


@pytest.mark.parametrize("query", ['C++', '"', 'AND', 'NEAR(', '-x', '🙂', '', 'a:b*'])
def test_hostile_queries_never_raise(library_path, query):
    lib = MeetingLibrary()
    _save(lib, 24, ("You", 0, 5, "we use C and cognos"))
    assert isinstance(lib.search(query), list)


def test_ranked_hits_with_marked_snippets(library_path):
    lib = MeetingLibrary()
    a = _save(lib, 24, ("You", 0, 5, "cognos reporting is massive"),
              ("Speaker 1", 6, 9, "unrelated chat"))
    _save(lib, 23, ("You", 0, 5, "nothing relevant here"))
    hits = lib.search("Cognos reports")
    assert [h.meeting_id for h in hits] == [a]
    assert hits[0].kind == "transcript" and hits[0].speaker == "You"
    assert hits[0].start_seconds == 0 and hits[0].segment_index == 0
    assert f"{HIT_OPEN}cognos{HIT_CLOSE}" in hits[0].snippet
    assert f"{HIT_OPEN}reporting{HIT_CLOSE}" in hits[0].snippet  # porter stem


def test_all_terms_first_then_any_term_fallback(library_path):
    lib = MeetingLibrary()
    both = _save(lib, 24, ("You", 0, 5, "rbac and cognos gaps"))
    one = _save(lib, 23, ("You", 0, 5, "rbac only"))
    assert [h.meeting_id for h in lib.search("rbac cognos")] == [both]
    assert {h.meeting_id for h in lib.search("rbac sso")} == {both, one}


def test_diacritics_and_filters(library_path):
    lib = MeetingLibrary()
    a = _save(lib, 24, ("You", 0, 5, "lunch at the café"))
    b = _save(lib, 20, ("You", 0, 5, "cafe again"))
    lib.save_notes(a, tags=["DMT"])
    lib.link_people(b, [("Refayet", None, "attendee")])
    assert {h.meeting_id for h in lib.search("cafe")} == {a, b}
    assert [h.meeting_id for h in lib.search("cafe", tag="dmt")] == [a]
    assert [h.meeting_id for h in lib.search("cafe", person="Refayet")] == [b]
    assert [h.meeting_id for h in lib.search("cafe", from_date="2026-09-22")] == [a]


def test_notes_are_searchable(library_path):
    lib = MeetingLibrary()
    a = _save(lib, 24, ("You", 0, 5, "hello"))
    lib.save_notes(a, summary="Recommend sustaining VFA", action_items=["Estimate modernization"])
    hits = lib.search("modernization")
    assert hits[0].kind == "notes" and hits[0].meeting_id == a
    assert hits[0].speaker is None and hits[0].start_seconds is None


def test_echo_across_tracks_collapses_to_one_hit(library_path):
    lib = MeetingLibrary()
    text = "the product ACP is so thin it does not make sense to invest"
    a = _save(lib, 24, ("You", 100, 150, text), ("Speaker 1", 120, 131, text),
              ("Speaker 1", 900, 905, "ACP is thin, I said it again later"))
    hits = lib.search("ACP thin invest")
    assert len([h for h in hits if h.meeting_id == a and h.start_seconds < 200]) == 1
    merged = next(h for h in hits if h.start_seconds < 200)
    assert set([merged.speaker, *merged.also_speakers]) == {"You", "Speaker 1"}


def test_collapse_keeps_different_meetings_and_distant_times():
    def hit(mid, start, end, speaker, snippet):
        return SearchHit(mid, "t", "2026-09-24T17:00:00Z", -240, "transcript",
                         speaker, start, end, 0, snippet, [], -1.0)
    kept = collapse_echoes([
        hit("m1", 0, 10, "You", "ACP is so thin"),
        hit("m1", 12, 14, "Speaker 1", "ACP is so thin"),   # overlaps within 5 s
        hit("m1", 300, 310, "Speaker 1", "ACP is so thin"), # far away: kept
        hit("m2", 0, 10, "You", "ACP is so thin"),          # other meeting: kept
    ])
    assert [(h.meeting_id, h.start_seconds) for h in kept] == [("m1", 0), ("m1", 300), ("m2", 0)]
    assert kept[0].also_speakers == ["Speaker 1"]


def test_collapse_keeps_overlapping_but_dissimilar_text():
    # Controller ruling: every collapse fixture above uses identical text, so
    # a mutation to the similarity threshold (e.g. `>= -1`) goes unnoticed.
    # Two hits in the same meeting with overlapping time ranges but clearly
    # different text (similarity < 0.8) must both be kept, not merged.
    def hit(mid, start, end, speaker, snippet):
        return SearchHit(mid, "t", "2026-09-24T17:00:00Z", -240, "transcript",
                         speaker, start, end, 0, snippet, [], -1.0)
    kept = collapse_echoes([
        hit("m1", 0, 10, "You", "the quarterly budget review is overdue"),
        hit("m1", 2, 12, "Speaker 1", "we should get lunch at the new taco place"),
    ])
    assert len(kept) == 2
    assert kept[0].also_speakers == []
    assert kept[1].also_speakers == []


def test_limit_is_clamped(library_path):
    lib = MeetingLibrary()
    for day in range(1, 29):
        _save(lib, day, ("You", 0, 5, "standup notes"))
    assert len(lib.search("standup", limit=500)) == 28
    assert len(lib.search("standup", limit=0)) == 1


def test_limit_is_clamped_to_50(library_path):
    # Controller ruling: assert the upper clamp itself, not just that a
    # small dataset returns fewer than 50. Save 60 distinct meetings (each
    # a separate day) so the collapse logic keeps all of them, and confirm
    # search(..., limit=1000) still returns exactly 50.
    lib = MeetingLibrary()
    base = datetime(2026, 1, 1, 13, 0, tzinfo=EDT)
    for i in range(60):
        day = base + timedelta(days=i)
        lib.save_meeting(NewMeeting(
            segments=[MeetingSegment("You", 0, 5, "quarterly standup notes")],
            duration_seconds=600, started_at=day))
    assert len(lib.search("standup", limit=1000)) == 50


def test_snippet_is_about_30_words(library_path):
    # Fix round 1, ruling 1: nothing asserted the snippet() token count, so
    # a regression back to the plan's 24 (instead of the spec's ~30) would
    # go unnoticed. Put the match near the middle of a ~60-word segment and
    # check the stripped snippet has more than 24 and at most 30 words.
    lib = MeetingLibrary()
    before = " ".join(f"before{i}" for i in range(30))
    after = " ".join(f"after{i}" for i in range(29))
    text = f"{before} needleword {after}"
    _save(lib, 24, ("You", 0, 5, text))
    hits = lib.search("needleword")
    plain = hits[0].snippet.replace(HIT_OPEN, "").replace(HIT_CLOSE, "")
    word_count = len([w for w in plain.split() if w != "…"])
    assert 24 < word_count <= 30


def test_stronger_match_ranks_first_within_a_kind(library_path):
    # bm25 is not comparable across the transcript and notes tables, so the
    # order is rank within each kind (transcript first on ties). Within one
    # kind the stronger match must still come first, which only the Python
    # re-sort guarantees once both kinds are concatenated.
    lib = MeetingLibrary()
    weak = _save(lib, 20, ("You", 0, 5, "gizmo mentioned once here and a lot of other words"))
    strong = _save(lib, 24, ("You", 0, 5, "gizmo gizmo gizmo"))
    lib.save_notes(weak, summary="gizmo")
    hits = lib.search("gizmo")
    assert [(h.kind, h.meeting_id) for h in hits] == [
        ("transcript", strong), ("notes", weak), ("transcript", weak)]


def test_notes_filter_excludes_out_of_range_meeting(library_path):
    # Fix round 1, ruling 3: nothing asserted that from_date/to_date/tag/
    # person filters actually apply to the notes_fts query, so a stray
    # `({where} OR 1)` in the notes branch would go unnoticed. Two meetings
    # each have a notes-only match; only the in-range one should come back.
    lib = MeetingLibrary()
    a = _save(lib, 24, ("You", 0, 5, "hello"))
    lib.save_notes(a, summary="mentions zephyrsummary project")
    b = _save(lib, 10, ("You", 0, 5, "hello"))
    lib.save_notes(b, summary="also mentions zephyrsummary project")
    assert {h.meeting_id for h in lib.search("zephyrsummary")} == {a, b}
    assert [h.meeting_id for h in lib.search("zephyrsummary", from_date="2026-09-22")] == [a]


def test_collapse_keeps_notes_hit_alongside_transcript_hit():
    # Fix round 1, ruling 4: the `kind == "transcript"` guard in
    # collapse_echoes has no direct test. A notes hit has start_seconds =
    # None, so removing the guard would raise TypeError when comparing it
    # against a transcript hit's numeric times in the same meeting.
    transcript = SearchHit("m1", "t", "2026-09-24T17:00:00Z", -240, "transcript",
                            "You", 0, 10, 0, "ACP is thin", [], -1.0)
    notes = SearchHit("m1", "t", "2026-09-24T17:00:00Z", -240, "notes",
                       None, None, None, None, "ACP is thin", [], -1.0)
    kept = collapse_echoes([transcript, notes])
    assert len(kept) == 2
    assert {h.kind for h in kept} == {"transcript", "notes"}


def test_search_none_or_empty_query_returns_empty_list(library_path):
    # Fix round 1, ruling 5: search(None) used to call fts_query(str(None))
    # and actually search for the literal word "None".
    lib = MeetingLibrary()
    _save(lib, 24, ("You", 0, 5, "None of this matters"))
    assert lib.search(None) == []
    assert lib.search("") == []


def test_notes_and_transcript_hits_interleave_by_rank(library_path):
    lib = MeetingLibrary()
    for day, text in [(21, "budget talk"), (22, "the budget again"), (23, "budget budget")]:
        _save(lib, day, ("You", 0, 5, text))
    for day in (24, 25):
        mid = _save(lib, day, ("You", 0, 5, "hello"))
        lib.save_notes(mid, summary="Agreed the budget")
    kinds = [h.kind for h in lib.search("budget", limit=10)]
    assert kinds == ["transcript", "notes", "transcript", "notes", "transcript"]


def _titled(lib, day, title, text="nothing relevant", hour=13):
    return lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0, 5, text)], duration_seconds=600, title=title,
        started_at=datetime(2026, 9, day, hour, 0, tzinfo=EDT)))


@pytest.fixture
def sept_25(monkeypatch):
    monkeypatch.setattr(meeting_library, "_today", lambda: date(2026, 9, 25))


def test_mark_title_highlights_case_insensitively_and_merges_overlaps():
    o, c = HIT_OPEN, HIT_CLOSE
    assert mark_title("Test Meeting", ["meet"]) == f"Test {o}Meet{c}ing"
    assert mark_title("Stand-up", ["stand", "and-up"]) == f"{o}Stand-up{c}"
    assert mark_title("C++ review", ["c++"]) == f"{o}C++{c} review"
    assert mark_title("Plain", []) == "Plain"


def test_title_hits_come_first_newest_first(library_path):
    lib = MeetingLibrary()
    old = _titled(lib, 20, "Test Meeting")
    new = _titled(lib, 24, "Another test meeting")
    body = _titled(lib, 22, "Unrelated", text="this meeting ran long")
    hits = lib.search("meeting")
    assert [(h.meeting_id, h.kind) for h in hits[:2]] == [(new, "meeting"), (old, "meeting")]
    assert (body, "transcript") in [(h.meeting_id, h.kind) for h in hits[2:]]
    t = hits[0]
    assert t.speaker is None and t.start_seconds is None and t.segment_index is None
    assert t.also_speakers == [] and t.score == 0.0
    assert t.snippet == f"Another test {HIT_OPEN}meeting{HIT_CLOSE}"


def test_every_title_word_must_appear(library_path):
    lib = MeetingLibrary()
    both = _titled(lib, 24, "Budget review Q4")
    _titled(lib, 23, "Budget sync")
    assert [h.meeting_id for h in lib.search("review budget") if h.kind == "meeting"] == [both]


def test_meeting_can_be_title_and_content_hit(library_path):
    lib = MeetingLibrary()
    mid = _titled(lib, 24, "Cognos review", text="cognos reporting is massive")
    assert [(h.meeting_id, h.kind) for h in lib.search("cognos")] == [
        (mid, "meeting"), (mid, "transcript")]


def test_date_only_lists_day_earliest_first(library_path, sept_25):
    lib = MeetingLibrary()
    late = _titled(lib, 24, "Retro", hour=16)
    early = _titled(lib, 24, "Stand-up", hour=9)
    _titled(lib, 23, "Other day")
    hits = lib.search("Sep 24")
    assert [(h.meeting_id, h.kind) for h in hits] == [(early, "meeting"), (late, "meeting")]
    assert hits[0].snippet == "Stand-up"


def test_date_only_with_no_meeting_that_day_is_empty(library_path, sept_25):
    lib = MeetingLibrary()
    _titled(lib, 24, "Retro")
    assert lib.search("Sep 21") == []


def test_default_title_found_by_word_and_by_date(library_path, sept_25):
    lib = MeetingLibrary()
    mid = lib.save_meeting(NewMeeting(
        segments=[MeetingSegment("You", 0, 5, "hello")], duration_seconds=600,
        started_at=datetime(2026, 9, 24, 13, 58, tzinfo=EDT)))
    assert lib.search("meeting")[0].meeting_id == mid
    by_date = lib.search("Sep 24")
    assert [(h.meeting_id, h.kind) for h in by_date] == [(mid, "meeting")]


def test_date_narrows_words(library_path, sept_25):
    lib = MeetingLibrary()
    on_day = _titled(lib, 24, "Standup", text="cognos")
    _titled(lib, 23, "Standup", text="cognos")
    assert {h.meeting_id for h in lib.search("standup Sep 24")} == {on_day}
    assert {h.meeting_id for h in lib.search("cognos 24 September")} == {on_day}
    assert lib.search("standup Sep 22") == []


def test_punctuation_left_by_date_is_ignored(library_path, sept_25):
    lib = MeetingLibrary()
    mid = _titled(lib, 24, "Standup")
    assert [h.meeting_id for h in lib.search("Sep 24, standup")] == [mid]


def test_date_and_explicit_range_intersect(library_path, sept_25):
    lib = MeetingLibrary()
    mid = _titled(lib, 24, "Standup")
    assert [h.meeting_id for h in lib.search("Sep 24", from_date="2026-09-20")] == [mid]
    assert lib.search("Sep 24", from_date="2026-09-25") == []
    assert lib.search("Sep 24", to_date="2026-09-23") == []


def test_today_uses_the_local_clock(library_path, monkeypatch):
    lib = MeetingLibrary()
    mid = _titled(lib, 24, "Standup")
    monkeypatch.setattr(meeting_library, "_today", lambda: date(2026, 9, 24))
    assert [h.meeting_id for h in lib.search("today")] == [mid]
    monkeypatch.setattr(meeting_library, "_today", lambda: date(2026, 9, 25))
    assert [h.meeting_id for h in lib.search("yesterday")] == [mid]
    assert lib.search("today") == []


def test_title_special_characters_match_literally(library_path):
    lib = MeetingLibrary()
    a = _titled(lib, 24, "C++ review")
    b = _titled(lib, 23, "100% done")
    c = _titled(lib, 22, "a_b sync")
    _titled(lib, 21, "axb sync")
    d = _titled(lib, 20, "a.b review")
    _titled(lib, 19, "axb review")
    assert [h.meeting_id for h in lib.search("a.b") if h.kind == "meeting"] == [d]
    assert mark_title("axb review", ["a.b"]) == "axb review"
    assert [h.meeting_id for h in lib.search("c++") if h.kind == "meeting"] == [a]
    assert [h.meeting_id for h in lib.search("100%") if h.kind == "meeting"] == [b]
    assert [h.meeting_id for h in lib.search("a_b") if h.kind == "meeting"] == [c]


def test_title_hits_respect_filters_and_limit(library_path):
    lib = MeetingLibrary()
    ids = [_titled(lib, d, "Weekly sync") for d in (20, 21, 22, 23)]
    lib.save_notes(ids[0], tags=["DMT"])
    assert [h.meeting_id for h in lib.search("weekly", tag="dmt")] == [ids[0]]
    assert len(lib.search("weekly", limit=2)) == 2


def test_punctuation_only_query_matches_nothing(library_path):
    lib = MeetingLibrary()
    _titled(lib, 24, "Standup")
    for query in ("?!", ", ", "-"):
        assert lib.search(query) == []
