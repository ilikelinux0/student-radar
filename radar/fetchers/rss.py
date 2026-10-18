"""RSS fetcher. Parses bytes this process already fetched."""

import feedparser

from radar.fetchers.feed import plain_text
from radar.fetchers.http import FetchError, HttpClient
from radar.models import RawItem, Source


def fetch_rss(source: Source, client: HttpClient) -> list[RawItem]:
    """Return one raw item per feed entry. Does not touch the database."""
    response = client.get(source.url)
    parsed = feedparser.parse(response.content)
    items: list[RawItem] = []
    for entry in parsed.entries:
        url = str(entry.get("link") or "")
        if not url:
            continue
        summary = plain_text(
            str(entry.get("summary") or entry.get("description") or "")
        )
        items.append(
            RawItem(title=str(entry.get("title") or ""), url=url, summary=summary)
        )
    if not items:
        raise FetchError(f"{source.url} did not contain any RSS items")
    return items
