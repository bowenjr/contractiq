"""OPS-13A U-P temporary-rule, baseline-binding, and policy regressions."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from core.approval_authority import AuthorityPolicy, PolicyLifecycle, StageMode
from core.approval_repository import ApprovalRepository
from core.bid_repository import BidRepository
from core.database import Database
from core.enums import ApprovalType, BidLevel
from core.owner_decision_service import OwnerDecisionService
from core.proposal_exchange import CustomerIssueCommand
from core.schemas import Approval, Provenance
from core.senior_approvals import derive_required_approvals
from tests.unit.test_issue_phase_approvals import _command, _level
from tests.unit.test_ops09 import BID_ID, NOW, import_candidate, service_with_ready_bid


def _pursue(service: object) -> None:
    OwnerDecisionService(service.db).record_owner_decision(  # type: ignore[attr-defined]
        BID_ID,
        "PURSUE",
        "The owner approved pursuit for policy-clarification verification.",
        "Jason",
    )


def _policy(service: object, *, roles: tuple[str, ...] = ("MARGIN",)) -> None:
    value = AuthorityPolicy(
        policy_id="POL-UP",
        name="Issue authority",
        description="Authority supplied for the test.",
        scope="BID",
        effective_from=NOW - timedelta(days=1),
        lifecycle_state=PolicyLifecycle.PUBLISHED,
        roles=roles,
        rules=({"rule_id": "R-ALL", "dimensions": {}},),
        stages=(
            {
                "order": 1,
                "mode": StageMode.ALL_REQUIRED.value,
                "roles": list(roles),
            },
        ),
        created_by="Jason",
        created_at=NOW,
        provenance=Provenance.from_human("Jason"),
    )
    ApprovalRepository(service.db).create_policy(value, "Jason")  # type: ignore[attr-defined]


def _approval(*, baseline_id: str | None) -> Approval:
    return Approval(
        approval_id="APR-UP",
        bid_id=BID_ID,
        approval_type=ApprovalType.MARGIN,
        obtained=True,
        authority="Margin Director",
        evidence_ref="Decision record UP",
        decision="APPROVED",
        decided_at=NOW,
        provenance=Provenance.from_human("Jason"),
        pricing_baseline_id=baseline_id,
    )


def _current_baseline(service: object) -> str:
    with service.db._conn() as conn:  # type: ignore[attr-defined]
        row = conn.execute(
            "SELECT selection_id FROM scenario_baselines WHERE bid_id=? "
            "ORDER BY selected_at DESC,selection_id DESC LIMIT 1",
            (BID_ID,),
        ).fetchone()
    assert row is not None
    return str(row["selection_id"])


def test_temporary_level_rule_wording_is_explicit() -> None:
    root = Path(__file__).parents[2]
    expected = "temporary governance-level rule"
    assert expected in (root / "core" / "senior_approvals.py").read_text()
    assert expected in (root / "core" / "gates.py").read_text()
    assert expected in (root / "templates" / "decisions.html").read_text()
    assert (
        "value and margin thresholds are not enforced"
        in (root / "docs" / "tasks" / "OPS-13A-bid-deliverables-discovery.md").read_text()
    )
    decisions = (root / "templates" / "decisions.html").read_text()
    assert 'id="authority-policies"' in decisions
    assert "/api/authority-policies/{id}/publish" in decisions
    assert "Intake and drafting are not blocked" in decisions


def test_recorded_approval_is_server_bound_and_candidate_snapshot_keeps_baseline(
    tmp_path: Path,
) -> None:
    service, repository = service_with_ready_bid(tmp_path)
    _level(service, repository, BidLevel.LEVEL_2)
    _pursue(service)
    baseline_id = _current_baseline(service)
    repository.record_gate_approval(_approval(baseline_id=None), "Jason")
    saved = repository.list_current_approvals(BID_ID)[0]
    assert saved.pricing_baseline_id == baseline_id
    _policy(service)

    export, _ = service.export_package(BID_ID, "Jason")
    _, _, candidate_id = import_candidate(
        service, export.export_id, export.package_id, export.canonical_sha256
    )
    candidate = service.history(BID_ID).candidates[0]
    service.approve_candidate(BID_ID, candidate_id, candidate.version, "Jason")
    with service.db._conn() as conn:
        row = conn.execute(
            "SELECT pricing_baseline_id FROM proposal_candidate_approvals "
            "WHERE candidate_id=? AND approval_id=?",
            (candidate_id, saved.approval_id),
        ).fetchone()
    assert row is not None and row["pricing_baseline_id"] == baseline_id


def test_obtained_approval_is_refused_without_selected_baseline(tmp_path: Path) -> None:
    service, repository = service_with_ready_bid(tmp_path)
    with service.db._conn() as conn:
        conn.execute("DELETE FROM scenario_baselines WHERE bid_id=?", (BID_ID,))
        conn.commit()
    with pytest.raises(
        ValueError, match="Select the pricing baseline before recording a senior approval"
    ):
        repository.record_gate_approval(_approval(baseline_id=None), "Jason")
    pending = _approval(baseline_id=None).model_copy(
        update={"approval_id": "APR-PENDING", "obtained": False}
    )
    assert repository.record_gate_approval(pending, "Jason") == "created"


def test_policy_and_current_baseline_bound_evidence_are_both_required(tmp_path: Path) -> None:
    service, repository = service_with_ready_bid(tmp_path)
    _level(service, repository, BidLevel.LEVEL_2)
    _pursue(service)
    baseline_id = _current_baseline(service)
    repository.create_approval(_approval(baseline_id=baseline_id))
    without_policy = service.assess(BID_ID)
    assert not without_policy.approvals_clear
    assert any(
        "APPROVAL_POLICY_NOT_CONFIGURED" in blocker.message for blocker in without_policy.blockers
    )

    policy_only = tmp_path / "policy-only"
    policy_only.mkdir()
    service2, repository2 = service_with_ready_bid(policy_only)
    _level(service2, repository2, BidLevel.LEVEL_2)
    _pursue(service2)
    repository2.create_approval(_approval(baseline_id=None))
    _policy(service2)
    legacy = service2.assess(BID_ID)
    assert not legacy.approvals_clear
    assert any(
        "recorded before a pricing baseline was identified" in blocker.message
        for blocker in legacy.blockers
    )
    assert repository2.list_current_approvals(BID_ID)[0].pricing_baseline_id is None


def test_effective_policy_must_cover_approval_role_or_type(tmp_path: Path) -> None:
    service, repository = service_with_ready_bid(tmp_path)
    _level(service, repository, BidLevel.LEVEL_2)
    _pursue(service)
    repository.create_approval(_approval(baseline_id=_current_baseline(service)))
    _policy(service, roles=("LEGAL",))
    assessment = service.assess(BID_ID)
    assert not assessment.approvals_clear
    assert any("bid-level:margin" in blocker.message for blocker in assessment.blockers)


def test_selecting_later_baseline_makes_prior_approval_non_current(tmp_path: Path) -> None:
    service, repository = service_with_ready_bid(tmp_path)
    _level(service, repository, BidLevel.LEVEL_2)
    _pursue(service)
    first = _current_baseline(service)
    repository.create_approval(_approval(baseline_id=first))
    _policy(service)
    assert service.assess(BID_ID).approvals_clear
    with service.db._conn() as conn:
        conn.execute(
            "INSERT INTO scenario_baselines VALUES(?,?,?,?,?,?)",
            (
                "SBL-UP-LATER",
                BID_ID,
                "SV-OPS09",
                "Jason",
                (NOW + timedelta(days=1)).isoformat(),
                "A later selected baseline",
            ),
        )
        conn.commit()
    changed = service.assess(BID_ID)
    assert not changed.approvals_clear
    assert any(
        "previously selected pricing baseline" in blocker.message for blocker in changed.blockers
    )
    assert repository.list_current_approvals(BID_ID)[0].pricing_baseline_id == first


def test_qualify_and_reject_require_explicit_approver_for_senior_requirement() -> None:
    positions = [
        {"position_id": "CP-Q", "disposition": "QUALIFY", "required_approver": None},
        {"position_id": "CP-R", "disposition": "REJECT", "required_approver": ""},
    ]
    assert derive_required_approvals(BidLevel.LEVEL_1, positions, [], []) == ()
    positions[1]["required_approver"] = "Legal Director"
    requirements = derive_required_approvals(BidLevel.LEVEL_1, positions, [], [])
    assert [item.key for item in requirements] == ["commercial-role:CP-R:legal director"]


def test_missing_policy_allows_export_but_refuses_approve_issue_and_links_policy(
    tmp_path: Path,
) -> None:
    service, repository = service_with_ready_bid(tmp_path)
    _level(service, repository, BidLevel.LEVEL_2)
    _pursue(service)
    repository.record_gate_approval(_approval(baseline_id=None), "Jason")
    export, _ = service.export_package(BID_ID, "Jason")
    assert export.is_draft
    assessment = service.assess(BID_ID)
    policy_blocker = next(
        blocker
        for blocker in assessment.blockers
        if "APPROVAL_POLICY_NOT_CONFIGURED" in blocker.message
    )
    assert policy_blocker.destination == f"/decisions?bid_id={BID_ID}#authority-policies"
    _, _, candidate_id = import_candidate(
        service, export.export_id, export.package_id, export.canonical_sha256
    )
    candidate = service.history(BID_ID).candidates[0]
    with pytest.raises(ValueError, match="blocked"):
        service.approve_candidate(BID_ID, candidate_id, candidate.version, "Jason")
    command: CustomerIssueCommand = _command(candidate_id, candidate.version)
    with pytest.raises(ValueError):
        service.issue(BID_ID, command, "Jason")


def test_approval_baseline_migration_is_idempotent_and_preserves_legacy_row_hash(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-approval.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE bids(bid_id TEXT PRIMARY KEY);
            CREATE TABLE approvals(
              approval_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,approval_type TEXT NOT NULL,
              required INTEGER NOT NULL DEFAULT 1,obtained INTEGER NOT NULL DEFAULT 0,
              authority TEXT,evidence_ref TEXT,decision TEXT,decided_at TEXT,
              provenance_json TEXT NOT NULL);
            INSERT INTO bids VALUES('B-LEGACY');
            INSERT INTO approvals VALUES(
              'APR-LEGACY','B-LEGACY','margin',1,1,'Director','Email','APPROVED',
              '2025-01-01T00:00:00+00:00','{}');
            """
        )
        before = tuple(conn.execute("SELECT * FROM approvals").fetchone())
    before_hash = hashlib.sha256(repr(before).encode()).hexdigest()
    BidRepository(Database(path))
    BidRepository(Database(path))
    with sqlite3.connect(path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(approvals)")]
        after = tuple(
            conn.execute(
                "SELECT approval_id,bid_id,approval_type,required,obtained,authority,"
                "evidence_ref,decision,decided_at,provenance_json FROM approvals"
            ).fetchone()
        )
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert "pricing_baseline_id" in columns
    assert hashlib.sha256(repr(after).encode()).hexdigest() == before_hash
