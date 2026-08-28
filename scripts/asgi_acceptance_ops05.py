"""Dependency-free HTML acceptance for OPS-05B bid-stage VDRL handover."""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit


def _multipart(filename: str, content: str) -> tuple[bytes, bytes]:
    boundary = "contractiq-ops05b-boundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="csv_file"; filename="requirements.csv"\r\n'
        "Content-Type: text/csv\r\n\r\n"
        f"{content}\r\n--{boundary}--\r\n"
    ).encode()
    return body, f"multipart/form-data; boundary={boundary}".encode()


async def request(
    application: Any,
    method: str,
    path: str,
    form: dict[str, str] | list[tuple[str, str]] | None = None,
    upload: tuple[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    sent: list[dict[str, Any]] = []
    if upload is not None:
        body, content_type = _multipart(*upload)
    else:
        body = urlencode(form or {}).encode()
        content_type = b"application/x-www-form-urlencoded"
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive() -> dict[str, Any]:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    parsed = urlsplit(path)
    await application(
        {
            "type": "http",
            "method": method,
            "path": parsed.path,
            "query_string": parsed.query.encode(),
            "headers": [(b"content-type", content_type)] if method == "POST" else [],
            "scheme": "http",
            "server": ("127.0.0.1", 0),
            "client": ("127.0.0.1", 1),
            "http_version": "1.1",
        },
        receive,
        send,
    )
    start = next(item for item in sent if item["type"] == "http.response.start")
    headers = {
        bytes(key).decode().lower(): bytes(value).decode()
        for key, value in start.get("headers", [])
    }
    response_body = b"".join(
        item.get("body", b"") for item in sent if item["type"] == "http.response.body"
    )
    return int(start["status"]), headers, response_body


async def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with tempfile.TemporaryDirectory(prefix="contractiq-ops05b-asgi-") as directory:
        os.environ["CONTRACTIQ_DB_PATH"] = str(Path(directory) / "app.db")
        os.environ["CONTRACTIQ_DOCUMENT_ROOT"] = str(Path(directory) / "documents")
        original_connection = socket.create_connection

        def blocked(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("OPS-05B attempted a network connection")

        socket.create_connection = blocked
        try:
            import app

            status, _, home = await request(app.app, "GET", "/")
            assert status == 200
            assert b'href="/bids"' in home
            assert b">Bids<" in home

            status, _, bids_page = await request(app.app, "GET", "/bids")
            assert status == 200
            assert b"Create Bid" in bids_page
            status, headers, _ = await request(
                app.app,
                "POST",
                "/bids",
                {
                    "project_name": "Northline Expansion Bid",
                    "customer": "Northline Mining",
                    "customer_type": "epcm",
                    "location": "Northern Ontario",
                    "sales_owner": "Sales Lead",
                    "bc_owner": "Jason",
                    "release_date": "2026-08-20",
                    "customer_due_date": "2026-09-30",
                    "internal_due_date": "2026-09-25",
                    "anticipated_award_date": "2026-11-15",
                    "estimated_value": "2500000",
                    "currency": "CAD",
                    "classification": "level_3",
                },
            )
            assert status == 303
            bid_path = headers["location"]
            bid_id = bid_path.rsplit("/", 1)[-1]
            status, _, bid_page = await request(
                app.app, "GET", f"{bid_path}/manufacturers-coverage"
            )
            assert status == 200
            assert b"Vendor Document Requirements" in bid_page
            assert b"Add supplier/equipment package" in bid_page
            assert b"post-award execution" not in bid_page

            packages_before = len(app.vendor_document_repository.list_packages())
            audits_before = len(app.bid_repository.list_audit(bid_id=bid_id))
            status, _, invalid = await request(
                app.app,
                "POST",
                "/vendor-documents/packages",
                {
                    "bid_id": "B-2026-9999",
                    "package_name": "Invalid package",
                    "package_code": "BAD",
                    "proposed_manufacturer": "No supplier",
                    "internal_owner": "Jason",
                },
            )
            assert status == 422
            assert b"Selected Bid does not exist" in invalid
            assert b"Invalid package" in invalid
            assert len(app.vendor_document_repository.list_packages()) == packages_before
            assert len(app.bid_repository.list_audit(bid_id=bid_id)) == audits_before

            status, headers, _ = await request(
                app.app,
                "POST",
                "/vendor-documents/packages",
                {
                    "bid_id": bid_id,
                    "package_name": "Low-voltage motor control centre",
                    "package_code": "LV-MCC-01",
                    "customer_epcm": "Example EPCM",
                    "proposed_manufacturer": "Atlas Switchgear",
                    "manufacturer_contact": "Morgan Lee",
                    "internal_owner": "Taylor Engineer",
                    "source_vdrl_reference": "VDRL-ELE-001",
                    "source_revision": "A",
                    "anticipated_award_date": "2026-11-15",
                    "forecast_delivery_date": "2027-06-01",
                    "notes": "Quotation verification",
                },
            )
            assert status == 303
            package_path = headers["location"]
            package_id = package_path.rsplit("/", 1)[-1]
            package = app.vendor_document_repository.get_package(package_id)
            assert package is not None and package.bid_id == bid_id
            status, _, workspace = await request(app.app, "GET", package_path)
            assert status == 200
            assert b"Home</a>" in workspace and b"Bids</a>" in workspace
            assert b"Northline Expansion Bid" in workspace and bid_id.encode() in workspace
            assert b"VDRL compliance register" in workspace
            assert b"Manufacturer Verification" in workspace
            assert b"Record a submission" not in workspace
            assert b"review_code" not in workspace
            assert b"resubmission" not in workspace.lower()

            status, headers, _ = await request(
                app.app,
                "POST",
                f"{package_path}/requirements",
                [
                    ("customer_requirement_code", "A01"),
                    ("deliverable_title", "General arrangement drawing"),
                    ("description", "Customer original description"),
                    ("required", "true"),
                    ("applicable", "true"),
                    ("requested_stages", "WITH_BID"),
                    ("requested_stages", "AFTER_AWARD"),
                    ("timing_anchor", "ANTICIPATED_AWARD"),
                    ("timing_offset_days", "28"),
                    ("original_contractual_timing", "ARA +4 weeks"),
                    ("customer_notes", "Preserve customer note"),
                    ("source_row_reference", "Row 12"),
                    ("source_revision", "A"),
                ],
            )
            assert status == 303
            requirement = app.vendor_document_repository.list_requirements(package_id)[0]
            original = requirement.model_dump()

            status, _, rejected = await request(
                app.app,
                "POST",
                f"/vendor-documents/requirements/{requirement.requirement_id}",
                {
                    "expected_version": str(requirement.version),
                    "verification_status": "CONFIRMED_WITH_EXCEPTION",
                    "proposed_manufacturer": "Atlas Switchgear",
                    "internal_owner": "Taylor Engineer",
                    "response_received_date": "2026-08-21",
                    "commercial_impact": "UNKNOWN",
                    "bid_disposition": "NONE",
                },
            )
            assert status == 422
            assert b"requires a proposed modification or exception" in rejected
            assert b"Atlas Switchgear" in rejected
            assert (
                app.vendor_document_repository.get_requirement(requirement.requirement_id)
                == requirement
            )

            status, headers, _ = await request(
                app.app,
                "POST",
                f"/vendor-documents/requirements/{requirement.requirement_id}",
                [
                    ("expected_version", str(requirement.version)),
                    ("verification_status", "CONFIRMED_WITH_EXCEPTION"),
                    ("proposed_manufacturer", "Atlas Switchgear"),
                    ("response_source", "Email from Morgan Lee"),
                    ("internal_owner", "Taylor Engineer"),
                    ("confirmation_requested_date", "2026-08-20"),
                    ("response_received_date", "2026-08-21"),
                    ("committed_stages", "WITH_BID"),
                    ("committed_stages", "AFTER_AWARD"),
                    ("committed_timing", "With bid and 4 weeks after award"),
                    ("evidence_reference", "EMAIL-42"),
                    ("proposed_exception", "Certified issue follows award"),
                    ("commercial_impact", "INCLUDED"),
                    ("bid_disposition", "QUALIFIED"),
                    ("disposition_approved", "true"),
                    ("deviation_reference", "DEV-12"),
                    ("handover_note", "Execution owner to retain qualification"),
                ],
            )
            assert status == 303
            stored = app.vendor_document_repository.get_requirement(requirement.requirement_id)
            assert stored is not None
            assert stored.customer_requirement_code == original["customer_requirement_code"]
            assert stored.deliverable_title == original["deliverable_title"]
            assert stored.description == original["description"]
            assert stored.requested_stages == original["requested_stages"]
            assert stored.proposed_exception == "Certified issue follows award"

            audit_count = len(app.bid_repository.list_audit(bid_id=bid_id))
            status, _, stale = await request(
                app.app,
                "POST",
                f"/vendor-documents/requirements/{requirement.requirement_id}",
                {
                    "expected_version": str(requirement.version),
                    "verification_status": "CONFIRMED_COMPLIANT",
                    "proposed_manufacturer": "Atlas Switchgear",
                    "internal_owner": "Taylor Engineer",
                    "response_received_date": "2026-08-21",
                    "evidence_reference": "EMAIL-43",
                    "commercial_impact": "NONE",
                    "bid_disposition": "NONE",
                },
            )
            assert status == 422
            assert b"changed in another tab" in stale
            assert len(app.bid_repository.list_audit(bid_id=bid_id)) == audit_count

            mismatch_rows = ["requirement_code,title,required,declared_required_total"]
            mismatch_rows.extend(
                f"R{index:02d},Requirement {index},yes,33" for index in range(1, 38)
            )
            mismatch = "\n".join(mismatch_rows)
            before_import = len(app.vendor_document_repository.list_requirements(package_id))
            status, _, preview = await request(
                app.app,
                "POST",
                f"{package_path}/import/preview",
                upload=("mismatch.csv", mismatch),
            )
            assert status == 422
            assert b"Declared required total 33 does not match 37 marked-required rows" in preview
            assert (
                len(app.vendor_document_repository.list_requirements(package_id)) == before_import
            )

            status, _, handover = await request(app.app, "GET", f"{package_path}/handover.csv")
            assert status == 200
            for expected in (
                b"Northline Expansion Bid",
                b"General arrangement drawing",
                b"WITH_BID | AFTER_AWARD",
                b"Email from Morgan Lee",
                b"Certified issue follows award",
                b"INCLUDED",
                b"QUALIFIED (approved)",
                b"Execution owner to retain qualification",
            ):
                assert expected in handover

            status, _, bid_reload = await request(
                app.app, "GET", f"{bid_path}/manufacturers-coverage"
            )
            assert status == 200
            assert package.package_name.encode() in bid_reload
            assert package_path.encode() in bid_reload
            status, _, global_page = await request(
                app.app, "GET", f"/vendor-documents?bid_id={bid_id}"
            )
            assert status == 200
            assert b"Bid-stage supplier commitments" in global_page
            assert b"post-award document execution" in global_page
        finally:
            socket.create_connection = original_connection
    print("OPS-05B ASGI acceptance: PASS")


if __name__ == "__main__":
    asyncio.run(main())
