"""Dependency-free in-process ASGI acceptance for TASK-18."""

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

    async def get(self, path: str) -> tuple[int, bytes]:
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
                "method": "GET",
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
        start = next(item for item in messages if item["type"] == "http.response.start")
        return int(start["status"]), b"".join(item.get("body", b"") for item in messages)


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="contractiq-task18-asgi-") as root:
        os.environ["CONTRACTIQ_DB_PATH"] = str(Path(root) / "acceptance.db")
        os.environ["CONTRACTIQ_DOCUMENT_ROOT"] = str(Path(root) / "managed")
        sys.path.insert(0, str(Path(__file__).parents[1]))
        import app

        client = Client(app.app)
        for path in (
            "/",
            "/my-day",
            "/commercial-scenarios",
            "/negotiations",
            "/proposals",
            "/decisions",
            "/api/proposals",
        ):
            status, body = await client.get(path)
            assert status == 200 and b"Traceback" not in body
    print("TASK-18 ASGI acceptance: PASS")


if __name__ == "__main__":
    asyncio.run(main())
