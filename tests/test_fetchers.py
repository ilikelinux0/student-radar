"""Fetchers parse fixtures and the HTTP client stays polite. No network."""

from pathlib import Path

import httpx
import pytest

from radar.fetchers import fetch_source
from radar.fetchers.http import MAX_RESPONSE_BYTES, FetchError, HttpClient
from radar.models import ApiFields, Settings, Source

FIXTURES = Path(__file__).parent / "fixtures"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "max_llm_items_per_run": 40,
        "keywords": ["credits"],
        "user_agent": "StudentRadar/0.1 (personal research bot)",
        "request_delay_seconds": 0,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _source(source_type: str, url: str, **overrides: object) -> Source:
    values: dict[str, object] = {
        "id": "example",
        "name": "Example",
        "url": url,
        "type": source_type,
        "frequency": "daily",
        "enabled": True,
    }
    values.update(overrides)
    return Source(**values)  # type: ignore[arg-type]


def _client(
    handler,
    settings: Settings | None = None,
    sleeper=None,
) -> HttpClient:
    return HttpClient(
        settings or _settings(),
        transport=httpx.MockTransport(handler),
        sleeper=sleeper or (lambda _seconds: None),
    )


def test_rss_fetcher_parses_feed_entries() -> None:
    """Breaks if RSS items lose their title, link, or description."""
    feed = (FIXTURES / "feed.xml").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        assert (
            request.headers["user-agent"] == "StudentRadar/0.1 (personal research bot)"
        )
        return httpx.Response(200, content=feed)

    result = fetch_source(
        _source("rss", "https://example.com/feed.xml"),
        _client(handler),
    )

    assert [(item.title, item.url, item.summary) for item in result.items or ()] == [
        (
            "Startup credits expanded",
            "https://example.com/replace-me/credits",
            "Providers added a free tier for new startups.",
        ),
        (
            "Grant deadline posted",
            "https://example.com/replace-me/grant",
            "Applications close on 2026-12-01.",
        ),
    ]


def test_rss_summary_is_plain_text() -> None:
    """Breaks if feed markup reaches the excerpt, the reader, or the model."""
    feed = (
        b"<?xml version='1.0'?><rss version='2.0'><channel><item>"
        b"<title>Credits</title><link>https://example.com/a</link>"
        b"<description><![CDATA[<p>Free <b>credits</b> &amp; perks.</p>"
        b"<p>Apply now.</p>]]></description>"
        b"</item></channel></rss>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=feed)

    result = fetch_source(
        _source("rss", "https://example.com/feed.xml"),
        _client(handler),
    )

    assert [item.summary for item in result.items or ()] == [
        "Free credits & perks. Apply now."
    ]


def test_api_fetcher_uses_items_path_and_field_map() -> None:
    """Breaks if a JSON source ignores items_path or the configured field names."""
    payload = (FIXTURES / "api.json").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=payload)

    source = _source(
        "api",
        "https://example.com/api",
        items_path="data.items",
        fields=ApiFields(title="headline", url="link", summary="body"),
    )
    result = fetch_source(source, _client(handler))

    assert [(item.title, item.url, item.summary) for item in result.items or ()] == [
        (
            "Cloud grant",
            "https://example.com/replace-me/cloud",
            "Up to a promotional credit for eligible startups.",
        )
    ]


def test_scrape_fetcher_returns_page_html_without_rendering() -> None:
    """Breaks if a static page is sent to the browser or the HTML is dropped."""
    page = (FIXTURES / "page_v1.html").read_bytes()
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=page)

    def render(url: str, user_agent: str) -> str:
        raise AssertionError(f"renderer called for {url} as {user_agent}")

    result = fetch_source(
        _source("scrape", "https://example.com/programs", js=False),
        _client(handler),
        render=render,
    )

    assert result.html is not None
    assert "promotional cloud credits" in result.html
    assert "/programs" in requested
    assert result.items is None


def test_javascript_source_uses_the_renderer_after_robots() -> None:
    """Breaks if js: true fetches the page with httpx instead of the renderer."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise AssertionError(f"page fetched with httpx: {request.url}")

    seen: list[tuple[str, str]] = []

    def render(url: str, user_agent: str) -> str:
        seen.append((url, user_agent))
        return "<html><body><p>Rendered credits.</p></body></html>"

    result = fetch_source(
        _source("scrape", "https://example.com/app", js=True),
        _client(handler),
        render=render,
    )

    assert result.html is not None
    assert "Rendered credits." in result.html
    assert seen == [
        ("https://example.com/app", "StudentRadar/0.1 (personal research bot)")
    ]
    assert requested == ["/robots.txt"]


def test_retries_server_errors_then_returns_the_body() -> None:
    """Breaks if a 500 is not retried twice or a later 200 is discarded."""
    attempts = {"page": 0}
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        attempts["page"] += 1
        if attempts["page"] < 3:
            return httpx.Response(500)
        return httpx.Response(200, text="ok")

    client = _client(handler, sleeper=slept.append)
    response = client.get("https://example.com/feed.xml")

    assert response.text == "ok"
    assert attempts["page"] == 3
    assert slept == [1.0, 2.0]


def test_client_error_is_not_retried() -> None:
    """Breaks if a 404 is retried or reported without its status."""
    attempts = {"page": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        attempts["page"] += 1
        return httpx.Response(404)

    client = _client(handler)

    with pytest.raises(FetchError, match="404"):
        client.get("https://example.com/missing")
    assert attempts["page"] == 1


def test_robots_disallow_blocks_the_fetch_and_is_cached() -> None:
    """Breaks if a disallowed path is fetched or robots.txt is downloaded twice."""
    robots_hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            robots_hits["n"] += 1
            return httpx.Response(
                200,
                text="User-agent: *\nDisallow: /private\n",
            )
        return httpx.Response(200, text="public")

    client = _client(handler)

    with pytest.raises(FetchError, match="robots.txt"):
        client.get("https://example.com/private")
    assert client.get("https://example.com/public").text == "public"
    assert robots_hits["n"] == 1


def test_robots_redirect_on_the_same_host_is_followed() -> None:
    """Breaks if a robots.txt redirect re-checks robots.txt and recurses."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(301, headers={"location": "/robots-live.txt"})
        if request.url.path == "/robots-live.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        return httpx.Response(200, text="public")

    client = _client(handler)

    assert client.get("https://example.com/public").text == "public"
    with pytest.raises(FetchError, match="robots.txt"):
        client.get("https://example.com/private")
    assert paths == ["/robots.txt", "/robots-live.txt", "/public"]


def test_failed_robots_fetch_is_not_repeated_for_the_host() -> None:
    """Breaks if every page on a host with a broken robots.txt retries it."""
    robots_hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            robots_hits["n"] += 1
            return httpx.Response(503)
        return httpx.Response(200, text="page")

    client = _client(handler)

    for path in ("/one", "/two"):
        with pytest.raises(FetchError):
            client.get(f"https://example.com{path}")
    assert robots_hits["n"] == 3


def test_delay_is_applied_between_requests() -> None:
    """Breaks if later requests ignore request_delay_seconds."""
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text="ok")

    client = _client(
        handler,
        settings=_settings(request_delay_seconds=2),
        sleeper=slept.append,
    )
    client.get("https://example.com/one")
    client.get("https://example.com/two")

    assert slept == [2.0, 2.0]


def test_oversized_response_is_rejected() -> None:
    """Breaks if a huge body is accepted or its bytes are mentioned in the error."""
    body = b"x" * (MAX_RESPONSE_BYTES + 1)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=body)

    client = _client(handler)

    with pytest.raises(FetchError, match="exceeded") as raised:
        client.get("https://example.com/huge")
    assert "xxx" not in str(raised.value)


def test_gzip_page_is_read_as_text() -> None:
    """Breaks if a compressed page is decoded twice and the body is dropped."""
    import gzip

    raw = gzip.compress(b"<html><body><p>promotional credits</p></body></html>")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            content=raw,
            headers={"content-encoding": "gzip", "content-type": "text/html"},
        )

    response = _client(handler).get("https://example.com/programs")

    assert "promotional credits" in response.text


def test_redirect_is_followed_only_when_robots_allows_the_target() -> None:
    """Breaks if a redirect skips robots.txt on the next URL."""
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/private"})
        return httpx.Response(200, text="landed")

    client = _client(handler)

    with pytest.raises(FetchError, match="robots.txt"):
        client.get("https://example.com/start")
    assert "/private" not in fetched

    allowed = client.get("https://example.com/next")
    assert allowed.text == "landed"


def test_redirect_loop_stops() -> None:
    """Breaks if redirects are followed without a cap."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "/again"})

    client = _client(handler)

    with pytest.raises(FetchError, match="redirected too many times"):
        client.get("https://example.com/again")


def test_rendered_page_over_the_byte_cap_fails() -> None:
    """Breaks if a js render can return an unbounded HTML string."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise AssertionError(request.url.path)

    def render(url: str, user_agent: str) -> str:
        return "x" * (MAX_RESPONSE_BYTES + 1)

    with pytest.raises(FetchError, match="exceeded"):
        fetch_source(
            _source("scrape", "https://example.com/app", js=True),
            _client(handler),
            render=render,
        )


def test_request_count_includes_robots_and_the_page() -> None:
    """Breaks if a sweep cannot see how many HTTP calls it made."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text="ok")

    client = _client(handler)
    client.get("https://example.com/credits")

    assert client.request_count == 2
