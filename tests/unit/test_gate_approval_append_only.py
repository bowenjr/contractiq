"""No caller can change an authoritative gate approval decision in place."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.bid_repository import BidRepository
from core.database import Database
from core.enums import ApprovalType
from core.gate_service import build_gate_context
from core.gates import ConditionState, evaluate_g4
from core.schemas import Approval, Bid, Provenance

REPO = Path(__file__).parents[2]


@pytest.fixture(autouse=True)
def _selected_pricing_baseline(bid_repo: BidRepository, valid_bid: Bid) -> None:
    """Existing append-only tests now bind obtained approvals to a selected baseline."""
    with bid_repo._conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS scenario_baselines(
            selection_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,scenario_version_id TEXT NOT NULL,
            selected_by TEXT NOT NULL,selected_at TEXT NOT NULL,rationale TEXT NOT NULL)"""
        )
        conn.execute(
            "INSERT INTO scenario_baselines VALUES(?,?,?,?,?,?)",
            (
                "SBL-APPEND",
                valid_bid.bid_id,
                "SV-APPEND",
                "jason",
                "2026-10-01T12:00:00+00:00",
                "Selected for append-only tests",
            ),
        )
        conn.commit()


def _approval(
    bid: Bid, approval_id: str, *, obtained: bool, kind: ApprovalType = ApprovalType.MARGIN
) -> Approval:
    return Approval(
        approval_id=approval_id,
        bid_id=bid.bid_id,
        approval_type=kind,
        obtained=obtained,
        authority="Executive sponsor" if obtained else None,
        evidence_ref="Minutes" if obtained else None,
        decision="Approved" if obtained else "Pending",
        decided_at=datetime.now(UTC) if obtained else None,
        provenance=Provenance.from_human("jason"),
    )


def _margin_met(repo: BidRepository, bid: Bid) -> bool:
    g4 = evaluate_g4(build_gate_context(repo, repo.db, bid.bid_id))
    condition = next(c for c in g4.conditions if c.condition_id == "g4.margin_approved")
    return condition.state is ConditionState.MET


@pytest.fixture
def chain(bid_repo: BidRepository, valid_bid: Bid) -> BidRepository:
    """NOT_OBTAINED (APR-1) superseded by OBTAINED (APR-2) for the margin control."""
    bid_repo.create_bid(valid_bid)
    bid_repo.record_gate_approval(_approval(valid_bid, "APR-1", obtained=False), "jason")
    bid_repo.record_gate_approval(_approval(valid_bid, "APR-2", obtained=True), "jason", "APR-1")
    return bid_repo


@pytest.mark.parametrize("approval_id", ["APR-1", "APR-2"])
def test_update_approval_is_rejected_for_current_and_superseded_rows(
    chain: BidRepository, valid_bid: Bid, approval_id: str
) -> None:
    before = chain.list_approvals(valid_bid.bid_id)
    changed = _approval(valid_bid, approval_id, obtained=approval_id == "APR-1")
    with pytest.raises(ValueError, match="cannot be edited in place"):
        chain.update_approval(changed)
    assert chain.list_approvals(valid_bid.bid_id) == before
    assert _margin_met(chain, valid_bid) is True


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE approvals SET obtained=0 WHERE approval_id='APR-2'",  # withdraw the current
        "UPDATE approvals SET decision='x' WHERE approval_id='APR-2'",
        "UPDATE approvals SET obtained=1 WHERE approval_id='APR-1'",  # rewrite history
        "UPDATE approvals SET supersedes_approval_id=NULL WHERE approval_id='APR-2'",
        "DELETE FROM approvals WHERE approval_id='APR-2'",  # would resurrect NOT_OBTAINED
        "DELETE FROM approvals WHERE approval_id='APR-1'",
    ],
)
def test_database_refuses_in_place_edits_and_deletes(
    chain: BidRepository, valid_bid: Bid, statement: str
) -> None:
    with chain._conn() as conn, pytest.raises(sqlite3.IntegrityError, match="approvals"):
        conn.execute(statement)
    assert [a.approval_id for a in chain.list_current_approvals(valid_bid.bid_id)] == ["APR-2"]
    assert _margin_met(chain, valid_bid) is True


def test_current_not_obtained_row_cannot_be_flipped_in_place_either(
    bid_repo: BidRepository, valid_bid: Bid
) -> None:
    bid_repo.create_bid(valid_bid)
    bid_repo.record_gate_approval(_approval(valid_bid, "APR-1", obtained=False), "jason")
    with bid_repo._conn() as conn, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE approvals SET obtained=1 WHERE approval_id='APR-1'")
    assert _margin_met(bid_repo, valid_bid) is False  # still blocked until a real second event


def test_a_second_unsuperseding_decision_cannot_be_inserted(
    chain: BidRepository, valid_bid: Bid
) -> None:
    with pytest.raises(ValueError, match="already been recorded"):
        chain.create_approval(_approval(valid_bid, "APR-3", obtained=False))
    with pytest.raises(ValueError, match="already been recorded"):
        chain.create_approval(_approval(valid_bid, "APR-4", obtained=False), "jason")
    with chain._conn() as conn, pytest.raises(sqlite3.IntegrityError, match="current decision"):
        conn.execute(
            "INSERT INTO approvals(approval_id,bid_id,approval_type,required,obtained,"
            "provenance_json) VALUES('APR-5',?,?,1,0,'{}')",
            (valid_bid.bid_id, ApprovalType.MARGIN.value),
        )
    assert [a.approval_id for a in chain.list_approvals(valid_bid.bid_id)] == ["APR-1", "APR-2"]


def test_an_obtained_decision_cannot_be_superseded_by_a_withdrawal(
    chain: BidRepository, valid_bid: Bid
) -> None:
    with (
        chain._conn() as conn,
        pytest.raises(sqlite3.IntegrityError, match="differently baseline-bound"),
    ):
        conn.execute(
            "INSERT INTO approvals(approval_id,bid_id,approval_type,required,obtained,"
            "provenance_json,supersedes_approval_id) VALUES('APR-6',?,?,1,0,'{}','APR-2')",
            (valid_bid.bid_id, ApprovalType.MARGIN.value),
        )
    assert _margin_met(chain, valid_bid) is True


def test_earlier_builds_narrow_triggers_are_replaced_on_upgrade(tmp_path: Path) -> None:
    path = tmp_path / "upgrade.db"
    repo = BidRepository(Database(path))
    with repo._conn() as conn:
        for name in (
            "approvals_append_only_update",
            "approvals_append_only_delete",
            "approvals_single_current",
        ):
            conn.execute(f"DROP TRIGGER {name}")
        conn.execute(
            "CREATE TRIGGER approvals_superseded_immutable BEFORE UPDATE ON approvals "
            "WHEN EXISTS(SELECT 1 FROM approvals s WHERE s.supersedes_approval_id=OLD.approval_id) "
            "BEGIN SELECT RAISE(ABORT,'narrow'); END"
        )
    BidRepository(Database(path))  # re-open applies the migration
    with sqlite3.connect(path) as conn:
        names = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
    assert "approvals_superseded_immutable" not in names
    assert {
        "approvals_append_only_update",
        "approvals_append_only_delete",
        "approvals_single_current",
    } <= names


def test_no_production_code_can_edit_or_delete_approvals() -> None:
    """Static guard: only the repository's own refusal and append paths touch the table."""
    write_pattern = re.compile(
        r"\b(UPDATE\s+approvals\b|DELETE\s+FROM\s+approvals\b|INSERT\s+OR\s+REPLACE\s+INTO\s+approvals\b)",
        re.IGNORECASE,
    )
    call_pattern = re.compile(r"\.update_approval\(")
    offenders: list[str] = []
    for root in ("core", "scripts"):
        for path in sorted((REPO / root).rglob("*.py")):
            text = path.read_text()
            for number, line in enumerate(text.splitlines(), start=1):
                if write_pattern.search(line) or call_pattern.search(line):
                    offenders.append(f"{path.relative_to(REPO)}:{number}")
    app_text = (REPO / "app.py").read_text()
    for number, line in enumerate(app_text.splitlines(), start=1):
        if write_pattern.search(line) or call_pattern.search(line):
            offenders.append(f"app.py:{number}")
    assert offenders == []
