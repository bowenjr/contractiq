from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from core.enums import (
    Actor,
    ApprovalType,
    Gate,
    GateStatus,
    InferencePolicy,
    NegotiationPriority,
    ObligationType,
    PillarId,
    TriggerType,
)
from core.pillars import ALL_PILLARS
from core.schemas import Approval, AuditEntry, Bid, GateRecord, Provenance


def test_every_model_instantiates_from_valid_minimal_data(
    valid_provenance: Provenance,
    valid_bid: Bid,
) -> None:
    approval = Approval(
        approval_id="A-1",
        bid_id=valid_bid.bid_id,
        approval_type=ApprovalType.BID_NO_BID,
        provenance=valid_provenance,
    )
    gate_record = GateRecord(bid_id=valid_bid.bid_id, gate=Gate.G0)
    audit_entry = AuditEntry(
        entry_id="AUD-1",
        bid_id=valid_bid.bid_id,
        actor="system",
        action="bid_created",
        detail="Bid created",
        timestamp=datetime.now(UTC),
    )

    assert valid_provenance.created_by == Actor.SYSTEM
    assert valid_bid.bid_id == "B-2026-0001"
    assert approval.required is True
    assert gate_record.status == GateStatus.NOT_STARTED
    assert audit_entry.action == "bid_created"


def test_bid_rejects_internal_due_date_after_customer_due_date(valid_bid: Bid) -> None:
    data = valid_bid.model_dump()
    data["internal_due_date"] = valid_bid.customer_due_date + timedelta(days=1)

    with pytest.raises(ValidationError):
        Bid.model_validate(data)


def test_bid_rejects_malformed_bid_id(valid_bid: Bid) -> None:
    data = valid_bid.model_dump()
    data["bid_id"] = "B-26-42"

    with pytest.raises(ValidationError):
        Bid.model_validate(data)


def test_bid_rejects_win_probability_above_100(valid_bid: Bid) -> None:
    data = valid_bid.model_dump()
    data["win_probability"] = 101

    with pytest.raises(ValidationError):
        Bid.model_validate(data)


def test_bid_defaults_to_local_only(valid_bid: Bid) -> None:
    assert valid_bid.inference_policy == InferencePolicy.LOCAL_ONLY


def test_provenance_rejects_unattributed_human_confirmation() -> None:
    with pytest.raises(ValidationError):
        Provenance(created_by=Actor.AI, human_confirmed=True, confirmed_by=None)


def test_provenance_from_ai_is_unconfirmed() -> None:
    provenance = Provenance.from_ai(
        agent_name="analysis_engine",
        model="local-model",
        source_document_id="DOC-1",
        source_location="Clause 14.2, p.31",
    )

    assert provenance.created_by == Actor.AI
    assert provenance.human_confirmed is False


def test_gate_override_requires_residual_risk_note() -> None:
    with pytest.raises(ValidationError):
        GateRecord(
            bid_id="B-2026-0001",
            gate=Gate.G1,
            status=GateStatus.OVERRIDDEN,
            override_by="jason",
        )


def test_models_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Provenance(created_by=Actor.SYSTEM, unknown_field="not allowed")


def test_pillar_id_matches_existing_pillars() -> None:
    assert {member.value for member in PillarId} == {pillar.pillar_id for pillar in ALL_PILLARS}


def test_salvaged_taxonomies_have_expected_member_counts() -> None:
    assert len(ObligationType) == 10
    assert len(TriggerType) == 7
    assert len(NegotiationPriority) == 3


def test_obligation_type_uses_short_codes_as_values() -> None:
    assert ObligationType.PERFORMANCE.value == "PERF"
