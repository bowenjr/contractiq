"""Pure status rules shared by Bid execution projections."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from pydantic import BaseModel, ConfigDict

from core.bid_execution import PrepStatus
from core.deliverables import DeliverableState
from core.vendor_document_control import VerificationStatus
from core.work_items import WorkItemPriority, WorkItemStatus


class EffectivePrepStatus(BaseModel):
    """A display status plus presentation-only context from its native register."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: PrepStatus
    waiting: bool = False
    reason: str | None = None

    @property
    def prep_status(self) -> PrepStatus:
        """Return the status under the storage-oriented name used by overlays."""
        return self.status

    @property
    def waiting_flag(self) -> bool:
        """Return the native waiting presentation flag."""
        return self.waiting


_WORK_ITEM_STATUS: dict[WorkItemStatus, PrepStatus] = {
    WorkItemStatus.OPEN: PrepStatus.NOT_STARTED,
    WorkItemStatus.IN_PROGRESS: PrepStatus.IN_PROGRESS,
    WorkItemStatus.WAITING: PrepStatus.IN_PROGRESS,
    WorkItemStatus.BLOCKED: PrepStatus.BLOCKED,
    WorkItemStatus.COMPLETED: PrepStatus.COMPLETE,
    WorkItemStatus.CANCELLED: PrepStatus.NOT_REQUIRED,
}

_MANUFACTURER_STATUS: dict[VerificationStatus, PrepStatus] = {
    VerificationStatus.CONFIRMED_COMPLIANT: PrepStatus.COMPLETE,
    VerificationStatus.CONFIRMED_WITH_EXCEPTION: PrepStatus.COMPLETE,
    VerificationStatus.NOT_APPLICABLE: PrepStatus.NOT_REQUIRED,
    VerificationStatus.AWAITING_MANUFACTURER: PrepStatus.IN_PROGRESS,
    VerificationStatus.CLARIFICATION_REQUIRED: PrepStatus.BLOCKED,
    VerificationStatus.CANNOT_COMPLY: PrepStatus.BLOCKED,
    VerificationStatus.NOT_REVIEWED: PrepStatus.NOT_STARTED,
}


def map_work_item_status(status: WorkItemStatus | str) -> EffectivePrepStatus:
    """Map a native action status without changing its stored register state."""
    native = WorkItemStatus(status)
    return EffectivePrepStatus(
        status=_WORK_ITEM_STATUS[native],
        waiting=native is WorkItemStatus.WAITING,
    )


def work_item_prep_status(status: WorkItemStatus | str) -> PrepStatus:
    """Return only the display status for callers that do not need waiting context."""
    return map_work_item_status(status).status


def map_manufacturer_status(
    verification_status: VerificationStatus | str,
    *,
    applicable: bool = True,
) -> PrepStatus:
    """Map read-only manufacturer verification to the common display status."""
    if not applicable:
        return PrepStatus.NOT_REQUIRED
    return _MANUFACTURER_STATUS[VerificationStatus(verification_status)]


def manufacturer_prep_status(
    verification_status: VerificationStatus | str,
    *,
    applicable: bool = True,
) -> PrepStatus:
    """Compatibility name for the manufacturer verification mapping."""
    return map_manufacturer_status(verification_status, applicable=applicable)


def effective_returnable_status(
    prep_status: PrepStatus | str | None,
    workflow_state: DeliverableState | str,
    condition_active: bool,
) -> EffectivePrepStatus:
    """Overlay returnable status with authoritative cancellation and condition rules."""
    native_state = DeliverableState(workflow_state)
    if native_state is DeliverableState.CANCELLED:
        return EffectivePrepStatus(status=PrepStatus.NOT_REQUIRED, reason="cancelled")
    if not condition_active:
        return EffectivePrepStatus(
            status=PrepStatus.NOT_REQUIRED,
            reason="condition not active",
        )
    return EffectivePrepStatus(
        status=PrepStatus(prep_status) if prep_status is not None else PrepStatus.NOT_STARTED
    )


def is_overdue(due: date | None, status: PrepStatus | str, as_of: date) -> bool:
    """Return whether an unfinished item is past its effective internal due date."""
    display_status = PrepStatus(status)
    return bool(
        due is not None
        and due < as_of
        and display_status not in {PrepStatus.COMPLETE, PrepStatus.NOT_REQUIRED}
    )


def is_critical(
    priority: WorkItemPriority | str | None,
    mandatory: bool = False,
) -> bool:
    """Apply the shared critical-item rule without inference."""
    if mandatory:
        return True
    return priority is not None and WorkItemPriority(priority) in {
        WorkItemPriority.HIGH,
        WorkItemPriority.CRITICAL,
    }


def waiting_on_prerequisite(statuses: Iterable[PrepStatus | str]) -> bool:
    """Return whether any active prerequisite remains incomplete."""
    terminal = {PrepStatus.COMPLETE, PrepStatus.NOT_REQUIRED}
    return any(PrepStatus(status) not in terminal for status in statuses)
