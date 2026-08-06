# ruff: noqa: E501, E701, E702, F405, I001
"""SQLite persistence and migration for authoritative TASK-10 records."""

import sqlite3
from datetime import date, datetime, UTC
from typing import cast
from uuid import uuid4

from core.database import Database
from core.requirements import RequirementLifecycle, RequirementOrigin
from core.schemas import Provenance
from core.scope_interfaces import *  # noqa: F403

SCOPE_INTERFACE_MIGRATION_ID = "task_10_scope_interfaces_v1"


class StaleScopeError(ValueError):
    pass


class ScopeNotFoundError(ValueError):
    pass


class ScopeSourceError(ValueError):
    pass


class ScopeInterfaceRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._apply_migration()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _apply_migration(self) -> None:
        statements = (
            """CREATE TABLE IF NOT EXISTS scope_interface_items (
            scope_item_id TEXT PRIMARY KEY, bid_id TEXT NOT NULL, title TEXT NOT NULL,
            description TEXT NOT NULL, scope_area TEXT NOT NULL, origin TEXT NOT NULL,
            customer_need TEXT NOT NULL, offer_position TEXT NOT NULL, pricing_state TEXT NOT NULL,
            responsible_party TEXT, owner TEXT, due_date TEXT, materiality TEXT NOT NULL,
            assumption_exclusion_note TEXT, evidence_decision_note TEXT, work_state TEXT NOT NULL,
            review_state TEXT NOT NULL, reviewer TEXT, review_note TEXT, lifecycle_state TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL,
            provenance_json TEXT NOT NULL, created_by TEXT NOT NULL,
            FOREIGN KEY (bid_id) REFERENCES bids(bid_id),
            CHECK(origin IN ('REQUIREMENT_DERIVED','INTERNAL')), CHECK(lifecycle_state IN ('ACTIVE','WITHDRAWN')),
            CHECK(version >= 1), CHECK(review_state <> 'ACCEPTED' OR reviewer IS NOT NULL))""",
            """CREATE TABLE IF NOT EXISTS scope_interfaces (
            interface_id TEXT PRIMARY KEY, bid_id TEXT NOT NULL, title TEXT NOT NULL,
            boundary_description TEXT NOT NULL, upstream_party TEXT NOT NULL, downstream_party TEXT NOT NULL,
            dependency_description TEXT NOT NULL, owner TEXT, due_date TEXT, materiality TEXT NOT NULL,
            dependency_state TEXT NOT NULL, not_applicable_rationale TEXT, work_state TEXT NOT NULL,
            review_state TEXT NOT NULL, reviewer TEXT, review_note TEXT, lifecycle_state TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL,
            provenance_json TEXT NOT NULL, created_by TEXT NOT NULL,
            FOREIGN KEY (bid_id) REFERENCES bids(bid_id), CHECK(lifecycle_state IN ('ACTIVE','WITHDRAWN')),
            CHECK(upstream_party <> downstream_party), CHECK(version >= 1))""",
            """CREATE TABLE IF NOT EXISTS requirement_scope_links (
            link_id TEXT PRIMARY KEY, requirement_id TEXT NOT NULL, scope_item_id TEXT NOT NULL,
            bid_id TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
            UNIQUE(requirement_id, scope_item_id), FOREIGN KEY(requirement_id) REFERENCES requirements(requirement_id),
            FOREIGN KEY(scope_item_id) REFERENCES scope_interface_items(scope_item_id), FOREIGN KEY(bid_id) REFERENCES bids(bid_id))""",
            """CREATE TABLE IF NOT EXISTS requirement_scope_relevance (
            requirement_id TEXT PRIMARY KEY, bid_id TEXT NOT NULL, relevance TEXT NOT NULL,
            rationale TEXT, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL,
            FOREIGN KEY(requirement_id) REFERENCES requirements(requirement_id), FOREIGN KEY(bid_id) REFERENCES bids(bid_id),
            CHECK(relevance IN ('UNASSESSED','APPLICABLE','NOT_APPLICABLE')),
            CHECK(relevance <> 'NOT_APPLICABLE' OR rationale IS NOT NULL))""",
            """CREATE TABLE IF NOT EXISTS interface_scope_links (
            link_id TEXT PRIMARY KEY, interface_id TEXT NOT NULL, scope_item_id TEXT NOT NULL,
            bid_id TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
            UNIQUE(interface_id, scope_item_id), FOREIGN KEY(interface_id) REFERENCES scope_interfaces(interface_id),
            FOREIGN KEY(scope_item_id) REFERENCES scope_interface_items(scope_item_id), FOREIGN KEY(bid_id) REFERENCES bids(bid_id))""",
            "CREATE INDEX IF NOT EXISTS idx_scope_items_bid_state ON scope_interface_items(bid_id,lifecycle_state,title,scope_item_id)",
            "CREATE INDEX IF NOT EXISTS idx_scope_interfaces_bid_state ON scope_interfaces(bid_id,lifecycle_state,title,interface_id)",
            "CREATE TRIGGER IF NOT EXISTS prevent_scope_item_delete BEFORE DELETE ON scope_interface_items BEGIN SELECT RAISE(ABORT,'scope items cannot be deleted'); END",
            "CREATE TRIGGER IF NOT EXISTS prevent_interface_delete BEFORE DELETE ON scope_interfaces BEGIN SELECT RAISE(ABORT,'interfaces cannot be deleted'); END",
        )
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in statements:
                conn.execute(statement)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _insert_audit(
        conn: sqlite3.Connection, bid_id: str, actor: str, action: str, detail: str
    ) -> None:
        conn.execute(
            "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) VALUES(?,?,?,?,?,?)",
            (f"AUD-{uuid4().hex}", bid_id, actor, action, detail, datetime.now(UTC).isoformat()),
        )

    @staticmethod
    def _scope(row: sqlite3.Row) -> ScopeItem:
        values = dict(row)
        values["due_date"] = date.fromisoformat(values["due_date"]) if values["due_date"] else None
        values["created_at"] = datetime.fromisoformat(values["created_at"])
        values["updated_at"] = datetime.fromisoformat(values["updated_at"])
        values["provenance"] = Provenance.model_validate_json(values.pop("provenance_json"))
        return ScopeItem(**values)

    @staticmethod
    def _interface(row: sqlite3.Row) -> InterfaceRecord:
        values = dict(row)
        values["due_date"] = date.fromisoformat(values["due_date"]) if values["due_date"] else None
        values["created_at"] = datetime.fromisoformat(values["created_at"])
        values["updated_at"] = datetime.fromisoformat(values["updated_at"])
        values["provenance"] = Provenance.model_validate_json(values.pop("provenance_json"))
        return InterfaceRecord(**values)

    def create_scope_item(
        self, item: ScopeItem, actor: str, requirement_ids: list[str] | None = None
    ) -> None:
        links = requirement_ids or []
        now = datetime.now(UTC).isoformat()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._validate_links(conn, item.bid_id, item.scope_item_id, links, actor)
            vals = item.model_dump(mode="json")
            vals.update(
                created_at=item.created_at.isoformat(),
                updated_at=item.updated_at.isoformat(),
                due_date=item.due_date.isoformat() if item.due_date else None,
                provenance_json=item.provenance.model_dump_json(),
            )
            cols = "scope_item_id,bid_id,title,description,scope_area,origin,customer_need,offer_position,pricing_state,responsible_party,owner,due_date,materiality,assumption_exclusion_note,evidence_decision_note,work_state,review_state,reviewer,review_note,lifecycle_state,created_at,updated_at,version,provenance_json,created_by"
            conn.execute(
                f"INSERT INTO scope_interface_items({cols}) VALUES({','.join('?' for _ in cols.split(','))})",
                tuple(
                    vals.get(c)
                    if c not in ("created_at", "updated_at", "due_date", "provenance_json")
                    else vals[c]
                    for c in cols.split(",")
                ),
            )
            for rid in links:
                self._link(
                    conn,
                    "requirement_scope_links",
                    "requirement_id",
                    rid,
                    "scope_item_id",
                    item.scope_item_id,
                    item.bid_id,
                    actor,
                    now,
                )
            self._insert_audit(conn, item.bid_id, actor, "scope_created", item.scope_item_id)
            conn.commit()

    def _validate_links(
        self,
        conn: sqlite3.Connection,
        bid_id: str,
        scope_id: str,
        requirement_ids: list[str],
        actor: str,
    ) -> None:
        for rid in requirement_ids:
            row = conn.execute(
                "SELECT bid_id,lifecycle_state,origin,source_document_version_id FROM requirements WHERE requirement_id=?",
                (rid,),
            ).fetchone()
            if (
                row is None
                or row["bid_id"] != bid_id
                or row["lifecycle_state"] != RequirementLifecycle.ACTIVE.value
            ):
                raise ScopeSourceError("requirement is not an active same-bid requirement")
            if (
                row["origin"] == RequirementOrigin.EXPLICIT.value
                and not row["source_document_version_id"]
            ):
                raise ScopeSourceError("requirement source is degraded")

    def _link(
        self,
        conn: sqlite3.Connection,
        table: str,
        left_col: str,
        left: str,
        right_col: str,
        right: str,
        bid: str,
        actor: str,
        now: str,
    ) -> None:
        conn.execute(
            f"INSERT INTO {table}(link_id,{left_col},{right_col},bid_id,created_at,created_by) VALUES(?,?,?,?,?,?)",
            (f"LINK-{uuid4().hex}", left, right, bid, now, actor),
        )

    def get_scope_item(self, scope_item_id: str) -> ScopeItem | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM scope_interface_items WHERE scope_item_id=?", (scope_item_id,)
            ).fetchone()
        return self._scope(row) if row else None

    def list_scope_items(self, bid_id: str | None = None) -> list[ScopeItem]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM scope_interface_items"
                + (" WHERE bid_id=?" if bid_id else "")
                + " ORDER BY title,scope_item_id",
                ((bid_id,) if bid_id else ()),
            ).fetchall()
        return [self._scope(r) for r in rows]

    def requirement_links(
        self, scope_item_id: str | None = None, requirement_id: str | None = None
    ) -> list[tuple[str, str]]:
        with self._conn() as c:
            q = "SELECT requirement_id,scope_item_id FROM requirement_scope_links"
            params: list[str] = []
            clauses = []
            if scope_item_id:
                clauses.append("scope_item_id=?")
                params.append(scope_item_id)
            if requirement_id:
                clauses.append("requirement_id=?")
                params.append(requirement_id)
            if clauses:
                q += " WHERE " + " AND ".join(clauses)
            return [(str(r[0]), str(r[1])) for r in c.execute(q, params)]

    def create_interface(
        self, record: InterfaceRecord, actor: str, scope_item_ids: list[str] | None = None
    ) -> None:
        ids = scope_item_ids or []
        now = datetime.now(UTC).isoformat()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for sid in ids:
                row = conn.execute(
                    "SELECT bid_id,lifecycle_state FROM scope_interface_items WHERE scope_item_id=?",
                    (sid,),
                ).fetchone()
                if (
                    row is None
                    or row["bid_id"] != record.bid_id
                    or row["lifecycle_state"] != LifecycleState.ACTIVE.value
                ):
                    raise ValueError("interface scope link must be active and same-bid")
            vals = record.model_dump(mode="json")
            vals.update(
                created_at=record.created_at.isoformat(),
                updated_at=record.updated_at.isoformat(),
                due_date=record.due_date.isoformat() if record.due_date else None,
                provenance_json=record.provenance.model_dump_json(),
            )
            cols = list(vals.keys())
            cols.remove("provenance")
            cols.remove("scope_item_ids") if "scope_item_ids" in cols else None
            names = "interface_id,bid_id,title,boundary_description,upstream_party,downstream_party,dependency_description,owner,due_date,materiality,dependency_state,not_applicable_rationale,work_state,review_state,reviewer,review_note,lifecycle_state,created_at,updated_at,version,provenance_json,created_by"
            conn.execute(
                f"INSERT INTO scope_interfaces({names}) VALUES({','.join('?' for _ in names.split(','))})",
                tuple(
                    vals.get(n) if n not in ("provenance_json",) else vals[n]
                    for n in names.split(",")
                ),
            )
            for sid in ids:
                self._link(
                    conn,
                    "interface_scope_links",
                    "interface_id",
                    record.interface_id,
                    "scope_item_id",
                    sid,
                    record.bid_id,
                    actor,
                    now,
                )
            self._insert_audit(conn, record.bid_id, actor, "interface_created", record.interface_id)
            conn.commit()

    def get_interface(self, interface_id: str) -> InterfaceRecord | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM scope_interfaces WHERE interface_id=?", (interface_id,)
            ).fetchone()
        return self._interface(row) if row else None

    def list_interfaces(self, bid_id: str | None = None) -> list[InterfaceRecord]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM scope_interfaces"
                + (" WHERE bid_id=?" if bid_id else "")
                + " ORDER BY title,interface_id",
                ((bid_id,) if bid_id else ()),
            ).fetchall()
        return [self._interface(r) for r in rows]

    def interface_scope_links(self, interface_id: str | None = None) -> list[tuple[str, str]]:
        with self._conn() as c:
            return [
                (str(r[0]), str(r[1]))
                for r in c.execute(
                    "SELECT interface_id,scope_item_id FROM interface_scope_links"
                    + (" WHERE interface_id=?" if interface_id else ""),
                    ((interface_id,) if interface_id else ()),
                ).fetchall()
            ]

    def withdraw_scope_item(self, scope_item_id: str, expected_version: int, actor: str) -> None:
        self._withdraw(
            "scope_interface_items", "scope_item_id", scope_item_id, expected_version, actor
        )

    def withdraw_interface(self, interface_id: str, expected_version: int, actor: str) -> None:
        self._withdraw("scope_interfaces", "interface_id", interface_id, expected_version, actor)

    def _withdraw(self, table: str, key: str, value: str, expected: int, actor: str) -> None:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                f"SELECT bid_id,version,lifecycle_state FROM {table} WHERE {key}=?", (value,)
            ).fetchone()
            if row is None:
                raise ScopeNotFoundError(value)
            if row["version"] != expected:
                raise StaleScopeError("stale update")
            if row["lifecycle_state"] == LifecycleState.WITHDRAWN.value:
                raise ValueError("withdrawal is irreversible")
            c.execute(
                f"UPDATE {table} SET lifecycle_state='WITHDRAWN',updated_at=?,version=version+1,review_state='NOT_REVIEWED' WHERE {key}=? AND version=?",
                (datetime.now(UTC).isoformat(), value, expected),
            )
            self._insert_audit(c, row["bid_id"], actor, "withdraw", value)
            c.commit()
