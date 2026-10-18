"""Static JSON snapshot written at the end of a sweep. No network."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import httpx
import jsonschema
import pytest

from radar.db import connect
from radar.export import publish_snapshot
from radar.models import Config, PersonalScore, Settings, Source
from radar.scorer import ScoreOutcome, ScoringItem
from radar.sweep import run_sweep
from tests.profile_sample import sample_profile

PROFILE = sample_profile()

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
SCHEMA = json.loads(
    (Path(__file__).parent / "fixtures" / "snapshot.schema.json").read_text(
        encoding="utf-8"
    )
)
PREFIX = "startup credits "
LONG = PREFIX + ("x" * (600 - len(PREFIX))) + "ENDMARKER"


def _settings() -> Settings:
    return Settings(
        max_llm_items_per_run=40,
        keywords=["credits"],
        user_agent="StudentRadar/0.1 (personal research bot)",
        request_delay_seconds=0,
        scorer_provider="bedrock",
    )


def _rss(items: list[tuple[str, str, str]]) -> bytes:
    body = "".join(
        f"<item><title>{title}</title><link>{url}</link>"
        f"<description>{summary}</description></item>"
        for title, url, summary in items
    )
    return (
        f"<?xml version='1.0'?><rss version='2.0'><channel>{body}</channel></rss>"
    ).encode()


def _score(
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
        high = item.url.endswith("/high")
        outcomes.append(
            ScoreOutcome(
                score=PersonalScore(
                    fit=9 if high else 4,
                    category="grant",
                    value="$1000" if high else None,
                    summary="Credits for startups.",
                    reasoning="REASONTOKEN",
                    offer_key="example:credits",
                    org="Example",
                    program_name="Credits",
                ),
                attempted=True,
            )
        )
    return outcomes


def _validate(document: object, name: str) -> None:
    jsonschema.Draft202012Validator(SCHEMA[name]).validate(document)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "findings.db")
    yield connection
    connection.close()


def test_sweep_writes_a_schema_valid_snapshot(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """Breaks if the snapshot drops a field, leaks a body, or skips the schema."""
    feed = _rss(
        [
            ("Lower note", "https://example.com/low", "startup credits short"),
            ("Best note", "https://example.com/high", LONG),
        ]
    )
    destination = tmp_path / "web" / "public" / "data"

    run_sweep(
        Config(
            sources=[
                Source(
                    id="feed",
                    name="Feed",
                    url="https://example.com/feed.xml",
                    type="rss",
                    frequency="daily",
                    tags=["ai", "cloud"],
                ),
                Source(
                    id="idle",
                    name="Idle",
                    url="https://example.com/idle",
                    type="scrape",
                    frequency="weekly",
                    tags=["turkey"],
                    enabled=False,
                ),
            ],
            settings=_settings(),
        ),
        conn,
        profile=PROFILE,
        score=_score,
        environ={"GEMINI_API_KEY": "secret-pass"},
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                404 if request.url.path == "/robots.txt" else 200,
                content=b"" if request.url.path == "/robots.txt" else feed,
            )
        ),
        sleeper=lambda _seconds: None,
        now=NOW,
        export_dir=destination,
    )

    offers_path = destination / "offers.json"
    sources_path = destination / "sources.json"
    offers = json.loads(offers_path.read_text(encoding="utf-8"))
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    _validate(offers, "offers")
    _validate(sources, "sources")

    assert offers["meta"]["generated_at"] == NOW.isoformat(timespec="microseconds")
    assert offers["meta"]["counts"] == {"offers": 1, "mentions": 2, "sources": 2}
    assert sources["meta"] == offers["meta"]
    offer = offers["offers"][0]
    assert offer["offer_key"] == "example:credits"
    assert offer["provider"] == "Example"
    assert offer["best_score"] == 9
    assert offer["amount"] == "$1000"
    assert offer["mention_count"] == 2
    best = offer["best_mention"]
    assert best["title"] == "Best note"
    assert best["source"] == "feed"
    assert best["url"] == "https://example.com/high"
    assert best["score"] == 9
    assert best["excerpt"] == LONG[:600]
    assert best["summary"] == "Credits for startups."
    assert best["reasoning"] == "REASONTOKEN"
    assert offer["category"] == "grant"
    assert [item["title"] for item in offer["mentions"]] == ["Best note", "Lower note"]
    assert set(offer["mentions"][0]) == {"title", "source", "url", "date", "score"}
    listed = {row["id"]: row for row in sources["sources"]}
    assert listed["feed"]["category"] == "ai"
    assert listed["feed"]["type"] == "rss"
    assert listed["feed"]["status"] == "ok"
    assert listed["feed"]["failures"] == 0
    assert listed["feed"]["items_found_last_run"] == 2
    assert listed["feed"]["last_checked"] == NOW.isoformat(timespec="microseconds")
    assert listed["idle"]["items_found_last_run"] is None
    assert listed["idle"]["status"] is None
    blob = offers_path.read_text(encoding="utf-8") + sources_path.read_text(
        encoding="utf-8"
    )
    assert "ENDMARKER" not in blob
    assert "secret-pass" not in blob
    assert "REASONTOKEN" in offers_path.read_text(encoding="utf-8")
    assert "REASONTOKEN" not in sources_path.read_text(encoding="utf-8")


def test_unscored_findings_are_listed_on_their_own(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """Breaks if a keyword hit without a score is left out of the reader."""
    feed = _rss([("Startup credits", "https://example.com/a", "startup credits")])
    destination = tmp_path / "data"

    run_sweep(
        Config(
            sources=[
                Source(
                    id="feed",
                    name="Feed",
                    url="https://example.com/feed.xml",
                    type="rss",
                    frequency="daily",
                )
            ],
            settings=_settings().model_copy(update={"scorer_provider": "none"}),
        ),
        conn,
        profile=PROFILE,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                404 if request.url.path == "/robots.txt" else 200,
                content=b"" if request.url.path == "/robots.txt" else feed,
            )
        ),
        sleeper=lambda _seconds: None,
        now=NOW,
        export_dir=destination,
    )

    offers = json.loads((destination / "offers.json").read_text(encoding="utf-8"))
    _validate(offers, "offers")
    assert offers["meta"]["counts"]["offers"] == 1
    assert offers["meta"]["counts"]["mentions"] == 1
    offer = offers["offers"][0]
    content_hash = conn.execute("SELECT content_hash FROM findings").fetchone()[0]
    assert offer["offer_key"] == f"legacy:{content_hash}"
    assert offer["provider"] is None
    assert offer["best_score"] is None
    assert offer["mention_count"] == 1
    assert offer["status"] == "new"
    assert offer["first_seen_at"] == NOW.isoformat(timespec="microseconds")
    assert offer["best_mention"]["title"] == "Startup credits"
    assert offer["best_mention"]["excerpt"] == "startup credits"
    assert [row["url"] for row in offer["mentions"]] == ["https://example.com/a"]


def test_dry_run_prints_the_export_and_writes_no_files(
    conn: sqlite3.Connection,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Breaks if a dry run creates offers.json or sources.json."""
    destination = tmp_path / "data"
    feed = _rss([("Startup credits", "https://example.com/a", "startup credits")])

    run_sweep(
        Config(
            sources=[
                Source(
                    id="feed",
                    name="Feed",
                    url="https://example.com/feed.xml",
                    type="rss",
                    frequency="daily",
                )
            ],
            settings=_settings(),
        ),
        conn,
        profile=PROFILE,
        dry_run=True,
        no_llm=True,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                404 if request.url.path == "/robots.txt" else 200,
                content=b"" if request.url.path == "/robots.txt" else feed,
            )
        ),
        sleeper=lambda _seconds: None,
        now=NOW,
        export_dir=destination,
    )

    assert not (destination / "offers.json").exists()
    assert not (destination / "sources.json").exists()
    printed = capsys.readouterr().out
    assert f"would write {destination / 'offers.json'}" in printed
    assert "offers=0" in printed
    assert "sources=1" in printed


def test_unchecked_source_keeps_the_previous_item_count(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """Breaks if a later sweep clears items_found_last_run for a source it skipped."""
    destination = tmp_path / "data"
    config = Config(
        sources=[
            Source(
                id="feed",
                name="Feed",
                url="https://example.com/feed.xml",
                type="rss",
                frequency="weekly",
                tags=["infra"],
            )
        ],
        settings=_settings(),
    )
    publish_snapshot(
        config,
        conn,
        generated_at=NOW,
        duration_seconds=0.5,
        items_found={"feed": 4},
        destination=destination,
        dry_run=False,
        profile=PROFILE,
    )
    publish_snapshot(
        config,
        conn,
        generated_at=NOW,
        duration_seconds=0.1,
        items_found={},
        destination=destination,
        dry_run=False,
        profile=PROFILE,
    )

    sources = json.loads((destination / "sources.json").read_text(encoding="utf-8"))
    _validate(sources, "sources")
    assert sources["sources"][0]["items_found_last_run"] == 4

    publish_snapshot(
        config,
        conn,
        generated_at=NOW,
        duration_seconds=0.1,
        items_found={"feed": 0},
        destination=destination,
        dry_run=False,
        profile=PROFILE,
    )
    sources = json.loads((destination / "sources.json").read_text(encoding="utf-8"))
    assert sources["sources"][0]["items_found_last_run"] == 0
