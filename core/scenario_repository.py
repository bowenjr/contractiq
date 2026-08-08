"""SQLite persistence for immutable TASK-16 scenarios and baseline history."""
# DDL strings are intentionally kept together for migration readability.
# ruff: noqa: E501

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from core.approval_repository import ApprovalRepository
from core.commercial_scenarios import (
    BaselineSelection,
    ScenarioFamily,
    ScenarioResult,
    ScenarioReview,
    ScenarioVersion,
)
from core.database import Database

SCENARIO_MIGRATION_ID = "task_16_commercial_scenarios_v1"


class ScenarioRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        ApprovalRepository(db)
        self._migrate()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _migrate(self) -> None:
        ddl = [
            "CREATE TABLE IF NOT EXISTS scenario_families(family_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,code TEXT NOT NULL,purpose TEXT NOT NULL,title TEXT NOT NULL,owner TEXT NOT NULL,intent TEXT NOT NULL,lifecycle TEXT NOT NULL,materiality TEXT NOT NULL,decision_date TEXT,version INTEGER NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(bid_id,code))",
            "CREATE TABLE IF NOT EXISTS scenario_versions(scenario_version_id TEXT PRIMARY KEY,family_id TEXT NOT NULL,bid_id TEXT NOT NULL,version_number INTEGER NOT NULL,state TEXT NOT NULL,presentation_currency TEXT NOT NULL,decimal_scale INTEGER NOT NULL,day_count_convention TEXT NOT NULL,lines_json TEXT NOT NULL,assumptions_json TEXT NOT NULL,cash_events_json TEXT NOT NULL,source_links_json TEXT NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL,fingerprint TEXT NOT NULL,UNIQUE(family_id,version_number))",
            "CREATE TABLE IF NOT EXISTS scenario_results(scenario_version_id TEXT PRIMARY KEY,result_json TEXT NOT NULL,fingerprint TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS scenario_reviews(review_id TEXT PRIMARY KEY,scenario_version_id TEXT NOT NULL,decision TEXT NOT NULL,reviewer TEXT NOT NULL,reviewed_at TEXT NOT NULL,rationale TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS scenario_comparisons(comparison_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,base_version_id TEXT NOT NULL,compared_version_id TEXT NOT NULL,revenue_delta TEXT NOT NULL,cost_delta TEXT NOT NULL,gross_profit_delta TEXT NOT NULL,fingerprint TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS scenario_baselines(selection_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,scenario_version_id TEXT NOT NULL,selected_by TEXT NOT NULL,selected_at TEXT NOT NULL,rationale TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS scenario_schema_migrations(migration_id TEXT PRIMARY KEY,applied_at TEXT NOT NULL)",
            "CREATE TRIGGER IF NOT EXISTS scenario_version_immutable BEFORE UPDATE ON scenario_versions BEGIN SELECT RAISE(ABORT,'scenario versions are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS scenario_version_no_delete BEFORE DELETE ON scenario_versions BEGIN SELECT RAISE(ABORT,'scenario versions cannot be deleted'); END",
            "CREATE TRIGGER IF NOT EXISTS scenario_result_immutable BEFORE UPDATE ON scenario_results BEGIN SELECT RAISE(ABORT,'scenario results are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS scenario_result_no_delete BEFORE DELETE ON scenario_results BEGIN SELECT RAISE(ABORT,'scenario results cannot be deleted'); END",
        ]
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in ddl:
                    conn.execute(statement)
                conn.execute(
                    "INSERT OR IGNORE INTO scenario_schema_migrations VALUES(?,?)",
                    (SCENARIO_MIGRATION_ID, datetime.now(UTC).isoformat()),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _audit(
        self, conn: sqlite3.Connection, bid_id: str, actor: str, action: str, detail: str
    ) -> None:
        conn.execute(
            "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) VALUES(?,?,?,?,?,?)",
            (f"AUD-{uuid4().hex}", bid_id, actor, action, detail, datetime.now(UTC).isoformat()),
        )

    def create_family(self, value: ScenarioFamily, actor: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO scenario_families VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (*value.model_dump(mode="json").values(),),
            )
            self._audit(conn, value.bid_id, actor, "scenario_family_created", value.family_id)
            conn.commit()

    def add_version(self, value: ScenarioVersion, result: ScenarioResult, actor: str) -> None:
        with self._conn() as conn:
            next_version = conn.execute(
                "SELECT COALESCE(MAX(version_number),0)+1 FROM scenario_versions WHERE family_id=?",
                (value.family_id,),
            ).fetchone()[0]
            if value.version_number != next_version:
                raise ValueError("scenario version must be monotonic")
            conn.execute(
                "INSERT INTO scenario_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    value.scenario_version_id,
                    value.family_id,
                    value.bid_id,
                    value.version_number,
                    value.state.value,
                    value.presentation_currency,
                    value.decimal_scale,
                    value.day_count_convention,
                    json.dumps(
                        [line.model_dump(mode="json") for line in value.lines], sort_keys=True
                    ),
                    json.dumps(
                        [item.model_dump(mode="json") for item in value.assumptions], sort_keys=True
                    ),
                    json.dumps(
                        [item.model_dump(mode="json") for item in value.cash_events], sort_keys=True
                    ),
                    json.dumps(
                        [item.model_dump(mode="json") for item in value.source_links],
                        sort_keys=True,
                    ),
                    value.created_by,
                    value.created_at.isoformat(),
                    value.fingerprint,
                ),
            )
            conn.execute(
                "INSERT INTO scenario_results VALUES(?,?,?)",
                (value.scenario_version_id, result.model_dump_json(), result.fingerprint),
            )
            self._audit(
                conn, value.bid_id, actor, "scenario_version_calculated", value.scenario_version_id
            )
            conn.commit()

    def add_review(self, value: ScenarioReview, bid_id: str, actor: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO scenario_reviews VALUES(?,?,?,?,?,?)",
                (
                    value.review_id,
                    value.scenario_version_id,
                    value.decision.value,
                    value.reviewer,
                    value.reviewed_at.isoformat(),
                    value.rationale,
                ),
            )
            self._audit(conn, bid_id, actor, "scenario_review_recorded", value.review_id)
            conn.commit()

    def select_baseline(self, value: BaselineSelection) -> None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM scenario_reviews WHERE scenario_version_id=? AND decision='ACCEPTED'",
                (value.scenario_version_id,),
            ).fetchone()
            if row is None:
                raise ValueError("baseline requires independent calculation acceptance")
            conn.execute(
                "INSERT INTO scenario_baselines VALUES(?,?,?,?,?,?)",
                (
                    value.selection_id,
                    value.bid_id,
                    value.scenario_version_id,
                    value.selected_by,
                    value.selected_at.isoformat(),
                    value.rationale,
                ),
            )
            self._audit(
                conn,
                value.bid_id,
                value.selected_by,
                "scenario_baseline_selected",
                value.scenario_version_id,
            )
            conn.commit()

    def families(self, bid_id: str | None = None) -> list[dict[str, Any]]:
        clause = " WHERE bid_id=?" if bid_id else ""
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM scenario_families" + clause, (bid_id,) if bid_id else ()
                ).fetchall()
            ]

    def versions(self, family_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM scenario_versions WHERE family_id=? ORDER BY version_number",
                    (family_id,),
                ).fetchall()
            ]

    def reviews(self, version_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM scenario_reviews WHERE scenario_version_id=? ORDER BY reviewed_at",
                    (version_id,),
                ).fetchall()
            ]

    def baselines(self, bid_id: str | None = None) -> list[dict[str, Any]]:
        clause = " WHERE bid_id=?" if bid_id else ""
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM scenario_baselines" + clause, (bid_id,) if bid_id else ()
                ).fetchall()
            ]
