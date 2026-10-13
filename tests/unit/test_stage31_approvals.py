"""Stage 3.1 approval applicability classification and presentation coverage."""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from types import ModuleType

import pytest

from core.approval_authority import AuthorityPolicy, PolicyLifecycle, StageMode
from core.approval_repository import ApprovalRepository
from core.bid_execution_approvals import (
    ApprovalApplicability,
    ApprovalRequirementFact,
    ApprovalRequirementStatus,
)
from core.bid_execution_dashboard import ExecutionDashboardService
from core.bid_execution_readiness import ExecutionReadinessVerdict, ReadinessSeverity
from core.enums import ApprovalType, BidLevel, Gate, GateStatus
from core.ops08 import PositionRevision
from core.schemas import Approval, Bid, GateRecord, Provenance
from tests.unit.test_stage3_readiness import (
    AS_OF,
    NOW,
    ServiceContext,
)
from tests.unit.test_stage3_readiness import service_context as _service_context_fixture
from tests.unit.test_stage3_ui_forms import _get
from tests.unit.test_stage3_ui_forms import forms_app as _forms_app_fixture


@pytest.fixture
def approval_context(tmp_db: object, bid_repo: object, valid_bid: Bid) -> ServiceContext:
    """Reuse the Stage 3 readiness service fixture under a Unit B-specific name."""
    return _service_context_fixture.__wrapped__(tmp_db, bid_repo, valid_bid)


@pytest.fixture
def approvals_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid_bid: Bid
) -> tuple[ModuleType, dict[str, str]]:
    """Reuse the socketless Stage 3 application fixture for approval page coverage."""
    return _forms_app_fixture.__wrapped__(tmp_path, monkeypatch, valid_bid)


def _set_level(context: ServiceContext, level: BidLevel) -> None:
    bid = context.bids.get_bid(context.bid.bid_id)
    assert bid is not None
    context.bids.update_bid(bid.model_copy(update={"classification": level}))


def _approval_facts(context: ServiceContext) -> list[ApprovalRequirementFact]:
    return context.readiness.facts(context.bid.bid_id, AS_OF).approvals


def _baseline(context: ServiceContext, baseline_id: str = "SBL-STAGE31") -> str:
    with context.db._conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS scenario_baselines(
            selection_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,scenario_version_id TEXT NOT NULL,
            selected_by TEXT NOT NULL,selected_at TEXT NOT NULL,rationale TEXT NOT NULL)"""
        )
        conn.execute(
            "INSERT INTO scenario_baselines VALUES(?,?,?,?,?,?)",
            (
                baseline_id,
                context.bid.bid_id,
                "SV-STAGE31",
                "Jason",
                NOW.isoformat(),
                "Accepted test pricing basis",
            ),
        )
        conn.commit()
    return baseline_id


def _policy(context: ServiceContext, *roles: str) -> None:
    policy = AuthorityPolicy(
        policy_id="POL-STAGE31",
        name="Stage 3.1 authority",
        description="Recorded authority used by the approval classification tests.",
        scope="BID",
        effective_from=NOW - timedelta(days=1),
        lifecycle_state=PolicyLifecycle.PUBLISHED,
        roles=roles,
        rules=({"rule_id": "R-STAGE31", "dimensions": {}},),
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
    ApprovalRepository(context.db).create_policy(policy, "Jason")


def _record_approval(
    context: ServiceContext,
    approval_type: ApprovalType,
    *,
    obtained: bool,
    baseline_id: str | None = None,
) -> None:
    context.bids.create_approval(
        Approval(
            approval_id=f"APR-STAGE31-{approval_type.value}",
            bid_id=context.bid.bid_id,
            approval_type=approval_type,
            required=True,
            obtained=obtained,
            authority=f"{approval_type.value.title()} Director" if obtained else None,
            evidence_ref="Controlled decision record" if obtained else None,
            decision="APPROVED" if obtained else "PENDING",
            decided_at=NOW if obtained else None,
            provenance=Provenance.from_human("Jason"),
            pricing_baseline_id=baseline_id,
        )
    )


def test_level_four_without_policy_or_baseline_separates_rule_from_assessment(
    approval_context: ServiceContext,
) -> None:
    _set_level(approval_context, BidLevel.LEVEL_4)

    report = approval_context.readiness.evaluate(approval_context.bid.bid_id, AS_OF)
    margin = next(item for item in report.approvals if item.requirement_key == "bid-level:margin")
    assessment = next(
        item
        for item in report.approvals
        if item.classification is ApprovalApplicability.NEEDS_ASSESSMENT
    )
    findings = {item.code: item for item in report.findings}

    assert margin.classification is ApprovalApplicability.ROUTINE_AUTHORIZATION_REQUIRED
    assert margin.status is ApprovalRequirementStatus.MISSING
    assert margin.establishing_basis == "Level 4 governance rule"
    assert findings["approval:bid-level:margin"].severity is ReadinessSeverity.BLOCKING
    assert assessment.heading == "Needs assessment"
    assert "No authority policy or accepted pricing basis" in assessment.detail
    assert findings["approval:senior-approval:configuration"].severity is ReadinessSeverity.WARNING
    wording = report.model_dump_json()
    assert "President" not in wording and "CEO" not in wording


@pytest.mark.parametrize("level", [BidLevel.LEVEL_0, BidLevel.LEVEL_1])
def test_low_level_without_recorded_requirement_is_not_applicable_information(
    approval_context: ServiceContext,
    level: BidLevel,
) -> None:
    _set_level(approval_context, level)

    report = approval_context.readiness.evaluate(approval_context.bid.bid_id, AS_OF)

    assert len(report.approvals) == 1
    assert report.approvals[0].classification is ApprovalApplicability.NOT_APPLICABLE
    assert report.approvals[0].heading == "No additional approval applies"
    assert next(item for item in report.findings if item.code.startswith("approval:")).severity is (
        ReadinessSeverity.INFO
    )


def test_commercial_required_approver_is_deviation_and_recorded_outcome_completes_it(
    approval_context: ServiceContext,
) -> None:
    _set_level(approval_context, BidLevel.LEVEL_1)
    position_id = approval_context.ops08.ensure_position(
        approval_context.bid.bid_id, "PAYMENT_TERMS", "Commercial lead", "Jason"
    )
    current = approval_context.ops08.detail(position_id)
    approval_context.ops08.revise(
        position_id,
        int(current["current_version"]),
        PositionRevision(owner="Commercial lead", required_approver="Regional Legal Director"),
        "Jason",
    )

    before = next(
        item
        for item in _approval_facts(approval_context)
        if item.requirement_key.startswith("commercial-role:")
    )
    assert before.classification is ApprovalApplicability.DEVIATION_APPROVAL_REQUIRED
    assert before.status is ApprovalRequirementStatus.MISSING
    assert before.heading == "Approval required from Regional Legal Director"

    current = approval_context.ops08.detail(position_id)
    approval_context.ops08.revise(
        position_id,
        int(current["current_version"]),
        PositionRevision(
            owner="Commercial lead",
            required_approver="Regional Legal Director",
            current_outcome="Regional Legal Director accepted the exception",
        ),
        "Jason",
    )
    after = next(
        item
        for item in _approval_facts(approval_context)
        if item.requirement_key.startswith("commercial-role:")
    )
    assert after.classification is ApprovalApplicability.COMPLETED
    assert after.status is ApprovalRequirementStatus.OBTAINED


def test_required_approval_row_is_routine_and_evidence_bound_outcome_is_completed(
    approval_context: ServiceContext,
) -> None:
    _set_level(approval_context, BidLevel.LEVEL_1)
    _record_approval(approval_context, ApprovalType.LEGAL, obtained=False)
    missing = next(
        item
        for item in _approval_facts(approval_context)
        if item.requirement_key == "approval-type:legal"
    )
    assert missing.classification is ApprovalApplicability.ROUTINE_AUTHORIZATION_REQUIRED
    assert missing.status is ApprovalRequirementStatus.MISSING

    baseline_id = _baseline(approval_context)
    _policy(approval_context, "LEGAL")
    pending = approval_context.bids.list_current_approvals(approval_context.bid.bid_id)[0]
    approval_context.bids.record_gate_approval(
        pending.model_copy(
            update={
                "approval_id": "APR-STAGE31-legal-obtained",
                "obtained": True,
                "authority": "Legal Director",
                "evidence_ref": "Controlled decision record",
                "decision": "APPROVED",
                "decided_at": NOW,
                "pricing_baseline_id": baseline_id,
            }
        ),
        "Jason",
        pending.approval_id,
    )
    report = approval_context.readiness.evaluate(approval_context.bid.bid_id, AS_OF)
    completed = next(
        item for item in report.approvals if item.requirement_key == "approval-type:legal"
    )
    finding = next(item for item in report.findings if item.code == "approval:approval-type:legal")
    assert completed.classification is ApprovalApplicability.COMPLETED
    assert completed.status is ApprovalRequirementStatus.OBTAINED
    assert finding.severity is ReadinessSeverity.INFO


def test_open_approval_route_is_a_deviation_blocker(approval_context: ServiceContext) -> None:
    _set_level(approval_context, BidLevel.LEVEL_1)
    _baseline(approval_context)
    _policy(approval_context, "Recorded Review Role")
    with approval_context.db._conn() as conn:
        conn.execute(
            """INSERT INTO approval_routes(
            route_id,case_id,bid_id,package_id,policy_id,state,matched_rules_json,
            requirements_json,requestor,submitted_at,approval_valid_until,version
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "ROUTE-STAGE31",
                "CASE-STAGE31",
                approval_context.bid.bid_id,
                "PACKAGE-STAGE31",
                "POL-STAGE31",
                "PENDING",
                "[]",
                '[{"role_code":"Recorded Review Role"}]',
                "Jason",
                NOW.isoformat(),
                None,
                1,
            ),
        )
        conn.commit()

    report = approval_context.readiness.evaluate(approval_context.bid.bid_id, AS_OF)
    route = next(
        item for item in report.approvals if item.requirement_key == "approval-route:ROUTE-STAGE31"
    )
    finding = next(item for item in report.findings if item.code.endswith("ROUTE-STAGE31"))
    assert route.classification is ApprovalApplicability.DEVIATION_APPROVAL_REQUIRED
    assert route.status is ApprovalRequirementStatus.MISSING
    assert route.heading == "Approval required from Recorded Review Role"
    assert finding.severity is ReadinessSeverity.BLOCKING


def test_confirmed_high_finding_without_authority_is_a_deviation_blocker(
    approval_context: ServiceContext,
) -> None:
    _set_level(approval_context, BidLevel.LEVEL_1)
    approval_context.db.create_document({"id": "DOC-STAGE31", "filename": "contract.pdf"})
    approval_context.bids.attach_document_to_bid("DOC-STAGE31", approval_context.bid.bid_id)
    approval_context.db.save_clause_findings(
        "DOC-STAGE31",
        [
            {
                "pillar_id": "risk_liability",
                "findings": [
                    {
                        "finding": "Uncapped liability",
                        "severity": "High",
                        "detail": "Confirmed exception needs authority.",
                    }
                ],
            }
        ],
        Provenance.from_human("Jason"),
    )
    finding_id = int(approval_context.db.get_clause_findings("DOC-STAGE31")[0]["id"])
    approval_context.db.confirm_clause_finding(finding_id, "Jason")

    report = approval_context.readiness.evaluate(approval_context.bid.bid_id, AS_OF)
    authority = next(
        item
        for item in report.approvals
        if item.requirement_key == "g4.high_findings_have_authority"
    )
    finding = next(
        item for item in report.findings if item.code == "approval:g4.high_findings_have_authority"
    )
    assert authority.classification is ApprovalApplicability.DEVIATION_APPROVAL_REQUIRED
    assert finding.severity is ReadinessSeverity.BLOCKING


def test_overridden_approval_condition_is_completed_information(
    approval_context: ServiceContext,
) -> None:
    _set_level(approval_context, BidLevel.LEVEL_2)
    approval_context.bids.upsert_gate_record(
        GateRecord(
            bid_id=approval_context.bid.bid_id,
            gate=Gate.G4,
            status=GateStatus.OVERRIDDEN,
            blockers=["g4.margin_approved"],
            override_by="Recorded authority",
            override_risk_note="Existing approved override",
            decided_at=NOW,
        )
    )

    report = approval_context.readiness.evaluate(approval_context.bid.bid_id, AS_OF)
    margin = next(item for item in report.approvals if item.requirement_key == "bid-level:margin")
    finding = next(item for item in report.findings if item.code == "approval:bid-level:margin")
    assert margin.classification is ApprovalApplicability.COMPLETED
    assert margin.status is ApprovalRequirementStatus.COMPLETED_BY_OVERRIDE
    assert finding.severity is ReadinessSeverity.INFO


def test_recording_margin_approval_changes_blocking_count_and_verdict(
    approval_context: ServiceContext,
) -> None:
    _set_level(approval_context, BidLevel.LEVEL_2)
    baseline_id = _baseline(approval_context)
    _policy(approval_context, "MARGIN")
    before = approval_context.readiness.evaluate(approval_context.bid.bid_id, AS_OF)

    _record_approval(
        approval_context,
        ApprovalType.MARGIN,
        obtained=True,
        baseline_id=baseline_id,
    )
    after = approval_context.readiness.evaluate(approval_context.bid.bid_id, AS_OF)

    assert before.blocking_count == 1
    assert before.verdict is ExecutionReadinessVerdict.NOT_READY
    assert after.blocking_count == 0
    assert after.verdict is ExecutionReadinessVerdict.READY_FOR_REVIEW
    assert next(
        item for item in after.approvals if item.requirement_key == "bid-level:margin"
    ).status is (ApprovalRequirementStatus.OBTAINED)


def test_evaluation_does_not_write_registers_audit_or_authority_state(
    approval_context: ServiceContext,
) -> None:
    ApprovalRepository(approval_context.db)
    watched = (
        "approvals",
        "approval_routes",
        "approval_events",
        "commercial_positions",
        "commercial_position_versions",
        "audit_log",
    )

    def counts() -> tuple[int, ...]:
        with approval_context.db._conn() as conn:
            return tuple(
                int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                for table in watched
            )

    before = counts()
    first = approval_context.readiness.evaluate(approval_context.bid.bid_id, AS_OF)
    second = approval_context.readiness.evaluate(approval_context.bid.bid_id, AS_OF)

    assert first == second
    assert counts() == before
    assert approval_context.bids.list_current_approvals(approval_context.bid.bid_id) == []


def test_readiness_page_has_advisory_approvals_section_without_authorizing_controls(
    approvals_app: tuple[ModuleType, dict[str, str]],
) -> None:
    module, ids = approvals_app
    status, body = _get(module, f"/bids/{ids['bid']}/execution/readiness")

    assert status == 200
    assert "<h2>Approvals" in body
    assert "Routine authorization required" in body
    assert "Needs assessment" in body
    assert "<form" not in body
    control_tags = re.findall(r"<(?:button|input)[^>]*>", body, flags=re.IGNORECASE)
    assert not any(
        word in tag.casefold()
        for tag in control_tags
        for word in ("approve", "submit", "authorize")
    )


def test_dashboard_readiness_tile_matches_evaluator_blocking_list(
    approval_context: ServiceContext,
) -> None:
    _set_level(approval_context, BidLevel.LEVEL_4)
    report = approval_context.readiness.evaluate(approval_context.bid.bid_id, AS_OF)
    dashboard_service = ExecutionDashboardService(
        approval_context.dashboard.projection,
        approval_context.bids,
        approval_context.deliverables,
        approval_context.readiness,
    )
    tile = next(
        item
        for item in dashboard_service.dashboard(approval_context.bid.bid_id, AS_OF).tiles
        if item.key == "readiness"
    )
    blocking = [item for item in report.findings if item.severity is ReadinessSeverity.BLOCKING]

    assert tile.blocking_count == report.blocking_count == len(blocking)
    assert tile.verdict == report.verdict_label
