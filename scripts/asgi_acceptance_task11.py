"""Dependency-free in-process ASGI acceptance oracle for TASK-11F."""
# SQL fixture mirrors the authoritative scope schema.
# ruff: noqa: E501

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


class ASGIClient:
    def __init__(self, application: Any) -> None:
        self.application = application

    async def request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, bytes, dict[str, str]]:
        body = json.dumps(payload).encode() if payload is not None else b""
        incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        outgoing: list[dict[str, Any]] = []
        await incoming.put({"type": "http.request", "body": body, "more_body": False})

        async def receive() -> dict[str, Any]:
            return await incoming.get()

        async def send(message: dict[str, Any]) -> None:
            outgoing.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(b"host", b"localhost"), (b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1),
            "server": ("localhost", 80),
            "scheme": "http",
        }
        await self.application(scope, receive, send)
        start = next(message for message in outgoing if message["type"] == "http.response.start")
        chunks = [
            message.get("body", b"")
            for message in outgoing
            if message["type"] == "http.response.body"
        ]
        return (
            int(start["status"]),
            b"".join(chunks),
            {key.decode().lower(): value.decode() for key, value in start["headers"]},
        )


async def lifespan(application: Any) -> None:
    incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    outgoing: list[dict[str, Any]] = []
    await incoming.put({"type": "lifespan.startup"})

    async def receive() -> dict[str, Any]:
        return await incoming.get()

    async def send(message: dict[str, Any]) -> None:
        outgoing.append(message)

    task = asyncio.create_task(
        application({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)
    )
    while not any(
        message["type"] in {"lifespan.startup.complete", "lifespan.startup.failed"}
        for message in outgoing
    ):
        await asyncio.sleep(0)
    if any(message["type"] == "lifespan.startup.failed" for message in outgoing):
        raise RuntimeError("application lifespan startup failed")
    await incoming.put({"type": "lifespan.shutdown"})
    await task


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="contractiq-task11f-") as root:
        os.environ["CONTRACTIQ_DB_PATH"] = str(Path(root) / "acceptance.db")
        os.environ["CONTRACTIQ_DOCUMENT_ROOT"] = str(Path(root) / "managed")
        sys.path.insert(0, str(Path(__file__).parents[1]))
        import app  # noqa: PLC0415
        from core.enums import BidLevel, CustomerType  # noqa: PLC0415
        from core.schemas import Bid  # noqa: PLC0415

        now = datetime.now(UTC)
        bid_id = "B-2026-0011"
        app.bid_repository.create_bid(
            Bid(
                bid_id=bid_id,
                customer="Synthetic",
                customer_type=CustomerType.EPC,
                project_name="Synthetic acceptance",
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
        with app.db._conn() as conn:
            conn.execute(
                "INSERT INTO scope_interface_items(scope_item_id,bid_id,title,description,scope_area,origin,customer_need,offer_position,pricing_state,responsible_party,owner,due_date,materiality,assumption_exclusion_note,evidence_decision_note,work_state,review_state,reviewer,review_note,lifecycle_state,created_at,updated_at,version,provenance_json,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "ASGI-SCOPE",
                    bid_id,
                    "Synthetic scope",
                    "Description",
                    "CORE_PRODUCTS",
                    "INTERNAL",
                    "REQUIRED",
                    "INCLUDED",
                    "UNCONFIRMED",
                    "SUPPLIER",
                    "owner",
                    None,
                    "MATERIAL",
                    None,
                    None,
                    "OPEN",
                    "NOT_REVIEWED",
                    None,
                    None,
                    "ACTIVE",
                    now.isoformat(),
                    now.isoformat(),
                    1,
                    json.dumps(
                        {
                            "created_by": "human",
                            "created_at": now.isoformat(),
                            "human_confirmed": False,
                        }
                    ),
                    "human",
                ),
            )
            conn.commit()
        client = ASGIClient(app.app)
        await lifespan(app.app)
        for path in [
            "/",
            f"/bids/{bid_id}",
            "/suppliers",
            "/requirements",
            "/scope-interfaces",
            "/my-day",
            "/documents",
            "/knowledge",
        ]:
            status, body, _ = await client.request("GET", path)
            if status != 200 or b"Traceback" in body:
                raise AssertionError(f"unsafe route response {path}: {status}")
        provenance = {
            "created_by": "human",
            "agent_name": "acceptance",
            "created_at": now.isoformat(),
            "human_confirmed": True,
            "confirmed_by": "acceptance",
            "confirmed_at": now.isoformat(),
        }
        supplier_id = "ASGI-SUPPLIER"
        status, body, _ = await client.request(
            "POST",
            "/api/suppliers",
            {
                "supplier_id": supplier_id,
                "bid_id": bid_id,
                "supplier_name": "Synthetic supplier",
                "provenance": provenance,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "created_by": "human",
            },
        )
        if status != 201:
            raise AssertionError(f"supplier create failed: {status} {body!r}")
        request_id = "ASGI-REQUEST"
        item_id = "ASGI-ITEM"
        status, body, _ = await client.request(
            "POST",
            "/api/supplier-requests",
            {
                "request_id": request_id,
                "bid_id": bid_id,
                "supplier_id": supplier_id,
                "request_type": "REQUEST_FOR_QUOTE",
                "title": "Synthetic RFQ",
                "purpose": "Synthetic purpose",
                "owner": "owner",
                "provenance": provenance,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "created_by": "human",
                "items": [
                    {
                        "request_item_id": item_id,
                        "request_id": request_id,
                        "bid_id": bid_id,
                        "sequence": 1,
                        "title": "Synthetic scope",
                        "confirmation_text": "Confirm",
                        "topic": "SCOPE_SUPPLY",
                        "support_role": "REQUIRED_SUPPORT",
                    }
                ],
            },
        )
        if status != 201:
            raise AssertionError(f"request create failed: {status} {body!r}")
        status, body, _ = await client.request(
            "POST",
            "/api/supplier-flow-down",
            {
                "request_item_id": item_id,
                "bid_id": bid_id,
                "target_type": "SCOPE_ITEM",
                "target_id": "ASGI-SCOPE",
                "created_at": now.isoformat(),
                "created_by": "human",
            },
        )
        if status != 201:
            raise AssertionError(f"flow-down create failed: {status} {body!r}")
        status, body, _ = await client.request(
            "POST", f"/api/supplier-requests/{request_id}/issue", {"expected_version": 1}
        )
        if status != 200:
            raise AssertionError(f"request issue failed: {status} {body!r}")
        status, _, _ = await client.request("GET", "/api/suppliers")
        if status != 200:
            raise AssertionError("supplier API did not respond")
    print("TASK-11F ASGI acceptance: PASS")


if __name__ == "__main__":
    asyncio.run(main())
