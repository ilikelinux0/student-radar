"""Subreddit search over public RSS. No Reddit credentials."""

import re
from urllib.parse import urlencode

from radar.fetchers.feed import parse_feed
from radar.fetchers.http import FetchError, HttpClient
from radar.models import RawItem, Settings, Source
from radar.queries import effective_queries
from radar.urls import canonicalize_url

_BLOCKED = frozenset({401, 403, 429})


def fetch_reddit(
    source: Source,
    client: HttpClient,
    settings: Settings,
    *,
    limit: int,
) -> tuple[list[RawItem], str | None]:
    """Search each subreddit. A block or rate limit stops the rest of this source."""
    queries = effective_queries(source, settings)
    if not queries:
        raise FetchError(f"{source.id} has no queries")
    items: list[RawItem] = []
    for subreddit in source.subreddits:
        for query in queries:
            if len(items) >= limit:
                return items, None
            url = _search_url(subreddit, query)
            try:
                response = client.get(url)
            except FetchError as exc:
                if _blocked(exc):
                    return items, _notice(exc)
                raise
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
    return items, None


def _search_url(subreddit: str, query: str) -> str:
    params = urlencode({"q": query, "restrict_sr": "1", "sort": "new"})
    return f"https://www.reddit.com/r/{subreddit}/search.rss?{params}"


def _blocked(exc: BaseException) -> bool:
    current: BaseException | None = exc
    while current is not None:
        if _status(str(current)) in _BLOCKED:
            return True
        current = current.__cause__
    return False


def _notice(exc: FetchError) -> str:
    status = 403
    current: BaseException | None = exc
    while current is not None:
        found = _status(str(current))
        if found in _BLOCKED:
            status = found
            break
        current = current.__cause__
    if status == 429:
        return f"unauthenticated access rate limited ({status})"
    return f"unauthenticated access blocked ({status})"


def _status(text: str) -> int | None:
    match = re.search(r"returned (\d{3})", text)
    if match is None:
        return None
    return int(match.group(1))
