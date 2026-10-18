"""Shared HTTP client: user agent, delay, timeout, retries, and robots.txt."""

import logging
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from radar.models import Settings

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 20.0
_ATTEMPTS = 3
_BACKOFF_SECONDS = (1.0, 2.0)
MAX_RESPONSE_BYTES = 4_000_000
_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

Sleeper = Callable[[float], None]


class FetchError(Exception):
    """A URL could not be fetched. The message names the status, not the body."""


class HttpClient:
    """Polite HTTP client for one sweep.

    robots.txt results are cached per host for the life of this client.
    A 404 robots.txt allows the host. A failed robots fetch fails the request,
    and later requests to that host fail the same way without fetching it again.
    The YouTube uploads feed is the exception: phase 15 fetches it anyway.
    Redirects stop after 5 hops, and each target is checked against robots.txt.
    A body over MAX_RESPONSE_BYTES fails the request. The body is not logged.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleeper: Sleeper | None = None,
    ) -> None:
        self.user_agent = settings.user_agent
        self._delay = settings.request_delay_seconds
        self._sleep = sleeper or _default_sleep
        self._started = False
        self.request_count = 0
        self._robots: dict[str, RobotFileParser | FetchError | None] = {}
        self._client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(_TIMEOUT_SECONDS),
            headers={"User-Agent": settings.user_agent},
            follow_redirects=False,
            trust_env=False,
        )

    def close(self) -> None:
        self._client.close()

    def get(self, url: str) -> httpx.Response:
        """Fetch a URL after the robots check.

        The uploads feed is fetched even when robots.txt disallows it.
        Every other URL, including a redirect off that feed, is still checked.
        """
        if not _youtube_uploads_feed(url):
            self.ensure_allowed(url)
        return self._request(url)

    def ensure_allowed(self, url: str) -> None:
        """Raise FetchError when robots.txt disallows url."""
        origin = _origin(url)
        if origin not in self._robots:
            try:
                self._robots[origin] = self._load_robots(origin)
            except FetchError as exc:
                self._robots[origin] = exc
                raise
        parser = self._robots[origin]
        if isinstance(parser, FetchError):
            raise FetchError(str(parser))
        if parser is not None and not parser.can_fetch(self.user_agent, url):
            raise FetchError(f"robots.txt disallows {url}")

    def wait_turn(self) -> None:
        """Sleep between outbound requests. The first request of a run does not wait."""
        if self._started and self._delay > 0:
            self._sleep(self._delay)
        self._started = True

    def _load_robots(self, origin: str) -> RobotFileParser | None:
        # robots.txt itself is always fetchable, so its redirects skip the check.
        response = self._request(
            f"{origin}/robots.txt", allow_not_found=True, check_robots=False
        )
        if response.status_code == 404:
            return None
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser

    def _request(
        self, url: str, *, allow_not_found: bool = False, check_robots: bool = True
    ) -> httpx.Response:
        current = url
        for hop in range(_MAX_REDIRECTS + 1):
            response = self._fetch_with_retries(
                current, allow_not_found=allow_not_found and hop == 0
            )
            if response.status_code not in _REDIRECT_STATUSES:
                return response
            if hop == _MAX_REDIRECTS:
                raise FetchError(f"{url} redirected too many times")
            location = response.headers.get("location", "").strip()
            if not location:
                raise FetchError(f"{current} redirected without a location")
            current = urljoin(str(response.url), location)
            if check_robots:
                self.ensure_allowed(current)
        raise FetchError(f"{url} redirected too many times")  # pragma: no cover

    def _fetch_with_retries(
        self, url: str, *, allow_not_found: bool = False
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(_ATTEMPTS):
            if attempt > 0:
                self._sleep(_BACKOFF_SECONDS[attempt - 1])
            self.wait_turn()
            try:
                response = self._read_limited(url)
            except httpx.TransportError as exc:
                last_error = exc
                logger.warning("fetch error url=%s attempt=%s", url, attempt + 1)
                continue
            status = response.status_code
            if status == 404 and allow_not_found:
                return response
            if status == 429 or status >= 500:
                last_error = FetchError(f"{url} returned {status}")
                logger.warning(
                    "fetch status=%s url=%s attempt=%s",
                    status,
                    url,
                    attempt + 1,
                )
                continue
            if status >= 400 and status not in _REDIRECT_STATUSES:
                raise FetchError(f"{url} returned {status}")
            return response
        raise FetchError(f"{url} failed after {_ATTEMPTS} attempts") from last_error

    def _read_limited(self, url: str) -> httpx.Response:
        self.request_count += 1
        with self._client.stream("GET", url) as response:
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise FetchError(
                        f"{url} response exceeded {MAX_RESPONSE_BYTES} bytes"
                    )
                chunks.append(chunk)
            headers = [
                (name, value)
                for name, value in response.headers.multi_items()
                if name.lower()
                not in {"content-encoding", "content-length", "transfer-encoding"}
            ]
            return httpx.Response(
                status_code=response.status_code,
                headers=headers,
                content=b"".join(chunks),
                request=response.request,
            )


def _youtube_uploads_feed(url: str) -> bool:
    """True only for the channel uploads feed, not for watch or caption URLs."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    return (
        host in {"www.youtube.com", "youtube.com"}
        and parsed.path == "/feeds/videos.xml"
    )


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise FetchError(f"unsupported URL: {url}")
    return f"{parsed.scheme}://{parsed.netloc}"


def _default_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)
