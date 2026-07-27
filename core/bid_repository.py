"""Persistence for ContractIQ's bid-centric deterministic spine."""

import json
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast

from core.database import Database
from core.enums import (
    ApprovalType,
    BidLevel,
    BidStatus,
    CustomerType,
    Gate,
    GateStatus,
    InferencePolicy,
    RiskTrigger,
)
from core.schemas import Approval, AuditEntry, Bid, GateRecord, Provenance


class BidRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._evolve_bid_schema()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _evolve_bid_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS bids (
                    bid_id TEXT PRIMARY KEY,
                    customer TEXT NOT NULL,
                    customer_type TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    location TEXT,
                    sales_owner TEXT NOT NULL,
                    bc_owner TEXT NOT NULL,
                    executive_sponsor TEXT,
                    release_date TEXT NOT NULL,
                    customer_due_date TEXT NOT NULL,
                    internal_due_date TEXT NOT NULL,
                    anticipated_award_date TEXT,
                    estimated_value TEXT NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'CAD',
                    margin_range TEXT,
                    win_probability INTEGER,
                    classification TEXT NOT NULL,
                    current_gate TEXT NOT NULL DEFAULT 'g0',
                    status TEXT NOT NULL DEFAULT 'active',
                    risk_triggers TEXT NOT NULL DEFAULT '[]',
                    inference_policy TEXT NOT NULL DEFAULT 'local_only',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    bid_id TEXT NOT NULL,
                    approval_type TEXT NOT NULL,
                    required INTEGER NOT NULL DEFAULT 1,
                    obtained INTEGER NOT NULL DEFAULT 0,
                    authority TEXT,
                    evidence_ref TEXT,
                    decision TEXT,
                    decided_at TEXT,
                    provenance_json TEXT NOT NULL,
                    FOREIGN KEY (bid_id) REFERENCES bids(bid_id)
                );

                CREATE TABLE IF NOT EXISTS gate_records (
                    bid_id TEXT NOT NULL,
                    gate TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'not_started',
                    blockers TEXT NOT NULL DEFAULT '[]',
                    override_by TEXT,
                    override_risk_note TEXT,
                    decided_at TEXT,
                    PRIMARY KEY (bid_id, gate),
                    FOREIGN KEY (bid_id) REFERENCES bids(bid_id)
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    entry_id TEXT PRIMARY KEY,
                    bid_id TEXT,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_approvals_bid ON approvals(bid_id);
                CREATE INDEX IF NOT EXISTS idx_gate_records_bid ON gate_records(bid_id);
                CREATE INDEX IF NOT EXISTS idx_audit_bid ON audit_log(bid_id);
                """
            )
            document_columns = {
                str(row["name"]) for row in conn.execute("PRAGMA table_info(documents)").fetchall()
            }
            if "bid_id" not in document_columns:
                conn.execute("ALTER TABLE documents ADD COLUMN bid_id TEXT REFERENCES bids(bid_id)")
            conn.commit()

    @staticmethod
    def _optional_str(value: object) -> str | None:
        return None if value is None else str(value)

    @staticmethod
    def _bid_values(bid: Bid) -> tuple[object, ...]:
        return (
            bid.bid_id,
            bid.customer,
            bid.customer_type.value,
            bid.project_name,
            bid.location,
            bid.sales_owner,
            bid.bc_owner,
            bid.executive_sponsor,
            bid.release_date.isoformat(),
            bid.customer_due_date.isoformat(),
            bid.internal_due_date.isoformat(),
            bid.anticipated_award_date.isoformat() if bid.anticipated_award_date else None,
            str(bid.estimated_value),
            bid.currency,
            bid.margin_range,
            bid.win_probability,
            bid.classification.value,
            bid.current_gate.value,
            bid.status.value,
            json.dumps([trigger.value for trigger in bid.risk_triggers]),
            bid.inference_policy.value,
            bid.created_at.isoformat(),
            bid.updated_at.isoformat(),
        )

    @classmethod
    def _bid_from_row(cls, row: sqlite3.Row) -> Bid:
        risk_trigger_values = cast(list[str], json.loads(str(row["risk_triggers"])))
        anticipated_award = cls._optional_str(row["anticipated_award_date"])
        win_probability = row["win_probability"]
        return Bid(
            bid_id=str(row["bid_id"]),
            customer=str(row["customer"]),
            customer_type=CustomerType(str(row["customer_type"])),
            project_name=str(row["project_name"]),
            location=cls._optional_str(row["location"]),
            sales_owner=str(row["sales_owner"]),
            bc_owner=str(row["bc_owner"]),
            executive_sponsor=cls._optional_str(row["executive_sponsor"]),
            release_date=date.fromisoformat(str(row["release_date"])),
            customer_due_date=date.fromisoformat(str(row["customer_due_date"])),
            internal_due_date=date.fromisoformat(str(row["internal_due_date"])),
            anticipated_award_date=(
                date.fromisoformat(anticipated_award) if anticipated_award is not None else None
            ),
            estimated_value=Decimal(str(row["estimated_value"])),
            currency=str(row["currency"]),
            margin_range=cls._optional_str(row["margin_range"]),
            win_probability=int(win_probability) if win_probability is not None else None,
            classification=BidLevel(str(row["classification"])),
            current_gate=Gate(str(row["current_gate"])),
            status=BidStatus(str(row["status"])),
            risk_triggers=[RiskTrigger(value) for value in risk_trigger_values],
            inference_policy=InferencePolicy(str(row["inference_policy"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    def create_bid(self, bid: Bid) -> None:
        if self.bid_exists(bid.bid_id):
            raise ValueError(f"Bid already exists: {bid.bid_id}")
        try:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO bids (
                        bid_id, customer, customer_type, project_name, location,
                        sales_owner, bc_owner, executive_sponsor, release_date,
                        customer_due_date, internal_due_date, anticipated_award_date,
                        estimated_value, currency, margin_range, win_probability,
                        classification, current_gate, status, risk_triggers,
                        inference_policy, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    self._bid_values(bid),
                )
                conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Bid already exists: {bid.bid_id}") from exc

    def get_bid(self, bid_id: str) -> Bid | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM bids WHERE bid_id = ?", (bid_id,)).fetchone()
        return self._bid_from_row(row) if row is not None else None

    def list_bids(self, status: BidStatus | None = None) -> list[Bid]:
        with self._conn() as conn:
            if status is None:
                rows = conn.execute(
                    "SELECT * FROM bids ORDER BY created_at DESC, bid_id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM bids WHERE status = ? ORDER BY created_at DESC, bid_id",
                    (status.value,),
                ).fetchall()
        return [self._bid_from_row(row) for row in rows]

    def update_bid(self, bid: Bid) -> None:
        updated_bid = bid.model_copy(update={"updated_at": datetime.now(UTC)})
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO bids (
                    bid_id, customer, customer_type, project_name, location,
                    sales_owner, bc_owner, executive_sponsor, release_date,
                    customer_due_date, internal_due_date, anticipated_award_date,
                    estimated_value, currency, margin_range, win_probability,
                    classification, current_gate, status, risk_triggers,
                    inference_policy, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(bid_id) DO UPDATE SET
                    customer = excluded.customer,
                    customer_type = excluded.customer_type,
                    project_name = excluded.project_name,
                    location = excluded.location,
                    sales_owner = excluded.sales_owner,
                    bc_owner = excluded.bc_owner,
                    executive_sponsor = excluded.executive_sponsor,
                    release_date = excluded.release_date,
                    customer_due_date = excluded.customer_due_date,
                    internal_due_date = excluded.internal_due_date,
                    anticipated_award_date = excluded.anticipated_award_date,
                    estimated_value = excluded.estimated_value,
                    currency = excluded.currency,
                    margin_range = excluded.margin_range,
                    win_probability = excluded.win_probability,
                    classification = excluded.classification,
                    current_gate = excluded.current_gate,
                    status = excluded.status,
                    risk_triggers = excluded.risk_triggers,
                    inference_policy = excluded.inference_policy,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                """,
                self._bid_values(updated_bid),
            )
            conn.commit()

    def bid_exists(self, bid_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM bids WHERE bid_id = ? LIMIT 1", (bid_id,)).fetchone()
        return row is not None

    def attach_document_to_bid(self, doc_id: str, bid_id: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE documents SET bid_id = ? WHERE id = ?", (bid_id, doc_id))
            conn.commit()

    def detach_document(self, doc_id: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE documents SET bid_id = NULL WHERE id = ?", (doc_id,))
            conn.commit()

    def list_documents_for_bid(self, bid_id: str) -> list[dict[str, object]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM documents WHERE bid_id = ? ORDER BY upload_date DESC, id",
                (bid_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _approval_from_row(row: sqlite3.Row) -> Approval:
        decided_at = BidRepository._optional_str(row["decided_at"])
        return Approval(
            approval_id=str(row["approval_id"]),
            bid_id=str(row["bid_id"]),
            approval_type=ApprovalType(str(row["approval_type"])),
            required=bool(row["required"]),
            obtained=bool(row["obtained"]),
            authority=BidRepository._optional_str(row["authority"]),
            evidence_ref=BidRepository._optional_str(row["evidence_ref"]),
            decision=BidRepository._optional_str(row["decision"]),
            decided_at=datetime.fromisoformat(decided_at) if decided_at is not None else None,
            provenance=Provenance.model_validate_json(str(row["provenance_json"])),
        )

    def create_approval(self, approval: Approval) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO approvals (
                    approval_id, bid_id, approval_type, required, obtained,
                    authority, evidence_ref, decision, decided_at, provenance_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    approval.approval_id,
                    approval.bid_id,
                    approval.approval_type.value,
                    int(approval.required),
                    int(approval.obtained),
                    approval.authority,
                    approval.evidence_ref,
                    approval.decision,
                    approval.decided_at.isoformat() if approval.decided_at else None,
                    approval.provenance.model_dump_json(),
                ),
            )
            conn.commit()

    def list_approvals(self, bid_id: str) -> list[Approval]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM approvals WHERE bid_id = ? ORDER BY approval_id",
                (bid_id,),
            ).fetchall()
        return [self._approval_from_row(row) for row in rows]

    def update_approval(self, approval: Approval) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE approvals SET
                    bid_id = ?, approval_type = ?, required = ?, obtained = ?,
                    authority = ?, evidence_ref = ?, decision = ?, decided_at = ?,
                    provenance_json = ?
                WHERE approval_id = ?
                """,
                (
                    approval.bid_id,
                    approval.approval_type.value,
                    int(approval.required),
                    int(approval.obtained),
                    approval.authority,
                    approval.evidence_ref,
                    approval.decision,
                    approval.decided_at.isoformat() if approval.decided_at else None,
                    approval.provenance.model_dump_json(),
                    approval.approval_id,
                ),
            )
            conn.commit()

    @staticmethod
    def _gate_record_from_row(row: sqlite3.Row) -> GateRecord:
        blocker_values = cast(list[str], json.loads(str(row["blockers"])))
        decided_at = BidRepository._optional_str(row["decided_at"])
        return GateRecord(
            bid_id=str(row["bid_id"]),
            gate=Gate(str(row["gate"])),
            status=GateStatus(str(row["status"])),
            blockers=blocker_values,
            override_by=BidRepository._optional_str(row["override_by"]),
            override_risk_note=BidRepository._optional_str(row["override_risk_note"]),
            decided_at=datetime.fromisoformat(decided_at) if decided_at is not None else None,
        )

    def upsert_gate_record(self, record: GateRecord) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO gate_records (
                    bid_id, gate, status, blockers, override_by,
                    override_risk_note, decided_at
                ) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(bid_id, gate) DO UPDATE SET
                    status = excluded.status,
                    blockers = excluded.blockers,
                    override_by = excluded.override_by,
                    override_risk_note = excluded.override_risk_note,
                    decided_at = excluded.decided_at
                """,
                (
                    record.bid_id,
                    record.gate.value,
                    record.status.value,
                    json.dumps(record.blockers),
                    record.override_by,
                    record.override_risk_note,
                    record.decided_at.isoformat() if record.decided_at else None,
                ),
            )
            conn.commit()

    def get_gate_record(self, bid_id: str, gate: Gate) -> GateRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM gate_records WHERE bid_id = ? AND gate = ?",
                (bid_id, gate.value),
            ).fetchone()
        return self._gate_record_from_row(row) if row is not None else None

    def list_gate_records(self, bid_id: str) -> list[GateRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM gate_records WHERE bid_id = ? ORDER BY gate",
                (bid_id,),
            ).fetchall()
        return [self._gate_record_from_row(row) for row in rows]

    @staticmethod
    def _audit_from_row(row: sqlite3.Row) -> AuditEntry:
        return AuditEntry(
            entry_id=str(row["entry_id"]),
            bid_id=BidRepository._optional_str(row["bid_id"]),
            actor=str(row["actor"]),
            action=str(row["action"]),
            detail=str(row["detail"]),
            timestamp=datetime.fromisoformat(str(row["timestamp"])),
        )

    def append_audit(self, entry: AuditEntry) -> None:
        with self._conn() as conn:
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
            conn.commit()

    def list_audit(self, bid_id: str | None = None) -> list[AuditEntry]:
        with self._conn() as conn:
            if bid_id is None:
                rows = conn.execute(
                    "SELECT * FROM audit_log ORDER BY timestamp, entry_id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM audit_log WHERE bid_id = ? ORDER BY timestamp, entry_id",
                    (bid_id,),
                ).fetchall()
        return [self._audit_from_row(row) for row in rows]
