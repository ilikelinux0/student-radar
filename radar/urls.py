"""Canonical article URLs for dedupe. The stored excerpt is unchanged."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_KEYS = frozenset(
    {
        "ref",
        "ref_src",
        "ref_url",
        "fbclid",
        "gclid",
        "gbraid",
        "wbraid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "yclid",
        "twclid",
        "dclid",
        "gad_source",
        "_ga",
        "_gl",
        "fb_action_ids",
        "fb_action_types",
        "fb_source",
        "mkt_tok",
    }
)


def canonicalize_url(url: str) -> str:
    """Lowercase the host, drop the fragment, and remove tracking parameters.

    A non-http URL is returned stripped and otherwise unchanged.
    """
    stripped = url.strip()
    parsed = urlsplit(stripped)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return stripped
    try:
        port = parsed.port
    except ValueError:
        return stripped
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    kept = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracker(key)
    ]
    query = urlencode(kept, doseq=True)
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path, query, ""))


def _is_tracker(key: str) -> bool:
    folded = key.lower()
    return folded.startswith("utm_") or folded in _TRACKING_KEYS
