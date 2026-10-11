"""Stage 2.1 — find and (narrowly) repair register records left by an interrupted acceptance.

``reconcile_origins`` compares three things for one Bid: the origin rows
(``core.deliverable_discovery_origins``), the register records carrying the finding's
``[DISC:<id>]`` marker (work items in every state, manufacturer requirements, **every**
commercial-position revision), and the reviewer's decision. It reports every inconsistency it finds.

What ``apply=True`` may do is deliberately small, and nothing else is ever done:

* backfill an origin row for a record that carries a marker (or a legacy accepted Deliverable);
* attach a found record to a claim that never recorded it;
* release a claim that has no record once its lease has expired.

It never writes or changes a review decision, never edits or deletes a register record, and writes
an audit row in the same transaction as each repair. Reports are read-only without ``apply``.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from core.deliverable_discovery_origins import (
    ORIGIN_LEASE_SECONDS,
    ORIGINS_TABLE,
    AuditWriter,
    Origin,
    active_origin,
    insert_origin,
)
from core.requirement_lifecycle import DestinationKind

_MARKER_RE = re.compile(r"\[DISC:([^\]\s]+)\]")


class ReconcileState(StrEnum):
    """The inconsistent situations the reconciliation recognises."""

    CLAIM_WITHOUT_RECORD = "CLAIM_WITHOUT_RECORD"
    RECORD_WITHOUT_DECISION = "RECORD_WITHOUT_DECISION"
    ORPHAN_AFTER_REJECT = "ORPHAN_AFTER_REJECT"
    MARKER_WITHOUT_ORIGIN = "MARKER_WITHOUT_ORIGIN"
    DUPLICATE_MARKERS = "DUPLICATE_MARKERS"


@dataclass(frozen=True)
class MarkedRecord:
    """A register record that carries a finding's origin marker."""

    proposal_id: str
    kind: DestinationKind
    record_id: str
    detail: str | None = None


@dataclass(frozen=True)
class ReconcileItem:
    """One inconsistency, what was done about it, and what a person should do."""

    state: ReconcileState
    proposal_id: str
    destination_kind: str | None
    record_ids: tuple[str, ...]
    advice: str
    repaired: bool = False
    repair: str | None = None


@dataclass
class ReconcileReport:
    """Everything found for a Bid; ``repaired`` counts changes made when ``apply`` was set."""

    bid_id: str
    applied: bool
    items: list[ReconcileItem] = field(default_factory=list)

    @property
    def repaired(self) -> int:
        return sum(1 for item in self.items if item.repaired)

    def by_state(self, state: ReconcileState) -> list[ReconcileItem]:
        return [item for item in self.items if item.state is state]

    def as_text(self) -> str:
        mode = "APPLIED" if self.applied else "DRY RUN (no changes made)"
        lines = [f"Discovery reconciliation for Bid {self.bid_id}: {mode}"]
        if not self.items:
            lines.append("Nothing to reconcile.")
        for item in self.items:
            done = f" -> {item.repair}" if item.repaired else ""
            lines.append(
                f"- {item.state.value} {item.proposal_id} {item.destination_kind or ''} "
                f"{', '.join(item.record_ids)}{done}\n    {item.advice}"
            )
        return "\n".join(lines)


def scan_marked_records(conn: sqlite3.Connection, bid_id: str) -> list[MarkedRecord]:
    """Every register record of the Bid that carries an origin marker.

    Work items are read in every state, manufacturer requirements across all of the Bid's
    packages, and commercial positions across **all** revisions, so a record is still found after
    its evidence text was later edited away or its work item closed.
    """
    found: list[MarkedRecord] = []
    for row in conn.execute(
        "SELECT work_item_id, details FROM work_items WHERE bid_id=? AND details LIKE '%[DISC:%'",
        (bid_id,),
    ):
        for proposal_id in dict.fromkeys(_MARKER_RE.findall(str(row["details"] or ""))):
            found.append(MarkedRecord(proposal_id, DestinationKind.WORK_ITEM, row["work_item_id"]))
    for row in conn.execute(
        """SELECT r.requirement_id, r.source_row_reference FROM vendor_bid_requirements r
        JOIN vendor_bid_packages p ON p.package_id=r.package_id
        WHERE p.bid_id=? AND r.source_row_reference LIKE '%[DISC:%'""",
        (bid_id,),
    ):
        for proposal_id in dict.fromkeys(_MARKER_RE.findall(str(row["source_row_reference"]))):
            found.append(
                MarkedRecord(proposal_id, DestinationKind.VENDOR_DOCUMENT, row["requirement_id"])
            )
    first_version: dict[tuple[str, str], int] = {}
    for row in conn.execute(
        """SELECT position_id, version_number, supporting_evidence
        FROM commercial_position_versions
        WHERE bid_id=? AND supporting_evidence LIKE '%[DISC:%' ORDER BY version_number""",
        (bid_id,),
    ):
        for proposal_id in _MARKER_RE.findall(str(row["supporting_evidence"] or "")):
            first_version.setdefault((proposal_id, row["position_id"]), row["version_number"])
    for (proposal_id, position_id), version in first_version.items():
        found.append(
            MarkedRecord(
                proposal_id,
                DestinationKind.COMMERCIAL_POSITION,
                position_id,
                f"position version {version}",
            )
        )
    return found


def marker_in_commercial_history(conn: sqlite3.Connection, position_id: str, marker: str) -> bool:
    """Whether any revision of a commercial position already carries ``marker``."""
    return (
        conn.execute(
            """SELECT 1 FROM commercial_position_versions
            WHERE position_id=? AND supporting_evidence LIKE ? LIMIT 1""",
            (position_id, f"%{marker}%"),
        ).fetchone()
        is not None
    )


def _decision(conn: sqlite3.Connection, proposal_id: str) -> str | None:
    row = conn.execute(
        """SELECT base.decision FROM deliverable_discovery_review_events base
        WHERE base.proposal_id=? AND NOT EXISTS (
            SELECT 1 FROM deliverable_discovery_review_events newer
            WHERE newer.supersedes_event_id=base.event_id)
        ORDER BY base.reviewed_at DESC LIMIT 1""",
        (proposal_id,),
    ).fetchone()
    return str(row["decision"]) if row is not None else None


def _legacy_deliverable_origins(conn: sqlite3.Connection, bid_id: str) -> list[MarkedRecord]:
    """Deliverables an accepted finding created before origins existed (never a reused link)."""
    rows = conn.execute(
        """SELECT e.proposal_id, e.linked_deliverable_id AS record_id
        FROM deliverable_discovery_review_events e
        LEFT JOIN deliverable_discovery_destinations d ON d.proposal_id=e.proposal_id
        WHERE e.bid_id=? AND e.decision='ACCEPTED' AND e.linked_deliverable_id IS NOT NULL
        AND (d.proposal_id IS NULL OR (d.reused=0 AND d.destination_kind='DELIVERABLE'))""",
        (bid_id,),
    ).fetchall()
    return [
        MarkedRecord(str(r["proposal_id"]), DestinationKind.DELIVERABLE, str(r["record_id"]))
        for r in rows
    ]


def reconcile_origins(
    conn_factory: Callable[[], sqlite3.Connection],
    audit: AuditWriter,
    bid_id: str,
    *,
    apply: bool = False,
    actor: str = "reconciliation",
    now: datetime | None = None,
    lease_seconds: int = ORIGIN_LEASE_SECONDS,
) -> ReconcileReport:
    """Report (and with ``apply`` narrowly repair) origin/record/decision inconsistencies."""
    current_time = now or datetime.now(UTC)
    report = ReconcileReport(bid_id=bid_id, applied=apply)
    with conn_factory() as conn:
        marked = scan_marked_records(conn, bid_id)
        marked += _legacy_deliverable_origins(conn, bid_id)
        by_proposal: dict[str, list[MarkedRecord]] = defaultdict(list)
        for record in marked:
            by_proposal[record.proposal_id].append(record)
        known = {
            str(r[0])
            for r in conn.execute(
                "SELECT proposal_id FROM deliverable_discovery_proposals WHERE bid_id=?",
                (bid_id,),
            )
        }

        for proposal_id, records in sorted(by_proposal.items()):
            if proposal_id not in known:
                continue
            distinct = tuple(sorted({r.record_id for r in records}))
            if len(distinct) > 1:
                report.items.append(
                    ReconcileItem(
                        ReconcileState.DUPLICATE_MARKERS,
                        proposal_id,
                        records[0].kind.value,
                        distinct,
                        "More than one register record carries this finding's marker. Keep the "
                        "right one and cancel the other through its own register; nothing was "
                        "changed here.",
                    )
                )
                continue
            origin = active_origin(conn, proposal_id)
            if origin is None:
                _backfill(conn, audit, report, records[0], bid_id, apply, actor, current_time)
            elif origin.record_id is None:
                _attach(conn, audit, report, origin, records[0], apply, actor)
            elif origin.record_id != records[0].record_id:
                report.items.append(
                    ReconcileItem(
                        ReconcileState.DUPLICATE_MARKERS,
                        proposal_id,
                        origin.destination_kind.value,
                        (origin.record_id, records[0].record_id),
                        "The origin names one record but a different record carries the marker. "
                        "Nothing was changed; inspect both registers.",
                    )
                )

        for row in conn.execute(
            f"SELECT * FROM {ORIGINS_TABLE} WHERE bid_id=? AND released_at IS NULL", (bid_id,)
        ).fetchall():
            origin = _origin_from_row(row)
            _classify_origin(
                conn, audit, report, origin, by_proposal, apply, actor, current_time, lease_seconds
            )
        if apply:
            conn.commit()
    return report


def _begin(conn: sqlite3.Connection) -> None:
    """Open the repair transaction once; every repair and its audit row share it."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def _origin_from_row(row: sqlite3.Row) -> Origin:
    return Origin(
        origin_id=str(row["origin_id"]),
        proposal_id=str(row["proposal_id"]),
        bid_id=str(row["bid_id"]),
        destination_kind=DestinationKind(row["destination_kind"]),
        record_id=row["record_id"],
        record_detail=row["record_detail"],
        claimed_at=datetime.fromisoformat(str(row["claimed_at"])),
        claimed_by=str(row["claimed_by"]),
        released_at=None,
    )


def _backfill(
    conn: sqlite3.Connection,
    audit: AuditWriter,
    report: ReconcileReport,
    record: MarkedRecord,
    bid_id: str,
    apply: bool,
    actor: str,
    now: datetime,
) -> None:
    item = ReconcileItem(
        ReconcileState.MARKER_WITHOUT_ORIGIN,
        record.proposal_id,
        record.kind.value,
        (record.record_id,),
        "A record created by this finding has no origin row (it predates origins). Applying the "
        "reconciliation records the origin; the record and the decision are not touched.",
    )
    if apply:
        _begin(conn)
        insert_origin(
            conn,
            audit,
            proposal_id=record.proposal_id,
            bid_id=bid_id,
            kind=record.kind,
            actor=actor,
            record_id=record.record_id,
            record_detail=record.detail,
            now=now,
        )
        item = ReconcileItem(
            item.state,
            item.proposal_id,
            item.destination_kind,
            item.record_ids,
            item.advice,
            True,
            "origin recorded",
        )
    report.items.append(item)


def _attach(
    conn: sqlite3.Connection,
    audit: AuditWriter,
    report: ReconcileReport,
    origin: Origin,
    record: MarkedRecord,
    apply: bool,
    actor: str,
) -> None:
    item = ReconcileItem(
        ReconcileState.CLAIM_WITHOUT_RECORD,
        origin.proposal_id,
        origin.destination_kind.value,
        (record.record_id,),
        "A claim exists and its record was written but never attached. Applying the "
        "reconciliation attaches it.",
    )
    if apply:
        _begin(conn)
        conn.execute(
            f"""UPDATE {ORIGINS_TABLE} SET record_id=?, record_detail=?
            WHERE origin_id=? AND record_id IS NULL AND released_at IS NULL""",
            (record.record_id, record.detail, origin.origin_id),
        )
        audit(
            conn,
            origin.bid_id,
            actor,
            "deliverable_discovery_origin_recorded",
            f"{origin.proposal_id}:{origin.destination_kind.value}:{record.record_id}",
        )
        item = ReconcileItem(
            item.state,
            item.proposal_id,
            item.destination_kind,
            item.record_ids,
            item.advice,
            True,
            "record attached to claim",
        )
    report.items.append(item)


def _classify_origin(
    conn: sqlite3.Connection,
    audit: AuditWriter,
    report: ReconcileReport,
    origin: Origin,
    by_proposal: dict[str, list[MarkedRecord]],
    apply: bool,
    actor: str,
    now: datetime,
    lease_seconds: int,
) -> None:
    decision = _decision(conn, origin.proposal_id)
    kind = origin.destination_kind.value
    if origin.record_id is None:
        if by_proposal.get(origin.proposal_id):
            return  # reported and attached above
        expired = now - origin.claimed_at >= timedelta(seconds=lease_seconds)
        item = ReconcileItem(
            ReconcileState.CLAIM_WITHOUT_RECORD,
            origin.proposal_id,
            kind,
            (),
            "A claim exists but no record carries the marker. "
            + (
                "Its lease has expired, so applying the reconciliation releases it."
                if expired
                else "Its lease has not expired; another request may still be working."
            ),
        )
        if apply and expired:
            _begin(conn)
            conn.execute(
                f"""UPDATE {ORIGINS_TABLE} SET released_at=?
                WHERE origin_id=? AND record_id IS NULL AND released_at IS NULL""",
                (now.isoformat(), origin.origin_id),
            )
            audit(
                conn,
                origin.bid_id,
                actor,
                "deliverable_discovery_origin_released",
                f"{origin.proposal_id}:{kind}:released by reconciliation after lease expiry",
            )
            item = ReconcileItem(
                item.state, item.proposal_id, kind, (), item.advice, True, "claim released"
            )
        report.items.append(item)
        return
    if decision == "ACCEPTED":
        return
    if decision in {"REJECTED", "DEFERRED"}:
        report.items.append(
            ReconcileItem(
                ReconcileState.ORPHAN_AFTER_REJECT,
                origin.proposal_id,
                kind,
                (origin.record_id,),
                f"The finding was later {decision.lower()} but the record it created still "
                "exists in its register. Review that record there and cancel it if unwanted; "
                "nothing was changed here.",
            )
        )
        return
    report.items.append(
        ReconcileItem(
            ReconcileState.RECORD_WITHOUT_DECISION,
            origin.proposal_id,
            kind,
            (origin.record_id,),
            "The record exists but the finding is still Pending (the acceptance was "
            "interrupted). Accept the finding again: it will reuse this record. The decision is "
            "never recorded automatically.",
        )
    )


__all__ = [
    "MarkedRecord",
    "ReconcileItem",
    "ReconcileReport",
    "ReconcileState",
    "marker_in_commercial_history",
    "reconcile_origins",
    "scan_marked_records",
]
