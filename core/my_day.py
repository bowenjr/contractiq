"""Pure deterministic projection of operational work into the My Day view."""

from datetime import date, timedelta
from enum import Enum

from pydantic import BaseModel, ConfigDict

from core.readiness import ReadinessReport, ReadinessVerdict
from core.work_items import WorkItem, WorkItemPriority, WorkItemStatus


class MyDayBucket(str, Enum):  # noqa: UP042 - presentation contract uses string values
    BLOCKED = "BLOCKED"
    WAITING = "WAITING"
    OVERDUE = "OVERDUE"
    DUE_TODAY = "DUE_TODAY"
    UPCOMING = "UPCOMING"
    LATER_OR_UNSCHEDULED = "LATER_OR_UNSCHEDULED"


class WorkItemSnapshot(BaseModel):
    """A work item with the parent label needed for presentation."""

    model_config = ConfigDict(extra="forbid")

    item: WorkItem
    bid_name: str


class ReadinessSnapshot(BaseModel):
    """A TASK-06 readiness report with its parent presentation label."""

    model_config = ConfigDict(extra="forbid")

    bid_id: str
    bid_name: str
    report: ReadinessReport


class ProjectedWorkItem(BaseModel):
    """One active work item classified once with independent due flags."""

    model_config = ConfigDict(extra="forbid")

    item: WorkItem
    bid_name: str
    bucket: MyDayBucket
    is_overdue: bool
    is_due_today: bool


class MyDayCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overdue: int
    due_today: int
    waiting: int
    blocked: int
    readiness_holds: int


class MyDayProjection(BaseModel):
    """Structured, presentation-ready result of one deterministic projection."""

    model_config = ConfigDict(extra="forbid")

    as_of: date
    horizon_days: int
    blocked: list[ProjectedWorkItem]
    waiting: list[ProjectedWorkItem]
    overdue: list[ProjectedWorkItem]
    due_today: list[ProjectedWorkItem]
    upcoming: list[ProjectedWorkItem]
    later_or_unscheduled: list[ProjectedWorkItem]
    readiness_holds: list[ReadinessSnapshot]
    counts: MyDayCounts


_PRIORITY_RANK: dict[WorkItemPriority, int] = {
    WorkItemPriority.CRITICAL: 0,
    WorkItemPriority.HIGH: 1,
    WorkItemPriority.NORMAL: 2,
    WorkItemPriority.LOW: 3,
}


def _bucket_for(item: WorkItem, as_of: date, horizon_days: int) -> MyDayBucket:
    if item.status == WorkItemStatus.BLOCKED:
        return MyDayBucket.BLOCKED
    if item.status == WorkItemStatus.WAITING:
        return MyDayBucket.WAITING
    if item.due_date is not None and item.due_date < as_of:
        return MyDayBucket.OVERDUE
    if item.due_date == as_of:
        return MyDayBucket.DUE_TODAY
    if item.due_date is not None and as_of + timedelta(
        days=1
    ) <= item.due_date <= as_of + timedelta(days=horizon_days):
        return MyDayBucket.UPCOMING
    return MyDayBucket.LATER_OR_UNSCHEDULED


def _sort_key(projected: ProjectedWorkItem) -> tuple[int, date, str, str]:
    item = projected.item
    return (
        _PRIORITY_RANK[item.priority],
        item.due_date or date.max,
        item.title.casefold(),
        item.work_item_id,
    )


def project_my_day(
    work_items: list[WorkItemSnapshot],
    readiness: list[ReadinessSnapshot],
    as_of: date,
    horizon_days: int,
) -> MyDayProjection:
    """Classify and order supplied snapshots without I/O or hidden time access."""
    if horizon_days < 1:
        raise ValueError("horizon_days must be at least one")

    buckets: dict[MyDayBucket, list[ProjectedWorkItem]] = {bucket: [] for bucket in MyDayBucket}
    active: list[ProjectedWorkItem] = []
    for snapshot in work_items:
        item = snapshot.item
        if item.status in {WorkItemStatus.COMPLETED, WorkItemStatus.CANCELLED}:
            continue
        projected = ProjectedWorkItem(
            item=item,
            bid_name=snapshot.bid_name,
            bucket=_bucket_for(item, as_of, horizon_days),
            is_overdue=item.due_date is not None and item.due_date < as_of,
            is_due_today=item.due_date == as_of,
        )
        buckets[projected.bucket].append(projected)
        active.append(projected)

    for entries in buckets.values():
        entries.sort(key=_sort_key)

    readiness_holds = [
        snapshot for snapshot in readiness if snapshot.report.verdict == ReadinessVerdict.HOLD
    ]
    readiness_holds.sort(key=lambda snapshot: (snapshot.bid_name.casefold(), snapshot.bid_id))

    return MyDayProjection(
        as_of=as_of,
        horizon_days=horizon_days,
        blocked=buckets[MyDayBucket.BLOCKED],
        waiting=buckets[MyDayBucket.WAITING],
        overdue=buckets[MyDayBucket.OVERDUE],
        due_today=buckets[MyDayBucket.DUE_TODAY],
        upcoming=buckets[MyDayBucket.UPCOMING],
        later_or_unscheduled=buckets[MyDayBucket.LATER_OR_UNSCHEDULED],
        readiness_holds=readiness_holds,
        counts=MyDayCounts(
            overdue=sum(item.is_overdue for item in active),
            due_today=sum(item.is_due_today for item in active),
            waiting=len(buckets[MyDayBucket.WAITING]),
            blocked=len(buckets[MyDayBucket.BLOCKED]),
            readiness_holds=len(readiness_holds),
        ),
    )
