"""Offer grouping across mentions. No network or model."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from radar.db import connect, get_offer, query_findings
from radar.models import Config, PersonalScore, Settings, Source
from radar.scorer import ScoreOutcome, ScoringItem
from radar.sweep import run_sweep
from tests.profile_sample import sample_profile

PROFILE = sample_profile()

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def _rss(items: list[tuple[str, str, str]]) -> bytes:
    body = "".join(
        f"<item><title>{title}</title><link>{url}</link>"
        f"<description>{description}</description></item>"
        for title, url, description in items
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<rss version="2.0"><channel><title>Example</title>{body}</channel></rss>'
    ).encode()


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "max_llm_items_per_run": 40,
        "keywords": ["internship", "grant"],
        "user_agent": "StudentRadar/0.1 (personal research bot)",
        "request_delay_seconds": 0,
        "scorer_provider": "bedrock",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _score_same_offer(
    items: object,
    settings: Settings,
    client: object,
    *,
    environ: object = None,
    spec: object = None,
) -> list[ScoreOutcome]:
    del settings, client, environ, spec
    assert isinstance(items, list)
    outcomes: list[ScoreOutcome] = []
    for item in items:
        assert isinstance(item, ScoringItem)
        stipend = "5000 TL" if "raised to 5000" in item.excerpt else "1000 TL"
        outcomes.append(
            ScoreOutcome(
                score=PersonalScore(
                    fit=9,
                    category="internship",
                    value=stipend,
                    summary="A summer internship for students.",
                    reasoning="Same program.",
                    offer_key="example:summer-internship",
                    org="Example",
                    program_name="Summer internship",
                ),
                attempted=True,
            )
        )
    return outcomes


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "radar.db")
    yield connection
    connection.close()


def _feed_config() -> Config:
    return Config(
        sources=[
            Source(
                id="feed",
                name="feed",
                url="https://example.com/feed.xml",
                type="rss",
                frequency="daily",
            )
        ],
        settings=_settings(),
    )


def test_two_wordings_merge_into_one_offer(conn: sqlite3.Connection) -> None:
    """Breaks if two articles about one program become two offers."""
    feed = _rss(
        [
            (
                "Example opens its summer internship",
                "https://example.com/intern-a",
                "internship applications for students",
            ),
            (
                "A recap of the Example summer internship",
                "https://example.com/intern-b",
                "another note on the internship",
            ),
        ]
    )

    run_sweep(
        _feed_config(),
        conn,
        profile=PROFILE,
        score=_score_same_offer,
        environ={},
        transport=_transport(feed),
        sleeper=lambda _seconds: None,
        now=NOW,
    )

    offer = get_offer(conn, "example:summer-internship")
    assert offer is not None
    assert offer.mention_count == 2
    assert offer.provider == "Example"
    assert offer.program_name == "Summer internship"
    assert offer.best_score == 9
    assert offer.amount == "1000 TL"
    keys = {row.offer_key for row in query_findings(conn)}
    assert keys == {"example:summer-internship"}


def test_a_new_mention_updates_the_offer(conn: sqlite3.Connection) -> None:
    """Breaks if a repeat mention is stored twice, or a new stipend is ignored."""
    first = (
        "Example opens its summer internship",
        "https://example.com/intern-a",
        "internship applications for students",
    )
    repeat = (
        "Yet another note on the Example internship",
        "https://example.com/intern-c",
        "internship again",
    )
    raised = (
        "Example raised the internship stipend",
        "https://example.com/intern-d",
        "internship stipend raised to 5000",
    )
    current = {"feed": _rss([first])}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=current["feed"])

    config = _feed_config()
    shared: dict[str, object] = {
        "profile": PROFILE,
        "score": _score_same_offer,
        "environ": {},
        "transport": httpx.MockTransport(handler),
        "sleeper": lambda _seconds: None,
        "force": True,
    }
    run_sweep(config, conn, now=NOW, **shared)  # type: ignore[arg-type]

    current["feed"] = _rss([first, repeat])
    run_sweep(config, conn, now=NOW + timedelta(days=1), **shared)  # type: ignore[arg-type]
    offer = get_offer(conn, "example:summer-internship")
    assert offer is not None
    assert offer.mention_count == 2
    assert offer.amount == "1000 TL"

    current["feed"] = _rss([first, repeat, raised])
    run_sweep(config, conn, now=NOW + timedelta(days=2), **shared)  # type: ignore[arg-type]
    offer = get_offer(conn, "example:summer-internship")
    assert offer is not None
    assert offer.amount == "5000 TL"
    assert offer.mention_count == 3


def test_discovery_keyword_gate_is_stricter_than_a_provider_page(
    conn: sqlite3.Connection,
) -> None:
    """Breaks if one body hit is stored from search, or dropped from a feed."""
    body_only = (
        "Office hours notes",
        "https://publisher.example/notes",
        "There is an internship in the fine print.",
    )
    title_hit = (
        "Internship recap",
        "https://publisher.example/recap",
        "A short note.",
    )
    news = _rss([body_only, title_hit])
    page = _rss(
        [
            (
                "Office hours notes",
                "https://example.com/page",
                "There is an internship in the fine print.",
            )
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        host = request.url.host
        if host == "news.google.com":
            return httpx.Response(200, content=news)
        return httpx.Response(200, content=page)

    summary = run_sweep(
        Config(
            sources=[
                Source(
                    id="news",
                    name="news",
                    type="news_search",
                    frequency="daily",
                    queries=["internship"],
                    lang="en-US",
                ),
                Source(
                    id="feed",
                    name="feed",
                    url="https://example.com/feed.xml",
                    type="rss",
                    frequency="daily",
                ),
            ],
            settings=_settings(keywords=["internship"]),
        ),
        conn,
        profile=PROFILE,
        no_llm=True,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _seconds: None,
        now=NOW,
    )

    rows = query_findings(conn)
    assert summary.new == 2
    assert {(row.source_id, row.title) for row in rows} == {
        ("news", "Internship recap"),
        ("feed", "Office hours notes"),
    }


def _transport(feed: bytes) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=feed)

    return httpx.MockTransport(handler)
