"""Eligibility: requirements against facts, by code, with reasons that hold no facts.

The sample profile: born 2001-02-03, TR citizen, no passport, graduates
2027-06, GPA 3.17/4, English C1, military service deferred.
"""

from datetime import date

import pytest

from radar.eligibility import evaluate
from radar.models import Requirements
from tests.profile_sample import sample_profile

FACTS = sample_profile().facts
OCT_2026 = date(2026, 10, 12)
JULY_2027 = date(2027, 7, 1)


def _check(reference: date = OCT_2026, **requirements: object):  # type: ignore[no-untyped-def]
    return evaluate(
        Requirements.model_validate(requirements), FACTS, reference=reference
    )


def test_no_requirements_is_eligible() -> None:
    """Breaks if silence in a posting hides it."""
    assert evaluate(None, FACTS, reference=OCT_2026).status == "eligible"


@pytest.mark.parametrize(
    ("reference", "requirements", "status", "reason"),
    [
        (
            OCT_2026,
            {"graduates_only": True},
            "unclear",
            "graduates only; check the start date",
        ),
        (date(2025, 10, 1), {"graduates_only": True}, "ineligible", "graduates only"),
        (JULY_2027, {"graduates_only": True}, "eligible", None),
        (OCT_2026, {"students_only": True}, "eligible", None),
        (JULY_2027, {"students_only": True}, "ineligible", "students only"),
        (OCT_2026, {"student_years": [3, 4]}, "eligible", None),
        (
            date(2025, 10, 1),
            {"student_years": [4]},
            "ineligible",
            "for class year 4 students",
        ),
        (JULY_2027, {"student_years": [4]}, "ineligible", "for class year 4 students"),
        (
            OCT_2026,
            {"graduating_by": "2026-09-30"},
            "ineligible",
            "must graduate by 2026-09-30",
        ),
        (OCT_2026, {"graduating_by": "2027-09-30"}, "eligible", None),
        (JULY_2027, {"max_years_since_graduation": 2}, "eligible", None),
        (OCT_2026, {"max_age": 25}, "eligible", None),
        (OCT_2026, {"max_age": 24}, "ineligible", "age limit 24"),
        (OCT_2026, {"born_on_or_after": "2001-01-01"}, "eligible", None),
        (
            OCT_2026,
            {"born_on_or_after": "2002-01-01"},
            "ineligible",
            "born on or after 2002-01-01",
        ),
        (OCT_2026, {"min_gpa": 3.0}, "eligible", None),
        (OCT_2026, {"min_gpa": 3.5}, "ineligible", "minimum GPA 3.5/4.0"),
        (OCT_2026, {"citizenship": ["TR"]}, "eligible", None),
        (OCT_2026, {"citizenship": ["US"]}, "ineligible", "requires citizenship: US"),
        (
            OCT_2026,
            {"work_authorization": ["DE"]},
            "ineligible",
            "requires work authorization in DE",
        ),
        (OCT_2026, {"min_english": "B2"}, "eligible", None),
        (OCT_2026, {"min_english": "C2"}, "ineligible", "English C2 or higher"),
        (
            OCT_2026,
            {"military_service": "completed"},
            "ineligible",
            "military service must be completed",
        ),
        (OCT_2026, {"military_service": "completed_or_deferred"}, "eligible", None),
        (
            JULY_2027,
            {"military_service": "completed_or_deferred"},
            "ineligible",
            "military service must be completed or deferred",
        ),
    ],
)
def test_requirement_rules(
    reference: date,
    requirements: dict[str, object],
    status: str,
    reason: str | None,
) -> None:
    """Breaks if a rule misreads the class year, age, deferral, or a threshold."""
    result = _check(reference, **requirements)

    assert (result.status, result.reason) == (status, reason)


def test_missing_fact_is_unclear() -> None:
    """Breaks if a requirement the profile cannot answer counts as a pass or fail."""
    facts = FACTS.model_copy(update={"gpa": None})

    result = evaluate(Requirements(min_gpa=3.0), facts, reference=OCT_2026)

    assert (result.status, result.reason) == ("unclear", "minimum GPA 3/4.0")


@pytest.mark.parametrize(
    ("category", "sponsorship", "remote", "status"),
    [
        ("internship", None, None, "unclear"),
        ("internship", False, None, "ineligible"),
        ("job", True, None, "unclear"),
        ("job", None, True, "eligible"),
        ("event", None, None, "unclear"),
    ],
)
def test_abroad_needs_sponsorship_or_a_passport(
    category: str, sponsorship: bool | None, remote: bool | None, status: str
) -> None:
    """Breaks if a role in Germany is shown as plainly eligible without a visa path."""
    result = evaluate(
        Requirements(visa_sponsorship=sponsorship),
        FACTS,
        reference=OCT_2026,
        category=category,
        country="DE",
        remote=remote,
    )

    assert result.status == status


def test_home_country_is_not_abroad() -> None:
    """Breaks if a Turkish posting is flagged for sponsorship."""
    result = evaluate(
        Requirements(), FACTS, reference=OCT_2026, category="job", country="TR"
    )

    assert result.status == "eligible"


def test_a_failure_beats_an_unknown() -> None:
    """Breaks if an unclear item hides a clear disqualification."""
    facts = FACTS.model_copy(update={"gpa": None})

    result = evaluate(
        Requirements(min_gpa=3.0, citizenship=["US"]), facts, reference=OCT_2026
    )

    assert (result.status, result.reason) == ("ineligible", "requires citizenship: US")


def test_reasons_never_quote_a_fact() -> None:
    """Breaks if an exported reason carries the birth date, GPA, or graduation month."""
    every = Requirements(
        students_only=True,
        graduates_only=True,
        student_years=[1],
        graduating_by=date(2020, 1, 1),
        max_age=10,
        born_on_or_after=date(2030, 1, 1),
        min_gpa=4.0,
        citizenship=["US"],
        work_authorization=["DE"],
        min_english="C2",
        military_service="completed",
    )
    reasons: list[str] = []
    for reference in (OCT_2026, JULY_2027):
        for field in every.model_fields_set:
            single = Requirements.model_validate({field: getattr(every, field)})
            reason = evaluate(single, FACTS, reference=reference).reason
            if reason:
                reasons.append(reason)

    text = "\n".join(reasons)
    assert reasons
    for value in ("2001", "3.17", "2027-06", "C1", "Istanbul", "deferred"):
        assert value not in text


@pytest.mark.parametrize(
    ("starts_at", "status"),
    [(date(2027, 3, 1), "ineligible"), (date(2027, 9, 1), "eligible")],
)
def test_graduates_only_uses_the_start_date(starts_at: date, status: str) -> None:
    """Breaks if a new-grad role starting after graduation is ruled out today."""
    result = evaluate(
        Requirements(graduates_only=True),
        FACTS,
        reference=starts_at,
        starts_at=starts_at,
    )

    assert result.status == status
