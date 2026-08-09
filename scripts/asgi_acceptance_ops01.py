"""Dependency-free in-process ASGI acceptance for OPS-01."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


async def request(app: Any, path: str, method: str = "GET", body: bytes = b"") -> tuple[int, bytes]:
    sent: list[dict[str, Any]] = []
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive() -> dict[str, Any]:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    parsed = urlsplit(path)
    await app(
        {
            "type": "http",
            "method": method,
            "path": parsed.path,
            "query_string": parsed.query.encode(),
            "headers": [],
            "scheme": "http",
            "server": ("127.0.0.1", 0),
            "client": ("127.0.0.1", 1),
            "http_version": "1.1",
        },
        receive,
        send,
    )
    status = next(
        int(message["status"]) for message in sent if message["type"] == "http.response.start"
    )
    payload = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return status, payload


async def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with tempfile.TemporaryDirectory(prefix="contractiq-ops01-asgi-") as directory:
        os.environ["CONTRACTIQ_DB_PATH"] = str(Path(directory) / "app.db")
        import app

        for path in (
            "/my-work",
            "/role-framework",
            "/my-day",
            "/api/ops/role-profiles",
            "/api/ops/work",
        ):
            status, body = await request(app.app, path)
            assert status == 200 and b"Traceback" not in body
        status, body = await request(
            app.app, "/api/work-items", "POST", b'{"title":"Synthetic capture","category":"OTHER"}'
        )
        assert status == 201, (status, body)
        status, body = await request(app.app, "/api/ops/work?unassigned=true")
        assert status == 200, (status, body)
    print("OPS-01 ASGI acceptance: PASS")


if __name__ == "__main__":
    asyncio.run(main())
