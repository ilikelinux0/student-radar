"""Sweep orchestrator and CLI."""

import argparse
import logging
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from radar import export
from radar.config import ConfigError, load_default
from radar.db import (
    attach_offer,
    close_missing_offers,
    due_sources,
    finding_exists,
    finding_url_exists,
    get_snapshot,
    get_source_state,
    insert_finding,
    open_database,
    remember_transcript,
    save_snapshot,
    transcript_checked,
    unscored_findings,
    update_score,
    upsert_source_state,
)
from radar.envfile import load_env_file
from radar.extract import added_items, extract_main_text, hash_item, hash_text
from radar.fetchers import FetchResult, fetch_source
from radar.fetchers.http import FetchError, HttpClient
from radar.filter import gate_groups, gate_keywords
from radar.models import (
    Config,
    NewFinding,
    ScoreUpdate,
    Settings,
    Snapshot,
    Source,
    SourceState,
)
from radar.personal import personal_update, verify_deadline
from radar.profile import Profile, ProfileError, load_profile
from radar.queries import DISCOVERY_TYPES, source_item_limit
from radar.scorer import (
    EXCERPT_LIMIT,
    ScoreError,
    ScoreOutcome,
    ScoreSpec,
    ScoringItem,
    bedrock_client,
    gemini_client,
    personal_spec,
    score_items,
    score_with_gemini,
)
from radar.transcripts import (
    TranscriptBudget,
    TranscriptFetch,
    expand_youtube,
    fetch_cues,
)
from radar.urls import canonicalize_url

logger = logging.getLogger(__name__)

SCORING_FAILED = "[scoring failed]"
_ERROR_LIMIT = 300

Sleeper = Callable[[float], None]
ScoreFn = Callable[..., list[ScoreOutcome]]


class SweepError(Exception):
    """The run cannot start. The message is safe to print."""


@dataclass(frozen=True)
class SweepSummary:
    """Counts for one run, including a dry run's would-be inserts."""

    checked: int
    failed: int
    new: int
    scored: int


def run_sweep(
    config: Config,
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    source_id: str | None = None,
    force: bool = False,
    no_llm: bool = False,
    score: ScoreFn = score_items,
    now: datetime | None = None,
    environ: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
    sleeper: Sleeper | None = None,
    export_dir: Path | None = None,
    transcript_fetcher: TranscriptFetch | None = None,
    profile: Profile,
) -> SweepSummary:
    """Fetch due sources, score new keyword hits, and write the snapshot.

    profile is the reader's profile. It is never logged.
    """
    started = time.perf_counter()
    moment = datetime.now(UTC) if now is None else now
    env = os.environ if environ is None else environ
    selected = _select(config, source_id)
    due = due_sources(conn, selected, moment, force=force)
    checked = 0
    failed = 0
    new = 0
    found_last_run: dict[str, int] = {}
    discovery_left = config.settings.max_new_items_per_run
    transcripts = TranscriptBudget(config.settings.max_transcripts_per_run)
    fetch_transcript = transcript_fetcher or fetch_cues
    http = HttpClient(config.settings, transport=transport, sleeper=sleeper)
    try:
        for index, source in enumerate(due):
            if source.type in DISCOVERY_TYPES and discovery_left <= 0:
                logger.info("discovery cap reached; skipping source=%s", source.id)
                continue
            checked += 1
            found_last_run[source.id] = 0
            try:
                since = None
                snapshot_text = None
                if source.type == "hn_search":
                    # A failed check moves last_checked_at too. Resuming from it
                    # would skip that run's stories, so search without a cursor
                    # and let the hash and URL checks drop repeats.
                    state = get_source_state(conn, source.id)
                    if state is not None and state.last_status == "ok":
                        since = state.last_checked_at
                if source.type == "github_watch":
                    previous = get_snapshot(conn, source.id)
                    snapshot_text = None if previous is None else previous.text
                limit = source_item_limit(source, config.settings)
                if source.type in DISCOVERY_TYPES:
                    limit = min(limit, discovery_left)
                result = fetch_source(
                    source,
                    http,
                    settings=config.settings,
                    since=since,
                    snapshot_text=snapshot_text,
                    limit=limit,
                )
                if source.type == "youtube_channel" and result.items is not None:
                    later = sum(
                        1
                        for upcoming in due[index + 1 :]
                        if upcoming.type == "youtube_channel"
                    )
                    windows, note = expand_youtube(
                        source,
                        result.items,
                        keywords=list(config.settings.keywords),
                        seen=lambda video_id: transcript_checked(conn, video_id),
                        remember=_remember_transcript(
                            conn, source.id, moment, dry_run=dry_run
                        ),
                        fetch=fetch_transcript,
                        budget=transcripts,
                        later_channels=later,
                    )
                    result = replace(result, items=tuple(windows), health_note=note)
                added = _store_source(
                    source,
                    result,
                    conn,
                    config.settings,
                    moment,
                    dry_run=dry_run,
                    limit=limit,
                )
                if source.closes_missing and result.items and not dry_run:
                    _close_missing(conn, source, result.items, moment)
                if source.type in DISCOVERY_TYPES:
                    discovery_left -= added
                new += added
                found_last_run[source.id] = added
                fetched_count = (
                    len(result.items)
                    if result.items is not None
                    else int(result.html is not None)
                )
                logger.info(
                    "source=%s fetched=%s kept=%s",
                    source.id,
                    fetched_count,
                    added,
                )
                if dry_run:
                    if result.notice:
                        print(
                            "would record source health for "
                            f"{source.id}: {result.notice}"
                        )
                    if result.health_note:
                        print(
                            "would record source health for "
                            f"{source.id}: {result.health_note}"
                        )
                elif result.notice:
                    failed += 1
                    _mark_error(conn, source.id, moment, FetchError(result.notice))
                elif result.health_note:
                    failed += 1
                    _mark_transcript_note(conn, source.id, moment, result.health_note)
                else:
                    _mark_ok(conn, source.id, moment)
            except Exception as exc:
                failed += 1
                logger.exception("source %s failed", source.id)
                if not dry_run:
                    _mark_error(conn, source.id, moment, exc)
    finally:
        http.close()
    spec = personal_spec(profile.fit_summary, moment.date().isoformat())
    scored = 0 if dry_run or no_llm else _score_pending(config, conn, score, env, spec)
    summary = SweepSummary(checked=checked, failed=failed, new=new, scored=scored)
    logger.info(
        "sweep checked=%s failed=%s new=%s scored=%s http=%s transcripts=%s",
        summary.checked,
        summary.failed,
        summary.new,
        summary.scored,
        http.request_count,
        config.settings.max_transcripts_per_run - transcripts.remaining,
    )
    export.publish_snapshot(
        config,
        conn,
        generated_at=moment,
        duration_seconds=round(time.perf_counter() - started, 3),
        items_found=found_last_run,
        destination=_export_destination(config.settings, export_dir),
        dry_run=dry_run,
        profile=profile,
    )
    return summary


def _export_destination(settings: Settings, override: Path | None) -> Path:
    """The caller's folder, else export_dir from settings."""
    if override is not None:
        return override
    return Path(settings.export_dir)


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Callable[..., object] = run_sweep,
    loader: Callable[[], Config] | None = None,
    opener: Callable[[Settings], sqlite3.Connection] | None = None,
    profile_loader: Callable[[], Profile | None] | None = None,
) -> int:
    """CLI entry point for `python -m radar.sweep`."""
    _tolerate_console_encoding()
    load_env_file(Path(".env"), os.environ)
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    args = _parser().parse_args(list(argv) if argv is not None else None)
    read_profile = profile_loader or (lambda: load_profile(os.environ))
    try:
        profile = read_profile()
    except ProfileError as exc:
        print(f"profile error: {exc}", file=sys.stderr)
        return 2
    if profile is None:
        logger.info(
            "sweep skipped: no profile (set RADAR_PROFILE or create profile.yaml)"
        )
        return 0
    load = loader or load_default
    open_db = opener or open_database
    try:
        config = load()
        conn = open_db(config.settings)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    try:
        runner(
            config,
            conn,
            dry_run=args.dry_run,
            source_id=args.source,
            force=args.force,
            no_llm=args.no_llm,
            profile=profile,
        )
    except SweepError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        close = getattr(conn, "close", None)
        if close is not None:
            close()
    return 0


def _tolerate_console_encoding() -> None:
    """Dry runs print item titles. On Windows a redirected stream is cp1252,
    and a Turkish title (ğ, ş, İ) raised there and failed that source."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="radar.sweep")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--source")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    return parser


def _select(config: Config, source_id: str | None) -> list[Source]:
    if source_id is None:
        return list(config.sources)
    selected = [source for source in config.sources if source.id == source_id]
    if not selected:
        raise SweepError(f"unknown source id {source_id!r}")
    return selected


def _store_source(
    source: Source,
    result: FetchResult,
    conn: sqlite3.Connection,
    settings: Settings,
    now: datetime,
    *,
    dry_run: bool,
    limit: int,
) -> int:
    if source.type == "scrape" or (result.items is None and result.html is not None):
        page = result.html or ""
        return _store_scrape(
            source, page, conn, settings, now, dry_run=dry_run, limit=limit
        )
    if result.items is None:
        raise FetchError(f"{source.id}: fetcher returned nothing")
    count = 0
    for raw in result.items:
        if count >= limit:
            logger.info("item cap reached source=%s", source.id)
            break
        if not str(raw.url).strip():
            continue
        url = canonicalize_url(raw.url)
        content_hash = hash_item(raw.title, url, raw.summary)
        count += _keep(
            conn,
            source,
            title=raw.title,
            url=url,
            content_hash=content_hash,
            excerpt=raw.summary,
            settings=settings,
            now=now,
            dry_run=dry_run,
            dedupe_url=True,
            loose=raw.loose,
        )
    if result.snapshot_text is not None:
        _save_text_snapshot(conn, source.id, result.snapshot_text, now, dry_run=dry_run)
    return count


def _store_scrape(
    source: Source,
    html: str,
    conn: sqlite3.Connection,
    settings: Settings,
    now: datetime,
    *,
    dry_run: bool,
    limit: int,
) -> int:
    text = extract_main_text(html, source.selector)
    if not text.strip():
        # A JavaScript-only shell extracts to nothing. Saving that as the
        # snapshot would report a source that never yields text as healthy.
        raise FetchError(f"{source.url} has no text (needs js: true?)")
    previous = get_snapshot(conn, source.id)
    previous_text = None if previous is None else previous.text
    count = 0
    for item in added_items(
        previous_text,
        text,
        url=source.url,
        title=source.name,
        split_first=bool(source.selector),
    ):
        if count >= limit:
            logger.info("item cap reached source=%s", source.id)
            break
        count += _keep(
            conn,
            source,
            title=item.title,
            url=item.url,
            content_hash=item.content_hash,
            excerpt=item.summary,
            settings=settings,
            now=now,
            dry_run=dry_run,
            dedupe_url=False,
        )
    if dry_run:
        print(f"would save snapshot for {source.id}")
    else:
        _save_text_snapshot(conn, source.id, text, now, dry_run=False)
    return count


def _save_text_snapshot(
    conn: sqlite3.Connection,
    source_id: str,
    text: str,
    now: datetime,
    *,
    dry_run: bool,
) -> None:
    if dry_run:
        print(f"would save snapshot for {source_id}")
        return
    save_snapshot(
        conn,
        Snapshot(
            source_id=source_id,
            content_hash=hash_text(text),
            text=text,
            fetched_at=now,
        ),
    )


def _keep(
    conn: sqlite3.Connection,
    source: Source,
    *,
    title: str,
    url: str,
    content_hash: str,
    excerpt: str,
    settings: Settings,
    now: datetime,
    dry_run: bool,
    dedupe_url: bool,
    loose: bool = False,
) -> int:
    url = canonicalize_url(url)
    if finding_exists(conn, content_hash):
        return 0
    if dedupe_url and finding_url_exists(conn, url):
        return 0
    if source.require or source.require_title:
        hits = gate_groups(
            title,
            excerpt,
            [settings.keyword_groups[name] for name in source.require],
            [settings.keyword_groups[name] for name in source.require_title],
        )
    else:
        hits = gate_keywords(
            title,
            excerpt,
            list(settings.keywords),
            strict=source.type in DISCOVERY_TYPES and not loose,
        )
    if not hits:
        return 0
    if dry_run:
        print(f"would insert {title} from {source.id}")
        return 1
    created = insert_finding(
        conn,
        NewFinding(
            source_id=source.id,
            title=title,
            url=url,
            content_hash=content_hash,
            excerpt=excerpt,
            found_at=now,
            keyword_hits=hits,
        ),
    )
    return 1 if created else 0


def _score_pending(
    config: Config,
    conn: sqlite3.Connection,
    score: ScoreFn,
    env: Mapping[str, str],
    spec: ScoreSpec,
) -> int:
    if config.settings.scorer_provider == "none":
        return 0
    pending = unscored_findings(conn, config.settings.max_llm_items_per_run)
    if not pending:
        return 0
    names = {source.id: source.name for source in config.sources}
    items = [
        ScoringItem(
            source_name=names.get(finding.source_id, finding.source_id),
            url=finding.url,
            excerpt=finding.excerpt,
            title=finding.title,
        )
        for finding in pending
    ]
    client: object | None = None
    try:
        active = score
        provider = config.settings.scorer_provider
        if score is score_items and provider == "gemini":
            active = score_with_gemini
            key = env.get("GEMINI_API_KEY", "").strip()
            if key:
                client = gemini_client(key)
        elif score is score_items:
            region = env.get("AWS_REGION", "").strip()
            if region:
                client = bedrock_client(region)
        outcomes = active(items, config.settings, client, environ=env, spec=spec)
    except ScoreError as exc:
        logger.error("scoring skipped: %s", exc)
        return 0
    except Exception:
        logger.exception("scoring failed")
        return 0
    finally:
        if client is not None and config.settings.scorer_provider == "gemini":
            client.close()  # type: ignore[attr-defined]
    # The score is fit, not confidence: a poor fit still has a real deadline,
    # so every scored mention may set the offer's deadline.
    threshold = 0
    scored = 0
    for finding, outcome in zip(pending, outcomes, strict=False):
        if not outcome.attempted:
            continue
        if outcome.score is None:
            _write_score(
                conn,
                finding.id,
                ScoreUpdate(score=None, summary=SCORING_FAILED),
                threshold=threshold,
            )
            continue
        checked = verify_deadline(
            outcome.score, finding.title, finding.excerpt[:EXCERPT_LIMIT]
        )
        update = personal_update(checked)
        _write_score(
            conn,
            finding.id,
            update,
            threshold=threshold,
        )
        scored += 1
    return scored


def _write_score(
    conn: sqlite3.Connection,
    finding_id: int,
    update: ScoreUpdate,
    *,
    threshold: int,
) -> None:
    update_score(conn, finding_id, update)
    attach_offer(conn, finding_id, update, threshold=threshold)


def _close_missing(
    conn: sqlite3.Connection,
    source: Source,
    items: Sequence[object],
    now: datetime,
) -> None:
    """A board lists every open posting, so one that is missing has closed."""
    present = {
        canonicalize_url(item.url)  # type: ignore[attr-defined]
        for item in items
        if str(getattr(item, "url", "")).strip()
    }
    closed, reopened = close_missing_offers(conn, source.id, present, now)
    if closed or reopened:
        logger.info("source=%s closed=%s reopened=%s", source.id, closed, reopened)


def _remember_transcript(
    conn: sqlite3.Connection, source_id: str, now: datetime, *, dry_run: bool
) -> Callable[[str], None]:
    def remember(video_id: str) -> None:
        if dry_run:
            return
        remember_transcript(conn, video_id, source_id, now)

    return remember


def _mark_transcript_note(
    conn: sqlite3.Connection, source_id: str, now: datetime, note: str
) -> None:
    """Record a transcript problem without counting it as a dead source."""
    upsert_source_state(
        conn,
        SourceState(
            source_id=source_id,
            last_checked_at=now,
            last_status="error",
            last_error=_clip_error(note),
            consecutive_failures=0,
        ),
    )


def _mark_ok(conn: sqlite3.Connection, source_id: str, now: datetime) -> None:
    upsert_source_state(
        conn,
        SourceState(
            source_id=source_id,
            last_checked_at=now,
            last_status="ok",
            last_error=None,
            consecutive_failures=0,
        ),
    )


def _mark_error(
    conn: sqlite3.Connection, source_id: str, now: datetime, exc: BaseException
) -> None:
    previous = get_source_state(conn, source_id)
    failures = 0 if previous is None else previous.consecutive_failures
    upsert_source_state(
        conn,
        SourceState(
            source_id=source_id,
            last_checked_at=now,
            last_status="error",
            last_error=_error_text(exc),
            consecutive_failures=failures + 1,
        ),
    )


def _error_text(exc: BaseException) -> str:
    return _clip_error(f"{type(exc).__name__}: {exc}")


def _clip_error(message: str) -> str:
    text = " ".join(message.split())
    if len(text) <= _ERROR_LIMIT:
        return text
    return text[: _ERROR_LIMIT - 3] + "..."


if __name__ == "__main__":
    raise SystemExit(main())
