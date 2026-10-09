"""Shared ASGI helper for the durable discovery POST/worker/redirect flow."""

from __future__ import annotations

from types import ModuleType

from scripts.asgi_acceptance_ops08 import request


async def post_run_and_finish(
    app: ModuleType,
    bid_id: str,
    operation_id: str,
    *,
    force_all: bool = False,
) -> tuple[str, bytes]:
    form = {"operation_id": operation_id}
    if force_all:
        form["force_all"] = "1"
    status, headers, body = await request(
        app.app,
        "POST",
        f"/bids/{bid_id}/deliverable-discovery/run",
        form,
    )
    assert status == 303, body[:800]
    location = headers["location"]
    app.discovery_job_runner.run_pending()
    status, _, page = await request(app.app, "GET", location)
    assert status == 200, page[:800]
    return location, page
