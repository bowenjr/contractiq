"""Additive schema migrations shared by discovery repository startup paths."""

from __future__ import annotations

import sqlite3

NOT_ATTEMPTED_MIGRATION_ID = "ops_13a_deliverable_discovery_not_attempted_v6"

_TABLE = "deliverable_discovery_file_outcomes"
_COLUMNS = (
    "run_id",
    "received_file_id",
    "outcome",
    "passages_total",
    "model_calls",
    "passages_analysed",
    "passages_model_failed",
    "passages_invalid_output",
    "detail",
    "created_at",
)
_CREATE_TABLE = f"""CREATE TABLE {_TABLE} (
    run_id TEXT NOT NULL, received_file_id TEXT NOT NULL, outcome TEXT NOT NULL,
    passages_total INTEGER NOT NULL, model_calls INTEGER NOT NULL,
    passages_analysed INTEGER NOT NULL, passages_model_failed INTEGER NOT NULL,
    passages_invalid_output INTEGER NOT NULL, detail TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY (run_id,received_file_id),
    FOREIGN KEY (run_id) REFERENCES deliverable_discovery_runs(run_id),
    FOREIGN KEY (received_file_id) REFERENCES bid_received_files(file_id),
    CHECK (outcome IN ('UNSUPPORTED_TYPE','UNREADABLE','EXTRACTED_NO_TEXT',
        'NOT_ATTEMPTED','MODEL_UNAVAILABLE','INVALID_MODEL_OUTPUT','PARTIALLY_ANALYSED',
        'ANALYSED_CANDIDATES','ANALYSED_ZERO_CANDIDATES'))
)"""
_TRIGGERS = (
    f"""CREATE TRIGGER IF NOT EXISTS ddfo_v4_no_update BEFORE UPDATE ON {_TABLE} BEGIN
    SELECT RAISE(ABORT,'discovery file outcomes are immutable'); END""",
    f"""CREATE TRIGGER IF NOT EXISTS ddfo_v4_no_delete BEFORE DELETE ON {_TABLE} BEGIN
    SELECT RAISE(ABORT,'discovery file outcomes cannot be deleted'); END""",
)


def migrate_deliverable_discovery_not_attempted(conn: sqlite3.Connection, applied_at: str) -> None:
    """Apply v6 inside the caller's transaction, preserving every v4 row verbatim."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS deliverable_discovery_schema_migrations(
        migration_id TEXT PRIMARY KEY,applied_at TEXT NOT NULL)"""
    )
    applied = conn.execute(
        "SELECT 1 FROM deliverable_discovery_schema_migrations WHERE migration_id=?",
        (NOT_ATTEMPTED_MIGRATION_ID,),
    ).fetchone()
    if applied is not None:
        return

    existing = conn.execute(
        "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (_TABLE,)
    ).fetchone()
    if existing is None:
        conn.execute(_CREATE_TABLE)
    elif "'NOT_ATTEMPTED'" not in str(existing[0]):
        columns = tuple(str(row[1]) for row in conn.execute(f"PRAGMA table_info({_TABLE})"))
        if columns != _COLUMNS:
            raise ValueError("unexpected deliverable discovery file outcome schema")
        old_table = f"{_TABLE}_v6_old"
        if conn.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (old_table,)
        ).fetchone():
            raise ValueError("unfinished deliverable discovery v6 migration found")
        conn.execute("DROP TRIGGER IF EXISTS ddfo_v4_no_update")
        conn.execute("DROP TRIGGER IF EXISTS ddfo_v4_no_delete")
        conn.execute(f"ALTER TABLE {_TABLE} RENAME TO {old_table}")
        conn.execute(_CREATE_TABLE)
        column_list = ",".join(_COLUMNS)
        conn.execute(f"INSERT INTO {_TABLE}({column_list}) SELECT {column_list} FROM {old_table}")
        conn.execute(f"DROP TABLE {old_table}")

    for statement in _TRIGGERS:
        conn.execute(statement)
    conn.execute(
        "INSERT INTO deliverable_discovery_schema_migrations VALUES (?,?)",
        (NOT_ATTEMPTED_MIGRATION_ID, applied_at),
    )


__all__ = ["NOT_ATTEMPTED_MIGRATION_ID", "migrate_deliverable_discovery_not_attempted"]
