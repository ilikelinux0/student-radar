"""Load and validate sources.yaml and settings.yaml.

Environment variables are not read here. Scoring reads them when that
step runs, so checking config does not require model credentials.
"""

from collections.abc import Sequence
from pathlib import Path

import yaml
from pydantic import ValidationError

from radar.models import Config, Settings, Source
from radar.queries import SEARCH_TYPES, effective_queries

SOURCES_FILE = "sources.yaml"
SETTINGS_FILE = "settings.yaml"


class ConfigError(Exception):
    """Raised when sources.yaml or settings.yaml cannot be loaded."""


def load_config(sources_path: Path, settings_path: Path) -> Config:
    """Load both YAML files and return validated config."""
    sources = _load_sources(sources_path)
    settings = _load_settings(settings_path)
    _require_queries(sources_path, sources, settings)
    _require_groups(sources_path, sources, settings)
    return Config(sources=sources, settings=settings)


def load_default(root: Path = Path(".")) -> Config:
    """Load sources.yaml and settings.yaml from root."""
    return load_config(root / SOURCES_FILE, root / SETTINGS_FILE)


def needs_playwright(sources: Sequence[Source]) -> bool:
    """True when an enabled source renders with a browser."""
    return any(source.enabled and source.js for source in sources)


def _load_yaml(path: Path) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path.name}: cannot read file ({exc.strerror})") from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name}: invalid YAML") from exc


def _load_sources(path: Path) -> list[Source]:
    data = _load_yaml(path)
    if not isinstance(data, list):
        raise ConfigError(f"{path.name}: expected a list of sources")
    sources: list[Source] = []
    seen: set[str] = set()
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ConfigError(f"{path.name}: source at index {index} must be a mapping")
        label = _source_label(entry, index)
        try:
            source = Source.model_validate(entry)
        except ValidationError as exc:
            raise ConfigError(f"{path.name}: {label}: {_summarize(exc)}") from exc
        if source.id in seen:
            raise ConfigError(f"{path.name}: duplicate source id {source.id!r}")
        seen.add(source.id)
        sources.append(source)
    return sources


def _load_settings(path: Path) -> Settings:
    data = _load_yaml(path)
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: expected a mapping")
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path.name}: {_summarize(exc)}") from exc


def _require_queries(path: Path, sources: list[Source], settings: Settings) -> None:
    for source in sources:
        if source.type not in SEARCH_TYPES:
            continue
        if effective_queries(source, settings):
            continue
        raise ConfigError(
            f"{path.name}: source {source.id!r}: queries: at least one is required"
        )


def _require_groups(path: Path, sources: list[Source], settings: Settings) -> None:
    for source in sources:
        for field, names in (
            ("require", source.require),
            ("require_title", source.require_title),
        ):
            for name in names:
                if settings.keyword_groups.get(name):
                    continue
                raise ConfigError(
                    f"{path.name}: source {source.id!r}: {field}: "
                    f"no keyword group named {name!r}"
                )


def _source_label(entry: dict[str, object], index: int) -> str:
    source_id = entry.get("id")
    if isinstance(source_id, str) and source_id:
        return f"source {source_id!r}"
    return f"source at index {index}"


def _summarize(exc: ValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"]) or "value"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)
