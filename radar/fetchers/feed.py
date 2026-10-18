"""Feed parsing shared by search fetchers. Existing RSS behavior stays in rss.py."""

import html
import re
from collections.abc import Mapping

import feedparser

from radar.models import RawItem

# Block tags become a space so "<p>a</p><p>b</p>" does not read as "ab".
_BLOCK_TAG = re.compile(
    r"</?(?:br|p|div|li|ul|ol|h[1-6]|tr|td|blockquote|section|article)\b[^>]*>",
    re.IGNORECASE,
)


def parse_feed(content: bytes) -> list[RawItem]:
    """Return one raw item per entry. An empty feed is an empty list."""
    parsed = feedparser.parse(content)
    items: list[RawItem] = []
    for entry in parsed.entries:
        url = _link(entry)
        if not url:
            continue
        items.append(
            RawItem(
                title=_text(entry.get("title")),
                url=url,
                summary=_summary(entry),
            )
        )
    return items


def plain_text(value: str) -> str:
    """Drop tags from a feed summary. The text is data, not markup to render."""
    text = html.unescape(value)
    text = _BLOCK_TAG.sub(" ", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _link(entry: Mapping[str, object]) -> str:
    link = entry.get("link")
    if isinstance(link, str) and link.strip():
        return link.strip()
    links = entry.get("links")
    if not isinstance(links, list):
        return ""
    for item in links:
        if not isinstance(item, Mapping):
            continue
        if item.get("rel", "alternate") not in {None, "alternate"}:
            continue
        href = item.get("href")
        if isinstance(href, str) and href.strip():
            return href.strip()
    return ""


def _summary(entry: Mapping[str, object]) -> str:
    group = entry.get("media_group")
    if isinstance(group, list):
        for item in group:
            if not isinstance(item, Mapping):
                continue
            text = item.get("media_description")
            if not text:
                text = item.get("description")
            if text:
                return plain_text(str(text))
    for key in ("media_description", "summary", "description"):
        value = entry.get(key)
        if value:
            return plain_text(str(value))
    return ""


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()
