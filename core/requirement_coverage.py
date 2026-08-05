"""Pure deterministic requirement coverage and exception calculations."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

from core.requirements import (
    ATTENTION_SIGNIFICANCE,
    EXCEPTION_DISPOSITIONS,
    Requirement,
    RequirementCategory,
    RequirementLifecycle,
    RequirementOrigin,
    RequirementReviewState,
    RequirementSignificance,
    RequirementWorkState,
    ResponseDisposition,
)


class CoverageRatio(BaseModel):
    """A numerator/denominator plus half-up percentage, or no-data None."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    percentage: float | None


class RequirementCoverage(BaseModel):
    """Stable active-register counts for one bid or portfolio input set."""

    model_config = ConfigDict(extra="forbid")

    total_active: int
    by_origin: dict[str, int]
    by_category: dict[str, int]
    by_significance: dict[str, int]
    by_work_state: dict[str, int]
    by_review_state: dict[str, int]
    by_disposition: dict[str, int]
    assessed: CoverageRatio
    fully_closed: CoverageRatio
    source_coverage: CoverageRatio
    exception_counts: dict[str, int]
    exception_total: int
    high_attention: int
    due_today: int
    overdue: int
    ownerless: int


def _ratio(numerator: int, denominator: int) -> CoverageRatio:
    if denominator == 0:
        return CoverageRatio(numerator=numerator, denominator=0, percentage=None)
    percentage = float(
        (Decimal(numerator) * Decimal(100) / Decimal(denominator)).quantize(
            Decimal("0.1"),
            rounding=ROUND_HALF_UP,
        )
    )
    return CoverageRatio(
        numerator=numerator,
        denominator=denominator,
        percentage=percentage,
    )


def _counts(values: list[str], vocabulary: list[str]) -> dict[str, int]:
    return {item: values.count(item) for item in vocabulary}


def calculate_requirement_coverage(
    requirements: list[Requirement],
    as_of_date: date,
) -> RequirementCoverage:
    """Calculate coverage without I/O, hidden time, framework, or external state."""
    active = [
        requirement
        for requirement in requirements
        if requirement.lifecycle_state == RequirementLifecycle.ACTIVE
    ]
    denominator = len(active)
    assessed_count = sum(
        requirement.disposition != ResponseDisposition.UNASSESSED for requirement in active
    )
    closed_count = sum(requirement.fully_closed for requirement in active)
    sourced_count = sum(
        requirement.source_document_version_id is not None for requirement in active
    )
    exception_counts = {
        disposition.value: sum(requirement.disposition == disposition for requirement in active)
        for disposition in sorted(EXCEPTION_DISPOSITIONS, key=lambda value: value.value)
    }
    high_attention = sum(
        requirement.significance in ATTENTION_SIGNIFICANCE and not requirement.fully_closed
        for requirement in active
    )
    overdue = sum(
        requirement.due_date is not None
        and requirement.due_date < as_of_date
        and not requirement.fully_closed
        for requirement in active
    )
    due_today = sum(
        requirement.due_date == as_of_date and not requirement.fully_closed
        for requirement in active
    )
    return RequirementCoverage(
        total_active=denominator,
        by_origin=_counts(
            [item.origin.value for item in active],
            [item.value for item in RequirementOrigin],
        ),
        by_category=_counts(
            [item.category.value for item in active],
            [item.value for item in RequirementCategory],
        ),
        by_significance=_counts(
            [item.significance.value for item in active],
            [item.value for item in RequirementSignificance],
        ),
        by_work_state=_counts(
            [item.work_state.value for item in active],
            [item.value for item in RequirementWorkState],
        ),
        by_review_state=_counts(
            [item.review_state.value for item in active],
            [item.value for item in RequirementReviewState],
        ),
        by_disposition=_counts(
            [item.disposition.value for item in active],
            [item.value for item in ResponseDisposition],
        ),
        assessed=_ratio(assessed_count, denominator),
        fully_closed=_ratio(closed_count, denominator),
        source_coverage=_ratio(sourced_count, denominator),
        exception_counts=exception_counts,
        exception_total=sum(exception_counts.values()),
        high_attention=high_attention,
        due_today=due_today,
        overdue=overdue,
        ownerless=sum(requirement.owner is None for requirement in active),
    )
