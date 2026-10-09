"""Durable, resumable background execution for deliverable discovery."""

from __future__ import annotations

import io
import json
import os
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from core.bid_package_repository import BidPackageRepository
from core.deliverable_discovery import (
    DiscoveryRun,
    ExtractionStatus,
    ExtractionUnit,
    LocatorType,
    RunStatus,
)
from core.deliverable_discovery_extraction import (
    RawUnit,
    UnsupportedMediaTypeError,
    extract_units,
    locator_type_for_filename,
)
from core.deliverable_discovery_job_repository import DeliverableDiscoveryJobRepository
from core.deliverable_discovery_llm import (
    LLMClientProtocol,
    PassageOutcome,
    PassageOutcomeKind,
    ProposedCandidate,
    analyse_units,
)
from core.deliverable_discovery_outcomes import (
    DiscoveryOutcome,
    FileAnalysisEvidence,
    derive_discovery_outcomes,
)
from core.deliverable_discovery_repository import DeliverableDiscoveryRepository
from core.deliverable_discovery_service import (
    DiscoveryUnavailableError,
    _consolidate,
    _managed_copy_failure,
)

Clock = Callable[[], datetime]
LEASE = timedelta(minutes=15)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


@dataclass(frozen=True)
class _PreparedFile:
    received_file_id: str
    release_id: str
    filename: str
    supported: bool
    extraction_completed: bool
    units: tuple[RawUnit, ...]


class DiscoveryJobService:
    """Fast request-side operations; none invokes extraction or the model."""

    def __init__(
        self,
        repository: DeliverableDiscoveryRepository,
        bid_package_repository: BidPackageRepository,
        *,
        job_repository: DeliverableDiscoveryJobRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.repository = repository
        self.bid_package_repository = bid_package_repository
        self.jobs = job_repository or DeliverableDiscoveryJobRepository(repository.db)
        self.clock = clock or (lambda: datetime.now(UTC))

    def start_job(
        self, bid_id: str, operation_id: str, actor: str, force_all: bool = False
    ) -> dict[str, Any]:
        eligible = self.bid_package_repository.eligible_received_files(bid_id)
        if not eligible:
            raise DiscoveryUnavailableError(
                "No received files are available for discovery. "
                'Mark files "Include for analysis" first.'
            )
        return self._decorate(
            self.jobs.create(bid_id, operation_id, actor, force_all, eligible, self.clock())
        )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.jobs.get(job_id)
        return self._decorate(row) if row else None

    def get_active_job(self, bid_id: str) -> dict[str, Any] | None:
        row = self.jobs.active(bid_id)
        return self._decorate(row) if row else None

    def latest_job(self, bid_id: str) -> dict[str, Any] | None:
        row = self.jobs.latest(bid_id)
        return self._decorate(row) if row else None

    def latest_finished_job(self, bid_id: str) -> dict[str, Any] | None:
        row = self.jobs.latest_finished(bid_id)
        return self._decorate(row) if row else None

    def job_progress(self, job_id: str) -> dict[str, Any] | None:
        return self.get_job(job_id)

    def cancel_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.jobs.cancel(job_id, self.clock())
        return self._decorate(row) if row else None

    def resume_job(self, job_id: str) -> dict[str, Any]:
        return self._decorate(self.jobs.resume(job_id, self.clock()))

    def _decorate(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        now = self.clock()
        started = _parse_time(result.get("started_at"))
        finished = _parse_time(result.get("finished_at"))
        result["elapsed_seconds"] = (
            max(0.0, ((finished or now) - started).total_seconds()) if started else 0.0
        )
        heartbeat = _parse_time(result.get("heartbeat_at"))
        result["heartbeat_age_seconds"] = (
            max(0, int((now - heartbeat).total_seconds()))
            if heartbeat is not None
            and result.get("lease_owner") is not None
            and result.get("state") in ("RUNNING", "CANCEL_REQUESTED")
            else None
        )
        result["label"] = {
            "QUEUED": "Queued",
            "RUNNING": "Analysing documents",
            "CANCEL_REQUESTED": "Cancelling",
            "ENDPOINT_ERROR": "Model endpoint unavailable",
            "SUCCEEDED": "Discovery complete",
            "PARTIAL": "Discovery partially complete",
            "FAILED": "Discovery failed",
            "CANCELLED": "Cancelled",
        }.get(str(result["state"]), str(result["state"]))
        return result


class DiscoveryJobRunner:
    def __init__(
        self,
        job_service: DiscoveryJobService,
        llm_client: LLMClientProtocol,
        *,
        poll_seconds: float = 2.0,
        pid_alive: Callable[[int], bool] = _pid_alive,
        stop_timeout_seconds: float = 3.0,
    ) -> None:
        self.service = job_service
        self.jobs = job_service.jobs
        self.llm_client = llm_client
        self.poll_seconds = poll_seconds
        self.stop_timeout_seconds = stop_timeout_seconds
        self.pid_alive = pid_alive
        self.host = socket.gethostname()
        self.pid = os.getpid()
        self.owner = f"DDR-{self.host}-{self.pid}-{uuid4().hex}"
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def notify(self) -> None:
        self._wake.set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._wake.clear()
        self._stopping.clear()
        self._thread = threading.Thread(target=self._loop, name="discovery-jobs", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=min(10.0, max(0.0, self.stop_timeout_seconds)))
            if thread.is_alive():
                self.jobs.release_owned(self.owner)
        self._thread = thread if thread and thread.is_alive() else None

    def _loop(self) -> None:
        while not self._stopping.is_set():
            self.run_pending()
            self._wake.wait(self.poll_seconds)
            self._wake.clear()

    def run_pending(self) -> int:
        processed = 0
        for job_id in self.jobs.claimable_ids(
            self.service.clock(),
            current_host=self.host,
            current_pid=self.pid,
            pid_alive=self.pid_alive,
        ):
            if self._stopping.is_set():
                break
            before = self.jobs.get(job_id)
            try:
                self.process_job(job_id)
            except Exception:
                # The expiring lease makes worker failure recoverable; one bad job
                # must not terminate the daemon loop.
                continue
            after = self.jobs.get(job_id)
            if before != after:
                processed += 1
        return processed

    def process_job(self, job_id: str) -> dict[str, Any] | None:
        now = self.service.clock()
        if self._stopping.is_set() or not self.jobs.claim(
            job_id,
            self.owner,
            now,
            now + LEASE,
            current_host=self.host,
            current_pid=self.pid,
            pid_alive=self.pid_alive,
        ):
            return self.service.get_job(job_id)
        self._process_claimed(job_id)
        return self.service.get_job(job_id)

    def _process_claimed(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            return
        prepared = [self._extract(job, row) for row in self.jobs.files(job_id)]
        total = sum(len(self._readable_units(item)) for item in prepared)
        now = self.service.clock()
        self.jobs.set_totals(job_id, self.owner, total, now, now + LEASE)
        recorded = {str(row["passage_key"]): row for row in self.jobs.passages(job_id)}

        for index, item in enumerate(prepared):
            self.jobs.file_progress(job_id, self.owner, item.received_file_id, item.filename, index)
            for key, raw in self._readable_units(item):
                if self._stopping.is_set():
                    self.jobs.release(job_id, self.owner)
                    return
                state = self.jobs.get(job_id)
                if state is None or state["state"] == "CANCEL_REQUESTED":
                    self._finalize(job_id, prepared, cancelled=True)
                    return
                if key in recorded:
                    continue
                analysis = analyse_units(self.llm_client, [(key, raw)], timeout=600)
                outcome = analysis.outcomes[0]
                now = self.service.clock()
                passage: dict[str, object] = {
                    "passage_key": key,
                    "received_file_id": item.received_file_id,
                    "locator_type": raw.locator_type,
                    "locator": raw.locator,
                    "outcome": outcome.outcome.value,
                    "candidates_json": json.dumps(
                        [candidate.model_dump(mode="json") for candidate in analysis.candidates],
                        sort_keys=True,
                    ),
                    "invalid_candidate_count": outcome.invalid_candidate_count,
                    "safe_reason": outcome.safe_reason,
                }
                failures = self.jobs.record_passage(
                    job_id, self.owner, passage, outcome.transport_failure, now, now + LEASE
                )
                if failures is None:
                    return
                recorded[key] = passage
                if self._stopping.is_set():
                    self.jobs.release(job_id, self.owner)
                    return
                state = self.jobs.get(job_id)
                if state is not None and state["state"] == "CANCEL_REQUESTED":
                    self._finalize(job_id, prepared, cancelled=True)
                    return
                if failures >= 3:
                    self.jobs.endpoint_error(job_id, self.owner, self._endpoint_message())
                    return
            self.jobs.file_progress(
                job_id, self.owner, item.received_file_id, item.filename, index + 1
            )

        state = self.jobs.get(job_id)
        self._finalize(
            job_id,
            prepared,
            cancelled=bool(state and state["state"] == "CANCEL_REQUESTED"),
        )

    def _extract(self, job: dict[str, Any], file_row: dict[str, Any]) -> _PreparedFile:
        filename = str(file_row["filename"])
        file_id = str(file_row["received_file_id"])
        release_id = str(file_row["release_id"])
        try:
            locator_type = LocatorType(locator_type_for_filename(filename))
        except UnsupportedMediaTypeError:
            return _PreparedFile(file_id, release_id, filename, False, False, ())
        completed = False
        units: tuple[RawUnit, ...]
        try:
            handle, evidence = self.service.bid_package_repository.open_received_file(
                str(job["bid_id"]), release_id, file_id
            )
        except Exception as exc:
            units = (_managed_copy_failure(locator_type, exc),)
        else:
            try:
                try:
                    data = handle.read()
                except Exception as exc:
                    units = (_managed_copy_failure(locator_type, exc),)
                else:
                    try:
                        units = tuple(
                            extract_units(
                                str(evidence["original_filename"]),
                                str(evidence["detected_media_type"])
                                if evidence["detected_media_type"] is not None
                                else None,
                                io.BytesIO(data),
                            )
                        )
                        completed = True
                    except ImportError:
                        raise
                    except UnsupportedMediaTypeError:
                        units = ()
                    except Exception as exc:
                        units = (_managed_copy_failure(locator_type, exc),)
            finally:
                try:
                    handle.close()
                except Exception:
                    pass
        return _PreparedFile(file_id, release_id, filename, True, completed, units)

    @staticmethod
    def _readable_units(item: _PreparedFile) -> list[tuple[str, RawUnit]]:
        return [
            (_passage_key(item.received_file_id, raw), raw)
            for raw in item.units
            if raw.readable and raw.text.strip()
        ]

    def _finalize(self, job_id: str, files: list[_PreparedFile], cancelled: bool) -> None:
        job = self.jobs.get(job_id)
        if job is None or job["run_id"] is not None:
            return
        rows = self.jobs.passages(job_id)
        if cancelled and not rows:
            self.jobs.finalize_without_run(job_id, self.owner, self.service.clock())
            return
        row_by_key = {str(row["passage_key"]): row for row in rows}
        run_id = f"DDN-{uuid4().hex}"
        started = _parse_time(job["started_at"]) or self.service.clock()
        completed = self.service.clock()
        extractions: list[ExtractionUnit] = []
        evidence: list[FileAnalysisEvidence] = []
        outcomes: list[PassageOutcome] = []
        candidates: list[ProposedCandidate] = []
        for item in files:
            passage_ids: list[str] = []
            unreadable: list[str] = []
            for raw in item.units:
                if raw.readable and not raw.text.strip():
                    continue
                key = _passage_key(item.received_file_id, raw)
                extraction_id = f"{run_id}|{key}"
                extractions.append(
                    ExtractionUnit(
                        extraction_id=extraction_id,
                        bid_id=str(job["bid_id"]),
                        run_id=run_id,
                        received_file_id=item.received_file_id,
                        locator_type=LocatorType(raw.locator_type),
                        locator=raw.locator,
                        text_excerpt=raw.text if raw.readable else "",
                        char_count=len(raw.text),
                        status=ExtractionStatus.OK if raw.readable else ExtractionStatus.UNREADABLE,
                        failure_reason=raw.failure_reason,
                        created_at=started,
                    )
                )
                if raw.readable:
                    passage_ids.append(key)
                    row = row_by_key.get(key)
                    if row is None:
                        outcomes.append(
                            PassageOutcome(
                                extraction_id=key,
                                outcome=PassageOutcomeKind.NOT_ATTEMPTED,
                                safe_reason="not attempted: job cancelled",
                            )
                        )
                    else:
                        outcomes.append(_outcome_from_row(row))
                        candidates.extend(
                            ProposedCandidate.model_validate(candidate).model_copy(
                                update={"extraction_id": extraction_id}
                            )
                            for candidate in json.loads(str(row["candidates_json"]))
                        )
                elif raw.failure_reason:
                    unreadable.append(raw.failure_reason)
            evidence.append(
                FileAnalysisEvidence(
                    received_file_id=item.received_file_id,
                    filename=item.filename,
                    supported=item.supported,
                    extraction_completed=item.extraction_completed,
                    passage_ids=tuple(passage_ids),
                    unreadable_reasons=tuple(unreadable),
                )
            )
        if not cancelled and len(row_by_key) != sum(len(item.passage_ids) for item in evidence):
            return
        run_outcome, file_outcomes = derive_discovery_outcomes(run_id, evidence, tuple(outcomes))
        lookup = {item.extraction_id: item for item in extractions}
        proposals = _consolidate(
            str(job["bid_id"]),
            run_id,
            started,
            candidates,
            lookup,
            self.service.repository.date_triggers(str(job["bid_id"])),
        )
        known = self.jobs.existing_dedup_keys(str(job["bid_id"]))
        proposals = [proposal for proposal in proposals if proposal.dedup_key not in known]
        invalid = sum(
            item.invalid_candidate_count
            + (1 if item.outcome is PassageOutcomeKind.INVALID_OUTPUT else 0)
            for item in outcomes
        )
        snapshot = self.service.bid_package_repository.current_snapshot(str(job["bid_id"]))
        run = DiscoveryRun(
            run_id=run_id,
            bid_id=str(job["bid_id"]),
            snapshot_id=str(snapshot["snapshot_id"]) if snapshot else None,
            operation_id=str(job["operation_id"]),
            status=RunStatus.FAILED
            if run_outcome.outcome is DiscoveryOutcome.FAILED
            else RunStatus.SUCCEEDED,
            proposal_count=len(proposals),
            rejected_source_count=invalid,
            actor=str(job["actor"]),
            started_at=started,
            completed_at=completed,
        )
        self.jobs.finalize_run(
            job_id,
            self.owner,
            run,
            extractions,
            proposals,
            run_outcome,
            file_outcomes,
            cancelled,
        )

    def _endpoint_message(self) -> str:
        authority = "configured host:port"
        for name in ("base_url", "endpoint", "url"):
            value = getattr(self.llm_client, name, None)
            if isinstance(value, str) and value:
                parsed = urlsplit(value if "://" in value else f"//{value}")
                authority = parsed.netloc or parsed.path.split("/")[0]
                break
        return f"Model endpoint {authority} is unavailable; retry is safe."


def _passage_key(received_file_id: str, raw: RawUnit) -> str:
    return f"{received_file_id}|{raw.locator_type}|{raw.locator}"


def _outcome_from_row(row: dict[str, Any]) -> PassageOutcome:
    candidates = json.loads(str(row["candidates_json"]))
    return PassageOutcome(
        extraction_id=str(row["passage_key"]),
        outcome=PassageOutcomeKind(str(row["outcome"])),
        candidate_count=len(candidates),
        invalid_candidate_count=int(row["invalid_candidate_count"]),
        safe_reason=str(row["safe_reason"]) if row["safe_reason"] is not None else None,
    )


def _parse_time(value: object) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value else None


__all__ = ["DiscoveryJobRunner", "DiscoveryJobService"]
