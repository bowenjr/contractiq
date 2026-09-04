"""OPS-07W deterministic workflow integration and additive evidence persistence."""

# ruff: noqa: E501

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.bid_repository import BidRepository
from core.classifier import ClassificationInput, ClassificationResult, classify
from core.database import Database
from core.enums import Actor, BidLevel, RiskTrigger
from core.export_controls import manufacturer_confirmation_clear, manufacturer_confirmation_issue
from core.schemas import Bid, Provenance
from core.work_items import (
    ResponsibilityDomain,
    WorkCategory,
    WorkItem,
    WorkItemCreate,
    WorkItemKind,
    WorkItemPriority,
    WorkItemStatus,
)

OPS07W_MIGRATION_ID = "ops_07w_workflow_integration_v1"
CLASSIFIER_POLICY_VERSION = "classifier-config-v1"
_LEVEL_RANK = {level: index for index, level in enumerate(BidLevel)}


class ClassificationAssessmentCommand(BaseModel):
    """Server-authoritative inputs for one append-only classification assessment."""

    model_config = ConfigDict(extra="forbid")

    estimated_value: Decimal = Field(ge=0)
    customer_type: str
    is_epc_epcm: bool = False
    strategic_customer: bool = False
    triggers: list[RiskTrigger] = Field(default_factory=list)
    selected_level: BidLevel
    override_rationale: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def normalize(self) -> ClassificationAssessmentCommand:
        self.triggers = sorted(set(self.triggers), key=lambda item: item.value)
        if self.override_rationale is not None:
            self.override_rationale = self.override_rationale.strip() or None
        return self


@dataclass(frozen=True)
class ClassificationAssessment:
    assessment_id: str
    bid_id: str
    sequence: int
    recommended_level: BidLevel
    selected_level: BidLevel
    rationale: tuple[str, ...]
    override_rationale: str | None
    triggers: tuple[RiskTrigger, ...]
    actor: str
    created_at: datetime


@dataclass(frozen=True)
class ExactEvidenceView:
    verification_row_id: str
    package_id: str
    package_code: str
    deliverable_title: str
    verification_status: str
    response_source: str | None
    response_received_date: str | None
    evidence_reference: str | None
    commercial_impact: str
    issue: str | None
    clear: bool


@dataclass(frozen=True)
class NavigatorStage:
    key: str
    label: str
    status: str
    completeness: str
    primary_label: str
    primary_path: str
    detail_path: str
    blocker: str | None = None


class Ops07WorkflowRepository:
    """One bounded persistence boundary for OPS-07W additions."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._migrate()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _migrate(self) -> None:
        statements = (
            """CREATE TABLE IF NOT EXISTS bid_classification_assessments(
                assessment_id TEXT PRIMARY KEY, bid_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                input_value TEXT NOT NULL, customer_type TEXT NOT NULL,
                is_epc_epcm INTEGER NOT NULL, strategic_customer INTEGER NOT NULL,
                recommended_level TEXT NOT NULL, selected_level TEXT NOT NULL,
                value_band_level TEXT NOT NULL, trigger_floor_level TEXT NOT NULL,
                deterministic_rationale_json TEXT NOT NULL, override_rationale TEXT,
                policy_version TEXT NOT NULL, bid_updated_at_snapshot TEXT NOT NULL,
                actor TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(bid_id) REFERENCES bids(bid_id), UNIQUE(bid_id,sequence),
                CHECK(sequence >= 1),
                CHECK(override_rationale IS NULL OR length(trim(override_rationale)) > 0))""",
            """CREATE TABLE IF NOT EXISTS bid_classification_assessment_triggers(
                assessment_id TEXT NOT NULL, trigger TEXT NOT NULL,
                PRIMARY KEY(assessment_id,trigger),
                FOREIGN KEY(assessment_id) REFERENCES bid_classification_assessments(assessment_id))""",
            """CREATE TABLE IF NOT EXISTS requirement_verification_links(
                link_id TEXT PRIMARY KEY, bid_id TEXT NOT NULL, requirement_id TEXT NOT NULL,
                verification_row_id TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
                FOREIGN KEY(bid_id) REFERENCES bids(bid_id),
                FOREIGN KEY(requirement_id) REFERENCES requirements(requirement_id),
                FOREIGN KEY(verification_row_id) REFERENCES vendor_bid_requirements(requirement_id),
                UNIQUE(requirement_id,verification_row_id))""",
            "CREATE INDEX IF NOT EXISTS idx_ops07w_assessment_bid ON bid_classification_assessments(bid_id,sequence)",
            "CREATE INDEX IF NOT EXISTS idx_ops07w_evidence_requirement ON requirement_verification_links(requirement_id,verification_row_id)",
            """CREATE TABLE IF NOT EXISTS ops07w_schema_migrations(
                migration_id TEXT PRIMARY KEY, applied_at TEXT NOT NULL)""",
        )
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT OR IGNORE INTO ops07w_schema_migrations VALUES(?,?)",
                    (OPS07W_MIGRATION_ID, datetime.now(UTC).isoformat()),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def assess(command: ClassificationAssessmentCommand) -> ClassificationResult:
        is_epc_epcm = command.is_epc_epcm or command.customer_type.casefold() in {"epc", "epcm"}
        return classify(
            ClassificationInput(
                estimated_value=command.estimated_value,
                triggers=command.triggers,
                is_epc_epcm=is_epc_epcm,
                strategic_customer=command.strategic_customer,
            )
        )

    @classmethod
    def validate_level(
        cls, command: ClassificationAssessmentCommand, result: ClassificationResult
    ) -> None:
        selected_rank = _LEVEL_RANK[command.selected_level]
        minimum_rank = _LEVEL_RANK[result.level]
        if selected_rank < minimum_rank:
            raise ValueError(
                f"Selected governance level cannot be lower than the recommended minimum {result.level.value.replace('_', ' ').title()}"
            )
        if selected_rank > minimum_rank and command.override_rationale is None:
            raise ValueError("A reason is required when selecting a higher governance level")
        if command.selected_level is BidLevel.LEVEL_4 and result.level is not BidLevel.LEVEL_4:
            # Level 4 may be selected upward, but remains visibly an override rather than inferred.
            if command.override_rationale is None:
                raise ValueError("Level 4 requires a trigger or an upward-override rationale")

    def record_assessment(
        self,
        bid_id: str,
        command: ClassificationAssessmentCommand,
        actor: str,
        *,
        expected_bid_updated_at: str | None = None,
    ) -> ClassificationAssessment:
        """Append evidence and update governance atomically without silently lowering it."""
        result = self.assess(command)
        self.validate_level(command, result)
        now = datetime.now(UTC)
        assessment_id = f"BCA-{uuid4()}"
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            bid = conn.execute(
                "SELECT classification,updated_at FROM bids WHERE bid_id=?", (bid_id,)
            ).fetchone()
            if bid is None:
                raise ValueError(f"Bid not found: {bid_id}")
            if (
                expected_bid_updated_at is not None
                and str(bid["updated_at"]) != expected_bid_updated_at
            ):
                raise ValueError("Bid classification is stale; reload and reassess")
            current = BidLevel(str(bid["classification"]))
            if _LEVEL_RANK[command.selected_level] < _LEVEL_RANK[current]:
                raise ValueError("Reassessment cannot silently lower the existing governance level")
            sequence = int(
                conn.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 FROM bid_classification_assessments WHERE bid_id=?",
                    (bid_id,),
                ).fetchone()[0]
            )
            rationale_json = json.dumps(result.rationale, sort_keys=True)
            conn.execute(
                """INSERT INTO bid_classification_assessments(
                assessment_id,bid_id,sequence,input_value,customer_type,is_epc_epcm,
                strategic_customer,recommended_level,selected_level,value_band_level,
                trigger_floor_level,deterministic_rationale_json,override_rationale,
                policy_version,bid_updated_at_snapshot,actor,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    assessment_id,
                    bid_id,
                    sequence,
                    str(command.estimated_value),
                    command.customer_type,
                    int(command.is_epc_epcm or command.customer_type.casefold() in {"epc", "epcm"}),
                    int(command.strategic_customer),
                    result.level.value,
                    command.selected_level.value,
                    result.value_band_level.value,
                    result.trigger_floor_level.value,
                    rationale_json,
                    command.override_rationale,
                    CLASSIFIER_POLICY_VERSION,
                    str(bid["updated_at"]),
                    actor,
                    now.isoformat(),
                ),
            )
            for trigger in command.triggers:
                conn.execute(
                    "INSERT INTO bid_classification_assessment_triggers VALUES(?,?)",
                    (assessment_id, trigger.value),
                )
            conn.execute(
                "UPDATE bids SET classification=?,risk_triggers=?,updated_at=? WHERE bid_id=? AND updated_at=?",
                (
                    command.selected_level.value,
                    json.dumps([item.value for item in command.triggers]),
                    now.isoformat(),
                    bid_id,
                    bid["updated_at"],
                ),
            )
            if conn.execute("SELECT changes()").fetchone()[0] != 1:
                raise ValueError("Bid classification is stale; reload and reassess")
            detail = {
                "assessment_id": assessment_id,
                "sequence": sequence,
                "recommended_level": result.level.value,
                "selected_level": command.selected_level.value,
                "override_rationale": command.override_rationale,
                "rationale": result.rationale,
                "triggers": [item.value for item in command.triggers],
            }
            conn.execute(
                "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) VALUES(?,?,?,?,?,?)",
                (
                    f"AUD-{uuid4()}",
                    bid_id,
                    actor,
                    "bid_classification_assessed",
                    json.dumps(detail, sort_keys=True),
                    now.isoformat(),
                ),
            )
            conn.commit()
        return ClassificationAssessment(
            assessment_id,
            bid_id,
            sequence,
            result.level,
            command.selected_level,
            tuple(result.rationale),
            command.override_rationale,
            tuple(command.triggers),
            actor,
            now,
        )

    def create_bid_with_assessment(
        self,
        bid: Bid,
        command: ClassificationAssessmentCommand,
        actor: str,
    ) -> ClassificationAssessment:
        """Create Bid, append calculation evidence, and audit in one transaction."""
        result = self.assess(command)
        self.validate_level(command, result)
        if bid.classification is not command.selected_level:
            raise ValueError("Bid classification must match the assessed selected level")
        now = datetime.now(UTC)
        assessment_id = f"BCA-{uuid4()}"
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                BidRepository._insert_bid(conn, bid)
                conn.execute(
                    """INSERT INTO bid_classification_assessments(
                    assessment_id,bid_id,sequence,input_value,customer_type,is_epc_epcm,
                    strategic_customer,recommended_level,selected_level,value_band_level,
                    trigger_floor_level,deterministic_rationale_json,override_rationale,
                    policy_version,bid_updated_at_snapshot,actor,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        assessment_id,
                        bid.bid_id,
                        1,
                        str(command.estimated_value),
                        command.customer_type,
                        int(
                            command.is_epc_epcm
                            or command.customer_type.casefold() in {"epc", "epcm"}
                        ),
                        int(command.strategic_customer),
                        result.level.value,
                        command.selected_level.value,
                        result.value_band_level.value,
                        result.trigger_floor_level.value,
                        json.dumps(result.rationale, sort_keys=True),
                        command.override_rationale,
                        CLASSIFIER_POLICY_VERSION,
                        bid.updated_at.isoformat(),
                        actor,
                        now.isoformat(),
                    ),
                )
                for trigger in command.triggers:
                    conn.execute(
                        "INSERT INTO bid_classification_assessment_triggers VALUES(?,?)",
                        (assessment_id, trigger.value),
                    )
                conn.execute(
                    "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) VALUES(?,?,?,?,?,?)",
                    (
                        f"AUD-{uuid4()}",
                        bid.bid_id,
                        actor,
                        "bid_project_created",
                        json.dumps(
                            {"bid_id": bid.bid_id, "project_name": bid.project_name}, sort_keys=True
                        ),
                        now.isoformat(),
                    ),
                )
                conn.execute(
                    "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) VALUES(?,?,?,?,?,?)",
                    (
                        f"AUD-{uuid4()}",
                        bid.bid_id,
                        actor,
                        "bid_classification_assessed",
                        json.dumps(
                            {
                                "assessment_id": assessment_id,
                                "recommended_level": result.level.value,
                                "selected_level": command.selected_level.value,
                                "override_rationale": command.override_rationale,
                                "rationale": result.rationale,
                                "triggers": [item.value for item in command.triggers],
                            },
                            sort_keys=True,
                        ),
                        now.isoformat(),
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return ClassificationAssessment(
            assessment_id,
            bid.bid_id,
            1,
            result.level,
            command.selected_level,
            tuple(result.rationale),
            command.override_rationale,
            tuple(command.triggers),
            actor,
            now,
        )

    def latest_assessment(self, bid_id: str) -> ClassificationAssessment | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM bid_classification_assessments WHERE bid_id=? ORDER BY sequence DESC LIMIT 1",
                (bid_id,),
            ).fetchone()
            if row is None:
                return None
            triggers = tuple(
                RiskTrigger(str(item[0]))
                for item in conn.execute(
                    "SELECT trigger FROM bid_classification_assessment_triggers WHERE assessment_id=? ORDER BY trigger",
                    (row["assessment_id"],),
                )
            )
        return ClassificationAssessment(
            str(row["assessment_id"]),
            bid_id,
            int(row["sequence"]),
            BidLevel(str(row["recommended_level"])),
            BidLevel(str(row["selected_level"])),
            tuple(json.loads(str(row["deterministic_rationale_json"]))),
            str(row["override_rationale"]) if row["override_rationale"] else None,
            triggers,
            str(row["actor"]),
            datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _audit(
        conn: sqlite3.Connection,
        bid_id: str,
        actor: str,
        action: str,
        detail: dict[str, object],
    ) -> None:
        conn.execute(
            "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) VALUES(?,?,?,?,?,?)",
            (
                f"AUD-{uuid4()}",
                bid_id,
                actor,
                action,
                json.dumps(detail, sort_keys=True),
                datetime.now(UTC).isoformat(),
            ),
        )

    def link_exact_evidence(
        self, bid_id: str, requirement_id: str, verification_row_id: str, actor: str
    ) -> None:
        """Link exact same-Bid package evidence after validating package responsibility."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT r.bid_id requirement_bid,v.package_id,p.bid_id package_bid
                FROM requirements r, vendor_bid_requirements v
                JOIN vendor_bid_packages p ON p.package_id=v.package_id
                WHERE r.requirement_id=? AND v.requirement_id=?""",
                (requirement_id, verification_row_id),
            ).fetchone()
            if row is None:
                raise ValueError("Requirement or manufacturer verification row was not found")
            if str(row["requirement_bid"]) != bid_id or str(row["package_bid"]) != bid_id:
                raise ValueError(
                    "Requirement and manufacturer evidence must belong to the same Bid"
                )
            package_id = str(row["package_id"])
            associated = conn.execute(
                "SELECT 1 FROM requirement_manufacturer_links WHERE requirement_id=? AND package_id=?",
                (requirement_id, package_id),
            ).fetchone()
            if associated is None:
                raise ValueError("Associate the manufacturer package before linking exact evidence")
            try:
                conn.execute(
                    "INSERT INTO requirement_verification_links VALUES(?,?,?,?,?,?)",
                    (
                        f"RVL-{uuid4()}",
                        bid_id,
                        requirement_id,
                        verification_row_id,
                        datetime.now(UTC).isoformat(),
                        actor,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("This manufacturer response is already linked") from exc
            self._audit(
                conn,
                bid_id,
                actor,
                "requirement_verification_linked",
                {"requirement_id": requirement_id, "verification_row_id": verification_row_id},
            )

    def unlink_exact_evidence(
        self, bid_id: str, requirement_id: str, verification_row_id: str, actor: str
    ) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = conn.execute(
                "DELETE FROM requirement_verification_links WHERE bid_id=? AND requirement_id=? AND verification_row_id=?",
                (bid_id, requirement_id, verification_row_id),
            )
            if result.rowcount != 1:
                raise ValueError("Exact manufacturer evidence link was not found")
            self._audit(
                conn,
                bid_id,
                actor,
                "requirement_verification_unlinked",
                {"requirement_id": requirement_id, "verification_row_id": verification_row_id},
            )

    def exact_evidence(self, requirement_id: str) -> list[ExactEvidenceView]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT v.requirement_id verification_row_id,v.package_id,p.package_code,
                v.deliverable_title,v.verification_status,v.response_source,
                v.response_received_date,v.evidence_reference,v.commercial_impact
                FROM requirement_verification_links l
                JOIN vendor_bid_requirements v ON v.requirement_id=l.verification_row_id
                JOIN vendor_bid_packages p ON p.package_id=v.package_id
                WHERE l.requirement_id=?
                ORDER BY lower(p.package_code),lower(v.deliverable_title),v.requirement_id""",
                (requirement_id,),
            ).fetchall()
        result = []
        for row in rows:
            clear = manufacturer_confirmation_clear(
                row["verification_status"], row["response_source"], row["response_received_date"]
            )
            result.append(
                ExactEvidenceView(
                    str(row["verification_row_id"]),
                    str(row["package_id"]),
                    str(row["package_code"]),
                    str(row["deliverable_title"]),
                    str(row["verification_status"]),
                    str(row["response_source"]) if row["response_source"] else None,
                    str(row["response_received_date"]) if row["response_received_date"] else None,
                    str(row["evidence_reference"]) if row["evidence_reference"] else None,
                    str(row["commercial_impact"]),
                    manufacturer_confirmation_issue(
                        row["verification_status"],
                        row["response_source"],
                        row["response_received_date"],
                    ),
                    clear,
                )
            )
        return result

    def available_exact_evidence(self, requirement_id: str) -> list[dict[str, object]]:
        """Return only rows from packages already assigned to this requirement."""
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """SELECT v.*,p.package_code,p.package_name
                    FROM requirement_manufacturer_links ml
                    JOIN vendor_bid_requirements v ON v.package_id=ml.package_id
                    JOIN vendor_bid_packages p ON p.package_id=v.package_id
                    LEFT JOIN requirement_verification_links el
                      ON el.requirement_id=ml.requirement_id AND el.verification_row_id=v.requirement_id
                    WHERE ml.requirement_id=? AND el.link_id IS NULL
                    ORDER BY lower(p.package_code),lower(v.deliverable_title),v.requirement_id""",
                    (requirement_id,),
                )
            ]

    def exact_evidence_clear(self, requirement_id: str) -> bool:
        rows = self.exact_evidence(requirement_id)
        return bool(rows) and all(row.clear for row in rows)

    def create_linked_work(
        self,
        *,
        source_kind: str,
        source_id: str,
        title: str,
        purpose: str,
        category: WorkCategory,
        actor: str,
        next_action_date: date | None = None,
    ) -> WorkItem:
        """Create Bid-owned work and its one authoritative source link atomically."""
        source_map = {
            "requirement": ("requirements", "requirement_id"),
            "scope": ("scope_interface_items", "scope_item_id"),
            "interface": ("scope_interfaces", "interface_id"),
            "manufacturer": ("vendor_bid_packages", "package_id"),
        }
        if source_kind not in source_map:
            raise ValueError("Unsupported My Work source kind")
        request = WorkItemCreate.model_validate(
            {
                "title": title,
                "category": category,
                "next_action_date": next_action_date,
            }
        )
        normalized_purpose = purpose.strip()
        if not normalized_purpose:
            raise ValueError("Purpose must be non-empty")
        table, column = source_map[source_kind]
        now = datetime.now(UTC)
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            source = conn.execute(
                f"SELECT bid_id FROM {table} WHERE {column}=?", (source_id,)
            ).fetchone()
            if source is None:
                raise ValueError("Authoritative source was not found")
            bid_id = str(source["bid_id"])
            item = WorkItem(
                work_item_id=f"WI-{uuid4()}",
                bid_id=bid_id,
                kind=WorkItemKind.TASK,
                title=request.title,
                details=request.details,
                status=WorkItemStatus.OPEN,
                priority=WorkItemPriority.NORMAL,
                due_date=None,
                waiting_on=None,
                blocker_note=None,
                category=request.category,
                responsibility_domain=ResponsibilityDomain.CROSS_FUNCTIONAL,
                next_action_date=request.next_action_date,
                created_at=now,
                updated_at=now,
                completed_at=None,
                version=1,
                provenance=Provenance(
                    created_by=Actor.HUMAN,
                    agent_name=actor,
                    created_at=now,
                    human_confirmed=True,
                    confirmed_by=actor,
                    confirmed_at=now,
                ),
            )
            values = (
                item.work_item_id,
                item.bid_id,
                item.kind.value,
                item.title,
                item.details,
                item.status.value,
                item.priority.value,
                None,
                None,
                None,
                now.isoformat(),
                now.isoformat(),
                None,
                1,
                item.provenance.model_dump_json(),
                item.category.value,
                ResponsibilityDomain.CROSS_FUNCTIONAL.value,
                item.next_action_date.isoformat() if item.next_action_date else None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                0,
            )
            conn.execute(
                """INSERT INTO work_items(work_item_id,bid_id,kind,title,details,status,priority,due_date,
                waiting_on,blocker_note,created_at,updated_at,completed_at,version,provenance_json,category,
                responsibility_domain,next_action_date,requester_label,waiting_party_kind,waiting_party_label,
                waiting_owed,requested_date,chase_date,blocker_description,resolution_owner,review_date,
                completion_outcome,cancellation_reason,completion_evidence,contribution_candidate)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values,
            )
            link_values: dict[str, str | None] = {
                "requirement_id": None,
                "scope_item_id": None,
                "interface_id": None,
                "package_id": None,
            }
            link_values[column] = source_id
            try:
                conn.execute(
                    """INSERT INTO domain_work_item_links(link_id,bid_id,work_item_id,purpose,
                    requirement_id,scope_item_id,interface_id,package_id,created_at,created_by)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        f"DWL-{uuid4()}",
                        bid_id,
                        item.work_item_id,
                        normalized_purpose,
                        link_values["requirement_id"],
                        link_values["scope_item_id"],
                        link_values["interface_id"],
                        link_values["package_id"],
                        now.isoformat(),
                        actor,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("An action is already linked for this source and purpose") from exc
            self._audit(
                conn,
                bid_id,
                actor,
                "work_item_created",
                {
                    "work_item_id": item.work_item_id,
                    "source_kind": source_kind,
                    "source_id": source_id,
                },
            )
            self._audit(
                conn,
                bid_id,
                actor,
                "domain_work_item_linked",
                {
                    "work_item_id": item.work_item_id,
                    "source_kind": source_kind,
                    "source_id": source_id,
                    "purpose": normalized_purpose,
                },
            )
            conn.commit()
        return item

    def linked_work(self, source_kind: str, source_id: str) -> list[dict[str, object]]:
        columns = {
            "requirement": "requirement_id",
            "scope": "scope_item_id",
            "interface": "interface_id",
            "manufacturer": "package_id",
        }
        column = columns.get(source_kind)
        if column is None:
            raise ValueError("Unsupported My Work source kind")
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    f"""SELECT w.work_item_id,w.title,w.status,l.purpose
                    FROM domain_work_item_links l JOIN work_items w ON w.work_item_id=l.work_item_id
                    WHERE l.{column}=? ORDER BY lower(w.title),w.work_item_id""",
                    (source_id,),
                )
            ]

    def navigator_counts(self, bid_id: str) -> dict[str, int]:
        """Resolve stage counts and exact manufacturer completion in bounded queries."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT
                (SELECT count(*) FROM documents WHERE bid_id=:bid) documents,
                (SELECT count(*) FROM requirements WHERE bid_id=:bid AND lifecycle_state='ACTIVE') requirements,
                (SELECT count(*) FROM requirements WHERE bid_id=:bid AND lifecycle_state='ACTIVE' AND response_text IS NOT NULL) responses,
                (SELECT count(*) FROM scope_interface_items WHERE bid_id=:bid AND lifecycle_state='ACTIVE') scopes,
                (SELECT count(*) FROM scope_interfaces WHERE bid_id=:bid AND lifecycle_state='ACTIVE') interfaces,
                (SELECT count(*) FROM vendor_bid_packages WHERE bid_id=:bid) packages,
                (SELECT count(*) FROM requirement_manufacturer_links WHERE bid_id=:bid) manufacturer_associations,
                (SELECT count(*) FROM commercial_items WHERE bid_id=:bid AND lifecycle_state <> 'WITHDRAWN') commercial,
                (SELECT count(*) FROM contract_issues WHERE bid_id=:bid AND lifecycle_state <> 'WITHDRAWN') risks,
                (SELECT count(*) FROM decision_cases WHERE bid_id=:bid) decisions,
                (SELECT count(*) FROM approvals WHERE bid_id=:bid) approvals,
                (SELECT count(*) FROM proposal_families WHERE bid_id=:bid) proposals,
                (SELECT count(*) FROM deliverable_items WHERE bid_id=:bid) deliverables,
                (SELECT count(*) FROM bid_classification_assessments WHERE bid_id=:bid) assessments""",
                {"bid": bid_id},
            ).fetchone()
            if conn.execute("SELECT 1 FROM bids WHERE bid_id=?", (bid_id,)).fetchone() is None:
                raise ValueError(f"Bid not found: {bid_id}")
            evidence_rows = conn.execute(
                """SELECT ml.requirement_id,l.verification_row_id,v.verification_status,
                v.response_source,v.response_received_date
                FROM requirement_manufacturer_links ml
                LEFT JOIN requirement_verification_links l ON l.requirement_id=ml.requirement_id
                LEFT JOIN vendor_bid_requirements v ON v.requirement_id=l.verification_row_id
                WHERE ml.bid_id=? ORDER BY ml.requirement_id,l.verification_row_id""",
                (bid_id,),
            ).fetchall()
        result = {key: int(row[key]) for key in row.keys()}
        requirements: dict[str, list[sqlite3.Row]] = {}
        for evidence in evidence_rows:
            requirements.setdefault(str(evidence["requirement_id"]), []).append(evidence)
        complete = 0
        blocked = 0
        for linked in requirements.values():
            actual = [item for item in linked if item["verification_row_id"] is not None]
            if actual and all(
                manufacturer_confirmation_clear(
                    item["verification_status"],
                    item["response_source"],
                    item["response_received_date"],
                )
                for item in actual
            ):
                complete += 1
            elif any(
                str(item["verification_status"])
                in {"CONFIRMED_WITH_EXCEPTION", "CLARIFICATION_REQUIRED", "CANNOT_COMPLY"}
                for item in actual
            ):
                blocked += 1
        result["manufacturer_requirements"] = len(requirements)
        result["manufacturer_complete"] = complete
        result["manufacturer_blocked"] = blocked
        return result

    def package_requirement_evidence(self, package_id: str) -> dict[str, list[dict[str, str]]]:
        """Batch-resolve TASK-09 requirements linked to each exact package evidence row."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT l.verification_row_id,r.requirement_id,r.statement,r.bid_id
                FROM requirement_verification_links l
                JOIN requirements r ON r.requirement_id=l.requirement_id
                JOIN vendor_bid_requirements v ON v.requirement_id=l.verification_row_id
                WHERE v.package_id=? ORDER BY l.verification_row_id,r.requirement_id""",
                (package_id,),
            ).fetchall()
        grouped: dict[str, list[dict[str, str]]] = {}
        for item in rows:
            grouped.setdefault(str(item["verification_row_id"]), []).append(
                {
                    "requirement_id": str(item["requirement_id"]),
                    "statement": str(item["statement"]),
                    "bid_id": str(item["bid_id"]),
                }
            )
        return grouped


def build_navigator(
    bid_id: str, counts: dict[str, int], readiness_verdict: str, owner: str
) -> list[NavigatorStage]:
    """Pure presentation projection; completeness never changes TASK-06 readiness."""
    requirements = counts["requirements"]
    responses = counts["responses"]
    paths = {
        "setup": f"/bids/{bid_id}/classification",
        "documents": f"/documents?bid_id={bid_id}#register-form",
        "requirements": f"/requirements?bid_id={bid_id}#create",
        "scope": f"/scope-interfaces?bid_id={bid_id}#create-scope",
        "manufacturer": f"/vendor-documents?bid_id={bid_id}#create-package",
        "commercial": f"/commercial?bid_id={bid_id}#author",
        "proposal": f"/proposals?bid_id={bid_id}#author",
        "handover": f"/bids/{bid_id}/requirements-handover.csv",
    }
    manufacturer_total = counts["manufacturer_requirements"]
    manufacturer_complete = counts["manufacturer_complete"]
    definitions = [
        (
            "setup",
            "Bid setup and classification",
            counts["assessments"],
            counts["assessments"],
            False,
            "Classification assessed",
        ),
        (
            "documents",
            "Customer documents and controlled sources",
            counts["documents"],
            counts["documents"],
            False,
            f"{counts['documents']} controlled source(s)",
        ),
        (
            "requirements",
            "Customer requirements and proposed responses",
            requirements,
            requirements and responses == requirements,
            False,
            f"{responses}/{requirements} responses recorded",
        ),
        (
            "scope",
            "Scope and interfaces",
            counts["scopes"] + counts["interfaces"],
            counts["scopes"] and counts["interfaces"],
            False,
            f"{counts['scopes']} scope item(s), {counts['interfaces']} interface(s)",
        ),
        (
            "manufacturer",
            "Manufacturers and coverage",
            counts["packages"] + counts["manufacturer_associations"] + manufacturer_total,
            manufacturer_total > 0 and manufacturer_complete == manufacturer_total,
            counts["manufacturer_blocked"] > 0,
            f"{manufacturer_complete}/{manufacturer_total} requirement(s) have complete exact evidence; {counts['packages']} package(s)",
        ),
        (
            "commercial",
            "Commercial, contract risk and approvals",
            counts["commercial"] + counts["risks"] + counts["decisions"] + counts["approvals"],
            counts["commercial"]
            and counts["risks"]
            and counts["decisions"]
            and counts["approvals"],
            False,
            f"{counts['commercial']} commercial, {counts['risks']} risk, {counts['approvals']} gate approval, {counts['decisions']} decision case(s)",
        ),
        (
            "proposal",
            "Proposal readiness",
            counts["proposals"] + counts["deliverables"],
            counts["proposals"] and counts["deliverables"],
            False,
            f"{counts['proposals']} proposal family, {counts['deliverables']} deliverable(s)",
        ),
        (
            "handover",
            "Handover",
            1,
            readiness_verdict.casefold() == "clear",
            readiness_verdict.casefold() != "clear",
            f"TASK-06 readiness: {readiness_verdict.upper()}",
        ),
    ]
    details = {
        "setup": f"/bids/{bid_id}",
        "documents": f"/documents?bid_id={bid_id}",
        "requirements": f"/bids/{bid_id}/requirements-scope",
        "scope": f"/scope-interfaces?bid_id={bid_id}",
        "manufacturer": f"/bids/{bid_id}/manufacturers-coverage",
        "commercial": f"/bids/{bid_id}/commercial-contract",
        "proposal": f"/bids/{bid_id}/proposal-negotiation",
        "handover": f"/bids/{bid_id}/award-handover",
    }
    labels = {
        "setup": "Review classification",
        "documents": "Register customer source",
        "requirements": "Add customer requirement",
        "scope": "Add scope item",
        "manufacturer": "Add supplier package",
        "commercial": "Add commercial exception",
        "proposal": "Create proposal family",
        "handover": "Download requirements handover",
    }
    stages = []
    for key, label, started, complete, blocked, statement in definitions:
        status = (
            "Blocked"
            if blocked
            else "Complete"
            if bool(complete)
            else "In progress"
            if bool(started)
            else "Not started"
        )
        stages.append(
            NavigatorStage(
                key,
                label,
                status,
                statement,
                labels[key],
                paths[key],
                details[key],
                f"TASK-06 is {readiness_verdict.upper()}; accountable: {owner}"
                if status == "Blocked"
                else None,
            )
        )
    return stages
