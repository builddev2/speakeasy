"""Find one date phrase in a search query ("Oct 3", "3rd October 2026",
"2026-10-03", "today", "yesterday") so search can narrow to that local day.
Pure: the caller passes today. Bare months, weekdays and numeric slash
dates are deliberately not dates — they collide with real words and
titles ("May", "Monday standup") or are ambiguous (10/3)."""

import re
from datetime import date, timedelta

_MONTH_NUMBERS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sept": 9, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
# Longest first, so "september" wins over "sep" and "sept" over "sep".
_MONTH = "(?P<month>" + "|".join(sorted(_MONTH_NUMBERS, key=len, reverse=True)) + r")\.?"
_DAY = r"(?P<day>\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?:,?\s+(?P<year>(?:19|2\d)\d\d)(?!\w))?"
_PATTERNS = [
    re.compile(rf"(?<!\w){_MONTH}\s+{_DAY}(?!\w){_YEAR}", re.I),
    re.compile(rf"(?<!\w){_DAY}\s+{_MONTH}(?!\w){_YEAR}", re.I),
    re.compile(r"(?<!\w)(?P<iso>(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2}))(?!\w)"),
    re.compile(r"(?<!\w)(?P<rel>today|yesterday)(?!\w)", re.I),
]


def _most_recent(month: int, day: int, today: date) -> date | None:
    # Eight years back always reaches a leap year for 29 February.
    for year in range(today.year, today.year - 9, -1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate <= today:
            return candidate
    return None


def _resolve(m: re.Match, today: date) -> date | None:
    groups = m.groupdict()
    if groups.get("rel"):
        return today if groups["rel"].lower() == "today" else today - timedelta(days=1)
    try:
        if groups.get("iso"):
            return date(int(groups["y"]), int(groups["m"]), int(groups["d"]))
        month = _MONTH_NUMBERS[groups["month"].lower()]
        day = int(groups["day"])
        if groups.get("year"):
            return date(int(groups["year"]), month, day)
    except ValueError:
        return None
    return _most_recent(month, day, today)


def parse_date_phrase(text: str, today: date) -> tuple[date | None, str]:
    best = None  # (start, end, date)
    for pattern in _PATTERNS:
        for m in pattern.finditer(text):
            day = _resolve(m, today)
            if day is not None:
                if best is None or m.start() < best[0]:
                    best = (m.start(), m.end(), day)
                break
    if best is None:
        return None, text
    start, end, day = best
    rest = " ".join((text[:start] + " " + text[end:]).split())
    return day, rest
