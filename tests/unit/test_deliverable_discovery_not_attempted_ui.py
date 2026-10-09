"""UI coverage for cancelled discovery work that never reached the model."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import ModuleType

import pytest

from scripts.asgi_acceptance_ops08 import request
from tests.unit.test_deliverable_discovery_job_routes import BID_ID, _include, _job_id, _start
from tests.unit.test_deliverable_discovery_job_routes import job_app as _job_app
from tests.unit.test_deliverable_discovery_jobs_recovery import PASSAGE
from tests.unit.test_deliverable_discovery_run_outcomes import FakeClient
from tests.unit.test_deliverable_discovery_service import _pdf_bytes


class _CancelAfterFirstClient(FakeClient):
    cancel: object = None

    def complete_json(self, prompt: str, **kwargs: object) -> object:
        result = super().complete_json(prompt, **kwargs)
        if callable(self.cancel):
            callback, self.cancel = self.cancel, None
            callback()
        return result


@pytest.fixture
def not_attempted_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    yield from _job_app.__wrapped__(tmp_path, monkeypatch)


def test_progress_and_discovery_pages_name_not_attempted_work(
    not_attempted_app: ModuleType,
) -> None:
    async def scenario() -> None:
        await _include(
            not_attempted_app,
            "Cancel-UI",
            {
                "a-first.pdf": _pdf_bytes([PASSAGE]),
                "b-never-called.pdf": _pdf_bytes([PASSAGE + " Second file."]),
            },
        )
        status, headers, _ = await _start(not_attempted_app, "cancel-ui")
        assert status == 303
        job_id = _job_id(headers["location"])
        client = _CancelAfterFirstClient()
        client.cancel = lambda: not_attempted_app.discovery_job_service.cancel_job(job_id)
        not_attempted_app.discovery_job_runner.llm_client = client

        not_attempted_app.discovery_job_runner.run_pending()

        status, _, progress = await request(not_attempted_app.app, "GET", headers["location"])
        assert status == 200
        assert b"1 not reached" in progress
        assert b"b-never-called.pdf" in progress
        assert b"Not analysed yet" in progress
        assert b"MODEL_UNAVAILABLE" not in progress

        status, _, discovery = await request(
            not_attempted_app.app,
            "GET",
            f"/bids/{BID_ID}/deliverable-discovery",
        )
        assert status == 200
        assert b"1 not yet analysed" in discovery
        assert b"b-never-called.pdf" in discovery
        assert b"Not analysed yet" in discovery
        assert b"MODEL_UNAVAILABLE" not in discovery

    asyncio.run(scenario())
