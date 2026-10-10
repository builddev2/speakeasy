"""Action items: pure parsing, validation, matching and date rules.

No SQL and no I/O. action_item_store.py, mcp_tools.py and the meetings
bridge build on these; frontend/src/meetings/actionItems.ts mirrors
due_bucket. Every ValueError message is safe to show Claude or the user.
"""

import difflib
import re
from dataclasses import dataclass
from datetime import date, timedelta

from .tag_names import clean_tag_names

SIMILAR_TASK_RATIO = 0.85
PRIORITIES = ("high", "normal", "low")
MAX_TASK, MAX_OWNER, MAX_PHRASE, MAX_NOTES = 500, 80, 80, 5000
MAX_ITEM_TAGS = 10
MAX_ITEMS_PER_SAVE = 50
MAX_BULK_IDS = 500
MAX_NAME, MAX_ALIASES, MAX_ALIAS = 80, 10, 40


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


UNSET = _Unset()  # "argument not given", distinct from None (= clear)


@dataclass(frozen=True)
class ItemInput:
    task: str
    owner: str = ""
    due_date: str | None = None
    due_phrase: str = ""
    priority: str = "normal"
    tags: tuple[str, ...] = ()


_OWNER_DASH = re.compile(r"\s+[—–]\s+")
_TRAILING_PAREN = re.compile(r"\s*\(([^()]{1,80})\)\s*$")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NON_WORD = re.compile(r"[\W_]+")
_OWNER_SPLIT = re.compile(r"\s*(?:,|&|/|\band\b)\s*", re.IGNORECASE)
_FIELDS = {"task", "owner", "due_date", "due_phrase", "priority", "tags"}


def _squash(text: str) -> str:
    return " ".join(text.split())


def check_id(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Action item id must be a number.")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("Action item id must be a whole number.")
    if int(value) < 1:
        raise ValueError("Action item id must be positive.")
    return int(value)


def check_task(value) -> str:
    if not isinstance(value, str) or not _squash(value):
        raise ValueError("Each action item needs a task (text).")
    task = _squash(value)
    if len(task) > MAX_TASK:
        raise ValueError(f"Task is longer than {MAX_TASK} characters.")
    return task


def clean_owner(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("owner must be text.")
    owner = _squash(value)
    if owner.casefold() == "unassigned":
        return ""
    if len(owner) > MAX_OWNER:
        raise ValueError(f"owner is longer than {MAX_OWNER} characters.")
    return owner


def check_priority(value) -> str:
    if value is None or value == "":
        return "normal"
    if value not in PRIORITIES:
        raise ValueError("priority must be high, normal or low.")
    return value


def check_status(value) -> str:
    if value not in ("open", "done"):
        raise ValueError("status must be open or done.")
    return value


def check_notes(value) -> str:
    if not isinstance(value, str):
        raise ValueError("notes must be text.")
    notes = value.rstrip()
    if len(notes) > MAX_NOTES:
        raise ValueError("Notes are longer than 5,000 characters.")
    return notes


def check_date(value, name: str = "due_date") -> str | None:
    if value is None or value == "":
        return None
    message = f"{name} must be a date like 2026-10-16."
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise ValueError(message)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(message) from None
    if not 2000 <= parsed.year <= 2100:
        raise ValueError(message)
    return value


def check_tags(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise ValueError("tags must be a list of text.")
    tags = clean_tag_names(value)
    if len(tags) > MAX_ITEM_TAGS:
        raise ValueError(f"At most {MAX_ITEM_TAGS} tags per action item.")
    return tags


def _check_phrase(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("due_phrase must be text.")
    phrase = _squash(value)
    if len(phrase) > MAX_PHRASE:
        raise ValueError(f"due_phrase is longer than {MAX_PHRASE} characters.")
    return phrase


def parse_legacy(text) -> ItemInput:
    """'Owner — task (due)' -> parts. Owner only before an em/en dash, and
    only when it looks like names (<= 40 chars, <= 4 words)."""
    original = _squash(str(text))
    rest, owner = original, ""
    m = _OWNER_DASH.search(rest)
    if m:
        head = rest[:m.start()].strip()
        if head and len(head) <= 40 and len(head.split()) <= 4:
            owner, rest = head, rest[m.end():].strip()
    phrase = ""
    p = _TRAILING_PAREN.search(rest)
    if p and p.start() > 0:
        phrase, rest = p.group(1).strip(), rest[:p.start()].strip()
    if not rest:
        return ItemInput(task=original)
    return ItemInput(task=rest, owner=clean_owner(owner[:MAX_OWNER]), due_phrase=phrase)


def validate_input(raw) -> ItemInput:
    if isinstance(raw, str):
        item = parse_legacy(raw)
        return ItemInput(check_task(item.task), item.owner, None, _check_phrase(item.due_phrase))
    if not isinstance(raw, dict):
        raise ValueError("Each action item must be text or an object with task.")
    unknown = sorted(set(raw) - _FIELDS)
    if unknown:
        raise ValueError(f"Unknown action item field: {unknown[0]}.")
    return ItemInput(
        task=check_task(raw.get("task")),
        owner=clean_owner(raw.get("owner")),
        due_date=check_date(raw.get("due_date")),
        due_phrase=_check_phrase(raw.get("due_phrase")),
        priority=check_priority(raw.get("priority")),
        tags=tuple(check_tags(raw.get("tags"))),
    )


# Filler words dropped before comparing, so "Book the room" matches "Book
# room" (0.82 with them kept, under SIMILAR_TASK_RATIO).
_FILLER = frozenset({"a", "an", "the", "to", "of", "for", "with", "and", "on", "in", "at"})


def norm(text: str) -> str:
    words = _NON_WORD.sub(" ", text.casefold()).split()
    return " ".join(w for w in words if w not in _FILLER)


def similar(a: str, b: str) -> float:
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def clean_identity(name, aliases) -> dict:
    if not isinstance(name, str):
        raise ValueError("Your name must be text.")
    if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
        raise ValueError("Other names must be a list of text.")
    name = _squash(name)
    if len(name) > MAX_NAME:
        raise ValueError(f"Your name is longer than {MAX_NAME} characters.")
    kept, seen = [], set()
    for alias in (_squash(a) for a in aliases):
        if not alias or alias.casefold() in seen:
            continue
        if len(alias) > MAX_ALIAS:
            raise ValueError(f"Each other name is at most {MAX_ALIAS} characters.")
        seen.add(alias.casefold())
        kept.append(alias)
    if len(kept) > MAX_ALIASES:
        raise ValueError(f"At most {MAX_ALIASES} other names.")
    return {"name": name, "aliases": kept}


SELF_WORDS = frozenset({"you", "me", "myself", "mine"})


def is_mine(owner: str, identity: dict | None) -> bool:
    identity = identity or {}
    name = (identity.get("name") or "").strip()
    candidates = ([_squash(c).casefold() for c in [name, *identity.get("aliases", [])] if c.strip()]
                  if name else [])
    for part in _OWNER_SPLIT.split(owner or ""):
        part = _squash(part).casefold()
        if not part:
            continue
        if part in SELF_WORDS:
            return True
        for c in candidates:
            if part == c or part.split()[0] == c.split()[0]:
                return True
    return False


_OWNER_SPLIT_KEEP = re.compile(r"(\s*(?:,|&|/|\band\b)\s*)", re.IGNORECASE)


def normalize_owner(owner: str, identity: dict | None) -> str:
    """Replace self words (you/me/myself/mine) with the user's name, dropping
    a duplicate when the name is already listed. No name set: unchanged."""
    name = _squash((identity or {}).get("name") or "")
    if not name or not owner:
        return owner
    pieces = _OWNER_SPLIT_KEEP.split(owner)  # parts at even indexes, separators at odd
    parts, seps = pieces[0::2], pieces[1::2]
    is_self = [_squash(p).casefold() in SELF_WORDS for p in parts]
    if not any(is_self):
        return owner
    seen = {_squash(p).casefold() for p, s in zip(parts, is_self) if not s}
    keep = []
    for part, selfish in zip(parts, is_self):
        if not selfish:
            keep.append(part)
        elif name.casefold() in seen:
            keep.append(None)
        else:
            seen.add(name.casefold())
            keep.append(name)
    out = ""
    for i, part in enumerate(keep):
        if part is not None:
            out += (seps[i - 1] if out and i > 0 else "") + part
    try:
        result = clean_owner(out)
    except ValueError:
        return owner
    return result if result else owner


def effective_due(due_date, due_override, due_cleared):
    if due_cleared:
        return None, "user"
    if due_override:
        return due_override, "user"
    if due_date:
        return due_date, "claude"
    return None, None


def due_bucket(due: str | None, today: date) -> str:
    if not due:
        return "none"
    day = date.fromisoformat(due)
    if day < today:
        return "overdue"
    if day == today:
        return "today"
    week_end = today + timedelta(days=6 - today.weekday())  # Sunday
    return "week" if day <= week_end else "later"


def format_mirror(owner: str, task: str, due_phrase: str, due: str | None) -> str:
    text = f"{owner} — {task}" if owner else task
    when = due_phrase or due
    return f"{text} ({when})" if when else text
