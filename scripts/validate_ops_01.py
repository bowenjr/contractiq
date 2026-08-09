"""Deterministic OPS-01 smoke and invariant validation."""

from __future__ import annotations

import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

from core.bid_repository import BidRepository
from core.database import Database
from core.ops_foundation import (
    OPS_MIGRATION_ID,
    RESPONSIBILITY_DOMAINS,
    WORK_CATEGORIES,
    OpsFoundationRepository,
)
from core.work_item_repository import WorkItemRepository
from core.work_item_service import WorkItemService


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="contractiq-ops01-") as directory:
        db = Database(Path(directory) / "ops.db")
        bids = BidRepository(db)
        work = WorkItemRepository(db)
        service = WorkItemService(work, bids, now_factory=lambda: datetime(2026, 8, 9, tzinfo=UTC))
        ops = OpsFoundationRepository(db)
        with db._conn() as conn:
            assert conn.execute(
                "SELECT 1 FROM ops_schema_migrations WHERE migration_id=?", (OPS_MIGRATION_ID,)
            ).fetchone()
            assert conn.execute("SELECT COUNT(*) FROM ops_role_profiles").fetchone()[0] == 0
        assert len(RESPONSIBILITY_DOMAINS) == 13 and len(WORK_CATEGORIES) == 18
        item = service.create_work_item(
            {
                "title": "Synthetic quick capture",
                "category": "CUSTOMER_REQUEST",
                "next_action_date": "2026-08-09",
            },
            "synthetic-actor",
        )
        assert item.bid_id is None and item.category.value == "CUSTOMER_REQUEST"
        assert work.get(item.work_item_id) == item
        profile_id = ops.create_profile(
            {
                "version_number": 1,
                "mission": "Synthetic mission",
                "boundaries": "Synthetic boundaries",
                "coordination": "Synthetic coordination",
                "outcomes": "Synthetic outcomes",
                "cadence": "Synthetic cadence",
                "domains": [RESPONSIBILITY_DOMAINS[0]],
                "effective_from": "2026-01-01",
                "created_by": "synthetic",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        )
        ops.publish_profile(profile_id)
        assert ops.effective_profile(date(2026, 8, 9)) is not None
        try:
            ops.create_profile(
                {
                    "version_number": 2,
                    "mission": "Overlap",
                    "boundaries": "",
                    "coordination": "",
                    "outcomes": "",
                    "cadence": "",
                    "domains": [],
                    "effective_from": "2026-06-01",
                    "created_by": "synthetic",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "state": "PUBLISHED",
                }
            )
        except ValueError:
            pass
        else:
            raise AssertionError("overlapping profile accepted")
        try:
            service.create_work_item({"title": "bad", "category": "UNKNOWN"}, "synthetic-actor")
        except ValueError:
            pass
        else:
            raise AssertionError("unknown category accepted")
        before = conn_count(db, "audit_log")
        assert ops.metrics()["unassigned_work"] == 1
        assert conn_count(db, "audit_log") == before
    print("OPS-01 validation: PASS")


def conn_count(db: Database, table: str) -> int:
    with db._conn() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


if __name__ == "__main__":
    main()
