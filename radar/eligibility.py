"""Eligibility from posting requirements and profile facts.

Pure code, no model. A reason is built from the requirement ("graduates
only", "requires US citizenship") and never quotes a fact, because reasons
are exported and facts are not.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal

from radar.models import Requirements
from radar.profile import Facts

Status = Literal["eligible", "ineligible", "unclear"]
_CEFR_ORDER = ("A1", "A2", "B1", "B2", "C1", "C2")
_WORK_CATEGORIES = frozenset({"internship", "job", "graduate_program"})


@dataclass(frozen=True)
class Eligibility:
    status: Status
    reason: str | None = None


def evaluate(
    requirements: Requirements | None,
    facts: Facts,
    *,
    reference: date,
    category: str | None = None,
    country: str | None = None,
    remote: bool | None = None,
    starts_at: date | None = None,
) -> Eligibility:
    """Check each stated requirement at the reference date.

    Any clear failure is ineligible. Otherwise anything that cannot be
    checked is unclear. The first reason in each group is the one shown.
    """
    req = requirements or Requirements()
    fails: list[str] = []
    unknown: list[str] = []
    graduated = reference >= facts.graduation
    year = _class_year(facts.graduation, reference)

    if req.students_only and graduated:
        fails.append("students only")
    if req.graduates_only:
        # New-grad roles take final-year applicants and start after
        # graduation. Without a start date, a graduation within a year is a
        # question to check, not a disqualification.
        if starts_at is not None:
            if starts_at < facts.graduation:
                fails.append("graduates only")
        elif not graduated:
            if _months_until(facts.graduation, reference) <= 12:
                unknown.append("graduates only; check the start date")
            else:
                fails.append("graduates only")
    if req.student_years:
        years = sorted(set(req.student_years))
        label = "class year " + ", ".join(str(number) for number in years)
        if graduated or year not in years:
            fails.append(f"for {label} students")
    if req.graduating_by is not None and facts.graduation > req.graduating_by:
        fails.append(f"must graduate by {req.graduating_by.isoformat()}")
    if req.max_years_since_graduation is not None and graduated:
        if _whole_years(facts.graduation, reference) > req.max_years_since_graduation:
            fails.append(
                f"at most {req.max_years_since_graduation} years since graduation"
            )
    if req.max_age is not None and _age(facts.birth_date, reference) > req.max_age:
        fails.append(f"age limit {req.max_age}")
    if req.born_on_or_after is not None and facts.birth_date < req.born_on_or_after:
        fails.append(f"born on or after {req.born_on_or_after.isoformat()}")
    if req.min_gpa is not None:
        if facts.gpa is None:
            unknown.append(f"minimum GPA {req.min_gpa:g}/4.0")
        elif facts.gpa / facts.gpa_scale * 4.0 < req.min_gpa:
            fails.append(f"minimum GPA {req.min_gpa:g}/4.0")
    if req.citizenship and not set(req.citizenship) & set(facts.citizenship):
        fails.append("requires citizenship: " + ", ".join(req.citizenship))
    if req.work_authorization and not set(req.work_authorization) & set(
        facts.work_authorization
    ):
        fails.append(
            "requires work authorization in " + ", ".join(req.work_authorization)
        )
    if req.min_english is not None:
        if facts.english is None:
            unknown.append(f"English {req.min_english} or higher")
        elif _CEFR_ORDER.index(facts.english) < _CEFR_ORDER.index(req.min_english):
            fails.append(f"English {req.min_english} or higher")
    if req.military_service is not None:
        status = _military(req.military_service, facts, reference)
        if status == "fail":
            fails.append(
                "military service must be completed"
                if req.military_service == "completed"
                else "military service must be completed or deferred"
            )
    _abroad(req, facts, category, country, remote, fails, unknown)

    if fails:
        return Eligibility("ineligible", fails[0])
    if unknown:
        return Eligibility("unclear", unknown[0])
    return Eligibility("eligible")


def _abroad(
    req: Requirements,
    facts: Facts,
    category: str | None,
    country: str | None,
    remote: bool | None,
    fails: list[str],
    unknown: list[str],
) -> None:
    home = set(facts.work_authorization) | set(facts.citizenship)
    if country is None or country in home or remote:
        return
    if category in _WORK_CATEGORIES:
        if req.visa_sponsorship is False:
            fails.append(f"in {country}, no visa sponsorship")
        elif req.visa_sponsorship is None:
            unknown.append(f"in {country}, visa sponsorship not stated")
        elif not facts.passport:
            unknown.append(f"in {country}, needs a passport and visa")
        return
    if not facts.passport:
        unknown.append(f"in {country}, needs a passport and visa")


def _military(requirement: str, facts: Facts, reference: date) -> Literal["ok", "fail"]:
    if facts.military_service in ("done", "exempt", "not_applicable"):
        return "ok"
    if requirement == "completed":
        return "fail"
    # completed_or_deferred: a student deferral lasts until it ends.
    ends = facts.deferral_ends or facts.graduation
    return "ok" if reference < ends else "fail"


def _months_until(later: date, earlier: date) -> int:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def _class_year(graduation: date, reference: date) -> int:
    """4 in the last twelve months before graduation, 3 the year before, and so on."""
    months_left = (graduation.year - reference.year) * 12 + (
        graduation.month - reference.month
    )
    if months_left <= 0:
        return 0
    return max(1, 4 - (months_left - 1) // 12)


def _age(birth: date, on: date) -> int:
    return on.year - birth.year - ((on.month, on.day) < (birth.month, birth.day))


def _whole_years(start: date, end: date) -> int:
    return end.year - start.year - ((end.month, end.day) < (start.month, start.day))
