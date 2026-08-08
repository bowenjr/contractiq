"""SQLite proposal versions, reviews, render metadata, and baseline lineage."""
# DDL and compact SQL are intentionally kept together.
# ruff: noqa: E501

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from core.approval_repository import ApprovalRepository
from core.database import Database
from core.proposals import (
    ProposalFamily,
    ProposalProfile,
    ProposalReview,
    ProposalVersion,
    RenderArtifact,
)

PROPOSAL_MIGRATION_ID = "task_18_proposal_production_v1"


class ProposalRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        ApprovalRepository(db)
        self._migrate()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _migrate(self) -> None:
        ddl = [
            "CREATE TABLE IF NOT EXISTS proposal_profiles(profile_id TEXT PRIMARY KEY,code TEXT NOT NULL,name TEXT NOT NULL,effective_from TEXT NOT NULL,effective_until TEXT,required_sections_json TEXT NOT NULL,published INTEGER NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(code,effective_from))",
            "CREATE TABLE IF NOT EXISTS proposal_families(family_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,code TEXT NOT NULL,applicability TEXT NOT NULL,title TEXT NOT NULL,owner TEXT NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(bid_id,code))",
            "CREATE TABLE IF NOT EXISTS proposal_versions(proposal_version_id TEXT PRIMARY KEY,family_id TEXT NOT NULL,bid_id TEXT NOT NULL,version_number INTEGER NOT NULL,lifecycle TEXT NOT NULL,profile_id TEXT NOT NULL,presentation_currency TEXT NOT NULL,sections_json TEXT NOT NULL,source_manifest_json TEXT NOT NULL,commercial_baseline_id TEXT,negotiated_position_id TEXT,created_by TEXT NOT NULL,created_at TEXT NOT NULL,fingerprint TEXT NOT NULL,UNIQUE(family_id,version_number))",
            "CREATE TABLE IF NOT EXISTS proposal_reviews(review_id TEXT PRIMARY KEY,proposal_version_id TEXT NOT NULL,reviewer TEXT NOT NULL,decision TEXT NOT NULL,rationale TEXT NOT NULL,reviewed_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS proposal_artifacts(artifact_id TEXT PRIMARY KEY,proposal_version_id TEXT NOT NULL,format TEXT NOT NULL,relative_path TEXT NOT NULL,media_type TEXT NOT NULL,byte_size INTEGER NOT NULL,sha256 TEXT NOT NULL,verified INTEGER NOT NULL)",
            "CREATE TABLE IF NOT EXISTS proposal_baselines(selection_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,proposal_version_id TEXT NOT NULL,selected_by TEXT NOT NULL,selected_at TEXT NOT NULL,rationale TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS proposal_schema_migrations(migration_id TEXT PRIMARY KEY,applied_at TEXT NOT NULL)",
            "CREATE TRIGGER IF NOT EXISTS proposal_version_immutable BEFORE UPDATE ON proposal_versions BEGIN SELECT RAISE(ABORT,'proposal versions are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS proposal_version_no_delete BEFORE DELETE ON proposal_versions BEGIN SELECT RAISE(ABORT,'proposal versions cannot be deleted'); END",
            "CREATE TRIGGER IF NOT EXISTS proposal_artifact_immutable BEFORE UPDATE ON proposal_artifacts BEGIN SELECT RAISE(ABORT,'proposal artifacts are immutable'); END",
            "CREATE TRIGGER IF NOT EXISTS proposal_artifact_no_delete BEFORE DELETE ON proposal_artifacts BEGIN SELECT RAISE(ABORT,'proposal artifacts cannot be deleted'); END",
        ]
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in ddl:
                    conn.execute(statement)
                conn.execute(
                    "INSERT OR IGNORE INTO proposal_schema_migrations VALUES(?,?)",
                    (PROPOSAL_MIGRATION_ID, datetime.now(UTC).isoformat()),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _audit(
        self, conn: sqlite3.Connection, bid_id: str, actor: str, action: str, detail: str
    ) -> None:
        conn.execute(
            "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) VALUES(?,?,?,?,?,?)",
            (f"AUD-{uuid4().hex}", bid_id, actor, action, detail, datetime.now(UTC).isoformat()),
        )

    def create_profile(self, value: ProposalProfile, actor: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO proposal_profiles VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    value.profile_id,
                    value.code,
                    value.name,
                    value.effective_from.isoformat(),
                    value.effective_until.isoformat() if value.effective_until else None,
                    json.dumps([item.value for item in value.required_sections]),
                    int(value.published),
                    value.created_by,
                    value.created_at.isoformat(),
                ),
            )
            self._audit(conn, "", actor, "proposal_profile_created", value.profile_id)
            conn.commit()

    def create_family(self, value: ProposalFamily, actor: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO proposal_families VALUES(?,?,?,?,?,?,?,?)",
                (*value.model_dump(mode="json").values(),),
            )
            self._audit(conn, value.bid_id, actor, "proposal_family_created", value.family_id)
            conn.commit()

    def add_version(self, value: ProposalVersion, actor: str) -> None:
        with self._conn() as conn:
            expected = conn.execute(
                "SELECT COALESCE(MAX(version_number),0)+1 FROM proposal_versions WHERE family_id=?",
                (value.family_id,),
            ).fetchone()[0]
            if value.version_number != expected:
                raise ValueError("proposal version must be monotonic")
            conn.execute(
                "INSERT INTO proposal_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    value.proposal_version_id,
                    value.family_id,
                    value.bid_id,
                    value.version_number,
                    value.lifecycle.value,
                    value.profile_id,
                    value.presentation_currency,
                    json.dumps(
                        [section.model_dump(mode="json") for section in value.sections],
                        sort_keys=True,
                    ),
                    json.dumps(value.source_manifest, sort_keys=True),
                    value.commercial_baseline_id,
                    value.negotiated_position_id,
                    value.created_by,
                    value.created_at.isoformat(),
                    value.fingerprint,
                ),
            )
            self._audit(
                conn, value.bid_id, actor, "proposal_version_created", value.proposal_version_id
            )
            conn.commit()

    def add_review(self, value: ProposalReview, bid_id: str, actor: str) -> None:
        if value.reviewer == actor:
            raise ValueError("self-review is not permitted")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO proposal_reviews VALUES(?,?,?,?,?,?)",
                (
                    value.review_id,
                    value.proposal_version_id,
                    value.reviewer,
                    value.decision,
                    value.rationale,
                    value.reviewed_at.isoformat(),
                ),
            )
            self._audit(conn, bid_id, actor, "proposal_review_recorded", value.review_id)
            conn.commit()

    def add_artifacts(self, values: tuple[RenderArtifact, ...], bid_id: str, actor: str) -> None:
        with self._conn() as conn:
            for value in values:
                conn.execute(
                    "INSERT INTO proposal_artifacts VALUES(?,?,?,?,?,?,?,?)",
                    (
                        value.artifact_id,
                        value.proposal_version_id,
                        value.format,
                        value.relative_path,
                        value.media_type,
                        value.byte_size,
                        value.sha256,
                        int(value.verified),
                    ),
                )
            self._audit(
                conn, bid_id, actor, "proposal_artifacts_rendered", values[0].proposal_version_id
            )
            conn.commit()

    def select_baseline(
        self,
        bid_id: str,
        proposal_version_id: str,
        actor: str,
        rationale: str,
        selected_at: datetime,
    ) -> None:
        with self._conn() as conn:
            if (
                conn.execute(
                    "SELECT 1 FROM proposal_reviews WHERE proposal_version_id=? AND decision='ACCEPTED'",
                    (proposal_version_id,),
                ).fetchone()
                is None
            ):
                raise ValueError("baseline requires independent review")
            if (
                conn.execute(
                    "SELECT 1 FROM proposal_artifacts WHERE proposal_version_id=? AND verified=1 GROUP BY proposal_version_id HAVING COUNT(*)=4",
                    (proposal_version_id,),
                ).fetchone()
                is None
            ):
                raise ValueError("baseline requires four verified artifacts")
            conn.execute(
                "INSERT INTO proposal_baselines VALUES(?,?,?,?,?,?)",
                (
                    f"PBL-{uuid4().hex}",
                    bid_id,
                    proposal_version_id,
                    actor,
                    selected_at.isoformat(),
                    rationale,
                ),
            )
            self._audit(conn, bid_id, actor, "proposal_baseline_selected", proposal_version_id)
            conn.commit()

    def families(self, bid_id: str | None = None) -> list[dict[str, Any]]:
        clause = " WHERE bid_id=?" if bid_id else ""
        with self._conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM proposal_families" + clause, (bid_id,) if bid_id else ()
                ).fetchall()
            ]

    def metrics(self, bid_id: str | None = None) -> dict[str, int]:
        clause = " WHERE bid_id=?" if bid_id else ""
        args = (bid_id,) if bid_id else ()
        with self._conn() as conn:
            return {
                "families_total": len(self.families(bid_id)),
                "versions_total": conn.execute(
                    "SELECT COUNT(*) FROM proposal_versions" + clause, args
                ).fetchone()[0],
                "reviews_total": conn.execute("SELECT COUNT(*) FROM proposal_reviews").fetchone()[
                    0
                ],
                "artifacts_total": conn.execute(
                    "SELECT COUNT(*) FROM proposal_artifacts"
                ).fetchone()[0],
                "baselines_total": conn.execute(
                    "SELECT COUNT(*) FROM proposal_baselines" + clause, args
                ).fetchone()[0],
            }
