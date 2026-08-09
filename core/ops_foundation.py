"""OPS-01 role framework and deterministic operational taxonomy."""
# ruff: noqa: E501

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import uuid4

from core.database import Database

OPS_MIGRATION_ID = "ops_01_role_work_foundation_v1"


RESPONSIBILITY_DOMAINS = (
    "STRATEGIC_OPPORTUNITY",
    "EPC_PROJECT_PURSUIT",
    "SAM_ENABLEMENT",
    "CUSTOMER_SOLUTION",
    "SUPPLIER_COORDINATION",
    "QUOTATION_COMMERCIAL",
    "CROSS_FUNCTIONAL",
    "RISK_ASSURANCE",
    "ACCOUNT_INTELLIGENCE",
    "OPERATIONAL_SUPPORT",
    "MANAGEMENT_INTELLIGENCE",
    "PROCESS_KNOWLEDGE",
    "ROLE_DEVELOPMENT",
)
WORK_CATEGORIES = (
    "OPPORTUNITY_DEVELOPMENT",
    "SAM_SUPPORT",
    "CUSTOMER_REQUEST",
    "CUSTOMER_MEETING",
    "SUPPLIER_REQUEST",
    "PRODUCT_TECHNICAL",
    "QUOTATION_PRICING",
    "COMMERCIAL_REVIEW",
    "PROJECT_COORDINATION",
    "OPERATIONAL_ISSUE",
    "MANAGEMENT_REQUEST",
    "REPORT_PRESENTATION",
    "PROCESS_IMPROVEMENT",
    "KNOWLEDGE_RESEARCH",
    "TRAINING_DEVELOPMENT",
    "STRATEGIC_INITIATIVE",
    "ADMINISTRATIVE",
    "OTHER",
)


class OpsFoundationRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._migrate()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _migrate(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS ops_role_profiles(profile_id TEXT PRIMARY KEY,version_number INTEGER NOT NULL,title TEXT,organization TEXT,mission TEXT NOT NULL,boundaries TEXT NOT NULL,coordination TEXT NOT NULL,outcomes TEXT NOT NULL,cadence TEXT NOT NULL,domains_json TEXT NOT NULL,effective_from TEXT NOT NULL,effective_until TEXT,state TEXT NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL,parent_profile_id TEXT,version_token TEXT NOT NULL,provenance_json TEXT,UNIQUE(version_number));
                CREATE TABLE IF NOT EXISTS ops_work_context_links(link_id TEXT PRIMARY KEY,work_item_id TEXT NOT NULL,context_kind TEXT NOT NULL,context_id TEXT,primary_link INTEGER NOT NULL,created_at TEXT NOT NULL,UNIQUE(work_item_id,context_kind,context_id));
                CREATE TABLE IF NOT EXISTS ops_schema_migrations(migration_id TEXT PRIMARY KEY,applied_at TEXT NOT NULL);
                DROP TRIGGER IF EXISTS ops_role_profile_immutable;
                CREATE TRIGGER IF NOT EXISTS ops_role_profile_immutable BEFORE UPDATE ON ops_role_profiles WHEN OLD.state='PUBLISHED' AND NEW.state='PUBLISHED' AND (OLD.mission<>NEW.mission OR OLD.boundaries<>NEW.boundaries OR OLD.domains_json<>NEW.domains_json OR OLD.version_number<>NEW.version_number) BEGIN SELECT RAISE(ABORT,'published role profiles are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS ops_work_context_no_delete BEFORE DELETE ON ops_work_context_links BEGIN SELECT RAISE(ABORT,'work context links cannot be deleted'); END;
            """)
            conn.execute(
                "INSERT OR IGNORE INTO ops_schema_migrations VALUES(?,?)",
                (OPS_MIGRATION_ID, datetime.now(UTC).isoformat()),
            )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(ops_role_profiles)").fetchall()
            }
            for name, definition in (
                ("parent_profile_id", "TEXT"),
                ("version_token", "TEXT NOT NULL DEFAULT ''"),
                ("provenance_json", "TEXT"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE ops_role_profiles ADD COLUMN {name} {definition}")
            conn.commit()

    def create_profile(self, data: dict[str, Any]) -> str:
        profile_id = str(data.get("profile_id") or f"RPF-{uuid4().hex}")
        if data.get("state", "DRAFT") not in {"DRAFT", "PUBLISHED", "RETIRED"}:
            raise ValueError("invalid role profile state")
        domains = data.get("domains", [])
        unknown = set(domains) - set(RESPONSIBILITY_DOMAINS)
        if unknown:
            raise ValueError("unknown responsibility domain")
        with self._conn() as conn:
            if data.get("state") == "PUBLISHED":
                overlap = conn.execute(
                    "SELECT 1 FROM ops_role_profiles WHERE state='PUBLISHED' AND effective_from <= ? AND (effective_until IS NULL OR effective_until >= ?)",
                    (data.get("effective_until") or "9999-12-31", data["effective_from"]),
                ).fetchone()
                if overlap:
                    raise ValueError("published role profile effective window overlaps")
            conn.execute(
                "INSERT INTO ops_role_profiles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    profile_id,
                    data["version_number"],
                    data.get("title"),
                    data.get("organization"),
                    data["mission"],
                    data.get("boundaries", ""),
                    data.get("coordination", ""),
                    data.get("outcomes", ""),
                    data.get("cadence", ""),
                    json.dumps(data.get("domains", []), sort_keys=True),
                    data["effective_from"],
                    data.get("effective_until"),
                    data.get("state", "DRAFT"),
                    data["created_by"],
                    data["created_at"],
                    data.get("parent_profile_id"),
                    str(data.get("version_token") or uuid4().hex),
                    json.dumps(data.get("provenance", {}), sort_keys=True),
                ),
            )
            conn.commit()
        return profile_id

    def publish_profile(self, profile_id: str) -> None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM ops_role_profiles WHERE profile_id=?", (profile_id,)
            ).fetchone()
            if row is None:
                raise ValueError("role profile not found")
            overlap = conn.execute(
                "SELECT 1 FROM ops_role_profiles WHERE state='PUBLISHED' AND profile_id<>? AND effective_from <= COALESCE(?, '9999-12-31') AND (effective_until IS NULL OR effective_until >= ?)",
                (profile_id, row["effective_until"], row["effective_from"]),
            ).fetchone()
            if overlap:
                raise ValueError("published role profile effective window overlaps")
            conn.execute(
                "UPDATE ops_role_profiles SET state='PUBLISHED' WHERE profile_id=?", (profile_id,)
            )
            conn.commit()

    def retire_profile(self, profile_id: str) -> None:
        with self._conn() as conn:
            if (
                conn.execute(
                    "SELECT 1 FROM ops_role_profiles WHERE profile_id=?", (profile_id,)
                ).fetchone()
                is None
            ):
                raise ValueError("role profile not found")
            conn.execute(
                "UPDATE ops_role_profiles SET state='RETIRED' WHERE profile_id=?", (profile_id,)
            )
            conn.commit()

    def revise_profile(self, profile_id: str, changes: dict[str, Any]) -> str:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM ops_role_profiles WHERE profile_id=?", (profile_id,)
            ).fetchone()
        if row is None:
            raise ValueError("role profile not found")
        data = dict(row)
        data.update(changes)
        data["profile_id"] = None
        data["version_number"] = int(row["version_number"]) + 1
        data["parent_profile_id"] = profile_id
        data["domains"] = json.loads(str(row["domains_json"]))
        data["state"] = "DRAFT"
        return self.create_profile(data)

    def effective_profile(self, when: date | None = None) -> dict[str, Any] | None:
        target = (when or date.today()).isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM ops_role_profiles WHERE state='PUBLISHED' AND effective_from <= ? AND (effective_until IS NULL OR effective_until >= ?) ORDER BY version_number DESC LIMIT 1",
                (target, target),
            ).fetchone()
        return dict(row) if row is not None else None

    def add_context_link(
        self, work_item_id: str, context_kind: str, context_id: str | None, primary: bool = False
    ) -> str:
        allowed = {"ACCOUNT", "PROJECT", "OPPORTUNITY", "INITIATIVE", "BID_CONTRACT_CASE"}
        if context_kind not in allowed:
            raise ValueError("invalid context kind")
        link_id = f"WCL-{uuid4().hex}"
        with self._conn() as conn:
            if primary:
                conn.execute(
                    "UPDATE ops_work_context_links SET primary_link=0 WHERE work_item_id=?",
                    (work_item_id,),
                )
            conn.execute(
                "INSERT INTO ops_work_context_links VALUES(?,?,?,?,?,?)",
                (
                    link_id,
                    work_item_id,
                    context_kind,
                    context_id,
                    int(primary),
                    datetime.now(UTC).isoformat(),
                ),
            )
            conn.commit()
        return link_id

    def profiles(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM ops_role_profiles ORDER BY version_number"
                ).fetchall()
            ]

    def metrics(self) -> dict[str, int]:
        with self._conn() as conn:
            active = conn.execute(
                "SELECT COUNT(*) FROM work_items WHERE status IN ('OPEN','IN_PROGRESS','WAITING','BLOCKED')"
            ).fetchone()[0]
            return {
                "profiles_total": conn.execute("SELECT COUNT(*) FROM ops_role_profiles").fetchone()[
                    0
                ],
                "published_profiles": conn.execute(
                    "SELECT COUNT(*) FROM ops_role_profiles WHERE state='PUBLISHED'"
                ).fetchone()[0],
                "active_work": active,
                "unassigned_work": conn.execute(
                    "SELECT COUNT(*) FROM work_items WHERE bid_id IS NULL"
                ).fetchone()[0],
                "blocked": conn.execute(
                    "SELECT COUNT(*) FROM work_items WHERE status='BLOCKED'"
                ).fetchone()[0],
                "waiting": conn.execute(
                    "SELECT COUNT(*) FROM work_items WHERE status='WAITING'"
                ).fetchone()[0],
                "other_category": conn.execute(
                    "SELECT COUNT(*) FROM work_items WHERE category='OTHER'"
                ).fetchone()[0],
            }
