"""Report (and optionally repair) register records left by an interrupted discovery acceptance.

Usage:
    python scripts/reconcile_discovery_origins.py --db PATH [--bid BID_ID] [--apply] [--actor NAME]

Without ``--apply`` nothing is changed. With it, only the narrow repairs described in
``core.deliverable_discovery_reconcile`` are made; a review decision is never written or changed
and no register record is edited or deleted. Point ``--db`` at the database you mean to inspect;
the script never defaults to the live database.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import Database  # noqa: E402
from core.deliverable_discovery_reconcile import reconcile_origins  # noqa: E402
from core.deliverable_discovery_repository import DeliverableDiscoveryRepository  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", required=True, type=Path, help="SQLite database file to inspect")
    parser.add_argument("--bid", help="Bid id; default is every Bid with discovery findings")
    parser.add_argument(
        "--apply", action="store_true", help="make the narrow repairs (default: no)"
    )
    parser.add_argument("--actor", default="reconciliation", help="actor recorded in audit rows")
    args = parser.parse_args(argv)
    if not args.db.is_file():
        print(f"Database not found: {args.db}. Pass the path of an existing database.")
        return 2
    repository = DeliverableDiscoveryRepository(Database(args.db))
    with repository._conn() as conn:
        bids = (
            [args.bid]
            if args.bid
            else [
                str(r[0])
                for r in conn.execute("SELECT DISTINCT bid_id FROM deliverable_discovery_proposals")
            ]
        )
    exit_code = 0
    for bid_id in bids:
        report = reconcile_origins(
            repository._conn, repository._audit, bid_id, apply=args.apply, actor=args.actor
        )
        print(report.as_text())
        if report.items and not args.apply:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
