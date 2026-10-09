"""Append-only gate approvals: NOT_OBTAINED stays history, a later OBTAINED supersedes it."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime

import pytest

from core.bid_repository import BidRepository
from core.database import Database
from core.enums import ApprovalType
from core.gate_service import build_gate_context
from core.gates import ConditionState, evaluate_g1, evaluate_g4
from core.schemas import Approval, Bid, Provenance
from tests.unit.test_gate_approval_resolve_path import (
    _request,
    _row_count,
)
from tests.unit.test_gate_approval_resolve_path import (
    gate_approval_app as gate_approval_app,
)


@pytest.fixture(autouse=True)
def _selected_pricing_baseline(bid_repo: BidRepository, valid_bid: Bid) -> None:
    """Existing lineage tests now record obtained approvals against a selected baseline."""
    with bid_repo._conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS scenario_baselines(
            selection_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,scenario_version_id TEXT NOT NULL,
            selected_by TEXT NOT NULL,selected_at TEXT NOT NULL,rationale TEXT NOT NULL)"""
        )
        conn.execute(
            "INSERT INTO scenario_baselines VALUES(?,?,?,?,?,?)",
            (
                "SBL-LINEAGE",
                valid_bid.bid_id,
                "SV-LINEAGE",
                "jason",
                "2026-10-01T12:00:00+00:00",
                "Selected for lineage tests",
            ),
        )
        conn.commit()


def _approval(
    bid: Bid,
    kind: ApprovalType,
    *,
    obtained: bool,
    approval_id: str,
    required: bool = True,
) -> Approval:
    return Approval(
        approval_id=approval_id,
        bid_id=bid.bid_id,
        approval_type=kind,
        required=required,
        obtained=obtained,
        authority="Executive sponsor" if obtained else None,
        evidence_ref="Meeting minutes ref" if obtained else None,
        decision="Approved" if obtained else "Not yet approved",
        decided_at=datetime.now(UTC) if obtained else None,
        provenance=Provenance.from_human("jason"),
    )


def _margin_met(repo: BidRepository, bid: Bid) -> bool:
    g4 = evaluate_g4(build_gate_context(repo, repo.db, bid.bid_id))
    condition = next(c for c in g4.conditions if c.condition_id == "g4.margin_approved")
    return condition.state is ConditionState.MET


def _audit_actions(repo: BidRepository, bid: Bid) -> list[str]:
    with repo._conn() as conn:
        rows = conn.execute(
            "SELECT action FROM audit_log WHERE bid_id=? ORDER BY timestamp", (bid.bid_id,)
        ).fetchall()
    return [str(row["action"]) for row in rows]


def test_not_obtained_then_obtained_keeps_history_and_unblocks_only_at_the_second_event(
    bid_repo: BidRepository, valid_bid: Bid
) -> None:
    bid_repo.create_bid(valid_bid)
    first = _approval(valid_bid, ApprovalType.MARGIN, obtained=False, approval_id="APR-1")
    assert bid_repo.record_gate_approval(first, "jason") == "created"
    assert _margin_met(bid_repo, valid_bid) is False  # NOT_OBTAINED keeps the gate blocked

    with bid_repo._conn() as conn:
        before = tuple(conn.execute("SELECT * FROM approvals WHERE approval_id='APR-1'").fetchone())

    second = _approval(valid_bid, ApprovalType.MARGIN, obtained=True, approval_id="APR-2")
    assert bid_repo.record_gate_approval(second, "jason", "APR-1") == "superseded"
    assert _margin_met(bid_repo, valid_bid) is True

    history = bid_repo.list_approvals(valid_bid.bid_id)
    assert [item.approval_id for item in history] == ["APR-1", "APR-2"]
    current = bid_repo.list_current_approvals(valid_bid.bid_id)
    assert [item.approval_id for item in current] == ["APR-2"]
    assert current[0].supersedes_approval_id == "APR-1"
    assert current[0].recorded_at is not None

    with bid_repo._conn() as conn:  # the earlier event is untouched evidence
        assert (
            tuple(conn.execute("SELECT * FROM approvals WHERE approval_id='APR-1'").fetchone())
            == before
        )
        with pytest.raises(sqlite3.IntegrityError, match="historical evidence"):
            conn.execute("UPDATE approvals SET obtained=1 WHERE approval_id='APR-1'")
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute("DELETE FROM approvals WHERE approval_id='APR-1'")
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert _audit_actions(bid_repo, valid_bid) == [
        "bid_gate_approval_created",
        "bid_gate_approval_superseded",
    ]


def test_duplicate_submission_is_idempotent_and_conflicts_are_rejected(
    bid_repo: BidRepository, valid_bid: Bid
) -> None:
    bid_repo.create_bid(valid_bid)
    not_obtained = _approval(valid_bid, ApprovalType.MARGIN, obtained=False, approval_id="APR-1")
    assert bid_repo.record_gate_approval(not_obtained, "jason") == "created"
    repeat = not_obtained.model_copy(update={"approval_id": "APR-1b"})
    assert bid_repo.record_gate_approval(repeat, "jason") == "unchanged"
    different = _approval(valid_bid, ApprovalType.MARGIN, obtained=False, approval_id="APR-1c")
    different = different.model_copy(update={"decision": "Still pending"})
    with pytest.raises(ValueError, match="already recorded as not obtained"):
        bid_repo.record_gate_approval(different, "jason")
    obtained = _approval(valid_bid, ApprovalType.MARGIN, obtained=True, approval_id="APR-2")
    with pytest.raises(ValueError, match="changed since the page was loaded"):
        bid_repo.record_gate_approval(obtained, "jason", None)  # stale: no expected id
    with pytest.raises(ValueError, match="changed since the page was loaded"):
        bid_repo.record_gate_approval(obtained, "jason", "APR-other")
    assert bid_repo.record_gate_approval(obtained, "jason", "APR-1") == "superseded"
    again = obtained.model_copy(update={"approval_id": "APR-2b"})
    assert bid_repo.record_gate_approval(again, "jason", "APR-1") == "unchanged"
    reverse = _approval(valid_bid, ApprovalType.MARGIN, obtained=False, approval_id="APR-3")
    with pytest.raises(ValueError, match="already recorded as obtained"):
        bid_repo.record_gate_approval(reverse, "jason")
    assert [a.approval_id for a in bid_repo.list_approvals(valid_bid.bid_id)] == ["APR-1", "APR-2"]
    assert len(_audit_actions(bid_repo, valid_bid)) == 2


def test_concurrent_second_supersession_of_the_same_record_is_blocked_by_the_database(
    bid_repo: BidRepository, valid_bid: Bid
) -> None:
    bid_repo.create_bid(valid_bid)
    bid_repo.record_gate_approval(
        _approval(valid_bid, ApprovalType.MARGIN, obtained=False, approval_id="APR-1"), "jason"
    )
    bid_repo.record_gate_approval(
        _approval(valid_bid, ApprovalType.MARGIN, obtained=True, approval_id="APR-2"),
        "jason",
        "APR-1",
    )
    with bid_repo._conn() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO approvals(approval_id,bid_id,approval_type,required,obtained,"
            "provenance_json,supersedes_approval_id) VALUES('APR-X',?,?,1,0,'{}','APR-1')",
            (valid_bid.bid_id, ApprovalType.MARGIN.value),
        )


def test_required_flag_history_does_not_keep_a_superseded_control_missing(
    bid_repo: BidRepository, valid_bid: Bid
) -> None:
    bid_repo.create_bid(valid_bid)
    bid_repo.record_gate_approval(
        _approval(valid_bid, ApprovalType.LEGAL, obtained=False, approval_id="APR-1"), "jason"
    )

    def required_met() -> bool:
        g4 = evaluate_g4(build_gate_context(bid_repo, bid_repo.db, valid_bid.bid_id))
        return (
            next(c for c in g4.conditions if c.condition_id == "g4.required_approvals").state
            is ConditionState.MET
        )

    assert required_met() is False
    bid_repo.record_gate_approval(
        _approval(valid_bid, ApprovalType.LEGAL, obtained=True, approval_id="APR-2"),
        "jason",
        "APR-1",
    )
    assert required_met() is True


def test_g1_ignores_legacy_bid_no_bid_approval_lineage(
    bid_repo: BidRepository, valid_bid: Bid
) -> None:
    bid_repo.create_bid(valid_bid)
    bid_repo.record_gate_approval(
        _approval(valid_bid, ApprovalType.BID_NO_BID, obtained=False, approval_id="APR-1"), "jason"
    )
    assert (
        evaluate_g1(build_gate_context(bid_repo, bid_repo.db, valid_bid.bid_id)).conditions[0].state
        is not ConditionState.MET
    )
    bid_repo.record_gate_approval(
        _approval(valid_bid, ApprovalType.BID_NO_BID, obtained=True, approval_id="APR-2"),
        "jason",
        "APR-1",
    )
    assert (
        evaluate_g1(build_gate_context(bid_repo, bid_repo.db, valid_bid.bid_id)).conditions[0].state
        is ConditionState.UNMET
    )


def test_existing_database_without_lineage_columns_is_upgraded_additively(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE bids(bid_id TEXT PRIMARY KEY);
            CREATE TABLE approvals(
              approval_id TEXT PRIMARY KEY, bid_id TEXT NOT NULL, approval_type TEXT NOT NULL,
              required INTEGER NOT NULL DEFAULT 1, obtained INTEGER NOT NULL DEFAULT 0,
              authority TEXT, evidence_ref TEXT, decision TEXT, decided_at TEXT,
              provenance_json TEXT NOT NULL);
            INSERT INTO bids VALUES('B-1');
            INSERT INTO approvals(approval_id,bid_id,approval_type,provenance_json)
              VALUES('LEGACY-1','B-1','margin','{}');
            """
        )
    repo = BidRepository(Database(path))
    with repo._conn() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(approvals)")}
        assert {"supersedes_approval_id", "recorded_at", "pricing_baseline_id"} <= columns
        assert conn.execute("SELECT count(*) FROM approvals").fetchone()[0] == 1
    # A pre-existing row is its own current record until something supersedes it.
    with repo._conn() as conn:
        current = conn.execute(
            "SELECT approval_id FROM approvals WHERE NOT EXISTS (SELECT 1 FROM approvals l "
            "WHERE l.supersedes_approval_id = approvals.approval_id)"
        ).fetchall()
    assert [row["approval_id"] for row in current] == ["LEGACY-1"]


def test_route_sequence_not_obtained_then_obtained_with_page_state(
    gate_approval_app,
    valid_bid: Bid,  # type: ignore[no-untyped-def]  # noqa: F811
) -> None:
    module = gate_approval_app
    path = f"/decisions?bid_id={valid_bid.bid_id}&approval_type=MARGIN"
    form = {
        "bid_id": valid_bid.bid_id,
        "approval_type": "margin",
        "authority": "",
        "evidence_ref": "",
        "decision": "Deferred",
    }
    status, _, _ = asyncio.run(_request(module.app, "POST", "/decisions/gate-approvals", form))
    assert status == 303
    _, _, body = asyncio.run(_request(module.app, "GET", path))
    page = body.decode()
    assert "Recorded as not obtained" in page
    assert "The gate stays blocked" in page
    assert "Current" in page and "Superseded" not in page
    current = module.bid_repository.list_current_approvals(valid_bid.bid_id)
    assert len(current) == 1 and current[0].obtained is False
    assert f'name="expected_current_approval_id" value="{current[0].approval_id}"' in page

    obtained = {
        **form,
        "obtained": "on",
        "authority": "Executive sponsor",
        "evidence_ref": "Minutes ref",
        "decision": "Approved",
        "expected_current_approval_id": current[0].approval_id,
    }
    status, _, _ = asyncio.run(_request(module.app, "POST", "/decisions/gate-approvals", obtained))
    assert status == 303
    # Resubmitting the same form (e.g. a double click) changes nothing.
    status, _, _ = asyncio.run(_request(module.app, "POST", "/decisions/gate-approvals", obtained))
    assert status == 303
    assert _row_count(module, "approvals") == 2
    assert _row_count(module, "audit_log") == 2
    _, _, body = asyncio.run(_request(module.app, "GET", path))
    page = body.decode()
    assert "Obtained." in page and "identifies the selected pricing baseline" in page
    assert "History" in page
    assert _margin_met(module.bid_repository, valid_bid) is True
    with module.db._conn() as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
