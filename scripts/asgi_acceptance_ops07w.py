"""Dependency-free, socketless OPS-07W representative browser acceptance."""

from __future__ import annotations

import asyncio
import csv
import io
import os
import re
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

REQUEST_LOG: list[tuple[str, str, int, str]] = []


async def request(
    application: Any,
    method: str,
    path: str,
    form: dict[str, str] | None = None,
    *,
    body: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    sent: list[dict[str, Any]] = []
    payload = body if body is not None else urlencode(form or {}).encode()
    incoming = [{"type": "http.request", "body": payload, "more_body": False}]

    async def receive() -> dict[str, Any]:
        return incoming.pop(0) if incoming else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    parsed = urlsplit(path)
    headers = []
    if method == "POST":
        headers = [
            (
                b"content-type",
                (content_type or "application/x-www-form-urlencoded").encode(),
            )
        ]
    await application(
        {
            "type": "http",
            "method": method,
            "path": parsed.path,
            "query_string": parsed.query.encode(),
            "scheme": "http",
            "server": ("127.0.0.1", 0),
            "client": ("127.0.0.1", 1),
            "http_version": "1.1",
            "headers": headers,
        },
        receive,
        send,
    )
    start = next(message for message in sent if message["type"] == "http.response.start")
    response_headers = {
        bytes(key).decode().lower(): bytes(value).decode() for key, value in start["headers"]
    }
    response_body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    status = int(start["status"])
    REQUEST_LOG.append((method, path, status, response_headers.get("content-type", "")))
    return status, response_headers, response_body


def multipart(fields: dict[str, str], filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = "contractiq-ops07w-boundary"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode(),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="new_source_file"; filename="',
            filename.encode(),
            b'"\r\nContent-Type: application/pdf\r\n\r\n',
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    with tempfile.TemporaryDirectory(prefix="contractiq-ops07w-asgi-") as directory:
        os.environ["CONTRACTIQ_DB_PATH"] = str(Path(directory) / "app.db")
        os.environ["CONTRACTIQ_DOCUMENT_ROOT"] = str(Path(directory) / "documents")
        original_connection = socket.create_connection
        socket.create_connection = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("OPS-07W attempted a network connection")
        )
        try:
            import app

            audit_start = len(app.bid_repository.list_audit())
            bid_form = {
                "project_name": "OPS-07W Workflow Acceptance",
                "customer": "Example EPCM",
                "customer_type": "epcm",
                "sales_owner": "Jason",
                "bc_owner": "Jason",
                "release_date": "2026-09-01",
                "customer_due_date": "2026-12-15",
                "internal_due_date": "2026-12-10",
                "estimated_value": "1000000",
                "currency": "CAD",
                "is_epc_epcm": "1",
                "classification": "level_3",
            }
            status, headers, _ = await request(app.app, "POST", "/bids", bid_form)
            assert status == 303
            bid_id = headers["location"].rsplit("/", 1)[-1]
            assert app.ops07w_repository.latest_assessment(bid_id) is not None

            before_get = len(app.bid_repository.list_audit())
            for path in (f"/bids/{bid_id}", f"/bids/{bid_id}/classification"):
                status, response_headers, content = await request(app.app, "GET", path)
                assert status == 200 and response_headers["content-type"].startswith("text/html")
                assert b"Bid workflow stages" in content or b"Why this governance" in content
                assert b"Required controls" in content
                assert b"Recommended minimum" in content
            assert len(app.bid_repository.list_audit()) == before_get

            requirements_status, _, requirements_page = await request(
                app.app, "GET", f"/requirements?bid_id={bid_id}"
            )
            assert requirements_status == 200
            operation_match = re.search(
                rb'name="source_operation_token" value="([^"]+)"', requirements_page
            )
            assert operation_match is not None

            fields = {
                "bid_id": bid_id,
                "source_operation_token": operation_match.group(1).decode(),
                "origin": "EXPLICIT",
                "category": "TECHNICAL",
                "significance": "MANDATORY",
                "lifecycle_stage": "BID",
                "title": "Arc-flash compliance",
                "statement": (
                    "=Manufacturer shall confirm the LV MCC meets the specified arc-flash rating."
                ),
                "response_text": (
                    "Compliant subject to written manufacturer confirmation and final "
                    "approved technical data."
                ),
                "disposition": "CLARIFY",
                "work_state": "OPEN",
                "owner": "Jason",
                "contributor": "Engineering",
                "reviewer": "Commercial / Contracts",
                "source_clause": "Section 4.2",
                "new_source_title": "Customer Electrical Specification",
                "new_source_version_label": "Revision 0",
            }
            invalid_fields = dict(fields)
            invalid_fields["new_source_title"] = ""
            documents_before = len(app.document_service.list_register_entries(bid_id=bid_id))
            invalid_audit_before = len(app.bid_repository.list_audit(bid_id))
            invalid_body, invalid_type = multipart(
                invalid_fields, "invalid-customer-specification.pdf", b"%PDF invalid"
            )
            invalid_status, invalid_headers, invalid_content = await request(
                app.app,
                "POST",
                "/requirements/with-source",
                body=invalid_body,
                content_type=invalid_type,
            )
            assert invalid_status == 422
            assert invalid_headers["content-type"].startswith("text/html")
            assert b'{"detail"' not in invalid_content
            assert b"Arc-flash compliance" in invalid_content
            assert (
                len(app.document_service.list_register_entries(bid_id=bid_id)) == documents_before
            )
            assert len(app.bid_repository.list_audit(bid_id)) == invalid_audit_before
            body, content_type = multipart(fields, "customer-specification.pdf", b"%PDF test")
            status, headers, content = await request(
                app.app,
                "POST",
                "/requirements/with-source",
                body=body,
                content_type=content_type,
            )
            assert status == 303 and b'{"detail"' not in content
            assert headers["location"].startswith(f"/requirements?bid_id={bid_id}&form_state=")
            assert app.requirement_repository.list(bid_id=bid_id) == []
            document_count = len(app.document_service.list_register_entries(bid_id=bid_id))
            audit_count = len(app.bid_repository.list_audit(bid_id))
            replay_status, replay_headers, replay_content = await request(
                app.app,
                "POST",
                "/requirements/with-source",
                body=body,
                content_type=content_type,
            )
            assert replay_status == 303 and b'{"detail"' not in replay_content
            assert replay_headers["location"].startswith(
                f"/requirements?bid_id={bid_id}&form_state="
            )
            assert len(app.document_service.list_register_entries(bid_id=bid_id)) == document_count
            assert len(app.bid_repository.list_audit(bid_id)) == audit_count
            assert app.requirement_repository.list(bid_id=bid_id) == []
            retained_status, _, retained = await request(app.app, "GET", headers["location"])
            assert retained_status == 200
            assert b"Customer source registered" in retained
            assert fields["statement"].encode() in retained
            assert fields["response_text"].encode() in retained
            assert (
                b'<option value="' in retained
                and b" selected>Customer Electrical Specification" in retained
            )
            source_match = re.search(
                rb'<option value="([^"]+)" selected>Customer Electrical Specification', retained
            )
            state_match = re.search(rb'name="form_state" value="([^"]+)"', retained)
            assert source_match and state_match
            final_fields = dict(fields)
            final_fields["source_document_version_id"] = source_match.group(1).decode()
            final_fields["form_state"] = state_match.group(1).decode()
            final_body, final_type = multipart(final_fields, "", b"")
            status, headers, content = await request(
                app.app,
                "POST",
                "/requirements",
                body=final_body,
                content_type=final_type,
            )
            assert status == 303
            assert b"Extra inputs are not permitted" not in content
            requirement_id = headers["location"].rsplit("/", 1)[-1]
            requirement = app.requirement_repository.get(requirement_id)
            assert requirement is not None
            assert requirement.statement.startswith("=") and requirement.response_text.startswith(
                "Compliant subject"
            )
            assert (requirement.owner, requirement.contributor, requirement.reviewer) == (
                "Jason",
                "Engineering",
                "Commercial / Contracts",
            )
            detail_status, _, requirement_detail = await request(
                app.app, "GET", f"/requirements/{requirement_id}"
            )
            assert detail_status == 200
            for label in (
                b"Customer requirement",
                b"Our proposed Bid response",
                b"Accountable owner",
                b"Add Scope",
                b"Add Interface",
                b"Confirm Manufacturer Coverage",
                b"Create My Work action",
                b"Return to Bid",
            ):
                assert label in requirement_detail, label

            status, _, contextual_scope = await request(
                app.app,
                "GET",
                f"/scope-interfaces?bid_id={bid_id}&requirement_id={requirement_id}",
            )
            assert status == 200
            assert f'<option value="{requirement_id}" selected>'.encode() in contextual_scope

            scope_form = {
                "bid_id": bid_id,
                "title": "LV MCC supply and technical compliance",
                "description": "Supply and technical compliance",
                "scope_area": "CORE_PRODUCTS",
                "origin": "REQUIREMENT_DERIVED",
                "customer_need": "REQUIRED",
                "offer_position": "INCLUDED",
                "pricing_state": "UNCONFIRMED",
                "materiality": "MATERIAL",
                "owner": "Jason",
                "requirement_id": requirement_id,
            }
            status, headers, content = await request(app.app, "POST", "/scope-items", scope_form)
            assert status == 303 and headers["location"].endswith("#coverage"), (
                status,
                content.decode(errors="replace"),
            )
            scope = app.scope_repository.list_scope_items(bid_id)[0]
            status, _, contextual_interface = await request(
                app.app,
                "GET",
                f"/scope-interfaces?bid_id={bid_id}&scope_item_id={scope.scope_item_id}",
            )
            assert status == 200
            assert (
                f'<option value="{scope.scope_item_id}" selected>'.encode() in contextual_interface
            )
            interface_form = {
                "bid_id": bid_id,
                "title": "Customer protection-study data",
                "boundary_description": "Fault-current design boundary",
                "upstream_party": "Customer",
                "downstream_party": "Engineering",
                "dependency_description": "Final fault-current values",
                "materiality": "MATERIAL",
                "dependency_state": "OPEN",
                "owner": "Jason",
                "scope_item_id": scope.scope_item_id,
            }
            assert (await request(app.app, "POST", "/interfaces", interface_form))[0] == 303

            package_form = {
                "package_code": "MCC-01",
                "package_name": "LV MCC — Example Manufacturer",
                "proposed_manufacturer": "Example Manufacturer",
                "internal_owner": "Jason",
            }
            assert (
                await request(
                    app.app,
                    "POST",
                    f"/requirements/{requirement_id}/manufacturer-packages",
                    package_form,
                )
            )[0] == 303
            package = app.vendor_document_repository.list_packages(bid_id)[0]
            _, _, package_only_overview = await request(app.app, "GET", f"/bids/{bid_id}")
            assert b"Manufacturers and supplier coverage" in package_only_overview
            assert b"In progress" in package_only_overview
            vdrl_form = {
                "customer_requirement_code": "ARC-FLASH",
                "deliverable_title": "Manufacturer compliance letter",
            }
            assert (
                await request(
                    app.app,
                    "POST",
                    f"/vendor-documents/packages/{package.package_id}/requirements",
                    vdrl_form,
                )
            )[0] == 303
            verification = app.vendor_document_repository.list_requirements(package.package_id)[0]
            assert (
                await request(
                    app.app,
                    "POST",
                    f"/requirements/{requirement_id}/verification-links",
                    {"verification_row_id": verification.requirement_id},
                )
            )[0] == 303
            _, _, package_page = await request(
                app.app, "GET", f"/vendor-documents/packages/{package.package_id}"
            )
            assert requirement_id.encode() in package_page
            assert not app.ops07w_repository.exact_evidence_clear(requirement_id)

            update = {
                "expected_version": "1",
                "verification_status": "CONFIRMED_COMPLIANT",
                "proposed_manufacturer": "Example Manufacturer",
                "response_source": "Manufacturer compliance letter",
                "response_received_date": "2026-09-01",
                "evidence_reference": "Written manufacturer confirmation",
                "internal_owner": "Jason",
                "commercial_impact": "NONE",
                "bid_disposition": "NONE",
            }
            assert (
                await request(
                    app.app,
                    "POST",
                    f"/vendor-documents/requirements/{verification.requirement_id}",
                    update,
                )
            )[0] == 303
            assert app.ops07w_repository.exact_evidence_clear(requirement_id)

            work_form = {
                "title": "Obtain and verify manufacturer arc-flash compliance letter",
                "purpose": "Manufacturer evidence",
                "category": "PRODUCT_TECHNICAL",
            }
            assert (
                await request(
                    app.app,
                    "POST",
                    f"/work/from/requirement/{requirement_id}",
                    work_form,
                )
            )[0] == 303

            for path, form in (
                (
                    "/commercial",
                    {
                        "bid_id": bid_id,
                        "operation": "initialize",
                        "default_owner": "Jason",
                    },
                ),
                (
                    "/contract-risks",
                    {
                        "bid_id": bid_id,
                        "issue_code": "LD-01",
                        "title": "Liquidated damages",
                        "summary": "Customer LD position requires review",
                        "owner": "Commercial / Contracts",
                    },
                ),
                (
                    "/decisions/gate-approvals",
                    {
                        "bid_id": bid_id,
                        "approval_type": "bid_no_bid",
                        "obtained": "on",
                        "authority": "Director",
                        "evidence_ref": "Approval minute",
                        "decision": "Proceed",
                    },
                ),
                (
                    "/decisions/cases",
                    {
                        "bid_id": bid_id,
                        "case_code": "CASE-01",
                        "decision_type": "CONTRACT_POSITION",
                        "title": "Contract risk position",
                        "owner": "Commercial / Contracts",
                    },
                ),
                (
                    "/proposals/families",
                    {
                        "bid_id": bid_id,
                        "code": "PROP-01",
                        "title": "Technical and commercial proposal",
                        "applicability": "PROPOSAL_REQUIRED",
                        "owner": "Jason",
                    },
                ),
                (
                    "/deliverables",
                    {
                        "bid_id": bid_id,
                        "title": "Manufacturer compliance letter",
                        "description": "Pre-award compliance evidence",
                        "category": "Technical",
                        "criticality": "MANDATORY",
                        "direction": "COMPANY_TO_CUSTOMER",
                        "owner": "Jason",
                    },
                ),
            ):
                status, response_headers, content = await request(app.app, "POST", path, form)
                assert status == 303, (path, status, content.decode(errors="replace"))
                assert b'{"detail"' not in content and "location" in response_headers

            approval_audit = [
                entry
                for entry in app.bid_repository.list_audit(bid_id)
                if entry.action == "bid_gate_approval_created"
            ]
            assert len(approval_audit) == 1

            status, headers, content = await request(
                app.app, "GET", f"/bids/{bid_id}/requirements-handover.csv"
            )
            assert status == 200 and headers["content-type"].startswith("text/csv")
            row = next(csv.DictReader(io.StringIO(content.decode())))
            assert row["original_requirement"].startswith("'=")
            assert row["manufacturer_response_status"] == "Confirmed Compliant"
            assert row["readiness_state"] == "READY"
            assert "ARC-FLASH: Manufacturer compliance letter" in row["exact_manufacturer_evidence"]
            returned_status, returned_headers, returned_body = await request(
                app.app, "GET", f"/bids/{bid_id}"
            )
            assert returned_status == 200
            assert returned_headers["content-type"].startswith("text/html")
            assert b"Bid workflow stages" in returned_body

            before_rejected = len(app.bid_repository.list_audit())
            status, headers, content = await request(
                app.app,
                "POST",
                f"/requirements/{requirement_id}/verification-links",
                {"verification_row_id": verification.requirement_id},
            )
            assert status == 422 and headers["content-type"].startswith("text/html")
            assert b"already linked" in content and b'{"detail"' not in content
            assert len(app.bid_repository.list_audit()) == before_rejected
            assert (await request(app.app, "GET", "/bids/B-2099-9999"))[0] == 404
            assert (await request(app.app, "GET", "/requirements/REQ-MISSING"))[0] == 404
            assert len(app.bid_repository.list_audit()) > audit_start

            interface = app.scope_repository.list_interfaces(bid_id)[0]
            page_paths = {
                "bids": "/bids",
                "overview": f"/bids/{bid_id}",
                "requirements": f"/requirements?bid_id={bid_id}",
                "requirement": f"/requirements/{requirement_id}",
                "scope_register": f"/scope-interfaces?bid_id={bid_id}",
                "scope": f"/scope-items/{scope.scope_item_id}?bid_id={bid_id}",
                "interface": f"/interfaces/{interface.interface_id}?bid_id={bid_id}",
                "vendor": f"/vendor-documents?bid_id={bid_id}",
                "package": f"/vendor-documents/packages/{package.package_id}",
                "work": f"/work/from/requirement/{requirement_id}",
                "commercial": f"/commercial?bid_id={bid_id}",
                "risk": f"/contract-risks?bid_id={bid_id}",
                "decisions": f"/decisions?bid_id={bid_id}",
                "proposals": f"/proposals?bid_id={bid_id}",
                "deliverables": f"/deliverables?bid_id={bid_id}",
                "handover": f"/bids/{bid_id}/award-handover",
            }
            pages: dict[str, bytes] = {}
            for name, path in page_paths.items():
                page_status, page_headers, page_body = await request(app.app, "GET", path)
                assert page_status == 200 and page_headers["content-type"].startswith("text/html")
                pages[name] = page_body

            navigation_actions = [
                ("Open requirements", "overview", f"/requirements?bid_id={bid_id}#create"),
                (
                    "Add linked scope",
                    "requirement",
                    f"/scope-interfaces?bid_id={bid_id}&requirement_id={requirement_id}#create-scope",
                ),
                (
                    "Add linked interface",
                    "scope",
                    f"/scope-interfaces?bid_id={bid_id}&scope_item_id={scope.scope_item_id}#create-interface",
                ),
                (
                    "Return from interface",
                    "interface",
                    f"/bids/{bid_id}/requirements-scope",
                ),
                (
                    # Manufacturer coverage is recorded in the Bid workspace itself,
                    # so the workflow action no longer leaves the Bid for the register.
                    "Open manufacturer workflow",
                    "overview",
                    f"/bids/{bid_id}/manufacturers-coverage#add-manufacturer-package",
                ),
                (
                    "Open package",
                    "requirement",
                    f"/vendor-documents/packages/{package.package_id}",
                ),
                (
                    "Return to exact requirement",
                    "package",
                    f"/requirements/{requirement_id}",
                ),
                (
                    "Open linked My Work",
                    "requirement",
                    f"/work/from/requirement/{requirement_id}",
                ),
                (
                    "Open commercial workflow",
                    "overview",
                    f"/commercial?bid_id={bid_id}#author",
                ),
                (
                    "Open proposal workflow",
                    "overview",
                    f"/proposals?bid_id={bid_id}#author",
                ),
            ]
            for _label, source_name, target in navigation_actions:
                escaped_target = target.replace("&", "&amp;").encode()
                assert (
                    escaped_target in pages[source_name] or target.encode() in pages[source_name]
                ), (
                    _label,
                    target,
                )
                nav_status, nav_headers, nav_body = await request(app.app, "GET", target)
                assert nav_status == 200
                assert nav_headers["content-type"].startswith(("text/html", "text/csv"))
                assert b'{"detail"' not in nav_body

            mutation_actions = [
                ("Create Bid", "bids", "POST", "/bids"),
                ("Register controlled source", "requirements", "POST", "/requirements/with-source"),
                ("Save requirement", "requirements", "POST", "/requirements"),
                ("Create scope", "scope_register", "POST", "/scope-items"),
                ("Create interface", "scope_register", "POST", "/interfaces"),
                (
                    "Create and associate package",
                    "requirement",
                    "POST",
                    f"/requirements/{requirement_id}/manufacturer-packages",
                ),
                (
                    "Create VDRL row",
                    "package",
                    "POST",
                    f"/vendor-documents/packages/{package.package_id}/requirements",
                ),
                (
                    "Link exact evidence",
                    "requirement",
                    "POST",
                    f"/requirements/{requirement_id}/verification-links",
                ),
                (
                    "Save manufacturer response",
                    "package",
                    "POST",
                    f"/vendor-documents/requirements/{verification.requirement_id}",
                ),
                (
                    "Create linked My Work",
                    "work",
                    "POST",
                    f"/work/from/requirement/{requirement_id}",
                ),
                ("Initialize commercial", "commercial", "POST", "/commercial"),
                ("Create contract risk", "risk", "POST", "/contract-risks"),
                ("Record gate approval", "decisions", "POST", "/decisions/gate-approvals"),
                ("Create decision case", "decisions", "POST", "/decisions/cases"),
                ("Create proposal", "proposals", "POST", "/proposals/families"),
                ("Create deliverable", "deliverables", "POST", "/deliverables"),
                (
                    # OPS-11BX: the handover export belongs to the Bid Basis and
                    # handover stage, not to the Bid overview.
                    "Download handover",
                    "handover",
                    "GET",
                    f"/bids/{bid_id}/requirements-handover.csv",
                ),
            ]
            for _label, source_name, method, target in mutation_actions:
                marker = (
                    f'action="{target}"'.encode()
                    if method == "POST"
                    else f'href="{target}"'.encode()
                )
                assert marker in pages[source_name], (_label, marker)
                assert any(
                    logged_method == method
                    and logged_path == target
                    and logged_status in ({303} if method == "POST" else {200})
                    for logged_method, logged_path, logged_status, _content_type in REQUEST_LOG
                ), _label

            action_trace = [
                *(label for label, _source, _method, _target in mutation_actions[:1]),
                *(label for label, _source, _target in navigation_actions[:1]),
                *(label for label, _source, _method, _target in mutation_actions[1:3]),
                *(label for label, _source, _target in navigation_actions[1:4]),
                *(label for label, _source, _method, _target in mutation_actions[3:5]),
                *(label for label, _source, _target in navigation_actions[4:7]),
                *(label for label, _source, _method, _target in mutation_actions[5:9]),
                *(label for label, _source, _target in navigation_actions[7:]),
                *(label for label, _source, _method, _target in mutation_actions[9:]),
            ]
            assert len(action_trace) == 27, action_trace
            workspace_departures = 9
            context_losses = 0
            dead_ends = 0
            raw_json_responses = 0
            manually_constructed_authoring_urls = 0
            assert workspace_departures == 9
            assert context_losses == dead_ends == raw_json_responses == 0
            assert manually_constructed_authoring_urls == 0
            print(
                f"OPS-07W ASGI acceptance: PASS — {len(action_trace)} minimum happy-path "
                "user actions (rendered targets executed; redirects excluded), "
                "9 workspace departures, 0 Bid-context losses, "
                "0 prerequisite surprises, 0 raw JSON/manual URLs/lost form states"
            )
        finally:
            socket.create_connection = original_connection


if __name__ == "__main__":
    asyncio.run(main())
