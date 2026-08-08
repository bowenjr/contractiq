"""Synthetic TASK-18 proposal composition, rendering, and baseline oracle."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.database import Database
from core.proposal_repository import PROPOSAL_MIGRATION_ID, ProposalRepository
from core.proposal_service import ProposalService
from core.proposals import (
    ContentOrigin,
    ProposalApplicability,
    ProposalFamily,
    ProposalProfile,
    ProposalReview,
    ProposalSection,
    ProposalVersion,
    SectionRole,
)


def main() -> None:
    now = datetime(2026, 1, 2, tzinfo=UTC)
    with tempfile.TemporaryDirectory(prefix="contractiq-task18-") as root:
        db = Database(Path(root) / "proposal.db")
        repo = ProposalRepository(db)
        service = ProposalService(repo, Path(root) / "artifacts")
        with db._conn() as conn:
            assert conn.execute(
                "SELECT 1 FROM proposal_schema_migrations WHERE migration_id=?",
                (PROPOSAL_MIGRATION_ID,),
            ).fetchone()
        profile = ProposalProfile(
            code="SYNTHETIC",
            name="Synthetic profile",
            effective_from=now - timedelta(minutes=1),
            required_sections=(SectionRole.COVER, SectionRole.PRICING),
            published=True,
            created_by="synthetic",
            created_at=now,
        )
        service.create_profile(profile, "synthetic")
        family = ProposalFamily(
            bid_id="B-SYNTH-18",
            code="OFFER-SYNTH",
            applicability=ProposalApplicability.PROPOSAL_REQUIRED,
            title="Synthetic offer",
            owner="synthetic",
            created_by="synthetic",
            created_at=now,
        )
        service.create_family(family, "synthetic")
        version = ProposalVersion(
            family_id=family.family_id,
            bid_id=family.bid_id,
            version_number=1,
            profile_id=profile.profile_id,
            presentation_currency="CAD",
            sections=(
                ProposalSection(
                    role=SectionRole.COVER,
                    heading="Synthetic Offer",
                    text="Synthetic customer-facing offer",
                    origin=ContentOrigin.OPERATOR_AUTHORED,
                ),
                ProposalSection(
                    role=SectionRole.PRICING,
                    heading="Price",
                    text="CAD 100.00",
                    origin=ContentOrigin.CALCULATED_PRESENTATION,
                    source_ids=("TASK16-SYNTH",),
                ),
            ),
            commercial_baseline_id="TASK16-SYNTH",
            created_by="synthetic",
            created_at=now,
        )
        service.add_version(version, "synthetic")
        artifacts = service.render(version, "synthetic")
        assert len(artifacts) == 4 and all(
            item.verified and item.byte_size > 0 for item in artifacts
        )
        try:
            service.review(
                ProposalReview(
                    proposal_version_id=version.proposal_version_id,
                    reviewer="synthetic",
                    decision="ACCEPTED",
                    rationale="self",
                    reviewed_at=now,
                ),
                family.bid_id,
                "synthetic",
            )
        except ValueError:
            pass
        else:
            raise AssertionError("self review was accepted")
        service.review(
            ProposalReview(
                proposal_version_id=version.proposal_version_id,
                reviewer="synthetic-reviewer",
                decision="ACCEPTED",
                rationale="Independent synthetic review",
                reviewed_at=now,
            ),
            family.bid_id,
            "synthetic-operator",
        )
        try:
            with db._conn() as conn:
                conn.execute(
                    "DELETE FROM proposal_versions WHERE proposal_version_id=?",
                    (version.proposal_version_id,),
                )
        except sqlite3.DatabaseError:
            pass
        else:
            raise AssertionError("proposal version deletion was permitted")
    print("TASK-18 validation: PASS")


if __name__ == "__main__":
    main()
