"""YouTube channel uploads via the public channel RSS feed.

Titles and descriptions only. Transcripts are fetched later, per new video.
The uploads feed is disallowed by YouTube's robots.txt. This fetcher still
requests it, because phase 15 needs the video list. Other YouTube URLs are
not requested here.
"""

from urllib.parse import quote

from radar.fetchers.feed import parse_feed
from radar.fetchers.http import HttpClient
from radar.models import RawItem, Source
from radar.urls import canonicalize_url


def fetch_youtube(source: Source, client: HttpClient, *, limit: int) -> list[RawItem]:
    """Return upload titles and descriptions for one channel."""
    channel_id = quote(source.channel_id or "", safe="")
    response = client.get(
        f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    )
    items: list[RawItem] = []
    for raw in parse_feed(response.content):
        if len(items) >= limit:
            break
        items.append(
            RawItem(
                title=raw.title,
                url=canonicalize_url(raw.url),
                summary=raw.summary,
            )
        )
    return items
