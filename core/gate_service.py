"""Database-facing assembly and persistence for deterministic gate evaluation."""

import json
import sqlite3
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from core.bid_repository import BidRepository
from core.database import Database
from core.enums import GateStatus
from core.gates import ConditionState, GateContext, GateResult, evaluate_all_gates
from core.schemas import AuditEntry, GateRecord


def _conn(db: Database) -> sqlite3.Connection:
    return cast(sqlite3.Connection, db._conn())


def _table_exists(db: Database, table_name: str) -> bool:
    with _conn(db) as conn:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
    return row is not None


def _table_has_bid_row(db: Database, table_name: str, bid_id: str) -> bool:
    if not _table_exists(db, table_name):
        return False
    with _conn(db) as conn:
        columns = {
            str(row["name"])
            for row in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        }
        if "bid_id" not in columns:
            return False
        row = conn.execute(
            f'SELECT 1 FROM "{table_name}" WHERE bid_id = ? LIMIT 1',
            (bid_id,),
        ).fetchone()
    return row is not None


def _supplier_assurance_clear(db: Database, bid_id: str) -> bool:
    """Adapt supplier assurance facts into TASK-06's existing capability seam."""
    if not _table_exists(db, "supplier_response_versions"):
        return False
    with _conn(db) as conn:
        versions = conn.execute(
            "SELECT response_version_id,review_state FROM supplier_response_versions "
            "WHERE bid_id=?",
            (bid_id,),
        ).fetchall()
        if not versions:
            return False
        for version in versions:
            if version["review_state"] != "ACCEPTED":
                return False
            rows = conn.execute(
                "SELECT state FROM supplier_response_coverage WHERE response_version_id=?",
                (version["response_version_id"],),
            ).fetchall()
            if any(row["state"] != "CONFIRMED" for row in rows):
                return False
    return True


def build_gate_context(repo: BidRepository, db: Database, bid_id: str) -> GateContext:
    """Load all currently available register data needed by the pure gate rules."""
    bid = repo.get_bid(bid_id)
    if bid is None:
        raise ValueError(f"Bid not found: {bid_id}")

    documents = repo.list_documents_for_bid(bid_id)
    scope_items: list[dict[str, object]] = []
    high_severity_findings: list[dict[str, object]] = []
    unconfirmed_counts = {
        "clause_findings": 0,
        "scope_items": 0,
        "obligations": 0,
        "negotiation_issues": 0,
    }
    for document in documents:
        doc_id = str(document["id"])
        scope_items.extend(cast(list[dict[str, object]], db.get_scope_items(doc_id)))
        findings = cast(list[dict[str, object]], db.get_clause_findings(doc_id))
        high_severity_findings.extend(
            finding
            for finding in findings
            if bool(finding.get("human_confirmed", False))
            and str(finding.get("severity") or "").strip().casefold() == "high"
        )
        document_counts = db.count_unconfirmed(doc_id)
        for table in unconfirmed_counts:
            unconfirmed_counts[table] += document_counts.get(table, 0)

    return GateContext(
        bid=bid,
        approvals=repo.list_approvals(bid_id),
        scope_items=scope_items,
        high_severity_findings=high_severity_findings,
        unconfirmed_counts=unconfirmed_counts,
        prior_gate_results={},
        has_compliance_matrix=_table_exists(db, "requirements"),
        has_supplier_register=_supplier_assurance_clear(db, bid_id),
        has_concession_log=_table_exists(db, "concession_log"),
        has_reconciliation=_table_exists(db, "reconciliation"),
        has_strategy_record=_table_has_bid_row(db, "bid_strategy", bid_id),
    )


def evaluate_and_store_gates(
    repo: BidRepository,
    db: Database,
    bid_id: str,
    actor: str = "system",
) -> list[GateResult]:
    """Evaluate all gates, persist computed statuses, and append one audit entry."""
    results = evaluate_all_gates(build_gate_context(repo, db, bid_id))
    decided_at = datetime.now(UTC)
    for result in results:
        repo.upsert_gate_record(
            GateRecord(
                bid_id=bid_id,
                gate=result.gate,
                status=GateStatus.PASSED if result.passed else GateStatus.IN_REVIEW,
                blockers=[
                    condition.description
                    for condition in result.conditions
                    if condition.state == ConditionState.UNMET
                ],
                decided_at=decided_at,
            )
        )

    repo.append_audit(
        AuditEntry(
            entry_id=f"AUD-{uuid4()}",
            bid_id=bid_id,
            actor=actor,
            action="gates_evaluated",
            detail=json.dumps(
                {
                    result.gate.value: {
                        "passed": result.passed,
                        "summary": result.summary,
                    }
                    for result in results
                },
                sort_keys=True,
            ),
            timestamp=decided_at,
        )
    )
    return results
