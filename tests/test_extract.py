"""Main-text extraction, normalization, hashing, and paragraph diffs."""

from pathlib import Path

from radar.extract import added_items, extract_main_text, hash_item, normalize

FIXTURES = Path(__file__).parent / "fixtures"
PAGE_URL = "https://example.com/replace-me"
PAGE_TITLE = "Example page"


def _page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_normalize_ignores_timestamp_and_counter_changes() -> None:
    """Breaks if a date, time, or view-count edit changes the normalized text."""
    earlier = (
        "Eligible startups can apply for promotional cloud credits.\n\n"
        "Updated 2026-10-01 09:00 UTC. 1,204 views."
    )
    later = (
        "Eligible startups can apply for promotional cloud credits.\n\n"
        "Updated 2026-10-11 15:30 UTC. 9,999 views."
    )

    assert normalize(earlier) == normalize(later)
    assert added_items(earlier, later, url=PAGE_URL, title=PAGE_TITLE) == []


def test_diff_returns_only_added_paragraphs() -> None:
    """Breaks if a new paragraph is missed or an unchanged paragraph is re-emitted."""
    previous = (
        "Eligible startups can apply for promotional cloud credits.\n\n"
        "The program covers inference for a new account."
    )
    current = previous + "\n\nA new grant covers GPU hours for research teams."

    added = added_items(previous, current, url=PAGE_URL, title=PAGE_TITLE)

    assert len(added) == 1
    assert added[0].summary == "A new grant covers GPU hours for research teams."
    assert added[0].url == PAGE_URL
    assert added[0].content_hash == hash_item("", "", added[0].summary)


def test_first_snapshot_is_one_baseline_item() -> None:
    """Breaks if the first sight of a page emits one item per paragraph or none."""
    current = _page("page_v1.html")
    text = extract_main_text(current, selector=None)
    items = added_items(None, text, url=PAGE_URL, title=PAGE_TITLE)

    assert len(items) == 1
    assert "promotional cloud credits" in items[0].summary
    assert items[0].content_hash == hash_item("", "", text)


def test_html_timestamp_change_adds_nothing_and_new_paragraph_is_kept() -> None:
    """Breaks if a timestamp edit looks like content or a new paragraph is missed."""
    first = extract_main_text(_page("page_v1.html"), selector=None)
    second = extract_main_text(_page("page_v2.html"), selector=None)
    third = extract_main_text(_page("page_v3.html"), selector=None)

    assert added_items(first, second, url=PAGE_URL, title=PAGE_TITLE) == []
    added = added_items(second, third, url=PAGE_URL, title=PAGE_TITLE)
    assert [item.summary for item in added] == [
        "A new grant covers GPU hours for research teams."
    ]


def test_hash_item_uses_normalized_title_link_and_summary() -> None:
    """Breaks if cosmetic date edits in an RSS summary change the content hash."""
    stable = hash_item(
        "Grant deadline posted",
        "https://example.com/replace-me/grant",
        "Applications close on 2026-12-01.",
    )
    moved = hash_item(
        "Grant deadline posted",
        "https://example.com/replace-me/grant",
        "Applications close on 2026-12-15.",
    )

    assert stable == moved
    assert stable != hash_item(
        "Grant deadline posted",
        "https://example.com/replace-me/grant",
        "Applications close and a new credit was added.",
    )


def test_selector_is_used_when_main_text_extraction_is_empty(monkeypatch) -> None:
    """Breaks if an empty trafilatura result ignores the source selector."""
    monkeypatch.setattr("radar.extract.trafilatura.extract", lambda *_a, **_k: None)
    html = (
        "<html><body><div class='offer'>"
        "<p>Free credits for startups.</p></div></body></html>"
    )

    text = extract_main_text(html, selector=".offer")

    assert "Free credits for startups." in text


def test_selector_wins_and_keeps_one_line_per_card() -> None:
    """Breaks if a listing page diffs its SEO blurb instead of its cards."""
    html = (
        "<html><body><article><p>"
        + "A long introduction about careers and internships for students. " * 20
        + "</p></article>"
        "<div class='jobs'><h5>Security Intern</h5>\n<p>Example A.Ş.</p></div>"
        "<div class='jobs'><h5>Yazılım Stajyeri</h5><p>Örnek</p></div>"
        "</body></html>"
    )

    text = extract_main_text(html, selector=".jobs")

    assert text == "Security Intern Example A.Ş.\nYazılım Stajyeri Örnek"


def test_card_listing_first_fetch_is_one_item_per_card() -> None:
    """Breaks if a listing's open cards are scored as one blob on day one."""
    items = added_items(
        None,
        "Security Intern Example\nYazılım Stajyeri Örnek",
        url="https://example.com/jobs",
        title="Listing",
        split_first=True,
    )

    assert [item.summary for item in items] == [
        "Security Intern Example",
        "Yazılım Stajyeri Örnek",
    ]


def test_a_card_logo_names_the_company() -> None:
    """Breaks if a listing card that shows its company only as a logo loses it."""
    html = (
        "<html><body>"
        "<div class='jobs'><img alt='TEI - TUSAŞ Motor Sanayii logo'>"
        "<h5>Aday Mühendislik Programı</h5></div>"
        "<div class='jobs'><img alt='decoration'><h5>Security Intern</h5></div>"
        "</body></html>"
    )

    text = extract_main_text(html, selector=".jobs")

    assert text.splitlines() == [
        "TEI - TUSAŞ Motor Sanayii · Aday Mühendislik Programı",
        "Security Intern",
    ]
