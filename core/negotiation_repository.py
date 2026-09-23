"""Transactional persistence for TASK-17 immutable negotiation evidence."""
# DDL and compact SQL statements are intentionally kept together.
# ruff: noqa: E501

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from core.approval_repository import ApprovalRepository
from core.database import Database
from core.negotiation import (
    Concession,
    ConditionalTrade,
    Mandate,
    NegotiationMovement,
    NegotiationPlan,
    PlanLifecycle,
    PlanVersion,
    TradeState,
    ValueState,
    validate_trade_transition,
)

NEGOTIATION_MIGRATION_ID = "task_17_negotiation_concessions_v1"


class NegotiationRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        ApprovalRepository(db)
        self._migrate()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _migrate(self) -> None:
        ddl = [
            "CREATE TABLE IF NOT EXISTS negotiation_plans(plan_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,code TEXT NOT NULL,applicability TEXT NOT NULL,title TEXT NOT NULL,owner TEXT NOT NULL,lifecycle TEXT NOT NULL,version INTEGER NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(bid_id,code))",
            "CREATE TABLE IF NOT EXISTS negotiation_plan_versions(plan_version_id TEXT PRIMARY KEY,plan_id TEXT NOT NULL,bid_id TEXT NOT NULL,version_number INTEGER NOT NULL,issues_json TEXT NOT NULL,source_links_json TEXT NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL,fingerprint TEXT NOT NULL,UNIQUE(plan_id,version_number))",
            "CREATE TABLE IF NOT EXISTS negotiation_mandates(mandate_id TEXT PRIMARY KEY,plan_version_id TEXT NOT NULL,bid_id TEXT NOT NULL,actors_json TEXT NOT NULL,actions_json TEXT NOT NULL,issues_json TEXT NOT NULL,limit_amount TEXT,currency TEXT,starts_at TEXT NOT NULL,ends_at TEXT NOT NULL,route_id TEXT,state TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS negotiation_trades(trade_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,plan_version_id TEXT NOT NULL,give TEXT NOT NULL,get TEXT NOT NULL,required_value TEXT NOT NULL,value_state TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL,trade_lineage_id TEXT NOT NULL,state_version INTEGER NOT NULL DEFAULT 1)",
            "CREATE TABLE IF NOT EXISTS negotiation_movements(event_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,movement_type TEXT NOT NULL,issue_code TEXT NOT NULL,actor TEXT NOT NULL,text TEXT NOT NULL,trade_id TEXT,authority_id TEXT,created_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS negotiation_concessions(concession_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,issue_code TEXT NOT NULL,version_number INTEGER NOT NULL,amount TEXT NOT NULL,currency TEXT NOT NULL,unit TEXT NOT NULL,basis TEXT NOT NULL,state TEXT NOT NULL,mandate_id TEXT,authority_event_id TEXT,created_at TEXT NOT NULL,UNIQUE(bid_id,issue_code,version_number))",
            "CREATE TABLE IF NOT EXISTS negotiation_schema_migrations(migration_id TEXT PRIMARY KEY,applied_at TEXT NOT NULL)",
            "CREATE TRIGGER IF NOT EXISTS negotiation_plan_version_immutable BEFORE UPDATE ON negotiation_plan_versions BEGIN SELECT RAISE(ABORT,'plan versions are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS negotiation_plan_version_no_delete BEFORE DELETE ON negotiation_plan_versions BEGIN SELECT RAISE(ABORT,'plan versions cannot be deleted'); END",
            "CREATE TRIGGER IF NOT EXISTS negotiation_movement_immutable BEFORE UPDATE ON negotiation_movements BEGIN SELECT RAISE(ABORT,'movements are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS negotiation_movement_no_delete BEFORE DELETE ON negotiation_movements BEGIN SELECT RAISE(ABORT,'movements cannot be deleted'); END",
            "CREATE TRIGGER IF NOT EXISTS negotiation_concession_immutable BEFORE UPDATE ON negotiation_concessions BEGIN SELECT RAISE(ABORT,'concessions are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS negotiation_concession_no_delete BEFORE DELETE ON negotiation_concessions BEGIN SELECT RAISE(ABORT,'concessions cannot be deleted'); END",
        ]
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in ddl:
                    conn.execute(statement)
                self._ensure_trade_lineage_columns(conn)
                conn.execute(
                    "INSERT OR IGNORE INTO negotiation_schema_migrations VALUES(?,?)",
                    (NEGOTIATION_MIGRATION_ID, datetime.now(UTC).isoformat()),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _ensure_trade_lineage_columns(self, conn: sqlite3.Connection) -> None:
        """Add trade-progression columns to databases created before they existed."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(negotiation_trades)")}
        if "trade_lineage_id" not in existing:
            conn.execute(
                "ALTER TABLE negotiation_trades ADD COLUMN trade_lineage_id TEXT NOT NULL DEFAULT ''"
            )
            conn.execute(
                "UPDATE negotiation_trades SET trade_lineage_id=trade_id WHERE trade_lineage_id=''"
            )
        if "state_version" not in existing:
            conn.execute(
                "ALTER TABLE negotiation_trades ADD COLUMN state_version INTEGER NOT NULL DEFAULT 1"
            )

    def _audit(
        self, conn: sqlite3.Connection, bid_id: str, actor: str, action: str, detail: str
    ) -> None:
        conn.execute(
            "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) VALUES(?,?,?,?,?,?)",
            (f"AUD-{uuid4().hex}", bid_id, actor, action, detail, datetime.now(UTC).isoformat()),
        )

    def create_plan(self, value: NegotiationPlan, actor: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO negotiation_plans VALUES(?,?,?,?,?,?,?,?,?,?)",
                (*value.model_dump(mode="json").values(),),
            )
            self._audit(conn, value.bid_id, actor, "negotiation_plan_created", value.plan_id)
            conn.commit()

    def add_version(self, value: PlanVersion, actor: str) -> None:
        with self._conn() as conn:
            expected = conn.execute(
                "SELECT COALESCE(MAX(version_number),0)+1 FROM negotiation_plan_versions WHERE plan_id=?",
                (value.plan_id,),
            ).fetchone()[0]
            if value.version_number != expected:
                raise ValueError("plan version must be monotonic")
            conn.execute(
                "INSERT INTO negotiation_plan_versions VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    value.plan_version_id,
                    value.plan_id,
                    value.bid_id,
                    value.version_number,
                    json.dumps(
                        [issue.model_dump(mode="json") for issue in value.issues], sort_keys=True
                    ),
                    json.dumps(value.source_links, sort_keys=True),
                    value.created_by,
                    value.created_at.isoformat(),
                    value.fingerprint,
                ),
            )
            self._audit(
                conn, value.bid_id, actor, "negotiation_plan_version_created", value.plan_version_id
            )
            conn.commit()

    def add_mandate(self, value: Mandate, actor: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO negotiation_mandates VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    value.mandate_id,
                    value.plan_version_id,
                    value.bid_id,
                    json.dumps(value.authorized_actors),
                    json.dumps(value.allowed_actions),
                    json.dumps(value.issue_codes),
                    str(value.limit_amount) if value.limit_amount is not None else None,
                    value.currency,
                    value.starts_at.isoformat(),
                    value.ends_at.isoformat(),
                    value.route_id,
                    value.state,
                ),
            )
            self._audit(conn, value.bid_id, actor, "negotiation_mandate_created", value.mandate_id)
            conn.commit()

    def add_trade(self, value: ConditionalTrade, actor: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO negotiation_trades VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    value.trade_id,
                    value.bid_id,
                    value.plan_version_id,
                    value.give,
                    value.get,
                    value.required_value,
                    value.value_state.value,
                    value.state.value,
                    value.created_at.isoformat(),
                    value.trade_lineage_id or value.trade_id,
                    1,
                ),
            )
            self._audit(conn, value.bid_id, actor, "conditional_trade_created", value.trade_id)
            conn.commit()

    def add_movement(self, value: NegotiationMovement) -> None:
        with self._conn() as conn:
            if (
                value.movement_type.value == "COMPANY_COMMITMENT_RECORDED"
                and not value.authority_id
            ):
                raise ValueError("company commitments require authority-at-event evidence")
            conn.execute(
                "INSERT INTO negotiation_movements VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    value.event_id,
                    value.bid_id,
                    value.movement_type.value,
                    value.issue_code,
                    value.actor,
                    value.text,
                    value.trade_id,
                    value.authority_id,
                    value.created_at.isoformat(),
                ),
            )
            self._audit(
                conn, value.bid_id, value.actor, "negotiation_movement_recorded", value.event_id
            )
            conn.commit()

    def add_concession(self, value: Concession, actor: str) -> None:
        if value.state == "COMMITTED" and not value.authority_event_id:
            raise ValueError("committed concessions require authority evidence")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO negotiation_concessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    value.concession_id,
                    value.bid_id,
                    value.issue_code,
                    value.version_number,
                    str(value.amount),
                    value.currency,
                    value.unit,
                    value.basis,
                    value.state,
                    value.mandate_id,
                    value.authority_event_id,
                    value.created_at.isoformat(),
                ),
            )
            self._audit(
                conn, value.bid_id, actor, "negotiation_concession_recorded", value.concession_id
            )
            conn.commit()

    def withdraw_plan(self, plan_id: str, actor: str, reason: str) -> None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT bid_id, lifecycle FROM negotiation_plans WHERE plan_id=?",
                (plan_id,),
            ).fetchone()
            if row is None:
                raise ValueError("negotiation plan not found")
            if row["lifecycle"] in (
                PlanLifecycle.WITHDRAWN.value,
                PlanLifecycle.CLOSED.value,
            ):
                raise ValueError("negotiation plan is already terminal and cannot be withdrawn")
            conn.execute(
                "UPDATE negotiation_plans SET lifecycle=? WHERE plan_id=?",
                (PlanLifecycle.WITHDRAWN.value, plan_id),
            )
            self._audit(
                conn, row["bid_id"], actor, "negotiation_plan_withdrawn", f"{plan_id}: {reason}"
            )
            conn.commit()

    def revoke_mandate(self, mandate_id: str, actor: str, reason: str) -> None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT bid_id, state FROM negotiation_mandates WHERE mandate_id=?",
                (mandate_id,),
            ).fetchone()
            if row is None:
                raise ValueError("negotiation mandate not found")
            if row["state"] == "REVOKED":
                raise ValueError("negotiation mandate is already revoked")
            conn.execute(
                "UPDATE negotiation_mandates SET state=? WHERE mandate_id=?",
                ("REVOKED", mandate_id),
            )
            self._audit(
                conn, row["bid_id"], actor, "negotiation_mandate_revoked", f"{mandate_id}: {reason}"
            )
            conn.commit()

    def progress_trade(self, trade_id: str, new_state: TradeState, actor: str, reason: str) -> None:
        """Append a new trade row carrying the next state; never mutate the existing row."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT trade_lineage_id FROM negotiation_trades WHERE trade_id=?",
                (trade_id,),
            ).fetchone()
            if row is None:
                raise ValueError("conditional trade not found")
            lineage = row["trade_lineage_id"]
            current = conn.execute(
                "SELECT * FROM negotiation_trades WHERE trade_lineage_id=? "
                "ORDER BY state_version DESC LIMIT 1",
                (lineage,),
            ).fetchone()
            current_state = TradeState(current["state"])
            validate_trade_transition(current_state, new_state)
            if (
                new_state == TradeState.COMMITTED
                and current["value_state"] != ValueState.EVIDENCED.value
            ):
                raise ValueError("conditional give cannot commit before value is evidenced")
            conn.execute(
                "INSERT INTO negotiation_trades VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"NTR-{uuid4().hex}",
                    current["bid_id"],
                    current["plan_version_id"],
                    current["give"],
                    current["get"],
                    current["required_value"],
                    current["value_state"],
                    new_state.value,
                    datetime.now(UTC).isoformat(),
                    lineage,
                    current["state_version"] + 1,
                ),
            )
            self._audit(
                conn,
                current["bid_id"],
                actor,
                "conditional_trade_state_changed",
                f"{trade_id}: {current_state.value} -> {new_state.value}: {reason}",
            )
            conn.commit()

    def current_trade(self, trade_id: str) -> ConditionalTrade:
        """Return the latest state of a trade lineage as a ConditionalTrade."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT trade_lineage_id FROM negotiation_trades WHERE trade_id=?",
                (trade_id,),
            ).fetchone()
            if row is None:
                raise ValueError("conditional trade not found")
            lineage = row["trade_lineage_id"]
            current = conn.execute(
                "SELECT * FROM negotiation_trades WHERE trade_lineage_id=? "
                "ORDER BY state_version DESC LIMIT 1",
                (lineage,),
            ).fetchone()
        return ConditionalTrade(
            trade_id=current["trade_id"],
            bid_id=current["bid_id"],
            plan_version_id=current["plan_version_id"],
            give=current["give"],
            get=current["get"],
            required_value=current["required_value"],
            value_state=ValueState(current["value_state"]),
            state=TradeState(current["state"]),
            created_at=datetime.fromisoformat(current["created_at"]),
            trade_lineage_id=lineage,
            state_version=current["state_version"],
        )

    def plans(self, bid_id: str | None = None) -> list[dict[str, Any]]:
        with self._conn() as conn:
            clause = " WHERE bid_id=?" if bid_id else ""
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM negotiation_plans" + clause, (bid_id,) if bid_id else ()
                ).fetchall()
            ]

    def metrics(self, bid_id: str | None = None) -> dict[str, int]:
        plans = self.plans(bid_id)
        with self._conn() as conn:
            clause = " WHERE bid_id=?" if bid_id else ""
            args = (bid_id,) if bid_id else ()
            return {
                "plans_total": len(plans),
                "mandates_total": conn.execute(
                    "SELECT COUNT(*) FROM negotiation_mandates" + clause, args
                ).fetchone()[0],
                "trades_total": conn.execute(
                    "SELECT COUNT(*) FROM negotiation_trades" + clause, args
                ).fetchone()[0],
                "concessions_total": conn.execute(
                    "SELECT COUNT(*) FROM negotiation_concessions" + clause, args
                ).fetchone()[0],
                "movements_total": conn.execute(
                    "SELECT COUNT(*) FROM negotiation_movements" + clause, args
                ).fetchone()[0],
            }
