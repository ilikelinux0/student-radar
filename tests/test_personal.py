"""Personal scoring: schema, quoted deadlines, keyword groups, and the sweep path."""

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from radar.config import ConfigError, load_config
from radar.db import connect, get_offer, query_findings
from radar.filter import gate_groups
from radar.models import Config, PersonalScore, Settings, Source
from radar.personal import personal_update, verify_deadline
from radar.scorer import ScoreOutcome, ScoreSpec, ScoringItem, personal_spec
from radar.sweep import run_sweep
from tests.profile_sample import sample_profile

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
FACT_VALUES = ("2001-02-03", "3.17", "Istanbul")


def _personal(**overrides: object) -> PersonalScore:
    values: dict[str, object] = {
        "fit": 8,
        "category": "internship",
        "org": "Example Security",
        "program_name": "Pentest Internship",
        "offer_key": "example-security:pentest-internship",
        "summary": "A long-term pentest internship for students.",
        "reasoning": "It matches an offensive security focus.",
    }
    values.update(overrides)
    return PersonalScore.model_validate(values)


def test_personal_score_defaults_and_normalizes() -> None:
    """Breaks if optional fields become required or codes are not normalized."""
    score = _personal(
        offer_key=" Example-Security:Pentest-Internship ",
        country="tr",
        requirements={"citizenship": ["tr"], "student_years": [3, 4]},
    )

    assert score.offer_key == "example-security:pentest-internship"
    assert score.country == "TR"
    assert score.application_status == "unknown"
    assert score.deadline_kind == "unknown"
    assert score.requirements.citizenship == ["TR"]
    assert score.requirements.max_age is None


def test_null_status_kind_and_requirements_mean_unknown() -> None:
    """Breaks if a null the model writes for "no deadline" rejects the object."""
    score = _personal(application_status=None, deadline_kind=None, requirements=None)

    assert score.application_status == "unknown"
    assert score.deadline_kind == "unknown"
    assert score.requirements.max_age is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"category": "credits"},
        {"fit": 11},
        {"surprise": True},
        {"requirements": {"student_years": [9]}},
        {"requirements": {"favourite_colour": "blue"}},
        {"country": "Turkey"},
        {"summary": "One sentence. Two sentences."},
    ],
)
def test_personal_score_rejects_bad_output(overrides: dict[str, object]) -> None:
    """Breaks if invalid model output is stored instead of retried."""
    with pytest.raises(ValidationError):
        _personal(**overrides)


def test_quoted_deadline_is_kept_after_folding() -> None:
    """Breaks if Turkish case or spacing makes a real quote look invented."""
    score = _personal(
        deadline="2026-11-11",
        deadline_quote="son başvuru:  11 KASIM 2026",
    )

    kept = verify_deadline(score, "Title", "Son Başvuru: 11 Kasım 2026 (17:30)")

    assert kept.deadline == date(2026, 11, 11)


@pytest.mark.parametrize(
    "quote",
    [None, "Son başvuru: 30 Kasım 2026"],
)
def test_deadline_without_a_matching_quote_is_dropped(quote: str | None) -> None:
    """Breaks if a date the excerpt does not state reaches the database."""
    score = _personal(deadline="2026-11-30", deadline_quote=quote)

    dropped = verify_deadline(score, "Title", "Son Başvuru: 11 Kasım 2026")

    assert dropped.deadline is None
    assert dropped.deadline_quote is None


def test_personal_update_maps_columns_and_details() -> None:
    """Breaks if a personal field is lost between the model and the row."""
    update = personal_update(
        _personal(
            value="25.000 TL",
            deadline="2026-11-11",
            deadline_quote="11 Kasım 2026",
            starts_at="2027-02-01",
            remote=False,
            requirements={"student_years": [4]},
        )
    )

    assert update.score == 8
    assert update.category == "internship"
    assert update.amount == "25.000 TL"
    assert update.deadline == "2026-11-11"
    assert update.provider == "Example Security"
    details = json.loads(update.details or "{}")
    assert details["deadline_quote"] == "11 Kasım 2026"
    assert details["starts_at"] == "2027-02-01"
    assert details["remote"] is False
    assert details["requirements"]["student_years"] == [4]


def test_personal_prompt_has_the_summary_and_no_facts() -> None:
    """Breaks if a profile fact is sent to the model."""
    profile = sample_profile()

    spec = personal_spec(profile.fit_summary, "2026-10-11")

    assert spec.schema is PersonalScore
    assert "offensive security" in spec.system
    assert "Today is 2026-10-11." in spec.system
    assert "<page_content>" in spec.system
    for value in FACT_VALUES:
        assert value not in spec.system


def test_gate_groups_needs_every_group() -> None:
    """Breaks if a senior role passes on the field word alone."""
    groups = [["intern", "yeni mezun"], ["security", "yazılım"]]

    assert gate_groups("Senior Security Engineer", "", groups) == []
    assert gate_groups("Security Intern", "", groups) == ["intern", "security"]
    assert gate_groups("Yazılım Stajyeri", "Yeni mezun", groups) == [
        "yeni mezun",
        "yazılım",
    ]


def test_require_names_a_known_group(tmp_path: Path) -> None:
    """Breaks if a typo in require silently keeps nothing."""
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "- id: board\n  name: Board\n  url: https://example.com/jobs\n"
        "  type: api\n  frequency: daily\n  require: [level, secutiry]\n",
        encoding="utf-8",
    )
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        "max_llm_items_per_run: 5\n"
        "keywords: [staj]\nuser_agent: test\nrequest_delay_seconds: 0\n"
        "keyword_groups:\n  level: [intern]\n  security: [security]\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="secutiry"):
        load_config(sources, settings)


def _transport() -> httpx.MockTransport:
    feed = (FIXTURES / "feed.xml").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=feed)

    return httpx.MockTransport(handler)


def _personal_config() -> Config:
    return Config(
        sources=[
            Source(
                id="feed",
                name="Feed",
                url="https://example.com/feed.xml",
                type="rss",
                frequency="daily",
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


def test_personal_sweep_scores_with_the_personal_spec(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Breaks if the personal run uses the credits prompt, drops titles, or logs
    the profile."""
    caplog.set_level(logging.DEBUG)
    profile = sample_profile()
    calls: list[tuple[list[ScoringItem], ScoreSpec]] = []

    def fake_score(
        items: list[ScoringItem],
        settings: Settings,
        client: object,
        *,
        environ: object = None,
        spec: ScoreSpec | None = None,
    ) -> list[ScoreOutcome]:
        del settings, client, environ
        assert spec is not None
        calls.append((items, spec))
        return [
            ScoreOutcome(
                score=_personal(
                    offer_key=f"example:{index}",
                    deadline="2026-12-01",
                    deadline_quote="not in the feed text",
                ),
                attempted=True,
            )
            for index, _item in enumerate(items)
        ]

    conn = connect(tmp_path / "personal.db")
    try:
        summary = run_sweep(
            _personal_config(),
            conn,
            score=fake_score,
            profile=profile,
            transport=_transport(),
            sleeper=lambda _seconds: None,
            now=NOW,
            export_dir=tmp_path / "snapshot",
        )
        rows = query_findings(conn)
        offer = get_offer(conn, "example:0")
        stored = "\n".join(
            str(value)
            for row in conn.execute("SELECT * FROM findings").fetchall()
            for value in tuple(row)
        )
    finally:
        conn.close()

    assert summary.scored == 2
    items, spec = calls[0]
    assert spec.schema is PersonalScore
    assert {item.title for item in items} == {
        "Grant deadline posted",
        "Startup credits expanded",
    }
    assert {row.category for row in rows} == {"internship"}
    assert all(row.deadline is None for row in rows)
    assert all(
        json.loads(row.details or "{}")["deadline_quote"] is None for row in rows
    )
    assert offer is not None
    assert offer.provider == "Example Security"
    for value in (*FACT_VALUES, profile.fit_summary.strip()[:40]):
        assert value not in caplog.text
        assert value not in stored


def test_title_groups_ignore_the_job_description() -> None:
    """Breaks if a senior role passes because its description says graduate."""
    level = ["intern", "new grad", "graduate"]
    tech = ["software", "devops", "security"]
    body = "We hire graduates of computer science. Software skills required."

    assert gate_groups("Sales Director", body, [], [level]) == []
    assert gate_groups("Software Engineer (New Grad)", body, [], [level, tech]) == [
        "new grad",
        "software",
    ]
    assert gate_groups("Security Intern", "", [tech], [level]) == [
        "intern",
        "security",
    ]
