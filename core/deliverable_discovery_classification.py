"""Insert-only lifecycle facts beside immutable discovery proposals.

The structure mirrors ``core.deliverable_discovery_evidence``: ``ensure_evidence_tables`` is
mirrored by :func:`ensure_classification_tables`, ``insert_proposal_evidence`` by
:func:`insert_proposal_classification`, and ``attach_evidence`` by
:func:`attach_classification`.  Callers own the transaction so proposal creation, later human
reclassification, destination linkage, and their audit rows remain atomic.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from core.deliverable_discovery import (
    DeliverableProposal,
    ProposalReviewDecision,
    SourceCitation,
)
from core.deliverable_discovery_evidence import QUOTES_TABLE
from core.ops08 import STANDARD_TOPICS
from core.requirement_lifecycle import (
    AppliesAt,
    DestinationKind,
    DiscoveryLifecycle,
    ItemKind,
    ProposalClassification,
)

CLASSIFICATIONS_TABLE = "deliverable_discovery_proposal_classification"
RECLASSIFICATIONS_TABLE = "deliverable_discovery_reclassifications"
DESTINATIONS_TABLE = "deliverable_discovery_destinations"
LEGACY_REVIEW_REASON = "Not classified (recorded before lifecycle classification)"

_LIFECYCLES = tuple(item.value for item in DiscoveryLifecycle)
_APPLIES_AT = tuple(item.value for item in AppliesAt)
_ITEM_KINDS = tuple(item.value for item in ItemKind)
_DESTINATION_KINDS = tuple(item.value for item in DestinationKind)
_STANDARD_TOPIC_KEYS = frozenset(topic[0] for topic in STANDARD_TOPICS)


def _sql_values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


_STATEMENTS = (
    f"""CREATE TABLE IF NOT EXISTS {CLASSIFICATIONS_TABLE} (
        proposal_id TEXT PRIMARY KEY, lifecycle TEXT NOT NULL, model_lifecycle TEXT NOT NULL,
        requirement_status TEXT NOT NULL, item_kind TEXT NOT NULL, description TEXT NOT NULL,
        applies_at TEXT, commercial_topic TEXT, responsible_party TEXT, dependencies TEXT,
        confidence TEXT NOT NULL, review_reason TEXT, source_section TEXT,
        classified_by TEXT NOT NULL,
        FOREIGN KEY (proposal_id) REFERENCES deliverable_discovery_proposals(proposal_id),
        CHECK (lifecycle IN ({_sql_values(_LIFECYCLES)})),
        CHECK (model_lifecycle IN ({_sql_values(_LIFECYCLES)})),
        CHECK (requirement_status IN ('REQUIRED','OPTIONAL','CONDITIONAL','UNCLEAR')),
        CHECK (item_kind IN ({_sql_values(_ITEM_KINDS)})),
        CHECK (applies_at IS NULL OR applies_at IN ({_sql_values(_APPLIES_AT)})),
        CHECK (confidence IN ('HIGH','MEDIUM','LOW')),
        CHECK (classified_by IN ('MODEL','RULE'))
    )""",
    f"""CREATE TABLE IF NOT EXISTS {RECLASSIFICATIONS_TABLE} (
        event_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, bid_id TEXT NOT NULL,
        lifecycle TEXT NOT NULL, applies_at TEXT, item_kind TEXT, commercial_topic TEXT,
        rationale TEXT NOT NULL, reviewer TEXT NOT NULL, supersedes_event_id TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (proposal_id) REFERENCES deliverable_discovery_proposals(proposal_id),
        FOREIGN KEY (bid_id) REFERENCES bids(bid_id),
        FOREIGN KEY (supersedes_event_id) REFERENCES {RECLASSIFICATIONS_TABLE}(event_id),
        CHECK (lifecycle IN ({_sql_values(_LIFECYCLES)})),
        CHECK (applies_at IS NULL OR applies_at IN ({_sql_values(_APPLIES_AT)})),
        CHECK (item_kind IS NULL OR item_kind IN ({_sql_values(_ITEM_KINDS)}))
    )""",
    f"""CREATE UNIQUE INDEX IF NOT EXISTS uq_ddrc_supersedes
        ON {RECLASSIFICATIONS_TABLE}(supersedes_event_id)
        WHERE supersedes_event_id IS NOT NULL""",
    f"""CREATE TABLE IF NOT EXISTS {DESTINATIONS_TABLE} (
        destination_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL UNIQUE,
        event_id TEXT NOT NULL, bid_id TEXT NOT NULL, destination_kind TEXT NOT NULL,
        record_id TEXT NOT NULL, reused INTEGER NOT NULL, note TEXT, created_at TEXT NOT NULL,
        FOREIGN KEY (proposal_id) REFERENCES deliverable_discovery_proposals(proposal_id),
        FOREIGN KEY (event_id) REFERENCES deliverable_discovery_review_events(event_id),
        FOREIGN KEY (bid_id) REFERENCES bids(bid_id),
        CHECK (destination_kind IN ({_sql_values(_DESTINATION_KINDS)})),
        CHECK (reused IN (0,1))
    )""",
    f"""CREATE TRIGGER IF NOT EXISTS ddpc_no_update BEFORE UPDATE ON
        {CLASSIFICATIONS_TABLE} BEGIN
        SELECT RAISE(ABORT,'discovery classifications are immutable'); END""",
    f"""CREATE TRIGGER IF NOT EXISTS ddpc_no_delete BEFORE DELETE ON
        {CLASSIFICATIONS_TABLE} BEGIN
        SELECT RAISE(ABORT,'discovery classifications cannot be deleted'); END""",
    f"""CREATE TRIGGER IF NOT EXISTS ddrc_no_update BEFORE UPDATE ON
        {RECLASSIFICATIONS_TABLE} BEGIN
        SELECT RAISE(ABORT,'discovery reclassifications are immutable'); END""",
    f"""CREATE TRIGGER IF NOT EXISTS ddrc_no_delete BEFORE DELETE ON
        {RECLASSIFICATIONS_TABLE} BEGIN
        SELECT RAISE(ABORT,'discovery reclassifications cannot be deleted'); END""",
    f"""CREATE TRIGGER IF NOT EXISTS dddest_no_update BEFORE UPDATE ON
        {DESTINATIONS_TABLE} BEGIN
        SELECT RAISE(ABORT,'discovery destinations are immutable'); END""",
    f"""CREATE TRIGGER IF NOT EXISTS dddest_no_delete BEFORE DELETE ON
        {DESTINATIONS_TABLE} BEGIN
        SELECT RAISE(ABORT,'discovery destinations cannot be deleted'); END""",
)


def ensure_classification_tables(conn: sqlite3.Connection) -> None:
    """Create lifecycle side tables inside the caller's transaction; safe to repeat."""
    for statement in _STATEMENTS:
        conn.execute(statement)


def insert_proposal_classification(conn: sqlite3.Connection, proposal: DeliverableProposal) -> None:
    """Write a proposal's optional base classification in the caller's transaction."""
    classification = proposal.classification
    if classification is None:
        return
    conn.execute(
        f"""INSERT INTO {CLASSIFICATIONS_TABLE}(
        proposal_id,lifecycle,model_lifecycle,requirement_status,item_kind,description,
        applies_at,commercial_topic,responsible_party,dependencies,confidence,review_reason,
        source_section,classified_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            proposal.proposal_id,
            classification.lifecycle.value,
            classification.model_lifecycle.value,
            classification.requirement_status.value,
            classification.item_kind.value,
            classification.description,
            classification.applies_at.value if classification.applies_at else None,
            classification.commercial_topic,
            classification.responsible_party,
            classification.dependencies,
            classification.confidence.value,
            classification.review_reason,
            classification.source_section,
            classification.classified_by.value,
        ),
    )


def _reclassifications(conn: sqlite3.Connection, proposal_id: str) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in conn.execute(
            f"""SELECT * FROM {RECLASSIFICATIONS_TABLE} WHERE proposal_id=?
            ORDER BY created_at,event_id""",
            (proposal_id,),
        ).fetchall()
    ]


def _current_reclassification(conn: sqlite3.Connection, proposal_id: str) -> dict[str, Any] | None:
    item = conn.execute(
        f"""SELECT base.* FROM {RECLASSIFICATIONS_TABLE} base
        WHERE base.proposal_id=? AND NOT EXISTS (
            SELECT 1 FROM {RECLASSIFICATIONS_TABLE} newer
            WHERE newer.supersedes_event_id=base.event_id)
        ORDER BY base.created_at DESC,base.event_id DESC LIMIT 1""",
        (proposal_id,),
    ).fetchone()
    return dict(item) if item is not None else None


def attach_classification(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """Attach base/effective classification, reviewer overlays, and destination to a row."""
    proposal_id = str(row["proposal_id"])
    base_row = conn.execute(
        f"SELECT * FROM {CLASSIFICATIONS_TABLE} WHERE proposal_id=?", (proposal_id,)
    ).fetchone()
    reclassifications = _reclassifications(conn, proposal_id)
    row["reclassifications"] = reclassifications
    destination = conn.execute(
        f"SELECT * FROM {DESTINATIONS_TABLE} WHERE proposal_id=?", (proposal_id,)
    ).fetchone()
    row["destination"] = dict(destination) if destination is not None else None

    if base_row is None:
        row["classification"] = None
        row.update(
            {
                "lifecycle": DiscoveryLifecycle.NEEDS_REVIEW.value,
                "model_lifecycle": None,
                "requirement_status": None,
                "item_kind": None,
                "description": None,
                "responsible_party": None,
                "dependencies": None,
                "confidence": None,
                "review_reason": LEGACY_REVIEW_REASON,
                "source_section": None,
                "applies_at": None,
                "commercial_topic": None,
                "classified_by": None,
                "legacy": True,
                "lifecycle_source": "legacy",
            }
        )
        return

    effective = dict(base_row)
    effective.pop("proposal_id", None)
    latest = _current_reclassification(conn, proposal_id)
    lifecycle_source = str(effective["classified_by"]).lower()
    if latest is not None:
        effective["lifecycle"] = latest["lifecycle"]
        effective["applies_at"] = latest["applies_at"]
        if latest["item_kind"] is not None:
            effective["item_kind"] = latest["item_kind"]
        if effective["item_kind"] == ItemKind.COMMERCIAL_QUALIFICATION.value:
            if latest["commercial_topic"] is not None:
                effective["commercial_topic"] = latest["commercial_topic"]
        else:
            effective["commercial_topic"] = None
        if effective["lifecycle"] == DiscoveryLifecycle.NEEDS_REVIEW.value:
            effective["review_reason"] = latest["rationale"]
        lifecycle_source = "reviewer"

    classification = ProposalClassification.model_validate(effective)
    classification_data = classification.model_dump(mode="json")
    row["classification"] = classification_data
    row.update(classification_data)
    row["legacy"] = False
    row["lifecycle_source"] = lifecycle_source


AuditWriter = Callable[[sqlite3.Connection, str, str, str, str], None]


def reclassify_proposals(
    conn_factory: Callable[[], sqlite3.Connection],
    audit: AuditWriter,
    bid_id: str,
    proposal_ids: Iterable[str],
    lifecycle: DiscoveryLifecycle | str,
    rationale: str,
    reviewer: str,
    *,
    applies_at: AppliesAt | str | None = None,
    item_kind: ItemKind | str | None = None,
    commercial_topic: str | None = None,
) -> list[dict[str, Any]]:
    """Append independent reviewer overrides, each atomically paired with its audit row."""
    lifecycle_value = DiscoveryLifecycle(lifecycle)
    applies_value = AppliesAt(applies_at) if applies_at is not None else None
    item_kind_value = ItemKind(item_kind) if item_kind is not None else None
    normalized_rationale = rationale.strip()
    normalized_reviewer = reviewer.strip()
    if not normalized_rationale:
        raise ValueError("Explain why these findings are being reclassified")
    if not normalized_reviewer:
        raise ValueError("Identify the reviewer reclassifying these findings")
    if lifecycle_value is not DiscoveryLifecycle.CONDITIONAL and applies_value is not None:
        raise ValueError("applies_at is only valid when lifecycle is CONDITIONAL")

    results: list[dict[str, Any]] = []
    for proposal_id in proposal_ids:
        try:
            with conn_factory() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    proposal = conn.execute(
                        "SELECT bid_id FROM deliverable_discovery_proposals WHERE proposal_id=?",
                        (proposal_id,),
                    ).fetchone()
                    if proposal is None or proposal["bid_id"] != bid_id:
                        raise ValueError("proposal not found in this Bid")
                    effective_row: dict[str, Any] = {"proposal_id": proposal_id}
                    attach_classification(conn, effective_row)
                    effective_item_kind = (
                        item_kind_value.value
                        if item_kind_value is not None
                        else effective_row["item_kind"]
                    )
                    effective_commercial_topic: str | None = None
                    if commercial_topic is not None:
                        if effective_item_kind != ItemKind.COMMERCIAL_QUALIFICATION.value:
                            raise ValueError(
                                "commercial_topic is only valid for a commercial qualification"
                            )
                        if commercial_topic not in _STANDARD_TOPIC_KEYS:
                            raise ValueError(
                                "commercial_topic must be a standard commercial topic key"
                            )
                        effective_commercial_topic = commercial_topic
                    elif effective_item_kind == ItemKind.COMMERCIAL_QUALIFICATION.value:
                        effective_commercial_topic = effective_row["commercial_topic"]
                    accepted = conn.execute(
                        """SELECT 1 FROM deliverable_discovery_review_events
                        WHERE proposal_id=? AND decision=? LIMIT 1""",
                        (proposal_id, ProposalReviewDecision.ACCEPTED.value),
                    ).fetchone()
                    if accepted is not None:
                        raise ValueError("accepted proposals cannot be reclassified")
                    current = _current_reclassification(conn, proposal_id)
                    event_id = f"DDRC-{uuid4().hex}"
                    created_at = datetime.now(UTC).isoformat()
                    conn.execute(
                        f"""INSERT INTO {RECLASSIFICATIONS_TABLE}(
                        event_id,proposal_id,bid_id,lifecycle,applies_at,item_kind,
                        commercial_topic,rationale,reviewer,supersedes_event_id,created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            event_id,
                            proposal_id,
                            bid_id,
                            lifecycle_value.value,
                            applies_value.value if applies_value else None,
                            effective_item_kind,
                            effective_commercial_topic,
                            normalized_rationale,
                            normalized_reviewer,
                            current["event_id"] if current is not None else None,
                            created_at,
                        ),
                    )
                    audit(
                        conn,
                        bid_id,
                        normalized_reviewer,
                        "deliverable_discovery_proposal_reclassified",
                        json.dumps(
                            {
                                "proposal_id": proposal_id,
                                "event_id": event_id,
                                "lifecycle": lifecycle_value.value,
                                "rationale": normalized_rationale,
                            },
                            sort_keys=True,
                        ),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            results.append({"proposal_id": proposal_id})
        except Exception as exc:
            results.append({"proposal_id": proposal_id, "error": str(exc)})
    return results


def record_destination(
    conn: sqlite3.Connection,
    *,
    proposal_id: str,
    event_id: str,
    bid_id: str,
    destination_kind: DestinationKind | str,
    record_id: str,
    reused: bool,
    note: str | None = None,
    destination_id: str | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Record one accepted proposal's register destination in the caller's transaction."""
    proposal = conn.execute(
        "SELECT bid_id FROM deliverable_discovery_proposals WHERE proposal_id=?", (proposal_id,)
    ).fetchone()
    event = conn.execute(
        """SELECT proposal_id,bid_id,decision FROM deliverable_discovery_review_events
        WHERE event_id=?""",
        (event_id,),
    ).fetchone()
    if proposal is None or proposal["bid_id"] != bid_id:
        raise ValueError("destination proposal was not found in this Bid")
    if (
        event is None
        or event["proposal_id"] != proposal_id
        or event["bid_id"] != bid_id
        or event["decision"] != ProposalReviewDecision.ACCEPTED.value
    ):
        raise ValueError("destination requires the proposal's accepted review event")
    destination = {
        "destination_id": destination_id or f"DDD-{uuid4().hex}",
        "proposal_id": proposal_id,
        "event_id": event_id,
        "bid_id": bid_id,
        "destination_kind": DestinationKind(destination_kind).value,
        "record_id": record_id,
        "reused": int(reused),
        "note": note,
        "created_at": (created_at or datetime.now(UTC)).isoformat(),
    }
    conn.execute(
        f"""INSERT INTO {DESTINATIONS_TABLE}(
        destination_id,proposal_id,event_id,bid_id,destination_kind,record_id,reused,note,created_at)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        tuple(destination.values()),
    )
    return destination


def _link_sources_to_deliverable(
    conn: sqlite3.Connection, bid_id: str, proposal_id: str, citation_ids: list[str]
) -> None:
    """Keep the register's cross-references complete when a later run cites another document.

    If the proposal was already accepted into a Deliverable, each newly cited received file gets a
    ``SOURCE_RECEIVED_FILE`` link (once). Other registers keep their original source.
    """
    accepted = conn.execute(
        """SELECT linked_deliverable_id FROM deliverable_discovery_review_events
        WHERE proposal_id=? AND decision='ACCEPTED' AND linked_deliverable_id IS NOT NULL
        ORDER BY reviewed_at DESC LIMIT 1""",
        (proposal_id,),
    ).fetchone()
    if accepted is None:
        return
    deliverable_id = str(accepted["linked_deliverable_id"])
    for citation_id in citation_ids:
        source = conn.execute(
            """SELECT received_file_id FROM deliverable_discovery_proposal_sources_v2
            WHERE citation_id=? AND received_file_id IS NOT NULL""",
            (citation_id,),
        ).fetchone()
        if source is None:
            continue
        file_id = str(source["received_file_id"])
        exists = conn.execute(
            """SELECT 1 FROM deliverable_links WHERE deliverable_id=?
            AND target_type='RECEIVED_FILE' AND target_id=? AND relation='SOURCE_RECEIVED_FILE'""",
            (deliverable_id, file_id),
        ).fetchone()
        if exists is None:
            conn.execute(
                "INSERT INTO deliverable_links VALUES (?,?,?,?,?,?,?,?)",
                (
                    f"DLK-{uuid4().hex}",
                    bid_id,
                    deliverable_id,
                    "RECEIVED_FILE",
                    file_id,
                    "SOURCE_RECEIVED_FILE",
                    datetime.now(UTC).isoformat(),
                    "system",
                ),
            )


def append_citations_transaction(
    conn_factory: Callable[[], sqlite3.Connection],
    audit: AuditWriter,
    proposal_id: str,
    citations: Iterable[SourceCitation],
) -> None:
    """Append previously unseen file/locator citations and quotations atomically."""
    with conn_factory() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            proposal = conn.execute(
                "SELECT bid_id FROM deliverable_discovery_proposals WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
            if proposal is None:
                raise ValueError("proposal not found; append citations to an existing proposal")
            bid_id = str(proposal["bid_id"])
            appended: list[str] = []
            for citation in citations:
                identity_column = (
                    "document_version_id"
                    if citation.document_version_id is not None
                    else "received_file_id"
                )
                identity = citation.document_version_id or citation.received_file_id
                extraction = conn.execute(
                    f"""SELECT 1 FROM deliverable_discovery_extractions_v2
                    WHERE bid_id=? AND {identity_column}=? AND locator=? AND status='OK'""",
                    (bid_id, identity, citation.locator),
                ).fetchone()
                if extraction is None:
                    raise ValueError("citation is not backed by a readable extraction for this Bid")
                duplicate = conn.execute(
                    f"""SELECT 1 FROM deliverable_discovery_proposal_sources_v2
                    WHERE proposal_id=? AND {identity_column}=? AND locator=?""",
                    (proposal_id, identity, citation.locator),
                ).fetchone()
                if duplicate is not None:
                    continue
                conn.execute(
                    """INSERT INTO deliverable_discovery_proposal_sources_v2(
                    citation_id,proposal_id,document_version_id,received_file_id,
                    locator_type,locator,excerpt) VALUES (?,?,?,?,?,?,?)""",
                    (
                        citation.citation_id,
                        proposal_id,
                        citation.document_version_id,
                        citation.received_file_id,
                        citation.locator_type.value,
                        citation.locator,
                        citation.excerpt,
                    ),
                )
                if citation.quote:
                    conn.execute(
                        f"INSERT INTO {QUOTES_TABLE}(citation_id,proposal_id,quote) VALUES (?,?,?)",
                        (citation.citation_id, proposal_id, citation.quote),
                    )
                appended.append(citation.citation_id)
            if appended:
                _link_sources_to_deliverable(conn, bid_id, proposal_id, appended)
                audit(
                    conn,
                    bid_id,
                    "system",
                    "deliverable_discovery_citation_appended",
                    json.dumps(
                        {"proposal_id": proposal_id, "citation_ids": appended}, sort_keys=True
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
