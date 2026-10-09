"""Recovery, cancellation, and endpoint-error ASGI discovery route scenarios."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from types import ModuleType

import pytest

from core.deliverable_discovery_jobs import DiscoveryJobRunner, DiscoveryJobService
from scripts.asgi_acceptance_ops08 import request
from tests.unit.test_bid_package_governing_ui import governing_ui_app as _app_fixture
from tests.unit.test_deliverable_discovery_job_routes import (
    BID_ID,
    PASSAGE,
    _include,
    _job_id,
    _start,
)
from tests.unit.test_deliverable_discovery_run_outcomes import FakeClient
from tests.unit.test_deliverable_discovery_service import _pdf_bytes


@pytest.fixture
def recovery_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    yield from _app_fixture.__wrapped__(tmp_path, monkeypatch)


def test_restart_recovers_without_repeating_passages_or_proposals(recovery_app: ModuleType) -> None:
    async def scenario() -> None:
        await _include(
            recovery_app,
            "Restart",
            {"bid.pdf": _pdf_bytes([PASSAGE, PASSAGE + " Second."])},
        )
        status, headers, _ = await _start(recovery_app, "restart")
        assert status == 303
        job_id = _job_id(headers["location"])
        service = recovery_app.discovery_job_service
        row = service.jobs.files(job_id)[0]
        now = service.clock()
        assert service.jobs.claim(job_id, "dead-process", now, now + timedelta(minutes=15))
        service.jobs.set_totals(job_id, "dead-process", 2, now, now + timedelta(minutes=15))
        service.jobs.record_passage(
            job_id,
            "dead-process",
            {
                "passage_key": f"{row['received_file_id']}|PAGE|p.1",
                "received_file_id": row["received_file_id"],
                "locator_type": "PAGE",
                "locator": "p.1",
                "outcome": "ANALYSED",
                "candidates_json": "[]",
                "invalid_candidate_count": 0,
                "safe_reason": None,
            },
            False,
            now,
            now - timedelta(seconds=1),
        )
        client = FakeClient()
        second_service = DiscoveryJobService(
            recovery_app.deliverable_discovery_repository,
            recovery_app.bid_package_repository,
            job_repository=service.jobs,
        )
        second_runner = DiscoveryJobRunner(second_service, client)
        assert second_runner.run_pending() == 1
        assert client.calls == 1
        status, _, page = await request(recovery_app.app, "GET", headers["location"])
        assert status == 200 and b"Analysis complete" in page
        with recovery_app.db._conn() as conn:
            assert (
                conn.execute("SELECT count(*) FROM deliverable_discovery_runs").fetchone()[0] == 1
            )
            assert (
                conn.execute("SELECT count(*) FROM deliverable_discovery_proposals").fetchone()[0]
                == 1
            )

    asyncio.run(scenario())


def test_cancel_route_retains_evidence_without_deliverables(recovery_app: ModuleType) -> None:
    async def scenario() -> None:
        await _include(
            recovery_app,
            "Cancel",
            {"bid.pdf": _pdf_bytes([PASSAGE, PASSAGE + " Second."])},
        )
        status, headers, _ = await _start(recovery_app, "cancel")
        assert status == 303
        job_id = _job_id(headers["location"])
        runner = recovery_app.discovery_job_runner
        service = recovery_app.discovery_job_service
        row = service.jobs.files(job_id)[0]
        now = service.clock()
        assert service.jobs.claim(job_id, runner.owner, now, now + timedelta(minutes=15))
        service.jobs.set_totals(job_id, runner.owner, 2, now, now + timedelta(minutes=15))
        service.jobs.record_passage(
            job_id,
            runner.owner,
            {
                "passage_key": f"{row['received_file_id']}|PAGE|p.1",
                "received_file_id": row["received_file_id"],
                "locator_type": "PAGE",
                "locator": "p.1",
                "outcome": "ANALYSED",
                "candidates_json": "[]",
                "invalid_candidate_count": 0,
                "safe_reason": None,
            },
            False,
            now,
            now + timedelta(minutes=15),
        )
        status, headers, _ = await request(
            recovery_app.app,
            "POST",
            f"/bids/{BID_ID}/deliverable-discovery/jobs/{job_id}/cancel",
        )
        assert status == 303
        runner._process_claimed(job_id)
        job = service.get_job(job_id)
        assert job and (job["state"], job["passages_done"]) == ("CANCELLED", 1)
        assert len(service.jobs.passages(job_id)) == 1
        with recovery_app.db._conn() as conn:
            assert conn.execute("SELECT count(*) FROM deliverable_items").fetchone()[0] == 0

    asyncio.run(scenario())


def test_dead_endpoint_alert_resume_and_safe_error_escape(recovery_app: ModuleType) -> None:
    async def scenario() -> None:
        await _include(
            recovery_app,
            "Endpoint",
            {"bid.pdf": _pdf_bytes([PASSAGE + str(index) for index in range(5)])},
        )
        client = FakeClient("unreachable")
        client.base_url = "http://127.0.0.1:8080/v1"
        recovery_app.discovery_job_runner.llm_client = client
        status, headers, _ = await _start(recovery_app, "endpoint")
        assert status == 303
        job_id = _job_id(headers["location"])
        recovery_app.discovery_job_runner.run_pending()
        assert client.calls == 3
        with recovery_app.db._conn() as conn:
            conn.execute(
                "UPDATE deliverable_discovery_jobs SET error_message=? WHERE job_id=?",
                ("safe <script>alert(1)</script>", job_id),
            )
        status, _, page = await request(recovery_app.app, "GET", headers["location"])
        assert status == 200
        assert b"The discovery model endpoint was unreachable (127.0.0.1:8080)" in page
        assert b"Nothing completed was lost" in page and b">Resume</button>" in page
        assert b"&lt;script&gt;" in page and b"<script>alert(1)</script>" not in page

        client.mode = "normal"
        status, headers, _ = await request(
            recovery_app.app,
            "POST",
            f"/bids/{BID_ID}/deliverable-discovery/jobs/{job_id}/resume",
        )
        assert status == 303
        recovery_app.discovery_job_runner.run_pending()
        job = recovery_app.discovery_job_service.get_job(job_id)
        assert job and job["state"] == "PARTIAL"
        assert client.calls == 5

    asyncio.run(scenario())
