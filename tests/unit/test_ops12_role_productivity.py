"""Focused OPS-12 tests for the shared Bid-summary projection.

Mirrors the fixture conventions in tests/unit/test_bid_control_center.py and
tests/unit/test_my_day.py — a deterministic ``valid_bid`` fixture, synthetic
``ReadinessReport``/``WorkItem`` builders, and a fixed ``AS_OF``.
"""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from core.bid_control_center import (
    BidPortfolioFilters,
    BidPortfolioView,
    MyDayBidBucket,
    last_activity_by_bid,
    project_bid_portfolio,
)
from core.enums import Actor, BidStatus, Gate
from core.readiness import Blocker, ReadinessReport, ReadinessVerdict
from core.schemas import AuditEntry, Bid, Provenance
from core.work_items import WorkItem, WorkItemPriority, WorkItemStatus

AS_OF = date(2026, 9, 25)
NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)


def _bid(valid_bid: Bid, suffix: str, **updates: object) -> Bid:
    return valid_bid.model_copy(
        update={
            "bid_id": f"B-2026-{suffix}",
            "project_name": f"Project {suffix}",
            "internal_due_date": date(2026, 10, 1),
            "customer_due_date": date(2026, 10, 5),
            **updates,
        }
    )


def _readiness(bid_id: str, *, hold: bool = False, extra_blockers: int = 0) -> ReadinessReport:
    blockers = (
        [
            Blocker(
                condition_id=f"g2.blocker_{i}",
                gate=Gate.G2,
                description=f"Blocker {i} is unresolved",
                detail="Deterministic test blocker.",
                material=True,
            )
            for i in range(1 + extra_blockers)
        ]
        if hold
        else []
    )
    return ReadinessReport(
        bid_id=bid_id,
        verdict=ReadinessVerdict.HOLD if hold else ReadinessVerdict.CLEAR,
        blockers=blockers,
        generated_at=NOW,
        summary="hold" if hold else "clear",
        advisory="Deterministic test projection.",
    )


def _work_item(
    item_id: int,
    bid_id: str,
    *,
    status: WorkItemStatus = WorkItemStatus.OPEN,
    due_date: date | None = None,
) -> WorkItem:
    return WorkItem(
        work_item_id=f"WI-{UUID(int=item_id)}",
        bid_id=bid_id,
        kind="TASK",
        title=f"Work item {item_id}",
        status=status,
        priority=WorkItemPriority.NORMAL,
        due_date=due_date,
        waiting_on="Manufacturer" if status is WorkItemStatus.WAITING else None,
        created_at=NOW,
        updated_at=NOW,
        completed_at=NOW if status == WorkItemStatus.COMPLETED else None,
        version=1,
        provenance=Provenance(created_by=Actor.SYSTEM, created_at=NOW),
    )


def _portfolio(bids, readiness_by_bid, work_items=(), **kwargs):
    return project_bid_portfolio(
        bids,
        readiness_by_bid,
        list(work_items),
        [],
        BidPortfolioFilters(view=BidPortfolioView.ALL),
        AS_OF,
        **kwargs,
    )


def test_bid_requiring_action_beats_waiting_when_both_present(valid_bid: Bid) -> None:
    """A Bid with a material blocker AND a waiting work item is REQUIRES_ACTION, not WAITING."""
    bid = _bid(valid_bid, "0001")
    waiting_item = _work_item(1, bid.bid_id, status=WorkItemStatus.WAITING)
    projection = _portfolio([bid], {bid.bid_id: _readiness(bid.bid_id, hold=True)}, [waiting_item])
    row = projection.rows[0]
    assert row.my_day_bucket is MyDayBidBucket.REQUIRES_ACTION


def test_bid_is_waiting_only_when_no_higher_priority_action_exists(valid_bid: Bid) -> None:
    """A waiting record alone (no blocker, no overdue deadline, no decision) is WAITING."""
    bid = _bid(valid_bid, "0002")
    waiting_item = _work_item(2, bid.bid_id, status=WorkItemStatus.WAITING)
    projection = _portfolio([bid], {bid.bid_id: _readiness(bid.bid_id, hold=False)}, [waiting_item])
    row = projection.rows[0]
    assert row.my_day_bucket is MyDayBidBucket.WAITING


def test_bid_with_no_signals_is_on_track_or_upcoming(valid_bid: Bid) -> None:
    bid = _bid(valid_bid, "0003")
    projection = _portfolio([bid], {bid.bid_id: _readiness(bid.bid_id, hold=False)})
    row = projection.rows[0]
    assert row.my_day_bucket is MyDayBidBucket.ON_TRACK_OR_UPCOMING


def test_history_status_bid_is_excluded_from_live_buckets(valid_bid: Bid) -> None:
    bid = _bid(valid_bid, "0004", status=BidStatus.WON)
    projection = _portfolio([bid], {bid.bid_id: _readiness(bid.bid_id, hold=False)})
    row = projection.rows[0]
    assert row.my_day_bucket is MyDayBidBucket.HISTORY


def test_overdue_work_item_forces_requires_action_even_without_bid_deadline_overdue(
    valid_bid: Bid,
) -> None:
    """A Bid with overdue *work* but no overdue Bid *deadline* must still require action."""
    bid = _bid(
        valid_bid,
        "0005",
        internal_due_date=date(2026, 12, 1),
        customer_due_date=date(2026, 12, 5),
    )
    overdue_item = _work_item(5, bid.bid_id, due_date=date(2026, 9, 1))
    projection = _portfolio([bid], {bid.bid_id: _readiness(bid.bid_id, hold=False)}, [overdue_item])
    row = projection.rows[0]
    assert row.overdue_work_count == 1
    assert row.my_day_bucket is MyDayBidBucket.REQUIRES_ACTION


def test_each_active_bid_appears_in_exactly_one_live_bucket(valid_bid: Bid) -> None:
    hold_bid = _bid(valid_bid, "0010")
    waiting_bid = _bid(valid_bid, "0011")
    clear_bid = _bid(valid_bid, "0012")
    won_bid = _bid(valid_bid, "0013", status=BidStatus.WON)
    bids = [hold_bid, waiting_bid, clear_bid, won_bid]
    readiness = {
        hold_bid.bid_id: _readiness(hold_bid.bid_id, hold=True),
        waiting_bid.bid_id: _readiness(waiting_bid.bid_id, hold=False),
        clear_bid.bid_id: _readiness(clear_bid.bid_id, hold=False),
        won_bid.bid_id: _readiness(won_bid.bid_id, hold=False),
    }
    work_items = [_work_item(20, waiting_bid.bid_id, status=WorkItemStatus.WAITING)]
    projection = _portfolio(bids, readiness, work_items)

    buckets: dict[str, list[str]] = {}
    for row in projection.rows:
        buckets.setdefault(row.my_day_bucket.value, []).append(row.bid.bid_id)

    all_bid_ids = [row.bid.bid_id for row in projection.rows]
    assert sorted(all_bid_ids) == sorted(bid.bid_id for bid in bids)
    assert len(all_bid_ids) == len(set(all_bid_ids)), "each Bid must appear exactly once"
    assert buckets["requires_action"] == [hold_bid.bid_id]
    assert buckets["waiting"] == [waiting_bid.bid_id]
    assert buckets["on_track_or_upcoming"] == [clear_bid.bid_id]
    assert buckets["history"] == [won_bid.bid_id]


def test_additional_blocker_count_excludes_overridden_and_counts_the_rest(valid_bid: Bid) -> None:
    bid = _bid(valid_bid, "0020")
    readiness = _readiness(bid.bid_id, hold=True, extra_blockers=2)
    projection = _portfolio([bid], {bid.bid_id: readiness})
    row = projection.rows[0]
    assert row.highest_blocker is not None
    assert row.additional_blocker_count == 2


def test_supplier_response_position_labels(valid_bid: Bid) -> None:
    silent_bid = _bid(valid_bid, "0030")
    other_bid = _bid(valid_bid, "0031")
    clear_bid = _bid(valid_bid, "0032")
    bids = [silent_bid, other_bid, clear_bid]
    readiness = {bid.bid_id: _readiness(bid.bid_id) for bid in bids}
    supplier_attention = [
        {
            "bid_id": silent_bid.bid_id,
            "entity_id": "SR-1",
            "code": "NO_RESPONSE",
            "severity": "HIGH",
        },
        {
            "bid_id": other_bid.bid_id,
            "entity_id": "SR-2",
            "code": "PARTIAL_COVERAGE",
            "severity": "MEDIUM",
        },
    ]
    projection = _portfolio(bids, readiness, supplier_attention=supplier_attention)
    positions = {row.bid.bid_id: row.supplier_response_position for row in projection.rows}
    assert positions[silent_bid.bid_id] == "Awaiting supplier response"
    assert positions[other_bid.bid_id] == "Supplier attention open"
    assert positions[clear_bid.bid_id] == "Clear"


def test_evidence_links_only_show_signals_actually_present(valid_bid: Bid) -> None:
    quiet_bid = _bid(valid_bid, "0040")
    waiting_bid = _bid(valid_bid, "0041")
    projection = _portfolio(
        [quiet_bid, waiting_bid],
        {
            quiet_bid.bid_id: _readiness(quiet_bid.bid_id),
            waiting_bid.bid_id: _readiness(waiting_bid.bid_id),
        },
        [_work_item(41, waiting_bid.bid_id, status=WorkItemStatus.WAITING)],
    )
    rows = {row.bid.bid_id: row for row in projection.rows}
    quiet_labels = {link.label for link in rows[quiet_bid.bid_id].evidence_links}
    waiting_labels = {link.label for link in rows[waiting_bid.bid_id].evidence_links}
    assert quiet_labels == {"Bid overview"}
    assert waiting_labels == {"Bid overview", "Waiting work items"}


def test_new_lifecycle_views_filter_deterministically(valid_bid: Bid) -> None:
    upcoming_bid = _bid(
        valid_bid,
        "0050",
        status=BidStatus.ACTIVE,
        internal_due_date=date(2026, 10, 1),
        customer_due_date=date(2026, 10, 5),
    )
    issued_bid = _bid(valid_bid, "0051", status=BidStatus.SUBMITTED)
    dormant_bid = _bid(
        valid_bid,
        "0052",
        status=BidStatus.ACTIVE,
        internal_due_date=date(2027, 1, 1),
        customer_due_date=date(2027, 1, 15),
    )
    bids = [upcoming_bid, issued_bid, dormant_bid]
    readiness = {bid.bid_id: _readiness(bid.bid_id) for bid in bids}
    stale_activity = {dormant_bid.bid_id: datetime(2026, 8, 1, tzinfo=UTC)}

    upcoming_projection = project_bid_portfolio(
        bids,
        readiness,
        [],
        [],
        BidPortfolioFilters(view=BidPortfolioView.UPCOMING),
        AS_OF,
    )
    assert [row.bid.bid_id for row in upcoming_projection.rows] == [upcoming_bid.bid_id]

    issued_projection = project_bid_portfolio(
        bids,
        readiness,
        [],
        [],
        BidPortfolioFilters(view=BidPortfolioView.ISSUED),
        AS_OF,
    )
    assert [row.bid.bid_id for row in issued_projection.rows] == [issued_bid.bid_id]

    dormant_projection = project_bid_portfolio(
        bids,
        readiness,
        [],
        [],
        BidPortfolioFilters(view=BidPortfolioView.DORMANT),
        AS_OF,
        last_activity=stale_activity,
    )
    assert [row.bid.bid_id for row in dormant_projection.rows] == [dormant_bid.bid_id]


def test_dormant_view_boundary_is_exactly_fourteen_days(valid_bid: Bid) -> None:
    bid = _bid(valid_bid, "0060")
    readiness = {bid.bid_id: _readiness(bid.bid_id)}
    reference = datetime.combine(AS_OF, datetime.min.time(), UTC)
    just_under = {bid.bid_id: reference - timedelta(days=13)}
    exactly_at = {bid.bid_id: reference - timedelta(days=14)}

    under_projection = project_bid_portfolio(
        [bid],
        readiness,
        [],
        [],
        BidPortfolioFilters(view=BidPortfolioView.DORMANT),
        AS_OF,
        last_activity=just_under,
    )
    at_projection = project_bid_portfolio(
        [bid],
        readiness,
        [],
        [],
        BidPortfolioFilters(view=BidPortfolioView.DORMANT),
        AS_OF,
        last_activity=exactly_at,
    )
    assert under_projection.rows == []
    assert [row.bid.bid_id for row in at_projection.rows] == [bid.bid_id]


def test_view_status_validator_rejects_mismatched_issued_and_dormant_status() -> None:
    import pytest

    with pytest.raises(ValueError, match="Issued view"):
        BidPortfolioFilters(view=BidPortfolioView.ISSUED, status=BidStatus.WON)
    with pytest.raises(ValueError, match="view can only be combined"):
        BidPortfolioFilters(view=BidPortfolioView.DORMANT, status=BidStatus.SUBMITTED)
    # Valid combinations still pass.
    BidPortfolioFilters(view=BidPortfolioView.ISSUED, status=BidStatus.SUBMITTED)
    BidPortfolioFilters(view=BidPortfolioView.UPCOMING, status=BidStatus.ACTIVE)


def test_last_activity_by_bid_prefers_latest_audit_row_falls_back_to_updated_at(
    valid_bid: Bid,
) -> None:
    audited_bid = _bid(valid_bid, "0070")
    unaudited_bid = _bid(valid_bid, "0071", updated_at=datetime(2026, 1, 1, tzinfo=UTC))
    entries = [
        AuditEntry(
            entry_id="AUD-1",
            bid_id=audited_bid.bid_id,
            actor="jason",
            action="note",
            detail="{}",
            timestamp=datetime(2026, 9, 1, tzinfo=UTC),
        ),
        AuditEntry(
            entry_id="AUD-2",
            bid_id=audited_bid.bid_id,
            actor="jason",
            action="note",
            detail="{}",
            timestamp=datetime(2026, 9, 20, tzinfo=UTC),
        ),
    ]
    result = last_activity_by_bid(entries, [audited_bid, unaudited_bid])
    assert result[audited_bid.bid_id] == datetime(2026, 9, 20, tzinfo=UTC)
    assert result[unaudited_bid.bid_id] == datetime(2026, 1, 1, tzinfo=UTC)
