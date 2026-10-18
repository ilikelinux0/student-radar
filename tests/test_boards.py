"""Job boards as api sources: field paths, URL templates, and closed postings."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from radar.db import (
    attach_offer,
    close_missing_offers,
    connect,
    get_offer,
    insert_finding,
    update_score,
)
from radar.fetchers import fetch_source
from radar.fetchers.http import HttpClient
from radar.models import (
    ApiFields,
    Config,
    NewFinding,
    PersonalScore,
    ScoreUpdate,
    Settings,
    Source,
)
from radar.scorer import ScoreOutcome, ScoringItem
from radar.sweep import run_sweep
from tests.profile_sample import sample_profile

PROFILE = sample_profile()

NOW = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)

LEVER = [
    {
        "text": "Security Intern",
        "hostedUrl": "https://jobs.lever.co/example/1",
        "descriptionPlain": "Work on <b>web</b> pentests.",
        "categories": {"location": "Istanbul", "team": "Security"},
    },
    {
        "text": "Account Executive",
        "hostedUrl": "https://jobs.lever.co/example/2",
        "descriptionPlain": "Sell things.",
        "categories": {},
    },
]
GREENHOUSE = {
    "jobs": [
        {
            "title": "Junior AppSec Engineer",
            "absolute_url": "https://boards.greenhouse.io/example/jobs/7",
            "location": {"name": "Istanbul, Türkiye"},
            "updated_at": "2026-10-01T10:00:00-04:00",
        }
    ]
}
SMARTRECRUITERS = {
    "content": [
        {
            "id": "744000140664419",
            "name": "Pentest Intern",
            "location": {"city": "Istanbul", "country": "tr", "remote": False},
            "releasedDate": "2026-10-03T09:00:00.000Z",
        }
    ]
}


def _client(payload: object) -> HttpClient:
    body = json.dumps(payload).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=body)

    settings = Settings(
        max_llm_items_per_run=5,
        keywords=["intern"],
        user_agent="StudentRadar/0.1 (personal research bot)",
        request_delay_seconds=0,
    )
    return HttpClient(
        settings, transport=httpx.MockTransport(handler), sleeper=lambda _s: None
    )


def _board(**overrides: object) -> Source:
    values: dict[str, object] = {
        "id": "board",
        "name": "Board",
        "url": "https://api.example.com/postings",
        "type": "api",
        "frequency": "daily",
    }
    values.update(overrides)
    return Source.model_validate(values)


def test_lever_board_keeps_location_and_strips_tags() -> None:
    """Breaks if the location is lost or HTML reaches the model."""
    source = _board(
        fields=ApiFields(
            title="text",
            url="hostedUrl",
            summary="descriptionPlain",
            location="categories.location",
        )
    )

    items = fetch_source(source, _client(LEVER)).items or ()

    assert [(item.title, item.url) for item in items] == [
        ("Security Intern", "https://jobs.lever.co/example/1"),
        ("Account Executive", "https://jobs.lever.co/example/2"),
    ]
    assert items[0].summary == "Location: Istanbul\nWork on web pentests."
    assert items[1].summary == "Sell things."


def test_greenhouse_board_reads_nested_location_and_date() -> None:
    """Breaks if a dotted path is read as a literal key."""
    source = _board(
        items_path="jobs",
        fields=ApiFields(
            title="title",
            url="absolute_url",
            summary="content",
            location="location.name",
            posted="updated_at",
        ),
    )

    items = fetch_source(source, _client(GREENHOUSE)).items or ()

    assert items[0].summary == (
        "Location: Istanbul, Türkiye\nPosted: 2026-10-01T10:00:00-04:00"
    )


def test_smartrecruiters_board_builds_the_public_url() -> None:
    """Breaks if a board without links stores no URL or the API URL."""
    source = _board(
        items_path="content",
        fields=ApiFields(
            title="name",
            url="id",
            summary="none",
            location="location",
            posted="releasedDate",
            url_template="https://jobs.smartrecruiters.com/Example/{id}",
        ),
    )

    items = fetch_source(source, _client(SMARTRECRUITERS)).items or ()

    assert items[0].url == "https://jobs.smartrecruiters.com/Example/744000140664419"
    assert items[0].summary.startswith("Location: Istanbul, tr\n")


@pytest.mark.parametrize(
    "template",
    ["https://jobs.example.com/no-placeholder", "http://jobs.example.com/{id}"],
)
def test_url_template_must_be_https_with_an_id(template: str) -> None:
    """Breaks if a template could build plain-http or constant links."""
    with pytest.raises(ValidationError, match="url_template"):
        ApiFields(url="id", url_template=template)


def test_closes_missing_is_only_for_api_sources() -> None:
    """Breaks if a scraped page could close offers it never listed."""
    with pytest.raises(ValidationError, match="closes_missing"):
        _board(type="rss", closes_missing=True)


def _score_by_url(
    items: list[ScoringItem],
    settings: Settings,
    client: object,
    *,
    environ: object = None,
    spec: object = None,
) -> list[ScoreOutcome]:
    del settings, client, environ, spec
    return [
        ScoreOutcome(
            score=PersonalScore(
                fit=9,
                category="job",
                summary="A posting.",
                reasoning="It matches.",
                offer_key=f"example:job-{item.url.rsplit('/', 1)[-1]}",
                org="Example",
                program_name="Job",
            ),
            attempted=True,
        )
        for item in items
    ]


def _run(conn: sqlite3.Connection, payload: object, now: datetime) -> None:
    body = json.dumps(payload).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=body)

    source = _board(
        closes_missing=True,
        fields=ApiFields(title="text", url="hostedUrl", summary="descriptionPlain"),
    )
    config = Config(
        sources=[source],
        settings=Settings(
            max_llm_items_per_run=10,
            keywords=["intern", "account"],
            user_agent="StudentRadar/0.1 (personal research bot)",
            request_delay_seconds=0,
            scorer_provider="bedrock",
        ),
    )
    run_sweep(
        config,
        conn,
        profile=PROFILE,
        force=True,
        score=_score_by_url,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _s: None,
        now=now,
    )


def test_a_vanished_posting_closes_and_reopens(tmp_path: Path) -> None:
    """Breaks if a removed job stays open, or a returning one stays closed."""
    conn = connect(tmp_path / "personal.db")
    try:
        _run(conn, LEVER, NOW)
        first = get_offer(conn, "example:job-2")
        _run(conn, LEVER[:1], NOW + timedelta(days=1))
        closed = get_offer(conn, "example:job-2")
        still_open = get_offer(conn, "example:job-1")
        _run(conn, LEVER, NOW + timedelta(days=2))
        reopened = get_offer(conn, "example:job-2")
    finally:
        conn.close()

    assert first is not None and first.closed_at is None
    assert closed is not None and closed.closed_at == NOW + timedelta(days=1)
    assert still_open is not None and still_open.closed_at is None
    assert reopened is not None and reopened.closed_at is None


def test_an_offer_seen_elsewhere_is_not_closed(tmp_path: Path) -> None:
    """Breaks if one board closes a program that another page still lists."""
    conn = connect(tmp_path / "personal.db")
    try:
        for index, source_id in enumerate(("board", "news"), start=1):
            insert_finding(
                conn,
                NewFinding(
                    source_id=source_id,
                    title="Intern",
                    url=f"https://example.com/{source_id}",
                    content_hash=f"h{index}",
                    excerpt="intern",
                    found_at=NOW,
                    keyword_hits=["intern"],
                ),
            )
            update = ScoreUpdate(score=9, offer_key="example:shared", provider="Ex")
            update_score(conn, index, update)
            attach_offer(conn, index, update, threshold=8)

        result = close_missing_offers(conn, "board", set(), NOW)
        offer = get_offer(conn, "example:shared")
    finally:
        conn.close()

    assert result == (0, 0)
    assert offer is not None and offer.closed_at is None
