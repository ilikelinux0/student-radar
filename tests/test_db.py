"""SQLite layer: due sources, hashes, filters, and idempotent migrations."""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest

import radar.db
from radar.db import (
    connect,
    due_sources,
    get_snapshot,
    get_source_state,
    insert_finding,
    migrate,
    open_database,
    query_findings,
    save_snapshot,
    set_status,
    update_score,
    upsert_source_state,
)
from radar.models import (
    FindingQuery,
    NewFinding,
    ScoreUpdate,
    Settings,
    Snapshot,
    Source,
    SourceState,
)

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)
WEEK = timedelta(days=7)
SLACK = timedelta(hours=2)


def _source(
    source_id: str,
    frequency: Literal["daily", "weekly"] = "daily",
    *,
    enabled: bool = True,
) -> Source:
    return Source(
        id=source_id,
        name=source_id,
        url="https://example.com/replace-me",
        type="rss",
        frequency=frequency,
        enabled=enabled,
    )


def _finding(
    content_hash: str,
    *,
    source_id: str = "s1",
    title: str = "Credits",
    url: str = "https://example.com/replace-me",
    excerpt: str = "excerpt",
    found_at: datetime = NOW,
    keyword_hits: list[str] | None = None,
) -> NewFinding:
    return NewFinding(
        source_id=source_id,
        title=title,
        url=url,
        content_hash=content_hash,
        excerpt=excerpt,
        found_at=found_at,
        keyword_hits=["credits"] if keyword_hits is None else keyword_hits,
    )


def _settings() -> Settings:
    return Settings(
        max_llm_items_per_run=40,
        keywords=["credits"],
        user_agent="StudentRadar/0.1 (personal research bot)",
        request_delay_seconds=2,
    )


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "findings.db")
    try:
        yield connection
    finally:
        connection.close()


def test_migrate_twice_keeps_one_row_per_version(conn: sqlite3.Connection) -> None:
    """Breaks if a second startup applies a migration again."""
    migrate(conn)
    migrate(conn)

    rows = conn.execute("SELECT version FROM schema_version").fetchall()
    assert [row["version"] for row in rows] == [1, 2, 3, 4, 5]
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {
        "sources_state",
        "snapshots",
        "findings",
        "offers",
        "transcript_checks",
        "schema_version",
    } <= (tables)


def test_failed_migration_leaves_no_half_applied_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Breaks if a migration that fails midway keeps its first statements."""
    path = tmp_path / "findings.db"
    connect(path).close()
    monkeypatch.setattr(
        radar.db,
        "_MIGRATIONS",
        (
            *radar.db._MIGRATIONS,
            (99, ("CREATE TABLE half (x TEXT)", "INSERT INTO missing VALUES (1)")),
        ),
    )

    with pytest.raises(sqlite3.OperationalError):
        connect(path)

    check = sqlite3.connect(path)
    try:
        names = {row[0] for row in check.execute("SELECT name FROM sqlite_master")}
        version = check.execute("SELECT MAX(version) FROM schema_version").fetchone()
    finally:
        check.close()
    assert "half" not in names
    assert version == (5,)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT 1 FROM findings WHERE url = ?",
        "SELECT id FROM findings WHERE offer_key = ? ORDER BY found_at DESC",
    ],
)
def test_per_item_lookups_use_an_index(conn: sqlite3.Connection, query: str) -> None:
    """Breaks if the URL dedupe or the alert subquery scans every finding."""
    plan = " ".join(
        str(row[3]) for row in conn.execute(f"EXPLAIN QUERY PLAN {query}", ("x",))
    )

    assert "USING INDEX" in plan or "USING COVERING INDEX" in plan


def test_open_database_creates_the_default_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Breaks if the default path is wrong or the data folder is not created."""
    monkeypatch.chdir(tmp_path)

    connection = open_database(_settings())
    try:
        assert Path("data/radar.db").is_file()
    finally:
        connection.close()


def test_never_checked_source_is_due(conn: sqlite3.Connection) -> None:
    """Breaks if a source with no state row waits for a full interval."""
    due = due_sources(conn, [_source("daily-new"), _source("off", enabled=False)], NOW)

    assert [source.id for source in due] == ["daily-new"]


def test_daily_source_becomes_due_two_hours_early(conn: sqlite3.Connection) -> None:
    """Breaks if a cron run that starts earlier than yesterday's skips the day."""
    upsert_source_state(
        conn,
        SourceState("daily", NOW - DAY + SLACK, "ok", None, 0),
    )
    upsert_source_state(
        conn,
        SourceState(
            "early", NOW - DAY + SLACK + timedelta(microseconds=1), "ok", None, 0
        ),
    )

    due = due_sources(conn, [_source("daily"), _source("early")], NOW)

    assert [source.id for source in due] == ["daily"]


def test_weekly_source_becomes_due_two_hours_early(conn: sqlite3.Connection) -> None:
    """Breaks if the weekly window waits a whole extra day for a late cron."""
    upsert_source_state(conn, SourceState("weekly", NOW - WEEK + SLACK, "ok", None, 0))
    upsert_source_state(
        conn,
        SourceState(
            "early", NOW - WEEK + SLACK + timedelta(microseconds=1), "ok", None, 0
        ),
    )

    due = due_sources(
        conn,
        [_source("weekly", "weekly"), _source("early", "weekly")],
        NOW,
    )

    assert [source.id for source in due] == ["weekly"]


def test_force_includes_enabled_sources_inside_the_window(
    conn: sqlite3.Connection,
) -> None:
    """Breaks if --force rechecks disabled sources or skips a fresh enabled one."""
    upsert_source_state(conn, SourceState("fresh", NOW, "ok", None, 0))

    due = due_sources(
        conn,
        [_source("fresh"), _source("off", enabled=False)],
        NOW,
        force=True,
    )

    assert [source.id for source in due] == ["fresh"]


def test_duplicate_content_hash_is_ignored_across_sources(
    conn: sqlite3.Connection,
) -> None:
    """Breaks if the same hash can be stored twice, including from another source."""
    first = _finding("same", keyword_hits=["credits", "ücretsiz"])
    second = _finding("same", source_id="s2", title="Other copy")

    assert insert_finding(conn, first) is True
    assert insert_finding(conn, second) is False

    rows = query_findings(conn)
    assert len(rows) == 1
    assert rows[0].source_id == "s1"
    assert rows[0].keyword_hits == ["credits", "ücretsiz"]
    assert rows[0].status == "new"
    assert rows[0].score is None


def test_query_filters_score_category_source_and_status(
    conn: sqlite3.Connection,
) -> None:
    """Breaks if a filter matches the wrong finding or includes an unscored row."""
    insert_finding(
        conn, _finding("high", source_id="s1", title="Cloud grant", found_at=NOW)
    )
    insert_finding(
        conn,
        _finding("low", source_id="s2", title="Shoe sale", found_at=NOW - DAY),
    )
    insert_finding(
        conn, _finding("plain", source_id="s1", title="Later", found_at=NOW - WEEK)
    )
    high_id = query_findings(conn, FindingQuery(text="Cloud"))[0].id
    low_id = query_findings(conn, FindingQuery(text="Shoe"))[0].id
    update_score(conn, high_id, ScoreUpdate(9, "grant", None, None, "A grant", "Fits."))
    update_score(conn, low_id, ScoreUpdate(4, "discount", "$10", None, "A sale", "No."))
    set_status(conn, low_id, "ignored")

    assert [
        row.content_hash for row in query_findings(conn, FindingQuery(min_score=8))
    ] == ["high"]
    assert [
        row.content_hash
        for row in query_findings(conn, FindingQuery(category="discount"))
    ] == ["low"]
    assert [
        row.content_hash for row in query_findings(conn, FindingQuery(source_id="s2"))
    ] == ["low"]
    assert [
        row.content_hash for row in query_findings(conn, FindingQuery(status="ignored"))
    ] == ["low"]


def test_date_range_is_inclusive_and_newest_first(conn: sqlite3.Connection) -> None:
    """Breaks if a boundary timestamp is dropped or older rows sort first."""
    insert_finding(conn, _finding("old", found_at=NOW - WEEK))
    insert_finding(conn, _finding("mid", found_at=NOW - DAY))
    insert_finding(conn, _finding("new", found_at=NOW))

    ranged = query_findings(conn, FindingQuery(start=NOW - DAY, end=NOW - DAY))
    assert [row.content_hash for row in ranged] == ["mid"]
    assert [row.content_hash for row in query_findings(conn)] == ["new", "mid", "old"]


def test_text_search_is_literal_and_case_insensitive(conn: sqlite3.Connection) -> None:
    """Breaks if search is case-sensitive, skips excerpts, or treats % as a wildcard."""
    insert_finding(conn, _finding("hit", title="Hello", excerpt="contains hibe here"))
    insert_finding(conn, _finding("other", title="100% off", excerpt="nope"))

    assert [
        row.content_hash for row in query_findings(conn, FindingQuery(text="HIBE"))
    ] == ["hit"]
    assert [
        row.content_hash for row in query_findings(conn, FindingQuery(text="%"))
    ] == ["other"]
    query_findings(conn, FindingQuery(text="'; DROP TABLE findings; --"))
    assert [row.content_hash for row in query_findings(conn)] == ["other", "hit"]


def test_snapshot_keeps_only_the_latest(conn: sqlite3.Connection) -> None:
    """Breaks if a source keeps an older snapshot or grows a history."""
    save_snapshot(conn, Snapshot("s1", "h1", "version one", NOW - DAY))
    save_snapshot(conn, Snapshot("s1", "h2", "version two", NOW))

    snapshot = get_snapshot(conn, "s1")
    assert snapshot == Snapshot("s1", "h2", "version two", NOW)
    assert get_snapshot(conn, "missing") is None


def test_source_state_upsert_replaces_fields(conn: sqlite3.Connection) -> None:
    """Breaks if a second state write leaves the old error or failure count."""
    upsert_source_state(conn, SourceState("s1", NOW - DAY, "error", "TimeoutError", 2))
    upsert_source_state(conn, SourceState("s1", NOW, "ok", None, 0))

    assert get_source_state(conn, "s1") == SourceState("s1", NOW, "ok", None, 0)
    assert get_source_state(conn, "missing") is None


def test_set_status_rejects_unknown_and_missing(conn: sqlite3.Connection) -> None:
    """Breaks if a bad status is stored or a missing id fails silently."""
    insert_finding(conn, _finding("one"))
    finding_id = query_findings(conn)[0].id

    set_status(conn, finding_id, "applied")
    assert query_findings(conn)[0].status == "applied"
    with pytest.raises(ValueError):
        set_status(conn, finding_id, "bogus")
    with pytest.raises(LookupError):
        set_status(conn, 999, "seen")


def test_migration_keeps_old_statuses(tmp_path: Path) -> None:
    """Breaks if a v1 finding status is dropped when offers take over."""
    path = tmp_path / "v1.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        """
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        INSERT INTO schema_version (version, applied_at)
        VALUES (1, '2026-10-01T00:00:00+00:00');
        CREATE TABLE findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            content_hash TEXT NOT NULL UNIQUE,
            excerpt TEXT NOT NULL,
            found_at TEXT NOT NULL,
            keyword_hits TEXT NOT NULL,
            score INTEGER,
            category TEXT,
            amount TEXT,
            deadline TEXT,
            summary TEXT,
            reasoning TEXT,
            emailed_at TEXT,
            status TEXT NOT NULL DEFAULT 'new'
        );
        """
    )
    raw.executemany(
        """
        INSERT INTO findings (
            source_id, title, url, content_hash, excerpt, found_at,
            keyword_hits, score, status
        ) VALUES (?, ?, ?, ?, ?, ?, '[]', ?, ?)
        """,
        [
            (
                "s1",
                "Applied offer",
                "https://example.com/applied",
                "appliedhash",
                "credits",
                "2026-10-01T00:00:00+00:00",
                9,
                "applied",
            ),
            (
                "s1",
                "Ignored offer",
                "https://example.com/ignored",
                "ignoredhash",
                "credits",
                "2026-10-02T00:00:00+00:00",
                8,
                "ignored",
            ),
        ],
    )
    raw.commit()
    raw.close()

    conn = connect(path)
    try:
        rows = {row.content_hash: row for row in query_findings(conn)}
        assert rows["appliedhash"].status == "applied"
        assert rows["appliedhash"].offer_key == "legacy:appliedhash"
        assert rows["ignoredhash"].status == "ignored"
        columns = [info["name"] for info in conn.execute("PRAGMA table_info(findings)")]
        assert "status" not in columns
        assert "offer_key" in columns
    finally:
        conn.close()


def test_migration_5_keeps_rows_ids_and_indexes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Breaks if rebuilding findings drops rows, renumbers ids, or loses an index."""
    path = tmp_path / "findings.db"
    with monkeypatch.context() as patch:
        patch.setattr(radar.db, "_MIGRATIONS", radar.db._MIGRATIONS[:4])
        old = connect(path)
        insert_finding(old, _finding("a"))
        insert_finding(old, _finding("b", url="https://example.com/b"))
        set_status(old, 2, "applied")
        old.execute(
            "UPDATE findings SET score = 9, category = 'credits', amount = '$1' "
            "WHERE id = 1"
        )
        old.commit()
        old.close()

    conn = connect(path)
    try:
        rows = query_findings(conn)
        indexes = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        offer_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(offers)")
        }
        insert_finding(conn, _finding("c", url="https://example.com/c"))
        newest = conn.execute("SELECT MAX(id) FROM findings").fetchone()[0]
    finally:
        conn.close()

    assert sorted((row.id, row.content_hash) for row in rows) == [(1, "a"), (2, "b")]
    by_hash = {row.content_hash: row for row in rows}
    assert by_hash["a"].score == 9
    assert by_hash["a"].amount == "$1"
    assert by_hash["b"].status == "applied"
    assert {"findings_url", "findings_offer"} <= indexes
    assert "closed_at" in offer_columns
    assert newest == 3


def test_personal_category_and_details_are_stored(conn: sqlite3.Connection) -> None:
    """Breaks if the old category CHECK rejects a personal category."""
    insert_finding(conn, _finding("a"))

    update_score(
        conn,
        1,
        ScoreUpdate(score=8, category="internship", details='{"remote": true}'),
    )

    row = query_findings(conn)[0]
    assert row.category == "internship"
    assert row.details == '{"remote": true}'


def test_unknown_category_is_still_rejected(conn: sqlite3.Connection) -> None:
    """Breaks if dropping the CHECK lets any string through update_score."""
    insert_finding(conn, _finding("a"))

    with pytest.raises(ValueError, match="invalid category"):
        update_score(conn, 1, ScoreUpdate(score=8, category="lottery"))
