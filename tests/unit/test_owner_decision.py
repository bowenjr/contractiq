"""OPS-13A-F-A owner pursuit decision acceptance matrix."""

from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from core.bid_repository import BidRepository
from core.database import Database
from core.enums import ApprovalType, BidStatus
from core.gate_service import build_gate_context
from core.gates import ConditionState, evaluate_g1
from core.owner_decision_repository import OwnerDecisionRepository
from core.owner_decision_service import OwnerDecisionService
from core.owner_decisions import OwnerDecisionValue
from core.readiness_service import evaluate_readiness, request_override
from core.schemas import Approval, Bid, Provenance


@pytest.fixture
def owner_repo(tmp_db: Database) -> OwnerDecisionRepository:
    return OwnerDecisionRepository(tmp_db)


@pytest.fixture
def owner_service(tmp_db: Database, owner_repo: OwnerDecisionRepository) -> OwnerDecisionService:
    return OwnerDecisionService(tmp_db, owner_repo)


def _status(repo: BidRepository, bid_id: str) -> BidStatus:
    bid = repo.get_bid(bid_id)
    assert bid is not None
    return bid.status


def _counts(repo: OwnerDecisionRepository, bid_id: str) -> tuple[int, int]:
    with repo._conn() as conn:
        decisions = int(
            conn.execute(
                "SELECT count(*) FROM bid_owner_decisions WHERE bid_id=?", (bid_id,)
            ).fetchone()[0]
        )
        audits = int(
            conn.execute("SELECT count(*) FROM audit_log WHERE bid_id=?", (bid_id,)).fetchone()[0]
        )
    return decisions, audits


def _legacy_approval(bid: Bid, approval_id: str = "LEGACY-BNB") -> Approval:
    return Approval(
        approval_id=approval_id,
        bid_id=bid.bid_id,
        approval_type=ApprovalType.BID_NO_BID,
        obtained=True,
        decision="Do not pursue",
        provenance=Provenance.from_human("legacy owner"),
    )


def test_t1_first_pursue_is_frozen_audited_and_meets_g1(
    bid_repo: BidRepository,
    owner_repo: OwnerDecisionRepository,
    owner_service: OwnerDecisionService,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    recorded = owner_service.record_owner_decision(
        valid_bid.bid_id, "PURSUE", "  The opportunity is strategically aligned.  ", "Owner"
    )

    assert recorded.decision is OwnerDecisionValue.PURSUE
    assert recorded.reason == "The opportunity is strategically aligned."
    assert recorded.supersedes_decision_id is None
    assert recorded.evidence_snapshot.received_files.total == 0
    assert recorded.evidence_snapshot.requirement_count == 0
    assert len(recorded.evidence_snapshot.unresolved_questions) == 2
    assert _status(bid_repo, valid_bid.bid_id) is BidStatus.ACTIVE
    assert _counts(owner_repo, valid_bid.bid_id) == (1, 1)
    g1 = evaluate_g1(build_gate_context(bid_repo, bid_repo.db, valid_bid.bid_id))
    assert g1.conditions[0].condition_id == "g1.owner_pursuit_decided"
    assert g1.conditions[0].state is ConditionState.MET
    with pytest.raises(ValidationError, match="frozen"):
        recorded.reason = "changed"  # type: ignore[misc]


def test_t2_t3_t4_append_lineage_and_change_status_atomically(
    bid_repo: BidRepository,
    owner_repo: OwnerDecisionRepository,
    owner_service: OwnerDecisionService,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    first = owner_service.record_owner_decision(
        valid_bid.bid_id, "DO_NOT_PURSUE", "Capacity is unavailable.", "Owner"
    )
    assert _status(bid_repo, valid_bid.bid_id) is BidStatus.NO_BID  # T2
    stopped_g1 = evaluate_g1(build_gate_context(bid_repo, bid_repo.db, valid_bid.bid_id))
    assert stopped_g1.conditions[0].state is ConditionState.NOT_ASSESSABLE
    second = owner_service.record_owner_decision(
        valid_bid.bid_id, "PURSUE", "Capacity has been secured.", "Owner", first.decision_id
    )
    assert second.supersedes_decision_id == first.decision_id
    assert _status(bid_repo, valid_bid.bid_id) is BidStatus.ACTIVE  # T4
    third = owner_service.record_owner_decision(
        valid_bid.bid_id,
        "DO_NOT_PURSUE",
        "Customer timing is no longer viable.",
        "Owner",
        second.decision_id,
    )
    assert third.supersedes_decision_id == second.decision_id
    assert _status(bid_repo, valid_bid.bid_id) is BidStatus.NO_BID  # T3
    assert [row.decision for row in owner_repo.list_history(valid_bid.bid_id)] == [
        OwnerDecisionValue.DO_NOT_PURSUE,
        OwnerDecisionValue.PURSUE,
        OwnerDecisionValue.DO_NOT_PURSUE,
    ]
    assert _counts(owner_repo, valid_bid.bid_id) == (3, 3)


def test_t5_t6_same_stale_and_missing_expectations_write_nothing(
    bid_repo: BidRepository,
    owner_repo: OwnerDecisionRepository,
    owner_service: OwnerDecisionService,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    current = owner_service.record_owner_decision(valid_bid.bid_id, "PURSUE", "Proceed.", "Owner")
    before = _counts(owner_repo, valid_bid.bid_id)
    with pytest.raises(ValueError, match="already the current decision"):
        owner_service.record_owner_decision(
            valid_bid.bid_id, "PURSUE", "Still proceed.", "Owner", current.decision_id
        )
    for expected in (None, "OD-stale"):
        with pytest.raises(ValueError, match="changed since the page was loaded"):
            owner_service.record_owner_decision(
                valid_bid.bid_id, "DO_NOT_PURSUE", "Stop.", "Owner", expected
            )
    assert _counts(owner_repo, valid_bid.bid_id) == before
    assert _status(bid_repo, valid_bid.bid_id) is BidStatus.ACTIVE


@pytest.mark.parametrize(
    ("decision", "reason", "message"),
    [
        ("UNKNOWN", "Reason", "must be PURSUE"),
        ("PURSUE", " ", "1 to 500"),
        ("PURSUE", "x" * 501, "1 to 500"),
    ],
)
def test_t7_invalid_input_is_refused(
    bid_repo: BidRepository,
    owner_repo: OwnerDecisionRepository,
    owner_service: OwnerDecisionService,
    valid_bid: Bid,
    decision: str,
    reason: str,
    message: str,
) -> None:
    bid_repo.create_bid(valid_bid)
    with pytest.raises(ValueError, match=message):
        owner_service.record_owner_decision(valid_bid.bid_id, decision, reason, "Owner")
    assert _counts(owner_repo, valid_bid.bid_id) == (0, 0)


@pytest.mark.parametrize("status", [BidStatus.SUBMITTED, BidStatus.WON, BidStatus.LOST])
def test_t8_terminal_status_refuses_a_change(
    bid_repo: BidRepository,
    owner_repo: OwnerDecisionRepository,
    owner_service: OwnerDecisionService,
    valid_bid: Bid,
    status: BidStatus,
) -> None:
    bid_repo.create_bid(valid_bid)
    current = owner_service.record_owner_decision(valid_bid.bid_id, "PURSUE", "Proceed.", "Owner")
    with owner_repo._conn() as conn:
        conn.execute("UPDATE bids SET status=? WHERE bid_id=?", (status.value, valid_bid.bid_id))
    with pytest.raises(ValueError, match="cannot change owner decision"):
        owner_service.record_owner_decision(
            valid_bid.bid_id, "DO_NOT_PURSUE", "Stop.", "Owner", current.decision_id
        )
    assert _counts(owner_repo, valid_bid.bid_id) == (1, 1)


def test_t8_issue_baseline_refuses_a_change(
    bid_repo: BidRepository,
    owner_repo: OwnerDecisionRepository,
    owner_service: OwnerDecisionService,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    current = owner_service.record_owner_decision(valid_bid.bid_id, "PURSUE", "Proceed.", "Owner")
    with owner_repo._conn() as conn:
        conn.execute("CREATE TABLE issued_offer_baselines(baseline_id TEXT,bid_id TEXT)")
        conn.execute("INSERT INTO issued_offer_baselines VALUES('BASE-1',?)", (valid_bid.bid_id,))
    with pytest.raises(ValueError, match="customer issue baseline"):
        owner_service.record_owner_decision(
            valid_bid.bid_id, "DO_NOT_PURSUE", "Stop.", "Owner", current.decision_id
        )
    assert _counts(owner_repo, valid_bid.bid_id) == (1, 1)


def test_t9_database_triggers_refuse_update_and_delete(
    bid_repo: BidRepository,
    owner_repo: OwnerDecisionRepository,
    owner_service: OwnerDecisionService,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    current = owner_service.record_owner_decision(valid_bid.bid_id, "PURSUE", "Proceed.", "Owner")
    with owner_repo._conn() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE bid_owner_decisions SET reason='Changed' WHERE decision_id=?",
                (current.decision_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "DELETE FROM bid_owner_decisions WHERE decision_id=?", (current.decision_id,)
            )


def test_t10_audit_failure_rolls_back_decision_and_status(
    bid_repo: BidRepository,
    owner_repo: OwnerDecisionRepository,
    owner_service: OwnerDecisionService,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    with owner_repo._conn() as conn:
        conn.execute(
            """CREATE TRIGGER reject_owner_audit BEFORE INSERT ON audit_log
            WHEN NEW.action='owner_pursuit_decision_recorded'
            BEGIN SELECT RAISE(ABORT,'audit refused'); END"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="audit refused"):
        owner_service.record_owner_decision(
            valid_bid.bid_id, "DO_NOT_PURSUE", "Do not proceed.", "Owner"
        )
    assert _counts(owner_repo, valid_bid.bid_id) == (0, 0)
    assert _status(bid_repo, valid_bid.bid_id) is BidStatus.ACTIVE


def test_t11_g12_g15_legacy_approval_is_untouched_and_does_not_meet_g1(
    bid_repo: BidRepository,
    owner_repo: OwnerDecisionRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    bid_repo.create_approval(_legacy_approval(valid_bid))
    before = bid_repo.list_approvals(valid_bid.bid_id)
    context = build_gate_context(bid_repo, bid_repo.db, valid_bid.bid_id)
    assert context.owner_decision is None
    assert evaluate_g1(context).conditions[0].state is ConditionState.UNMET
    report = evaluate_readiness(bid_repo, bid_repo.db, valid_bid.bid_id)
    assert any(item.condition_id == "g1.owner_pursuit_decided" for item in report.blockers)
    assert bid_repo.list_approvals(valid_bid.bid_id) == before
    assert owner_repo.list_history(valid_bid.bid_id) == []


def test_g14_owner_decision_readiness_override_is_refused_without_writes(
    bid_repo: BidRepository,
    owner_repo: OwnerDecisionRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    with pytest.raises(ValueError, match="cannot be overridden"):
        request_override(
            bid_repo,
            bid_repo.db,
            valid_bid.bid_id,
            "g1.owner_pursuit_decided",
            "Executive",
            "Accept risk",
        )
    assert bid_repo.list_gate_records(valid_bid.bid_id) == []
    assert _counts(owner_repo, valid_bid.bid_id) == (0, 0)


@pytest.mark.parametrize("status", [BidStatus.SUBMITTED, BidStatus.WON, BidStatus.LOST])
def test_g16_legacy_terminal_bid_is_not_regated(
    bid_repo: BidRepository,
    owner_repo: OwnerDecisionRepository,
    valid_bid: Bid,
    status: BidStatus,
) -> None:
    bid_repo.create_bid(valid_bid.model_copy(update={"status": status}))
    result = evaluate_g1(build_gate_context(bid_repo, bid_repo.db, valid_bid.bid_id))
    assert result.passed is True
    assert result.conditions[0].state is ConditionState.NOT_ASSESSABLE
    assert owner_repo.list_history(valid_bid.bid_id) == []
