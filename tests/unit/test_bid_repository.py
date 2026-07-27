from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from core.bid_repository import BidRepository
from core.database import Database
from core.enums import (
    Actor,
    ApprovalType,
    BidStatus,
    CustomerType,
    Gate,
    GateStatus,
    InferencePolicy,
    RiskTrigger,
)
from core.schemas import Approval, AuditEntry, Bid, GateRecord, Provenance


def test_create_and_get_bid_round_trips_every_field(
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid = valid_bid.model_copy(
        update={
            "customer": "Precision EPC",
            "customer_type": CustomerType.EPCM,
            "location": "Toronto, ON",
            "executive_sponsor": "Executive Sponsor",
            "anticipated_award_date": date(2026, 9, 15),
            "estimated_value": Decimal("1234567.89"),
            "currency": "USD",
            "margin_range": "18-22%",
            "win_probability": 73,
            "risk_triggers": [
                RiskTrigger.NON_STANDARD_TERMS,
                RiskTrigger.LIQUIDATED_DAMAGES,
            ],
            "inference_policy": InferencePolicy.CLOUD_OK,
        }
    )

    bid_repo.create_bid(bid)

    loaded = bid_repo.get_bid(bid.bid_id)
    assert loaded == bid
    assert loaded is not None
    assert loaded.estimated_value == Decimal("1234567.89")
    assert not isinstance(loaded.estimated_value, float)


def test_get_unknown_bid_returns_none(bid_repo: BidRepository) -> None:
    assert bid_repo.get_bid("B-2026-9999") is None


def test_create_duplicate_bid_raises_value_error(
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)

    with pytest.raises(ValueError, match="Bid already exists"):
        bid_repo.create_bid(valid_bid)


def test_list_bids_returns_all_and_filters_by_status(
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    held_bid = valid_bid.model_copy(update={"bid_id": "B-2026-0002", "status": BidStatus.HELD})
    bid_repo.create_bid(valid_bid)
    bid_repo.create_bid(held_bid)

    assert {bid.bid_id for bid in bid_repo.list_bids()} == {
        valid_bid.bid_id,
        held_bid.bid_id,
    }
    assert bid_repo.list_bids(status=BidStatus.ACTIVE) == [valid_bid]


def test_update_bid_changes_field_and_bumps_updated_at(
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    old_updated_at = datetime(2020, 1, 1, tzinfo=UTC)
    original = valid_bid.model_copy(update={"updated_at": old_updated_at})
    bid_repo.create_bid(original)

    bid_repo.update_bid(original.model_copy(update={"project_name": "Updated Project"}))

    loaded = bid_repo.get_bid(original.bid_id)
    assert loaded is not None
    assert loaded.project_name == "Updated Project"
    assert loaded.updated_at > old_updated_at


def test_update_bid_upserts_when_bid_does_not_exist(
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.update_bid(valid_bid)

    assert bid_repo.bid_exists(valid_bid.bid_id) is True
    loaded = bid_repo.get_bid(valid_bid.bid_id)
    assert loaded is not None
    assert loaded.updated_at >= valid_bid.updated_at


def test_attach_list_and_detach_document(
    bid_repo: BidRepository,
    tmp_db: Database,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    tmp_db.create_document({"id": "DOC-1", "filename": "bid-document.pdf"})

    bid_repo.attach_document_to_bid("DOC-1", valid_bid.bid_id)

    documents = bid_repo.list_documents_for_bid(valid_bid.bid_id)
    assert len(documents) == 1
    assert documents[0]["id"] == "DOC-1"
    assert documents[0]["bid_id"] == valid_bid.bid_id

    bid_repo.detach_document("DOC-1")

    document = tmp_db.get_document("DOC-1")
    assert document is not None
    assert document["bid_id"] is None
    assert bid_repo.list_documents_for_bid(valid_bid.bid_id) == []


def test_existing_create_document_path_defaults_bid_id_to_null(
    bid_repo: BidRepository,
    tmp_db: Database,
) -> None:
    tmp_db.create_document({"id": "DOC-2", "filename": "standalone.pdf"})

    document = tmp_db.get_document("DOC-2")
    assert document is not None
    assert document["filename"] == "standalone.pdf"
    assert document["bid_id"] is None


def test_approval_round_trips_provenance(
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    approval = Approval(
        approval_id="APP-1",
        bid_id=valid_bid.bid_id,
        approval_type=ApprovalType.LEGAL,
        obtained=True,
        authority="Legal Counsel",
        decision="Approved",
        decided_at=datetime(2026, 7, 20, 14, 30, tzinfo=UTC),
        provenance=Provenance.from_human("jason"),
    )

    bid_repo.create_approval(approval)

    loaded = bid_repo.list_approvals(valid_bid.bid_id)
    assert loaded == [approval]
    assert loaded[0].provenance.human_confirmed is True
    assert loaded[0].provenance.created_by == Actor.HUMAN


def test_update_approval_persists_full_model(
    bid_repo: BidRepository,
    valid_bid: Bid,
    valid_provenance: Provenance,
) -> None:
    bid_repo.create_bid(valid_bid)
    approval = Approval(
        approval_id="APP-2",
        bid_id=valid_bid.bid_id,
        approval_type=ApprovalType.MARGIN,
        provenance=valid_provenance,
    )
    bid_repo.create_approval(approval)
    updated = approval.model_copy(
        update={
            "obtained": True,
            "authority": "Finance Director",
            "decision": "Approved at 18% margin",
            "decided_at": datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
        }
    )

    bid_repo.update_approval(updated)

    assert bid_repo.list_approvals(valid_bid.bid_id) == [updated]


def test_upsert_gate_record_updates_without_duplicate(
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    bid_repo.upsert_gate_record(
        GateRecord(
            bid_id=valid_bid.bid_id,
            gate=Gate.G1,
            status=GateStatus.IN_REVIEW,
            blockers=["Legal review"],
        )
    )
    updated = GateRecord(
        bid_id=valid_bid.bid_id,
        gate=Gate.G1,
        status=GateStatus.PASSED,
        blockers=[],
        decided_at=datetime(2026, 7, 21, tzinfo=UTC),
    )

    bid_repo.upsert_gate_record(updated)

    assert bid_repo.get_gate_record(valid_bid.bid_id, Gate.G1) == updated
    assert bid_repo.list_gate_records(valid_bid.bid_id) == [updated]


def test_overridden_gate_round_trips_residual_risk_note(
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    record = GateRecord(
        bid_id=valid_bid.bid_id,
        gate=Gate.G2,
        status=GateStatus.OVERRIDDEN,
        override_by="jason",
        override_risk_note="Proceed with capped commercial exposure.",
        decided_at=datetime(2026, 7, 22, tzinfo=UTC),
    )

    bid_repo.upsert_gate_record(record)

    loaded = bid_repo.get_gate_record(valid_bid.bid_id, Gate.G2)
    assert loaded == record
    assert loaded is not None
    assert loaded.override_risk_note == record.override_risk_note


def test_append_and_list_audit_with_optional_bid_filter(
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    bid_entry = AuditEntry(
        entry_id="AUD-1",
        bid_id=valid_bid.bid_id,
        actor="jason",
        action="bid_created",
        detail="Created bid",
        timestamp=datetime(2026, 7, 23, 10, 0, tzinfo=UTC),
    )
    global_entry = AuditEntry(
        entry_id="AUD-2",
        bid_id=None,
        actor="system",
        action="migration",
        detail="Bid schema evolved",
        timestamp=datetime(2026, 7, 23, 10, 1, tzinfo=UTC),
    )

    bid_repo.append_audit(bid_entry)
    bid_repo.append_audit(global_entry)

    assert bid_repo.list_audit(bid_id=valid_bid.bid_id) == [bid_entry]
    assert bid_repo.list_audit() == [bid_entry, global_entry]


def test_schema_evolution_is_idempotent_and_bid_id_is_nullable_once(
    bid_repo: BidRepository,
    tmp_db: Database,
) -> None:
    second_repository = BidRepository(tmp_db)

    with tmp_db._conn() as conn:
        bid_id_columns = [
            dict(row)
            for row in conn.execute("PRAGMA table_info(documents)").fetchall()
            if row["name"] == "bid_id"
        ]

    assert isinstance(second_repository, BidRepository)
    assert len(bid_id_columns) == 1
    assert bid_id_columns[0]["notnull"] == 0
