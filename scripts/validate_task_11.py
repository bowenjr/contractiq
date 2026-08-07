"""Deterministic, isolated TASK-11 validation smoke."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from core.database import Database
from core.supplier_repository import SupplierRepository


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="contractiq-task11-") as directory:
        db = Database(Path(directory) / "validation.db")
        SupplierRepository(db)
        with sqlite3.connect(db.db_path) as connection:
            names = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','trigger')"
                )
            }
        required = {
            "bid_suppliers",
            "supplier_requests",
            "supplier_request_items",
            "supplier_responses",
            "supplier_response_versions",
            "supplier_response_coverage",
            "supplier_no_delete",
            "supplier_version_no_delete",
            "supplier_item_flow_down",
            "supplier_schema_migrations",
        }
        if not required <= names:
            missing = ", ".join(sorted(required - names))
            raise SystemExit(f"missing TASK-11 schema objects: {missing}")
    print("TASK-11 validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
