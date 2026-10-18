"""Shared discovery query lists. Fetchers do not repeat the list per source."""

from radar.models import Settings, Source

SEARCH_TYPES = frozenset({"news_search", "hn_search", "reddit_search"})
DISCOVERY_TYPES = frozenset(
    {
        "news_search",
        "hn_search",
        "reddit_search",
        "github_watch",
        "youtube_channel",
    }
)


def effective_queries(source: Source, settings: Settings) -> list[str]:
    """Return the shared group first, then extra queries, without duplicates."""
    grouped: list[str] = []
    if source.query_group == "en":
        grouped = list(settings.discovery_queries.en)
    elif source.query_group == "tr":
        grouped = list(settings.discovery_queries.tr)
    seen: set[str] = set()
    ordered: list[str] = []
    for query in [*grouped, *source.queries]:
        text = query.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def source_item_limit(source: Source, settings: Settings) -> int:
    """How many new items this source may add. A source override wins."""
    if source.max_items_per_run is not None:
        return source.max_items_per_run
    return settings.max_items_per_source
