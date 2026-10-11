"""Stage 2.1 — which register record did an accepted finding create?

A finding that is accepted into a vendor register, commercial position or work item writes a record
through a service that owns its own transaction, so the discovery database cannot make "create the
record" and "record the acceptance" one atomic step. This module closes the gap with a *claim*:

1. ``claim_origin`` inserts the finding's single active origin row before any record is written.
   The partial unique index makes a second concurrent or repeated accept fail (or reuse the same
   origin), so two records can never originate from one finding.
2. ``set_origin_record`` attaches the record id once it exists (the one-way ``NULL -> value``
   transition the triggers allow).
3. A claim abandoned by a crashed process is adopted after ``ORIGIN_LEASE_SECONDS``: the stale claim
   is released and a new one inserted in the same transaction, so history is never edited.

Mirrors ``core.deliverable_discovery_classification`` (``DESTINATIONS_TABLE`` DDL and triggers,
``record_destination``): additive ``IF NOT EXISTS`` DDL, caller-owned transactions, audit rows in
the same transaction as every change. Rows are never deleted.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from core.requirement_lifecycle import DestinationKind

ORIGINS_TABLE = "deliverable_discovery_origins"
ORIGIN_LEASE_SECONDS = 120
IN_PROGRESS_MESSAGE = (
    "This finding is already being accepted by another request; retry in a moment."
)

AuditWriter = Callable[[sqlite3.Connection, str, str, str, str], None]

_KINDS = ",".join(f"'{item.value}'" for item in DestinationKind)

ORIGIN_STATEMENTS = (
    f"""CREATE TABLE IF NOT EXISTS {ORIGINS_TABLE} (
        origin_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, bid_id TEXT NOT NULL,
        destination_kind TEXT NOT NULL, record_id TEXT, record_detail TEXT,
        claimed_at TEXT NOT NULL, claimed_by TEXT NOT NULL, released_at TEXT,
        FOREIGN KEY (proposal_id) REFERENCES deliverable_discovery_proposals(proposal_id),
        FOREIGN KEY (bid_id) REFERENCES bids(bid_id),
        CHECK (destination_kind IN ({_KINDS}))
    )""",
    f"""CREATE UNIQUE INDEX IF NOT EXISTS uq_ddorigin_active_proposal
        ON {ORIGINS_TABLE}(proposal_id) WHERE released_at IS NULL""",
    f"""CREATE UNIQUE INDEX IF NOT EXISTS uq_ddorigin_active_record
        ON {ORIGINS_TABLE}(destination_kind, record_id)
        WHERE record_id IS NOT NULL AND released_at IS NULL
        AND destination_kind <> 'COMMERCIAL_POSITION'""",
    f"""CREATE TRIGGER IF NOT EXISTS ddorigin_no_delete BEFORE DELETE ON {ORIGINS_TABLE} BEGIN
        SELECT RAISE(ABORT,'discovery origins cannot be deleted'); END""",
    f"""CREATE TRIGGER IF NOT EXISTS ddorigin_transition_only BEFORE UPDATE ON {ORIGINS_TABLE}
        WHEN NOT (
            OLD.origin_id IS NEW.origin_id AND OLD.proposal_id IS NEW.proposal_id
            AND OLD.bid_id IS NEW.bid_id AND OLD.destination_kind IS NEW.destination_kind
            AND OLD.claimed_at IS NEW.claimed_at AND OLD.claimed_by IS NEW.claimed_by
            AND (OLD.record_id IS NEW.record_id
                 OR (OLD.record_id IS NULL AND NEW.record_id IS NOT NULL))
            AND (OLD.record_detail IS NEW.record_detail
                 OR (OLD.record_id IS NULL AND NEW.record_id IS NOT NULL))
            AND (OLD.released_at IS NEW.released_at
                 OR (OLD.released_at IS NULL AND NEW.released_at IS NOT NULL))
        ) BEGIN
        SELECT RAISE(ABORT,'discovery origins only change record_id or released_at once'); END""",
)


def ensure_origin_tables(conn: sqlite3.Connection) -> None:
    """Create the origin table, indexes and triggers inside the caller's transaction."""
    for statement in ORIGIN_STATEMENTS:
        conn.execute(statement)


@dataclass(frozen=True)
class Origin:
    """One claim (and, once written, the record) that a finding created."""

    origin_id: str
    proposal_id: str
    bid_id: str
    destination_kind: DestinationKind
    record_id: str | None
    record_detail: str | None
    claimed_at: datetime
    claimed_by: str
    released_at: datetime | None


def _origin(row: sqlite3.Row) -> Origin:
    return Origin(
        origin_id=str(row["origin_id"]),
        proposal_id=str(row["proposal_id"]),
        bid_id=str(row["bid_id"]),
        destination_kind=DestinationKind(row["destination_kind"]),
        record_id=str(row["record_id"]) if row["record_id"] is not None else None,
        record_detail=str(row["record_detail"]) if row["record_detail"] is not None else None,
        claimed_at=datetime.fromisoformat(str(row["claimed_at"])),
        claimed_by=str(row["claimed_by"]),
        released_at=(
            datetime.fromisoformat(str(row["released_at"]))
            if row["released_at"] is not None
            else None
        ),
    )


def active_origin(conn: sqlite3.Connection, proposal_id: str) -> Origin | None:
    """The finding's unreleased origin, if any."""
    row = conn.execute(
        f"SELECT * FROM {ORIGINS_TABLE} WHERE proposal_id=? AND released_at IS NULL",
        (proposal_id,),
    ).fetchone()
    return _origin(row) if row is not None else None


def insert_origin(
    conn: sqlite3.Connection,
    audit: AuditWriter,
    *,
    proposal_id: str,
    bid_id: str,
    kind: DestinationKind,
    actor: str,
    record_id: str | None = None,
    record_detail: str | None = None,
    now: datetime | None = None,
) -> Origin:
    """Insert one origin row in the caller's transaction (claim, or a finished origin)."""
    stamp = (now or datetime.now(UTC)).isoformat()
    origin_id = f"DDO-{uuid4().hex}"
    try:
        conn.execute(
            f"""INSERT INTO {ORIGINS_TABLE}(origin_id,proposal_id,bid_id,destination_kind,record_id,
            record_detail,claimed_at,claimed_by,released_at) VALUES (?,?,?,?,?,?,?,?,NULL)""",
            (origin_id, proposal_id, bid_id, kind.value, record_id, record_detail, stamp, actor),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError(
            "This finding or record already has an origin. Open the finding again; if it still "
            "shows Pending, run the discovery reconciliation to inspect it."
        ) from exc
    audit(
        conn,
        bid_id,
        actor,
        "deliverable_discovery_origin_claimed",
        f"{proposal_id}:{kind.value}:{record_id or 'pending'}",
    )
    row = conn.execute(f"SELECT * FROM {ORIGINS_TABLE} WHERE origin_id=?", (origin_id,)).fetchone()
    return _origin(row)


def claim_origin(
    conn_factory: Callable[[], sqlite3.Connection],
    audit: AuditWriter,
    *,
    proposal_id: str,
    bid_id: str,
    kind: DestinationKind,
    actor: str,
    now: datetime | None = None,
    lease_seconds: int = ORIGIN_LEASE_SECONDS,
) -> Origin:
    """Claim the right to create this finding's register record.

    Returns the existing origin when the finding already created its record (a safe repeat), and
    a fresh claim otherwise. A live claim held by another request raises ``ValueError``; a claim
    older than ``lease_seconds`` belongs to a request that died and is adopted atomically.
    """
    current_time = now or datetime.now(UTC)
    with conn_factory() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            proposal = conn.execute(
                "SELECT bid_id FROM deliverable_discovery_proposals WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
            if proposal is None or proposal["bid_id"] != bid_id:
                raise ValueError("proposal not found in this Bid")
            existing = active_origin(conn, proposal_id)
            if existing is not None and existing.destination_kind is not kind:
                raise ValueError(
                    "This finding already created a record in another register. Reconcile it "
                    "before changing its lifecycle."
                )
            if existing is not None and existing.record_id is not None:
                conn.commit()
                return existing
            if existing is not None:
                age = current_time - existing.claimed_at
                if age < timedelta(seconds=lease_seconds):
                    raise ValueError(IN_PROGRESS_MESSAGE)
                _release(conn, audit, existing, actor, current_time, "adopted after lease expiry")
            origin = insert_origin(
                conn,
                audit,
                proposal_id=proposal_id,
                bid_id=bid_id,
                kind=kind,
                actor=actor,
                now=current_time,
            )
            conn.commit()
            return origin
        except Exception:
            conn.rollback()
            raise


def _release(
    conn: sqlite3.Connection,
    audit: AuditWriter,
    origin: Origin,
    actor: str,
    now: datetime,
    reason: str,
) -> None:
    changed = conn.execute(
        f"""UPDATE {ORIGINS_TABLE} SET released_at=?
        WHERE origin_id=? AND released_at IS NULL AND record_id IS NULL""",
        (now.isoformat(), origin.origin_id),
    ).rowcount
    if changed != 1:
        raise ValueError(IN_PROGRESS_MESSAGE)
    audit(
        conn,
        origin.bid_id,
        actor,
        "deliverable_discovery_origin_released",
        f"{origin.proposal_id}:{origin.destination_kind.value}:{reason}",
    )


def release_origin(
    conn_factory: Callable[[], sqlite3.Connection],
    audit: AuditWriter,
    origin: Origin,
    actor: str,
    reason: str,
    now: datetime | None = None,
) -> None:
    """Release a claim that has no record, so a retry can claim again immediately."""
    with conn_factory() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _release(conn, audit, origin, actor, now or datetime.now(UTC), reason)
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def set_origin_record(
    conn_factory: Callable[[], sqlite3.Connection],
    audit: AuditWriter,
    origin: Origin,
    record_id: str,
    actor: str,
    record_detail: str | None = None,
) -> Origin:
    """Attach the created record to the claim (idempotent for the same record)."""
    with conn_factory() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                f"SELECT * FROM {ORIGINS_TABLE} WHERE origin_id=?", (origin.origin_id,)
            ).fetchone()
            if row is None or row["released_at"] is not None:
                raise ValueError(
                    "The claim on this finding was released. Accept the finding again."
                )
            if row["record_id"] is not None:
                if row["record_id"] != record_id:
                    raise ValueError(
                        "This finding already created a different record. Reconcile it before "
                        "accepting again."
                    )
                conn.commit()
                return _origin(row)
            conn.execute(
                f"UPDATE {ORIGINS_TABLE} SET record_id=?, record_detail=? "
                "WHERE origin_id=? AND record_id IS NULL AND released_at IS NULL",
                (record_id, record_detail, origin.origin_id),
            )
            audit(
                conn,
                origin.bid_id,
                actor,
                "deliverable_discovery_origin_recorded",
                f"{origin.proposal_id}:{origin.destination_kind.value}:{record_id}",
            )
            row = conn.execute(
                f"SELECT * FROM {ORIGINS_TABLE} WHERE origin_id=?", (origin.origin_id,)
            ).fetchone()
            conn.commit()
            return _origin(row)
        except Exception:
            conn.rollback()
            raise


__all__ = [
    "IN_PROGRESS_MESSAGE",
    "ORIGINS_TABLE",
    "ORIGIN_LEASE_SECONDS",
    "Origin",
    "active_origin",
    "claim_origin",
    "ensure_origin_tables",
    "insert_origin",
    "release_origin",
    "set_origin_record",
]
