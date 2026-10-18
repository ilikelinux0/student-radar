"""Generic JSON API fetcher. Job boards (Lever, Greenhouse, Ashby,
SmartRecruiters) are configured as api sources with a field map."""

from collections.abc import Mapping
from urllib.parse import quote

from radar.fetchers.feed import plain_text
from radar.fetchers.http import FetchError, HttpClient
from radar.models import RawItem, Source


def fetch_api(source: Source, client: HttpClient) -> list[RawItem]:
    """Return raw items from a JSON list. Does not touch the database."""
    response = client.get(source.url)
    try:
        payload = response.json()
    except ValueError as exc:
        raise FetchError(f"{source.url} did not return JSON") from exc
    found = _dig(payload, source.items_path, source.url)
    if not isinstance(found, list):
        raise FetchError(f"{source.url} items_path did not resolve to a list")
    fields = source.fields
    items: list[RawItem] = []
    for entry in found:
        if not isinstance(entry, Mapping):
            continue
        url = _text(_field(entry, fields.url))
        if url and fields.url_template:
            url = fields.url_template.replace("{id}", quote(url, safe=""))
        if not url:
            continue
        items.append(
            RawItem(
                title=plain_text(_text(_field(entry, fields.title))),
                url=url,
                summary=_summary(entry, source),
            )
        )
    return items


def _summary(entry: Mapping[str, object], source: Source) -> str:
    """Location and posting date go first, so the model reads them before the body."""
    fields = source.fields
    lines: list[str] = []
    location = _place(_field(entry, fields.location)) if fields.location else ""
    if location:
        lines.append(f"Location: {location}")
    posted = _text(_field(entry, fields.posted)) if fields.posted else ""
    if posted:
        lines.append(f"Posted: {posted}")
    body = plain_text(_text(_field(entry, fields.summary)))
    if body:
        lines.append(body)
    return "\n".join(lines)


def _field(entry: Mapping[str, object], path: str) -> object:
    """A dotted path such as location.name. A missing step is None."""
    current: object = entry
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _place(value: object) -> str:
    """A location may be a string, a list of strings, or a mapping of parts."""
    if isinstance(value, Mapping):
        parts = [
            _text(value.get(key))
            for key in ("name", "city", "region", "country")
            if value.get(key)
        ]
        return ", ".join(part for part in parts if part)
    if isinstance(value, list):
        return "; ".join(_place(part) for part in value if part)
    return _text(value)


def _dig(payload: object, path: str | None, url: str) -> object:
    if path is None or path == "":
        return payload
    current = payload
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise FetchError(f"{url} items_path {path!r} was not found")
        current = current[part]
    return current


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()
