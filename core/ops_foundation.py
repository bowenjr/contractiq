"""OPS-01 role framework and deterministic operational taxonomy."""
# ruff: noqa: E501

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from pydantic import ValidationError

from core.database import Database
from core.role_profiles import (
    RoleProfile,
    RoleProfileCreate,
    RoleProfilePublicationResult,
    RoleProfileState,
)
from core.schemas import AuditEntry, Provenance

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


class RoleProfileNotFoundError(ValueError):
    """Raised when a stable role-profile ID does not exist."""


class StaleRoleProfileError(ValueError):
    """Raised when optimistic concurrency rejects a role-profile mutation."""


class RoleProfileLifecycleError(ValueError):
    """Raised when a role-profile lifecycle operation is not allowed."""


class RoleProfileOverlapError(ValueError):
    """Raised when effective published role-profile windows overlap."""


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

    @staticmethod
    def _optional_text(value: object) -> str | None:
        return None if value is None else str(value)

    @classmethod
    def _role_profile_from_row(cls, row: sqlite3.Row) -> RoleProfile:
        provenance: Provenance | None = None
        raw_provenance = cls._optional_text(row["provenance_json"])
        if raw_provenance and raw_provenance != "{}":
            try:
                provenance = Provenance.model_validate_json(raw_provenance)
            except (ValidationError, ValueError):
                provenance = None
        domains = json.loads(str(row["domains_json"]))
        return RoleProfile(
            profile_id=str(row["profile_id"]),
            version_number=int(row["version_number"]),
            title=str(row["title"] or ""),
            organization=cls._optional_text(row["organization"]),
            mission=str(row["mission"]),
            boundaries=str(row["boundaries"]),
            coordination=str(row["coordination"]),
            outcomes=str(row["outcomes"]),
            cadence=str(row["cadence"]),
            domains=domains,
            effective_from=date.fromisoformat(str(row["effective_from"])),
            effective_until=(
                date.fromisoformat(str(row["effective_until"]))
                if row["effective_until"] is not None
                else None
            ),
            state=RoleProfileState(str(row["state"])),
            created_by=str(row["created_by"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            parent_profile_id=cls._optional_text(row["parent_profile_id"]),
            version_token=str(row["version_token"]),
            provenance=provenance,
        )

    @staticmethod
    def _profile_values(profile: RoleProfile) -> tuple[object, ...]:
        return (
            profile.profile_id,
            profile.version_number,
            profile.title,
            profile.organization,
            profile.mission,
            profile.boundaries,
            profile.coordination,
            profile.outcomes,
            profile.cadence,
            json.dumps([domain.value for domain in profile.domains]),
            profile.effective_from.isoformat(),
            profile.effective_until.isoformat() if profile.effective_until else None,
            profile.state.value,
            profile.created_by,
            profile.created_at.isoformat(),
            profile.parent_profile_id,
            profile.version_token,
            profile.provenance.model_dump_json() if profile.provenance is not None else None,
        )

    @staticmethod
    def _insert_audit(conn: sqlite3.Connection, entry: AuditEntry) -> None:
        if entry.bid_id is not None:
            raise ValueError("role-profile audit entries must use bid_id=NULL")
        conn.execute(
            "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) "
            "VALUES(?,?,?,?,?,?)",
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
    def _require_token(profile: RoleProfile, expected_token: str) -> None:
        if profile.version_token != expected_token:
            raise StaleRoleProfileError(f"Stale role profile token for {profile.profile_id}")

    @staticmethod
    def _published_overlap(
        conn: sqlite3.Connection,
        profile: RoleProfile,
        *,
        excluded_profile_ids: tuple[str, ...] = (),
    ) -> sqlite3.Row | None:
        clauses = [
            "state='PUBLISHED'",
            "effective_from <= ?",
            "COALESCE(effective_until, '9999-12-31') >= ?",
        ]
        values: list[object] = [
            profile.effective_until.isoformat() if profile.effective_until else "9999-12-31",
            profile.effective_from.isoformat(),
        ]
        if excluded_profile_ids:
            placeholders = ",".join("?" for _ in excluded_profile_ids)
            clauses.append(f"profile_id NOT IN ({placeholders})")
            values.extend(excluded_profile_ids)
        return cast(
            sqlite3.Row | None,
            conn.execute(
                f"SELECT * FROM ops_role_profiles WHERE {' AND '.join(clauses)} "
                "ORDER BY version_number LIMIT 1",
                values,
            ).fetchone(),
        )

    def get_role_profile(self, profile_id: str) -> RoleProfile | None:
        """Return one typed role-profile snapshot without mutation."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM ops_role_profiles WHERE profile_id=?",
                (profile_id,),
            ).fetchone()
        return self._role_profile_from_row(row) if row is not None else None

    def list_role_profiles(self) -> list[RoleProfile]:
        """Return all role-profile versions in deterministic newest-first order."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM ops_role_profiles ORDER BY version_number DESC, profile_id"
            ).fetchall()
        return [self._role_profile_from_row(row) for row in rows]

    def list_role_profile_children(self, profile_id: str) -> list[RoleProfile]:
        """Return direct child versions for a controlled profile."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM ops_role_profiles WHERE parent_profile_id=? "
                "ORDER BY version_number, profile_id",
                (profile_id,),
            ).fetchall()
        return [self._role_profile_from_row(row) for row in rows]

    def list_role_profile_audit(self, profile_id: str) -> list[AuditEntry]:
        """Return global role audit events referring to one profile."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE bid_id IS NULL "
                "AND action LIKE 'role_profile_%' ORDER BY timestamp, entry_id"
            ).fetchall()
        entries: list[AuditEntry] = []
        for row in rows:
            try:
                detail = json.loads(str(row["detail"]))
            except json.JSONDecodeError:
                continue
            related = {
                detail.get("profile_id"),
                detail.get("parent_profile_id"),
                detail.get("child_profile_id"),
            }
            if profile_id not in related:
                continue
            entries.append(
                AuditEntry(
                    entry_id=str(row["entry_id"]),
                    bid_id=None,
                    actor=str(row["actor"]),
                    action=str(row["action"]),
                    detail=str(row["detail"]),
                    timestamp=datetime.fromisoformat(str(row["timestamp"])),
                )
            )
        return entries

    def create_role_profile_draft(
        self,
        request: RoleProfileCreate,
        *,
        profile_id: str,
        created_by: str,
        created_at: datetime,
        version_token: str,
        provenance: Provenance,
        audit_factory: Callable[[RoleProfile], AuditEntry],
    ) -> RoleProfile:
        """Allocate, create, and audit a DRAFT in one immediate transaction."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            version_number = int(
                conn.execute(
                    "SELECT COALESCE(MAX(version_number),0)+1 FROM ops_role_profiles"
                ).fetchone()[0]
            )
            profile = RoleProfile(
                profile_id=profile_id,
                version_number=version_number,
                state=RoleProfileState.DRAFT,
                created_by=created_by,
                created_at=created_at,
                parent_profile_id=None,
                version_token=version_token,
                provenance=provenance,
                **request.model_dump(),
            )
            conn.execute(
                "INSERT INTO ops_role_profiles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                self._profile_values(profile),
            )
            self._insert_audit(conn, audit_factory(profile))
        return profile

    def update_role_profile_draft(
        self,
        profile: RoleProfile,
        *,
        expected_token: str,
        audit_entry: AuditEntry,
    ) -> None:
        """Replace DRAFT authored content and its audit atomically."""
        if profile.state != RoleProfileState.DRAFT:
            raise RoleProfileLifecycleError("Only DRAFT role profiles can be edited")
        with self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE ops_role_profiles SET
                    title=?, organization=?, mission=?, boundaries=?, coordination=?,
                    outcomes=?, cadence=?, domains_json=?, effective_from=?, effective_until=?,
                    version_token=?
                WHERE profile_id=? AND state='DRAFT' AND version_token=?
                """,
                (
                    profile.title,
                    profile.organization,
                    profile.mission,
                    profile.boundaries,
                    profile.coordination,
                    profile.outcomes,
                    profile.cadence,
                    json.dumps([domain.value for domain in profile.domains]),
                    profile.effective_from.isoformat(),
                    profile.effective_until.isoformat() if profile.effective_until else None,
                    profile.version_token,
                    profile.profile_id,
                    expected_token,
                ),
            )
            if cursor.rowcount == 0:
                row = conn.execute(
                    "SELECT * FROM ops_role_profiles WHERE profile_id=?",
                    (profile.profile_id,),
                ).fetchone()
                if row is None:
                    raise RoleProfileNotFoundError(f"Role profile not found: {profile.profile_id}")
                current = self._role_profile_from_row(row)
                if current.state != RoleProfileState.DRAFT:
                    raise RoleProfileLifecycleError("Only DRAFT role profiles can be edited")
                raise StaleRoleProfileError(f"Stale role profile token for {profile.profile_id}")
            self._insert_audit(conn, audit_entry)

    def create_role_profile_revision(
        self,
        parent_profile_id: str,
        *,
        expected_parent_token: str,
        child_profile_id: str,
        child_version_token: str,
        parent_version_token: str,
        created_by: str,
        created_at: datetime,
        provenance: Provenance,
        audit_factory: Callable[[RoleProfile, RoleProfile, RoleProfile], AuditEntry],
    ) -> tuple[RoleProfile, RoleProfile]:
        """Copy one controlled profile to a single child DRAFT atomically."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM ops_role_profiles WHERE profile_id=?",
                (parent_profile_id,),
            ).fetchone()
            if row is None:
                raise RoleProfileNotFoundError(f"Role profile not found: {parent_profile_id}")
            parent_before = self._role_profile_from_row(row)
            self._require_token(parent_before, expected_parent_token)
            if parent_before.state not in {
                RoleProfileState.PUBLISHED,
                RoleProfileState.RETIRED,
            }:
                raise RoleProfileLifecycleError(
                    "Only PUBLISHED or RETIRED role profiles can be revised"
                )
            existing_child = conn.execute(
                "SELECT profile_id FROM ops_role_profiles "
                "WHERE parent_profile_id=? AND state='DRAFT' LIMIT 1",
                (parent_profile_id,),
            ).fetchone()
            if existing_child is not None:
                raise RoleProfileLifecycleError(
                    "An active DRAFT revision already exists for this profile"
                )
            version_number = int(
                conn.execute(
                    "SELECT COALESCE(MAX(version_number),0)+1 FROM ops_role_profiles"
                ).fetchone()[0]
            )
            child = parent_before.model_copy(
                update={
                    "profile_id": child_profile_id,
                    "version_number": version_number,
                    "state": RoleProfileState.DRAFT,
                    "created_by": created_by,
                    "created_at": created_at,
                    "parent_profile_id": parent_profile_id,
                    "version_token": child_version_token,
                    "provenance": provenance,
                }
            )
            parent_after = parent_before.model_copy(update={"version_token": parent_version_token})
            cursor = conn.execute(
                "UPDATE ops_role_profiles SET version_token=? "
                "WHERE profile_id=? AND version_token=?",
                (parent_version_token, parent_profile_id, expected_parent_token),
            )
            if cursor.rowcount != 1:
                raise StaleRoleProfileError(f"Stale role profile token for {parent_profile_id}")
            conn.execute(
                "INSERT INTO ops_role_profiles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                self._profile_values(child),
            )
            self._insert_audit(conn, audit_factory(parent_before, parent_after, child))
        return parent_after, child

    def publish_role_profile_draft(
        self,
        profile_id: str,
        *,
        expected_token: str,
        parent_expected_token: str | None,
        published_token: str,
        parent_token: str,
        audit_factory: Callable[
            [RoleProfile, RoleProfile, RoleProfile | None, RoleProfile | None],
            list[AuditEntry],
        ],
    ) -> RoleProfilePublicationResult:
        """Publish a DRAFT, atomically superseding its PUBLISHED parent when applicable."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM ops_role_profiles WHERE profile_id=?",
                (profile_id,),
            ).fetchone()
            if row is None:
                raise RoleProfileNotFoundError(f"Role profile not found: {profile_id}")
            draft_before = self._role_profile_from_row(row)
            self._require_token(draft_before, expected_token)
            if draft_before.state != RoleProfileState.DRAFT:
                raise RoleProfileLifecycleError("Only DRAFT role profiles can be published")
            draft_before.validate_for_publication()

            parent_before: RoleProfile | None = None
            parent_after: RoleProfile | None = None
            excluded: tuple[str, ...] = (profile_id,)
            if draft_before.parent_profile_id is not None:
                parent_row = conn.execute(
                    "SELECT * FROM ops_role_profiles WHERE profile_id=?",
                    (draft_before.parent_profile_id,),
                ).fetchone()
                if parent_row is None:
                    raise RoleProfileLifecycleError("Revision parent no longer exists")
                parent_before = self._role_profile_from_row(parent_row)
                if parent_before.state == RoleProfileState.PUBLISHED:
                    if parent_expected_token is None:
                        raise StaleRoleProfileError(
                            "Publishing this revision requires the parent expected token"
                        )
                    self._require_token(parent_before, parent_expected_token)
                    if draft_before.effective_from <= parent_before.effective_from:
                        raise RoleProfileLifecycleError(
                            "A published revision must start after its parent"
                        )
                    closed_at = draft_before.effective_from - timedelta(days=1)
                    parent_until = parent_before.effective_until
                    if parent_until is None or parent_until >= draft_before.effective_from:
                        parent_until = closed_at
                    parent_after = parent_before.model_copy(
                        update={
                            "effective_until": parent_until,
                            "version_token": parent_token,
                        }
                    )
                    excluded = (profile_id, parent_before.profile_id)
                elif parent_before.state != RoleProfileState.RETIRED:
                    raise RoleProfileLifecycleError(
                        "A revision parent must be PUBLISHED or RETIRED"
                    )

            overlap = self._published_overlap(
                conn,
                draft_before,
                excluded_profile_ids=excluded,
            )
            if overlap is not None:
                raise RoleProfileOverlapError(
                    f"Published role profile effective window overlaps {overlap['profile_id']}"
                )

            published = draft_before.model_copy(
                update={
                    "state": RoleProfileState.PUBLISHED,
                    "version_token": published_token,
                }
            )
            if parent_before is not None and parent_after is not None:
                cursor = conn.execute(
                    "UPDATE ops_role_profiles SET effective_until=?, version_token=? "
                    "WHERE profile_id=? AND state='PUBLISHED' AND version_token=?",
                    (
                        parent_after.effective_until.isoformat()
                        if parent_after.effective_until
                        else None,
                        parent_after.version_token,
                        parent_before.profile_id,
                        parent_expected_token,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StaleRoleProfileError(
                        f"Stale role profile token for {parent_before.profile_id}"
                    )
            cursor = conn.execute(
                "UPDATE ops_role_profiles SET state='PUBLISHED',version_token=? "
                "WHERE profile_id=? AND state='DRAFT' AND version_token=?",
                (published_token, profile_id, expected_token),
            )
            if cursor.rowcount != 1:
                raise StaleRoleProfileError(f"Stale role profile token for {profile_id}")
            for entry in audit_factory(
                draft_before,
                published,
                parent_before,
                parent_after,
            ):
                self._insert_audit(conn, entry)
        return RoleProfilePublicationResult(
            published=published,
            superseded_parent=parent_after,
        )

    def retire_role_profile_version(
        self,
        profile_id: str,
        *,
        expected_token: str,
        retired_token: str,
        audit_factory: Callable[[RoleProfile, RoleProfile], AuditEntry],
    ) -> RoleProfile:
        """Retire one PUBLISHED profile and audit it atomically."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM ops_role_profiles WHERE profile_id=?",
                (profile_id,),
            ).fetchone()
            if row is None:
                raise RoleProfileNotFoundError(f"Role profile not found: {profile_id}")
            before = self._role_profile_from_row(row)
            self._require_token(before, expected_token)
            if before.state != RoleProfileState.PUBLISHED:
                raise RoleProfileLifecycleError("Only PUBLISHED role profiles can be retired")
            retired = before.model_copy(
                update={
                    "state": RoleProfileState.RETIRED,
                    "version_token": retired_token,
                }
            )
            cursor = conn.execute(
                "UPDATE ops_role_profiles SET state='RETIRED',version_token=? "
                "WHERE profile_id=? AND state='PUBLISHED' AND version_token=?",
                (retired_token, profile_id, expected_token),
            )
            if cursor.rowcount != 1:
                raise StaleRoleProfileError(f"Stale role profile token for {profile_id}")
            self._insert_audit(conn, audit_factory(before, retired))
        return retired

    def effective_role_profile(self, as_of: date) -> RoleProfile | None:
        """Return exactly one effective PUBLISHED profile for an explicit date."""
        target = as_of.isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM ops_role_profiles WHERE state='PUBLISHED' "
                "AND effective_from<=? AND (effective_until IS NULL OR effective_until>=?) "
                "ORDER BY version_number, profile_id",
                (target, target),
            ).fetchall()
        if len(rows) > 1:
            raise RoleProfileOverlapError(
                f"Multiple PUBLISHED role profiles are effective on {target}"
            )
        return self._role_profile_from_row(rows[0]) if rows else None

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
