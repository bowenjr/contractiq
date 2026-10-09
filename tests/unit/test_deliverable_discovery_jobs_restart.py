"""Restart latency and graceful discovery-worker shutdown tests."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest

from core.deliverable_discovery_jobs import DiscoveryJobRunner, DiscoveryJobService
from scripts.asgi_acceptance_ops08 import request
from tests.unit.test_bid_package_governing_ui import governing_ui_app as _app_fixture
from tests.unit.test_bid_package_storage import BID_ID
from tests.unit.test_deliverable_discovery_job_routes import _include, _job_id, _start
from tests.unit.test_deliverable_discovery_jobs_recovery import PASSAGE, _Clock, _jobs
from tests.unit.test_deliverable_discovery_run_outcomes import FakeClient


def _owner(host: str, pid: int) -> str:
    return f"DDR-{host}-{pid}-{uuid4().hex}"


def _record_first(service: DiscoveryJobService, job_id: str, owner: str, clock: _Clock) -> None:
    file_row = service.jobs.files(job_id)[0]
    service.jobs.set_totals(job_id, owner, 2, clock(), clock() + timedelta(minutes=15))
    assert (
        service.jobs.record_passage(
            job_id,
            owner,
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
            clock(),
            clock() + timedelta(minutes=15),
        )
        == 0
    )


def _wait_until(predicate: object, timeout: float = 2.0) -> None:
    callback = predicate
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(callback) and callback():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached")


def test_unexpired_dead_pid_reclaims_and_resumes_once(tmp_path: Path) -> None:
    clock = _Clock()
    client = FakeClient()
    fx, service = _jobs(tmp_path, [PASSAGE, PASSAGE + " Second."], client, clock)
    job = service.start_job(BID_ID, "dead-owner", "Jason")
    dead_pid = os.getpid() + 100_000
    old_owner = _owner(socket.gethostname(), dead_pid)
    assert service.jobs.claim(job["job_id"], old_owner, clock(), clock() + timedelta(minutes=15))
    _record_first(service, job["job_id"], old_owner, clock)

    runner = DiscoveryJobRunner(service, client, pid_alive=lambda pid: pid != dead_pid)
    assert job["job_id"] in runner.jobs.claimable_ids(
        clock(), current_host=runner.host, current_pid=runner.pid, pid_alive=runner.pid_alive
    )
    result = runner.process_job(job["job_id"])
    assert result and (result["state"], result["passages_done"], client.calls) == (
        "SUCCEEDED",
        2,
        1,
    )
    assert len(service.jobs.passages(job["job_id"])) == 2
    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        assert conn.execute("SELECT count(*) FROM deliverable_discovery_runs").fetchone()[0] == 1
        assert (
            conn.execute("SELECT count(*) FROM deliverable_discovery_proposals").fetchone()[0] == 1
        )


def test_real_exited_subprocess_pid_is_reclaimed_immediately(tmp_path: Path) -> None:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    dead_pid = process.pid
    assert process.wait(timeout=5) == 0
    clock = _Clock()
    client = FakeClient()
    _fx, service = _jobs(tmp_path, [PASSAGE], client, clock)
    job = service.start_job(BID_ID, "real-dead-owner", "Jason")
    old_owner = _owner(socket.gethostname(), dead_pid)
    assert service.jobs.claim(job["job_id"], old_owner, clock(), clock() + timedelta(minutes=15))
    result = DiscoveryJobRunner(service, client).process_job(job["job_id"])
    assert result and (result["state"], client.calls) == ("SUCCEEDED", 1)


@pytest.mark.parametrize("kind", ("same-process", "foreign-host", "legacy"))
def test_live_foreign_and_legacy_unexpired_owners_are_not_stolen(tmp_path: Path, kind: str) -> None:
    clock = _Clock()
    client = FakeClient()
    _fx, service = _jobs(tmp_path, [PASSAGE], client, clock)
    job = service.start_job(BID_ID, f"owner-{kind}", "Jason")
    if kind == "same-process":
        owner = _owner(socket.gethostname(), os.getpid())
    elif kind == "foreign-host":
        owner = _owner(f"foreign-{socket.gethostname()}", os.getpid() + 10_000)
    else:
        owner = f"DDR-{uuid4().hex}"
    assert service.jobs.claim(job["job_id"], owner, clock(), clock() + timedelta(minutes=15))
    runner = DiscoveryJobRunner(service, client, pid_alive=lambda _pid: False)
    result = runner.process_job(job["job_id"])
    assert result and result["state"] == "RUNNING" and client.calls == 0
    clock.advance(minutes=16)
    result = runner.process_job(job["job_id"])
    assert result and result["state"] == "SUCCEEDED" and client.calls == 1


class _StopAfterCallClient(FakeClient):
    stop: object = None

    def complete_json(self, prompt: str, **kwargs: object) -> object:
        result = super().complete_json(prompt, **kwargs)
        if callable(self.stop):
            callback, self.stop = self.stop, None
            callback()
        return result


def test_graceful_stop_between_passages_requeues_and_resumes(tmp_path: Path) -> None:
    client = _StopAfterCallClient()
    _fx, service = _jobs(tmp_path, [PASSAGE, PASSAGE + " Second."], client)
    job = service.start_job(BID_ID, "stop-between", "Jason")
    runner = DiscoveryJobRunner(service, client, poll_seconds=0.01)
    client.stop = runner.stop
    runner.start()
    _wait_until(
        lambda: (
            service.get_job(job["job_id"])["state"] == "QUEUED"  # type: ignore[index]
            and service.get_job(job["job_id"])["passages_done"] == 1
        )  # type: ignore[index]
    )
    stopped = service.get_job(job["job_id"])
    assert stopped and (stopped["passages_done"], stopped["lease_owner"], client.calls) == (
        1,
        None,
        1,
    )
    assert len(service.jobs.passages(job["job_id"])) == 1

    next_client = FakeClient()
    completed = DiscoveryJobRunner(service, next_client).process_job(job["job_id"])
    assert completed and (completed["state"], completed["passages_done"]) == ("SUCCEEDED", 2)
    assert next_client.calls == 1
    assert len(service.jobs.passages(job["job_id"])) == 2


class _BlockingClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def complete_json(self, prompt: str, **kwargs: object) -> object:
        self.entered.set()
        assert self.release.wait(timeout=5)
        return super().complete_json(prompt, **kwargs)


def test_stop_during_call_releases_lease_and_orphan_fails_quietly(tmp_path: Path) -> None:
    client = _BlockingClient()
    fx, service = _jobs(tmp_path, [PASSAGE], client)
    job = service.start_job(BID_ID, "stop-inflight", "Jason")
    runner = DiscoveryJobRunner(service, client, poll_seconds=0.01, stop_timeout_seconds=0.05)
    runner.start()
    assert client.entered.wait(timeout=2)
    runner.stop()
    orphan = runner._thread
    stopped = service.get_job(job["job_id"])
    assert orphan and orphan.is_alive()
    assert stopped and (stopped["state"], stopped["lease_owner"]) == ("QUEUED", None)

    next_client = FakeClient()
    completed = DiscoveryJobRunner(service, next_client).process_job(job["job_id"])
    assert completed and completed["state"] == "SUCCEEDED"
    client.release.set()
    orphan.join(timeout=2)
    assert not orphan.is_alive()
    assert len(service.jobs.passages(job["job_id"])) == 1
    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        assert conn.execute("SELECT count(*) FROM deliverable_discovery_runs").fetchone()[0] == 1
        assert (
            conn.execute("SELECT count(*) FROM deliverable_discovery_proposals").fetchone()[0] == 1
        )


@pytest.fixture
def restart_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    yield from _app_fixture.__wrapped__(tmp_path, monkeypatch)


def test_heartbeat_age_and_stale_progress_note(restart_app: ModuleType) -> None:
    async def scenario() -> None:
        clock = _Clock()
        restart_app.discovery_job_service.clock = clock
        await _include(restart_app, "Heartbeat", {"bid.pdf": b"%PDF-1.4"})
        status, headers, _ = await _start(restart_app, "heartbeat")
        assert status == 303
        job_id = _job_id(headers["location"])
        service = restart_app.discovery_job_service
        assert service.job_progress(job_id)["heartbeat_age_seconds"] is None
        now = clock()
        assert service.jobs.claim(job_id, "test-owner", now, now + timedelta(minutes=15))
        clock.advance(seconds=91)
        assert service.job_progress(job_id)["heartbeat_age_seconds"] == 91
        status, _, page = await request(restart_app.app, "GET", headers["location"])
        assert status == 200
        assert b"No progress has been recorded for 91 s" in page
        assert b"job resumes automatically when the worker starts" in page

    asyncio.run(scenario())
