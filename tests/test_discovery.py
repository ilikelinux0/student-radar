"""Discovery fetchers, URL canonicalization, and item caps. No network."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from radar.config import ConfigError, load_config
from radar.db import (
    connect,
    get_snapshot,
    get_source_state,
    query_findings,
    save_snapshot,
    upsert_source_state,
)
from radar.fetchers import fetch_source
from radar.fetchers.http import FetchError, HttpClient
from radar.models import (
    Config,
    DiscoveryQueries,
    Settings,
    Snapshot,
    Source,
    SourceState,
)
from radar.queries import effective_queries
from radar.sweep import run_sweep
from radar.transcripts import Cue
from radar.urls import canonicalize_url
from tests.profile_sample import sample_profile

PROFILE = sample_profile()

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
SINCE = datetime(2026, 10, 10, tzinfo=UTC)
CHANNEL_ID = "UCabcdefghijklmnopqrstuv"

SEEDED_EN = [
    "startup credits",
    "free credits for startups",
    "startup program credits",
    "API credits startups",
    "cloud credits startups",
    "AI startup program",
    "founders program credits",
]
SEEDED_TR = [
    "girişim kredisi",
    "startup kredisi",
    "girişimci destek programı",
    "hibe programı girişim",
    "bulut kredisi",
    "kuluçka programı başvuru",
]


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "max_llm_items_per_run": 40,
        "keywords": ["credits", "grant"],
        "user_agent": "StudentRadar/0.1 (personal research bot)",
        "request_delay_seconds": 0,
        "scorer_provider": "none",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _source(**overrides: object) -> Source:
    values: dict[str, object] = {
        "id": "example",
        "name": "Example",
        "frequency": "daily",
        "enabled": True,
    }
    values.update(overrides)
    return Source(**values)  # type: ignore[arg-type]


def _client(handler) -> HttpClient:
    return HttpClient(
        _settings(),
        transport=httpx.MockTransport(handler),
        sleeper=lambda _seconds: None,
    )


def _config(*sources: Source, **settings: object) -> Config:
    return Config(sources=list(sources), settings=_settings(**settings))


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "findings.db")
    yield connection
    connection.close()


def test_canonicalize_url_strips_trackers_host_case_and_fragment() -> None:
    """Breaks if utm, ref, or fbclid survive, or the host case and fragment stay."""
    canonical = canonicalize_url(
        "HTTPS://News.Example/path?utm_source=x&id=9&fbclid=a&ref=r#section"
    )

    assert canonical == "https://news.example/path?id=9"


def test_canonicalize_url_leaves_a_malformed_port_alone() -> None:
    """Breaks if one feed entry with a bad port fails the whole source."""
    assert canonicalize_url(" https://Example.com:abc/x ") == (
        "https://Example.com:abc/x"
    )
    assert canonicalize_url("http://[::1]:8080/a?utm_source=x") == (
        "http://[::1]:8080/a"
    )


def test_canonicalize_url_keeps_a_non_tracking_query() -> None:
    """Breaks if a real query parameter is dropped with the trackers."""
    assert (
        canonicalize_url("https://example.com/a?utm_medium=email&ref=nav&page=2")
        == "https://example.com/a?page=2"
    )


def test_checked_in_discovery_queries_cover_every_group_in_use() -> None:
    """Breaks if a source points at a query group that settings.yaml leaves empty."""
    config = load_config(ROOT / "sources.yaml", ROOT / "settings.yaml")

    for source in config.sources:
        if source.query_group is not None:
            assert getattr(config.settings.discovery_queries, source.query_group)
    assert config.settings.max_items_per_source == 20
    assert config.settings.max_new_items_per_run == 50


def test_discovery_queries_default_to_empty() -> None:
    """Breaks if built-in search phrases come back when settings omit them."""
    settings = Settings(
        max_llm_items_per_run=1,
        keywords=["intern"],
        user_agent="StudentRadar/0.1 (personal research bot)",
        request_delay_seconds=0,
    )

    assert settings.discovery_queries.en == []
    assert settings.discovery_queries.tr == []


def test_query_group_expands_the_shared_list_then_extra_queries() -> None:
    """Breaks if a source repeats the shared list or drops its own extra query."""
    settings = _settings(discovery_queries=DiscoveryQueries(en=SEEDED_EN, tr=SEEDED_TR))
    source = _source(
        type="news_search",
        lang="en-US",
        query_group="en",
        queries=["cloud credits startups", "office hours credits"],
    )

    assert effective_queries(source, settings)[:2] == [
        "startup credits",
        "free credits for startups",
    ]
    assert effective_queries(source, settings)[-1] == "office hours credits"
    assert effective_queries(source, settings).count("cloud credits startups") == 1


def test_news_search_without_queries_is_rejected(tmp_path: Path) -> None:
    """Breaks if a search source with no queries and no group is fetched later."""
    sources = tmp_path / "sources.yaml"
    settings = tmp_path / "settings.yaml"
    sources.write_text(
        """\
- id: news
  name: News
  type: news_search
  lang: en-US
  frequency: daily
  enabled: false
""",
        encoding="utf-8",
    )
    settings.write_text(
        """\
max_llm_items_per_run: 40
keywords: [credits]
user_agent: "StudentRadar/0.1"
request_delay_seconds: 0
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="queries"):
        load_config(sources, settings)


def test_news_locale_must_be_en_us_or_tr_tr(tmp_path: Path) -> None:
    """Breaks if a third Google News locale is accepted."""
    sources = tmp_path / "sources.yaml"
    settings = tmp_path / "settings.yaml"
    sources.write_text(
        """\
- id: news
  name: News
  type: news_search
  lang: fr-FR
  query_group: en
  frequency: daily
  enabled: false
""",
        encoding="utf-8",
    )
    settings.write_text(
        """\
max_llm_items_per_run: 40
keywords: [credits]
user_agent: "StudentRadar/0.1"
request_delay_seconds: 0
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="lang"):
        load_config(sources, settings)


def test_news_search_resolves_the_publisher_and_falls_back() -> None:
    """Breaks if a News redirect is stored, or a failed resolve drops the item."""
    feed = (FIXTURES / "google_news.xml").read_bytes()
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/rss/search":
            return httpx.Response(200, content=feed)
        if request.url.path == "/rss/articles/AAA":
            return httpx.Response(
                302,
                headers={
                    "location": (
                        "https://publisher.example/credits"
                        "?utm_source=google&fbclid=abc#section"
                    )
                },
            )
        if request.url.host == "publisher.example":
            return httpx.Response(200, text="ok")
        if request.url.path == "/rss/articles/BBB":
            return httpx.Response(404)
        raise AssertionError(f"unexpected {request.url}")

    result = fetch_source(
        _source(
            type="news_search",
            lang="en-US",
            queries=["startup credits"],
        ),
        _client(handler),
        settings=_settings(),
    )

    assert [(item.title, item.url) for item in result.items or ()] == [
        ("Cloud startup credits expanded", "https://publisher.example/credits"),
        ("Grant round opens", "https://news.google.com/rss/articles/BBB"),
    ]
    assert "/rss/articles/AAA" in requested
    assert "/rss/articles/BBB" in requested


def test_news_search_requests_each_query_for_en_us_and_tr_tr() -> None:
    """Breaks if a query is skipped or the locale parameters are not en-US and tr-TR."""
    empty = (FIXTURES / "google_news_empty.xml").read_bytes()
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/rss/search":
            seen.append(dict(request.url.params))
            return httpx.Response(200, content=empty)
        raise AssertionError(f"unexpected {request.url}")

    fetch_source(
        _source(
            id="en",
            type="news_search",
            lang="en-US",
            queries=["startup credits", "cloud credits startups"],
        ),
        _client(handler),
        settings=_settings(),
    )
    fetch_source(
        _source(
            id="tr",
            type="news_search",
            lang="tr-TR",
            region="TR",
            queries=["girişim kredisi"],
        ),
        _client(handler),
        settings=_settings(),
    )

    assert seen == [
        {
            "q": "startup credits",
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        },
        {
            "q": "cloud credits startups",
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        },
        {
            "q": "girişim kredisi",
            "hl": "tr-TR",
            "gl": "TR",
            "ceid": "TR:tr",
        },
    ]


def test_news_search_does_not_resolve_past_the_source_cap() -> None:
    """Breaks if a search or a Google News link is fetched after the cap is full."""
    feed = (FIXTURES / "google_news.xml").read_bytes()
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/rss/search":
            return httpx.Response(200, content=feed)
        if request.url.path == "/rss/articles/AAA":
            return httpx.Response(
                302,
                headers={"location": "https://publisher.example/credits"},
            )
        if request.url.host == "publisher.example":
            return httpx.Response(200, text="ok")
        raise AssertionError(f"unexpected {request.url}")

    result = fetch_source(
        _source(
            type="news_search",
            lang="en-US",
            queries=["startup credits", "cloud credits startups"],
            max_items_per_run=1,
        ),
        _client(handler),
        settings=_settings(),
    )

    assert [item.title for item in result.items or ()] == [
        "Cloud startup credits expanded"
    ]
    assert requested.count("/rss/search") == 1
    assert "/rss/articles/BBB" not in requested


def test_hn_search_keeps_stories_newer_than_last_check_newest_first() -> None:
    """Breaks if an old hit, a comment, or a reversed story list is returned."""
    payload = (FIXTURES / "hn_search.json").read_bytes()
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(dict(request.url.params))
        return httpx.Response(200, content=payload)

    result = fetch_source(
        _source(
            type="hn_search",
            queries=["startup credits"],
            stories=True,
            comments=False,
        ),
        _client(handler),
        settings=_settings(),
        since=SINCE,
    )

    assert [(item.title, item.url, item.summary) for item in result.items or ()] == [
        (
            "Show HN: startup credits tracker",
            "https://publisher.example/credits",
            "",
        ),
        (
            "Ask HN: grant help",
            "https://news.ycombinator.com/item?id=4",
            "Where are the startup credits?",
        ),
    ]
    assert seen == [
        {
            "query": "startup credits",
            "tags": "story",
            "hitsPerPage": "20",
            "numericFilters": "created_at_i>1791590400",
        }
    ]


def test_hn_search_includes_comments_when_asked() -> None:
    """Breaks if comments stay hidden, or stories disappear, when the flag is on."""
    payload = (FIXTURES / "hn_search.json").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        assert request.url.params["tags"] == "(story,comment)"
        return httpx.Response(200, content=payload)

    result = fetch_source(
        _source(
            type="hn_search",
            queries=["startup credits"],
            stories=True,
            comments=True,
        ),
        _client(handler),
        settings=_settings(),
        since=SINCE,
    )

    assert [(item.title, item.url) for item in result.items or ()][:1] == [
        ("Ask HN: cloud credits", "https://news.ycombinator.com/item?id=9")
    ]
    assert "Show HN: startup credits tracker" in [
        item.title for item in result.items or ()
    ]
    comment = (result.items or ())[0]
    assert comment.summary == "We got startup credits from the program."


def test_hn_search_can_skip_stories() -> None:
    """Breaks if stories are still returned when the stories flag is off."""
    payload = (FIXTURES / "hn_search.json").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        assert request.url.params["tags"] == "comment"
        return httpx.Response(200, content=payload)

    result = fetch_source(
        _source(
            type="hn_search",
            queries=["startup credits"],
            stories=False,
            comments=True,
        ),
        _client(handler),
        settings=_settings(),
        since=SINCE,
    )

    assert [(item.title, item.summary) for item in result.items or ()] == [
        ("Ask HN: cloud credits", "We got startup credits from the program.")
    ]


def test_hn_search_lists_a_story_once_across_queries() -> None:
    """Breaks if overlapping queries fill the source cap with the same story."""
    payload = (FIXTURES / "hn_search.json").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=payload)

    result = fetch_source(
        _source(
            type="hn_search",
            queries=["startup credits", "cloud credits startups"],
            stories=True,
            comments=False,
        ),
        _client(handler),
        settings=_settings(),
        since=SINCE,
    )

    urls = [item.url for item in result.items or ()]
    assert urls == [
        "https://publisher.example/credits",
        "https://news.ycombinator.com/item?id=4",
    ]


@pytest.mark.parametrize(
    ("status", "filtered"),
    [("ok", True), ("error", False)],
)
def test_hn_search_only_resumes_from_a_successful_check(
    conn: sqlite3.Connection, status: str, filtered: bool
) -> None:
    """Breaks if a failed run moves the HN cursor and its stories are skipped."""
    payload = (FIXTURES / "hn_search.json").read_bytes()
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(dict(request.url.params))
        return httpx.Response(200, content=payload)

    upsert_source_state(
        conn,
        SourceState("hn", NOW - timedelta(days=2), status, None, 0),  # type: ignore[arg-type]
    )

    run_sweep(
        _config(_source(id="hn", name="HN", type="hn_search", queries=["credits"])),
        conn,
        profile=PROFILE,
        no_llm=True,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _seconds: None,
        now=NOW,
    )

    assert len(seen) == 1
    assert ("numericFilters" in seen[0]) is filtered


def test_reddit_search_reads_each_subreddit_query() -> None:
    """Breaks if a subreddit search is not public RSS or keeps tracking parameters."""
    feed = (FIXTURES / "reddit_search.xml").read_bytes()
    seen: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        assert "authorization" not in {name.lower() for name in request.headers}
        seen.append((request.url.path, dict(request.url.params)))
        return httpx.Response(200, content=feed)

    result = fetch_source(
        _source(
            type="reddit_search",
            subreddits=["startups", "sideproject"],
            queries=["startup credits"],
        ),
        _client(handler),
        settings=_settings(),
    )

    assert seen == [
        (
            "/r/startups/search.rss",
            {"q": "startup credits", "restrict_sr": "1", "sort": "new"},
        ),
        (
            "/r/sideproject/search.rss",
            {"q": "startup credits", "restrict_sr": "1", "sort": "new"},
        ),
    ]
    assert [(item.title, item.url) for item in result.items or ()] == [
        (
            "Reddit startup credits thread",
            "https://www.reddit.com/r/startups/comments/abc/startup_credits",
        ),
        (
            "Reddit startup credits thread",
            "https://www.reddit.com/r/startups/comments/abc/startup_credits",
        ),
    ]


def test_reddit_block_skips_remaining_requests_without_auth() -> None:
    """Breaks if a 403 raises, continues to the next subreddit, or sends credentials."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        assert "authorization" not in {name.lower() for name in request.headers}
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/r/startups/search.rss":
            return httpx.Response(403)
        raise AssertionError(f"fetched after a block: {request.url}")

    result = fetch_source(
        _source(
            type="reddit_search",
            subreddits=["startups", "sideproject"],
            queries=["startup credits"],
        ),
        _client(handler),
        settings=_settings(),
    )

    assert result.items == ()
    assert result.notice is not None
    assert "blocked" in result.notice
    assert "403" in result.notice
    assert "/r/sideproject/search.rss" not in requested


def test_reddit_rate_limit_is_a_skip() -> None:
    """Breaks if a 429 fails the fetcher instead of recording a rate limit."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(429)

    result = fetch_source(
        _source(
            type="reddit_search",
            subreddits=["startups"],
            queries=["startup credits"],
        ),
        _client(handler),
        settings=_settings(),
    )

    assert result.items == ()
    assert result.notice is not None
    assert "rate limited" in result.notice
    assert "429" in result.notice


def test_reddit_server_error_still_raises() -> None:
    """Breaks if a 500 is treated as an unauthenticated block."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(500)

    with pytest.raises(FetchError):
        fetch_source(
            _source(
                type="reddit_search",
                subreddits=["startups"],
                queries=["startup credits"],
            ),
            _client(handler),
            settings=_settings(),
        )


def test_github_watch_baseline_is_the_readme() -> None:
    """Breaks if the first fetch emits a diff or ignores the latest commit."""
    result = fetch_source(
        _source(type="github_watch", repo="acme/credits-list"),
        _client(_github_handler("readme_v1.md")),
        settings=_settings(),
    )

    assert result.snapshot_text == (FIXTURES / "readme_v1.md").read_text(
        encoding="utf-8"
    )
    assert [(item.title, item.url) for item in result.items or ()] == [
        (
            "Add startup credits section",
            "https://github.com/acme/credits-list/commit/abc123",
        )
    ]
    summary = (result.items or ())[0].summary
    assert "Startup credits live here." in summary
    assert not summary.startswith("---")


def test_github_watch_survives_a_readme_that_is_not_utf8() -> None:
    """Breaks if one stray Latin-1 byte fails the whole README watch."""
    handler = _github_handler("readme_v1.md")

    def latin1(request: httpx.Request) -> httpx.Response:
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(200, content=b"Caf\xe9 startup credits\n")
        return handler(request)

    result = fetch_source(
        _source(type="github_watch", repo="acme/credits-list"),
        _client(latin1),
        settings=_settings(),
    )

    assert result.snapshot_text == "Caf\ufffd startup credits\n"


def test_github_watch_item_is_the_readme_diff() -> None:
    """Breaks if a README edit is stored as the whole file instead of a diff."""
    previous = (FIXTURES / "readme_v1.md").read_text(encoding="utf-8")
    result = fetch_source(
        _source(type="github_watch", repo="acme/credits-list"),
        _client(_github_handler("readme_v2.md")),
        settings=_settings(),
        snapshot_text=previous,
    )

    item = (result.items or ())[0]
    assert item.title == "Add startup credits section"
    assert item.url == "https://github.com/acme/credits-list/commit/abc123"
    assert "+API credits for new accounts." in item.summary
    assert result.snapshot_text == (FIXTURES / "readme_v2.md").read_text(
        encoding="utf-8"
    )


def test_github_watch_ignores_an_unchanged_readme() -> None:
    """Breaks if a commit feed emits an item when the README text did not change."""
    previous = (FIXTURES / "readme_v1.md").read_text(encoding="utf-8")
    result = fetch_source(
        _source(type="github_watch", repo="acme/credits-list"),
        _client(_github_handler("readme_v1.md")),
        settings=_settings(),
        snapshot_text=previous,
    )

    assert result.items == ()
    assert result.snapshot_text == previous


def test_youtube_channel_stores_title_and_description_only() -> None:
    """Breaks if the description keeps HTML or this fetcher requests a caption URL."""
    feed = (FIXTURES / "youtube_channel.xml").read_bytes()
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        assert request.url.params["channel_id"] == CHANNEL_ID
        return httpx.Response(200, content=feed)

    result = fetch_source(
        _source(type="youtube_channel", channel_id=CHANNEL_ID),
        _client(handler),
        settings=_settings(),
    )

    assert [(item.title, item.url, item.summary) for item in result.items or ()] == [
        (
            "Cloud credits office hours",
            "https://www.youtube.com/watch?v=abcdefghijk",
            "How to apply for startup credits.",
        )
    ]
    assert requested == ["/feeds/videos.xml"]


def test_canonical_url_dedupes_across_sources(
    conn: sqlite3.Connection,
) -> None:
    """Breaks if the same article with different trackers is stored twice."""
    first = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><item>
<title>Startup credits one</title>
<link>https://Example.com/credits?utm_source=a&amp;fbclid=1</link>
<description>A startup credits note.</description>
</item></channel></rss>
"""
    second = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><item>
<title>Startup credits two</title>
<link>https://example.com/credits?ref=b#x</link>
<description>Another writeup of the credits offer.</description>
</item></channel></rss>
"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/a":
            return httpx.Response(200, text=first)
        if request.url.path == "/b":
            return httpx.Response(200, text=second)
        raise AssertionError(f"unexpected {request.url}")

    summary = run_sweep(
        _config(
            _source(id="a", name="A", type="rss", url="https://example.com/a"),
            _source(id="b", name="B", type="rss", url="https://example.com/b"),
        ),
        conn,
        profile=PROFILE,
        no_llm=True,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _seconds: None,
        now=NOW,
    )

    rows = query_findings(conn)
    assert summary.new == 1
    assert [(row.title, row.url) for row in rows] == [
        ("Startup credits one", "https://example.com/credits")
    ]


def test_discovery_cap_does_not_drop_a_later_curated_source(
    conn: sqlite3.Connection,
) -> None:
    """Breaks if a search that fills the global cap skips a curated feed."""
    news = (FIXTURES / "google_news.xml").read_bytes()
    feed = (FIXTURES / "feed.xml").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "news.google.com" and request.url.path == "/rss/search":
            return httpx.Response(200, content=news)
        if request.url.path == "/rss/articles/AAA":
            return httpx.Response(
                302,
                headers={"location": "https://publisher.example/credits"},
            )
        if request.url.host == "publisher.example":
            return httpx.Response(200, text="ok")
        if request.url.path == "/rss/articles/BBB":
            return httpx.Response(404)
        if request.url.path == "/feed.xml":
            return httpx.Response(200, content=feed)
        raise AssertionError(f"unexpected {request.url}")

    summary = run_sweep(
        _config(
            _source(
                id="news",
                name="News",
                type="news_search",
                lang="en-US",
                queries=["startup credits"],
            ),
            _source(
                id="feed",
                name="Feed",
                type="rss",
                url="https://example.com/feed.xml",
            ),
            _source(
                id="hn",
                name="HN",
                type="hn_search",
                queries=["startup credits"],
            ),
            max_new_items_per_run=1,
        ),
        conn,
        profile=PROFILE,
        no_llm=True,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _seconds: None,
        now=NOW,
    )

    assert summary.failed == 0
    assert summary.checked == 2
    assert get_source_state(conn, "hn") is None
    titles = sorted(row.title for row in query_findings(conn))
    assert titles == [
        "Cloud startup credits expanded",
        "Grant deadline posted",
        "Startup credits expanded",
    ]


def test_per_source_cap_limits_a_feed(conn: sqlite3.Connection) -> None:
    """Breaks if one feed can store more items than max_items_per_source."""
    feed = (FIXTURES / "feed.xml").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=feed)

    summary = run_sweep(
        _config(
            _source(id="feed", name="Feed", type="rss", url="https://example.com/feed"),
            max_items_per_source=1,
        ),
        conn,
        profile=PROFILE,
        no_llm=True,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _seconds: None,
        now=NOW,
    )

    assert summary.new == 1
    assert [row.title for row in query_findings(conn)] == ["Startup credits expanded"]


def test_scrape_paragraphs_are_not_collapsed_to_one_url(
    conn: sqlite3.Connection,
) -> None:
    """Breaks if URL dedupe keeps only the first new paragraph of a scraped page."""
    first = """\
<!DOCTYPE html><html><body><article>
<p>Intro paragraph.</p>
</article></body></html>
"""
    second = """\
<!DOCTYPE html><html><body><article>
<p>Intro paragraph.</p>
<p>New startup credits for founders.</p>
<p>A grant covers the rest.</p>
</article></body></html>
"""
    pages = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        pages["n"] += 1
        body = first if pages["n"] == 1 else second
        return httpx.Response(200, text=body)

    source = _source(
        id="page",
        name="Page",
        type="scrape",
        url="https://example.com/page",
    )
    transport = httpx.MockTransport(handler)
    run_sweep(
        _config(source),
        conn,
        profile=PROFILE,
        no_llm=True,
        transport=transport,
        sleeper=lambda _seconds: None,
        now=NOW,
    )
    run_sweep(
        _config(source),
        conn,
        profile=PROFILE,
        force=True,
        no_llm=True,
        transport=transport,
        sleeper=lambda _seconds: None,
        now=NOW + timedelta(days=1),
    )

    rows = query_findings(conn)
    assert len(rows) == 2
    assert {row.url for row in rows} == {"https://example.com/page"}
    assert {row.excerpt for row in rows} == {
        "New startup credits for founders.",
        "A grant covers the rest.",
    }


def test_dry_run_prints_each_discovery_type_and_writes_nothing(
    conn: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    """Breaks if a discovery dry run writes, or one of the five types is silent."""

    def credits_window(video_id: str, languages: tuple[str, ...]) -> list[Cue]:
        assert video_id == "abcdefghijk"
        assert languages == ("en", "tr")
        return [Cue(text="startup credits this week", start=90)]

    summary = run_sweep(
        _config(
            _source(
                id="news",
                name="News",
                type="news_search",
                lang="en-US",
                queries=["startup credits"],
            ),
            _source(
                id="hn",
                name="HN",
                type="hn_search",
                queries=["startup credits"],
            ),
            _source(
                id="reddit",
                name="Reddit",
                type="reddit_search",
                subreddits=["startups"],
                queries=["startup credits"],
            ),
            _source(
                id="github",
                name="GitHub",
                type="github_watch",
                repo="acme/credits-list",
            ),
            _source(
                id="youtube",
                name="YouTube",
                type="youtube_channel",
                channel_id=CHANNEL_ID,
            ),
        ),
        conn,
        profile=PROFILE,
        dry_run=True,
        no_llm=True,
        transport=httpx.MockTransport(_dry_run_handler),
        sleeper=lambda _seconds: None,
        now=NOW,
        transcript_fetcher=credits_window,
    )

    assert query_findings(conn) == []
    assert get_source_state(conn, "news") is None
    assert get_snapshot(conn, "github") is None
    assert summary.failed == 0
    assert summary.new == 8
    printed = capsys.readouterr().out
    for title in (
        "Cloud startup credits expanded",
        "Show HN: startup credits tracker",
        "Reddit startup credits thread",
        "Add startup credits section",
        "Cloud credits office hours",
    ):
        assert title in printed
    assert "would save snapshot for github" in printed


def test_reddit_block_is_recorded_and_keeps_earlier_items(
    conn: sqlite3.Connection,
) -> None:
    """Breaks if a later 403 drops items already fetched or skips source health."""
    feed = (FIXTURES / "reddit_search.xml").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/r/startups/search.rss":
            return httpx.Response(200, content=feed)
        if request.url.path == "/r/sideproject/search.rss":
            return httpx.Response(403)
        raise AssertionError(f"unexpected {request.url}")

    summary = run_sweep(
        _config(
            _source(
                id="reddit",
                name="Reddit",
                type="reddit_search",
                subreddits=["startups", "sideproject"],
                queries=["startup credits"],
            )
        ),
        conn,
        profile=PROFILE,
        no_llm=True,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _seconds: None,
        now=NOW,
    )

    assert summary.failed == 1
    assert summary.new == 1
    assert [row.title for row in query_findings(conn)] == [
        "Reddit startup credits thread"
    ]
    state = get_source_state(conn, "reddit")
    assert state is not None
    assert state.last_status == "error"
    assert state.last_error is not None
    assert "blocked" in state.last_error
    assert "403" in state.last_error
    assert state.consecutive_failures == 1


def test_github_diff_is_stored_and_an_unchanged_readme_is_not(
    conn: sqlite3.Connection,
) -> None:
    """Breaks if the second README fetch repeats the baseline or skips the diff."""
    save_snapshot(
        conn,
        Snapshot(
            source_id="github",
            content_hash="old",
            text=(FIXTURES / "readme_v1.md").read_text(encoding="utf-8"),
            fetched_at=NOW,
        ),
    )
    source = _source(
        id="github", name="GitHub", type="github_watch", repo="acme/credits-list"
    )
    summary = run_sweep(
        _config(source),
        conn,
        profile=PROFILE,
        no_llm=True,
        transport=httpx.MockTransport(
            lambda request: _github_handler("readme_v2.md")(request)
        ),
        sleeper=lambda _seconds: None,
        now=NOW,
    )

    rows = query_findings(conn)
    assert summary.new == 1
    assert rows[0].title == "Add startup credits section"
    assert "+API credits for new accounts." in rows[0].excerpt
    snapshot = get_snapshot(conn, "github")
    assert snapshot is not None
    assert "API credits for new accounts." in snapshot.text

    again = run_sweep(
        _config(source),
        conn,
        profile=PROFILE,
        force=True,
        no_llm=True,
        transport=httpx.MockTransport(
            lambda request: _github_handler("readme_v2.md")(request)
        ),
        sleeper=lambda _seconds: None,
        now=NOW,
    )
    assert again.new == 0
    assert len(query_findings(conn)) == 1


def _github_handler(readme_name: str):
    atom = (FIXTURES / "github_commits.atom").read_bytes()
    readme = (FIXTURES / readme_name).read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "github.com":
            assert request.url.path == "/acme/credits-list/commits/HEAD/README.md.atom"
            return httpx.Response(200, content=atom)
        if request.url.host == "raw.githubusercontent.com":
            assert request.url.path == "/acme/credits-list/HEAD/README.md"
            return httpx.Response(200, content=readme)
        raise AssertionError(f"unexpected {request.url}")

    return handler


def _dry_run_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/robots.txt":
        return httpx.Response(404)
    host = request.url.host
    if host == "news.google.com" and request.url.path == "/rss/search":
        return httpx.Response(200, content=(FIXTURES / "google_news.xml").read_bytes())
    if host == "news.google.com" and request.url.path == "/rss/articles/AAA":
        return httpx.Response(
            302,
            headers={"location": "https://publisher.example/credits"},
        )
    if host == "publisher.example":
        return httpx.Response(200, text="ok")
    if host == "news.google.com" and request.url.path == "/rss/articles/BBB":
        return httpx.Response(404)
    if host == "hn.algolia.com":
        return httpx.Response(200, content=(FIXTURES / "hn_search.json").read_bytes())
    if host == "www.reddit.com":
        return httpx.Response(
            200, content=(FIXTURES / "reddit_search.xml").read_bytes()
        )
    if host == "github.com":
        return httpx.Response(
            200, content=(FIXTURES / "github_commits.atom").read_bytes()
        )
    if host == "raw.githubusercontent.com":
        return httpx.Response(200, content=(FIXTURES / "readme_v1.md").read_bytes())
    if host == "www.youtube.com":
        return httpx.Response(
            200, content=(FIXTURES / "youtube_channel.xml").read_bytes()
        )
    raise AssertionError(f"unexpected {request.url}")
