"""Isolated deterministic OPS-08 acceptance validator."""

from pathlib import Path
from tempfile import TemporaryDirectory

from core.database import Database
from core.ops08 import MIGRATION_ID, Ops08Repository, PositionRevision


def main() -> None:
    with TemporaryDirectory() as directory:
        db = Database(Path(directory) / "ops08.db")
        repository = Ops08Repository(db)
        assert repository is not None
        with db._conn() as connection:
            assert (
                connection.execute("SELECT migration_id FROM ops08_schema_migrations").fetchone()[0]
                == MIGRATION_ID
            )
            assert connection.execute("SELECT count(*) FROM commercial_topics").fetchone()[0] >= 30
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert PositionRevision().disposition.value == "NOT_REVIEWED"
    print("OPS-08 validation: PASS")


if __name__ == "__main__":
    main()
