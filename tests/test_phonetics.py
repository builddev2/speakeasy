"""Unit tests for the phonetic key (phonetics.py) — pure, no hardware."""

from speakeasy.phonetics import phonetic_key


def test_sound_alikes_collide():
    assert phonetic_key("clod") == phonetic_key("Claude") == "C43"


def test_near_spellings_collide():
    assert phonetic_key("kubernetis") == phonetic_key("Kubernetes") == "K16532"


def test_anthropic_misspelling_collides():
    assert phonetic_key("anthropik") == phonetic_key("Anthropic") == "A53612"


def test_unrelated_words_differ():
    assert phonetic_key("cat") != phonetic_key("Claude")


def test_double_letters_collapse():
    assert phonetic_key("ll") == phonetic_key("l") == "L"


def test_no_letters_is_empty():
    assert phonetic_key("123!") == ""
    assert phonetic_key("") == ""


def test_case_insensitive():
    assert phonetic_key("CLAUDE") == phonetic_key("claude")
