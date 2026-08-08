"""Transactional SQLite persistence for TASK-15."""
# SQL DDL strings are intentionally kept together for migration readability.
# ruff: noqa: E501

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from core.approval_authority import (
    ApprovalEvent,
    AuthorityPolicy,
    DecisionCase,
    DecisionPackage,
    RoleAssignment,
    RouteCycle,
    SubjectLink,
)
from core.database import Database

APPROVAL_MIGRATION_ID = "task_15_approval_authority_v1"


class ApprovalRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._migrate()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _migrate(self) -> None:
        sql = [
            "CREATE TABLE IF NOT EXISTS audit_log(entry_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,actor TEXT NOT NULL,action TEXT NOT NULL,detail TEXT NOT NULL,timestamp TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS authority_policies(policy_id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL,scope TEXT NOT NULL,version_number INTEGER NOT NULL,effective_from TEXT NOT NULL,effective_until TEXT,lifecycle_state TEXT NOT NULL,roles_json TEXT NOT NULL,rules_json TEXT NOT NULL,stages_json TEXT NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL,provenance_json TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS authority_assignments(assignment_id TEXT PRIMARY KEY,policy_id TEXT NOT NULL,role_code TEXT NOT NULL,actor_id TEXT NOT NULL,effective_from TEXT NOT NULL,effective_until TEXT,assigned_by TEXT NOT NULL,rationale TEXT NOT NULL,created_at TEXT NOT NULL,provenance_json TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS decision_cases(case_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,case_code TEXT NOT NULL,decision_type TEXT NOT NULL,title TEXT NOT NULL,owner TEXT NOT NULL,lifecycle_state TEXT NOT NULL,materiality TEXT NOT NULL,due_date TEXT,version INTEGER NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL,provenance_json TEXT NOT NULL,UNIQUE(bid_id,case_code))",
            "CREATE TABLE IF NOT EXISTS decision_subject_links(subject_link_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,case_id TEXT NOT NULL,subject_type TEXT NOT NULL,subject_id TEXT NOT NULL,relation TEXT NOT NULL,version_id TEXT,created_at TEXT NOT NULL,created_by TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS decision_packages(package_id TEXT PRIMARY KEY,case_id TEXT NOT NULL,bid_id TEXT NOT NULL,version_number INTEGER NOT NULL,issue TEXT NOT NULL,options_json TEXT NOT NULL,effects_json TEXT NOT NULL,recommendation TEXT NOT NULL,requested_outcome TEXT NOT NULL,residual_risk TEXT NOT NULL,deadline TEXT NOT NULL,approval_valid_until TEXT,subject_links_json TEXT NOT NULL,author TEXT NOT NULL,created_at TEXT NOT NULL,supersedes_package_id TEXT,fingerprint TEXT NOT NULL,UNIQUE(case_id,version_number))",
            "CREATE TABLE IF NOT EXISTS approval_routes(route_id TEXT PRIMARY KEY,case_id TEXT NOT NULL,bid_id TEXT NOT NULL,package_id TEXT NOT NULL,policy_id TEXT NOT NULL,state TEXT NOT NULL,matched_rules_json TEXT NOT NULL,requirements_json TEXT NOT NULL,requestor TEXT NOT NULL,submitted_at TEXT NOT NULL,approval_valid_until TEXT,version INTEGER NOT NULL)",
            "CREATE TABLE IF NOT EXISTS approval_events(event_id TEXT PRIMARY KEY,route_id TEXT NOT NULL,requirement_id TEXT NOT NULL,package_id TEXT NOT NULL,bid_id TEXT NOT NULL,actor_id TEXT NOT NULL,decision TEXT NOT NULL,rationale TEXT,created_at TEXT NOT NULL,UNIQUE(route_id,requirement_id,actor_id))",
            "CREATE TABLE IF NOT EXISTS approval_schema_migrations(migration_id TEXT PRIMARY KEY,applied_at TEXT NOT NULL)",
            "CREATE TRIGGER IF NOT EXISTS decision_case_no_delete BEFORE DELETE ON decision_cases BEGIN SELECT RAISE(ABORT,'decision cases cannot be deleted'); END",
            "CREATE TRIGGER IF NOT EXISTS decision_package_immutable BEFORE UPDATE ON decision_packages BEGIN SELECT RAISE(ABORT,'decision packages are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS decision_package_no_delete BEFORE DELETE ON decision_packages BEGIN SELECT RAISE(ABORT,'decision packages cannot be deleted'); END",
            "CREATE TRIGGER IF NOT EXISTS approval_event_immutable BEFORE UPDATE ON approval_events BEGIN SELECT RAISE(ABORT,'approval events are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS approval_event_no_delete BEFORE DELETE ON approval_events BEGIN SELECT RAISE(ABORT,'approval events cannot be deleted'); END",
        ]
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                for s in sql:
                    c.execute(s)
                c.execute(
                    "INSERT OR IGNORE INTO approval_schema_migrations VALUES (?,?)",
                    (APPROVAL_MIGRATION_ID, datetime.now(UTC).isoformat()),
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

    def create_policy(self, v: AuthorityPolicy, actor: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO authority_policies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    v.policy_id,
                    v.name,
                    v.description,
                    v.scope,
                    v.version_number,
                    v.effective_from.isoformat(),
                    v.effective_until.isoformat() if v.effective_until else None,
                    v.lifecycle_state.value,
                    json.dumps(v.roles),
                    json.dumps(v.rules, sort_keys=True),
                    json.dumps(v.stages, sort_keys=True),
                    v.created_by,
                    v.created_at.isoformat(),
                    v.provenance.model_dump_json(),
                ),
            )
            c.commit()

    def policy_models(self) -> list[AuthorityPolicy]:
        values: list[AuthorityPolicy] = []
        for row in self.policies():
            values.append(
                AuthorityPolicy(
                    policy_id=row["policy_id"],
                    name=row["name"],
                    description=row["description"],
                    scope=row["scope"],
                    version_number=row["version_number"],
                    effective_from=datetime.fromisoformat(row["effective_from"]),
                    effective_until=(
                        datetime.fromisoformat(row["effective_until"])
                        if row["effective_until"]
                        else None
                    ),
                    lifecycle_state=row["lifecycle_state"],
                    roles=tuple(json.loads(row["roles_json"])),
                    rules=tuple(json.loads(row["rules_json"])),
                    stages=tuple(json.loads(row["stages_json"])),
                    created_by=row["created_by"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    provenance=json.loads(row["provenance_json"]),
                )
            )
        return values

    def publish_policy(self, policy_id: str, actor: str) -> None:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                row = c.execute(
                    "SELECT * FROM authority_policies WHERE policy_id=?", (policy_id,)
                ).fetchone()
                if row is None or row["lifecycle_state"] != "DRAFT":
                    raise ValueError("policy is not draft")
                if not row["roles_json"] or not row["rules_json"] or not row["stages_json"]:
                    raise ValueError("policy requires roles, rules, and stages")
                c.execute(
                    "UPDATE authority_policies SET lifecycle_state='PUBLISHED' WHERE policy_id=?",
                    (policy_id,),
                )
                self._audit(c, "", actor, "policy_published", policy_id)
                c.commit()
            except Exception:
                c.rollback()
                raise

    def assign(self, v: RoleAssignment, actor: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO authority_assignments VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    v.assignment_id,
                    v.policy_id,
                    v.role_code,
                    v.actor_id,
                    v.effective_from.isoformat(),
                    v.effective_until.isoformat() if v.effective_until else None,
                    v.assigned_by,
                    v.rationale,
                    v.created_at.isoformat(),
                    v.provenance.model_dump_json(),
                ),
            )
            c.commit()

    def create_case(self, v: DecisionCase, actor: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO decision_cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    v.case_id,
                    v.bid_id,
                    v.case_code,
                    v.decision_type.value,
                    v.title,
                    v.owner,
                    v.lifecycle_state.value,
                    v.materiality,
                    v.due_date.isoformat() if v.due_date else None,
                    v.version,
                    v.created_by,
                    v.created_at.isoformat(),
                    v.provenance.model_dump_json(),
                ),
            )
            c.commit()

    def add_subject(self, v: SubjectLink, actor: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO decision_subject_links VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    v.subject_link_id,
                    v.bid_id,
                    v.case_id,
                    v.subject_type.value,
                    v.subject_id,
                    v.relation,
                    v.version_id,
                    v.created_at.isoformat(),
                    v.created_by,
                ),
            )
            c.commit()

    def add_package(self, v: DecisionPackage, actor: str) -> None:
        with self._conn() as c:
            nxt = c.execute(
                "SELECT COALESCE(MAX(version_number),0)+1 FROM decision_packages WHERE case_id=?",
                (v.case_id,),
            ).fetchone()[0]
            if v.version_number != nxt:
                raise ValueError("package version must be monotonic")
            c.execute(
                "INSERT INTO decision_packages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    v.package_id,
                    v.case_id,
                    v.bid_id,
                    v.version_number,
                    v.issue,
                    str(v.options),
                    str(v.effects),
                    v.recommendation,
                    v.requested_outcome,
                    v.residual_risk,
                    v.deadline.isoformat(),
                    v.approval_valid_until.isoformat() if v.approval_valid_until else None,
                    str(v.subject_links),
                    v.author,
                    v.created_at.isoformat(),
                    v.supersedes_package_id,
                    v.fingerprint,
                ),
            )
            c.commit()

    def create_route(self, v: RouteCycle, actor: str) -> None:
        with self._conn() as c:
            policy = c.execute(
                "SELECT lifecycle_state, effective_from, effective_until FROM authority_policies WHERE policy_id=?",
                (v.policy_id,),
            ).fetchone()
            if policy is None or policy["lifecycle_state"] != "PUBLISHED":
                raise ValueError("route requires a published policy")
            c.execute(
                "INSERT INTO approval_routes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    v.route_id,
                    v.case_id,
                    v.bid_id,
                    v.package_id,
                    v.policy_id,
                    v.state.value,
                    json.dumps(v.matched_rule_ids),
                    json.dumps(
                        [requirement.model_dump(mode="json") for requirement in v.requirements]
                    ),
                    v.requestor,
                    v.submitted_at.isoformat(),
                    v.approval_valid_until.isoformat() if v.approval_valid_until else None,
                    v.version,
                ),
            )
            c.commit()

    def event(self, v: ApprovalEvent, actor: str) -> None:
        with self._conn() as c:
            route = c.execute(
                "SELECT requestor, policy_id, state FROM approval_routes WHERE route_id=?",
                (v.route_id,),
            ).fetchone()
            if route is None:
                raise ValueError("route not found")
            if route["state"] in {"WITHDRAWN", "EXPIRED", "SUPERSEDED", "REVOKED"}:
                raise ValueError("route is not active")
            if v.actor_id == route["requestor"]:
                raise ValueError("separation of duties forbids self approval")
            requirement_rows = c.execute(
                "SELECT requirements_json FROM approval_routes WHERE route_id=?", (v.route_id,)
            ).fetchone()
            requirements = json.loads(requirement_rows["requirements_json"])
            matched = next(
                (item for item in requirements if item.get("requirement_id") == v.requirement_id),
                None,
            )
            if matched is None:
                raise ValueError("route requirement not found")
            assigned = c.execute(
                "SELECT 1 FROM authority_assignments WHERE policy_id=? AND role_code=? "
                "AND actor_id=? AND effective_from<=? AND (effective_until IS NULL OR effective_until>?)",
                (
                    route["policy_id"],
                    matched["role_code"],
                    v.actor_id,
                    v.created_at.isoformat(),
                    v.created_at.isoformat(),
                ),
            ).fetchone()
            if assigned is None:
                raise ValueError("actor has no effective assignment for this role")
            c.execute(
                "INSERT INTO approval_events VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    v.event_id,
                    v.route_id,
                    v.requirement_id,
                    v.package_id,
                    v.bid_id,
                    v.actor_id,
                    v.decision.value,
                    v.rationale,
                    v.created_at.isoformat(),
                ),
            )
            c.commit()

    def policies(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM authority_policies ORDER BY version_number"
                ).fetchall()
            ]

    def cases(self, bid_id: str | None = None) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM decision_cases" + (" WHERE bid_id=?" if bid_id else ""),
                    (bid_id,) if bid_id else (),
                ).fetchall()
            ]

    def packages(self, case_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM decision_packages WHERE case_id=? ORDER BY version_number",
                    (case_id,),
                ).fetchall()
            ]

    def routes(self, bid_id: str | None = None) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM approval_routes" + (" WHERE bid_id=?" if bid_id else ""),
                    (bid_id,) if bid_id else (),
                ).fetchall()
            ]

    def events(self, route_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM approval_events WHERE route_id=?", (route_id,)
                ).fetchall()
            ]
