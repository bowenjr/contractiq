"""Deterministic validation for the OPS-06 Bid control-centre projections."""

from __future__ import annotations

import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import ValidationError

from core.bid_control_center import (
    BidControlCenterService,
    BidPortfolioFilters,
    BidPortfolioView,
    BidWorkspaceAttention,
)
from core.bid_repository import BidRepository
from core.database import Database
from core.enums import BidStatus, Gate
from core.my_day import MyDayCounts, MyDayProjection, ReadinessSnapshot
from core.readiness import Blocker, ReadinessReport, ReadinessVerdict
from core.schemas import Bid
from core.work_item_repository import WorkItemRepository


def _bid(identifier: str, status: str, internal_due: str, customer_due: str) -> Bid:
    return Bid.model_validate(
        {
            "bid_id": identifier,
            "customer": "Example EPCM",
            "customer_type": "epcm",
            "project_name": f"Control centre {identifier}",
            "sales_owner": "Sales",
            "bc_owner": "Jason",
            "release_date": "2026-08-01",
            "customer_due_date": customer_due,
            "internal_due_date": internal_due,
            "estimated_value": "1000000",
            "classification": "level_3",
            "status": status,
            "created_at": "2026-08-01T09:00:00+00:00",
            "updated_at": "2026-08-01T09:00:00+00:00",
        }
    )


def _report(bid_id: str, verdict: ReadinessVerdict) -> ReadinessReport:
    return ReadinessReport(
        bid_id=bid_id,
        verdict=verdict,
        blockers=(
            [
                Blocker(
                    condition_id="g2.requirements_complete",
                    gate=Gate.G2,
                    description="Customer requirements remain unresolved",
                    detail="Material customer requirements are incomplete.",
                    material=True,
                )
            ]
            if verdict is ReadinessVerdict.HOLD
            else []
        ),
        generated_at=datetime(2026, 8, 27, 9, tzinfo=UTC),
        summary=f"{verdict.value} deterministic readiness",
        advisory="Existing gate result.",
    )


def main() -> None:
    as_of = date(2026, 8, 27)
    with tempfile.TemporaryDirectory(prefix="contractiq-ops06-validation-") as directory:
        database = Database(Path(directory) / "validation.db")
        bids = BidRepository(database)
        work = WorkItemRepository(database)
        held = _bid("B-2026-0601", "held", "2026-08-26", "2026-09-10")
        upcoming = _bid("B-2026-0602", "active", "2026-09-01", "2026-09-03")
        history = _bid("B-2026-0603", "won", "2026-08-20", "2026-08-22")
        for bid in (held, upcoming, history):
            bids.create_bid(bid)
        reports = [
            ReadinessSnapshot(
                bid_id=held.bid_id,
                bid_name=held.project_name,
                report=_report(held.bid_id, ReadinessVerdict.HOLD),
            ),
            ReadinessSnapshot(
                bid_id=upcoming.bid_id,
                bid_name=upcoming.project_name,
                report=_report(upcoming.bid_id, ReadinessVerdict.CLEAR),
            ),
            ReadinessSnapshot(
                bid_id=history.bid_id,
                bid_name=history.project_name,
                report=_report(history.bid_id, ReadinessVerdict.CLEAR),
            ),
        ]

        def load_projection(value: date) -> MyDayProjection:
            assert value == as_of
            return MyDayProjection(
                as_of=value,
                horizon_days=7,
                blocked=[],
                waiting=[],
                overdue=[],
                due_today=[],
                upcoming=[],
                later_or_unscheduled=[],
                readiness_reports=reports,
                readiness_holds=[reports[0]],
                requirement_attention=[],
                counts=MyDayCounts(
                    overdue=0,
                    due_today=0,
                    waiting=0,
                    blocked=0,
                    readiness_holds=1,
                    requirement_attention=0,
                    requirement_overdue=0,
                    requirement_due_today=0,
                ),
            )

        reports_by_id = {snapshot.bid_id: snapshot.report for snapshot in reports}
        service = BidControlCenterService(
            bids,
            work,
            load_projection,
            lambda bid_id: reports_by_id[bid_id],
            lambda _bid_id, _as_of: BidWorkspaceAttention(),
        )
        current = service.portfolio(BidPortfolioFilters(), as_of=as_of)
        assert [row.bid.bid_id for row in current.rows] == [held.bid_id, upcoming.bid_id]
        assert current.rows[0].sort_tier == 0
        assert current.rows[1].sort_tier == 2
        archived = service.portfolio(
            BidPortfolioFilters(view=BidPortfolioView.HISTORY),
            as_of=as_of,
        )
        assert [row.bid.status for row in archived.rows] == [BidStatus.WON]
        workspace = service.workspace(held.bid_id, as_of=as_of)
        assert workspace.bid.bid_id == held.bid_id
        assert workspace.current_gate_label == "Bid setup"
        assert workspace.next_action_destination == f"/bids/{held.bid_id}/requirements-scope"
        try:
            BidPortfolioFilters.model_validate({"readiness": "escalate"})
        except ValidationError:
            pass
        else:
            raise AssertionError("ESCALATE must not be exposed as a current portfolio filter")
        try:
            BidPortfolioFilters.model_validate({"view": "history", "status": "active"})
        except ValidationError:
            pass
        else:
            raise AssertionError("Contradictory portfolio filters must be rejected")
        with database._conn() as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    print("OPS-06 validation: PASS")


if __name__ == "__main__":
    main()
