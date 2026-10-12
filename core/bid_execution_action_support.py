"""Shared overlay, dependency, bulk, and navigation commands for Bid execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date

from core.bid_execution import BidExecutionUpdate, ExecutionSubjectKind, PrepStatus
from core.bid_execution_action_models import (
    ActionResult,
    ManufacturerInputChanges,
    ReturnableChanges,
)
from core.bid_execution_projection import (
    ExecutionFilter,
    ExecutionListKind,
    ExecutionProjectionService,
)
from core.bid_execution_service import BidExecutionService
from core.bid_repository import BidRepository
from core.ops07 import Ops07Repository
from core.requirement_repository import RequirementRepository
from core.work_item_service import WorkItemService

ReturnableChangesData = ReturnableChanges | Mapping[str, object]
ManufacturerInputChangesData = ManufacturerInputChanges | Mapping[str, object]

_SUBJECT_LIST: dict[ExecutionSubjectKind, ExecutionListKind] = {
    ExecutionSubjectKind.WORK_ITEM: ExecutionListKind.ACTION,
    ExecutionSubjectKind.RETURNABLE: ExecutionListKind.RETURNABLE,
    ExecutionSubjectKind.MANUFACTURER_INPUT: ExecutionListKind.MANUFACTURER_INPUT,
}


class BidExecutionActionSupport:
    """Provide shared commands to the concrete Bid execution action service."""

    def __init__(
        self,
        work_item_service: WorkItemService,
        execution_service: BidExecutionService,
        projection: ExecutionProjectionService,
        bid_repository: BidRepository,
        *,
        ops07_repository: Ops07Repository | None = None,
        requirement_repository: RequirementRepository | None = None,
        as_of_factory: Callable[[], date] | None = None,
    ) -> None:
        self.work_items = work_item_service
        self.execution = execution_service
        self.projection = projection
        self.bids = bid_repository
        self.ops07 = ops07_repository or Ops07Repository(bid_repository.db)
        self.requirements = requirement_repository or RequirementRepository(bid_repository.db)
        self._as_of = as_of_factory or date.today

    def _bid_deadline(self, bid_id: str) -> date:
        bid = self.bids.get_bid(bid_id)
        if bid is None:
            raise ValueError("Bid not found; return to the Bid list and choose an existing Bid")
        return bid.customer_due_date

    def _validate_due_date(self, bid_id: str, due_date: date | None) -> None:
        deadline = self._bid_deadline(bid_id)
        if due_date is not None and due_date > deadline:
            raise ValueError(
                f"the internal due date is after the customer deadline of {deadline.isoformat()}; "
                "choose a date on or before the customer deadline"
            )

    @staticmethod
    def _result(
        message: str,
        kind: ExecutionSubjectKind,
        subject_id: str | None,
        next_url: str | None,
        *,
        changed: bool,
        reused: bool = False,
    ) -> ActionResult:
        return ActionResult(
            message=message,
            reused=reused,
            changed=changed,
            subject_kind=kind,
            subject_id=subject_id,
            next_item_url=next_url,
        )

    def _next(self, bid_id: str, kind: ExecutionSubjectKind, subject_id: str) -> str | None:
        return self.next_item_url(bid_id, _SUBJECT_LIST[kind], subject_id, self._as_of())

    def _update_overlay(
        self,
        bid_id: str,
        kind: ExecutionSubjectKind,
        subject_id: str,
        changes: ReturnableChanges | ManufacturerInputChanges,
        actor: str,
    ) -> ActionResult:
        fields = changes.model_fields_set
        if "note" in fields and changes.note is None:
            raise ValueError("progress note cannot be blank; enter a note or leave it unchanged")
        current = self.execution.get_record(bid_id, kind, subject_id)
        values: dict[str, object] = {
            "responsible": current.responsible if current else None,
            "contributors": list(current.contributors) if current else [],
            "internal_due_date": current.internal_due_date if current else None,
            "prep_status": current.prep_status if current else None,
            "priority": current.priority if current else None,
            "blocked_reason": current.blocked_reason if current else None,
            "completion_evidence": current.completion_evidence if current else None,
            "evidence_document_version_id": current.evidence_document_version_id
            if current
            else None,
        }
        permitted = set(values) & fields
        for name in permitted:
            value = getattr(changes, name)
            values[name] = [] if name == "contributors" and value is None else value
        candidate = BidExecutionUpdate.model_validate(
            {
                "expected_version": current.version if current else 0,
                **values,
                "note": changes.note if "note" in fields else None,
            }
        )
        changed_fields = any(
            values[name]
            != (
                getattr(current, name)
                if current is not None
                else ([] if name == "contributors" else None)
            )
            for name in permitted
        )
        has_note = "note" in fields and changes.note is not None
        if changed_fields:
            self.execution.set_record(bid_id, kind, subject_id, candidate, actor)
        elif has_note and changes.note is not None:
            self.execution.add_note(bid_id, kind, subject_id, changes.note, actor)
        changed = changed_fields or has_note
        noun = "returnable" if kind is ExecutionSubjectKind.RETURNABLE else "manufacturer input"
        return self._result(
            f"Saved - {noun} updated." if changed else "No changes to save.",
            kind,
            subject_id,
            self._next(bid_id, kind, subject_id),
            changed=changed,
        )

    def update_returnable(
        self, bid_id: str, deliverable_id: str, changes: ReturnableChangesData, actor: str
    ) -> ActionResult:
        """Merge supplied returnable fields over its current execution overlay."""
        request = ReturnableChanges.model_validate(changes)
        return self._update_overlay(
            bid_id, ExecutionSubjectKind.RETURNABLE, deliverable_id, request, actor
        )

    def update_manufacturer_input(
        self,
        bid_id: str,
        requirement_id: str,
        changes: ManufacturerInputChangesData,
        actor: str,
    ) -> ActionResult:
        """Merge editable manufacturer fields while keeping verification status read-only."""
        request = ManufacturerInputChanges.model_validate(changes)
        if "status" in request.model_fields_set:
            raise ValueError(
                "manufacturer-input status comes from verification; change it on the "
                "manufacturer-requirement screen"
            )
        return self._update_overlay(
            bid_id,
            ExecutionSubjectKind.MANUFACTURER_INPUT,
            requirement_id,
            request,
            actor,
        )

    def add_note(
        self,
        bid_id: str,
        kind: ExecutionSubjectKind | str,
        subject_id: str,
        body: str,
        actor: str,
    ) -> ActionResult:
        """Append one progress note through the execution service."""
        subject_kind = self._subject_kind(kind)
        self.execution.add_note(bid_id, subject_kind, subject_id, body, actor)
        return self._result(
            "Saved - progress note added.",
            subject_kind,
            subject_id,
            self._next(bid_id, subject_kind, subject_id),
            changed=True,
        )

    def add_prerequisite(
        self,
        bid_id: str,
        item_kind: ExecutionSubjectKind | str,
        item_id: str,
        prerequisite_kind: ExecutionSubjectKind | str,
        prerequisite_id: str,
        note: str | None,
        actor: str,
    ) -> ActionResult:
        """Add one validated dependency through the execution service."""
        kind = self._subject_kind(item_kind)
        prerequisite_subject_kind = self._subject_kind(prerequisite_kind)
        self.execution.add_dependency(
            bid_id, kind, item_id, prerequisite_subject_kind, prerequisite_id, note, actor
        )
        return self._result(
            "Saved - prerequisite added.",
            kind,
            item_id,
            self._next(bid_id, kind, item_id),
            changed=True,
        )

    def remove_prerequisite(self, bid_id: str, dependency_id: str, actor: str) -> ActionResult:
        """Remove one dependency through the execution service."""
        removed = self.execution.remove_dependency(bid_id, dependency_id, actor)
        return self._result(
            "Saved - prerequisite removed.",
            removed.item_kind,
            removed.item_id,
            self._next(bid_id, removed.item_kind, removed.item_id),
            changed=True,
        )

    @staticmethod
    def _subject_kind(kind: ExecutionSubjectKind | ExecutionListKind | str) -> ExecutionSubjectKind:
        aliases = {
            "ACTION": ExecutionSubjectKind.WORK_ITEM,
            "ACTIONS": ExecutionSubjectKind.WORK_ITEM,
            "WORK-ITEM": ExecutionSubjectKind.WORK_ITEM,
            "RETURNABLES": ExecutionSubjectKind.RETURNABLE,
            "MANUFACTURER-INPUT": ExecutionSubjectKind.MANUFACTURER_INPUT,
            "MANUFACTURER-INPUTS": ExecutionSubjectKind.MANUFACTURER_INPUT,
        }
        raw = (
            kind.value if isinstance(kind, (ExecutionSubjectKind, ExecutionListKind)) else str(kind)
        )
        alias = aliases.get(raw.upper())
        if alias is not None:
            return alias
        return ExecutionSubjectKind(raw.upper().replace("-", "_"))

    def bulk_assign(
        self,
        bid_id: str,
        kind: ExecutionSubjectKind | ExecutionListKind | str,
        subject_ids: Sequence[str],
        responsible: str,
        internal_due_date: date | None,
        actor: str,
    ) -> ActionResult:
        """Validate a complete selection, then assign only changed outstanding items."""
        subject_kind = self._subject_kind(kind)
        ids = list(subject_ids)
        if not ids:
            raise ValueError("no items were selected; select at least one outstanding item")
        if len(set(ids)) != len(ids):
            raise ValueError("the selection contains duplicates; select each item only once")
        owner = responsible.strip()
        if not owner:
            raise ValueError(
                "responsible person cannot be blank; enter the person taking ownership"
            )
        self._validate_due_date(bid_id, internal_due_date)
        for subject_id in ids:
            self.execution.subject_resolver.resolve(bid_id, subject_kind, subject_id)
        projected = self.projection.items(
            bid_id, _SUBJECT_LIST[subject_kind], ExecutionFilter.ALL, self._as_of()
        )
        by_id = {item.subject_id: item for item in projected}
        selected = [by_id.get(subject_id) for subject_id in ids]
        if any(item is None for item in selected) or any(
            item is not None and item.status in {PrepStatus.COMPLETE, PrepStatus.NOT_REQUIRED}
            for item in selected
        ):
            raise ValueError(
                "every selected item must still be outstanding; refresh the list and select again"
            )

        already_owner = sum(item is not None and item.responsible == owner for item in selected)
        changed_count = 0
        for subject_id in ids:
            if subject_kind is ExecutionSubjectKind.WORK_ITEM:
                payload: dict[str, object] = {"responsible": owner}
                if internal_due_date is not None:
                    payload["due_date"] = internal_due_date
                result = self.update_action(bid_id, subject_id, payload, actor)
            else:
                payload = {"responsible": owner}
                if internal_due_date is not None:
                    payload["internal_due_date"] = internal_due_date
                if subject_kind is ExecutionSubjectKind.RETURNABLE:
                    result = self.update_returnable(bid_id, subject_id, payload, actor)
                else:
                    result = self.update_manufacturer_input(bid_id, subject_id, payload, actor)
            changed_count += int(result.changed)

        label = {
            ExecutionSubjectKind.WORK_ITEM: "action",
            ExecutionSubjectKind.RETURNABLE: "returnable",
            ExecutionSubjectKind.MANUFACTURER_INPUT: "manufacturer input",
        }[subject_kind]
        plural = label if len(ids) == 1 else f"{label}s"
        message = (
            f"Assigned {len(ids)} {plural} to {owner} ({already_owner} already had this owner)."
        )
        return self._result(
            message,
            subject_kind,
            ids[-1],
            self._next(bid_id, subject_kind, ids[-1]),
            changed=changed_count > 0,
            reused=changed_count == 0,
        )

    def next_item_url(
        self,
        bid_id: str,
        list_kind: ExecutionListKind | str,
        after_subject_id: str,
        as_of: date,
    ) -> str | None:
        """Return the next outstanding projected item, wrapping and preferring unassigned work."""
        raw = list_kind.value if isinstance(list_kind, ExecutionListKind) else str(list_kind)
        aliases = {
            "actions": ExecutionListKind.ACTION,
            "work-item": ExecutionListKind.ACTION,
            "returnable": ExecutionListKind.RETURNABLE,
            "returnables": ExecutionListKind.RETURNABLE,
            "manufacturer-input": ExecutionListKind.MANUFACTURER_INPUT,
            "manufacturer-inputs": ExecutionListKind.MANUFACTURER_INPUT,
            "commercial": ExecutionListKind.COMMERCIAL,
        }
        kind = aliases.get(raw.casefold())
        if kind is None:
            kind = ExecutionListKind(raw)
        ordered = self.projection.items(bid_id, kind, ExecutionFilter.ALL, as_of)
        if not ordered:
            return None
        start = next(
            (
                index + 1
                for index, item in enumerate(ordered)
                if item.subject_id == after_subject_id
            ),
            0,
        )
        wrapped = ordered[start:] + ordered[:start]
        outstanding = [
            item
            for item in wrapped
            if item.status not in {PrepStatus.COMPLETE, PrepStatus.NOT_REQUIRED}
        ]
        if not outstanding:
            return None
        preferred = next((item for item in outstanding if item.unassigned), outstanding[0])
        return preferred.item_url

    def update_action(
        self,
        bid_id: str,
        work_item_id: str,
        changes: Mapping[str, object],
        actor: str,
    ) -> ActionResult:
        """Declare the concrete service hook used by bulk action assignment."""
        raise NotImplementedError
