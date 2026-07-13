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


def test_add_word_updates_fuzzy_index_without_reload():
    # A vocab word added at runtime must be usable by the fuzzy snapper
    # immediately — no app restart / profile reload.
    p = Profile("t")
    assert p.apply("kubernetis") == "kubernetis"  # not in vocab yet
    p.add_word("Kubernetes")
    assert p.apply("kubernetis") == "Kubernetes"  # snaps right away


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


# -- fuzzy vocabulary snapping ----------------------------------------------


def test_fuzzy_snaps_near_spelling_to_vocab_word():
    p = Profile("t", vocabulary=["Kubernetes"])
    assert p.apply("we deployed kubernetis today") == "we deployed Kubernetes today"


def test_fuzzy_snaps_misspelled_proper_noun():
    p = Profile("t", vocabulary=["Anthropic"])
    assert p.apply("i work at anthropik") == "i work at Anthropic"


def test_fuzzy_exact_vocab_word_left_as_written():
    # An exact (case-insensitive) vocab match is protected: a common-word
    # homograph like the fruit "apple" is NOT clobbered to the vocab "Apple".
    p = Profile("t", vocabulary=["Apple"])
    assert p.apply("i ate an apple") == "i ate an apple"


def test_fuzzy_leaves_unrelated_word_alone():
    p = Profile("t", vocabulary=["Claude"])
    assert p.apply("the cat sat") == "the cat sat"


def test_fuzzy_low_overlap_homophone_does_not_snap():
    # Passes the phonetic gate but fails the ratio gate — left for corrections.
    p = Profile("t", vocabulary=["Claude"])
    assert p.apply("a big clod of dirt") == "a big clod of dirt"


def test_fuzzy_preserves_surrounding_punctuation():
    p = Profile("t", vocabulary=["Kubernetes"])
    assert p.apply("(kubernetis).") == "(Kubernetes)."


def test_fuzzy_ignores_short_tokens():
    p = Profile("t", vocabulary=["Go"])
    # "go" is below FUZZY_MIN_TOKEN_LEN, so it is never touched.
    assert p.apply("we go now") == "we go now"


def test_corrections_run_before_fuzzy():
    # Exact correction wins; fuzzy does not re-touch the produced word.
    p = Profile("t", vocabulary=["Claude"], corrections={"clod": "Claude"})
    assert p.apply("hey clod") == "hey Claude"


def test_fuzzy_does_not_touch_correction_targets():
    # "Claude" is a correction target and a vocab word; a correct occurrence
    # is left exactly as written (no spurious re-snap / casing churn).
    p = Profile("t", vocabulary=["Claude"], corrections={"clawed": "Claude"})
    assert p.apply("Claude is here") == "Claude is here"


def test_fuzzy_disabled_is_noop(monkeypatch):
    from speakeasy import config
    monkeypatch.setattr(config, "FUZZY_VOCAB_ENABLED", False)
    p = Profile("t", vocabulary=["Kubernetes"])
    assert p.apply("kubernetis") == "kubernetis"


def test_import_vocabulary_rebuilds_once(make_profile):
    profile = make_profile()
    assert profile.import_vocabulary(["Kubernetes", "Claude", "Kubernetes", ""]) == 2
    assert profile.apply("kubernetis") == "Kubernetes"
