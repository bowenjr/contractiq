"""SQLite persistence and atomic audit writes for operational work items."""
# ruff: noqa: E501

import sqlite3
from datetime import date, datetime
from typing import cast

from core.database import Database
from core.schemas import AuditEntry, Provenance
from core.work_items import (
    ACTIVE_WORK_ITEM_STATUSES,
    ResponsibilityDomain,
    WaitingPartyKind,
    WorkCategory,
    WorkItem,
    WorkItemKind,
    WorkItemPriority,
    WorkItemStatus,
)

WORK_ITEM_MIGRATION_ID = "task_07_work_items_v1"


class WorkItemNotFoundError(ValueError):
    """Raised when an authoritative work-item ID does not exist."""


class StaleWorkItemError(ValueError):
    """Raised when optimistic-concurrency validation rejects an update."""


class WorkItemRepository:
    """Repository for one authoritative work-item register."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._apply_work_items_v1()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _apply_work_items_v1(self) -> None:
        """Apply the additive, idempotent TASK-07 work-item migration."""
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='work_items'"
            ).fetchone()
            if existing is not None:
                bid_not_null = next(
                    int(row["notnull"])
                    for row in conn.execute("PRAGMA table_info(work_items)").fetchall()
                    if row["name"] == "bid_id"
                )
                if bid_not_null:
                    conn.execute("PRAGMA foreign_keys=OFF")
                    conn.execute("ALTER TABLE work_items RENAME TO work_items_ops_legacy")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS work_items (
                    work_item_id TEXT PRIMARY KEY,
                    bid_id TEXT,
                    kind TEXT NOT NULL CHECK (kind IN ('TASK', 'MILESTONE')),
                    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
                    details TEXT,
                    status TEXT NOT NULL CHECK (status IN (
                        'OPEN', 'IN_PROGRESS', 'WAITING', 'BLOCKED',
                        'COMPLETED', 'CANCELLED'
                    )),
                    priority TEXT NOT NULL DEFAULT 'NORMAL' CHECK (priority IN (
                        'LOW', 'NORMAL', 'HIGH', 'CRITICAL'
                    )),
                    due_date TEXT,
                    waiting_on TEXT,
                    blocker_note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
                    provenance_json TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'OTHER',
                    responsibility_domain TEXT,
                    next_action_date TEXT,
                    requester_label TEXT,
                    waiting_party_kind TEXT,
                    waiting_party_label TEXT,
                    waiting_owed TEXT,
                    requested_date TEXT,
                    chase_date TEXT,
                    blocker_description TEXT,
                    resolution_owner TEXT,
                    review_date TEXT,
                    completion_outcome TEXT,
                    completion_evidence TEXT,
                    contribution_candidate INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (bid_id) REFERENCES bids(bid_id),
                    CHECK (kind <> 'MILESTONE' OR due_date IS NOT NULL),
                    CHECK (
                        (status = 'WAITING' AND length(trim(waiting_on)) > 0)
                        OR (status <> 'WAITING' AND waiting_on IS NULL)
                    ),
                    CHECK (
                        (status = 'BLOCKED' AND length(trim(blocker_note)) > 0)
                        OR (status <> 'BLOCKED' AND blocker_note IS NULL)
                    ),
                    CHECK (
                        (status = 'COMPLETED' AND completed_at IS NOT NULL)
                        OR (status <> 'COMPLETED' AND completed_at IS NULL)
                    )
                );

                CREATE INDEX IF NOT EXISTS idx_work_items_bid_status_due
                    ON work_items(bid_id, status, due_date);
                CREATE INDEX IF NOT EXISTS idx_work_items_status_due
                    ON work_items(status, due_date);
                CREATE TRIGGER IF NOT EXISTS work_items_no_hard_delete
                    BEFORE DELETE ON work_items
                    BEGIN SELECT RAISE(ABORT, 'work items are append-only; cancel instead'); END;
                """
            )
            legacy = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='work_items_ops_legacy'"
            ).fetchone()
            if legacy is not None:
                conn.execute(
                    "INSERT INTO work_items(work_item_id,bid_id,kind,title,details,status,priority,due_date,waiting_on,blocker_note,created_at,updated_at,completed_at,version,provenance_json) SELECT work_item_id,bid_id,kind,title,details,status,priority,due_date,waiting_on,blocker_note,created_at,updated_at,completed_at,version,provenance_json FROM work_items_ops_legacy"
                )
                conn.execute("DROP TABLE work_items_ops_legacy")
                conn.execute("PRAGMA foreign_keys=ON")
            columns = {
                str(row["name"]) for row in conn.execute("PRAGMA table_info(work_items)").fetchall()
            }
            for name, definition in (
                ("category", "TEXT NOT NULL DEFAULT 'OTHER'"),
                ("responsibility_domain", "TEXT"),
                ("next_action_date", "TEXT"),
                ("requester_label", "TEXT"),
                ("waiting_party_kind", "TEXT"),
                ("waiting_party_label", "TEXT"),
                ("waiting_owed", "TEXT"),
                ("requested_date", "TEXT"),
                ("chase_date", "TEXT"),
                ("blocker_description", "TEXT"),
                ("resolution_owner", "TEXT"),
                ("review_date", "TEXT"),
                ("completion_outcome", "TEXT"),
                ("completion_evidence", "TEXT"),
                ("contribution_candidate", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE work_items ADD COLUMN {name} {definition}")

    @staticmethod
    def _optional_str(value: object) -> str | None:
        return None if value is None else str(value)

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> WorkItem:
        due_date = cls._optional_str(row["due_date"])
        completed_at = cls._optional_str(row["completed_at"])
        return WorkItem(
            work_item_id=str(row["work_item_id"]),
            bid_id=str(row["bid_id"]) if row["bid_id"] is not None else None,
            kind=WorkItemKind(str(row["kind"])),
            title=str(row["title"]),
            details=cls._optional_str(row["details"]),
            status=WorkItemStatus(str(row["status"])),
            priority=WorkItemPriority(str(row["priority"])),
            due_date=date.fromisoformat(due_date) if due_date is not None else None,
            waiting_on=cls._optional_str(row["waiting_on"]),
            blocker_note=cls._optional_str(row["blocker_note"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            completed_at=(
                datetime.fromisoformat(completed_at) if completed_at is not None else None
            ),
            version=int(row["version"]),
            provenance=Provenance.model_validate_json(str(row["provenance_json"])),
            category=WorkCategory(str(row["category"] or "OTHER")),
            responsibility_domain=(
                ResponsibilityDomain(str(row["responsibility_domain"]))
                if row["responsibility_domain"] is not None
                else None
            ),
            next_action_date=(
                date.fromisoformat(str(row["next_action_date"]))
                if row["next_action_date"] is not None
                else None
            ),
            requester_label=cls._optional_str(row["requester_label"]),
            waiting_party_kind=(
                WaitingPartyKind(str(row["waiting_party_kind"]))
                if row["waiting_party_kind"]
                else None
            ),
            waiting_party_label=cls._optional_str(row["waiting_party_label"]),
            waiting_owed=cls._optional_str(row["waiting_owed"]),
            requested_date=(
                date.fromisoformat(str(row["requested_date"])) if row["requested_date"] else None
            ),
            chase_date=(date.fromisoformat(str(row["chase_date"])) if row["chase_date"] else None),
            blocker_description=cls._optional_str(row["blocker_description"]),
            resolution_owner=cls._optional_str(row["resolution_owner"]),
            review_date=(
                date.fromisoformat(str(row["review_date"])) if row["review_date"] else None
            ),
            completion_outcome=cls._optional_str(row["completion_outcome"]),
            completion_evidence=cls._optional_str(row["completion_evidence"]),
            contribution_candidate=bool(row["contribution_candidate"]),
        )

    @staticmethod
    def _values(item: WorkItem) -> tuple[object, ...]:
        return (
            item.work_item_id,
            item.bid_id,
            item.kind.value,
            item.title,
            item.details,
            item.status.value,
            item.priority.value,
            item.due_date.isoformat() if item.due_date is not None else None,
            item.waiting_on,
            item.blocker_note,
            item.created_at.isoformat(),
            item.updated_at.isoformat(),
            item.completed_at.isoformat() if item.completed_at is not None else None,
            item.version,
            item.provenance.model_dump_json(),
            item.category.value,
            item.responsibility_domain.value if item.responsibility_domain else None,
            item.next_action_date.isoformat() if item.next_action_date else None,
            item.requester_label,
            item.waiting_party_kind.value if item.waiting_party_kind else None,
            item.waiting_party_label,
            item.waiting_owed,
            item.requested_date.isoformat() if item.requested_date else None,
            item.chase_date.isoformat() if item.chase_date else None,
            item.blocker_description,
            item.resolution_owner,
            item.review_date.isoformat() if item.review_date else None,
            item.completion_outcome,
            item.completion_evidence,
            int(item.contribution_candidate),
        )

    @staticmethod
    def _insert_audit(conn: sqlite3.Connection, entry: AuditEntry) -> None:
        conn.execute(
            """
            INSERT INTO audit_log (
                entry_id, bid_id, actor, action, detail, timestamp
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                entry.entry_id,
                entry.bid_id,
                entry.actor,
                entry.action,
                entry.detail,
                entry.timestamp.isoformat(),
            ),
        )

    @staticmethod
    def _validate_audit(item: WorkItem, entry: AuditEntry) -> None:
        if entry.bid_id != item.bid_id:
            raise ValueError("audit bid_id must match the work item")

    def create(self, item: WorkItem, audit_entry: AuditEntry) -> None:
        """Create a work item and its audit record in one transaction."""
        self._validate_audit(item, audit_entry)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO work_items (
                    work_item_id, bid_id, kind, title, details, status,
                    priority, due_date, waiting_on, blocker_note, created_at,
                    updated_at, completed_at, version, provenance_json,
                    category, responsibility_domain, next_action_date, requester_label,
                    waiting_party_kind, waiting_party_label, waiting_owed, requested_date,
                    chase_date, blocker_description, resolution_owner, review_date,
                    completion_outcome, completion_evidence, contribution_candidate
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                self._values(item),
            )
            self._insert_audit(conn, audit_entry)

    def get(self, work_item_id: str) -> WorkItem | None:
        """Fetch one work-item snapshot by stable ID."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM work_items WHERE work_item_id = ?",
                (work_item_id,),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def list(
        self,
        bid_id: str | None = None,
        *,
        active_only: bool = False,
    ) -> list[WorkItem]:
        """List work items with optional parent and active-state filters."""
        clauses: list[str] = []
        values: list[object] = []
        if bid_id is not None:
            clauses.append("bid_id = ?")
            values.append(bid_id)
        if active_only:
            placeholders = ",".join("?" for _ in ACTIVE_WORK_ITEM_STATUSES)
            clauses.append(f"status IN ({placeholders})")
            values.extend(sorted(status.value for status in ACTIVE_WORK_ITEM_STATUSES))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM work_items{where} ORDER BY created_at, work_item_id",
                values,
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def _replace(
        self,
        item: WorkItem,
        expected_version: int,
        audit_entry: AuditEntry,
    ) -> None:
        self._validate_audit(item, audit_entry)
        if item.version != expected_version + 1:
            raise ValueError("replacement version must increment expected_version by one")
        with self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE work_items SET
                    bid_id = ?, kind = ?, title = ?, details = ?, status = ?,
                    priority = ?, due_date = ?, waiting_on = ?, blocker_note = ?,
                    created_at = ?, updated_at = ?, completed_at = ?, version = ?,
                    provenance_json = ?, category = ?, responsibility_domain = ?,
                    next_action_date = ?, requester_label = ?, waiting_party_kind = ?,
                    waiting_party_label = ?, waiting_owed = ?, requested_date = ?, chase_date = ?,
                    blocker_description = ?, resolution_owner = ?, review_date = ?,
                    completion_outcome = ?, completion_evidence = ?, contribution_candidate = ?
                WHERE work_item_id = ? AND version = ?
                """,
                (
                    item.bid_id,
                    item.kind.value,
                    item.title,
                    item.details,
                    item.status.value,
                    item.priority.value,
                    item.due_date.isoformat() if item.due_date is not None else None,
                    item.waiting_on,
                    item.blocker_note,
                    item.created_at.isoformat(),
                    item.updated_at.isoformat(),
                    item.completed_at.isoformat() if item.completed_at is not None else None,
                    item.version,
                    item.provenance.model_dump_json(),
                    item.category.value,
                    item.responsibility_domain.value if item.responsibility_domain else None,
                    item.next_action_date.isoformat() if item.next_action_date else None,
                    item.requester_label,
                    item.waiting_party_kind.value if item.waiting_party_kind else None,
                    item.waiting_party_label,
                    item.waiting_owed,
                    item.requested_date.isoformat() if item.requested_date else None,
                    item.chase_date.isoformat() if item.chase_date else None,
                    item.blocker_description,
                    item.resolution_owner,
                    item.review_date.isoformat() if item.review_date else None,
                    item.completion_outcome,
                    item.completion_evidence,
                    int(item.contribution_candidate),
                    item.work_item_id,
                    expected_version,
                ),
            )
            if cursor.rowcount == 0:
                exists = conn.execute(
                    "SELECT 1 FROM work_items WHERE work_item_id = ?",
                    (item.work_item_id,),
                ).fetchone()
                if exists is None:
                    raise WorkItemNotFoundError(f"Work item not found: {item.work_item_id}")
                raise StaleWorkItemError(f"Stale work item version: expected {expected_version}")
            self._insert_audit(conn, audit_entry)

    def update(
        self,
        item: WorkItem,
        expected_version: int,
        audit_entry: AuditEntry,
    ) -> None:
        """Persist permitted descriptive edits and audit them atomically."""
        self._replace(item, expected_version, audit_entry)

    def transition(
        self,
        item: WorkItem,
        expected_version: int,
        audit_entry: AuditEntry,
    ) -> None:
        """Persist an explicit state transition and audit it atomically."""
        self._replace(item, expected_version, audit_entry)
