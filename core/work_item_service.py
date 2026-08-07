"""Application services for validated, audited operational work management."""

import json
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from typing import cast
from uuid import UUID, uuid4

from pydantic import ValidationError

from core.bid_repository import BidRepository
from core.database import Database
from core.enums import Actor
from core.my_day import (
    MyDayProjection,
    ReadinessSnapshot,
    RequirementAttentionSnapshot,
    WorkItemSnapshot,
    project_my_day,
)
from core.readiness import ReadinessReport
from core.readiness_service import evaluate_readiness
from core.requirement_repository import RequirementRepository
from core.schemas import AuditEntry, Provenance
from core.supplier_assurance_rules import calculate_gaps
from core.work_item_repository import (
    StaleWorkItemError,
    WorkItemNotFoundError,
    WorkItemRepository,
)
from core.work_items import (
    WorkItem,
    WorkItemCreate,
    WorkItemEdit,
    WorkItemStatus,
    WorkItemTransition,
)

WorkItemCreateData = WorkItemCreate | Mapping[str, object]
WorkItemEditData = WorkItemEdit | Mapping[str, object]
WorkItemTransitionData = WorkItemTransition | Mapping[str, object]


class WorkItemService:
    """Validated mutation boundary for operational work items."""

    def __init__(
        self,
        repository: WorkItemRepository,
        bid_repository: BidRepository,
        *,
        now_factory: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self.repository = repository
        self.bid_repository = bid_repository
        self._now_factory = now_factory or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or uuid4

    def _now(self) -> datetime:
        now = self._now_factory()
        if now.tzinfo is None:
            raise ValueError("now_factory must return a timezone-aware datetime")
        return now.astimezone(UTC)

    @staticmethod
    def _actor(actor: str) -> str:
        normalized = actor.strip()
        if not normalized:
            raise ValueError("actor must be non-empty")
        return normalized

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}-{self._id_factory()}"

    def _audit(
        self,
        *,
        item: WorkItem,
        actor: str,
        action: str,
        at: datetime,
        before: WorkItem | None,
    ) -> AuditEntry:
        detail = json.dumps(
            {
                "work_item_id": item.work_item_id,
                "operation": action,
                "before": before.model_dump(mode="json") if before is not None else None,
                "after": item.model_dump(mode="json"),
            },
            sort_keys=True,
        )
        return AuditEntry(
            entry_id=self._new_id("AUD"),
            bid_id=item.bid_id,
            actor=actor,
            action=action,
            detail=detail,
            timestamp=at,
        )

    def create_work_item(self, data: WorkItemCreateData, actor: str) -> WorkItem:
        """Validate first, then create a parent-owned item with atomic audit."""
        request = WorkItemCreate.model_validate(data)
        normalized_actor = self._actor(actor)
        if self.bid_repository.get_bid(request.bid_id) is None:
            raise ValueError(f"Bid not found: {request.bid_id}")

        now = self._now()
        item = WorkItem(
            work_item_id=self._new_id("WI"),
            bid_id=request.bid_id,
            kind=request.kind,
            title=request.title,
            details=request.details,
            status=request.status,
            priority=request.priority,
            due_date=request.due_date,
            waiting_on=(request.waiting_on if request.status == WorkItemStatus.WAITING else None),
            blocker_note=(
                request.blocker_note if request.status == WorkItemStatus.BLOCKED else None
            ),
            created_at=now,
            updated_at=now,
            completed_at=now if request.status == WorkItemStatus.COMPLETED else None,
            version=1,
            provenance=Provenance(
                created_by=Actor.HUMAN,
                agent_name=normalized_actor,
                created_at=now,
                human_confirmed=True,
                confirmed_by=normalized_actor,
                confirmed_at=now,
            ),
        )
        self.repository.create(
            item,
            self._audit(
                item=item,
                actor=normalized_actor,
                action="work_item_created",
                at=now,
                before=None,
            ),
        )
        return item

    def get_work_item(self, work_item_id: str) -> WorkItem:
        """Return one item or a stable not-found error."""
        item = self.repository.get(work_item_id)
        if item is None:
            raise WorkItemNotFoundError(f"Work item not found: {work_item_id}")
        return item

    @staticmethod
    def _require_version(item: WorkItem, expected_version: int) -> None:
        if item.version != expected_version:
            raise StaleWorkItemError(
                f"Stale work item version: expected {expected_version}, current {item.version}"
            )

    def edit_work_item(
        self,
        work_item_id: str,
        data: WorkItemEditData,
        actor: str,
    ) -> WorkItem:
        """Edit only descriptive fields using optimistic concurrency."""
        request = WorkItemEdit.model_validate(data)
        normalized_actor = self._actor(actor)
        current = self.get_work_item(work_item_id)
        self._require_version(current, request.expected_version)

        updates = request.model_dump(exclude_unset=True)
        updates.pop("expected_version", None)
        updates.update(
            {
                "updated_at": self._now(),
                "version": current.version + 1,
            }
        )
        candidate_data = current.model_dump()
        candidate_data.update(updates)
        updated = WorkItem.model_validate(candidate_data)
        audit = self._audit(
            item=updated,
            actor=normalized_actor,
            action="work_item_updated",
            at=updated.updated_at,
            before=current,
        )
        self.repository.update(updated, request.expected_version, audit)
        return updated

    def transition_work_item(
        self,
        work_item_id: str,
        data: WorkItemTransitionData,
        actor: str,
    ) -> WorkItem:
        """Apply an explicit status transition and clear conditional fields."""
        request = WorkItemTransition.model_validate(data)
        normalized_actor = self._actor(actor)
        current = self.get_work_item(work_item_id)
        self._require_version(current, request.expected_version)
        now = self._now()
        updated = WorkItem.model_validate(
            {
                **current.model_dump(),
                "status": request.status,
                "waiting_on": (
                    request.waiting_on if request.status == WorkItemStatus.WAITING else None
                ),
                "blocker_note": (
                    request.blocker_note if request.status == WorkItemStatus.BLOCKED else None
                ),
                "completed_at": (now if request.status == WorkItemStatus.COMPLETED else None),
                "updated_at": now,
                "version": current.version + 1,
            }
        )
        audit = self._audit(
            item=updated,
            actor=normalized_actor,
            action="work_item_status_transitioned",
            at=now,
            before=current,
        )
        self.repository.transition(updated, request.expected_version, audit)
        return updated


class MyDayService:
    """Query service joining active work with TASK-06 readiness reports."""

    def __init__(
        self,
        work_repository: WorkItemRepository,
        bid_repository: BidRepository,
        db: Database,
        *,
        requirement_repository: RequirementRepository | None = None,
        readiness_loader: Callable[[str], ReadinessReport] | None = None,
    ) -> None:
        self.work_repository = work_repository
        self.bid_repository = bid_repository
        self.db = db
        self.requirement_repository = requirement_repository
        self._readiness_loader = readiness_loader or self._evaluate_readiness

    def _evaluate_readiness(self, bid_id: str) -> ReadinessReport:
        return evaluate_readiness(self.bid_repository, self.db, bid_id)

    def get_my_day(
        self,
        *,
        as_of: date,
        horizon_days: int = 7,
    ) -> MyDayProjection:
        """Load current state and pass explicit dates to the pure projector."""
        bids = self.bid_repository.list_bids()
        bid_names = {bid.bid_id: bid.project_name for bid in bids}
        work_snapshots = [
            WorkItemSnapshot(
                item=item,
                bid_name=bid_names.get(item.bid_id, item.bid_id),
            )
            for item in self.work_repository.list(active_only=True)
        ]
        readiness_snapshots = [
            ReadinessSnapshot(
                bid_id=bid.bid_id,
                bid_name=bid.project_name,
                report=self._readiness_loader(bid.bid_id),
            )
            for bid in bids
        ]
        requirement_snapshots = (
            [
                RequirementAttentionSnapshot(
                    requirement=requirement,
                    bid_name=bid_names.get(requirement.bid_id, requirement.bid_id),
                )
                for requirement in self.requirement_repository.list()
            ]
            if self.requirement_repository is not None
            else []
        )
        supplier_attention: list[dict[str, str]] = []
        with self.db._conn() as conn:
            supplier_tables_ready = (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='supplier_requests'"
                ).fetchone()
                is not None
            )
            for bid in bids if supplier_tables_ready else []:
                requests = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM supplier_requests WHERE bid_id=?", (bid.bid_id,)
                    ).fetchall()
                ]
                items = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM supplier_request_items WHERE bid_id=?", (bid.bid_id,)
                    ).fetchall()
                ]
                responses = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM supplier_response_versions WHERE bid_id=?", (bid.bid_id,)
                    ).fetchall()
                ]
                coverage = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT c.* FROM supplier_response_coverage c "
                        "JOIN supplier_response_versions v USING(response_version_id) "
                        "WHERE v.bid_id=?",
                        (bid.bid_id,),
                    ).fetchall()
                ]
                supplier_attention.extend(
                    {
                        "bid_id": bid.bid_id,
                        "entity_id": gap.entity_id,
                        "code": gap.code,
                        "severity": gap.severity,
                    }
                    for gap in calculate_gaps(
                        requests, items, responses, coverage, as_of_date=as_of
                    )
                )
        return project_my_day(
            work_snapshots,
            readiness_snapshots,
            as_of,
            horizon_days,
            requirement_snapshots,
            supplier_attention=supplier_attention,
        )


def validation_error_message(exc: ValidationError) -> str:
    """Return concise field-aware validation text for UI and scripts."""
    errors = cast(list[dict[str, object]], exc.errors(include_url=False))
    parts: list[str] = []
    for error in errors:
        location = ".".join(str(part) for part in cast(tuple[object, ...], error["loc"]))
        message = str(error["msg"])
        parts.append(f"{location}: {message}" if location else message)
    return "; ".join(parts)
