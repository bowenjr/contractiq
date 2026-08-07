"""Transactional SQLite repository for TASK-13 commercial completeness."""
# ruff: noqa: E501

from __future__ import annotations

import sqlite3
from builtins import list as builtin_list
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from core.commercial import AssessmentVersion, CommercialItem, CommercialLink, CommercialReview
from core.database import Database

COMMERCIAL_MIGRATION_ID = "task_13_commercial_completeness_v1"
STANDARD_FACTOR_CATEGORIES = (
    "FREIGHT_LOGISTICS",
    "DUTY_BROKERAGE",
    "TAXES_FEES",
    "CURRENCY_FX",
    "PRICE_VALIDITY",
    "ESCALATION",
    "PAYMENT_CARRY",
    "HOLDBACK_RETENTION",
    "BONDS_INSURANCE",
    "WARRANTY_SERVICE",
    "TESTING_INSPECTION",
    "DOCUMENTATION",
    "FIELD_SERVICE",
    "CONTINGENCY_RISK",
)


class CommercialRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._migrate()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _migrate(self) -> None:
        statements = [
            """CREATE TABLE IF NOT EXISTS commercial_items (commercial_item_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL,category TEXT NOT NULL,basis_role TEXT NOT NULL,materiality TEXT NOT NULL,owner TEXT,due_date TEXT,lifecycle_state TEXT NOT NULL,version INTEGER NOT NULL,provenance_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,created_by TEXT NOT NULL,FOREIGN KEY(bid_id) REFERENCES bids(bid_id),CHECK(lifecycle_state IN ('DRAFT','ACTIVE','WITHDRAWN')))""",
            """CREATE TABLE IF NOT EXISTS commercial_links (link_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,commercial_item_id TEXT NOT NULL,target_type TEXT NOT NULL,target_id TEXT NOT NULL,relation TEXT NOT NULL,created_at TEXT NOT NULL,created_by TEXT NOT NULL,FOREIGN KEY(commercial_item_id) REFERENCES commercial_items(commercial_item_id),UNIQUE(commercial_item_id,target_type,target_id,relation))""",
            """CREATE TABLE IF NOT EXISTS commercial_assessments (assessment_id TEXT PRIMARY KEY,commercial_item_id TEXT NOT NULL,bid_id TEXT NOT NULL,version_number INTEGER NOT NULL,applicability TEXT NOT NULL,treatment TEXT NOT NULL,amount_decimal TEXT,currency TEXT,evidence_basis TEXT,evidence_target_id TEXT,rationale TEXT,validity_until TEXT,assessed_by TEXT NOT NULL,assessed_at TEXT NOT NULL,supersedes_assessment_id TEXT,provenance_json TEXT NOT NULL,created_at TEXT NOT NULL,FOREIGN KEY(commercial_item_id) REFERENCES commercial_items(commercial_item_id),UNIQUE(commercial_item_id,version_number),CHECK(amount_decimal IS NULL OR amount_decimal NOT LIKE '%,%'))""",
            """CREATE TABLE IF NOT EXISTS commercial_reviews (review_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,commercial_item_id TEXT NOT NULL,assessment_id TEXT NOT NULL,decision TEXT NOT NULL,reviewer TEXT NOT NULL,rationale TEXT,reviewed_at TEXT NOT NULL,provenance_json TEXT NOT NULL,FOREIGN KEY(assessment_id) REFERENCES commercial_assessments(assessment_id))""",
            """CREATE TABLE IF NOT EXISTS commercial_schema_migrations (migration_id TEXT PRIMARY KEY,applied_at TEXT NOT NULL)""",
            """CREATE TRIGGER IF NOT EXISTS commercial_no_delete BEFORE DELETE ON commercial_items BEGIN SELECT RAISE(ABORT,'commercial items cannot be deleted'); END""",
            """CREATE TRIGGER IF NOT EXISTS commercial_link_no_delete BEFORE DELETE ON commercial_links BEGIN SELECT RAISE(ABORT,'commercial links cannot be deleted'); END""",
            """CREATE TRIGGER IF NOT EXISTS commercial_assessment_immutable BEFORE UPDATE ON commercial_assessments BEGIN SELECT RAISE(ABORT,'commercial assessments are immutable'); END""",
            """CREATE TRIGGER IF NOT EXISTS commercial_assessment_no_delete BEFORE DELETE ON commercial_assessments BEGIN SELECT RAISE(ABORT,'commercial assessments cannot be deleted'); END""",
            """CREATE TRIGGER IF NOT EXISTS commercial_review_immutable BEFORE UPDATE ON commercial_reviews BEGIN SELECT RAISE(ABORT,'commercial reviews are immutable'); END""",
            """CREATE TRIGGER IF NOT EXISTS commercial_review_no_delete BEFORE DELETE ON commercial_reviews BEGIN SELECT RAISE(ABORT,'commercial reviews cannot be deleted'); END""",
        ]
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT OR IGNORE INTO commercial_schema_migrations VALUES (?,?)",
                    (COMMERCIAL_MIGRATION_ID, datetime.now(UTC).isoformat()),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _audit(conn: sqlite3.Connection, bid_id: str, actor: str, action: str, detail: str) -> None:
        conn.execute(
            "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) VALUES(?,?,?,?,?,?)",
            (f"AUD-{uuid4().hex}", bid_id, actor, action, detail, datetime.now(UTC).isoformat()),
        )

    def create(self, item: CommercialItem, actor: str) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO commercial_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        item.commercial_item_id,
                        item.bid_id,
                        item.title,
                        item.description,
                        item.category.value,
                        item.basis_role.value,
                        item.materiality,
                        item.owner,
                        item.due_date.isoformat() if item.due_date else None,
                        item.lifecycle_state.value,
                        item.version,
                        item.provenance.model_dump_json(),
                        item.created_at.isoformat(),
                        item.updated_at.isoformat(),
                        item.created_by,
                    ),
                )
                self._audit(
                    conn, item.bid_id, actor, "commercial_item_created", item.commercial_item_id
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def initialize_standard(self, bid_id: str, actor: str) -> list[str]:
        created: list[str] = []
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for category in STANDARD_FACTOR_CATEGORIES:
                    found = conn.execute(
                        "SELECT 1 FROM commercial_items WHERE bid_id=? AND category=? AND lifecycle_state!='WITHDRAWN'",
                        (bid_id, category),
                    ).fetchone()
                    if found:
                        continue
                    now = datetime.now(UTC)
                    ident = f"COM-{uuid4().hex}"
                    conn.execute(
                        "INSERT INTO commercial_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            ident,
                            bid_id,
                            category.replace("_", " ").title(),
                            f"Assess {category.replace('_', ' ').lower()}",
                            category,
                            "COMMERCIAL_FACTOR",
                            "MATERIAL",
                            None,
                            None,
                            "DRAFT",
                            1,
                            "{}",
                            now.isoformat(),
                            now.isoformat(),
                            actor,
                        ),
                    )
                    created.append(ident)
                self._audit(
                    conn, bid_id, actor, "commercial_standard_initialized", str(len(created))
                )
                conn.commit()
                return created
            except Exception:
                conn.rollback()
                raise

    def add_link(self, link: CommercialLink, actor: str) -> None:
        targets = {
            "REQUIREMENT": ("requirements", "requirement_id"),
            "SCOPE_ITEM": ("scope_interface_items", "scope_item_id"),
            "INTERFACE": ("scope_interfaces", "interface_id"),
            "SUPPLIER_REQUEST_ITEM": ("supplier_request_items", "request_item_id"),
            "SUPPLIER_RESPONSE_VERSION": ("supplier_response_versions", "response_version_id"),
            "DELIVERABLE": ("deliverable_items", "deliverable_id"),
            "DOCUMENT_VERSION": ("document_versions", "document_version_id"),
            "COMMERCIAL_ITEM": ("commercial_items", "commercial_item_id"),
        }
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                item = conn.execute(
                    "SELECT bid_id,lifecycle_state FROM commercial_items WHERE commercial_item_id=?",
                    (link.commercial_item_id,),
                ).fetchone()
                if (
                    item is None
                    or item["bid_id"] != link.bid_id
                    or item["lifecycle_state"] != "DRAFT"
                ):
                    raise ValueError("link requires same-bid draft commercial item")
                table, column = targets[link.target_type.value]
                target = conn.execute(
                    f"SELECT bid_id FROM {table} WHERE {column}=?", (link.target_id,)
                ).fetchone()
                if target is None or target["bid_id"] != link.bid_id:
                    raise ValueError("commercial link target missing or cross-bid")
                if (
                    link.target_type.value == "COMMERCIAL_ITEM"
                    and link.target_id == link.commercial_item_id
                ):
                    raise ValueError("commercial lineage cycle")
                conn.execute(
                    "INSERT INTO commercial_links VALUES (?,?,?,?,?,?,?,?)",
                    (
                        link.link_id,
                        link.bid_id,
                        link.commercial_item_id,
                        link.target_type.value,
                        link.target_id,
                        link.relation.value,
                        link.created_at.isoformat(),
                        link.created_by,
                    ),
                )
                self._audit(conn, link.bid_id, actor, "commercial_link_created", link.link_id)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def activate(self, item_id: str, expected_version: int, actor: str) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT bid_id,category,basis_role FROM commercial_items WHERE commercial_item_id=? AND version=? AND lifecycle_state='DRAFT'",
                    (item_id, expected_version),
                ).fetchone()
                if row is None:
                    raise ValueError("stale or non-draft commercial item")
                links = conn.execute(
                    "SELECT 1 FROM commercial_links WHERE commercial_item_id=?", (item_id,)
                ).fetchone()
                if links is None:
                    raise ValueError("commercial item requires a source link")
                if (
                    row["category"] == "SCOPE_PRICE"
                    and row["basis_role"] == "CUSTOMER_PRICE"
                    and conn.execute(
                        "SELECT 1 FROM commercial_links WHERE commercial_item_id=? AND target_type='SCOPE_ITEM'",
                        (item_id,),
                    ).fetchone()
                    is None
                ):
                    raise ValueError("customer scope price requires scope link")
                conn.execute(
                    "UPDATE commercial_items SET lifecycle_state='ACTIVE',version=version+1,updated_at=? WHERE commercial_item_id=? AND version=?",
                    (datetime.now(UTC).isoformat(), item_id, expected_version),
                )
                self._audit(conn, row["bid_id"], actor, "commercial_item_activated", item_id)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def add_assessment(self, value: AssessmentVersion, actor: str) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                item = conn.execute(
                    "SELECT bid_id,lifecycle_state FROM commercial_items WHERE commercial_item_id=?",
                    (value.commercial_item_id,),
                ).fetchone()
                if (
                    item is None
                    or item["bid_id"] != value.bid_id
                    or item["lifecycle_state"] != "ACTIVE"
                ):
                    raise ValueError("assessment requires active same-bid item")
                next_version = conn.execute(
                    "SELECT COALESCE(MAX(version_number),0)+1 FROM commercial_assessments WHERE commercial_item_id=?",
                    (value.commercial_item_id,),
                ).fetchone()[0]
                if value.version_number != next_version:
                    raise ValueError("assessment version must be monotonic")
                conn.execute(
                    "INSERT INTO commercial_assessments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        value.assessment_id,
                        value.commercial_item_id,
                        value.bid_id,
                        value.version_number,
                        value.applicability.value,
                        value.treatment.value,
                        str(value.amount) if value.amount is not None else None,
                        value.currency,
                        value.evidence_basis.value if value.evidence_basis else None,
                        value.evidence_target_id,
                        value.rationale,
                        value.validity_until.isoformat() if value.validity_until else None,
                        value.assessed_by,
                        value.assessed_at.isoformat(),
                        value.supersedes_assessment_id,
                        value.provenance.model_dump_json(),
                        value.created_at.isoformat(),
                    ),
                )
                self._audit(
                    conn, value.bid_id, actor, "commercial_assessment_created", value.assessment_id
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def add_review(self, value: CommercialReview, actor: str) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT bid_id,commercial_item_id,assessed_by FROM commercial_assessments WHERE assessment_id=?",
                    (value.assessment_id,),
                ).fetchone()
                if (
                    row is None
                    or row["bid_id"] != value.bid_id
                    or row["commercial_item_id"] != value.commercial_item_id
                ):
                    raise ValueError("review identity mismatch")
                if row["assessed_by"] == value.reviewer and value.decision.value == "ACCEPTED":
                    raise ValueError("assessment author cannot self-accept")
                existing = conn.execute(
                    "SELECT decision FROM commercial_reviews WHERE assessment_id=? AND decision='ACCEPTED'",
                    (value.assessment_id,),
                ).fetchone()
                if existing:
                    raise ValueError("assessment already accepted")
                conn.execute(
                    "INSERT INTO commercial_reviews VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        value.review_id,
                        value.bid_id,
                        value.commercial_item_id,
                        value.assessment_id,
                        value.decision.value,
                        value.reviewer,
                        value.rationale,
                        value.reviewed_at.isoformat(),
                        value.provenance.model_dump_json(),
                    ),
                )
                self._audit(
                    conn, value.bid_id, actor, "commercial_assessment_reviewed", value.assessment_id
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def list(self, bid_id: str | None = None) -> builtin_list[dict[str, Any]]:
        query = "SELECT * FROM commercial_items"
        params: tuple[str, ...] = ()
        if bid_id:
            query += " WHERE bid_id=?"
            params = (bid_id,)
        query += " ORDER BY title,commercial_item_id"
        with self._conn() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def links(self, item_id: str) -> builtin_list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM commercial_links WHERE commercial_item_id=? ORDER BY link_id",
                    (item_id,),
                ).fetchall()
            ]

    def assessments(self, item_id: str) -> builtin_list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM commercial_assessments WHERE commercial_item_id=? ORDER BY version_number",
                    (item_id,),
                ).fetchall()
            ]

    def reviews(self, item_id: str) -> builtin_list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM commercial_reviews WHERE commercial_item_id=? ORDER BY reviewed_at",
                    (item_id,),
                ).fetchall()
            ]
