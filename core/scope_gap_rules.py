# ruff: noqa: E501, B023, B007, UP042, I001
"""Pure deterministic TASK-10 gap and coverage calculations."""

from datetime import date
from enum import Enum
from pydantic import BaseModel, ConfigDict

from core.scope_interfaces import (
    CustomerNeed,
    DependencyState,
    InterfaceRecord,
    LifecycleState,
    Materiality,
    OfferPosition,
    PricingState,
    ScopeItem,
    ScopeOrigin,
    ScopeRelevance,
    WorkState,
    ReviewState,
)


class GapSeverity(str, Enum):
    BLOCKING_ATTENTION = "BLOCKING_ATTENTION"
    ADVISORY = "ADVISORY"


class Gap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    code: str
    entity_type: str
    entity_id: str
    bid_id: str
    severity: GapSeverity
    explanation: str
    due_date: date | None = None


class Ratio(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    numerator: int
    denominator: int
    percentage_basis_points: int
    has_population: bool


class ScopeCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active_scope_items: int
    assessed_customer_need: int
    included: int
    excluded: int
    option: int
    undecided: int
    included_priced: int
    required_assigned: int
    fully_closed: int
    active_interfaces: int
    fully_closed_interfaces: int
    gaps: list[Gap]
    ratios: dict[str, Ratio]


def _severity(item_materiality: Materiality, blocking: bool = True) -> GapSeverity:
    return (
        GapSeverity.BLOCKING_ATTENTION
        if blocking or item_materiality in (Materiality.MATERIAL, Materiality.UNASSESSED)
        else GapSeverity.ADVISORY
    )


def evaluate_gaps(
    scope_items: list[ScopeItem],
    interfaces: list[InterfaceRecord],
    requirement_assessments: dict[str, ScopeRelevance] | None = None,
    requirement_links: dict[str, list[str]] | None = None,
    requirement_summaries: dict[str, dict[str, object]] | None = None,
    as_of_date: date | None = None,
) -> list[Gap]:
    """Return stable, ordered gaps; no I/O or implicit clock is used."""
    today = as_of_date
    assessments = requirement_assessments or {}
    links = requirement_links or {}
    summaries = requirement_summaries or {}
    gaps: list[Gap] = []
    for item in sorted(
        (x for x in scope_items if x.lifecycle_state == LifecycleState.ACTIVE),
        key=lambda x: (x.bid_id, x.scope_item_id),
    ):

        def add(code: str, text: str, blocking: bool = True) -> None:
            gaps.append(
                Gap(
                    code=code,
                    entity_type="scope_item",
                    entity_id=item.scope_item_id,
                    bid_id=item.bid_id,
                    severity=_severity(item.materiality, blocking),
                    explanation=text,
                    due_date=item.due_date,
                )
            )

        if item.customer_need == CustomerNeed.REQUIRED and not item.responsible_party:
            add("REQUIRED_UNASSIGNED", "Required scope has no responsible party.")
        if (
            item.customer_need == CustomerNeed.REQUIRED
            and item.offer_position == OfferPosition.UNDECIDED
        ):
            add("REQUIRED_OFFER_UNDECIDED", "Required scope has no offer position.")
        if (
            item.customer_need == CustomerNeed.REQUIRED
            and item.offer_position == OfferPosition.EXCLUDED
        ):
            add("REQUIRED_EXCLUDED", "Required scope is explicitly excluded.")
        if item.offer_position == OfferPosition.INCLUDED and item.pricing_state in (
            PricingState.UNCONFIRMED,
            PricingState.NOT_PRICED,
        ):
            add("INCLUDED_UNPRICED", "Included scope has unresolved pricing.")
        if item.customer_need == CustomerNeed.NOT_REQUIRED and (
            item.pricing_state in (PricingState.PRICED, PricingState.ALLOWANCED)
            or item.offer_position in (OfferPosition.INCLUDED, OfferPosition.OPTION)
        ):
            add(
                "PRICED_NOT_REQUIRED",
                "Pricing or coverage exists for scope marked not required.",
                False,
            )
        if (
            item.customer_need in (CustomerNeed.UNASSESSED, CustomerNeed.UNCLEAR)
            or item.materiality == Materiality.UNASSESSED
            or item.offer_position == OfferPosition.UNDECIDED
        ):
            add("SCOPE_UNASSESSED", "Scope assessment remains unresolved.")
        linked = links.get(item.scope_item_id, [])
        if item.origin == ScopeOrigin.REQUIREMENT_DERIVED and not linked:
            add(
                "REQUIREMENT_DERIVED_UNLINKED",
                "Requirement-derived scope has no active requirement link.",
            )
        if (
            item.work_state in (WorkState.READY_FOR_REVIEW, WorkState.COMPLETE)
            and item.review_state != ReviewState.ACCEPTED
        ):
            add("SCOPE_REVIEW_INCOMPLETE", "Scope is awaiting independent review.", False)
        if today and item.due_date and item.due_date < today and not item.fully_closed:
            add("SCOPE_OVERDUE", "Scope closure is overdue.")
        if item.owner is None and (
            item.materiality in (Materiality.MATERIAL, Materiality.UNASSESSED)
            or item.customer_need == CustomerNeed.REQUIRED
        ):
            add("SCOPE_OWNER_MISSING", "High-attention scope has no internal owner.")
    for req_id, relevance in sorted(assessments.items()):
        summary = summaries.get(req_id, {})
        bid_id = str(summary.get("bid_id", ""))
        lifecycle = str(summary.get("lifecycle_state", "ACTIVE"))
        if lifecycle != "ACTIVE":
            continue
        if relevance == ScopeRelevance.UNASSESSED:
            gaps.append(
                Gap(
                    code="REQUIREMENT_SCOPE_UNASSESSED",
                    entity_type="requirement",
                    entity_id=req_id,
                    bid_id=bid_id,
                    severity=GapSeverity.BLOCKING_ATTENTION,
                    explanation="Requirement scope relevance is unassessed.",
                )
            )
        elif relevance == ScopeRelevance.APPLICABLE and not links.get(req_id):
            gaps.append(
                Gap(
                    code="APPLICABLE_REQUIREMENT_UNMAPPED",
                    entity_type="requirement",
                    entity_id=req_id,
                    bid_id=bid_id,
                    severity=GapSeverity.BLOCKING_ATTENTION,
                    explanation="Applicable requirement has no active scope mapping.",
                )
            )
        for item_id in links.get(req_id, []):
            if str(summary.get("source_health", "HEALTHY")) != "HEALTHY":
                gaps.append(
                    Gap(
                        code="REQUIREMENT_SOURCE_DEGRADED",
                        entity_type="requirement",
                        entity_id=req_id,
                        bid_id=bid_id,
                        severity=GapSeverity.BLOCKING_ATTENTION,
                        explanation="Existing mapping uses degraded source evidence.",
                    )
                )
    for interface in sorted(
        (x for x in interfaces if x.lifecycle_state == LifecycleState.ACTIVE),
        key=lambda x: (x.bid_id, x.interface_id),
    ):
        linked = links.get(interface.interface_id, [])

        def add_i(code: str, text: str, blocking: bool = True) -> None:
            gaps.append(
                Gap(
                    code=code,
                    entity_type="interface",
                    entity_id=interface.interface_id,
                    bid_id=interface.bid_id,
                    severity=_severity(interface.materiality, blocking),
                    explanation=text,
                    due_date=interface.due_date,
                )
            )

        if interface.owner is None and interface.materiality in (
            Materiality.MATERIAL,
            Materiality.UNASSESSED,
        ):
            add_i("INTERFACE_OWNER_MISSING", "Interface has no internal owner.")
        if interface.dependency_state == DependencyState.OPEN:
            add_i("INTERFACE_DEPENDENCY_OPEN", "Interface dependency remains open.")
        if not linked and interface.materiality in (Materiality.MATERIAL, Materiality.UNASSESSED):
            add_i("INTERFACE_SCOPE_UNLINKED", "Interface has no active scope link.")
        if (
            interface.work_state in (WorkState.READY_FOR_REVIEW, WorkState.COMPLETE)
            and interface.review_state != ReviewState.ACCEPTED
        ):
            add_i("INTERFACE_REVIEW_INCOMPLETE", "Interface is awaiting independent review.", False)
        if (
            today
            and interface.due_date
            and interface.due_date < today
            and not interface.fully_closed
        ):
            add_i("INTERFACE_OVERDUE", "Interface closure is overdue.")
    return sorted(gaps, key=lambda g: (g.bid_id, g.entity_type, g.entity_id, g.code))


def _ratio(n: int, d: int) -> Ratio:
    return Ratio(
        numerator=n,
        denominator=d,
        percentage_basis_points=(n * 10000 // d if d else 0),
        has_population=bool(d),
    )


def calculate_coverage(
    scope_items: list[ScopeItem], interfaces: list[InterfaceRecord], gaps: list[Gap]
) -> ScopeCoverage:
    active = [x for x in scope_items if x.lifecycle_state == LifecycleState.ACTIVE]
    ints = [x for x in interfaces if x.lifecycle_state == LifecycleState.ACTIVE]
    assessed = [
        x for x in active if x.customer_need not in (CustomerNeed.UNASSESSED, CustomerNeed.UNCLEAR)
    ]
    included = [x for x in active if x.offer_position == OfferPosition.INCLUDED]
    required = [x for x in active if x.customer_need == CustomerNeed.REQUIRED]
    ratios = {
        "customer_need_assessed": _ratio(len(assessed), len(active)),
        "fully_closed_scope": _ratio(sum(x.fully_closed for x in active), len(active)),
        "fully_closed_interfaces": _ratio(sum(x.fully_closed for x in ints), len(ints)),
    }
    return ScopeCoverage(
        active_scope_items=len(active),
        assessed_customer_need=len(assessed),
        included=len(included),
        excluded=sum(x.offer_position == OfferPosition.EXCLUDED for x in active),
        option=sum(x.offer_position == OfferPosition.OPTION for x in active),
        undecided=sum(x.offer_position == OfferPosition.UNDECIDED for x in active),
        included_priced=sum(
            x.pricing_state
            in (PricingState.PRICED, PricingState.ALLOWANCED, PricingState.NO_CHARGE)
            for x in included
        ),
        required_assigned=sum(bool(x.responsible_party) for x in required),
        fully_closed=sum(x.fully_closed for x in active),
        active_interfaces=len(ints),
        fully_closed_interfaces=sum(x.fully_closed for x in ints),
        gaps=gaps,
        ratios=ratios,
    )
