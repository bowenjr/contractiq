"""SQLite migration and atomic persistence for controlled documents."""

import sqlite3
from datetime import date, datetime
from typing import cast

from core.database import Database
from core.document_control import (
    ControlledDocument,
    DocumentCategory,
    DocumentLifecycle,
    DocumentVersion,
    DocumentVersionState,
)
from core.schemas import AuditEntry, Provenance

DOCUMENT_CONTROL_MIGRATION_ID = "task_08_document_control_v1"


class ControlledDocumentNotFoundError(ValueError):
    """Raised when a controlled logical document does not exist."""


class DocumentVersionNotFoundError(ValueError):
    """Raised when an immutable document version does not exist."""


class StaleDocumentError(ValueError):
    """Raised when document/current-version expectations are stale."""


class DuplicateDocumentVersionError(ValueError):
    """Raised when identical bytes already exist for one logical document."""


class DocumentRepository:
    """The supported persistence interface for controlled document evidence."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._apply_document_control_v1()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _apply_document_control_v1(self) -> None:
        """Apply the additive, idempotent TASK-08 migration."""
        additions = {
            "control_managed": "INTEGER NOT NULL DEFAULT 0 CHECK (control_managed IN (0,1))",
            "control_title": "TEXT",
            "document_number": "TEXT",
            "document_category": "TEXT",
            "issuer": "TEXT",
            "control_lifecycle": "TEXT",
            "current_version_id": "TEXT",
            "control_created_at": "TEXT",
            "control_updated_at": "TEXT",
            "control_version": "INTEGER",
            "control_provenance_json": "TEXT",
        }
        with self._conn() as conn:
            columns = {
                str(row["name"]) for row in conn.execute("PRAGMA table_info(documents)").fetchall()
            }
            for name, declaration in additions.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE documents ADD COLUMN {name} {declaration}")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS document_versions (
                    document_version_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    version_label TEXT NOT NULL CHECK (length(trim(version_label)) > 0),
                    issued_date TEXT,
                    received_at TEXT,
                    original_filename TEXT NOT NULL
                        CHECK (length(trim(original_filename)) > 0),
                    media_type TEXT,
                    byte_size INTEGER NOT NULL CHECK (byte_size > 0),
                    sha256_digest TEXT NOT NULL CHECK (
                        length(sha256_digest) = 64
                        AND sha256_digest = lower(sha256_digest)
                        AND sha256_digest NOT GLOB '*[^0-9a-f]*'
                    ),
                    storage_key TEXT NOT NULL UNIQUE CHECK (
                        length(trim(storage_key)) > 0
                        AND substr(storage_key, 1, 1) <> '/'
                        AND instr(storage_key, '..') = 0
                    ),
                    predecessor_version_id TEXT,
                    version_state TEXT NOT NULL CHECK (
                        version_state IN ('CURRENT', 'SUPERSEDED')
                    ),
                    created_at TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    FOREIGN KEY (document_id) REFERENCES documents(id),
                    FOREIGN KEY (predecessor_version_id)
                        REFERENCES document_versions(document_version_id),
                    UNIQUE (document_id, sha256_digest)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS uq_document_versions_current
                    ON document_versions(document_id) WHERE version_state = 'CURRENT';
                CREATE INDEX IF NOT EXISTS idx_control_documents_bid_category_state_title
                    ON documents(
                        bid_id, document_category, control_lifecycle, control_title
                    ) WHERE control_managed = 1;
                CREATE INDEX IF NOT EXISTS idx_document_versions_document_state_created
                    ON document_versions(document_id, version_state, created_at);
                CREATE INDEX IF NOT EXISTS idx_document_versions_document_hash
                    ON document_versions(document_id, sha256_digest);

                CREATE TRIGGER IF NOT EXISTS validate_controlled_document_insert
                BEFORE INSERT ON documents
                WHEN NEW.control_managed = 1
                BEGIN
                    SELECT CASE WHEN NEW.bid_id IS NULL THEN
                        RAISE(ABORT, 'controlled document requires bid_id') END;
                    SELECT CASE WHEN NEW.control_title IS NULL
                        OR length(trim(NEW.control_title)) = 0 THEN
                        RAISE(ABORT, 'controlled document requires title') END;
                    SELECT CASE WHEN NEW.document_category IS NULL
                        OR NEW.document_category NOT IN (
                        'SOLICITATION','ADDENDUM','SPECIFICATION','DRAWING','COMMERCIAL',
                        'CONTRACTUAL','SUPPLIER','INTERNAL','DELIVERABLE','OTHER'
                    ) THEN RAISE(ABORT, 'invalid controlled document category') END;
                    SELECT CASE WHEN NEW.control_lifecycle IS NULL
                        OR NEW.control_lifecycle NOT IN ('ACTIVE','WITHDRAWN') THEN
                        RAISE(ABORT, 'invalid controlled document lifecycle') END;
                    SELECT CASE WHEN NEW.current_version_id IS NULL THEN
                        RAISE(ABORT, 'controlled document requires current version') END;
                    SELECT CASE WHEN NEW.control_version IS NULL OR NEW.control_version < 1 THEN
                        RAISE(ABORT, 'controlled document version must be positive') END;
                END;

                CREATE TRIGGER IF NOT EXISTS validate_controlled_document_update
                BEFORE UPDATE OF control_title, document_category, control_lifecycle,
                    current_version_id, control_version ON documents
                WHEN NEW.control_managed = 1
                BEGIN
                    SELECT CASE WHEN NEW.control_title IS NULL
                        OR length(trim(NEW.control_title)) = 0 THEN
                        RAISE(ABORT, 'controlled document requires title') END;
                    SELECT CASE WHEN NEW.document_category IS NULL
                        OR NEW.document_category NOT IN (
                        'SOLICITATION','ADDENDUM','SPECIFICATION','DRAWING','COMMERCIAL',
                        'CONTRACTUAL','SUPPLIER','INTERNAL','DELIVERABLE','OTHER'
                    ) THEN RAISE(ABORT, 'invalid controlled document category') END;
                    SELECT CASE WHEN NEW.control_lifecycle IS NULL
                        OR NEW.control_lifecycle NOT IN ('ACTIVE','WITHDRAWN') THEN
                        RAISE(ABORT, 'invalid controlled document lifecycle') END;
                    SELECT CASE WHEN NEW.current_version_id IS NULL THEN
                        RAISE(ABORT, 'controlled document requires current version') END;
                    SELECT CASE WHEN NEW.control_version IS NULL OR NEW.control_version < 1 THEN
                        RAISE(ABORT, 'controlled document version must be positive') END;
                END;
                """
            )

    @staticmethod
    def _optional_str(value: object) -> str | None:
        return None if value is None else str(value)

    @classmethod
    def _document_from_row(cls, row: sqlite3.Row) -> ControlledDocument:
        return ControlledDocument(
            document_id=str(row["id"]),
            bid_id=str(row["bid_id"]),
            title=str(row["control_title"]),
            document_number=cls._optional_str(row["document_number"]),
            category=DocumentCategory(str(row["document_category"])),
            issuer=cls._optional_str(row["issuer"]),
            notes=cls._optional_str(row["notes"]),
            lifecycle_state=DocumentLifecycle(str(row["control_lifecycle"])),
            current_version_id=str(row["current_version_id"]),
            created_at=datetime.fromisoformat(str(row["control_created_at"])),
            updated_at=datetime.fromisoformat(str(row["control_updated_at"])),
            version=int(row["control_version"]),
            provenance=Provenance.model_validate_json(str(row["control_provenance_json"])),
        )

    @classmethod
    def _version_from_row(cls, row: sqlite3.Row) -> DocumentVersion:
        issued = cls._optional_str(row["issued_date"])
        received = cls._optional_str(row["received_at"])
        return DocumentVersion(
            document_version_id=str(row["document_version_id"]),
            document_id=str(row["document_id"]),
            version_label=str(row["version_label"]),
            issued_date=date.fromisoformat(issued) if issued is not None else None,
            received_at=datetime.fromisoformat(received) if received is not None else None,
            original_filename=str(row["original_filename"]),
            media_type=cls._optional_str(row["media_type"]),
            byte_size=int(row["byte_size"]),
            sha256_digest=str(row["sha256_digest"]),
            storage_key=str(row["storage_key"]),
            predecessor_version_id=cls._optional_str(row["predecessor_version_id"]),
            version_state=DocumentVersionState(str(row["version_state"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            provenance=Provenance.model_validate_json(str(row["provenance_json"])),
        )

    @staticmethod
    def _insert_audit(conn: sqlite3.Connection, audit: AuditEntry) -> None:
        conn.execute(
            """
            INSERT INTO audit_log (entry_id, bid_id, actor, action, detail, timestamp)
            VALUES (?,?,?,?,?,?)
            """,
            (
                audit.entry_id,
                audit.bid_id,
                audit.actor,
                audit.action,
                audit.detail,
                audit.timestamp.isoformat(),
            ),
        )

    @staticmethod
    def _version_values(version: DocumentVersion) -> tuple[object, ...]:
        return (
            version.document_version_id,
            version.document_id,
            version.version_label,
            version.issued_date.isoformat() if version.issued_date is not None else None,
            version.received_at.isoformat() if version.received_at is not None else None,
            version.original_filename,
            version.media_type,
            version.byte_size,
            version.sha256_digest,
            version.storage_key,
            version.predecessor_version_id,
            version.version_state.value,
            version.created_at.isoformat(),
            version.provenance.model_dump_json(),
        )

    def create_with_first_version(
        self,
        document: ControlledDocument,
        version: DocumentVersion,
        audit: AuditEntry,
    ) -> None:
        """Atomically create a logical document, first version, and audit record."""
        if version.document_id != document.document_id:
            raise ValueError("first version must belong to the logical document")
        if version.version_state != DocumentVersionState.CURRENT:
            raise ValueError("first version must be CURRENT")
        if version.predecessor_version_id is not None:
            raise ValueError("first version cannot have a predecessor")
        if document.current_version_id != version.document_version_id:
            raise ValueError("current version pointer must identify the first version")
        if audit.bid_id != document.bid_id:
            raise ValueError("audit bid_id must match document bid_id")
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO documents (
                    id, bid_id, filename, status, upload_date, notes,
                    control_managed, control_title, document_number,
                    document_category, issuer, control_lifecycle,
                    current_version_id, control_created_at, control_updated_at,
                    control_version, control_provenance_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    document.document_id,
                    document.bid_id,
                    version.original_filename,
                    "controlled",
                    document.created_at.isoformat(),
                    document.notes,
                    1,
                    document.title,
                    document.document_number,
                    document.category.value,
                    document.issuer,
                    document.lifecycle_state.value,
                    document.current_version_id,
                    document.created_at.isoformat(),
                    document.updated_at.isoformat(),
                    document.version,
                    document.provenance.model_dump_json(),
                ),
            )
            conn.execute(
                """
                INSERT INTO document_versions (
                    document_version_id, document_id, version_label, issued_date,
                    received_at, original_filename, media_type, byte_size,
                    sha256_digest, storage_key, predecessor_version_id,
                    version_state, created_at, provenance_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                self._version_values(version),
            )
            self._insert_audit(conn, audit)

    def get(self, document_id: str) -> ControlledDocument | None:
        """Fetch one controlled logical document."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE id = ? AND control_managed = 1",
                (document_id,),
            ).fetchone()
        return self._document_from_row(row) if row is not None else None

    def list_documents(
        self,
        *,
        bid_id: str | None = None,
        category: DocumentCategory | None = None,
        lifecycle: DocumentLifecycle | None = None,
    ) -> list[ControlledDocument]:
        """List controlled documents using deterministic filters and ordering."""
        clauses = ["control_managed = 1"]
        values: list[object] = []
        if bid_id is not None:
            clauses.append("bid_id = ?")
            values.append(bid_id)
        if category is not None:
            clauses.append("document_category = ?")
            values.append(category.value)
        if lifecycle is not None:
            clauses.append("control_lifecycle = ?")
            values.append(lifecycle.value)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM documents WHERE {' AND '.join(clauses)} "
                "ORDER BY lower(control_title), id",
                values,
            ).fetchall()
        return [self._document_from_row(row) for row in rows]

    def get_version(self, document_version_id: str) -> DocumentVersion | None:
        """Fetch immutable version evidence by stable ID."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM document_versions WHERE document_version_id = ?",
                (document_version_id,),
            ).fetchone()
        return self._version_from_row(row) if row is not None else None

    def list_versions(self, document_id: str) -> list[DocumentVersion]:
        """Return current then prior versions in deterministic newest-first order."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM document_versions WHERE document_id = ?
                ORDER BY CASE version_state WHEN 'CURRENT' THEN 0 ELSE 1 END,
                         created_at DESC, document_version_id DESC
                """,
                (document_id,),
            ).fetchall()
        return [self._version_from_row(row) for row in rows]

    def has_digest(self, document_id: str, sha256_digest: str) -> bool:
        """Return whether identical bytes already belong to this document."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM document_versions WHERE document_id = ? AND sha256_digest = ?",
                (document_id, sha256_digest),
            ).fetchone()
        return row is not None

    def update_metadata(
        self,
        document: ControlledDocument,
        expected_version: int,
        audit: AuditEntry,
    ) -> None:
        """Persist an optimistic metadata update and audit it atomically."""
        with self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE documents SET control_title = ?, document_number = ?,
                    document_category = ?, issuer = ?, notes = ?,
                    control_updated_at = ?, control_version = ?
                WHERE id = ? AND control_managed = 1 AND control_version = ?
                """,
                (
                    document.title,
                    document.document_number,
                    document.category.value,
                    document.issuer,
                    document.notes,
                    document.updated_at.isoformat(),
                    document.version,
                    document.document_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleDocumentError("controlled document changed before metadata update")
            self._insert_audit(conn, audit)

    def add_version(
        self,
        document: ControlledDocument,
        new_version: DocumentVersion,
        expected_version: int,
        expected_current_version_id: str,
        audit: AuditEntry,
    ) -> None:
        """Atomically supersede the prior current version and insert its successor."""
        if new_version.document_id != document.document_id:
            raise ValueError("new version must belong to the logical document")
        if new_version.predecessor_version_id != expected_current_version_id:
            raise ValueError("predecessor must be the expected current version")
        if document.current_version_id != new_version.document_version_id:
            raise ValueError("document pointer must identify the new version")
        try:
            with self._conn() as conn:
                current = conn.execute(
                    """
                    SELECT current_version_id, control_version FROM documents
                    WHERE id = ? AND control_managed = 1
                    """,
                    (document.document_id,),
                ).fetchone()
                if current is None:
                    raise ControlledDocumentNotFoundError(
                        f"Controlled document not found: {document.document_id}"
                    )
                if (
                    int(current["control_version"]) != expected_version
                    or str(current["current_version_id"]) != expected_current_version_id
                ):
                    raise StaleDocumentError("controlled document/current version changed")
                superseded = conn.execute(
                    """
                    UPDATE document_versions SET version_state = 'SUPERSEDED'
                    WHERE document_version_id = ? AND document_id = ?
                      AND version_state = 'CURRENT'
                    """,
                    (expected_current_version_id, document.document_id),
                )
                if superseded.rowcount != 1:
                    raise StaleDocumentError("expected predecessor is not current")
                conn.execute(
                    """
                    INSERT INTO document_versions (
                        document_version_id, document_id, version_label, issued_date,
                        received_at, original_filename, media_type, byte_size,
                        sha256_digest, storage_key, predecessor_version_id,
                        version_state, created_at, provenance_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    self._version_values(new_version),
                )
                updated = conn.execute(
                    """
                    UPDATE documents SET current_version_id = ?, control_updated_at = ?,
                        control_version = ?
                    WHERE id = ? AND control_version = ? AND current_version_id = ?
                    """,
                    (
                        document.current_version_id,
                        document.updated_at.isoformat(),
                        document.version,
                        document.document_id,
                        expected_version,
                        expected_current_version_id,
                    ),
                )
                if updated.rowcount != 1:
                    raise StaleDocumentError("controlled document changed during version add")
                self._insert_audit(conn, audit)
        except sqlite3.IntegrityError as exc:
            if "document_versions.document_id, document_versions.sha256_digest" in str(exc):
                raise DuplicateDocumentVersionError(
                    "identical bytes already exist for this controlled document"
                ) from exc
            raise

    def withdraw(
        self,
        document: ControlledDocument,
        expected_version: int,
        audit: AuditEntry,
    ) -> None:
        """Withdraw without deleting logical or immutable version history."""
        with self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE documents SET control_lifecycle = 'WITHDRAWN',
                    control_updated_at = ?, control_version = ?
                WHERE id = ? AND control_managed = 1 AND control_version = ?
                """,
                (
                    document.updated_at.isoformat(),
                    document.version,
                    document.document_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleDocumentError("controlled document changed before withdrawal")
            self._insert_audit(conn, audit)

    def referenced_storage_keys(self) -> set[str]:
        """Return opaque keys referenced by committed version evidence."""
        with self._conn() as conn:
            rows = conn.execute("SELECT storage_key FROM document_versions").fetchall()
        return {str(row["storage_key"]) for row in rows}

    def all_versions(self) -> list[DocumentVersion]:
        """Return all committed version evidence for diagnostics."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM document_versions ORDER BY document_id, created_at"
            ).fetchall()
        return [self._version_from_row(row) for row in rows]
