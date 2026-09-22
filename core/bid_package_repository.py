"""Additive SQLite persistence for the three OPS-11 Bid package ledgers."""
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import cast

from core.bid_package_intake import (
    AcknowledgementCreate,
    AnalysisEligibility,
    BulkFileDispositionCreate,
    ChannelCheckCreate,
    DirectiveCreate,
    DirectiveDispositionCreate,
    FileDispositionCreate,
    FileDocumentLinkCreate,
    IntakeAttention,
    PackageIntakeSummary,
    ReleaseNoticeCreate,
    ReleasePreview,
    ReleaseRegistration,
    SnapshotCreate,
)
from core.bid_package_storage import StagedRelease
from core.database import Database
from core.schemas import Provenance

OPS_11_MIGRATION_ID = "ops_11_bid_package_intake_addendum_control_v1"


class IntakeNotFoundError(ValueError):
    """Raised when an intake record does not exist in the expected Bid."""


class StaleIntakeError(ValueError):
    """Raised when correction lineage or current-snapshot evidence changed."""


class IntakeConflictError(ValueError):
    """Raised when authoritative intake evidence is internally contradictory."""


class ReleaseNotIncorporableError(ValueError):
    """Raised when a release does not meet deterministic incorporation rules."""


def _dict(row: sqlite3.Row) -> dict[str, object]:
    return {str(key): row[key] for key in row.keys()}


class BidPackageRepository:
    """Authoritative repository with immutable evidence and append-only decisions."""

    _TABLES = (
        "bid_release_notices",
        "bid_release_notice_items",
        "bid_release_channel_checks",
        "bid_received_releases",
        "bid_release_notice_links",
        "bid_received_files",
        "bid_received_file_document_links",
        "bid_received_file_disposition_events",
        "bid_addendum_directives",
        "bid_addendum_dispositions",
        "bid_release_acknowledgement_events",
        "bid_basis_snapshots",
        "bid_basis_snapshot_releases",
        "bid_basis_snapshot_documents",
        "bid_intake_processing_runs",
        "bid_intake_processing_file_results",
        "bid_intake_work_item_links",
        "bid_package_intake_schema_migrations",
    )

    def __init__(self, db: Database) -> None:
        self.db = db
        self._apply_migration()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _audit(
        conn: sqlite3.Connection,
        *,
        entry_id: str,
        bid_id: str,
        actor: str,
        action: str,
        detail: dict[str, object],
        at: datetime,
    ) -> None:
        conn.execute(
            "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) VALUES(?,?,?,?,?,?)",
            (entry_id, bid_id, actor, action, json.dumps(detail, sort_keys=True), at.isoformat()),
        )

    def _apply_migration(self) -> None:
        tables = (
            """CREATE TABLE IF NOT EXISTS bid_release_notices(
            notice_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,release_type TEXT NOT NULL,
            exact_customer_reference TEXT,customer_issue_date TEXT,expected_receipt_date TEXT,
            channel TEXT NOT NULL,observed_at TEXT NOT NULL,expectation TEXT NOT NULL,
            summary TEXT NOT NULL,evidence_document_version_id TEXT,evidence_reference TEXT,
            supersedes_notice_id TEXT,operation_id TEXT NOT NULL,created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,provenance_json TEXT NOT NULL,
            FOREIGN KEY(bid_id) REFERENCES bids(bid_id),
            FOREIGN KEY(evidence_document_version_id) REFERENCES document_versions(document_version_id),
            FOREIGN KEY(supersedes_notice_id) REFERENCES bid_release_notices(notice_id),
            CHECK(release_type IN ('INITIAL_PACKAGE','ADDENDUM','CLARIFICATION','REVISED_PACKAGE','OTHER')),
            CHECK(channel IN ('EMAIL','PORTAL','LETTER','TELEPHONE','MEETING','ADDENDUM_DOCUMENT','HAND_DELIVERY','OTHER')),
            CHECK(expectation IN ('EXPECTED','INFORMATION_ONLY','RESOLVED_NOT_REQUIRED')),
            UNIQUE(bid_id,operation_id))""",
            """CREATE TABLE IF NOT EXISTS bid_release_notice_items(
            notice_item_id TEXT PRIMARY KEY,notice_id TEXT NOT NULL,item_sequence INTEGER NOT NULL,
            declared_identifier TEXT,declared_title TEXT,declared_revision TEXT,
            expected_filename TEXT,operation_id TEXT NOT NULL,created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,provenance_json TEXT NOT NULL,
            FOREIGN KEY(notice_id) REFERENCES bid_release_notices(notice_id),
            CHECK(item_sequence>0),UNIQUE(notice_id,item_sequence),UNIQUE(notice_id,operation_id))""",
            """CREATE TABLE IF NOT EXISTS bid_release_channel_checks(
            channel_check_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,channel TEXT NOT NULL,
            checked_at TEXT NOT NULL,observed_customer_reference TEXT,customer_issue_date TEXT,
            result TEXT NOT NULL,evidence_reference TEXT,operation_id TEXT NOT NULL,
            created_by TEXT NOT NULL,created_at TEXT NOT NULL,provenance_json TEXT NOT NULL,
            FOREIGN KEY(bid_id) REFERENCES bids(bid_id),
            CHECK(channel IN ('EMAIL','PORTAL','LETTER','TELEPHONE','MEETING','ADDENDUM_DOCUMENT','HAND_DELIVERY','OTHER')),
            UNIQUE(bid_id,operation_id))""",
            """CREATE TABLE IF NOT EXISTS bid_received_releases(
            release_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,release_type TEXT NOT NULL,
            exact_customer_reference TEXT,customer_issue_date TEXT,received_at TEXT NOT NULL,
            received_channel TEXT NOT NULL,receipt_sequence INTEGER NOT NULL,copy_mode TEXT NOT NULL,
            source_root_alias TEXT NOT NULL,source_fingerprint TEXT NOT NULL,file_count INTEGER NOT NULL,
            total_bytes INTEGER NOT NULL,managed_release_key TEXT NOT NULL UNIQUE,note TEXT,
            operation_id TEXT NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL,
            provenance_json TEXT NOT NULL,FOREIGN KEY(bid_id) REFERENCES bids(bid_id),
            CHECK(release_type IN ('INITIAL_PACKAGE','ADDENDUM','CLARIFICATION','REVISED_PACKAGE','OTHER')),
            CHECK(received_channel IN ('EMAIL','PORTAL','LETTER','TELEPHONE','MEETING','ADDENDUM_DOCUMENT','HAND_DELIVERY','OTHER')),
            CHECK(receipt_sequence>0),CHECK(copy_mode='COPY_MANAGED_ONLY'),CHECK(file_count>=0),
            CHECK(total_bytes>=0),UNIQUE(bid_id,receipt_sequence),UNIQUE(bid_id,operation_id))""",
            """CREATE TABLE IF NOT EXISTS bid_release_notice_links(
            notice_link_id TEXT PRIMARY KEY,notice_id TEXT NOT NULL,release_id TEXT NOT NULL,
            relationship TEXT NOT NULL,operation_id TEXT NOT NULL,created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,provenance_json TEXT NOT NULL,
            FOREIGN KEY(notice_id) REFERENCES bid_release_notices(notice_id),
            FOREIGN KEY(release_id) REFERENCES bid_received_releases(release_id),
            CHECK(relationship IN ('FULFILS','PARTIALLY_FULFILS','RELATED')),
            UNIQUE(notice_id,release_id,relationship),UNIQUE(operation_id))""",
            """CREATE TABLE IF NOT EXISTS bid_received_files(
            file_id TEXT PRIMARY KEY,release_id TEXT NOT NULL,original_relative_path TEXT NOT NULL,
            original_filename TEXT NOT NULL,extension TEXT NOT NULL,detected_media_type TEXT,
            byte_size INTEGER NOT NULL,sha256 TEXT NOT NULL,managed_storage_key TEXT NOT NULL UNIQUE,
            registered_by TEXT NOT NULL,registered_at TEXT NOT NULL,provenance_json TEXT NOT NULL,
            FOREIGN KEY(release_id) REFERENCES bid_received_releases(release_id),
            CHECK(length(original_relative_path)>0 AND length(original_relative_path)<=2048),
            CHECK(length(original_filename)>0 AND length(original_filename)<=512),
            CHECK(byte_size>=0),CHECK(length(sha256)=64 AND sha256=lower(sha256)
            AND sha256 NOT GLOB '*[^0-9a-f]*'),UNIQUE(release_id,original_relative_path))""",
            """CREATE TABLE IF NOT EXISTS bid_received_file_document_links(
            file_document_link_id TEXT PRIMARY KEY,file_id TEXT NOT NULL,document_version_id TEXT NOT NULL,
            relationship TEXT NOT NULL,supersedes_link_id TEXT,operation_id TEXT NOT NULL,
            created_by TEXT NOT NULL,created_at TEXT NOT NULL,provenance_json TEXT NOT NULL,
            FOREIGN KEY(file_id) REFERENCES bid_received_files(file_id),
            FOREIGN KEY(document_version_id) REFERENCES document_versions(document_version_id),
            FOREIGN KEY(supersedes_link_id) REFERENCES bid_received_file_document_links(file_document_link_id),
            CHECK(relationship IN ('EXACT_BYTES','REPRESENTS_VERSION','SUPPORTING_EVIDENCE')),
            UNIQUE(file_id,operation_id))""",
            """CREATE TABLE IF NOT EXISTS bid_received_file_disposition_events(
            disposition_event_id TEXT PRIMARY KEY,file_id TEXT NOT NULL,content_form TEXT NOT NULL,
            classification_method TEXT NOT NULL,confidence REAL,analysis_eligibility TEXT NOT NULL,
            exclusion_reason TEXT,duplicate_of_file_id TEXT,supersedes_event_id TEXT,
            operation_id TEXT NOT NULL,actor TEXT NOT NULL,recorded_at TEXT NOT NULL,
            provenance_json TEXT NOT NULL,FOREIGN KEY(file_id) REFERENCES bid_received_files(file_id),
            FOREIGN KEY(duplicate_of_file_id) REFERENCES bid_received_files(file_id),
            FOREIGN KEY(supersedes_event_id) REFERENCES bid_received_file_disposition_events(disposition_event_id),
            CHECK(content_form IN ('TEXTUAL','DRAWING','MIXED','UNKNOWN')),
            CHECK(classification_method IN ('SAFE_DEFAULT','HUMAN_REVIEW','LOCAL_RULE','LOCAL_AI_PROPOSAL','IMPORTED_AI_PROPOSAL')),
            CHECK(analysis_eligibility IN ('NOT_ASSESSED','ELIGIBLE','EXCLUDED')),
            CHECK(confidence IS NULL OR (confidence>=0 AND confidence<=1)),
            CHECK((analysis_eligibility='EXCLUDED' AND length(trim(exclusion_reason))>0)
            OR (analysis_eligibility<>'EXCLUDED' AND exclusion_reason IS NULL)),
            CHECK(duplicate_of_file_id IS NULL OR duplicate_of_file_id<>file_id),
            UNIQUE(file_id,operation_id))""",
            """CREATE TABLE IF NOT EXISTS bid_addendum_directives(
            directive_id TEXT PRIMARY KEY,release_id TEXT NOT NULL,directive_type TEXT NOT NULL,
            description TEXT NOT NULL,materiality TEXT NOT NULL,source_file_id TEXT,
            source_locator TEXT,target_document_version_id TEXT,supersedes_directive_id TEXT,
            operation_id TEXT NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL,
            provenance_json TEXT NOT NULL,FOREIGN KEY(release_id) REFERENCES bid_received_releases(release_id),
            FOREIGN KEY(source_file_id) REFERENCES bid_received_files(file_id),
            FOREIGN KEY(target_document_version_id) REFERENCES document_versions(document_version_id),
            FOREIGN KEY(supersedes_directive_id) REFERENCES bid_addendum_directives(directive_id),
            CHECK(materiality IN ('MATERIAL','NON_MATERIAL')),UNIQUE(release_id,operation_id))""",
            """CREATE TABLE IF NOT EXISTS bid_addendum_dispositions(
            disposition_id TEXT PRIMARY KEY,directive_id TEXT NOT NULL,status TEXT NOT NULL,
            rationale TEXT NOT NULL,resulting_document_version_id TEXT,approval_id TEXT,route_id TEXT,
            supersedes_disposition_id TEXT,operation_id TEXT NOT NULL,decided_by TEXT NOT NULL,
            decided_at TEXT NOT NULL,provenance_json TEXT NOT NULL,
            FOREIGN KEY(directive_id) REFERENCES bid_addendum_directives(directive_id),
            FOREIGN KEY(resulting_document_version_id) REFERENCES document_versions(document_version_id),
            FOREIGN KEY(approval_id) REFERENCES approvals(approval_id),
            FOREIGN KEY(route_id) REFERENCES approval_routes(route_id),
            FOREIGN KEY(supersedes_disposition_id) REFERENCES bid_addendum_dispositions(disposition_id),
            CHECK(status IN ('INCORPORATED','NOT_APPLICABLE','INTERNAL_WAIVER','UNRESOLVED')),
            CHECK((status='INCORPORATED' AND resulting_document_version_id IS NOT NULL
            AND approval_id IS NULL AND route_id IS NULL) OR
            (status='INTERNAL_WAIVER' AND resulting_document_version_id IS NULL
            AND ((approval_id IS NOT NULL)+(route_id IS NOT NULL)=1)) OR
            (status IN ('NOT_APPLICABLE','UNRESOLVED') AND resulting_document_version_id IS NULL
            AND approval_id IS NULL AND route_id IS NULL)),UNIQUE(directive_id,operation_id))""",
            """CREATE TABLE IF NOT EXISTS bid_release_acknowledgement_events(
            acknowledgement_event_id TEXT PRIMARY KEY,release_id TEXT NOT NULL,event_type TEXT NOT NULL,
            due_at TEXT,acknowledgement_reference TEXT,approval_id TEXT,route_id TEXT,note TEXT,
            supersedes_event_id TEXT,operation_id TEXT NOT NULL,actor TEXT NOT NULL,
            recorded_at TEXT NOT NULL,provenance_json TEXT NOT NULL,
            FOREIGN KEY(release_id) REFERENCES bid_received_releases(release_id),
            FOREIGN KEY(approval_id) REFERENCES approvals(approval_id),
            FOREIGN KEY(route_id) REFERENCES approval_routes(route_id),
            FOREIGN KEY(supersedes_event_id) REFERENCES bid_release_acknowledgement_events(acknowledgement_event_id),
            CHECK(event_type IN ('REQUIRED','NOT_REQUIRED','ACKNOWLEDGED','EXCEPTION_APPROVED')),
            CHECK((event_type='ACKNOWLEDGED' AND length(trim(acknowledgement_reference))>0
            AND approval_id IS NULL AND route_id IS NULL) OR
            (event_type='EXCEPTION_APPROVED' AND ((approval_id IS NOT NULL)+(route_id IS NOT NULL)=1)) OR
            (event_type IN ('REQUIRED','NOT_REQUIRED') AND approval_id IS NULL AND route_id IS NULL)),
            UNIQUE(release_id,operation_id))""",
            """CREATE TABLE IF NOT EXISTS bid_basis_snapshots(
            snapshot_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,snapshot_sequence INTEGER NOT NULL,
            predecessor_snapshot_id TEXT,label TEXT NOT NULL,note TEXT,snapshot_hash TEXT NOT NULL,
            operation_id TEXT NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL,
            provenance_json TEXT NOT NULL,FOREIGN KEY(bid_id) REFERENCES bids(bid_id),
            FOREIGN KEY(predecessor_snapshot_id) REFERENCES bid_basis_snapshots(snapshot_id),
            CHECK(snapshot_sequence>0),CHECK(length(snapshot_hash)=64),
            UNIQUE(bid_id,snapshot_sequence),UNIQUE(bid_id,operation_id))""",
            """CREATE TABLE IF NOT EXISTS bid_basis_snapshot_releases(
            snapshot_release_id TEXT PRIMARY KEY,snapshot_id TEXT NOT NULL,release_id TEXT NOT NULL,
            incorporation_sequence INTEGER NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL,
            provenance_json TEXT NOT NULL,FOREIGN KEY(snapshot_id) REFERENCES bid_basis_snapshots(snapshot_id),
            FOREIGN KEY(release_id) REFERENCES bid_received_releases(release_id),
            CHECK(incorporation_sequence>0),UNIQUE(snapshot_id,release_id),
            UNIQUE(snapshot_id,incorporation_sequence))""",
            """CREATE TABLE IF NOT EXISTS bid_basis_snapshot_documents(
            snapshot_document_id TEXT PRIMARY KEY,snapshot_id TEXT NOT NULL,document_id TEXT NOT NULL,
            document_version_id TEXT NOT NULL,source_release_id TEXT NOT NULL,
            source_received_file_id TEXT,basis_role TEXT NOT NULL,created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,provenance_json TEXT NOT NULL,
            FOREIGN KEY(snapshot_id) REFERENCES bid_basis_snapshots(snapshot_id),
            FOREIGN KEY(document_id) REFERENCES documents(id),
            FOREIGN KEY(document_version_id) REFERENCES document_versions(document_version_id),
            FOREIGN KEY(source_release_id) REFERENCES bid_received_releases(release_id),
            FOREIGN KEY(source_received_file_id) REFERENCES bid_received_files(file_id),
            CHECK(basis_role IN ('GOVERNING','QUALIFICATION','EXCLUSION')),
            UNIQUE(snapshot_id,document_id))""",
            """CREATE TABLE IF NOT EXISTS bid_intake_processing_runs(
            processing_run_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,release_id TEXT,
            run_type TEXT NOT NULL,state TEXT NOT NULL,operation_id TEXT NOT NULL,
            started_at TEXT NOT NULL,completed_at TEXT NOT NULL,actor TEXT NOT NULL,
            provenance_json TEXT NOT NULL,FOREIGN KEY(bid_id) REFERENCES bids(bid_id),
            FOREIGN KEY(release_id) REFERENCES bid_received_releases(release_id),
            CHECK(run_type IN ('INVENTORY_REGISTRATION','REPROCESS')),
            CHECK(state IN ('SUCCEEDED','FAILED')),UNIQUE(bid_id,operation_id))""",
            """CREATE TABLE IF NOT EXISTS bid_intake_processing_file_results(
            processing_file_result_id TEXT PRIMARY KEY,processing_run_id TEXT NOT NULL,file_id TEXT,
            stage TEXT NOT NULL,status TEXT NOT NULL,message TEXT,recorded_at TEXT NOT NULL,
            provenance_json TEXT NOT NULL,FOREIGN KEY(processing_run_id) REFERENCES bid_intake_processing_runs(processing_run_id),
            FOREIGN KEY(file_id) REFERENCES bid_received_files(file_id),
            CHECK(status IN ('SUCCEEDED','FAILED','SKIPPED')),
            UNIQUE(processing_run_id,file_id,stage))""",
            """CREATE TABLE IF NOT EXISTS bid_intake_work_item_links(
            intake_work_item_link_id TEXT PRIMARY KEY,work_item_id TEXT NOT NULL,notice_id TEXT,
            release_id TEXT,file_id TEXT,directive_id TEXT,acknowledgement_event_id TEXT,
            operation_id TEXT NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL,
            provenance_json TEXT NOT NULL,FOREIGN KEY(work_item_id) REFERENCES work_items(work_item_id),
            FOREIGN KEY(notice_id) REFERENCES bid_release_notices(notice_id),
            FOREIGN KEY(release_id) REFERENCES bid_received_releases(release_id),
            FOREIGN KEY(file_id) REFERENCES bid_received_files(file_id),
            FOREIGN KEY(directive_id) REFERENCES bid_addendum_directives(directive_id),
            FOREIGN KEY(acknowledgement_event_id) REFERENCES bid_release_acknowledgement_events(acknowledgement_event_id),
            CHECK((notice_id IS NOT NULL)+(release_id IS NOT NULL)+(file_id IS NOT NULL)+
            (directive_id IS NOT NULL)+(acknowledgement_event_id IS NOT NULL)=1),
            UNIQUE(operation_id))""",
            """CREATE TABLE IF NOT EXISTS bid_package_intake_schema_migrations(
            migration_id TEXT PRIMARY KEY,applied_at TEXT NOT NULL)""",
        )
        indexes = (
            "CREATE INDEX IF NOT EXISTS idx_bid_release_notices_bid_observed ON bid_release_notices(bid_id,observed_at)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_bid_notice_superseded_once ON bid_release_notices(supersedes_notice_id) WHERE supersedes_notice_id IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_bid_received_releases_bid_sequence ON bid_received_releases(bid_id,receipt_sequence)",
            "CREATE INDEX IF NOT EXISTS idx_bid_received_files_release_path ON bid_received_files(release_id,original_relative_path)",
            "CREATE INDEX IF NOT EXISTS idx_bid_received_files_digest ON bid_received_files(sha256,byte_size)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_bid_file_link_superseded_once ON bid_received_file_document_links(supersedes_link_id) WHERE supersedes_link_id IS NOT NULL",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_bid_file_disposition_superseded_once ON bid_received_file_disposition_events(supersedes_event_id) WHERE supersedes_event_id IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_bid_directives_release ON bid_addendum_directives(release_id,materiality)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_bid_directive_superseded_once ON bid_addendum_directives(supersedes_directive_id) WHERE supersedes_directive_id IS NOT NULL",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_bid_disposition_superseded_once ON bid_addendum_dispositions(supersedes_disposition_id) WHERE supersedes_disposition_id IS NOT NULL",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_bid_ack_superseded_once ON bid_release_acknowledgement_events(supersedes_event_id) WHERE supersedes_event_id IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_bid_basis_snapshots_bid_sequence ON bid_basis_snapshots(bid_id,snapshot_sequence DESC)",
            "CREATE INDEX IF NOT EXISTS idx_bid_intake_work_item ON bid_intake_work_item_links(work_item_id)",
        )
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in tables:
                conn.execute(statement)
            for statement in indexes:
                conn.execute(statement)
            for table in self._TABLES:
                conn.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_immutable BEFORE UPDATE ON {table} "
                    f"BEGIN SELECT RAISE(ABORT,'{table} rows are immutable'); END"
                )
                conn.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
                    f"BEGIN SELECT RAISE(ABORT,'{table} rows cannot be deleted'); END"
                )
            conn.execute(
                "INSERT OR IGNORE INTO bid_package_intake_schema_migrations(migration_id,applied_at) VALUES(?,?)",
                (OPS_11_MIGRATION_ID, self._now().isoformat()),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def migration_tables(self) -> tuple[str, ...]:
        return self._TABLES

    @staticmethod
    def _current_clause(id_column: str, supersedes_column: str, table: str) -> str:
        return f"NOT EXISTS(SELECT 1 FROM {table} newer WHERE newer.{supersedes_column}=base.{id_column})"

    def _bid_exists(self, conn: sqlite3.Connection, bid_id: str) -> None:
        if conn.execute("SELECT 1 FROM bids WHERE bid_id=?", (bid_id,)).fetchone() is None:
            raise IntakeNotFoundError(f"Bid not found: {bid_id}")

    def get_release_by_operation(self, bid_id: str, operation_id: str) -> dict[str, object] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM bid_received_releases WHERE bid_id=? AND operation_id=?",
                (bid_id, operation_id),
            ).fetchone()
        return _dict(row) if row is not None else None

    def managed_release_directories(self) -> tuple[str, ...]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT managed_release_key FROM bid_received_releases ORDER BY release_id"
            ).fetchall()
        return tuple(str(row["managed_release_key"]) for row in rows)

    def create_notice(
        self,
        notice_id: str,
        command: ReleaseNoticeCreate,
        *,
        actor: str,
        at: datetime,
        provenance: Provenance,
        audit_id: str,
    ) -> dict[str, object]:
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._bid_exists(conn, command.bid_id)
            existing = conn.execute(
                "SELECT * FROM bid_release_notices WHERE bid_id=? AND operation_id=?",
                (command.bid_id, command.operation_id),
            ).fetchone()
            if existing is not None:
                conn.rollback()
                return _dict(existing)
            if command.supersedes_notice_id is not None:
                prior = conn.execute(
                    """SELECT 1 FROM bid_release_notices base WHERE notice_id=? AND bid_id=?
                    AND NOT EXISTS(SELECT 1 FROM bid_release_notices newer
                    WHERE newer.supersedes_notice_id=base.notice_id)""",
                    (command.supersedes_notice_id, command.bid_id),
                ).fetchone()
                if prior is None:
                    raise StaleIntakeError(
                        "notice correction target is missing or already superseded"
                    )
            conn.execute(
                """INSERT INTO bid_release_notices VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    notice_id,
                    command.bid_id,
                    command.release_type.value,
                    command.exact_customer_reference,
                    command.customer_issue_date.isoformat()
                    if command.customer_issue_date
                    else None,
                    command.expected_receipt_date.isoformat()
                    if command.expected_receipt_date
                    else None,
                    command.channel.value,
                    command.observed_at.isoformat(),
                    command.expectation.value,
                    command.summary,
                    command.evidence_document_version_id,
                    command.evidence_reference,
                    command.supersedes_notice_id,
                    command.operation_id,
                    actor,
                    at.isoformat(),
                    provenance.model_dump_json(),
                ),
            )
            self._audit(
                conn,
                entry_id=audit_id,
                bid_id=command.bid_id,
                actor=actor,
                action="bid_release_notice_recorded",
                detail={"notice_id": notice_id, "operation_id": command.operation_id},
                at=at,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.notice(notice_id, command.bid_id)

    def notice(self, notice_id: str, bid_id: str) -> dict[str, object]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM bid_release_notices WHERE notice_id=? AND bid_id=?",
                (notice_id, bid_id),
            ).fetchone()
        if row is None:
            raise IntakeNotFoundError("release notice not found in this Bid")
        return _dict(row)

    def create_channel_check(
        self,
        check_id: str,
        command: ChannelCheckCreate,
        *,
        actor: str,
        at: datetime,
        provenance: Provenance,
        audit_id: str,
    ) -> dict[str, object]:
        with self._conn() as conn:
            self._bid_exists(conn, command.bid_id)
            existing = conn.execute(
                "SELECT * FROM bid_release_channel_checks WHERE bid_id=? AND operation_id=?",
                (command.bid_id, command.operation_id),
            ).fetchone()
            if existing is not None:
                return _dict(existing)
            conn.execute(
                "INSERT INTO bid_release_channel_checks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    check_id,
                    command.bid_id,
                    command.channel.value,
                    command.checked_at.isoformat(),
                    command.observed_customer_reference,
                    command.customer_issue_date.isoformat()
                    if command.customer_issue_date
                    else None,
                    command.result,
                    command.evidence_reference,
                    command.operation_id,
                    actor,
                    at.isoformat(),
                    provenance.model_dump_json(),
                ),
            )
            self._audit(
                conn,
                entry_id=audit_id,
                bid_id=command.bid_id,
                actor=actor,
                action="bid_release_channel_checked",
                detail={"channel_check_id": check_id, "operation_id": command.operation_id},
                at=at,
            )
        return self.channel_check(check_id, command.bid_id)

    def channel_check(self, check_id: str, bid_id: str) -> dict[str, object]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM bid_release_channel_checks WHERE channel_check_id=? AND bid_id=?",
                (check_id, bid_id),
            ).fetchone()
        if row is None:
            raise IntakeNotFoundError("channel check not found in this Bid")
        return _dict(row)

    def register_release(
        self,
        *,
        release_id: str,
        processing_run_id: str,
        result_ids: Sequence[str],
        default_disposition_ids: Sequence[str],
        command: ReleaseRegistration,
        preview: ReleasePreview,
        staged: StagedRelease,
        file_ids: dict[str, str],
        actor: str,
        at: datetime,
        provenance: Provenance,
        audit_id: str,
        publish: Callable[[StagedRelease], object],
        compensate: Callable[[StagedRelease], None],
    ) -> tuple[dict[str, object], bool]:
        """Insert, publish, audit, and commit as one compensated operation."""
        if (
            len(result_ids) != preview.file_count
            or len(default_disposition_ids) != preview.file_count
        ):
            raise ValueError("one processing result and initial disposition is required per file")
        conn = self._conn()
        published = False
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._bid_exists(conn, command.bid_id)
            existing = conn.execute(
                "SELECT * FROM bid_received_releases WHERE bid_id=? AND operation_id=?",
                (command.bid_id, command.operation_id),
            ).fetchone()
            if existing is not None:
                conn.rollback()
                return _dict(existing), False
            sequence = int(
                conn.execute(
                    "SELECT COALESCE(MAX(receipt_sequence),0)+1 FROM bid_received_releases WHERE bid_id=?",
                    (command.bid_id,),
                ).fetchone()[0]
            )
            conn.execute(
                """INSERT INTO bid_received_releases(
                release_id,bid_id,release_type,exact_customer_reference,customer_issue_date,
                received_at,received_channel,receipt_sequence,copy_mode,source_root_alias,
                source_fingerprint,file_count,total_bytes,managed_release_key,note,operation_id,
                created_by,created_at,provenance_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    release_id,
                    command.bid_id,
                    command.release_type.value,
                    command.exact_customer_reference,
                    command.customer_issue_date.isoformat()
                    if command.customer_issue_date
                    else None,
                    command.received_at.isoformat(),
                    command.received_channel.value,
                    sequence,
                    "COPY_MANAGED_ONLY",
                    command.source_root_alias,
                    command.expected_source_fingerprint,
                    preview.file_count,
                    preview.total_bytes,
                    staged.final_relative_directory,
                    command.note,
                    command.operation_id,
                    actor,
                    at.isoformat(),
                    provenance.model_dump_json(),
                ),
            )
            conn.execute(
                "INSERT INTO bid_intake_processing_runs VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    processing_run_id,
                    command.bid_id,
                    release_id,
                    "INVENTORY_REGISTRATION",
                    "SUCCEEDED",
                    command.operation_id,
                    at.isoformat(),
                    at.isoformat(),
                    actor,
                    provenance.model_dump_json(),
                ),
            )
            for index, (item, result_id, disposition_id) in enumerate(
                zip(preview.files, result_ids, default_disposition_ids, strict=True)
            ):
                file_id = file_ids[item.original_relative_path]
                storage_storage_key = f"{staged.final_relative_directory}/originals/{file_id}.bin"
                conn.execute(
                    """INSERT INTO bid_received_files VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        file_id,
                        release_id,
                        item.original_relative_path,
                        item.original_filename,
                        item.extension,
                        item.detected_media_type,
                        item.byte_size,
                        item.sha256,
                        storage_storage_key,
                        actor,
                        at.isoformat(),
                        provenance.model_dump_json(),
                    ),
                )
                conn.execute(
                    """INSERT INTO bid_received_file_disposition_events(
                    disposition_event_id,file_id,content_form,classification_method,confidence,
                    analysis_eligibility,exclusion_reason,duplicate_of_file_id,supersedes_event_id,
                    operation_id,actor,recorded_at,provenance_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        disposition_id,
                        file_id,
                        "UNKNOWN",
                        "SAFE_DEFAULT",
                        None,
                        "NOT_ASSESSED",
                        None,
                        None,
                        None,
                        f"{command.operation_id}:safe-default:{index}",
                        actor,
                        at.isoformat(),
                        provenance.model_dump_json(),
                    ),
                )
                conn.execute(
                    """INSERT INTO bid_intake_processing_file_results VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        result_id,
                        processing_run_id,
                        file_id,
                        "MANAGED_COPY_VERIFICATION",
                        "SUCCEEDED",
                        "Source and managed-copy size and SHA-256 matched independently.",
                        at.isoformat(),
                        provenance.model_dump_json(),
                    ),
                )
            publish(staged)
            published = True
            self._audit(
                conn,
                entry_id=audit_id,
                bid_id=command.bid_id,
                actor=actor,
                action="bid_received_release_registered",
                detail={
                    "release_id": release_id,
                    "operation_id": command.operation_id,
                    "file_count": preview.file_count,
                    "total_bytes": preview.total_bytes,
                    "receipt_sequence": sequence,
                    "source_folder": command.source_folder,
                },
                at=at,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            if published:
                compensate(staged)
            raise
        finally:
            conn.close()
        return self.release(release_id, command.bid_id), True

    def release(self, release_id: str, bid_id: str) -> dict[str, object]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM bid_received_releases WHERE release_id=? AND bid_id=?",
                (release_id, bid_id),
            ).fetchone()
        if row is None:
            raise IntakeNotFoundError("received release not found in this Bid")
        return _dict(row)

    def file(self, file_id: str, bid_id: str) -> dict[str, object]:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT f.*,r.bid_id FROM bid_received_files f JOIN bid_received_releases r
                ON r.release_id=f.release_id WHERE f.file_id=? AND r.bid_id=?""",
                (file_id, bid_id),
            ).fetchone()
        if row is None:
            raise IntakeNotFoundError("received file not found in this Bid")
        return _dict(row)

    def link_notice(
        self,
        *,
        link_id: str,
        bid_id: str,
        notice_id: str,
        release_id: str,
        relationship: str,
        operation_id: str,
        actor: str,
        at: datetime,
        provenance: Provenance,
        audit_id: str,
    ) -> dict[str, object]:
        with self._conn() as conn:
            self.notice(notice_id, bid_id)
            self.release(release_id, bid_id)
            existing = conn.execute(
                "SELECT * FROM bid_release_notice_links WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if existing is not None:
                return _dict(existing)
            conn.execute(
                "INSERT INTO bid_release_notice_links VALUES(?,?,?,?,?,?,?,?)",
                (
                    link_id,
                    notice_id,
                    release_id,
                    relationship,
                    operation_id,
                    actor,
                    at.isoformat(),
                    provenance.model_dump_json(),
                ),
            )
            self._audit(
                conn,
                entry_id=audit_id,
                bid_id=bid_id,
                actor=actor,
                action="bid_release_notice_linked",
                detail={"notice_id": notice_id, "release_id": release_id},
                at=at,
            )
        return self.notice_link(link_id)

    def notice_link(self, link_id: str) -> dict[str, object]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM bid_release_notice_links WHERE notice_link_id=?", (link_id,)
            ).fetchone()
        if row is None:
            raise IntakeNotFoundError("release notice link not found")
        return _dict(row)

    @staticmethod
    def _verify_current_lineage(
        conn: sqlite3.Connection,
        *,
        table: str,
        id_column: str,
        parent_column: str,
        parent_id: str,
        supersedes_column: str,
        supersedes_id: str | None,
    ) -> None:
        if supersedes_id is None:
            existing = conn.execute(
                f"SELECT 1 FROM {table} WHERE {parent_column}=?", (parent_id,)
            ).fetchone()
            if existing is not None:
                raise StaleIntakeError("an existing event must be explicitly superseded")
            return
        prior = conn.execute(
            f"""SELECT 1 FROM {table} base WHERE base.{id_column}=?
            AND base.{parent_column}=? AND NOT EXISTS(SELECT 1 FROM {table} newer
            WHERE newer.{supersedes_column}=base.{id_column})""",
            (supersedes_id, parent_id),
        ).fetchone()
        if prior is None:
            raise StaleIntakeError(
                "correction target is missing, mismatched, or already superseded"
            )

    def add_file_disposition(
        self,
        *,
        event_id: str,
        bid_id: str,
        file_id: str,
        command: FileDispositionCreate,
        actor: str,
        at: datetime,
        provenance: Provenance,
        audit_id: str,
    ) -> dict[str, object]:
        with self._conn() as conn:
            self.file(file_id, bid_id)
            existing = conn.execute(
                "SELECT * FROM bid_received_file_disposition_events WHERE file_id=? AND operation_id=?",
                (file_id, command.operation_id),
            ).fetchone()
            if existing is not None:
                return _dict(existing)
            self._verify_current_lineage(
                conn,
                table="bid_received_file_disposition_events",
                id_column="disposition_event_id",
                parent_column="file_id",
                parent_id=file_id,
                supersedes_column="supersedes_event_id",
                supersedes_id=command.supersedes_event_id,
            )
            if command.duplicate_of_file_id is not None:
                duplicate = self.file(command.duplicate_of_file_id, bid_id)
                current = self.file(file_id, bid_id)
                if (
                    duplicate["sha256"] != current["sha256"]
                    or duplicate["byte_size"] != current["byte_size"]
                ):
                    raise IntakeConflictError("duplicate-of evidence must have identical bytes")
            conn.execute(
                "INSERT INTO bid_received_file_disposition_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    file_id,
                    command.content_form.value,
                    command.classification_method.value,
                    command.confidence,
                    command.analysis_eligibility.value,
                    command.exclusion_reason,
                    command.duplicate_of_file_id,
                    command.supersedes_event_id,
                    command.operation_id,
                    actor,
                    at.isoformat(),
                    provenance.model_dump_json(),
                ),
            )
            self._audit(
                conn,
                entry_id=audit_id,
                bid_id=bid_id,
                actor=actor,
                action="bid_received_file_disposition_recorded",
                detail={"file_id": file_id, "disposition_event_id": event_id},
                at=at,
            )
        return self.current_file_disposition(file_id)

    def add_file_dispositions_bulk(
        self,
        *,
        bid_id: str,
        command: BulkFileDispositionCreate,
        event_ids: Sequence[str],
        audit_ids: Sequence[str],
        actor: str,
        at: datetime,
        provenance: Provenance,
    ) -> tuple[dict[str, object], ...]:
        """Append selected file decisions and their audits in one SQLite transaction.

        All selected rows, including rows that already have the requested decision, are checked
        before the first event is inserted.  No-op selections deliberately create no replacement
        event or audit record.
        """
        if len(event_ids) != len(command.items) or len(audit_ids) != len(command.items):
            raise ValueError("bulk review identifiers do not match the selected files")
        if len({item.file_id for item in command.items}) != len(command.items):
            raise IntakeConflictError("a received file can only be selected once")
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._bid_exists(conn, bid_id)
            release = conn.execute(
                "SELECT 1 FROM bid_received_releases WHERE release_id=? AND bid_id=?",
                (command.release_id, bid_id),
            ).fetchone()
            if release is None:
                raise IntakeNotFoundError("received release not found in this Bid")

            pending: list[tuple[str, sqlite3.Row, str, str]] = []
            for position, item in enumerate(command.items):
                file_row = conn.execute(
                    """SELECT f.* FROM bid_received_files f JOIN bid_received_releases r
                    ON r.release_id=f.release_id WHERE f.file_id=? AND r.bid_id=?""",
                    (item.file_id, bid_id),
                ).fetchone()
                if file_row is None:
                    raise IntakeNotFoundError("selected received file not found in this Bid")
                if str(file_row["release_id"]) != command.release_id:
                    raise IntakeConflictError(
                        "every selected file must belong to the displayed received package"
                    )
                current = conn.execute(
                    """SELECT base.* FROM bid_received_file_disposition_events base
                    WHERE base.file_id=? AND NOT EXISTS(SELECT 1 FROM
                    bid_received_file_disposition_events newer
                    WHERE newer.supersedes_event_id=base.disposition_event_id)""",
                    (item.file_id,),
                ).fetchone()
                if (
                    current is None
                    or str(current["disposition_event_id"]) != item.supersedes_event_id
                ):
                    raise StaleIntakeError(
                        "one or more selected files changed; refresh the package before reviewing"
                    )
                target_form = (
                    command.content_form.value
                    if command.content_form
                    else str(current["content_form"])
                )
                target_eligibility = command.analysis_eligibility.value
                target_reason = (
                    command.exclusion_reason
                    if command.analysis_eligibility is AnalysisEligibility.EXCLUDED
                    else None
                )
                changed = (
                    target_form != str(current["content_form"])
                    or target_eligibility != str(current["analysis_eligibility"])
                    or target_reason != current["exclusion_reason"]
                )
                if changed:
                    pending.append(
                        (item.file_id, current, event_ids[position], audit_ids[position])
                    )

            created: list[dict[str, object]] = []
            for file_id, current, event_id, audit_id in pending:
                content_form = (
                    command.content_form.value
                    if command.content_form
                    else str(current["content_form"])
                )
                exclusion_reason = (
                    command.exclusion_reason
                    if command.analysis_eligibility is AnalysisEligibility.EXCLUDED
                    else None
                )
                conn.execute(
                    "INSERT INTO bid_received_file_disposition_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        event_id,
                        file_id,
                        content_form,
                        "HUMAN_REVIEW",
                        None,
                        command.analysis_eligibility.value,
                        exclusion_reason,
                        current["duplicate_of_file_id"],
                        current["disposition_event_id"],
                        command.operation_id,
                        actor,
                        at.isoformat(),
                        provenance.model_dump_json(),
                    ),
                )
                self._audit(
                    conn,
                    entry_id=audit_id,
                    bid_id=bid_id,
                    actor=actor,
                    action="bid_received_file_disposition_recorded",
                    detail={"file_id": file_id, "disposition_event_id": event_id},
                    at=at,
                )
                created.append(
                    {
                        "file_id": file_id,
                        "disposition_event_id": event_id,
                        "analysis_eligibility": command.analysis_eligibility.value,
                    }
                )
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()
        return tuple(created)

    def current_file_disposition(self, file_id: str) -> dict[str, object]:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT base.* FROM bid_received_file_disposition_events base
                WHERE base.file_id=? AND NOT EXISTS(SELECT 1 FROM bid_received_file_disposition_events
                newer WHERE newer.supersedes_event_id=base.disposition_event_id)""",
                (file_id,),
            ).fetchone()
        if row is None:
            raise IntakeNotFoundError("current file disposition not found")
        return _dict(row)

    def link_file_document(
        self,
        *,
        link_id: str,
        bid_id: str,
        file_id: str,
        command: FileDocumentLinkCreate,
        actor: str,
        at: datetime,
        provenance: Provenance,
        audit_id: str,
    ) -> dict[str, object]:
        with self._conn() as conn:
            self.file(file_id, bid_id)
            version = conn.execute(
                """SELECT v.*,d.bid_id,d.control_managed FROM document_versions v
                JOIN documents d ON d.id=v.document_id WHERE v.document_version_id=?""",
                (command.document_version_id,),
            ).fetchone()
            if (
                version is None
                or version["bid_id"] != bid_id
                or not int(version["control_managed"])
            ):
                raise IntakeConflictError("controlled version must belong to this Bid")
            existing = conn.execute(
                "SELECT * FROM bid_received_file_document_links WHERE file_id=? AND operation_id=?",
                (file_id, command.operation_id),
            ).fetchone()
            if existing is not None:
                return _dict(existing)
            self._verify_current_lineage(
                conn,
                table="bid_received_file_document_links",
                id_column="file_document_link_id",
                parent_column="file_id",
                parent_id=file_id,
                supersedes_column="supersedes_link_id",
                supersedes_id=command.supersedes_link_id,
            )
            if command.relationship.value == "EXACT_BYTES":
                received = self.file(file_id, bid_id)
                if (
                    received["sha256"] != version["sha256_digest"]
                    or received["byte_size"] != version["byte_size"]
                ):
                    raise IntakeConflictError(
                        "exact-bytes link does not match controlled-version evidence"
                    )
            conn.execute(
                "INSERT INTO bid_received_file_document_links VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    link_id,
                    file_id,
                    command.document_version_id,
                    command.relationship.value,
                    command.supersedes_link_id,
                    command.operation_id,
                    actor,
                    at.isoformat(),
                    provenance.model_dump_json(),
                ),
            )
            self._audit(
                conn,
                entry_id=audit_id,
                bid_id=bid_id,
                actor=actor,
                action="bid_received_file_controlled_version_linked",
                detail={"file_id": file_id, "document_version_id": command.document_version_id},
                at=at,
            )
        return self.current_file_document_link(file_id)

    def current_file_document_link(self, file_id: str) -> dict[str, object]:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT base.* FROM bid_received_file_document_links base WHERE base.file_id=?
                AND NOT EXISTS(SELECT 1 FROM bid_received_file_document_links newer
                WHERE newer.supersedes_link_id=base.file_document_link_id)""",
                (file_id,),
            ).fetchone()
        if row is None:
            raise IntakeNotFoundError("current file-to-document link not found")
        return _dict(row)

    def add_directive(
        self,
        *,
        directive_id: str,
        bid_id: str,
        release_id: str,
        command: DirectiveCreate,
        actor: str,
        at: datetime,
        provenance: Provenance,
        audit_id: str,
    ) -> dict[str, object]:
        with self._conn() as conn:
            self.release(release_id, bid_id)
            existing = conn.execute(
                "SELECT * FROM bid_addendum_directives WHERE release_id=? AND operation_id=?",
                (release_id, command.operation_id),
            ).fetchone()
            if existing is not None:
                return _dict(existing)
            if command.supersedes_directive_id is not None:
                prior = conn.execute(
                    """SELECT 1 FROM bid_addendum_directives base WHERE directive_id=?
                    AND release_id=? AND NOT EXISTS(SELECT 1 FROM bid_addendum_directives newer
                    WHERE newer.supersedes_directive_id=base.directive_id)""",
                    (command.supersedes_directive_id, release_id),
                ).fetchone()
                if prior is None:
                    raise StaleIntakeError("directive correction target is missing or superseded")
            if command.source_file_id is not None:
                source = self.file(command.source_file_id, bid_id)
                if source["release_id"] != release_id:
                    raise IntakeConflictError("directive source file must belong to its release")
            if command.target_document_version_id is not None:
                self._validate_document_version(conn, bid_id, command.target_document_version_id)
            conn.execute(
                "INSERT INTO bid_addendum_directives VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    directive_id,
                    release_id,
                    command.directive_type,
                    command.description,
                    command.materiality.value,
                    command.source_file_id,
                    command.source_locator,
                    command.target_document_version_id,
                    command.supersedes_directive_id,
                    command.operation_id,
                    actor,
                    at.isoformat(),
                    provenance.model_dump_json(),
                ),
            )
            self._audit(
                conn,
                entry_id=audit_id,
                bid_id=bid_id,
                actor=actor,
                action="bid_addendum_directive_recorded",
                detail={"directive_id": directive_id, "release_id": release_id},
                at=at,
            )
        return self.directive(directive_id, bid_id)

    def directive(self, directive_id: str, bid_id: str) -> dict[str, object]:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT d.*,r.bid_id FROM bid_addendum_directives d
                JOIN bid_received_releases r ON r.release_id=d.release_id
                WHERE d.directive_id=? AND r.bid_id=?""",
                (directive_id, bid_id),
            ).fetchone()
        if row is None:
            raise IntakeNotFoundError("addendum directive not found in this Bid")
        return _dict(row)

    @staticmethod
    def _validate_document_version(
        conn: sqlite3.Connection, bid_id: str, document_version_id: str
    ) -> sqlite3.Row:
        row = conn.execute(
            """SELECT v.*,d.bid_id,d.control_managed,d.control_lifecycle,d.current_version_id,
            d.control_title,d.document_number FROM document_versions v JOIN documents d
            ON d.id=v.document_id WHERE v.document_version_id=?""",
            (document_version_id,),
        ).fetchone()
        if row is None or row["bid_id"] != bid_id or not int(row["control_managed"]):
            raise IntakeConflictError("exact controlled version must belong to this Bid")
        return cast(sqlite3.Row, row)

    @staticmethod
    def _approval_is_authoritative(
        conn: sqlite3.Connection,
        bid_id: str,
        approval_id: str | None,
        route_id: str | None,
    ) -> bool:
        if approval_id is not None:
            row = conn.execute(
                "SELECT obtained FROM approvals WHERE approval_id=? AND bid_id=?",
                (approval_id, bid_id),
            ).fetchone()
            return row is not None and bool(row["obtained"])
        if route_id is not None:
            row = conn.execute(
                "SELECT state FROM approval_routes WHERE route_id=? AND bid_id=?",
                (route_id, bid_id),
            ).fetchone()
            return row is not None and row["state"] == "APPROVED"
        return False

    def add_directive_disposition(
        self,
        *,
        disposition_id: str,
        bid_id: str,
        directive_id: str,
        command: DirectiveDispositionCreate,
        actor: str,
        at: datetime,
        provenance: Provenance,
        audit_id: str,
    ) -> dict[str, object]:
        with self._conn() as conn:
            self.directive(directive_id, bid_id)
            existing = conn.execute(
                "SELECT * FROM bid_addendum_dispositions WHERE directive_id=? AND operation_id=?",
                (directive_id, command.operation_id),
            ).fetchone()
            if existing is not None:
                return _dict(existing)
            self._verify_current_lineage(
                conn,
                table="bid_addendum_dispositions",
                id_column="disposition_id",
                parent_column="directive_id",
                parent_id=directive_id,
                supersedes_column="supersedes_disposition_id",
                supersedes_id=command.supersedes_disposition_id,
            )
            if command.resulting_document_version_id is not None:
                self._validate_document_version(conn, bid_id, command.resulting_document_version_id)
            if command.status.value == "INTERNAL_WAIVER" and not self._approval_is_authoritative(
                conn, bid_id, command.approval_id, command.route_id
            ):
                raise IntakeConflictError(
                    "internal waiver requires an obtained authoritative approval"
                )
            conn.execute(
                "INSERT INTO bid_addendum_dispositions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    disposition_id,
                    directive_id,
                    command.status.value,
                    command.rationale,
                    command.resulting_document_version_id,
                    command.approval_id,
                    command.route_id,
                    command.supersedes_disposition_id,
                    command.operation_id,
                    actor,
                    at.isoformat(),
                    provenance.model_dump_json(),
                ),
            )
            self._audit(
                conn,
                entry_id=audit_id,
                bid_id=bid_id,
                actor=actor,
                action="bid_addendum_directive_disposition_recorded",
                detail={
                    "directive_id": directive_id,
                    "disposition_id": disposition_id,
                    "status": command.status.value,
                },
                at=at,
            )
        return self.current_directive_disposition(directive_id)

    def current_directive_disposition(self, directive_id: str) -> dict[str, object]:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT base.* FROM bid_addendum_dispositions base
                WHERE base.directive_id=? AND NOT EXISTS(SELECT 1 FROM bid_addendum_dispositions newer
                WHERE newer.supersedes_disposition_id=base.disposition_id)""",
                (directive_id,),
            ).fetchone()
        if row is None:
            raise IntakeNotFoundError("current directive disposition not found")
        return _dict(row)

    def add_acknowledgement(
        self,
        *,
        event_id: str,
        bid_id: str,
        release_id: str,
        command: AcknowledgementCreate,
        actor: str,
        at: datetime,
        provenance: Provenance,
        audit_id: str,
    ) -> dict[str, object]:
        with self._conn() as conn:
            self.release(release_id, bid_id)
            existing = conn.execute(
                """SELECT * FROM bid_release_acknowledgement_events
                WHERE release_id=? AND operation_id=?""",
                (release_id, command.operation_id),
            ).fetchone()
            if existing is not None:
                return _dict(existing)
            self._verify_current_lineage(
                conn,
                table="bid_release_acknowledgement_events",
                id_column="acknowledgement_event_id",
                parent_column="release_id",
                parent_id=release_id,
                supersedes_column="supersedes_event_id",
                supersedes_id=command.supersedes_event_id,
            )
            if (
                command.event_type.value == "EXCEPTION_APPROVED"
                and not self._approval_is_authoritative(
                    conn, bid_id, command.approval_id, command.route_id
                )
            ):
                raise IntakeConflictError(
                    "acknowledgement exception requires an obtained authoritative approval"
                )
            conn.execute(
                """INSERT INTO bid_release_acknowledgement_events VALUES(
                ?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    release_id,
                    command.event_type.value,
                    command.due_at.isoformat() if command.due_at else None,
                    command.acknowledgement_reference,
                    command.approval_id,
                    command.route_id,
                    command.note,
                    command.supersedes_event_id,
                    command.operation_id,
                    actor,
                    at.isoformat(),
                    provenance.model_dump_json(),
                ),
            )
            self._audit(
                conn,
                entry_id=audit_id,
                bid_id=bid_id,
                actor=actor,
                action="bid_release_acknowledgement_recorded",
                detail={"release_id": release_id, "event_type": command.event_type.value},
                at=at,
            )
        return self.current_acknowledgement(release_id)

    def current_acknowledgement(self, release_id: str) -> dict[str, object]:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT base.* FROM bid_release_acknowledgement_events base
                WHERE base.release_id=? AND NOT EXISTS(SELECT 1
                FROM bid_release_acknowledgement_events newer
                WHERE newer.supersedes_event_id=base.acknowledgement_event_id)""",
                (release_id,),
            ).fetchone()
        if row is None:
            raise IntakeNotFoundError("current acknowledgement state not found")
        return _dict(row)

    def _release_incorporation_issues(
        self, conn: sqlite3.Connection, bid_id: str, release_id: str
    ) -> list[str]:
        issues: list[str] = []
        release = conn.execute(
            "SELECT * FROM bid_received_releases WHERE release_id=? AND bid_id=?",
            (release_id, bid_id),
        ).fetchone()
        if release is None:
            return ["release does not belong to this Bid"]
        succeeded = conn.execute(
            """SELECT 1 FROM bid_intake_processing_runs WHERE release_id=?
            AND run_type='INVENTORY_REGISTRATION' AND state='SUCCEEDED'""",
            (release_id,),
        ).fetchone()
        if succeeded is None:
            issues.append("managed storage and inventory verification has not succeeded")
        file_rows = conn.execute(
            """SELECT f.file_id,disp.analysis_eligibility,link.document_version_id
            FROM bid_received_files f
            LEFT JOIN bid_received_file_disposition_events disp ON disp.file_id=f.file_id
            AND NOT EXISTS(SELECT 1 FROM bid_received_file_disposition_events newer
            WHERE newer.supersedes_event_id=disp.disposition_event_id)
            LEFT JOIN bid_received_file_document_links link ON link.file_id=f.file_id
            AND NOT EXISTS(SELECT 1 FROM bid_received_file_document_links newer
            WHERE newer.supersedes_link_id=link.file_document_link_id)
            WHERE f.release_id=?""",
            (release_id,),
        ).fetchall()
        for row in file_rows:
            eligibility = str(row["analysis_eligibility"])
            if eligibility == AnalysisEligibility.NOT_ASSESSED.value:
                issues.append(f"file {row['file_id']} has not been reviewed")
            elif (
                eligibility == AnalysisEligibility.ELIGIBLE.value
                and row["document_version_id"] is None
            ):
                issues.append(
                    f"eligible file {row['file_id']} lacks an exact controlled-version link"
                )
            elif row["document_version_id"] is not None:
                try:
                    self._validate_document_version(conn, bid_id, str(row["document_version_id"]))
                except IntakeConflictError:
                    issues.append(f"file {row['file_id']} has an invalid controlled-version link")
        directives = conn.execute(
            """SELECT d.directive_id,d.materiality,disp.status FROM bid_addendum_directives d
            LEFT JOIN bid_addendum_dispositions disp ON disp.directive_id=d.directive_id
            AND NOT EXISTS(SELECT 1 FROM bid_addendum_dispositions newer
            WHERE newer.supersedes_disposition_id=disp.disposition_id)
            WHERE d.release_id=? AND NOT EXISTS(SELECT 1 FROM bid_addendum_directives newer
            WHERE newer.supersedes_directive_id=d.directive_id)""",
            (release_id,),
        ).fetchall()
        for row in directives:
            status = str(row["status"] or "UNRESOLVED")
            if status not in {"INCORPORATED", "NOT_APPLICABLE"}:
                issues.append(
                    f"directive {row['directive_id']} remains {status.lower().replace('_', ' ')}"
                )
        return issues

    def incorporation_issues(self, bid_id: str, release_id: str) -> tuple[str, ...]:
        with self._conn() as conn:
            return tuple(self._release_incorporation_issues(conn, bid_id, release_id))

    def create_snapshot(
        self,
        *,
        snapshot_id: str,
        snapshot_release_ids: Sequence[str],
        snapshot_document_ids: Sequence[str],
        bid_id: str,
        command: SnapshotCreate,
        actor: str,
        at: datetime,
        provenance: Provenance,
        audit_id: str,
    ) -> dict[str, object]:
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM bid_basis_snapshots WHERE bid_id=? AND operation_id=?",
                (bid_id, command.operation_id),
            ).fetchone()
            if existing is not None:
                conn.rollback()
                return _dict(existing)
            current = conn.execute(
                """SELECT * FROM bid_basis_snapshots WHERE bid_id=?
                ORDER BY snapshot_sequence DESC LIMIT 1""",
                (bid_id,),
            ).fetchone()
            current_id = str(current["snapshot_id"]) if current is not None else None
            if current_id != command.expected_current_snapshot_id:
                raise StaleIntakeError("current Bid Basis changed; reload before publishing")
            if tuple(command.release_ids) != tuple(dict.fromkeys(command.release_ids)):
                raise ValueError("release selection must be unique")
            for release_id in command.release_ids:
                issues = self._release_incorporation_issues(conn, bid_id, release_id)
                if issues:
                    raise ReleaseNotIncorporableError("; ".join(issues))
            prior_release_ids: set[str] = set()
            if current_id is not None:
                prior_release_ids = {
                    str(row["release_id"])
                    for row in conn.execute(
                        "SELECT release_id FROM bid_basis_snapshot_releases WHERE snapshot_id=?",
                        (current_id,),
                    ).fetchall()
                }
                if not prior_release_ids.issubset(set(command.release_ids)):
                    raise IntakeConflictError("a new Bid Basis cannot silently drop prior releases")
            documents: dict[str, tuple[str, str, str | None]] = {}
            if current_id is not None:
                for row in conn.execute(
                    """SELECT document_id,document_version_id,source_release_id,
                    source_received_file_id FROM bid_basis_snapshot_documents WHERE snapshot_id=?""",
                    (current_id,),
                ).fetchall():
                    documents[str(row["document_id"])] = (
                        str(row["document_version_id"]),
                        str(row["source_release_id"]),
                        str(row["source_received_file_id"])
                        if row["source_received_file_id"] is not None
                        else None,
                    )
            for release_id in command.release_ids:
                for row in conn.execute(
                    """SELECT v.document_id,link.document_version_id,f.file_id
                    FROM bid_received_files f JOIN bid_received_file_document_links link
                    ON link.file_id=f.file_id AND NOT EXISTS(
                    SELECT 1 FROM bid_received_file_document_links newer
                    WHERE newer.supersedes_link_id=link.file_document_link_id)
                    JOIN document_versions v ON v.document_version_id=link.document_version_id
                    JOIN bid_received_file_disposition_events disp ON disp.file_id=f.file_id
                    AND NOT EXISTS(SELECT 1 FROM bid_received_file_disposition_events newer
                    WHERE newer.supersedes_event_id=disp.disposition_event_id)
                    WHERE f.release_id=? AND disp.analysis_eligibility='ELIGIBLE'""",
                    (release_id,),
                ).fetchall():
                    documents[str(row["document_id"])] = (
                        str(row["document_version_id"]),
                        release_id,
                        str(row["file_id"]),
                    )
            if not documents:
                raise ReleaseNotIncorporableError(
                    "Bid Basis requires at least one exact controlled document version"
                )
            sequence = int(current["snapshot_sequence"]) + 1 if current is not None else 1
            canonical = json.dumps(
                {
                    "bid_id": bid_id,
                    "sequence": sequence,
                    "predecessor": current_id,
                    "releases": list(command.release_ids),
                    "documents": sorted(
                        (doc_id, values[0], values[1]) for doc_id, values in documents.items()
                    ),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            snapshot_hash = hashlib.sha256(canonical).hexdigest()
            conn.execute(
                "INSERT INTO bid_basis_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    snapshot_id,
                    bid_id,
                    sequence,
                    current_id,
                    command.label,
                    command.note,
                    snapshot_hash,
                    command.operation_id,
                    actor,
                    at.isoformat(),
                    provenance.model_dump_json(),
                ),
            )
            for index, (release_id, row_id) in enumerate(
                zip(command.release_ids, snapshot_release_ids, strict=True), 1
            ):
                conn.execute(
                    "INSERT INTO bid_basis_snapshot_releases VALUES(?,?,?,?,?,?,?)",
                    (
                        row_id,
                        snapshot_id,
                        release_id,
                        index,
                        actor,
                        at.isoformat(),
                        provenance.model_dump_json(),
                    ),
                )
            if len(snapshot_document_ids) != len(documents):
                raise ValueError("one snapshot-document ID is required per document")
            for row_id, (document_id, values) in zip(
                snapshot_document_ids, sorted(documents.items()), strict=True
            ):
                version_id, source_release_id, source_file_id = values
                exact = self._validate_document_version(conn, bid_id, version_id)
                if str(exact["document_id"]) != document_id:
                    raise IntakeConflictError("snapshot document/version identity mismatch")
                conn.execute(
                    "INSERT INTO bid_basis_snapshot_documents VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        row_id,
                        snapshot_id,
                        document_id,
                        version_id,
                        source_release_id,
                        source_file_id,
                        "GOVERNING",
                        actor,
                        at.isoformat(),
                        provenance.model_dump_json(),
                    ),
                )
            self._audit(
                conn,
                entry_id=audit_id,
                bid_id=bid_id,
                actor=actor,
                action="bid_basis_snapshot_published",
                detail={
                    "snapshot_id": snapshot_id,
                    "snapshot_sequence": sequence,
                    "snapshot_hash": snapshot_hash,
                    "release_ids": list(command.release_ids),
                },
                at=at,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.current_snapshot(bid_id) or {}

    def current_snapshot(self, bid_id: str) -> dict[str, object] | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM bid_basis_snapshots WHERE bid_id=?
                ORDER BY snapshot_sequence DESC LIMIT 1""",
                (bid_id,),
            ).fetchone()
        return _dict(row) if row is not None else None

    def snapshot_release_ids(self, snapshot_id: str) -> tuple[str, ...]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT release_id FROM bid_basis_snapshot_releases WHERE snapshot_id=?
                ORDER BY incorporation_sequence""",
                (snapshot_id,),
            ).fetchall()
        return tuple(str(row["release_id"]) for row in rows)

    def candidate_snapshot_document_count(self, bid_id: str, release_ids: Sequence[str]) -> int:
        """Return the exact logical-document cardinality used by snapshot publication."""
        document_ids: set[str] = set()
        current = self.current_snapshot(bid_id)
        with self._conn() as conn:
            if current is not None:
                document_ids.update(
                    str(row["document_id"])
                    for row in conn.execute(
                        "SELECT document_id FROM bid_basis_snapshot_documents WHERE snapshot_id=?",
                        (current["snapshot_id"],),
                    ).fetchall()
                )
            for release_id in release_ids:
                document_ids.update(
                    str(row["document_id"])
                    for row in conn.execute(
                        """SELECT DISTINCT v.document_id FROM bid_received_files f
                        JOIN bid_received_file_document_links link ON link.file_id=f.file_id
                        AND NOT EXISTS(SELECT 1 FROM bid_received_file_document_links newer
                        WHERE newer.supersedes_link_id=link.file_document_link_id)
                        JOIN document_versions v ON v.document_version_id=link.document_version_id
                        JOIN bid_received_file_disposition_events disp ON disp.file_id=f.file_id
                        AND NOT EXISTS(SELECT 1 FROM bid_received_file_disposition_events newer
                        WHERE newer.supersedes_event_id=disp.disposition_event_id)
                        WHERE f.release_id=? AND disp.analysis_eligibility='ELIGIBLE'""",
                        (release_id,),
                    ).fetchall()
                )
        return len(document_ids)

    def release_detail(self, bid_id: str, release_id: str) -> dict[str, object]:
        release = self.release(release_id, bid_id)
        with self._conn() as conn:
            files = [
                _dict(row)
                for row in conn.execute(
                    """SELECT f.*,disp.disposition_event_id,disp.content_form,
                    disp.classification_method,disp.confidence,disp.analysis_eligibility,
                    disp.exclusion_reason,disp.duplicate_of_file_id,
                    link.file_document_link_id,link.document_version_id,
                    d.id AS document_id,d.control_title,d.document_number,v.version_label
                    FROM bid_received_files f
                    LEFT JOIN bid_received_file_disposition_events disp ON disp.file_id=f.file_id
                    AND NOT EXISTS(SELECT 1 FROM bid_received_file_disposition_events newer
                    WHERE newer.supersedes_event_id=disp.disposition_event_id)
                    LEFT JOIN bid_received_file_document_links link ON link.file_id=f.file_id
                    AND NOT EXISTS(SELECT 1 FROM bid_received_file_document_links newer
                    WHERE newer.supersedes_link_id=link.file_document_link_id)
                    LEFT JOIN document_versions v ON v.document_version_id=link.document_version_id
                    LEFT JOIN documents d ON d.id=v.document_id WHERE f.release_id=?
                    ORDER BY lower(f.original_relative_path),f.file_id""",
                    (release_id,),
                ).fetchall()
            ]
            directives = [
                _dict(row)
                for row in conn.execute(
                    """SELECT d.*,disp.disposition_id,disp.status AS disposition_status,
                    disp.rationale,disp.resulting_document_version_id,disp.approval_id,
                    disp.route_id FROM bid_addendum_directives d
                    LEFT JOIN bid_addendum_dispositions disp ON disp.directive_id=d.directive_id
                    AND NOT EXISTS(SELECT 1 FROM bid_addendum_dispositions newer
                    WHERE newer.supersedes_disposition_id=disp.disposition_id)
                    WHERE d.release_id=? AND NOT EXISTS(SELECT 1 FROM bid_addendum_directives newer
                    WHERE newer.supersedes_directive_id=d.directive_id)
                    ORDER BY d.created_at,d.directive_id""",
                    (release_id,),
                ).fetchall()
            ]
            ack = conn.execute(
                """SELECT base.* FROM bid_release_acknowledgement_events base
                WHERE base.release_id=? AND NOT EXISTS(SELECT 1 FROM
                bid_release_acknowledgement_events newer
                WHERE newer.supersedes_event_id=base.acknowledgement_event_id)""",
                (release_id,),
            ).fetchone()
            linked_notices = [
                _dict(row)
                for row in conn.execute(
                    """SELECT n.*,l.relationship FROM bid_release_notice_links l
                    JOIN bid_release_notices n ON n.notice_id=l.notice_id
                    WHERE l.release_id=? ORDER BY n.observed_at""",
                    (release_id,),
                ).fetchall()
            ]
        release["files"] = files
        release["directives"] = directives
        release["acknowledgement"] = _dict(ack) if ack is not None else None
        release["linked_notices"] = linked_notices
        release["incorporation_issues"] = list(self.incorporation_issues(bid_id, release_id))
        return release

    def _attention(self, bid_id: str) -> list[IntakeAttention]:
        destination = f"/bids/{bid_id}/package-intake-addenda"
        attention: list[IntakeAttention] = []
        with self._conn() as conn:
            notices = conn.execute(
                """SELECT base.* FROM bid_release_notices base WHERE base.bid_id=?
                AND base.expectation='EXPECTED'
                AND NOT EXISTS(SELECT 1 FROM bid_release_notices newer
                WHERE newer.supersedes_notice_id=base.notice_id)
                AND NOT EXISTS(SELECT 1 FROM bid_release_notice_links l
                WHERE l.notice_id=base.notice_id AND l.relationship='FULFILS')""",
                (bid_id,),
            ).fetchall()
            for row in notices:
                reference = str(row["exact_customer_reference"] or row["release_type"])
                attention.append(
                    IntakeAttention(
                        code="EXPECTED_RELEASE_MISSING",
                        message=f"Expected customer release {reference} has not been received or resolved.",
                        destination=destination,
                        record_id=str(row["notice_id"]),
                        blocking=True,
                    )
                )
            snapshot = conn.execute(
                """SELECT snapshot_id FROM bid_basis_snapshots WHERE bid_id=?
                ORDER BY snapshot_sequence DESC LIMIT 1""",
                (bid_id,),
            ).fetchone()
            snapshot_id = str(snapshot["snapshot_id"]) if snapshot is not None else None
            releases = conn.execute(
                "SELECT * FROM bid_received_releases WHERE bid_id=? ORDER BY receipt_sequence",
                (bid_id,),
            ).fetchall()
            incorporated: set[str] = set()
            if snapshot_id is not None:
                incorporated = {
                    str(row["release_id"])
                    for row in conn.execute(
                        "SELECT release_id FROM bid_basis_snapshot_releases WHERE snapshot_id=?",
                        (snapshot_id,),
                    ).fetchall()
                }
            prior_issue_date: str | None = None
            for row in releases:
                release_id = str(row["release_id"])
                reference = str(
                    row["exact_customer_reference"] or f"receipt {row['receipt_sequence']}"
                )
                if row["exact_customer_reference"] is None:
                    attention.append(
                        IntakeAttention(
                            code="RELEASE_REFERENCE_MISSING",
                            message=(
                                f"Receipt {row['receipt_sequence']} has no exact customer release "
                                "reference; human confirmation is required."
                            ),
                            destination=f"{destination}/releases/{release_id}",
                            record_id=release_id,
                            blocking=False,
                        )
                    )
                if release_id not in incorporated:
                    attention.append(
                        IntakeAttention(
                            code="RECEIVED_RELEASE_NOT_IN_BASIS",
                            message=f"Received release {reference} is not in the current Bid Basis.",
                            destination=f"{destination}/releases/{release_id}",
                            record_id=release_id,
                            blocking=True,
                        )
                    )
                issue_date = str(row["customer_issue_date"]) if row["customer_issue_date"] else None
                if (
                    issue_date is not None
                    and prior_issue_date is not None
                    and issue_date < prior_issue_date
                ):
                    attention.append(
                        IntakeAttention(
                            code="RELEASE_DATE_SEQUENCE_CONFLICT",
                            message=(
                                f"Release {reference} has a customer issue date earlier than the "
                                "preceding internal receipt sequence; human review is required."
                            ),
                            destination=f"{destination}/releases/{release_id}",
                            record_id=release_id,
                            blocking=False,
                        )
                    )
                if issue_date is not None:
                    prior_issue_date = issue_date
                ack = conn.execute(
                    """SELECT base.* FROM bid_release_acknowledgement_events base
                    WHERE base.release_id=? AND NOT EXISTS(SELECT 1 FROM
                    bid_release_acknowledgement_events newer
                    WHERE newer.supersedes_event_id=base.acknowledgement_event_id)""",
                    (release_id,),
                ).fetchone()
                if ack is not None and ack["event_type"] == "REQUIRED":
                    due_at = (
                        datetime.fromisoformat(str(ack["due_at"]))
                        if ack["due_at"] is not None
                        else None
                    )
                    attention.append(
                        IntakeAttention(
                            code="ACKNOWLEDGEMENT_OUTSTANDING",
                            message=f"Acknowledgement for release {reference} is outstanding.",
                            destination=f"{destination}/releases/{release_id}",
                            record_id=str(ack["acknowledgement_event_id"]),
                            due_at=due_at,
                            blocking=True,
                        )
                    )
            duplicate_refs = conn.execute(
                """SELECT exact_customer_reference FROM bid_received_releases
                WHERE bid_id=? AND exact_customer_reference IS NOT NULL
                GROUP BY exact_customer_reference HAVING count(*)>1""",
                (bid_id,),
            ).fetchall()
            for row in duplicate_refs:
                attention.append(
                    IntakeAttention(
                        code="RELEASE_REFERENCE_CONFLICT",
                        message=(
                            f"Customer reference {row['exact_customer_reference']} is recorded for "
                            "multiple received releases and requires human resolution."
                        ),
                        destination=destination,
                        blocking=False,
                    )
                )
            latest_channels: dict[str, str] = {}
            for channel in ("PORTAL", "EMAIL"):
                row = conn.execute(
                    """SELECT observed_customer_reference FROM bid_release_channel_checks
                    WHERE bid_id=? AND channel=? AND observed_customer_reference IS NOT NULL
                    ORDER BY checked_at DESC,channel_check_id DESC LIMIT 1""",
                    (bid_id, channel),
                ).fetchone()
                if row is not None:
                    latest_channels[channel] = str(row["observed_customer_reference"])
            if len(latest_channels) == 2 and latest_channels["PORTAL"] != latest_channels["EMAIL"]:
                attention.append(
                    IntakeAttention(
                        code="PORTAL_EMAIL_DISCREPANCY",
                        message=(
                            "Latest manually recorded portal and email release references differ; "
                            "confirm the customer release position."
                        ),
                        destination=destination,
                        blocking=False,
                    )
                )
            if snapshot_id is not None:
                invalid = conn.execute(
                    """SELECT sd.document_version_id FROM bid_basis_snapshot_documents sd
                    JOIN documents d ON d.id=sd.document_id
                    WHERE sd.snapshot_id=? AND (d.control_lifecycle<>'ACTIVE'
                    OR d.current_version_id<>sd.document_version_id)""",
                    (snapshot_id,),
                ).fetchall()
                for row in invalid:
                    version_id = str(row["document_version_id"])
                    attention.append(
                        IntakeAttention(
                            code="BASIS_DOCUMENT_VERSION_INVALID",
                            message=f"Current Bid Basis version {version_id} is no longer current and active.",
                            destination=destination,
                            record_id=version_id,
                            blocking=True,
                        )
                    )
        return attention

    def issue_blockers(self, bid_id: str) -> tuple[IntakeAttention, ...]:
        """Return deterministic OPS-09 blockers only after intake has population."""
        with self._conn() as conn:
            populated = conn.execute(
                """SELECT 1 FROM bid_release_notices WHERE bid_id=? UNION ALL
                SELECT 1 FROM bid_received_releases WHERE bid_id=? LIMIT 1""",
                (bid_id, bid_id),
            ).fetchone()
        if populated is None:
            return ()
        return tuple(item for item in self._attention(bid_id) if item.blocking)

    def summary(self, bid_id: str) -> PackageIntakeSummary:
        with self._conn() as conn:
            notices = tuple(
                _dict(row)
                for row in conn.execute(
                    """SELECT base.* FROM bid_release_notices base WHERE base.bid_id=?
                    AND NOT EXISTS(SELECT 1 FROM bid_release_notices newer
                    WHERE newer.supersedes_notice_id=base.notice_id)
                    ORDER BY base.observed_at DESC,base.notice_id DESC""",
                    (bid_id,),
                ).fetchall()
            )
            checks = tuple(
                _dict(row)
                for row in conn.execute(
                    """SELECT * FROM bid_release_channel_checks WHERE bid_id=?
                    ORDER BY checked_at DESC,channel_check_id DESC""",
                    (bid_id,),
                ).fetchall()
            )
            current = self.current_snapshot(bid_id)
            current_release_ids = (
                set(self.snapshot_release_ids(str(current["snapshot_id"]))) if current else set()
            )
            release_rows: list[dict[str, object]] = []
            for row in conn.execute(
                """SELECT * FROM bid_received_releases WHERE bid_id=?
                ORDER BY receipt_sequence DESC""",
                (bid_id,),
            ).fetchall():
                item = _dict(row)
                item["incorporated"] = str(row["release_id"]) in current_release_ids
                progress = conn.execute(
                    """SELECT
                    (SELECT count(*) FROM bid_received_files f JOIN
                    bid_received_file_disposition_events d ON d.file_id=f.file_id
                    AND d.analysis_eligibility<>'NOT_ASSESSED' AND NOT EXISTS(
                    SELECT 1 FROM bid_received_file_disposition_events newer
                    WHERE newer.supersedes_event_id=d.disposition_event_id)
                    WHERE f.release_id=?) +
                    (SELECT count(*) FROM bid_addendum_directives ad JOIN
                    bid_addendum_dispositions dd ON dd.directive_id=ad.directive_id
                    AND dd.status IN ('INCORPORATED','NOT_APPLICABLE') AND NOT EXISTS(
                    SELECT 1 FROM bid_addendum_dispositions newer
                    WHERE newer.supersedes_disposition_id=dd.disposition_id)
                    WHERE ad.release_id=?)""",
                    (row["release_id"], row["release_id"]),
                ).fetchone()[0]
                item["partial"] = not bool(item["incorporated"]) and int(progress) > 0
                release_rows.append(item)
            count_rows = conn.execute(
                """SELECT disp.content_form,count(*) AS count FROM bid_received_files f
                JOIN bid_received_releases r ON r.release_id=f.release_id
                JOIN bid_received_file_disposition_events disp ON disp.file_id=f.file_id
                AND NOT EXISTS(SELECT 1 FROM bid_received_file_disposition_events newer
                WHERE newer.supersedes_event_id=disp.disposition_event_id)
                WHERE r.bid_id=? GROUP BY disp.content_form""",
                (bid_id,),
            ).fetchall()
            duplicate_count = int(
                conn.execute(
                    """SELECT count(*) FROM bid_received_files f
                    JOIN bid_received_releases r ON r.release_id=f.release_id
                    WHERE r.bid_id=? AND EXISTS(SELECT 1 FROM bid_received_files earlier
                    JOIN bid_received_releases er ON er.release_id=earlier.release_id
                    WHERE er.bid_id=r.bid_id AND earlier.sha256=f.sha256
                    AND earlier.byte_size=f.byte_size AND earlier.rowid<f.rowid)""",
                    (bid_id,),
                ).fetchone()[0]
            )
            excluded_count = int(
                conn.execute(
                    """SELECT count(*) FROM bid_received_file_disposition_events disp
                    JOIN bid_received_files f ON f.file_id=disp.file_id
                    JOIN bid_received_releases r ON r.release_id=f.release_id
                    WHERE r.bid_id=? AND disp.analysis_eligibility='EXCLUDED'
                    AND NOT EXISTS(SELECT 1 FROM bid_received_file_disposition_events newer
                    WHERE newer.supersedes_event_id=disp.disposition_event_id)""",
                    (bid_id,),
                ).fetchone()[0]
            )
        return PackageIntakeSummary(
            bid_id=bid_id,
            notices=notices,
            channel_checks=checks,
            releases=tuple(release_rows),
            current_snapshot=current,
            attention=tuple(self._attention(bid_id)),
            content_form_counts={str(row["content_form"]): int(row["count"]) for row in count_rows},
            duplicate_count=duplicate_count,
            excluded_count=excluded_count,
        )

    def basis_register(self, bid_id: str, snapshot_id: str | None = None) -> dict[str, object]:
        with self._conn() as conn:
            if snapshot_id is None:
                snapshot = conn.execute(
                    """SELECT * FROM bid_basis_snapshots WHERE bid_id=?
                    ORDER BY snapshot_sequence DESC LIMIT 1""",
                    (bid_id,),
                ).fetchone()
            else:
                snapshot = conn.execute(
                    "SELECT * FROM bid_basis_snapshots WHERE snapshot_id=? AND bid_id=?",
                    (snapshot_id, bid_id),
                ).fetchone()
            if snapshot is None:
                raise IntakeNotFoundError("controlled Bid Basis snapshot not found")
            bid = conn.execute("SELECT * FROM bids WHERE bid_id=?", (bid_id,)).fetchone()
            if bid is None:
                raise IntakeNotFoundError("Bid not found")
            releases = [
                _dict(row)
                for row in conn.execute(
                    """SELECT r.release_id,r.release_type,r.exact_customer_reference,
                    r.customer_issue_date,r.received_at,r.received_channel,sr.incorporation_sequence
                    FROM bid_basis_snapshot_releases sr JOIN bid_received_releases r
                    ON r.release_id=sr.release_id WHERE sr.snapshot_id=?
                    ORDER BY sr.incorporation_sequence""",
                    (snapshot["snapshot_id"],),
                ).fetchall()
            ]
            documents = [
                _dict(row)
                for row in conn.execute(
                    """SELECT d.document_number,d.control_title,v.version_label,
                    v.document_version_id,sd.basis_role,r.exact_customer_reference
                    FROM bid_basis_snapshot_documents sd JOIN documents d ON d.id=sd.document_id
                    JOIN document_versions v ON v.document_version_id=sd.document_version_id
                    JOIN bid_received_releases r ON r.release_id=sd.source_release_id
                    WHERE sd.snapshot_id=? ORDER BY lower(d.control_title),d.id""",
                    (snapshot["snapshot_id"],),
                ).fetchall()
            ]
            directives = [
                _dict(row)
                for row in conn.execute(
                    """SELECT d.directive_type,d.description,d.materiality,disp.status,
                    disp.rationale,r.exact_customer_reference
                    FROM bid_basis_snapshot_releases sr JOIN bid_received_releases r
                    ON r.release_id=sr.release_id JOIN bid_addendum_directives d
                    ON d.release_id=r.release_id AND NOT EXISTS(SELECT 1
                    FROM bid_addendum_directives newer
                    WHERE newer.supersedes_directive_id=d.directive_id)
                    JOIN bid_addendum_dispositions disp ON disp.directive_id=d.directive_id
                    AND NOT EXISTS(SELECT 1 FROM bid_addendum_dispositions newer
                    WHERE newer.supersedes_disposition_id=disp.disposition_id)
                    WHERE sr.snapshot_id=? ORDER BY sr.incorporation_sequence,d.created_at""",
                    (snapshot["snapshot_id"],),
                ).fetchall()
            ]
            acknowledgements = [
                _dict(row)
                for row in conn.execute(
                    """SELECT r.exact_customer_reference,a.event_type,
                    a.acknowledgement_reference,a.recorded_at
                    FROM bid_basis_snapshot_releases sr JOIN bid_received_releases r
                    ON r.release_id=sr.release_id JOIN bid_release_acknowledgement_events a
                    ON a.release_id=r.release_id AND NOT EXISTS(SELECT 1 FROM
                    bid_release_acknowledgement_events newer
                    WHERE newer.supersedes_event_id=a.acknowledgement_event_id)
                    WHERE sr.snapshot_id=? AND a.event_type IN ('ACKNOWLEDGED','NOT_REQUIRED')
                    ORDER BY sr.incorporation_sequence""",
                    (snapshot["snapshot_id"],),
                ).fetchall()
            ]
            requirement_table = conn.execute(
                """SELECT 1 FROM sqlite_master WHERE type='table' AND name='requirements'"""
            ).fetchone()
            qualifications = (
                [
                    _dict(row)
                    for row in conn.execute(
                        """SELECT requirement_id,title,statement,disposition,response_text
                        FROM requirements WHERE bid_id=? AND lifecycle_state='ACTIVE'
                        AND disposition IN ('DEVIATE','EXCLUDE')
                        AND source_document_version_id IN (SELECT document_version_id
                        FROM bid_basis_snapshot_documents WHERE snapshot_id=?)
                        ORDER BY requirement_id""",
                        (bid_id, snapshot["snapshot_id"]),
                    ).fetchall()
                ]
                if requirement_table is not None
                else []
            )
        return {
            "bid": {
                "bid_id": str(bid["bid_id"]),
                "customer": str(bid["customer"]),
                "project_name": str(bid["project_name"]),
            },
            "snapshot": _dict(snapshot),
            "releases": releases,
            "documents": documents,
            "directives": directives,
            "acknowledgements": acknowledgements,
            "qualifications": qualifications,
            "unresolved_items": [],
        }

    def link_work_item(
        self,
        *,
        link_id: str,
        bid_id: str,
        work_item_id: str,
        operation_id: str,
        actor: str,
        at: datetime,
        provenance: Provenance,
        audit_id: str,
        notice_id: str | None = None,
        release_id: str | None = None,
        file_id: str | None = None,
        directive_id: str | None = None,
        acknowledgement_event_id: str | None = None,
    ) -> dict[str, object]:
        targets = (notice_id, release_id, file_id, directive_id, acknowledgement_event_id)
        if sum(value is not None for value in targets) != 1:
            raise ValueError("exactly one intake work-item target is required")
        self.validate_work_target(
            bid_id=bid_id,
            notice_id=notice_id,
            release_id=release_id,
            file_id=file_id,
            directive_id=directive_id,
            acknowledgement_event_id=acknowledgement_event_id,
        )
        with self._conn() as conn:
            work = conn.execute(
                "SELECT 1 FROM work_items WHERE work_item_id=? AND bid_id=?",
                (work_item_id, bid_id),
            ).fetchone()
            if work is None:
                raise IntakeConflictError("work item must belong to this Bid")
            existing = conn.execute(
                "SELECT * FROM bid_intake_work_item_links WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if existing is not None:
                return _dict(existing)
            conn.execute(
                "INSERT INTO bid_intake_work_item_links VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    link_id,
                    work_item_id,
                    notice_id,
                    release_id,
                    file_id,
                    directive_id,
                    acknowledgement_event_id,
                    operation_id,
                    actor,
                    at.isoformat(),
                    provenance.model_dump_json(),
                ),
            )
            self._audit(
                conn,
                entry_id=audit_id,
                bid_id=bid_id,
                actor=actor,
                action="bid_intake_work_item_linked",
                detail={"work_item_id": work_item_id, "operation_id": operation_id},
                at=at,
            )
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM bid_intake_work_item_links WHERE intake_work_item_link_id=?",
                (link_id,),
            ).fetchone()
        if row is None:
            raise IntakeNotFoundError("intake work-item link not found")
        return _dict(row)

    def validate_work_target(
        self,
        *,
        bid_id: str,
        notice_id: str | None = None,
        release_id: str | None = None,
        file_id: str | None = None,
        directive_id: str | None = None,
        acknowledgement_event_id: str | None = None,
    ) -> None:
        targets = (notice_id, release_id, file_id, directive_id, acknowledgement_event_id)
        if sum(value is not None for value in targets) != 1:
            raise ValueError("exactly one intake work-item target is required")
        target_checks = (
            ("bid_release_notices", "notice_id", notice_id, "bid_id"),
            ("bid_received_releases", "release_id", release_id, "bid_id"),
            (
                "bid_received_files JOIN bid_received_releases USING(release_id)",
                "file_id",
                file_id,
                "bid_id",
            ),
            (
                "bid_addendum_directives JOIN bid_received_releases USING(release_id)",
                "directive_id",
                directive_id,
                "bid_id",
            ),
            (
                "bid_release_acknowledgement_events JOIN bid_received_releases USING(release_id)",
                "acknowledgement_event_id",
                acknowledgement_event_id,
                "bid_id",
            ),
        )
        with self._conn() as conn:
            for table, column, value, bid_column in target_checks:
                if value is not None:
                    found = conn.execute(
                        f"SELECT 1 FROM {table} WHERE {column}=? AND {bid_column}=?",
                        (value, bid_id),
                    ).fetchone()
                    if found is None:
                        raise IntakeConflictError("intake target must belong to this Bid")

    def linked_work_items(self, bid_id: str) -> tuple[dict[str, object], ...]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT l.*,w.title,w.status,w.due_date FROM bid_intake_work_item_links l
                JOIN work_items w ON w.work_item_id=l.work_item_id WHERE w.bid_id=?
                ORDER BY l.created_at DESC""",
                (bid_id,),
            ).fetchall()
        return tuple(_dict(row) for row in rows)

    def work_link_by_operation(self, operation_id: str) -> dict[str, object] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM bid_intake_work_item_links WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
        return _dict(row) if row is not None else None

    def file_download_evidence(
        self, bid_id: str, release_id: str, file_id: str
    ) -> dict[str, object]:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT f.* FROM bid_received_files f JOIN bid_received_releases r
                ON r.release_id=f.release_id WHERE r.bid_id=? AND r.release_id=? AND f.file_id=?""",
                (bid_id, release_id, file_id),
            ).fetchone()
        if row is None:
            raise IntakeNotFoundError("received file not found in this Bid and release")
        return _dict(row)

    def record_reprocess(
        self,
        *,
        processing_run_id: str,
        result_ids: Sequence[str],
        bid_id: str,
        release_id: str,
        operation_id: str,
        results: Sequence[tuple[str, str, str]],
        actor: str,
        at: datetime,
        provenance: Provenance,
        audit_id: str,
    ) -> dict[str, object]:
        if len(result_ids) != len(results):
            raise ValueError("one processing result ID is required per file result")
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            release = conn.execute(
                "SELECT 1 FROM bid_received_releases WHERE release_id=? AND bid_id=?",
                (release_id, bid_id),
            ).fetchone()
            if release is None:
                raise IntakeNotFoundError("received release not found in this Bid")
            existing = conn.execute(
                "SELECT * FROM bid_intake_processing_runs WHERE bid_id=? AND operation_id=?",
                (bid_id, operation_id),
            ).fetchone()
            if existing is not None:
                conn.rollback()
                return _dict(existing)
            state = (
                "SUCCEEDED" if all(status == "SUCCEEDED" for _, status, _ in results) else "FAILED"
            )
            conn.execute(
                "INSERT INTO bid_intake_processing_runs VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    processing_run_id,
                    bid_id,
                    release_id,
                    "REPROCESS",
                    state,
                    operation_id,
                    at.isoformat(),
                    at.isoformat(),
                    actor,
                    provenance.model_dump_json(),
                ),
            )
            for result_id, (file_id, status, message) in zip(result_ids, results, strict=True):
                conn.execute(
                    "INSERT INTO bid_intake_processing_file_results VALUES(?,?,?,?,?,?,?,?)",
                    (
                        result_id,
                        processing_run_id,
                        file_id,
                        "MANAGED_COPY_REVERIFICATION",
                        status,
                        message,
                        at.isoformat(),
                        provenance.model_dump_json(),
                    ),
                )
            self._audit(
                conn,
                entry_id=audit_id,
                bid_id=bid_id,
                actor=actor,
                action="bid_received_release_reverified",
                detail={
                    "release_id": release_id,
                    "processing_run_id": processing_run_id,
                    "state": state,
                    "operation_id": operation_id,
                },
                at=at,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM bid_intake_processing_runs WHERE processing_run_id=?",
                (processing_run_id,),
            ).fetchone()
        if row is None:
            raise IntakeNotFoundError("processing run not found")
        return _dict(row)


__all__ = [
    "BidPackageRepository",
    "IntakeConflictError",
    "IntakeNotFoundError",
    "OPS_11_MIGRATION_ID",
    "ReleaseNotIncorporableError",
    "StaleIntakeError",
]
