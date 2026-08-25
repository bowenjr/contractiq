"""SQLite persistence for unpublished OPS-05B bid-stage VDRL control."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any, cast

from core.database import Database
from core.schemas import AuditEntry, Provenance
from core.vendor_document_control import (
    BulkVerificationUpdate,
    CustomerRequirement,
    ManufacturerVerification,
    SupplierPackage,
    VdrlTemplate,
)

VENDOR_DOCUMENT_MIGRATION_ID = "ops_05_bid_stage_vdrl_v1"


class VendorDocumentNotFoundError(ValueError):
    pass


class StaleVendorDocumentError(ValueError):
    pass


class VendorDocumentRepository:
    """Persist Bid-owned VDRL requirements without an execution-document model."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._migrate()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _migrate(self) -> None:
        statements = (
            """CREATE TABLE IF NOT EXISTS vendor_vdrl_templates(
                template_id TEXT PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL,
                stages_json TEXT NOT NULL, provenance_json TEXT NOT NULL,
                created_at TEXT NOT NULL, UNIQUE(name,version))""",
            """CREATE TABLE IF NOT EXISTS vendor_bid_packages(
                package_id TEXT PRIMARY KEY, bid_id TEXT NOT NULL,
                package_name TEXT NOT NULL, package_code TEXT NOT NULL,
                customer_epcm TEXT, proposed_manufacturer TEXT NOT NULL,
                manufacturer_contact TEXT, internal_owner TEXT NOT NULL,
                source_vdrl_reference TEXT, source_revision TEXT,
                anticipated_award_date TEXT, forecast_delivery_date TEXT, notes TEXT,
                template_id TEXT NOT NULL, template_version INTEGER NOT NULL,
                version_token TEXT NOT NULL, provenance_json TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(bid_id) REFERENCES bids(bid_id),
                FOREIGN KEY(template_id) REFERENCES vendor_vdrl_templates(template_id),
                UNIQUE(bid_id,package_code))""",
            """CREATE TABLE IF NOT EXISTS vendor_bid_requirements(
                requirement_id TEXT PRIMARY KEY, package_id TEXT NOT NULL,
                customer_requirement_code TEXT NOT NULL, deliverable_title TEXT NOT NULL,
                description TEXT NOT NULL, required INTEGER NOT NULL,
                requested_stages_json TEXT NOT NULL, timing_anchor TEXT NOT NULL,
                timing_offset_days INTEGER NOT NULL, original_contractual_timing TEXT,
                original_contractual_date TEXT, customer_notes TEXT,
                source_row_reference TEXT, source_revision TEXT, applicable INTEGER NOT NULL,
                verification_status TEXT NOT NULL, proposed_manufacturer TEXT,
                response_source TEXT, internal_owner TEXT,
                confirmation_requested_date TEXT, response_received_date TEXT,
                committed_stages_json TEXT NOT NULL, committed_timing TEXT,
                evidence_reference TEXT, manufacturer_notes TEXT, proposed_exception TEXT,
                commercial_impact TEXT NOT NULL, bid_disposition TEXT NOT NULL,
                disposition_approved INTEGER NOT NULL, clarification_reference TEXT,
                deviation_reference TEXT, unresolved_action TEXT, handover_note TEXT,
                version INTEGER NOT NULL, provenance_json TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(package_id) REFERENCES vendor_bid_packages(package_id),
                UNIQUE(package_id,customer_requirement_code))""",
            """CREATE TABLE IF NOT EXISTS vendor_document_schema_migrations(
                migration_id TEXT PRIMARY KEY, applied_at TEXT NOT NULL)""",
            """CREATE INDEX IF NOT EXISTS idx_vendor_bid_packages_bid
                ON vendor_bid_packages(bid_id,package_code)""",
            """CREATE INDEX IF NOT EXISTS idx_vendor_bid_requirements_register
                ON vendor_bid_requirements(package_id,verification_status,commercial_impact)""",
            """CREATE TRIGGER IF NOT EXISTS vendor_customer_requirement_immutable
                BEFORE UPDATE OF customer_requirement_code,deliverable_title,description,required,
                    requested_stages_json,timing_anchor,timing_offset_days,
                    original_contractual_timing,original_contractual_date,customer_notes,
                    source_row_reference,source_revision,applicable
                ON vendor_bid_requirements
                BEGIN SELECT RAISE(ABORT,'original customer VDRL requirement is immutable'); END""",
        )
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT OR IGNORE INTO vendor_document_schema_migrations VALUES(?,?)",
                    (VENDOR_DOCUMENT_MIGRATION_ID, datetime.now(UTC).isoformat()),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _insert_audit(conn: sqlite3.Connection, audit: AuditEntry) -> None:
        conn.execute(
            "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) "
            "VALUES(?,?,?,?,?,?)",
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
    def _date(value: object) -> str | None:
        return value.isoformat() if hasattr(value, "isoformat") else None

    @staticmethod
    def _template(row: sqlite3.Row) -> VdrlTemplate:
        return VdrlTemplate.model_validate(
            {
                "template_id": row["template_id"],
                "name": row["name"],
                "version": row["version"],
                "stages": json.loads(row["stages_json"]),
                "provenance": Provenance.model_validate_json(row["provenance_json"]),
                "created_at": row["created_at"],
            }
        )

    @staticmethod
    def _package(row: sqlite3.Row) -> SupplierPackage:
        return SupplierPackage.model_validate(
            {key: row[key] for key in SupplierPackage.model_fields if key not in {"provenance"}}
            | {"provenance": Provenance.model_validate_json(row["provenance_json"])}
        )

    @staticmethod
    def _requirement(row: sqlite3.Row) -> CustomerRequirement:
        values = dict(row)
        values["requested_stages"] = json.loads(values.pop("requested_stages_json"))
        values["committed_stages"] = json.loads(values.pop("committed_stages_json"))
        values["provenance"] = Provenance.model_validate_json(values.pop("provenance_json"))
        return CustomerRequirement.model_validate(values)

    def ensure_template(self, template: VdrlTemplate) -> VdrlTemplate:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO vendor_vdrl_templates(
                    template_id,name,version,stages_json,provenance_json,created_at
                ) VALUES(?,?,?,?,?,?)""",
                (
                    template.template_id,
                    template.name,
                    template.version,
                    json.dumps([stage.model_dump(mode="json") for stage in template.stages]),
                    template.provenance.model_dump_json(),
                    template.created_at.isoformat(),
                ),
            )
            conn.commit()
        stored = self.get_template(template.template_id)
        if stored is None:
            raise RuntimeError("standard VDRL template initialization failed")
        return stored

    def get_template(self, template_id: str) -> VdrlTemplate | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM vendor_vdrl_templates WHERE template_id=?", (template_id,)
            ).fetchone()
        return self._template(row) if row is not None else None

    def create_package(self, package: SupplierPackage, audit: AuditEntry) -> SupplierPackage:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """INSERT INTO vendor_bid_packages(
                        package_id,bid_id,package_name,package_code,customer_epcm,
                        proposed_manufacturer,manufacturer_contact,internal_owner,
                        source_vdrl_reference,source_revision,anticipated_award_date,
                        forecast_delivery_date,notes,template_id,template_version,
                        version_token,provenance_json,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        package.package_id,
                        package.bid_id,
                        package.package_name,
                        package.package_code,
                        package.customer_epcm,
                        package.proposed_manufacturer,
                        package.manufacturer_contact,
                        package.internal_owner,
                        package.source_vdrl_reference,
                        package.source_revision,
                        self._date(package.anticipated_award_date),
                        self._date(package.forecast_delivery_date),
                        package.notes,
                        package.template_id,
                        package.template_version,
                        package.version_token,
                        package.provenance.model_dump_json(),
                        package.created_at.isoformat(),
                        package.updated_at.isoformat(),
                    ),
                )
                self._insert_audit(conn, audit)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return package

    def get_package(self, package_id: str) -> SupplierPackage | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM vendor_bid_packages WHERE package_id=?", (package_id,)
            ).fetchone()
        return self._package(row) if row is not None else None

    def list_packages(self, bid_id: str | None = None) -> list[SupplierPackage]:
        with self._conn() as conn:
            if bid_id is None:
                rows = conn.execute(
                    "SELECT * FROM vendor_bid_packages ORDER BY bid_id,package_code,package_id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM vendor_bid_packages WHERE bid_id=? "
                    "ORDER BY package_code,package_id",
                    (bid_id,),
                ).fetchall()
        return [self._package(row) for row in rows]

    @staticmethod
    def _requirement_values(requirement: CustomerRequirement) -> tuple[Any, ...]:
        return (
            requirement.requirement_id,
            requirement.package_id,
            requirement.customer_requirement_code,
            requirement.deliverable_title,
            requirement.description or "",
            int(requirement.required),
            json.dumps(requirement.requested_stages),
            requirement.timing_anchor.value,
            requirement.timing_offset_days,
            requirement.original_contractual_timing,
            VendorDocumentRepository._date(requirement.original_contractual_date),
            requirement.customer_notes,
            requirement.source_row_reference,
            requirement.source_revision,
            int(requirement.applicable),
            requirement.verification_status.value,
            requirement.proposed_manufacturer,
            requirement.response_source,
            requirement.internal_owner,
            requirement.confirmation_requested_date,
            requirement.response_received_date,
            json.dumps(requirement.committed_stages),
            requirement.committed_timing,
            requirement.evidence_reference,
            requirement.manufacturer_notes,
            requirement.proposed_exception,
            requirement.commercial_impact.value,
            requirement.bid_disposition.value,
            int(requirement.disposition_approved),
            requirement.clarification_reference,
            requirement.deviation_reference,
            requirement.unresolved_action,
            requirement.handover_note,
            requirement.version,
            requirement.provenance.model_dump_json(),
            requirement.created_at.isoformat(),
            requirement.updated_at.isoformat(),
        )

    @classmethod
    def _insert_requirement(
        cls, conn: sqlite3.Connection, requirement: CustomerRequirement
    ) -> None:
        conn.execute(
            """INSERT INTO vendor_bid_requirements(
                requirement_id,package_id,customer_requirement_code,deliverable_title,
                description,required,requested_stages_json,timing_anchor,timing_offset_days,
                original_contractual_timing,original_contractual_date,customer_notes,
                source_row_reference,source_revision,applicable,verification_status,
                proposed_manufacturer,response_source,internal_owner,confirmation_requested_date,
                response_received_date,committed_stages_json,committed_timing,evidence_reference,
                manufacturer_notes,proposed_exception,commercial_impact,bid_disposition,
                disposition_approved,clarification_reference,deviation_reference,
                unresolved_action,handover_note,version,provenance_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            cls._requirement_values(requirement),
        )

    def create_requirement(
        self, requirement: CustomerRequirement, audit: AuditEntry
    ) -> CustomerRequirement:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._insert_requirement(conn, requirement)
                self._insert_audit(conn, audit)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return requirement

    def import_requirements(
        self, requirements: list[CustomerRequirement], audit: AuditEntry
    ) -> list[CustomerRequirement]:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for requirement in requirements:
                    self._insert_requirement(conn, requirement)
                self._insert_audit(conn, audit)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return requirements

    def get_requirement(self, requirement_id: str) -> CustomerRequirement | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM vendor_bid_requirements WHERE requirement_id=?",
                (requirement_id,),
            ).fetchone()
        return self._requirement(row) if row is not None else None

    def list_requirements(self, package_id: str | None = None) -> list[CustomerRequirement]:
        with self._conn() as conn:
            if package_id is None:
                rows = conn.execute(
                    "SELECT * FROM vendor_bid_requirements "
                    "ORDER BY package_id,customer_requirement_code,requirement_id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM vendor_bid_requirements WHERE package_id=? "
                    "ORDER BY customer_requirement_code,requirement_id",
                    (package_id,),
                ).fetchall()
        return [self._requirement(row) for row in rows]

    @staticmethod
    def _verification_values(value: ManufacturerVerification) -> tuple[Any, ...]:
        return (
            value.verification_status.value,
            value.proposed_manufacturer,
            value.response_source,
            value.internal_owner,
            VendorDocumentRepository._date(value.confirmation_requested_date),
            VendorDocumentRepository._date(value.response_received_date),
            json.dumps(value.committed_stages),
            value.committed_timing,
            value.evidence_reference,
            value.manufacturer_notes,
            value.proposed_exception,
            value.commercial_impact.value,
            value.bid_disposition.value,
            int(value.disposition_approved),
            value.clarification_reference,
            value.deviation_reference,
            value.unresolved_action,
            value.handover_note,
        )

    def update_verification(
        self,
        requirement_id: str,
        expected_version: int,
        value: ManufacturerVerification,
        updated_at: datetime,
        audit: AuditEntry,
    ) -> CustomerRequirement:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = conn.execute(
                    """UPDATE vendor_bid_requirements SET
                        verification_status=?,proposed_manufacturer=?,response_source=?,
                        internal_owner=?,confirmation_requested_date=?,response_received_date=?,
                        committed_stages_json=?,committed_timing=?,evidence_reference=?,
                        manufacturer_notes=?,proposed_exception=?,commercial_impact=?,
                        bid_disposition=?,disposition_approved=?,clarification_reference=?,
                        deviation_reference=?,unresolved_action=?,handover_note=?,
                        version=version+1,updated_at=?
                    WHERE requirement_id=? AND version=?""",
                    self._verification_values(value)
                    + (updated_at.isoformat(), requirement_id, expected_version),
                )
                if result.rowcount != 1:
                    raise StaleVendorDocumentError(
                        "requirement changed in another tab; reload before saving"
                    )
                self._insert_audit(conn, audit)
                row = conn.execute(
                    "SELECT * FROM vendor_bid_requirements WHERE requirement_id=?",
                    (requirement_id,),
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        if row is None:
            raise VendorDocumentNotFoundError("customer requirement not found")
        return self._requirement(row)

    def bulk_update(
        self,
        update: BulkVerificationUpdate,
        values: dict[str, Any],
        updated_at: datetime,
        audit: AuditEntry,
    ) -> None:
        assignments = [f"{column}=?" for column in values]
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for target in update.targets:
                    result = conn.execute(
                        f"""UPDATE vendor_bid_requirements SET {",".join(assignments)},
                            version=version+1,updated_at=?
                            WHERE requirement_id=? AND version=?""",  # noqa: S608 - allowlisted columns
                        tuple(values.values())
                        + (updated_at.isoformat(), target.requirement_id, target.expected_version),
                    )
                    if result.rowcount != 1:
                        raise StaleVendorDocumentError(
                            "one or more requirements changed; no bulk changes were saved"
                        )
                self._insert_audit(conn, audit)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def audit(self, bid_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE bid_id=? "
                "AND action LIKE 'vendor_vdrl_%' ORDER BY timestamp,entry_id",
                (bid_id,),
            ).fetchall()
        return [dict(row) for row in rows]
