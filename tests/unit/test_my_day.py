from datetime import UTC, date, datetime
from uuid import UUID

from core.enums import Actor, Gate
from core.my_day import (
    ReadinessSnapshot,
    WorkItemSnapshot,
    project_my_day,
    work_item_attention_reasons,
    work_item_attention_tier,
    work_item_order_key,
)
from core.readiness import Blocker, ReadinessReport, ReadinessVerdict
from core.schemas import Provenance
from core.work_items import WorkItem, WorkItemPriority, WorkItemStatus

AS_OF = date(2026, 8, 5)
NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)


def _item(
    item_id: int,
    title: str,
    *,
    due_date: date | None = None,
    status: WorkItemStatus = WorkItemStatus.OPEN,
    priority: WorkItemPriority = WorkItemPriority.NORMAL,
    waiting_on: str | None = None,
    blocker_note: str | None = None,
    next_action_date: date | None = None,
    chase_date: date | None = None,
) -> WorkItemSnapshot:
    return WorkItemSnapshot(
        item=WorkItem(
            work_item_id=f"WI-{UUID(int=item_id)}",
            bid_id="B-2026-0001",
            kind="TASK",
            title=title,
            status=status,
            priority=priority,
            due_date=due_date,
            waiting_on=waiting_on,
            blocker_note=blocker_note,
            next_action_date=next_action_date,
            chase_date=chase_date,
            created_at=NOW,
            updated_at=NOW,
            completed_at=NOW if status == WorkItemStatus.COMPLETED else None,
            version=1,
            provenance=Provenance(created_by=Actor.SYSTEM, created_at=NOW),
        ),
        bid_name="North Plant Upgrade",
    )


def _hold() -> ReadinessSnapshot:
    report = ReadinessReport(
        bid_id="B-2026-0002",
        verdict=ReadinessVerdict.HOLD,
        blockers=[
            Blocker(
                condition_id="g4.margin_approved",
                gate=Gate.G4,
                description="Margin approval is required.",
                detail="Approval has not been obtained.",
                material=True,
            )
        ],
        generated_at=NOW,
        summary="Bid is on HOLD: 1 material blocker requires resolution or override.",
        advisory="All currently defined conditions were assessable.",
    )
    return ReadinessSnapshot(
        bid_id=report.bid_id,
        bid_name="Hospital Expansion",
        report=report,
    )


def test_fixed_date_boundaries_and_exclusions() -> None:
    items = [
        _item(1, "Overdue", due_date=date(2026, 8, 4)),
        _item(2, "Today", due_date=date(2026, 8, 5)),
        _item(3, "Tomorrow", due_date=date(2026, 8, 6)),
        _item(4, "Horizon edge", due_date=date(2026, 8, 12)),
        _item(5, "Later", due_date=date(2026, 8, 13)),
        _item(6, "Unscheduled"),
        _item(7, "Done", status=WorkItemStatus.COMPLETED),
        _item(8, "Cancelled", status=WorkItemStatus.CANCELLED),
    ]

    result = project_my_day(items, [], AS_OF, 7)

    assert [entry.item.title for entry in result.overdue] == ["Overdue"]
    assert [entry.item.title for entry in result.due_today] == ["Today"]
    assert [entry.item.title for entry in result.upcoming] == ["Tomorrow", "Horizon edge"]
    assert result.later_or_unscheduled == []
    assert all(
        entry.item.title not in {"Done", "Cancelled"}
        for bucket in (
            result.blocked,
            result.waiting,
            result.overdue,
            result.due_today,
            result.upcoming,
            result.later_or_unscheduled,
        )
        for entry in bucket
    )


def test_blocked_and_waiting_take_precedence_but_keep_due_flags() -> None:
    items = [
        _item(
            1,
            "Blocked late",
            due_date=date(2026, 8, 4),
            status=WorkItemStatus.BLOCKED,
            blocker_note="Vendor quote missing",
        ),
        _item(
            2,
            "Waiting late",
            due_date=date(2026, 8, 4),
            status=WorkItemStatus.WAITING,
            waiting_on="Customer",
        ),
        _item(
            3,
            "Waiting today",
            due_date=AS_OF,
            status=WorkItemStatus.WAITING,
            waiting_on="Legal",
        ),
    ]

    result = project_my_day(items, [], AS_OF, 7)

    assert [entry.item.title for entry in result.blocked] == ["Blocked late"]
    assert [entry.item.title for entry in result.waiting] == [
        "Waiting late",
        "Waiting today",
    ]
    assert result.blocked[0].is_overdue is True
    assert result.waiting[0].is_overdue is True
    assert result.waiting[1].is_due_today is True
    assert result.overdue == []
    assert result.due_today == []
    assert result.counts.overdue == 2
    assert result.counts.due_today == 1


def test_blocked_due_today_retains_both_reasons_and_primary_tier() -> None:
    blocked = _item(
        20,
        "Blocked today",
        due_date=AS_OF,
        status=WorkItemStatus.BLOCKED,
        blocker_note="Approval missing",
    )

    result = project_my_day([blocked], [], AS_OF, 7)

    assert len(result.blocked) == 1
    assert result.blocked[0].reasons == ["BLOCKED", "DUE_TODAY"]
    assert result.counts.blocked == 1
    assert result.counts.due_today == 1
    assert work_item_attention_tier(blocked.item, AS_OF) == 0


def test_ordering_uses_priority_date_title_and_id_tie_breakers() -> None:
    items = [
        _item(9, "Zulu", priority=WorkItemPriority.CRITICAL),
        _item(8, "High", due_date=date(2026, 8, 13), priority=WorkItemPriority.HIGH),
        _item(7, "Bravo", due_date=date(2026, 8, 14)),
        _item(6, "alpha", due_date=date(2026, 8, 14)),
        _item(5, "Alpha", due_date=date(2026, 8, 14)),
        _item(4, "No date"),
        _item(3, "Low", due_date=date(2026, 8, 13), priority=WorkItemPriority.LOW),
    ]

    result = project_my_day(items, [], AS_OF, 7)

    assert result.later_or_unscheduled == []


def test_readiness_holds_are_separate_and_unchanged() -> None:
    hold = _hold()
    clear = hold.model_copy(
        update={
            "bid_id": "B-2026-0003",
            "report": hold.report.model_copy(
                update={"bid_id": "B-2026-0003", "verdict": ReadinessVerdict.CLEAR}
            ),
        }
    )

    result = project_my_day([], [clear, hold], AS_OF, 7)

    assert result.readiness_holds == [hold]
    assert result.readiness_holds[0].report == hold.report
    assert result.counts.readiness_holds == 1


def test_any_future_non_clear_readiness_verdict_is_blocker_attention() -> None:
    escalated = _hold().model_copy(
        update={"report": _hold().report.model_copy(update={"verdict": ReadinessVerdict.ESCALATE})}
    )

    result = project_my_day([], [escalated], AS_OF, 7)

    assert result.readiness_holds == [escalated]
    assert result.counts.readiness_holds == 1


def test_repeated_projection_with_identical_inputs_is_equal() -> None:
    items = [_item(1, "Same", due_date=AS_OF)]
    readiness = [_hold()]

    assert project_my_day(items, readiness, AS_OF, 7) == project_my_day(items, readiness, AS_OF, 7)


def test_multiple_attention_reasons_are_deduplicated_on_one_item() -> None:
    item = _item(
        10,
        "Several reasons",
        due_date=date(2026, 8, 4),
        next_action_date=date(2026, 8, 3),
        chase_date=date(2026, 8, 2),
        status=WorkItemStatus.WAITING,
        waiting_on="Supplier",
    )

    result = project_my_day([item], [], AS_OF, 7)

    assert len(result.waiting) == 1
    assert result.waiting[0].reasons == [
        "DUE_OVERDUE",
        "NEXT_ACTION_OVERDUE",
        "FOLLOW_UP_OVERDUE",
    ]
    assert result.overdue == []


def test_shared_attention_ordering_uses_tier_priority_date_and_stable_id() -> None:
    items = [
        _item(7, "Unscheduled"),
        _item(6, "Later", due_date=date(2026, 8, 20)),
        _item(5, "Upcoming", next_action_date=date(2026, 8, 8)),
        _item(4, "Legacy waiting", status=WorkItemStatus.WAITING, waiting_on="Customer"),
        _item(3, "Today", due_date=AS_OF),
        _item(2, "Overdue", next_action_date=date(2026, 8, 4)),
        _item(
            1,
            "Blocked",
            status=WorkItemStatus.BLOCKED,
            blocker_note="Approval missing",
        ),
    ]

    ordered = sorted(items, key=lambda entry: work_item_order_key(entry.item, AS_OF))

    assert [entry.item.title for entry in ordered] == [
        "Blocked",
        "Overdue",
        "Today",
        "Legacy waiting",
        "Upcoming",
        "Later",
        "Unscheduled",
    ]
    assert work_item_attention_reasons(items[3].item, AS_OF) == ["WAITING_ATTENTION"]

    same_date = [
        _item(11, "Normal", due_date=AS_OF),
        _item(12, "Critical", due_date=AS_OF, priority=WorkItemPriority.CRITICAL),
        _item(10, "Stable first", due_date=AS_OF),
    ]
    tied = sorted(same_date, key=lambda entry: work_item_order_key(entry.item, AS_OF))
    assert [entry.item.work_item_id for entry in tied] == [
        f"WI-{UUID(int=12)}",
        f"WI-{UUID(int=10)}",
        f"WI-{UUID(int=11)}",
    ]
