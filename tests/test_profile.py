"""Profile loading. Values never appear in errors, and a missing profile skips."""

import logging
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from radar.db import connect
from radar.models import Config, Settings
from radar.profile import Profile, ProfileError, load_profile
from radar.sweep import main
from tests.profile_sample import PROFILE_YAML, sample_profile


def test_profile_loads_from_file(tmp_path: Path) -> None:
    """Breaks if the gitignored file is not read, or the month is misparsed."""
    path = tmp_path / "profile.yaml"
    path.write_text(PROFILE_YAML, encoding="utf-8")

    profile = load_profile({}, path)

    assert profile is not None
    assert profile.facts.graduation == date(2027, 6, 1)
    assert profile.facts.citizenship == ["TR"]
    assert profile.facts.english == "C1"
    assert profile.private_terms == ["Zephyrcorp"]


def test_environment_wins_over_file(tmp_path: Path) -> None:
    """Breaks if CI's secret is ignored when a stray local file exists."""
    path = tmp_path / "profile.yaml"
    path.write_text(PROFILE_YAML.replace("gpa: 3.17", "gpa: 2.5"), encoding="utf-8")

    profile = load_profile({"RADAR_PROFILE": PROFILE_YAML}, path)

    assert profile is not None
    assert profile.facts.gpa == 3.17


def test_blank_environment_falls_back_to_file(tmp_path: Path) -> None:
    """Breaks if an empty secret hides the local file."""
    path = tmp_path / "profile.yaml"
    path.write_text(PROFILE_YAML, encoding="utf-8")

    assert load_profile({"RADAR_PROFILE": "  "}, path) is not None


def test_no_profile_is_none(tmp_path: Path) -> None:
    """Breaks if a missing profile is an error instead of a skip."""
    assert load_profile({}, tmp_path / "profile.yaml") is None


def test_invalid_yaml_error_hides_the_text() -> None:
    """Breaks if a YAML error message prints the profile line it failed on."""
    broken = "fit_summary: secret-summary-text\nfacts: [unclosed secret-value\n"

    with pytest.raises(ProfileError) as caught:
        load_profile({"RADAR_PROFILE": broken})

    message = str(caught.value)
    assert "invalid YAML" in message
    assert "secret" not in message
    assert caught.value.__cause__ is None


def test_validation_error_hides_the_values() -> None:
    """Breaks if pydantic's input values leak into the error."""
    leaky = PROFILE_YAML.replace("birth_date: 2001-02-03", "birth_date: secret-date")
    leaky = leaky.replace("english: C1", "english: secret-level")

    with pytest.raises(ProfileError) as caught:
        load_profile({"RADAR_PROFILE": leaky})

    message = str(caught.value)
    assert "facts.birth_date" in message
    assert "facts.english" in message
    assert "secret" not in message
    assert caught.value.__cause__ is None


def test_unknown_profile_keys_are_rejected() -> None:
    """Breaks if a typo in a fact name is silently ignored."""
    with pytest.raises(ProfileError, match="facts.nationality"):
        load_profile({"RADAR_PROFILE": PROFILE_YAML + "  nationality: TR\n"})


def _record_runner(seen: dict[str, object]):  # type: ignore[no-untyped-def]
    def runner(config: Config, connection: sqlite3.Connection, **kwargs: object):  # type: ignore[no-untyped-def]
        del config
        connection.close()
        seen.update(kwargs)

    return runner


def test_run_without_profile_skips(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Breaks if a missing profile fetches anything or fails the CI job."""
    caplog.set_level(logging.INFO)
    calls: list[str] = []

    def loader() -> Config:
        calls.append("loaded")
        raise AssertionError("config must not load without a profile")

    code = main([], loader=loader, profile_loader=lambda: None)

    assert code == 0
    assert calls == []
    assert "sweep skipped" in caplog.text


def test_run_with_bad_profile_exits_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Breaks if a broken profile is treated as a successful run."""

    def broken() -> Profile | None:
        raise ProfileError("RADAR_PROFILE: invalid YAML near line 2")

    code = main([], profile_loader=broken)

    assert code == 2
    assert "invalid YAML" in capsys.readouterr().err


def test_profile_reaches_the_runner(tmp_path: Path) -> None:
    """Breaks if the loaded profile is dropped before the sweep."""
    seen: dict[str, object] = {}
    profile = sample_profile()

    def loader() -> Config:
        return Config(sources=[], settings=_personal_settings())

    def opener(_settings: Settings) -> sqlite3.Connection:
        return connect(tmp_path / "personal.db")

    code = main(
        [],
        runner=_record_runner(seen),
        loader=loader,
        opener=opener,
        profile_loader=lambda: profile,
    )

    assert code == 0
    assert seen["profile"] is profile


def _personal_settings() -> Settings:
    return Settings(
        max_llm_items_per_run=10,
        keywords=["staj"],
        user_agent="StudentRadar/0.1 (personal research bot)",
        request_delay_seconds=0,
    )
