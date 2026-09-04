"""Shared, deterministic pre-award handover projection.

This module does not decide a gate.  It reconciles existing authoritative response,
evidence, accountability and disposition facts for consistent handover presentation.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from core.database import Database
from core.export_controls import csv_safe_cell


def _present(value: object | None) -> bool:
    return value is not None and bool(str(value).strip())


def _label(value: object) -> str:
    return str(value).replace("_", " ").strip().title()


@dataclass(frozen=True)
class HandoverAssessment:
    """Separate handover facts and their reconciled result."""

    response_status: str
    evidence_status: str
    accountability_status: str
    commercial_disposition_status: str
    ready: bool
    blocking_reasons: tuple[str, ...]

    @property
    def readiness_label(self) -> str:
        return "Ready" if self.ready else "Not ready"


def assess_manufacturer_handover(
    *,
    required: bool,
    applicable: bool,
    verification_status: object,
    response_source: object | None,
    response_received_date: object | None,
    internal_owner: object | None,
    commercial_impact: object,
    bid_disposition: object,
    disposition_approved: bool,
    unresolved_action: object | None,
) -> HandoverAssessment:
    """Project existing OPS-05B facts without replacing its readiness policy."""
    status = str(verification_status)
    response_status = _label(status)
    if not required or not applicable or status == "NOT_APPLICABLE":
        return HandoverAssessment(
            response_status="Not applicable",
            evidence_status="Not required",
            accountability_status="Not required",
            commercial_disposition_status="Not required",
            ready=True,
            blocking_reasons=(),
        )

    blockers: list[str] = []
    response_received = status in {
        "CONFIRMED_COMPLIANT",
        "CONFIRMED_WITH_EXCEPTION",
        "CANNOT_COMPLY",
    }
    if status == "NOT_REVIEWED":
        blockers.append("Manufacturer response has not been reviewed")
    elif status == "AWAITING_MANUFACTURER":
        blockers.append("Manufacturer response is outstanding")
    elif status == "CLARIFICATION_REQUIRED":
        blockers.append("Manufacturer clarification is required")
    elif status == "CANNOT_COMPLY":
        blockers.append("Manufacturer cannot comply")

    evidence_complete = (
        response_received and _present(response_source) and _present(response_received_date)
    )
    if response_received and not evidence_complete:
        missing = []
        if not _present(response_source):
            missing.append("response source")
        if not _present(response_received_date):
            missing.append("response date")
        blockers.append("Recorded compliant confirmation lacks " + " and ".join(missing))
    elif not response_received:
        evidence_complete = False

    owner_complete = _present(internal_owner)
    if not owner_complete:
        blockers.append("Required internal ownership is missing")

    commercial = str(commercial_impact)
    disposition = str(bid_disposition)
    commercial_complete = commercial != "UNKNOWN"
    if not commercial_complete:
        blockers.append("Commercial impact is unresolved")
    if status in {"CONFIRMED_WITH_EXCEPTION", "CANNOT_COMPLY"} and (
        disposition == "NONE" or not disposition_approved
    ):
        commercial_complete = False
        blockers.append("Commercial exception or Bid disposition is not approved")
    if _present(unresolved_action):
        blockers.append(f"Outstanding action: {str(unresolved_action).strip()}")

    return HandoverAssessment(
        response_status=response_status,
        evidence_status="Complete" if evidence_complete else "Incomplete",
        accountability_status="Complete" if owner_complete else "Missing accountability",
        commercial_disposition_status=(
            "Complete" if commercial_complete else "Commercial exception"
        ),
        ready=not blockers,
        blocking_reasons=tuple(dict.fromkeys(blockers)),
    )


@dataclass(frozen=True)
class BidHandoverReport:
    bid: Mapping[str, object]
    gate_verdict: str
    handover_ready: bool
    blockers: tuple[str, ...]
    generated_at: datetime
    generated_by: str
    sections: Mapping[str, tuple[Mapping[str, object], ...]]


class BidHandoverService:
    """Read-only whole-Bid transfer projection over existing authoritative tables."""

    def __init__(
        self,
        db: Database,
        *,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self._now = now_factory or (lambda: datetime.now(UTC))

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    @staticmethod
    def _rows(
        conn: sqlite3.Connection, query: str, parameters: Sequence[object]
    ) -> tuple[Mapping[str, object], ...]:
        return tuple(dict(row) for row in conn.execute(query, parameters).fetchall())

    def report(
        self,
        bid_id: str,
        *,
        gate_verdict: str,
        gate_blockers: Sequence[str],
        generated_by: str,
    ) -> BidHandoverReport:
        with self._conn() as conn:
            bid_row = conn.execute("SELECT * FROM bids WHERE bid_id=?", (bid_id,)).fetchone()
            if bid_row is None:
                raise ValueError(f"Bid not found: {bid_id}")
            bid = dict(bid_row)
            sections: dict[str, tuple[Mapping[str, object], ...]] = {
                "requirements": self._rows(
                    conn,
                    """SELECT requirement_id,title,statement,response_text,disposition,owner,
                    contributor,reviewer,source_document_id,source_document_version_id,
                    source_clause,source_page_start,source_page_end,source_locator_note,
                    evidence_description,proposal_location,work_state,review_state,lifecycle_state,
                    provenance_json FROM requirements WHERE bid_id=?
                    ORDER BY lower(title),requirement_id""",
                    (bid_id,),
                ),
                "scope": self._rows(
                    conn,
                    """SELECT scope_item_id,title,description,offer_position,pricing_state,
                    responsible_party,owner,assumption_exclusion_note,work_state,review_state,
                    lifecycle_state,provenance_json FROM scope_interface_items WHERE bid_id=?
                    ORDER BY lower(title),scope_item_id""",
                    (bid_id,),
                ),
                "interfaces": self._rows(
                    conn,
                    """SELECT interface_id,title,boundary_description,upstream_party,
                    downstream_party,dependency_description,owner,dependency_state,work_state,
                    review_state,lifecycle_state,provenance_json
                    FROM scope_interfaces WHERE bid_id=?
                    ORDER BY lower(title),interface_id""",
                    (bid_id,),
                ),
                "manufacturer": self._manufacturer_rows(conn, bid_id),
                "commercial": self._rows(
                    conn,
                    """SELECT commercial_item_id,title,description,category,basis_role,
                    materiality,owner,due_date,lifecycle_state,provenance_json
                    FROM commercial_items WHERE bid_id=?
                    ORDER BY lower(title),commercial_item_id""",
                    (bid_id,),
                ),
                "risks": self._rows(
                    conn,
                    """SELECT issue_id,issue_code,title,summary,owner,materiality,due_date,
                    lifecycle_state,provenance_json FROM contract_issues WHERE bid_id=?
                    ORDER BY lower(title),issue_id""",
                    (bid_id,),
                ),
                "approvals": self._rows(
                    conn,
                    """SELECT approval_id,approval_type,required,obtained,authority,evidence_ref,
                    decision,decided_at,provenance_json FROM approvals WHERE bid_id=?
                    ORDER BY approval_type,approval_id""",
                    (bid_id,),
                ),
                "decisions": self._rows(
                    conn,
                    """SELECT case_id,case_code,decision_type,title,owner,lifecycle_state,
                    materiality,due_date,provenance_json FROM decision_cases WHERE bid_id=?
                    ORDER BY lower(title),case_id""",
                    (bid_id,),
                ),
                "proposals": self._rows(
                    conn,
                    """SELECT family_id,code,applicability,title,owner,created_by,created_at
                    FROM proposal_families WHERE bid_id=? ORDER BY lower(title),family_id""",
                    (bid_id,),
                ),
                "deliverables": self._rows(
                    conn,
                    """SELECT deliverable_id,title,description,category,criticality,materiality,
                    lifecycle_phase,direction,workflow_state,owner,supplier_id,recipient,due_basis,
                    fixed_due_date,event_name,offset_days,condition_text,required_format,
                    required_review_role,provenance_json FROM deliverable_items WHERE bid_id=?
                    ORDER BY lower(title),deliverable_id""",
                    (bid_id,),
                ),
                "supplier_commitments": self._rows(
                    conn,
                    """SELECT sc.* FROM supplier_commitments sc JOIN deliverable_items d
                    ON d.deliverable_id=sc.deliverable_id WHERE sc.bid_id=?
                    ORDER BY d.title,sc.commitment_id""",
                    (bid_id,),
                ),
                "work": self._rows(
                    conn,
                    """SELECT work_item_id,title,details,status,priority,due_date,waiting_on,
                    blocker_note,category,responsibility_domain,next_action_date,requester_label,
                    waiting_party_label,resolution_owner,completion_outcome,provenance_json
                    FROM work_items WHERE bid_id=? AND status NOT IN ('COMPLETED','CANCELLED')
                    ORDER BY CASE priority WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1
                    WHEN 'NORMAL' THEN 2 ELSE 3 END,COALESCE(due_date,'9999-12-31'),
                    lower(title),work_item_id""",
                    (bid_id,),
                ),
            }

        blockers = list(gate_blockers)
        for row in sections["manufacturer"]:
            if row["handover_readiness"] == "Not ready":
                blockers.extend(cast(str, row["handover_blocking_reasons"]).split(" | "))
        for row in sections["work"]:
            blockers.append(f"Outstanding action: {row['title']}")
        unique_blockers = tuple(dict.fromkeys(item for item in blockers if item))
        return BidHandoverReport(
            bid=bid,
            gate_verdict=gate_verdict,
            handover_ready=gate_verdict.casefold() == "clear" and not unique_blockers,
            blockers=unique_blockers,
            generated_at=self._now(),
            generated_by=generated_by,
            sections=sections,
        )

    def _manufacturer_rows(
        self, conn: sqlite3.Connection, bid_id: str
    ) -> tuple[Mapping[str, object], ...]:
        raw = self._rows(
            conn,
            """SELECT p.package_id,p.package_code,p.package_name,p.proposed_manufacturer,
            p.internal_owner AS package_owner,p.source_vdrl_reference,p.source_revision,
            v.* FROM vendor_bid_packages p JOIN vendor_bid_requirements v
            ON v.package_id=p.package_id WHERE p.bid_id=?
            ORDER BY p.package_code,v.customer_requirement_code,v.requirement_id""",
            (bid_id,),
        )
        result: list[Mapping[str, object]] = []
        for source in raw:
            assessment = assess_manufacturer_handover(
                required=bool(source["required"]),
                applicable=bool(source["applicable"]),
                verification_status=source["verification_status"],
                response_source=source["response_source"],
                response_received_date=source["response_received_date"],
                internal_owner=source["internal_owner"],
                commercial_impact=source["commercial_impact"],
                bid_disposition=source["bid_disposition"],
                disposition_approved=bool(source["disposition_approved"]),
                unresolved_action=source["unresolved_action"],
            )
            row = dict(source)
            row.update(
                {
                    "manufacturer_response_status": assessment.response_status,
                    "response_evidence_status": assessment.evidence_status,
                    "accountability_status": assessment.accountability_status,
                    "commercial_disposition_status": assessment.commercial_disposition_status,
                    "handover_readiness": assessment.readiness_label,
                    "handover_blocking_reasons": " | ".join(assessment.blocking_reasons),
                }
            )
            result.append(row)
        return tuple(result)

    @staticmethod
    def csv(report: BidHandoverReport) -> str:
        """Return one deterministic, formula-safe, long-form transfer CSV."""
        output = io.StringIO(newline="")
        columns = [
            "bid_id",
            "project_name",
            "customer",
            "generated_at",
            "generated_by",
            "gate_verdict",
            "handover_readiness",
            "handover_blocking_reasons",
            "domain",
            "record_id",
            "title",
            "status",
            "owner",
            "source_version",
            "source_locator",
            "details",
        ]
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for domain, rows in report.sections.items():
            for row in rows:
                record_id = next(
                    (str(value) for key, value in row.items() if key.endswith("_id") and value), ""
                )
                title = (
                    row.get("title")
                    or row.get("deliverable_title")
                    or row.get("package_name")
                    or ""
                )
                status = (
                    row.get("handover_readiness")
                    or row.get("lifecycle_state")
                    or row.get("workflow_state")
                    or row.get("status")
                    or row.get("decision")
                    or ""
                )
                details = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
                values = {
                    "bid_id": report.bid["bid_id"],
                    "project_name": report.bid["project_name"],
                    "customer": report.bid["customer"],
                    "generated_at": report.generated_at.isoformat(),
                    "generated_by": report.generated_by,
                    "gate_verdict": report.gate_verdict,
                    "handover_readiness": "Ready" if report.handover_ready else "Not ready",
                    "handover_blocking_reasons": " | ".join(report.blockers),
                    "domain": domain,
                    "record_id": record_id,
                    "title": title,
                    "status": status,
                    "owner": row.get("owner") or row.get("internal_owner") or "",
                    "source_version": row.get("source_document_version_id")
                    or row.get("source_revision")
                    or "",
                    "source_locator": row.get("source_locator_note")
                    or row.get("source_clause")
                    or row.get("source_row_reference")
                    or "",
                    "details": details,
                }
                writer.writerow({key: csv_safe_cell(value) for key, value in values.items()})
        if not any(report.sections.values()):
            fallback = {
                "bid_id": report.bid["bid_id"],
                "project_name": report.bid["project_name"],
                "customer": report.bid["customer"],
                "generated_at": report.generated_at.isoformat(),
                "generated_by": report.generated_by,
                "gate_verdict": report.gate_verdict,
                "handover_readiness": "Ready" if report.handover_ready else "Not ready",
                "handover_blocking_reasons": " | ".join(report.blockers),
                "domain": "bid",
            }
            writer.writerow({key: csv_safe_cell(value) for key, value in fallback.items()})
        return output.getvalue()


__all__ = [
    "BidHandoverReport",
    "BidHandoverService",
    "HandoverAssessment",
    "assess_manufacturer_handover",
]
