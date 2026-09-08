"""Dependency-free in-process ASGI acceptance for OPS-07 browser behavior."""

from __future__ import annotations

import asyncio
import csv
import io
import os
import socket
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit


async def request(
    application: Any, method: str, path: str, form: dict[str, str] | None = None
) -> tuple[int, dict[str, str], bytes]:
    sent: list[dict[str, Any]] = []
    body = urlencode(form or {}).encode()
    incoming = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive() -> dict[str, Any]:
        return incoming.pop(0) if incoming else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    parsed = urlsplit(path)
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
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")]
            if method == "POST"
            else [],
        },
        receive,
        send,
    )
    start = next(message for message in sent if message["type"] == "http.response.start")
    headers = {
        bytes(key).decode().lower(): bytes(value).decode() for key, value in start["headers"]
    }
    response = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return int(start["status"]), headers, response


async def create_bid(application: Any) -> str:
    status, headers, _ = await request(
        application,
        "POST",
        "/bids",
        {
            "project_name": "OPS-07 Bid",
            "customer": "Example EPCM",
            "customer_type": "epcm",
            "sales_owner": "Sales",
            "bc_owner": "Jason",
            "release_date": "2026-08-20",
            "customer_due_date": "2026-09-30",
            "internal_due_date": "2026-09-25",
            "estimated_value": "1000000",
            "currency": "CAD",
            "classification": "level_3",
            "is_epc_epcm": "1",
        },
    )
    assert status == 303
    return headers["location"].rsplit("/", 1)[-1]


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    with tempfile.TemporaryDirectory(prefix="contractiq-ops07-asgi-") as directory:
        os.environ["CONTRACTIQ_DB_PATH"] = str(Path(directory) / "app.db")
        os.environ["CONTRACTIQ_DOCUMENT_ROOT"] = str(Path(directory) / "documents")
        original_connection = socket.create_connection
        socket.create_connection = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("OPS-07 attempted a network connection")
        )
        try:
            import app
            from core.schemas import Provenance
            from core.scope_interfaces import Materiality, ScopeArea, ScopeItem, ScopeOrigin
            from core.vendor_document_control import (
                CustomerRequirementCreate,
                SupplierPackageCreate,
            )

            bid_id = await create_bid(app.app)
            requirement = app.requirement_service.create_requirement(
                {
                    "bid_id": bid_id,
                    "title": "Formula-safe requirement",
                    "statement": '=HYPERLINK("https://example.invalid","x")',
                    "origin": "INTERNAL",
                    "category": "TECHNICAL",
                    "significance": "MANDATORY",
                },
                "Jason",
            )
            now = datetime.now(UTC)
            scope = ScopeItem(
                bid_id=bid_id,
                title="Scope",
                description="Scope",
                scope_area=ScopeArea.CORE_PRODUCTS,
                origin=ScopeOrigin.INTERNAL,
                materiality=Materiality.MATERIAL,
                created_at=now,
                updated_at=now,
                provenance=Provenance.from_human("Jason"),
                created_by="Jason",
            )
            app.scope_service.create_scope_item(scope, "Jason")
            package = app.vendor_document_service.create_package(
                SupplierPackageCreate(
                    bid_id=bid_id,
                    package_name="Package",
                    package_code="PKG-1",
                    proposed_manufacturer="Manufacturer",
                    internal_owner="Jason",
                ),
                "Jason",
            )
            vendor_row = app.vendor_document_service.create_requirement(
                CustomerRequirementCreate(
                    package_id=package.package_id,
                    customer_requirement_code="C-1",
                    deliverable_title="Confirmation",
                ),
                "Jason",
            )
            with app.db._conn() as conn:
                conn.execute(
                    "UPDATE vendor_bid_requirements SET verification_status='CONFIRMED_COMPLIANT', "
                    "response_received_date=? WHERE requirement_id=?",
                    ("2026-08-21", vendor_row.requirement_id),
                )

            hydrated_vendor_row = app.vendor_document_repository.get_requirement(
                vendor_row.requirement_id
            )
            assert hydrated_vendor_row is not None
            assert hydrated_vendor_row.response_received_date is not None
            assert hydrated_vendor_row.response_received_date.isoformat() == "2026-08-21"
            status, headers, package_body = await request(
                app.app, "GET", f"/vendor-documents/packages/{package.package_id}"
            )
            assert status == 200 and headers["content-type"].startswith("text/html")
            assert b'value="2026-08-21"' in package_body

            before_audit = len(app.bid_repository.list_audit())
            for path in (
                f"/bids/{bid_id}/requirements-scope",
                f"/requirements/{requirement.requirement_id}",
            ):
                status, headers, body = await request(app.app, "GET", path)
                assert status == 200 and headers["content-type"].startswith("text/html") and body
            assert len(app.bid_repository.list_audit()) == before_audit

            scope_path = f"/requirements/{requirement.requirement_id}/scope-links"
            status, headers, _ = await request(
                app.app, "POST", scope_path, {"scope_item_id": scope.scope_item_id}
            )
            assert status == 303 and headers["location"].endswith("#coverage")
            after_link = len(app.bid_repository.list_audit())
            links_before = app.scope_repository.requirement_links(
                requirement_id=requirement.requirement_id
            )
            status, headers, body = await request(
                app.app, "POST", scope_path, {"scope_item_id": scope.scope_item_id}
            )
            assert status == 422 and headers["content-type"].startswith("text/html")
            assert b"already linked" in body and b'{"detail"' not in body
            assert (
                app.scope_repository.requirement_links(requirement_id=requirement.requirement_id)
                == links_before
            )
            assert len(app.bid_repository.list_audit()) == after_link
            unlink = f"{scope_path}/{scope.scope_item_id}/remove"
            assert (await request(app.app, "POST", unlink))[0] == 303
            after_unlink = len(app.bid_repository.list_audit())
            assert (await request(app.app, "POST", unlink))[0] == 404
            assert len(app.bid_repository.list_audit()) == after_unlink

            app.ops07_repository.link_manufacturer(
                bid_id, requirement.requirement_id, package.package_id, "Jason"
            )
            app.ops07w_repository.link_exact_evidence(
                bid_id, requirement.requirement_id, vendor_row.requirement_id, "Jason"
            )
            status, headers, content = await request(
                app.app, "GET", f"/bids/{bid_id}/requirements-handover.csv"
            )
            assert status == 200 and headers["content-type"].startswith("text/csv")
            row = next(csv.DictReader(io.StringIO(content.decode())))
            assert row["manufacturer_verification"] == "CONFIRMED_COMPLIANT"
            assert row["readiness_state"] == "NOT_READY"
            assert row["original_requirement"].startswith("'=")
            assert "lacks response source" in row["unresolved_action"]
            assert (await request(app.app, "GET", "/bids/B-2099-9999"))[0] == 404
            assert (await request(app.app, "GET", "/requirements/REQ-MISSING"))[0] == 404
            assert len(app.bid_repository.list_audit()) == after_unlink + 2
            print("OPS-07 ASGI acceptance: PASS")
        finally:
            socket.create_connection = original_connection


if __name__ == "__main__":
    asyncio.run(main())
