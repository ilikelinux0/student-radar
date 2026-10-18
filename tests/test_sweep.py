"""Sweep pipeline against a temp database. No network or model."""

import logging
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from radar.db import (
    connect,
    get_snapshot,
    get_source_state,
    insert_finding,
    query_findings,
)
from radar.models import Config, NewFinding, PersonalScore, Settings, Source
from radar.scorer import ScoreOutcome, ScoringItem, score_items
from radar.sweep import SCORING_FAILED, main, run_sweep
from tests.profile_sample import sample_profile

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
PROFILE = sample_profile()


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "max_llm_items_per_run": 40,
        "keywords": ["credits", "grant"],
        "user_agent": "StudentRadar/0.1 (personal research bot)",
        "request_delay_seconds": 0,
        "scorer_provider": "bedrock",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _source(source_id: str, url: str, kind: str = "rss") -> Source:
    return Source(
        id=source_id,
        name=source_id,
        url=url,
        type=kind,  # type: ignore[arg-type]
        frequency="daily",
        enabled=True,
    )


def _config(*sources: Source, **settings: object) -> Config:
    return Config(sources=list(sources), settings=_settings(**settings))


def _named_score(item: object, **overrides: object) -> PersonalScore:
    assert isinstance(item, ScoringItem)
    tail = item.url.rstrip("/").rsplit("/", 1)[-1]
    program = re.sub(r"[^a-z0-9]+", "-", tail.lower()).strip("-") or "item"
    values: dict[str, object] = {
        "fit": 9,
        "category": "grant",
        "value": "$1000",
        "summary": "A grant for students.",
        "reasoning": "Matches the stated goals.",
        "offer_key": f"example:{program}",
        "org": "Example",
        "program_name": program,
    }
    values.update(overrides)
    return PersonalScore(**values)  # type: ignore[arg-type]


def _score_high(
    items: object,
    settings: Settings,
    client: object,
    *,
    environ: object = None,
    spec: object = None,
) -> list[ScoreOutcome]:
    del settings, client, environ, spec
    assert isinstance(items, list)
    return [ScoreOutcome(score=_named_score(item), attempted=True) for item in items]


def _fixture_transport(broken: bool = False) -> httpx.MockTransport:
    feed = (FIXTURES / "feed.xml").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if broken and request.url.path == "/broken":
            return httpx.Response(500)
        return httpx.Response(200, content=feed)

    return httpx.MockTransport(handler)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "findings.db")
    yield connection
    connection.close()


def test_run_scores_fixture_feed(
    conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    """Breaks if a fixture item is dropped or scored wrong."""
    caplog.set_level(logging.INFO)
    summary = run_sweep(
        _config(_source("feed", "https://example.com/feed.xml")),
        conn,
        profile=PROFILE,
        score=_score_high,
        environ={},
        transport=_fixture_transport(),
        sleeper=lambda _seconds: None,
        now=NOW,
    )

    assert summary.checked == 1
    assert summary.failed == 0
    assert summary.new == 2
    assert summary.scored == 2
    rows = query_findings(conn)
    assert [row.title for row in rows] == [
        "Grant deadline posted",
        "Startup credits expanded",
    ]
    assert rows[0].score == 9
    assert rows[0].category == "grant"
    assert rows[0].amount == "$1000"
    assert rows[1].keyword_hits == ["credits"]
    assert "checked=1" in caplog.text
    assert "failed=0" in caplog.text
    assert "new=2" in caplog.text
    assert "scored=2" in caplog.text
    state = get_source_state(conn, "feed")
    assert state is not None
    assert state.last_status == "ok"
    assert state.consecutive_failures == 0


def test_failing_source_does_not_stop_others(
    conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    """Breaks if one fetch error aborts the run or skips the healthy source."""
    caplog.set_level(logging.INFO)
    summary = run_sweep(
        _config(
            _source("broken", "https://example.com/broken"),
            _source("feed", "https://example.com/feed.xml"),
        ),
        conn,
        profile=PROFILE,
        score=_score_high,
        transport=_fixture_transport(broken=True),
        sleeper=lambda _seconds: None,
        now=NOW,
    )

    assert summary.checked == 2
    assert summary.failed == 1
    assert summary.new == 2
    assert [row.source_id for row in query_findings(conn)] == ["feed", "feed"]
    state = get_source_state(conn, "broken")
    assert state is not None
    assert state.last_status == "error"
    assert state.last_error is not None
    assert state.last_error.startswith("FetchError:")
    assert "secret" not in state.last_error
    assert state.consecutive_failures == 1
    assert state.last_checked_at == NOW
    assert "failed=1" in caplog.text

    run_sweep(
        _config(
            _source("broken", "https://example.com/broken"),
            _source("feed", "https://example.com/feed.xml"),
        ),
        conn,
        profile=PROFILE,
        score=_score_high,
        force=True,
        transport=_fixture_transport(broken=True),
        sleeper=lambda _seconds: None,
        now=NOW,
    )
    again = get_source_state(conn, "broken")
    assert again is not None
    assert again.consecutive_failures == 2


def test_dry_run_writes_nothing(
    conn: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    """Breaks if a dry run writes or scores."""

    def explode(*_args: object, **_kwargs: object) -> list[ScoreOutcome]:
        raise AssertionError("scorer called")

    summary = run_sweep(
        _config(_source("feed", "https://example.com/feed.xml")),
        conn,
        profile=PROFILE,
        dry_run=True,
        score=explode,
        transport=_fixture_transport(),
        sleeper=lambda _seconds: None,
        now=NOW,
    )

    assert query_findings(conn) == []
    assert get_source_state(conn, "feed") is None
    assert summary.new == 2
    assert summary.scored == 0
    printed = capsys.readouterr().out
    assert "would insert" in printed
    assert "Startup credits expanded" in printed
    assert "Grant deadline posted" in printed


def test_gave_up_score_is_not_queued_again(conn: sqlite3.Connection) -> None:
    """Breaks if a failed score is sent to the model again on the next run."""
    calls = {"n": 0}

    def give_up(
        items: object,
        settings: Settings,
        client: object,
        *,
        environ: object = None,
        spec: object = None,
    ) -> list[ScoreOutcome]:
        del settings, client, environ, spec
        assert isinstance(items, list)
        calls["n"] += len(items)
        return [ScoreOutcome(score=None, attempted=True) for _item in items]

    config = _config(_source("feed", "https://example.com/feed.xml"))
    shared: dict[str, object] = {
        "score": give_up,
        "transport": _fixture_transport(),
        "sleeper": lambda _seconds: None,
        "now": NOW,
    }
    run_sweep(config, conn, profile=PROFILE, **shared)  # type: ignore[arg-type]
    run_sweep(config, conn, profile=PROFILE, force=True, **shared)  # type: ignore[arg-type]

    assert calls["n"] == 2
    rows = query_findings(conn)
    assert rows
    assert all(row.score is None for row in rows)
    assert all(row.summary == SCORING_FAILED for row in rows)


def test_keyword_miss_is_not_stored(conn: sqlite3.Connection) -> None:
    """Breaks if an item with no keyword is stored or scored."""
    seen: list[str] = []

    def score(
        items: object,
        settings: Settings,
        client: object,
        *,
        environ: object = None,
        spec: object = None,
    ) -> list[ScoreOutcome]:
        del settings, client, environ, spec
        assert isinstance(items, list)
        seen.extend(item.excerpt for item in items)  # type: ignore[attr-defined]
        return _score_high(items, _settings(), None)

    run_sweep(
        _config(
            _source("feed", "https://example.com/feed.xml"),
            keywords=["credits"],
        ),
        conn,
        profile=PROFILE,
        score=score,
        transport=_fixture_transport(),
        sleeper=lambda _seconds: None,
        now=NOW,
    )

    rows = query_findings(conn)
    assert [row.title for row in rows] == ["Startup credits expanded"]
    assert seen == ["Providers added a free tier for new startups."]


def test_scrape_saves_snapshot_when_keywords_miss(conn: sqlite3.Connection) -> None:
    """Breaks if a keyword miss skips the snapshot and the page looks new next run."""
    page = """\
<!DOCTYPE html>
<html lang="en"><body><article>
<h1>Office hours</h1>
<p>The weather is mild today.</p>
</article></body></html>
"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=page)

    run_sweep(
        _config(
            _source("page", "https://example.com/page", "scrape"),
            keywords=["credits"],
        ),
        conn,
        profile=PROFILE,
        no_llm=True,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _seconds: None,
        now=NOW,
    )

    assert query_findings(conn) == []
    snapshot = get_snapshot(conn, "page")
    assert snapshot is not None
    assert "weather" in snapshot.text


def test_scrape_with_no_text_is_a_failed_check(conn: sqlite3.Connection) -> None:
    """Breaks if a JavaScript-only page with no text is reported as healthy."""
    shell = '<!DOCTYPE html><html><head><script src="/app.js"></script></head>'
    shell += "<body></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=shell)

    summary = run_sweep(
        _config(_source("page", "https://example.com/page", "scrape")),
        conn,
        profile=PROFILE,
        no_llm=True,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _seconds: None,
        now=NOW,
    )

    assert summary.failed == 1
    state = get_source_state(conn, "page")
    assert state is not None
    assert state.last_status == "error"
    assert state.last_error is not None and "no text" in state.last_error
    assert get_snapshot(conn, "page") is None


def test_scrape_baseline_is_stored_when_it_matches(
    conn: sqlite3.Connection,
) -> None:
    """Breaks if the first scrape drops a keyword hit or skips the snapshot."""
    page = (FIXTURES / "page_v1.html").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=page)

    run_sweep(
        _config(
            _source("page", "https://example.com/page", "scrape"),
            keywords=["credits"],
        ),
        conn,
        profile=PROFILE,
        score=_score_high,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _seconds: None,
        now=NOW,
    )

    rows = query_findings(conn)
    assert len(rows) == 1
    assert "credits" in rows[0].keyword_hits
    assert "promotional cloud credits" in rows[0].excerpt
    assert get_snapshot(conn, "page") is not None


def test_oldest_unscored_is_scored_first(conn: sqlite3.Connection) -> None:
    """Breaks if the budget spends itself on the newest row and leaves a backlog."""
    insert_finding(
        conn,
        NewFinding(
            source_id="feed",
            title="Older credits",
            url="https://example.com/old",
            content_hash="older-credits",
            excerpt="older credits offer",
            found_at=NOW - timedelta(days=2),
            keyword_hits=["credits"],
        ),
    )
    seen: list[str] = []

    def score(
        items: object,
        settings: Settings,
        client: object,
        *,
        environ: object = None,
        spec: object = None,
    ) -> list[ScoreOutcome]:
        del settings, client, environ, spec
        assert isinstance(items, list)
        seen.extend(item.excerpt for item in items)  # type: ignore[attr-defined]
        return _score_high(items, _settings(), None)

    run_sweep(
        _config(
            _source("feed", "https://example.com/feed.xml"),
            max_llm_items_per_run=1,
        ),
        conn,
        profile=PROFILE,
        score=score,
        transport=_fixture_transport(),
        sleeper=lambda _seconds: None,
        now=NOW,
    )

    assert seen == ["older credits offer"]
    fresh = [row for row in query_findings(conn) if row.title != "Older credits"]
    assert fresh
    assert all(row.score is None and row.summary is None for row in fresh)


def test_missing_model_id_leaves_rows_queued(conn: sqlite3.Connection) -> None:
    """Breaks if a missing model id crashes the run or flags the row as gave up."""
    summary = run_sweep(
        _config(_source("feed", "https://example.com/feed.xml")),
        conn,
        profile=PROFILE,
        score=score_items,
        environ={},
        transport=_fixture_transport(),
        sleeper=lambda _seconds: None,
        now=NOW,
    )

    assert summary.scored == 0
    assert summary.failed == 0
    rows = query_findings(conn)
    assert rows
    assert all(row.score is None and row.summary is None for row in rows)


def test_none_provider_stores_hits_without_scoring(conn: sqlite3.Connection) -> None:
    """Breaks if provider none calls a model or drops keyword hits."""

    def explode(
        items: object,
        settings: Settings,
        client: object,
        *,
        environ: object = None,
        spec: object = None,
    ) -> list[ScoreOutcome]:
        raise AssertionError(f"scorer called for {items} {settings} {client} {spec}")

    summary = run_sweep(
        _config(
            _source("feed", "https://example.com/feed.xml"),
            scorer_provider="none",
        ),
        conn,
        profile=PROFILE,
        score=explode,
        environ={},
        transport=_fixture_transport(),
        sleeper=lambda _seconds: None,
        now=NOW,
    )

    assert summary.scored == 0
    rows = query_findings(conn)
    assert rows
    assert all(
        row.score is None and row.summary is None and row.keyword_hits for row in rows
    )


def test_no_llm_stores_without_scoring(conn: sqlite3.Connection) -> None:
    """Breaks if --no-llm still calls the model or drops the finding."""

    def explode(*_args: object, **_kwargs: object) -> list[ScoreOutcome]:
        raise AssertionError("scorer called")

    summary = run_sweep(
        _config(_source("feed", "https://example.com/feed.xml")),
        conn,
        profile=PROFILE,
        no_llm=True,
        score=explode,
        transport=_fixture_transport(),
        sleeper=lambda _seconds: None,
        now=NOW,
    )

    assert summary.new == 2
    assert summary.scored == 0
    rows = query_findings(conn)
    assert len(rows) == 2
    assert all(row.score is None and row.summary is None for row in rows)


def test_cli_flags_reach_the_runner(tmp_path: Path) -> None:
    """Breaks if a CLI flag is parsed and then ignored."""
    seen: dict[str, object] = {}

    def runner(
        config: Config, connection: sqlite3.Connection, **kwargs: object
    ) -> object:
        del config
        connection.close()
        seen.update(kwargs)
        return None

    def loader() -> Config:
        return _config(_source("feed", "https://example.com/feed.xml"))

    def opener(_settings_obj: Settings) -> sqlite3.Connection:
        return connect(tmp_path / "findings.db")

    code = main(
        ["--dry-run", "--force", "--source", "feed", "--no-llm"],
        runner=runner,
        loader=loader,
        opener=opener,
        profile_loader=sample_profile,
    )

    assert code == 0
    assert seen["dry_run"] is True
    assert seen["force"] is True
    assert seen["source_id"] == "feed"
    assert seen["no_llm"] is True
    assert seen["profile"] == PROFILE


def test_unknown_source_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Breaks if an unknown --source id is treated as an empty successful run."""

    def loader() -> Config:
        return _config(_source("feed", "https://example.com/feed.xml"))

    def opener(_settings_obj: Settings) -> sqlite3.Connection:
        return connect(tmp_path / "findings.db")

    code = main(
        ["--source", "missing"],
        loader=loader,
        opener=opener,
        profile_loader=sample_profile,
    )

    assert code == 2
    assert "missing" in capsys.readouterr().err


def test_snapshot_goes_to_the_export_dir(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """Breaks if the snapshot ignores export_dir from settings."""
    target = tmp_path / "custom-snapshot"
    run_sweep(
        _config(
            _source("feed", "https://example.com/feed.xml"),
            export_dir=str(target),
        ),
        conn,
        profile=PROFILE,
        score=_score_high,
        transport=_fixture_transport(),
        sleeper=lambda _seconds: None,
        now=NOW,
    )

    assert (target / "offers.json").is_file()
    assert (target / "sources.json").is_file()


def test_dry_run_prints_turkish_titles_on_a_narrow_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Breaks if a cp1252 stdout makes a Turkish title fail the source."""
    import io

    raw = io.BytesIO()
    narrow = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr("sys.stdout", narrow)

    def runner(config: Config, connection: sqlite3.Connection, **_: object) -> None:
        del config
        connection.close()
        print("would insert Yazılım Stajyeri İlanı from youthall")

    def loader() -> Config:
        return _config()

    def opener(_settings_obj: Settings) -> sqlite3.Connection:
        return connect(tmp_path / "findings.db")

    code = main(
        ["--dry-run"],
        runner=runner,
        loader=loader,
        opener=opener,
        profile_loader=sample_profile,
    )
    narrow.flush()

    assert code == 0
    assert b"would insert Yaz" in raw.getvalue()


def test_cli_without_a_profile_skips_the_run() -> None:
    """Breaks if a run with no profile loads config, opens the database, or fails."""

    def explode(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("should not be called")

    code = main(
        ["--dry-run"],
        runner=explode,
        loader=explode,
        opener=explode,
        profile_loader=lambda: None,
    )

    assert code == 0
