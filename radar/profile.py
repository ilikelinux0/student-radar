"""Your profile: what the model may know, and the facts code checks.

This is personal data. Only fit_summary is sent to a model. Facts are compared
by code and are never sent, stored, exported, or logged. Error messages name
the field and never quote the value.
"""

import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from radar.models import Cefr

DEFAULT_PROFILE_PATH = Path("profile.yaml")
PROFILE_ENV = "RADAR_PROFILE"

_COUNTRY = re.compile(r"^[A-Z]{2}$")
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


class ProfileError(Exception):
    """The profile cannot be read. The message never contains profile text."""


class Facts(BaseModel):
    """Eligibility facts. Code compares these with posting requirements."""

    model_config = ConfigDict(extra="forbid")

    birth_date: date
    citizenship: list[str] = Field(min_length=1)
    passport: bool
    work_authorization: list[str] = Field(default_factory=list)
    graduation: date
    gpa: float | None = Field(default=None, gt=0)
    gpa_scale: float = Field(default=4.0, gt=0)
    english: Cefr | None = None
    military_service: Literal["done", "deferred", "exempt", "not_applicable"]
    deferral_ends: date | None = None
    home_city: str = Field(min_length=1)

    @field_validator("citizenship", "work_authorization", mode="before")
    @classmethod
    def _countries(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        codes = [str(item).strip().upper() for item in value]
        for code in codes:
            if _COUNTRY.fullmatch(code) is None:
                raise ValueError("use two-letter country codes")
        return codes

    @field_validator("graduation", mode="before")
    @classmethod
    def _month(cls, value: object) -> object:
        # "2027-06" means the start of that month.
        if isinstance(value, str):
            match = _MONTH.fullmatch(value.strip())
            if match is not None:
                return date(int(match.group(1)), int(match.group(2)), 1)
        return value


class Profile(BaseModel):
    """fit_summary goes to the model. private_terms are scrubbed from its output."""

    model_config = ConfigDict(extra="forbid")

    fit_summary: str = Field(min_length=20)
    private_terms: list[str] = Field(default_factory=list)
    facts: Facts


def load_profile(
    environ: Mapping[str, str], path: Path = DEFAULT_PROFILE_PATH
) -> Profile | None:
    """RADAR_PROFILE wins over the file. Neither one present means no profile."""
    text = environ.get(PROFILE_ENV, "").strip()
    origin = PROFILE_ENV
    if not text:
        if not path.is_file():
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ProfileError(f"{path.name}: cannot read file") from exc
        origin = path.name
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        # The YAML message quotes the line it failed on. Keep only its number.
        mark = getattr(exc, "problem_mark", None)
        where = "" if mark is None else f" near line {mark.line + 1}"
        raise ProfileError(f"{origin}: invalid YAML{where}") from None
    if not isinstance(data, dict):
        raise ProfileError(f"{origin}: expected a mapping")
    try:
        return Profile.model_validate(data)
    except ValidationError as exc:
        raise ProfileError(f"{origin}: {_fields(exc)}") from None


def _fields(exc: ValidationError) -> str:
    """Field names and messages only. Pydantic's input values are dropped."""
    parts: list[str] = []
    for err in exc.errors(include_input=False, include_url=False):
        loc = ".".join(str(part) for part in err["loc"]) or "value"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)
