"""Transcript windows, timestamp links, and the no-transcript path. No network."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled

from radar.config import load_config
from radar.db import connect, get_source_state, query_findings
from radar.fetchers import fetch_source
from radar.fetchers.http import FetchError, HttpClient
from radar.models import Config, Settings, Source
from radar.sweep import run_sweep
from radar.transcripts import (
    Cue,
    NoTranscript,
    TranscriptError,
    fetch_cues,
    timestamp_url,
    transcript_share,
    video_id_from_url,
    window_cues,
)
from tests.profile_sample import sample_profile

PROFILE = sample_profile()

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
CHANNEL = "UCabcdefghijklmnopqrstuv"
OTHER = "UC" + "z" * 22
VIDEO = "abcdefghijk"
KEYWORDS = [
    "credits",
    "startup program",
    "free tier",
    "grant",
    "kredi",
    "hibe",
    "destek",
    "ücretsiz",
    "girişimci",
    "startup programı",
]


def _settings(**overrides: object) -> Settings:
    # The transcript fixture is Turkish startup talk, so the gate uses those words.
    loaded = load_config(ROOT / "sources.yaml", ROOT / "settings.yaml").settings
    update = {"request_delay_seconds": 0, "keywords": KEYWORDS, **overrides}
    return loaded.model_copy(update=update)


def _source(**overrides: object) -> Source:
    values: dict[str, object] = {
        "id": "youtube",
        "name": "YouTube",
        "type": "youtube_channel",
        "channel_id": CHANNEL,
        "frequency": "weekly",
        "enabled": True,
    }
    values.update(overrides)
    return Source(**values)  # type: ignore[arg-type]


def _config(*sources: Source, **settings: object) -> Config:
    return Config(sources=list(sources), settings=_settings(**settings))


def _cues() -> list[Cue]:
    rows = json.loads((FIXTURES / "transcript_cues.json").read_text(encoding="utf-8"))
    return [Cue(text=row["text"], start=row["start"]) for row in rows]


def _feed(videos: list[tuple[str, str]]) -> bytes:
    entries = []
    for video_id, title in videos:
        entries.append(
            "<entry>"
            f"<title>{title}</title>"
            '<link rel="alternate" '
            f'href="https://www.youtube.com/watch?v={video_id}"/>'
            "</entry>"
        )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">' + "".join(entries) + "</feed>"
    )
    return body.encode()


def _transport(feeds: dict[str, bytes]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /feeds/\n")
        if request.url.path == "/feeds/videos.xml":
            channel = request.url.params["channel_id"]
            return httpx.Response(200, content=feeds[channel])
        raise AssertionError(f"unexpected {request.url}")

    return httpx.MockTransport(handler)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "findings.db")
    yield connection
    connection.close()


def test_cues_split_into_two_minute_windows() -> None:
    """Breaks if a cue past 120 seconds stays in the first window."""
    windows = window_cues(_cues())

    assert [window.start_seconds for window in windows] == [0, 130, 250]
    assert windows[0].text == "intro weather still the weather"
    assert "kredi" in windows[1].text
    assert windows[1].text.endswith("başvuru detayları")
    assert windows[2].text == "closing music"


def test_timestamp_link_uses_the_window_start() -> None:
    """Breaks if the deep link drops t= or keeps a tracker."""
    linked = timestamp_url(
        "https://www.YouTube.com/watch?v=abcdefghijk&utm_source=share&t=1s",
        130,
    )

    assert linked == "https://www.youtube.com/watch?v=abcdefghijk&t=130s"
    assert video_id_from_url("https://youtu.be/abcdefghijk") == VIDEO


def test_transcript_budget_is_shared_across_later_channels() -> None:
    """Breaks if the first channel can take every transcript in the run."""
    assert transcript_share(8, 3) == 2
    assert transcript_share(2, 1) == 1
    assert transcript_share(1, 0) == 1
    assert transcript_share(0, 2) == 0


def test_turkish_window_is_stored_with_a_timestamp(
    conn: sqlite3.Connection,
) -> None:
    """Breaks if a title hit keeps every window, or the link omits the minute."""
    seen: list[tuple[str, ...]] = []

    def fetch(video_id: str, languages: tuple[str, ...]) -> list[Cue]:
        seen.append(languages)
        assert video_id == VIDEO
        return _cues()

    summary = run_sweep(
        _config(_source(tags=["turkey"])),
        conn,
        profile=PROFILE,
        no_llm=True,
        transport=_transport({CHANNEL: _feed([(VIDEO, "Weekly update")])}),
        sleeper=lambda _seconds: None,
        now=NOW,
        transcript_fetcher=fetch,
    )

    rows = query_findings(conn)
    assert summary.new == 1
    assert len(rows) == 1
    assert rows[0].url == "https://www.youtube.com/watch?v=abcdefghijk&t=130s"
    assert "kredi" in rows[0].excerpt
    assert "başvuru" in rows[0].excerpt
    assert "intro weather" not in rows[0].excerpt
    assert "closing music" not in rows[0].excerpt
    assert rows[0].keyword_hits == ["kredi", "ücretsiz", "girişimci"]
    assert seen == [("tr", "en")]
    state = get_source_state(conn, "youtube")
    assert state is not None
    assert state.last_status == "ok"


def test_no_transcript_is_recorded_and_a_later_video_is_kept(
    conn: sqlite3.Connection,
) -> None:
    """Breaks if a missing transcript aborts the channel or hides the next video."""

    def fetch(video_id: str, languages: tuple[str, ...]) -> list[Cue]:
        del languages
        if video_id == VIDEO:
            raise NoTranscript(video_id)
        if video_id == "bcdefghijkl":
            return [Cue(text="startup credits for founders", start=15)]
        raise RuntimeError("SECRET TRANSCRIPT")

    summary = run_sweep(
        _config(
            _source(),
        ),
        conn,
        profile=PROFILE,
        no_llm=True,
        transport=_transport(
            {
                CHANNEL: _feed(
                    [
                        (VIDEO, "Weekly update"),
                        ("bcdefghijkl", "Office hours"),
                        ("cdefghijklm", "Broken upload"),
                    ]
                )
            }
        ),
        sleeper=lambda _seconds: None,
        now=NOW,
        transcript_fetcher=fetch,
    )

    rows = query_findings(conn)
    assert summary.failed == 1
    assert summary.new == 1
    assert len(rows) == 1
    assert rows[0].url == "https://www.youtube.com/watch?v=bcdefghijkl&t=15s"
    state = get_source_state(conn, "youtube")
    assert state is not None
    assert state.last_status == "error"
    assert state.consecutive_failures == 0
    assert state.last_error is not None
    assert "no transcript: abcdefghijk" in state.last_error
    assert "transcript failed: cdefghijklm" in state.last_error
    assert "SECRET" not in state.last_error


def test_a_fetched_video_is_not_downloaded_again(
    conn: sqlite3.Connection,
) -> None:
    """Breaks if the next run downloads a transcript it already windowed."""
    calls: list[str] = []

    def fetch(video_id: str, languages: tuple[str, ...]) -> list[Cue]:
        del languages
        calls.append(video_id)
        return [Cue(text="hello weather", start=0)]

    feed = {CHANNEL: _feed([(VIDEO, "Weekly update")])}
    kwargs = {
        "no_llm": True,
        "transport": _transport(feed),
        "sleeper": lambda _seconds: None,
        "transcript_fetcher": fetch,
    }
    run_sweep(_config(_source()), conn, profile=PROFILE, now=NOW, **kwargs)
    run_sweep(_config(_source()), conn, profile=PROFILE, force=True, now=NOW, **kwargs)

    assert calls == [VIDEO]
    assert query_findings(conn) == []


def test_transcript_cap_stops_later_videos(conn: sqlite3.Connection) -> None:
    """Breaks if one channel downloads every new video in a single run."""
    calls: list[str] = []

    def fetch(video_id: str, languages: tuple[str, ...]) -> list[Cue]:
        del languages
        calls.append(video_id)
        return [Cue(text="startup credits", start=0)]

    run_sweep(
        _config(_source(), max_transcripts_per_run=1),
        conn,
        profile=PROFILE,
        no_llm=True,
        transport=_transport(
            {CHANNEL: _feed([(VIDEO, "Weekly update"), ("bcdefghijkl", "Next upload")])}
        ),
        sleeper=lambda _seconds: None,
        now=NOW,
        transcript_fetcher=fetch,
    )

    assert calls == [VIDEO]
    assert len(query_findings(conn)) == 1


def test_two_channels_share_one_transcript_budget(
    conn: sqlite3.Connection,
) -> None:
    """Breaks if the first channel spends the whole run cap."""
    calls: list[str] = []

    def fetch(video_id: str, languages: tuple[str, ...]) -> list[Cue]:
        del languages
        calls.append(video_id)
        return [Cue(text="startup credits", start=30)]

    run_sweep(
        _config(
            _source(id="first", channel_id=CHANNEL),
            _source(id="second", channel_id=OTHER),
            max_transcripts_per_run=2,
        ),
        conn,
        profile=PROFILE,
        no_llm=True,
        transport=_transport(
            {
                CHANNEL: _feed([("aaaaaaaaaaa", "Weekly update")]),
                OTHER: _feed([("bbbbbbbbbbb", "Weekly update")]),
            }
        ),
        sleeper=lambda _seconds: None,
        now=NOW,
        transcript_fetcher=fetch,
    )

    assert calls == ["aaaaaaaaaaa", "bbbbbbbbbbb"]


def test_uploads_feed_is_fetched_when_robots_disallows_it() -> None:
    """Breaks if the phase 15 feed exception is dropped."""
    feed = (FIXTURES / "youtube_channel.xml").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /feeds/\n")
        if request.url.path == "/feeds/videos.xml":
            return httpx.Response(200, content=feed)
        raise AssertionError(f"unexpected {request.url}")

    result = fetch_source(
        _source(),
        HttpClient(
            _settings(),
            transport=httpx.MockTransport(handler),
            sleeper=lambda _seconds: None,
        ),
        settings=_settings(),
    )

    assert result.items is not None
    assert result.items[0].title == "Cloud credits office hours"


def test_other_youtube_urls_still_obey_robots() -> None:
    """Breaks if the uploads exception allows a watch page."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        raise AssertionError(f"unexpected {request.url}")

    client = HttpClient(
        _settings(),
        transport=httpx.MockTransport(handler),
        sleeper=lambda _seconds: None,
    )
    try:
        with pytest.raises(FetchError, match="robots.txt"):
            client.get("https://www.youtube.com/watch?v=abcdefghijk")
    finally:
        client.close()


@pytest.mark.parametrize(
    "error",
    [
        TranscriptsDisabled(VIDEO),
        NoTranscriptFound(VIDEO, ["en"], None),  # type: ignore[arg-type]
    ],
)
def test_library_missing_transcript_is_no_transcript(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """Breaks if a disabled transcript is treated as a crash."""

    class Api:
        def fetch(self, video_id: str, languages: tuple[str, ...] = ()) -> list[Cue]:
            del video_id, languages
            raise error

    monkeypatch.setattr("radar.transcripts.YouTubeTranscriptApi", lambda: Api())

    with pytest.raises(NoTranscript, match=VIDEO):
        fetch_cues(VIDEO)


def test_library_failure_hides_the_response_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if a transcript error message includes the fetched text."""

    class Api:
        def fetch(self, video_id: str, languages: tuple[str, ...] = ()) -> list[Cue]:
            del video_id, languages
            raise RuntimeError("SECRET TRANSCRIPT")

    monkeypatch.setattr("radar.transcripts.YouTubeTranscriptApi", lambda: Api())

    with pytest.raises(TranscriptError, match=VIDEO) as caught:
        fetch_cues(VIDEO)

    assert "SECRET" not in str(caught.value)
