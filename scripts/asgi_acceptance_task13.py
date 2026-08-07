"""Dependency-free in-process ASGI acceptance for TASK-13."""

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
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        sent: list[dict[str, Any]] = []
        await queue.put(
            {
                "type": "http.request",
                "body": json.dumps(payload).encode() if payload is not None else b"",
                "more_body": False,
            }
        )

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
        start = next(m for m in sent if m["type"] == "http.response.start")
        return int(start["status"]), b"".join(
            m.get("body", b"") for m in sent if m["type"] == "http.response.body"
        )


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="contractiq-task13-asgi-") as root:
        os.environ["CONTRACTIQ_DB_PATH"] = str(Path(root) / "acceptance.db")
        os.environ["CONTRACTIQ_DOCUMENT_ROOT"] = str(Path(root) / "managed")
        sys.path.insert(0, str(Path(__file__).parents[1]))
        import app
        from core.enums import BidLevel, CustomerType
        from core.schemas import Bid

        now = datetime.now(UTC)
        bid_id = "B-2026-0013"
        app.bid_repository.create_bid(
            Bid(
                bid_id=bid_id,
                customer="Synthetic",
                customer_type=CustomerType.EPC,
                project_name="Commercial acceptance",
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
        for path in (
            "/",
            "/my-day",
            "/documents",
            "/requirements",
            "/scope-interfaces",
            "/suppliers",
            "/deliverables",
            "/commercial",
            f"/bids/{bid_id}",
        ):
            status, body = await client.request("GET", path)
            assert status == 200 and b"Traceback" not in body
        status, _ = await client.request(
            "POST", "/api/commercial/initialize", {"bid_id": bid_id, "actor": "acceptance"}
        )
        assert status == 200
        status, _ = await client.request(
            "POST", "/api/commercial/initialize", {"bid_id": bid_id, "actor": "acceptance"}
        )
        assert status == 200
        status, body = await client.request("GET", "/api/commercial")
        assert status == 200 and b"metrics" in body
        print("TASK-13 ASGI acceptance: PASS")


if __name__ == "__main__":
    asyncio.run(main())
