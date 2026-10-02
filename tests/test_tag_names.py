import pytest

from speakeasy.tag_names import clean_tag_name, clean_tag_names, tag_slug


@pytest.mark.parametrize("name", ["Project X", "project-x", "ProjectX",
                                  "  PROJECT_x ", "project.x!"])
def test_spelling_variants_share_one_slug(name):
    assert tag_slug(name) == "projectx"


def test_diacritics_and_compatibility_forms_fold():
    assert tag_slug("Café") == tag_slug("cafe") == "cafe"
    assert tag_slug("ﬁnance") == "finance"
    assert tag_slug("Straße") == "strasse"
    assert tag_slug("İstanbul") == "istanbul"


def test_no_stemming_and_digits_kept():
    assert tag_slug("plan") != tag_slug("plans")
    assert tag_slug("Q3 2026") == "q32026"


def test_clean_tag_name_collapses_whitespace_and_validates():
    assert clean_tag_name("  Q3   planning ") == "Q3 planning"
    assert clean_tag_name("x" * 40) == "x" * 40
    with pytest.raises(ValueError, match="at most 40 characters"):
        clean_tag_name("x" * 41)
    with pytest.raises(ValueError, match="letter or digit"):
        clean_tag_name("!!!")


def test_clean_tag_names_skips_blanks_and_keeps_first_spelling():
    assert clean_tag_names([" DMT", "dmt", "", "  ", "D-M-T", "VFA "]) == ["DMT", "VFA"]
