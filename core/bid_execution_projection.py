"""Unified, deterministic list projection for Bid execution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from core.bid_execution import BidExecutionRecord, ExecutionSubjectKind, PrepStatus
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
from core.bid_execution_trace import BidExecutionTrace, ExecutionSourceTrace
from core.bid_repository import BidRepository
from core.deliverable_repository import DeliverableRepository
from core.deliverables import DeliverableCriticality, DeliverableState, DueBasis, LifecyclePhase
from core.ops08 import NegotiationState, Ops08Repository, PositionDisposition
from core.vendor_document_control import CustomerRequirement
from core.vendor_document_repository import VendorDocumentRepository
from core.work_item_repository import WorkItemRepository
from core.work_item_service import WorkItemService
from core.work_items import WorkItem, WorkItemPriority, WorkItemStatus

_DISCOVERY_MARKER = re.compile(r"\[DISC:([^\]]+)]")


class ExecutionListKind(StrEnum):
    """The four execution lists sharing one row contract."""

    RETURNABLE = "RETURNABLE"
    MANUFACTURER_INPUT = "MANUFACTURER_INPUT"
    ACTION = "ACTION"
    COMMERCIAL = "COMMERCIAL"

    @property
    def slug(self) -> str:
        """Return the stable item URL segment for this list kind."""
        return {
            self.RETURNABLE: "returnable",
            self.MANUFACTURER_INPUT: "manufacturer-input",
            self.ACTION: "work-item",
            self.COMMERCIAL: "commercial",
        }[self]


class ExecutionFilter(StrEnum):
    """The only supported execution list filters."""

    ALL = "all"
    OUTSTANDING = "outstanding"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETE = "complete"
    UNASSIGNED = "unassigned"
    OVERDUE = "overdue"
    WAITING = "waiting"
    NOT_REQUIRED = "not_required"


class ExecutionItemLink(BaseModel):
    """Business-readable dependency link embedded in a projected item."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    status_label: str
    item_url: str


class ExecutionItem(BaseModel):
    """Frozen row shape shared by execution and commercial lists."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ExecutionListKind
    subject_id: str
    bid_id: str
    title: str
    description: str
    responsible: str | None = None
    responsible_origin: Literal["assigned", "register"] | None = None
    contributors: list[str] = Field(default_factory=list)
    internal_due_date: date | None = None
    customer_deadline: date | None = None
    customer_deadline_label: str | None = None
    status: PrepStatus
    status_label: str
    priority: WorkItemPriority | None = None
    mandatory: bool = False
    critical: bool = False
    overdue: bool = False
    unassigned: bool = True
    waiting_on_prerequisite: bool = False
    prerequisites: list[ExecutionItemLink] = Field(default_factory=list)
    dependents: list[ExecutionItemLink] = Field(default_factory=list)
    source_label: str | None = None
    source_url: str | None = None
    evidence: str | None = None
    last_updated: datetime
    item_url: str
    origin: Literal["From RFP finding", "Added manually"]


@dataclass(frozen=True)
class _Draft:
    kind: ExecutionListKind
    subject_kind: ExecutionSubjectKind | None
    subject_id: str
    bid_id: str
    title: str
    description: str
    responsible: str | None
    responsible_origin: Literal["assigned", "register"] | None
    contributors: list[str]
    internal_due_date: date | None
    customer_deadline: date | None
    customer_deadline_label: str | None
    status: PrepStatus
    status_label: str
    priority: WorkItemPriority | None
    mandatory: bool
    evidence: str | None
    last_updated: datetime
    item_url: str
    origin: Literal["From RFP finding", "Added manually"]
    source_label: str | None
    source_url: str | None
    normally_visible: bool = True


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _as_date(value: object) -> date | None:
    return date.fromisoformat(str(value)) if value else None


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _latest(*values: datetime | None) -> datetime:
    present = [value for value in values if value is not None]
    if not present:
        raise ValueError("execution item has no update timestamp; repair its source register")
    return max(present)


def _effective_owner(
    overlay: BidExecutionRecord | None, native_owner: str | None
) -> tuple[str | None, Literal["assigned", "register"] | None]:
    if overlay is not None and overlay.responsible:
        return overlay.responsible, "assigned"
    normalized = _optional_text(native_owner)
    return (normalized, "register") if normalized else (None, None)


def _source_label(trace: ExecutionSourceTrace) -> str | None:
    if not trace.sources:
        return None
    source = trace.sources[0]
    return f"{source.document_name} — {source.locator}" if source.locator else source.document_name


class ExecutionProjectionService:
    """Load each authoritative register once and produce shared execution rows."""

    def __init__(
        self,
        work_item_repository: WorkItemRepository | WorkItemService,
        deliverable_repository: DeliverableRepository,
        vendor_document_repository: VendorDocumentRepository,
        bid_execution_repository: BidExecutionRepository | BidExecutionService,
        ops08_repository: Ops08Repository,
        bid_repository: BidRepository,
        trace: BidExecutionTrace,
    ) -> None:
        self.work_items = (
            work_item_repository.repository
            if isinstance(work_item_repository, WorkItemService)
            else work_item_repository
        )
        self.deliverables = deliverable_repository
        self.vendor_documents = vendor_document_repository
        self.execution = (
            bid_execution_repository.repository
            if isinstance(bid_execution_repository, BidExecutionService)
            else bid_execution_repository
        )
        self.ops08 = ops08_repository
        self.bids = bid_repository
        self.trace = trace

    def items(
        self,
        bid_id: str,
        list_kind: ExecutionListKind | str,
        state: ExecutionFilter | str,
        as_of: date,
    ) -> list[ExecutionItem]:
        """Return one filtered execution list using caller-supplied date facts."""
        kind = ExecutionListKind(list_kind)
        selected_filter = ExecutionFilter(state)
        projected = self._project(bid_id, as_of)
        selected = [item for item, visible in projected if item.kind is kind and visible]
        if kind is ExecutionListKind.MANUFACTURER_INPUT:
            if selected_filter is ExecutionFilter.NOT_REQUIRED:
                selected = [
                    item
                    for item, _ in projected
                    if item.kind is kind and item.status is PrepStatus.NOT_REQUIRED
                ]
        return self._ordered(self._filter(selected, selected_filter))

    def all_items(self, bid_id: str, as_of: date) -> list[ExecutionItem]:
        """Return all normally visible rows across the four registers."""
        return self._ordered([item for item, visible in self._project(bid_id, as_of) if visible])

    def _project(self, bid_id: str, as_of: date) -> list[tuple[ExecutionItem, bool]]:
        bid = self.bids.get_bid(bid_id)
        if bid is None:
            raise ValueError("Bid not found; return to the Bid list and choose an existing Bid")
        work_items = self.work_items.list(bid_id, active_only=False)
        deliverables = self.deliverables.list(bid_id)
        packages = self.vendor_documents.list_packages(bid_id)
        package_ids = {package.package_id for package in packages}
        requirements = [
            item
            for item in self.vendor_documents.list_requirements()
            if item.package_id in package_ids
        ]
        overlays = {
            (record.subject_kind, record.subject_id): record
            for record in self.execution.list_records(bid_id)
        }
        latest_notes: dict[tuple[ExecutionSubjectKind, str], datetime] = {}
        for note in self.execution.list_notes_for_bid(bid_id):
            latest_notes[(note.subject_kind, note.subject_id)] = max(
                note.created_at,
                latest_notes.get((note.subject_kind, note.subject_id), note.created_at),
            )
        dependencies = [
            edge for edge in self.execution.list_dependencies(bid_id) if edge.removed_at is None
        ]
        commercial = self.ops08.workspace(bid_id)
        subjects = [
            *((ExecutionSubjectKind.WORK_ITEM, item.work_item_id) for item in work_items),
            *(
                (ExecutionSubjectKind.RETURNABLE, str(row["deliverable_id"]))
                for row in deliverables
            ),
            *(
                (ExecutionSubjectKind.MANUFACTURER_INPUT, item.requirement_id)
                for item in requirements
            ),
        ]
        markers = self._markers(work_items, requirements)
        traces = self.trace.source_traces(bid_id, subjects, markers=markers)

        flat_drafts: list[_Draft] = [
            *(self._work_draft(item, overlays, latest_notes, traces) for item in work_items),
            *(
                self._returnable_draft(row, bid.customer_due_date, overlays, latest_notes, traces)
                for row in deliverables
            ),
            *(
                self._manufacturer_draft(bid_id, item, overlays, latest_notes, traces)
                for item in requirements
            ),
        ]
        for row in commercial:
            flat_drafts.extend(self._commercial_drafts(bid_id, row))
        by_subject = {
            (draft.subject_kind, draft.subject_id): draft
            for draft in flat_drafts
            if draft.subject_kind is not None
        }
        prerequisites: dict[tuple[ExecutionSubjectKind, str], list[_Draft]] = {}
        dependents: dict[tuple[ExecutionSubjectKind, str], list[_Draft]] = {}
        for edge in dependencies:
            item_key = (edge.item_kind, edge.item_id)
            prerequisite_key = (edge.prerequisite_kind, edge.prerequisite_id)
            if item_key in by_subject and prerequisite_key in by_subject:
                prerequisites.setdefault(item_key, []).append(by_subject[prerequisite_key])
                dependents.setdefault(prerequisite_key, []).append(by_subject[item_key])
        projected: list[tuple[ExecutionItem, bool]] = []
        for draft in flat_drafts:
            key = (draft.subject_kind, draft.subject_id) if draft.subject_kind is not None else None
            projected.append(
                (
                    self._finish(
                        draft,
                        as_of,
                        prerequisites.get(key, []) if key is not None else [],
                        dependents.get(key, []) if key is not None else [],
                    ),
                    draft.normally_visible,
                )
            )
        return projected

    @staticmethod
    def _markers(
        work_items: list[WorkItem], requirements: list[CustomerRequirement]
    ) -> dict[tuple[ExecutionSubjectKind, str], str]:
        values: dict[tuple[ExecutionSubjectKind, str], str] = {}
        for kind, subject_id, text in (
            *(
                (ExecutionSubjectKind.WORK_ITEM, item.work_item_id, item.details)
                for item in work_items
            ),
            *(
                (
                    ExecutionSubjectKind.MANUFACTURER_INPUT,
                    item.requirement_id,
                    item.source_row_reference,
                )
                for item in requirements
            ),
        ):
            match = _DISCOVERY_MARKER.search(text or "")
            if match:
                values[(kind, subject_id)] = match.group(1)
        return values

    @staticmethod
    def _work_draft(
        item: WorkItem,
        overlays: dict[tuple[ExecutionSubjectKind, str], BidExecutionRecord],
        notes: dict[tuple[ExecutionSubjectKind, str], datetime],
        traces: dict[tuple[ExecutionSubjectKind, str], ExecutionSourceTrace],
    ) -> _Draft:
        key = (ExecutionSubjectKind.WORK_ITEM, item.work_item_id)
        overlay = overlays.get(key)
        mapped = map_work_item_status(item.status)
        label = mapped.status.label
        if item.status is WorkItemStatus.WAITING:
            waiting_party = item.waiting_party_label or item.waiting_on
            label = f"Waiting on {waiting_party}" if waiting_party else "Waiting"
        trace = traces[key]
        return _Draft(
            ExecutionListKind.ACTION,
            key[0],
            key[1],
            str(item.bid_id),
            item.title,
            item.details or "",
            *_effective_owner(overlay, None),
            list(overlay.contributors) if overlay else [],
            item.due_date,
            None,
            None,
            mapped.status,
            label,
            item.priority,
            False,
            item.completion_evidence or item.completion_outcome,
            _latest(item.updated_at, overlay.updated_at if overlay else None, notes.get(key)),
            f"/bids/{item.bid_id}/execution/items/work-item/{quote(item.work_item_id)}",
            trace.origin,
            _source_label(trace),
            trace.source_url,
        )

    @staticmethod
    def _returnable_draft(
        row: dict[str, Any],
        bid_due: date,
        overlays: dict[tuple[ExecutionSubjectKind, str], BidExecutionRecord],
        notes: dict[tuple[ExecutionSubjectKind, str], datetime],
        traces: dict[tuple[ExecutionSubjectKind, str], ExecutionSourceTrace],
    ) -> _Draft:
        subject_id = str(row["deliverable_id"])
        key = (ExecutionSubjectKind.RETURNABLE, subject_id)
        overlay = overlays.get(key)
        effective = effective_returnable_status(
            overlay.prep_status if overlay else None,
            DeliverableState(str(row["workflow_state"])),
            bool(row["condition_active"]),
        )
        mandatory = (
            DeliverableCriticality(str(row["criticality"])) is DeliverableCriticality.MANDATORY
        )
        fixed = DueBasis(str(row["due_basis"])) is DueBasis.FIXED_DATE
        trace = traces[key]
        label = effective.status.label
        if effective.reason:
            label = f"{label} — {effective.reason}"
        return _Draft(
            ExecutionListKind.RETURNABLE,
            key[0],
            key[1],
            str(row["bid_id"]),
            str(row["title"]),
            str(row["description"]),
            *_effective_owner(overlay, _optional_text(row["owner"])),
            list(overlay.contributors) if overlay else [],
            overlay.internal_due_date if overlay else None,
            _as_date(row["fixed_due_date"]) if fixed else bid_due,
            "stated in the RFP" if fixed else "Bid closing",
            effective.status,
            label,
            overlay.priority if overlay else None,
            mandatory,
            overlay.completion_evidence if overlay else None,
            _latest(
                _as_datetime(row["updated_at"]),
                overlay.updated_at if overlay else None,
                notes.get(key),
            ),
            f"/bids/{row['bid_id']}/execution/items/returnable/{quote(subject_id)}",
            trace.origin,
            _source_label(trace),
            trace.source_url,
            LifecyclePhase(str(row["lifecycle_phase"]))
            in {LifecyclePhase.WITH_BID, LifecyclePhase.BOTH},
        )

    @staticmethod
    def _manufacturer_draft(
        bid_id: str,
        item: CustomerRequirement,
        overlays: dict[tuple[ExecutionSubjectKind, str], BidExecutionRecord],
        notes: dict[tuple[ExecutionSubjectKind, str], datetime],
        traces: dict[tuple[ExecutionSubjectKind, str], ExecutionSourceTrace],
    ) -> _Draft:
        key = (ExecutionSubjectKind.MANUFACTURER_INPUT, item.requirement_id)
        overlay = overlays.get(key)
        status = (
            map_manufacturer_status(item.verification_status, applicable=item.applicable)
            if item.required
            else PrepStatus.NOT_REQUIRED
        )
        trace = traces[key]
        return _Draft(
            ExecutionListKind.MANUFACTURER_INPUT,
            key[0],
            key[1],
            bid_id,
            item.deliverable_title,
            item.description,
            *_effective_owner(overlay, item.internal_owner),
            list(overlay.contributors) if overlay else [],
            overlay.internal_due_date if overlay else None,
            None,
            None,
            status,
            status.label,
            overlay.priority if overlay else None,
            False,
            item.evidence_reference,
            _latest(item.updated_at, overlay.updated_at if overlay else None, notes.get(key)),
            f"/bids/{bid_id}/execution/items/manufacturer-input/{quote(item.requirement_id)}",
            trace.origin,
            _source_label(trace),
            trace.source_url,
            item.required and item.applicable,
        )

    @staticmethod
    def _commercial_drafts(bid_id: str, row: dict[str, Any]) -> list[_Draft]:
        disposition = PositionDisposition(str(row["disposition"]))
        negotiation = NegotiationState(str(row["negotiation_state"]))
        clarification = (
            disposition is PositionDisposition.CLARIFICATION_REQUIRED
            and negotiation is not NegotiationState.RESOLVED
        )
        decision = (
            disposition is PositionDisposition.NOT_REVIEWED
            and bool(_optional_text(row["customer_position"]))
        ) or (
            disposition in {PositionDisposition.REJECT, PositionDisposition.QUALIFY}
            and bool(_optional_text(row["required_approver"]))
            and not bool(_optional_text(row["current_outcome"]))
        )
        if not clarification and not decision:
            return []
        subject_id = str(row["position_id"])
        marker = _DISCOVERY_MARKER.search(str(row["supporting_evidence"] or ""))
        origin: Literal["From RFP finding", "Added manually"] = (
            "From RFP finding" if marker else "Added manually"
        )
        status = PrepStatus.BLOCKED if clarification else PrepStatus.NOT_STARTED
        state_label = "Clarification" if clarification else "Decision needed"
        owner = _optional_text(row["owner"])
        due = _as_date(row["due_date"])
        return [
            _Draft(
                ExecutionListKind.COMMERCIAL,
                None,
                subject_id,
                bid_id,
                str(row["label"]),
                str(row["description"]),
                owner,
                "register" if owner else None,
                [],
                due,
                None,
                None,
                status,
                state_label,
                None,
                False,
                _optional_text(row["current_outcome"])
                or _optional_text(row["supporting_evidence"]),
                _as_datetime(row["created_at"]),
                f"/commercial/{quote(subject_id)}",
                origin,
                _optional_text(row["source_locator"]),
                (f"/bids/{bid_id}/execution/source/{marker.group(1)}" if marker else None),
            )
        ]

    @staticmethod
    def _finish(
        draft: _Draft,
        as_of: date,
        prerequisite_drafts: list[_Draft],
        dependent_drafts: list[_Draft],
    ) -> ExecutionItem:
        waiting = waiting_on_prerequisite(item.status for item in prerequisite_drafts)

        def links(values: list[_Draft]) -> list[ExecutionItemLink]:
            return [
                ExecutionItemLink(
                    title=value.title,
                    status_label=value.status_label,
                    item_url=value.item_url,
                )
                for value in sorted(values, key=lambda item: item.title.casefold())
            ]

        return ExecutionItem(
            kind=draft.kind,
            subject_id=draft.subject_id,
            bid_id=draft.bid_id,
            title=draft.title,
            description=draft.description,
            responsible=draft.responsible,
            responsible_origin=draft.responsible_origin,
            contributors=draft.contributors,
            internal_due_date=draft.internal_due_date,
            customer_deadline=draft.customer_deadline,
            customer_deadline_label=draft.customer_deadline_label,
            status=draft.status,
            status_label=draft.status_label,
            priority=draft.priority,
            mandatory=draft.mandatory,
            critical=is_critical(draft.priority, mandatory=draft.mandatory),
            overdue=is_overdue(draft.internal_due_date, draft.status, as_of),
            unassigned=draft.responsible is None,
            waiting_on_prerequisite=waiting,
            prerequisites=links(prerequisite_drafts),
            dependents=links(dependent_drafts),
            source_label=draft.source_label,
            source_url=draft.source_url,
            evidence=draft.evidence,
            last_updated=draft.last_updated,
            item_url=draft.item_url,
            origin=draft.origin,
        )

    @staticmethod
    def _filter(items: list[ExecutionItem], state: ExecutionFilter) -> list[ExecutionItem]:
        if state is ExecutionFilter.ALL:
            return items
        if state is ExecutionFilter.OUTSTANDING:
            return [
                item
                for item in items
                if item.status not in {PrepStatus.COMPLETE, PrepStatus.NOT_REQUIRED}
            ]
        if state is ExecutionFilter.IN_PROGRESS:
            return [item for item in items if item.status is PrepStatus.IN_PROGRESS]
        if state is ExecutionFilter.BLOCKED:
            return [
                item
                for item in items
                if item.status is PrepStatus.BLOCKED or item.waiting_on_prerequisite
            ]
        if state is ExecutionFilter.COMPLETE:
            return [item for item in items if item.status is PrepStatus.COMPLETE]
        if state is ExecutionFilter.UNASSIGNED:
            return [item for item in items if item.unassigned]
        if state is ExecutionFilter.OVERDUE:
            return [item for item in items if item.overdue]
        if state is ExecutionFilter.WAITING:
            return [item for item in items if item.waiting_on_prerequisite]
        return [item for item in items if item.status is PrepStatus.NOT_REQUIRED]

    @staticmethod
    def _ordered(items: list[ExecutionItem]) -> list[ExecutionItem]:
        return sorted(
            items,
            key=lambda item: (
                not item.overdue,
                not (item.status is PrepStatus.BLOCKED or item.waiting_on_prerequisite),
                item.internal_due_date is None,
                item.internal_due_date or date.max,
                item.title.casefold(),
                item.subject_id,
            ),
        )
