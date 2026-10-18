"""Keyword filter: case-insensitive and Turkish-letter tolerant."""

import pytest

from radar.filter import gate_keywords, match_keywords


@pytest.mark.parametrize(
    ("text", "keyword"),
    [
        ("i", "ı"),
        ("ı", "i"),
        ("I", "ı"),
        ("İ", "i"),
        ("ş", "s"),
        ("s", "Ş"),
        ("ğ", "g"),
        ("ü", "u"),
        ("Ü", "u"),
        ("ö", "o"),
        ("ç", "c"),
        ("C", "ç"),
    ],
)
def test_turkish_letters_match_either_way(text: str, keyword: str) -> None:
    """Breaks if a Turkish letter only matches its exact Unicode form."""
    assert match_keywords(f"hello {text} there", [keyword]) == [keyword]


def test_match_is_case_insensitive_and_keeps_keyword_order() -> None:
    """Breaks if CREDITS misses, or hits follow text order instead of keyword order."""
    hits = match_keywords(
        "A GRANT and some CREDITS arrived",
        ["credits", "grant", "perk"],
    )

    assert hits == ["credits", "grant"]


def test_folded_keyword_matches_and_is_returned_once() -> None:
    """Breaks if ücretsiz misses ucretsiz, or a repeated keyword is returned twice."""
    hits = match_keywords(
        "Ucretsiz destek for a Startup Programı",
        ["ücretsiz", "ücretsiz", "startup program", "kredi"],
    )

    assert hits == ["ücretsiz", "startup program"]


def test_unrelated_text_matches_nothing() -> None:
    """Breaks if a miss is reported as a hit."""
    assert match_keywords("office chairs on sale", ["credits", "hibe"]) == []


def test_strict_gate_needs_a_title_hit_or_two_hits() -> None:
    """Breaks if one body-only hit passes a discovery source."""
    keywords = ["credits", "grant"]

    assert gate_keywords("Office hours", "startup credits", keywords, strict=True) == []
    assert gate_keywords("Startup credits", "A short note.", keywords, strict=True) == [
        "credits"
    ]
    assert gate_keywords(
        "Office hours", "startup credits and a grant", keywords, strict=True
    ) == ["credits", "grant"]
    assert gate_keywords("Office hours", "startup credits", keywords, strict=False) == [
        "credits"
    ]
