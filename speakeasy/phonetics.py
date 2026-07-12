"""A small vendored phonetic key for fuzzy vocabulary matching (offline, pure).

Soundex-style consonant grouping, but without Soundex's fixed 4-char width —
longer words keep more signal, which matters for technical vocabulary. The
first letter is preserved; vowels drop out and act as separators; consecutive
identical consonant groups collapse. Sound-alikes and near-spellings map to the
same key ("clod"/"Claude" -> "C43"), which gates the fuzzy snap in profiles.py.
"""

# Soundex digit groups for consonants; vowels and h/w/y map to "" (separators).
_CODES = {
    **dict.fromkeys("bfpv", "1"),
    **dict.fromkeys("cgjkqsxz", "2"),
    **dict.fromkeys("dt", "3"),
    "l": "4",
    **dict.fromkeys("mn", "5"),
    "r": "6",
}


def phonetic_key(word: str) -> str:
    """Return a Soundex-style phonetic key, or "" if the word has no letters."""
    letters = [c for c in word.lower() if c.isalpha()]
    if not letters:
        return ""
    key = letters[0].upper()
    prev = _CODES.get(letters[0], "")
    for c in letters[1:]:
        code = _CODES.get(c, "")
        if code and code != prev:
            key += code
        prev = code  # vowels reset prev to "" so they separate equal groups
    return key
