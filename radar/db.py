"""SQLite schema, migrations, and queries."""

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from radar.models import (
    Finding,
    FindingQuery,
    FindingStatus,
    NewFinding,
    Offer,
    ScoreUpdate,
    Settings,
    Snapshot,
    Source,
    SourceState,
    SourceStatus,
)

_DAY = timedelta(days=1)
_WEEK = timedelta(days=7)
# GitHub starts a scheduled run late by minutes to an hour. Without slack, a
# run that starts earlier than yesterday's finds a daily source 23h old and
# skips it for a whole day.
_SLACK = timedelta(hours=2)
_STATUSES = frozenset({"new", "seen", "applied", "ignored"})
# PersonalScore limits the categories. The credits ones are from older databases.
_CATEGORIES = frozenset(
    {
        "credits",
        "discount",
        "grant",
        "program",
        "other",
        "internship",
        "job",
        "graduate_program",
        "training",
        "certification",
        "event",
        "competition",
    }
)
_SOURCE_STATUSES = frozenset({"ok", "error"})
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_FINDING_SELECT = """
SELECT
    f.id,
    f.source_id,
    f.title,
    f.url,
    f.content_hash,
    f.excerpt,
    f.found_at,
    f.keyword_hits,
    f.score,
    f.category,
    f.amount,
    f.deadline,
    f.summary,
    f.reasoning,
    f.emailed_at,
    f.offer_key,
    f.details,
    COALESCE(o.status, 'new') AS status
FROM findings AS f
LEFT JOIN offers AS o ON o.offer_key = f.offer_key
"""

_SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
)
"""

_MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        1,
        (
            """
            CREATE TABLE sources_state (
                source_id TEXT PRIMARY KEY,
                last_checked_at TEXT,
                last_status TEXT CHECK (
                    last_status IN ('ok', 'error') OR last_status IS NULL
                ),
                last_error TEXT,
                consecutive_failures INTEGER NOT NULL DEFAULT 0
                    CHECK (consecutive_failures >= 0)
            )
            """,
            """
            CREATE TABLE snapshots (
                source_id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                text TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                content_hash TEXT NOT NULL UNIQUE,
                excerpt TEXT NOT NULL,
                found_at TEXT NOT NULL,
                keyword_hits TEXT NOT NULL,
                score INTEGER CHECK (score IS NULL OR (score >= 0 AND score <= 10)),
                category TEXT CHECK (
                    category IS NULL OR category IN (
                        'credits', 'discount', 'grant', 'program', 'other'
                    )
                ),
                amount TEXT,
                deadline TEXT,
                summary TEXT,
                reasoning TEXT,
                emailed_at TEXT,
                status TEXT NOT NULL DEFAULT 'new' CHECK (
                    status IN ('new', 'seen', 'applied', 'ignored')
                )
            )
            """,
        ),
    ),
    (
        2,
        (
            """
            CREATE TABLE offers (
                offer_key TEXT PRIMARY KEY,
                provider TEXT,
                program_name TEXT,
                best_score INTEGER CHECK (
                    best_score IS NULL OR (best_score >= 0 AND best_score <= 10)
                ),
                amount TEXT,
                deadline TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                mention_count INTEGER NOT NULL DEFAULT 1
                    CHECK (mention_count >= 1),
                status TEXT NOT NULL DEFAULT 'new' CHECK (
                    status IN ('new', 'seen', 'applied', 'ignored')
                ),
                alerted_at TEXT,
                alerted_amount TEXT,
                alerted_deadline TEXT,
                confident_at TEXT
            )
            """,
            """
            INSERT INTO offers (
                offer_key, provider, program_name, best_score, amount, deadline,
                first_seen_at, last_seen_at, mention_count, status,
                alerted_at, alerted_amount, alerted_deadline, confident_at
            )
            SELECT
                'legacy:' || content_hash,
                NULL,
                NULL,
                score,
                amount,
                deadline,
                found_at,
                found_at,
                1,
                status,
                emailed_at,
                CASE WHEN emailed_at IS NOT NULL THEN amount ELSE NULL END,
                CASE WHEN emailed_at IS NOT NULL THEN deadline ELSE NULL END,
                CASE WHEN score IS NOT NULL THEN found_at ELSE NULL END
            FROM findings
            """,
            "ALTER TABLE findings ADD COLUMN offer_key TEXT",
            """
            UPDATE findings SET offer_key = 'legacy:' || content_hash
            """,
            "ALTER TABLE findings DROP COLUMN status",
        ),
    ),
    (
        3,
        (
            """
            CREATE TABLE transcript_checks (
                video_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                checked_at TEXT NOT NULL
            )
            """,
        ),
    ),
    (
        4,
        (
            "CREATE INDEX findings_url ON findings (url)",
            "CREATE INDEX findings_offer ON findings (offer_key, found_at)",
        ),
    ),
    (
        5,
        (
            # SQLite cannot drop a CHECK, so findings is rebuilt without the
            # company-only category list. Ids are copied, so offers and the
            # AUTOINCREMENT sequence carry over.
            """
            CREATE TABLE findings_v5 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                content_hash TEXT NOT NULL UNIQUE,
                excerpt TEXT NOT NULL,
                found_at TEXT NOT NULL,
                keyword_hits TEXT NOT NULL,
                score INTEGER CHECK (score IS NULL OR (score >= 0 AND score <= 10)),
                category TEXT,
                amount TEXT,
                deadline TEXT,
                summary TEXT,
                reasoning TEXT,
                emailed_at TEXT,
                offer_key TEXT,
                details TEXT
            )
            """,
            """
            INSERT INTO findings_v5 (
                id, source_id, title, url, content_hash, excerpt, found_at,
                keyword_hits, score, category, amount, deadline, summary,
                reasoning, emailed_at, offer_key
            )
            SELECT
                id, source_id, title, url, content_hash, excerpt, found_at,
                keyword_hits, score, category, amount, deadline, summary,
                reasoning, emailed_at, offer_key
            FROM findings
            """,
            "DROP TABLE findings",
            "ALTER TABLE findings_v5 RENAME TO findings",
            "CREATE INDEX findings_url ON findings (url)",
            "CREATE INDEX findings_offer ON findings (offer_key, found_at)",
            "ALTER TABLE offers ADD COLUMN closed_at TEXT",
        ),
    ),
)


def connect(path: Path) -> sqlite3.Connection:
    """Open the database at path, create parent folders, and migrate."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def open_database(settings: Settings) -> sqlite3.Connection:
    """Open settings.db_path. A relative path is resolved from the current directory."""
    return connect(Path(settings.db_path))


def migrate(conn: sqlite3.Connection) -> None:
    """Apply pending schema versions. Running this twice does not change the schema.

    Each version runs in one transaction, so a failure leaves the previous
    version intact instead of a half-built schema the next run cannot migrate.
    """
    conn.execute(_SCHEMA_VERSION)
    conn.commit()
    current = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_version"
    ).fetchone()
    version = 0 if current is None else int(current[0])
    for migration_version, statements in _MIGRATIONS:
        if migration_version <= version:
            continue
        conn.execute("BEGIN")
        try:
            for statement in statements:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (migration_version, _to_text(datetime.now(UTC))),
            )
        except BaseException:
            conn.rollback()
            raise
        conn.commit()


def upsert_source_state(conn: sqlite3.Connection, state: SourceState) -> None:
    """Insert or replace the check state for one source."""
    if state.last_status is not None and state.last_status not in _SOURCE_STATUSES:
        raise ValueError(f"invalid source status: {state.last_status}")
    checked_at = (
        None if state.last_checked_at is None else _to_text(state.last_checked_at)
    )
    conn.execute(
        """
        INSERT INTO sources_state (
            source_id, last_checked_at, last_status, last_error, consecutive_failures
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            last_checked_at = excluded.last_checked_at,
            last_status = excluded.last_status,
            last_error = excluded.last_error,
            consecutive_failures = excluded.consecutive_failures
        """,
        (
            state.source_id,
            checked_at,
            state.last_status,
            state.last_error,
            state.consecutive_failures,
        ),
    )
    conn.commit()


def get_source_state(conn: sqlite3.Connection, source_id: str) -> SourceState | None:
    """Return the stored check state, or None if this source has never been checked."""
    row = conn.execute(
        """
        SELECT source_id, last_checked_at, last_status, last_error, consecutive_failures
        FROM sources_state
        WHERE source_id = ?
        """,
        (source_id,),
    ).fetchone()
    if row is None:
        return None
    return _state_from_row(row)


def transcript_checked(conn: sqlite3.Connection, video_id: str) -> bool:
    """True when this video's transcript was already fetched."""
    if _VIDEO_ID.fullmatch(video_id) is None:
        return False
    row = conn.execute(
        "SELECT 1 FROM transcript_checks WHERE video_id = ?",
        (video_id,),
    ).fetchone()
    return row is not None


def remember_transcript(
    conn: sqlite3.Connection, video_id: str, source_id: str, checked_at: datetime
) -> None:
    """Remember a fetched transcript so a later run does not download it again."""
    if _VIDEO_ID.fullmatch(video_id) is None:
        raise ValueError("invalid video id")
    conn.execute(
        """
        INSERT INTO transcript_checks (video_id, source_id, checked_at)
        VALUES (?, ?, ?)
        ON CONFLICT(video_id) DO NOTHING
        """,
        (video_id, source_id, _to_text(checked_at)),
    )
    conn.commit()


def list_source_states(conn: sqlite3.Connection) -> list[SourceState]:
    """Return every source health row, ordered by source id."""
    rows = conn.execute(
        """
        SELECT source_id, last_checked_at, last_status, last_error, consecutive_failures
        FROM sources_state
        ORDER BY source_id
        """
    ).fetchall()
    return [_state_from_row(row) for row in rows]


def due_sources(
    conn: sqlite3.Connection,
    sources: list[Source],
    now: datetime,
    *,
    force: bool = False,
) -> list[Source]:
    """Return enabled sources that are due at now, in input order.

    Daily means 24 hours and weekly means 7 days, both rolling from
    last_checked_at in UTC, less two hours of slack for a late cron start.
    A missing row is due. Disabled sources are skipped even when force is true.
    """
    moment = _require_aware(now)
    due: list[Source] = []
    for source in sources:
        if not source.enabled:
            continue
        if force or _is_due(get_source_state(conn, source.id), source, moment):
            due.append(source)
    return due


def save_snapshot(conn: sqlite3.Connection, snapshot: Snapshot) -> None:
    """Store the latest snapshot for a source, replacing any previous one."""
    conn.execute(
        """
        INSERT INTO snapshots (source_id, content_hash, text, fetched_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            content_hash = excluded.content_hash,
            text = excluded.text,
            fetched_at = excluded.fetched_at
        """,
        (
            snapshot.source_id,
            snapshot.content_hash,
            snapshot.text,
            _to_text(snapshot.fetched_at),
        ),
    )
    conn.commit()


def get_snapshot(conn: sqlite3.Connection, source_id: str) -> Snapshot | None:
    """Return the latest snapshot for a source, or None."""
    row = conn.execute(
        """
        SELECT source_id, content_hash, text, fetched_at
        FROM snapshots
        WHERE source_id = ?
        """,
        (source_id,),
    ).fetchone()
    if row is None:
        return None
    return Snapshot(
        source_id=row["source_id"],
        content_hash=row["content_hash"],
        text=row["text"],
        fetched_at=_from_text(row["fetched_at"]),
    )


def insert_finding(conn: sqlite3.Connection, finding: NewFinding) -> bool:
    """Insert a finding. Return False when content_hash is already stored."""
    cursor = conn.execute(
        """
        INSERT INTO findings (
            source_id, title, url, content_hash, excerpt, found_at, keyword_hits
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(content_hash) DO NOTHING
        """,
        (
            finding.source_id,
            finding.title,
            finding.url,
            finding.content_hash,
            finding.excerpt,
            _to_text(finding.found_at),
            json.dumps(finding.keyword_hits, ensure_ascii=False),
        ),
    )
    conn.commit()
    return cursor.rowcount == 1


def update_score(conn: sqlite3.Connection, finding_id: int, score: ScoreUpdate) -> None:
    """Write scoring fields onto an existing finding."""
    if score.score is not None and not 0 <= score.score <= 10:
        raise ValueError("score must be between 0 and 10")
    if score.category is not None and score.category not in _CATEGORIES:
        raise ValueError(f"invalid category: {score.category}")
    cursor = conn.execute(
        """
        UPDATE findings
        SET score = ?, category = ?, amount = ?, deadline = ?,
            summary = ?, reasoning = ?, details = ?
        WHERE id = ?
        """,
        (
            score.score,
            score.category,
            score.amount,
            score.deadline,
            score.summary,
            score.reasoning,
            score.details,
            finding_id,
        ),
    )
    if cursor.rowcount == 0:
        raise LookupError(f"finding {finding_id} not found")
    conn.commit()


def set_status(conn: sqlite3.Connection, finding_id: int, status: str) -> None:
    """Set the offer status for a finding. Reject unknown statuses and missing ids."""
    if status not in _STATUSES:
        raise ValueError(f"invalid status: {status}")
    row = conn.execute(
        """
        SELECT id, offer_key, content_hash, found_at, score, amount, deadline
        FROM findings
        WHERE id = ?
        """,
        (finding_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"finding {finding_id} not found")
    key = row["offer_key"]
    if not key:
        key = f"legacy:{row['content_hash']}"
        confident = None if row["score"] is None else row["found_at"]
        conn.execute(
            """
            INSERT INTO offers (
                offer_key, best_score, amount, deadline,
                first_seen_at, last_seen_at, mention_count, status, confident_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, 'new', ?)
            ON CONFLICT(offer_key) DO NOTHING
            """,
            (
                key,
                row["score"],
                row["amount"],
                row["deadline"],
                row["found_at"],
                row["found_at"],
                confident,
            ),
        )
        conn.execute(
            "UPDATE findings SET offer_key = ? WHERE id = ?",
            (key, finding_id),
        )
    cursor = conn.execute(
        "UPDATE offers SET status = ? WHERE offer_key = ?",
        (status, key),
    )
    if cursor.rowcount == 0:
        raise LookupError(f"offer {key} not found")
    conn.commit()


def attach_offer(
    conn: sqlite3.Connection,
    finding_id: int,
    update: ScoreUpdate,
    *,
    threshold: int,
) -> None:
    """Link a scored finding to its offer and refresh offer fields when confident."""
    if update.offer_key is None or update.score is None:
        return
    row = conn.execute(
        "SELECT found_at, offer_key FROM findings WHERE id = ?",
        (finding_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"finding {finding_id} not found")
    found_at = _from_text(row["found_at"])
    found_text = _to_text(found_at)
    carried = _release_legacy(conn, row["offer_key"], update.offer_key)
    current = conn.execute(
        "SELECT * FROM offers WHERE offer_key = ?",
        (update.offer_key,),
    ).fetchone()
    confident = update.score >= threshold
    if current is None:
        conn.execute(
            """
            INSERT INTO offers (
                offer_key, provider, program_name, best_score, amount, deadline,
                first_seen_at, last_seen_at, mention_count, status, confident_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                update.offer_key,
                update.provider,
                update.program_name,
                update.score,
                update.amount if confident else None,
                update.deadline if confident else None,
                found_text,
                found_text,
                carried,
                found_text if confident else None,
            ),
        )
    else:
        _update_offer_mention(
            conn, current, update, found_at, found_text, confident=confident
        )
    conn.execute(
        "UPDATE findings SET offer_key = ? WHERE id = ?",
        (update.offer_key, finding_id),
    )
    conn.commit()


def close_missing_offers(
    conn: sqlite3.Connection,
    source_id: str,
    present_urls: set[str],
    now: datetime,
) -> tuple[int, int]:
    """Close a board's offers whose postings are gone. Reopen ones that are back.

    Only offers whose every mention came from this source are touched, so a
    program also seen on another page is never closed by one board.
    Returns (closed, reopened).
    """
    rows = conn.execute(
        """
        SELECT o.offer_key, o.closed_at
        FROM offers AS o
        WHERE EXISTS (
                SELECT 1 FROM findings AS f
                WHERE f.offer_key = o.offer_key AND f.source_id = ?
            )
          AND NOT EXISTS (
                SELECT 1 FROM findings AS f
                WHERE f.offer_key = o.offer_key AND f.source_id != ?
            )
        """,
        (source_id, source_id),
    ).fetchall()
    closed = 0
    reopened = 0
    stamp = _to_text(now)
    for row in rows:
        urls = {
            url
            for (url,) in conn.execute(
                "SELECT url FROM findings WHERE offer_key = ?", (row["offer_key"],)
            )
        }
        live = bool(urls & present_urls)
        if not live and row["closed_at"] is None:
            conn.execute(
                "UPDATE offers SET closed_at = ? WHERE offer_key = ?",
                (stamp, row["offer_key"]),
            )
            closed += 1
        elif live and row["closed_at"] is not None:
            conn.execute(
                "UPDATE offers SET closed_at = NULL WHERE offer_key = ?",
                (row["offer_key"],),
            )
            reopened += 1
    conn.commit()
    return closed, reopened


def list_offers(conn: sqlite3.Connection) -> list[Offer]:
    """Return every offer, newest last_seen_at first."""
    rows = conn.execute(
        "SELECT * FROM offers ORDER BY last_seen_at DESC, offer_key"
    ).fetchall()
    return [_offer_from_row(row) for row in rows]


def get_offer(conn: sqlite3.Connection, offer_key: str) -> Offer | None:
    """Return one offer, or None when that key has not been stored."""
    row = conn.execute(
        "SELECT * FROM offers WHERE offer_key = ?",
        (offer_key,),
    ).fetchone()
    if row is None:
        return None
    return _offer_from_row(row)


def query_findings(
    conn: sqlite3.Connection, filters: FindingQuery | None = None
) -> list[Finding]:
    """Return findings matching filters, newest first.

    Filter fragments below are fixed SQL written in this function. Values from
    the caller are bound parameters.
    """
    query = filters or FindingQuery()
    clauses: list[str] = []
    params: list[object] = []
    if query.min_score is not None:
        clauses.append("f.score >= ?")
        params.append(query.min_score)
    if query.category is not None:
        clauses.append("f.category = ?")
        params.append(query.category)
    if query.source_id is not None:
        clauses.append("f.source_id = ?")
        params.append(query.source_id)
    if query.status is not None:
        clauses.append("COALESCE(o.status, 'new') = ?")
        params.append(query.status)
    if query.start is not None:
        clauses.append("f.found_at >= ?")
        params.append(_to_text(query.start))
    if query.end is not None:
        clauses.append("f.found_at <= ?")
        params.append(_to_text(query.end))
    if query.text:
        clauses.append(
            "("
            "instr(lower(f.title), lower(?)) > 0 OR "
            "instr(lower(ifnull(f.summary, '')), lower(?)) > 0 OR "
            "instr(lower(ifnull(f.excerpt, '')), lower(?)) > 0"
            ")"
        )
        params.extend((query.text, query.text, query.text))
    where = ""
    if clauses:
        where = "WHERE " + " AND ".join(clauses)
    rows = conn.execute(
        f"{_FINDING_SELECT} {where} ORDER BY f.found_at DESC, f.id DESC",
        params,
    ).fetchall()
    return [_finding_from_row(row) for row in rows]


def finding_exists(conn: sqlite3.Connection, content_hash: str) -> bool:
    """Return whether this hash is already stored."""
    row = conn.execute(
        "SELECT 1 FROM findings WHERE content_hash = ?",
        (content_hash,),
    ).fetchone()
    return row is not None


def finding_url_exists(conn: sqlite3.Connection, url: str) -> bool:
    """Return whether this canonical URL is already stored.

    Scrape paragraphs share one page URL, so callers skip this check for scrapes.
    """
    row = conn.execute(
        "SELECT 1 FROM findings WHERE url = ? LIMIT 1",
        (url,),
    ).fetchone()
    return row is not None


def unscored_findings(conn: sqlite3.Connection, limit: int) -> list[Finding]:
    """Keyword hits still waiting for a score, oldest first.

    A null score whose summary is set has already been given up on.
    Those rows stay out of this queue.
    """
    if limit <= 0:
        return []
    rows = conn.execute(
        f"""
        {_FINDING_SELECT}
        WHERE f.score IS NULL
          AND f.summary IS NULL
          AND f.keyword_hits != '[]'
        ORDER BY f.found_at ASC, f.id ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_finding_from_row(row) for row in rows]


def _state_from_row(row: sqlite3.Row) -> SourceState:
    checked_at = row["last_checked_at"]
    status: SourceStatus | None = row["last_status"]
    return SourceState(
        source_id=row["source_id"],
        last_checked_at=None if checked_at is None else _from_text(checked_at),
        last_status=status,
        last_error=row["last_error"],
        consecutive_failures=int(row["consecutive_failures"]),
    )


def _is_due(state: SourceState | None, source: Source, now: datetime) -> bool:
    if state is None or state.last_checked_at is None:
        return True
    window = _WEEK if source.frequency == "weekly" else _DAY
    return now - state.last_checked_at >= window - _SLACK


def _finding_from_row(row: sqlite3.Row) -> Finding:
    hits = json.loads(row["keyword_hits"])
    if not isinstance(hits, list):
        raise ValueError(f"finding {row['id']} has invalid keyword_hits")
    status: FindingStatus = row["status"]
    emailed_at = row["emailed_at"]
    return Finding(
        id=int(row["id"]),
        source_id=row["source_id"],
        title=row["title"],
        url=row["url"],
        content_hash=row["content_hash"],
        excerpt=row["excerpt"],
        found_at=_from_text(row["found_at"]),
        keyword_hits=[str(hit) for hit in hits],
        score=None if row["score"] is None else int(row["score"]),
        category=row["category"],
        amount=row["amount"],
        deadline=row["deadline"],
        summary=row["summary"],
        reasoning=row["reasoning"],
        emailed_at=None if emailed_at is None else _from_text(emailed_at),
        status=status,
        offer_key=row["offer_key"],
        details=row["details"],
    )


def _offer_from_row(row: sqlite3.Row) -> Offer:
    status: FindingStatus = row["status"]
    return Offer(
        offer_key=row["offer_key"],
        provider=row["provider"],
        program_name=row["program_name"],
        best_score=None if row["best_score"] is None else int(row["best_score"]),
        amount=row["amount"],
        deadline=row["deadline"],
        first_seen_at=_from_text(row["first_seen_at"]),
        last_seen_at=_from_text(row["last_seen_at"]),
        mention_count=int(row["mention_count"]),
        status=status,
        closed_at=None if row["closed_at"] is None else _from_text(row["closed_at"]),
    )


def _release_legacy(conn: sqlite3.Connection, prior: str | None, new_key: str) -> str:
    """Drop a pre-score legacy offer and return the status a new offer should keep."""
    if not prior or prior == new_key or not prior.startswith("legacy:"):
        return "new"
    previous = conn.execute(
        "SELECT status FROM offers WHERE offer_key = ?",
        (prior,),
    ).fetchone()
    conn.execute("DELETE FROM offers WHERE offer_key = ?", (prior,))
    if previous is None:
        return "new"
    status: str = previous["status"]
    return status


def _update_offer_mention(
    conn: sqlite3.Connection,
    current: sqlite3.Row,
    update: ScoreUpdate,
    found_at: datetime,
    found_text: str,
    *,
    confident: bool,
) -> None:
    best = current["best_score"]
    amount = current["amount"]
    deadline = current["deadline"]
    confident_at = current["confident_at"]
    provider = current["provider"] or update.provider
    program = current["program_name"] or update.program_name
    stored = None if confident_at is None else _from_text(confident_at)
    if confident and (stored is None or found_at >= stored):
        best = update.score
        if update.amount is not None:
            amount = update.amount
        if update.deadline is not None:
            deadline = update.deadline
        confident_at = found_text
        if update.provider:
            provider = update.provider
        if update.program_name:
            program = update.program_name
    elif best is None:
        best = update.score
    first_seen = _from_text(current["first_seen_at"])
    last_seen = _from_text(current["last_seen_at"])
    first_text = found_text if found_at < first_seen else current["first_seen_at"]
    last_text = found_text if found_at > last_seen else current["last_seen_at"]
    conn.execute(
        """
        UPDATE offers
        SET provider = ?, program_name = ?, best_score = ?, amount = ?,
            deadline = ?, first_seen_at = ?, last_seen_at = ?,
            mention_count = mention_count + 1, confident_at = ?
        WHERE offer_key = ?
        """,
        (
            provider,
            program,
            best,
            amount,
            deadline,
            first_text,
            last_text,
            confident_at,
            update.offer_key,
        ),
    )


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _to_text(value: datetime) -> str:
    return _require_aware(value).isoformat(timespec="microseconds")


def _from_text(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
