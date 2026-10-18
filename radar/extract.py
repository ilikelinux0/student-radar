"""HTML main-text extraction, item splitting, normalization, and hashing."""

import difflib
import hashlib
import re

import trafilatura
from bs4 import BeautifulSoup, Tag

from radar.models import Item

_PATTERNS = (
    re.compile(
        r"\b\d{4}-\d{2}-\d{2}(?:[t ]\d{2}:\d{2}(?::\d{2})?(?:z|[+-]\d{2}:?\d{2})?)?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:utc|gmt)?\b", re.IGNORECASE),
    re.compile(
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
        r"\s+\d{1,2},?\s+\d{4}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
        r"\s+\d{4}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"),
    re.compile(
        r"\b\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b[\d,]+\s*(?:views?|comments?|shares?|reads?)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:views?|comments?|shares?|reads?)\s*[:=]?\s*[\d,]+\b",
        re.IGNORECASE,
    ),
)


def normalize(text: str) -> str:
    """Lowercase, collapse whitespace, and drop dates, times, and counters.

    The result is for hashing and diffing. Stored excerpts keep the original text.
    """
    folded = text.lower()
    for pattern in _PATTERNS:
        folded = pattern.sub(" ", folded)
    return re.sub(r"\s+", " ", folded).strip()


def hash_text(text: str) -> str:
    """Return the sha256 hex digest of normalized text."""
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def hash_item(title: str, link: str, summary: str) -> str:
    """Hash a feed or API entry from its title, link, and summary."""
    return hash_text(f"{title}\n{link}\n{summary}")


def extract_main_text(html: str, selector: str | None = None) -> str:
    """Return the main text of an HTML page.

    A configured CSS selector wins: each matching element becomes one line, so
    a listing page diffs card by card. Without a selector, or when it matches
    nothing, trafilatura runs, and the full document text is the last resort.
    Trafilatura picks the page blurb over a grid of cards, which is why a
    listing source sets a selector.
    """
    soup: BeautifulSoup | None = None
    if selector:
        soup = BeautifulSoup(html, "lxml")
        cards = [_card_text(node) for node in soup.select(selector)]
        lines = [card for card in cards if card]
        if lines:
            return "\n".join(lines)
    extracted = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
    )
    if extracted and extracted.strip():
        return extracted.strip()
    if soup is None:
        soup = BeautifulSoup(html, "lxml")
    return soup.get_text("\n", strip=True)


def _card_text(node: Tag) -> str:
    """A card's text, led by the company from its logo's alt text.

    Listing cards often show the company only as a logo ("TEI logo").
    """
    text = " ".join(node.get_text(" ", strip=True).split())
    for image in node.find_all("img"):
        alt = " ".join(str(image.get("alt") or "").split())
        if alt.lower().endswith(" logo"):
            name = alt[: -len(" logo")].strip()
            if name and name not in text:
                return f"{name} · {text}" if text else name
            break
    return text


def added_items(
    previous: str | None,
    current: str,
    *,
    url: str,
    title: str,
    split_first: bool = False,
) -> list[Item]:
    """Turn a page into items.

    The first snapshot is one baseline item, unless split_first is set: a card
    listing (a source with a selector) then yields one item per card, so the
    cards already open on day one are scored one by one. Later calls return
    only paragraphs whose normalized text is new or changed.
    """
    if previous is None and not split_first:
        return [
            Item(
                title=title,
                url=url,
                summary=current.strip(),
                content_hash=hash_text(current),
            )
        ]
    old = [folded for _original, folded in _blocks(previous or "")]
    new_blocks = _blocks(current)
    new = [folded for _original, folded in new_blocks]
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    items: list[Item] = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag not in {"insert", "replace"}:
            continue
        items.extend(
            Item(
                title=title,
                url=url,
                summary=original,
                content_hash=hash_text(original),
            )
            for original, _folded in new_blocks[j1:j2]
        )
    return items


def _blocks(text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for part in re.split(r"\n+", text.strip()):
        original = part.strip()
        if not original:
            continue
        folded = normalize(original)
        if not folded:
            continue
        blocks.append((original, folded))
    return blocks
