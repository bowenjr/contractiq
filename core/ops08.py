"""OPS-08 commercial-position control and deterministic workspace projection."""
# SQL DDL and deterministic export rows remain contiguous for audit readability.
# ruff: noqa: E501

from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, model_validator

from core.database import Database
from core.enums import Actor
from core.export_controls import csv_safe_cell
from core.schemas import Provenance
from core.work_items import (
    ResponsibilityDomain,
    WorkCategory,
    WorkItem,
    WorkItemCreate,
    WorkItemKind,
    WorkItemPriority,
    WorkItemStatus,
)

MIGRATION_ID = "ops_08_commercial_contract_risk_workspace_v1"


class PositionDisposition(StrEnum):
    NOT_REVIEWED = "NOT_REVIEWED"
    ACCEPT = "ACCEPT"
    QUALIFY = "QUALIFY"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    REJECT = "REJECT"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class NegotiationState(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"


class PositionRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_position: str = ""
    proposed_position: str = ""
    disposition: PositionDisposition = PositionDisposition.NOT_REVIEWED
    source_document_version_id: str | None = None
    clause: str | None = None
    section: str | None = None
    page: str | None = None
    source_locator: str | None = None
    interpretation: str | None = None
    rationale: str | None = None
    financial_impact: str | None = None
    schedule_impact: str | None = None
    operational_impact: str | None = None
    owner: str | None = None
    contributor: str | None = None
    reviewer: str | None = None
    required_approver: str | None = None
    due_date: date | None = None
    negotiation_state: NegotiationState = NegotiationState.NOT_STARTED
    supporting_evidence: str | None = None
    current_outcome: str | None = None

    @model_validator(mode="after")
    def disposition_requirements(self) -> PositionRevision:
        explicit = bool(self.customer_position.strip())
        if explicit and (
            not self.source_document_version_id or not (self.source_locator or "").strip()
        ):
            raise ValueError("explicit customer terms require exact source version and locator")
        if self.disposition is PositionDisposition.NOT_REVIEWED:
            return self
        if not self.owner:
            raise ValueError("reviewed commercial positions require an accountable owner")
        if self.disposition is PositionDisposition.ACCEPT and not explicit:
            raise ValueError("accept requires the customer position and exact source")
        if self.disposition is PositionDisposition.QUALIFY and not self.proposed_position.strip():
            raise ValueError("qualify requires our proposed Bid position")
        if self.disposition is PositionDisposition.CLARIFICATION_REQUIRED and (
            not self.current_outcome or not self.due_date
        ):
            raise ValueError("clarification requires a next action and due date")
        if (
            self.disposition in {PositionDisposition.REJECT, PositionDisposition.NOT_APPLICABLE}
            and not (self.rationale or "").strip()
        ):
            raise ValueError("reject and not applicable require a reason")
        if self.disposition is PositionDisposition.REJECT and not self.required_approver:
            raise ValueError("reject requires an escalation approver")
        return self


STANDARD_TOPICS: tuple[tuple[str, str, str, str], ...] = (
    ("PRICE_BASIS", "Price basis", "Pricing", "Customer price basis and inclusions"),
    ("BID_VALIDITY", "Bid validity", "Pricing", "Validity period and expiry"),
    ("CURRENCY", "Currency", "Pricing", "Transaction and settlement currency"),
    ("FX_EXPOSURE", "Foreign-exchange exposure", "Pricing", "Currency movement allocation"),
    ("TAXES_DUTIES", "Taxes and duties", "Pricing", "Tax, duty and brokerage responsibility"),
    ("FREIGHT_DELIVERY", "Freight and delivery terms", "Delivery", "Freight responsibility"),
    ("INCOTERMS", "Incoterms", "Delivery", "Applicable delivery rule"),
    ("PAYMENT_TERMS", "Payment terms", "Payment", "Payment timing and credit"),
    ("BILLING_MILESTONES", "Billing milestones", "Payment", "Invoice milestone evidence"),
    ("HOLDBACK", "Holdback / retainage", "Payment", "Retained payment"),
    ("BONDS_GUARANTEES", "Bonds and guarantees", "Security", "Security instruments"),
    ("LETTERS_OF_CREDIT", "Letters of credit", "Security", "Letter-of-credit requirements"),
    ("INSURANCE", "Insurance", "Risk allocation", "Required insurance"),
    ("WARRANTY", "Warranty", "Risk allocation", "Warranty scope and period"),
    ("LIQUIDATED_DAMAGES", "Liquidated damages", "Risk allocation", "Delay or performance damages"),
    (
        "LIMITATION_LIABILITY",
        "Limitation of liability",
        "Risk allocation",
        "Liability cap and exclusions",
    ),
    ("INDEMNITY", "Indemnity", "Risk allocation", "Indemnity obligations"),
    ("TERMINATION", "Termination", "Legal terms", "Termination rights and effects"),
    ("SUSPENSION", "Suspension", "Legal terms", "Suspension rights and effects"),
    ("SCHEDULE", "Schedule obligations", "Delivery", "Schedule commitments"),
    ("CUSTOMER_DELAY", "Customer-caused delay", "Delivery", "Customer delay relief"),
    ("CHANGE_MANAGEMENT", "Change management", "Legal terms", "Change control"),
    ("FORCE_MAJEURE", "Force majeure", "Legal terms", "Excusable events"),
    ("INTELLECTUAL_PROPERTY", "Intellectual property", "Legal terms", "IP ownership and licence"),
    ("CONFIDENTIALITY", "Confidentiality", "Legal terms", "Confidentiality obligations"),
    ("GOVERNING_LAW", "Governing law", "Legal terms", "Applicable law"),
    ("DISPUTE_RESOLUTION", "Dispute resolution", "Legal terms", "Dispute process"),
    ("ESCALATION", "Price escalation", "Pricing", "Price adjustment mechanism"),
    ("COMMODITY_EXPOSURE", "Commodity exposure", "Pricing", "Commodity movement allocation"),
    ("SEPARATELY_CHARGEABLE", "Separately chargeable work", "Scope", "Separately priced work"),
    ("EXCLUSIONS", "Exclusions", "Scope", "Excluded obligations"),
    ("ASSUMPTIONS", "Assumptions", "Scope", "Bid assumptions"),
    ("QUALIFICATIONS", "Qualifications", "Scope", "Bid qualifications"),
)


class Ops08Repository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._migrate()
        self.bootstrap_topics()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _migrate(self) -> None:
        ddl = (
            "CREATE TABLE IF NOT EXISTS commercial_topics(topic_id TEXT PRIMARY KEY,topic_key TEXT NOT NULL UNIQUE,current_version INTEGER NOT NULL,active INTEGER NOT NULL,created_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS commercial_topic_versions(topic_version_id TEXT PRIMARY KEY,topic_id TEXT NOT NULL,version_number INTEGER NOT NULL,label TEXT NOT NULL,description TEXT NOT NULL,display_group TEXT NOT NULL,display_order INTEGER NOT NULL,governance_json TEXT NOT NULL,provenance_json TEXT NOT NULL,created_at TEXT NOT NULL,created_by TEXT NOT NULL,FOREIGN KEY(topic_id) REFERENCES commercial_topics(topic_id),UNIQUE(topic_id,version_number))",
            "CREATE TABLE IF NOT EXISTS commercial_positions(position_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,topic_id TEXT NOT NULL,current_version INTEGER NOT NULL,created_at TEXT NOT NULL,created_by TEXT NOT NULL,FOREIGN KEY(bid_id) REFERENCES bids(bid_id),FOREIGN KEY(topic_id) REFERENCES commercial_topics(topic_id),UNIQUE(bid_id,topic_id))",
            "CREATE TABLE IF NOT EXISTS commercial_position_versions(position_version_id TEXT PRIMARY KEY,position_id TEXT NOT NULL,bid_id TEXT NOT NULL,topic_version_id TEXT NOT NULL,version_number INTEGER NOT NULL,customer_position TEXT NOT NULL,proposed_position TEXT NOT NULL,disposition TEXT NOT NULL,source_document_version_id TEXT,clause TEXT,section TEXT,page TEXT,source_locator TEXT,interpretation TEXT,rationale TEXT,financial_impact TEXT,schedule_impact TEXT,operational_impact TEXT,owner TEXT,contributor TEXT,reviewer TEXT,required_approver TEXT,due_date TEXT,negotiation_state TEXT NOT NULL,supporting_evidence TEXT,current_outcome TEXT,provenance_json TEXT NOT NULL,created_at TEXT NOT NULL,created_by TEXT NOT NULL,FOREIGN KEY(position_id) REFERENCES commercial_positions(position_id),FOREIGN KEY(bid_id) REFERENCES bids(bid_id),FOREIGN KEY(topic_version_id) REFERENCES commercial_topic_versions(topic_version_id),FOREIGN KEY(source_document_version_id) REFERENCES document_versions(document_version_id),UNIQUE(position_id,version_number))",
            "CREATE TABLE IF NOT EXISTS ops08_schema_migrations(migration_id TEXT PRIMARY KEY,applied_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS commercial_position_work_links(link_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,position_id TEXT NOT NULL,position_version_id TEXT NOT NULL,work_item_id TEXT NOT NULL,purpose TEXT NOT NULL,created_at TEXT NOT NULL,created_by TEXT NOT NULL,FOREIGN KEY(bid_id) REFERENCES bids(bid_id),FOREIGN KEY(position_id) REFERENCES commercial_positions(position_id),FOREIGN KEY(position_version_id) REFERENCES commercial_position_versions(position_version_id),FOREIGN KEY(work_item_id) REFERENCES work_items(work_item_id),UNIQUE(position_id,purpose))",
            "CREATE TABLE IF NOT EXISTS commercial_position_relationships(link_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,position_id TEXT NOT NULL,position_version_id TEXT NOT NULL,relation TEXT NOT NULL,requirement_id TEXT,scope_item_id TEXT,interface_id TEXT,risk_issue_id TEXT,negotiation_plan_id TEXT,decision_case_id TEXT,approval_route_id TEXT,manufacturer_evidence_id TEXT,created_at TEXT NOT NULL,created_by TEXT NOT NULL,FOREIGN KEY(bid_id) REFERENCES bids(bid_id),FOREIGN KEY(position_id) REFERENCES commercial_positions(position_id),FOREIGN KEY(position_version_id) REFERENCES commercial_position_versions(position_version_id),FOREIGN KEY(requirement_id) REFERENCES requirements(requirement_id),FOREIGN KEY(scope_item_id) REFERENCES scope_interface_items(scope_item_id),FOREIGN KEY(interface_id) REFERENCES scope_interfaces(interface_id),FOREIGN KEY(risk_issue_id) REFERENCES contract_issues(issue_id),FOREIGN KEY(negotiation_plan_id) REFERENCES negotiation_plans(plan_id),FOREIGN KEY(decision_case_id) REFERENCES decision_cases(case_id),FOREIGN KEY(approval_route_id) REFERENCES approval_routes(route_id),FOREIGN KEY(manufacturer_evidence_id) REFERENCES vendor_bid_requirements(requirement_id),CHECK((requirement_id IS NOT NULL)+(scope_item_id IS NOT NULL)+(interface_id IS NOT NULL)+(risk_issue_id IS NOT NULL)+(negotiation_plan_id IS NOT NULL)+(decision_case_id IS NOT NULL)+(approval_route_id IS NOT NULL)+(manufacturer_evidence_id IS NOT NULL)=1),UNIQUE(position_id,relation,requirement_id,scope_item_id,interface_id,risk_issue_id,negotiation_plan_id,decision_case_id,approval_route_id,manufacturer_evidence_id))",
            "CREATE INDEX IF NOT EXISTS idx_ops08_positions_bid ON commercial_positions(bid_id)",
            "CREATE INDEX IF NOT EXISTS idx_ops08_versions_bid_disposition ON commercial_position_versions(bid_id,disposition)",
            "CREATE TRIGGER IF NOT EXISTS ops08_topic_no_delete BEFORE DELETE ON commercial_topics WHEN EXISTS(SELECT 1 FROM commercial_positions WHERE topic_id=OLD.topic_id) BEGIN SELECT RAISE(ABORT,'referenced commercial topics cannot be deleted'); END",
            "CREATE TRIGGER IF NOT EXISTS ops08_topic_version_immutable BEFORE UPDATE ON commercial_topic_versions BEGIN SELECT RAISE(ABORT,'commercial topic versions are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS ops08_topic_version_no_delete BEFORE DELETE ON commercial_topic_versions BEGIN SELECT RAISE(ABORT,'commercial topic versions cannot be deleted'); END",
            "CREATE TRIGGER IF NOT EXISTS ops08_position_version_immutable BEFORE UPDATE ON commercial_position_versions BEGIN SELECT RAISE(ABORT,'commercial position versions are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS ops08_position_version_no_delete BEFORE DELETE ON commercial_position_versions BEGIN SELECT RAISE(ABORT,'commercial position versions cannot be deleted'); END",
        )
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in ddl:
                    conn.execute(statement)
                conn.execute(
                    "INSERT OR IGNORE INTO ops08_schema_migrations VALUES(?,?)",
                    (MIGRATION_ID, datetime.now(UTC).isoformat()),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def bootstrap_topics(self) -> None:
        now = datetime.now(UTC).isoformat()
        provenance = Provenance.from_human("system-bootstrap").model_dump_json()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for order, (key, label, group, description) in enumerate(STANDARD_TOPICS, 1):
                topic_id = f"CT-{key}"
                version_id = f"CTV-{key}-1"
                conn.execute(
                    "INSERT OR IGNORE INTO commercial_topics VALUES(?,?,1,1,?)",
                    (topic_id, key, now),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO commercial_topic_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        version_id,
                        topic_id,
                        1,
                        label,
                        description,
                        group,
                        order,
                        "[]",
                        provenance,
                        now,
                        "system-bootstrap",
                    ),
                )
            conn.commit()

    def retire_topic(self, topic_key: str) -> None:
        """Deactivate future initialization without changing historical meaning."""
        with self._conn() as conn:
            changed = conn.execute(
                "UPDATE commercial_topics SET active=0 WHERE topic_key=? AND active=1",
                (topic_key,),
            ).rowcount
            if changed != 1:
                raise ValueError("active commercial topic not found")
            conn.commit()

    def revise_topic(
        self,
        topic_key: str,
        expected_version: int,
        *,
        label: str,
        description: str,
        display_group: str,
        display_order: int,
        actor: str,
    ) -> str:
        """Append a configuration version; existing positions retain their snapshot."""
        fields = [label.strip(), description.strip(), display_group.strip()]
        if not all(fields) or display_order < 1:
            raise ValueError("topic label, description, group and positive order are required")
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                topic = conn.execute(
                    "SELECT * FROM commercial_topics WHERE topic_key=?", (topic_key,)
                ).fetchone()
                if topic is None or int(topic["current_version"]) != expected_version:
                    raise ValueError("stale or missing commercial topic")
                next_version = expected_version + 1
                version_id = f"CTV-{uuid4().hex}"
                now = datetime.now(UTC)
                conn.execute(
                    """INSERT INTO commercial_topic_versions VALUES
                    (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        version_id,
                        topic["topic_id"],
                        next_version,
                        fields[0],
                        fields[1],
                        fields[2],
                        display_order,
                        "[]",
                        Provenance.from_human(actor).model_dump_json(),
                        now.isoformat(),
                        actor,
                    ),
                )
                changed = conn.execute(
                    "UPDATE commercial_topics SET current_version=? WHERE topic_id=? AND current_version=?",
                    (next_version, topic["topic_id"], expected_version),
                ).rowcount
                if changed != 1:
                    raise ValueError("stale commercial topic")
                conn.commit()
                return version_id
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _audit(conn: sqlite3.Connection, bid_id: str, actor: str, action: str, detail: str) -> None:
        conn.execute(
            "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) VALUES(?,?,?,?,?,?)",
            (f"AUD-{uuid4().hex}", bid_id, actor, action, detail, datetime.now(UTC).isoformat()),
        )

    def initialize(self, bid_id: str, owner: str, actor: str) -> int:
        now = datetime.now(UTC)
        created = 0
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if conn.execute("SELECT 1 FROM bids WHERE bid_id=?", (bid_id,)).fetchone() is None:
                    raise ValueError("Bid not found")
                topics = conn.execute(
                    "SELECT * FROM commercial_topics WHERE active=1 ORDER BY topic_key"
                ).fetchall()
                for topic in topics:
                    if conn.execute(
                        "SELECT 1 FROM commercial_positions WHERE bid_id=? AND topic_id=?",
                        (bid_id, topic["topic_id"]),
                    ).fetchone():
                        continue
                    position_id = f"CP-{uuid4().hex}"
                    conn.execute(
                        "INSERT INTO commercial_positions VALUES(?,?,?,1,?,?)",
                        (position_id, bid_id, topic["topic_id"], now.isoformat(), actor),
                    )
                    revision = PositionRevision(owner=owner)
                    self._insert_revision(
                        conn, position_id, bid_id, str(topic["topic_id"]), 1, revision, actor, now
                    )
                    self._audit(conn, bid_id, actor, "commercial_position_initialized", position_id)
                    created += 1
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return created

    def _insert_revision(
        self,
        conn: sqlite3.Connection,
        position_id: str,
        bid_id: str,
        topic_id: str,
        version: int,
        value: PositionRevision,
        actor: str,
        now: datetime,
    ) -> str:
        topic_version_id = str(
            conn.execute(
                "SELECT topic_version_id FROM commercial_topic_versions WHERE topic_id=? ORDER BY version_number DESC LIMIT 1",
                (topic_id,),
            ).fetchone()[0]
        )
        version_id = f"CPV-{uuid4().hex}"
        payload = value.model_dump(mode="json")
        conn.execute(
            "INSERT INTO commercial_position_versions VALUES("
            + ",".join("?" for _ in range(29))
            + ")",
            (
                version_id,
                position_id,
                bid_id,
                topic_version_id,
                version,
                payload["customer_position"],
                payload["proposed_position"],
                payload["disposition"],
                payload["source_document_version_id"],
                payload["clause"],
                payload["section"],
                payload["page"],
                payload["source_locator"],
                payload["interpretation"],
                payload["rationale"],
                payload["financial_impact"],
                payload["schedule_impact"],
                payload["operational_impact"],
                payload["owner"],
                payload["contributor"],
                payload["reviewer"],
                payload["required_approver"],
                payload["due_date"],
                payload["negotiation_state"],
                payload["supporting_evidence"],
                payload["current_outcome"],
                Provenance.from_human(actor).model_dump_json(),
                now.isoformat(),
                actor,
            ),
        )
        return version_id

    def revise(
        self, position_id: str, expected_version: int, value: PositionRevision, actor: str
    ) -> str:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM commercial_positions WHERE position_id=?", (position_id,)
                ).fetchone()
                if row is None:
                    raise ValueError("commercial position not found")
                if int(row["current_version"]) != expected_version:
                    raise ValueError("stale commercial position version")
                next_version = expected_version + 1
                version_id = self._insert_revision(
                    conn,
                    position_id,
                    str(row["bid_id"]),
                    str(row["topic_id"]),
                    next_version,
                    value,
                    actor,
                    datetime.now(UTC),
                )
                changed = conn.execute(
                    "UPDATE commercial_positions SET current_version=? WHERE position_id=? AND current_version=?",
                    (next_version, position_id, expected_version),
                ).rowcount
                if changed != 1:
                    raise ValueError("stale commercial position version")
                self._audit(
                    conn,
                    str(row["bid_id"]),
                    actor,
                    "commercial_position_revised",
                    json.dumps({"position_id": position_id, "version": next_version}),
                )
                conn.commit()
                return version_id
            except Exception:
                conn.rollback()
                raise

    def workspace(self, bid_id: str) -> list[dict[str, Any]]:
        """Return the deterministic current commercial matrix."""
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """SELECT p.position_id,p.current_version,t.topic_key,tv.label,tv.description,tv.display_group,tv.display_order,v.* FROM commercial_positions p JOIN commercial_topics t ON t.topic_id=p.topic_id JOIN commercial_position_versions v ON v.position_id=p.position_id AND v.version_number=p.current_version JOIN commercial_topic_versions tv ON tv.topic_version_id=v.topic_version_id WHERE p.bid_id=? ORDER BY tv.display_group,tv.display_order,p.position_id""",
                    (bid_id,),
                ).fetchall()
            ]

    def bulk_assign_owner(
        self, bid_id: str, targets: list[tuple[str, int]], owner: str, actor: str
    ) -> int:
        if not owner.strip() or not targets:
            raise ValueError("select positions and provide an accountable owner")
        if len({target[0] for target in targets}) != len(targets):
            raise ValueError("duplicate commercial position target")
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                resolved: list[tuple[sqlite3.Row, PositionRevision]] = []
                for position_id, expected in targets:
                    row = conn.execute(
                        """SELECT p.*,v.* FROM commercial_positions p
                        JOIN commercial_position_versions v ON v.position_id=p.position_id
                        AND v.version_number=p.current_version WHERE p.position_id=?""",
                        (position_id,),
                    ).fetchone()
                    if row is None or row["bid_id"] != bid_id or row["current_version"] != expected:
                        raise ValueError("stale or cross-Bid commercial position target")
                    values = {key: row[key] for key in PositionRevision.model_fields}
                    values["owner"] = owner.strip()
                    resolved.append((row, PositionRevision.model_validate(values)))
                for row, revision in resolved:
                    next_version = int(row["current_version"]) + 1
                    self._insert_revision(
                        conn,
                        str(row["position_id"]),
                        bid_id,
                        str(row["topic_id"]),
                        next_version,
                        revision,
                        actor,
                        datetime.now(UTC),
                    )
                    conn.execute(
                        "UPDATE commercial_positions SET current_version=? WHERE position_id=?",
                        (next_version, row["position_id"]),
                    )
                    self._audit(
                        conn,
                        bid_id,
                        actor,
                        "commercial_position_bulk_assigned",
                        str(row["position_id"]),
                    )
                conn.commit()
                return len(resolved)
            except Exception:
                conn.rollback()
                raise

    def detail(self, position_id: str) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT p.current_version,t.topic_key,tv.label,tv.description,v.* FROM commercial_positions p JOIN commercial_topics t ON t.topic_id=p.topic_id JOIN commercial_position_versions v ON v.position_id=p.position_id AND v.version_number=p.current_version JOIN commercial_topic_versions tv ON tv.topic_version_id=v.topic_version_id WHERE p.position_id=?""",
                (position_id,),
            ).fetchone()
            if row is None:
                raise ValueError("commercial position not found")
            result = dict(row)
            result["history"] = [
                dict(v)
                for v in conn.execute(
                    "SELECT * FROM commercial_position_versions WHERE position_id=? ORDER BY version_number",
                    (position_id,),
                ).fetchall()
            ]
            result["relationships"] = [
                dict(value)
                for value in conn.execute(
                    "SELECT * FROM commercial_position_relationships WHERE position_id=? ORDER BY created_at,link_id",
                    (position_id,),
                ).fetchall()
            ]
            return result

    def link(
        self, position_id: str, target_kind: str, target_id: str, relation: str, actor: str
    ) -> str:
        targets = {
            "requirement": ("requirements", "requirement_id", "requirement_id"),
            "scope": ("scope_interface_items", "scope_item_id", "scope_item_id"),
            "interface": ("scope_interfaces", "interface_id", "interface_id"),
            "risk": ("contract_issues", "issue_id", "risk_issue_id"),
            "negotiation": ("negotiation_plans", "plan_id", "negotiation_plan_id"),
            "decision": ("decision_cases", "case_id", "decision_case_id"),
            "approval": ("approval_routes", "route_id", "approval_route_id"),
            "manufacturer": (
                "vendor_bid_requirements",
                "requirement_id",
                "manufacturer_evidence_id",
            ),
        }
        if target_kind not in targets or not relation.strip():
            raise ValueError("unsupported commercial relationship")
        table, key, column = targets[target_kind]
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                position = conn.execute(
                    "SELECT * FROM commercial_positions WHERE position_id=?", (position_id,)
                ).fetchone()
                target = conn.execute(
                    f"SELECT bid_id FROM {table} WHERE {key}=?", (target_id,)
                ).fetchone()
                if position is None or target is None or target["bid_id"] != position["bid_id"]:
                    raise ValueError("commercial relationship is missing or cross-Bid")
                version_id = conn.execute(
                    "SELECT position_version_id FROM commercial_position_versions WHERE position_id=? AND version_number=?",
                    (position_id, position["current_version"]),
                ).fetchone()[0]
                values: dict[str, str | None] = {
                    name: None
                    for name in (
                        "requirement_id",
                        "scope_item_id",
                        "interface_id",
                        "risk_issue_id",
                        "negotiation_plan_id",
                        "decision_case_id",
                        "approval_route_id",
                        "manufacturer_evidence_id",
                    )
                }
                values[column] = target_id
                duplicate = conn.execute(
                    f"""SELECT 1 FROM commercial_position_relationships
                    WHERE position_id=? AND relation=? AND {column}=?""",
                    (position_id, relation.strip(), target_id),
                ).fetchone()
                if duplicate is not None:
                    raise ValueError("duplicate commercial relationship")
                link_id = f"CPL-{uuid4().hex}"
                conn.execute(
                    "INSERT INTO commercial_position_relationships VALUES("
                    + ",".join("?" for _ in range(15))
                    + ")",
                    (
                        link_id,
                        position["bid_id"],
                        position_id,
                        version_id,
                        relation.strip(),
                        *values.values(),
                        datetime.now(UTC).isoformat(),
                        actor,
                    ),
                )
                self._audit(
                    conn, str(position["bid_id"]), actor, "commercial_position_linked", link_id
                )
                conn.commit()
                return link_id
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise ValueError("duplicate commercial relationship") from exc
            except Exception:
                conn.rollback()
                raise

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
        """Create controlled My Work linked to the current position revision."""
        if source_kind != "commercial":
            raise ValueError("unsupported My Work source kind")
        request = WorkItemCreate.model_validate(
            {"title": title, "category": category, "next_action_date": next_action_date}
        )
        normalized_purpose = purpose.strip()
        if not normalized_purpose:
            raise ValueError("Purpose must be non-empty")
        now = datetime.now(UTC)
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                position = conn.execute(
                    "SELECT * FROM commercial_positions WHERE position_id=?", (source_id,)
                ).fetchone()
                if position is None:
                    raise ValueError("Authoritative source was not found")
                version_id = conn.execute(
                    "SELECT position_version_id FROM commercial_position_versions WHERE position_id=? AND version_number=?",
                    (source_id, position["current_version"]),
                ).fetchone()[0]
                item = WorkItem(
                    work_item_id=f"WI-{uuid4()}",
                    bid_id=str(position["bid_id"]),
                    kind=WorkItemKind.TASK,
                    title=request.title,
                    details=request.details,
                    status=WorkItemStatus.OPEN,
                    priority=WorkItemPriority.NORMAL,
                    due_date=None,
                    waiting_on=None,
                    blocker_note=None,
                    category=request.category,
                    responsibility_domain=ResponsibilityDomain.QUOTATION_COMMERCIAL,
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
                conn.execute(
                    """INSERT INTO work_items(work_item_id,bid_id,kind,title,details,status,priority,due_date,
                    waiting_on,blocker_note,created_at,updated_at,completed_at,version,provenance_json,category,
                    responsibility_domain,next_action_date,requester_label,waiting_party_kind,waiting_party_label,
                    waiting_owed,requested_date,chase_date,blocker_description,resolution_owner,review_date,
                    completion_outcome,cancellation_reason,completion_evidence,contribution_candidate)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
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
                        ResponsibilityDomain.QUOTATION_COMMERCIAL.value,
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
                    ),
                )
                conn.execute(
                    """INSERT INTO commercial_position_work_links
                    (link_id,bid_id,position_id,position_version_id,work_item_id,purpose,created_at,created_by)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        f"CPW-{uuid4().hex}",
                        item.bid_id,
                        source_id,
                        version_id,
                        item.work_item_id,
                        normalized_purpose,
                        now.isoformat(),
                        actor,
                    ),
                )
                self._audit(
                    conn,
                    str(item.bid_id),
                    actor,
                    "work_item_created",
                    json.dumps({"work_item_id": item.work_item_id, "position_id": source_id}),
                )
                self._audit(
                    conn,
                    str(item.bid_id),
                    actor,
                    "commercial_position_work_linked",
                    item.work_item_id,
                )
                conn.commit()
                return item
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise ValueError("An action is already linked for this source and purpose") from exc
            except Exception:
                conn.rollback()
                raise

    def readiness(self, bid_id: str) -> dict[str, Any]:
        rows = self.workspace(bid_id)
        blockers: list[str] = []
        for row in rows:
            if row["disposition"] == PositionDisposition.NOT_REVIEWED:
                blockers.append(f"{row['label']}: not reviewed")
            if row["disposition"] == PositionDisposition.CLARIFICATION_REQUIRED:
                blockers.append(f"{row['label']}: clarification unresolved")
            if row["required_approver"] and not row["current_outcome"]:
                blockers.append(f"{row['label']}: required approval missing")
            if row["negotiation_state"] in {
                NegotiationState.NOT_STARTED,
                NegotiationState.IN_PROGRESS,
            } and row["disposition"] in {PositionDisposition.QUALIFY, PositionDisposition.REJECT}:
                blockers.append(f"{row['label']}: negotiation unresolved")
        return {
            "state": "NOT_STARTED" if not rows else "BLOCKED" if blockers else "COMPLETE",
            "blockers": blockers,
            "total": len(rows),
            "reviewed": sum(row["disposition"] != PositionDisposition.NOT_REVIEWED for row in rows),
        }

    def qualifications_csv(self, bid_id: str) -> str:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            (
                "Topic",
                "Customer wording",
                "Source version",
                "Locator",
                "Our proposed wording",
                "Reason",
                "Financial impact",
                "Owner",
                "Approval",
                "Negotiation",
                "Outcome",
            )
        )
        for row in self.workspace(bid_id):
            if row["disposition"] not in {PositionDisposition.QUALIFY, PositionDisposition.REJECT}:
                continue
            writer.writerow(
                tuple(
                    csv_safe_cell(str(value or ""))
                    for value in (
                        row["label"],
                        row["customer_position"],
                        row["source_document_version_id"],
                        row["source_locator"],
                        row["proposed_position"],
                        row["rationale"],
                        row["financial_impact"],
                        row["owner"],
                        row["required_approver"],
                        row["negotiation_state"],
                        row["current_outcome"],
                    )
                )
            )
        return output.getvalue()

    def proposal_input_csv(self, bid_id: str) -> str:
        """Return deterministic OPS-09 input without generating proposal prose."""
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            (
                "Topic",
                "Disposition",
                "Customer wording",
                "Our proposed Bid position",
                "Source version",
                "Locator",
                "Owner",
                "Approval requirement",
                "Negotiation state",
                "Unresolved",
            )
        )
        for row in self.workspace(bid_id):
            unresolved = row["disposition"] == PositionDisposition.NOT_REVIEWED or (
                row["disposition"] in {PositionDisposition.QUALIFY, PositionDisposition.REJECT}
                and row["negotiation_state"] != NegotiationState.RESOLVED
            )
            writer.writerow(
                tuple(
                    csv_safe_cell(str(value or ""))
                    for value in (
                        row["label"],
                        row["disposition"],
                        row["customer_position"],
                        row["proposed_position"],
                        row["source_document_version_id"],
                        row["source_locator"],
                        row["owner"],
                        row["required_approver"],
                        row["negotiation_state"],
                        "YES" if unresolved else "NO",
                    )
                )
            )
        return output.getvalue()

    def unresolved_actions_csv(self, bid_id: str) -> str:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(("Topic", "Owner", "Due date", "Next action", "Disposition"))
        blockers = set(self.readiness(bid_id)["blockers"])
        for row in self.workspace(bid_id):
            if not any(str(blocker).startswith(f"{row['label']}:") for blocker in blockers):
                continue
            writer.writerow(
                tuple(
                    csv_safe_cell(str(value or ""))
                    for value in (
                        row["label"],
                        row["owner"],
                        row["due_date"],
                        row["current_outcome"],
                        row["disposition"],
                    )
                )
            )
        return output.getvalue()
