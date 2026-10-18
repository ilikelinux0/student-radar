"""Local .env loading. Values are never logged."""

import logging
from pathlib import Path

import pytest

from radar.envfile import load_env_file


def test_env_file_fills_missing_names_and_does_not_override(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Breaks if a .env value overrides the process or is written to the log."""
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            [
                "# comment",
                "GEMINI_API_KEY=from-file",
                "GEMINI_MODEL=gemini-3.1-flash-lite",
                "ALREADY=from-file",
                "",
            ]
        ),
        encoding="utf-8",
    )
    environ = {"ALREADY": "from-process"}
    caplog.set_level(logging.DEBUG)

    load_env_file(path, environ)

    assert environ["GEMINI_API_KEY"] == "from-file"
    assert environ["GEMINI_MODEL"] == "gemini-3.1-flash-lite"
    assert environ["ALREADY"] == "from-process"
    assert "from-file" not in caplog.text


def test_missing_env_file_is_ignored(tmp_path: Path) -> None:
    """Breaks if a missing .env stops the sweep from starting."""
    environ: dict[str, str] = {}
    load_env_file(tmp_path / ".env", environ)
    assert environ == {}
