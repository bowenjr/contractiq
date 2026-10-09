"""Focused cancellation evidence tests for OPS-13A NOT_ATTEMPTED outcomes."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from core.deliverable_discovery_jobs import DiscoveryJobRunner
from tests.unit.test_bid_package_storage import BID_ID
from tests.unit.test_deliverable_discovery_jobs_recovery import PASSAGE, _jobs
from tests.unit.test_deliverable_discovery_run_outcomes import FakeClient


class _CancelAfterFirstClient(FakeClient):
    cancel: object = None

    def complete_json(self, prompt: str, **kwargs: object) -> object:
        result = super().complete_json(prompt, **kwargs)
        if callable(self.cancel):
            callback, self.cancel = self.cancel, None
            callback()
        return result


def test_cancel_before_first_call_has_no_fabricated_run(tmp_path: Path) -> None:
    client = FakeClient()
    fx, service = _jobs(tmp_path, [PASSAGE], client)
    job = service.start_job(BID_ID, "cancel-before-call", "Jason")
    runner = DiscoveryJobRunner(service, client)
    now = service.clock()
    assert service.jobs.claim(job["job_id"], runner.owner, now, now + timedelta(minutes=15))

    assert service.cancel_job(job["job_id"])["state"] == "CANCEL_REQUESTED"  # type: ignore[index]
    runner._process_claimed(job["job_id"])
    cancelled = service.get_job(job["job_id"])

    assert cancelled and (cancelled["state"], cancelled["run_id"]) == ("CANCELLED", None)
    assert client.calls == 0
    assert service.jobs.passages(job["job_id"]) == []
    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        assert conn.execute("SELECT count(*) FROM deliverable_discovery_runs").fetchone()[0] == 0
        assert (
            conn.execute("SELECT count(*) FROM deliverable_discovery_file_outcomes").fetchone()[0]
            == 0
        )


def test_cancel_after_one_call_derives_remaining_passage_as_not_attempted(
    tmp_path: Path,
) -> None:
    client = _CancelAfterFirstClient()
    fx, service = _jobs(tmp_path, [PASSAGE, PASSAGE + " Second."], client)
    job = service.start_job(BID_ID, "cancel-after-one", "Jason")
    client.cancel = lambda: service.cancel_job(job["job_id"])
    runner = DiscoveryJobRunner(service, client)

    result = runner.process_job(job["job_id"])

    assert result and (result["state"], result["passages_done"], client.calls) == (
        "CANCELLED",
        1,
        1,
    )
    assert len(service.jobs.passages(job["job_id"])) == 1
    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        run = conn.execute(
            "SELECT * FROM deliverable_discovery_run_outcomes WHERE run_id=?",
            (result["run_id"],),
        ).fetchone()
        file_outcome = conn.execute(
            "SELECT * FROM deliverable_discovery_file_outcomes WHERE run_id=?",
            (result["run_id"],),
        ).fetchone()
    assert run and (
        run["passages_total"],
        run["model_calls"],
        run["passages_analysed"],
        run["passages_model_failed"],
    ) == (2, 1, 1, 0)
    assert file_outcome and file_outcome["outcome"] == "PARTIALLY_ANALYSED"
    assert "1 passage(s) not attempted" in file_outcome["detail"]
    assert "MODEL_UNAVAILABLE" not in file_outcome["detail"]


def test_cancelled_finalization_is_idempotent_after_restart(tmp_path: Path) -> None:
    client = _CancelAfterFirstClient()
    fx, service = _jobs(tmp_path, [PASSAGE, PASSAGE + " Second."], client)
    job = service.start_job(BID_ID, "cancel-idempotent", "Jason")
    client.cancel = lambda: service.cancel_job(job["job_id"])
    runner = DiscoveryJobRunner(service, client)
    first = runner.process_job(job["job_id"])
    assert first and first["run_id"]

    replay = DiscoveryJobRunner(service, FakeClient()).process_job(job["job_id"])

    assert replay and replay["run_id"] == first["run_id"]
    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        counts = tuple(
            conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "deliverable_discovery_job_passages",
                "deliverable_discovery_runs",
                "deliverable_discovery_run_outcomes",
                "deliverable_discovery_file_outcomes",
            )
        )
    assert counts == (1, 1, 1, 1)
