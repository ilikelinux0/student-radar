"""Fetcher registry. Maps a source type to a fetcher.

Fetchers return raw items or page text. They do not touch the database.
"""

from dataclasses import dataclass
from datetime import datetime

from radar.fetchers.api import fetch_api
from radar.fetchers.github import fetch_github
from radar.fetchers.hn import fetch_hn
from radar.fetchers.http import FetchError, HttpClient
from radar.fetchers.news import fetch_news
from radar.fetchers.reddit import fetch_reddit
from radar.fetchers.rss import fetch_rss
from radar.fetchers.scrape import RenderPage, fetch_scrape
from radar.fetchers.youtube import fetch_youtube
from radar.models import RawItem, Settings, Source
from radar.queries import source_item_limit


@dataclass(frozen=True)
class FetchResult:
    """Raw feed items, raw HTML, or a README snapshot. Fetchers do not write."""

    items: tuple[RawItem, ...] | None = None
    html: str | None = None
    notice: str | None = None
    snapshot_text: str | None = None
    health_note: str | None = None


def fetch_source(
    source: Source,
    client: HttpClient,
    *,
    render: RenderPage | None = None,
    settings: Settings | None = None,
    since: datetime | None = None,
    snapshot_text: str | None = None,
    limit: int | None = None,
) -> FetchResult:
    """Fetch one source. rss and api return items. scrape returns HTML."""
    cap = limit
    if cap is None and settings is not None:
        cap = source_item_limit(source, settings)
    if cap is None:
        cap = 20
    if source.type == "rss":
        return FetchResult(items=tuple(fetch_rss(source, client)))
    if source.type == "api":
        return FetchResult(items=tuple(fetch_api(source, client)))
    if source.type == "scrape":
        return FetchResult(html=fetch_scrape(source, client, render=render))
    if settings is None:
        raise FetchError(f"{source.id} requires settings")
    if source.type == "news_search":
        return FetchResult(items=tuple(fetch_news(source, client, settings, limit=cap)))
    if source.type == "hn_search":
        return FetchResult(
            items=tuple(fetch_hn(source, client, settings, since=since, limit=cap))
        )
    if source.type == "reddit_search":
        items, notice = fetch_reddit(source, client, settings, limit=cap)
        return FetchResult(items=tuple(items), notice=notice)
    if source.type == "github_watch":
        items, text = fetch_github(source, client, snapshot_text=snapshot_text)
        return FetchResult(items=tuple(items), snapshot_text=text)
    if source.type == "youtube_channel":
        return FetchResult(items=tuple(fetch_youtube(source, client, limit=cap)))
    raise FetchError(f"unsupported source type {source.type}")
