"""SQLite persistence and additive migration for TASK-14."""
# ruff: noqa: E501

from __future__ import annotations

import sqlite3
from builtins import list as builtin_list
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from core.contract_risk import ContractIssue, RiskAssessment, RiskLink, RiskReview, RiskSource
from core.database import Database

RISK_MIGRATION_ID = "task_14_contract_risk_control_v1"


class ContractRiskRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._migrate()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _migrate(self) -> None:
        sql = [
            """CREATE TABLE IF NOT EXISTS contract_issues(issue_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,issue_code TEXT NOT NULL,title TEXT NOT NULL,summary TEXT NOT NULL,owner TEXT,materiality TEXT NOT NULL,due_date TEXT,lifecycle_state TEXT NOT NULL,version INTEGER NOT NULL,provenance_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,created_by TEXT NOT NULL,FOREIGN KEY(bid_id) REFERENCES bids(bid_id),UNIQUE(bid_id,issue_code))""",
            """CREATE TABLE IF NOT EXISTS contract_risk_sources(source_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,issue_id TEXT NOT NULL,source_type TEXT NOT NULL,target_id TEXT,source_title TEXT,issuer_role TEXT,source_date TEXT,locator TEXT NOT NULL,rationale TEXT,reviewed_at TEXT,expires_at TEXT,created_at TEXT NOT NULL,created_by TEXT NOT NULL,FOREIGN KEY(issue_id) REFERENCES contract_issues(issue_id))""",
            """CREATE TABLE IF NOT EXISTS contract_risk_links(link_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,issue_id TEXT NOT NULL,target_type TEXT NOT NULL,target_id TEXT NOT NULL,relation TEXT NOT NULL,created_at TEXT NOT NULL,created_by TEXT NOT NULL,FOREIGN KEY(issue_id) REFERENCES contract_issues(issue_id),UNIQUE(issue_id,target_type,target_id,relation))""",
            """CREATE TABLE IF NOT EXISTS contract_risk_assessments(assessment_id TEXT PRIMARY KEY,issue_id TEXT NOT NULL,bid_id TEXT NOT NULL,version_number INTEGER NOT NULL,category TEXT NOT NULL,customer_position TEXT NOT NULL,company_position TEXT,target_position TEXT,fallback_position TEXT,business_impact TEXT NOT NULL,affected_functions TEXT NOT NULL,disposition TEXT NOT NULL,likelihood TEXT NOT NULL,consequence TEXT NOT NULL,exposure_basis TEXT NOT NULL,minimum_decimal TEXT,most_likely_decimal TEXT,maximum_decimal TEXT,currency TEXT,rationale TEXT,escalation_owner TEXT,assessed_by TEXT NOT NULL,assessed_at TEXT NOT NULL,supersedes_assessment_id TEXT,provenance_json TEXT NOT NULL,created_at TEXT NOT NULL,FOREIGN KEY(issue_id) REFERENCES contract_issues(issue_id),UNIQUE(issue_id,version_number))""",
            """CREATE TABLE IF NOT EXISTS contract_risk_reviews(review_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,issue_id TEXT NOT NULL,assessment_id TEXT NOT NULL,decision TEXT NOT NULL,reviewer TEXT NOT NULL,rationale TEXT,reviewed_at TEXT NOT NULL,provenance_json TEXT NOT NULL,FOREIGN KEY(assessment_id) REFERENCES contract_risk_assessments(assessment_id))""",
            "CREATE TABLE IF NOT EXISTS contract_risk_schema_migrations(migration_id TEXT PRIMARY KEY,applied_at TEXT NOT NULL)",
            "CREATE TRIGGER IF NOT EXISTS contract_issue_no_delete BEFORE DELETE ON contract_issues BEGIN SELECT RAISE(ABORT,'contract issues cannot be deleted'); END",
            "CREATE TRIGGER IF NOT EXISTS contract_assessment_immutable BEFORE UPDATE ON contract_risk_assessments BEGIN SELECT RAISE(ABORT,'contract assessments are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS contract_assessment_no_delete BEFORE DELETE ON contract_risk_assessments BEGIN SELECT RAISE(ABORT,'contract assessments cannot be deleted'); END",
            "CREATE TRIGGER IF NOT EXISTS contract_review_immutable BEFORE UPDATE ON contract_risk_reviews BEGIN SELECT RAISE(ABORT,'contract reviews are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS contract_review_no_delete BEFORE DELETE ON contract_risk_reviews BEGIN SELECT RAISE(ABORT,'contract reviews cannot be deleted'); END",
        ]
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                for statement in sql:
                    c.execute(statement)
                c.execute(
                    "INSERT OR IGNORE INTO contract_risk_schema_migrations VALUES (?,?)",
                    (RISK_MIGRATION_ID, datetime.now(UTC).isoformat()),
                )
                c.commit()
            except Exception:
                c.rollback()
                raise

    def _audit(self, c: sqlite3.Connection, bid: str, actor: str, action: str, detail: str) -> None:
        c.execute(
            "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) VALUES(?,?,?,?,?,?)",
            (f"AUD-{uuid4().hex}", bid, actor, action, detail, datetime.now(UTC).isoformat()),
        )

    def create(self, v: ContractIssue, actor: str) -> None:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                c.execute(
                    "INSERT INTO contract_issues VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        v.issue_id,
                        v.bid_id,
                        v.issue_code.upper().replace(" ", "_"),
                        v.title,
                        v.summary,
                        v.owner,
                        v.materiality,
                        v.due_date.isoformat() if v.due_date else None,
                        v.lifecycle_state.value,
                        v.version,
                        v.provenance.model_dump_json(),
                        v.created_at.isoformat(),
                        v.updated_at.isoformat(),
                        v.created_by,
                    ),
                )
                self._audit(c, v.bid_id, actor, "contract_issue_created", v.issue_id)
                c.commit()
            except Exception:
                c.rollback()
                raise

    def add_source(self, v: RiskSource, actor: str) -> None:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                row = c.execute(
                    "SELECT bid_id,lifecycle_state FROM contract_issues WHERE issue_id=?",
                    (v.issue_id,),
                ).fetchone()
                if row is None or row["bid_id"] != v.bid_id or row["lifecycle_state"] != "DRAFT":
                    raise ValueError("source requires same-bid draft issue")
                c.execute(
                    "INSERT INTO contract_risk_sources VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        v.source_id,
                        v.bid_id,
                        v.issue_id,
                        v.source_type.value,
                        v.target_id,
                        v.source_title,
                        v.issuer_role,
                        v.source_date.isoformat() if v.source_date else None,
                        v.locator,
                        v.rationale,
                        v.reviewed_at.isoformat() if v.reviewed_at else None,
                        v.expires_at.isoformat() if v.expires_at else None,
                        v.created_at.isoformat(),
                        v.created_by,
                    ),
                )
                self._audit(c, v.bid_id, actor, "contract_source_created", v.source_id)
                c.commit()
            except Exception:
                c.rollback()
                raise

    def add_link(self, v: RiskLink, actor: str) -> None:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                row = c.execute(
                    "SELECT bid_id,lifecycle_state FROM contract_issues WHERE issue_id=?",
                    (v.issue_id,),
                ).fetchone()
                if row is None or row["bid_id"] != v.bid_id or row["lifecycle_state"] != "DRAFT":
                    raise ValueError("link requires same-bid draft issue")
                tables = {
                    "REQUIREMENT": ("requirements", "requirement_id"),
                    "SCOPE_ITEM": ("scope_interface_items", "scope_item_id"),
                    "INTERFACE": ("scope_interfaces", "interface_id"),
                    "SUPPLIER_REQUEST_ITEM": ("supplier_request_items", "request_item_id"),
                    "SUPPLIER_RESPONSE_VERSION": (
                        "supplier_response_versions",
                        "response_version_id",
                    ),
                    "DELIVERABLE_OBLIGATION": ("deliverable_items", "deliverable_id"),
                    "COMMERCIAL_ITEM": ("commercial_items", "commercial_item_id"),
                    "CONTROLLED_DOCUMENT_VERSION": ("document_versions", "document_version_id"),
                    "CONTRACT_ISSUE": ("contract_issues", "issue_id"),
                }
                table, col = tables[v.target_type.value]
                target = c.execute(
                    f"SELECT bid_id FROM {table} WHERE {col}=?", (v.target_id,)
                ).fetchone()
                if target is None or target["bid_id"] != v.bid_id:
                    raise ValueError("risk link target missing or cross-bid")
                if v.target_type.value == "CONTRACT_ISSUE" and v.target_id == v.issue_id:
                    raise ValueError("risk lineage cycle")
                c.execute(
                    "INSERT INTO contract_risk_links VALUES (?,?,?,?,?,?,?,?)",
                    (
                        v.link_id,
                        v.bid_id,
                        v.issue_id,
                        v.target_type.value,
                        v.target_id,
                        v.relation.value,
                        v.created_at.isoformat(),
                        v.created_by,
                    ),
                )
                self._audit(c, v.bid_id, actor, "contract_link_created", v.link_id)
                c.commit()
            except Exception:
                c.rollback()
                raise

    def activate(self, issue_id: str, expected: int, actor: str) -> None:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                row = c.execute(
                    "SELECT bid_id FROM contract_issues WHERE issue_id=? AND version=? AND lifecycle_state='DRAFT'",
                    (issue_id, expected),
                ).fetchone()
                if row is None:
                    raise ValueError("stale or non-draft issue")
                if (
                    c.execute(
                        "SELECT 1 FROM contract_risk_sources WHERE issue_id=?", (issue_id,)
                    ).fetchone()
                    is None
                ):
                    raise ValueError("issue requires source")
                if (
                    c.execute(
                        "SELECT 1 FROM contract_risk_assessments WHERE issue_id=?", (issue_id,)
                    ).fetchone()
                    is None
                ):
                    raise ValueError("issue requires assessment")
                c.execute(
                    "UPDATE contract_issues SET lifecycle_state='ACTIVE',version=version+1,updated_at=? WHERE issue_id=? AND version=?",
                    (datetime.now(UTC).isoformat(), issue_id, expected),
                )
                self._audit(c, row["bid_id"], actor, "contract_issue_activated", issue_id)
                c.commit()
            except Exception:
                c.rollback()
                raise

    def add_assessment(self, v: RiskAssessment, actor: str) -> None:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                row = c.execute(
                    "SELECT bid_id FROM contract_issues WHERE issue_id=?", (v.issue_id,)
                ).fetchone()
                if row is None or row["bid_id"] != v.bid_id:
                    raise ValueError("assessment identity mismatch")
                nxt = c.execute(
                    "SELECT COALESCE(MAX(version_number),0)+1 FROM contract_risk_assessments WHERE issue_id=?",
                    (v.issue_id,),
                ).fetchone()[0]
                if v.version_number != nxt:
                    raise ValueError("assessment version must be monotonic")
                c.execute(
                    "INSERT INTO contract_risk_assessments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        v.assessment_id,
                        v.issue_id,
                        v.bid_id,
                        v.version_number,
                        v.category.value,
                        v.customer_position,
                        v.company_position,
                        v.target_position,
                        v.fallback_position,
                        v.business_impact,
                        "|".join(v.affected_functions),
                        v.disposition.value,
                        v.likelihood.name,
                        v.consequence.name,
                        v.exposure_basis.value,
                        str(v.minimum) if v.minimum is not None else None,
                        str(v.most_likely) if v.most_likely is not None else None,
                        str(v.maximum) if v.maximum is not None else None,
                        v.currency,
                        v.rationale,
                        v.escalation_owner,
                        v.assessed_by,
                        v.assessed_at.isoformat(),
                        v.supersedes_assessment_id,
                        v.provenance.model_dump_json(),
                        v.created_at.isoformat(),
                    ),
                )
                self._audit(c, v.bid_id, actor, "contract_assessment_created", v.assessment_id)
                c.commit()
            except Exception:
                c.rollback()
                raise

    def add_review(self, v: RiskReview, actor: str) -> None:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                row = c.execute(
                    "SELECT bid_id,issue_id,assessed_by FROM contract_risk_assessments WHERE assessment_id=?",
                    (v.assessment_id,),
                ).fetchone()
                if row is None or row["bid_id"] != v.bid_id or row["issue_id"] != v.issue_id:
                    raise ValueError("review identity mismatch")
                if row["assessed_by"] == v.reviewer:
                    raise ValueError("assessment author cannot review")
                c.execute(
                    "INSERT INTO contract_risk_reviews VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        v.review_id,
                        v.bid_id,
                        v.issue_id,
                        v.assessment_id,
                        v.decision.value,
                        v.reviewer,
                        v.rationale,
                        v.reviewed_at.isoformat(),
                        v.provenance.model_dump_json(),
                    ),
                )
                self._audit(c, v.bid_id, actor, "contract_assessment_reviewed", v.assessment_id)
                c.commit()
            except Exception:
                c.rollback()
                raise

    def list(self, bid_id: str | None = None) -> builtin_list[dict[str, Any]]:
        q = "SELECT * FROM contract_issues"
        p: tuple[str, ...] = ()
        if bid_id:
            q += " WHERE bid_id=?"
            p = (bid_id,)
        with self._conn() as c:
            return [dict(r) for r in c.execute(q + " ORDER BY title,issue_id", p).fetchall()]

    def sources(self, i: str) -> builtin_list[dict[str, Any]]:
        with self._conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM contract_risk_sources WHERE issue_id=?", (i,)
                ).fetchall()
            ]

    def links(self, i: str) -> builtin_list[dict[str, Any]]:
        with self._conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM contract_risk_links WHERE issue_id=?", (i,)
                ).fetchall()
            ]

    def assessments(self, i: str) -> builtin_list[dict[str, Any]]:
        with self._conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM contract_risk_assessments WHERE issue_id=? ORDER BY version_number",
                    (i,),
                ).fetchall()
            ]

    def reviews(self, i: str) -> builtin_list[dict[str, Any]]:
        with self._conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM contract_risk_reviews WHERE issue_id=? ORDER BY reviewed_at",
                    (i,),
                ).fetchall()
            ]
