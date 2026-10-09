"""ASGI job-route tests mirroring ``scripts.asgi_acceptance_ops08.request``."""

from __future__ import annotations

import asyncio
import threading
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from urllib.parse import urlsplit

import pytest

from scripts.asgi_acceptance_ops08 import request
from tests.unit.test_bid_package_governing_ui import (
    BID_ID,
    _files_by_name,
    _register_release,
    _review,
)
from tests.unit.test_bid_package_governing_ui import governing_ui_app as _app_fixture
from tests.unit.test_deliverable_discovery_run_outcomes import FakeClient
from tests.unit.test_deliverable_discovery_service import _pdf_bytes

PASSAGE = "Submit the completed signed Bid Form to Procurement with the complete offer now."
OTHER_BID = "B-WRONG"


@pytest.fixture
def job_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    yield from _app_fixture.__wrapped__(tmp_path, monkeypatch)


async def _include(app: ModuleType, folder: str, files: dict[str, bytes]) -> None:
    source = next(iter(app.INTAKE_SOURCE_ROOTS.values())) / folder
    source.mkdir()
    for name, content in files.items():
        (source / name).write_bytes(content)
    release_path = await _register_release(app, folder=folder, reference=folder)
    release_id = release_path.rsplit("/", 1)[-1]
    rows = _files_by_name(app, BID_ID, release_id)
    await _review(
        app,
        release_path,
        [str(row["file_id"]) for row in rows.values()],
        "ELIGIBLE",
        f"include-{folder}",
    )


async def _start(app: ModuleType, operation_id: str) -> tuple[int, dict[str, str], bytes]:
    return await request(
        app.app,
        "POST",
        f"/bids/{BID_ID}/deliverable-discovery/run",
        {"operation_id": operation_id},
    )


def _job_id(location: str) -> str:
    return urlsplit(location).path.rsplit("/", 1)[-1]


def test_post_only_queues_without_model_or_file_read(
    job_app: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailClient:
        def complete_json(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("model called in request path")

    async def scenario() -> None:
        await _include(job_app, "Queue-Only", {"bid.pdf": _pdf_bytes([PASSAGE])})
        job_app.discovery_job_runner.llm_client = FailClient()

        def fail_read(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("file read in request path")

        monkeypatch.setattr(job_app.bid_package_repository, "open_received_file", fail_read)
        status, headers, _ = await _start(job_app, "queue-only")
        assert status == 303
        assert "/deliverable-discovery/jobs/DDJ-" in headers["location"]
        job = job_app.discovery_job_service.get_job(_job_id(headers["location"]))
        assert job and (job["state"], job["passages_done"]) == ("QUEUED", 0)

    asyncio.run(scenario())


def test_queued_midrun_final_progress_and_status_json(job_app: ModuleType) -> None:
    async def scenario() -> None:
        await _include(
            job_app,
            "Progress",
            {"bid.pdf": _pdf_bytes([PASSAGE, PASSAGE + " Second."])},
        )
        client = FakeClient()
        job_app.discovery_job_runner.llm_client = client
        status, headers, _ = await _start(job_app, "progress")
        assert status == 303
        location = headers["location"]
        job_id = _job_id(location)
        status, _, queued = await request(job_app.app, "GET", location)
        assert status == 200
        assert b'<meta http-equiv="refresh" content="3">' in queued
        assert b"Waiting to start" in queued and b"0/0 passages" in queued

        service = job_app.discovery_job_service
        runner = job_app.discovery_job_runner
        file_row = service.jobs.files(job_id)[0]
        now = service.clock()
        assert service.jobs.claim(job_id, runner.owner, now, now + timedelta(minutes=15))
        service.jobs.set_totals(job_id, runner.owner, 2, now, now + timedelta(minutes=15))
        service.jobs.record_passage(
            job_id,
            runner.owner,
            {
                "passage_key": f"{file_row['received_file_id']}|PAGE|p.1",
                "received_file_id": file_row["received_file_id"],
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
        status, _, middle = await request(job_app.app, "GET", location)
        assert status == 200
        assert b"Analysing" in middle and b"1/2 passages" in middle
        assert b"bid.pdf" in middle

        runner._process_claimed(job_id)
        status, _, final = await request(job_app.app, "GET", location)
        assert status == 200
        assert b"Analysis complete" in final
        assert "2 with text · 2 analysed by the model · 0 without a usable answer".encode() in final
        assert b"Elapsed" in final and b"Review proposed deliverables" in final
        assert "Analysed — possible returnables found".encode() in final
        assert b"ANALYSED_CANDIDATES" not in final
        assert b"Status:" in final and b"State:" not in final
        assert b"Text extracted from" in final
        assert b"<summary>Details</summary>" in final
        status, _, payload = await request(job_app.app, "GET", f"{location}/status")
        assert status == 200
        assert b'"state":"SUCCEEDED"' in payload
        assert b'"passages_done":2' in payload
        assert client.calls == 1

        status, headers, _ = await _start(job_app, "nothing-new")
        assert status == 303
        status, _, nothing_new = await request(job_app.app, "GET", headers["location"])
        assert status == 200
        assert b"All 1 Included files were already analysed; nothing new to do" in nothing_new
        assert b"Re-analyse all Included files" in nothing_new
        assert client.calls == 1

    asyncio.run(scenario())


def test_idempotent_already_active_and_concurrent_posts(job_app: ModuleType) -> None:
    async def prepare() -> None:
        await _include(job_app, "Concurrent", {"bid.pdf": _pdf_bytes([PASSAGE])})

    asyncio.run(prepare())
    results: list[str] = []
    barrier = threading.Barrier(5)

    def post(index: int) -> None:
        barrier.wait()
        _, headers, _ = asyncio.run(_start(job_app, f"concurrent-{index}"))
        results.append(_job_id(headers["location"]))

    threads = [threading.Thread(target=post, args=(index,)) for index in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(results)) == 1
    with job_app.db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM deliverable_discovery_jobs").fetchone()[0] == 1
        operation_id = str(
            conn.execute("SELECT operation_id FROM deliverable_discovery_jobs").fetchone()[0]
        )
    replay = asyncio.run(_start(job_app, operation_id))
    active = asyncio.run(_start(job_app, "different-operation"))
    assert replay[0] == active[0] == 303
    assert _job_id(replay[1]["location"]) == results[0]
    assert _job_id(active[1]["location"]) == results[0]
    assert "notice=already_active" in active[1]["location"]
    status, _, page = asyncio.run(request(job_app.app, "GET", active[1]["location"]))
    assert status == 200 and b"already active for this Bid" in page


def test_startup_and_shutdown_use_existing_lifecycle_hook(
    job_app: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(job_app.discovery_job_runner, "start", lambda: calls.append("start"))
    monkeypatch.setattr(job_app.discovery_job_runner, "stop", lambda: calls.append("stop"))
    monkeypatch.setenv("CONTRACTIQ_DISCOVERY_RUNNER", "off")
    asyncio.run(job_app.startup_event())
    assert calls == []
    monkeypatch.setenv("CONTRACTIQ_DISCOVERY_RUNNER", "on")
    asyncio.run(job_app.startup_event())
    asyncio.run(job_app.shutdown_event())
    assert calls == ["start", "stop"]


def test_disconnect_completion_active_banner_wrong_bid_and_escaping(job_app: ModuleType) -> None:
    async def scenario() -> None:
        hostile = "evil<script>alert(1)<script>.pdf"
        await _include(job_app, "Disconnect", {hostile: _pdf_bytes([PASSAGE])})
        job_app.discovery_job_runner.llm_client = FakeClient()
        status, headers, _ = await _start(job_app, "disconnect")
        assert status == 303
        location = headers["location"]
        job_id = _job_id(location)
        status, _, discovery = await request(
            job_app.app, "GET", f"/bids/{BID_ID}/deliverable-discovery"
        )
        assert status == 200
        assert b"Analysis in progress: Waiting to start" in discovery
        assert job_id.encode() in discovery

        job_app.discovery_job_runner.run_pending()
        status, _, final = await request(job_app.app, "GET", location)
        assert status == 200 and b"Analysis complete" in final
        assert b"&lt;script&gt;" in final and b"<script>alert(1)<script>" not in final
        for suffix, method in (
            ("", "GET"),
            ("/status", "GET"),
            ("/cancel", "POST"),
            ("/resume", "POST"),
        ):
            wrong = f"/bids/{OTHER_BID}/deliverable-discovery/jobs/{job_id}{suffix}"
            status, _, _ = await request(job_app.app, method, wrong)
            assert status == 404
        status, _, body = await request(job_app.app, "POST", f"{location}/cancel")
        assert status == 409 and b"already finished" in body
        status, _, body = await request(job_app.app, "POST", f"{location}/resume")
        assert status == 409 and b"cannot be resumed" in body

    asyncio.run(scenario())
