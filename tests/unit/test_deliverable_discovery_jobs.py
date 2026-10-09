"""Durable discovery job tests, mirroring run-outcome fixture construction."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from core.deliverable_discovery_job_repository import DeliverableDiscoveryJobRepository
from core.deliverable_discovery_jobs import DiscoveryJobRunner, DiscoveryJobService
from tests.unit.test_bid_package_storage import BID_ID
from tests.unit.test_deliverable_discovery_run_outcomes import FakeClient, _fixture
from tests.unit.test_deliverable_discovery_service import _pdf_bytes

PASSAGE = "Submit the completed signed Bid Form to Procurement with the complete offer now."


def _jobs(tmp_path: Path, files: dict[str, bytes], client: FakeClient):
    fx = _fixture(tmp_path, files, client)
    service = DiscoveryJobService(
        fx["repository"],  # type: ignore[arg-type]
        fx["service"].bid_package_repository,  # type: ignore[union-attr]
    )
    return fx, service, DiscoveryJobRunner(service, client)


def test_start_is_prompt_and_replay_or_active_is_idempotent(tmp_path: Path) -> None:
    client = FakeClient()
    fx, service, _runner = _jobs(tmp_path, {"bid.pdf": _pdf_bytes([PASSAGE])}, client)
    first = service.start_job(BID_ID, "job-one", "Jason")
    assert (first["state"], client.calls, first["already_active"]) == ("QUEUED", 0, False)
    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        assert (
            conn.execute("SELECT count(*) FROM deliverable_discovery_extractions_v2").fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT count(*) FROM deliverable_discovery_job_passages").fetchone()[0]
            == 0
        )
    replay = service.start_job(BID_ID, "job-one", "Jason")
    active = service.start_job(BID_ID, "job-two", "Jason")
    assert replay["job_id"] == first["job_id"]
    assert (active["job_id"], active["already_active"]) == (first["job_id"], True)


def test_successful_process_persists_evidence_run_and_proposals(tmp_path: Path) -> None:
    client = FakeClient()
    fx, service, runner = _jobs(
        tmp_path, {"bid.pdf": _pdf_bytes([PASSAGE, PASSAGE + " Again."])}, client
    )
    job = service.start_job(BID_ID, "success", "Jason")
    result = runner.process_job(job["job_id"])
    assert result is not None
    assert (result["state"], result["passages_done"], result["passages_analysed"]) == (
        "SUCCEEDED",
        2,
        2,
    )
    assert result["run_id"] and client.calls == 2
    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        assert (
            conn.execute("SELECT count(*) FROM deliverable_discovery_job_passages").fetchone()[0]
            == 2
        )
        assert conn.execute("SELECT count(*) FROM deliverable_discovery_runs").fetchone()[0] == 1
        assert (
            conn.execute("SELECT count(*) FROM deliverable_discovery_proposals").fetchone()[0] == 1
        )
        source = conn.execute("SELECT * FROM deliverable_discovery_proposal_sources_v2").fetchone()
        assert source["received_file_id"] and source["locator_type"] == "PAGE"
        assert conn.execute("SELECT count(*) FROM deliverable_items").fetchone()[0] == 0
    runner.process_job(job["job_id"])
    assert client.calls == 2


def test_concurrent_start_and_claim_create_and_process_once(tmp_path: Path) -> None:
    client = FakeClient()
    fx, service, _runner = _jobs(tmp_path, {"bid.pdf": _pdf_bytes([PASSAGE])}, client)
    barrier = threading.Barrier(6)
    results: list[dict[str, object]] = []

    def start(index: int) -> None:
        barrier.wait()
        results.append(service.start_job(BID_ID, f"concurrent-{index}", "Jason"))

    threads = [threading.Thread(target=start, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len({str(item["job_id"]) for item in results}) == 1
    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        assert conn.execute("SELECT count(*) FROM deliverable_discovery_jobs").fetchone()[0] == 1
    job_id = str(results[0]["job_id"])
    runners = [DiscoveryJobRunner(service, client), DiscoveryJobRunner(service, client)]
    claim_barrier = threading.Barrier(2)

    def process(runner: DiscoveryJobRunner) -> None:
        claim_barrier.wait()
        runner.process_job(job_id)

    workers = [threading.Thread(target=process, args=(runner,)) for runner in runners]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert client.calls == 1
    assert service.get_job(job_id)["state"] == "SUCCEEDED"  # type: ignore[index]


class _CancellingClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.cancel: Callable[[], object] | None = None

    def complete_json(self, prompt: str, **kwargs: object) -> object:
        result = super().complete_json(prompt, **kwargs)
        if self.cancel:
            callback, self.cancel = self.cancel, None
            callback()
        return result


def test_cancel_before_start_and_after_inflight_keeps_evidence(tmp_path: Path) -> None:
    client = _CancellingClient()
    fx, service, runner = _jobs(
        tmp_path, {"bid.pdf": _pdf_bytes([PASSAGE, PASSAGE + " Second."])}, client
    )
    queued = service.start_job(BID_ID, "cancel-queued", "Jason")
    assert service.cancel_job(queued["job_id"])["state"] == "CANCELLED"  # type: ignore[index]
    assert client.calls == 0
    running = service.start_job(BID_ID, "cancel-running", "Jason", force_all=True)
    client.cancel = lambda: service.cancel_job(running["job_id"])
    result = runner.process_job(running["job_id"])
    assert result and (result["state"], result["passages_done"]) == ("CANCELLED", 1)
    assert result["run_id"]
    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        assert (
            conn.execute("SELECT outcome FROM deliverable_discovery_run_outcomes").fetchone()[0]
            == "PARTIAL"
        )
        assert conn.execute("SELECT count(*) FROM deliverable_items").fetchone()[0] == 0


def test_mixed_unsupported_and_zero_candidate_outcomes(tmp_path: Path) -> None:
    client = FakeClient("zero")
    fx, service, runner = _jobs(
        tmp_path, {"good.pdf": _pdf_bytes([PASSAGE]), "notes.txt": b"unsupported"}, client
    )
    job = service.start_job(BID_ID, "mixed", "Jason")
    result = runner.process_job(job["job_id"])
    assert result and result["state"] == "PARTIAL"
    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        outcomes = {
            row[0]
            for row in conn.execute("SELECT outcome FROM deliverable_discovery_file_outcomes")
        }
    assert outcomes == {"ANALYSED_ZERO_CANDIDATES", "UNSUPPORTED_TYPE"}


def test_migration_is_idempotent_and_passages_are_append_only(tmp_path: Path) -> None:
    client = FakeClient()
    fx, service, runner = _jobs(tmp_path, {"bid.pdf": _pdf_bytes([PASSAGE])}, client)
    job = service.start_job(BID_ID, "migration", "Jason")
    runner.process_job(job["job_id"])
    DeliverableDiscoveryJobRepository(fx["database"])  # type: ignore[arg-type]
    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE deliverable_discovery_job_passages SET safe_reason='x'")
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute("DELETE FROM deliverable_discovery_job_passages")
