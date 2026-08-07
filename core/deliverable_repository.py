"""SQLite persistence for the authoritative TASK-12 deliverable register."""

from __future__ import annotations

import sqlite3
from builtins import list as builtin_list
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from core.database import Database
from core.deliverables import (
    Deliverable,
    DeliverableLink,
    ReviewDecisionRecord,
    SubmissionVersion,
    SupplierCommitment,
)

DELIVERABLE_MIGRATION_ID = "task_12_vendor_data_control_v1"


class DeliverableRepository:
    """Owns the additive migration and atomic register writes."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._migrate()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _migrate(self) -> None:
        statements = [
            """CREATE TABLE IF NOT EXISTS deliverable_items (
                deliverable_id TEXT PRIMARY KEY, bid_id TEXT NOT NULL,
                title TEXT NOT NULL, description TEXT NOT NULL, category TEXT NOT NULL,
                criticality TEXT NOT NULL, materiality TEXT NOT NULL,
                lifecycle_phase TEXT NOT NULL, direction TEXT NOT NULL,
                workflow_state TEXT NOT NULL, owner TEXT, supplier_id TEXT,
                recipient TEXT, due_basis TEXT NOT NULL, fixed_due_date TEXT,
                event_name TEXT, offset_days INTEGER, condition_text TEXT,
                condition_active INTEGER NOT NULL DEFAULT 1, required_format TEXT,
                required_review_role TEXT, cancel_reason TEXT, version INTEGER NOT NULL,
                provenance_json TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, created_by TEXT NOT NULL,
                FOREIGN KEY (bid_id) REFERENCES bids(bid_id),
                CHECK (workflow_state IN ('DRAFT','ACTIVE','SATISFIED','CANCELLED'))
            )""",
            """CREATE TABLE IF NOT EXISTS deliverable_links (
                link_id TEXT PRIMARY KEY, bid_id TEXT NOT NULL, deliverable_id TEXT NOT NULL,
                target_type TEXT NOT NULL, target_id TEXT NOT NULL, relation TEXT NOT NULL,
                created_at TEXT NOT NULL, created_by TEXT NOT NULL,
                FOREIGN KEY (deliverable_id) REFERENCES deliverable_items(deliverable_id),
                UNIQUE (deliverable_id,target_type,target_id,relation)
            )""",
            """CREATE TABLE IF NOT EXISTS supplier_commitments (
                commitment_id TEXT PRIMARY KEY, deliverable_id TEXT NOT NULL,
                bid_id TEXT NOT NULL, supplier_id TEXT NOT NULL,
                response_version_id TEXT NOT NULL, committed_due_date TEXT NOT NULL,
                validity_until TEXT, supersedes_commitment_id TEXT,
                created_at TEXT NOT NULL, created_by TEXT NOT NULL,
                FOREIGN KEY (deliverable_id) REFERENCES deliverable_items(deliverable_id)
            )""",
            """CREATE TABLE IF NOT EXISTS deliverable_submissions (
                submission_id TEXT PRIMARY KEY, deliverable_id TEXT NOT NULL,
                bid_id TEXT NOT NULL, version_number INTEGER NOT NULL,
                sender TEXT NOT NULL, recipient TEXT NOT NULL, submitted_at TEXT NOT NULL,
                evidence_mode TEXT NOT NULL, document_version_id TEXT,
                evidence_note TEXT, reference TEXT, expires_at TEXT,
                disposition TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
                FOREIGN KEY (deliverable_id) REFERENCES deliverable_items(deliverable_id),
                UNIQUE (deliverable_id,version_number)
            )""",
            """CREATE TABLE IF NOT EXISTS deliverable_reviews (
                review_id TEXT PRIMARY KEY, deliverable_id TEXT NOT NULL,
                bid_id TEXT NOT NULL, submission_id TEXT NOT NULL, decision TEXT NOT NULL,
                reviewer TEXT NOT NULL, rationale TEXT, reviewed_at TEXT NOT NULL,
                version INTEGER NOT NULL,
                FOREIGN KEY (submission_id) REFERENCES deliverable_submissions(submission_id)
            )""",
            """CREATE TABLE IF NOT EXISTS deliverable_schema_migrations (
                migration_id TEXT PRIMARY KEY, applied_at TEXT NOT NULL
            )""",
            """CREATE TRIGGER IF NOT EXISTS deliverable_no_delete
                BEFORE DELETE ON deliverable_items BEGIN
                SELECT RAISE(ABORT,'deliverables cannot be deleted'); END""",
            """CREATE TRIGGER IF NOT EXISTS submission_no_update
                BEFORE UPDATE ON deliverable_submissions BEGIN
                SELECT RAISE(ABORT,'submission versions are immutable'); END""",
            """CREATE TRIGGER IF NOT EXISTS submission_no_delete
                BEFORE DELETE ON deliverable_submissions BEGIN
                SELECT RAISE(ABORT,'submission versions cannot be deleted'); END""",
        ]
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT OR IGNORE INTO deliverable_schema_migrations VALUES (?,?)",
                    (DELIVERABLE_MIGRATION_ID, datetime.now(UTC).isoformat()),
                )
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

    def create(self, item: Deliverable, actor: str) -> None:
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
                conn.execute(
                    "INSERT INTO deliverable_items VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
                self._audit(conn, item.bid_id, actor, "deliverable_created", item.deliverable_id)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def add_link(self, link: DeliverableLink, actor: str) -> None:
        targets = {
            "REQUIREMENT": ("requirements", "requirement_id"),
            "SCOPE_ITEM": ("scope_interface_items", "scope_item_id"),
            "INTERFACE": ("scope_interfaces", "interface_id"),
            "SUPPLIER_REQUEST_ITEM": ("supplier_request_items", "request_item_id"),
            "SUPPLIER_RESPONSE_VERSION": ("supplier_response_versions", "response_version_id"),
            "DOCUMENT_VERSION": ("document_versions", "document_version_id"),
        }
        table, column = targets[link.target_type.value]
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                item = conn.execute(
                    "SELECT bid_id,workflow_state FROM deliverable_items WHERE deliverable_id=?",
                    (link.deliverable_id,),
                ).fetchone()
                if (
                    item is None
                    or item["bid_id"] != link.bid_id
                    or item["workflow_state"] != "DRAFT"
                ):
                    raise ValueError("link requires same-bid draft deliverable")
                target = conn.execute(
                    f"SELECT bid_id FROM {table} WHERE {column}=?", (link.target_id,)
                ).fetchone()
                if target is None or target["bid_id"] != link.bid_id:
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
                self._audit(conn, link.bid_id, actor, "deliverable_link_created", link.link_id)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def activate(self, deliverable_id: str, expected_version: int, actor: str) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT bid_id FROM deliverable_items WHERE deliverable_id=? "
                    "AND version=? AND workflow_state='DRAFT'",
                    (deliverable_id, expected_version),
                ).fetchone()
                if row is None:
                    raise ValueError("stale or non-draft deliverable")
                if (
                    conn.execute(
                        "SELECT 1 FROM deliverable_links WHERE deliverable_id=?", (deliverable_id,)
                    ).fetchone()
                    is None
                ):
                    raise ValueError("active deliverable requires a source link")
                conn.execute(
                    "UPDATE deliverable_items SET workflow_state='ACTIVE',version=version+1,"
                    "updated_at=? WHERE deliverable_id=? AND version=?",
                    (datetime.now(UTC).isoformat(), deliverable_id, expected_version),
                )
                self._audit(conn, row["bid_id"], actor, "deliverable_activated", deliverable_id)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def add_commitment(self, commitment: SupplierCommitment, actor: str) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                item = conn.execute(
                    "SELECT bid_id,supplier_id FROM deliverable_items WHERE deliverable_id=?",
                    (commitment.deliverable_id,),
                ).fetchone()
                if (
                    item is None
                    or item["bid_id"] != commitment.bid_id
                    or item["supplier_id"] != commitment.supplier_id
                ):
                    raise ValueError("commitment identity mismatch")
                response = conn.execute(
                    "SELECT bid_id,supplier_id,review_state FROM supplier_response_versions "
                    "WHERE response_version_id=?",
                    (commitment.response_version_id,),
                ).fetchone()
                if (
                    response is None
                    or response["bid_id"] != commitment.bid_id
                    or response["supplier_id"] != commitment.supplier_id
                    or response["review_state"] != "ACCEPTED"
                ):
                    raise ValueError("commitment requires an accepted same-bid supplier response")
                conn.execute(
                    "INSERT INTO supplier_commitments VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        commitment.commitment_id,
                        commitment.deliverable_id,
                        commitment.bid_id,
                        commitment.supplier_id,
                        commitment.response_version_id,
                        commitment.committed_due_date.isoformat(),
                        commitment.validity_until.isoformat()
                        if commitment.validity_until
                        else None,
                        commitment.supersedes_commitment_id,
                        commitment.created_at.isoformat(),
                        commitment.created_by,
                    ),
                )
                self._audit(
                    conn,
                    commitment.bid_id,
                    actor,
                    "deliverable_commitment_created",
                    commitment.commitment_id,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def add_submission(self, submission: SubmissionVersion, actor: str) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                item = conn.execute(
                    "SELECT bid_id FROM deliverable_items WHERE deliverable_id=?",
                    (submission.deliverable_id,),
                ).fetchone()
                if item is None or item["bid_id"] != submission.bid_id:
                    raise ValueError("submission identity mismatch")
                next_version = conn.execute(
                    "SELECT COALESCE(MAX(version_number),0)+1 "
                    "FROM deliverable_submissions WHERE deliverable_id=?",
                    (submission.deliverable_id,),
                ).fetchone()[0]
                if submission.version_number != next_version:
                    raise ValueError("submission version must be the next immutable version")
                conn.execute(
                    "INSERT INTO deliverable_submissions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        submission.submission_id,
                        submission.deliverable_id,
                        submission.bid_id,
                        submission.version_number,
                        submission.sender,
                        submission.recipient,
                        submission.submitted_at.isoformat(),
                        submission.evidence_mode.value,
                        submission.document_version_id,
                        submission.evidence_note,
                        submission.reference,
                        submission.expires_at.isoformat() if submission.expires_at else None,
                        submission.disposition.value,
                        submission.created_at.isoformat(),
                        submission.created_by,
                    ),
                )
                self._audit(
                    conn,
                    submission.bid_id,
                    actor,
                    "deliverable_submission_created",
                    submission.submission_id,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def add_review(self, review: ReviewDecisionRecord, actor: str) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT bid_id,deliverable_id FROM deliverable_submissions "
                    "WHERE submission_id=?",
                    (review.submission_id,),
                ).fetchone()
                if (
                    row is None
                    or row["bid_id"] != review.bid_id
                    or row["deliverable_id"] != review.deliverable_id
                ):
                    raise ValueError("review identity mismatch")
                conn.execute(
                    "INSERT INTO deliverable_reviews VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        review.review_id,
                        review.deliverable_id,
                        review.bid_id,
                        review.submission_id,
                        review.decision.value,
                        review.reviewer,
                        review.rationale,
                        review.reviewed_at.isoformat(),
                        review.version,
                    ),
                )
                self._audit(
                    conn,
                    review.bid_id,
                    actor,
                    "deliverable_submission_reviewed",
                    review.submission_id,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def list(self, bid_id: str | None = None) -> builtin_list[dict[str, Any]]:
        query = "SELECT * FROM deliverable_items"
        params: tuple[str, ...] = ()
        if bid_id:
            query += " WHERE bid_id=?"
            params = (bid_id,)
        query += " ORDER BY title,deliverable_id"
        with self._conn() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def links(self, deliverable_id: str) -> builtin_list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM deliverable_links WHERE deliverable_id=? ORDER BY link_id",
                    (deliverable_id,),
                ).fetchall()
            ]

    def commitments(self, deliverable_id: str) -> builtin_list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM supplier_commitments WHERE deliverable_id=? ORDER BY created_at",
                    (deliverable_id,),
                ).fetchall()
            ]

    def submissions(self, deliverable_id: str) -> builtin_list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM deliverable_submissions "
                    "WHERE deliverable_id=? ORDER BY version_number",
                    (deliverable_id,),
                ).fetchall()
            ]

    def reviews(self, deliverable_id: str) -> builtin_list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM deliverable_reviews WHERE deliverable_id=? ORDER BY reviewed_at",
                    (deliverable_id,),
                ).fetchall()
            ]

    def history(self, deliverable_id: str) -> builtin_list[dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT bid_id FROM deliverable_items WHERE deliverable_id=?", (deliverable_id,)
            ).fetchone()
            if row is None:
                raise ValueError("deliverable not found")
            return [
                dict(item)
                for item in conn.execute(
                    "SELECT * FROM audit_log WHERE bid_id=? AND detail LIKE ? "
                    "ORDER BY timestamp,entry_id",
                    (row["bid_id"], f"%{deliverable_id}%"),
                ).fetchall()
            ]

    def raw_items(self, bid_id: str | None = None) -> builtin_list[dict[str, Any]]:
        return self.list(bid_id)
