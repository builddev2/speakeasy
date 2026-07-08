"""Unit tests for the read-aloud alignment core (training.py).

Only the pure functions are exercised — _find_sublist and _capture — so no
recorder, model, or hotkey is involved.
"""

from speakeasy.training import _capture, _find_sublist


# -- _find_sublist ----------------------------------------------------------


def test_find_sublist_found_in_middle():
    assert _find_sublist(["a", "b", "c", "d"], ["b", "c"]) == 1


def test_find_sublist_at_start_and_end():
    assert _find_sublist(["x", "y", "z"], ["x"]) == 0
    assert _find_sublist(["x", "y", "z"], ["z"]) == 2


def test_find_sublist_missing_returns_none():
    assert _find_sublist(["a", "b"], ["c"]) is None


def test_find_sublist_multiword_phrase():
    hay = "it is a clone of wispr flow".split()
    assert _find_sublist(hay, ["wispr", "flow"]) == 5


# -- _capture ---------------------------------------------------------------


def test_capture_clean_when_target_heard_correctly(make_profile):
    p = make_profile()
    clean, saved = _capture(
        "Deploy the service to Kubernetes.",
        ["Kubernetes"],
        "deploy the service to kubernetes",
        p,
    )
    assert clean is True
    assert saved == 0
    assert p.corrections == {}


def test_capture_saves_mishearing_of_target(make_profile):
    p = make_profile()
    clean, saved = _capture(
        "Deploy the service to Kubernetes.",
        ["Kubernetes"],
        "deploy the service to communities",
        p,
    )
    assert clean is False
    assert saved == 1
    assert p.corrections == {"communities": "Kubernetes"}


def test_capture_saves_multiword_target_as_one_unit(make_profile):
    p = make_profile()
    clean, saved = _capture(
        "It's a clone of Wispr Flow.",
        ["Wispr Flow"],
        "it's a clone of whisper flo",
        p,
    )
    assert clean is False
    assert saved == 1
    # The whole phrase is saved together — not "whisper flo flow" fragments.
    assert p.corrections == {"whisper flo": "Wispr Flow"}


def test_capture_skips_when_heard_span_is_common_words(make_profile):
    p = make_profile()
    # Target misheard as an everyday word — must not clobber "to"/"the"/etc.
    clean, saved = _capture(
        "Restart the nginx reverse proxy.",
        ["nginx"],
        "restart the the reverse proxy",
        p,
    )
    assert clean is False       # it was misheard
    assert saved == 0           # but nothing usable/safe to save
    assert p.corrections == {}


def test_capture_ignores_untargeted_words(make_profile):
    p = make_profile()
    # "service" is misheard but not a target, so no correction is saved.
    clean, saved = _capture(
        "Deploy the service to Kubernetes.",
        ["Kubernetes"],
        "deploy the serviss to kubernetes",
        p,
    )
    assert clean is True        # every *target* was correct
    assert saved == 0
    assert p.corrections == {}
