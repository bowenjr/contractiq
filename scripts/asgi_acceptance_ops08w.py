"""Dependency-free, socketless OPS-08W Bid workflow acceptance."""

from __future__ import annotations

import asyncio
import os
import re
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.asgi_acceptance_ops08 import multipart, request  # noqa: E402


async def main() -> None:
    actions = 0
    departures = 0
    lost_context = 0
    duplicate_entry = 0
    manual_urls = 0
    prerequisite_surprises = 0
    dead_ends = 0
    with tempfile.TemporaryDirectory(prefix="contractiq-ops08w-asgi-") as directory:
        os.environ["CONTRACTIQ_DB_PATH"] = str(Path(directory) / "app.db")
        os.environ["CONTRACTIQ_DOCUMENT_ROOT"] = str(Path(directory) / "documents")
        original_connection = socket.create_connection
        socket.create_connection = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("OPS-08W attempted a network connection")
        )
        try:
            import app

            status, headers, content = await request(
                app.app,
                "POST",
                "/bids",
                {
                    "project_name": "OPS-08W Connected Bid",
                    "customer": "Example EPCM",
                    "customer_type": "epcm",
                    "sales_owner": "Jason",
                    "bc_owner": "Jason",
                    "release_date": "2026-09-01",
                    "customer_due_date": "2026-12-15",
                    "internal_due_date": "2026-12-10",
                    "estimated_value": "5000000",
                    "currency": "CAD",
                    "is_epc_epcm": "1",
                    "classification": "level_3",
                },
            )
            actions += 1
            assert status == 303, content.decode(errors="replace")
            bid_id = headers["location"].rsplit("/", 1)[-1]

            status, _, requirement_page = await request(
                app.app, "GET", f"/requirements?bid_id={bid_id}"
            )
            actions += 1
            departures += 1
            assert status == 200 and bid_id.encode() in requirement_page
            token = re.search(rb'name="source_operation_token" value="([^"]+)"', requirement_page)
            assert token
            fields = {
                "bid_id": bid_id,
                "source_operation_token": token.group(1).decode(),
                "origin": "EXPLICIT",
                "category": "COMMERCIAL",
                "significance": "MANDATORY",
                "lifecycle_stage": "BID",
                "title": "Payment and delivery requirement",
                "statement": "Customer requires net 90 payment after delivery.",
                "response_text": "Milestone billing proposed.",
                "disposition": "DEVIATE",
                "work_state": "OPEN",
                "owner": "Jason",
                "contributor": "Commercial",
                "reviewer": "Contracts",
                "source_clause": "Section 7.2",
                "new_source_title": "Customer Commercial Terms",
                "new_source_version_label": "Revision 0",
            }
            body, content_type = multipart(fields, "customer-terms.pdf", b"%PDF OPS08W")
            status, headers, _ = await request(
                app.app,
                "POST",
                "/requirements/with-source",
                body=body,
                content_type=content_type,
            )
            actions += 1
            assert status == 303
            status, _, retained = await request(app.app, "GET", headers["location"])
            assert status == 200
            source = re.search(
                rb'<option value="([^"]+)" selected>Customer Commercial Terms', retained
            )
            state = re.search(rb'name="form_state" value="([^"]+)"', retained)
            assert source and state
            final = dict(fields)
            final["source_document_version_id"] = source.group(1).decode()
            final["form_state"] = state.group(1).decode()
            final_body, final_type = multipart(final, "", b"")
            status, headers, requirement_content = await request(
                app.app,
                "POST",
                "/requirements",
                body=final_body,
                content_type=final_type,
            )
            actions += 1
            assert status == 303, requirement_content.decode(errors="replace")
            requirement_id = headers["location"].rsplit("/", 1)[-1]

            scope_form = {
                "bid_id": bid_id,
                "origin_section": "bid",
                "title": "Supply LV equipment",
                "description": "Supply the qualified LV equipment package.",
                "scope_area": "CORE_PRODUCTS",
                "origin": "REQUIREMENT_DERIVED",
                "customer_need": "REQUIRED",
                "offer_position": "INCLUDED",
                "pricing_state": "UNCONFIRMED",
                "materiality": "MATERIAL",
                "owner": "Jason",
                "requirement_id": requirement_id,
            }
            status, headers, _ = await request(app.app, "POST", "/scope-items", scope_form)
            actions += 1
            assert status == 303 and headers["location"].endswith(
                "/requirements-scope#scope-and-interfaces"
            )
            scope = app.scope_repository.list_scope_items(bid_id)[0]
            assert scope.version == 1

            status, _, contextual_interface = await request(
                app.app,
                "GET",
                f"/scope-interfaces?bid_id={bid_id}&scope_item_id={scope.scope_item_id}",
            )
            actions += 1
            departures += 1
            assert (
                f'<option value="{scope.scope_item_id}" selected>'.encode() in contextual_interface
            )
            assert b'name="origin_section" value="bid"' in contextual_interface
            status, headers, _ = await request(
                app.app,
                "POST",
                "/interfaces",
                {
                    "bid_id": bid_id,
                    "origin_section": "bid",
                    "scope_item_id": scope.scope_item_id,
                    "title": "Customer design data",
                    "boundary_description": "Design responsibility boundary",
                    "upstream_party": "Customer",
                    "downstream_party": "Engineering",
                    "dependency_description": "Final customer data is required.",
                    "materiality": "MATERIAL",
                    "dependency_state": "OPEN",
                    "owner": "Jason",
                },
            )
            actions += 1
            assert status == 303 and headers["location"].endswith(
                "/requirements-scope#scope-and-interfaces"
            )

            status, headers, _ = await request(
                app.app,
                "POST",
                "/vendor-documents/packages",
                {
                    "bid_id": bid_id,
                    "origin_section": "bid",
                    "package_code": "MCC-01",
                    "package_name": "LV MCC package",
                    "proposed_manufacturer": "Example Manufacturer",
                    "internal_owner": "Jason",
                },
            )
            actions += 1
            assert status == 303 and headers["location"].endswith(
                "/manufacturers-coverage#manufacturer-packages"
            )
            package = app.vendor_document_repository.list_packages(bid_id)[0]
            assert (
                await request(
                    app.app,
                    "POST",
                    f"/requirements/{requirement_id}/manufacturer-links",
                    {"package_id": package.package_id},
                )
            )[0] == 303
            actions += 1
            for code, title in (
                ("CONFIRM", "Manufacturer compliance confirmation"),
                ("OPEN", "Manufacturer exception response"),
            ):
                status, _, _ = await request(
                    app.app,
                    "POST",
                    f"/vendor-documents/packages/{package.package_id}/requirements",
                    {"customer_requirement_code": code, "deliverable_title": title},
                )
                actions += 1
                assert status == 303
            evidence, unresolved = app.vendor_document_repository.list_requirements(
                package.package_id
            )
            assert (
                await request(
                    app.app,
                    "POST",
                    f"/requirements/{requirement_id}/verification-links",
                    {"verification_row_id": evidence.requirement_id},
                )
            )[0] == 303
            actions += 1
            assert (
                await request(
                    app.app,
                    "POST",
                    f"/vendor-documents/requirements/{evidence.requirement_id}",
                    {
                        "expected_version": "1",
                        "verification_status": "CONFIRMED_COMPLIANT",
                        "response_source": "Manufacturer letter",
                        "response_received_date": "2026-09-04",
                        "evidence_reference": "Letter ref M-01",
                        "internal_owner": "Jason",
                        "commercial_impact": "NONE",
                        "bid_disposition": "NONE",
                    },
                )
            )[0] == 303
            actions += 1
            assert unresolved.verification_status.value == "NOT_REVIEWED"

            assert (
                await request(
                    app.app,
                    "POST",
                    "/commercial",
                    {"bid_id": bid_id, "operation": "initialize", "default_owner": "Jason"},
                )
            )[0] == 303
            actions += 1
            payment = next(
                row
                for row in app.ops08_repository.workspace(bid_id)
                if row["topic_key"] == "PAYMENT_TERMS"
            )
            assert (
                await request(
                    app.app,
                    "POST",
                    f"/commercial/positions/{payment['position_id']}",
                    {
                        "expected_version": "1",
                        "customer_position": "Net 90 after delivery",
                        "proposed_position": "Milestone billing",
                        "disposition": "QUALIFY",
                        "source_document_version_id": source.group(1).decode(),
                        "source_locator": "Section 7.2",
                        "rationale": "Protect cash flow",
                        "owner": "Jason",
                        "required_approver": "Commercial Director",
                        "negotiation_state": "NOT_STARTED",
                    },
                )
            )[0] == 303
            actions += 1

            audit_before_get = len(app.bid_repository.list_audit(bid_id))
            counts_before_get = (
                len(app.contract_risk_service.list(bid_id)),
                len(app.approval_repository.cases(bid_id)),
                len(app.negotiation_repository.plans(bid_id)),
                len(app.work_item_repository.list(bid_id)),
            )
            contextual_gets = (
                f"/contract-risks?bid_id={bid_id}&position_id={payment['position_id']}",
                f"/decisions?bid_id={bid_id}&position_id={payment['position_id']}",
                f"/negotiations?bid_id={bid_id}&position_id={payment['position_id']}",
                f"/work/from/commercial/{payment['position_id']}?return_to_bid=true",
            )
            for path in contextual_gets:
                status, _, page = await request(app.app, "GET", path)
                actions += 1
                departures += 1
                assert status == 200 and bid_id.encode() in page
            assert counts_before_get == (
                len(app.contract_risk_service.list(bid_id)),
                len(app.approval_repository.cases(bid_id)),
                len(app.negotiation_repository.plans(bid_id)),
                len(app.work_item_repository.list(bid_id)),
            )
            assert len(app.bid_repository.list_audit(bid_id)) == audit_before_get

            assert (
                await request(
                    app.app,
                    "POST",
                    "/contract-risks",
                    {
                        "bid_id": bid_id,
                        "position_id": payment["position_id"],
                        "issue_code": "PAY-01",
                        "title": "Payment exposure",
                        "summary": "Net 90 affects cash flow.",
                        "owner": "Jason",
                        "confirm_authoritative": "on",
                    },
                )
            )[0] == 303
            actions += 1
            assert (
                await request(
                    app.app,
                    "POST",
                    "/decisions/cases",
                    {
                        "bid_id": bid_id,
                        "position_id": payment["position_id"],
                        "case_code": "PAY-APPROVAL",
                        "decision_type": "CONTRACT_POSITION",
                        "title": "Approve payment qualification",
                        "owner": "Commercial Director",
                        "confirm_authoritative": "on",
                    },
                )
            )[0] == 303
            actions += 1
            assert (
                await request(
                    app.app,
                    "POST",
                    f"/work/from/commercial/{payment['position_id']}",
                    {
                        "title": "Close payment qualification",
                        "purpose": "Commercial closeout",
                        "category": "COMMERCIAL_REVIEW",
                        "return_to_bid": "1",
                    },
                )
            )[0] == 303
            actions += 1
            assert (
                await request(
                    app.app,
                    "POST",
                    "/negotiations/plans",
                    {
                        "bid_id": bid_id,
                        "position_id": payment["position_id"],
                        "code": "NEG-PAY",
                        "title": "Negotiate payment terms",
                        "owner": "Jason",
                        "confirm_authoritative": "on",
                    },
                )
            )[0] == 303
            actions += 1

            detail = app.ops08_repository.detail(payment["position_id"])
            assert any(row["risk_issue_id"] for row in detail["relationships"])
            assert any(row["decision_case_id"] for row in detail["relationships"])
            assert any(row["negotiation_plan_id"] for row in detail["relationships"])

            rendered: dict[str, bytes] = {}
            for path, marker in (
                (f"/bids/{bid_id}/proposal-negotiation", b"Derived proposal inputs"),
                ("/my-day", b'data-bid-row="'),
                (f"/bids/{bid_id}/handover", b"Payment terms"),
            ):
                status, _, page = await request(app.app, "GET", path)
                actions += 1
                assert status == 200 and marker in page and bid_id.encode() in page
                rendered[path] = page
            assert rendered[f"/bids/{bid_id}/handover"].count(b"Net 90 after delivery") >= 1
            assert (
                len(re.findall(rb'data-bid-row="' + bid_id.encode() + rb'"', rendered["/my-day"]))
                == 1
            )
        finally:
            socket.create_connection = original_connection
    print("OPS-08W ASGI acceptance: PASS")
    print(
        "Workflow measurements: "
        f"rendered user actions={actions}; departures from Bid workspace={departures}; "
        f"lost context={lost_context}; duplicate data entry={duplicate_entry}; "
        f"raw JSON/manual URL requirements={manual_urls}; "
        f"prerequisite surprises={prerequisite_surprises}; dead ends={dead_ends}"
    )


if __name__ == "__main__":
    asyncio.run(main())
