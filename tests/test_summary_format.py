from speakeasy.summary_format import SUMMARY_INSTRUCTIONS, parse_summary


def test_new_format():
    text = ("TL;DR: Scoped the MVP.\n\n## Decisions\n- No client docs in MVP\n"
            "- Accuracy over speed\n\n## Key points\n- Top-3 match is success\n\n"
            "## Open questions\n- Team after ERB on Oct 22?")
    assert parse_summary(text) == [
        {"kind": "tldr", "text": "Scoped the MVP."},
        {"kind": "heading", "text": "Decisions"},
        {"kind": "bullets", "items": ["No client docs in MVP", "Accuracy over speed"]},
        {"kind": "heading", "text": "Key points"},
        {"kind": "bullets", "items": ["Top-3 match is success"]},
        {"kind": "heading", "text": "Open questions"},
        {"kind": "bullets", "items": ["Team after ERB on Oct 22?"]},
    ]


def test_legacy_free_form_summary():
    # Shape of the stored summary of meeting 20261001-165512-f167.
    text = ("Project Black working session (Part 2), ~31 min. Continuation\n"
            "of the sprint prep.\n\n"
            "CORE CONCEPT (consensus)\n- Field tool: assessor photographs equipment.\n"
            "- Success test: right answer first or in top 3.\n\n"
            "CLIENT-PROVIDED DOCUMENTS (pre-visit docs, as-builts, old roof reports)\n"
            "- DECISION: Not in MVP / no demo.\n\nNEXT\n- Continue tomorrow.\n\n"
            "Note: transcript has misrecognitions.")
    assert parse_summary(text) == [
        {"kind": "para", "text": "Project Black working session (Part 2), ~31 min. "
                                 "Continuation of the sprint prep."},
        {"kind": "heading", "text": "CORE CONCEPT (consensus)"},
        {"kind": "bullets", "items": ["Field tool: assessor photographs equipment.",
                                      "Success test: right answer first or in top 3."]},
        {"kind": "heading",
         "text": "CLIENT-PROVIDED DOCUMENTS (pre-visit docs, as-builts, old roof reports)"},
        {"kind": "bullets", "items": ["DECISION: Not in MVP / no demo."]},
        {"kind": "heading", "text": "NEXT"},
        {"kind": "bullets", "items": ["Continue tomorrow."]},
        {"kind": "para", "text": "Note: transcript has misrecognitions."},
    ]


def test_wrapped_bullet_and_tldr_continue_and_bold_is_stripped():
    text = ("**TL;DR:** First half\nsecond half.\n### Decisions:\n"
            "* **Ship** it\n  by Friday\n\n• Hire later")
    assert parse_summary(text) == [
        {"kind": "tldr", "text": "First half second half."},
        {"kind": "heading", "text": "Decisions"},
        {"kind": "bullets", "items": ["Ship it by Friday", "Hire later"]},
    ]


def test_caps_heading_rules():
    blocks = parse_summary("OK\n\nDECISIONS:\n\nRSMeans AND CTC\n\n" + "A" * 61)
    assert blocks == [
        {"kind": "para", "text": "OK"},               # fewer than 3 letters
        {"kind": "heading", "text": "DECISIONS"},      # trailing colon removed
        {"kind": "para", "text": "RSMeans AND CTC"},  # has lowercase
        {"kind": "para", "text": "A" * 61},            # longer than 60
    ]


def test_tldr_only_counts_first():
    assert parse_summary("Intro.\n\nTL;DR: late") == [
        {"kind": "para", "text": "Intro."}, {"kind": "para", "text": "TL;DR: late"}]


def test_empty():
    assert parse_summary("") == [] and parse_summary(None) == []


def test_instructions_name_the_sections_and_limits():
    for needle in ("TL;DR:", "## Decisions", "## Key points", "## Open questions",
                   "150 words", "Owner — task", "list_tags", "at most 3 new"):
        assert needle in SUMMARY_INSTRUCTIONS
