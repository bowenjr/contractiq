"""Pure deterministic TASK-13 completeness gaps and metrics."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from core.commercial import CommercialGap
from core.commercial_repository import STANDARD_FACTOR_CATEGORIES

GAP_CODES = (
    "COMMERCIAL_REQUIRED_NO_SOURCE",
    "COMMERCIAL_REQUIRED_NO_OWNER",
    "COMMERCIAL_UNASSESSED",
    "COMMERCIAL_NO_CURRENT_ASSESSMENT",
    "COMMERCIAL_ASSESSMENT_UNREVIEWED",
    "COMMERCIAL_REVIEW_CHANGES_REQUIRED",
    "COMMERCIAL_REVIEW_REJECTED",
    "COMMERCIAL_ACCEPTED_NOT_LATEST",
    "COMMERCIAL_TREATMENT_UNRESOLVED",
    "COMMERCIAL_PRICE_AMOUNT_MISSING",
    "COMMERCIAL_CURRENCY_MISSING",
    "COMMERCIAL_EVIDENCE_MISSING",
    "COMMERCIAL_EVIDENCE_UNHEALTHY",
    "COMMERCIAL_EVIDENCE_EXPIRED",
    "COMMERCIAL_SUPPLIER_BASIS_NONCURRENT",
    "COMMERCIAL_INCLUDED_ELSEWHERE_BROKEN",
    "COMMERCIAL_INCLUDED_ELSEWHERE_CYCLE",
    "COMMERCIAL_SCOPE_INCLUDED_NO_COVERAGE",
    "COMMERCIAL_SCOPE_NOT_PRICED",
    "COMMERCIAL_SCOPE_PRICE_STATE_MISMATCH",
    "COMMERCIAL_SCOPE_DUPLICATE_OR_CONFLICTING_COVERAGE",
    "COMMERCIAL_MATERIAL_EXCLUSION",
    "COMMERCIAL_STANDARD_FACTOR_MISSING",
    "COMMERCIAL_FACTOR_UNASSESSED",
    "COMMERCIAL_OVERDUE",
)


def _date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def calculate_commercial_gaps(
    items: list[dict[str, Any]],
    *,
    as_of: date,
    links: dict[str, list[dict[str, Any]]] | None = None,
    assessments: dict[str, list[dict[str, Any]]] | None = None,
    reviews: dict[str, list[dict[str, Any]]] | None = None,
    scope_items: list[dict[str, Any]] | None = None,
    expected_bid_id: str | None = None,
) -> list[CommercialGap]:
    links = links or {}
    assessments = assessments or {}
    reviews = reviews or {}
    scope_items = scope_items or []
    result: list[CommercialGap] = []

    def add(
        code: str,
        bid: str,
        item_id: str | None,
        text: str,
        severity: str = "BLOCKING_ATTENTION",
        target: str | None = None,
    ) -> None:
        result.append(
            CommercialGap(
                code=code,
                bid_id=bid,
                commercial_item_id=item_id,
                target_id=target,
                severity=severity,
                explanation=text,
                dedup_key=f"{bid}:{item_id or target}:{code}",
            )
        )

    active = [item for item in items if item.get("lifecycle_state") == "ACTIVE"]
    categories = {str(item.get("category")) for item in active}
    for category in STANDARD_FACTOR_CATEGORIES:
        if category not in categories:
            add(
                "COMMERCIAL_STANDARD_FACTOR_MISSING",
                expected_bid_id or (str(items[0].get("bid_id", "")) if items else ""),
                None,
                f"Standard factor {category} has no active commercial item.",
            )
    for item in active:
        bid = str(item["bid_id"])
        ident = str(item["commercial_item_id"])
        required = item.get("materiality") != "INFORMATIONAL"
        item_links = links.get(ident, [])
        if required and not item_links:
            add(
                "COMMERCIAL_REQUIRED_NO_SOURCE",
                bid,
                ident,
                "Active commercial item has no authoritative source link.",
            )
        if required and not item.get("owner"):
            add("COMMERCIAL_REQUIRED_NO_OWNER", bid, ident, "Active commercial item has no owner.")
        versions = assessments.get(ident, [])
        latest = versions[-1] if versions else None
        if latest is None:
            add("COMMERCIAL_NO_CURRENT_ASSESSMENT", bid, ident, "No assessment version exists.")
            if item.get("category") in STANDARD_FACTOR_CATEGORIES:
                add(
                    "COMMERCIAL_FACTOR_UNASSESSED",
                    bid,
                    ident,
                    "Standard commercial factor is unassessed.",
                )
            continue
        if latest.get("applicability") == "UNASSESSED":
            add("COMMERCIAL_UNASSESSED", bid, ident, "Applicability is unassessed.")
        if latest.get("treatment") == "UNRESOLVED":
            add(
                "COMMERCIAL_TREATMENT_UNRESOLVED", bid, ident, "Commercial treatment is unresolved."
            )
        if latest.get("treatment") in {"FIRM_PRICED", "SEPARATELY_PRICED", "ALLOWANCED"}:
            if latest.get("amount_decimal") is None:
                add(
                    "COMMERCIAL_PRICE_AMOUNT_MISSING",
                    bid,
                    ident,
                    "Priced treatment has no exact amount.",
                )
            if not latest.get("currency"):
                add("COMMERCIAL_CURRENCY_MISSING", bid, ident, "Priced treatment has no currency.")
        item_reviews = reviews.get(ident, [])
        matching = [
            row for row in item_reviews if row.get("assessment_id") == latest.get("assessment_id")
        ]
        if not matching:
            add(
                "COMMERCIAL_ASSESSMENT_UNREVIEWED",
                bid,
                ident,
                "Latest assessment is not independently reviewed.",
            )
        else:
            decision = matching[-1].get("decision")
            if decision == "CHANGES_REQUIRED":
                add(
                    "COMMERCIAL_REVIEW_CHANGES_REQUIRED",
                    bid,
                    ident,
                    "Latest assessment requires changes.",
                )
            if decision == "REJECTED":
                add("COMMERCIAL_REVIEW_REJECTED", bid, ident, "Latest assessment was rejected.")
        accepted = [row for row in item_reviews if row.get("decision") == "ACCEPTED"]
        if accepted and accepted[-1].get("assessment_id") != latest.get("assessment_id"):
            add("COMMERCIAL_ACCEPTED_NOT_LATEST", bid, ident, "Accepted assessment is not latest.")
        if latest.get("evidence_target_id") is None and latest.get("evidence_basis") in {
            "CONTROLLED_DOCUMENT_VERSION",
            "ACCEPTED_SUPPLIER_RESPONSE",
        }:
            add("COMMERCIAL_EVIDENCE_MISSING", bid, ident, "Required evidence target is missing.")
        if latest.get("evidence_unhealthy"):
            add(
                "COMMERCIAL_EVIDENCE_UNHEALTHY", bid, ident, "Evidence is diagnostically unhealthy."
            )
        expiry = _date(latest.get("validity_until"))
        if expiry is not None and expiry < as_of:
            add("COMMERCIAL_EVIDENCE_EXPIRED", bid, ident, "Assessment validity has expired.")
        due = _date(item.get("due_date"))
        if due is not None and due < as_of:
            add("COMMERCIAL_OVERDUE", bid, ident, "Commercial item is overdue.")
        if latest.get("treatment") == "EXCLUDED":
            add(
                "COMMERCIAL_MATERIAL_EXCLUSION",
                bid,
                ident,
                "Commercial exclusion remains visible after review.",
                "ADVISORY",
            )
        if latest.get("treatment") == "INCLUDED_ELSEWHERE":
            target = latest.get("evidence_target_id")
            if (
                not target
                or target == ident
                or not any(row.get("commercial_item_id") == target for row in items)
            ):
                add(
                    "COMMERCIAL_INCLUDED_ELSEWHERE_BROKEN",
                    bid,
                    ident,
                    "Included-elsewhere lineage is missing or invalid.",
                )
    for scope in scope_items:
        if scope.get("offer_position") == "INCLUDED":
            covers = [
                item
                for item in active
                if item.get("category") == "SCOPE_PRICE"
                and item.get("basis_role") == "CUSTOMER_PRICE"
                and any(
                    link.get("target_type") == "SCOPE_ITEM"
                    and link.get("target_id") == scope.get("scope_item_id")
                    for link in links.get(str(item.get("commercial_item_id")), [])
                )
            ]
            if not covers:
                add(
                    "COMMERCIAL_SCOPE_INCLUDED_NO_COVERAGE",
                    str(scope.get("bid_id")),
                    None,
                    "Included scope item has no explicit commercial coverage.",
                    target=str(scope.get("scope_item_id")),
                )
            if scope.get("pricing_state") in {"UNCONFIRMED", "NOT_PRICED"}:
                add(
                    "COMMERCIAL_SCOPE_NOT_PRICED",
                    str(scope.get("bid_id")),
                    None,
                    "Included scope item remains unpriced in TASK-10.",
                    target=str(scope.get("scope_item_id")),
                )
            if len(covers) > 1:
                add(
                    "COMMERCIAL_SCOPE_DUPLICATE_OR_CONFLICTING_COVERAGE",
                    str(scope.get("bid_id")),
                    None,
                    "Multiple customer-price commercial items cover the scope item.",
                    target=str(scope.get("scope_item_id")),
                )
            for cover in covers:
                latest_rows = (assessments.get(str(cover.get("commercial_item_id"))) or [])[-1:]
                if latest_rows and (
                    (
                        scope.get("pricing_state") == "PRICED"
                        and latest_rows[0].get("treatment")
                        not in {"FIRM_PRICED", "SEPARATELY_PRICED"}
                    )
                    or (
                        scope.get("pricing_state") == "ALLOWANCED"
                        and latest_rows[0].get("treatment") != "ALLOWANCED"
                    )
                    or (
                        scope.get("pricing_state") == "NO_CHARGE"
                        and latest_rows[0].get("treatment") != "NO_CHARGE"
                    )
                ):
                    add(
                        "COMMERCIAL_SCOPE_PRICE_STATE_MISMATCH",
                        str(scope.get("bid_id")),
                        cover.get("commercial_item_id"),
                        "TASK-10 pricing state conflicts with accepted commercial treatment.",
                        target=str(scope.get("scope_item_id")),
                    )
    return sorted(
        result,
        key=lambda gap: (gap.bid_id, gap.commercial_item_id or "", gap.target_id or "", gap.code),
    )


def commercial_metrics(items: list[dict[str, Any]], gaps: list[CommercialGap]) -> dict[str, int]:
    result = {code: 0 for code in GAP_CODES}
    result.update(
        {
            "active_items": sum(item.get("lifecycle_state") == "ACTIVE" for item in items),
            "open_gaps": len(gaps),
            "blocking_gaps": sum(g.severity.startswith("BLOCK") for g in gaps),
            "has_population": int(bool(items)),
        }
    )
    for gap in gaps:
        result[gap.code] = result.get(gap.code, 0) + 1
    return result
