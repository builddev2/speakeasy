from datetime import date

import pytest

from speakeasy import action_items as ai


@pytest.mark.parametrize("text, owner, task, phrase", [
    ("Jason — Send budget draft (Fri)", "Jason", "Send budget draft", "Fri"),
    ("Unassigned — Book the room", "", "Book the room", ""),
    ("Siam, Refayet – Review the deck (by end of month)", "Siam, Refayet", "Review the deck", "by end of month"),
    ("Follow up - urgent", "", "Follow up - urgent", ""),           # plain hyphen is not an owner split
    ("A very long owner name here — task", "", "A very long owner name here — task", ""),  # 5 words: not an owner
    ("Just a task", "", "Just a task", ""),
    ("Alpha Beta Gamma Delta Epsilon — task", "", "Alpha Beta Gamma Delta Epsilon — task", ""),  # exactly 5 words
    ("Alpha Beta Gamma Delta — task", "Alpha Beta Gamma Delta", "task", ""),  # exactly 4 words splits
    ("Jason — (Fri)", "Jason", "(Fri)", ""),
    ("(Fri)", "", "(Fri)", ""),                                     # empty task falls back to the original
    ("Jason —   Send   it  ", "Jason", "Send it", ""),
])
def test_parse_legacy(text, owner, task, phrase):
    item = ai.parse_legacy(text)
    assert (item.owner, item.task, item.due_phrase, item.due_date) == (owner, task, phrase, None)


def test_validate_input_object():
    item = ai.validate_input({"task": " Ship  it ", "owner": "unassigned", "due_date": "2026-10-16",
                              "due_phrase": "by Friday", "priority": "high", "tags": ["Budget", "budget"]})
    assert item == ai.ItemInput("Ship it", "", "2026-10-16", "by Friday", "high", ("Budget",))


@pytest.mark.parametrize("raw, message", [
    ({"task": ""}, "task"),
    ({"task": "x" * 501}, "500"),
    ({"task": "ok", "owner": "x" * 81}, "80"),
    ({"task": "ok", "priority": "urgent"}, "priority"),
    ({"task": "ok", "due_date": "2026-02-30"}, "date"),
    ({"task": "ok", "due_date": "1999-12-31"}, "date"),
    ({"task": "ok", "due_phrase": "x" * 81}, "80"),
    ({"task": "ok", "tags": ["t"] * 0 + [f"t{i}" for i in range(11)]}, "10"),
    ({"task": "ok", "colour": "red"}, "colour"),
    (42, "text or an object"),
])
def test_validate_input_rejects(raw, message):
    with pytest.raises(ValueError, match=message):
        ai.validate_input(raw)


def test_check_id_accepts_integral_float_and_rejects_bool():
    assert ai.check_id(7.0) == 7 and ai.check_id(7) == 7
    for bad in (True, 7.5, 0, -1, "7", None):
        with pytest.raises(ValueError):
            ai.check_id(bad)


def test_check_notes_limit_and_keeps_newlines():
    assert ai.check_notes("a\nb  ") == "a\nb"
    with pytest.raises(ValueError, match="5,000"):
        ai.check_notes("x" * 5001)


def test_similar():
    assert ai.norm("Send the Budget-draft!") == "send budget draft"
    assert ai.similar("Send budget draft", "send the budget draft") >= ai.SIMILAR_TASK_RATIO
    assert ai.similar("Book the room", "Book room") >= ai.SIMILAR_TASK_RATIO   # 0.82 without filler removal
    assert ai.similar("Send budget draft", "Book the room") < ai.SIMILAR_TASK_RATIO
    assert ai.similar("Email Siam", "Email Refayet") < ai.SIMILAR_TASK_RATIO


@pytest.mark.parametrize("owner, expected", [
    ("Jason", True), ("jason chiu", True), ("JC", True), ("Siam, Jason", True),
    ("Siam and JC", True), ("Siam & Jas", True), ("Siam", False), ("", False), ("Jasonette", False),
])
def test_is_mine(owner, expected):
    identity = {"name": "Jason Chiu", "aliases": ["JC", "Jas"]}
    assert ai.is_mine(owner, identity) is expected


@pytest.mark.parametrize("identity", [None, {"name": "", "aliases": []}, {"name": "Jason Chiu", "aliases": ["JC"]}])
@pytest.mark.parametrize("owner, expected", [
    ("You", True), ("me", True), ("Myself", True), ("Siam & you", True),
    ("Young", False), ("Yousef", False), ("Mention", False), ("", False),
])
def test_is_mine_self_words(owner, expected, identity):
    assert ai.is_mine(owner, identity) is expected


def test_is_mine_without_name_is_false():
    assert ai.is_mine("Jason", {"name": "", "aliases": ["Jason"]}) is False


def test_clean_identity():
    assert ai.clean_identity("  Jason  Chiu ", ["JC", "jc", " ", "Jas"]) == \
        {"name": "Jason Chiu", "aliases": ["JC", "Jas"]}
    with pytest.raises(ValueError):
        ai.clean_identity("x" * 81, [])
    with pytest.raises(ValueError):
        ai.clean_identity("J", [f"a{i}" for i in range(11)])
    with pytest.raises(ValueError):
        ai.clean_identity("J", ["x" * 41])


def test_effective_due():
    assert ai.effective_due("2026-10-16", None, False) == ("2026-10-16", "claude")
    assert ai.effective_due("2026-10-16", "2026-10-20", False) == ("2026-10-20", "user")
    assert ai.effective_due("2026-10-16", None, True) == (None, "user")
    assert ai.effective_due(None, None, False) == (None, None)


SUN, MON = date(2026, 10, 11), date(2026, 10, 12)

@pytest.mark.parametrize("due, today, bucket", [
    (None, SUN, "none"),
    ("2026-10-11", SUN, "today"), ("2026-10-11", MON, "overdue"),
    ("2026-10-12", SUN, "later"),                 # Monday is next week when today is Sunday
    ("2026-10-18", MON, "week"), ("2026-10-19", MON, "later"),
    ("2026-10-10", SUN, "overdue"),
])
def test_due_bucket(due, today, bucket):
    assert ai.due_bucket(due, today) == bucket


def test_format_mirror():
    assert ai.format_mirror("Jason", "Send it", "by Friday", "2026-10-16") == "Jason — Send it (by Friday)"
    assert ai.format_mirror("", "Send it", "", "2026-10-16") == "Send it (2026-10-16)"
    assert ai.format_mirror("", "Send it", "", None) == "Send it"


def test_check_status():
    assert ai.check_status("open") == "open" and ai.check_status("done") == "done"
    for bad in ("closed", "", None, "Open"):
        with pytest.raises(ValueError, match="status must be open or done."):
            ai.check_status(bad)


def test_similar_is_zero_when_either_side_is_only_filler():
    assert ai.similar("the", "to") == 0.0
    assert ai.similar("the", "Send budget") == 0.0
    assert ai.similar("", "") == 0.0
