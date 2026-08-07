"""Application services for supplier assurance workflows."""
# ruff: noqa: E501

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import uuid4

from core.database import Database
from core.supplier_assurance import (
    Coverage,
    CoverageState,
    FlowDownLink,
    RequestItem,
    ResponseVersion,
    ReviewState,
    Supplier,
    SupplierRequest,
)
from core.supplier_assurance_rules import SupplierGap, calculate_gaps
from core.supplier_repository import SupplierRepository


class SupplierService:
    """Coordinates validation, optimistic concurrency, and atomic writes."""

    def __init__(self, db: Database, repository: SupplierRepository | None = None) -> None:
        self.db = db
        self.repository = repository or SupplierRepository(db)

    def suppliers(self, bid_id: str | None = None) -> list[dict[str, Any]]:
        return self.repository.list_suppliers(bid_id)

    def requests(self, bid_id: str | None = None) -> list[dict[str, Any]]:
        return self.repository.list_requests(bid_id)

    def create_supplier(self, supplier: Supplier, actor: str = "operator") -> Supplier:
        self.repository.create_supplier(supplier, actor)
        return supplier

    def create_request(
        self, request: SupplierRequest, items: list[RequestItem], actor: str = "operator"
    ) -> SupplierRequest:
        if request.request_state.value != "DRAFT" and request.issued_at is None:
            raise ValueError("issued requests require issued_at")
        self.repository.create_request(request, items, actor)
        return request

    def add_flow_down(self, link: FlowDownLink, actor: str = "operator") -> FlowDownLink:
        self.repository.add_flow_down(link, actor)
        return link

    def issue_request(
        self, request_id: str, expected_version: int, actor: str = "operator"
    ) -> None:
        self.repository.issue_request(request_id, expected_version, actor)

    def close_request(
        self, request_id: str, expected_version: int, rationale: str, actor: str = "operator"
    ) -> None:
        self.repository.close_request(request_id, expected_version, rationale, actor)

    def withdraw_request(
        self, request_id: str, expected_version: int, actor: str = "operator"
    ) -> None:
        self.repository.withdraw_request(request_id, expected_version, actor)

    def create_response(
        self,
        response: ResponseVersion,
        coverage: list[Coverage],
        actor: str = "operator",
    ) -> ResponseVersion:
        """Insert an immutable response version and exactly one row per item."""
        with cast(sqlite3.Connection, self.db._conn()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                request = conn.execute(
                    "SELECT bid_id,supplier_id,request_state FROM supplier_requests WHERE request_id=?",
                    (response.request_id,),
                ).fetchone()
                if (
                    request is None
                    or request["bid_id"] != response.bid_id
                    or request["supplier_id"] != response.supplier_id
                ):
                    raise ValueError("response request identity mismatch")
                if request["request_state"] != "ISSUED":
                    raise ValueError("responses require an issued request")
                item_rows = conn.execute(
                    "SELECT request_item_id FROM supplier_request_items WHERE request_id=? ORDER BY sequence",
                    (response.request_id,),
                ).fetchall()
                item_ids = [str(row["request_item_id"]) for row in item_rows]
                provided = {row.request_item_id for row in coverage}
                if not provided <= set(item_ids):
                    raise ValueError("coverage cannot reference a non-request item")
                coverage_by_item = {row.request_item_id: row for row in coverage}
                coverage = [
                    coverage_by_item.get(
                        item_id,
                        Coverage(request_item_id=item_id, state=CoverageState.SILENT),
                    )
                    for item_id in item_ids
                ]
                if response.version_number != int(
                    conn.execute(
                        "SELECT COALESCE(MAX(version_number),0)+1 FROM supplier_response_versions WHERE response_id=?",
                        (response.response_id,),
                    ).fetchone()[0]
                ):
                    raise ValueError("response version is not the next immutable version")
                conn.execute(
                    "INSERT INTO supplier_responses VALUES (?,?,?,?,?,?,?, ?,?) ON CONFLICT(response_id) DO NOTHING",
                    (
                        response.response_id,
                        response.request_id,
                        response.supplier_id,
                        response.bid_id,
                        None,
                        None,
                        1,
                        response.created_at.isoformat(),
                        response.created_at.isoformat(),
                    ),
                )
                conn.execute(
                    "INSERT INTO supplier_response_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        response.response_version_id,
                        response.response_id,
                        response.request_id,
                        response.supplier_id,
                        response.bid_id,
                        response.version_number,
                        response.supplier_reference,
                        response.received_at.isoformat(),
                        response.evidence_mode.value,
                        response.document_version_id,
                        response.evidence_note,
                        response.validity_state.value,
                        response.valid_until.isoformat() if response.valid_until else None,
                        response.overall_note,
                        response.review_state.value,
                        response.reviewer,
                        response.review_note,
                        response.created_at.isoformat(),
                        response.created_by,
                    ),
                )
                for row in coverage:
                    conn.execute(
                        "INSERT INTO supplier_response_coverage VALUES (?,?,?,?,?,?)",
                        (
                            response.response_version_id,
                            row.request_item_id,
                            row.state.value,
                            row.exception_kind.value if row.exception_kind else None,
                            row.evidence_text,
                            row.operator_note,
                        ),
                    )
                conn.execute(
                    "UPDATE supplier_responses SET latest_version_id=?,updated_at=?,version=version+1 WHERE response_id=?",
                    (
                        response.response_version_id,
                        response.created_at.isoformat(),
                        response.response_id,
                    ),
                )
                self._audit(
                    conn,
                    response.bid_id,
                    actor,
                    "supplier_response_version_created",
                    response.response_version_id,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return response

    def review_response(
        self,
        response_version_id: str,
        reviewer: str,
        state: ReviewState,
        note: str | None = None,
        expected_version: int | None = None,
    ) -> None:
        if not reviewer.strip():
            raise ValueError("independent reviewer is required")
        with cast(sqlite3.Connection, self.db._conn()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT response_id,bid_id FROM supplier_response_versions WHERE response_version_id=?",
                    (response_version_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("response version not found")
                params: list[object] = [state.value, reviewer, note, response_version_id]
                predicate = "response_version_id=?"
                if expected_version is not None:
                    params.append(expected_version)
                    predicate += " AND version_number=?"
                changed = conn.execute(
                    f"UPDATE supplier_response_versions SET review_state=?,reviewer=?,review_note=? WHERE {predicate}",
                    tuple(params),
                ).rowcount
                if changed != 1:
                    raise ValueError("stale supplier response update")
                if state is ReviewState.ACCEPTED:
                    conn.execute(
                        "UPDATE supplier_responses SET accepted_version_id=? WHERE response_id=?",
                        (response_version_id, row["response_id"]),
                    )
                self._audit(
                    conn, row["bid_id"], reviewer, "supplier_response_reviewed", response_version_id
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _audit(
        self, conn: sqlite3.Connection, bid_id: str, actor: str, action: str, detail: str
    ) -> None:
        conn.execute(
            "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) VALUES(?,?,?,?,?,?)",
            (
                f"AUD-{uuid4().hex}",
                bid_id,
                actor,
                action,
                json.dumps({"id": detail}),
                datetime.now(UTC).isoformat(),
            ),
        )

    def gaps(self, bid_id: str, as_of: date | None = None) -> list[SupplierGap]:
        as_of = as_of or datetime.now(UTC).date()
        with cast(sqlite3.Connection, self.db._conn()) as conn:
            requests = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM supplier_requests WHERE bid_id=?", (bid_id,)
                ).fetchall()
            ]
            items = [
                dict(row)
                for row in conn.execute(
                    "SELECT i.*,f.target_type,f.target_id FROM supplier_request_items i LEFT JOIN supplier_item_flow_down f USING(request_item_id) WHERE i.bid_id=?",
                    (bid_id,),
                ).fetchall()
            ]
            responses = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM supplier_response_versions WHERE bid_id=?", (bid_id,)
                ).fetchall()
            ]
            coverage = [
                dict(row)
                for row in conn.execute(
                    "SELECT c.* FROM supplier_response_coverage c JOIN supplier_response_versions v USING(response_version_id) WHERE v.bid_id=?",
                    (bid_id,),
                ).fetchall()
            ]
        return list(calculate_gaps(requests, items, responses, coverage, as_of_date=as_of))

    def metrics(self, bid_id: str) -> dict[str, int | float | bool]:
        with cast(sqlite3.Connection, self.db._conn()) as conn:
            total = int(
                conn.execute(
                    "SELECT COUNT(*) FROM supplier_request_items i JOIN supplier_requests r USING(request_id) WHERE r.bid_id=? AND r.request_state='ISSUED'",
                    (bid_id,),
                ).fetchone()[0]
            )
            confirmed = int(
                conn.execute(
                    "SELECT COUNT(*) FROM supplier_response_coverage c JOIN supplier_response_versions v USING(response_version_id) WHERE v.bid_id=? AND c.state='CONFIRMED'",
                    (bid_id,),
                ).fetchone()[0]
            )
            gaps = self.gaps(bid_id)
            return {
                "issued_items": total,
                "confirmed_items": confirmed,
                "coverage_percent": round(confirmed / total * 100, 2) if total else 0.0,
                "has_population": total > 0,
                "blocking_attention": sum(gap.severity == "BLOCKING_ATTENTION" for gap in gaps),
                "advisory_attention": sum(gap.severity == "ADVISORY" for gap in gaps),
            }
