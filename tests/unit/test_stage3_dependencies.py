"""Stage 3 subject resolution and concurrency-safe dependency coverage."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest

from core.bid_execution import ExecutionSubjectKind
from core.bid_execution_repository import BidExecutionRepository
from core.bid_execution_service import BidExecutionService
from core.bid_execution_subjects import ExecutionSubjectResolver
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
from core.schemas import Bid, Provenance
from core.vendor_document_control import CustomerRequirementCreate, SupplierPackageCreate
from core.vendor_document_repository import VendorDocumentRepository
from core.vendor_document_service import VendorDocumentService
from core.work_item_repository import WorkItemRepository
from core.work_item_service import WorkItemService

NOW = datetime(2026, 7, 10, 14, 30, tzinfo=UTC)


@dataclass(frozen=True)
class Context:
    db: Database
    bid: Bid
    other_bid: Bid
    bids: BidRepository
    execution_repository: BidExecutionRepository
    execution_service: BidExecutionService
    resolver: ExecutionSubjectResolver
    deliverables: DeliverableRepository
    work_items: WorkItemService
    vendor_documents: VendorDocumentService


def _context(db: Database, bids: BidRepository, bid: Bid) -> Context:
    other_bid = bid.model_copy(update={"bid_id": "B-2026-0002", "project_name": "Other Project"})
    bids.create_bid(bid)
    bids.create_bid(other_bid)
    work_repository = WorkItemRepository(db)
    deliverables = DeliverableRepository(db)
    vendor_repository = VendorDocumentRepository(db)
    resolver = ExecutionSubjectResolver(work_repository, deliverables, vendor_repository)
    execution_repository = BidExecutionRepository(db)
    return Context(
        db=db,
        bid=bid,
        other_bid=other_bid,
        bids=bids,
        execution_repository=execution_repository,
        execution_service=BidExecutionService(
            execution_repository,
            bids,
            DocumentRepository(db),
            resolver,
            now_factory=lambda: NOW,
        ),
        resolver=resolver,
        deliverables=deliverables,
        work_items=WorkItemService(work_repository, bids, now_factory=lambda: NOW),
        vendor_documents=VendorDocumentService(vendor_repository, bids, now_factory=lambda: NOW),
    )


def _returnable(
    context: Context,
    deliverable_id: str,
    title: str,
    *,
    bid: Bid | None = None,
) -> str:
    owning_bid = bid or context.bid
    context.deliverables.create(
        Deliverable(
            deliverable_id=deliverable_id,
            bid_id=owning_bid.bid_id,
            title=title,
            description=f"Prepare {title}",
            category="Submission",
            criticality=DeliverableCriticality.MANDATORY,
            lifecycle_phase=LifecyclePhase.WITH_BID,
            direction=DeliverableDirection.INTERNAL,
            workflow_state=DeliverableState.ACTIVE,
            owner="Proposal Lead",
            due_basis=DueBasis.FIXED_DATE,
            fixed_due_date=date(2026, 7, 31),
            provenance=Provenance.from_human("jason"),
            created_at=NOW,
            updated_at=NOW,
            created_by="jason",
        ),
        "jason",
    )
    return deliverable_id


def _work(
    context: Context,
    title: str,
    *,
    bid: Bid | None = None,
    priority: str = "NORMAL",
) -> str:
    owning_bid = bid or context.bid
    return context.work_items.create_work_item(
        {
            "bid_id": owning_bid.bid_id,
            "title": title,
            "status": "IN_PROGRESS",
            "priority": priority,
            "due_date": date(2026, 7, 20),
        },
        "jason",
    ).work_item_id


def _manufacturer(context: Context, title: str, *, bid: Bid | None = None) -> str:
    owning_bid = bid or context.bid
    suffix = title.upper().replace(" ", "-")
    package = context.vendor_documents.create_package(
        SupplierPackageCreate(
            bid_id=owning_bid.bid_id,
            package_name=f"{title} package",
            package_code=f"PKG-{suffix}",
            proposed_manufacturer="Atlas Equipment",
            internal_owner="Vendor Coordinator",
        ),
        "jason",
    )
    requirement = context.vendor_documents.create_requirement(
        CustomerRequirementCreate(
            package_id=package.package_id,
            customer_requirement_code=f"REQ-{suffix}",
            deliverable_title=title,
            required=True,
            applicable=True,
        ),
        "jason",
    )
    return requirement.requirement_id


def _audit_count(context: Context, action: str) -> int:
    with context.db._conn() as conn:
        return int(
            conn.execute("SELECT COUNT(*) FROM audit_log WHERE action=?", (action,)).fetchone()[0]
        )


@pytest.fixture
def context(tmp_db: Database, bid_repo: BidRepository, valid_bid: Bid) -> Context:
    return _context(tmp_db, bid_repo, valid_bid)


def test_subject_resolver_returns_native_facts_for_all_three_kinds(context: Context) -> None:
    work_id = _work(context, "Review pricing", priority="HIGH")
    returnable_id = _returnable(context, "DEL-FACTS", "Signed Bid form")
    manufacturer_id = _manufacturer(context, "Motor data sheet")

    work = context.resolver.resolve(context.bid.bid_id, "WORK_ITEM", work_id)
    returnable = context.resolver.resolve(context.bid.bid_id, "RETURNABLE", returnable_id)
    manufacturer = context.resolver.resolve(
        context.bid.bid_id, "MANUFACTURER_INPUT", manufacturer_id
    )

    assert (work.title, work.native_status.value, work.priority.value, work.due_date) == (
        "Review pricing",
        "IN_PROGRESS",
        "HIGH",
        date(2026, 7, 20),
    )
    assert work.native_owner is None
    assert (
        returnable.native_owner,
        returnable.criticality,
        returnable.lifecycle_phase,
        returnable.workflow_state,
        returnable.condition_active,
        returnable.fixed_due_date,
        returnable.due_basis,
    ) == (
        "Proposal Lead",
        DeliverableCriticality.MANDATORY,
        LifecyclePhase.WITH_BID,
        DeliverableState.ACTIVE,
        True,
        date(2026, 7, 31),
        DueBasis.FIXED_DATE,
    )
    assert (manufacturer.native_owner, manufacturer.required, manufacturer.applicable) == (
        "Vendor Coordinator",
        True,
        True,
    )
    assert manufacturer.verification_status.value == "NOT_REVIEWED"


def test_add_list_and_remove_dependency_are_audited(context: Context) -> None:
    prerequisite_id = _work(context, "Confirm pricing")
    item_id = _returnable(context, "DEL-LIST", "Commercial submission")

    dependency = context.execution_service.add_dependency(
        context.bid.bid_id,
        "RETURNABLE",
        item_id,
        "WORK_ITEM",
        prerequisite_id,
        "Pricing must be confirmed first",
        "jason",
    )

    assert context.execution_repository.list_dependencies(context.bid.bid_id) == [dependency]
    assert context.execution_repository.list_active_dependencies_for_subject(
        context.bid.bid_id, ExecutionSubjectKind.WORK_ITEM, prerequisite_id
    ) == [dependency]
    assert _audit_count(context, "bid_execution_dependency_added") == 1

    removed = context.execution_service.remove_dependency(
        context.bid.bid_id, dependency.dependency_id, "jason"
    )
    assert removed.removed_at == NOW
    assert removed.removed_by == "jason"
    assert (
        context.execution_repository.list_active_dependencies_for_subject(
            context.bid.bid_id, ExecutionSubjectKind.RETURNABLE, item_id
        )
        == []
    )
    assert _audit_count(context, "bid_execution_dependency_removed") == 1
    with pytest.raises(ValueError, match="already removed; refresh"):
        context.execution_service.remove_dependency(
            context.bid.bid_id, dependency.dependency_id, "jason"
        )
    assert _audit_count(context, "bid_execution_dependency_removed") == 1
    with context.db._conn() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="marked removed once"):
            conn.execute(
                "UPDATE bid_execution_dependencies SET removed_at=NULL,removed_by=NULL "
                "WHERE dependency_id=?",
                (dependency.dependency_id,),
            )


def test_self_duplicate_cross_bid_and_missing_subjects_are_refused(context: Context) -> None:
    item_id = _work(context, "Prepare response")
    other_id = _work(context, "Other Bid review", bid=context.other_bid)

    with pytest.raises(ValueError, match="cannot depend on itself; choose a different"):
        context.execution_service.add_dependency(
            context.bid.bid_id, "WORK_ITEM", item_id, "WORK_ITEM", item_id, None, "jason"
        )

    prerequisite_id = _returnable(context, "DEL-DUP", "Response volume")
    context.execution_service.add_dependency(
        context.bid.bid_id,
        "WORK_ITEM",
        item_id,
        "RETURNABLE",
        prerequisite_id,
        None,
        "jason",
    )
    before = _audit_count(context, "bid_execution_dependency_added")
    with pytest.raises(ValueError, match="active dependency already exists"):
        context.execution_service.add_dependency(
            context.bid.bid_id,
            "WORK_ITEM",
            item_id,
            "RETURNABLE",
            prerequisite_id,
            None,
            "jason",
        )
    with pytest.raises(ValueError, match="belongs to a different Bid"):
        context.execution_service.add_dependency(
            context.bid.bid_id,
            "WORK_ITEM",
            item_id,
            "WORK_ITEM",
            other_id,
            None,
            "jason",
        )
    with pytest.raises(ValueError, match="was not found; choose an existing returnable"):
        context.execution_service.add_dependency(
            context.bid.bid_id,
            "WORK_ITEM",
            item_id,
            "RETURNABLE",
            "DEL-MISSING",
            None,
            "jason",
        )
    assert _audit_count(context, "bid_execution_dependency_added") == before


def test_direct_and_long_mixed_kind_cycles_name_the_business_chain(context: Context) -> None:
    first = _work(context, "Pricing review")
    second = _returnable(context, "DEL-CYCLE", "Bid form")
    context.execution_service.add_dependency(
        context.bid.bid_id, "WORK_ITEM", first, "RETURNABLE", second, None, "jason"
    )
    with pytest.raises(
        ValueError,
        match="Bid form would depend on Pricing review, which already depends on Bid form",
    ):
        context.execution_service.add_dependency(
            context.bid.bid_id, "RETURNABLE", second, "WORK_ITEM", first, None, "jason"
        )

    third = _manufacturer(context, "Manufacturer drawing")
    context.execution_service.add_dependency(
        context.bid.bid_id,
        "RETURNABLE",
        second,
        "MANUFACTURER_INPUT",
        third,
        None,
        "jason",
    )
    with pytest.raises(
        ValueError,
        match=(
            "Manufacturer drawing would depend on Pricing review, which already depends on "
            "Bid form, which already depends on Manufacturer drawing"
        ),
    ):
        context.execution_service.add_dependency(
            context.bid.bid_id,
            "MANUFACTURER_INPUT",
            third,
            "WORK_ITEM",
            first,
            None,
            "jason",
        )
    assert len(context.execution_repository.list_dependencies(context.bid.bid_id)) == 2
    assert _audit_count(context, "bid_execution_dependency_added") == 2


def test_opposite_edges_are_atomic_under_two_threads(context: Context) -> None:
    first = _work(context, "Technical review")
    second = _work(context, "Commercial review")
    barrier = threading.Barrier(2)

    def attempt(item_id: str, prerequisite_id: str) -> str:
        barrier.wait()
        try:
            context.execution_service.add_dependency(
                context.bid.bid_id,
                "WORK_ITEM",
                item_id,
                "WORK_ITEM",
                prerequisite_id,
                None,
                "jason",
            )
        except ValueError as exc:
            return str(exc)
        return "success"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda pair: attempt(*pair),
                ((first, second), (second, first)),
            )
        )

    assert results.count("success") == 1
    [refusal] = [result for result in results if result != "success"]
    assert "would depend on" in refusal
    assert "locked" not in refusal.lower()
    assert len(context.execution_repository.list_dependencies(context.bid.bid_id)) == 1
    assert _audit_count(context, "bid_execution_dependency_added") == 1


def test_removed_edge_can_be_readded_and_history_is_preserved(context: Context) -> None:
    item_id = _work(context, "Assemble response")
    prerequisite_id = _returnable(context, "DEL-READD", "Technical schedule")
    first = context.execution_service.add_dependency(
        context.bid.bid_id,
        "WORK_ITEM",
        item_id,
        "RETURNABLE",
        prerequisite_id,
        None,
        "jason",
    )
    context.execution_service.remove_dependency(context.bid.bid_id, first.dependency_id, "jason")
    second = context.execution_service.add_dependency(
        context.bid.bid_id,
        "WORK_ITEM",
        item_id,
        "RETURNABLE",
        prerequisite_id,
        "Reinstated after scope review",
        "jason",
    )

    history = context.execution_repository.list_dependencies(context.bid.bid_id)
    assert len(history) == 2
    by_id = {dependency.dependency_id: dependency for dependency in history}
    assert by_id[first.dependency_id].removed_at == NOW
    assert by_id[second.dependency_id] == second
    assert second.dependency_id != first.dependency_id


def test_audit_failure_rolls_back_add_and_remove(
    context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    item_id = _work(context, "Compile offer")
    prerequisite_id = _returnable(context, "DEL-AUDIT", "Technical offer")
    real_audit = context.execution_repository._audit

    def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(context.execution_repository, "_audit", fail_audit)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        context.execution_service.add_dependency(
            context.bid.bid_id,
            "WORK_ITEM",
            item_id,
            "RETURNABLE",
            prerequisite_id,
            None,
            "jason",
        )
    assert context.execution_repository.list_dependencies(context.bid.bid_id) == []

    monkeypatch.setattr(context.execution_repository, "_audit", real_audit)
    dependency = context.execution_service.add_dependency(
        context.bid.bid_id,
        "WORK_ITEM",
        item_id,
        "RETURNABLE",
        prerequisite_id,
        None,
        "jason",
    )
    monkeypatch.setattr(context.execution_repository, "_audit", fail_audit)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        context.execution_service.remove_dependency(
            context.bid.bid_id, dependency.dependency_id, "jason"
        )
    [still_active] = context.execution_repository.list_dependencies(context.bid.bid_id)
    assert still_active.removed_at is None
    assert _audit_count(context, "bid_execution_dependency_removed") == 0


def test_dependency_hard_delete_is_blocked(context: Context) -> None:
    item_id = _work(context, "Prepare appendix")
    prerequisite_id = _returnable(context, "DEL-DELETE-DEP", "Compliance schedule")
    dependency = context.execution_service.add_dependency(
        context.bid.bid_id,
        "WORK_ITEM",
        item_id,
        "RETURNABLE",
        prerequisite_id,
        None,
        "jason",
    )

    with context.db._conn() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute(
                "DELETE FROM bid_execution_dependencies WHERE dependency_id=?",
                (dependency.dependency_id,),
            )


def test_completing_prerequisite_never_writes_to_dependent(context: Context) -> None:
    dependent_id = _returnable(context, "DEL-DEPENDENT", "Submission cover")
    prerequisite_id = _returnable(context, "DEL-PREREQUISITE", "Approval memo")
    dependent = context.execution_service.set_record(
        context.bid.bid_id,
        "RETURNABLE",
        dependent_id,
        {"expected_version": 0, "responsible": "Bid Manager"},
        "jason",
    )
    context.execution_service.add_dependency(
        context.bid.bid_id,
        "RETURNABLE",
        dependent_id,
        "RETURNABLE",
        prerequisite_id,
        None,
        "jason",
    )

    context.execution_service.set_record(
        context.bid.bid_id,
        "RETURNABLE",
        prerequisite_id,
        {
            "expected_version": 0,
            "prep_status": "COMPLETE",
            "completion_evidence": "Approval recorded in the review log",
        },
        "jason",
    )

    assert (
        context.execution_service.get_record(context.bid.bid_id, "RETURNABLE", dependent_id)
        == dependent
    )
