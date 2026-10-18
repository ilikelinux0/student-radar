"""The personal snapshot: eligibility, closed postings, and no profile data."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from radar.db import connect
from radar.models import Config, PersonalScore, Settings, Source
from radar.scorer import ScoreOutcome, ScoreSpec, ScoringItem
from radar.sweep import run_sweep
from tests.profile_sample import sample_profile

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)


def _transport() -> httpx.MockTransport:
    feed = (FIXTURES / "feed.xml").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=feed)

    return httpx.MockTransport(handler)


def _config() -> Config:
    return Config(
        sources=[
            Source(
                id="feed",
                name="Feed",
                url="https://example.com/feed.xml",
                type="rss",
                frequency="daily",
                usually_opens="Feb–Mar",
            )
        ],
        settings=Settings(
            max_llm_items_per_run=10,
            keywords=["credits", "grant"],
            user_agent="StudentRadar/0.1 (personal research bot)",
            request_delay_seconds=0,
            scorer_provider="gemini",
        ),
    )


def _scorer(by_title: dict[str, dict[str, object]]):  # type: ignore[no-untyped-def]
    def score(
        items: list[ScoringItem],
        settings: Settings,
        client: object,
        *,
        environ: object = None,
        spec: ScoreSpec | None = None,
    ) -> list[ScoreOutcome]:
        del settings, client, environ, spec
        outcomes: list[ScoreOutcome] = []
        for item in items:
            values: dict[str, object] = {
                "fit": 8,
                "category": "grant",
                "org": "Example",
                "program_name": item.title,
                "offer_key": "example:" + item.title.lower().replace(" ", "-")[:40],
                "summary": "A program that Zephyrcorp people would like.",
                "reasoning": "It fits the focus.",
            }
            values.update(by_title.get(item.title, {}))
            outcomes.append(
                ScoreOutcome(score=PersonalScore.model_validate(values), attempted=True)
            )
        return outcomes

    return score


def _export(
    tmp_path: Path, by_title: dict[str, dict[str, object]]
) -> tuple[dict, dict]:  # type: ignore[type-arg]
    conn = connect(tmp_path / "personal.db")
    try:
        run_sweep(
            _config(),
            conn,
            score=_scorer(by_title),
            profile=sample_profile(),
            transport=_transport(),
            sleeper=lambda _s: None,
            now=NOW,
            export_dir=tmp_path / "snapshot",
        )
    finally:
        conn.close()
    offers = json.loads((tmp_path / "snapshot" / "offers.json").read_text("utf-8"))
    sources = json.loads((tmp_path / "snapshot" / "sources.json").read_text("utf-8"))
    return offers, sources


def _by_name(offers: dict) -> dict[str, dict]:  # type: ignore[type-arg]
    return {row["program_name"]: row for row in offers["offers"]}


def test_personal_snapshot_has_eligibility_and_dates(tmp_path: Path) -> None:
    """Breaks if a personal field is missing or eligibility ignores requirements."""
    offers, sources = _export(
        tmp_path,
        {
            "Grant deadline posted": {
                "deadline": "2026-12-01",
                "deadline_quote": "Applications close on 2026-12-01",
                "deadline_kind": "apply_by",
                "application_status": "open",
                "requirements": {"student_years": [3, 4], "citizenship": ["TR"]},
            },
            "Startup credits expanded": {
                "requirements": {"citizenship": ["US"]},
                "country": "DE",
            },
        },
    )

    rows = _by_name(offers)
    grant = rows["Grant deadline posted"]
    assert grant["fit"] == 8
    assert grant["deadline"] == "2026-12-01"
    assert grant["eligibility"] == "eligible"
    assert grant["eligibility_reason"] is None
    assert grant["deadline_kind"] == "apply_by"
    assert grant["deadline_approx"] is False
    assert grant["application_status"] == "open"
    assert grant["closed"] is False
    assert grant["usually_opens"] == "Feb–Mar"
    credits = rows["Startup credits expanded"]
    assert credits["eligibility"] == "ineligible"
    assert credits["eligibility_reason"] == "requires citizenship: US"
    assert sources["sources"][0]["usually_opens"] == "Feb–Mar"
    assert sources["sources"][0]["url"] == "https://example.com/feed.xml"


def test_a_quote_without_a_year_is_approximate(tmp_path: Path) -> None:
    """Breaks if a deadline the model had to give a year to is shown as exact."""
    offers, _sources = _export(
        tmp_path,
        {
            "Grant deadline posted": {
                "deadline": "2026-12-01",
                "deadline_quote": "Applications close on",
            }
        },
    )

    assert _by_name(offers)["Grant deadline posted"]["deadline_approx"] is True


def test_personal_snapshot_holds_no_profile_data(tmp_path: Path) -> None:
    """Breaks if a fact, the fit summary, or a private term reaches the files."""
    profile = sample_profile()
    offers, sources = _export(
        tmp_path,
        {
            "Grant deadline posted": {
                "requirements": {"min_gpa": 3.5, "max_age": 20},
            }
        },
    )

    text = json.dumps(offers, ensure_ascii=False) + json.dumps(
        sources, ensure_ascii=False
    )
    assert "Zephyrcorp" not in text
    assert "[private]" in text
    assert profile.fit_summary.strip()[:40] not in text
    for value in ("2001-02-03", "3.17", "Istanbul", "deferred", "C1"):
        assert value not in text
    assert _by_name(offers)["Grant deadline posted"]["eligibility_reason"] == (
        "age limit 20"
    )


def test_a_low_fit_offer_keeps_its_deadline(tmp_path: Path) -> None:
    """Breaks if a confidence threshold hides a real deadline on a poor fit."""
    offers, _sources = _export(
        tmp_path,
        {
            "Grant deadline posted": {
                "fit": 3,
                "deadline": "2026-12-01",
                "deadline_quote": "Applications close on 2026-12-01",
            }
        },
    )

    row = _by_name(offers)["Grant deadline posted"]
    assert row["fit"] == 3
    assert row["deadline"] == "2026-12-01"
