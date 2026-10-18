"""Write the static JSON snapshot the frontend reads. No secrets, no page bodies.

Eligibility is computed here from the stored requirements and the profile
facts. Facts and the fit summary are never
written; reasons are built from requirements only.
"""

import json
import logging
import re
import sqlite3
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path

from radar.db import list_offers, list_source_states, query_findings
from radar.eligibility import evaluate
from radar.models import Config, Finding, Offer, Requirements, Source, SourceState
from radar.profile import Profile

logger = logging.getLogger(__name__)

EXCERPT_LIMIT = 600
_YEAR = re.compile(r"\b\d{4}\b")


def publish_snapshot(
    config: Config,
    conn: sqlite3.Connection,
    *,
    generated_at: datetime,
    duration_seconds: float,
    items_found: Mapping[str, int],
    destination: Path,
    dry_run: bool,
    profile: Profile,
) -> None:
    """Write offers.json and sources.json. A dry run prints the counts only."""
    states = {row.source_id: row for row in list_source_states(conn)}
    by_offer: dict[str, list[Finding]] = {}
    unscored: list[Finding] = []
    for finding in query_findings(conn):
        if finding.offer_key is None:
            unscored.append(finding)
            continue
        by_offer.setdefault(finding.offer_key, []).append(finding)
    offers = [
        _offer_row(offer, by_offer.get(offer.offer_key, []))
        for offer in list_offers(conn)
    ]
    offers.extend(_unscored_row(finding) for finding in unscored)
    opens = {
        source.id: source.usually_opens
        for source in config.sources
        if source.usually_opens
    }
    records = {offer.offer_key: offer for offer in list_offers(conn)}
    today = generated_at.date()
    for row in offers:
        key = str(row["offer_key"])
        mentions = by_offer.get(key) or [
            finding for finding in unscored if f"legacy:{finding.content_hash}" == key
        ]
        row.update(
            _personal_fields(records.get(key), mentions, profile, today, opens, row)
        )
        _scrub(row, profile.private_terms)
    previous = _previous_counts(destination / "sources.json")
    sources = [
        _source_row(source, states.get(source.id), items_found, previous)
        for source in config.sources
    ]
    for source, row in zip(config.sources, sources, strict=True):
        row["usually_opens"] = source.usually_opens
        row["url"] = source.url or None
    mention_count = sum(len(offer["mentions"]) for offer in offers)
    meta = {
        "generated_at": _stamp(generated_at),
        "counts": {
            "offers": len(offers),
            "mentions": mention_count,
            "sources": len(sources),
        },
        "duration_seconds": duration_seconds,
    }
    offers_path = destination / "offers.json"
    sources_path = destination / "sources.json"
    if dry_run:
        print(
            f"would write {offers_path} offers={len(offers)} "
            f"mentions={mention_count} and {sources_path} sources={len(sources)}"
        )
        return
    destination.mkdir(parents=True, exist_ok=True)
    _write(offers_path, {"meta": meta, "offers": offers})
    _write(sources_path, {"meta": meta, "sources": sources})
    logger.info(
        "snapshot offers=%s mentions=%s sources=%s",
        len(offers),
        mention_count,
        len(sources),
    )


def _offer_row(offer: Offer, mentions: list[Finding]) -> dict[str, object]:
    best = _best_mention(mentions)
    return {
        "offer_key": offer.offer_key,
        "provider": offer.provider,
        "program_name": offer.program_name,
        "best_score": offer.best_score,
        "amount": offer.amount,
        "deadline": offer.deadline,
        "first_seen_at": _stamp(offer.first_seen_at),
        "last_seen_at": _stamp(offer.last_seen_at),
        "mention_count": offer.mention_count,
        "status": offer.status,
        "category": None if best is None else best.category,
        "best_mention": None if best is None else _mention(best, excerpt=True),
        "mentions": [_mention(row, excerpt=False) for row in mentions],
    }


def _unscored_row(finding: Finding) -> dict[str, object]:
    """A keyword hit with no offer yet. The key matches the one set_status uses."""
    seen = _stamp(finding.found_at)
    return {
        "offer_key": f"legacy:{finding.content_hash}",
        "provider": None,
        "program_name": None,
        "best_score": None,
        "amount": None,
        "deadline": None,
        "first_seen_at": seen,
        "last_seen_at": seen,
        "mention_count": 1,
        "status": finding.status,
        "category": None,
        "best_mention": _mention(finding, excerpt=True),
        "mentions": [_mention(finding, excerpt=False)],
    }


def _personal_fields(
    offer: Offer | None,
    mentions: list[Finding],
    profile: Profile,
    today: date,
    opens: Mapping[str, str | None],
    row: Mapping[str, object],
) -> dict[str, object]:
    """Fit, eligibility, and dates from the best mention. Unscored rows have none."""
    best = _best_mention(mentions)
    details = _details(best)
    deadline = _date(row.get("deadline"))
    starts = _date(details.get("starts_at"))
    eligibility: str | None = None
    reason: str | None = None
    if best is not None and best.score is not None:
        result = evaluate(
            _requirements(details),
            profile.facts,
            reference=starts or deadline or today,
            category=best.category,
            country=_text(details.get("country")),
            remote=details.get("remote")
            if isinstance(details.get("remote"), bool)
            else None,
            starts_at=starts,
        )
        eligibility, reason = result.status, result.reason
    quote = _text(details.get("deadline_quote"))
    status = _text(details.get("application_status")) or "unknown"
    closed_at = None if offer is None else offer.closed_at
    usually = next(
        (
            opens[finding.source_id]
            for finding in mentions
            if finding.source_id in opens
        ),
        None,
    )
    return {
        "fit": row.get("best_score"),
        "eligibility": eligibility,
        "eligibility_reason": reason,
        "application_status": status,
        "deadline_kind": _text(details.get("deadline_kind")) or "unknown",
        "deadline_approx": bool(deadline and quote and _YEAR.search(quote) is None),
        "starts_at": _text(details.get("starts_at")),
        "ends_at": _text(details.get("ends_at")),
        "location": _text(details.get("location")),
        "remote": details.get("remote")
        if isinstance(details.get("remote"), bool)
        else None,
        "closed": closed_at is not None or status == "closed",
        "usually_opens": usually,
    }


def _scrub(row: dict[str, object], terms: list[str]) -> None:
    """Remove private terms from model-written text, in case the model echoed them."""
    best = row.get("best_mention")
    if not terms or not isinstance(best, dict):
        return
    for key in ("summary", "reasoning"):
        value = best.get(key)
        if not isinstance(value, str):
            continue
        for term in terms:
            if term.strip():
                value = re.sub(re.escape(term.strip()), "[private]", value, flags=re.I)
        best[key] = value


def _details(finding: Finding | None) -> dict[str, object]:
    if finding is None or not finding.details:
        return {}
    try:
        parsed = json.loads(finding.details)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _requirements(details: Mapping[str, object]) -> Requirements | None:
    raw = details.get("requirements")
    if not isinstance(raw, dict):
        return None
    try:
        return Requirements.model_validate(raw)
    except ValueError:
        return None


def _date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _best_mention(mentions: list[Finding]) -> Finding | None:
    if not mentions:
        return None
    return max(
        mentions,
        key=lambda row: (
            row.score is not None,
            row.score if row.score is not None else -1,
            row.found_at,
            row.id,
        ),
    )


def _mention(finding: Finding, *, excerpt: bool) -> dict[str, object]:
    row: dict[str, object] = {
        "title": finding.title,
        "source": finding.source_id,
        "url": finding.url,
        "date": _stamp(finding.found_at),
        "score": finding.score,
    }
    if excerpt:
        row["excerpt"] = finding.excerpt[:EXCERPT_LIMIT]
        row["summary"] = _clip(finding.summary, 500)
        row["reasoning"] = _clip(finding.reasoning, 1000)
    return row


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value[:limit]


def _source_row(
    source: Source,
    state: SourceState | None,
    items_found: Mapping[str, int],
    previous: Mapping[str, int],
) -> dict[str, object]:
    if source.id in items_found:
        found: int | None = items_found[source.id]
    elif source.id in previous:
        found = previous[source.id]
    else:
        found = None
    checked = None if state is None else state.last_checked_at
    return {
        "id": source.id,
        "name": source.name,
        "category": source.tags[0] if source.tags else None,
        "type": source.type,
        "last_checked": None if checked is None else _stamp(checked),
        "status": None if state is None else state.last_status,
        "failures": 0 if state is None else state.consecutive_failures,
        "items_found_last_run": found,
        "last_error": None if state is None else state.last_error,
    }


def _previous_counts(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = document.get("sources") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        return {}
    counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_id = row.get("id")
        found = row.get("items_found_last_run")
        if (
            isinstance(source_id, str)
            and isinstance(found, int)
            and not isinstance(found, bool)
            and found >= 0
        ):
            counts[source_id] = found
    return counts


def _write(path: Path, document: dict[str, object]) -> None:
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")
