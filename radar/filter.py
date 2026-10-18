"""Keyword pre-filter for English and Turkish terms."""

_FOLDS = str.maketrans(
    {
        "İ": "i",
        "I": "i",
        "ı": "i",
        "Ş": "s",
        "ş": "s",
        "Ğ": "g",
        "ğ": "g",
        "Ü": "u",
        "ü": "u",
        "Ö": "o",
        "ö": "o",
        "Ç": "c",
        "ç": "c",
    }
)


def fold(text: str) -> str:
    """Fold case and Turkish letters so ı/i, ş/s, ğ/g, ü/u, ö/o, and ç/c match."""
    return text.translate(_FOLDS).casefold()


def match_keywords(text: str, keywords: list[str]) -> list[str]:
    """Return keywords that occur in text, in keyword-list order, once each."""
    folded = fold(text)
    hits: list[str] = []
    for keyword in keywords:
        needle = fold(keyword)
        if needle and needle in folded and keyword not in hits:
            hits.append(keyword)
    return hits


def gate_groups(
    title: str,
    body: str,
    groups: list[list[str]],
    title_groups: list[list[str]] | None = None,
) -> list[str]:
    """Every group needs a hit. Returns all hits, in order.

    title_groups must hit in the title; groups may hit in the title or body.
    A job board needs a level word (intern, yeni mezun) in the title: a job
    description says "graduate" or "öğrenci" for senior roles too.
    """
    hits: list[str] = []
    checks = [(title, group) for group in title_groups or []]
    checks += [(f"{title}\n{body}", group) for group in groups]
    for text, group in checks:
        found = match_keywords(text, group)
        if not found:
            return []
        hits.extend(hit for hit in found if hit not in hits)
    return hits


def gate_keywords(
    title: str, body: str, keywords: list[str], *, strict: bool
) -> list[str]:
    """Return hits that are allowed through the pre-filter.

    Discovery sources are strict: one hit in the title, or at least two hits
    in the title and body together. Provider pages pass on any hit.
    """
    hits = match_keywords(f"{title}\n{body}", keywords)
    if not hits or not strict:
        return hits
    if match_keywords(title, keywords):
        return hits
    if len(hits) >= 2:
        return hits
    return []
