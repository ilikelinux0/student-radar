"""Scoring helpers. Profile facts never pass through here."""

import json
import logging

from radar.filter import fold
from radar.models import PersonalScore, ScoreUpdate

logger = logging.getLogger(__name__)


def verify_deadline(score: PersonalScore, *seen: str) -> PersonalScore:
    """Keep the deadline only when its quote is in the text the model was shown.

    A date with no quote, or a quote the excerpt does not contain, is dropped
    with its quote. Case, Turkish letters, and whitespace are folded first.
    """
    if score.deadline is None and score.deadline_quote is None:
        return score
    quote = _squash(score.deadline_quote or "")
    if quote and quote in _squash("\n".join(seen)):
        return score
    logger.info("deadline dropped: quote not found in the excerpt")
    return score.model_copy(update={"deadline": None, "deadline_quote": None})


def personal_update(score: PersonalScore) -> ScoreUpdate:
    """Map the personal schema onto the shared columns plus findings.details."""
    details = {
        "application_status": score.application_status,
        "deadline_quote": score.deadline_quote,
        "deadline_kind": score.deadline_kind,
        "starts_at": _iso(score.starts_at),
        "ends_at": _iso(score.ends_at),
        "location": score.location,
        "country": score.country,
        "remote": score.remote,
        "requirements": score.requirements.model_dump(mode="json"),
    }
    return ScoreUpdate(
        score=score.fit,
        category=score.category,
        amount=score.value,
        deadline=_iso(score.deadline),
        summary=score.summary,
        reasoning=score.reasoning,
        offer_key=score.offer_key,
        provider=score.org,
        program_name=score.program_name,
        details=json.dumps(details, ensure_ascii=False, sort_keys=True),
    )


def _squash(text: str) -> str:
    return " ".join(fold(text).split())


def _iso(value: object) -> str | None:
    if value is None:
        return None
    return value.isoformat()  # type: ignore[attr-defined]
