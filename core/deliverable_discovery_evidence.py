"""Insert-only evidence that qualifies a discovery finding without altering the finding.

Proposals and their sources are immutable by trigger. What the analysis can say *about* a finding —
how far its due date can be trusted, and the sentence that creates the obligation — is stored here,
beside it, in two additive insert-only tables. A finding with no row here predates these tables;
readers treat its date as unverified.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from core.date_integrity import DueDateBasis
from core.deliverable_discovery import DeliverableProposal

DATES_TABLE = "deliverable_discovery_proposal_dates"
QUOTES_TABLE = "deliverable_discovery_citation_quotes"
UNVERIFIED = "UNVERIFIED"
"""Basis reported for a finding recorded before due-date evidence was kept."""

_STATEMENTS = (
    f"""CREATE TABLE IF NOT EXISTS {DATES_TABLE} (
        proposal_id TEXT PRIMARY KEY, basis TEXT NOT NULL, proposed_date TEXT, rule_text TEXT,
        FOREIGN KEY (proposal_id) REFERENCES deliverable_discovery_proposals(proposal_id),
        CHECK (basis IN ('STATED','CALCULATED','PROPOSED','UNKNOWN'))
    )""",
    f"""CREATE TABLE IF NOT EXISTS {QUOTES_TABLE} (
        citation_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, quote TEXT NOT NULL,
        FOREIGN KEY (proposal_id) REFERENCES deliverable_discovery_proposals(proposal_id)
    )""",
    f"""CREATE TRIGGER IF NOT EXISTS ddpd_no_update BEFORE UPDATE ON {DATES_TABLE} BEGIN
        SELECT RAISE(ABORT,'discovery date evidence is immutable'); END""",
    f"""CREATE TRIGGER IF NOT EXISTS ddpd_no_delete BEFORE DELETE ON {DATES_TABLE} BEGIN
        SELECT RAISE(ABORT,'discovery date evidence cannot be deleted'); END""",
    f"""CREATE TRIGGER IF NOT EXISTS ddcq_no_update BEFORE UPDATE ON {QUOTES_TABLE} BEGIN
        SELECT RAISE(ABORT,'discovery quotations are immutable'); END""",
    f"""CREATE TRIGGER IF NOT EXISTS ddcq_no_delete BEFORE DELETE ON {QUOTES_TABLE} BEGIN
        SELECT RAISE(ABORT,'discovery quotations cannot be deleted'); END""",
)


def ensure_evidence_tables(conn: sqlite3.Connection) -> None:
    """Create the evidence tables inside the caller's transaction. Safe to repeat."""
    for statement in _STATEMENTS:
        conn.execute(statement)


def insert_proposal_evidence(conn: sqlite3.Connection, proposal: DeliverableProposal) -> None:
    """Write a proposal's date and quotation evidence in the caller's transaction."""
    conn.execute(
        f"INSERT INTO {DATES_TABLE} VALUES (?,?,?,?)",
        (
            proposal.proposal_id,
            proposal.due_date_basis.value,
            proposal.proposed_due_date.isoformat() if proposal.proposed_due_date else None,
            proposal.due_date_text,
        ),
    )
    for source in proposal.sources:
        if source.quote:
            conn.execute(
                f"INSERT INTO {QUOTES_TABLE} VALUES (?,?,?)",
                (source.citation_id, proposal.proposal_id, source.quote),
            )


def attach_evidence(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """Add date evidence and per-source quotations to a proposal row read from the database.

    Adds ``due_date_basis``, ``proposed_due_date`` and ``due_date_text`` to the row and a ``quote``
    to each source. ``due_date`` is left set only when it is stated or calculated. For a finding
    recorded before this evidence existed, a stored date is reported as ``UNVERIFIED`` and exposed
    only as ``proposed_due_date``.
    """
    evidence = conn.execute(
        f"SELECT basis,proposed_date,rule_text FROM {DATES_TABLE} WHERE proposal_id=?",
        (row["proposal_id"],),
    ).fetchone()
    stored = row.get("due_date")
    if evidence is None:
        row["due_date_basis"] = UNVERIFIED if stored else DueDateBasis.UNKNOWN.value
        row["proposed_due_date"] = stored
        row["due_date_text"] = None
        row["due_date"] = None
    else:
        row["due_date_basis"] = evidence["basis"]
        row["proposed_due_date"] = evidence["proposed_date"]
        row["due_date_text"] = evidence["rule_text"]
        if evidence["basis"] not in {"STATED", "CALCULATED"}:
            row["due_date"] = None
    quotes = {
        item["citation_id"]: item["quote"]
        for item in conn.execute(
            f"SELECT citation_id,quote FROM {QUOTES_TABLE} WHERE proposal_id=?",
            (row["proposal_id"],),
        )
    }
    for source in row.get("sources", []):
        source["quote"] = quotes.get(source["citation_id"])


def parse_date(value: object) -> date | None:
    """An ISO date string or ``None``."""
    return date.fromisoformat(str(value)) if value else None
