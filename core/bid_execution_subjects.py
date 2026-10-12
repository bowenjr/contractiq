"""Resolve Stage 3 execution subjects through authoritative register repositories."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict

from core.bid_execution import ExecutionSubjectKind
from core.deliverable_repository import DeliverableRepository
from core.deliverables import (
    DeliverableCriticality,
    DeliverableState,
    DueBasis,
    LifecyclePhase,
)
from core.vendor_document_control import VerificationStatus
from core.vendor_document_repository import VendorDocumentRepository
from core.work_item_repository import WorkItemRepository
from core.work_items import WorkItemPriority, WorkItemStatus


class ExecutionSubjectFacts(BaseModel):
    """Frozen native facts for one subject in an authoritative register."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ExecutionSubjectKind
    subject_id: str
    bid_id: str
    title: str
    native_owner: str | None = None

    criticality: DeliverableCriticality | None = None
    lifecycle_phase: LifecyclePhase | None = None
    workflow_state: DeliverableState | None = None
    condition_active: bool | None = None
    fixed_due_date: date | None = None
    due_basis: DueBasis | None = None

    required: bool | None = None
    applicable: bool | None = None
    verification_status: VerificationStatus | None = None

    native_status: WorkItemStatus | None = None
    priority: WorkItemPriority | None = None
    due_date: date | None = None


class ExecutionSubjectResolver:
    """Resolve and Bid-scope existing work, deliverable, and manufacturer records."""

    def __init__(
        self,
        work_item_repository: WorkItemRepository,
        deliverable_repository: DeliverableRepository,
        vendor_document_repository: VendorDocumentRepository,
    ) -> None:
        self.work_items = work_item_repository
        self.deliverables = deliverable_repository
        self.vendor_documents = vendor_document_repository

    @staticmethod
    def _kind(kind: ExecutionSubjectKind | str) -> ExecutionSubjectKind:
        try:
            return ExecutionSubjectKind(kind)
        except ValueError as exc:
            raise ValueError(
                "unknown execution item type; choose a pre-bid action, returnable, or "
                "manufacturer input"
            ) from exc

    @staticmethod
    def _missing(kind: ExecutionSubjectKind) -> ValueError:
        return ValueError(
            f"{kind.label} was not found; choose an existing {kind.label.lower()} from this Bid"
        )

    @staticmethod
    def _cross_bid(kind: ExecutionSubjectKind) -> ValueError:
        return ValueError(
            f"{kind.label} belongs to a different Bid; open it from the correct Bid workspace"
        )

    def resolve(
        self,
        bid_id: str,
        kind: ExecutionSubjectKind | str,
        subject_id: str,
    ) -> ExecutionSubjectFacts:
        """Return native facts or refuse a missing or cross-Bid subject with a fix."""
        subject_kind = self._kind(kind)
        if subject_kind is ExecutionSubjectKind.WORK_ITEM:
            return self._resolve_work_item(bid_id, subject_id)
        if subject_kind is ExecutionSubjectKind.RETURNABLE:
            return self._resolve_returnable(bid_id, subject_id)
        return self._resolve_manufacturer_input(bid_id, subject_id)

    def _resolve_work_item(self, bid_id: str, subject_id: str) -> ExecutionSubjectFacts:
        item = self.work_items.get(subject_id)
        if item is None:
            raise self._missing(ExecutionSubjectKind.WORK_ITEM)
        if item.bid_id != bid_id:
            raise self._cross_bid(ExecutionSubjectKind.WORK_ITEM)
        return ExecutionSubjectFacts(
            kind=ExecutionSubjectKind.WORK_ITEM,
            subject_id=item.work_item_id,
            bid_id=bid_id,
            title=item.title,
            native_status=item.status,
            priority=item.priority,
            due_date=item.due_date,
        )

    def _resolve_returnable(self, bid_id: str, subject_id: str) -> ExecutionSubjectFacts:
        row = next(
            (
                item
                for item in self.deliverables.list()
                if str(item["deliverable_id"]) == subject_id
            ),
            None,
        )
        if row is None:
            raise self._missing(ExecutionSubjectKind.RETURNABLE)
        if str(row["bid_id"]) != bid_id:
            raise self._cross_bid(ExecutionSubjectKind.RETURNABLE)
        fixed_due_date = row["fixed_due_date"]
        return ExecutionSubjectFacts(
            kind=ExecutionSubjectKind.RETURNABLE,
            subject_id=subject_id,
            bid_id=bid_id,
            title=str(row["title"]),
            native_owner=str(row["owner"]) if row["owner"] is not None else None,
            criticality=DeliverableCriticality(str(row["criticality"])),
            lifecycle_phase=LifecyclePhase(str(row["lifecycle_phase"])),
            workflow_state=DeliverableState(str(row["workflow_state"])),
            condition_active=bool(row["condition_active"]),
            fixed_due_date=(
                date.fromisoformat(str(fixed_due_date)) if fixed_due_date is not None else None
            ),
            due_basis=DueBasis(str(row["due_basis"])),
        )

    def _resolve_manufacturer_input(self, bid_id: str, subject_id: str) -> ExecutionSubjectFacts:
        requirement = self.vendor_documents.get_requirement(subject_id)
        if requirement is None:
            raise self._missing(ExecutionSubjectKind.MANUFACTURER_INPUT)
        package = self.vendor_documents.get_package(requirement.package_id)
        if package is None:
            raise ValueError(
                "manufacturer input has no supplier package; repair the manufacturer register "
                "before using it in Bid execution"
            )
        if package.bid_id != bid_id:
            raise self._cross_bid(ExecutionSubjectKind.MANUFACTURER_INPUT)
        return ExecutionSubjectFacts(
            kind=ExecutionSubjectKind.MANUFACTURER_INPUT,
            subject_id=requirement.requirement_id,
            bid_id=bid_id,
            title=requirement.deliverable_title,
            native_owner=requirement.internal_owner,
            required=requirement.required,
            applicable=requirement.applicable,
            verification_status=requirement.verification_status,
        )
