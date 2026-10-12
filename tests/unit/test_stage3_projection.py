"""Unit coverage for the Stage 3 unified execution projection."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from core.bid_execution import ExecutionSubjectKind, PrepStatus
from core.bid_execution_projection import (
    ExecutionFilter,
    ExecutionListKind,
    ExecutionProjectionService,
)
from core.bid_execution_repository import BidExecutionRepository
from core.bid_execution_service import BidExecutionService
from core.bid_execution_status import (
    effective_returnable_status,
    is_critical,
    is_overdue,
    map_manufacturer_status,
    map_work_item_status,
    waiting_on_prerequisite,
)
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
    DueBasis,
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
class ProjectionContext:
    db: Database
    bid: Bid
    bids: BidRepository
    work_repository: WorkItemRepository
    work_service: WorkItemService
    deliverables: DeliverableRepository
    vendor_repository: VendorDocumentRepository
    vendor_service: VendorDocumentService
    execution_repository: BidExecutionRepository
    execution_service: BidExecutionService
    ops08: Ops08Repository
    projection: ExecutionProjectionService


@pytest.fixture
def context(tmp_db: Database, bid_repo: BidRepository, valid_bid: Bid) -> ProjectionContext:
    bid_repo.create_bid(valid_bid)
    work_repository = WorkItemRepository(tmp_db)
    deliverables = DeliverableRepository(tmp_db)
    vendor_repository = VendorDocumentRepository(tmp_db)
    documents = DocumentRepository(tmp_db)
    execution_repository = BidExecutionRepository(tmp_db)
    resolver = ExecutionSubjectResolver(work_repository, deliverables, vendor_repository)
    execution_service = BidExecutionService(
        execution_repository,
        bid_repo,
        documents,
        resolver,
        now_factory=lambda: NOW,
    )
    ids = iter(UUID(int=value) for value in range(1, 500))
    work_service = WorkItemService(
        work_repository,
        bid_repo,
        now_factory=lambda: NOW,
        id_factory=lambda: next(ids),
    )
    vendor_service = VendorDocumentService(
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
    return ProjectionContext(
        tmp_db,
        valid_bid,
        bid_repo,
        work_repository,
        work_service,
        deliverables,
        vendor_repository,
        vendor_service,
        execution_repository,
        execution_service,
        ops08,
        projection,
    )


def _work(context: ProjectionContext, title: str, **values: object) -> str:
    item = context.work_service.create_work_item(
        {"bid_id": context.bid.bid_id, "title": title, **values}, "jason"
    )
    return item.work_item_id


def _returnable(
    context: ProjectionContext,
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


def _package(context: ProjectionContext) -> str:
    package = context.vendor_service.create_package(
        SupplierPackageCreate(
            bid_id=context.bid.bid_id,
            package_name="Pump package",
            package_code="PKG-PUMP",
            proposed_manufacturer="Atlas",
            internal_owner="Vendor lead",
        ),
        "jason",
    )
    return package.package_id


def _manufacturer(
    context: ProjectionContext,
    package_id: str,
    title: str,
    *,
    required: bool = True,
    applicable: bool = True,
    marker: str | None = None,
) -> str:
    item = context.vendor_service.create_requirement(
        CustomerRequirementCreate(
            package_id=package_id,
            customer_requirement_code=title.upper().replace(" ", "-")[:100],
            deliverable_title=title,
            description=f"Obtain {title}",
            required=required,
            applicable=applicable,
            source_row_reference=marker,
        ),
        "jason",
    )
    return item.requirement_id


@pytest.mark.parametrize(
    ("native", "expected", "waiting"),
    [
        ("OPEN", PrepStatus.NOT_STARTED, False),
        ("IN_PROGRESS", PrepStatus.IN_PROGRESS, False),
        ("WAITING", PrepStatus.IN_PROGRESS, True),
        ("BLOCKED", PrepStatus.BLOCKED, False),
        ("COMPLETED", PrepStatus.COMPLETE, False),
        ("CANCELLED", PrepStatus.NOT_REQUIRED, False),
    ],
)
def test_work_item_status_mapping(native: str, expected: PrepStatus, waiting: bool) -> None:
    mapped = map_work_item_status(native)
    assert (mapped.status, mapped.waiting) == (expected, waiting)


@pytest.mark.parametrize(
    ("native", "expected"),
    [
        ("CONFIRMED_COMPLIANT", PrepStatus.COMPLETE),
        ("CONFIRMED_WITH_EXCEPTION", PrepStatus.COMPLETE),
        ("NOT_APPLICABLE", PrepStatus.NOT_REQUIRED),
        ("AWAITING_MANUFACTURER", PrepStatus.IN_PROGRESS),
        ("CLARIFICATION_REQUIRED", PrepStatus.BLOCKED),
        ("CANNOT_COMPLY", PrepStatus.BLOCKED),
        ("NOT_REVIEWED", PrepStatus.NOT_STARTED),
    ],
)
def test_manufacturer_status_mapping(native: str, expected: PrepStatus) -> None:
    assert map_manufacturer_status(native) is expected
    assert map_manufacturer_status(native, applicable=False) is PrepStatus.NOT_REQUIRED


def test_effective_returnable_and_pure_attention_rules() -> None:
    assert effective_returnable_status(None, "ACTIVE", True).status is PrepStatus.NOT_STARTED
    cancelled = effective_returnable_status("COMPLETE", "CANCELLED", True)
    inactive = effective_returnable_status("BLOCKED", "ACTIVE", False)
    assert (cancelled.status, cancelled.reason) == (PrepStatus.NOT_REQUIRED, "cancelled")
    assert (inactive.status, inactive.reason) == (
        PrepStatus.NOT_REQUIRED,
        "condition not active",
    )
    assert is_overdue(date(2026, 7, 9), PrepStatus.BLOCKED, AS_OF)
    assert not is_overdue(date(2026, 7, 9), PrepStatus.COMPLETE, AS_OF)
    assert is_critical("HIGH") and is_critical(None, mandatory=True)
    assert waiting_on_prerequisite([PrepStatus.COMPLETE, PrepStatus.NOT_STARTED])
    assert not waiting_on_prerequisite([PrepStatus.COMPLETE, PrepStatus.NOT_REQUIRED])


def _mixed_actions(context: ProjectionContext) -> dict[str, str]:
    ids = {
        "blocked": _work(
            context,
            "Overdue blocker",
            status="BLOCKED",
            priority="HIGH",
            due_date=date(2026, 7, 1),
            blocker_note="Price is missing",
            blocker_description="Price is missing",
            resolution_owner="Commercial lead",
        ),
        "progress": _work(
            context,
            "Draft response",
            status="IN_PROGRESS",
            due_date=date(2026, 7, 20),
        ),
        "complete": _work(
            context,
            "Completed review",
            status="COMPLETED",
            completion_outcome="Reviewed",
        ),
        "cancelled": _work(
            context,
            "Removed action",
            status="CANCELLED",
            cancellation_reason="No longer needed",
        ),
        "dependent": _work(context, "Assemble response"),
        "prerequisite": _work(context, "Confirm scope"),
        "native_wait": _work(
            context,
            "Await customer",
            status="WAITING",
            waiting_on="Customer clarification",
            waiting_party_label="Customer",
            waiting_owed="Clarification",
            chase_date=date(2026, 7, 12),
        ),
    }
    context.execution_service.set_record(
        context.bid.bid_id,
        ExecutionSubjectKind.WORK_ITEM,
        ids["progress"],
        {"expected_version": 0, "responsible": "Proposal lead"},
        "jason",
    )
    context.execution_service.add_dependency(
        context.bid.bid_id,
        "WORK_ITEM",
        ids["dependent"],
        "WORK_ITEM",
        ids["prerequisite"],
        None,
        "jason",
    )
    return ids


def test_each_execution_filter_uses_the_shared_mixed_projection(
    context: ProjectionContext,
) -> None:
    _mixed_actions(context)
    expected = {
        ExecutionFilter.ALL: 7,
        ExecutionFilter.OUTSTANDING: 5,
        ExecutionFilter.IN_PROGRESS: 2,
        ExecutionFilter.BLOCKED: 2,
        ExecutionFilter.COMPLETE: 1,
        ExecutionFilter.UNASSIGNED: 4,
        ExecutionFilter.OVERDUE: 1,
        ExecutionFilter.WAITING: 1,
        ExecutionFilter.NOT_REQUIRED: 1,
    }
    for state, count in expected.items():
        assert len(context.projection.items(context.bid.bid_id, "ACTION", state, AS_OF)) == count


def test_waiting_is_dependency_only_and_completion_does_not_mutate_dependent(
    context: ProjectionContext,
) -> None:
    ids = _mixed_actions(context)
    waiting = context.projection.items(
        context.bid.bid_id, ExecutionListKind.ACTION, ExecutionFilter.WAITING, AS_OF
    )
    assert [item.title for item in waiting] == ["Assemble response"]
    assert context.work_repository.get(ids["dependent"]).status.value == "OPEN"  # type: ignore[union-attr]

    prerequisite = context.work_service.get_work_item(ids["prerequisite"])
    context.work_service.transition_work_item(
        prerequisite.work_item_id,
        {
            "expected_version": prerequisite.version,
            "status": "COMPLETED",
            "completion_outcome": "Scope confirmed",
        },
        "jason",
    )

    dependent = next(
        item
        for item in context.projection.items(context.bid.bid_id, "ACTION", "all", AS_OF)
        if item.subject_id == ids["dependent"]
    )
    assert dependent.status is PrepStatus.NOT_STARTED
    assert not dependent.waiting_on_prerequisite
    assert context.work_repository.get(ids["dependent"]).status.value == "OPEN"  # type: ignore[union-attr]


def test_returnable_scope_inactive_status_owner_and_deadline_labels(
    context: ProjectionContext,
) -> None:
    fixed = _returnable(
        context,
        "DEL-FIXED",
        "Signed form",
        owner="Register owner",
        due_basis=DueBasis.FIXED_DATE,
        fixed_due_date=date(2026, 7, 25),
    )
    _returnable(
        context,
        "DEL-CLOSING",
        "Pricing schedule",
        due_basis=DueBasis.WITH_BID,
    )
    _returnable(
        context,
        "DEL-INACTIVE",
        "Conditional certificate",
        criticality=DeliverableCriticality.CONDITIONAL,
        condition_text="Only for offshore supply",
        condition_active=False,
    )
    _returnable(
        context,
        "DEL-POST",
        "Post-award drawing",
        lifecycle_phase=LifecyclePhase.POST_AWARD,
    )

    rows = context.projection.items(context.bid.bid_id, "RETURNABLE", "all", AS_OF)
    by_id = {item.subject_id: item for item in rows}
    assert "DEL-POST" not in by_id
    assert by_id[fixed].responsible == "Register owner"
    assert by_id[fixed].responsible_origin == "register"
    assert by_id[fixed].customer_deadline == date(2026, 7, 25)
    assert by_id[fixed].customer_deadline_label == "stated in the RFP"
    assert by_id["DEL-CLOSING"].customer_deadline == context.bid.customer_due_date
    assert by_id["DEL-CLOSING"].customer_deadline_label == "Bid closing"
    assert by_id["DEL-INACTIVE"].status is PrepStatus.NOT_REQUIRED
    assert by_id["DEL-INACTIVE"].status_label == "Not required — condition not active"


def test_nonrequired_manufacturer_rows_only_appear_in_not_required_filter(
    context: ProjectionContext,
) -> None:
    package_id = _package(context)
    required = _manufacturer(context, package_id, "Required data sheet")
    optional = _manufacturer(context, package_id, "Optional brochure", required=False)
    inapplicable = _manufacturer(context, package_id, "Unused certificate", applicable=False)
    all_ids = {
        item.subject_id
        for item in context.projection.items(context.bid.bid_id, "MANUFACTURER_INPUT", "all", AS_OF)
    }
    not_required_ids = {
        item.subject_id
        for item in context.projection.items(
            context.bid.bid_id, "MANUFACTURER_INPUT", "not_required", AS_OF
        )
    }
    assert all_ids == {required}
    assert not_required_ids == {optional, inapplicable}
    required_item = context.projection.items(
        context.bid.bid_id, "MANUFACTURER_INPUT", "all", AS_OF
    )[0]
    assert required_item.responsible == "Vendor lead"
    assert required_item.responsible_origin == "register"


def _insert_discovery_source(context: ProjectionContext, proposal_id: str, work_id: str) -> None:
    with context.db._conn() as conn:
        conn.executescript(
            """CREATE TABLE IF NOT EXISTS deliverable_discovery_destinations(
            destination_id TEXT PRIMARY KEY,proposal_id TEXT,event_id TEXT,bid_id TEXT,
            destination_kind TEXT,record_id TEXT,reused INTEGER,note TEXT,created_at TEXT);
            CREATE TABLE IF NOT EXISTS deliverable_discovery_proposal_sources_v2(
            citation_id TEXT PRIMARY KEY,proposal_id TEXT,document_version_id TEXT,
            received_file_id TEXT,locator_type TEXT,locator TEXT,excerpt TEXT);
            CREATE TABLE IF NOT EXISTS deliverable_discovery_citation_quotes(
            citation_id TEXT PRIMARY KEY,proposal_id TEXT,quote TEXT);"""
        )
        conn.execute(
            "INSERT INTO deliverable_discovery_destinations VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "destination-one",
                proposal_id,
                "review-one",
                context.bid.bid_id,
                "WORK_ITEM",
                work_id,
                0,
                None,
                NOW.isoformat(),
            ),
        )
        conn.execute(
            "INSERT INTO deliverable_discovery_proposal_sources_v2 VALUES(?,?,?,?,?,?,?)",
            ("citation-one", proposal_id, None, None, "PAGE", "page 7", "source text"),
        )
        conn.execute(
            "INSERT INTO deliverable_discovery_citation_quotes VALUES(?,?,?)",
            ("citation-one", proposal_id, "verified customer wording"),
        )


def test_manual_and_finding_origin_with_verified_source(context: ProjectionContext) -> None:
    manual_id = _work(context, "Manual action")
    finding_id = _work(context, "Finding action", details="Found [DISC:finding-one]")
    _insert_discovery_source(context, "finding-one", finding_id)

    rows = context.projection.items(context.bid.bid_id, "ACTION", "all", AS_OF)
    by_id = {item.subject_id: item for item in rows}
    assert by_id[manual_id].origin == "Added manually"
    assert by_id[finding_id].origin == "From RFP finding"
    assert by_id[finding_id].source_label == "Source document — page 7"
    assert by_id[finding_id].source_url.endswith("/source/finding-one")
    trace = BidExecutionTrace(context.db).source_trace(context.bid.bid_id, "WORK_ITEM", finding_id)
    assert trace.sources[0].verified_quote == "verified customer wording"
    assert trace.sources[0].finding_id == "finding-one"


def test_commercial_clarification_and_decision_states_link_existing_screen(
    context: ProjectionContext,
) -> None:
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
            due_date=date(2026, 7, 9),
            current_outcome="Ask customer to confirm milestones",
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
            proposed_position="Limit warranty to twelve months",
            disposition=PositionDisposition.QUALIFY,
            owner="Commercial lead",
            required_approver="General manager",
            rationale="Customer term exceeds standard position",
        ),
        "jason",
    )

    rows = context.projection.items(context.bid.bid_id, "COMMERCIAL", "all", AS_OF)
    by_id = {item.subject_id: item for item in rows}
    assert by_id[clarification_id].status is PrepStatus.BLOCKED
    assert by_id[clarification_id].status_label == "Clarification"
    assert by_id[clarification_id].overdue
    assert by_id[decision_id].status is PrepStatus.NOT_STARTED
    assert by_id[decision_id].status_label == "Decision needed"
    assert all(item.item_url == f"/commercial/{item.subject_id}" for item in rows)


def test_ordering_and_no_internal_ids_in_business_labels(context: ProjectionContext) -> None:
    _mixed_actions(context)
    _returnable(context, "DEL-LABEL", "Business title")
    rows = context.projection.all_items(context.bid.bid_id, AS_OF)
    assert rows[0].title == "Overdue blocker"
    blocked_positions = [
        index
        for index, item in enumerate(rows)
        if item.status is PrepStatus.BLOCKED or item.waiting_on_prerequisite
    ]
    ordinary_positions = [
        index
        for index, item in enumerate(rows)
        if not item.overdue
        and item.status is not PrepStatus.BLOCKED
        and not item.waiting_on_prerequisite
    ]
    assert max(blocked_positions) < min(ordinary_positions)
    for item in rows:
        labels = [
            item.title,
            item.status_label,
            item.origin,
            item.source_label or "",
            item.customer_deadline_label or "",
            *(link.title for link in item.prerequisites),
            *(link.status_label for link in item.prerequisites),
        ]
        assert not any(
            prefix in label for prefix in ("WI-", "DEL-", "DDP-", "BER-") for label in labels
        )


def test_projection_query_count_is_bounded_for_fifty_items(
    context: ProjectionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    for number in range(50):
        _work(context, f"Action {number:02d}")
    statements: list[str] = []
    original = context.db._conn

    def traced_connection() -> sqlite3.Connection:
        conn = original()
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(context.db, "_conn", traced_connection)
    rows = context.projection.items(context.bid.bid_id, "ACTION", "all", AS_OF)

    reads = [
        statement for statement in statements if statement.lstrip().upper().startswith("SELECT")
    ]
    assert len(rows) == 50
    assert len(reads) <= 20
