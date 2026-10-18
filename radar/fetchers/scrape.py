"""HTML scrape fetcher. Playwright is imported only when a source sets js: true."""

from collections.abc import Callable

from radar.fetchers.http import MAX_RESPONSE_BYTES, FetchError, HttpClient
from radar.models import Source

RenderPage = Callable[[str, str], str]


def fetch_scrape(
    source: Source,
    client: HttpClient,
    *,
    render: RenderPage | None = None,
) -> str:
    """Return raw HTML. A js source is rendered; other sources are a plain GET."""
    if not source.js:
        return client.get(source.url).text
    client.ensure_allowed(source.url)
    client.wait_turn()
    renderer = render or render_with_playwright
    html = renderer(source.url, client.user_agent)
    if len(html.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise FetchError(f"{source.url} response exceeded {MAX_RESPONSE_BYTES} bytes")
    return html


def render_with_playwright(url: str, user_agent: str) -> str:
    """Render url in headless Chromium. Import Playwright only here."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise FetchError(
            "Playwright is not installed. Install it before enabling js: true."
        ) from exc
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=user_agent)
            page.goto(url, timeout=20_000, wait_until="domcontentloaded")
            return page.content()
        finally:
            browser.close()
