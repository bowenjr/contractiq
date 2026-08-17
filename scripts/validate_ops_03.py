"""Deterministic OPS-03 role-profile lifecycle validation."""

from __future__ import annotations

import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

from core.bid_repository import BidRepository
from core.database import Database
from core.ops_foundation import (
    OpsFoundationRepository,
    RoleProfileLifecycleError,
    StaleRoleProfileError,
)
from core.role_profile_service import RoleProfileService
from core.role_profiles import RoleProfileState

NOW = datetime(2026, 8, 17, 14, tzinfo=UTC)


def complete(title: str, start: str) -> dict[str, object]:
    return {
        "title": title,
        "mission": "Deliver controlled bids and contracts outcomes.",
        "boundaries": "No silent commercial authority.",
        "coordination": "Coordinate sales, suppliers, and governance.",
        "outcomes": "Auditable decisions and complete offers.",
        "cadence": "Daily attention and weekly portfolio review.",
        "domains": ["CUSTOMER_SOLUTION", "RISK_ASSURANCE"],
        "effective_from": start,
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="contractiq-ops03-") as directory:
        database = Database(Path(directory) / "ops03.db")
        bids = BidRepository(database)
        repository = OpsFoundationRepository(database)
        ids = iter(UUID(int=value) for value in range(1, 200))
        service = RoleProfileService(
            repository,
            now_factory=lambda: NOW,
            id_factory=lambda: next(ids),
        )

        draft = service.create_profile(
            {"title": "Initial role", "effective_from": "2026-09-01"},
            "validator",
        )
        assert draft.state == RoleProfileState.DRAFT
        assert draft.provenance is not None and draft.provenance.human_confirmed
        before_rejection = bids.list_audit()
        try:
            service.publish_profile(
                draft.profile_id,
                {"expected_version_token": draft.version_token},
                "validator",
            )
        except ValueError:
            pass
        else:
            raise AssertionError("incomplete DRAFT was published")
        assert bids.list_audit() == before_rejection

        parent = service.edit_draft(
            draft.profile_id,
            {
                **complete("Initial role", "2026-09-01"),
                "expected_version_token": draft.version_token,
            },
            "validator",
        )
        parent = service.publish_profile(
            parent.profile_id,
            {"expected_version_token": parent.version_token},
            "validator",
        ).published
        authored_parent = parent.model_dump(
            include={
                "title",
                "mission",
                "boundaries",
                "coordination",
                "outcomes",
                "cadence",
                "domains",
            }
        )
        child = service.revise_profile(
            parent.profile_id,
            {"expected_version_token": parent.version_token},
            "validator",
        )
        parent_after_revision = service.get_profile(parent.profile_id)
        assert child.parent_profile_id == parent.profile_id
        assert child.version_number == 2
        try:
            service.revise_profile(
                parent.profile_id,
                {"expected_version_token": parent_after_revision.version_token},
                "validator",
            )
        except RoleProfileLifecycleError:
            pass
        else:
            raise AssertionError("duplicate active child DRAFT was created")

        child = service.edit_draft(
            child.profile_id,
            {
                **complete("Successor role", "2026-10-01"),
                "expected_version_token": child.version_token,
            },
            "validator",
        )
        publication = service.publish_profile(
            child.profile_id,
            {
                "expected_version_token": child.version_token,
                "parent_expected_version_token": parent_after_revision.version_token,
            },
            "validator",
        )
        assert publication.superseded_parent is not None
        assert publication.superseded_parent.effective_until == date(2026, 9, 30)
        before_changeover = service.effective_profile(as_of=date(2026, 9, 30))
        at_changeover = service.effective_profile(as_of=date(2026, 10, 1))
        after_changeover = service.effective_profile(as_of=date(2026, 10, 2))
        assert before_changeover is not None and before_changeover.profile_id == parent.profile_id
        assert at_changeover is not None and at_changeover.profile_id == child.profile_id
        assert after_changeover is not None and after_changeover.profile_id == child.profile_id
        parent_after = service.get_profile(parent.profile_id)
        assert parent_after.model_dump(include=set(authored_parent)) == authored_parent

        before_stale = bids.list_audit()
        child_after = service.get_profile(child.profile_id)
        try:
            service.retire_profile(
                child.profile_id,
                {"expected_version_token": "stale"},
                "validator",
            )
        except StaleRoleProfileError:
            pass
        else:
            raise AssertionError("stale retirement was accepted")
        assert service.get_profile(child.profile_id) == child_after
        assert bids.list_audit() == before_stale

        retired = service.retire_profile(
            child.profile_id,
            {"expected_version_token": child_after.version_token},
            "validator",
        )
        assert retired.state == RoleProfileState.RETIRED
        assert service.effective_profile(as_of=date(2026, 10, 1)) is None
        actions = [entry.action for entry in bids.list_audit()]
        assert actions.count("role_profile_created") == 1
        assert actions.count("role_profile_revised") == 1
        assert actions.count("role_profile_superseded") == 1
        assert actions.count("role_profile_retired") == 1
    print("OPS-03 validation: PASS")


if __name__ == "__main__":
    main()
