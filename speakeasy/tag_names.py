"""Tag display names and canonical slugs.

A slug is a tag's identity: names with the same slug are the same tag
("Project X", "project-x", "ProjectX" -> "projectx"). Case, spacing,
punctuation and diacritics never split a tag; there is no stemming, so
"plan" and "plans" stay apart. Shared by meeting_store (the v3 backfill)
and meeting_library. Pure: no I/O.
"""

import unicodedata

MAX_TAG_CHARS = 40
MAX_TAGS_PER_MEETING = 20
MAX_NEW_TAGS_PER_SAVE = 3
MAX_DESCRIPTION_CHARS = 200


def tag_slug(name) -> str:
    # NFKD after casefold splits accents (and casefold's own combining dot,
    # as in "İ") into marks, which isalnum() then drops with punctuation.
    folded = unicodedata.normalize("NFKD", str(name).casefold())
    return "".join(c for c in folded if c.isalnum())


def clean_tag_name(raw) -> str:
    """One display name: whitespace collapsed, length and slug checked."""
    name = " ".join(str(raw).split())
    if len(name) > MAX_TAG_CHARS:
        raise ValueError(f"Tags are at most {MAX_TAG_CHARS} characters.")
    if not tag_slug(name):
        raise ValueError(f"Tag {name!r} must contain a letter or digit.")
    return name


def clean_tag_names(raws) -> list[str]:
    """Clean a list: blank entries skipped, one name per slug (first wins)."""
    seen, result = set(), []
    for raw in raws:
        if not str(raw).strip():
            continue
        name = clean_tag_name(raw)
        slug = tag_slug(name)
        if slug not in seen:
            seen.add(slug)
            result.append(name)
    return result
