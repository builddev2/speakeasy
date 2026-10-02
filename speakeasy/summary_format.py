"""How Claude writes a meeting summary, and how the app lays one out.

SUMMARY_INSTRUCTIONS is handed to Claude by the pending_summaries MCP tool,
so the format lives in one place. parse_summary turns stored summary text
(this format, or the free-form text older summaries used) into blocks the
Meetings window renders with real spacing. Pure: no I/O, no AppKit.
"""

import re

SUMMARY_INSTRUCTIONS = """\
Write the summary in exactly this shape (about 150 words in total):

TL;DR: 1-2 sentences: what the meeting was for and where it landed.

## Decisions
- One line each. Write "- None" if nothing was decided.

## Key points
- At most 6, one line each: only what matters later.

## Open questions
- Unresolved issues, risks, and ideas parked for later.

Rules:
- Short, plain sentences. No speaker-mapping notes, no notes about
  transcription errors, no "Speaker 3": use a name if the transcript gives
  one, otherwise describe the role ("the facilitator").
- Action items go in action_items, not the summary, each as
  "Owner — task (due)"; use "Unassigned" when nobody took it. Parked ideas go
  under Open questions, not action items.
- Tags: call list_tags first and reuse existing tags (any spelling or alias
  matches). Create a new tag only for a genuinely new topic, at most 3 new
  per meeting; 3-8 tags in total. If save_notes says it would create too
  many new tags, drop the new ones and save again.
- If the meeting is under 2 minutes or has almost no content, the summary is
  just "TL;DR: Too short to summarise." with no sections, no action items
  and no tags.
- Save with save_notes (summary, action_items and tags together)."""

_MD_HEADING = re.compile(r"^#{1,6}\s+(.*)$")
_BULLET = re.compile(r"^[-*•]\s+(.*)$")
_TLDR = re.compile(r"^tl;?dr\s*:\s*(.*)$", re.IGNORECASE)
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_PARENS = re.compile(r"\([^)]*\)")
_MAX_CAPS_HEADING = 60


def _clean(text: str) -> str:
    return _BOLD.sub(r"\1", text).strip()


def _caps_heading(line: str) -> bool:
    # Older summaries used ALL-CAPS section lines such as
    # "CORE CONCEPT (consensus)"; a parenthesised aside may be lowercase.
    core = _PARENS.sub("", line).strip()
    letters = [c for c in core if c.isalpha()]
    return (len(core) <= _MAX_CAPS_HEADING and len(letters) >= 3
            and not any(c.islower() for c in letters))


def parse_summary(text: str | None) -> list[dict]:
    """Blocks: tldr {text}, heading {text}, bullets {items}, para {text}."""
    blocks: list[dict] = []
    para: list[str] = []
    prev = None  # "tldr" / "bullet" while the previous line continues one

    def flush():
        if para:
            blocks.append({"kind": "para", "text": " ".join(para)})
            para.clear()

    for raw in (text or "").splitlines():
        line = _clean(raw)
        if not line:
            flush()
            prev = None
            continue
        tldr = _TLDR.match(line) if not blocks and not para else None
        if tldr:
            blocks.append({"kind": "tldr", "text": tldr.group(1).strip()})
            prev = "tldr"
            continue
        heading = _MD_HEADING.match(line)
        if heading:
            flush()
            blocks.append({"kind": "heading",
                           "text": heading.group(1).strip().rstrip(":").strip()})
            prev = None
            continue
        bullet = _BULLET.match(line)
        if bullet:
            flush()
            item = bullet.group(1).strip()
            if blocks and blocks[-1]["kind"] == "bullets":
                blocks[-1]["items"].append(item)
            else:
                blocks.append({"kind": "bullets", "items": [item]})
            prev = "bullet"
            continue
        # ALL-CAPS section lines only start a block; mid-paragraph or
        # mid-bullet they are just a wrapped line ("... wraps to\nERB.").
        if prev is None and not para and _caps_heading(line):
            blocks.append({"kind": "heading", "text": line.rstrip(":").strip()})
            continue
        if prev == "bullet":
            blocks[-1]["items"][-1] = f'{blocks[-1]["items"][-1]} {line}'.strip()
        elif prev == "tldr":
            blocks[-1]["text"] = f'{blocks[-1]["text"]} {line}'.strip()
        else:
            para.append(line)
    flush()
    return blocks
