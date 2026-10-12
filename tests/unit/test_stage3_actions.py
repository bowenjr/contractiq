"""Unit coverage for the Stage 3 Bid execution command layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from core.bid_execution import ExecutionSubjectKind, PrepStatus
from core.bid_execution_action_models import (
    ManufacturerInputChanges,
    ReturnableChanges,
    WorkItemActionChanges,
)
from core.bid_execution_actions import BidExecutionActionService
from core.bid_execution_projection import (
    ExecutionFilter,
    ExecutionListKind,
    ExecutionProjectionService,
)
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
from core.ops08 import Ops08Repository
from core.schemas import Bid, Provenance
from core.vendor_document_control import CustomerRequirementCreate, SupplierPackageCreate
from core.vendor_document_repository import VendorDocumentRepository
from core.vendor_document_service import VendorDocumentService
from core.work_item_repository import WorkItemRepository
from core.work_item_service import WorkItemService
from core.work_items import WorkItemStatus

NOW = datetime(2026, 7, 10, 14, 30, tzinfo=UTC)
AS_OF = date(2026, 7, 10)


@dataclass(frozen=True)
class ActionContext:
    db: Database
    bid: Bid
    bids: BidRepository
    work_repository: WorkItemRepository
    execution_repository: BidExecutionRepository
    execution_service: BidExecutionService
    deliverables: DeliverableRepository
    vendors: VendorDocumentRepository
    vendor_service: VendorDocumentService
    projection: ExecutionProjectionService
    actions: BidExecutionActionService


def _context(db: Database, bid: Bid, *, create_bid: bool) -> ActionContext:
    bids = BidRepository(db)
    if create_bid:
        bids.create_bid(bid)
    work_repository = WorkItemRepository(db)
    deliverables = DeliverableRepository(db)
    vendors = VendorDocumentRepository(db)
    execution_repository = BidExecutionRepository(db)
    execution_service = BidExecutionService(
        execution_repository,
        bids,
        DocumentRepository(db),
        ExecutionSubjectResolver(work_repository, deliverables, vendors),
        now_factory=lambda: NOW,
    )
    work_service = WorkItemService(work_repository, bids, now_factory=lambda: NOW)
    projection = ExecutionProjectionService(
        work_repository,
        deliverables,
        vendors,
        execution_repository,
        Ops08Repository(db),
        bids,
        BidExecutionTrace(db),
    )
    actions = BidExecutionActionService(
        work_service,
        execution_service,
        projection,
        bids,
        as_of_factory=lambda: AS_OF,
    )
    return ActionContext(
        db,
        bid,
        bids,
        work_repository,
        execution_repository,
        execution_service,
        deliverables,
        vendors,
        VendorDocumentService(vendors, bids, now_factory=lambda: NOW),
        projection,
        actions,
    )


@pytest.fixture
def context(tmp_db: Database, valid_bid: Bid) -> ActionContext:
    return _context(tmp_db, valid_bid, create_bid=True)


def _returnable(context: ActionContext, subject_id: str, title: str) -> str:
    context.deliverables.create(
        Deliverable(
            deliverable_id=subject_id,
            bid_id=context.bid.bid_id,
            title=title,
            description=f"Prepare {title}",
            category="Submission",
            criticality=DeliverableCriticality.MANDATORY,
            lifecycle_phase=LifecyclePhase.WITH_BID,
            direction=DeliverableDirection.INTERNAL,
            workflow_state=DeliverableState.ACTIVE,
            provenance=Provenance.from_human("jason"),
            created_at=NOW,
            updated_at=NOW,
            created_by="jason",
        ),
        "jason",
    )
    return subject_id


def _manufacturer(context: ActionContext, title: str) -> str:
    package = context.vendor_service.create_package(
        SupplierPackageCreate(
            bid_id=context.bid.bid_id,
            package_name="Pump package",
            package_code=f"PKG-{title.upper().replace(' ', '-')}",
            proposed_manufacturer="Atlas",
            internal_owner="Vendor lead",
        ),
        "jason",
    )
    requirement = context.vendor_service.create_requirement(
        CustomerRequirementCreate(
            package_id=package.package_id,
            customer_requirement_code=f"REQ-{title}",
            deliverable_title=title,
            description=f"Obtain {title}",
        ),
        "jason",
    )
    return requirement.requirement_id


def _counts(db: Database) -> tuple[int, int, int]:
    with db._conn() as conn:
        return tuple(
            int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("work_items", "bid_execution_records", "audit_log")
        )  # type: ignore[return-value]


def test_manual_action_has_no_inferred_owner_or_date_and_reuses_active_title(
    context: ActionContext,
) -> None:
    first = context.actions.create_manual_action(
        context.bid.bid_id,
        "Coordinate final review",
        "Bring the response together",
        None,
        [],
        None,
        "HIGH",
        "REQ-MISSING",
        None,
        "jason",
    )
    item = context.work_repository.get(str(first.subject_id))
    record = context.execution_service.get_record(
        context.bid.bid_id, ExecutionSubjectKind.WORK_ITEM, str(first.subject_id)
    )
    assert item is not None and item.due_date is None
    assert item.status is WorkItemStatus.OPEN
    assert record is not None and record.responsible is None and record.contributors == []
    assert "progress note" in first.message
    assert (
        len(
            context.execution_service.list_notes(
                context.bid.bid_id, ExecutionSubjectKind.WORK_ITEM, str(first.subject_id)
            )
        )
        == 1
    )
    before = _counts(context.db)

    second = context.actions.create_manual_action(
        context.bid.bid_id,
        " coordinate--FINAL review ",
        "Different details do not replace the existing action",
        "Someone else",
        ["Contributor"],
        date(2026, 7, 20),
        "LOW",
        None,
        None,
        "jason",
    )

    assert second.reused and not second.changed
    assert second.subject_id == first.subject_id
    assert _counts(context.db) == before


def test_manual_action_refuses_blank_title_and_date_after_customer_deadline(
    context: ActionContext,
) -> None:
    with pytest.raises(ValueError, match="title.*enter"):
        context.actions.create_manual_action(
            context.bid.bid_id, "  ", None, None, None, None, "NORMAL", None, None, "jason"
        )
    with pytest.raises(ValueError, match="customer deadline.*choose"):
        context.actions.create_manual_action(
            context.bid.bid_id,
            "Late action",
            None,
            None,
            None,
            date(2026, 8, 1),
            "NORMAL",
            None,
            None,
            "jason",
        )
    assert _counts(context.db)[0] == 0


def test_action_status_translation_and_required_fix_messages(context: ActionContext) -> None:
    created = context.actions.create_manual_action(
        context.bid.bid_id,
        "Status lifecycle",
        None,
        None,
        None,
        None,
        "NORMAL",
        None,
        None,
        "jason",
    )
    subject_id = str(created.subject_id)
    context.actions.update_action(
        context.bid.bid_id, subject_id, WorkItemActionChanges(status="In progress"), "jason"
    )
    assert context.work_repository.get(subject_id).status is WorkItemStatus.IN_PROGRESS  # type: ignore[union-attr]

    with pytest.raises(ValueError, match="Blocked requires.*provide both"):
        context.actions.update_action(
            context.bid.bid_id, subject_id, WorkItemActionChanges(status="Blocked"), "jason"
        )
    context.actions.update_action(
        context.bid.bid_id,
        subject_id,
        WorkItemActionChanges(
            status="Blocked", blocker_description="Pricing missing", resolution_owner="Sam"
        ),
        "jason",
    )
    assert context.work_repository.get(subject_id).status is WorkItemStatus.BLOCKED  # type: ignore[union-attr]

    with pytest.raises(ValueError, match="Complete requires.*completion reference"):
        context.actions.update_action(
            context.bid.bid_id,
            subject_id,
            WorkItemActionChanges(status="Complete", completion_outcome="Reviewed"),
            "jason",
        )
    context.actions.update_action(
        context.bid.bid_id,
        subject_id,
        WorkItemActionChanges(
            status="Complete",
            completion_outcome="Reviewed",
            completion_evidence="Review record",
        ),
        "jason",
    )
    assert context.work_repository.get(subject_id).status is WorkItemStatus.COMPLETED  # type: ignore[union-attr]

    context.actions.update_action(
        context.bid.bid_id, subject_id, WorkItemActionChanges(status="Not started"), "jason"
    )
    with pytest.raises(ValueError, match="Not required needs.*explain"):
        context.actions.update_action(
            context.bid.bid_id,
            subject_id,
            WorkItemActionChanges(status="Not required", cancellation_reason=None),
            "jason",
        )
    context.actions.update_action(
        context.bid.bid_id,
        subject_id,
        WorkItemActionChanges(status="Not required", cancellation_reason="Scope removed"),
        "jason",
    )
    assert context.work_repository.get(subject_id).status is WorkItemStatus.CANCELLED  # type: ignore[union-attr]


def test_partial_returnable_update_preserves_fields_and_explicit_clear_removes_them(
    context: ActionContext,
) -> None:
    subject_id = _returnable(context, "DEL-PARTIAL", "Technical schedule")
    context.execution_service.set_record(
        context.bid.bid_id,
        ExecutionSubjectKind.RETURNABLE,
        subject_id,
        {
            "expected_version": 0,
            "responsible": "Alex",
            "contributors": ["Morgan"],
            "internal_due_date": date(2026, 7, 20),
            "prep_status": PrepStatus.IN_PROGRESS,
            "priority": "HIGH",
        },
        "jason",
    )
    context.actions.update_returnable(
        context.bid.bid_id,
        subject_id,
        ReturnableChanges(responsible="Taylor"),
        "jason",
    )
    saved = context.execution_service.get_record(
        context.bid.bid_id, ExecutionSubjectKind.RETURNABLE, subject_id
    )
    assert saved is not None
    assert saved.responsible == "Taylor"
    assert saved.contributors == ["Morgan"]
    assert saved.internal_due_date == date(2026, 7, 20)
    assert saved.prep_status is PrepStatus.IN_PROGRESS
    assert saved.priority.value == "HIGH"  # type: ignore[union-attr]

    context.actions.update_returnable(
        context.bid.bid_id,
        subject_id,
        ReturnableChanges(responsible=None, contributors=None, internal_due_date=None),
        "jason",
    )
    cleared = context.execution_service.get_record(
        context.bid.bid_id, ExecutionSubjectKind.RETURNABLE, subject_id
    )
    assert cleared is not None
    assert cleared.responsible is None and cleared.contributors == []
    assert cleared.internal_due_date is None
    assert cleared.prep_status is PrepStatus.IN_PROGRESS


def test_manufacturer_status_change_points_to_verification_screen(context: ActionContext) -> None:
    subject_id = _manufacturer(context, "Data sheet")
    with pytest.raises(
        ValueError, match="status comes from verification.*manufacturer-requirement"
    ):
        context.actions.update_manufacturer_input(
            context.bid.bid_id,
            subject_id,
            ManufacturerInputChanges(status="Complete"),
            "jason",
        )
    assert context.execution_repository.list_records(context.bid.bid_id) == []


def test_bulk_assign_validates_all_then_skips_unchanged_and_is_idempotent(
    context: ActionContext,
) -> None:
    first = _returnable(context, "DEL-A", "A schedule")
    second = _returnable(context, "DEL-B", "B schedule")
    complete = _returnable(context, "DEL-C", "C schedule")
    context.execution_service.set_record(
        context.bid.bid_id,
        ExecutionSubjectKind.RETURNABLE,
        complete,
        {
            "expected_version": 0,
            "prep_status": "COMPLETE",
            "completion_evidence": "Submitted pack",
        },
        "jason",
    )
    before = _counts(context.db)
    with pytest.raises(ValueError, match="every selected item must still be outstanding"):
        context.actions.bulk_assign(
            context.bid.bid_id,
            "RETURNABLE",
            [first, complete],
            "Alex",
            date(2026, 7, 20),
            "jason",
        )
    assert _counts(context.db) == before

    context.actions.update_returnable(
        context.bid.bid_id,
        first,
        ReturnableChanges(responsible="Alex", internal_due_date=date(2026, 7, 20)),
        "jason",
    )
    assigned = context.actions.bulk_assign(
        context.bid.bid_id,
        ExecutionListKind.RETURNABLE,
        [first, second],
        "Alex",
        date(2026, 7, 20),
        "jason",
    )
    assert assigned.changed
    assert assigned.message == "Assigned 2 returnables to Alex (1 already had this owner)."
    before_repeat = _counts(context.db)
    repeated = context.actions.bulk_assign(
        context.bid.bid_id,
        "returnable",
        [first, second],
        "Alex",
        date(2026, 7, 20),
        "jason",
    )
    assert not repeated.changed and repeated.reused
    assert _counts(context.db) == before_repeat


def test_next_item_wraps_prefers_unassigned_and_skips_completed(context: ActionContext) -> None:
    first = _returnable(context, "DEL-NEXT-A", "A returnable")
    completed = _returnable(context, "DEL-NEXT-B", "B returnable")
    unassigned = _returnable(context, "DEL-NEXT-C", "C returnable")
    context.actions.update_returnable(
        context.bid.bid_id, first, ReturnableChanges(responsible="Alex"), "jason"
    )
    context.actions.update_returnable(
        context.bid.bid_id,
        completed,
        ReturnableChanges(prep_status="Complete", completion_evidence="Submission reference"),
        "jason",
    )

    next_url = context.actions.next_item_url(
        context.bid.bid_id, ExecutionListKind.RETURNABLE, completed, AS_OF
    )

    assert next_url is not None and next_url.endswith(unassigned)
    assert completed not in next_url


def test_messages_hide_internal_ids_and_raw_enum_names_and_state_survives_restart(
    tmp_path: Path, valid_bid: Bid
) -> None:
    path = tmp_path / "restart.db"
    first_context = _context(Database(path), valid_bid, create_bid=True)
    subject_id = _returnable(first_context, "DEL-RESTART", "Restart returnable")
    result = first_context.actions.update_returnable(
        valid_bid.bid_id,
        subject_id,
        ReturnableChanges(
            responsible="Alex",
            internal_due_date=date(2026, 7, 20),
            prep_status="In progress",
        ),
        "jason",
    )
    for forbidden in ("WI-", "DEL-", "BER-", "IN_PROGRESS", "RETURNABLE"):
        assert forbidden not in result.message

    reopened = _context(Database(path), valid_bid, create_bid=False)
    record = reopened.execution_service.get_record(
        valid_bid.bid_id, ExecutionSubjectKind.RETURNABLE, subject_id
    )
    projected = reopened.projection.items(
        valid_bid.bid_id, ExecutionListKind.RETURNABLE, ExecutionFilter.ALL, AS_OF
    )
    assert record is not None
    assert record.responsible == "Alex" and record.internal_due_date == date(2026, 7, 20)
    assert projected[0].status is PrepStatus.IN_PROGRESS


def test_no_change_save_writes_nothing(context: ActionContext) -> None:
    subject_id = _returnable(context, "DEL-NOOP", "No-op returnable")
    context.actions.update_returnable(
        context.bid.bid_id, subject_id, ReturnableChanges(responsible="Alex"), "jason"
    )
    before = _counts(context.db)
    result = context.actions.update_returnable(
        context.bid.bid_id, subject_id, ReturnableChanges(responsible="Alex"), "jason"
    )
    assert result.message == "No changes to save."
    assert not result.changed
    assert _counts(context.db) == before


def test_manual_action_uses_existing_service_audits(context: ActionContext) -> None:
    """Confirm the existing mutation services own the resulting audit actions."""
    result = context.actions.create_manual_action(
        context.bid.bid_id,
        "Audited action",
        None,
        "Alex",
        None,
        None,
        "NORMAL",
        None,
        None,
        "jason",
    )
    with context.db._conn() as conn:
        actions = {
            str(row[0])
            for row in conn.execute("SELECT action FROM audit_log ORDER BY timestamp").fetchall()
        }
    assert result.changed
    assert {"work_item_created", "bid_execution_record_saved"} <= actions
    assert "bid_execution_action_saved" not in actions
