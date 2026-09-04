"""Additive OPS-07 relationships, bulk assignment, and handover reporting."""
# ruff: noqa: E501

from __future__ import annotations

import csv
import io
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.database import Database
from core.export_controls import csv_safe_cell
from core.handover import assess_manufacturer_handover
from core.requirement_repository import RequirementNotFoundError, StaleRequirementError


class BulkRequirementTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    expected_version: int = Field(ge=1)


class BulkResponsibilityAssignment(BaseModel):
    """Independent assignments; omitted fields are preserved and explicit null clears."""

    model_config = ConfigDict(extra="forbid")

    targets: list[BulkRequirementTarget] = Field(min_length=1)
    owner: str | None = Field(default=None, max_length=200)
    contributor: str | None = Field(default=None, max_length=200)
    reviewer: str | None = Field(default=None, max_length=200)
    clear_owner: bool = False
    clear_contributor: bool = False
    clear_reviewer: bool = False

    @model_validator(mode="after")
    def validate_assignment(self) -> BulkResponsibilityAssignment:
        fields = self.model_fields_set
        if not fields.intersection({"owner", "contributor", "reviewer"}) and not any(
            (self.clear_owner, self.clear_contributor, self.clear_reviewer)
        ):
            raise ValueError("Choose an owner, contributor, reviewer, or an explicit Clear action")
        if len({target.requirement_id for target in self.targets}) != len(self.targets):
            raise ValueError("A requirement may only be selected once")
        return self


class Ops07Repository:
    """Transactional persistence for OPS-07 additive junctions and bulk assignment."""

    def __init__(self, db: Database, now_factory: Callable[[], datetime] | None = None) -> None:
        self.db = db
        self._now = now_factory or (lambda: datetime.now(UTC))
        self._migrate()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _migrate(self) -> None:
        statements = (
            """CREATE TABLE IF NOT EXISTS requirement_manufacturer_links(
                link_id TEXT PRIMARY KEY, bid_id TEXT NOT NULL, requirement_id TEXT NOT NULL,
                package_id TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
                UNIQUE(requirement_id, package_id),
                FOREIGN KEY(bid_id) REFERENCES bids(bid_id),
                FOREIGN KEY(requirement_id) REFERENCES requirements(requirement_id),
                FOREIGN KEY(package_id) REFERENCES vendor_bid_packages(package_id))""",
            """CREATE TABLE IF NOT EXISTS domain_work_item_links(
                link_id TEXT PRIMARY KEY, bid_id TEXT NOT NULL, work_item_id TEXT NOT NULL,
                purpose TEXT NOT NULL CHECK(length(trim(purpose)) > 0),
                requirement_id TEXT, scope_item_id TEXT, interface_id TEXT, package_id TEXT,
                created_at TEXT NOT NULL, created_by TEXT NOT NULL,
                FOREIGN KEY(bid_id) REFERENCES bids(bid_id),
                FOREIGN KEY(work_item_id) REFERENCES work_items(work_item_id),
                FOREIGN KEY(requirement_id) REFERENCES requirements(requirement_id),
                FOREIGN KEY(scope_item_id) REFERENCES scope_interface_items(scope_item_id),
                FOREIGN KEY(interface_id) REFERENCES scope_interfaces(interface_id),
                FOREIGN KEY(package_id) REFERENCES vendor_bid_packages(package_id),
                CHECK((requirement_id IS NOT NULL) + (scope_item_id IS NOT NULL) +
                      (interface_id IS NOT NULL) + (package_id IS NOT NULL) = 1))""",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_ops07_work_requirement ON domain_work_item_links(requirement_id,purpose) WHERE requirement_id IS NOT NULL",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_ops07_work_scope ON domain_work_item_links(scope_item_id,purpose) WHERE scope_item_id IS NOT NULL",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_ops07_work_interface ON domain_work_item_links(interface_id,purpose) WHERE interface_id IS NOT NULL",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_ops07_work_package ON domain_work_item_links(package_id,purpose) WHERE package_id IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_ops07_manufacturer_requirement ON requirement_manufacturer_links(requirement_id,package_id)",
        )
        with self._conn() as conn:
            for statement in statements:
                conn.execute(statement)

    @staticmethod
    def _audit(
        conn: sqlite3.Connection,
        bid_id: str,
        actor: str,
        action: str,
        detail: dict[str, object],
        at: datetime,
    ) -> None:
        conn.execute(
            "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) VALUES(?,?,?,?,?,?)",
            (
                f"AUD-{uuid4()}",
                bid_id,
                actor,
                action,
                json.dumps(detail, sort_keys=True),
                at.isoformat(),
            ),
        )

    def bulk_assign(self, bid_id: str, command: BulkResponsibilityAssignment, actor: str) -> None:
        """Validate every row first, then update versions and audits in one transaction."""
        at = self._now()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows: list[sqlite3.Row] = []
            for target in command.targets:
                row = conn.execute(
                    "SELECT * FROM requirements WHERE requirement_id=?", (target.requirement_id,)
                ).fetchone()
                if row is None:
                    raise RequirementNotFoundError(
                        f"Requirement not found: {target.requirement_id}"
                    )
                if str(row["bid_id"]) != bid_id or str(row["lifecycle_state"]) != "ACTIVE":
                    raise ValueError(
                        "Every selected requirement must be active and belong to this Bid"
                    )
                if int(row["version"]) != target.expected_version:
                    raise StaleRequirementError(
                        f"Stale requirement version: {target.requirement_id}"
                    )
                rows.append(row)
            conn.create_function("contractiq_requirement_update_allowed", 0, lambda: 1)
            fields = command.model_fields_set
            for row, target in zip(rows, command.targets, strict=True):
                owner = (
                    None
                    if command.clear_owner
                    else command.owner
                    if "owner" in fields
                    else row["owner"]
                )
                contributor = (
                    None
                    if command.clear_contributor
                    else command.contributor
                    if "contributor" in fields
                    else row["contributor"]
                )
                reviewer = (
                    None
                    if command.clear_reviewer
                    else command.reviewer
                    if "reviewer" in fields
                    else row["reviewer"]
                )
                cursor = conn.execute(
                    """UPDATE requirements SET owner=?,contributor=?,reviewer=?,updated_at=?,version=version+1
                    WHERE requirement_id=? AND version=?""",
                    (
                        owner,
                        contributor,
                        reviewer,
                        at.isoformat(),
                        target.requirement_id,
                        target.expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StaleRequirementError(
                        f"Stale requirement version: {target.requirement_id}"
                    )
                self._audit(
                    conn,
                    bid_id,
                    actor,
                    "requirement_responsibility_bulk_assigned",
                    {
                        "requirement_id": target.requirement_id,
                        "owner": owner,
                        "contributor": contributor,
                        "reviewer": reviewer,
                    },
                    at,
                )

    def link_scope(self, bid_id: str, requirement_id: str, scope_item_id: str, actor: str) -> None:
        self._link_same_bid(
            "scope_interface_items", "scope_item_id", scope_item_id, bid_id, requirement_id, actor
        )

    def _link_same_bid(
        self, table: str, key: str, value: str, bid_id: str, requirement_id: str, actor: str
    ) -> None:
        at = self._now()
        with self._conn() as conn:
            requirement = conn.execute(
                "SELECT bid_id FROM requirements WHERE requirement_id=?", (requirement_id,)
            ).fetchone()
            related = conn.execute(f"SELECT bid_id FROM {table} WHERE {key}=?", (value,)).fetchone()
            if requirement is None or related is None:
                raise RequirementNotFoundError("Requirement or related record was not found")
            if str(requirement["bid_id"]) != bid_id or str(related["bid_id"]) != bid_id:
                raise ValueError("Relationship records must belong to the same Bid")
            try:
                conn.execute(
                    "INSERT INTO requirement_scope_links(link_id,requirement_id,scope_item_id,bid_id,created_at,created_by) VALUES(?,?,?,?,?,?)",
                    (f"RSL-{uuid4()}", requirement_id, value, bid_id, at.isoformat(), actor),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("This scope item is already linked") from exc
            self._audit(
                conn,
                bid_id,
                actor,
                "requirement_scope_linked",
                {"requirement_id": requirement_id, "scope_item_id": value},
                at,
            )

    def unlink_scope(
        self, bid_id: str, requirement_id: str, scope_item_id: str, actor: str
    ) -> None:
        self._unlink(
            "requirement_scope_links",
            "scope_item_id",
            scope_item_id,
            bid_id,
            requirement_id,
            actor,
            "requirement_scope_unlinked",
        )

    def link_manufacturer(
        self, bid_id: str, requirement_id: str, package_id: str, actor: str
    ) -> None:
        at = self._now()
        with self._conn() as conn:
            req = conn.execute(
                "SELECT bid_id FROM requirements WHERE requirement_id=?", (requirement_id,)
            ).fetchone()
            package = conn.execute(
                "SELECT bid_id FROM vendor_bid_packages WHERE package_id=?", (package_id,)
            ).fetchone()
            if req is None or package is None:
                raise RequirementNotFoundError("Requirement or manufacturer package was not found")
            if str(req["bid_id"]) != bid_id or str(package["bid_id"]) != bid_id:
                raise ValueError("Relationship records must belong to the same Bid")
            try:
                conn.execute(
                    "INSERT INTO requirement_manufacturer_links VALUES(?,?,?,?,?,?)",
                    (f"RML-{uuid4()}", bid_id, requirement_id, package_id, at.isoformat(), actor),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("This manufacturer package is already linked") from exc
            self._audit(
                conn,
                bid_id,
                actor,
                "requirement_manufacturer_linked",
                {"requirement_id": requirement_id, "package_id": package_id},
                at,
            )

    def unlink_manufacturer(
        self, bid_id: str, requirement_id: str, package_id: str, actor: str
    ) -> None:
        self._unlink(
            "requirement_manufacturer_links",
            "package_id",
            package_id,
            bid_id,
            requirement_id,
            actor,
            "requirement_manufacturer_unlinked",
        )

    def _unlink(
        self,
        table: str,
        related_key: str,
        related_id: str,
        bid_id: str,
        requirement_id: str,
        actor: str,
        action: str,
    ) -> None:
        at = self._now()
        with self._conn() as conn:
            cursor = conn.execute(
                f"DELETE FROM {table} WHERE bid_id=? AND requirement_id=? AND {related_key}=?",
                (bid_id, requirement_id, related_id),
            )
            if cursor.rowcount != 1:
                raise RequirementNotFoundError("Relationship was not found")
            self._audit(
                conn,
                bid_id,
                actor,
                action,
                {"requirement_id": requirement_id, related_key: related_id},
                at,
            )

    def coverage(self, requirement_id: str) -> dict[str, list[dict[str, object]]]:
        with self._conn() as conn:
            scopes = [
                dict(row)
                for row in conn.execute(
                    """SELECT s.* FROM scope_interface_items s JOIN requirement_scope_links l ON l.scope_item_id=s.scope_item_id WHERE l.requirement_id=? ORDER BY lower(s.title),s.scope_item_id""",
                    (requirement_id,),
                )
            ]
            packages = [
                dict(row)
                for row in conn.execute(
                    """SELECT p.* FROM vendor_bid_packages p JOIN requirement_manufacturer_links l ON l.package_id=p.package_id WHERE l.requirement_id=? ORDER BY lower(p.package_name),p.package_id""",
                    (requirement_id,),
                )
            ]
            interfaces = [
                dict(row)
                for row in conn.execute(
                    """SELECT DISTINCT i.* FROM scope_interfaces i JOIN interface_scope_links isl ON isl.interface_id=i.interface_id JOIN requirement_scope_links rsl ON rsl.scope_item_id=isl.scope_item_id WHERE rsl.requirement_id=? ORDER BY lower(i.title),i.interface_id""",
                    (requirement_id,),
                )
            ]
            work = [
                dict(row)
                for row in conn.execute(
                    """SELECT w.* FROM work_items w JOIN domain_work_item_links l ON l.work_item_id=w.work_item_id WHERE l.requirement_id=? ORDER BY w.created_at,w.work_item_id""",
                    (requirement_id,),
                )
            ]
        return {"scopes": scopes, "packages": packages, "interfaces": interfaces, "work": work}

    def link_work_item(
        self,
        *,
        bid_id: str,
        work_item_id: str,
        source_kind: str,
        source_id: str,
        purpose: str,
        actor: str,
    ) -> None:
        """Link one existing action to one validated authoritative domain source."""
        source_map = {
            "requirement": ("requirements", "requirement_id"),
            "scope": ("scope_interface_items", "scope_item_id"),
            "interface": ("scope_interfaces", "interface_id"),
            "manufacturer": ("vendor_bid_packages", "package_id"),
        }
        if source_kind not in source_map:
            raise ValueError("Unsupported My Work source kind")
        normalized_purpose = purpose.strip()
        if not normalized_purpose:
            raise ValueError("My Work link purpose must be non-empty")
        table, column = source_map[source_kind]
        at = self._now()
        with self._conn() as conn:
            source = conn.execute(
                f"SELECT bid_id FROM {table} WHERE {column}=?", (source_id,)
            ).fetchone()
            work = conn.execute(
                "SELECT bid_id FROM work_items WHERE work_item_id=?", (work_item_id,)
            ).fetchone()
            if source is None or work is None:
                raise ValueError("My Work action or authoritative source was not found")
            if str(source["bid_id"]) != bid_id or str(work["bid_id"] or "") != bid_id:
                raise ValueError("My Work action and source must belong to the same Bid")
            values: dict[str, str | None] = {
                "requirement_id": None,
                "scope_item_id": None,
                "interface_id": None,
                "package_id": None,
            }
            values[column] = source_id
            try:
                conn.execute(
                    """INSERT INTO domain_work_item_links(
                    link_id,bid_id,work_item_id,purpose,requirement_id,scope_item_id,
                    interface_id,package_id,created_at,created_by)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        f"DWL-{uuid4()}",
                        bid_id,
                        work_item_id,
                        normalized_purpose,
                        values["requirement_id"],
                        values["scope_item_id"],
                        values["interface_id"],
                        values["package_id"],
                        at.isoformat(),
                        actor,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("An action is already linked for this source and purpose") from exc
            self._audit(
                conn,
                bid_id,
                actor,
                "domain_work_item_linked",
                {
                    "source_kind": source_kind,
                    "source_id": source_id,
                    "work_item_id": work_item_id,
                    "purpose": normalized_purpose,
                },
                at,
            )

    def handover_csv(self, bid_id: str) -> str:
        """Produce deterministic Bid-scoped coverage output without inferring compliance."""
        with self._conn() as conn:
            bid = conn.execute(
                "SELECT bid_id,project_name,customer FROM bids WHERE bid_id=?", (bid_id,)
            ).fetchone()
            if bid is None:
                raise ValueError(f"Bid not found: {bid_id}")
            requirements = conn.execute(
                "SELECT * FROM requirements WHERE bid_id=? ORDER BY lower(title),requirement_id",
                (bid_id,),
            ).fetchall()
            output = io.StringIO(newline="")
            columns = [
                "bid_id",
                "project_name",
                "customer",
                "requirement_id",
                "original_requirement",
                "source_document_id",
                "source_version_id",
                "disposition",
                "proposed_response",
                "owner",
                "contributor",
                "reviewer",
                "scope_items",
                "interfaces",
                "manufacturer_packages",
                "manufacturer_verification",
                "manufacturer_response_status",
                "response_evidence_status",
                "internal_accountability_status",
                "commercial_disposition_status",
                "exact_manufacturer_evidence",
                "response_sources",
                "response_dates",
                "evidence_notes",
                "commercial_impact",
                "unresolved_action",
                "linked_my_work",
                "readiness_state",
                "handover_readiness",
                "handover_blocking_reasons",
            ]
            writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            for requirement in requirements:
                rid = str(requirement["requirement_id"])
                coverage = self.coverage(rid)
                package_ids = [str(item["package_id"]) for item in coverage["packages"]]
                # Exact evidence is deliberate. Package responsibility alone must never cause
                # unrelated package-wide VDRL rows to appear as requirement evidence.
                exact_table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='requirement_verification_links'"
                ).fetchone()
                vendor_rows: list[sqlite3.Row] = []
                if exact_table is not None:
                    vendor_rows = conn.execute(
                        """SELECT v.* FROM requirement_verification_links l
                        JOIN vendor_bid_requirements v
                          ON v.requirement_id=l.verification_row_id
                        WHERE l.requirement_id=?
                        ORDER BY v.package_id,v.customer_requirement_code,v.requirement_id""",
                        (rid,),
                    ).fetchall()
                statuses = [str(row["verification_status"]) for row in vendor_rows]
                package_ids_with_rows = {str(row["package_id"]) for row in vendor_rows}
                packages_without_rows = [
                    package_id
                    for package_id in package_ids
                    if package_id not in package_ids_with_rows
                ]
                assessments = [
                    assess_manufacturer_handover(
                        required=bool(row["required"]),
                        applicable=bool(row["applicable"]),
                        verification_status=row["verification_status"],
                        response_source=row["response_source"],
                        response_received_date=row["response_received_date"],
                        internal_owner=row["internal_owner"],
                        commercial_impact=row["commercial_impact"],
                        bid_disposition=row["bid_disposition"],
                        disposition_approved=bool(row["disposition_approved"]),
                        unresolved_action=row["unresolved_action"],
                    )
                    for row in vendor_rows
                ]
                issues = [issue for item in assessments for issue in item.blocking_reasons]
                issues.extend(
                    f"Manufacturer package {package_id} has no exact linked verification evidence"
                    for package_id in packages_without_rows
                )
                handover_unresolved = (
                    not package_ids
                    or not vendor_rows
                    or bool(packages_without_rows)
                    or any(not item.ready for item in assessments)
                )
                row = {
                    "bid_id": bid_id,
                    "project_name": bid["project_name"],
                    "customer": bid["customer"],
                    "requirement_id": rid,
                    "original_requirement": requirement["statement"],
                    "source_document_id": requirement["source_document_id"] or "",
                    "source_version_id": requirement["source_document_version_id"] or "",
                    "disposition": requirement["disposition"],
                    "proposed_response": requirement["response_text"] or "",
                    "owner": requirement["owner"] or "",
                    "contributor": requirement["contributor"] or "",
                    "reviewer": requirement["reviewer"] or "",
                    "scope_items": " | ".join(str(item["title"]) for item in coverage["scopes"]),
                    "interfaces": " | ".join(str(item["title"]) for item in coverage["interfaces"]),
                    "manufacturer_packages": " | ".join(
                        f"{item['package_code']}: {item['proposed_manufacturer']}"
                        for item in coverage["packages"]
                    ),
                    "manufacturer_verification": " | ".join(statuses) if statuses else "UNANSWERED",
                    "manufacturer_response_status": " | ".join(
                        item.response_status for item in assessments
                    )
                    or "Unanswered",
                    "response_evidence_status": " | ".join(
                        item.evidence_status for item in assessments
                    )
                    or "Incomplete",
                    "internal_accountability_status": " | ".join(
                        item.accountability_status for item in assessments
                    )
                    or "Missing accountability",
                    "commercial_disposition_status": " | ".join(
                        item.commercial_disposition_status for item in assessments
                    )
                    or "Unresolved",
                    "exact_manufacturer_evidence": " | ".join(
                        f"{row['customer_requirement_code']}: {row['deliverable_title']}"
                        for row in vendor_rows
                    ),
                    "response_sources": " | ".join(
                        str(row["response_source"] or "") for row in vendor_rows
                    ),
                    "response_dates": " | ".join(
                        str(row["response_received_date"] or "") for row in vendor_rows
                    ),
                    "evidence_notes": " | ".join(
                        str(row["evidence_reference"] or row["manufacturer_notes"] or "")
                        for row in vendor_rows
                    )
                    or " | ".join(issue for issue in issues if issue),
                    "commercial_impact": " | ".join(
                        str(row["commercial_impact"]) for row in vendor_rows
                    ),
                    "unresolved_action": " | ".join(
                        str(row["unresolved_action"] or "") for row in vendor_rows
                    )
                    or " | ".join(issue for issue in issues if issue),
                    "linked_my_work": " | ".join(
                        str(item["work_item_id"]) for item in coverage["work"]
                    ),
                    "readiness_state": "NOT_READY" if handover_unresolved else "READY",
                    "handover_readiness": "Not ready" if handover_unresolved else "Ready",
                    "handover_blocking_reasons": " | ".join(issue for issue in issues if issue),
                }
                writer.writerow({key: csv_safe_cell(value) for key, value in row.items()})
        return output.getvalue()
