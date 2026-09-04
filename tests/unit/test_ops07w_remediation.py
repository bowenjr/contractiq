"""Behavioral acceptance for the consolidated OPS-07W remediation."""

from __future__ import annotations

import asyncio
import csv
import importlib.util
import io
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

import pytest
from starlette.datastructures import FormData

from core.handover import BidHandoverService, assess_manufacturer_handover
from core.schemas import Bid
from core.vendor_document_control import (
    CustomerRequirementCreate,
    RequirementVerificationUpdate,
    SupplierPackageCreate,
)


async def _request(application: Any, path: str) -> tuple[int, dict[str, str], bytes]:
    sent: list[dict[str, Any]] = []
    incoming = [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive() -> dict[str, Any]:
        return incoming.pop(0) if incoming else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    parsed = urlsplit(path)
    await application(
        {
            "type": "http",
            "method": "GET",
            "path": parsed.path,
            "query_string": parsed.query.encode(),
            "scheme": "http",
            "server": ("127.0.0.1", 0),
            "client": ("127.0.0.1", 1),
            "http_version": "1.1",
            "headers": [],
        },
        receive,
        send,
    )
    start = next(item for item in sent if item["type"] == "http.response.start")
    headers = {
        bytes(key).decode().lower(): bytes(value).decode() for key, value in start["headers"]
    }
    body = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
    return int(start["status"]), headers, body


@pytest.fixture
def remediation_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("CONTRACTIQ_DB_PATH", str(tmp_path / "remediation.db"))
    monkeypatch.setenv("CONTRACTIQ_DOCUMENT_ROOT", str(tmp_path / "documents"))
    sys.modules.pop("app", None)
    path = Path(__file__).parents[2] / "app.py"
    spec = importlib.util.spec_from_file_location("app", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load application")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    module._working_date = lambda: date(2026, 9, 3)
    return module


def test_governance_checkbox_labels_alignment_and_unknown_field_error(
    remediation_app: ModuleType,
) -> None:
    page = bytes(asyncio.run(remediation_app.bids_projects(object())).body).decode()
    assert 'class="checkbox-options"' in page
    assert page.count('class="checkbox-option"') >= 3
    assert '<label class="checkbox-option"><input type="checkbox"' in page
    assert "<span>EPC/EPCM pursuit</span></label>" in page

    class UnknownFieldRequest:
        async def form(self) -> FormData:
            return FormData(
                [
                    ("estimated_value", "1000"),
                    ("customer_type", "direct"),
                    ("unexpected_governance_fact", "true"),
                ]
            )

    rejected = asyncio.run(remediation_app.preview_bid_classification(UnknownFieldRequest()))
    rejected_body = bytes(rejected.body).decode()
    assert rejected.status_code == 422
    assert "Unrecognized classification field(s): unexpected_governance_fact" in rejected_body


@pytest.mark.parametrize(
    (
        "status",
        "source",
        "received",
        "owner",
        "impact",
        "disposition",
        "approved",
        "action",
        "ready",
        "expected",
    ),
    [
        (
            "CONFIRMED_COMPLIANT",
            "Manufacturer email",
            "2026-09-02",
            None,
            "INCLUDED",
            "NONE",
            False,
            None,
            False,
            "ownership",
        ),
        (
            "CONFIRMED_COMPLIANT",
            "Manufacturer email",
            "2026-09-02",
            "Morgan",
            "INCLUDED",
            "NONE",
            False,
            None,
            True,
            "",
        ),
        (
            "CONFIRMED_COMPLIANT",
            None,
            "2026-09-02",
            "Morgan",
            "INCLUDED",
            "NONE",
            False,
            None,
            False,
            "lacks",
        ),
        (
            "AWAITING_MANUFACTURER",
            None,
            None,
            "Morgan",
            "UNKNOWN",
            "NONE",
            False,
            None,
            False,
            "outstanding",
        ),
        (
            "CLARIFICATION_REQUIRED",
            None,
            None,
            "Morgan",
            "NONE",
            "NONE",
            False,
            None,
            False,
            "clarification",
        ),
        (
            "CANNOT_COMPLY",
            "Letter",
            "2026-09-02",
            "Morgan",
            "EXCLUDED",
            "EXCLUDED",
            True,
            None,
            False,
            "cannot comply",
        ),
        (
            "CONFIRMED_WITH_EXCEPTION",
            "Letter",
            "2026-09-02",
            "Morgan",
            "ALLOWANCE",
            "QUALIFIED",
            False,
            None,
            False,
            "not approved",
        ),
        (
            "CONFIRMED_COMPLIANT",
            "Letter",
            "2026-09-02",
            "Morgan",
            "INCLUDED",
            "NONE",
            False,
            "Confirm price",
            False,
            "Outstanding action",
        ),
    ],
)
def test_shared_handover_assessment_permutations(
    status: str,
    source: str | None,
    received: str | None,
    owner: str | None,
    impact: str,
    disposition: str,
    approved: bool,
    action: str | None,
    ready: bool,
    expected: str,
) -> None:
    result = assess_manufacturer_handover(
        required=True,
        applicable=True,
        verification_status=status,
        response_source=source,
        response_received_date=received,
        internal_owner=owner,
        commercial_impact=impact,
        bid_disposition=disposition,
        disposition_approved=approved,
        unresolved_action=action,
    )
    assert result.ready is ready
    assert expected.casefold() in " | ".join(result.blocking_reasons).casefold()
    if status == "CONFIRMED_COMPLIANT":
        assert result.response_status == "Confirmed Compliant"
    if source and received and status.startswith("CONFIRMED"):
        assert result.evidence_status == "Complete"


def test_not_applicable_existing_policy_is_ready() -> None:
    result = assess_manufacturer_handover(
        required=True,
        applicable=False,
        verification_status="NOT_REVIEWED",
        response_source=None,
        response_received_date=None,
        internal_owner=None,
        commercial_impact="UNKNOWN",
        bid_disposition="NONE",
        disposition_approved=False,
        unresolved_action=None,
    )
    assert result.ready and result.response_status == "Not applicable"


def _manufacturer_row(module: ModuleType, bid: Bid) -> tuple[str, str]:
    package = module.vendor_document_service.create_package(
        SupplierPackageCreate(
            bid_id=bid.bid_id,
            package_name="MCC",
            package_code="MCC-01",
            proposed_manufacturer="ABB",
            internal_owner="Bid Coordinator",
        ),
        "Jason",
    )
    row = module.vendor_document_service.create_requirement(
        CustomerRequirementCreate(
            package_id=package.package_id,
            customer_requirement_code="MCC-DRAWINGS",
            deliverable_title="MCC drawings",
            requested_stages=["WITH_BID"],
        ),
        "Jason",
    )
    module.vendor_document_service.update_verification(
        row.requirement_id,
        RequirementVerificationUpdate(
            expected_version=row.version,
            verification_status="CONFIRMED_COMPLIANT",
            proposed_manufacturer="ABB",
            response_source="ABB letter",
            response_received_date=date(2026, 9, 2),
            commercial_impact="INCLUDED",
        ),
        "Jason",
    )
    return package.package_id, row.requirement_id


def test_requirement_manufacturer_and_whole_bid_agree_missing_owner(
    remediation_app: ModuleType, valid_bid: Bid
) -> None:
    remediation_app.bid_repository.create_bid(valid_bid)
    requirement = remediation_app.requirement_service.create_requirement(
        {
            "bid_id": valid_bid.bid_id,
            "title": "MCC drawings",
            "statement": "Submit drawings",
            "origin": "INTERNAL",
            "category": "DOCUMENTATION",
            "significance": "MANDATORY",
        },
        "Jason",
    )
    package_id, row_id = _manufacturer_row(remediation_app, valid_bid)
    remediation_app.ops07_repository.link_manufacturer(
        valid_bid.bid_id, requirement.requirement_id, package_id, "Jason"
    )
    remediation_app.ops07w_repository.link_exact_evidence(
        valid_bid.bid_id, requirement.requirement_id, row_id, "Jason"
    )

    requirement_csv = next(
        csv.DictReader(io.StringIO(remediation_app.ops07_repository.handover_csv(valid_bid.bid_id)))
    )
    manufacturer_csv = remediation_app.vendor_document_service.handover_csv(package_id)
    report = remediation_app.bid_handover_service.report(
        valid_bid.bid_id,
        gate_verdict="clear",
        gate_blockers=[],
        generated_by="test server",
    )
    manufacturer = report.sections["manufacturer"][0]
    assert requirement_csv["manufacturer_response_status"] == "Confirmed Compliant"
    assert requirement_csv["response_evidence_status"] == "Complete"
    assert requirement_csv["handover_readiness"] == "Not ready"
    assert "ownership" in requirement_csv["handover_blocking_reasons"].casefold()
    assert "Overall handover readiness,Not ready" in manufacturer_csv
    assert manufacturer["handover_readiness"] == "Not ready"
    assert not report.handover_ready

    current = remediation_app.vendor_document_repository.get_requirement(row_id)
    assert current is not None
    remediation_app.vendor_document_service.update_verification(
        row_id,
        RequirementVerificationUpdate(
            expected_version=current.version,
            verification_status="CONFIRMED_COMPLIANT",
            proposed_manufacturer="ABB",
            response_source="ABB letter",
            response_received_date=date(2026, 9, 2),
            internal_owner="Morgan",
            commercial_impact="INCLUDED",
        ),
        "Jason",
    )
    refreshed = remediation_app.bid_handover_service.report(
        valid_bid.bid_id,
        gate_verdict="hold",
        gate_blockers=["Another authoritative gate blocker"],
        generated_by="test server",
    )
    assert "ownership" not in " | ".join(refreshed.blockers).casefold()
    assert not refreshed.handover_ready
    assert "Another authoritative gate blocker" in refreshed.blockers


def test_static_response_navigation_handover_and_gets_are_read_only(
    remediation_app: ModuleType, valid_bid: Bid
) -> None:
    remediation_app.bid_repository.create_bid(valid_bid)
    before = remediation_app.bid_repository.list_audit(valid_bid.bid_id)
    static_mount = next(route for route in remediation_app.app.routes if route.name == "static")
    scope = {"type": "http", "method": "GET", "headers": []}
    full_path, stat_result = static_mount.app.lookup_path("style.css")
    assert stat_result is not None
    stylesheet = static_mount.app.file_response(full_path, stat_result, scope)
    assert stylesheet.status_code == 200
    assert dict(stylesheet.raw_headers)[b"content-type"].startswith(b"text/css")
    assert b"--brand" in Path(stylesheet.path).read_bytes()
    outside_path, outside_stat = static_mount.app.lookup_path("../app.py")
    assert outside_path == "" and outside_stat is None
    pages = (
        asyncio.run(remediation_app.bid_detail(object(), valid_bid.bid_id)),
        asyncio.run(remediation_app.bid_award_handover(valid_bid.bid_id)),
        asyncio.run(remediation_app.bid_handover_report(valid_bid.bid_id)),
        asyncio.run(remediation_app.commercial_register(valid_bid.bid_id)),
        asyncio.run(remediation_app.contract_risks_register(valid_bid.bid_id)),
        asyncio.run(remediation_app.decisions_register(valid_bid.bid_id)),
        asyncio.run(remediation_app.proposals_register(valid_bid.bid_id)),
        asyncio.run(remediation_app.deliverables_register(valid_bid.bid_id)),
    )
    for page in pages:
        body = bytes(page.body)
        assert page.status_code == 200
        assert b'href="/static/style.css"' in body
    award = bytes(pages[1].body)
    assert f"/bids/{valid_bid.bid_id}/handover".encode() in award
    handover = bytes(pages[2].body)
    for heading in (
        b"Requirements and proposed Bid responses",
        b"Scope inclusions, exclusions and customer dependencies",
        b"Manufacturer coverage, response evidence and commercial disposition",
        b"Contract risks",
        b"Decisions",
        b"Supplier commitments",
        b"Deliverables",
        b"Outstanding My Work actions",
        b"Handover provenance",
    ):
        assert heading in handover
    assert remediation_app.bid_repository.list_audit(valid_bid.bid_id) == before


def test_consolidated_csv_is_deterministic_and_formula_safe(
    remediation_app: ModuleType, valid_bid: Bid
) -> None:
    dangerous = valid_bid.model_copy(
        update={"project_name": "=cmd|' /C calc'!A0", "customer": "+Customer"}
    )
    remediation_app.bid_repository.create_bid(dangerous)
    fixed = datetime(2026, 9, 3, 15, 0, tzinfo=UTC)
    service = BidHandoverService(remediation_app.db, now_factory=lambda: fixed)
    report = service.report(
        dangerous.bid_id, gate_verdict="hold", gate_blockers=["Incomplete"], generated_by="server"
    )
    first = service.csv(report)
    assert first == service.csv(report)
    row = next(csv.DictReader(io.StringIO(first)))
    assert row["project_name"].startswith("'=")
    assert row["customer"].startswith("'+")
    assert row["generated_at"] == fixed.isoformat()
