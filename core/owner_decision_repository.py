"""Append-only persistence for owner pursuit decisions."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from core.database import Database
from core.enums import BidStatus
from core.owner_decision_evidence import collect_owner_decision_evidence
from core.owner_decisions import OwnerDecision, OwnerDecisionValue
from core.schemas import Provenance


def migrate_owner_decisions(conn: sqlite3.Connection) -> None:
    """Add the immutable owner-decision table and lineage guards idempotently."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bid_owner_decisions(
            decision_id TEXT PRIMARY KEY,
            bid_id TEXT NOT NULL,
            decision TEXT NOT NULL CHECK(decision IN ('PURSUE','DO_NOT_PURSUE')),
            reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 500),
            decided_by TEXT NOT NULL CHECK(length(trim(decided_by)) > 0),
            decided_at TEXT NOT NULL,
            supersedes_decision_id TEXT,
            evidence_snapshot_json TEXT NOT NULL CHECK(json_valid(evidence_snapshot_json)),
            provenance_json TEXT NOT NULL CHECK(json_valid(provenance_json)),
            FOREIGN KEY(bid_id) REFERENCES bids(bid_id),
            FOREIGN KEY(supersedes_decision_id)
                REFERENCES bid_owner_decisions(decision_id)
        );
        CREATE INDEX IF NOT EXISTS idx_owner_decisions_bid
            ON bid_owner_decisions(bid_id,decided_at);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_owner_decision_superseded_once
            ON bid_owner_decisions(supersedes_decision_id)
            WHERE supersedes_decision_id IS NOT NULL;
        CREATE TRIGGER IF NOT EXISTS owner_decisions_immutable_update
            BEFORE UPDATE ON bid_owner_decisions BEGIN
            SELECT RAISE(ABORT,'owner decisions are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS owner_decisions_immutable_delete
            BEFORE DELETE ON bid_owner_decisions BEGIN
            SELECT RAISE(ABORT,'owner decisions are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS owner_decisions_single_current
            BEFORE INSERT ON bid_owner_decisions
            WHEN NEW.supersedes_decision_id IS NULL AND EXISTS(
                SELECT 1 FROM bid_owner_decisions current
                WHERE current.bid_id=NEW.bid_id AND NOT EXISTS(
                    SELECT 1 FROM bid_owner_decisions newer
                    WHERE newer.supersedes_decision_id=current.decision_id))
            BEGIN SELECT RAISE(ABORT,'a current owner decision already exists'); END;
        CREATE TRIGGER IF NOT EXISTS owner_decisions_supersession_scope
            BEFORE INSERT ON bid_owner_decisions
            WHEN NEW.supersedes_decision_id IS NOT NULL AND NOT EXISTS(
                SELECT 1 FROM bid_owner_decisions current
                WHERE current.decision_id=NEW.supersedes_decision_id
                AND current.bid_id=NEW.bid_id AND NOT EXISTS(
                    SELECT 1 FROM bid_owner_decisions newer
                    WHERE newer.supersedes_decision_id=current.decision_id))
            BEGIN SELECT RAISE(ABORT,'owner decision predecessor is not current for this Bid'); END;
        """
    )


class OwnerDecisionRepository:
    """Read and atomically append immutable owner pursuit decisions."""

    def __init__(self, db: Database) -> None:
        self.db = db
        with self._conn() as conn:
            migrate_owner_decisions(conn)

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    @staticmethod
    def _from_row(row: sqlite3.Row) -> OwnerDecision:
        return OwnerDecision(
            decision_id=str(row["decision_id"]),
            bid_id=str(row["bid_id"]),
            decision=OwnerDecisionValue(str(row["decision"])),
            reason=str(row["reason"]),
            decided_by=str(row["decided_by"]),
            decided_at=datetime.fromisoformat(str(row["decided_at"])),
            supersedes_decision_id=(
                str(row["supersedes_decision_id"])
                if row["supersedes_decision_id"] is not None
                else None
            ),
            evidence_snapshot=json.loads(str(row["evidence_snapshot_json"])),
            provenance=Provenance.model_validate_json(str(row["provenance_json"])),
        )

    def get_current(self, bid_id: str) -> OwnerDecision | None:
        """Return the unsuperseded decision for a Bid, or its legacy default of none."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM bid_owner_decisions current WHERE current.bid_id=?
                AND NOT EXISTS(SELECT 1 FROM bid_owner_decisions newer
                    WHERE newer.supersedes_decision_id=current.decision_id)
                ORDER BY current.decided_at DESC,current.decision_id DESC LIMIT 1""",
                (bid_id,),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def list_history(self, bid_id: str) -> list[OwnerDecision]:
        """Return every immutable decision in recording order."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM bid_owner_decisions WHERE bid_id=? ORDER BY decided_at,decision_id",
                (bid_id,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _has_issue_baseline(conn: sqlite3.Connection, bid_id: str) -> bool:
        table = conn.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='issued_offer_baselines'"
        ).fetchone()
        if table is None:
            return False
        return (
            conn.execute(
                "SELECT 1 FROM issued_offer_baselines WHERE bid_id=? LIMIT 1", (bid_id,)
            ).fetchone()
            is not None
        )

    def record(
        self,
        bid_id: str,
        decision: OwnerDecisionValue,
        reason: str,
        actor: str,
        expected_current_decision_id: str | None,
    ) -> OwnerDecision:
        """Append decision, audit, and any status transition in one transaction."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                bid = conn.execute("SELECT status FROM bids WHERE bid_id=?", (bid_id,)).fetchone()
                if bid is None:
                    raise ValueError(f"Bid {bid_id} does not exist; create the Bid before deciding")
                current_row = conn.execute(
                    """SELECT * FROM bid_owner_decisions current WHERE current.bid_id=?
                    AND NOT EXISTS(SELECT 1 FROM bid_owner_decisions newer
                        WHERE newer.supersedes_decision_id=current.decision_id)
                    LIMIT 1""",
                    (bid_id,),
                ).fetchone()
                current = self._from_row(current_row) if current_row is not None else None
                if current is not None and current.decision is decision:
                    raise ValueError(
                        f"{decision.value} is already the current decision; "
                        "choose the other outcome"
                    )
                if current is None and expected_current_decision_id is not None:
                    raise ValueError(
                        "No current owner decision exists; refresh and record without a predecessor"
                    )
                if current is not None and expected_current_decision_id != current.decision_id:
                    raise ValueError(
                        "The owner decision changed since the page was loaded; "
                        "refresh and try again"
                    )

                status = BidStatus(str(bid["status"]))
                if status in {BidStatus.SUBMITTED, BidStatus.WON, BidStatus.LOST}:
                    raise ValueError(
                        f"A {status.value} Bid cannot change owner decision; "
                        "leave its status unchanged"
                    )
                if current is not None and self._has_issue_baseline(conn, bid_id):
                    raise ValueError(
                        "A Bid with a customer issue baseline cannot change owner decision; "
                        "preserve the issued evidence"
                    )

                decided_at = datetime.now(UTC)
                recorded = OwnerDecision(
                    decision_id=f"OD-{uuid4().hex}",
                    bid_id=bid_id,
                    decision=decision,
                    reason=reason,
                    decided_by=actor,
                    decided_at=decided_at,
                    supersedes_decision_id=current.decision_id if current is not None else None,
                    evidence_snapshot=collect_owner_decision_evidence(conn, bid_id),
                    provenance=Provenance.from_human(actor),
                )
                conn.execute(
                    """INSERT INTO bid_owner_decisions(
                    decision_id,bid_id,decision,reason,decided_by,decided_at,
                    supersedes_decision_id,evidence_snapshot_json,provenance_json)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        recorded.decision_id,
                        recorded.bid_id,
                        recorded.decision.value,
                        recorded.reason,
                        recorded.decided_by,
                        recorded.decided_at.isoformat(),
                        recorded.supersedes_decision_id,
                        json.dumps(
                            recorded.evidence_snapshot.model_dump(mode="json"), sort_keys=True
                        ),
                        recorded.provenance.model_dump_json(),
                    ),
                )
                new_status = None
                if decision is OwnerDecisionValue.DO_NOT_PURSUE:
                    new_status = BidStatus.NO_BID
                elif current is not None and current.decision is OwnerDecisionValue.DO_NOT_PURSUE:
                    new_status = BidStatus.ACTIVE
                if new_status is not None:
                    conn.execute(
                        "UPDATE bids SET status=?,updated_at=? WHERE bid_id=?",
                        (new_status.value, decided_at.isoformat(), bid_id),
                    )
                conn.execute(
                    "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        f"AUD-{uuid4().hex}",
                        bid_id,
                        actor,
                        "owner_pursuit_decision_recorded",
                        json.dumps(
                            {
                                "decision": decision.value,
                                "decision_id": recorded.decision_id,
                                "reason": recorded.reason,
                                "supersedes_decision_id": recorded.supersedes_decision_id,
                            },
                            sort_keys=True,
                        ),
                        decided_at.isoformat(),
                    ),
                )
                conn.commit()
                return recorded
            except Exception:
                conn.rollback()
                raise


__all__ = ["OwnerDecisionRepository", "migrate_owner_decisions"]
