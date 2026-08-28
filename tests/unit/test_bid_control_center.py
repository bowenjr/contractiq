from datetime import UTC, date, datetime

from core.bid_control_center import (
    BidDeadlineAttention,
    BidPortfolioFilters,
    BidPortfolioView,
    BidReadinessFilter,
    classification_controls,
    project_bid_portfolio,
    project_bid_workspace,
)
from core.enums import BidLevel, BidStatus, Gate
from core.readiness import Blocker, ReadinessReport, ReadinessVerdict
from core.schemas import Bid


def _readiness(bid_id: str, verdict: ReadinessVerdict = ReadinessVerdict.CLEAR) -> ReadinessReport:
    blockers = (
        [
            Blocker(
                condition_id="g2.requirements_complete",
                gate=Gate.G2,
                description="Customer requirements remain unresolved",
                detail="At least one material requirement is incomplete.",
                material=True,
            )
        ]
        if verdict is not ReadinessVerdict.CLEAR
        else []
    )
    return ReadinessReport(
        bid_id=bid_id,
        verdict=verdict,
        blockers=blockers,
        generated_at=datetime(2026, 8, 27, 12, tzinfo=UTC),
        summary=f"{verdict.value} readiness",
        advisory="Deterministic test projection.",
    )


def _bid(valid_bid: Bid, suffix: str, **updates: object) -> Bid:
    return valid_bid.model_copy(
        update={
            "bid_id": f"B-2026-{suffix}",
            "project_name": f"Project {suffix}",
            **updates,
        }
    )


def test_classification_controls_scale_without_new_policy(valid_bid: Bid) -> None:
    level_one = valid_bid.model_copy(update={"classification": BidLevel.LEVEL_1})
    level_three = valid_bid.model_copy(update={"classification": BidLevel.LEVEL_3})

    one = {control.gate: control for control in classification_controls(level_one)}
    three = {control.gate: control for control in classification_controls(level_three)}

    assert one[Gate.G1].required is True
    assert one[Gate.G4].required is False
    assert three[Gate.G1].required is True
    assert three[Gate.G4].required is True


def test_portfolio_uses_deterministic_role_order_and_stable_tie_break(valid_bid: Bid) -> None:
    held = _bid(valid_bid, "0004", status=BidStatus.HELD)
    overdue_b = _bid(
        valid_bid,
        "0002",
        internal_due_date=date(2026, 8, 1),
        customer_due_date=date(2026, 9, 1),
    )
    overdue_a = _bid(
        valid_bid,
        "0001",
        internal_due_date=date(2026, 8, 1),
        customer_due_date=date(2026, 9, 1),
    )
    upcoming = _bid(
        valid_bid,
        "0003",
        internal_due_date=date(2026, 8, 10),
        customer_due_date=date(2026, 8, 12),
    )
    bids = [upcoming, overdue_b, held, overdue_a]

    projection = project_bid_portfolio(
        bids,
        {bid.bid_id: _readiness(bid.bid_id) for bid in bids},
        [],
        [],
        BidPortfolioFilters(),
        date(2026, 8, 5),
    )

    assert [row.bid.bid_id for row in projection.rows] == [
        held.bid_id,
        overdue_a.bid_id,
        overdue_b.bid_id,
        upcoming.bid_id,
    ]
    assert [row.sort_tier for row in projection.rows] == [0, 1, 1, 2]


def test_portfolio_filters_intersect_and_history_is_explicit(valid_bid: Bid) -> None:
    current = valid_bid.model_copy(update={"classification": BidLevel.LEVEL_3})
    held = _bid(
        valid_bid,
        "0002",
        classification=BidLevel.LEVEL_3,
        status=BidStatus.HELD,
    )
    won = _bid(valid_bid, "0003", status=BidStatus.WON)
    bids = [current, held, won]
    reports = {
        current.bid_id: _readiness(current.bid_id),
        held.bid_id: _readiness(held.bid_id, ReadinessVerdict.HOLD),
        won.bid_id: _readiness(won.bid_id),
    }

    filtered = project_bid_portfolio(
        bids,
        reports,
        [],
        [],
        BidPortfolioFilters(
            view=BidPortfolioView.CURRENT,
            classification=BidLevel.LEVEL_3,
            readiness=BidReadinessFilter.HOLD,
            owner="coordinator",
            deadline=BidDeadlineAttention.OVERDUE,
        ),
        date(2026, 8, 5),
    )
    history = project_bid_portfolio(
        bids,
        reports,
        [],
        [],
        BidPortfolioFilters(view=BidPortfolioView.HISTORY),
        date(2026, 8, 5),
    )

    assert [row.bid.bid_id for row in filtered.rows] == [held.bid_id]
    assert [row.bid.bid_id for row in history.rows] == [won.bid_id]


def test_workspace_blocker_drives_explained_next_action(valid_bid: Bid) -> None:
    report = _readiness(valid_bid.bid_id, ReadinessVerdict.HOLD)

    workspace = project_bid_workspace(valid_bid, report, [], [], date(2026, 8, 5))

    assert workspace.next_action == "Resolve: Customer requirements remain unresolved"
    assert workspace.next_action_destination.endswith("/requirements-scope")
    assert workspace.blockers[0].owner == valid_bid.bc_owner
    assert workspace.blockers[0].owing_party == "Bid team"
    assert workspace.blockers[0].consequence
    assert workspace.blockers[0].evidence_source


def test_supplier_silence_is_counted_as_waiting_not_compliance(valid_bid: Bid) -> None:
    attention = [
        {
            "bid_id": valid_bid.bid_id,
            "entity_id": "REQ-01",
            "code": "SUPPLIER_RESPONSE_ITEM_SILENT",
            "severity": "BLOCKING_ATTENTION",
        }
    ]
    report = _readiness(valid_bid.bid_id)

    portfolio = project_bid_portfolio(
        [valid_bid],
        {valid_bid.bid_id: report},
        [],
        [],
        BidPortfolioFilters(),
        date(2026, 8, 5),
        supplier_attention=attention,
    )
    workspace = project_bid_workspace(
        valid_bid,
        report,
        [],
        [],
        date(2026, 8, 5),
        supplier_attention=attention,
    )

    assert portfolio.rows[0].waiting_count == 1
    assert workspace.waiting_control_attention == attention
    assert workspace.next_action.startswith("Follow up")
