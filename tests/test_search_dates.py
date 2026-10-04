from datetime import date

import pytest

from speakeasy.search_dates import parse_date_phrase

TODAY = date(2026, 10, 3)


@pytest.mark.parametrize("text, day, rest", [
    ("Oct 3", date(2026, 10, 3), ""),
    ("oct 3", date(2026, 10, 3), ""),
    ("October 3", date(2026, 10, 3), ""),
    ("Oct. 3", date(2026, 10, 3), ""),
    ("oct 3rd", date(2026, 10, 3), ""),
    ("Sept 3", date(2026, 9, 3), ""),
    ("Sep 3", date(2026, 9, 3), ""),
    ("3 Oct", date(2026, 10, 3), ""),
    ("03 oct", date(2026, 10, 3), ""),
    ("3rd October", date(2026, 10, 3), ""),
    ("1st May", date(2026, 5, 1), ""),
    ("Oct 3 2025", date(2025, 10, 3), ""),
    ("Oct 3, 2025", date(2025, 10, 3), ""),
    ("3 Oct 2025", date(2025, 10, 3), ""),
    ("3rd October, 2025", date(2025, 10, 3), ""),
    ("2026-10-03", date(2026, 10, 3), ""),
    ("today", date(2026, 10, 3), ""),
    ("Yesterday", date(2026, 10, 2), ""),
    ("standup Oct 3", date(2026, 10, 3), "standup"),
    ("Oct 3 standup review", date(2026, 10, 3), "standup review"),
    ("budget  today  please", date(2026, 10, 3), "budget please"),
])
def test_recognised_phrases(text, day, rest):
    assert parse_date_phrase(text, TODAY) == (day, rest)


def test_no_year_means_most_recent_not_after_today():
    assert parse_date_phrase("Oct 4", TODAY)[0] == date(2025, 10, 4)
    assert parse_date_phrase("Dec 20", date(2026, 1, 5))[0] == date(2025, 12, 20)
    assert parse_date_phrase("Feb 29", TODAY)[0] == date(2024, 2, 29)


def test_first_phrase_wins_and_later_one_stays_as_words():
    assert parse_date_phrase("Oct 3 vs Oct 4", TODAY) == (date(2026, 10, 3), "vs Oct 4")
    assert parse_date_phrase("3 Oct 4", TODAY) == (date(2026, 10, 3), "4")


@pytest.mark.parametrize("text", [
    "May", "march madness", "Monday standup", "10/3", "3.10", "3", "2026",
    "Feb 30", "31 Sept", "2026-13-01", "2026-02-30", "octopus 3", "todays plan",
    "", "   ", "Oct", "Oct 32",
])
def test_not_dates(text):
    assert parse_date_phrase(text, TODAY) == (None, text)


def test_title_with_time_after_date():
    assert parse_date_phrase("Meeting — Oct 3, 7:58 PM", TODAY) == (
        date(2026, 10, 3), "Meeting — , 7:58 PM")
