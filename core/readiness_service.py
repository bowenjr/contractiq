"""Database-facing readiness assessment and override protocol."""

import json
import sqlite3
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from core.bid_repository import BidRepository
from core.database import Database
from core.enums import GateStatus
from core.gate_service import build_gate_context
from core.gates import evaluate_all_gates
from core.readiness import ReadinessReport, assess_readiness
from core.schemas import AuditEntry, GateRecord


def _conn(db: Database) -> sqlite3.Connection:
    return cast(sqlite3.Connection, db._conn())


def _audit_detail(condition_id: str, risk_note: str) -> str:
    return json.dumps(
        {"condition_id": condition_id, "risk_note": risk_note},
        sort_keys=True,
    )


def _load_overrides(repo: BidRepository, bid_id: str) -> dict[str, tuple[str, str]]:
    overrides: dict[str, tuple[str, str]] = {}

    for record in repo.list_gate_records(bid_id):
        if (
            record.status == GateStatus.OVERRIDDEN
            and record.override_by is not None
            and record.override_risk_note is not None
        ):
            for condition_id in record.blockers:
                overrides[condition_id] = (record.override_by, record.override_risk_note)

    for entry in repo.list_audit(bid_id):
        if entry.action != "readiness_override":
            continue
        try:
            detail = json.loads(entry.detail)
        except json.JSONDecodeError:
            continue
        if not isinstance(detail, dict):
            continue
        parsed_condition_id = detail.get("condition_id")
        risk_note = detail.get("risk_note")
        if isinstance(parsed_condition_id, str) and isinstance(risk_note, str):
            overrides[parsed_condition_id] = (entry.actor, risk_note)

    return overrides


def evaluate_readiness(
    repo: BidRepository,
    db: Database,
    bid_id: str,
) -> ReadinessReport:
    """Read current bid state and return its deterministic readiness report."""
    gate_results = evaluate_all_gates(build_gate_context(repo, db, bid_id))
    report = assess_readiness(
        gate_results,
        overrides=_load_overrides(repo, bid_id),
        now=datetime.now(UTC),
    )
    return report.model_copy(update={"bid_id": bid_id})


def _write_override(
    db: Database,
    record: GateRecord,
    audit_entry: AuditEntry,
) -> None:
    """Atomically write both required records for one readiness override."""
    with _conn(db) as conn:
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
        conn.execute(
            """
            INSERT INTO audit_log (
                entry_id, bid_id, actor, action, detail, timestamp
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                audit_entry.entry_id,
                audit_entry.bid_id,
                audit_entry.actor,
                audit_entry.action,
                audit_entry.detail,
                audit_entry.timestamp.isoformat(),
            ),
        )
        conn.commit()


def request_override(
    repo: BidRepository,
    db: Database,
    bid_id: str,
    condition_id: str,
    authorized_by: str,
    risk_note: str,
) -> ReadinessReport:
    """Record an authorized per-condition override and reassess readiness."""
    normalized_note = risk_note.strip()
    if not normalized_note:
        raise ValueError("risk_note must be non-empty")
    normalized_authority = authorized_by.strip()
    if not normalized_authority:
        raise ValueError("authorized_by must be non-empty")

    current_report = evaluate_readiness(repo, db, bid_id)
    blocker = next(
        (item for item in current_report.blockers if item.condition_id == condition_id),
        None,
    )
    if blocker is None:
        raise ValueError(f"Condition is not a current material blocker: {condition_id}")

    decided_at = datetime.now(UTC)
    record = GateRecord(
        bid_id=bid_id,
        gate=blocker.gate,
        status=GateStatus.OVERRIDDEN,
        blockers=[condition_id],
        override_by=normalized_authority,
        override_risk_note=normalized_note,
        decided_at=decided_at,
    )
    audit_entry = AuditEntry(
        entry_id=f"AUD-{uuid4()}",
        bid_id=bid_id,
        actor=normalized_authority,
        action="readiness_override",
        detail=_audit_detail(condition_id, normalized_note),
        timestamp=decided_at,
    )
    _write_override(db, record, audit_entry)
    return evaluate_readiness(repo, db, bid_id)
