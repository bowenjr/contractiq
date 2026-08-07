"""Dependency-free in-process ASGI acceptance for TASK-14."""

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
    def __init__(self, app: Any) -> None:
        self.app = app

    async def request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, bytes]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        out: list[dict[str, Any]] = []
        await q.put(
            {
                "type": "http.request",
                "body": json.dumps(payload).encode() if payload else b"",
                "more_body": False,
            }
        )

        async def receive() -> dict[str, Any]:
            return await q.get()

        async def send(m: dict[str, Any]) -> None:
            out.append(m)

        await self.app(
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
        start = next(m for m in out if m["type"] == "http.response.start")
        return int(start["status"]), b"".join(
            m.get("body", b"") for m in out if m["type"] == "http.response.body"
        )


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="contractiq-task14-asgi-") as root:
        os.environ["CONTRACTIQ_DB_PATH"] = str(Path(root) / "acceptance.db")
        os.environ["CONTRACTIQ_DOCUMENT_ROOT"] = str(Path(root) / "managed")
        sys.path.insert(0, str(Path(__file__).parents[1]))
        import app
        from core.enums import BidLevel, CustomerType
        from core.schemas import Bid

        now = datetime.now(UTC)
        bid = "B-2026-0014"
        app.bid_repository.create_bid(
            Bid(
                bid_id=bid,
                customer="Synthetic",
                customer_type=CustomerType.EPC,
                project_name="Risk acceptance",
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
            "/contract-risks",
            f"/bids/{bid}",
        ):
            status, body = await client.request("GET", path)
            assert status == 200 and b"Traceback" not in body
        status, body = await client.request("GET", "/api/contract-risks")
        assert status == 200 and b"metrics" in body
        print("TASK-14 ASGI acceptance: PASS")


if __name__ == "__main__":
    asyncio.run(main())
