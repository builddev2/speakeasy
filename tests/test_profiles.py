"""Unit tests for the correction layer (profiles.py) — all pure, no hardware."""

import pytest

from speakeasy.profiles import Profile, list_profiles, load_profiles, normalize


# -- normalize --------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Claude", "claude"),
        ("  Wispr   Flow  ", "wispr flow"),
        ("U.S.A.!", "u s a"),
        ("don't", "don't"),          # apostrophes are kept
        ("Hello, world.", "hello world"),
        ("", ""),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


# -- apply ------------------------------------------------------------------


def test_apply_basic_correction():
    p = Profile("t", corrections={"clod": "Claude"})
    assert p.apply("i asked clod today") == "i asked Claude today"


def test_apply_case_insensitive_match_keeps_intended_casing():
    p = Profile("t", corrections={"clod": "Claude"})
    assert p.apply("Clod is great") == "Claude is great"


def test_apply_longest_phrase_wins():
    p = Profile("t", corrections={"wispr": "Whisper", "wispr flow": "Wispr Flow"})
    # The two-word phrase must win over the single-word key.
    assert p.apply("i use wispr flow daily") == "i use Wispr Flow daily"


def test_apply_only_whole_words():
    p = Profile("t", corrections={"cat": "dog"})
    assert p.apply("category") == "category"  # substring must not match


def test_apply_noop_without_corrections():
    p = Profile("t")
    assert p.apply("nothing to change") == "nothing to change"


def test_apply_handedited_nonnormalized_key_does_not_crash():
    """Regression: a hand-added key with capitals/punctuation must still work.

    Before the fix, apply() looked up the normalized matched text against a
    raw key and raised KeyError. Keys are now normalized on construction.
    """
    p = Profile("t", corrections={"Clod": "Claude", "U.S.": "United States"})
    assert p.apply("clod lives in the u s") == "Claude lives in the United States"
    assert p.corrections == {"clod": "Claude", "u s": "United States"}


def test_construction_dedupes_keys_that_normalize_equal():
    # "Clod" and "clod" collapse to one key; last one wins.
    p = Profile("t", corrections={"Clod": "First", "clod": "Second"})
    assert p.corrections == {"clod": "Second"}


# -- add_correction guards --------------------------------------------------


def test_add_correction_saves_valid(make_profile):
    p = make_profile()
    assert p.add_correction("communities", "Kubernetes") is True
    assert p.corrections["communities"] == "Kubernetes"


@pytest.mark.parametrize("heard", ["", "   ", "!!!"])
def test_add_correction_rejects_empty_heard(make_profile, heard):
    p = make_profile()
    assert p.add_correction(heard, "Kubernetes") is False


def test_add_correction_rejects_heard_equals_intended(make_profile):
    p = make_profile()
    assert p.add_correction("Claude", "Claude") is False


def test_add_correction_protects_vocabulary(make_profile):
    p = make_profile(vocabulary=["cache"])
    # "cache" is a real word the user uses; don't let a take rewrite it.
    assert p.add_correction("cache", "cash") is False


def test_add_correction_protects_existing_targets(make_profile):
    p = make_profile(corrections={"clod": "Claude"})
    # "claude" is already an intended output; refuse to map it away.
    assert p.add_correction("Claude", "cloud") is False


# -- add_word ---------------------------------------------------------------


def test_add_word_dedupes_case_insensitively(make_profile):
    p = make_profile()
    p.add_word("Claude")
    p.add_word("claude")
    assert p.vocabulary == ["Claude"]


# -- persistence & loading --------------------------------------------------


def test_save_load_roundtrip(make_profile, profiles_dir):
    p = make_profile("jason", vocabulary=["Claude"], corrections={"clod": "Claude"})
    p.mark_session_done("Everyday phrases")
    loaded = Profile.load("jason")
    assert loaded.vocabulary == ["Claude"]
    assert loaded.corrections == {"clod": "Claude"}
    assert loaded.sessions_done == ["Everyday phrases"]


def test_load_profiles_skips_corrupt_file(profiles_dir, capsys):
    (profiles_dir / "good.json").write_text('{"name": "good"}', encoding="utf-8")
    (profiles_dir / "broken.json").write_text("{ not valid json", encoding="utf-8")

    loaded = load_profiles()

    assert [p.name for p in loaded] == ["good"]  # broken one skipped, not fatal
    assert "broken" in capsys.readouterr().out


def test_list_profiles_empty_for_fresh_dir(profiles_dir):
    assert list_profiles() == []  # no .json files yet
