import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest

from core.bid_repository import BidRepository
from core.database import Database
from core.ops_foundation import (
    OpsFoundationRepository,
    RoleProfileLifecycleError,
    RoleProfileOverlapError,
    StaleRoleProfileError,
)
from core.role_profile_service import RoleProfileService
from core.role_profiles import RoleProfileState
from core.schemas import AuditEntry

NOW = datetime(2026, 8, 17, 14, tzinfo=UTC)


def _service(tmp_path: Path) -> tuple[Database, OpsFoundationRepository, RoleProfileService]:
    database = Database(tmp_path / "roles.db")
    BidRepository(database)
    repository = OpsFoundationRepository(database)
    ids = iter(UUID(int=value) for value in range(1, 200))
    service = RoleProfileService(
        repository,
        now_factory=lambda: NOW,
        id_factory=lambda: next(ids),
    )
    return database, repository, service


def _complete(title: str, start: str = "2026-09-01") -> dict[str, object]:
    return {
        "title": title,
        "organization": "Example organization",
        "mission": "Deliver controlled bids and contracts outcomes.",
        "boundaries": "No silent commercial authority.",
        "coordination": "Coordinate sales, suppliers, and governance.",
        "outcomes": "Auditable decisions and complete offers.",
        "cadence": "Daily attention and weekly portfolio review.",
        "domains": ["CUSTOMER_SOLUTION", "RISK_ASSURANCE"],
        "effective_from": start,
    }


def test_create_edit_publish_effective_and_retire_are_audited(tmp_path: Path) -> None:
    _, repository, service = _service(tmp_path)
    draft = service.create_profile(
        {"title": "  Initial role  ", "effective_from": "2026-09-01"},
        "jason",
    )
    assert draft.version_number == 1
    assert draft.state == RoleProfileState.DRAFT
    assert draft.provenance is not None and draft.provenance.human_confirmed
    first_token = draft.version_token

    edited = service.edit_draft(
        draft.profile_id,
        {**_complete("Initial role"), "expected_version_token": first_token},
        "jason",
    )
    assert edited.version_token != first_token
    assert edited.mission.startswith("Deliver controlled")

    published = service.publish_profile(
        draft.profile_id,
        {"expected_version_token": edited.version_token},
        "jason",
    ).published
    assert published.state == RoleProfileState.PUBLISHED
    assert service.effective_profile(as_of=date(2026, 8, 31)) is None
    assert service.effective_profile(as_of=date(2026, 9, 1)) == published

    retired = service.retire_profile(
        published.profile_id,
        {"expected_version_token": published.version_token},
        "jason",
    )
    assert retired.state == RoleProfileState.RETIRED
    assert service.effective_profile(as_of=date(2026, 9, 1)) is None
    assert [entry.action for entry in repository.list_role_profile_audit(draft.profile_id)] == [
        "role_profile_created",
        "role_profile_draft_updated",
        "role_profile_published",
        "role_profile_retired",
    ]


def test_publish_requires_complete_human_profile_and_rejects_overlap(tmp_path: Path) -> None:
    _, repository, service = _service(tmp_path)
    incomplete = service.create_profile(
        {"title": "Incomplete", "effective_from": "2026-09-01"},
        "jason",
    )
    before = len(repository.list_role_profile_audit(incomplete.profile_id))
    with pytest.raises(ValueError, match="non-empty mission"):
        service.publish_profile(
            incomplete.profile_id,
            {"expected_version_token": incomplete.version_token},
            "jason",
        )
    assert service.get_profile(incomplete.profile_id) == incomplete
    assert len(repository.list_role_profile_audit(incomplete.profile_id)) == before

    first = service.create_profile(_complete("First"), "jason")
    first = service.publish_profile(
        first.profile_id,
        {"expected_version_token": first.version_token},
        "jason",
    ).published
    second = service.create_profile(_complete("Second", "2026-10-01"), "jason")
    before_all = len(service.list_audit(second.profile_id))
    with pytest.raises(RoleProfileOverlapError):
        service.publish_profile(
            second.profile_id,
            {"expected_version_token": second.version_token},
            "jason",
        )
    assert service.get_profile(first.profile_id) == first
    assert service.get_profile(second.profile_id) == second
    assert len(service.list_audit(second.profile_id)) == before_all


def test_revision_uses_global_version_and_atomic_supersession(tmp_path: Path) -> None:
    _, repository, service = _service(tmp_path)
    parent = service.create_profile(_complete("Parent", "2026-09-01"), "jason")
    parent = service.publish_profile(
        parent.profile_id,
        {"expected_version_token": parent.version_token},
        "jason",
    ).published
    unrelated = service.create_profile(
        {"title": "Unrelated draft", "effective_from": "2027-01-01"},
        "jason",
    )
    assert unrelated.version_number == 2

    child = service.revise_profile(
        parent.profile_id,
        {"expected_version_token": parent.version_token},
        "jason",
    )
    assert child.version_number == 3
    assert child.parent_profile_id == parent.profile_id
    assert child.state == RoleProfileState.DRAFT
    assert child.title == parent.title
    parent_after_revision = service.get_profile(parent.profile_id)
    assert parent_after_revision.version_token != parent.version_token
    with pytest.raises(StaleRoleProfileError):
        service.revise_profile(
            parent.profile_id,
            {"expected_version_token": parent.version_token},
            "jason",
        )
    with pytest.raises(RoleProfileLifecycleError, match="active DRAFT revision"):
        service.revise_profile(
            parent.profile_id,
            {"expected_version_token": parent_after_revision.version_token},
            "jason",
        )

    child = service.edit_draft(
        child.profile_id,
        {
            **_complete("Successor", "2026-10-01"),
            "expected_version_token": child.version_token,
        },
        "jason",
    )
    result = service.publish_profile(
        child.profile_id,
        {
            "expected_version_token": child.version_token,
            "parent_expected_version_token": parent_after_revision.version_token,
        },
        "jason",
    )
    assert result.published.state == RoleProfileState.PUBLISHED
    assert result.superseded_parent is not None
    assert result.superseded_parent.state == RoleProfileState.PUBLISHED
    assert result.superseded_parent.effective_until == date(2026, 9, 30)
    assert service.effective_profile(as_of=date(2026, 9, 30)).profile_id == parent.profile_id
    assert service.effective_profile(as_of=date(2026, 10, 1)).profile_id == child.profile_id
    assert service.effective_profile(as_of=date(2026, 10, 2)).profile_id == child.profile_id
    actions = [entry.action for entry in repository.list_role_profile_audit(child.profile_id)]
    assert "role_profile_revised" in actions
    assert "role_profile_published" in actions
    assert "role_profile_superseded" in actions


def test_stale_mutations_leave_profile_and_audit_unchanged(tmp_path: Path) -> None:
    _, repository, service = _service(tmp_path)
    draft = service.create_profile(_complete("Stale test"), "jason")
    original = service.get_profile(draft.profile_id)
    audit_before = repository.list_role_profile_audit(draft.profile_id)

    with pytest.raises(StaleRoleProfileError):
        service.edit_draft(
            draft.profile_id,
            {**_complete("Changed"), "expected_version_token": "stale"},
            "jason",
        )
    with pytest.raises(StaleRoleProfileError):
        service.publish_profile(
            draft.profile_id,
            {"expected_version_token": "stale"},
            "jason",
        )
    assert service.get_profile(draft.profile_id) == original
    assert repository.list_role_profile_audit(draft.profile_id) == audit_before


def test_published_content_is_immutable_and_stale_revise_retire_fail(tmp_path: Path) -> None:
    _, repository, service = _service(tmp_path)
    draft = service.create_profile(_complete("Controlled"), "jason")
    published = service.publish_profile(
        draft.profile_id,
        {"expected_version_token": draft.version_token},
        "jason",
    ).published
    profile_before = service.get_profile(published.profile_id)
    audit_before = repository.list_role_profile_audit(published.profile_id)

    with pytest.raises(RoleProfileLifecycleError, match="Only DRAFT"):
        service.edit_draft(
            published.profile_id,
            {
                **_complete("Illegal direct edit"),
                "expected_version_token": published.version_token,
            },
            "jason",
        )
    with pytest.raises(StaleRoleProfileError):
        service.revise_profile(
            published.profile_id,
            {"expected_version_token": "stale"},
            "jason",
        )
    with pytest.raises(StaleRoleProfileError):
        service.retire_profile(
            published.profile_id,
            {"expected_version_token": "stale"},
            "jason",
        )
    assert service.get_profile(published.profile_id) == profile_before
    assert repository.list_role_profile_audit(published.profile_id) == audit_before


def test_retired_parent_revision_publishes_without_reactivation(tmp_path: Path) -> None:
    _, _, service = _service(tmp_path)
    parent = service.create_profile(
        {
            **_complete("Retired parent", "2026-09-01"),
            "effective_until": "2026-09-30",
        },
        "jason",
    )
    parent = service.publish_profile(
        parent.profile_id,
        {"expected_version_token": parent.version_token},
        "jason",
    ).published
    parent = service.retire_profile(
        parent.profile_id,
        {"expected_version_token": parent.version_token},
        "jason",
    )
    child = service.revise_profile(
        parent.profile_id,
        {"expected_version_token": parent.version_token},
        "jason",
    )
    child = service.edit_draft(
        child.profile_id,
        {
            **_complete("Retired successor", "2026-10-01"),
            "expected_version_token": child.version_token,
        },
        "jason",
    )
    published = service.publish_profile(
        child.profile_id,
        {"expected_version_token": child.version_token},
        "jason",
    ).published
    assert service.get_profile(parent.profile_id).state == RoleProfileState.RETIRED
    assert published.state == RoleProfileState.PUBLISHED
    assert service.effective_profile(as_of=date(2026, 10, 1)) == published


def test_supersession_audit_failure_rolls_back_parent_and_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, _, service = _service(tmp_path)
    parent = service.create_profile(_complete("Parent"), "jason")
    parent = service.publish_profile(
        parent.profile_id,
        {"expected_version_token": parent.version_token},
        "jason",
    ).published
    child = service.revise_profile(
        parent.profile_id,
        {"expected_version_token": parent.version_token},
        "jason",
    )
    parent = service.get_profile(parent.profile_id)
    child = service.edit_draft(
        child.profile_id,
        {
            **_complete("Child", "2026-10-01"),
            "expected_version_token": child.version_token,
        },
        "jason",
    )
    parent_before = service.get_profile(parent.profile_id)
    child_before = service.get_profile(child.profile_id)
    audit_before = BidRepository(database).list_audit()

    duplicate = AuditEntry(
        entry_id="AUD-duplicate-supersession",
        bid_id=None,
        actor="seed",
        action="seed",
        detail="{}",
        timestamp=NOW,
    )
    BidRepository(database).append_audit(duplicate)

    def duplicate_audit(**_: object) -> AuditEntry:
        return duplicate

    monkeypatch.setattr(service, "_audit", duplicate_audit)
    with pytest.raises(sqlite3.IntegrityError):
        service.publish_profile(
            child.profile_id,
            {
                "expected_version_token": child.version_token,
                "parent_expected_version_token": parent.version_token,
            },
            "jason",
        )
    assert service.get_profile(parent.profile_id) == parent_before
    assert service.get_profile(child.profile_id) == child_before
    assert BidRepository(database).list_audit() == [*audit_before, duplicate]


def test_audit_collision_rolls_back_profile_create(tmp_path: Path) -> None:
    database = Database(tmp_path / "roles.db")
    bids = BidRepository(database)
    repository = OpsFoundationRepository(database)
    ids = iter((UUID(int=1), UUID(int=2), UUID(int=3)))
    service = RoleProfileService(
        repository,
        now_factory=lambda: NOW,
        id_factory=lambda: next(ids),
    )
    bids.append_audit(
        AuditEntry(
            entry_id=f"AUD-{UUID(int=3)}",
            bid_id=None,
            actor="seed",
            action="seed",
            detail="{}",
            timestamp=NOW,
        )
    )

    with pytest.raises(sqlite3.IntegrityError):
        service.create_profile(
            {"title": "Rollback", "effective_from": "2026-09-01"},
            "jason",
        )
    assert repository.list_role_profiles() == []


def test_legacy_empty_provenance_is_uncertain_and_cannot_publish(tmp_path: Path) -> None:
    database, repository, service = _service(tmp_path)
    legacy_id = repository.create_profile(
        {
            "version_number": 1,
            "title": "Legacy",
            "mission": "Legacy mission",
            "domains": ["CUSTOMER_SOLUTION"],
            "effective_from": "2026-09-01",
            "created_by": "legacy",
            "created_at": NOW.isoformat(),
            "provenance": {},
        }
    )
    legacy = service.get_profile(legacy_id)
    assert legacy.provenance is None
    audit_before = len(BidRepository(database).list_audit())
    with pytest.raises(ValueError, match="human-authored provenance"):
        service.publish_profile(
            legacy_id,
            {"expected_version_token": legacy.version_token},
            "jason",
        )
    assert len(BidRepository(database).list_audit()) == audit_before


def test_overlapping_legacy_published_rows_fail_effective_lookup_visibly(tmp_path: Path) -> None:
    database, repository, service = _service(tmp_path)
    first = repository.create_profile(
        {
            "version_number": 1,
            "title": "Legacy one",
            "mission": "Legacy mission",
            "domains": ["CUSTOMER_SOLUTION"],
            "effective_from": "2026-09-01",
            "state": "PUBLISHED",
            "created_by": "legacy",
            "created_at": NOW.isoformat(),
        }
    )
    second = repository.create_profile(
        {
            "version_number": 2,
            "title": "Legacy two",
            "mission": "Legacy mission",
            "domains": ["RISK_ASSURANCE"],
            "effective_from": "2026-10-01",
            "state": "DRAFT",
            "created_by": "legacy",
            "created_at": NOW.isoformat(),
        }
    )
    with database._conn() as conn:
        conn.execute(
            "UPDATE ops_role_profiles SET state='PUBLISHED' WHERE profile_id=?",
            (second,),
        )
    assert repository.get_role_profile(first) is not None
    with pytest.raises(RoleProfileOverlapError, match="Multiple PUBLISHED"):
        service.effective_profile(as_of=date(2026, 10, 1))
