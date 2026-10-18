"""Watch a GitHub README through its commits Atom feed."""

import difflib

from radar.extract import normalize
from radar.fetchers.feed import parse_feed
from radar.fetchers.http import HttpClient
from radar.models import RawItem, Source
from radar.urls import canonicalize_url

_DIFF_LIMIT = 1500


def fetch_github(
    source: Source,
    client: HttpClient,
    *,
    snapshot_text: str | None,
) -> tuple[list[RawItem], str]:
    """Return a README diff since snapshot_text, and the current README text.

    The first fetch (no snapshot) is one baseline item, the same rule as a scrape.
    Later fetches emit one item only when the README text changed.
    """
    repo = source.repo or ""
    atom = client.get(f"https://github.com/{repo}/commits/HEAD/README.md.atom")
    readme = client.get(f"https://raw.githubusercontent.com/{repo}/HEAD/README.md")
    text = readme.content.decode("utf-8", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    item = _item(source, text, snapshot_text, _commits(atom.content))
    if item is None:
        return [], text
    return [item], text


def _commits(content: bytes) -> list[tuple[str, str]]:
    return [(item.title, item.url) for item in parse_feed(content) if item.url]


def _item(
    source: Source,
    text: str,
    previous: str | None,
    commits: list[tuple[str, str]],
) -> RawItem | None:
    if commits:
        title, url = commits[0]
    else:
        title, url = "README changed", f"https://github.com/{source.repo}"
    url = canonicalize_url(url)
    if previous is None:
        return RawItem(title=title, url=url, summary=text.strip())
    if normalize(previous) == normalize(text):
        return None
    return RawItem(title=title, url=url, summary=_diff(previous, text))


def _diff(previous: str, current: str) -> str:
    lines = difflib.unified_diff(
        previous.splitlines(),
        current.splitlines(),
        fromfile="README.md",
        tofile="README.md",
        lineterm="",
    )
    text = "\n".join(lines).strip()
    if len(text) <= _DIFF_LIMIT:
        return text
    return text[:_DIFF_LIMIT]
