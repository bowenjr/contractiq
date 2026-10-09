"""Recovery and breaker tests mirroring the discovery run-outcome fixture."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.deliverable_discovery_jobs import DiscoveryJobRunner, DiscoveryJobService
from tests.unit.test_bid_package_storage import BID_ID
from tests.unit.test_deliverable_discovery_run_outcomes import FakeClient, _fixture
from tests.unit.test_deliverable_discovery_service import _pdf_bytes

PASSAGE = "Submit the completed signed Bid Form to Procurement with the complete offer now."


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 29, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


def _jobs(tmp_path: Path, pages: list[str], client: FakeClient, clock: _Clock | None = None):
    fx = _fixture(tmp_path, {"bid.pdf": _pdf_bytes(pages)}, client)
    service = DiscoveryJobService(
        fx["repository"],  # type: ignore[arg-type]
        fx["service"].bid_package_repository,  # type: ignore[union-attr]
        clock=clock,
    )
    return fx, service


def test_expired_job_is_reclaimed_and_recorded_passage_is_not_recalled(tmp_path: Path) -> None:
    clock = _Clock()
    client = FakeClient()
    fx, service = _jobs(tmp_path, [PASSAGE, PASSAGE + " Second."], client, clock)
    job = service.start_job(BID_ID, "recover", "Jason")
    file_row = service.jobs.files(job["job_id"])[0]
    owner = "dead-runner"
    assert service.jobs.claim(job["job_id"], owner, clock(), clock() + timedelta(minutes=15))
    key = f"{file_row['received_file_id']}|PAGE|p.1"
    service.jobs.set_totals(job["job_id"], owner, 2, clock(), clock() + timedelta(minutes=15))
    service.jobs.record_passage(
        job["job_id"],
        owner,
        {
            "passage_key": key,
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
    clock.advance(minutes=16)
    runner = DiscoveryJobRunner(service, client)
    result = runner.process_job(job["job_id"])
    assert result and (result["state"], result["passages_done"]) == ("SUCCEEDED", 2)
    assert client.calls == 1
    runner.process_job(job["job_id"])
    assert client.calls == 1
    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        assert conn.execute("SELECT count(*) FROM deliverable_discovery_runs").fetchone()[0] == 1
        assert (
            conn.execute("SELECT count(*) FROM deliverable_discovery_proposals").fetchone()[0] == 1
        )


def test_unexpired_lease_is_not_stolen(tmp_path: Path) -> None:
    clock = _Clock()
    client = FakeClient()
    _fx, service = _jobs(tmp_path, [PASSAGE], client, clock)
    job = service.start_job(BID_ID, "leased", "Jason")
    assert service.jobs.claim(
        job["job_id"], "live-runner", clock(), clock() + timedelta(minutes=15)
    )
    result = DiscoveryJobRunner(service, client).process_job(job["job_id"])
    assert result and result["state"] == "RUNNING"
    assert client.calls == 0


def test_breaker_stops_at_three_and_resume_finishes_remaining_passages(tmp_path: Path) -> None:
    client = FakeClient("unreachable")
    _fx, service = _jobs(tmp_path, [PASSAGE + str(i) for i in range(5)], client)
    job = service.start_job(BID_ID, "breaker", "Jason")
    runner = DiscoveryJobRunner(service, client)
    stopped = runner.process_job(job["job_id"])
    assert stopped and (stopped["state"], stopped["passages_done"]) == ("ENDPOINT_ERROR", 3)
    assert (client.calls, stopped["error_code"]) == (3, "ENDPOINT_UNAVAILABLE")
    assert len(service.jobs.passages(job["job_id"])) == 3
    client.mode = "normal"
    service.resume_job(job["job_id"])
    completed = runner.process_job(job["job_id"])
    assert completed and completed["state"] == "PARTIAL"
    assert client.calls == 5


@pytest.mark.parametrize("mode", ("runtime", "invalid"))
def test_nontransport_and_invalid_failures_do_not_trip_breaker(tmp_path: Path, mode: str) -> None:
    class Client(FakeClient):
        def complete_json(self, prompt: str, **_kwargs: object) -> object:
            self.calls += 1
            if mode == "runtime":
                raise RuntimeError("API returned 500")
            return {"candidates": "bad"}

    client = Client()
    _fx, service = _jobs(tmp_path, [PASSAGE + str(i) for i in range(4)], client)
    job = service.start_job(BID_ID, f"not-breaker-{mode}", "Jason")
    result = DiscoveryJobRunner(service, client).process_job(job["job_id"])
    assert result and result["state"] == "FAILED"
    # Unusable output gets exactly one corrective retry per passage; a failed call gets none.
    expected_calls = 8 if mode == "invalid" else 4
    assert (client.calls, result["passages_done"]) == (expected_calls, 4)
    assert result["error_code"] is None


def test_cancel_from_endpoint_error_records_attempts_and_unattempted_passages(
    tmp_path: Path,
) -> None:
    client = FakeClient("unreachable")
    fx, service = _jobs(tmp_path, [PASSAGE + str(i) for i in range(4)], client)
    job = service.start_job(BID_ID, "endpoint-cancel", "Jason")
    runner = DiscoveryJobRunner(service, client)
    assert runner.process_job(job["job_id"])["state"] == "ENDPOINT_ERROR"  # type: ignore[index]
    assert service.cancel_job(job["job_id"])["state"] == "CANCEL_REQUESTED"  # type: ignore[index]
    cancelled = runner.process_job(job["job_id"])
    assert cancelled and cancelled["state"] == "CANCELLED" and cancelled["run_id"]
    assert len(service.jobs.passages(job["job_id"])) == 3
    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        run = conn.execute(
            "SELECT * FROM deliverable_discovery_run_outcomes WHERE run_id=?",
            (cancelled["run_id"],),
        ).fetchone()
        file_outcome = conn.execute(
            "SELECT * FROM deliverable_discovery_file_outcomes WHERE run_id=?",
            (cancelled["run_id"],),
        ).fetchone()
    assert run and (run["model_calls"], run["passages_model_failed"]) == (3, 3)
    assert file_outcome and file_outcome["outcome"] == "MODEL_UNAVAILABLE"
    assert "failed for all 3 attempted passage(s); 1 not attempted" in file_outcome["detail"]


def test_prior_fully_analysed_scope_is_skipped_and_force_all_reprocesses(tmp_path: Path) -> None:
    client = FakeClient("zero")
    _fx, service = _jobs(tmp_path, [PASSAGE], client)
    runner = DiscoveryJobRunner(service, client)
    first = service.start_job(BID_ID, "scope-first", "Jason")
    assert runner.process_job(first["job_id"])["state"] == "SUCCEEDED"  # type: ignore[index]
    calls = client.calls
    empty = service.start_job(BID_ID, "scope-empty", "Jason")
    assert (empty["state"], empty["error_code"], empty["files_skipped_prior"]) == (
        "SUCCEEDED",
        "NOTHING_NEW",
        1,
    )
    assert empty["run_id"] is None and client.calls == calls
    forced = service.start_job(BID_ID, "scope-force", "Jason", force_all=True)
    assert runner.process_job(forced["job_id"])["state"] == "SUCCEEDED"  # type: ignore[index]
    assert client.calls == calls + 1


def test_partial_unique_index_rejects_a_second_active_job(tmp_path: Path) -> None:
    client = FakeClient()
    fx, service = _jobs(tmp_path, [PASSAGE], client)
    first = service.start_job(BID_ID, "first-active", "Jason")
    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            conn.execute(
                """INSERT INTO deliverable_discovery_jobs(
                job_id,bid_id,operation_id,actor,state,force_all,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    "DDJ-second",
                    BID_ID,
                    "second-active",
                    "Jason",
                    "QUEUED",
                    0,
                    datetime.now(UTC).isoformat(),
                ),
            )
    assert service.get_active_job(BID_ID)["job_id"] == first["job_id"]  # type: ignore[index]
