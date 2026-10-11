"""SQLite persistence for OPS-13A deliverable discovery. See
docs/tasks/OPS-13A-bid-deliverables-discovery.md for the additive migration plan.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import uuid4

from core.database import Database
from core.deliverable_discovery import (
    AddendumDiscoveryFlag,
    DeliverableProposal,
    DiscoveryRun,
    ExtractionUnit,
    ProposalReviewEvent,
    SourceCitation,
    SourcePromotion,
)
from core.deliverable_discovery_classification import (
    append_citations_in_transaction,
    append_citations_transaction,
    attach_classification,
    ensure_classification_tables,
    insert_proposal_classification,
    reclassify_proposals,
    record_destination,
)
from core.deliverable_discovery_collisions import (
    CollisionSource,
    DiscoveryCollision,
    ExistingObligation,
    ObligationFacts,
    base_dedup_key,
)
from core.deliverable_discovery_evidence import (
    attach_evidence,
    ensure_evidence_tables,
    insert_proposal_evidence,
)
from core.deliverable_discovery_job_repository import migrate_deliverable_discovery_jobs
from core.deliverable_discovery_migrations import (
    COLLISIONS_TABLE,
    migrate_deliverable_discovery_collisions,
    migrate_deliverable_discovery_not_attempted,
    record_collision,
)
from core.deliverable_discovery_outcomes import (
    DiscoveryFileOutcome,
    DiscoveryRunOutcome,
)
from core.deliverable_discovery_promotion import promote_source as promote_source_transaction
from core.deliverables import Deliverable, DeliverableLink
from core.requirement_lifecycle import AppliesAt, DestinationKind, DiscoveryLifecycle, ItemKind

DISCOVERY_MIGRATION_ID = "ops_13a_deliverable_discovery_v1"
RECEIVED_FILE_MIGRATION_ID = "ops_13a_deliverable_discovery_received_files_v2"
EXTRACTIONS_TABLE = "deliverable_discovery_extractions_v2"
SOURCES_TABLE = "deliverable_discovery_proposal_sources_v2"
PROMOTIONS_MIGRATION_ID = "ops_13a_deliverable_discovery_promotions_v3"
OUTCOMES_MIGRATION_ID = "ops_13a_deliverable_discovery_run_outcomes_v4"


class DiscoveryRunConflictError(ValueError):
    """A different run already used this operation_id for a different bid."""


class DeliverableDiscoveryRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._migrate()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _migrate(self) -> None:
        statements = [
            """CREATE TABLE IF NOT EXISTS deliverable_discovery_runs (
                run_id TEXT PRIMARY KEY, bid_id TEXT NOT NULL, snapshot_id TEXT,
                operation_id TEXT NOT NULL, status TEXT NOT NULL,
                proposal_count INTEGER NOT NULL, rejected_source_count INTEGER NOT NULL,
                actor TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT NOT NULL,
                FOREIGN KEY (bid_id) REFERENCES bids(bid_id),
                CHECK (status IN ('SUCCEEDED','FAILED')),
                UNIQUE (bid_id, operation_id)
            )""",
            """CREATE TABLE IF NOT EXISTS deliverable_discovery_extractions (
                extraction_id TEXT PRIMARY KEY, bid_id TEXT NOT NULL, run_id TEXT NOT NULL,
                document_version_id TEXT NOT NULL, locator_type TEXT NOT NULL,
                locator TEXT NOT NULL, text_excerpt TEXT NOT NULL, char_count INTEGER NOT NULL,
                status TEXT NOT NULL, failure_reason TEXT, created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES deliverable_discovery_runs(run_id),
                CHECK (status IN ('OK','UNREADABLE')),
                CHECK (locator_type IN ('PAGE','PARAGRAPH','SHEET_CELL'))
            )""",
            """CREATE TABLE IF NOT EXISTS deliverable_discovery_proposals (
                proposal_id TEXT PRIMARY KEY, bid_id TEXT NOT NULL, run_id TEXT NOT NULL,
                title TEXT NOT NULL, category TEXT NOT NULL, criticality TEXT NOT NULL,
                condition_text TEXT, due_date TEXT, requested_format TEXT,
                requested_recipient TEXT, proposed_owner TEXT, dedup_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (bid_id) REFERENCES bids(bid_id),
                FOREIGN KEY (run_id) REFERENCES deliverable_discovery_runs(run_id),
                CHECK (criticality IN ('REQUIRED','CONDITIONAL','UNCLEAR')),
                UNIQUE (bid_id, dedup_key)
            )""",
            """CREATE TABLE IF NOT EXISTS deliverable_discovery_proposal_sources (
                citation_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL,
                document_version_id TEXT NOT NULL, locator_type TEXT NOT NULL,
                locator TEXT NOT NULL, excerpt TEXT NOT NULL,
                FOREIGN KEY (proposal_id) REFERENCES deliverable_discovery_proposals(proposal_id)
            )""",
            """CREATE TABLE IF NOT EXISTS deliverable_discovery_review_events (
                event_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, bid_id TEXT NOT NULL,
                decision TEXT NOT NULL, reviewer TEXT NOT NULL, rationale TEXT,
                linked_deliverable_id TEXT, supersedes_event_id TEXT, reviewed_at TEXT NOT NULL,
                FOREIGN KEY (proposal_id) REFERENCES deliverable_discovery_proposals(proposal_id),
                CHECK (decision IN ('PENDING','ACCEPTED','REJECTED','DEFERRED'))
            )""",
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_ddre_supersedes
                ON deliverable_discovery_review_events(supersedes_event_id)
                WHERE supersedes_event_id IS NOT NULL""",
            """CREATE TABLE IF NOT EXISTS deliverable_discovery_addendum_flags (
                flag_id TEXT PRIMARY KEY, bid_id TEXT NOT NULL, directive_id TEXT NOT NULL,
                proposal_id TEXT, deliverable_id TEXT, rationale TEXT NOT NULL,
                supersedes_flag_id TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL
            )""",
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_ddaf_supersedes
                ON deliverable_discovery_addendum_flags(supersedes_flag_id)
                WHERE supersedes_flag_id IS NOT NULL""",
            """CREATE TABLE IF NOT EXISTS deliverable_discovery_schema_migrations (
                migration_id TEXT PRIMARY KEY, applied_at TEXT NOT NULL
            )""",
            f"""CREATE TABLE IF NOT EXISTS {EXTRACTIONS_TABLE} (
                extraction_id TEXT PRIMARY KEY, bid_id TEXT NOT NULL, run_id TEXT NOT NULL,
                document_version_id TEXT, received_file_id TEXT,
                locator_type TEXT NOT NULL, locator TEXT NOT NULL, text_excerpt TEXT NOT NULL,
                char_count INTEGER NOT NULL, status TEXT NOT NULL, failure_reason TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES deliverable_discovery_runs(run_id),
                FOREIGN KEY (received_file_id) REFERENCES bid_received_files(file_id),
                CHECK ((document_version_id IS NOT NULL) <> (received_file_id IS NOT NULL)),
                CHECK (status IN ('OK','UNREADABLE')),
                CHECK (locator_type IN ('PAGE','PARAGRAPH','SHEET_CELL'))
            )""",
            f"""CREATE TABLE IF NOT EXISTS {SOURCES_TABLE} (
                citation_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL,
                document_version_id TEXT, received_file_id TEXT,
                locator_type TEXT NOT NULL, locator TEXT NOT NULL, excerpt TEXT NOT NULL,
                FOREIGN KEY (proposal_id) REFERENCES deliverable_discovery_proposals(proposal_id),
                FOREIGN KEY (received_file_id) REFERENCES bid_received_files(file_id),
                CHECK ((document_version_id IS NOT NULL) <> (received_file_id IS NOT NULL))
            )""",
            f"""CREATE TABLE IF NOT EXISTS deliverable_discovery_source_promotions (
                promotion_id TEXT PRIMARY KEY, bid_id TEXT NOT NULL,
                deliverable_id TEXT NOT NULL, proposal_id TEXT NOT NULL,
                citation_id TEXT NOT NULL, received_file_id TEXT NOT NULL,
                document_version_id TEXT NOT NULL, file_document_link_id TEXT NOT NULL,
                link_relationship TEXT NOT NULL, rationale TEXT NOT NULL,
                operation_id TEXT NOT NULL, promoted_by TEXT NOT NULL, promoted_at TEXT NOT NULL,
                FOREIGN KEY (deliverable_id) REFERENCES deliverable_items(deliverable_id),
                FOREIGN KEY (proposal_id) REFERENCES deliverable_discovery_proposals(proposal_id),
                FOREIGN KEY (citation_id) REFERENCES {SOURCES_TABLE}(citation_id),
                FOREIGN KEY (received_file_id) REFERENCES bid_received_files(file_id),
                FOREIGN KEY (document_version_id) REFERENCES document_versions(document_version_id),
                FOREIGN KEY (file_document_link_id) REFERENCES
                    bid_received_file_document_links(file_document_link_id),
                UNIQUE (bid_id, operation_id),
                UNIQUE (deliverable_id, citation_id)
            )""",
            """CREATE TABLE IF NOT EXISTS deliverable_discovery_run_outcomes (
                run_id TEXT PRIMARY KEY, outcome TEXT NOT NULL,
                files_included INTEGER NOT NULL, files_unsupported INTEGER NOT NULL,
                files_unreadable INTEGER NOT NULL, files_extracted INTEGER NOT NULL,
                files_analysed INTEGER NOT NULL, files_failed INTEGER NOT NULL,
                passages_total INTEGER NOT NULL, model_calls INTEGER NOT NULL,
                passages_analysed INTEGER NOT NULL, passages_model_failed INTEGER NOT NULL,
                passages_invalid_output INTEGER NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES deliverable_discovery_runs(run_id),
                CHECK (outcome IN ('SUCCEEDED','PARTIAL','FAILED'))
            )""",
            """CREATE TABLE IF NOT EXISTS deliverable_discovery_file_outcomes (
                run_id TEXT NOT NULL, received_file_id TEXT NOT NULL, outcome TEXT NOT NULL,
                passages_total INTEGER NOT NULL, model_calls INTEGER NOT NULL,
                passages_analysed INTEGER NOT NULL, passages_model_failed INTEGER NOT NULL,
                passages_invalid_output INTEGER NOT NULL, detail TEXT NOT NULL,
                created_at TEXT NOT NULL, PRIMARY KEY (run_id,received_file_id),
                FOREIGN KEY (run_id) REFERENCES deliverable_discovery_runs(run_id),
                FOREIGN KEY (received_file_id) REFERENCES bid_received_files(file_id),
                CHECK (outcome IN ('UNSUPPORTED_TYPE','UNREADABLE','EXTRACTED_NO_TEXT',
                    'NOT_ATTEMPTED','MODEL_UNAVAILABLE','INVALID_MODEL_OUTPUT','PARTIALLY_ANALYSED',
                    'ANALYSED_CANDIDATES','ANALYSED_ZERO_CANDIDATES'))
            )""",
            """CREATE TRIGGER IF NOT EXISTS ddrun_no_update
                BEFORE UPDATE ON deliverable_discovery_runs BEGIN
                SELECT RAISE(ABORT,'discovery runs are immutable'); END""",
            """CREATE TRIGGER IF NOT EXISTS ddprop_no_update
                BEFORE UPDATE ON deliverable_discovery_proposals BEGIN
                SELECT RAISE(ABORT,'discovery proposals are immutable'); END""",
            """CREATE TRIGGER IF NOT EXISTS ddreview_no_update
                BEFORE UPDATE ON deliverable_discovery_review_events BEGIN
                SELECT RAISE(ABORT,'review events are append-only'); END""",
            """CREATE TRIGGER IF NOT EXISTS ddaddendum_no_update
                BEFORE UPDATE ON deliverable_discovery_addendum_flags BEGIN
                SELECT RAISE(ABORT,'addendum flags are append-only'); END""",
            f"""CREATE TRIGGER IF NOT EXISTS ddx_v2_no_update BEFORE UPDATE ON
                {EXTRACTIONS_TABLE} BEGIN
                SELECT RAISE(ABORT,'discovery extractions are immutable'); END""",
            f"""CREATE TRIGGER IF NOT EXISTS ddx_v2_no_delete BEFORE DELETE ON
                {EXTRACTIONS_TABLE} BEGIN
                SELECT RAISE(ABORT,'discovery extractions cannot be deleted'); END""",
            f"""CREATE TRIGGER IF NOT EXISTS ddc_v2_no_update BEFORE UPDATE ON
                {SOURCES_TABLE} BEGIN
                SELECT RAISE(ABORT,'discovery sources are immutable'); END""",
            f"""CREATE TRIGGER IF NOT EXISTS ddc_v2_no_delete BEFORE DELETE ON
                {SOURCES_TABLE} BEGIN
                SELECT RAISE(ABORT,'discovery sources cannot be deleted'); END""",
            """CREATE TRIGGER IF NOT EXISTS ddsp_v3_no_update BEFORE UPDATE ON
                deliverable_discovery_source_promotions BEGIN
                SELECT RAISE(ABORT,'discovery source promotions are immutable'); END""",
            """CREATE TRIGGER IF NOT EXISTS ddsp_v3_no_delete BEFORE DELETE ON
                deliverable_discovery_source_promotions BEGIN
                SELECT RAISE(ABORT,'discovery source promotions cannot be deleted'); END""",
            """CREATE TRIGGER IF NOT EXISTS ddro_v4_no_update BEFORE UPDATE ON
                deliverable_discovery_run_outcomes BEGIN
                SELECT RAISE(ABORT,'discovery run outcomes are immutable'); END""",
            """CREATE TRIGGER IF NOT EXISTS ddro_v4_no_delete BEFORE DELETE ON
                deliverable_discovery_run_outcomes BEGIN
                SELECT RAISE(ABORT,'discovery run outcomes cannot be deleted'); END""",
            """CREATE TRIGGER IF NOT EXISTS ddfo_v4_no_update BEFORE UPDATE ON
                deliverable_discovery_file_outcomes BEGIN
                SELECT RAISE(ABORT,'discovery file outcomes are immutable'); END""",
            """CREATE TRIGGER IF NOT EXISTS ddfo_v4_no_delete BEFORE DELETE ON
                deliverable_discovery_file_outcomes BEGIN
                SELECT RAISE(ABORT,'discovery file outcomes cannot be deleted'); END""",
        ]
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in statements:
                    conn.execute(statement)
                # Preserve populated v1 evidence exactly where it is and copy it forward into
                # the additive XOR-capable tables. INSERT OR IGNORE makes startup idempotent.
                conn.execute(
                    f"""INSERT OR IGNORE INTO {EXTRACTIONS_TABLE}(
                    extraction_id,bid_id,run_id,document_version_id,received_file_id,
                    locator_type,locator,text_excerpt,char_count,status,failure_reason,created_at)
                    SELECT extraction_id,bid_id,run_id,document_version_id,NULL,locator_type,
                    locator,text_excerpt,char_count,status,failure_reason,created_at
                    FROM deliverable_discovery_extractions"""
                )
                conn.execute(
                    f"""INSERT OR IGNORE INTO {SOURCES_TABLE}(
                    citation_id,proposal_id,document_version_id,received_file_id,
                    locator_type,locator,excerpt)
                    SELECT citation_id,proposal_id,document_version_id,NULL,locator_type,
                    locator,excerpt FROM deliverable_discovery_proposal_sources"""
                )
                conn.execute(
                    "INSERT OR IGNORE INTO deliverable_discovery_schema_migrations VALUES (?,?)",
                    (DISCOVERY_MIGRATION_ID, datetime.now(UTC).isoformat()),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO deliverable_discovery_schema_migrations VALUES (?,?)",
                    (RECEIVED_FILE_MIGRATION_ID, datetime.now(UTC).isoformat()),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO deliverable_discovery_schema_migrations VALUES (?,?)",
                    (PROMOTIONS_MIGRATION_ID, datetime.now(UTC).isoformat()),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO deliverable_discovery_schema_migrations VALUES (?,?)",
                    (OUTCOMES_MIGRATION_ID, datetime.now(UTC).isoformat()),
                )
                migrate_deliverable_discovery_jobs(conn, datetime.now(UTC).isoformat())
                migrate_deliverable_discovery_not_attempted(conn, datetime.now(UTC).isoformat())
                migrate_deliverable_discovery_collisions(conn, datetime.now(UTC).isoformat())
                ensure_evidence_tables(conn)
                ensure_classification_tables(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _audit(conn: sqlite3.Connection, bid_id: str, actor: str, action: str, detail: str) -> None:
        conn.execute(
            "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) "
            "VALUES(?,?,?,?,?,?)",
            (f"AUD-{uuid4().hex}", bid_id, actor, action, detail, datetime.now(UTC).isoformat()),
        )

    def existing_dedup_keys(self, bid_id: str) -> set[str]:
        """Dedup keys already proposed for this Bid, across every run — used so a rerun or
        reimport of the same content never creates a second proposal for the same obligation."""
        with self._conn() as conn:
            return {
                row["dedup_key"]
                for row in conn.execute(
                    "SELECT dedup_key FROM deliverable_discovery_proposals WHERE bid_id=?",
                    (bid_id,),
                ).fetchall()
            }

    def existing_dedup_index(self, bid_id: str) -> dict[str, str]:
        """Map every persisted dedup key to its immutable proposal identifier."""
        with self._conn() as conn:
            return {
                str(row["dedup_key"]): str(row["proposal_id"])
                for row in conn.execute(
                    "SELECT dedup_key,proposal_id FROM deliverable_discovery_proposals "
                    "WHERE bid_id=?",
                    (bid_id,),
                ).fetchall()
            }

    def existing_collision_index(self, bid_id: str) -> dict[str, tuple[ExistingObligation, ...]]:
        """Group immutable stored findings by their unsuffixed title/lifecycle key."""
        grouped: dict[str, list[ExistingObligation]] = {}
        with self._conn() as conn:
            proposals = conn.execute(
                """SELECT proposal_id,dedup_key,condition_text,due_date
                FROM deliverable_discovery_proposals WHERE bid_id=?
                ORDER BY created_at,proposal_id""",
                (bid_id,),
            ).fetchall()
            for proposal in proposals:
                proposal_id = str(proposal["proposal_id"])
                classification = conn.execute(
                    """SELECT description,responsible_party,source_section
                    FROM deliverable_discovery_proposal_classification WHERE proposal_id=?""",
                    (proposal_id,),
                ).fetchone()
                date_evidence = conn.execute(
                    """SELECT basis,rule_text FROM deliverable_discovery_proposal_dates
                    WHERE proposal_id=?""",
                    (proposal_id,),
                ).fetchone()
                source_rows = conn.execute(
                    """SELECT source.document_version_id,source.received_file_id,
                    source.locator,quote.quote
                    FROM deliverable_discovery_proposal_sources_v2 source
                    LEFT JOIN deliverable_discovery_citation_quotes quote
                        ON quote.citation_id=source.citation_id
                    WHERE source.proposal_id=? ORDER BY source.citation_id""",
                    (proposal_id,),
                ).fetchall()
                facts = ObligationFacts(
                    description=(
                        str(classification["description"])
                        if classification is not None and classification["description"] is not None
                        else None
                    ),
                    due_date=(
                        date.fromisoformat(str(proposal["due_date"]))
                        if proposal["due_date"] is not None
                        else None
                    ),
                    due_date_basis=(
                        str(date_evidence["basis"]) if date_evidence is not None else None
                    ),
                    timing_text=(
                        str(date_evidence["rule_text"])
                        if date_evidence is not None and date_evidence["rule_text"] is not None
                        else None
                    ),
                    responsible_party=(
                        str(classification["responsible_party"])
                        if classification is not None
                        and classification["responsible_party"] is not None
                        else None
                    ),
                    condition_text=(
                        str(proposal["condition_text"])
                        if proposal["condition_text"] is not None
                        else None
                    ),
                    source_section=(
                        str(classification["source_section"])
                        if classification is not None
                        and classification["source_section"] is not None
                        else None
                    ),
                    sources=tuple(
                        CollisionSource(
                            file_id=str(
                                source["document_version_id"] or source["received_file_id"]
                            ),
                            locator=str(source["locator"]),
                            quote=str(source["quote"]) if source["quote"] is not None else None,
                        )
                        for source in source_rows
                    ),
                )
                dedup_key = str(proposal["dedup_key"])
                grouped.setdefault(base_dedup_key(dedup_key), []).append(
                    ExistingObligation(proposal_id, dedup_key, facts)
                )
        return {key: tuple(items) for key, items in grouped.items()}

    def existing_run(self, bid_id: str, operation_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT r.*,o.outcome,o.files_included,o.files_unsupported,
                o.files_unreadable,o.files_extracted,o.files_analysed,o.files_failed,
                o.passages_total,o.model_calls,o.passages_analysed,o.passages_model_failed,
                o.passages_invalid_output,o.created_at AS outcome_created_at
                FROM deliverable_discovery_runs r
                LEFT JOIN deliverable_discovery_run_outcomes o ON o.run_id=r.run_id
                WHERE r.bid_id=? AND r.operation_id=?""",
                (bid_id, operation_id),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["file_outcomes"] = self._file_outcomes(conn, str(row["run_id"]))
            return result

    def file_outcomes_for_bid(self, bid_id: str) -> list[dict[str, Any]]:
        """Return every immutable file outcome for a Bid, oldest run first."""
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """SELECT outcome.*,run.completed_at AS run_completed_at,
                    run.rowid AS run_order
                    FROM deliverable_discovery_file_outcomes outcome
                    JOIN deliverable_discovery_runs run ON run.run_id=outcome.run_id
                    WHERE run.bid_id=?
                    ORDER BY run.completed_at,run.rowid,outcome.received_file_id""",
                    (bid_id,),
                ).fetchall()
            ]

    @staticmethod
    def _file_outcomes(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in conn.execute(
                """SELECT outcome.*,file.original_filename
                FROM deliverable_discovery_file_outcomes outcome
                JOIN bid_received_files file ON file.file_id=outcome.received_file_id
                WHERE outcome.run_id=? ORDER BY lower(file.original_filename),received_file_id""",
                (run_id,),
            ).fetchall()
        ]

    def list_unreadable_extractions(self, bid_id: str) -> list[dict[str, Any]]:
        """List unreadable units only when they belong to each file's newest extraction run."""
        with self._conn() as conn:
            rows = conn.execute(
                f"""WITH file_runs AS (
                    SELECT e.received_file_id,e.run_id,r.completed_at,r.rowid,
                    ROW_NUMBER() OVER (
                        PARTITION BY e.received_file_id
                        ORDER BY r.completed_at DESC,r.rowid DESC
                    ) AS newest
                    FROM {EXTRACTIONS_TABLE} e
                    JOIN deliverable_discovery_runs r ON r.run_id=e.run_id
                    WHERE e.bid_id=? AND e.received_file_id IS NOT NULL
                    GROUP BY e.received_file_id,e.run_id,r.completed_at,r.rowid
                )
                SELECT e.received_file_id,f.original_filename,e.locator,e.failure_reason,
                    e.run_id,e.created_at
                FROM {EXTRACTIONS_TABLE} e
                JOIN file_runs latest ON latest.received_file_id=e.received_file_id
                    AND latest.run_id=e.run_id AND latest.newest=1
                JOIN bid_received_files f ON f.file_id=e.received_file_id
                WHERE e.bid_id=? AND e.status='UNREADABLE'
                ORDER BY lower(f.original_filename),e.locator,e.extraction_id""",
                (bid_id, bid_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_run(
        self,
        run: DiscoveryRun,
        extractions: list[ExtractionUnit],
        proposals: list[DeliverableProposal],
        actor: str,
        run_outcome: DiscoveryRunOutcome | None = None,
        file_outcomes: list[DiscoveryFileOutcome] | None = None,
        collisions: list[DiscoveryCollision] | None = None,
        appends: Sequence[tuple[str, Iterable[SourceCitation]]] | None = None,
    ) -> None:
        """Persist run, extraction, proposal and optional outcome facts atomically."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO deliverable_discovery_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
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
                        f"INSERT INTO {EXTRACTIONS_TABLE} VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
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
                        "INSERT INTO deliverable_discovery_proposals VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                        source_column = (
                            "document_version_id"
                            if source.document_version_id is not None
                            else "received_file_id"
                        )
                        source_id = source.document_version_id or source.received_file_id
                        known = conn.execute(
                            f"SELECT 1 FROM {EXTRACTIONS_TABLE} WHERE bid_id=? "
                            f"AND {source_column}=? AND locator=? AND status='OK'",
                            (proposal.bid_id, source_id, source.locator),
                        ).fetchone()
                        if known is None:
                            raise ValueError(
                                "proposal citation is not in this Bid's extraction cache"
                            )
                        conn.execute(
                            f"INSERT INTO {SOURCES_TABLE} VALUES (?,?,?,?,?,?,?)",
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
                for existing_id, citations in appends or ():
                    append_citations_in_transaction(conn, self._audit, existing_id, citations)
                for collision in collisions or []:
                    record_collision(conn, collision, run.completed_at)
                    self._audit(
                        conn,
                        run.bid_id,
                        actor,
                        "deliverable_discovery_collision_recorded",
                        json.dumps(
                            {
                                "proposal_id": collision.proposal_id,
                                "other_proposal_id": collision.other_proposal_id,
                                "reasons": collision.reasons,
                            },
                            sort_keys=True,
                        ),
                    )
                if run_outcome is not None:
                    conn.execute(
                        "INSERT INTO deliverable_discovery_run_outcomes VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            run_outcome.run_id,
                            run_outcome.outcome.value,
                            run_outcome.files_included,
                            run_outcome.files_unsupported,
                            run_outcome.files_unreadable,
                            run_outcome.files_extracted,
                            run_outcome.files_analysed,
                            run_outcome.files_failed,
                            run_outcome.passages_total,
                            run_outcome.model_calls,
                            run_outcome.passages_analysed,
                            run_outcome.passages_model_failed,
                            run_outcome.passages_invalid_output,
                            run.completed_at.isoformat(),
                        ),
                    )
                for file_outcome in file_outcomes or []:
                    conn.execute(
                        "INSERT INTO deliverable_discovery_file_outcomes VALUES "
                        "(?,?,?,?,?,?,?,?,?,?)",
                        (
                            file_outcome.run_id,
                            file_outcome.received_file_id,
                            file_outcome.outcome.value,
                            file_outcome.passages_total,
                            file_outcome.model_calls,
                            file_outcome.passages_analysed,
                            file_outcome.passages_model_failed,
                            file_outcome.passages_invalid_output,
                            file_outcome.detail,
                            run.completed_at.isoformat(),
                        ),
                    )
                self._audit(
                    conn,
                    run.bid_id,
                    actor,
                    "deliverable_discovery_run_completed",
                    (
                        f"{run.run_id}:{run.proposal_count} proposals"
                        if run_outcome is None
                        else f"{run.run_id}:outcome={run_outcome.outcome.value}:"
                        f"files={run_outcome.files_included}:"
                        f"analysed={run_outcome.files_analysed}:"
                        f"failed={run_outcome.files_failed}:"
                        f"passages_analysed={run_outcome.passages_analysed}:"
                        f"model_failed={run_outcome.passages_model_failed}:"
                        f"invalid_output={run_outcome.passages_invalid_output}:"
                        f"proposals={run.proposal_count}"
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def date_triggers(self, bid_id: str) -> dict[str, date]:
        """Dates a person recorded on the Bid that a calendar-day rule may be counted from."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT release_date,customer_due_date FROM bids WHERE bid_id=?", (bid_id,)
            ).fetchone()
        if row is None:
            return {}
        return {
            "issue": date.fromisoformat(str(row["release_date"])),
            "closing": date.fromisoformat(str(row["customer_due_date"])),
        }

    def list_proposals(self, bid_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM deliverable_discovery_proposals WHERE bid_id=? "
                    "ORDER BY created_at,proposal_id",
                    (bid_id,),
                ).fetchall()
            ]
            for row in rows:
                row["sources"] = [
                    dict(source)
                    for source in conn.execute(
                        f"SELECT * FROM {SOURCES_TABLE} WHERE proposal_id=? ORDER BY citation_id",
                        (row["proposal_id"],),
                    ).fetchall()
                ]
                row["review_events"] = self._review_events(conn, row["proposal_id"])
                attach_evidence(conn, row)
                attach_classification(conn, row)
            return rows

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM deliverable_discovery_proposals WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
            if row is None:
                return None
            data = dict(row)
            data["sources"] = [
                dict(source)
                for source in conn.execute(
                    f"SELECT * FROM {SOURCES_TABLE} WHERE proposal_id=? ORDER BY citation_id",
                    (proposal_id,),
                ).fetchall()
            ]
            data["review_events"] = self._review_events(conn, proposal_id)
            attach_evidence(conn, data)
            attach_classification(conn, data)
            return data

    def reclassify(
        self,
        bid_id: str,
        proposal_ids: list[str],
        lifecycle: DiscoveryLifecycle | str,
        rationale: str,
        reviewer: str,
        *,
        applies_at: AppliesAt | str | None = None,
        item_kind: ItemKind | str | None = None,
        commercial_topic: str | None = None,
    ) -> list[dict[str, Any]]:
        """Append reviewer lifecycle overrides independently for the selected proposals."""
        return reclassify_proposals(
            self._conn,
            self._audit,
            bid_id,
            proposal_ids,
            lifecycle,
            rationale,
            reviewer,
            applies_at=applies_at,
            item_kind=item_kind,
            commercial_topic=commercial_topic,
        )

    def append_citations(self, proposal_id: str, citations: list[SourceCitation]) -> None:
        """Append new source evidence to a proposal without mutating existing evidence."""
        append_citations_transaction(self._conn, self._audit, proposal_id, citations)

    def list_collisions(self, bid_id: str) -> list[dict[str, Any]]:
        """Return immutable collision evidence for tests and later review presentation."""
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    f"""SELECT collision.* FROM {COLLISIONS_TABLE} collision
                    JOIN deliverable_discovery_proposals proposal
                        ON proposal.proposal_id=collision.proposal_id
                    WHERE proposal.bid_id=? ORDER BY collision.created_at,
                        collision.proposal_id,collision.other_proposal_id""",
                    (bid_id,),
                ).fetchall()
            ]

    @staticmethod
    def _review_events(conn: sqlite3.Connection, proposal_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM deliverable_discovery_review_events WHERE proposal_id=? "
                "ORDER BY reviewed_at",
                (proposal_id,),
            ).fetchall()
        ]

    def current_decision(self, proposal_id: str) -> dict[str, Any] | None:
        """The latest (non-superseded) review event for a proposal, or None if never reviewed."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM deliverable_discovery_review_events base
                WHERE base.proposal_id=? AND NOT EXISTS (
                    SELECT 1 FROM deliverable_discovery_review_events newer
                    WHERE newer.supersedes_event_id=base.event_id
                )
                ORDER BY base.reviewed_at DESC LIMIT 1""",
                (proposal_id,),
            ).fetchone()
            return dict(row) if row else None

    def add_review_event(self, event: ProposalReviewEvent, actor: str) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                proposal = conn.execute(
                    "SELECT bid_id FROM deliverable_discovery_proposals WHERE proposal_id=?",
                    (event.proposal_id,),
                ).fetchone()
                if proposal is None or proposal["bid_id"] != event.bid_id:
                    raise ValueError("review event identity mismatch")
                conn.execute(
                    "INSERT INTO deliverable_discovery_review_events VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        event.event_id,
                        event.proposal_id,
                        event.bid_id,
                        event.decision.value,
                        event.reviewer,
                        event.rationale,
                        event.linked_deliverable_id,
                        event.supersedes_event_id,
                        event.reviewed_at.isoformat(),
                    ),
                )
                self._audit(
                    conn,
                    event.bid_id,
                    actor,
                    "deliverable_discovery_proposal_reviewed",
                    f"{event.proposal_id}:{event.decision.value}",
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def accept_proposal(
        self,
        item: Deliverable,
        link: DeliverableLink,
        event: ProposalReviewEvent,
        actor: str,
    ) -> None:
        """Create, source-link, activate, and review one accepted proposal atomically.

        The SQL and validation sequence mirrors `DeliverableRepository.create`, `add_link`,
        and `activate`; keeping it on this connection also makes the discovery review event and
        every corresponding audit row part of the same transaction.
        """
        if event.linked_deliverable_id != item.deliverable_id:
            raise ValueError("accepted review must link the created deliverable")
        if item.workflow_state.value != "DRAFT":
            raise ValueError("accepted deliverable must begin in DRAFT")
        if link.deliverable_id != item.deliverable_id or link.bid_id != item.bid_id:
            raise ValueError("deliverable link identity mismatch")
        expected_relation = {
            "DOCUMENT_VERSION": "SOURCE_DOCUMENT_VERSION",
            "RECEIVED_FILE": "SOURCE_RECEIVED_FILE",
        }.get(link.target_type.value)
        if expected_relation is None or link.relation.value != expected_relation:
            raise ValueError("discovery source link type and relation do not match")
        if event.proposal_id == "" or event.bid_id != item.bid_id:
            raise ValueError("review event identity mismatch")

        values = (
            item.deliverable_id,
            item.bid_id,
            item.title,
            item.description,
            item.category,
            item.criticality.value,
            item.materiality,
            item.lifecycle_phase.value,
            item.direction.value,
            item.workflow_state.value,
            item.owner,
            item.supplier_id,
            item.recipient,
            item.due_basis.value,
            item.fixed_due_date.isoformat() if item.fixed_due_date else None,
            item.event_name,
            item.offset_days,
            item.condition_text,
            int(item.condition_active),
            item.required_format,
            item.required_review_role,
            item.cancel_reason,
            item.version,
            item.provenance.model_dump_json(),
            item.created_at.isoformat(),
            item.updated_at.isoformat(),
            item.created_by,
        )
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                proposal = conn.execute(
                    "SELECT bid_id FROM deliverable_discovery_proposals WHERE proposal_id=?",
                    (event.proposal_id,),
                ).fetchone()
                if proposal is None or proposal["bid_id"] != item.bid_id:
                    raise ValueError("proposal not found in this Bid")
                current = conn.execute(
                    """SELECT base.event_id,base.decision
                    FROM deliverable_discovery_review_events base
                    WHERE base.proposal_id=? AND NOT EXISTS(
                        SELECT 1 FROM deliverable_discovery_review_events newer
                        WHERE newer.supersedes_event_id=base.event_id)
                    ORDER BY base.reviewed_at DESC LIMIT 1""",
                    (event.proposal_id,),
                ).fetchone()
                current_id = str(current["event_id"]) if current is not None else None
                if current is not None and current["decision"] == "ACCEPTED":
                    raise ValueError("proposal already accepted")
                if event.supersedes_event_id != current_id:
                    raise ValueError("review event supersedes stale current decision")
                conn.execute(
                    "INSERT INTO deliverable_items VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
                self._audit(conn, item.bid_id, actor, "deliverable_created", item.deliverable_id)

                if link.target_type.value == "DOCUMENT_VERSION":
                    target = conn.execute(
                        """SELECT d.bid_id FROM document_versions v JOIN documents d
                        ON d.id=v.document_id WHERE v.document_version_id=?""",
                        (link.target_id,),
                    ).fetchone()
                elif link.target_type.value == "RECEIVED_FILE":
                    target = conn.execute(
                        """SELECT r.bid_id FROM bid_received_files f JOIN bid_received_releases r
                        ON r.release_id=f.release_id WHERE f.file_id=?""",
                        (link.target_id,),
                    ).fetchone()
                else:
                    raise ValueError("discovery acceptance requires a source-document link")
                if target is None or target["bid_id"] != item.bid_id:
                    raise ValueError("link target is missing or cross-bid")
                conn.execute(
                    "INSERT INTO deliverable_links VALUES (?,?,?,?,?,?,?,?)",
                    (
                        link.link_id,
                        link.bid_id,
                        link.deliverable_id,
                        link.target_type.value,
                        link.target_id,
                        link.relation.value,
                        link.created_at.isoformat(),
                        link.created_by,
                    ),
                )
                self._audit(conn, item.bid_id, actor, "deliverable_link_created", link.link_id)
                activated = conn.execute(
                    "UPDATE deliverable_items SET workflow_state='ACTIVE',version=version+1,"
                    "updated_at=? WHERE deliverable_id=? AND version=? AND workflow_state='DRAFT'",
                    (datetime.now(UTC).isoformat(), item.deliverable_id, item.version),
                )
                if activated.rowcount != 1:
                    raise ValueError("stale or non-draft deliverable")
                self._audit(conn, item.bid_id, actor, "deliverable_activated", item.deliverable_id)
                conn.execute(
                    "INSERT INTO deliverable_discovery_review_events VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        event.event_id,
                        event.proposal_id,
                        event.bid_id,
                        event.decision.value,
                        event.reviewer,
                        event.rationale,
                        event.linked_deliverable_id,
                        event.supersedes_event_id,
                        event.reviewed_at.isoformat(),
                    ),
                )
                record_destination(
                    conn,
                    proposal_id=event.proposal_id,
                    event_id=event.event_id,
                    bid_id=event.bid_id,
                    destination_kind=DestinationKind.DELIVERABLE,
                    record_id=item.deliverable_id,
                    reused=False,
                    note="Deliverable created",
                )
                self._audit(
                    conn,
                    event.bid_id,
                    actor,
                    "deliverable_discovery_proposal_reviewed",
                    f"{event.proposal_id}:{event.decision.value}",
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def promote_source(
        self,
        *,
        bid_id: str,
        deliverable_id: str,
        citation_id: str,
        document_version_id: str,
        rationale: str,
        operation_id: str,
        actor: str,
    ) -> SourcePromotion:
        return promote_source_transaction(
            self._conn(),
            bid_id=bid_id,
            deliverable_id=deliverable_id,
            citation_id=citation_id,
            document_version_id=document_version_id,
            rationale=rationale,
            operation_id=operation_id,
            actor=actor,
            audit=self._audit,
        )

    def list_promotions(self, bid_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """SELECT * FROM deliverable_discovery_source_promotions
                    WHERE bid_id=? ORDER BY promoted_at,promotion_id""",
                    (bid_id,),
                ).fetchall()
            ]

    def add_addendum_flag(self, flag: AddendumDiscoveryFlag) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO deliverable_discovery_addendum_flags VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        flag.flag_id,
                        flag.bid_id,
                        flag.directive_id,
                        flag.proposal_id,
                        flag.deliverable_id,
                        flag.rationale,
                        flag.supersedes_flag_id,
                        flag.created_at.isoformat(),
                        flag.created_by,
                    ),
                )
                self._audit(
                    conn,
                    flag.bid_id,
                    flag.created_by,
                    "deliverable_discovery_addendum_flag_recorded",
                    flag.flag_id,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def list_addendum_flags(self, bid_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """SELECT base.* FROM deliverable_discovery_addendum_flags base
                    WHERE base.bid_id=? AND NOT EXISTS (
                        SELECT 1 FROM deliverable_discovery_addendum_flags newer
                        WHERE newer.supersedes_flag_id=base.flag_id
                    ) ORDER BY base.created_at""",
                    (bid_id,),
                ).fetchall()
            ]

    def received_file_ids_for_version(
        self, bid_id: str, document_version_id: str
    ) -> list[dict[str, Any]]:
        """Current received-file links that identify this Bid's controlled version."""
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """SELECT base.file_id,file.original_filename,base.relationship
                    FROM bid_received_file_document_links base
                    JOIN bid_received_files file ON file.file_id=base.file_id
                    JOIN bid_received_releases release ON release.release_id=file.release_id
                    WHERE release.bid_id=? AND base.document_version_id=?
                    AND base.relationship IN ('EXACT_BYTES','REPRESENTS_VERSION')
                    AND NOT EXISTS (
                        SELECT 1 FROM bid_received_file_document_links newer
                        WHERE newer.supersedes_link_id=base.file_document_link_id
                    ) ORDER BY base.created_at,base.file_document_link_id""",
                    (bid_id, document_version_id),
                ).fetchall()
            ]

    def known_source(
        self,
        bid_id: str,
        document_version_id: str | None,
        received_file_id: str | None,
        locator: str,
    ) -> bool:
        """Whether one exact source identity+locator is in this Bid's extraction cache."""
        if (document_version_id is None) == (received_file_id is None):
            raise ValueError("known source requires exactly one source identity")
        identity_column = (
            "document_version_id" if document_version_id is not None else "received_file_id"
        )
        identity = document_version_id or received_file_id
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT 1 FROM {EXTRACTIONS_TABLE} WHERE bid_id=? "
                f"AND {identity_column}=? AND locator=? AND status='OK'",
                (bid_id, identity, locator),
            ).fetchone()
            return row is not None
