"""Pure deterministic supplier assurance rules.

The rule function accepts plain immutable projections so it can be used by the
UI, My Day, validation script, and readiness adapter without I/O or a clock.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class SupplierGap:
    code: str
    entity_id: str
    bid_id: str
    severity: str
    reasons: tuple[str, ...] = ()


def calculate_gaps(
    requests: Sequence[Mapping[str, object]],
    items: Sequence[Mapping[str, object]],
    responses: Sequence[Mapping[str, object]],
    coverage: Sequence[Mapping[str, object]],
    *,
    as_of_date: date,
    warning_days: int = 0,
) -> tuple[SupplierGap, ...]:
    """Return stable, deduplicated attention rows for the supplied snapshot."""
    if warning_days < 0:
        raise ValueError("warning_days must be non-negative")
    gaps: dict[tuple[str, str], SupplierGap] = {}
    item_by_id = {str(row["request_item_id"]): row for row in items}
    request_by_id = {str(row["request_id"]): row for row in requests}
    latest_by_response: dict[str, Mapping[str, object]] = {}
    for response in responses:
        key = str(response.get("response_id", response.get("response_version_id", "")))
        old = latest_by_response.get(key)
        if old is None or int(str(response.get("version_number", 0))) > int(
            str(old.get("version_number", 0))
        ):
            latest_by_response[key] = response
    response_for_request = {str(row.get("request_id")): row for row in latest_by_response.values()}

    def add(code: str, entity: str, bid: str, severity: str) -> None:
        gaps.setdefault((code, entity), SupplierGap(code, entity, bid, severity, (code,)))

    for request_id, request in sorted(request_by_id.items()):
        bid = str(request.get("bid_id", ""))
        if request.get("request_state") == "ISSUED" and request.get("due_date"):
            if (
                date.fromisoformat(str(request["due_date"])) < as_of_date
                and request_id not in response_for_request
            ):
                add("SUPPLIER_REQUEST_OVERDUE_NO_RESPONSE", request_id, bid, "BLOCKING_ATTENTION")
        if (
            request.get("request_state") == "CLOSED"
            and request.get("close_rationale")
            and request_id not in response_for_request
        ):
            add("SUPPLIER_REQUEST_CLOSED_WITHOUT_RESPONSE", request_id, bid, "ADVISORY")

    for response in latest_by_response.values():
        bid = str(response.get("bid_id", ""))
        entity = str(response.get("response_version_id", response.get("response_id", "")))
        if response.get("review_state") != "ACCEPTED":
            add("LATEST_RESPONSE_UNREVIEWED", entity, bid, "BLOCKING_ATTENTION")
        if response.get("review_state") == "CHANGES_REQUIRED":
            add("SUPPLIER_RESPONSE_CHANGES_REQUIRED", entity, bid, "BLOCKING_ATTENTION")
        if response.get("accepted_version_id") and response.get(
            "accepted_version_id"
        ) != response.get("response_version_id"):
            add("LATEST_RESPONSE_UNREVIEWED", entity, bid, "BLOCKING_ATTENTION")
        state = str(response.get("validity_state", "NOT_PROVIDED"))
        if state == "NOT_PROVIDED":
            add("SUPPLIER_RESPONSE_VALIDITY_NOT_PROVIDED", entity, bid, "ADVISORY")
        elif response.get("valid_until"):
            expiry = date.fromisoformat(str(response["valid_until"]))
            if expiry < as_of_date:
                add("SUPPLIER_RESPONSE_EXPIRED", entity, bid, "BLOCKING_ATTENTION")
            elif expiry == as_of_date:
                add("SUPPLIER_RESPONSE_EXPIRES_TODAY", entity, bid, "ADVISORY")
            elif (expiry - as_of_date).days <= warning_days:
                add("SUPPLIER_RESPONSE_EXPIRING", entity, bid, "ADVISORY")

    for row in coverage:
        item_id = str(row["request_item_id"])
        item = item_by_id.get(item_id, {})
        request = request_by_id.get(str(item.get("request_id")), {})
        bid = str(request.get("bid_id", ""))
        if row.get("state") == "SILENT":
            add("SUPPLIER_RESPONSE_ITEM_SILENT", item_id, bid, "BLOCKING_ATTENTION")
        if row.get("state") == "EXCEPTION":
            add("SUPPLIER_RESPONSE_EXCEPTION", item_id, bid, "ADVISORY")
            if (
                row.get("exception_kind") == "EXCLUSION"
                and item.get("scope_offer_position") == "INCLUDED"
            ):
                add("SUPPLIER_EXCLUDED_CUSTOMER_INCLUDED", item_id, bid, "BLOCKING_ATTENTION")
        role = str(item.get("support_role", ""))
        if role == "REQUIRED_SUPPORT" and row.get("state") != "CONFIRMED":
            if item.get("customer_need") == "REQUIRED":
                add("SUPPLIER_REQUIRED_SCOPE_UNCONFIRMED", item_id, bid, "BLOCKING_ATTENTION")
            if item.get("requirement_significance") in {"MANDATORY", "DISQUALIFYING"}:
                add(
                    "SUPPLIER_MANDATORY_REQUIREMENT_UNCONFIRMED", item_id, bid, "BLOCKING_ATTENTION"
                )
            if item.get("interface_materiality") in {"MATERIAL", "UNASSESSED"}:
                add("SUPPLIER_MATERIAL_INTERFACE_UNCONFIRMED", item_id, bid, "BLOCKING_ATTENTION")
        if item.get("target_health") in {"INACTIVE", "DEGRADED"}:
            add(
                "SUPPLIER_TARGET_INACTIVE_OR_DEGRADED",
                item_id,
                bid,
                "BLOCKING_ATTENTION" if role == "REQUIRED_SUPPORT" else "ADVISORY",
            )
        if (
            response_for_request.get(str(item.get("request_id")), {}).get("evidence_health")
            == "DEGRADED"
        ):
            add(
                "SUPPLIER_RESPONSE_EVIDENCE_DEGRADED",
                item_id,
                bid,
                "BLOCKING_ATTENTION" if role == "REQUIRED_SUPPORT" else "ADVISORY",
            )
    return tuple(sorted(gaps.values(), key=lambda gap: (gap.bid_id, gap.entity_id, gap.code)))
