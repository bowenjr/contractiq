"""SQLite persistence for the authoritative TASK-11 supplier register."""
# SQL strings remain readable and mirror the migration contract.
# ruff: noqa: E501

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from core.database import Database
from core.supplier_assurance import (
    FlowDownLink,
    RequestItem,
    Supplier,
    SupplierRequest,
)

SUPPLIER_MIGRATION_ID = "task_11_supplier_assurance_v1"
SUPPLIER_COMPLETION_MIGRATION_ID = "task_11_supplier_assurance_completion_v1"


class SupplierRepository:
    """Atomic repository; every authoritative write also writes an audit row."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._migrate()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _migrate(self) -> None:
        statements = [
            """CREATE TABLE IF NOT EXISTS bid_suppliers (
                supplier_id TEXT PRIMARY KEY, bid_id TEXT NOT NULL,
                supplier_name TEXT NOT NULL, manufacturer_name TEXT,
                operator_reference TEXT, note TEXT, lifecycle_state TEXT NOT NULL,
                provenance_json TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, version INTEGER NOT NULL,
                created_by TEXT NOT NULL, FOREIGN KEY (bid_id) REFERENCES bids(bid_id),
                CHECK (lifecycle_state IN ('ACTIVE','WITHDRAWN'))
            )""",
            """CREATE TABLE IF NOT EXISTS supplier_requests (
                request_id TEXT PRIMARY KEY, bid_id TEXT NOT NULL,
                supplier_id TEXT NOT NULL, request_type TEXT NOT NULL,
                title TEXT NOT NULL, external_reference TEXT, purpose TEXT NOT NULL,
                owner TEXT NOT NULL, due_date TEXT, request_state TEXT NOT NULL,
                issued_at TEXT, closed_at TEXT, close_rationale TEXT,
                supersedes_request_id TEXT, lifecycle_state TEXT NOT NULL,
                provenance_json TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, version INTEGER NOT NULL,
                created_by TEXT NOT NULL, FOREIGN KEY (bid_id) REFERENCES bids(bid_id),
                FOREIGN KEY (supplier_id) REFERENCES bid_suppliers(supplier_id),
                CHECK (request_state IN ('DRAFT','ISSUED','CLOSED')),
                CHECK (lifecycle_state IN ('ACTIVE','WITHDRAWN'))
            )""",
            """CREATE TABLE IF NOT EXISTS supplier_request_items (
                request_item_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
                bid_id TEXT NOT NULL, sequence INTEGER NOT NULL, title TEXT NOT NULL,
                confirmation_text TEXT NOT NULL, topic TEXT NOT NULL,
                materiality TEXT NOT NULL, support_role TEXT NOT NULL,
                operator_note TEXT, FOREIGN KEY (request_id) REFERENCES supplier_requests(request_id),
                FOREIGN KEY (bid_id) REFERENCES bids(bid_id),
                UNIQUE (request_id, sequence),
                CHECK (support_role IN ('REQUIRED_SUPPORT','CANDIDATE_ALTERNATIVE','INFORMATION_ONLY'))
            )""",
            """CREATE TABLE IF NOT EXISTS supplier_responses (
                response_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
                supplier_id TEXT NOT NULL, bid_id TEXT NOT NULL,
                latest_version_id TEXT, accepted_version_id TEXT,
                version INTEGER NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, FOREIGN KEY (request_id) REFERENCES supplier_requests(request_id)
            )""",
            """CREATE TABLE IF NOT EXISTS supplier_response_versions (
                response_version_id TEXT PRIMARY KEY, response_id TEXT NOT NULL,
                request_id TEXT NOT NULL, supplier_id TEXT NOT NULL, bid_id TEXT NOT NULL,
                version_number INTEGER NOT NULL, supplier_reference TEXT,
                received_at TEXT NOT NULL, evidence_mode TEXT NOT NULL,
                document_version_id TEXT, evidence_note TEXT, validity_state TEXT NOT NULL,
                valid_until TEXT, overall_note TEXT, review_state TEXT NOT NULL,
                reviewer TEXT, review_note TEXT, created_at TEXT NOT NULL,
                created_by TEXT NOT NULL, FOREIGN KEY (response_id) REFERENCES supplier_responses(response_id),
                FOREIGN KEY (request_id) REFERENCES supplier_requests(request_id),
                UNIQUE (response_id, version_number),
                CHECK (review_state IN ('NOT_REVIEWED','ACCEPTED','CHANGES_REQUIRED'))
            )""",
            """CREATE TABLE IF NOT EXISTS supplier_response_coverage (
                response_version_id TEXT NOT NULL, request_item_id TEXT NOT NULL,
                state TEXT NOT NULL, exception_kind TEXT, evidence_text TEXT,
                operator_note TEXT, PRIMARY KEY (response_version_id, request_item_id),
                FOREIGN KEY (response_version_id) REFERENCES supplier_response_versions(response_version_id),
                FOREIGN KEY (request_item_id) REFERENCES supplier_request_items(request_item_id),
                CHECK (state IN ('CONFIRMED','EXCEPTION','NOT_APPLICABLE','SILENT'))
            )""",
            """CREATE TRIGGER IF NOT EXISTS supplier_no_delete
                BEFORE DELETE ON bid_suppliers BEGIN
                SELECT RAISE(ABORT, 'supplier identities are immutable; withdraw instead'); END""",
            """CREATE TRIGGER IF NOT EXISTS supplier_version_no_delete
                BEFORE DELETE ON supplier_response_versions BEGIN
                SELECT RAISE(ABORT, 'supplier response versions are immutable'); END""",
        ]
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in statements:
                    conn.execute(statement)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self._completion_migrate()

    def _completion_migrate(self) -> None:
        """Forward-only additive hardening for the published partial checkpoint."""
        statements = [
            """CREATE TABLE IF NOT EXISTS supplier_item_flow_down (
                link_id TEXT PRIMARY KEY, request_item_id TEXT NOT NULL,
                bid_id TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
                created_at TEXT NOT NULL, created_by TEXT NOT NULL,
                FOREIGN KEY (request_item_id) REFERENCES supplier_request_items(request_item_id),
                UNIQUE(request_item_id,target_type,target_id),
                CHECK(target_type IN ('REQUIREMENT','SCOPE_ITEM','INTERFACE'))
            )""",
            """CREATE TABLE IF NOT EXISTS supplier_schema_migrations (
                migration_id TEXT PRIMARY KEY, applied_at TEXT NOT NULL
            )""",
            """CREATE TRIGGER IF NOT EXISTS supplier_issued_item_immutable
                BEFORE UPDATE ON supplier_request_items
                WHEN (SELECT request_state FROM supplier_requests WHERE request_id=OLD.request_id)='ISSUED'
                BEGIN SELECT RAISE(ABORT,'issued request items are immutable'); END""",
            """CREATE TRIGGER IF NOT EXISTS supplier_issued_item_no_delete
                BEFORE DELETE ON supplier_request_items
                WHEN (SELECT request_state FROM supplier_requests WHERE request_id=OLD.request_id)='ISSUED'
                BEGIN SELECT RAISE(ABORT,'issued request items are immutable'); END""",
            """CREATE TRIGGER IF NOT EXISTS supplier_version_no_update
                BEFORE UPDATE OF response_id,request_id,supplier_id,bid_id,version_number,evidence_mode,document_version_id,evidence_note,validity_state,valid_until,overall_note,created_at,created_by
                ON supplier_response_versions BEGIN SELECT RAISE(ABORT,'response version content is immutable'); END""",
            """CREATE TRIGGER IF NOT EXISTS supplier_coverage_no_update
                BEFORE UPDATE ON supplier_response_coverage BEGIN SELECT RAISE(ABORT,'response coverage is immutable'); END""",
            """CREATE TRIGGER IF NOT EXISTS supplier_coverage_no_delete
                BEFORE DELETE ON supplier_response_coverage BEGIN SELECT RAISE(ABORT,'response coverage is immutable'); END""",
            """CREATE TRIGGER IF NOT EXISTS supplier_flow_down_no_update
                BEFORE UPDATE ON supplier_item_flow_down BEGIN SELECT RAISE(ABORT,'flow-down links are immutable'); END""",
            """CREATE TRIGGER IF NOT EXISTS supplier_flow_down_no_delete
                BEFORE DELETE ON supplier_item_flow_down BEGIN SELECT RAISE(ABORT,'flow-down links are immutable'); END""",
            """CREATE TRIGGER IF NOT EXISTS supplier_response_pointer_guard
                BEFORE UPDATE OF latest_version_id,accepted_version_id ON supplier_responses
                WHEN (NEW.latest_version_id IS NOT NULL AND NOT EXISTS
                    (SELECT 1 FROM supplier_response_versions v WHERE v.response_version_id=NEW.latest_version_id AND v.response_id=OLD.response_id))
                  OR (NEW.accepted_version_id IS NOT NULL AND NOT EXISTS
                    (SELECT 1 FROM supplier_response_versions v WHERE v.response_version_id=NEW.accepted_version_id AND v.response_id=OLD.response_id))
                BEGIN SELECT RAISE(ABORT,'response pointer must reference its response aggregate'); END""",
        ]
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT OR IGNORE INTO supplier_schema_migrations VALUES (?,?)",
                    (SUPPLIER_COMPLETION_MIGRATION_ID, datetime.now(UTC).isoformat()),
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

    def create_supplier(self, supplier: Supplier, actor: str = "operator") -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO bid_suppliers VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        supplier.supplier_id,
                        supplier.bid_id,
                        supplier.supplier_name,
                        supplier.manufacturer_name,
                        supplier.operator_reference,
                        supplier.note,
                        supplier.lifecycle_state.value,
                        supplier.provenance.model_dump_json(),
                        supplier.created_at.isoformat(),
                        supplier.updated_at.isoformat(),
                        supplier.version,
                        supplier.created_by,
                    ),
                )
                self._audit(conn, supplier.bid_id, actor, "supplier_created", supplier.supplier_id)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def create_request(
        self, request: SupplierRequest, items: list[RequestItem], actor: str = "operator"
    ) -> None:
        if not items:
            raise ValueError("supplier request requires at least one checklist item")
        if len({item.sequence for item in items}) != len(items):
            raise ValueError("request item sequences must be unique")
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                supplier = conn.execute(
                    "SELECT bid_id,lifecycle_state FROM bid_suppliers WHERE supplier_id=?",
                    (request.supplier_id,),
                ).fetchone()
                if (
                    supplier is None
                    or supplier["bid_id"] != request.bid_id
                    or supplier["lifecycle_state"] != "ACTIVE"
                ):
                    raise ValueError("supplier is not active in the request bid")
                conn.execute(
                    "INSERT INTO supplier_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        request.request_id,
                        request.bid_id,
                        request.supplier_id,
                        request.request_type.value,
                        request.title,
                        request.external_reference,
                        request.purpose,
                        request.owner,
                        request.due_date.isoformat() if request.due_date else None,
                        request.request_state.value,
                        request.issued_at.isoformat() if request.issued_at else None,
                        request.closed_at.isoformat() if request.closed_at else None,
                        request.close_rationale,
                        request.supersedes_request_id,
                        request.lifecycle_state.value,
                        request.provenance.model_dump_json(),
                        request.created_at.isoformat(),
                        request.updated_at.isoformat(),
                        request.version,
                        request.created_by,
                    ),
                )
                for item in items:
                    if item.bid_id != request.bid_id or item.request_id != request.request_id:
                        raise ValueError("request item must belong to request bid and id")
                    conn.execute(
                        "INSERT INTO supplier_request_items VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            item.request_item_id,
                            item.request_id,
                            item.bid_id,
                            item.sequence,
                            item.title,
                            item.confirmation_text,
                            item.topic.value,
                            item.materiality.value,
                            item.support_role.value,
                            item.operator_note,
                        ),
                    )
                self._audit(
                    conn, request.bid_id, actor, "supplier_request_created", request.request_id
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def list_suppliers(self, bid_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM bid_suppliers"
        params: tuple[str, ...] = ()
        if bid_id:
            query += " WHERE bid_id=?"
            params = (bid_id,)
        query += " ORDER BY supplier_name,supplier_id"
        with self._conn() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def list_requests(self, bid_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM supplier_requests"
        params: tuple[str, ...] = ()
        if bid_id:
            query += " WHERE bid_id=?"
            params = (bid_id,)
        query += " ORDER BY title,request_id"
        with self._conn() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def add_flow_down(self, link: FlowDownLink, actor: str = "operator") -> None:
        """Create one explicit same-bid link; free text cannot create links."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                item = conn.execute(
                    "SELECT bid_id,request_id FROM supplier_request_items WHERE request_item_id=?",
                    (link.request_item_id,),
                ).fetchone()
                if item is None or item["bid_id"] != link.bid_id:
                    raise ValueError("flow-down item is not in the requested bid")
                request = conn.execute(
                    "SELECT request_state FROM supplier_requests WHERE request_id=?",
                    (item["request_id"],),
                ).fetchone()
                if request is None or request["request_state"] == "ISSUED":
                    raise ValueError("issued request links are immutable")
                table, column = {
                    "REQUIREMENT": ("requirements", "requirement_id"),
                    "SCOPE_ITEM": ("scope_interface_items", "scope_item_id"),
                    "INTERFACE": ("scope_interfaces", "interface_id"),
                }[link.target_type.value]
                target = conn.execute(
                    f"SELECT bid_id FROM {table} WHERE {column}=?", (link.target_id,)
                ).fetchone()
                if target is None or target["bid_id"] != link.bid_id:
                    raise ValueError("flow-down target does not exist")
                conn.execute(
                    "INSERT INTO supplier_item_flow_down VALUES (?,?,?,?,?,?,?)",
                    (
                        link.link_id,
                        link.request_item_id,
                        link.bid_id,
                        link.target_type.value,
                        link.target_id,
                        link.created_at.isoformat(),
                        link.created_by,
                    ),
                )
                self._audit(conn, link.bid_id, actor, "supplier_flow_down_created", link.link_id)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def list_flow_down(self, request_item_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM supplier_item_flow_down WHERE request_item_id=? ORDER BY target_type,target_id",
                    (request_item_id,),
                ).fetchall()
            ]

    def issue_request(
        self, request_id: str, expected_version: int, actor: str = "operator"
    ) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT bid_id,request_state FROM supplier_requests WHERE request_id=? AND version=?",
                    (request_id, expected_version),
                ).fetchone()
                if row is None or row["request_state"] != "DRAFT":
                    raise ValueError("stale or non-draft request")
                items = conn.execute(
                    "SELECT request_item_id,support_role,materiality FROM supplier_request_items WHERE request_id=?",
                    (request_id,),
                ).fetchall()
                if not items or any(
                    not conn.execute(
                        "SELECT 1 FROM supplier_item_flow_down WHERE request_item_id=?",
                        (item["request_item_id"],),
                    ).fetchone()
                    for item in items
                ):
                    raise ValueError("every request item requires an explicit flow-down link")
                now = datetime.now(UTC).isoformat()
                conn.execute(
                    "UPDATE supplier_requests SET request_state='ISSUED',issued_at=?,updated_at=?,version=version+1 WHERE request_id=? AND version=?",
                    (now, now, request_id, expected_version),
                )
                self._audit(conn, row["bid_id"], actor, "supplier_request_issued", request_id)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def close_request(
        self, request_id: str, expected_version: int, rationale: str, actor: str = "operator"
    ) -> None:
        if not rationale.strip():
            raise ValueError("closure requires an explicit rationale")
        self._transition_request(request_id, expected_version, "CLOSED", rationale, actor)

    def withdraw_request(
        self, request_id: str, expected_version: int, actor: str = "operator"
    ) -> None:
        self._transition_request(request_id, expected_version, "WITHDRAWN", None, actor)

    def _transition_request(
        self,
        request_id: str,
        expected_version: int,
        state: str,
        rationale: str | None,
        actor: str,
    ) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT bid_id FROM supplier_requests WHERE request_id=? AND version=?",
                    (request_id, expected_version),
                ).fetchone()
                if row is None:
                    raise ValueError("stale supplier request update")
                now = datetime.now(UTC).isoformat()
                if state == "WITHDRAWN":
                    conn.execute(
                        "UPDATE supplier_requests SET lifecycle_state='WITHDRAWN',updated_at=?,version=version+1 WHERE request_id=? AND version=?",
                        (now, request_id, expected_version),
                    )
                else:
                    conn.execute(
                        "UPDATE supplier_requests SET request_state='CLOSED',closed_at=?,close_rationale=?,updated_at=?,version=version+1 WHERE request_id=? AND version=?",
                        (now, rationale, now, request_id, expected_version),
                    )
                self._audit(
                    conn, row["bid_id"], actor, f"supplier_request_{state.lower()}", request_id
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
