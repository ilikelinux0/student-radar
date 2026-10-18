"""Config loading: valid files load, and bad sources fail with a readable error."""

from pathlib import Path

import pytest

from radar.config import ConfigError, load_config, load_default, needs_playwright
from radar.models import Source

ROOT = Path(__file__).resolve().parents[1]

VALID_SETTINGS = """\
max_llm_items_per_run: 40
keywords:
  - credits
user_agent: "StudentRadar/0.1 (personal research bot)"
request_delay_seconds: 2
"""


def _write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def test_checked_in_config_loads() -> None:
    """Breaks if the repo YAML is rejected or stops writing to the reader's folder."""
    config = load_default(ROOT)

    assert any(source.enabled for source in config.sources)
    assert config.settings.db_path == "data/radar.db"
    assert config.settings.export_dir == "web/public/data"
    assert config.settings.scorer_provider == "gemini"
    assert {"level", "tech"} <= set(config.settings.keyword_groups)
    assert "StudentRadar" in config.settings.user_agent


def test_db_path_defaults_when_omitted(tmp_path: Path) -> None:
    """Breaks if settings without db_path fail to load or use another path."""
    sources = _write(
        tmp_path,
        "sources.yaml",
        """\
- id: example-rss
  name: Example RSS
  url: https://example.com/replace-me
  type: rss
  frequency: daily
  enabled: false
""",
    )
    settings = _write(tmp_path, "settings.yaml", VALID_SETTINGS)

    config = load_config(sources, settings)

    assert config.settings.db_path == "data/radar.db"
    assert config.settings.export_dir == "web/public/data"


def test_unknown_scorer_provider_is_rejected(tmp_path: Path) -> None:
    """Breaks if a provider other than none, gemini, or bedrock is accepted."""
    sources = _write(
        tmp_path,
        "sources.yaml",
        """\
- id: example-rss
  name: Example RSS
  url: https://example.com/replace-me
  type: rss
  frequency: daily
  enabled: false
""",
    )
    settings = _write(
        tmp_path,
        "settings.yaml",
        VALID_SETTINGS + "scorer_provider: openai\n",
    )

    with pytest.raises(ConfigError, match="scorer_provider"):
        load_config(sources, settings)


def test_bad_source_type_names_the_source(tmp_path: Path) -> None:
    """Breaks if an unknown type is accepted or the error hides which source."""
    sources = _write(
        tmp_path,
        "sources.yaml",
        """\
- id: example-rss
  name: Example RSS
  url: https://example.com/replace-me
  type: webhook
  frequency: daily
  enabled: false
""",
    )
    settings = _write(tmp_path, "settings.yaml", VALID_SETTINGS)

    with pytest.raises(ConfigError) as exc:
        load_config(sources, settings)

    message = str(exc.value)
    assert "example-rss" in message
    assert "type" in message


def test_missing_url_names_the_field(tmp_path: Path) -> None:
    """Breaks if a source without a url is accepted."""
    sources = _write(
        tmp_path,
        "sources.yaml",
        """\
- id: example-rss
  name: Example RSS
  type: rss
  frequency: daily
  enabled: false
""",
    )
    settings = _write(tmp_path, "settings.yaml", VALID_SETTINGS)

    with pytest.raises(ConfigError) as exc:
        load_config(sources, settings)

    message = str(exc.value)
    assert "example-rss" in message
    assert "url" in message


def test_duplicate_source_id_is_rejected(tmp_path: Path) -> None:
    """Breaks if two sources can share an id."""
    sources = _write(
        tmp_path,
        "sources.yaml",
        """\
- id: example-rss
  name: Example RSS
  url: https://example.com/replace-me
  type: rss
  frequency: daily
  enabled: false
- id: example-rss
  name: Example RSS again
  url: https://example.com/replace-me/other
  type: api
  frequency: weekly
  enabled: false
""",
    )
    settings = _write(tmp_path, "settings.yaml", VALID_SETTINGS)

    with pytest.raises(ConfigError) as exc:
        load_config(sources, settings)

    message = str(exc.value)
    assert "duplicate" in message
    assert "example-rss" in message


def _source(source_id: str, *, enabled: bool, js: bool) -> Source:
    return Source(
        id=source_id,
        name=source_id,
        url="https://example.com/replace-me",
        type="scrape",
        frequency="daily",
        enabled=enabled,
        js=js,
    )


def test_playwright_is_needed_only_for_an_enabled_js_source() -> None:
    """Breaks if a disabled js source installs a browser, or an enabled one does not."""
    disabled = _source("off", enabled=False, js=True)
    plain = _source("page", enabled=True, js=False)
    browser = _source("app", enabled=True, js=True)
    assert needs_playwright([disabled, plain]) is False
    assert needs_playwright([disabled, plain, browser]) is True


def test_old_panel_keys_are_rejected(tmp_path: Path) -> None:
    """Breaks if a settings file from the two-panel version loads silently."""
    sources = _write(tmp_path, "sources.yaml", "[]\n")
    settings = _write(tmp_path, "settings.yaml", VALID_SETTINGS + "panel: personal\n")

    with pytest.raises(ConfigError, match="panel"):
        load_config(sources, settings)
