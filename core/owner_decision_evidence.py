"""Pure assembly and database collection for owner-decision evidence snapshots."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping

from core.owner_decisions import (
    DiscoveryJobSnapshot,
    OwnerDecisionEvidenceSnapshot,
    ReceivedFileReviewSnapshot,
)

_FINISHED_DISCOVERY_STATES = frozenset({"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"})


def _as_int(value: object) -> int:
    return int(str(value))


def build_owner_decision_evidence_snapshot(
    *,
    received_files_total: int,
    received_files_reviewed: int,
    latest_discovery_job: Mapping[str, object] | None,
    discovery_partial_or_failed_files: int,
    requirement_count: int,
    accepted_deliverable_count: int,
) -> OwnerDecisionEvidenceSnapshot:
    """Build the reusable frozen view of facts and unresolved questions."""
    undecided = max(received_files_total - received_files_reviewed, 0)
    questions: list[str] = []
    if undecided:
        questions.append(f"{undecided} received file(s) have not been reviewed.")
    if discovery_partial_or_failed_files:
        questions.append(
            f"{discovery_partial_or_failed_files} discovery file(s) were partial or failed."
        )
    if requirement_count == 0:
        questions.append("No customer requirements have been recorded.")
    if accepted_deliverable_count == 0:
        questions.append("No discovered deliverables have been accepted.")

    job = None
    if latest_discovery_job is not None:
        job = DiscoveryJobSnapshot(
            job_id=str(latest_discovery_job["job_id"]),
            state=str(latest_discovery_job["state"]),
            files_total=_as_int(latest_discovery_job["files_total"]),
            files_done=_as_int(latest_discovery_job["files_done"]),
            files_failed=_as_int(latest_discovery_job.get("files_failed", 0)),
            passages_total=_as_int(latest_discovery_job["passages_total"]),
            passages_analysed=_as_int(latest_discovery_job["passages_analysed"]),
            passages_failed=_as_int(latest_discovery_job["passages_failed"]),
            partial_or_failed_files=discovery_partial_or_failed_files,
        )
    return OwnerDecisionEvidenceSnapshot(
        received_files=ReceivedFileReviewSnapshot(
            total=received_files_total,
            reviewed=received_files_reviewed,
            undecided=undecided,
        ),
        latest_discovery_job=job,
        requirement_count=requirement_count,
        accepted_deliverable_count=accepted_deliverable_count,
        unresolved_questions=tuple(questions),
    )


def initial_requirements_assessment_available(
    evidence: OwnerDecisionEvidenceSnapshot,
) -> bool:
    """Return whether enough initial assessment exists to reach owner decision.

    A discovery job counts once it has any terminal outcome.  Partial and failed
    outcomes deliberately count: this workflow checkpoint asks whether an
    assessment was attempted, not whether every source produced an LLM result.
    Human-authored requirements and accepted discovered deliverables are equally
    valid evidence that the initial assessment has begun.
    """
    job = evidence.latest_discovery_job
    discovery_finished = job is not None and job.state.upper() in _FINISHED_DISCOVERY_STATES
    return (
        discovery_finished
        or evidence.requirement_count > 0
        or evidence.accepted_deliverable_count > 0
    )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (table_name,)
        ).fetchone()
        is not None
    )


def collect_owner_decision_evidence(
    conn: sqlite3.Connection, bid_id: str
) -> OwnerDecisionEvidenceSnapshot:
    """Collect available register facts and pass them through the pure builder."""
    total = reviewed = 0
    if _table_exists(conn, "bid_received_files"):
        row = conn.execute(
            """SELECT count(*) AS total,
            sum(CASE WHEN current.analysis_eligibility IS NOT NULL
                AND current.analysis_eligibility<>'NOT_ASSESSED' THEN 1 ELSE 0 END) AS reviewed
            FROM bid_received_files f
            JOIN bid_received_releases r ON r.release_id=f.release_id
            LEFT JOIN bid_received_file_disposition_events current ON current.file_id=f.file_id
              AND NOT EXISTS(SELECT 1 FROM bid_received_file_disposition_events newer
                WHERE newer.supersedes_event_id=current.disposition_event_id)
            WHERE r.bid_id=?""",
            (bid_id,),
        ).fetchone()
        total = int(row["total"] or 0)
        reviewed = int(row["reviewed"] or 0)

    latest_job: sqlite3.Row | None = None
    partial_or_failed = 0
    if _table_exists(conn, "deliverable_discovery_jobs"):
        latest_job = conn.execute(
            """SELECT job_id,state,files_total,files_done,passages_total,
            passages_analysed,passages_failed,run_id FROM deliverable_discovery_jobs
            WHERE bid_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1""",
            (bid_id,),
        ).fetchone()
        if latest_job is not None:
            run_id = latest_job["run_id"]
            if run_id and _table_exists(conn, "deliverable_discovery_file_outcomes"):
                partial_or_failed = int(
                    conn.execute(
                        """SELECT count(*) FROM deliverable_discovery_file_outcomes
                        WHERE run_id=? AND outcome NOT IN
                        ('ANALYSED_CANDIDATES','ANALYSED_ZERO_CANDIDATES')""",
                        (run_id,),
                    ).fetchone()[0]
                )

    requirement_count = 0
    if _table_exists(conn, "requirements"):
        requirement_count = int(
            conn.execute(
                "SELECT count(*) FROM requirements WHERE bid_id=? AND lifecycle_state='ACTIVE'",
                (bid_id,),
            ).fetchone()[0]
        )

    accepted_count = 0
    if _table_exists(conn, "deliverable_discovery_review_events"):
        accepted_count = int(
            conn.execute(
                """SELECT count(*) FROM deliverable_discovery_review_events current
                WHERE current.bid_id=? AND current.decision='ACCEPTED'
                AND NOT EXISTS(SELECT 1 FROM deliverable_discovery_review_events newer
                    WHERE newer.supersedes_event_id=current.event_id)""",
                (bid_id,),
            ).fetchone()[0]
        )

    return build_owner_decision_evidence_snapshot(
        received_files_total=total,
        received_files_reviewed=reviewed,
        latest_discovery_job=dict(latest_job) if latest_job is not None else None,
        discovery_partial_or_failed_files=partial_or_failed,
        requirement_count=requirement_count,
        accepted_deliverable_count=accepted_count,
    )


__all__ = [
    "build_owner_decision_evidence_snapshot",
    "collect_owner_decision_evidence",
    "initial_requirements_assessment_available",
]
