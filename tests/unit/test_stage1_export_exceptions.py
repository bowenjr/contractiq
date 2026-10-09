"""Stage 1 / Fix 3 and Fix 5: approved exceptions do not block export and are carried in the
package; unresolved ones still block; the closing instant is exported only when it is recorded."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, time
from pathlib import Path

import pytest

from core.proposal_exchange import ProposalControlStatus
from core.schemas import Provenance
from tests.unit.test_ops09 import BID_ID, NOW, service_with_ready_bid

SUFFIX = BID_ID[-4:]


def _add_manufacturer_row(
    service,
    *,
    status: str,
    disposition: str = "NONE",
    approved: int = 0,
    exception: str | None = "Delivery 12 weeks, not 8",
) -> str:
    """Insert one manufacturer package with one VDRL requirement. Returns the requirement id."""
    stamp = NOW.isoformat()
    provenance = Provenance.from_human("Jason").model_dump_json()
    package_id = f"VBP-{SUFFIX}"
    requirement_id = f"VBR-{SUFFIX}"
    with sqlite3.connect(service.db.db_path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            """INSERT OR IGNORE INTO vendor_vdrl_templates
            VALUES('TPL-1','Standard',1,'[]',?,?)""",
            (provenance, stamp),
        )
        conn.execute(
            """INSERT INTO vendor_bid_packages(package_id,bid_id,package_name,package_code,
            proposed_manufacturer,internal_owner,template_id,template_version,version_token,
            provenance_json,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                package_id,
                BID_ID,
                "Switchgear",
                "SWG",
                "Acme Switchgear",
                "Jason",
                "TPL-1",
                1,
                "tok-1",
                provenance,
                stamp,
                stamp,
            ),
        )
        conn.execute(
            """INSERT INTO vendor_bid_requirements(requirement_id,package_id,
            customer_requirement_code,deliverable_title,description,required,requested_stages_json,
            timing_anchor,timing_offset_days,applicable,verification_status,response_source,
            internal_owner,response_received_date,committed_stages_json,proposed_exception,
            commercial_impact,bid_disposition,disposition_approved,version,provenance_json,
            created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                requirement_id,
                package_id,
                "VDRL-001",
                "Delivery schedule",
                "Deliver within the schedule",
                1,
                "[]",
                "ANTICIPATED_AWARD",
                0,
                1,
                status,
                "Manufacturer letter",
                "Jason",
                "2026-09-05",
                "[]",
                exception,
                "NONE",
                disposition,
                approved,
                1,
                provenance,
                stamp,
                stamp,
            ),
        )
        conn.commit()
    return requirement_id


def _codes(service) -> set[str]:
    return {blocker.code for blocker in service.assess(BID_ID).blockers}


def _package(record) -> dict:
    return json.loads(record.canonical_package_json)


def test_baseline_fixture_is_ready(tmp_path: Path) -> None:
    service, _ = service_with_ready_bid(tmp_path)
    assert service.assess(BID_ID).status is ProposalControlStatus.READY_FOR_EXPORT


def test_approved_manufacturer_exception_does_not_block_and_is_in_the_package(
    tmp_path: Path,
) -> None:
    service, _ = service_with_ready_bid(tmp_path)
    requirement_id = _add_manufacturer_row(
        service, status="CONFIRMED_WITH_EXCEPTION", disposition="ACCEPTED", approved=1
    )

    assessment = service.assess(BID_ID)
    assert "MANUFACTURER_EXCEPTION_UNDISPOSED" not in {b.code for b in assessment.blockers}
    assert assessment.status is ProposalControlStatus.READY_FOR_EXPORT

    record, _ = service.export_package(BID_ID, "Jason")
    items = [
        item
        for item in _package(record)["qualifications_and_deviations"]
        if item["item_id"] == f"QD-{requirement_id}"
    ]
    assert len(items) == 1
    assert items[0]["status"] == "APPROVED"
    assert items[0]["kind"] == "DEVIATION"
    assert "Delivery 12 weeks, not 8" in items[0]["text"]


def test_unresolved_manufacturer_exception_still_blocks_export(tmp_path: Path) -> None:
    service, _ = service_with_ready_bid(tmp_path)
    _add_manufacturer_row(service, status="CONFIRMED_WITH_EXCEPTION")

    assert "MANUFACTURER_EXCEPTION_UNDISPOSED" in _codes(service)
    assert service.assess(BID_ID).status is not ProposalControlStatus.READY_FOR_EXPORT


def test_disposition_recorded_but_not_approved_still_blocks(tmp_path: Path) -> None:
    service, _ = service_with_ready_bid(tmp_path)
    _add_manufacturer_row(
        service, status="CONFIRMED_WITH_EXCEPTION", disposition="ACCEPTED", approved=0
    )
    assert "MANUFACTURER_EXCEPTION_UNDISPOSED" in _codes(service)


def test_cannot_comply_with_approved_disposition_is_resolved_but_other_gaps_still_block(
    tmp_path: Path,
) -> None:
    service, _ = service_with_ready_bid(tmp_path)
    _add_manufacturer_row(service, status="CANNOT_COMPLY", disposition="MODIFIED", approved=1)
    assert "MANUFACTURER_EXCEPTION_UNDISPOSED" not in _codes(service)


def test_a_missing_manufacturer_response_still_blocks(tmp_path: Path) -> None:
    service, _ = service_with_ready_bid(tmp_path)
    _add_manufacturer_row(service, status="AWAITING_MANUFACTURER", exception=None)
    assert "MANUFACTURER_RESPONSE_BLOCKER" in _codes(service)


def _set_position(service, disposition: str, negotiation_state: str) -> None:
    """Append a new position version (versions are immutable) and make it current."""
    with sqlite3.connect(service.db.db_path) as conn:
        conn.execute(
            """INSERT INTO commercial_position_versions(position_version_id,position_id,bid_id,
            topic_version_id,version_number,customer_position,proposed_position,disposition,
            rationale,owner,contributor,reviewer,negotiation_state,supporting_evidence,
            current_outcome,provenance_json,created_at,created_by)
            SELECT position_version_id||'-v2',position_id,bid_id,topic_version_id,2,
            customer_position,proposed_position,?,rationale,owner,contributor,reviewer,?,
            supporting_evidence,current_outcome,provenance_json,created_at,created_by
            FROM commercial_position_versions WHERE version_number=1""",
            (disposition, negotiation_state),
        )
        conn.execute("UPDATE commercial_positions SET current_version=2")
        conn.commit()


def test_resolved_commercial_qualification_exports_as_an_approved_qualification(
    tmp_path: Path,
) -> None:
    service, _ = service_with_ready_bid(tmp_path)
    _set_position(service, "QUALIFY", "RESOLVED")

    assert service.assess(BID_ID).status is ProposalControlStatus.READY_FOR_EXPORT
    record, _ = service.export_package(BID_ID, "Jason")
    package = _package(record)
    positions = package["commercial_positions"]
    assert positions and positions[0]["status"] == "CONDITIONAL"
    sources = {item["source"]["source_type"] for item in package["qualifications_and_deviations"]}
    assert "commercial_position" in sources


def test_unresolved_commercial_qualification_still_blocks(tmp_path: Path) -> None:
    service, _ = service_with_ready_bid(tmp_path)
    _set_position(service, "QUALIFY", "OPEN")
    assert "UNRESOLVED_COMMERCIAL_POSITION" in _codes(service)


def test_export_validation_is_not_removed_for_rejected_positions(tmp_path: Path) -> None:
    service, _ = service_with_ready_bid(tmp_path)
    _set_position(service, "REJECT", "RESOLVED")
    assert "UNRESOLVED_COMMERCIAL_POSITION" in _codes(service)


def test_due_at_is_null_when_the_closing_time_is_not_recorded(tmp_path: Path) -> None:
    service, _ = service_with_ready_bid(tmp_path)
    record, _ = service.export_package(BID_ID, "Jason")
    package = _package(record)
    assert package["opportunity"]["due_at"] is None
    assert "23:59:59" not in record.canonical_package_json


def test_due_at_is_the_recorded_closing_instant_in_utc(tmp_path: Path) -> None:
    service, bids = service_with_ready_bid(tmp_path)
    # Seeded closing date is 2026-10-01; Toronto is on daylight time (UTC-4) that day.
    bids.record_customer_closing(BID_ID, time(12, 0), "America/Toronto", "Jason", "RFP p.3")
    record, _ = service.export_package(BID_ID, "Jason")
    assert _package(record)["opportunity"]["due_at"] == "2026-10-01T16:00:00Z"


def test_recording_the_closing_time_makes_an_existing_export_stale(tmp_path: Path) -> None:
    service, bids = service_with_ready_bid(tmp_path)
    first, _ = service.export_package(BID_ID, "Jason")
    bids.record_customer_closing(BID_ID, time(12, 0), "America/Toronto", "Jason", "RFP p.3")
    assert service.assess(BID_ID).status is ProposalControlStatus.STALE
    second, replayed = service.export_package(BID_ID, "Jason")
    assert not replayed and second.supersedes_export_id == first.export_id
    assert (
        datetime.fromisoformat(_package(second)["opportunity"]["due_at"]).astimezone(UTC).hour == 16
    )


@pytest.mark.parametrize("bad_time", [None])
def test_record_closing_rejects_a_time_without_a_zone(tmp_path: Path, bad_time: None) -> None:
    _, bids = service_with_ready_bid(tmp_path)
    with pytest.raises(ValueError, match="together"):
        bids.record_customer_closing(BID_ID, time(12, 0), None, "Jason", "RFP")
    with pytest.raises(ValueError, match="unknown time zone"):
        bids.record_customer_closing(BID_ID, time(12, 0), "Mars/Olympus", "Jason", "RFP")
