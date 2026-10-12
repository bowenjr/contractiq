"""Additive SQLite schema and atomic persistence for Bid execution overlays."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from core.bid_execution import BidExecutionNote, BidExecutionRecord, ExecutionSubjectKind
from core.database import Database


class BidExecutionRepository:
    """Own the Stage 3 additive migration and audited execution writes."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._migrate()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _migrate(self) -> None:
        statements = (
            """CREATE TABLE IF NOT EXISTS bid_execution_records (
                execution_id TEXT PRIMARY KEY,
                bid_id TEXT NOT NULL,
                subject_kind TEXT NOT NULL CHECK (subject_kind IN
                    ('WORK_ITEM','RETURNABLE','MANUFACTURER_INPUT')),
                subject_id TEXT NOT NULL,
                responsible TEXT,
                contributors_json TEXT NOT NULL,
                internal_due_date TEXT,
                prep_status TEXT CHECK (prep_status IS NULL OR prep_status IN
                    ('NOT_STARTED','IN_PROGRESS','BLOCKED','COMPLETE','NOT_REQUIRED')),
                priority TEXT,
                blocked_reason TEXT,
                completion_evidence TEXT,
                evidence_document_version_id TEXT,
                version INTEGER NOT NULL CHECK (version >= 1),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL,
                FOREIGN KEY (bid_id) REFERENCES bids(bid_id),
                UNIQUE (subject_kind, subject_id)
            )""",
            """CREATE TABLE IF NOT EXISTS bid_execution_notes (
                note_id TEXT PRIMARY KEY,
                bid_id TEXT NOT NULL,
                subject_kind TEXT NOT NULL CHECK (subject_kind IN
                    ('WORK_ITEM','RETURNABLE','MANUFACTURER_INPUT')),
                subject_id TEXT NOT NULL,
                body TEXT NOT NULL CHECK (
                    length(trim(body)) > 0 AND length(body) <= 4000),
                created_at TEXT NOT NULL,
                created_by TEXT NOT NULL,
                FOREIGN KEY (bid_id) REFERENCES bids(bid_id)
            )""",
            """CREATE TABLE IF NOT EXISTS bid_execution_dependencies (
                dependency_id TEXT PRIMARY KEY,
                bid_id TEXT NOT NULL,
                item_kind TEXT NOT NULL CHECK (item_kind IN
                    ('WORK_ITEM','RETURNABLE','MANUFACTURER_INPUT')),
                item_id TEXT NOT NULL,
                prerequisite_kind TEXT NOT NULL CHECK (prerequisite_kind IN
                    ('WORK_ITEM','RETURNABLE','MANUFACTURER_INPUT')),
                prerequisite_id TEXT NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL,
                created_by TEXT NOT NULL,
                removed_at TEXT,
                removed_by TEXT,
                FOREIGN KEY (bid_id) REFERENCES bids(bid_id),
                CHECK ((removed_at IS NULL AND removed_by IS NULL)
                    OR (removed_at IS NOT NULL AND removed_by IS NOT NULL))
            )""",
            """CREATE INDEX IF NOT EXISTS idx_bid_execution_records_bid
                ON bid_execution_records(bid_id, subject_kind)""",
            """CREATE INDEX IF NOT EXISTS idx_bid_execution_notes_subject
                ON bid_execution_notes(subject_kind, subject_id, created_at)""",
            """CREATE INDEX IF NOT EXISTS idx_bid_execution_dependencies_bid
                ON bid_execution_dependencies(bid_id, item_kind, item_id)""",
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_bid_execution_dependency_active
                ON bid_execution_dependencies(
                    item_kind,item_id,prerequisite_kind,prerequisite_id
                ) WHERE removed_at IS NULL""",
            """CREATE TRIGGER IF NOT EXISTS bid_execution_record_no_delete
                BEFORE DELETE ON bid_execution_records BEGIN
                SELECT RAISE(ABORT,
                    'execution records cannot be deleted; update the record instead');
                END""",
            """CREATE TRIGGER IF NOT EXISTS bid_execution_note_no_update
                BEFORE UPDATE ON bid_execution_notes BEGIN
                SELECT RAISE(ABORT,
                    'execution notes are append-only; add a correcting note instead');
                END""",
            """CREATE TRIGGER IF NOT EXISTS bid_execution_note_no_delete
                BEFORE DELETE ON bid_execution_notes BEGIN
                SELECT RAISE(ABORT,
                    'execution notes cannot be deleted; add a correcting note instead');
                END""",
            """CREATE TRIGGER IF NOT EXISTS bid_execution_dependency_no_delete
                BEFORE DELETE ON bid_execution_dependencies BEGIN
                SELECT RAISE(ABORT,
                    'execution dependencies cannot be deleted; mark the link removed instead');
                END""",
            """CREATE TRIGGER IF NOT EXISTS bid_execution_dependency_remove_once
                BEFORE UPDATE ON bid_execution_dependencies
                WHEN NOT (
                    OLD.removed_at IS NULL
                    AND NEW.removed_at IS NOT NULL
                    AND NEW.removed_by IS NOT NULL
                    AND NEW.dependency_id IS OLD.dependency_id
                    AND NEW.bid_id IS OLD.bid_id
                    AND NEW.item_kind IS OLD.item_kind
                    AND NEW.item_id IS OLD.item_id
                    AND NEW.prerequisite_kind IS OLD.prerequisite_kind
                    AND NEW.prerequisite_id IS OLD.prerequisite_id
                    AND NEW.note IS OLD.note
                    AND NEW.created_at IS OLD.created_at
                    AND NEW.created_by IS OLD.created_by
                ) BEGIN
                SELECT RAISE(ABORT,
                    'a dependency may only be marked removed once; add a new link instead');
                END""",
        )
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in statements:
                    conn.execute(statement)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _audit(
        conn: sqlite3.Connection,
        bid_id: str,
        actor: str,
        action: str,
        detail: str,
    ) -> None:
        conn.execute(
            "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) "
            "VALUES(?,?,?,?,?,?)",
            (f"AUD-{uuid4().hex}", bid_id, actor, action, detail, datetime.now(UTC).isoformat()),
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> BidExecutionRecord:
        contributors = cast(list[object], json.loads(str(row["contributors_json"])))
        return BidExecutionRecord(
            execution_id=str(row["execution_id"]),
            bid_id=str(row["bid_id"]),
            subject_kind=ExecutionSubjectKind(str(row["subject_kind"])),
            subject_id=str(row["subject_id"]),
            responsible=str(row["responsible"]) if row["responsible"] is not None else None,
            contributors=[str(value) for value in contributors],
            internal_due_date=row["internal_due_date"],
            prep_status=row["prep_status"],
            priority=row["priority"],
            blocked_reason=(
                str(row["blocked_reason"]) if row["blocked_reason"] is not None else None
            ),
            completion_evidence=(
                str(row["completion_evidence"]) if row["completion_evidence"] is not None else None
            ),
            evidence_document_version_id=(
                str(row["evidence_document_version_id"])
                if row["evidence_document_version_id"] is not None
                else None
            ),
            version=int(row["version"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            updated_by=str(row["updated_by"]),
        )

    @staticmethod
    def _note_from_row(row: sqlite3.Row) -> BidExecutionNote:
        return BidExecutionNote(
            note_id=str(row["note_id"]),
            bid_id=str(row["bid_id"]),
            subject_kind=ExecutionSubjectKind(str(row["subject_kind"])),
            subject_id=str(row["subject_id"]),
            body=str(row["body"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            created_by=str(row["created_by"]),
        )

    @staticmethod
    def _record_values(record: BidExecutionRecord) -> tuple[object, ...]:
        return (
            record.execution_id,
            record.bid_id,
            record.subject_kind.value,
            record.subject_id,
            record.responsible,
            json.dumps(record.contributors),
            record.internal_due_date.isoformat() if record.internal_due_date else None,
            record.prep_status.value if record.prep_status else None,
            record.priority.value if record.priority else None,
            record.blocked_reason,
            record.completion_evidence,
            record.evidence_document_version_id,
            record.version,
            record.created_at.isoformat(),
            record.updated_at.isoformat(),
            record.updated_by,
        )

    @staticmethod
    def _note_values(note: BidExecutionNote) -> tuple[object, ...]:
        return (
            note.note_id,
            note.bid_id,
            note.subject_kind.value,
            note.subject_id,
            note.body,
            note.created_at.isoformat(),
            note.created_by,
        )

    @staticmethod
    def _mutable_values(record: BidExecutionRecord) -> tuple[object, ...]:
        return (
            record.responsible,
            tuple(record.contributors),
            record.internal_due_date,
            record.prep_status,
            record.priority,
            record.blocked_reason,
            record.completion_evidence,
            record.evidence_document_version_id,
        )

    def get_record(
        self, subject_kind: ExecutionSubjectKind, subject_id: str
    ) -> BidExecutionRecord | None:
        """Fetch the current overlay for one subject, if it has been created."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM bid_execution_records WHERE subject_kind=? AND subject_id=?",
                (subject_kind.value, subject_id),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def save_record(
        self,
        desired: BidExecutionRecord,
        expected_version: int | None,
        actor: str,
        *,
        note: BidExecutionNote | None = None,
    ) -> BidExecutionRecord:
        """Insert or update an overlay and its audit evidence atomically.

        An expected version of ``None`` or ``0`` means the caller expects no row. An
        unchanged desired state does not update the row or write audit evidence.
        """
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM bid_execution_records WHERE subject_kind=? AND subject_id=?",
                    (desired.subject_kind.value, desired.subject_id),
                ).fetchone()
                existing = self._from_row(row) if row is not None else None
                expected_absent = expected_version in (None, 0)
                if existing is None and not expected_absent:
                    raise ValueError(
                        "the execution record changed or is missing; reload the item and save again"
                    )
                if existing is not None and (
                    expected_absent or existing.version != expected_version
                ):
                    raise ValueError(
                        "the execution record was updated by someone else; reload the item and "
                        "apply your changes again"
                    )
                if existing is not None and existing.bid_id != desired.bid_id:
                    raise ValueError(
                        "the execution record belongs to a different Bid; open it from the "
                        "correct Bid workspace"
                    )
                if note is not None and not note.same_subject(desired):
                    raise ValueError(
                        "the progress note targets a different item; reopen the intended item "
                        "and add the note there"
                    )

                changed = existing is None or self._mutable_values(
                    existing
                ) != self._mutable_values(desired)
                if not changed and note is None:
                    conn.commit()
                    return cast(BidExecutionRecord, existing)

                saved = desired
                if existing is None:
                    saved = desired.model_copy(update={"version": 1})
                    conn.execute(
                        "INSERT INTO bid_execution_records VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        self._record_values(saved),
                    )
                elif changed:
                    saved = desired.model_copy(
                        update={
                            "execution_id": existing.execution_id,
                            "version": existing.version + 1,
                            "created_at": existing.created_at,
                        }
                    )
                    values = self._record_values(saved)
                    cursor = conn.execute(
                        """UPDATE bid_execution_records SET
                            responsible=?,contributors_json=?,internal_due_date=?,prep_status=?,
                            priority=?,blocked_reason=?,completion_evidence=?,
                            evidence_document_version_id=?,version=?,updated_at=?,updated_by=?
                            WHERE execution_id=? AND version=?""",
                        values[4:13] + values[14:16] + (existing.execution_id, existing.version),
                    )
                    if cursor.rowcount != 1:
                        raise ValueError(
                            "the execution record was updated by someone else; reload the item "
                            "and apply your changes again"
                        )
                else:
                    saved = existing

                if note is not None:
                    conn.execute(
                        "INSERT INTO bid_execution_notes VALUES (?,?,?,?,?,?,?)",
                        self._note_values(note),
                    )
                detail = json.dumps(
                    {
                        "before": existing.model_dump(mode="json") if existing else None,
                        "after": saved.model_dump(mode="json"),
                        "note_id": note.note_id if note else None,
                    },
                    sort_keys=True,
                )
                action = "bid_execution_record_saved" if changed else "bid_execution_note_added"
                self._audit(conn, saved.bid_id, actor, action, detail)
                conn.commit()
                return saved
            except Exception:
                conn.rollback()
                raise

    def add_note(self, note: BidExecutionNote) -> BidExecutionNote:
        """Append a note and its audit row in one transaction."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO bid_execution_notes VALUES (?,?,?,?,?,?,?)",
                    self._note_values(note),
                )
                self._audit(
                    conn,
                    note.bid_id,
                    note.created_by,
                    "bid_execution_note_added",
                    json.dumps(
                        {
                            "subject_kind": note.subject_kind.value,
                            "subject_id": note.subject_id,
                            "note_id": note.note_id,
                        },
                        sort_keys=True,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return note

    def list_notes(
        self, subject_kind: ExecutionSubjectKind, subject_id: str
    ) -> list[BidExecutionNote]:
        """List immutable notes in creation order for one subject."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM bid_execution_notes WHERE subject_kind=? AND subject_id=? "
                "ORDER BY created_at,note_id",
                (subject_kind.value, subject_id),
            ).fetchall()
        return [self._note_from_row(row) for row in rows]
