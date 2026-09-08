"""Database-facing assembly and persistence for deterministic gate evaluation."""

import json
import sqlite3
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from core.bid_repository import BidRepository
from core.database import Database
from core.enums import GateStatus
from core.export_controls import manufacturer_confirmation_clear
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


def _manufacturer_coverage_clear(db: Database, bid_id: str) -> bool | None:
    """Evaluate actual OPS-05B responses; association alone never confirms."""
    if not _table_exists(db, "requirement_manufacturer_links"):
        return None
    with _conn(db) as conn:
        links = conn.execute(
            "SELECT package_id FROM requirement_manufacturer_links WHERE bid_id=?", (bid_id,)
        ).fetchall()
        if not links:
            return None
        for link in links:
            rows = conn.execute(
                "SELECT verification_status,response_source,response_received_date "
                "FROM vendor_bid_requirements WHERE package_id=?",
                (link["package_id"],),
            ).fetchall()
            if not rows or any(
                not manufacturer_confirmation_clear(
                    row["verification_status"],
                    row["response_source"],
                    row["response_received_date"],
                )
                for row in rows
            ):
                return False
    return True


def _commercial_review_state(db: Database, bid_id: str) -> tuple[bool | None, str]:
    """Adapt OPS-08 current immutable positions into the existing G4 gate."""
    if not _table_exists(db, "commercial_positions"):
        return None, ""
    with _conn(db) as conn:
        rows = conn.execute(
            """SELECT v.* FROM commercial_positions p
            JOIN commercial_position_versions v ON v.position_id=p.position_id
              AND v.version_number=p.current_version
            WHERE p.bid_id=?""",
            (bid_id,),
        ).fetchall()
    if not rows:
        return None, ""
    unresolved = [
        row
        for row in rows
        if row["disposition"] == "NOT_REVIEWED"
        or not str(row["owner"] or "").strip()
        or (
            row["disposition"] in {"QUALIFY", "REJECT", "CLARIFICATION_REQUIRED"}
            and row["negotiation_state"] != "RESOLVED"
        )
        or (row["required_approver"] and not str(row["current_outcome"] or "").strip())
    ]
    return not unresolved, f"{len(unresolved)} commercial position(s) remain unresolved."


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

    if _table_exists(db, "scope_interface_items"):
        with _conn(db) as conn:
            authoritative = conn.execute(
                "SELECT * FROM scope_interface_items WHERE bid_id=? AND lifecycle_state='ACTIVE'",
                (bid_id,),
            ).fetchall()
            unresolved_interfaces = conn.execute(
                "SELECT owner FROM scope_interfaces WHERE bid_id=? AND lifecycle_state='ACTIVE' "
                "AND (dependency_state='OPEN' OR work_state<>'COMPLETE' "
                "OR review_state<>'ACCEPTED')",
                (bid_id,),
            ).fetchall()
        scope_items = [
            {
                "human_confirmed": True,
                "included_in_quote": row["offer_position"] in {"INCLUDED", "OPTION"},
                "priced": row["pricing_state"]
                in {"PRICED", "ALLOWANCED", "NO_CHARGE", "NOT_APPLICABLE"},
                "owner": row["owner"],
                "gap_status": "CLOSED"
                if row["work_state"] == "COMPLETE" and row["review_state"] == "ACCEPTED"
                else "OPEN",
            }
            for row in authoritative
        ]
        scope_items.extend(
            {
                "human_confirmed": True,
                "included_in_quote": False,
                "priced": False,
                "owner": row["owner"],
                "gap_status": "OPEN",
            }
            for row in unresolved_interfaces
        )

    manufacturer_clear = _manufacturer_coverage_clear(db, bid_id)
    commercial_clear, commercial_detail = _commercial_review_state(db, bid_id)

    return GateContext(
        bid=bid,
        approvals=repo.list_approvals(bid_id),
        scope_items=scope_items,
        high_severity_findings=high_severity_findings,
        unconfirmed_counts=unconfirmed_counts,
        prior_gate_results={},
        has_compliance_matrix=_table_exists(db, "requirements"),
        has_supplier_register=_supplier_assurance_clear(db, bid_id),
        supplier_coverage_clear=manufacturer_clear,
        has_concession_log=_table_exists(db, "concession_log"),
        has_reconciliation=_table_exists(db, "reconciliation"),
        has_strategy_record=_table_has_bid_row(db, "bid_strategy", bid_id),
        commercial_review_clear=commercial_clear,
        commercial_review_detail=commercial_detail,
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
