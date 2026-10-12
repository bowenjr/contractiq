"""Command layer used by the Bid execution screens."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from core.bid_execution import BidExecutionUpdate, ExecutionSubjectKind, PrepStatus
from core.bid_execution_action_models import (
    ActionChanges,
    ActionResult,
    ManufacturerInputChanges,
    ReturnableChanges,
    WorkItemActionChanges,
)
from core.bid_execution_action_support import BidExecutionActionSupport
from core.deliverable_discovery_destinations import normalized_title
from core.work_items import (
    WorkCategory,
    WorkItem,
    WorkItemCreate,
    WorkItemPriority,
    WorkItemStatus,
)

ActionChangesData = WorkItemActionChanges | Mapping[str, object]

_NATIVE_STATUS: dict[PrepStatus, WorkItemStatus] = {
    PrepStatus.NOT_STARTED: WorkItemStatus.OPEN,
    PrepStatus.IN_PROGRESS: WorkItemStatus.IN_PROGRESS,
    PrepStatus.BLOCKED: WorkItemStatus.BLOCKED,
    PrepStatus.COMPLETE: WorkItemStatus.COMPLETED,
    PrepStatus.NOT_REQUIRED: WorkItemStatus.CANCELLED,
}


class BidExecutionActionService(BidExecutionActionSupport):
    """Coordinate screen commands through existing audited mutation services."""

    def create_manual_action(
        self,
        bid_id: str,
        title: str,
        details: str | None,
        responsible: str | None,
        contributors: list[str] | None,
        due_date: date | None,
        priority: WorkItemPriority | str,
        source_requirement_id: str | None,
        prerequisite: tuple[ExecutionSubjectKind | str, str] | None,
        actor: str,
    ) -> ActionResult:
        """Create one manual action, or reuse an active action with the same title."""
        wanted = normalized_title(title)
        if not wanted:
            raise ValueError("action title cannot be blank; enter a short action title")
        self._validate_due_date(bid_id, due_date)
        overlay = BidExecutionUpdate(
            expected_version=0,
            responsible=responsible,
            contributors=contributors or [],
        )
        prerequisite_kind = (
            self._subject_kind(prerequisite[0]) if prerequisite is not None else None
        )
        if prerequisite is not None and prerequisite_kind is not None:
            self.execution.subject_resolver.resolve(bid_id, prerequisite_kind, prerequisite[1])
        source_requirement = (
            self.requirements.get(source_requirement_id)
            if source_requirement_id is not None
            else None
        )
        if source_requirement is not None and source_requirement.bid_id != bid_id:
            raise ValueError(
                "the source requirement belongs to a different Bid; choose a requirement from "
                "this Bid"
            )
        for item in self.work_items.repository.list(bid_id, active_only=True):
            if normalized_title(item.title) == wanted:
                return self._result(
                    "Saved - existing action reused.",
                    ExecutionSubjectKind.WORK_ITEM,
                    item.work_item_id,
                    self._next(bid_id, ExecutionSubjectKind.WORK_ITEM, item.work_item_id),
                    changed=False,
                    reused=True,
                )
        created = self.work_items.create_work_item(
            WorkItemCreate(
                bid_id=bid_id,
                title=title,
                details=details,
                status=WorkItemStatus.OPEN,
                priority=WorkItemPriority(priority),
                due_date=due_date,
                category=WorkCategory.PROJECT_COORDINATION,
            ),
            actor,
        )
        self.execution.set_record(
            bid_id,
            ExecutionSubjectKind.WORK_ITEM,
            created.work_item_id,
            overlay,
            actor,
        )
        source_as_note = False
        if source_requirement_id is not None:
            if source_requirement is None:
                source_as_note = True
                self.execution.add_note(
                    bid_id,
                    ExecutionSubjectKind.WORK_ITEM,
                    created.work_item_id,
                    f"Source requirement reference: {source_requirement_id}",
                    actor,
                )
            else:
                try:
                    self.ops07.link_work_item(
                        bid_id=bid_id,
                        work_item_id=created.work_item_id,
                        source_kind="requirement",
                        source_id=source_requirement_id,
                        purpose="Bid execution source",
                        actor=actor,
                    )
                except ValueError:
                    source_as_note = True
                    self.execution.add_note(
                        bid_id,
                        ExecutionSubjectKind.WORK_ITEM,
                        created.work_item_id,
                        f"Source requirement reference: {source_requirement_id}",
                        actor,
                    )
        if prerequisite is not None and prerequisite_kind is not None:
            self.execution.add_dependency(
                bid_id,
                ExecutionSubjectKind.WORK_ITEM,
                created.work_item_id,
                prerequisite_kind,
                prerequisite[1],
                None,
                actor,
            )
        message = (
            "Saved - action created and its source was recorded as a progress note."
            if source_as_note
            else "Saved - action created."
        )
        return self._result(
            message,
            ExecutionSubjectKind.WORK_ITEM,
            created.work_item_id,
            self._next(bid_id, ExecutionSubjectKind.WORK_ITEM, created.work_item_id),
            changed=True,
        )

    @staticmethod
    def _conditional_value(changes: WorkItemActionChanges, item: WorkItem, name: str) -> object:
        return getattr(changes, name) if name in changes.model_fields_set else getattr(item, name)

    def _validate_action_transition(
        self, target: WorkItemStatus, changes: WorkItemActionChanges, item: WorkItem
    ) -> dict[str, object]:
        payload: dict[str, object] = {"expected_version": item.version, "status": target}
        if target is WorkItemStatus.BLOCKED:
            blocker = self._conditional_value(changes, item, "blocker_description")
            owner = self._conditional_value(changes, item, "resolution_owner")
            if not blocker or not owner:
                raise ValueError(
                    "Blocked requires a blocker description and resolution owner; provide both "
                    "before saving"
                )
            payload.update(
                blocker_description=blocker,
                blocker_note=blocker,
                resolution_owner=owner,
            )
        elif target is WorkItemStatus.COMPLETED:
            outcome = self._conditional_value(changes, item, "completion_outcome")
            evidence = self._conditional_value(changes, item, "completion_evidence")
            if not outcome or not evidence:
                raise ValueError(
                    "Complete requires an outcome and completion reference; provide both before "
                    "saving"
                )
            payload.update(completion_outcome=outcome, completion_evidence=evidence)
        elif target is WorkItemStatus.CANCELLED:
            reason = self._conditional_value(changes, item, "cancellation_reason")
            if not reason:
                raise ValueError(
                    "Not required needs a cancellation reason; explain why the action is no "
                    "longer required"
                )
            payload["cancellation_reason"] = reason
        return payload

    def update_action(
        self,
        bid_id: str,
        work_item_id: str,
        changes: ActionChangesData,
        actor: str,
    ) -> ActionResult:
        """Merge a partial action edit and use native work-item mutations for native fields."""
        request = WorkItemActionChanges.model_validate(changes)
        item = self.work_items.get_work_item(work_item_id)
        if item.bid_id != bid_id:
            raise ValueError("this action belongs to a different Bid; open it from that Bid")
        fields = request.model_fields_set
        if "priority" in fields and request.priority is None:
            raise ValueError("priority cannot be blank; choose a priority")
        if "due_date" in fields:
            self._validate_due_date(bid_id, request.due_date)
        if "note" in fields and request.note is None:
            raise ValueError("progress note cannot be blank; enter a note or leave it unchanged")

        changed = False
        target = _NATIVE_STATUS[request.status] if request.status is not None else item.status
        relevant_fields = {
            WorkItemStatus.BLOCKED: {"blocker_description", "resolution_owner"},
            WorkItemStatus.COMPLETED: {"completion_outcome", "completion_evidence"},
            WorkItemStatus.CANCELLED: {"cancellation_reason"},
        }.get(target, set())
        conditional: dict[str, object] = {
            "expected_version": item.version,
            "status": target,
        }
        if target is not item.status or fields.intersection(relevant_fields):
            conditional = self._validate_action_transition(target, request, item)
        if target is not item.status:
            item = self.work_items.transition_work_item(work_item_id, conditional, actor)
            changed = True

        edit: dict[str, object] = {"expected_version": item.version}
        for name in ("priority", "due_date"):
            if name in fields and getattr(request, name) != getattr(item, name):
                edit[name] = getattr(request, name)
        relevant = {
            WorkItemStatus.BLOCKED: ("blocker_description", "resolution_owner"),
            WorkItemStatus.COMPLETED: ("completion_outcome", "completion_evidence"),
            WorkItemStatus.CANCELLED: ("cancellation_reason",),
        }.get(target, ())
        for name in relevant:
            if name in fields and getattr(request, name) != getattr(item, name):
                edit[name] = getattr(request, name)
        if len(edit) > 1:
            item = self.work_items.edit_work_item(work_item_id, edit, actor)
            changed = True

        record = self.execution.get_record(bid_id, ExecutionSubjectKind.WORK_ITEM, work_item_id)
        responsible = record.responsible if record else None
        contributors = list(record.contributors) if record else []
        if "responsible" in fields:
            responsible = request.responsible
        if "contributors" in fields:
            contributors = request.contributors or []
        overlay_changed = record is None and bool(responsible or contributors)
        if record is not None:
            overlay_changed = (
                responsible != record.responsible or contributors != record.contributors
            )
        if overlay_changed:
            self.execution.set_record(
                bid_id,
                ExecutionSubjectKind.WORK_ITEM,
                work_item_id,
                BidExecutionUpdate(
                    expected_version=record.version if record else 0,
                    responsible=responsible,
                    contributors=contributors,
                    note=request.note if "note" in fields else None,
                ),
                actor,
            )
            changed = True
        elif "note" in fields and request.note is not None:
            self.execution.add_note(
                bid_id, ExecutionSubjectKind.WORK_ITEM, work_item_id, request.note, actor
            )
            changed = True
        message = "Saved - action updated." if changed else "No changes to save."
        return self._result(
            message,
            ExecutionSubjectKind.WORK_ITEM,
            work_item_id,
            self._next(bid_id, ExecutionSubjectKind.WORK_ITEM, work_item_id),
            changed=changed,
        )


__all__ = [
    "ActionChanges",
    "ActionResult",
    "BidExecutionActionService",
    "ManufacturerInputChanges",
    "ReturnableChanges",
    "WorkItemActionChanges",
]
