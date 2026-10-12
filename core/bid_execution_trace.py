"""Read-only source traceability for Bid execution subjects."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Mapping
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from core.bid_execution import ExecutionSubjectKind
from core.database import Database

_MARKER = re.compile(r"\[DISC:([^\]]+)]")
SubjectKey = tuple[ExecutionSubjectKind, str]


class ExecutionTraceSource(BaseModel):
    """One business-readable citation behind an execution item."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_name: str
    locator: str | None = None
    verified_quote: str | None = None
    finding_id: str | None = None


class ExecutionSourceTrace(BaseModel):
    """Frozen origin and citations for one execution subject."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: Literal["From RFP finding", "Added manually"]
    sources: list[ExecutionTraceSource] = Field(default_factory=list)
    source_url: str | None = None


class BidExecutionTrace:
    """Resolve execution citations with bounded, read-only SQLite queries."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    @staticmethod
    def _tables(conn: sqlite3.Connection) -> set[str]:
        return {
            str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    @staticmethod
    def _document_title_expression(conn: sqlite3.Connection, alias: str) -> str:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
        if "control_title" in columns:
            return f"COALESCE({alias}.control_title,{alias}.filename)"
        return f"{alias}.filename"

    @staticmethod
    def _marker(value: str | None) -> str | None:
        match = _MARKER.search(value or "")
        return match.group(1) if match else None

    def source_trace(
        self,
        bid_id: str,
        kind: ExecutionSubjectKind | str,
        subject_id: str,
    ) -> ExecutionSourceTrace:
        """Resolve one subject's source without exposing technical IDs as labels."""
        subject_kind = ExecutionSubjectKind(kind)
        markers: dict[SubjectKey, str] = {}
        with self._conn() as conn:
            if subject_kind is ExecutionSubjectKind.WORK_ITEM:
                row = conn.execute(
                    "SELECT details FROM work_items WHERE bid_id=? AND work_item_id=?",
                    (bid_id, subject_id),
                ).fetchone()
                marker = self._marker(str(row["details"]) if row and row["details"] else None)
            elif subject_kind is ExecutionSubjectKind.MANUFACTURER_INPUT:
                row = conn.execute(
                    """SELECT r.source_row_reference FROM vendor_bid_requirements r
                    JOIN vendor_bid_packages p ON p.package_id=r.package_id
                    WHERE p.bid_id=? AND r.requirement_id=?""",
                    (bid_id, subject_id),
                ).fetchone()
                marker = self._marker(
                    str(row["source_row_reference"])
                    if row and row["source_row_reference"]
                    else None
                )
            else:
                marker = None
        if marker is not None:
            markers[(subject_kind, subject_id)] = marker
        return self.source_traces(bid_id, [(subject_kind, subject_id)], markers=markers)[
            (subject_kind, subject_id)
        ]

    def source_traces(
        self,
        bid_id: str,
        subjects: Iterable[SubjectKey],
        *,
        markers: Mapping[SubjectKey, str] | None = None,
    ) -> dict[SubjectKey, ExecutionSourceTrace]:
        """Resolve many traces together so a list projection does not query per row."""
        keys = tuple(dict.fromkeys(subjects))
        sources: dict[SubjectKey, list[ExecutionTraceSource]] = {key: [] for key in keys}
        findings: dict[SubjectKey, list[str]] = {key: [] for key in keys}
        supplied_markers = markers or {}
        with self._conn() as conn:
            tables = self._tables(conn)
            proposal_for_key = self._proposal_links(conn, tables, bid_id, keys, supplied_markers)
            for key, proposal_ids in proposal_for_key.items():
                findings[key].extend(proposal_ids)
            self._append_discovery_sources(conn, tables, proposal_for_key, sources)
            self._append_deliverable_sources(conn, tables, bid_id, keys, sources)
            self._append_work_requirement_sources(conn, tables, bid_id, keys, sources)
        result: dict[SubjectKey, ExecutionSourceTrace] = {}
        for key in keys:
            unique = list(dict.fromkeys(sources[key]))
            first_finding = findings[key][0] if findings[key] else None
            result[key] = ExecutionSourceTrace(
                origin="From RFP finding" if first_finding else "Added manually",
                sources=unique,
                source_url=(
                    f"/bids/{bid_id}/execution/source/{first_finding}" if first_finding else None
                ),
            )
        return result

    @staticmethod
    def _proposal_links(
        conn: sqlite3.Connection,
        tables: set[str],
        bid_id: str,
        keys: tuple[SubjectKey, ...],
        markers: Mapping[SubjectKey, str],
    ) -> dict[SubjectKey, list[str]]:
        linked: dict[SubjectKey, list[str]] = {key: [] for key in keys}
        by_record = {subject_id: key for key in keys for subject_id in (key[1],)}
        if "deliverable_discovery_destinations" in tables and by_record:
            placeholders = ",".join("?" for _ in by_record)
            rows = conn.execute(
                f"SELECT proposal_id,record_id FROM deliverable_discovery_destinations "
                f"WHERE bid_id=? AND record_id IN ({placeholders}) ORDER BY created_at",
                (bid_id, *by_record),
            ).fetchall()
            for row in rows:
                key = by_record.get(str(row["record_id"]))
                if key is not None:
                    linked[key].append(str(row["proposal_id"]))
        for key, proposal_id in markers.items():
            if key in linked and proposal_id not in linked[key]:
                linked[key].append(proposal_id)
        return linked

    def _append_discovery_sources(
        self,
        conn: sqlite3.Connection,
        tables: set[str],
        proposal_for_key: Mapping[SubjectKey, list[str]],
        result: dict[SubjectKey, list[ExecutionTraceSource]],
    ) -> None:
        proposal_ids = tuple(
            dict.fromkeys(value for values in proposal_for_key.values() for value in values)
        )
        if "deliverable_discovery_proposal_sources_v2" in tables:
            source_table = "deliverable_discovery_proposal_sources_v2"
        elif "deliverable_discovery_proposal_sources" in tables:
            source_table = "deliverable_discovery_proposal_sources"
        else:
            return
        if not proposal_ids:
            return
        document_joins = ""
        document_name = "NULL"
        if {"document_versions", "documents"}.issubset(tables):
            document_name = self._document_title_expression(conn, "doc")
            document_joins = """LEFT JOIN document_versions version
              ON version.document_version_id=source.document_version_id
            LEFT JOIN documents doc ON doc.id=version.document_id"""
        file_join = ""
        received_name = "NULL"
        if (
            source_table == "deliverable_discovery_proposal_sources_v2"
            and "bid_received_files" in tables
        ):
            received_name = "file.original_filename"
            file_join = "LEFT JOIN bid_received_files file ON file.file_id=source.received_file_id"
        quote_value = "NULL"
        quote_join = ""
        if "deliverable_discovery_citation_quotes" in tables:
            quote_value = "quote.quote"
            quote_join = """LEFT JOIN deliverable_discovery_citation_quotes quote
              ON quote.citation_id=source.citation_id"""
        placeholders = ",".join("?" for _ in proposal_ids)
        rows = conn.execute(
            f"""SELECT source.proposal_id,source.locator,{quote_value} AS quote,
            {document_name} AS document_name,{received_name} AS received_name
            FROM {source_table} source
            {quote_join}
            {document_joins}
            {file_join}
            WHERE source.proposal_id IN ({placeholders}) ORDER BY source.citation_id""",
            proposal_ids,
        ).fetchall()
        keyed = {
            proposal_id: key
            for key, proposal_ids_for_key in proposal_for_key.items()
            for proposal_id in proposal_ids_for_key
        }
        for row in rows:
            key = keyed.get(str(row["proposal_id"]))
            if key is None:
                continue
            name = row["document_name"] or row["received_name"] or "Source document"
            result[key].append(
                ExecutionTraceSource(
                    document_name=str(name),
                    locator=str(row["locator"]) if row["locator"] else None,
                    verified_quote=str(row["quote"]) if row["quote"] else None,
                    finding_id=str(row["proposal_id"]),
                )
            )

    def _append_deliverable_sources(
        self,
        conn: sqlite3.Connection,
        tables: set[str],
        bid_id: str,
        keys: tuple[SubjectKey, ...],
        result: dict[SubjectKey, list[ExecutionTraceSource]],
    ) -> None:
        ids = [key[1] for key in keys if key[0] is ExecutionSubjectKind.RETURNABLE]
        if not ids or "deliverable_links" not in tables:
            return
        document_joins = ""
        document_name = "NULL"
        if {"document_versions", "documents"}.issubset(tables):
            document_name = self._document_title_expression(conn, "doc")
            document_joins = """LEFT JOIN document_versions version
              ON link.target_type='DOCUMENT_VERSION'
              AND version.document_version_id=link.target_id
            LEFT JOIN documents doc ON doc.id=version.document_id"""
        file_join = ""
        received_name = "NULL"
        if "bid_received_files" in tables:
            received_name = "file.original_filename"
            file_join = """LEFT JOIN bid_received_files file
              ON link.target_type='RECEIVED_FILE' AND file.file_id=link.target_id"""
        requirement_joins = ""
        requirement_fields = """NULL AS requirement_title,NULL AS source_clause,
            NULL AS source_page_start,NULL AS source_page_end,NULL AS source_locator_note,
            NULL AS source_excerpt,NULL AS requirement_document_name"""
        if "requirements" in tables:
            requirement_document_name = "NULL"
            requirement_document_join = ""
            if "documents" in tables:
                requirement_document_name = self._document_title_expression(conn, "reqdoc")
                requirement_document_join = (
                    "LEFT JOIN documents reqdoc ON reqdoc.id=req.source_document_id"
                )
            requirement_fields = (
                "req.title AS requirement_title,req.source_clause,req.source_page_start,"
                "req.source_page_end,req.source_locator_note,req.source_excerpt,"
                f"{requirement_document_name} AS requirement_document_name"
            )
            requirement_joins = f"""LEFT JOIN requirements req
              ON link.target_type='REQUIREMENT' AND req.requirement_id=link.target_id
            {requirement_document_join}"""
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"""SELECT link.deliverable_id,link.relation,link.target_id,
            {document_name} AS document_name,{received_name} AS received_name,
            {requirement_fields}
            FROM deliverable_links link
            {document_joins}
            {file_join}
            {requirement_joins}
            WHERE link.bid_id=? AND link.deliverable_id IN ({placeholders})
              AND link.relation IN ('SOURCE_DOCUMENT_VERSION','SOURCE_RECEIVED_FILE',
                                    'CREATED_BY_REQUIREMENT') ORDER BY link.link_id""",
            (bid_id, *ids),
        ).fetchall()
        for row in rows:
            key = (ExecutionSubjectKind.RETURNABLE, str(row["deliverable_id"]))
            if row["relation"] == "CREATED_BY_REQUIREMENT":
                result[key].append(self._requirement_source(row))
            else:
                name = row["document_name"] or row["received_name"] or "Source document"
                result[key].append(ExecutionTraceSource(document_name=str(name)))

    def _append_work_requirement_sources(
        self,
        conn: sqlite3.Connection,
        tables: set[str],
        bid_id: str,
        keys: tuple[SubjectKey, ...],
        result: dict[SubjectKey, list[ExecutionTraceSource]],
    ) -> None:
        ids = [key[1] for key in keys if key[0] is ExecutionSubjectKind.WORK_ITEM]
        if not ids or not {"domain_work_item_links", "requirements"}.issubset(tables):
            return
        document_name = "NULL"
        document_join = ""
        if "documents" in tables:
            document_name = self._document_title_expression(conn, "doc")
            document_join = "LEFT JOIN documents doc ON doc.id=req.source_document_id"
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"""SELECT link.work_item_id,req.title AS requirement_title,req.source_clause,
            req.source_page_start,req.source_page_end,req.source_locator_note,
            req.source_excerpt,{document_name} AS requirement_document_name
            FROM domain_work_item_links link JOIN requirements req
              ON req.requirement_id=link.requirement_id
            {document_join}
            WHERE link.bid_id=? AND link.work_item_id IN ({placeholders})
            ORDER BY link.created_at,link.link_id""",
            (bid_id, *ids),
        ).fetchall()
        for row in rows:
            key = (ExecutionSubjectKind.WORK_ITEM, str(row["work_item_id"]))
            result[key].append(self._requirement_source(row))

    @staticmethod
    def _requirement_source(row: sqlite3.Row) -> ExecutionTraceSource:
        pages = None
        if row["source_page_start"] is not None:
            pages = f"page {row['source_page_start']}"
            if row["source_page_end"] not in (None, row["source_page_start"]):
                pages += f"–{row['source_page_end']}"
        locator = row["source_locator_note"] or row["source_clause"] or pages
        name = row["requirement_document_name"] or row["requirement_title"] or "Requirement"
        return ExecutionTraceSource(
            document_name=str(name),
            locator=str(locator) if locator else None,
            verified_quote=str(row["source_excerpt"]) if row["source_excerpt"] else None,
        )


ExecutionTraceService = BidExecutionTrace
SourceTrace = ExecutionSourceTrace
SourceTraceSource = ExecutionTraceSource


def source_trace(
    db: Database,
    bid_id: str,
    kind: ExecutionSubjectKind | str,
    subject_id: str,
) -> ExecutionSourceTrace:
    """Convenience wrapper for one read-only trace lookup."""
    return BidExecutionTrace(db).source_trace(bid_id, kind, subject_id)
