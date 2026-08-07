"""Pure deterministic TASK-14 gaps and metrics."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from core.contract_risk import RiskGap

GAP_CODES = (
    "CONTRACT_RISK_SOURCE_MISSING",
    "CONTRACT_RISK_SOURCE_UNHEALTHY",
    "CONTRACT_RISK_SOURCE_NOT_CURRENT",
    "CONTRACT_RISK_MANUAL_SOURCE_STALE",
    "CONTRACT_RISK_ASSESSMENT_MISSING",
    "CONTRACT_RISK_ASSESSMENT_UNREVIEWED",
    "CONTRACT_RISK_REVIEW_CHANGES_REQUIRED",
    "CONTRACT_RISK_ACCEPTED_NOT_LATEST",
    "CONTRACT_RISK_CUSTOMER_POSITION_MISSING",
    "CONTRACT_RISK_COMPANY_POSITION_MISSING",
    "CONTRACT_RISK_BUSINESS_IMPACT_MISSING",
    "CONTRACT_RISK_AFFECTED_FUNCTION_MISSING",
    "CONTRACT_RISK_OWNER_MISSING",
    "CONTRACT_RISK_LIKELIHOOD_UNASSESSED",
    "CONTRACT_RISK_CONSEQUENCE_UNASSESSED",
    "CONTRACT_RISK_EXPOSURE_UNASSESSED",
    "CONTRACT_RISK_EXPOSURE_INCOHERENT",
    "CONTRACT_RISK_POSITION_UNRESOLVED",
    "CONTRACT_RISK_FALLBACK_AUTHORITY_MISSING",
    "CONTRACT_RISK_HIGH_NO_ESCALATION_OWNER",
    "CONTRACT_RISK_UNCAPPED_EXPOSURE",
    "CONTRACT_RISK_REQUIRED_LINK_MISSING",
    "CONTRACT_RISK_REQUIREMENT_DEVIATION_UNLINKED",
    "CONTRACT_RISK_SUPPLIER_EXCEPTION_UNLINKED",
    "CONTRACT_RISK_COMMERCIAL_IMPACT_UNLINKED",
    "CONTRACT_RISK_DUPLICATE_OR_CONFLICT",
    "CONTRACT_RISK_RESOLUTION_UNPROVEN",
    "CONTRACT_RISK_PAST_DUE",
)


def _date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def calculate_risk_gaps(
    items: list[dict[str, Any]],
    *,
    as_of: date,
    sources: dict[str, list[dict[str, Any]]] | None = None,
    assessments: dict[str, list[dict[str, Any]]] | None = None,
    reviews: dict[str, list[dict[str, Any]]] | None = None,
    links: dict[str, list[dict[str, Any]]] | None = None,
) -> list[RiskGap]:
    sources = sources or {}
    assessments = assessments or {}
    reviews = reviews or {}
    links = links or {}
    result: list[RiskGap] = []

    def add(
        code: str, row: dict[str, Any], explanation: str, severity: str = "BLOCKING_ATTENTION"
    ) -> None:
        result.append(
            RiskGap(
                code=code,
                bid_id=str(row["bid_id"]),
                issue_id=str(row["issue_id"]),
                severity=severity,
                explanation=explanation,
                dedup_key=f"{row['issue_id']}:{code}",
            )
        )

    for row in sorted(
        items, key=lambda value: (str(value.get("bid_id")), str(value.get("issue_id")))
    ):
        if row.get("lifecycle_state") not in {"ACTIVE", "RESOLVED"}:
            continue
        issue_id = str(row["issue_id"])
        source_rows = sources.get(issue_id, [])
        versions = assessments.get(issue_id, [])
        latest = versions[-1] if versions else None
        review_rows = reviews.get(issue_id, [])
        if not source_rows:
            add("CONTRACT_RISK_SOURCE_MISSING", row, "Active issue has no exact source.")
        if not row.get("owner"):
            add("CONTRACT_RISK_OWNER_MISSING", row, "Active issue has no owner.")
        if latest is None:
            add("CONTRACT_RISK_ASSESSMENT_MISSING", row, "No assessment version exists.")
            continue
        if not latest.get("customer_position"):
            add("CONTRACT_RISK_CUSTOMER_POSITION_MISSING", row, "Customer position is missing.")
        if latest.get("disposition") != "NOT_APPLICABLE" and not latest.get("company_position"):
            add(
                "CONTRACT_RISK_COMPANY_POSITION_MISSING",
                row,
                "Proposed company position is missing.",
            )
        if not latest.get("business_impact"):
            add("CONTRACT_RISK_BUSINESS_IMPACT_MISSING", row, "Business impact is missing.")
        if not latest.get("affected_functions"):
            add("CONTRACT_RISK_AFFECTED_FUNCTION_MISSING", row, "Affected function is missing.")
        if latest.get("likelihood") == "UNASSESSED":
            add("CONTRACT_RISK_LIKELIHOOD_UNASSESSED", row, "Likelihood is unassessed.")
        if latest.get("consequence") == "UNASSESSED":
            add("CONTRACT_RISK_CONSEQUENCE_UNASSESSED", row, "Consequence is unassessed.")
        if latest.get("exposure_basis") == "NOT_ASSESSED":
            add("CONTRACT_RISK_EXPOSURE_UNASSESSED", row, "Exposure basis is unassessed.")
        matching = [
            review
            for review in review_rows
            if review.get("assessment_id") == latest.get("assessment_id")
        ]
        if not matching:
            add(
                "CONTRACT_RISK_ASSESSMENT_UNREVIEWED",
                row,
                "Latest assessment is not independently reviewed.",
            )
        elif matching[-1].get("decision") in {"CHANGES_REQUIRED", "REJECTED"}:
            add("CONTRACT_RISK_REVIEW_CHANGES_REQUIRED", row, "Latest review is not accepted.")
        accepted = [review for review in review_rows if review.get("decision") == "ACCEPTED"]
        if accepted and accepted[-1].get("assessment_id") != latest.get("assessment_id"):
            add("CONTRACT_RISK_ACCEPTED_NOT_LATEST", row, "Accepted assessment is not latest.")
        if latest.get("exposure_basis") == "UNLIMITED_OR_UNCAPPED":
            add(
                "CONTRACT_RISK_UNCAPPED_EXPOSURE",
                row,
                "Exposure is explicitly unlimited or uncapped.",
                "HIGH_ATTENTION",
            )
        if (
            latest.get("likelihood") in {"LIKELY", "ALMOST_CERTAIN"}
            and latest.get("consequence") in {"MAJOR", "SEVERE", "CATASTROPHIC"}
            and not latest.get("escalation_owner")
        ):
            add(
                "CONTRACT_RISK_HIGH_NO_ESCALATION_OWNER",
                row,
                "High or critical issue has no escalation owner.",
            )
        if latest.get("exposure_basis") == "MONETARY_RANGE" and any(
            latest.get(key) is None
            for key in ("minimum_decimal", "most_likely_decimal", "maximum_decimal", "currency")
        ):
            add("CONTRACT_RISK_EXPOSURE_INCOHERENT", row, "Monetary exposure is incomplete.")
        expiry = _date(latest.get("validity_until"))
        if expiry is not None and expiry < as_of:
            add("CONTRACT_RISK_PAST_DUE", row, "Issue assessment is past due.")
    return sorted(result, key=lambda gap: (gap.bid_id, gap.issue_id or "", gap.code))


def risk_metrics(items: list[dict[str, Any]], gaps: list[RiskGap]) -> dict[str, int]:
    result = {code: 0 for code in GAP_CODES}
    result.update(
        {
            "total": len(items),
            "active": sum(row.get("lifecycle_state") == "ACTIVE" for row in items),
            "blocking": sum(gap.severity.startswith("BLOCK") for gap in gaps),
            "high_attention": sum(gap.severity == "HIGH_ATTENTION" for gap in gaps),
            "has_population": int(bool(items)),
        }
    )
    for gap in gaps:
        result[gap.code] = result.get(gap.code, 0) + 1
    return result
