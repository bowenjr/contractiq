"""SQLite migration and atomic persistence for authoritative requirements."""

import sqlite3
from datetime import date, datetime
from typing import cast

from core.database import Database
from core.requirements import (
    Requirement,
    RequirementCategory,
    RequirementLifecycle,
    RequirementOrigin,
    RequirementReviewState,
    RequirementSignificance,
    RequirementStage,
    RequirementWorkState,
    ResponseDisposition,
)
from core.schemas import AuditEntry, Provenance

REQUIREMENT_MIGRATION_ID = "task_09_requirements_v1"


class RequirementNotFoundError(ValueError):
    """Raised when an authoritative requirement does not exist."""


class StaleRequirementError(ValueError):
    """Raised when optimistic concurrency rejects an update."""


class RequirementSourceError(ValueError):
    """Raised when controlled source evidence is not authoritative for the bid."""


class RequirementRepository:
    """Persistence boundary for one bid-owned manual requirement register."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._apply_requirements_v1()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _apply_requirements_v1(self) -> None:
        """Apply the additive transactional TASK-09 migration idempotently."""
        statements = (
            """CREATE TABLE IF NOT EXISTS requirements (
                requirement_id TEXT PRIMARY KEY,
                bid_id TEXT NOT NULL,
                title TEXT NOT NULL CHECK (length(trim(title)) > 0),
                statement TEXT NOT NULL CHECK (length(trim(statement)) > 0),
                interpretation TEXT,
                origin TEXT NOT NULL CHECK (origin IN ('EXPLICIT','IMPLIED','INTERNAL')),
                category TEXT NOT NULL CHECK (category IN (
                    'TECHNICAL','SCOPE','COMMERCIAL','CONTRACTUAL','SCHEDULE',
                    'QUALITY','DOCUMENTATION','SUBMISSION','SUPPLIER','REGULATORY','OTHER'
                )),
                significance TEXT NOT NULL CHECK (significance IN (
                    'DISQUALIFYING','MANDATORY','SCORED','INFORMATIONAL'
                )),
                lifecycle_stage TEXT NOT NULL CHECK (
                    lifecycle_stage IN ('BID','POST_AWARD','BOTH')
                ),
                lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN (
                    'ACTIVE','SUPERSEDED','WITHDRAWN'
                )),
                superseded_by_requirement_id TEXT,
                owner TEXT,
                due_date TEXT,
                source_document_id TEXT,
                source_document_version_id TEXT,
                source_clause TEXT,
                source_page_start INTEGER,
                source_page_end INTEGER,
                source_locator_note TEXT,
                source_excerpt TEXT,
                disposition TEXT NOT NULL CHECK (disposition IN (
                    'UNASSESSED','COMPLY','CLARIFY','DEVIATE','EXCLUDE','OPTION','NOT_APPLICABLE'
                )),
                response_text TEXT,
                evidence_description TEXT,
                proposal_location TEXT,
                work_state TEXT NOT NULL CHECK (work_state IN (
                    'OPEN','IN_PROGRESS','READY_FOR_REVIEW','COMPLETE'
                )),
                review_state TEXT NOT NULL CHECK (review_state IN (
                    'NOT_REVIEWED','ACCEPTED','CHANGES_REQUIRED'
                )),
                reviewer TEXT,
                review_note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                version INTEGER NOT NULL CHECK (version >= 1),
                provenance_json TEXT NOT NULL,
                FOREIGN KEY (bid_id) REFERENCES bids(bid_id),
                FOREIGN KEY (source_document_id) REFERENCES documents(id),
                FOREIGN KEY (source_document_version_id)
                    REFERENCES document_versions(document_version_id),
                FOREIGN KEY (superseded_by_requirement_id)
                    REFERENCES requirements(requirement_id),
                CHECK (interpretation IS NULL OR length(trim(interpretation)) > 0),
                CHECK (owner IS NULL OR length(trim(owner)) > 0),
                CHECK (source_clause IS NULL OR length(trim(source_clause)) > 0),
                CHECK (source_locator_note IS NULL OR length(trim(source_locator_note)) > 0),
                CHECK (source_excerpt IS NULL OR length(trim(source_excerpt)) > 0),
                CHECK (response_text IS NULL OR length(trim(response_text)) > 0),
                CHECK (evidence_description IS NULL
                    OR length(trim(evidence_description)) > 0),
                CHECK (proposal_location IS NULL OR length(trim(proposal_location)) > 0),
                CHECK (reviewer IS NULL OR length(trim(reviewer)) > 0),
                CHECK (review_note IS NULL OR length(trim(review_note)) > 0),
                CHECK (length(title) <= 300 AND length(statement) <= 10000),
                CHECK (interpretation IS NULL OR length(interpretation) <= 5000),
                CHECK (source_excerpt IS NULL OR length(source_excerpt) <= 4000),
                CHECK (response_text IS NULL OR length(response_text) <= 10000),
                CHECK (evidence_description IS NULL
                    OR length(evidence_description) <= 5000),
                CHECK (proposal_location IS NULL OR length(proposal_location) <= 1000),
                CHECK (source_page_start IS NULL OR source_page_start >= 1),
                CHECK (source_page_end IS NULL OR source_page_end >= 1),
                CHECK (source_page_end IS NULL OR (
                    source_page_start IS NOT NULL AND source_page_end >= source_page_start
                )),
                CHECK ((source_document_id IS NULL) = (source_document_version_id IS NULL)),
                CHECK (origin <> 'EXPLICIT' OR source_document_version_id IS NOT NULL),
                CHECK (origin <> 'EXPLICIT' OR source_clause IS NOT NULL
                    OR source_page_start IS NOT NULL OR source_locator_note IS NOT NULL
                    OR source_excerpt IS NOT NULL),
                CHECK ((lifecycle_state = 'SUPERSEDED') =
                    (superseded_by_requirement_id IS NOT NULL)),
                CHECK (superseded_by_requirement_id IS NULL
                    OR superseded_by_requirement_id <> requirement_id),
                CHECK (work_state <> 'COMPLETE' OR disposition <> 'UNASSESSED'),
                CHECK (work_state NOT IN ('READY_FOR_REVIEW','COMPLETE')
                    OR disposition NOT IN ('COMPLY','CLARIFY','DEVIATE','EXCLUDE','OPTION')
                    OR response_text IS NOT NULL),
                CHECK (disposition <> 'NOT_APPLICABLE' OR response_text IS NOT NULL),
                CHECK (review_state <> 'ACCEPTED' OR (
                    reviewer IS NOT NULL AND disposition <> 'UNASSESSED'
                    AND work_state IN ('READY_FOR_REVIEW','COMPLETE')
                )),
                CHECK (review_state <> 'CHANGES_REQUIRED' OR (
                    reviewer IS NOT NULL AND work_state <> 'COMPLETE'
                ))
            )""",
            """CREATE INDEX IF NOT EXISTS idx_requirements_bid_lifecycle
                ON requirements(bid_id, lifecycle_state, title, requirement_id)""",
            """CREATE INDEX IF NOT EXISTS idx_requirements_bid_significance
                ON requirements(bid_id, significance, lifecycle_state)""",
            """CREATE INDEX IF NOT EXISTS idx_requirements_owner_due
                ON requirements(owner, due_date, lifecycle_state)""",
            """CREATE INDEX IF NOT EXISTS idx_requirements_workflow
                ON requirements(disposition, work_state, review_state, lifecycle_state)""",
            """CREATE INDEX IF NOT EXISTS idx_requirements_category
                ON requirements(category, lifecycle_state)""",
            """CREATE INDEX IF NOT EXISTS idx_requirements_source_version
                ON requirements(source_document_version_id)""",
            """CREATE INDEX IF NOT EXISTS idx_requirements_search_title
                ON requirements(lower(title), requirement_id)""",
            "DROP TRIGGER IF EXISTS validate_requirement_insert_source",
            "DROP TRIGGER IF EXISTS validate_requirement_update",
            "DROP TRIGGER IF EXISTS prevent_requirement_delete",
            """CREATE TRIGGER validate_requirement_insert_source
            BEFORE INSERT ON requirements
            BEGIN
                SELECT CASE WHEN NEW.source_document_version_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM document_versions v
                    JOIN documents d ON d.id = v.document_id
                    WHERE v.document_version_id = NEW.source_document_version_id
                      AND v.document_id = NEW.source_document_id
                      AND d.control_managed = 1
                      AND d.bid_id = NEW.bid_id
                ) THEN RAISE(ABORT, 'requirement source is not controlled by bid') END;
                SELECT CASE WHEN NEW.lifecycle_state <> 'ACTIVE' THEN
                    RAISE(ABORT, 'new requirement must be ACTIVE') END;
                SELECT CASE WHEN NEW.version <> 1 THEN
                    RAISE(ABORT, 'new requirement version must be one') END;
            END""",
            """CREATE TRIGGER validate_requirement_update
            BEFORE UPDATE ON requirements
            BEGIN
                SELECT CASE WHEN contractiq_requirement_update_allowed() <> 1 THEN
                    RAISE(ABORT, 'requirement updates require audited repository') END;
                SELECT CASE WHEN NEW.requirement_id IS NOT OLD.requirement_id
                    OR NEW.bid_id IS NOT OLD.bid_id OR NEW.origin IS NOT OLD.origin
                    OR NEW.source_document_id IS NOT OLD.source_document_id
                    OR NEW.source_document_version_id IS NOT OLD.source_document_version_id
                    OR NEW.source_clause IS NOT OLD.source_clause
                    OR NEW.source_page_start IS NOT OLD.source_page_start
                    OR NEW.source_page_end IS NOT OLD.source_page_end
                    OR NEW.source_locator_note IS NOT OLD.source_locator_note
                    OR NEW.source_excerpt IS NOT OLD.source_excerpt
                    OR NEW.created_at IS NOT OLD.created_at
                    OR NEW.provenance_json IS NOT OLD.provenance_json THEN
                    RAISE(ABORT, 'requirement identity and source evidence are immutable') END;
                SELECT CASE WHEN OLD.lifecycle_state <> 'ACTIVE'
                    AND NEW.lifecycle_state IS NOT OLD.lifecycle_state THEN
                    RAISE(ABORT, 'closed requirement lifecycle is irreversible') END;
                SELECT CASE WHEN NEW.version <> OLD.version + 1 THEN
                    RAISE(ABORT, 'requirement version must increment by one') END;
            END""",
            """CREATE TRIGGER prevent_requirement_delete
            BEFORE DELETE ON requirements
            BEGIN
                SELECT RAISE(ABORT, 'requirements cannot be deleted');
            END""",
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
    def _optional_str(value: object) -> str | None:
        return None if value is None else str(value)

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> Requirement:
        due_date = cls._optional_str(row["due_date"])
        return Requirement(
            requirement_id=str(row["requirement_id"]),
            bid_id=str(row["bid_id"]),
            title=str(row["title"]),
            statement=str(row["statement"]),
            interpretation=cls._optional_str(row["interpretation"]),
            origin=RequirementOrigin(str(row["origin"])),
            category=RequirementCategory(str(row["category"])),
            significance=RequirementSignificance(str(row["significance"])),
            lifecycle_stage=RequirementStage(str(row["lifecycle_stage"])),
            lifecycle_state=RequirementLifecycle(str(row["lifecycle_state"])),
            superseded_by_requirement_id=cls._optional_str(row["superseded_by_requirement_id"]),
            owner=cls._optional_str(row["owner"]),
            due_date=date.fromisoformat(due_date) if due_date is not None else None,
            source_document_id=cls._optional_str(row["source_document_id"]),
            source_document_version_id=cls._optional_str(row["source_document_version_id"]),
            source_clause=cls._optional_str(row["source_clause"]),
            source_page_start=(
                int(row["source_page_start"]) if row["source_page_start"] is not None else None
            ),
            source_page_end=(
                int(row["source_page_end"]) if row["source_page_end"] is not None else None
            ),
            source_locator_note=cls._optional_str(row["source_locator_note"]),
            source_excerpt=cls._optional_str(row["source_excerpt"]),
            disposition=ResponseDisposition(str(row["disposition"])),
            response_text=cls._optional_str(row["response_text"]),
            evidence_description=cls._optional_str(row["evidence_description"]),
            proposal_location=cls._optional_str(row["proposal_location"]),
            work_state=RequirementWorkState(str(row["work_state"])),
            review_state=RequirementReviewState(str(row["review_state"])),
            reviewer=cls._optional_str(row["reviewer"]),
            review_note=cls._optional_str(row["review_note"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            version=int(row["version"]),
            provenance=Provenance.model_validate_json(str(row["provenance_json"])),
        )

    @staticmethod
    def _values(requirement: Requirement) -> tuple[object, ...]:
        return (
            requirement.requirement_id,
            requirement.bid_id,
            requirement.title,
            requirement.statement,
            requirement.interpretation,
            requirement.origin.value,
            requirement.category.value,
            requirement.significance.value,
            requirement.lifecycle_stage.value,
            requirement.lifecycle_state.value,
            requirement.superseded_by_requirement_id,
            requirement.owner,
            requirement.due_date.isoformat() if requirement.due_date is not None else None,
            requirement.source_document_id,
            requirement.source_document_version_id,
            requirement.source_clause,
            requirement.source_page_start,
            requirement.source_page_end,
            requirement.source_locator_note,
            requirement.source_excerpt,
            requirement.disposition.value,
            requirement.response_text,
            requirement.evidence_description,
            requirement.proposal_location,
            requirement.work_state.value,
            requirement.review_state.value,
            requirement.reviewer,
            requirement.review_note,
            requirement.created_at.isoformat(),
            requirement.updated_at.isoformat(),
            requirement.version,
            requirement.provenance.model_dump_json(),
        )

    @staticmethod
    def _insert_audit(conn: sqlite3.Connection, audit: AuditEntry) -> None:
        conn.execute(
            """INSERT INTO audit_log (entry_id, bid_id, actor, action, detail, timestamp)
            VALUES (?,?,?,?,?,?)""",
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
    def _validate_audit(requirement: Requirement, audit: AuditEntry) -> None:
        if audit.bid_id != requirement.bid_id:
            raise ValueError("audit bid_id must match requirement bid_id")

    def create(self, requirement: Requirement, audit: AuditEntry) -> None:
        """Create requirement, immutable source reference, and audit atomically."""
        self._validate_audit(requirement, audit)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO requirements (
                    requirement_id, bid_id, title, statement, interpretation,
                    origin, category, significance, lifecycle_stage, lifecycle_state,
                    superseded_by_requirement_id, owner, due_date, source_document_id,
                    source_document_version_id, source_clause, source_page_start,
                    source_page_end, source_locator_note, source_excerpt, disposition,
                    response_text, evidence_description, proposal_location, work_state,
                    review_state, reviewer, review_note, created_at, updated_at, version,
                    provenance_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                self._values(requirement),
            )
            self._insert_audit(conn, audit)

    def get(self, requirement_id: str) -> Requirement | None:
        """Fetch one authoritative requirement by stable ID."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM requirements WHERE requirement_id = ?",
                (requirement_id,),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def list(
        self,
        *,
        bid_id: str | None = None,
        origin: RequirementOrigin | None = None,
        category: RequirementCategory | None = None,
        significance: RequirementSignificance | None = None,
        lifecycle: RequirementLifecycle | None = None,
        disposition: ResponseDisposition | None = None,
        work_state: RequirementWorkState | None = None,
        review_state: RequirementReviewState | None = None,
        owner: str | None = None,
    ) -> list[Requirement]:
        """List/filter requirements with stable title/ID ordering."""
        filters: tuple[tuple[str, object | None], ...] = (
            ("bid_id", bid_id),
            ("origin", origin.value if origin is not None else None),
            ("category", category.value if category is not None else None),
            (
                "significance",
                significance.value if significance is not None else None,
            ),
            ("lifecycle_state", lifecycle.value if lifecycle is not None else None),
            ("disposition", disposition.value if disposition is not None else None),
            ("work_state", work_state.value if work_state is not None else None),
            ("review_state", review_state.value if review_state is not None else None),
            ("owner", owner),
        )
        clauses: list[str] = []
        values: list[object] = []
        for column, value in filters:
            if value is not None:
                clauses.append(f"{column} = ?")
                values.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM requirements{where} ORDER BY lower(title), requirement_id",
                values,
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def _replace(
        self,
        requirement: Requirement,
        expected_version: int,
        audit: AuditEntry,
    ) -> None:
        self._validate_audit(requirement, audit)
        if requirement.version != expected_version + 1:
            raise ValueError("replacement version must increment expected_version by one")
        with self._conn() as conn:
            conn.create_function("contractiq_requirement_update_allowed", 0, lambda: 1)
            cursor = conn.execute(
                """UPDATE requirements SET
                    title = ?, statement = ?, interpretation = ?, category = ?,
                    significance = ?, lifecycle_stage = ?, lifecycle_state = ?,
                    superseded_by_requirement_id = ?, owner = ?, due_date = ?,
                    disposition = ?, response_text = ?, evidence_description = ?,
                    proposal_location = ?, work_state = ?, review_state = ?,
                    reviewer = ?, review_note = ?, updated_at = ?, version = ?
                WHERE requirement_id = ? AND version = ?""",
                (
                    requirement.title,
                    requirement.statement,
                    requirement.interpretation,
                    requirement.category.value,
                    requirement.significance.value,
                    requirement.lifecycle_stage.value,
                    requirement.lifecycle_state.value,
                    requirement.superseded_by_requirement_id,
                    requirement.owner,
                    requirement.due_date.isoformat() if requirement.due_date is not None else None,
                    requirement.disposition.value,
                    requirement.response_text,
                    requirement.evidence_description,
                    requirement.proposal_location,
                    requirement.work_state.value,
                    requirement.review_state.value,
                    requirement.reviewer,
                    requirement.review_note,
                    requirement.updated_at.isoformat(),
                    requirement.version,
                    requirement.requirement_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                exists = conn.execute(
                    "SELECT 1 FROM requirements WHERE requirement_id = ?",
                    (requirement.requirement_id,),
                ).fetchone()
                if exists is None:
                    raise RequirementNotFoundError(
                        f"Requirement not found: {requirement.requirement_id}"
                    )
                raise StaleRequirementError(
                    f"Stale requirement version: expected {expected_version}"
                )
            self._insert_audit(conn, audit)

    def update_metadata(
        self,
        requirement: Requirement,
        expected_version: int,
        audit: AuditEntry,
    ) -> None:
        """Persist audited descriptive/assignment changes."""
        current = self._assert_only_changes(
            requirement,
            expected_version,
            {
                "title",
                "statement",
                "interpretation",
                "category",
                "significance",
                "lifecycle_stage",
                "owner",
                "due_date",
                "review_state",
                "reviewer",
                "review_note",
            },
        )
        self._assert_preserved_or_reset_review(current, requirement)
        self._replace(requirement, expected_version, audit)

    def update_workflow(
        self,
        requirement: Requirement,
        expected_version: int,
        audit: AuditEntry,
    ) -> None:
        """Persist audited response and work-state changes."""
        current = self._assert_only_changes(
            requirement,
            expected_version,
            {
                "disposition",
                "response_text",
                "evidence_description",
                "proposal_location",
                "work_state",
                "review_state",
                "reviewer",
                "review_note",
            },
        )
        self._assert_preserved_or_reset_review(current, requirement)
        self._replace(requirement, expected_version, audit)

    def record_review(
        self,
        requirement: Requirement,
        expected_version: int,
        audit: AuditEntry,
    ) -> None:
        """Persist one audited independent review decision."""
        self._assert_only_changes(
            requirement,
            expected_version,
            {"work_state", "review_state", "reviewer", "review_note"},
        )
        if requirement.review_state == RequirementReviewState.NOT_REVIEWED:
            raise ValueError("review operation requires an independent decision")
        self._replace(requirement, expected_version, audit)

    def withdraw(
        self,
        requirement: Requirement,
        expected_version: int,
        audit: AuditEntry,
    ) -> None:
        """Withdraw without deleting source, workflow, or audit history."""
        current = self._assert_only_changes(
            requirement,
            expected_version,
            {"lifecycle_state"},
        )
        if (
            current.lifecycle_state != RequirementLifecycle.ACTIVE
            or requirement.lifecycle_state != RequirementLifecycle.WITHDRAWN
        ):
            raise ValueError("withdrawal must transition ACTIVE to WITHDRAWN")
        self._replace(requirement, expected_version, audit)

    def _assert_only_changes(
        self,
        requirement: Requirement,
        expected_version: int,
        allowed: set[str],
    ) -> Requirement:
        """Reject silent cross-operation mutation at the repository boundary."""
        current = self.get(requirement.requirement_id)
        if current is None:
            raise RequirementNotFoundError(f"Requirement not found: {requirement.requirement_id}")
        if current.version != expected_version:
            raise StaleRequirementError(
                f"Stale requirement version: expected {expected_version}, current {current.version}"
            )
        permitted = allowed | {"updated_at", "version"}
        current_values = current.model_dump()
        replacement_values = requirement.model_dump()
        changed = {
            field
            for field in Requirement.model_fields
            if current_values[field] != replacement_values[field]
        }
        disallowed = sorted(changed - permitted)
        if disallowed:
            raise ValueError("repository operation cannot change fields: " + ", ".join(disallowed))
        if requirement.version != current.version + 1:
            raise ValueError("replacement version must increment by one")
        return current

    @staticmethod
    def _assert_preserved_or_reset_review(
        current: Requirement,
        replacement: Requirement,
    ) -> None:
        old_review = (current.review_state, current.reviewer, current.review_note)
        new_review = (
            replacement.review_state,
            replacement.reviewer,
            replacement.review_note,
        )
        reset_review = (RequirementReviewState.NOT_REVIEWED, None, None)
        if new_review not in {old_review, reset_review}:
            raise ValueError("metadata/workflow operations may only preserve or reset review")
