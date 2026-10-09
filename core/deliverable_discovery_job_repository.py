"""Durable SQLite queue and evidence store for deliverable discovery jobs."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast
from uuid import uuid4

from core.database import Database
from core.deliverable_discovery import DeliverableProposal, DiscoveryRun, ExtractionUnit
from core.deliverable_discovery_classification import insert_proposal_classification
from core.deliverable_discovery_evidence import insert_proposal_evidence
from core.deliverable_discovery_migrations import migrate_deliverable_discovery_not_attempted
from core.deliverable_discovery_outcomes import DiscoveryFileOutcome, DiscoveryRunOutcome

JOB_MIGRATION_ID = "ops_13a_deliverable_discovery_jobs_v5"
ACTIVE_STATES = ("QUEUED", "RUNNING", "CANCEL_REQUESTED", "ENDPOINT_ERROR")
TERMINAL_STATES = ("SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED")


def _parse_runner_owner(owner: object) -> tuple[str, int] | None:
    if not isinstance(owner, str):
        return None
    try:
        prefix, raw_pid, token = owner.rsplit("-", 2)
        host = prefix.removeprefix("DDR-")
        pid = int(raw_pid)
        int(token, 16)
    except (ValueError, TypeError):
        return None
    if not prefix.startswith("DDR-") or not host or pid <= 0 or len(token) != 32:
        return None
    return host, pid


def _unexpired_owner_is_dead(
    owner: object,
    *,
    current_host: str,
    current_pid: int,
    pid_alive: Callable[[int], bool],
) -> bool:
    parsed = _parse_runner_owner(owner)
    if parsed is None:
        return False
    host, pid = parsed
    return host == current_host and pid != current_pid and not pid_alive(pid)


def migrate_deliverable_discovery_jobs(conn: sqlite3.Connection, applied_at: str) -> None:
    """Apply v5 inside the caller's migration transaction."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS deliverable_discovery_schema_migrations(
        migration_id TEXT PRIMARY KEY,applied_at TEXT NOT NULL)"""
    )
    statements = (
        """CREATE TABLE IF NOT EXISTS deliverable_discovery_jobs(
        job_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,operation_id TEXT NOT NULL,actor TEXT NOT NULL,
        state TEXT NOT NULL,force_all INTEGER NOT NULL,files_total INTEGER NOT NULL DEFAULT 0,
        files_skipped_prior INTEGER NOT NULL DEFAULT 0,passages_total INTEGER NOT NULL DEFAULT 0,
        passages_done INTEGER NOT NULL DEFAULT 0,passages_analysed INTEGER NOT NULL DEFAULT 0,
        passages_failed INTEGER NOT NULL DEFAULT 0,files_done INTEGER NOT NULL DEFAULT 0,
        current_file_id TEXT,current_file_name TEXT,
        consecutive_connection_failures INTEGER NOT NULL DEFAULT 0,
        error_code TEXT,error_message TEXT,run_id TEXT,lease_owner TEXT,lease_expires_at TEXT,
        heartbeat_at TEXT,created_at TEXT NOT NULL,started_at TEXT,finished_at TEXT,
        cancel_requested_at TEXT,
        FOREIGN KEY(bid_id) REFERENCES bids(bid_id),UNIQUE(bid_id,operation_id),
        CHECK(state IN ('QUEUED','RUNNING','CANCEL_REQUESTED','ENDPOINT_ERROR',
        'SUCCEEDED','PARTIAL','FAILED','CANCELLED')),
        CHECK(force_all IN (0,1)),CHECK(files_total>=0 AND files_skipped_prior>=0),
        CHECK(passages_total>=0 AND passages_done>=0 AND passages_analysed>=0
        AND passages_failed>=0),
        CHECK(files_done>=0 AND consecutive_connection_failures>=0))""",
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_ddjob_active_bid
        ON deliverable_discovery_jobs(bid_id)
        WHERE state IN ('QUEUED','RUNNING','CANCEL_REQUESTED','ENDPOINT_ERROR')""",
        """CREATE TABLE IF NOT EXISTS deliverable_discovery_job_files(
        job_id TEXT NOT NULL,received_file_id TEXT NOT NULL,release_id TEXT NOT NULL,
        filename TEXT NOT NULL,ordinal INTEGER NOT NULL,created_at TEXT NOT NULL,
        PRIMARY KEY(job_id,received_file_id),UNIQUE(job_id,ordinal),
        FOREIGN KEY(job_id) REFERENCES deliverable_discovery_jobs(job_id),
        FOREIGN KEY(received_file_id) REFERENCES bid_received_files(file_id))""",
        """CREATE TABLE IF NOT EXISTS deliverable_discovery_job_passages(
        job_id TEXT NOT NULL,passage_key TEXT NOT NULL,received_file_id TEXT NOT NULL,
        locator_type TEXT NOT NULL,locator TEXT NOT NULL,outcome TEXT NOT NULL,
        candidates_json TEXT NOT NULL,
        invalid_candidate_count INTEGER NOT NULL,safe_reason TEXT,created_at TEXT NOT NULL,
        PRIMARY KEY(job_id,passage_key),
        FOREIGN KEY(job_id) REFERENCES deliverable_discovery_jobs(job_id),
        FOREIGN KEY(received_file_id) REFERENCES bid_received_files(file_id),
        CHECK(locator_type IN ('PAGE','PARAGRAPH','SHEET_CELL')),
        CHECK(outcome IN ('ANALYSED','MODEL_FAILURE','INVALID_OUTPUT')),
        CHECK(invalid_candidate_count>=0),CHECK(json_valid(candidates_json)))""",
        """CREATE TRIGGER IF NOT EXISTS ddjp_v5_no_update BEFORE UPDATE ON
        deliverable_discovery_job_passages BEGIN
        SELECT RAISE(ABORT,'discovery job passages are append-only'); END""",
        """CREATE TRIGGER IF NOT EXISTS ddjp_v5_no_delete BEFORE DELETE ON
        deliverable_discovery_job_passages BEGIN
        SELECT RAISE(ABORT,'discovery job passages cannot be deleted'); END""",
        """CREATE TRIGGER IF NOT EXISTS ddjf_v5_no_update BEFORE UPDATE ON
        deliverable_discovery_job_files BEGIN
        SELECT RAISE(ABORT,'discovery job files are immutable'); END""",
        """CREATE TRIGGER IF NOT EXISTS ddjf_v5_no_delete BEFORE DELETE ON
        deliverable_discovery_job_files BEGIN
        SELECT RAISE(ABORT,'discovery job files cannot be deleted'); END""",
    )
    for statement in statements:
        conn.execute(statement)
    conn.execute(
        "INSERT OR IGNORE INTO deliverable_discovery_schema_migrations VALUES (?,?)",
        (JOB_MIGRATION_ID, applied_at),
    )


class DeliverableDiscoveryJobRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            migrate_deliverable_discovery_jobs(conn, datetime.now().astimezone().isoformat())
            migrate_deliverable_discovery_not_attempted(
                conn, datetime.now().astimezone().isoformat()
            )
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = cast(sqlite3.Connection, self.db._conn())
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @staticmethod
    def _row(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT * FROM deliverable_discovery_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        return dict(row) if row else None

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            return self._row(conn, job_id)

    def active(self, bid_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM deliverable_discovery_jobs WHERE bid_id=? AND state IN (?,?,?,?)",
                (bid_id, *ACTIVE_STATES),
            ).fetchone()
            return dict(row) if row else None

    def latest(self, bid_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM deliverable_discovery_jobs WHERE bid_id=?
                ORDER BY created_at DESC,rowid DESC LIMIT 1""",
                (bid_id,),
            ).fetchone()
            return dict(row) if row else None

    def latest_finished(self, bid_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM deliverable_discovery_jobs WHERE bid_id=?
                AND state IN (?,?,?,?) ORDER BY created_at DESC,rowid DESC LIMIT 1""",
                (bid_id, *TERMINAL_STATES),
            ).fetchone()
            return dict(row) if row else None

    def create(
        self,
        bid_id: str,
        operation_id: str,
        actor: str,
        force_all: bool,
        eligible_files: list[dict[str, object]],
        now: datetime,
    ) -> dict[str, Any]:
        timestamp = now.isoformat()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            same = conn.execute(
                "SELECT * FROM deliverable_discovery_jobs WHERE bid_id=? AND operation_id=?",
                (bid_id, operation_id),
            ).fetchone()
            if same:
                result = dict(same)
                result["already_active"] = False
                return result
            active = conn.execute(
                "SELECT * FROM deliverable_discovery_jobs WHERE bid_id=? AND state IN (?,?,?,?)",
                (bid_id, *ACTIVE_STATES),
            ).fetchone()
            if active:
                result = dict(active)
                result["already_active"] = True
                return result
            analysed = {
                str(row[0])
                for row in conn.execute(
                    """SELECT DISTINCT f.received_file_id FROM deliverable_discovery_file_outcomes f
                    JOIN deliverable_discovery_runs r ON r.run_id=f.run_id WHERE r.bid_id=?
                    AND f.outcome IN ('ANALYSED_CANDIDATES','ANALYSED_ZERO_CANDIDATES')""",
                    (bid_id,),
                )
            }
            scoped = [f for f in eligible_files if force_all or str(f["file_id"]) not in analysed]
            skipped = len(eligible_files) - len(scoped)
            job_id = f"DDJ-{uuid4().hex}"
            state = "QUEUED" if scoped else "SUCCEEDED"
            error_code = None if scoped else "NOTHING_NEW"
            message = None if scoped else "All Included files were fully analysed previously."
            finished = None if scoped else timestamp
            conn.execute(
                """INSERT INTO deliverable_discovery_jobs(
                job_id,bid_id,operation_id,actor,state,force_all,files_total,files_skipped_prior,
                error_code,error_message,created_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job_id,
                    bid_id,
                    operation_id,
                    actor,
                    state,
                    int(force_all),
                    len(scoped),
                    skipped,
                    error_code,
                    message,
                    timestamp,
                    finished,
                ),
            )
            for ordinal, file_row in enumerate(scoped):
                conn.execute(
                    "INSERT INTO deliverable_discovery_job_files VALUES(?,?,?,?,?,?)",
                    (
                        job_id,
                        str(file_row["file_id"]),
                        str(file_row["release_id"]),
                        str(file_row["original_filename"]),
                        ordinal,
                        timestamp,
                    ),
                )
            conn.commit()
            result = self._row(conn, job_id) or {}
            result["already_active"] = False
            return result

    def files(self, job_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM deliverable_discovery_job_files WHERE job_id=? ORDER BY ordinal",
                    (job_id,),
                )
            ]

    def passages(self, job_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """SELECT * FROM deliverable_discovery_job_passages
                    WHERE job_id=? ORDER BY rowid""",
                    (job_id,),
                )
            ]

    def claim(
        self,
        job_id: str,
        owner: str,
        now: datetime,
        expires: datetime,
        *,
        current_host: str = "",
        current_pid: int = 0,
        pid_alive: Callable[[int], bool] | None = None,
    ) -> bool:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT state,lease_owner,lease_expires_at
                FROM deliverable_discovery_jobs WHERE job_id=?""",
                (job_id,),
            ).fetchone()
            if row is None:
                return False
            expired = row["lease_expires_at"] is None or row["lease_expires_at"] <= now.isoformat()
            dead_local_owner = bool(
                pid_alive
                and current_host
                and current_pid
                and _unexpired_owner_is_dead(
                    row["lease_owner"],
                    current_host=current_host,
                    current_pid=current_pid,
                    pid_alive=pid_alive,
                )
            )
            claimable = row["state"] == "QUEUED" or (
                row["state"] in ("RUNNING", "CANCEL_REQUESTED") and (expired or dead_local_owner)
            )
            if not claimable:
                return False
            state = "RUNNING" if row["state"] == "QUEUED" else row["state"]
            conn.execute(
                """UPDATE deliverable_discovery_jobs SET state=?,lease_owner=?,lease_expires_at=?,
                heartbeat_at=?,started_at=COALESCE(started_at,?) WHERE job_id=?""",
                (state, owner, expires.isoformat(), now.isoformat(), now.isoformat(), job_id),
            )
            conn.commit()
            return True

    def claimable_ids(
        self,
        now: datetime,
        *,
        current_host: str = "",
        current_pid: int = 0,
        pid_alive: Callable[[int], bool] | None = None,
    ) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT job_id,state,lease_owner,lease_expires_at
                FROM deliverable_discovery_jobs
                WHERE state IN ('QUEUED','RUNNING','CANCEL_REQUESTED')
                ORDER BY created_at,rowid"""
            )
            result: list[str] = []
            for row in rows:
                expired = (
                    row["lease_expires_at"] is None or row["lease_expires_at"] <= now.isoformat()
                )
                dead_local_owner = bool(
                    pid_alive
                    and current_host
                    and current_pid
                    and _unexpired_owner_is_dead(
                        row["lease_owner"],
                        current_host=current_host,
                        current_pid=current_pid,
                        pid_alive=pid_alive,
                    )
                )
                if row["state"] == "QUEUED" or expired or dead_local_owner:
                    result.append(str(row["job_id"]))
            return result

    def set_totals(
        self, job_id: str, owner: str, total: int, now: datetime, expires: datetime
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE deliverable_discovery_jobs
                SET passages_total=?,heartbeat_at=?,lease_expires_at=?
                WHERE job_id=? AND lease_owner=? AND state IN ('RUNNING','CANCEL_REQUESTED')""",
                (total, now.isoformat(), expires.isoformat(), job_id, owner),
            )

    def record_passage(
        self,
        job_id: str,
        owner: str,
        passage: dict[str, object],
        transport_failure: bool,
        now: datetime,
        expires: datetime,
    ) -> int | None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job = conn.execute(
                "SELECT * FROM deliverable_discovery_jobs WHERE job_id=? AND lease_owner=?",
                (job_id, owner),
            ).fetchone()
            if job is None or job["state"] not in ("RUNNING", "CANCEL_REQUESTED"):
                return None
            inserted = conn.execute(
                """INSERT OR IGNORE INTO deliverable_discovery_job_passages
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    job_id,
                    passage["passage_key"],
                    passage["received_file_id"],
                    passage["locator_type"],
                    passage["locator"],
                    passage["outcome"],
                    passage["candidates_json"],
                    passage["invalid_candidate_count"],
                    passage["safe_reason"],
                    now.isoformat(),
                ),
            ).rowcount
            failures = int(job["consecutive_connection_failures"])
            if inserted:
                failures = failures + 1 if transport_failure else 0
                analysed = int(passage["outcome"] == "ANALYSED")
                conn.execute(
                    """UPDATE deliverable_discovery_jobs SET passages_done=passages_done+1,
                    passages_analysed=passages_analysed+?,passages_failed=passages_failed+?,
                    consecutive_connection_failures=?,heartbeat_at=?,lease_expires_at=?
                    ,current_file_id=?,current_file_name=(SELECT filename FROM
                    deliverable_discovery_job_files WHERE job_id=? AND received_file_id=?)
                    WHERE job_id=?""",
                    (
                        analysed,
                        1 - analysed,
                        failures,
                        now.isoformat(),
                        expires.isoformat(),
                        passage["received_file_id"],
                        job_id,
                        passage["received_file_id"],
                        job_id,
                    ),
                )
            conn.commit()
            return failures

    def release(self, job_id: str, owner: str) -> bool:
        """Release one owned lease while preserving resumable job evidence."""
        with self._conn() as conn:
            updated = conn.execute(
                """UPDATE deliverable_discovery_jobs
                SET state=CASE WHEN state='RUNNING' THEN 'QUEUED' ELSE state END,
                lease_owner=NULL,lease_expires_at=NULL
                WHERE job_id=? AND lease_owner=?
                AND state IN ('RUNNING','CANCEL_REQUESTED')""",
                (job_id, owner),
            ).rowcount
            return updated == 1

    def release_owned(self, owner: str) -> int:
        """Release every active lease owned by one runner in a guarded statement."""
        with self._conn() as conn:
            return int(
                conn.execute(
                    """UPDATE deliverable_discovery_jobs
                    SET state=CASE WHEN state='RUNNING' THEN 'QUEUED' ELSE state END,
                    lease_owner=NULL,lease_expires_at=NULL
                    WHERE lease_owner=? AND state IN ('RUNNING','CANCEL_REQUESTED')""",
                    (owner,),
                ).rowcount
            )

    def file_progress(self, job_id: str, owner: str, file_id: str, name: str, done: int) -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE deliverable_discovery_jobs SET current_file_id=?,current_file_name=?,
                files_done=max(files_done,?) WHERE job_id=? AND lease_owner=?""",
                (file_id, name, done, job_id, owner),
            )

    def endpoint_error(self, job_id: str, owner: str, message: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE deliverable_discovery_jobs SET state='ENDPOINT_ERROR',
                error_code='ENDPOINT_UNAVAILABLE',error_message=?,lease_owner=NULL,
                lease_expires_at=NULL WHERE job_id=? AND lease_owner=?""",
                (message[:300], job_id, owner),
            )

    def cancel(self, job_id: str, now: datetime) -> dict[str, Any] | None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._row(conn, job_id)
            if row is None or row["state"] in TERMINAL_STATES:
                return row
            if row["state"] == "QUEUED":
                conn.execute(
                    """UPDATE deliverable_discovery_jobs SET state='CANCELLED',finished_at=?,
                    cancel_requested_at=?,lease_owner=NULL,lease_expires_at=NULL WHERE job_id=?""",
                    (now.isoformat(), now.isoformat(), job_id),
                )
            else:
                conn.execute(
                    """UPDATE deliverable_discovery_jobs SET state='CANCEL_REQUESTED',
                    cancel_requested_at=?,lease_owner=CASE WHEN state='ENDPOINT_ERROR'
                    THEN NULL ELSE lease_owner END,
                    lease_expires_at=CASE WHEN state='ENDPOINT_ERROR'
                    THEN NULL ELSE lease_expires_at END
                    WHERE job_id=?""",
                    (now.isoformat(), job_id),
                )
            conn.commit()
            return self._row(conn, job_id)

    def resume(self, job_id: str, now: datetime) -> dict[str, Any]:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._row(conn, job_id)
            if row is None:
                raise ValueError("discovery job not found")
            expired = row["lease_expires_at"] is None or row["lease_expires_at"] <= now.isoformat()
            if row["state"] != "ENDPOINT_ERROR" and not (row["state"] == "RUNNING" and expired):
                raise ValueError("discovery job is not resumable")
            conn.execute(
                """UPDATE deliverable_discovery_jobs
                SET state='QUEUED',error_code=NULL,error_message=NULL,
                lease_owner=NULL,lease_expires_at=NULL,
                consecutive_connection_failures=0 WHERE job_id=?""",
                (job_id,),
            )
            conn.commit()
            return self._row(conn, job_id) or {}

    def finalize_without_run(self, job_id: str, owner: str, now: datetime) -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE deliverable_discovery_jobs SET state='CANCELLED',finished_at=?,
                lease_owner=NULL,lease_expires_at=NULL WHERE job_id=? AND lease_owner=?
                AND state='CANCEL_REQUESTED' AND run_id IS NULL""",
                (now.isoformat(), job_id, owner),
            )

    def finalize_run(
        self,
        job_id: str,
        owner: str,
        run: DiscoveryRun,
        extractions: list[ExtractionUnit],
        proposals: list[DeliverableProposal],
        run_outcome: DiscoveryRunOutcome,
        file_outcomes: list[DiscoveryFileOutcome],
        cancelled: bool,
    ) -> bool:
        """Write immutable run facts and terminal job state in one transaction."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job = self._row(conn, job_id)
            if job is None or job["run_id"] is not None or job["finished_at"] is not None:
                return False
            if job["lease_owner"] != owner or job["state"] not in ("RUNNING", "CANCEL_REQUESTED"):
                return False
            self._insert_run(conn, run, extractions, proposals, run_outcome, file_outcomes)
            terminal = "CANCELLED" if cancelled else run_outcome.outcome.value
            updated = conn.execute(
                """UPDATE deliverable_discovery_jobs
                SET state=?,run_id=?,finished_at=?,
                files_done=CASE WHEN ? THEN files_done ELSE files_total END,
                current_file_id=NULL,current_file_name=NULL,lease_owner=NULL,lease_expires_at=NULL
                WHERE job_id=? AND run_id IS NULL AND finished_at IS NULL""",
                (terminal, run.run_id, run.completed_at.isoformat(), int(cancelled), job_id),
            ).rowcount
            if updated != 1:
                conn.rollback()
                return False
            detail = (
                f"{run.run_id}:outcome={run_outcome.outcome.value}:files={run_outcome.files_included}:"
                f"analysed={run_outcome.files_analysed}:failed={run_outcome.files_failed}:"
                f"passages_analysed={run_outcome.passages_analysed}:"
                f"model_failed={run_outcome.passages_model_failed}:"
                f"invalid_output={run_outcome.passages_invalid_output}:proposals={run.proposal_count}:"
                f"job={job_id}:job_state={terminal}:passages_done={job['passages_done']}"
            )
            conn.execute(
                """INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp)
                VALUES(?,?,?,?,?,?)""",
                (
                    f"AUD-{uuid4().hex}",
                    run.bid_id,
                    run.actor,
                    "deliverable_discovery_run_completed",
                    detail,
                    run.completed_at.isoformat(),
                ),
            )
            conn.commit()
            return True

    @staticmethod
    def _insert_run(
        conn: sqlite3.Connection,
        run: DiscoveryRun,
        extractions: list[ExtractionUnit],
        proposals: list[DeliverableProposal],
        outcome: DiscoveryRunOutcome,
        file_outcomes: list[DiscoveryFileOutcome],
    ) -> None:
        conn.execute(
            "INSERT INTO deliverable_discovery_runs VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                run.run_id,
                run.bid_id,
                run.snapshot_id,
                run.operation_id,
                run.status.value,
                run.proposal_count,
                run.rejected_source_count,
                run.actor,
                run.started_at.isoformat(),
                run.completed_at.isoformat(),
            ),
        )
        for unit in extractions:
            conn.execute(
                "INSERT INTO deliverable_discovery_extractions_v2 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    unit.extraction_id,
                    unit.bid_id,
                    unit.run_id,
                    unit.document_version_id,
                    unit.received_file_id,
                    unit.locator_type.value,
                    unit.locator,
                    unit.text_excerpt,
                    unit.char_count,
                    unit.status.value,
                    unit.failure_reason,
                    unit.created_at.isoformat(),
                ),
            )
        for proposal in proposals:
            conn.execute(
                "INSERT INTO deliverable_discovery_proposals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    proposal.proposal_id,
                    proposal.bid_id,
                    proposal.run_id,
                    proposal.title,
                    proposal.category,
                    proposal.criticality.value,
                    proposal.condition_text,
                    proposal.due_date.isoformat() if proposal.due_date else None,
                    proposal.requested_format,
                    proposal.requested_recipient,
                    proposal.proposed_owner,
                    proposal.dedup_key,
                    proposal.created_at.isoformat(),
                ),
            )
            for source in proposal.sources:
                conn.execute(
                    "INSERT INTO deliverable_discovery_proposal_sources_v2 VALUES(?,?,?,?,?,?,?)",
                    (
                        source.citation_id,
                        proposal.proposal_id,
                        source.document_version_id,
                        source.received_file_id,
                        source.locator_type.value,
                        source.locator,
                        source.excerpt,
                    ),
                )
            insert_proposal_evidence(conn, proposal)
            insert_proposal_classification(conn, proposal)
        conn.execute(
            "INSERT INTO deliverable_discovery_run_outcomes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                outcome.run_id,
                outcome.outcome.value,
                outcome.files_included,
                outcome.files_unsupported,
                outcome.files_unreadable,
                outcome.files_extracted,
                outcome.files_analysed,
                outcome.files_failed,
                outcome.passages_total,
                outcome.model_calls,
                outcome.passages_analysed,
                outcome.passages_model_failed,
                outcome.passages_invalid_output,
                run.completed_at.isoformat(),
            ),
        )
        for item in file_outcomes:
            conn.execute(
                "INSERT INTO deliverable_discovery_file_outcomes VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    item.run_id,
                    item.received_file_id,
                    item.outcome.value,
                    item.passages_total,
                    item.model_calls,
                    item.passages_analysed,
                    item.passages_model_failed,
                    item.passages_invalid_output,
                    item.detail,
                    run.completed_at.isoformat(),
                ),
            )

    def existing_dedup_keys(self, bid_id: str) -> set[str]:
        with self._conn() as conn:
            return {
                str(row[0])
                for row in conn.execute(
                    "SELECT dedup_key FROM deliverable_discovery_proposals WHERE bid_id=?",
                    (bid_id,),
                )
            }
