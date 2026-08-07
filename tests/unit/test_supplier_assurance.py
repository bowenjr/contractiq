"""Focused TASK-11C supplier assurance integration tests."""
# Long SQL fixtures mirror the authoritative schema columns.
# ruff: noqa: E501

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from core.bid_repository import BidRepository
from core.database import Database
from core.enums import BidLevel, CustomerType
from core.schemas import Bid, Provenance
from core.scope_repository import ScopeInterfaceRepository
from core.supplier_assurance import (
    Coverage,
    CoverageState,
    EvidenceMode,
    FlowDownLink,
    FlowDownTargetType,
    RequestItem,
    RequestType,
    ResponseVersion,
    ReviewState,
    Supplier,
    SupplierRequest,
    SupportRole,
    Topic,
)
from core.supplier_assurance_rules import calculate_gaps
from core.supplier_service import SupplierService


def _service(tmp_path: Path) -> tuple[SupplierService, str, Supplier, SupplierRequest, RequestItem]:
    db = Database(tmp_path / "supplier.db")
    bids = BidRepository(db)
    now = datetime.now(UTC)
    bid_id = "B-2026-0001"
    bids.create_bid(
        Bid(
            bid_id=bid_id,
            customer="Synthetic customer",
            customer_type=CustomerType.EPC,
            project_name="Synthetic project",
            sales_owner="sales",
            bc_owner="bc",
            release_date=now.date(),
            customer_due_date=now.date(),
            internal_due_date=now.date(),
            estimated_value=Decimal("1"),
            classification=BidLevel.LEVEL_0,
            created_at=now,
            updated_at=now,
        )
    )
    service = SupplierService(db)
    ScopeInterfaceRepository(db)
    provenance = Provenance.from_human("tester")
    supplier = Supplier(
        bid_id=bid_id,
        supplier_name="Synthetic supplier",
        provenance=provenance,
        created_at=now,
        updated_at=now,
        created_by="tester",
    )
    service.create_supplier(supplier)
    request = SupplierRequest(
        bid_id=bid_id,
        supplier_id=supplier.supplier_id,
        request_type=RequestType.REQUEST_FOR_QUOTE,
        title="Synthetic RFQ",
        purpose="Synthetic purpose",
        owner="owner",
        provenance=provenance,
        created_at=now,
        updated_at=now,
        created_by="tester",
    )
    item = RequestItem(
        request_id=request.request_id,
        bid_id=bid_id,
        sequence=1,
        title="Synthetic scope",
        confirmation_text="Confirm supply",
        topic=Topic.SCOPE_SUPPLY,
        support_role=SupportRole.REQUIRED_SUPPORT,
    )
    service.create_request(request, [item])
    return service, bid_id, supplier, request, item


def test_explicit_flow_down_locks_issue_and_response_silence(tmp_path: Path) -> None:
    service, bid_id, supplier, request, item = _service(tmp_path)
    now = datetime.now(UTC)
    with service.db._conn() as conn:
        conn.execute(
            "INSERT INTO scope_interface_items(scope_item_id,bid_id,title,description,scope_area,origin,customer_need,offer_position,pricing_state,responsible_party,owner,due_date,materiality,assumption_exclusion_note,evidence_decision_note,work_state,review_state,reviewer,review_note,lifecycle_state,created_at,updated_at,version,provenance_json,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "SCOPE-1",
                bid_id,
                "Synthetic scope",
                "Description",
                "EQUIPMENT",
                "INTERNAL",
                "REQUIRED",
                "INCLUDED",
                "UNASSESSED",
                "SUPPLIER",
                "owner",
                None,
                "MATERIAL",
                None,
                None,
                "NOT_STARTED",
                "NOT_REVIEWED",
                None,
                None,
                "ACTIVE",
                now.isoformat(),
                now.isoformat(),
                1,
                "{}",
                "tester",
            ),
        )
        conn.commit()
    service.add_flow_down(
        FlowDownLink(
            request_item_id=item.request_item_id,
            bid_id=bid_id,
            target_type=FlowDownTargetType.SCOPE_ITEM,
            target_id="SCOPE-1",
            created_at=now,
            created_by="tester",
        )
    )
    service.issue_request(request.request_id, 1)
    with pytest.raises(ValueError, match="immutable"):
        service.add_flow_down(
            FlowDownLink(
                request_item_id=item.request_item_id,
                bid_id=bid_id,
                target_type=FlowDownTargetType.SCOPE_ITEM,
                target_id="SCOPE-1",
                created_at=now,
                created_by="tester",
            )
        )
    response = ResponseVersion(
        response_id="RESP-1",
        request_id=request.request_id,
        supplier_id=supplier.supplier_id,
        bid_id=bid_id,
        version_number=1,
        received_at=now,
        evidence_mode=EvidenceMode.MANUAL_RECORD,
        evidence_note="Synthetic email locator",
        created_at=now,
        created_by="tester",
    )
    service.create_response(response, [])
    with service.db._conn() as conn:
        row = conn.execute("SELECT state FROM supplier_response_coverage").fetchone()
    assert row["state"] == CoverageState.SILENT.value


def test_review_acceptance_and_exception_remain_visible(tmp_path: Path) -> None:
    service, bid_id, supplier, request, item = _service(tmp_path)
    now = datetime.now(UTC)
    with service.db._conn() as conn:
        conn.execute(
            "INSERT INTO scope_interface_items(scope_item_id,bid_id,title,description,scope_area,origin,customer_need,offer_position,pricing_state,responsible_party,owner,due_date,materiality,assumption_exclusion_note,evidence_decision_note,work_state,review_state,reviewer,review_note,lifecycle_state,created_at,updated_at,version,provenance_json,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "SCOPE-2",
                bid_id,
                "Synthetic scope",
                "Description",
                "EQUIPMENT",
                "INTERNAL",
                "REQUIRED",
                "INCLUDED",
                "UNASSESSED",
                "SUPPLIER",
                "owner",
                None,
                "MATERIAL",
                None,
                None,
                "NOT_STARTED",
                "NOT_REVIEWED",
                None,
                None,
                "ACTIVE",
                now.isoformat(),
                now.isoformat(),
                1,
                "{}",
                "tester",
            ),
        )
        conn.commit()
    service.add_flow_down(
        FlowDownLink(
            request_item_id=item.request_item_id,
            bid_id=bid_id,
            target_type=FlowDownTargetType.SCOPE_ITEM,
            target_id="SCOPE-2",
            created_at=now,
            created_by="tester",
        )
    )
    service.issue_request(request.request_id, 1)
    response = ResponseVersion(
        response_id="RESP-2",
        request_id=request.request_id,
        supplier_id=supplier.supplier_id,
        bid_id=bid_id,
        version_number=1,
        received_at=now,
        evidence_mode=EvidenceMode.MANUAL_RECORD,
        evidence_note="locator",
        created_at=now,
        created_by="tester",
    )
    service.create_response(
        response,
        [
            Coverage(
                request_item_id=item.request_item_id,
                state=CoverageState.EXCEPTION,
                exception_kind="EXCLUSION",
                evidence_text="supplier excludes",
            )
        ],
    )
    service.review_response(response.response_version_id, "reviewer", ReviewState.ACCEPTED)
    with service.db._conn() as conn:
        row = conn.execute(
            "SELECT accepted_version_id FROM supplier_responses WHERE response_id='RESP-2'"
        ).fetchone()
    assert row["accepted_version_id"] == response.response_version_id
    assert any(g.code == "SUPPLIER_RESPONSE_EXCEPTION" for g in service.gaps(bid_id))


def test_pure_rules_are_stable_and_empty_population_is_zero() -> None:
    gaps = calculate_gaps([], [], [], [], as_of_date=datetime(2026, 8, 7, tzinfo=UTC).date())
    assert gaps == ()
