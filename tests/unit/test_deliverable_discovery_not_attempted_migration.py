"""Fresh and v4/v5 upgrade coverage for the additive discovery v6 migration."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.deliverable_discovery_job_repository import DeliverableDiscoveryJobRepository
from core.deliverable_discovery_migrations import NOT_ATTEMPTED_MIGRATION_ID
from tests.unit.test_bid_package_storage import BID_ID
from tests.unit.test_deliverable_discovery_run_outcomes import FakeClient, _fixture

_OLD_TABLE = """CREATE TABLE deliverable_discovery_file_outcomes_v4 (
    run_id TEXT NOT NULL, received_file_id TEXT NOT NULL, outcome TEXT NOT NULL,
    passages_total INTEGER NOT NULL, model_calls INTEGER NOT NULL,
    passages_analysed INTEGER NOT NULL, passages_model_failed INTEGER NOT NULL,
    passages_invalid_output INTEGER NOT NULL, detail TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY (run_id,received_file_id),
    FOREIGN KEY (run_id) REFERENCES deliverable_discovery_runs(run_id),
    FOREIGN KEY (received_file_id) REFERENCES bid_received_files(file_id),
    CHECK (outcome IN ('UNSUPPORTED_TYPE','UNREADABLE','EXTRACTED_NO_TEXT',
        'MODEL_UNAVAILABLE','INVALID_MODEL_OUTPUT','PARTIALLY_ANALYSED',
        'ANALYSED_CANDIDATES','ANALYSED_ZERO_CANDIDATES'))
)"""


def _downgrade_to_v5(database: object) -> tuple[object, ...]:
    with database._conn() as conn:  # type: ignore[attr-defined]
        original = tuple(
            conn.execute("SELECT * FROM deliverable_discovery_file_outcomes").fetchone()
        )
        conn.execute("DROP TRIGGER ddfo_v4_no_update")
        conn.execute("DROP TRIGGER ddfo_v4_no_delete")
        conn.execute(_OLD_TABLE)
        conn.execute(
            "INSERT INTO deliverable_discovery_file_outcomes_v4 "
            "SELECT * FROM deliverable_discovery_file_outcomes"
        )
        conn.execute("DROP TABLE deliverable_discovery_file_outcomes")
        conn.execute(
            "ALTER TABLE deliverable_discovery_file_outcomes_v4 "
            "RENAME TO deliverable_discovery_file_outcomes"
        )
        conn.execute(
            "DELETE FROM deliverable_discovery_schema_migrations WHERE migration_id=?",
            (NOT_ATTEMPTED_MIGRATION_ID,),
        )
    return original


def test_fresh_database_has_v6_vocabulary_and_migration_record(tmp_path: Path) -> None:
    fx = _fixture(tmp_path, {"notes.txt": b"notes"}, FakeClient())
    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        sql = conn.execute(
            "SELECT sql FROM sqlite_schema WHERE name='deliverable_discovery_file_outcomes'"
        ).fetchone()[0]
        applied = conn.execute(
            "SELECT count(*) FROM deliverable_discovery_schema_migrations WHERE migration_id=?",
            (NOT_ATTEMPTED_MIGRATION_ID,),
        ).fetchone()[0]
    assert "NOT_ATTEMPTED" in sql
    assert applied == 1


def test_job_repository_upgrades_v4_v5_rows_and_remains_idempotent(tmp_path: Path) -> None:
    fx = _fixture(tmp_path, {"notes.txt": b"notes"}, FakeClient())
    result = fx["service"].run_discovery(BID_ID, "old-row", "Jason")  # type: ignore[union-attr]
    original = _downgrade_to_v5(fx["database"])

    DeliverableDiscoveryJobRepository(fx["database"])  # type: ignore[arg-type]
    DeliverableDiscoveryJobRepository(fx["database"])  # type: ignore[arg-type]

    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        preserved = tuple(
            conn.execute(
                "SELECT * FROM deliverable_discovery_file_outcomes WHERE run_id=?",
                (result["run_id"],),
            ).fetchone()
        )
        triggers = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type='trigger' AND name LIKE 'ddfo_v4_%'"
            )
        }
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE deliverable_discovery_file_outcomes SET detail='changed' WHERE run_id=?",
                (result["run_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute(
                "DELETE FROM deliverable_discovery_file_outcomes WHERE run_id=?",
                (result["run_id"],),
            )
        received_file_id = original[1]
        conn.execute(
            "INSERT INTO deliverable_discovery_runs VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "migration-test-run",
                BID_ID,
                None,
                "migration-test-operation",
                "FAILED",
                0,
                0,
                "Jason",
                "2026-09-30T00:00:00+00:00",
                "2026-09-30T00:00:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO deliverable_discovery_file_outcomes VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "migration-test-run",
                received_file_id,
                "NOT_ATTEMPTED",
                1,
                0,
                0,
                0,
                0,
                "not attempted",
                "2026-09-30T00:00:00+00:00",
            ),
        )
    assert preserved == original
    assert triggers == {"ddfo_v4_no_update", "ddfo_v4_no_delete"}
