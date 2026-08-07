"""Dependency-free in-process ASGI acceptance for TASK-12."""

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


class Client:
    def __init__(self, application: Any) -> None:
        self.application = application

    async def request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, bytes]:
        body = json.dumps(payload).encode() if payload is not None else b""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        sent: list[dict[str, Any]] = []
        await queue.put({"type": "http.request", "body": body, "more_body": False})

        async def receive() -> dict[str, Any]:
            return await queue.get()

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await self.application(
            {
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
            },
            receive,
            send,
        )
        start = next(message for message in sent if message["type"] == "http.response.start")
        return int(start["status"]), b"".join(
            message.get("body", b"") for message in sent if message["type"] == "http.response.body"
        )


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="contractiq-task12-asgi-") as root:
        os.environ["CONTRACTIQ_DB_PATH"] = str(Path(root) / "acceptance.db")
        os.environ["CONTRACTIQ_DOCUMENT_ROOT"] = str(Path(root) / "managed")
        sys.path.insert(0, str(Path(__file__).parents[1]))
        import app  # noqa: PLC0415
        from core.enums import BidLevel, CustomerType  # noqa: PLC0415
        from core.schemas import Bid  # noqa: PLC0415

        now = datetime.now(UTC)
        bid_id = "B-2026-0012"
        app.bid_repository.create_bid(
            Bid(
                bid_id=bid_id,
                customer="Synthetic",
                customer_type=CustomerType.EPC,
                project_name="Vendor acceptance",
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
        client = Client(app.app)
        status, _ = await client.request("GET", "/deliverables")
        assert status == 200
        provenance = {
            "created_by": "human",
            "agent_name": "acceptance",
            "created_at": now.isoformat(),
            "human_confirmed": True,
            "confirmed_by": "acceptance",
            "confirmed_at": now.isoformat(),
        }
        status, body = await client.request(
            "POST",
            "/api/deliverables",
            {
                "bid_id": bid_id,
                "title": "Synthetic deliverable",
                "description": "Provide schedule",
                "category": "SCHEDULE",
                "criticality": "MANDATORY",
                "lifecycle_phase": "WITH_BID",
                "direction": "INTERNAL",
                "due_basis": "UNSCHEDULED",
                "owner": "owner",
                "recipient": "customer",
                "provenance": provenance,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "created_by": "human",
            },
        )
        assert status == 201, body
        deliverable_id = json.loads(body)["deliverable_id"]
        status, _ = await client.request("GET", f"/deliverables/{deliverable_id}")
        assert status == 200
        status, _ = await client.request("GET", f"/deliverables/{deliverable_id}/history")
        assert status == 200
        print("TASK-12 ASGI acceptance: PASS")


if __name__ == "__main__":
    asyncio.run(main())
