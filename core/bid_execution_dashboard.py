"""Deterministic presentation projection for the Bid execution dashboard."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from core.bid_execution_projection import (
    ExecutionFilter,
    ExecutionItem,
    ExecutionListKind,
    ExecutionProjectionService,
)
from core.bid_repository import BidRepository
from core.deliverable_repository import DeliverableRepository
from core.deliverables import DeliverableCriticality, LifecyclePhase
from core.schemas import Bid


class ExecutionListSlug(StrEnum):
    """Stable URL segments for the execution list screens."""

    RETURNABLES = "returnables"
    MANUFACTURER_INPUTS = "manufacturer-inputs"
    ACTIONS = "actions"
    COMMERCIAL = "commercial"
    OVERDUE = "overdue"


class ExecutionDashboardTile(BaseModel):
    """One frozen dashboard link whose count reconciles to its destination list."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    label: str
    count: int
    url: str
    tone: str
    verdict: str | None = None
    blocking_count: int | None = None


class BidExecutionDashboard(BaseModel):
    """Frozen, clock-free presentation facts for one Bid execution dashboard."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    customer: str
    project_name: str
    bid_reference: str
    submission_deadline: date
    submission_deadline_time: str | None
    submission_deadline_timezone: str | None
    submission_deadline_detail: str
    submission_deadline_label: str
    bid_owner: str
    bid_status_label: str
    days_to_deadline: int
    tiles: tuple[ExecutionDashboardTile, ...]
    post_award_deliverable_count: int
    non_blocking_returnable_count: int
    informational_text: tuple[str, str]
    empty_state: str | None = None


class BidExecutionDashboardNotFoundError(ValueError):
    """Raised when a dashboard is requested for a Bid that does not exist."""


_TILE_SPECS: tuple[tuple[str, str, ExecutionListSlug, ExecutionFilter, str], ...] = (
    (
        "returnables_total",
        "All bid returnables",
        ExecutionListSlug.RETURNABLES,
        ExecutionFilter.ALL,
        "neutral",
    ),
    (
        "returnables_outstanding",
        "Outstanding returnables",
        ExecutionListSlug.RETURNABLES,
        ExecutionFilter.OUTSTANDING,
        "attention",
    ),
    (
        "returnables_complete",
        "Complete returnables",
        ExecutionListSlug.RETURNABLES,
        ExecutionFilter.COMPLETE,
        "positive",
    ),
    (
        "returnables_blocked",
        "Blocked returnables",
        ExecutionListSlug.RETURNABLES,
        ExecutionFilter.BLOCKED,
        "critical",
    ),
    (
        "returnables_unassigned",
        "Unassigned outstanding returnables",
        ExecutionListSlug.RETURNABLES,
        ExecutionFilter.UNASSIGNED,
        "attention",
    ),
    (
        "manufacturer_inputs_outstanding",
        "Outstanding manufacturer inputs",
        ExecutionListSlug.MANUFACTURER_INPUTS,
        ExecutionFilter.OUTSTANDING,
        "attention",
    ),
    (
        "pre_bid_actions_outstanding",
        "Outstanding pre-bid actions",
        ExecutionListSlug.ACTIONS,
        ExecutionFilter.OUTSTANDING,
        "attention",
    ),
    (
        "unresolved_clarifications",
        "Unresolved clarifications",
        ExecutionListSlug.COMMERCIAL,
        ExecutionFilter.CLARIFICATION,
        "critical",
    ),
    (
        "commercial_decisions_needed",
        "Commercial decisions needed",
        ExecutionListSlug.COMMERCIAL,
        ExecutionFilter.DECISION,
        "attention",
    ),
    (
        "overdue",
        "Overdue items",
        ExecutionListSlug.OVERDUE,
        ExecutionFilter.OVERDUE,
        "critical",
    ),
    (
        "blocked",
        "Blocked items",
        ExecutionListSlug.OVERDUE,
        ExecutionFilter.BLOCKED,
        "critical",
    ),
)

_LIST_KINDS: Mapping[ExecutionListSlug, ExecutionListKind] = {
    ExecutionListSlug.RETURNABLES: ExecutionListKind.RETURNABLE,
    ExecutionListSlug.MANUFACTURER_INPUTS: ExecutionListKind.MANUFACTURER_INPUT,
    ExecutionListSlug.ACTIONS: ExecutionListKind.ACTION,
    ExecutionListSlug.COMMERCIAL: ExecutionListKind.COMMERCIAL,
}


def _deadline_detail(bid: Bid) -> str:
    if bid.customer_due_time is None or bid.customer_due_timezone is None:
        return "time not recorded"
    local = bid.customer_due_time
    zone = ZoneInfo(bid.customer_due_timezone)
    zone_name = datetime.combine(bid.customer_due_date, local, zone).tzname()
    zone_label = f" {zone_name}" if zone_name else ""
    return f"{local.strftime('%H:%M')}{zone_label} ({bid.customer_due_timezone})"


def _status_label(bid: Bid) -> str:
    return bid.status.value.replace("_", " ").title()


def _tile_url(
    bid_id: str,
    list_slug: ExecutionListSlug,
    state: ExecutionFilter,
) -> str:
    return f"/bids/{bid_id}/execution/{list_slug.value}?state={state.value}"


def project_bid_execution_dashboard(
    bid: Bid,
    tile_lists: Mapping[str, Sequence[ExecutionItem]],
    *,
    post_award_deliverable_count: int,
    non_blocking_returnable_count: int,
    has_registered_items: bool,
    as_of: date,
) -> BidExecutionDashboard:
    """Build a dashboard only from already-loaded authoritative projection facts."""
    tiles = tuple(
        ExecutionDashboardTile(
            key=key,
            label=label,
            count=len(tile_lists[key]),
            url=_tile_url(bid.bid_id, slug, state),
            tone=tone,
        )
        for key, label, slug, state, tone in _TILE_SPECS
    ) + (
        ExecutionDashboardTile(
            key="readiness",
            label="Submission readiness",
            count=0,
            url=f"/bids/{bid.bid_id}/execution/readiness",
            tone="neutral",
        ),
    )
    deadline_detail = _deadline_detail(bid)
    return BidExecutionDashboard(
        customer=bid.customer,
        project_name=bid.project_name,
        bid_reference=bid.bid_id,
        submission_deadline=bid.customer_due_date,
        submission_deadline_time=(
            bid.customer_due_time.strftime("%H:%M") if bid.customer_due_time else None
        ),
        submission_deadline_timezone=bid.customer_due_timezone,
        submission_deadline_detail=deadline_detail,
        submission_deadline_label=f"{bid.customer_due_date.isoformat()} — {deadline_detail}",
        bid_owner=bid.bc_owner,
        bid_status_label=_status_label(bid),
        days_to_deadline=(bid.customer_due_date - as_of).days,
        tiles=tiles,
        post_award_deliverable_count=post_award_deliverable_count,
        non_blocking_returnable_count=non_blocking_returnable_count,
        informational_text=(
            f"{post_award_deliverable_count} post-award obligations recorded — not bid blockers",
            f"{non_blocking_returnable_count} optional or conditional-inactive returnables "
            "recorded — not bid blockers",
        ),
        empty_state=(
            None
            if has_registered_items
            else "No bid execution items are registered yet; all dashboard counts are zero."
        ),
    )


class ExecutionDashboardService:
    """Load shared filtered lists and build the Bid execution dashboard projection."""

    def __init__(
        self,
        projection: ExecutionProjectionService,
        bid_repository: BidRepository,
        deliverable_repository: DeliverableRepository | None = None,
    ) -> None:
        self.projection = projection
        self.bids = bid_repository
        self.deliverables = deliverable_repository or projection.deliverables

    def filtered_list(
        self,
        bid_id: str,
        list_slug: ExecutionListSlug | str,
        state: ExecutionFilter | str,
        as_of: date,
    ) -> list[ExecutionItem]:
        """Return exactly the filtered list opened by a dashboard tile URL."""
        slug = ExecutionListSlug(list_slug)
        selected_filter = ExecutionFilter(state)
        if slug is ExecutionListSlug.OVERDUE:
            return self.projection.all_items(bid_id, as_of, selected_filter)
        return self.projection.items(bid_id, _LIST_KINDS[slug], selected_filter, as_of)

    def dashboard(self, bid_id: str, as_of: date) -> BidExecutionDashboard:
        """Return a deterministic dashboard using only the caller-provided date."""
        bid = self.bids.get_bid(bid_id)
        if bid is None:
            raise BidExecutionDashboardNotFoundError(
                "Bid not found; return to the Bid list and choose an existing Bid"
            )
        tile_lists = {
            key: self.filtered_list(bid_id, slug, state, as_of)
            for key, _label, slug, state, _tone in _TILE_SPECS
        }
        all_items = self.filtered_list(
            bid_id, ExecutionListSlug.OVERDUE, ExecutionFilter.ALL, as_of
        )
        nonrequired_manufacturer_items = self.filtered_list(
            bid_id,
            ExecutionListSlug.MANUFACTURER_INPUTS,
            ExecutionFilter.NOT_REQUIRED,
            as_of,
        )
        deliverables = self.deliverables.list(bid_id)
        post_award_count = sum(
            LifecyclePhase(str(row["lifecycle_phase"])) is LifecyclePhase.POST_AWARD
            for row in deliverables
        )
        non_blocking_count = sum(
            LifecyclePhase(str(row["lifecycle_phase"]))
            in {LifecyclePhase.WITH_BID, LifecyclePhase.BOTH}
            and (
                DeliverableCriticality(str(row["criticality"]))
                is not DeliverableCriticality.MANDATORY
                or not bool(row["condition_active"])
            )
            for row in deliverables
        )
        return project_bid_execution_dashboard(
            bid,
            tile_lists,
            post_award_deliverable_count=post_award_count,
            non_blocking_returnable_count=non_blocking_count,
            has_registered_items=bool(all_items or nonrequired_manufacturer_items or deliverables),
            as_of=as_of,
        )


__all__ = [
    "BidExecutionDashboard",
    "BidExecutionDashboardNotFoundError",
    "ExecutionDashboardService",
    "ExecutionDashboardTile",
    "ExecutionListSlug",
    "project_bid_execution_dashboard",
]
