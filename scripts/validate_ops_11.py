"""Validate OPS-11 schema evidence and the socketless synthetic workflow."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.approval_repository import ApprovalRepository  # noqa: E402
from core.bid_package_repository import (  # noqa: E402
    OPS_11_MIGRATION_ID,
    BidPackageRepository,
)
from core.bid_repository import BidRepository  # noqa: E402
from core.database import Database  # noqa: E402
from core.document_repository import DocumentRepository  # noqa: E402
from core.work_item_repository import WorkItemRepository  # noqa: E402
from scripts.asgi_acceptance_ops11 import main as acceptance_main  # noqa: E402
from scripts.asgi_acceptance_ops11by import main as simplification_main  # noqa: E402


def validate_schema() -> None:
    with tempfile.TemporaryDirectory(prefix="contractiq-ops11-schema-") as directory:
        database = Database(Path(directory) / "validation.db")
        BidRepository(database)
        DocumentRepository(database)
        WorkItemRepository(database)
        ApprovalRepository(database)
        repository = BidPackageRepository(database)
        BidPackageRepository(database)
        with database._conn() as conn:
            actual = {
                str(row["name"])
                for row in conn.execute(
                    """SELECT name FROM sqlite_master WHERE type='table'
                    AND name LIKE 'bid_%'"""
                ).fetchall()
                if str(row["name"]) in repository.migration_tables()
            }
            assert actual == set(repository.migration_tables())
            migrations = conn.execute(
                "SELECT migration_id FROM bid_package_intake_schema_migrations"
            ).fetchall()
            assert [row["migration_id"] for row in migrations] == [OPS_11_MIGRATION_ID]
            trigger_count = conn.execute(
                """SELECT count(*) FROM sqlite_master WHERE type='trigger'
                AND (name LIKE 'bid_%_immutable' OR name LIKE 'bid_%_no_delete')"""
            ).fetchone()[0]
            assert int(trigger_count) == 36
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            conn.execute("BEGIN")
            conn.execute("ROLLBACK")
            assert isinstance(conn, sqlite3.Connection)


def main() -> None:
    validate_schema()
    measurements = asyncio.run(acceptance_main())
    assert measurements["user_actions"] == 11
    simplification = asyncio.run(simplification_main())
    assert simplification["re_uploads_of_held_files"] == 0
    assert simplification["receipt_times_typed"] == 0
    assert simplification["record_identifiers_shown"] == 0
    assert simplification["get_mutations"] == 0
    assert simplification["source_mutations"] == 0
    print("OPS-11 validation: PASS")


if __name__ == "__main__":
    main()
