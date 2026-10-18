"""Pydantic models at the config, fetch, and database boundaries."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FindingStatus = Literal["new", "seen", "applied", "ignored"]
SourceStatus = Literal["ok", "error"]
SourceType = Literal[
    "rss",
    "api",
    "scrape",
    "news_search",
    "hn_search",
    "reddit_search",
    "github_watch",
    "youtube_channel",
]
NewsLang = Literal["en-US", "tr-TR"]

_PAGE_TYPES = frozenset({"rss", "api", "scrape"})
_SUBREDDIT = re.compile(r"^[A-Za-z0-9_]{2,21}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_OFFER_KEY = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*:[a-z0-9]+(?:-[a-z0-9]+)*$")
_REGION_FOR_LANG = {"en-US": "US", "tr-TR": "TR"}
# A mark ends a sentence only before a space or the end, so "$1.5M", "Node.js",
# and "aws.amazon.com" stay inside one sentence. A mark after a lone letter
# ("e.g.", "U.K.") is an abbreviation.
_SENTENCE_END = re.compile(r"(?<!\b[A-Za-z])[.!?]+[\"')\]]*(?=\s|$)")


class ApiFields(BaseModel):
    """JSON keys for an API source. Omitted keys default to title, url, and summary.

    Keys may be dotted paths (location.name). url_template turns an id into a
    public link, for boards whose list has no URL: {id} is replaced.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = "title"
    url: str = "url"
    summary: str = "summary"
    location: str | None = None
    posted: str | None = None
    url_template: str | None = None

    @field_validator("url_template")
    @classmethod
    def _template(cls, value: str | None) -> str | None:
        if value is not None and (
            "{id}" not in value or not value.startswith("https://")
        ):
            raise ValueError("url_template must be an https URL containing {id}")
        return value


class DiscoveryQueries(BaseModel):
    """Shared search phrases. A source points at en or tr instead of copying them."""

    model_config = ConfigDict(extra="forbid")

    en: list[str] = Field(default_factory=list)
    tr: list[str] = Field(default_factory=list)


class Source(BaseModel):
    """One watched page, feed, API, or discovery search. URLs come from YAML."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    url: str = ""
    type: SourceType
    frequency: Literal["daily", "weekly"]
    js: bool = False
    selector: str | None = None
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    items_path: str | None = None
    fields: ApiFields = Field(default_factory=ApiFields)
    queries: list[str] = Field(default_factory=list)
    query_group: Literal["en", "tr"] | None = None
    lang: NewsLang | None = None
    region: Literal["US", "TR"] | None = None
    subreddits: list[str] = Field(default_factory=list)
    stories: bool = True
    comments: bool = False
    repo: str | None = None
    channel_id: str | None = None
    max_items_per_run: int | None = Field(default=None, gt=0)
    require: list[str] = Field(default_factory=list)
    require_title: list[str] = Field(default_factory=list)
    closes_missing: bool = False
    usually_opens: str | None = Field(default=None, max_length=40)

    @field_validator("repo")
    @classmethod
    def _repo_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _REPO.fullmatch(value) is None or ".." in value.split("/"):
            raise ValueError("repo must be owner/name")
        return value

    @field_validator("channel_id")
    @classmethod
    def _channel_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _CHANNEL_ID.fullmatch(value) is None:
            raise ValueError("channel_id must be a YouTube channel id")
        return value

    @field_validator("subreddits")
    @classmethod
    def _subreddit_names(cls, value: list[str]) -> list[str]:
        for name in value:
            if _SUBREDDIT.fullmatch(name) is None:
                raise ValueError(f"invalid subreddit {name}")
        return value

    @model_validator(mode="after")
    def _check_type_fields(self) -> Self:
        if self.type in _PAGE_TYPES and not self.url.strip():
            raise ValueError("url is required")
        if self.type == "news_search":
            if self.lang is None:
                raise ValueError("lang is required")
            expected = _REGION_FOR_LANG[self.lang]
            if self.region is not None and self.region != expected:
                raise ValueError("region does not match lang")
        if self.type == "hn_search" and not self.stories and not self.comments:
            raise ValueError("stories or comments must be enabled")
        if self.type == "reddit_search" and not self.subreddits:
            raise ValueError("subreddits is required")
        if self.type == "github_watch" and self.repo is None:
            raise ValueError("repo is required")
        if self.type == "youtube_channel" and self.channel_id is None:
            raise ValueError("channel_id is required")
        if self.closes_missing and self.type != "api":
            raise ValueError("closes_missing is only for api job boards")
        return self


class Settings(BaseModel):
    """Run settings from settings.yaml. Secrets stay in environment variables."""

    model_config = ConfigDict(extra="forbid")

    scorer_provider: Literal["none", "gemini", "bedrock"] = "none"
    max_llm_items_per_run: int = Field(gt=0)
    llm_batch_size: int = Field(default=8, gt=0)
    keywords: list[str] = Field(min_length=1)
    user_agent: str = Field(min_length=1)
    request_delay_seconds: float = Field(ge=0)
    db_path: str = Field(default="data/radar.db", min_length=1)
    export_dir: str = Field(default="web/public/data", min_length=1)
    max_items_per_source: int = Field(default=20, gt=0)
    max_new_items_per_run: int = Field(default=50, gt=0)
    max_transcripts_per_run: int = Field(default=8, gt=0)
    discovery_queries: DiscoveryQueries = Field(default_factory=DiscoveryQueries)
    keyword_groups: dict[str, list[str]] = Field(default_factory=dict)


_MAX_SUMMARY_CHARS = 500
_MAX_REASONING_CHARS = 1000
_MAX_AMOUNT_CHARS = 200


Cefr = Literal["A1", "A2", "B1", "B2", "C1", "C2"]
PersonalCategory = Literal[
    "internship",
    "job",
    "graduate_program",
    "grant",
    "training",
    "certification",
    "event",
    "competition",
    "other",
]
_COUNTRY_CODE = re.compile(r"^[A-Z]{2}$")
_MAX_QUOTE_CHARS = 200
_MAX_PLACE_CHARS = 120


class Requirements(BaseModel):
    """What a posting says it requires. None means the posting does not say."""

    model_config = ConfigDict(extra="forbid")

    student_years: list[int] | None = None
    students_only: bool | None = None
    graduates_only: bool | None = None
    graduating_by: date | None = None
    max_years_since_graduation: int | None = Field(default=None, ge=0, le=20)
    max_age: int | None = Field(default=None, ge=10, le=100)
    born_on_or_after: date | None = None
    min_gpa: float | None = Field(default=None, ge=0, le=4)
    citizenship: list[str] | None = None
    work_authorization: list[str] | None = None
    min_english: Cefr | None = None
    military_service: Literal["completed", "completed_or_deferred"] | None = None
    visa_sponsorship: bool | None = None

    @field_validator("student_years")
    @classmethod
    def _years(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and any(year < 1 or year > 6 for year in value):
            raise ValueError("student_years must be class years 1 to 6")
        return value

    @field_validator("citizenship", "work_authorization", mode="before")
    @classmethod
    def _countries(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [_country_code(item) for item in value]


class PersonalScore(BaseModel):
    """Validated model JSON. Extra fields are rejected so output stays data.

    Eligibility is not here: code decides it.
    """

    model_config = ConfigDict(extra="forbid")

    fit: int = Field(ge=0, le=10)
    category: PersonalCategory
    org: str = Field(min_length=1, max_length=80)
    program_name: str = Field(min_length=1, max_length=120)
    offer_key: str = Field(min_length=3, max_length=120)
    summary: str
    reasoning: str
    value: str | None = None
    application_status: Literal["open", "not_yet_open", "closed", "unknown"] = "unknown"
    deadline: date | None = None
    deadline_quote: str | None = Field(default=None, max_length=_MAX_QUOTE_CHARS)
    deadline_kind: Literal["apply_by", "event_date", "rolling", "unknown"] = "unknown"
    starts_at: date | None = None
    ends_at: date | None = None
    location: str | None = Field(default=None, max_length=_MAX_PLACE_CHARS)
    country: str | None = None
    remote: bool | None = None
    requirements: Requirements = Field(default_factory=Requirements)

    @field_validator("offer_key", mode="before")
    @classmethod
    def _offer_key(cls, value: object) -> object:
        return _check_offer_key(value)

    @field_validator("org", "program_name", mode="before")
    @classmethod
    def _trimmed_label(cls, value: object) -> object:
        return _trimmed(value)

    @field_validator("value")
    @classmethod
    def _value_length(cls, value: str | None) -> str | None:
        return _check_amount(value)

    @field_validator("summary")
    @classmethod
    def _one_sentence(cls, value: str) -> str:
        return _check_summary(value)

    @field_validator("reasoning")
    @classmethod
    def _two_sentences(cls, value: str) -> str:
        return _check_reasoning(value)

    @field_validator("country", mode="before")
    @classmethod
    def _country(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return _country_code(value)

    @field_validator("application_status", "deadline_kind", mode="before")
    @classmethod
    def _null_is_unknown(cls, value: object) -> object:
        # Without a deadline the model often answers null here. Rejecting that
        # sent a whole batch back one item at a time.
        return "unknown" if value is None else value

    @field_validator("requirements", mode="before")
    @classmethod
    def _null_requirements(cls, value: object) -> object:
        return {} if value is None else value


def _check_offer_key(value: object) -> object:
    if not isinstance(value, str):
        return value
    key = value.strip().lower()
    if _OFFER_KEY.fullmatch(key) is None:
        raise ValueError("offer_key must be provider:program")
    return key


def _trimmed(value: object) -> object:
    if not isinstance(value, str):
        return value
    return value.strip()


def _check_amount(value: str | None) -> str | None:
    if value is not None and len(value) > _MAX_AMOUNT_CHARS:
        raise ValueError("amount is too long")
    return value


def _check_summary(value: str) -> str:
    if len(value) > _MAX_SUMMARY_CHARS:
        raise ValueError("summary is too long")
    if _sentence_count(value) != 1:
        raise ValueError("summary must be exactly 1 sentence")
    return value


def _check_reasoning(value: str) -> str:
    if len(value) > _MAX_REASONING_CHARS:
        raise ValueError("reasoning is too long")
    count = _sentence_count(value)
    if count < 1 or count > 2:
        raise ValueError("reasoning must be 1 or 2 sentences")
    return value


def _country_code(value: object) -> str:
    code = str(value).strip().upper()
    if _COUNTRY_CODE.fullmatch(code) is None:
        raise ValueError("country must be a two-letter code")
    return code


def _sentence_count(value: str) -> int:
    stripped = value.strip()
    if not stripped:
        return 0
    count = 0
    for match in _SENTENCE_END.finditer(stripped):
        following = stripped[match.end() :].lstrip()
        # "U.S. nonprofits" and "approx. five": a lowercase word goes on.
        if following and following[0].islower():
            continue
        count += 1
    return count or 1


@dataclass(frozen=True)
class RawItem:
    """One feed or API entry before hashing. Scrapes do not use this."""

    title: str
    url: str
    summary: str
    loose: bool = False


@dataclass(frozen=True)
class Item:
    """A fetched item with its content hash. The summary keeps original casing."""

    title: str
    url: str
    summary: str
    content_hash: str


@dataclass(frozen=True)
class Config:
    """Validated sources plus settings."""

    sources: list[Source]
    settings: Settings


@dataclass(frozen=True)
class SourceState:
    """Last check result for one source."""

    source_id: str
    last_checked_at: datetime | None
    last_status: SourceStatus | None
    last_error: str | None
    consecutive_failures: int


@dataclass(frozen=True)
class Snapshot:
    """Latest main-text snapshot for one scraped source."""

    source_id: str
    content_hash: str
    text: str
    fetched_at: datetime


@dataclass(frozen=True)
class NewFinding:
    """A finding before it is stored. Score and status are filled by the database."""

    source_id: str
    title: str
    url: str
    content_hash: str
    excerpt: str
    found_at: datetime
    keyword_hits: list[str]


@dataclass(frozen=True)
class Finding:
    """One stored finding."""

    id: int
    source_id: str
    title: str
    url: str
    content_hash: str
    excerpt: str
    found_at: datetime
    keyword_hits: list[str]
    score: int | None
    category: str | None
    amount: str | None
    deadline: str | None
    summary: str | None
    reasoning: str | None
    emailed_at: datetime | None
    status: FindingStatus
    offer_key: str | None = None
    details: str | None = None


@dataclass(frozen=True)
class Offer:
    """One program, grouped from one or more findings."""

    offer_key: str
    provider: str | None
    program_name: str | None
    best_score: int | None
    amount: str | None
    deadline: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    mention_count: int
    status: FindingStatus
    closed_at: datetime | None = None


@dataclass(frozen=True)
class ScoreUpdate:
    """Fields written when a finding is scored. None score means not scored."""

    score: int | None
    category: str | None = None
    amount: str | None = None
    deadline: str | None = None
    summary: str | None = None
    reasoning: str | None = None
    offer_key: str | None = None
    provider: str | None = None
    program_name: str | None = None
    details: str | None = None


@dataclass(frozen=True)
class FindingQuery:
    """Filters for the findings list. None means that filter is off."""

    min_score: int | None = None
    category: str | None = None
    source_id: str | None = None
    status: FindingStatus | None = None
    start: datetime | None = None
    end: datetime | None = None
    text: str | None = None
