"""YouTube transcript windows. The full transcript is never stored or scored."""

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled

from radar.filter import match_keywords
from radar.models import RawItem, Source
from radar.urls import canonicalize_url

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 120
_MAX_WINDOW_CHARS = 4000
_NOTE_LIMIT = 300
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_WATCH_HOSTS = frozenset({"youtube.com", "m.youtube.com", "music.youtube.com"})

TranscriptFetch = Callable[[str, tuple[str, ...]], list["Cue"]]


class NoTranscript(Exception):
    """The video has no transcript. The message is the video id only."""

    def __init__(self, video_id: str) -> None:
        super().__init__(f"no transcript for {video_id}")


class TranscriptError(Exception):
    """The transcript request failed. The message does not include a body."""

    def __init__(self, video_id: str) -> None:
        super().__init__(f"transcript failed for {video_id}")


@dataclass(frozen=True)
class Cue:
    """One transcript line and the second it appears."""

    text: str
    start: float


@dataclass(frozen=True)
class TranscriptWindow:
    """About two minutes of cues. `start_seconds` is the first cue."""

    start_seconds: int
    text: str


@dataclass
class TranscriptBudget:
    """How many transcripts this sweep may still download."""

    remaining: int

    def take(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


def languages_for(source: Source) -> tuple[str, ...]:
    """Prefer Turkish captions for a Turkey channel, then English, or the reverse."""
    if "turkey" in source.tags:
        return ("tr", "en")
    return ("en", "tr")


def transcript_share(remaining: int, later_channels: int) -> int:
    """How many transcripts this channel may use so later channels keep a share."""
    if remaining <= 0:
        return 0
    if later_channels <= 0:
        return remaining
    return max(1, remaining // (later_channels + 1))


def video_id_from_url(url: str) -> str | None:
    """Return the 11-character video id, or None when the URL is not a video."""
    parsed = urlsplit(url.strip())
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/", 1)[0]
    elif host in _WATCH_HOSTS:
        parts = [part for part in parsed.path.split("/") if part]
        if parsed.path == "/watch":
            candidate = dict(parse_qsl(parsed.query)).get("v", "")
        elif parts and parts[0] in {"shorts", "embed", "live"} and len(parts) > 1:
            candidate = parts[1]
        else:
            return None
    else:
        return None
    if _VIDEO_ID.fullmatch(candidate) is None:
        return None
    return candidate


def timestamp_url(video_url: str, start_seconds: int) -> str:
    """Canonical video URL with `&t=XXs` so the owner can jump to that minute."""
    start = max(0, start_seconds)
    canonical = canonicalize_url(video_url)
    parsed = urlsplit(canonical)
    kept = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() != "t"
    ]
    kept.append(("t", f"{start}s"))
    query = urlencode(kept)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


def window_cues(
    cues: Sequence[Cue], *, seconds: int = WINDOW_SECONDS
) -> list[TranscriptWindow]:
    """Group cues into windows of `seconds`. The stamp is the first cue's start."""
    if seconds <= 0:
        raise ValueError("window must be positive")
    windows: list[TranscriptWindow] = []
    bucket_start: float | None = None
    stamp = 0
    parts: list[str] = []

    def flush() -> None:
        if bucket_start is None or not parts:
            return
        text = " ".join(parts)
        if len(text) > _MAX_WINDOW_CHARS:
            text = text[:_MAX_WINDOW_CHARS]
        windows.append(TranscriptWindow(start_seconds=stamp, text=text))

    for cue in sorted(cues, key=lambda item: item.start):
        text = " ".join(cue.text.split())
        if not text:
            continue
        start = max(0.0, cue.start)
        if bucket_start is None or start >= bucket_start + seconds:
            flush()
            bucket_start = start
            stamp = int(start)
            parts = [text]
        else:
            parts.append(text)
    flush()
    return windows


def matching_windows(
    windows: Sequence[TranscriptWindow], keywords: Sequence[str]
) -> list[TranscriptWindow]:
    """Keep windows that contain at least one keyword. The rest are dropped."""
    words = list(keywords)
    return [window for window in windows if match_keywords(window.text, words)]


def fetch_cues(video_id: str, languages: tuple[str, ...] = ("en", "tr")) -> list[Cue]:
    """Download one transcript. Raises NoTranscript or TranscriptError."""
    try:
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=languages)
    except (NoTranscriptFound, TranscriptsDisabled) as exc:
        logger.info("no transcript video=%s error=%s", video_id, type(exc).__name__)
        raise NoTranscript(video_id) from exc
    except Exception as exc:
        logger.warning(
            "transcript failed video=%s error=%s", video_id, type(exc).__name__
        )
        raise TranscriptError(video_id) from exc
    return [Cue(text=snippet.text, start=float(snippet.start)) for snippet in fetched]


def expand_youtube(
    source: Source,
    items: Sequence[RawItem],
    *,
    keywords: Sequence[str],
    seen: Callable[[str], bool],
    remember: Callable[[str], None],
    fetch: TranscriptFetch,
    budget: TranscriptBudget,
    later_channels: int,
) -> tuple[list[RawItem], str | None]:
    """Turn new videos into keyword windows. One failure does not stop the source."""
    languages = languages_for(source)
    share = transcript_share(budget.remaining, later_channels)
    kept: list[RawItem] = []
    missing: list[str] = []
    failed: list[str] = []
    used = 0
    for raw in items:
        video_id = video_id_from_url(raw.url)
        if video_id is None:
            logger.warning("youtube item has no video id source=%s", source.id)
            continue
        if seen(video_id):
            continue
        if used >= share or not budget.take():
            logger.info("transcript cap reached source=%s", source.id)
            break
        used += 1
        try:
            cues = fetch(video_id, languages)
        except NoTranscript:
            missing.append(video_id)
            continue
        except Exception:
            logger.warning("transcript failed video=%s", video_id)
            failed.append(video_id)
            continue
        remember(video_id)
        for window in matching_windows(window_cues(cues), keywords):
            kept.append(
                RawItem(
                    title=raw.title,
                    url=timestamp_url(raw.url, window.start_seconds),
                    summary=window.text,
                    loose=True,
                )
            )
    return kept, _health_note(missing, failed)


def _health_note(missing: list[str], failed: list[str]) -> str | None:
    parts: list[str] = []
    if missing:
        parts.append("no transcript: " + ", ".join(missing))
    if failed:
        parts.append("transcript failed: " + ", ".join(failed))
    if not parts:
        return None
    note = "; ".join(parts)
    if len(note) <= _NOTE_LIMIT:
        return note
    return note[: _NOTE_LIMIT - 3] + "..."
