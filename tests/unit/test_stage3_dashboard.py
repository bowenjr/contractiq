"""Unit coverage for the Stage 3 Bid execution dashboard projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest

from core.bid_execution import ExecutionSubjectKind
from core.bid_execution_dashboard import ExecutionDashboardService
from core.bid_execution_projection import ExecutionProjectionService
from core.bid_execution_repository import BidExecutionRepository
from core.bid_execution_service import BidExecutionService
from core.bid_execution_subjects import ExecutionSubjectResolver
from core.bid_execution_trace import BidExecutionTrace
from core.bid_repository import BidRepository
from core.database import Database
from core.deliverable_repository import DeliverableRepository
from core.deliverables import (
    Deliverable,
    DeliverableCriticality,
    DeliverableDirection,
    DeliverableState,
    LifecyclePhase,
)
from core.document_repository import DocumentRepository
from core.ops08 import NegotiationState, Ops08Repository, PositionDisposition, PositionRevision
from core.schemas import Bid, Provenance
from core.vendor_document_control import CustomerRequirementCreate, SupplierPackageCreate
from core.vendor_document_repository import VendorDocumentRepository
from core.vendor_document_service import VendorDocumentService
from core.work_item_repository import WorkItemRepository
from core.work_item_service import WorkItemService

NOW = datetime(2026, 7, 10, 14, 30, tzinfo=UTC)
AS_OF = date(2026, 7, 10)


@dataclass(frozen=True)
class DashboardContext:
    bid: Bid
    bids: BidRepository
    work: WorkItemService
    deliverables: DeliverableRepository
    vendors: VendorDocumentService
    execution: BidExecutionService
    ops08: Ops08Repository
    dashboard: ExecutionDashboardService


@pytest.fixture
def context(tmp_db: Database, bid_repo: BidRepository, valid_bid: Bid) -> DashboardContext:
    bid_repo.create_bid(valid_bid)
    work_repository = WorkItemRepository(tmp_db)
    deliverables = DeliverableRepository(tmp_db)
    vendor_repository = VendorDocumentRepository(tmp_db)
    execution_repository = BidExecutionRepository(tmp_db)
    execution = BidExecutionService(
        execution_repository,
        bid_repo,
        DocumentRepository(tmp_db),
        ExecutionSubjectResolver(work_repository, deliverables, vendor_repository),
        now_factory=lambda: NOW,
    )
    ids = iter(UUID(int=value) for value in range(700, 1000))
    work = WorkItemService(
        work_repository,
        bid_repo,
        now_factory=lambda: NOW,
        id_factory=lambda: next(ids),
    )
    vendors = VendorDocumentService(
        vendor_repository,
        bid_repo,
        now_factory=lambda: NOW,
        id_factory=lambda: next(ids),
    )
    ops08 = Ops08Repository(tmp_db)
    projection = ExecutionProjectionService(
        work_repository,
        deliverables,
        vendor_repository,
        execution_repository,
        ops08,
        bid_repo,
        BidExecutionTrace(tmp_db),
    )
    return DashboardContext(
        valid_bid,
        bid_repo,
        work,
        deliverables,
        vendors,
        execution,
        ops08,
        ExecutionDashboardService(projection, bid_repo, deliverables),
    )


def _returnable(
    context: DashboardContext,
    deliverable_id: str,
    title: str,
    **values: object,
) -> str:
    payload: dict[str, object] = {
        "deliverable_id": deliverable_id,
        "bid_id": context.bid.bid_id,
        "title": title,
        "description": f"Prepare {title}",
        "category": "Submission",
        "criticality": DeliverableCriticality.MANDATORY,
        "lifecycle_phase": LifecyclePhase.WITH_BID,
        "direction": DeliverableDirection.INTERNAL,
        "workflow_state": DeliverableState.ACTIVE,
        "provenance": Provenance.from_human("jason"),
        "created_at": NOW,
        "updated_at": NOW,
        "created_by": "jason",
    }
    payload.update(values)
    context.deliverables.create(Deliverable.model_validate(payload), "jason")
    return deliverable_id


def _populate(context: DashboardContext) -> str:
    changing_id = _returnable(context, "DEL-CHANGE", "Technical response")
    complete_id = _returnable(context, "DEL-DONE", "Signed form")
    blocked_id = _returnable(context, "DEL-BLOCK", "Pricing schedule")
    _returnable(
        context,
        "DEL-POST",
        "Post-award drawing",
        lifecycle_phase=LifecyclePhase.POST_AWARD,
    )
    _returnable(
        context,
        "DEL-OPTIONAL",
        "Optional brochure",
        criticality=DeliverableCriticality.INFORMATIONAL,
    )
    _returnable(
        context,
        "DEL-INACTIVE",
        "Conditional certificate",
        criticality=DeliverableCriticality.CONDITIONAL,
        condition_text="Only for offshore supply",
        condition_active=False,
    )
    context.execution.set_record(
        context.bid.bid_id,
        ExecutionSubjectKind.RETURNABLE,
        complete_id,
        {"expected_version": 0, "prep_status": "COMPLETE", "completion_evidence": "Filed"},
        "jason",
    )
    context.execution.set_record(
        context.bid.bid_id,
        ExecutionSubjectKind.RETURNABLE,
        blocked_id,
        {
            "expected_version": 0,
            "responsible": "Commercial lead",
            "prep_status": "BLOCKED",
            "blocked_reason": "Price missing",
        },
        "jason",
    )
    context.work.create_work_item(
        {
            "bid_id": context.bid.bid_id,
            "title": "Resolve overdue exception",
            "status": "BLOCKED",
            "priority": "HIGH",
            "due_date": date(2026, 7, 1),
            "blocker_note": "Customer answer needed",
            "blocker_description": "Customer answer needed",
            "resolution_owner": "Bid lead",
        },
        "jason",
    )
    context.work.create_work_item(
        {
            "bid_id": context.bid.bid_id,
            "title": "Completed review",
            "status": "COMPLETED",
            "completion_outcome": "Reviewed",
        },
        "jason",
    )
    package = context.vendors.create_package(
        SupplierPackageCreate(
            bid_id=context.bid.bid_id,
            package_name="Pump package",
            package_code="PKG-PUMP",
            proposed_manufacturer="Atlas",
            internal_owner="Vendor lead",
        ),
        "jason",
    )
    context.vendors.create_requirement(
        CustomerRequirementCreate(
            package_id=package.package_id,
            customer_requirement_code="DATA-SHEET",
            deliverable_title="Manufacturer data sheet",
            description="Obtain the data sheet",
        ),
        "jason",
    )
    clarification_id = context.ops08.ensure_position(
        context.bid.bid_id, "PAYMENT_TERMS", "Commercial lead", "jason"
    )
    clarification = context.ops08.detail(clarification_id)
    context.ops08.revise(
        clarification_id,
        int(clarification["current_version"]),
        PositionRevision(
            disposition=PositionDisposition.CLARIFICATION_REQUIRED,
            owner="Commercial lead",
            due_date=date(2026, 7, 2),
            current_outcome="Ask customer",
            negotiation_state=NegotiationState.IN_PROGRESS,
        ),
        "jason",
    )
    decision_id = context.ops08.ensure_position(
        context.bid.bid_id, "WARRANTY", "Commercial lead", "jason"
    )
    decision = context.ops08.detail(decision_id)
    context.ops08.revise(
        decision_id,
        int(decision["current_version"]),
        PositionRevision(
            proposed_position="Limit warranty",
            disposition=PositionDisposition.QUALIFY,
            owner="Commercial lead",
            required_approver="General manager",
            rationale="Non-standard term",
        ),
        "jason",
    )
    return changing_id


def _counts(context: DashboardContext) -> dict[str, int]:
    return {
        tile.key: tile.count
        for tile in context.dashboard.dashboard(context.bid.bid_id, AS_OF).tiles
    }


def test_header_deadline_time_and_days_are_explicit(context: DashboardContext) -> None:
    without_time = context.dashboard.dashboard(context.bid.bid_id, date(2026, 7, 1))
    assert without_time.customer == context.bid.customer
    assert without_time.project_name == context.bid.project_name
    assert without_time.bid_reference == context.bid.bid_id
    assert without_time.bid_owner == context.bid.bc_owner
    assert without_time.bid_status_label == "Active"
    assert without_time.submission_deadline == date(2026, 7, 31)
    assert without_time.submission_deadline_detail == "time not recorded"
    assert without_time.days_to_deadline == 30

    context.bids.update_bid(
        context.bid.model_copy(
            update={
                "customer_due_time": time(12, 0),
                "customer_due_timezone": "America/Toronto",
            }
        )
    )
    with_time = context.dashboard.dashboard(context.bid.bid_id, date(2026, 8, 2))
    assert with_time.submission_deadline_time == "12:00"
    assert with_time.submission_deadline_timezone == "America/Toronto"
    assert "12:00" in with_time.submission_deadline_label
    assert "America/Toronto" in with_time.submission_deadline_label
    assert with_time.days_to_deadline == -2


def test_every_tile_reconciles_to_the_shared_filtered_list(context: DashboardContext) -> None:
    _populate(context)
    dashboard = context.dashboard.dashboard(context.bid.bid_id, AS_OF)
    for tile in dashboard.tiles:
        if tile.key == "readiness":
            assert tile.verdict is None and tile.blocking_count is None
            continue
        parsed = urlsplit(tile.url)
        slug = parsed.path.rsplit("/", maxsplit=1)[-1]
        state = parse_qs(parsed.query)["state"][0]
        assert tile.count == len(
            context.dashboard.filtered_list(context.bid.bid_id, slug, state, AS_OF)
        )


def test_owner_and_status_changes_only_affect_relevant_tiles(context: DashboardContext) -> None:
    changing_id = _populate(context)
    before_owner = _counts(context)
    record = context.execution.set_record(
        context.bid.bid_id,
        ExecutionSubjectKind.RETURNABLE,
        changing_id,
        {"expected_version": 0, "responsible": "Proposal lead"},
        "jason",
    )
    after_owner = _counts(context)
    assert {
        key: after_owner[key] - before_owner[key]
        for key in before_owner
        if after_owner[key] != before_owner[key]
    } == {"returnables_unassigned": -1}

    context.execution.set_record(
        context.bid.bid_id,
        ExecutionSubjectKind.RETURNABLE,
        changing_id,
        {
            "expected_version": record.version,
            "responsible": "Proposal lead",
            "prep_status": "COMPLETE",
            "completion_evidence": "Approved response",
        },
        "jason",
    )
    after_complete = _counts(context)
    assert {
        key: after_complete[key] - after_owner[key]
        for key in after_owner
        if after_complete[key] != after_owner[key]
    } == {"returnables_outstanding": -1, "returnables_complete": 1}


def test_post_award_and_optional_information_are_not_problem_tiles(
    context: DashboardContext,
) -> None:
    _populate(context)
    dashboard = context.dashboard.dashboard(context.bid.bid_id, AS_OF)
    returnable_lists = [
        context.dashboard.filtered_list(context.bid.bid_id, "returnables", state, AS_OF)
        for state in ("all", "outstanding", "complete", "blocked", "unassigned")
    ]
    assert all("DEL-POST" not in {item.subject_id for item in rows} for rows in returnable_lists)
    assert dashboard.post_award_deliverable_count == 1
    assert dashboard.non_blocking_returnable_count == 2
    assert all("not bid blockers" in text for text in dashboard.informational_text)


def test_labels_order_empty_state_and_clock_purity(context: DashboardContext) -> None:
    empty = context.dashboard.dashboard(context.bid.bid_id, AS_OF)
    assert empty.empty_state is not None
    assert all(tile.count == 0 for tile in empty.tiles)

    _populate(context)
    first = context.dashboard.dashboard(context.bid.bid_id, AS_OF)
    second = context.dashboard.dashboard(context.bid.bid_id, AS_OF)
    assert first == second
    assert first.empty_state is None
    assert [tile.key for tile in first.tiles] == [
        "returnables_total",
        "returnables_outstanding",
        "returnables_complete",
        "returnables_blocked",
        "returnables_unassigned",
        "manufacturer_inputs_outstanding",
        "manufacturer_inputs_unassigned",
        "pre_bid_actions_outstanding",
        "unresolved_clarifications",
        "commercial_decisions_needed",
        "overdue",
        "blocked",
        "readiness",
    ]
    for tile in first.tiles:
        assert not any(value in tile.label for value in ("DEL-", "WI-", "BER-", "_"))
