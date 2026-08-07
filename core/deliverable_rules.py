"""Pure deterministic TASK-12 gap and metric calculations."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from core.deliverables import DeliverableCriticality, DeliverableGap, DeliverableState, DueBasis

GAP_CODES = (
    "DELIVERABLE_REQUIRED_NO_SOURCE",
    "DELIVERABLE_REQUIRED_NO_OWNER",
    "DELIVERABLE_REQUIRED_NO_PERFORMER",
    "DELIVERABLE_REQUIRED_NO_RECIPIENT",
    "DELIVERABLE_REQUIRED_UNSCHEDULED",
    "DELIVERABLE_SUPPLIER_NOT_ASSIGNED",
    "DELIVERABLE_SUPPLIER_NO_COMMITMENT",
    "DELIVERABLE_SUPPLIER_COMMITMENT_NONCURRENT",
    "DELIVERABLE_SUPPLIER_COMMITMENT_EXPIRED",
    "DELIVERABLE_SUPPLIER_COMMITMENT_DEGRADED",
    "DELIVERABLE_SUPPLIER_COMMITMENT_LATE",
    "DELIVERABLE_OVERDUE_NO_SUBMISSION",
    "DELIVERABLE_DUE_SOON",
    "DELIVERABLE_SUBMISSION_UNREVIEWED",
    "DELIVERABLE_SUBMISSION_REJECTED",
    "DELIVERABLE_REVISION_REQUIRED",
    "DELIVERABLE_ACCEPTED_NOT_LATEST",
    "DELIVERABLE_EVIDENCE_MISSING",
    "DELIVERABLE_EVIDENCE_DEGRADED",
    "DELIVERABLE_EVIDENCE_EXPIRED",
    "DELIVERABLE_FLOWDOWN_MISMATCH",
)


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def calculate_deliverable_gaps(
    items: list[dict[str, Any]],
    *,
    as_of: date,
    links: dict[str, list[dict[str, Any]]] | None = None,
    commitments: dict[str, list[dict[str, Any]]] | None = None,
    submissions: dict[str, list[dict[str, Any]]] | None = None,
    reviews: dict[str, list[dict[str, Any]]] | None = None,
    due_soon_days: int = 7,
) -> list[DeliverableGap]:
    links = links or {}
    commitments = commitments or {}
    submissions = submissions or {}
    reviews = reviews or {}
    result: list[DeliverableGap] = []
    for item in sorted(items, key=lambda row: (str(row.get("bid_id")), str(row["deliverable_id"]))):
        if item.get("workflow_state") in {
            DeliverableState.CANCELLED.value,
            DeliverableState.SATISFIED.value,
        }:
            continue
        if item.get("criticality") == DeliverableCriticality.CONDITIONAL.value and not item.get(
            "condition_active"
        ):
            continue
        required = item.get("criticality") == DeliverableCriticality.MANDATORY.value or bool(
            item.get("condition_active")
        )
        did = str(item["deliverable_id"])
        bid = str(item["bid_id"])
        severity = "BLOCKING_ATTENTION" if required else "ADVISORY"

        def add(
            code: str,
            text: str,
            ids: tuple[str, ...] = (),
            current_bid: str = bid,
            current_id: str = did,
            current_severity: str = severity,
        ) -> None:
            result.append(
                DeliverableGap(
                    code=code,
                    bid_id=current_bid,
                    deliverable_id=current_id,
                    severity=current_severity,
                    explanation=text,
                    source_ids=ids,
                    dedup_key=f"{current_id}:{code}",
                )
            )

        if required and not links.get(did):
            add(
                "DELIVERABLE_REQUIRED_NO_SOURCE",
                "Required deliverable has no authoritative source link.",
            )
        if required and not item.get("owner"):
            add("DELIVERABLE_REQUIRED_NO_OWNER", "Required deliverable has no owner.")
        if required and not item.get("recipient"):
            add("DELIVERABLE_REQUIRED_NO_RECIPIENT", "Required deliverable has no recipient.")
        if required and not item.get("owner"):
            add("DELIVERABLE_REQUIRED_NO_PERFORMER", "Required deliverable has no performer/owner.")
        if required and item.get("due_basis") == DueBasis.UNSCHEDULED.value:
            add("DELIVERABLE_REQUIRED_UNSCHEDULED", "Required deliverable is unscheduled.")
        supplier = item.get("supplier_id")
        if required and str(item.get("direction", "")).startswith("SUPPLIER_") and not supplier:
            add(
                "DELIVERABLE_SUPPLIER_NOT_ASSIGNED",
                "Supplier-directed deliverable has no assigned supplier.",
            )
        rows = commitments.get(did, [])
        commitment = rows[-1] if rows else None
        if required and supplier and commitment is None:
            add("DELIVERABLE_SUPPLIER_NO_COMMITMENT", "Supplier deliverable has no commitment.")
        if commitment:
            valid_until = _as_date(commitment.get("validity_until"))
            due = _as_date(commitment.get("committed_due_date"))
            if valid_until and valid_until < as_of:
                add("DELIVERABLE_SUPPLIER_COMMITMENT_EXPIRED", "Supplier commitment has expired.")
            if commitment.get("current") is False:
                add(
                    "DELIVERABLE_SUPPLIER_COMMITMENT_NONCURRENT",
                    "Supplier commitment is not current.",
                )
            if commitment.get("degraded"):
                add(
                    "DELIVERABLE_SUPPLIER_COMMITMENT_DEGRADED",
                    "Supplier commitment evidence is degraded.",
                )
            if due and due < as_of:
                add("DELIVERABLE_SUPPLIER_COMMITMENT_LATE", "Supplier commitment is late.")
        latest = submissions.get(did, [])
        latest_submission = latest[-1] if latest else None
        accepted = [row for row in latest if row.get("decision") == "ACCEPTED"]
        latest_review = (reviews.get(did) or [])[-1:]
        if required and latest_submission is None:
            add("DELIVERABLE_OVERDUE_NO_SUBMISSION", "Required deliverable has no submission.")
        due_date = _as_date(item.get("fixed_due_date"))
        if due_date and due_date < as_of and latest_submission is None:
            add("DELIVERABLE_OVERDUE_NO_SUBMISSION", "Deliverable is overdue without a submission.")
        elif due_date and 0 <= (due_date - as_of).days <= due_soon_days:
            add("DELIVERABLE_DUE_SOON", "Deliverable is due soon.")
        if latest_submission:
            if not latest_review:
                add(
                    "DELIVERABLE_SUBMISSION_UNREVIEWED",
                    "Latest submission has no independent review.",
                )
            if latest_review and latest_review[0].get("decision") == "REJECTED":
                add("DELIVERABLE_SUBMISSION_REJECTED", "Latest submission was rejected.")
            if latest_review and latest_review[0].get("decision") == "REVISION_REQUIRED":
                add("DELIVERABLE_REVISION_REQUIRED", "Latest submission requires revision.")
            if accepted and accepted[-1].get("submission_id") != latest_submission.get(
                "submission_id"
            ):
                add(
                    "DELIVERABLE_ACCEPTED_NOT_LATEST",
                    "Accepted submission is not the latest version.",
                )
            if latest_submission.get(
                "evidence_mode"
            ) == "MANUAL_RECORD" and not latest_submission.get("evidence_note"):
                add("DELIVERABLE_EVIDENCE_MISSING", "Submission has no evidence note.")
            if latest_submission.get("evidence_degraded"):
                add(
                    "DELIVERABLE_EVIDENCE_DEGRADED",
                    "Submission evidence is diagnostically degraded.",
                )
            expires = _as_date(latest_submission.get("expires_at"))
            if expires is not None and expires < as_of:
                add("DELIVERABLE_EVIDENCE_EXPIRED", "Submission evidence has expired.")
        elif required:
            add("DELIVERABLE_EVIDENCE_MISSING", "No submission evidence is recorded.")
        if any(
            row.get("cross_bid") or row.get("inactive") or row.get("degraded")
            for row in links.get(did, [])
        ):
            add(
                "DELIVERABLE_FLOWDOWN_MISMATCH",
                "A source link is inactive, cross-bid, or diagnostically degraded.",
            )
    return sorted(result, key=lambda gap: (gap.bid_id, gap.deliverable_id, gap.code))


def deliverable_metrics(gaps: list[DeliverableGap], items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {code: 0 for code in GAP_CODES}
    for gap in gaps:
        counts[gap.code] = counts.get(gap.code, 0) + 1
    counts["total_deliverables"] = len(items)
    counts["open_gaps"] = len(gaps)
    counts["blocking_gaps"] = sum(gap.severity == "BLOCKING_ATTENTION" for gap in gaps)
    return counts
