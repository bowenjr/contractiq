"""Dependency-free in-process ASGI acceptance for TASK-15."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


class Client:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def request(self, method: str, path: str) -> tuple[int, bytes]:
        messages: list[dict[str, Any]] = []
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        await queue.put({"type": "http.request", "body": b"", "more_body": False})

        async def receive() -> dict[str, Any]:
            return await queue.get()

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        await self.app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": method,
                "path": path,
                "raw_path": path.encode(),
                "query_string": b"",
                "headers": [(b"host", b"localhost")],
                "client": ("127.0.0.1", 1),
                "server": ("localhost", 80),
                "scheme": "http",
            },
            receive,
            send,
        )
        start = next(message for message in messages if message["type"] == "http.response.start")
        body = b"".join(message.get("body", b"") for message in messages)
        return int(start["status"]), body


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="contractiq-task15-asgi-") as root:
        os.environ["CONTRACTIQ_DB_PATH"] = str(Path(root) / "acceptance.db")
        os.environ["CONTRACTIQ_DOCUMENT_ROOT"] = str(Path(root) / "managed")
        sys.path.insert(0, str(Path(__file__).parents[1]))
        import app

        client = Client(app.app)
        for path in (
            "/",
            "/my-day",
            "/requirements",
            "/scope-interfaces",
            "/suppliers",
            "/deliverables",
            "/commercial",
            "/contract-risks",
            "/decisions",
        ):
            status, body = await client.request("GET", path)
            assert status == 200 and b"Traceback" not in body
        status, body = await client.request("GET", "/api/decisions")
        assert status == 200 and b"policies" in body
    print("TASK-15 ASGI acceptance: PASS")


if __name__ == "__main__":
    asyncio.run(main())
