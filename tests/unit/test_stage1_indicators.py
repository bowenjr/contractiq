"""Stage 1 / Fix 2: My Day, the Bids list and the Bid workspace report the same blockers and the
same overdue returnables, and never say "No active blocker" while a blocker exists."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, date, datetime

from core.bid_control_center import BidPortfolioFilters, project_bid_portfolio
from core.bid_workflow import (
    OutstandingItem,
    PackageIntakeFacts,
    project_bid_workflow,
)
from core.ops07w import build_navigator
from core.readiness import ReadinessReport, ReadinessVerdict
from core.schemas import Bid
from tests.unit.test_next_action_order import BID_ID, COUNTS
from tests.unit.test_package_intake_separation import (
    BID_ID as APP_BID_ID,
)
from tests.unit.test_package_intake_separation import (
    _page,
    _register_release,
    intake_app,  # noqa: F401 (fixture)
)

AS_OF = date(2026, 10, 1)


def _item(
    heading: str,
    *,
    count: int = 1,
    code: str | None = None,
    blocking: bool = True,
    scope: str = "whole_bid",
) -> OutstandingItem:
    return OutstandingItem(
        heading=heading,
        detail="detail",
        consequence="consequence",
        owner="Jason",
        owing_party="Company",
        due_date=None,
        evidence_source="register",
        resolve_label="Resolve",
        resolve_path=f"/bids/{BID_ID}/x",
        blocking=blocking,
        code=code,
        scope=scope,
        count=count,
    )


def _workflow(*items: OutstandingItem):
    return project_bid_workflow(
        BID_ID,
        bid_status="active",
        navigator=build_navigator(BID_ID, COUNTS, "hold", "Jason"),
        intake=PackageIntakeFacts(
            releases_received=1,
            releases_incorporated=1,
            files_requiring_review=0,
            expected_missing=0,
            latest_release_id="REL-1",
            included_files=1,
        ),
        readiness_verdict="hold",
        outstanding=items,
    )


def _clear_gate_report(bid_id: str) -> ReadinessReport:
    """The legacy gate check has nothing to say — the case that used to hide real blockers."""
    return ReadinessReport(
        bid_id=bid_id,
        verdict=ReadinessVerdict.CLEAR,
        blockers=[],
        generated_at=datetime(2026, 10, 1, tzinfo=UTC),
        summary="Clear",
        advisory="Deterministic test projection.",
    )


def _row(bid: Bid, workflow, **attention: list[dict[str, str]]):
    portfolio = project_bid_portfolio(
        [bid],
        {bid.bid_id: _clear_gate_report(bid.bid_id)},
        [],
        [],
        BidPortfolioFilters(),
        AS_OF,
        workflow_by_bid={bid.bid_id: workflow},
        **attention,
    )
    return portfolio.rows[0]


def test_workspace_blockers_reach_my_day_even_when_the_gate_check_is_clear(valid_bid: Bid) -> None:
    workflow = _workflow(
        _item("Manufacturer response outstanding", count=4, code="MANUFACTURER_RESPONSE_BLOCKER"),
        _item("Commercial position unresolved", code="UNRESOLVED_COMMERCIAL_POSITION"),
    )
    row = _row(valid_bid, workflow)
    assert row.highest_blocker is not None
    assert row.highest_blocker.heading == "Manufacturer response outstanding"
    assert row.blocking_count == 5  # records, not headings — the workspace counts the same way
    assert row.blocking_count == workflow.blocking_count
    assert row.additional_blocker_count == 1


def test_items_due_only_before_issue_are_not_development_blockers(valid_bid: Bid) -> None:
    workflow = _workflow(
        _item("Approval pending", scope="pending_before_issue", code="X"),
    )
    row = _row(valid_bid, workflow)
    assert row.highest_blocker is None and row.blocking_count == 0


def test_a_bid_with_no_blockers_still_reads_clear(valid_bid: Bid) -> None:
    row = _row(valid_bid, _workflow())
    assert row.highest_blocker is None and row.blocking_count == 0


def test_non_blocking_items_do_not_count(valid_bid: Bid) -> None:
    row = _row(valid_bid, _workflow(_item("Note only", blocking=False, code="N")))
    assert row.blocking_count == 0 and row.highest_blocker is None


def test_overdue_returnables_are_part_of_the_overdue_figure(valid_bid: Bid) -> None:
    overdue = {"bid_id": valid_bid.bid_id, "code": "DELIVERABLE_OVERDUE_NO_SUBMISSION"}
    other_bid = {"bid_id": "B-OTHER", "code": "DELIVERABLE_OVERDUE_NO_SUBMISSION"}
    other_code = {"bid_id": valid_bid.bid_id, "code": "DELIVERABLE_AWAITING_REVIEW"}
    row = _row(
        valid_bid, _workflow(), deliverable_attention=[overdue, overdue, other_bid, other_code]
    )
    assert row.overdue_returnable_count == 2
    assert row.overdue_work_count == 2
    assert _row(valid_bid, _workflow()).overdue_work_count == 0


def test_open_manufacturer_rows_are_not_reported_as_supplier_clear(valid_bid: Bid) -> None:
    open_rows = _workflow(
        _item("Manufacturer response outstanding", code="MANUFACTURER_RESPONSE_BLOCKER")
    )
    assert _row(valid_bid, open_rows).supplier_response_position == "Manufacturer response open"
    assert _row(valid_bid, _workflow()).supplier_response_position == "Clear"


# ------------------------------------------------------------------- rendered pages agree


def _int(pattern: str, text: str) -> int:
    match = re.search(pattern, text)
    assert match, f"{pattern!r} not found"
    return int(match.group(1))


def test_the_three_pages_report_the_same_blocking_count(intake_app) -> None:  # noqa: F811
    async def scenario() -> None:
        await _register_release(intake_app)
        workspace = await _page(intake_app, f"/bids/{APP_BID_ID}")
        bids = await _page(intake_app, "/bids")
        my_day = await _page(intake_app, "/my-day")

        in_workspace = _int(r"What is outstanding \((\d+) blocking\)", workspace)
        assert in_workspace > 0
        assert "No active blocker" not in bids and "No active blocker" not in my_day
        assert _int(r"(\d+) blocking — ", bids) == in_workspace
        assert _int(r"Development checks: (\d+) blocking", my_day) == in_workspace
        assert _int(r"(\d+) blocking — ", my_day) == in_workspace

    asyncio.run(scenario())
