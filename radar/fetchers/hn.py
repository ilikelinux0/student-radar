"""Hacker News search via the public Algolia API. Newest first."""

from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import urlencode

from radar.fetchers.http import FetchError, HttpClient
from radar.models import RawItem, Settings, Source
from radar.queries import effective_queries
from radar.urls import canonicalize_url


def fetch_hn(
    source: Source,
    client: HttpClient,
    settings: Settings,
    *,
    since: datetime | None,
    limit: int,
) -> list[RawItem]:
    """Return stories and, when asked, comments newer than since."""
    queries = effective_queries(source, settings)
    if not queries:
        raise FetchError(f"{source.id} has no queries")
    if not source.stories and not source.comments:
        raise FetchError(f"{source.id} has neither stories nor comments")
    collected: list[tuple[datetime, RawItem]] = []
    for query in queries:
        response = client.get(_search_url(source, query, since, limit))
        try:
            payload = response.json()
        except ValueError as exc:
            raise FetchError(f"{source.id} did not return JSON") from exc
        hits = payload.get("hits") if isinstance(payload, dict) else None
        if not isinstance(hits, list):
            raise FetchError(f"{source.id} did not return hits")
        for hit in hits:
            if not isinstance(hit, Mapping):
                continue
            created = _created_at(hit.get("created_at"))
            if since is not None and (created is None or created <= since):
                continue
            kind = _kind(hit)
            if kind == "comment" and not source.comments:
                continue
            if kind == "story" and not source.stories:
                continue
            item = _item(hit, kind)
            if item is None:
                continue
            when = created or datetime.min.replace(tzinfo=UTC)
            collected.append((when, item))
    collected.sort(key=lambda pair: pair[0], reverse=True)
    # Overlapping queries return the same story. Drop repeats before the cap
    # so they do not push other stories out.
    unique: list[RawItem] = []
    seen: set[str] = set()
    for _when, item in collected:
        if item.url in seen:
            continue
        seen.add(item.url)
        unique.append(item)
    return unique[:limit]


def _search_url(
    source: Source,
    query: str,
    since: datetime | None,
    limit: int,
) -> str:
    if source.stories and source.comments:
        tags = "(story,comment)"
    elif source.comments:
        tags = "comment"
    else:
        tags = "story"
    params = {"query": query, "tags": tags, "hitsPerPage": str(limit)}
    if since is not None:
        params["numericFilters"] = f"created_at_i>{int(since.timestamp())}"
    return "https://hn.algolia.com/api/v1/search_by_date?" + urlencode(params)


def _kind(hit: Mapping[str, object]) -> str:
    tags = hit.get("_tags")
    if isinstance(tags, list) and "comment" in tags:
        return "comment"
    return "story"


def _item(hit: Mapping[str, object], kind: str) -> RawItem | None:
    object_id = _string(hit.get("objectID"))
    if kind == "comment":
        title = _string(hit.get("story_title")) or "Comment"
        url = _string(hit.get("story_url"))
        summary = _string(hit.get("comment_text"))
    else:
        title = _string(hit.get("title"))
        url = _string(hit.get("url"))
        summary = _string(hit.get("story_text"))
    if not url and object_id:
        url = f"https://news.ycombinator.com/item?id={object_id}"
    if not url:
        return None
    return RawItem(title=title, url=canonicalize_url(url), summary=summary)


def _created_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _string(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()
