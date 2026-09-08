"""Dependency-free, socketless OPS-08 representative browser acceptance."""

from __future__ import annotations

import asyncio
import os
import re
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit


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
            (b"content-type", (content_type or "application/x-www-form-urlencoded").encode())
        ]
    await application(
        {
            "type": "http",
            "method": method,
            "path": parsed.path,
            "query_string": parsed.query.encode(),
            "headers": headers,
            "scheme": "http",
            "server": ("127.0.0.1", 0),
            "client": ("127.0.0.1", 1),
            "http_version": "1.1",
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
    return int(start["status"]), response_headers, response_body


def multipart(fields: dict[str, str], filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = "contractiq-ops08-boundary"
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
    with tempfile.TemporaryDirectory(prefix="contractiq-ops08-asgi-") as directory:
        os.environ["CONTRACTIQ_DB_PATH"] = str(Path(directory) / "app.db")
        os.environ["CONTRACTIQ_DOCUMENT_ROOT"] = str(Path(directory) / "documents")
        original_connection = socket.create_connection
        socket.create_connection = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("OPS-08 attempted a network connection")
        )
        try:
            import app

            status, headers, _ = await request(
                app.app,
                "POST",
                "/bids",
                {
                    "project_name": "OPS-08 Commercial Acceptance",
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
            assert status == 303
            bid_id = headers["location"].rsplit("/", 1)[-1]
            status, _, page = await request(app.app, "GET", f"/commercial?bid_id={bid_id}")
            assert status == 200 and b"Commercial review not started" in page
            audit_before_get = len(app.bid_repository.list_audit(bid_id))
            status, _, _ = await request(
                app.app,
                "POST",
                "/commercial",
                {"bid_id": bid_id, "operation": "initialize", "default_owner": "Jason"},
            )
            assert status == 303
            positions = {row["topic_key"]: row for row in app.ops08_repository.workspace(bid_id)}
            assert len(positions) >= 30

            payment = positions["PAYMENT_TERMS"]
            status, _, detail = await request(
                app.app, "GET", f"/commercial/{payment['position_id']}"
            )
            assert status == 200 and b"original position" in detail
            token_marker = b'name="source_operation_token" value="'
            token = detail.split(token_marker, 1)[1].split(b'"', 1)[0].decode()
            retained = {
                "expected_version": "1",
                "source_operation_token": token,
                "customer_position": "Net 90 after delivery",
                "proposed_position": (
                    "Milestone billing with approved-document and delivery events"
                ),
                "disposition": "QUALIFY",
                "source_locator": "Commercial terms section 7.2, page 18",
                "rationale": "Protect cash flow",
                "owner": "Jason",
                "negotiation_state": "IN_PROGRESS",
                "new_source_title": "Customer Commercial Terms",
                "new_source_version_label": "Revision 0",
            }
            source_body, source_type = multipart(retained, "customer-terms.pdf", b"%PDF OPS08")
            status, headers, _ = await request(
                app.app,
                "POST",
                f"/commercial/positions/{payment['position_id']}/with-source",
                body=source_body,
                content_type=source_type,
            )
            assert status == 303 and "form_state=" in headers["location"]
            status, _, returned = await request(app.app, "GET", headers["location"])
            assert status == 200 and b"Net 90 after delivery" in returned
            assert b"Customer source registered and selected" in returned
            selected_source = re.search(rb'<option value="([^"]+)" selected>', returned)
            assert selected_source is not None
            source_id = selected_source.group(1).decode()

            terms = {
                "PAYMENT_TERMS": (
                    "Net 90 after delivery",
                    "Milestone billing with approved-document and delivery events",
                    "",
                ),
                "LIQUIDATED_DAMAGES": (
                    "1% per week, uncapped",
                    "0.5% per week, capped at 10% of affected equipment value",
                    "VP Commercial",
                ),
                "WARRANTY": (
                    "24 months after commissioning",
                    "Manufacturer standard warranty from shipment; extension separately priced",
                    "",
                ),
                "LIMITATION_LIABILITY": (
                    "Unlimited",
                    "Aggregate cap at contract value with standard exclusions",
                    "Legal Director",
                ),
                "ESCALATION": (
                    "Fixed price for long project duration",
                    "Currency and commodity adjustment mechanism",
                    "",
                ),
            }
            for key, (customer, proposed, approver) in terms.items():
                row = positions[key]
                status, _, _ = await request(
                    app.app,
                    "POST",
                    f"/commercial/positions/{row['position_id']}",
                    {
                        "expected_version": "1",
                        "customer_position": customer,
                        "proposed_position": proposed,
                        "disposition": "QUALIFY",
                        "source_document_version_id": source_id,
                        "source_locator": f"Commercial terms · {key}",
                        "rationale": "Bid qualification required",
                        "owner": "Jason",
                        "required_approver": approver,
                        "negotiation_state": "IN_PROGRESS",
                    },
                )
                assert status == 303

            status, csv_headers, proposal = await request(
                app.app, "GET", f"/commercial-proposal-input.csv?bid_id={bid_id}"
            )
            assert status == 200 and csv_headers["content-type"].startswith("text/csv")
            assert b"Net 90 after delivery" in proposal and b"Unlimited" in proposal
            assert f'href="/bids/{bid_id}/handover"'.encode() in page
            assert len(app.bid_repository.list_audit(bid_id)) > audit_before_get
        finally:
            socket.create_connection = original_connection
    print("OPS-08 ASGI acceptance: PASS")


if __name__ == "__main__":
    asyncio.run(main())
