"""OPS-09 local proposal exchange, readiness, import and issue controls."""

# SQL and frozen-contract projection expressions are intentionally kept together.
# ruff: noqa: E501

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from core.bid_repository import BidRepository
from core.database import Database
from core.proposal_exchange import (
    CustomerIssueCommand,
    ImportedArtifact,
    IssuedOfferBaseline,
    ManifestReceipt,
    ProposalBlocker,
    ProposalControlHistory,
    ProposalControlStatus,
    ProposalExportRecord,
    ProposalIssueCandidate,
    ProposalReadinessAssessment,
)
from core.proposal_exchange_contract import (
    CONTRACT_VERSION,
    MANIFEST_SCHEMA_ID,
    PACKAGE_SCHEMA_ID,
    ExchangeContractError,
    JsonObject,
    area_hashes,
    canonical_json_bytes,
    canonical_sha256,
    raw_sha256,
    strict_json_loads,
    validate_manifest,
    validate_package,
)
from core.readiness_service import evaluate_readiness

MIGRATION_ID = "ops_09_proposal_exchange_issue_control_v1"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MEDIA_TYPE = "application/pdf"


class ProposalExchangeNotFoundError(ValueError):
    """Raised when a requested OPS-09 record does not exist in the Bid."""


class StaleProposalCandidateError(ValueError):
    """Raised on optimistic-concurrency or current-source failure."""


class ProposalImportError(ValueError):
    """Raised before mutation when a manifest or artifact is unsafe or inconsistent."""


def _text(value: object | None) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _utc_timestamp(value: object | None) -> str | None:
    text = _text(value)
    if text is None:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _version(value: object) -> int | str:
    if isinstance(value, int):
        return value
    text = str(value).strip()
    return int(text) if text.isdigit() and int(text) > 0 else text.replace(" ", "-")


def _source(source_type: str, source_id: str, version: object, projection: object) -> JsonObject:
    return {
        "source_type": source_type,
        "source_id": source_id,
        "source_version": _version(version),
        "source_sha256": canonical_sha256(projection),
    }


def _row_projection(row: Mapping[str, object], fields: Sequence[str]) -> JsonObject:
    return {field: row.get(field) for field in fields}


class ProposalExchangeService:
    """Own the file-only exchange while composing existing deterministic controls."""

    def __init__(
        self,
        db: Database,
        bid_repository: BidRepository,
        artifact_root: Path,
        *,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.bid_repository = bid_repository
        self.artifact_root = artifact_root
        self._now = now_factory or (lambda: datetime.now(UTC))
        self._migrate()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _migrate(self) -> None:
        statements = (
            """CREATE TABLE IF NOT EXISTS proposal_exchange_exports(
            export_id TEXT PRIMARY KEY,package_id TEXT NOT NULL UNIQUE,bid_id TEXT NOT NULL,
            contract_version TEXT NOT NULL,source_projection_sha256 TEXT NOT NULL,
            canonical_sha256 TEXT NOT NULL,canonical_package_json TEXT NOT NULL,
            area_hashes_json TEXT NOT NULL,supersedes_export_id TEXT,created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,FOREIGN KEY(bid_id) REFERENCES bids(bid_id),
            FOREIGN KEY(supersedes_export_id) REFERENCES proposal_exchange_exports(export_id))""",
            """CREATE TABLE IF NOT EXISTS proposal_generation_manifest_receipts(
            receipt_id TEXT PRIMARY KEY,export_id TEXT NOT NULL,bid_id TEXT NOT NULL,
            proposal_id TEXT NOT NULL,proposal_revision TEXT NOT NULL,manifest_sha256 TEXT NOT NULL,
            manifest_json TEXT NOT NULL,generation_status TEXT NOT NULL,generated_at TEXT NOT NULL,
            imported_by TEXT NOT NULL,imported_at TEXT NOT NULL,
            FOREIGN KEY(export_id) REFERENCES proposal_exchange_exports(export_id),
            FOREIGN KEY(bid_id) REFERENCES bids(bid_id),UNIQUE(export_id,manifest_sha256))""",
            """CREATE TABLE IF NOT EXISTS proposal_imported_artifacts(
            artifact_id TEXT PRIMARY KEY,receipt_id TEXT NOT NULL,role TEXT NOT NULL,
            filename TEXT NOT NULL,media_type TEXT NOT NULL,byte_size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,storage_path TEXT NOT NULL UNIQUE,
            FOREIGN KEY(receipt_id) REFERENCES proposal_generation_manifest_receipts(receipt_id),
            UNIQUE(receipt_id,role),UNIQUE(receipt_id,filename))""",
            """CREATE TABLE IF NOT EXISTS proposal_issue_candidates(
            candidate_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,export_id TEXT NOT NULL,
            receipt_id TEXT NOT NULL,status TEXT NOT NULL,version INTEGER NOT NULL,
            supersedes_candidate_id TEXT,created_by TEXT NOT NULL,created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,FOREIGN KEY(bid_id) REFERENCES bids(bid_id),
            FOREIGN KEY(export_id) REFERENCES proposal_exchange_exports(export_id),
            FOREIGN KEY(receipt_id) REFERENCES proposal_generation_manifest_receipts(receipt_id),
            FOREIGN KEY(supersedes_candidate_id) REFERENCES proposal_issue_candidates(candidate_id),
            UNIQUE(receipt_id))""",
            """CREATE TABLE IF NOT EXISTS proposal_candidate_approvals(
            link_id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL,approval_id TEXT,route_id TEXT,
            linked_at TEXT NOT NULL,linked_by TEXT NOT NULL,
            FOREIGN KEY(candidate_id) REFERENCES proposal_issue_candidates(candidate_id),
            FOREIGN KEY(approval_id) REFERENCES approvals(approval_id),
            FOREIGN KEY(route_id) REFERENCES approval_routes(route_id),
            CHECK((approval_id IS NOT NULL)+(route_id IS NOT NULL)=1),
            UNIQUE(candidate_id,approval_id,route_id))""",
            """CREATE TABLE IF NOT EXISTS proposal_customer_issue_events(
            issue_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,candidate_id TEXT NOT NULL,
            issue_revision TEXT NOT NULL,issued_at TEXT NOT NULL,issue_method TEXT NOT NULL,
            destination_reference TEXT NOT NULL,offer_valid_until TEXT,note TEXT NOT NULL,
            export_id TEXT NOT NULL,receipt_id TEXT NOT NULL,actor TEXT NOT NULL,
            recorded_at TEXT NOT NULL,FOREIGN KEY(bid_id) REFERENCES bids(bid_id),
            FOREIGN KEY(candidate_id) REFERENCES proposal_issue_candidates(candidate_id),
            FOREIGN KEY(export_id) REFERENCES proposal_exchange_exports(export_id),
            FOREIGN KEY(receipt_id) REFERENCES proposal_generation_manifest_receipts(receipt_id),
            UNIQUE(bid_id,issue_revision))""",
            """CREATE TABLE IF NOT EXISTS issued_offer_baselines(
            baseline_id TEXT PRIMARY KEY,issue_id TEXT NOT NULL UNIQUE,bid_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,issue_revision TEXT NOT NULL,issued_at TEXT NOT NULL,
            issue_method TEXT NOT NULL,destination_reference TEXT NOT NULL,
            offer_valid_until TEXT,note TEXT NOT NULL,export_id TEXT NOT NULL,
            package_id TEXT NOT NULL,package_sha256 TEXT NOT NULL,receipt_id TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL,snapshot_json TEXT NOT NULL,
            successor_of_baseline_id TEXT,actor TEXT NOT NULL,recorded_at TEXT NOT NULL,
            FOREIGN KEY(issue_id) REFERENCES proposal_customer_issue_events(issue_id),
            FOREIGN KEY(bid_id) REFERENCES bids(bid_id),
            FOREIGN KEY(candidate_id) REFERENCES proposal_issue_candidates(candidate_id),
            FOREIGN KEY(successor_of_baseline_id) REFERENCES issued_offer_baselines(baseline_id),
            UNIQUE(bid_id,issue_revision))""",
            """CREATE TABLE IF NOT EXISTS issued_offer_artifacts(
            baseline_artifact_id TEXT PRIMARY KEY,baseline_id TEXT NOT NULL,role TEXT NOT NULL,
            filename TEXT NOT NULL,media_type TEXT NOT NULL,byte_size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,FOREIGN KEY(baseline_id) REFERENCES issued_offer_baselines(baseline_id),
            UNIQUE(baseline_id,role))""",
            """CREATE TABLE IF NOT EXISTS issued_offer_approvals(
            baseline_approval_id TEXT PRIMARY KEY,baseline_id TEXT NOT NULL,
            approval_reference TEXT NOT NULL,authority_reference TEXT,decision TEXT NOT NULL,
            decided_at TEXT,FOREIGN KEY(baseline_id) REFERENCES issued_offer_baselines(baseline_id),
            UNIQUE(baseline_id,approval_reference))""",
            """CREATE TABLE IF NOT EXISTS proposal_exchange_schema_migrations(
            migration_id TEXT PRIMARY KEY,applied_at TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS idx_proposal_exports_bid ON proposal_exchange_exports(bid_id,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_proposal_candidates_bid ON proposal_issue_candidates(bid_id,created_at)",
            "CREATE INDEX IF NOT EXISTS idx_issued_baselines_bid ON issued_offer_baselines(bid_id,issued_at)",
            "CREATE TRIGGER IF NOT EXISTS proposal_exchange_export_immutable BEFORE UPDATE ON proposal_exchange_exports BEGIN SELECT RAISE(ABORT,'proposal exports are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS proposal_exchange_export_no_delete BEFORE DELETE ON proposal_exchange_exports BEGIN SELECT RAISE(ABORT,'proposal exports cannot be deleted'); END",
            "CREATE TRIGGER IF NOT EXISTS proposal_manifest_immutable BEFORE UPDATE ON proposal_generation_manifest_receipts BEGIN SELECT RAISE(ABORT,'manifest receipts are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS proposal_manifest_no_delete BEFORE DELETE ON proposal_generation_manifest_receipts BEGIN SELECT RAISE(ABORT,'manifest receipts cannot be deleted'); END",
            "CREATE TRIGGER IF NOT EXISTS proposal_imported_artifact_immutable BEFORE UPDATE ON proposal_imported_artifacts BEGIN SELECT RAISE(ABORT,'imported artifacts are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS proposal_imported_artifact_no_delete BEFORE DELETE ON proposal_imported_artifacts BEGIN SELECT RAISE(ABORT,'imported artifacts cannot be deleted'); END",
            "CREATE TRIGGER IF NOT EXISTS proposal_issue_event_immutable BEFORE UPDATE ON proposal_customer_issue_events BEGIN SELECT RAISE(ABORT,'customer issue events are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS proposal_issue_event_no_delete BEFORE DELETE ON proposal_customer_issue_events BEGIN SELECT RAISE(ABORT,'customer issue events cannot be deleted'); END",
            "CREATE TRIGGER IF NOT EXISTS issued_offer_baseline_immutable BEFORE UPDATE ON issued_offer_baselines BEGIN SELECT RAISE(ABORT,'issued offer baselines are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS issued_offer_baseline_no_delete BEFORE DELETE ON issued_offer_baselines BEGIN SELECT RAISE(ABORT,'issued offer baselines cannot be deleted'); END",
            "CREATE TRIGGER IF NOT EXISTS issued_offer_artifact_immutable BEFORE UPDATE ON issued_offer_artifacts BEGIN SELECT RAISE(ABORT,'issued offer artifacts are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS issued_offer_artifact_no_delete BEFORE DELETE ON issued_offer_artifacts BEGIN SELECT RAISE(ABORT,'issued offer artifacts cannot be deleted'); END",
            "CREATE TRIGGER IF NOT EXISTS issued_offer_approval_immutable BEFORE UPDATE ON issued_offer_approvals BEGIN SELECT RAISE(ABORT,'issued offer approvals are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS issued_offer_approval_no_delete BEFORE DELETE ON issued_offer_approvals BEGIN SELECT RAISE(ABORT,'issued offer approvals cannot be deleted'); END",
        )
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT OR IGNORE INTO proposal_exchange_schema_migrations VALUES(?,?)",
                    (MIGRATION_ID, self._now().isoformat()),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _audit(
        self,
        conn: sqlite3.Connection,
        bid_id: str,
        actor: str,
        action: str,
        detail: object,
    ) -> None:
        conn.execute(
            "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) VALUES(?,?,?,?,?,?)",
            (
                f"AUD-{uuid4().hex}",
                bid_id,
                actor,
                action,
                json.dumps(detail, sort_keys=True, separators=(",", ":")),
                self._now().isoformat(),
            ),
        )

    @staticmethod
    def _rows(
        conn: sqlite3.Connection, query: str, parameters: Sequence[object] = ()
    ) -> list[dict[str, object]]:
        return [dict(row) for row in conn.execute(query, parameters).fetchall()]

    def _build_package(
        self,
        bid_id: str,
        package_id: str,
        generated_at: datetime,
        *,
        validate_result: bool = True,
    ) -> JsonObject:
        bid = self.bid_repository.get_bid(bid_id)
        if bid is None:
            raise ProposalExchangeNotFoundError(f"Bid not found: {bid_id}")
        bid_dump = bid.model_dump(mode="json")
        bid_version = bid.updated_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        bid_source = _source("bid", bid.bid_id, bid_version, bid_dump)
        customer_projection = {"name": bid.customer, "customer_type": bid.customer_type.value}
        opportunity_projection = {
            "name": bid.project_name,
            "location": bid.location,
            "due_at": bid.customer_due_date.isoformat(),
        }
        with self._conn() as conn:
            requirement_rows = self._rows(
                conn,
                "SELECT * FROM requirements WHERE bid_id=? AND lifecycle_state='ACTIVE' ORDER BY requirement_id",
                (bid_id,),
            )
            scope_rows = self._rows(
                conn,
                "SELECT * FROM scope_interface_items WHERE bid_id=? AND lifecycle_state='ACTIVE' ORDER BY scope_item_id",
                (bid_id,),
            )
            interface_rows = self._rows(
                conn,
                "SELECT * FROM scope_interfaces WHERE bid_id=? AND lifecycle_state='ACTIVE' ORDER BY interface_id",
                (bid_id,),
            )
            package_rows = self._rows(
                conn,
                "SELECT * FROM vendor_bid_packages WHERE bid_id=? ORDER BY package_id",
                (bid_id,),
            )
            vendor_rows = self._rows(
                conn,
                """SELECT v.* FROM vendor_bid_requirements v JOIN vendor_bid_packages p
                ON p.package_id=v.package_id WHERE p.bid_id=? ORDER BY v.requirement_id""",
                (bid_id,),
            )
            commercial_rows = self._rows(
                conn,
                """SELECT p.position_id,p.current_version,v.*,tv.label FROM commercial_positions p
                JOIN commercial_position_versions v ON v.position_id=p.position_id
                AND v.version_number=p.current_version JOIN commercial_topic_versions tv
                ON tv.topic_version_id=v.topic_version_id WHERE p.bid_id=? ORDER BY p.position_id""",
                (bid_id,),
            )
            risk_rows = self._rows(
                conn,
                """SELECT i.*,a.assessment_id,a.version_number AS assessment_version,
                a.disposition AS assessment_disposition,a.business_impact
                FROM contract_issues i LEFT JOIN contract_risk_assessments a
                ON a.issue_id=i.issue_id AND a.version_number=(SELECT max(a2.version_number)
                FROM contract_risk_assessments a2 WHERE a2.issue_id=i.issue_id)
                WHERE i.bid_id=? AND i.lifecycle_state<>'WITHDRAWN' ORDER BY i.issue_id""",
                (bid_id,),
            )
            approval_rows = self._rows(
                conn, "SELECT * FROM approvals WHERE bid_id=? ORDER BY approval_id", (bid_id,)
            )
            decision_rows = self._rows(
                conn, "SELECT * FROM decision_cases WHERE bid_id=? ORDER BY case_id", (bid_id,)
            )
            route_rows = self._rows(
                conn, "SELECT * FROM approval_routes WHERE bid_id=? ORDER BY route_id", (bid_id,)
            )
            pricing_row = conn.execute(
                """SELECT b.*,v.version_number,v.fingerprint,f.title FROM scenario_baselines b
                JOIN scenario_versions v ON v.scenario_version_id=b.scenario_version_id
                JOIN scenario_families f ON f.family_id=v.family_id WHERE b.bid_id=?
                ORDER BY b.selected_at DESC,b.selection_id DESC LIMIT 1""",
                (bid_id,),
            ).fetchone()
            delivery_rows = self._rows(
                conn,
                """SELECT * FROM deliverable_items WHERE bid_id=? AND workflow_state<>'CANCELLED'
                AND lifecycle_phase IN ('WITH_BID','PRE_AWARD','BOTH')
                AND direction IN ('COMPANY_TO_CUSTOMER','SUPPLIER_TO_CUSTOMER_VIA_COMPANY')
                ORDER BY deliverable_id""",
                (bid_id,),
            )
            document_rows = self._rows(
                conn,
                """SELECT d.id,d.control_title,d.control_version,v.* FROM documents d
                JOIN document_versions v ON v.document_version_id=d.current_version_id
                WHERE d.bid_id=? AND d.control_managed=1 AND d.control_lifecycle='ACTIVE'
                ORDER BY d.id""",
                (bid_id,),
            )

        response_status = {
            "COMPLY": "COMPLIANT",
            "DEVIATE": "NON_COMPLIANT",
            "EXCLUDE": "NON_COMPLIANT",
            "NOT_APPLICABLE": "NOT_APPLICABLE",
        }
        requirements: list[JsonObject] = []
        qualifications: list[JsonObject] = []
        for row in requirement_rows:
            projection = _row_projection(
                row,
                (
                    "requirement_id",
                    "statement",
                    "response_text",
                    "disposition",
                    "version",
                    "source_document_version_id",
                    "source_clause",
                    "source_locator_note",
                ),
            )
            source = _source("requirement", str(row["requirement_id"]), row["version"], projection)
            requirements.append(
                {
                    "requirement_id": row["requirement_id"],
                    "requirement_text": row["statement"],
                    "proposed_response": _text(row.get("response_text")),
                    "response_status": response_status.get(str(row["disposition"]), "UNRESOLVED"),
                    "source": source,
                }
            )
            kind = {
                "DEVIATE": "DEVIATION",
                "EXCLUDE": "QUALIFICATION",
                "CLARIFY": "CLARIFICATION",
            }.get(str(row["disposition"]))
            response_text = _text(row.get("response_text"))
            if kind and response_text is not None:
                qualifications.append(
                    {
                        "item_id": f"QD-{row['requirement_id']}",
                        "kind": kind,
                        "text": response_text,
                        "status": "APPROVED" if row["review_state"] == "ACCEPTED" else "PENDING",
                        "related_requirement_ids": [row["requirement_id"]],
                        "source": source,
                    }
                )

        inclusions: list[JsonObject] = []
        exclusions: list[JsonObject] = []
        for row in scope_rows:
            projection = _row_projection(
                row,
                (
                    "scope_item_id",
                    "description",
                    "offer_position",
                    "assumption_exclusion_note",
                    "work_state",
                    "review_state",
                    "version",
                ),
            )
            value = {
                "scope_item_id": row["scope_item_id"],
                "description": row["description"],
                "source": _source(
                    "scope_item", str(row["scope_item_id"]), row["version"], projection
                ),
            }
            if row["offer_position"] in {"INCLUDED", "OPTION"}:
                inclusions.append(value)
            elif row["offer_position"] == "EXCLUDED":
                exclusions.append(value)
        interfaces = [
            {
                "interface_id": row["interface_id"],
                "description": row["boundary_description"],
                "responsibility": row["dependency_description"],
                "counterparty": _text(row.get("upstream_party")),
                "source": _source(
                    "scope_interface",
                    str(row["interface_id"]),
                    row["version"],
                    _row_projection(
                        row,
                        (
                            "interface_id",
                            "boundary_description",
                            "dependency_description",
                            "upstream_party",
                            "downstream_party",
                            "dependency_state",
                            "version",
                        ),
                    ),
                ),
            }
            for row in interface_rows
        ]

        safe_media = {
            "application/pdf",
            DOCX_MEDIA_TYPE,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "text/plain",
            "text/csv",
            "image/png",
            "image/jpeg",
        }
        supporting_documents = [
            {
                "document_id": row["id"],
                "title": row["control_title"],
                "revision": _text(row.get("version_label")),
                "relative_path": None,
                "media_type": row["media_type"],
                "sha256": row["sha256_digest"],
                "source": _source(
                    "controlled_document",
                    str(row["id"]),
                    row["control_version"],
                    _row_projection(
                        row,
                        (
                            "id",
                            "document_version_id",
                            "version_label",
                            "sha256_digest",
                            "media_type",
                            "byte_size",
                        ),
                    ),
                ),
            }
            for row in document_rows
            if row.get("media_type") in safe_media
        ]

        vendor_by_package: dict[str, list[dict[str, object]]] = {}
        for row in vendor_rows:
            vendor_by_package.setdefault(str(row["package_id"]), []).append(row)
        manufacturer_packages: list[JsonObject] = []
        vdrl_commitments: list[JsonObject] = []
        manufacturer_status = {
            "CONFIRMED_COMPLIANT": "CONFIRMED",
            "CONFIRMED_WITH_EXCEPTION": "CONDITIONAL",
            "NOT_APPLICABLE": "NOT_APPLICABLE",
        }
        vdrl_status = {
            "CONFIRMED_COMPLIANT": "COMMITTED",
            "CONFIRMED_WITH_EXCEPTION": "CONDITIONAL",
            "NOT_APPLICABLE": "NOT_APPLICABLE",
        }
        for package_row in package_rows:
            package_vendor_rows = vendor_by_package.get(str(package_row["package_id"]), [])
            commitments = []
            for row in package_vendor_rows:
                projection = _row_projection(
                    row,
                    (
                        "requirement_id",
                        "deliverable_title",
                        "description",
                        "verification_status",
                        "response_source",
                        "response_received_date",
                        "proposed_exception",
                        "committed_timing",
                        "version",
                    ),
                )
                source = _source(
                    "manufacturer_commitment",
                    str(row["requirement_id"]),
                    row["version"],
                    projection,
                )
                commitment_text = (
                    _text(row.get("manufacturer_notes"))
                    or _text(row.get("proposed_exception"))
                    or _text(row.get("description"))
                    or str(row["deliverable_title"])
                )
                commitments.append(
                    {
                        "commitment_id": row["requirement_id"],
                        "commitment_text": commitment_text,
                        "status": manufacturer_status.get(
                            str(row["verification_status"]), "UNCONFIRMED"
                        ),
                        "evidence_document_ids": [],
                        "source": source,
                    }
                )
                vdrl_commitments.append(
                    {
                        "vdrl_id": row["requirement_id"],
                        "document_code": row["customer_requirement_code"],
                        "title": row["deliverable_title"],
                        "commitment": commitment_text,
                        "status": vdrl_status.get(str(row["verification_status"]), "UNRESOLVED"),
                        "source": _source(
                            "vdrl_commitment",
                            str(row["requirement_id"]),
                            row["version"],
                            projection,
                        ),
                    }
                )
            package_projection = _row_projection(
                package_row,
                (
                    "package_id",
                    "package_code",
                    "package_name",
                    "proposed_manufacturer",
                    "source_vdrl_reference",
                    "source_revision",
                    "version_token",
                ),
            )
            manufacturer_packages.append(
                {
                    "package_id": package_row["package_id"],
                    "description": package_row["package_name"],
                    "manufacturer_name": _text(package_row.get("proposed_manufacturer")),
                    "equipment": [],
                    "commitments": sorted(commitments, key=lambda item: str(item["commitment_id"])),
                    "source": _source(
                        "manufacturer_package",
                        str(package_row["package_id"]),
                        package_row["version_token"],
                        package_projection,
                    ),
                }
            )

        commercial_status = {
            "ACCEPT": "APPROVED",
            "QUALIFY": "CONDITIONAL",
            "NOT_APPLICABLE": "NOT_APPLICABLE",
        }
        commercial_positions = [
            {
                "commercial_position_id": row["position_id"],
                "topic": row["label"],
                "customer_position": _text(row.get("customer_position")),
                # Preserve the missing authoritative value. Readiness reports the
                # correction destination before an export can be created.
                "proposed_position": _text(row.get("proposed_position")) or "",
                "status": commercial_status.get(str(row["disposition"]), "UNRESOLVED"),
                "source": _source(
                    "commercial_position",
                    str(row["position_id"]),
                    row["version_number"],
                    _row_projection(
                        row,
                        (
                            "position_id",
                            "topic_version_id",
                            "customer_position",
                            "proposed_position",
                            "disposition",
                            "negotiation_state",
                            "current_outcome",
                            "version_number",
                        ),
                    ),
                ),
            }
            for row in commercial_rows
        ]

        risks = []
        for row in risk_rows:
            materiality = str(row["materiality"])
            rating = "HIGH" if materiality in {"MATERIAL", "HIGH", "CRITICAL"} else "LOW"
            disposition = _text(row.get("assessment_disposition"))
            status = "OPEN"
            if disposition in {"ACCEPT", "ACCEPTED"}:
                status = "ACCEPTED"
            elif disposition and disposition not in {"UNASSESSED", "ESCALATE"}:
                status = "MITIGATED"
            risks.append(
                {
                    "risk_id": row["issue_id"],
                    "description": row["summary"],
                    "rating": rating,
                    "proposal_disposition": disposition,
                    "status": status,
                    "source": _source(
                        "contract_risk",
                        str(row["issue_id"]),
                        row["version"],
                        _row_projection(
                            row,
                            (
                                "issue_id",
                                "summary",
                                "materiality",
                                "lifecycle_state",
                                "version",
                                "assessment_id",
                                "assessment_version",
                                "assessment_disposition",
                            ),
                        ),
                    ),
                }
            )

        decisions: list[JsonObject] = []
        for row in approval_rows:
            decisions.append(
                {
                    "decision_id": row["approval_id"],
                    "subject": str(row["approval_type"]).replace("_", " ").title(),
                    "decision": _text(row.get("decision")) or "Approval not yet recorded",
                    "status": "APPROVED" if row["obtained"] else "PENDING",
                    "authority_reference": _text(row.get("authority")),
                    "decided_at": _utc_timestamp(row.get("decided_at")),
                    "source": _source(
                        "approval",
                        str(row["approval_id"]),
                        1,
                        _row_projection(
                            row,
                            (
                                "approval_id",
                                "approval_type",
                                "required",
                                "obtained",
                                "authority",
                                "evidence_ref",
                                "decision",
                                "decided_at",
                            ),
                        ),
                    ),
                }
            )
        routes_by_case = {str(row["case_id"]): row for row in route_rows}
        for row in decision_rows:
            route = routes_by_case.get(str(row["case_id"]))
            route_state = str(route["state"]) if route else "PENDING"
            status = (
                "APPROVED"
                if route_state == "APPROVED"
                else "REJECTED"
                if route_state == "REJECTED"
                else "PENDING"
            )
            decisions.append(
                {
                    "decision_id": row["case_id"],
                    "subject": row["title"],
                    "decision": _text(route.get("state") if route else None)
                    or "Decision route not completed",
                    "status": status,
                    "authority_reference": _text(route.get("policy_id") if route else None),
                    "decided_at": None,
                    "source": _source(
                        "decision_case",
                        str(row["case_id"]),
                        row["version"],
                        {"case": row, "route": route},
                    ),
                }
            )

        selected_pricing: JsonObject | None = None
        if pricing_row is not None:
            pricing = dict(pricing_row)
            selected_pricing = {
                "scenario_id": pricing["scenario_version_id"],
                "scenario_version": pricing["version_number"],
                "scenario_label": _text(pricing.get("title")),
                "source": _source(
                    "pricing_scenario",
                    str(pricing["scenario_version_id"]),
                    pricing["version_number"],
                    _row_projection(
                        pricing,
                        (
                            "selection_id",
                            "scenario_version_id",
                            "version_number",
                            "fingerprint",
                            "selected_at",
                        ),
                    ),
                ),
            }

        delivery_commitments = []
        for row in delivery_rows:
            if row["workflow_state"] == "SATISFIED":
                status = "COMMITTED"
            elif _text(row.get("condition_text")):
                status = "CONDITIONAL"
            elif row["workflow_state"] == "ACTIVE":
                status = "PROVISIONAL"
            else:
                status = "UNRESOLVED"
            wording = _text(row.get("condition_text")) or str(row["description"])
            delivery_commitments.append(
                {
                    "delivery_commitment_id": row["deliverable_id"],
                    "description": row["title"],
                    "target_date": _text(row.get("fixed_due_date")),
                    "commitment_text": wording,
                    "status": status,
                    "source": _source(
                        "delivery_commitment",
                        str(row["deliverable_id"]),
                        row["version"],
                        _row_projection(
                            row,
                            (
                                "deliverable_id",
                                "description",
                                "workflow_state",
                                "fixed_due_date",
                                "event_name",
                                "offset_days",
                                "condition_text",
                                "version",
                            ),
                        ),
                    ),
                }
            )

        result: JsonObject = {
            "schema_id": PACKAGE_SCHEMA_ID,
            "schema_version": CONTRACT_VERSION,
            "package_id": package_id,
            "source_application": {"name": "ContractIQ", "version": "2.0.0"},
            "generated_at": generated_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "bid": {
                "bid_id": bid.bid_id,
                "bid_revision": bid_version,
                "name": bid.project_name,
                "source": bid_source,
            },
            "customer": {
                "customer_id": None,
                "name": bid.customer,
                "reference": None,
                "source": _source("customer", bid.bid_id, bid_version, customer_projection),
            },
            "opportunity": {
                "opportunity_id": None,
                "name": bid.project_name,
                "customer_reference": None,
                "rfp_reference": None,
                "due_at": f"{bid.customer_due_date.isoformat()}T23:59:59Z",
                "project_location": bid.location,
                "source": _source("opportunity", bid.bid_id, bid_version, opportunity_projection),
            },
            "requirements": sorted(requirements, key=lambda item: str(item["requirement_id"])),
            "scope": {
                "inclusions": sorted(inclusions, key=lambda item: str(item["scope_item_id"])),
                "exclusions": sorted(exclusions, key=lambda item: str(item["scope_item_id"])),
                "interfaces": sorted(interfaces, key=lambda item: str(item["interface_id"])),
            },
            "manufacturer_packages": manufacturer_packages,
            "vdrl_commitments": sorted(vdrl_commitments, key=lambda item: str(item["vdrl_id"])),
            "commercial_positions": commercial_positions,
            "qualifications_and_deviations": sorted(
                qualifications, key=lambda item: str(item["item_id"])
            ),
            "proposal_issue_risks": risks,
            "decisions_and_approvals": sorted(decisions, key=lambda item: str(item["decision_id"])),
            "selected_pricing_scenario": selected_pricing,
            "delivery_commitments": delivery_commitments,
            "proposal_inputs": [],
            "supporting_documents": supporting_documents,
        }
        if validate_result:
            validate_package(result)
        return result

    def current_package_projection(self, bid_id: str) -> JsonObject:
        """Build a non-persisted source projection used for readiness and staleness."""
        return self._build_package(
            bid_id,
            f"PPK-{bid_id}-CURRENT",
            self._now(),
            validate_result=False,
        )

    def _base_blockers(self, bid_id: str, package: JsonObject) -> list[ProposalBlocker]:
        blockers: list[ProposalBlocker] = []

        def add(
            code: str, area: str, message: str, destination: str, record_id: str | None = None
        ) -> None:
            blockers.append(
                ProposalBlocker(
                    code=code,
                    area=area,
                    message=message,
                    destination=destination,
                    record_id=record_id,
                )
            )

        gate_report = evaluate_readiness(self.bid_repository, self.db, bid_id)
        for gate_blocker in gate_report.blockers:
            if not gate_blocker.overridden:
                section = {
                    "g0": "",
                    "g1": "",
                    "g2": "/requirements-scope",
                    "g3": "/manufacturers-coverage",
                    "g4": "/commercial-contract",
                    "g5": "/proposal-negotiation",
                    "g6": "/proposal-negotiation",
                }.get(gate_blocker.gate.value, "")
                add(
                    f"GATE_{gate_blocker.condition_id.upper()}",
                    "Bid gate",
                    f"{gate_blocker.description} {gate_blocker.detail}".strip(),
                    f"/bids/{bid_id}{section}",
                )
        requirements = cast(list[JsonObject], package["requirements"])
        if not requirements:
            add(
                "MISSING_REQUIREMENTS",
                "Requirements",
                "No active customer requirement is registered.",
                f"/requirements?bid_id={bid_id}#create",
            )
        for row in requirements:
            rid = str(row["requirement_id"])
            if row["response_status"] == "UNRESOLVED":
                add(
                    "UNRESOLVED_REQUIREMENT",
                    "Requirements",
                    "Customer requirement response remains unresolved.",
                    f"/requirements/{rid}",
                    rid,
                )
            if row["proposed_response"] is None:
                add(
                    "MISSING_PROPOSED_RESPONSE",
                    "Requirements",
                    "A proposed Bid response is missing.",
                    f"/requirements/{rid}",
                    rid,
                )
        with self._conn() as conn:
            controlled = conn.execute(
                """SELECT count(*) FROM documents d JOIN document_versions v
                ON v.document_version_id=d.current_version_id WHERE d.bid_id=?
                AND d.control_managed=1 AND d.control_lifecycle='ACTIVE'""",
                (bid_id,),
            ).fetchone()[0]
            raw_scope = self._rows(
                conn,
                "SELECT * FROM scope_interface_items WHERE bid_id=? AND lifecycle_state='ACTIVE'",
                (bid_id,),
            )
            raw_interfaces = self._rows(
                conn,
                "SELECT * FROM scope_interfaces WHERE bid_id=? AND lifecycle_state='ACTIVE'",
                (bid_id,),
            )
            vendor = self._rows(
                conn,
                """SELECT v.* FROM vendor_bid_requirements v JOIN vendor_bid_packages p
                ON p.package_id=v.package_id WHERE p.bid_id=?""",
                (bid_id,),
            )
            commercial = self._rows(
                conn,
                """SELECT p.position_id,v.* FROM commercial_positions p JOIN
                commercial_position_versions v ON v.position_id=p.position_id
                AND v.version_number=p.current_version WHERE p.bid_id=?""",
                (bid_id,),
            )
            risks = self._rows(
                conn,
                "SELECT * FROM contract_issues WHERE bid_id=? AND lifecycle_state<>'WITHDRAWN'",
                (bid_id,),
            )
            deliveries = cast(list[JsonObject], package["delivery_commitments"])
            family_owner = conn.execute(
                "SELECT owner FROM proposal_families WHERE bid_id=? ORDER BY created_at DESC LIMIT 1",
                (bid_id,),
            ).fetchone()
        if not controlled:
            add(
                "MISSING_CONTROLLED_SOURCE",
                "Controlled sources",
                "No current controlled source document is registered for this Bid.",
                f"/documents?bid_id={bid_id}",
            )
        if not raw_scope:
            add(
                "MISSING_SCOPE",
                "Scope and interfaces",
                "No authoritative Bid scope is recorded.",
                f"/bids/{bid_id}/requirements-scope#scope-and-interfaces",
            )
        for row in raw_scope:
            if (
                row["offer_position"] == "UNDECIDED"
                or row["pricing_state"] in {"UNCONFIRMED", "NOT_PRICED"}
                or row["work_state"] != "COMPLETE"
                or row["review_state"] != "ACCEPTED"
                or not _text(row.get("owner"))
            ):
                add(
                    "UNRESOLVED_SCOPE",
                    "Scope and interfaces",
                    f"Scope item {row['title']} is not fully resolved, reviewed and owned.",
                    f"/scope-items/{row['scope_item_id']}",
                    str(row["scope_item_id"]),
                )
        for row in raw_interfaces:
            if (
                row["dependency_state"] == "OPEN"
                or row["work_state"] != "COMPLETE"
                or row["review_state"] != "ACCEPTED"
                or not _text(row.get("owner"))
            ):
                add(
                    "UNRESOLVED_INTERFACE",
                    "Scope and interfaces",
                    f"Interface {row['title']} is unresolved or lacks an accountable owner.",
                    f"/interfaces/{row['interface_id']}",
                    str(row["interface_id"]),
                )
        for row in vendor:
            status = str(row["verification_status"])
            if status not in {"CONFIRMED_COMPLIANT", "NOT_APPLICABLE"}:
                add(
                    "MANUFACTURER_RESPONSE_BLOCKER",
                    "Manufacturer and VDRL",
                    f"{row['deliverable_title']} has no clear manufacturer response or disposition.",
                    f"/vendor-documents/packages/{row['package_id']}",
                    str(row["requirement_id"]),
                )
            if status == "CONFIRMED_COMPLIANT" and (
                not _text(row.get("response_source"))
                or not _text(row.get("response_received_date"))
            ):
                add(
                    "MISSING_MANUFACTURER_EVIDENCE",
                    "Manufacturer and VDRL",
                    f"{row['deliverable_title']} lacks response source or response date evidence.",
                    f"/vendor-documents/packages/{row['package_id']}",
                    str(row["requirement_id"]),
                )
            if status == "CONFIRMED_WITH_EXCEPTION" and not bool(row["disposition_approved"]):
                add(
                    "MANUFACTURER_EXCEPTION_UNDISPOSED",
                    "Manufacturer and VDRL",
                    f"{row['deliverable_title']} has an exception without approved disposition.",
                    f"/vendor-documents/packages/{row['package_id']}",
                    str(row["requirement_id"]),
                )
        if not commercial:
            add(
                "MISSING_COMMERCIAL_POSITION",
                "Commercial",
                "Commercial proposal positions have not been established.",
                f"/bids/{bid_id}/commercial-contract",
            )
        for row in commercial:
            if (
                row["disposition"] in {"NOT_REVIEWED", "CLARIFICATION_REQUIRED", "REJECT"}
                or not _text(row.get("proposed_position"))
                or not _text(row.get("owner"))
                or (row["disposition"] == "QUALIFY" and row["negotiation_state"] != "RESOLVED")
            ):
                add(
                    "UNRESOLVED_COMMERCIAL_POSITION",
                    "Commercial",
                    "A commercial position is unresolved, unowned, or missing proposed wording.",
                    f"/commercial/{row['position_id']}",
                    str(row["position_id"]),
                )
        for row in cast(list[JsonObject], package["qualifications_and_deviations"]):
            if row["status"] != "APPROVED":
                add(
                    "UNDISPOSED_QUALIFICATION",
                    "Qualifications and deviations",
                    "A proposal qualification, deviation or clarification is not approved.",
                    f"/requirements/{row['related_requirement_ids'][0]}",
                    str(row["item_id"]),
                )
        for row in risks:
            issue_id = str(row["issue_id"])
            with self._conn() as conn:
                accepted = conn.execute(
                    """SELECT 1 FROM contract_risk_reviews
                    WHERE issue_id=? AND assessment_id=? AND decision='ACCEPTED' LIMIT 1""",
                    (issue_id, row.get("assessment_id")),
                ).fetchone()
            if str(row["materiality"]) in {"MATERIAL", "HIGH", "CRITICAL"} and accepted is None:
                add(
                    "BLOCKING_CONTRACT_RISK",
                    "Contract risk",
                    f"Material contract risk {row['title']} lacks accepted disposition.",
                    f"/contract-risks/{issue_id}",
                    issue_id,
                )
        if family_owner is None or not _text(family_owner["owner"]):
            add(
                "MISSING_ACCOUNTABLE_OWNER",
                "Proposal",
                "No accountable proposal-family owner is recorded.",
                f"/proposals?bid_id={bid_id}#author",
            )
        if package["selected_pricing_scenario"] is None:
            add(
                "MISSING_SELECTED_PRICING_SCENARIO",
                "Pricing",
                "No accepted pricing-scenario baseline is selected.",
                f"/commercial-scenarios?bid_id={bid_id}",
            )
        if not deliveries:
            add(
                "MISSING_DELIVERY_COMMITMENT",
                "Delivery",
                "No proposal-facing delivery commitment is recorded.",
                f"/deliverables?bid_id={bid_id}#author",
            )
        for row in deliveries:
            if row["status"] == "UNRESOLVED":
                add(
                    "UNRESOLVED_DELIVERY_COMMITMENT",
                    "Delivery",
                    "A proposal-facing delivery commitment remains unresolved.",
                    f"/deliverables/{row['delivery_commitment_id']}",
                    str(row["delivery_commitment_id"]),
                )
        unique: dict[tuple[str, str | None], ProposalBlocker] = {}
        for blocker in blockers:
            unique.setdefault((blocker.code, blocker.record_id), blocker)
        return list(unique.values())

    def _approvals(self, bid_id: str) -> list[dict[str, object]]:
        with self._conn() as conn:
            legacy = self._rows(
                conn,
                "SELECT approval_id,authority,decision,decided_at,required,obtained FROM approvals WHERE bid_id=? ORDER BY approval_id",
                (bid_id,),
            )
            routes = self._rows(
                conn,
                "SELECT route_id,policy_id,state,submitted_at FROM approval_routes WHERE bid_id=? ORDER BY route_id",
                (bid_id,),
            )
        values = [
            {
                "reference": row["approval_id"],
                "authority": row["authority"],
                "decision": row["decision"] or ("APPROVED" if row["obtained"] else "PENDING"),
                "decided_at": row["decided_at"],
                "clear": not bool(row["required"]) or bool(row["obtained"]),
                "kind": "approval",
            }
            for row in legacy
        ]
        values.extend(
            {
                "reference": row["route_id"],
                "authority": row["policy_id"],
                "decision": row["state"],
                "decided_at": row["submitted_at"],
                "clear": row["state"] == "APPROVED",
                "kind": "route",
            }
            for row in routes
        )
        return values

    def _latest_export(self, bid_id: str) -> ProposalExportRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM proposal_exchange_exports WHERE bid_id=? ORDER BY rowid DESC LIMIT 1",
                (bid_id,),
            ).fetchone()
        return self._export_from_row(row) if row is not None else None

    @staticmethod
    def _export_from_row(row: sqlite3.Row) -> ProposalExportRecord:
        return ProposalExportRecord(
            export_id=str(row["export_id"]),
            package_id=str(row["package_id"]),
            bid_id=str(row["bid_id"]),
            contract_version=str(row["contract_version"]),
            source_projection_sha256=str(row["source_projection_sha256"]),
            canonical_sha256=str(row["canonical_sha256"]),
            canonical_package_json=str(row["canonical_package_json"]),
            area_hashes=cast(dict[str, str], json.loads(str(row["area_hashes_json"]))),
            supersedes_export_id=_text(row["supersedes_export_id"]),
            created_by=str(row["created_by"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    def _artifacts(self, receipt_id: str) -> tuple[ImportedArtifact, ...]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM proposal_imported_artifacts WHERE receipt_id=? ORDER BY role",
                (receipt_id,),
            ).fetchall()
        return tuple(
            ImportedArtifact(
                artifact_id=str(row["artifact_id"]),
                receipt_id=str(row["receipt_id"]),
                role=str(row["role"]),
                filename=str(row["filename"]),
                media_type=str(row["media_type"]),
                byte_size=int(row["byte_size"]),
                sha256=str(row["sha256"]),
                storage_path=str(row["storage_path"]),
            )
            for row in rows
        )

    def _receipt_from_row(self, row: sqlite3.Row) -> ManifestReceipt:
        return ManifestReceipt(
            receipt_id=str(row["receipt_id"]),
            export_id=str(row["export_id"]),
            bid_id=str(row["bid_id"]),
            proposal_id=str(row["proposal_id"]),
            proposal_revision=str(row["proposal_revision"]),
            manifest_sha256=str(row["manifest_sha256"]),
            manifest_json=str(row["manifest_json"]),
            generation_status=str(row["generation_status"]),
            generated_at=datetime.fromisoformat(str(row["generated_at"])),
            imported_by=str(row["imported_by"]),
            imported_at=datetime.fromisoformat(str(row["imported_at"])),
            artifacts=self._artifacts(str(row["receipt_id"])),
        )

    @staticmethod
    def _candidate_from_row(row: sqlite3.Row) -> ProposalIssueCandidate:
        return ProposalIssueCandidate(
            candidate_id=str(row["candidate_id"]),
            bid_id=str(row["bid_id"]),
            export_id=str(row["export_id"]),
            receipt_id=str(row["receipt_id"]),
            status=ProposalControlStatus(str(row["status"])),
            version=int(row["version"]),
            supersedes_candidate_id=_text(row["supersedes_candidate_id"]),
            created_by=str(row["created_by"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _baseline_from_row(row: sqlite3.Row) -> IssuedOfferBaseline:
        return IssuedOfferBaseline(
            baseline_id=str(row["baseline_id"]),
            issue_id=str(row["issue_id"]),
            bid_id=str(row["bid_id"]),
            candidate_id=str(row["candidate_id"]),
            issue_revision=str(row["issue_revision"]),
            issued_at=datetime.fromisoformat(str(row["issued_at"])),
            issue_method=str(row["issue_method"]),
            destination_reference=str(row["destination_reference"]),
            offer_valid_until=(
                datetime.fromisoformat(str(row["offer_valid_until"])).date()
                if row["offer_valid_until"]
                else None
            ),
            note=str(row["note"]),
            export_id=str(row["export_id"]),
            package_id=str(row["package_id"]),
            package_sha256=str(row["package_sha256"]),
            receipt_id=str(row["receipt_id"]),
            manifest_sha256=str(row["manifest_sha256"]),
            snapshot=cast(JsonObject, json.loads(str(row["snapshot_json"]))),
            successor_of_baseline_id=_text(row["successor_of_baseline_id"]),
            actor=str(row["actor"]),
            recorded_at=datetime.fromisoformat(str(row["recorded_at"])),
        )

    def history(self, bid_id: str) -> ProposalControlHistory:
        """Return immutable exchange/issue history for one Bid."""
        with self._conn() as conn:
            export_rows = conn.execute(
                "SELECT * FROM proposal_exchange_exports WHERE bid_id=? ORDER BY rowid DESC",
                (bid_id,),
            ).fetchall()
            receipt_rows = conn.execute(
                "SELECT * FROM proposal_generation_manifest_receipts WHERE bid_id=? ORDER BY rowid DESC",
                (bid_id,),
            ).fetchall()
            candidate_rows = conn.execute(
                "SELECT * FROM proposal_issue_candidates WHERE bid_id=? ORDER BY rowid DESC",
                (bid_id,),
            ).fetchall()
            baseline_rows = conn.execute(
                "SELECT * FROM issued_offer_baselines WHERE bid_id=? ORDER BY rowid DESC",
                (bid_id,),
            ).fetchall()
        candidates = tuple(self._candidate_from_row(row) for row in candidate_rows)
        if len(candidates) > 1:
            candidates = (candidates[0],) + tuple(
                candidate.model_copy(update={"status": ProposalControlStatus.SUPERSEDED})
                for candidate in candidates[1:]
            )
        return ProposalControlHistory(
            exports=tuple(self._export_from_row(row) for row in export_rows),
            receipts=tuple(self._receipt_from_row(row) for row in receipt_rows),
            candidates=candidates,
            baselines=tuple(self._baseline_from_row(row) for row in baseline_rows),
        )

    def assess(self, bid_id: str) -> ProposalReadinessAssessment:
        """Compose existing gate readiness with OPS-09 exchange and issue evidence."""
        package = self.current_package_projection(bid_id)
        blockers = self._base_blockers(bid_id, package)
        try:
            validate_package(package)
        except ExchangeContractError as exc:
            blockers.append(
                ProposalBlocker(
                    code="INVALID_SOURCE_PROJECTION",
                    area="Exchange package",
                    message=str(exc),
                    destination=f"/bids/{bid_id}/proposal-negotiation",
                )
            )
        history = self.history(bid_id)
        latest_export = history.exports[0] if history.exports else None
        latest_candidate = history.candidates[0] if history.candidates else None
        current_hashes = area_hashes(package)
        changed = tuple(
            area
            for area, digest in current_hashes.items()
            if latest_export is not None and latest_export.area_hashes.get(area) != digest
        )
        approvals = self._approvals(bid_id)
        approvals_clear = all(bool(item["clear"]) for item in approvals)
        if latest_export is None:
            status = (
                ProposalControlStatus.BLOCKED
                if blockers
                else ProposalControlStatus.READY_FOR_EXPORT
            )
            action = (
                "Resolve proposal readiness blockers"
                if blockers
                else "Prepare Proposal Studio package"
            )
            return ProposalReadinessAssessment(
                bid_id=bid_id,
                status=status,
                blockers=tuple(blockers),
                approvals_clear=approvals_clear,
                next_action=action,
                next_action_path=f"/bids/{bid_id}/proposal-issue-control",
            )
        if changed:
            stale = ProposalBlocker(
                code="STALE_PROPOSAL_FACTS",
                area="Export staleness",
                message="Authoritative proposal facts changed in: " + ", ".join(changed) + ".",
                destination=f"/bids/{bid_id}/proposal-issue-control",
                record_id=latest_export.export_id,
            )
            return ProposalReadinessAssessment(
                bid_id=bid_id,
                status=ProposalControlStatus.STALE,
                blockers=tuple(blockers + [stale]),
                changed_areas=changed,
                current_export_id=latest_export.export_id,
                candidate_id=latest_candidate.candidate_id if latest_candidate else None,
                candidate_version=latest_candidate.version if latest_candidate else None,
                approvals_clear=approvals_clear,
                next_action="Prepare a new package from the changed facts",
                next_action_path=f"/bids/{bid_id}/proposal-issue-control",
            )
        if latest_candidate is None:
            return ProposalReadinessAssessment(
                bid_id=bid_id,
                status=ProposalControlStatus.BLOCKED
                if blockers
                else ProposalControlStatus.EXPORTED,
                blockers=tuple(blockers),
                current_export_id=latest_export.export_id,
                approvals_clear=approvals_clear,
                next_action="Import the Proposal Studio generation manifest and artifacts",
                next_action_path=f"/bids/{bid_id}/proposal-issue-control#import-manifest",
            )
        if latest_candidate.export_id != latest_export.export_id:
            superseded = ProposalBlocker(
                code="SUPERSEDED_CANDIDATE",
                area="Proposal candidate",
                message="The prior proposal candidate was superseded by the current export.",
                destination=f"/bids/{bid_id}/proposal-issue-control#import-manifest",
                record_id=latest_candidate.candidate_id,
            )
            return ProposalReadinessAssessment(
                bid_id=bid_id,
                status=ProposalControlStatus.SUPERSEDED,
                blockers=tuple(blockers + [superseded]),
                current_export_id=latest_export.export_id,
                candidate_id=latest_candidate.candidate_id,
                candidate_version=latest_candidate.version,
                approvals_clear=approvals_clear,
                next_action="Import generated artifacts for the current export",
                next_action_path=f"/bids/{bid_id}/proposal-issue-control#import-manifest",
            )
        artifacts = self._artifacts(latest_candidate.receipt_id)
        artifact_errors = self._artifact_integrity_errors(artifacts)
        for message in artifact_errors:
            blockers.append(
                ProposalBlocker(
                    code="ARTIFACT_HASH_MISMATCH",
                    area="Generated artifacts",
                    message=message,
                    destination=f"/bids/{bid_id}/proposal-issue-control#import-manifest",
                    record_id=latest_candidate.candidate_id,
                )
            )
        if latest_candidate.status is ProposalControlStatus.ISSUED:
            status = ProposalControlStatus.ISSUED
            action = "Review the immutable issue history"
            path = f"/bids/{bid_id}/proposal-issue-control#history"
        elif latest_candidate.status is ProposalControlStatus.APPROVED_FOR_ISSUE:
            status = ProposalControlStatus.APPROVED_FOR_ISSUE
            action = "Record manual customer issue"
            path = f"/bids/{bid_id}/proposal-issue-control#record-issue"
        elif blockers or artifact_errors:
            status = ProposalControlStatus.BLOCKED
            action = "Resolve proposal readiness blockers"
            path = f"/bids/{bid_id}/proposal-issue-control"
        else:
            status = ProposalControlStatus.AWAITING_APPROVAL
            action = "Confirm approved-for-issue control"
            path = f"/bids/{bid_id}/proposal-issue-control#approve"
        return ProposalReadinessAssessment(
            bid_id=bid_id,
            status=status,
            blockers=tuple(blockers),
            current_export_id=latest_export.export_id,
            candidate_id=latest_candidate.candidate_id,
            candidate_version=latest_candidate.version,
            manifest_valid=True,
            artifacts_verified=bool(artifacts) and not artifact_errors,
            approvals_clear=approvals_clear,
            next_action=action,
            next_action_path=path,
        )

    def export_package(self, bid_id: str, actor: str) -> tuple[ProposalExportRecord, bool]:
        """Create an immutable export, or replay the latest unchanged export."""
        assessment = self.assess(bid_id)
        if assessment.blockers and any(
            blocker.code != "STALE_PROPOSAL_FACTS" for blocker in assessment.blockers
        ):
            raise ValueError(
                "Proposal package is blocked; resolve the listed readiness items first"
            )
        now = self._now()
        probe = self._build_package(bid_id, f"PPK-{bid_id}-PROBE", now)
        hashes = area_hashes(probe)
        projection_hash = canonical_sha256(hashes)
        latest = self._latest_export(bid_id)
        if latest is not None and latest.source_projection_sha256 == projection_hash:
            return latest, True
        export_id = f"PEX-{uuid4().hex}"
        package_id = f"PPK-{bid_id}-{uuid4().hex[:16]}"
        package = self._build_package(bid_id, package_id, now)
        canonical = canonical_json_bytes(package)
        record = ProposalExportRecord(
            export_id=export_id,
            package_id=package_id,
            bid_id=bid_id,
            contract_version=CONTRACT_VERSION,
            source_projection_sha256=projection_hash,
            canonical_sha256=raw_sha256(canonical),
            canonical_package_json=canonical.decode("utf-8"),
            area_hashes=hashes,
            supersedes_export_id=latest.export_id if latest else None,
            created_by=actor,
            created_at=now,
        )
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO proposal_exchange_exports VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        record.export_id,
                        record.package_id,
                        record.bid_id,
                        record.contract_version,
                        record.source_projection_sha256,
                        record.canonical_sha256,
                        record.canonical_package_json,
                        json.dumps(record.area_hashes, sort_keys=True, separators=(",", ":")),
                        record.supersedes_export_id,
                        record.created_by,
                        record.created_at.isoformat(),
                    ),
                )
                self._audit(
                    conn,
                    bid_id,
                    actor,
                    "proposal_package_exported",
                    {
                        "export_id": record.export_id,
                        "package_id": record.package_id,
                        "canonical_sha256": record.canonical_sha256,
                    },
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return record, False

    def export_by_id(self, bid_id: str, export_id: str) -> ProposalExportRecord:
        """Load one immutable export after enforcing Bid ownership."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM proposal_exchange_exports WHERE export_id=? AND bid_id=?",
                (export_id, bid_id),
            ).fetchone()
        if row is None:
            raise ProposalExchangeNotFoundError("Proposal export not found for this Bid")
        return self._export_from_row(row)

    @staticmethod
    def _validate_artifact_name(filename: str) -> None:
        if (
            not filename
            or Path(filename).name != filename
            or "/" in filename
            or "\\" in filename
            or filename in {".", ".."}
            or "//" in filename
        ):
            raise ProposalImportError(f"Unsafe artifact filename: {filename!r}")
        normalized = os.path.normcase(os.path.normpath(filename))
        if normalized != filename or normalized.startswith("..") or os.path.isabs(normalized):
            raise ProposalImportError(f"Ambiguous normalized artifact filename: {filename!r}")

    def import_manifest(
        self,
        bid_id: str,
        export_id: str,
        manifest_bytes: bytes,
        artifact_files: Mapping[str, tuple[str, bytes]],
        actor: str,
    ) -> ProposalIssueCandidate:
        """Validate all local files before atomically registering a new issue candidate."""
        export = self.export_by_id(bid_id, export_id)
        current = self.current_package_projection(bid_id)
        if area_hashes(current) != export.area_hashes:
            raise StaleProposalCandidateError("The selected export is stale; prepare a new package")
        try:
            manifest = strict_json_loads(manifest_bytes)
            validate_manifest(manifest)
        except ExchangeContractError as exc:
            raise ProposalImportError(str(exc)) from exc
        if (
            manifest.get("schema_id") != MANIFEST_SCHEMA_ID
            or manifest.get("schema_version") != CONTRACT_VERSION
        ):
            raise ProposalImportError(
                "Unsupported generation-manifest schema identifier or version"
            )
        source_package = cast(JsonObject, manifest["source_proposal_package"])
        if source_package.get("package_id") != export.package_id:
            raise ProposalImportError("Manifest package identity does not match this export or Bid")
        if source_package.get("sha256") != export.canonical_sha256:
            raise ProposalImportError("Manifest source package hash does not match the export")
        if manifest["generation_status"] not in {"SUCCEEDED", "SUCCEEDED_WITH_WARNINGS"}:
            raise ProposalImportError(
                "Only successful controlled generations can become issue candidates"
            )
        generated = cast(JsonObject, manifest["generated_files"])
        expected: dict[str, tuple[str, str, str]] = {}
        for role, media_type in (("docx", DOCX_MEDIA_TYPE), ("pdf", PDF_MEDIA_TYPE)):
            declaration = generated.get(role)
            if declaration is not None:
                item = cast(JsonObject, declaration)
                filename = str(item["filename"])
                self._validate_artifact_name(filename)
                expected[role] = (filename, media_type, str(item["sha256"]))
        if set(artifact_files) != set(expected):
            missing = sorted(set(expected) - set(artifact_files))
            extra = sorted(set(artifact_files) - set(expected))
            raise ProposalImportError(
                "Artifact set does not match manifest"
                + (f"; missing: {', '.join(missing)}" if missing else "")
                + (f"; unexpected: {', '.join(extra)}" if extra else "")
            )
        normalized_names: set[str] = set()
        prepared: list[tuple[str, str, str, bytes, str]] = []
        for role, (actual_name, data) in artifact_files.items():
            expected_name, media_type, expected_hash = expected[role]
            self._validate_artifact_name(actual_name)
            normalized = os.path.normcase(os.path.normpath(actual_name))
            if normalized in normalized_names:
                raise ProposalImportError("Duplicate normalized artifact destination")
            normalized_names.add(normalized)
            if actual_name != expected_name:
                raise ProposalImportError(
                    f"{role.upper()} artifact filename does not match manifest"
                )
            digest = raw_sha256(data)
            if digest != expected_hash:
                raise ProposalImportError(
                    f"{role.upper()} artifact SHA-256 does not match manifest"
                )
            if not data:
                raise ProposalImportError(f"{role.upper()} artifact is empty")
            prepared.append((role, actual_name, media_type, data, digest))

        receipt_id = f"PMR-{uuid4().hex}"
        candidate_id = f"PIC-{uuid4().hex}"
        now = self._now()
        final_root = (self.artifact_root / bid_id / receipt_id).resolve()
        artifact_root = self.artifact_root.resolve()
        if final_root != artifact_root and artifact_root not in final_root.parents:
            raise ProposalImportError("Artifact storage destination is unsafe")
        temp_parent = self.artifact_root.parent
        temp_parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix="proposal-import-", dir=temp_parent))
        moved = False
        try:
            for _, filename, _, data, _ in prepared:
                (temp_dir / filename).write_bytes(data)
            with self._conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    prior = conn.execute(
                        "SELECT candidate_id FROM proposal_issue_candidates WHERE bid_id=? ORDER BY rowid DESC LIMIT 1",
                        (bid_id,),
                    ).fetchone()
                    manifest_json = canonical_json_bytes(manifest).decode("utf-8")
                    conn.execute(
                        "INSERT INTO proposal_generation_manifest_receipts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            receipt_id,
                            export_id,
                            bid_id,
                            manifest["proposal_id"],
                            str(manifest["proposal_revision"]),
                            canonical_sha256(manifest),
                            manifest_json,
                            manifest["generation_status"],
                            manifest["generated_at"],
                            actor,
                            now.isoformat(),
                        ),
                    )
                    conn.execute(
                        "INSERT INTO proposal_issue_candidates VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            candidate_id,
                            bid_id,
                            export_id,
                            receipt_id,
                            ProposalControlStatus.GENERATED_ARTIFACTS_RECEIVED.value,
                            1,
                            str(prior["candidate_id"]) if prior else None,
                            actor,
                            now.isoformat(),
                            now.isoformat(),
                        ),
                    )
                    final_root.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(temp_dir, final_root)
                    moved = True
                    for role, filename, media_type, data, digest in prepared:
                        conn.execute(
                            "INSERT INTO proposal_imported_artifacts VALUES(?,?,?,?,?,?,?,?)",
                            (
                                f"PIA-{uuid4().hex}",
                                receipt_id,
                                role,
                                filename,
                                media_type,
                                len(data),
                                digest,
                                str(final_root / filename),
                            ),
                        )
                    self._audit(
                        conn,
                        bid_id,
                        actor,
                        "proposal_generation_imported",
                        {
                            "receipt_id": receipt_id,
                            "candidate_id": candidate_id,
                            "export_id": export_id,
                            "artifacts": [item[0] for item in prepared],
                        },
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    if moved and final_root.exists():
                        shutil.rmtree(final_root)
                    raise
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM proposal_issue_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("Candidate persistence failed")
        return self._candidate_from_row(row)

    @staticmethod
    def _artifact_integrity_errors(artifacts: Sequence[ImportedArtifact]) -> list[str]:
        errors: list[str] = []
        for artifact in artifacts:
            path = Path(artifact.storage_path)
            try:
                content = path.read_bytes()
            except OSError:
                errors.append(f"{artifact.filename} is missing from controlled local storage")
                continue
            if len(content) != artifact.byte_size or raw_sha256(content) != artifact.sha256:
                errors.append(f"{artifact.filename} no longer matches its recorded SHA-256")
        return errors

    def approve_candidate(
        self, bid_id: str, candidate_id: str, expected_version: int, actor: str
    ) -> ProposalIssueCandidate:
        """Recheck deterministic controls and mark one current candidate approved for issue."""
        assessment = self.assess(bid_id)
        if (
            assessment.candidate_id != candidate_id
            or assessment.candidate_version != expected_version
        ):
            raise StaleProposalCandidateError("Candidate changed; refresh before approving")
        if assessment.blockers or not assessment.artifacts_verified:
            raise ValueError("Candidate is blocked or artifact verification is incomplete")
        if not assessment.approvals_clear:
            raise ValueError("Required approval authority is not clear")
        now = self._now()
        approvals = self._approvals(bid_id)
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                changed = conn.execute(
                    """UPDATE proposal_issue_candidates SET status=?,version=version+1,updated_at=?
                    WHERE candidate_id=? AND bid_id=? AND version=?
                    AND status='GENERATED_ARTIFACTS_RECEIVED'""",
                    (
                        ProposalControlStatus.APPROVED_FOR_ISSUE.value,
                        now.isoformat(),
                        candidate_id,
                        bid_id,
                        expected_version,
                    ),
                ).rowcount
                if changed != 1:
                    raise StaleProposalCandidateError("Candidate changed; refresh before approving")
                for approval in approvals:
                    conn.execute(
                        "INSERT INTO proposal_candidate_approvals VALUES(?,?,?,?,?,?)",
                        (
                            f"PCA-{uuid4().hex}",
                            candidate_id,
                            approval["reference"] if approval["kind"] == "approval" else None,
                            approval["reference"] if approval["kind"] == "route" else None,
                            now.isoformat(),
                            actor,
                        ),
                    )
                self._audit(
                    conn,
                    bid_id,
                    actor,
                    "proposal_candidate_approved_for_issue",
                    {
                        "candidate_id": candidate_id,
                        "approvals": [a["reference"] for a in approvals],
                    },
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self._candidate(candidate_id, bid_id)

    def _candidate(self, candidate_id: str, bid_id: str) -> ProposalIssueCandidate:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM proposal_issue_candidates WHERE candidate_id=? AND bid_id=?",
                (candidate_id, bid_id),
            ).fetchone()
        if row is None:
            raise ProposalExchangeNotFoundError("Proposal issue candidate not found for this Bid")
        return self._candidate_from_row(row)

    def issue(self, bid_id: str, command: CustomerIssueCommand, actor: str) -> IssuedOfferBaseline:
        """Atomically record manual customer issue and its immutable offered baseline."""
        assessment = self.assess(bid_id)
        if (
            assessment.candidate_id != command.candidate_id
            or assessment.candidate_version != command.expected_version
        ):
            raise StaleProposalCandidateError("Candidate changed; refresh before recording issue")
        if assessment.status is not ProposalControlStatus.APPROVED_FOR_ISSUE:
            raise ValueError("Only a current approved-for-issue candidate can be issued")
        if (
            assessment.blockers
            or not assessment.artifacts_verified
            or not assessment.approvals_clear
        ):
            raise ValueError("Readiness, approval, staleness or artifact verification failed")
        candidate = self._candidate(command.candidate_id, bid_id)
        export = self.export_by_id(bid_id, candidate.export_id)
        with self._conn() as conn:
            receipt_row = conn.execute(
                "SELECT * FROM proposal_generation_manifest_receipts WHERE receipt_id=?",
                (candidate.receipt_id,),
            ).fetchone()
        if receipt_row is None:
            raise ProposalExchangeNotFoundError("Generation manifest receipt is missing")
        receipt = self._receipt_from_row(receipt_row)
        artifacts = receipt.artifacts
        integrity_errors = self._artifact_integrity_errors(artifacts)
        if integrity_errors:
            raise ValueError("; ".join(integrity_errors))
        approvals = self._approvals(bid_id)
        now = self._now()
        issue_id = f"PIE-{uuid4().hex}"
        baseline_id = f"IOB-{uuid4().hex}"
        snapshot = {
            "package": strict_json_loads(export.canonical_package_json.encode("utf-8")),
            "manifest": strict_json_loads(receipt.manifest_json.encode("utf-8")),
            "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
            "approvals": approvals,
        }
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = conn.execute(
                    "SELECT status,version FROM proposal_issue_candidates WHERE candidate_id=? AND bid_id=?",
                    (command.candidate_id, bid_id),
                ).fetchone()
                if (
                    current is None
                    or current["status"] != ProposalControlStatus.APPROVED_FOR_ISSUE.value
                    or int(current["version"]) != command.expected_version
                ):
                    raise StaleProposalCandidateError(
                        "Candidate changed; refresh before recording issue"
                    )
                previous = conn.execute(
                    "SELECT baseline_id FROM issued_offer_baselines WHERE bid_id=? ORDER BY rowid DESC LIMIT 1",
                    (bid_id,),
                ).fetchone()
                conn.execute(
                    "INSERT INTO proposal_customer_issue_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        issue_id,
                        bid_id,
                        command.candidate_id,
                        command.issue_revision,
                        command.issued_at.isoformat(),
                        command.issue_method,
                        command.destination_reference,
                        command.offer_valid_until.isoformat()
                        if command.offer_valid_until
                        else None,
                        command.note,
                        export.export_id,
                        receipt.receipt_id,
                        actor,
                        now.isoformat(),
                    ),
                )
                conn.execute(
                    "INSERT INTO issued_offer_baselines VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        baseline_id,
                        issue_id,
                        bid_id,
                        command.candidate_id,
                        command.issue_revision,
                        command.issued_at.isoformat(),
                        command.issue_method,
                        command.destination_reference,
                        command.offer_valid_until.isoformat()
                        if command.offer_valid_until
                        else None,
                        command.note,
                        export.export_id,
                        export.package_id,
                        export.canonical_sha256,
                        receipt.receipt_id,
                        receipt.manifest_sha256,
                        json.dumps(snapshot, sort_keys=True, separators=(",", ":")),
                        str(previous["baseline_id"]) if previous else None,
                        actor,
                        now.isoformat(),
                    ),
                )
                for artifact in artifacts:
                    conn.execute(
                        "INSERT INTO issued_offer_artifacts VALUES(?,?,?,?,?,?,?)",
                        (
                            f"IOA-{uuid4().hex}",
                            baseline_id,
                            artifact.role,
                            artifact.filename,
                            artifact.media_type,
                            artifact.byte_size,
                            artifact.sha256,
                        ),
                    )
                for approval in approvals:
                    conn.execute(
                        "INSERT INTO issued_offer_approvals VALUES(?,?,?,?,?,?)",
                        (
                            f"IOP-{uuid4().hex}",
                            baseline_id,
                            approval["reference"],
                            approval["authority"],
                            approval["decision"],
                            approval["decided_at"],
                        ),
                    )
                changed = conn.execute(
                    """UPDATE proposal_issue_candidates SET status='ISSUED',version=version+1,
                    updated_at=? WHERE candidate_id=? AND version=?""",
                    (now.isoformat(), command.candidate_id, command.expected_version),
                ).rowcount
                if changed != 1:
                    raise StaleProposalCandidateError("Candidate changed during issue")
                self._audit(
                    conn,
                    bid_id,
                    actor,
                    "proposal_customer_issue_recorded",
                    {
                        "issue_id": issue_id,
                        "baseline_id": baseline_id,
                        "candidate_id": command.candidate_id,
                        "issue_revision": command.issue_revision,
                        "package_sha256": export.canonical_sha256,
                    },
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM issued_offer_baselines WHERE baseline_id=?", (baseline_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("Issued baseline persistence failed")
        return self._baseline_from_row(row)

    def handover_rows(self, bid_id: str) -> tuple[dict[str, object], ...]:
        """Project issued offer evidence and live changes for Bid handover/reporting."""
        history = self.history(bid_id)
        assessment = self.assess(bid_id)
        rows: list[dict[str, object]] = []
        for index, baseline in enumerate(history.baselines):
            artifacts = cast(list[dict[str, object]], baseline.snapshot.get("artifacts", []))
            approvals = cast(list[dict[str, object]], baseline.snapshot.get("approvals", []))
            rows.append(
                {
                    "baseline_id": baseline.baseline_id,
                    "issue_revision": baseline.issue_revision,
                    "issued_at": baseline.issued_at.isoformat(),
                    "issue_method": baseline.issue_method,
                    "destination_reference": baseline.destination_reference,
                    "offer_valid_until": (
                        baseline.offer_valid_until.isoformat()
                        if baseline.offer_valid_until
                        else None
                    ),
                    "package_id": baseline.package_id,
                    "package_sha256": baseline.package_sha256,
                    "manifest_id": baseline.receipt_id,
                    "manifest_sha256": baseline.manifest_sha256,
                    "artifact_identities": " | ".join(
                        f"{item['filename']} ({item['sha256']})" for item in artifacts
                    ),
                    "approval_references": " | ".join(str(item["reference"]) for item in approvals),
                    "superseded": index > 0,
                    "changes_after_issue": (
                        ", ".join(assessment.changed_areas)
                        if index == 0 and assessment.changed_areas
                        else "None detected"
                    ),
                    "remaining_actions": " | ".join(
                        blocker.message for blocker in assessment.blockers
                    ),
                    "actor": baseline.actor,
                    "recorded_at": baseline.recorded_at.isoformat(),
                }
            )
        return tuple(rows)
