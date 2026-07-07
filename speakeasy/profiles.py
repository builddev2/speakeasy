"""Per-user profiles: personal vocabulary + learned pronunciation corrections.

The speech model is fixed, so profiles improve accuracy with a correction
layer: training (training.py) records what the model actually hears when the
user says their own words, and stores heard → intended mappings here. At
dictation time apply() rewrites those misrecognitions in the transcript — one
pre-compiled regex substitution, so it adds no perceptible latency.

Each profile is a plain JSON file in PROFILES_DIR, hand-editable:

    {
      "name": "jason",
      "created": "2026-07-07T12:00:00",
      "vocabulary": ["Claude", "Wispr"],
      "corrections": {"clod": "Claude"}
    }
"""

import json
import re
from datetime import datetime

from . import config

# Profile names double as filenames; keep them boring and safe.
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,39}")

_PUNCT_RE = re.compile(r"[^\w\s']+")


def normalize(text: str) -> str:
    """Lowercase, strip punctuation (keep apostrophes), collapse whitespace."""
    return " ".join(_PUNCT_RE.sub(" ", text.lower()).split())


def list_profiles() -> list[str]:
    if not config.PROFILES_DIR.is_dir():
        return []
    return sorted(p.stem for p in config.PROFILES_DIR.glob("*.json"))


class Profile:
    def __init__(
        self,
        name: str,
        vocabulary: list[str] | None = None,
        corrections: dict[str, str] | None = None,
        created: str | None = None,
        sessions_done: list[str] | None = None,
    ) -> None:
        self.name = name
        self.vocabulary = vocabulary or []
        self.corrections = corrections or {}
        self.sessions_done = sessions_done or []
        self.created = created or datetime.now().isoformat(timespec="seconds")
        self._rebuild()

    # -- storage --------------------------------------------------------

    @property
    def path(self):
        return config.PROFILES_DIR / f"{self.name}.json"

    @classmethod
    def create(cls, name: str) -> "Profile":
        name = name.strip()
        if not _NAME_RE.fullmatch(name):
            raise ValueError(
                "Profile names use letters, digits, spaces, - and _ (max 40 chars)."
            )
        profile = cls(name)
        profile.save()
        return profile

    @classmethod
    def load(cls, name: str) -> "Profile":
        path = config.PROFILES_DIR / f"{name}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            name=name,
            vocabulary=data.get("vocabulary", []),
            corrections=data.get("corrections", {}),
            created=data.get("created"),
            sessions_done=data.get("sessions_done", []),
        )

    def save(self) -> None:
        config.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "name": self.name,
                    "created": self.created,
                    "vocabulary": self.vocabulary,
                    "corrections": self.corrections,
                    "sessions_done": self.sessions_done,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    # -- learning -------------------------------------------------------

    def add_word(self, word: str) -> None:
        if normalize(word) not in {normalize(w) for w in self.vocabulary}:
            self.vocabulary.append(word)
            self.save()

    def mark_session_done(self, name: str) -> None:
        if name not in self.sessions_done:
            self.sessions_done.append(name)
            self.save()

    def add_correction(self, heard: str, intended: str) -> bool:
        """Store a heard → intended mapping; returns False when skipped.

        Skips mappings that would rewrite a word the user actually uses:
        an empty heard form, a heard form equal to the intended form, or a
        heard form that is itself a vocabulary word / correction target.
        """
        heard = normalize(heard)
        if not heard or heard == normalize(intended):
            return False
        protected = {normalize(w) for w in self.vocabulary}
        protected |= {normalize(v) for v in self.corrections.values()}
        if heard in protected:
            return False
        self.corrections[heard] = intended
        self._rebuild()
        self.save()
        return True

    # -- applying -------------------------------------------------------

    def _rebuild(self) -> None:
        if not self.corrections:
            self._pattern = None
            return
        # Longest-first so multi-word phrases win over their sub-words.
        keys = sorted(self.corrections, key=len, reverse=True)
        alternation = "|".join(re.escape(k) for k in keys)
        self._pattern = re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)

    def apply(self, text: str) -> str:
        """Rewrite learned misrecognitions in a transcript."""
        if self._pattern is None:
            return text
        return self._pattern.sub(
            lambda m: self.corrections[normalize(m.group(0))], text
        )
