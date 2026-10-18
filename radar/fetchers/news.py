"""Google News RSS search. One request per query. No network in tests."""

from urllib.parse import parse_qs, urlencode, urlsplit

from radar.fetchers.feed import parse_feed
from radar.fetchers.http import FetchError, HttpClient
from radar.models import RawItem, Settings, Source
from radar.queries import effective_queries
from radar.urls import canonicalize_url

_LOCALE = {
    "en-US": ("US", "US:en"),
    "tr-TR": ("TR", "TR:tr"),
}


def fetch_news(
    source: Source,
    client: HttpClient,
    settings: Settings,
    *,
    limit: int,
) -> list[RawItem]:
    """Search Google News once per query and resolve publisher links when possible."""
    queries = effective_queries(source, settings)
    if not queries:
        raise FetchError(f"{source.id} has no queries")
    if source.lang not in _LOCALE:
        raise FetchError(f"{source.id} has no supported lang")
    items: list[RawItem] = []
    for query in queries:
        if len(items) >= limit:
            break
        response = client.get(_search_url(query, source.lang))
        for raw in parse_feed(response.content):
            if len(items) >= limit:
                break
            items.append(
                RawItem(
                    title=raw.title,
                    url=canonicalize_url(_resolve(client, raw.url)),
                    summary=raw.summary,
                )
            )
    return items


def _search_url(query: str, lang: str) -> str:
    region, ceid = _LOCALE[lang]
    params = urlencode({"q": query, "hl": lang, "gl": region, "ceid": ceid})
    return f"https://news.google.com/rss/search?{params}"


def _resolve(client: HttpClient, url: str) -> str:
    if not _is_google_news(url):
        return url
    direct = _direct_target(url)
    if direct is not None:
        return direct
    try:
        response = client.get(url)
    except FetchError:
        return url
    final = str(response.url)
    if _is_google_news(final):
        return url
    return final


def _is_google_news(url: str) -> bool:
    host = urlsplit(url).hostname
    return host is not None and host.lower() == "news.google.com"


def _direct_target(url: str) -> str | None:
    params = parse_qs(urlsplit(url).query)
    for key in ("url", "u"):
        for value in params.get(key, []):
            if value.startswith(("http://", "https://")):
                return value
    return None
