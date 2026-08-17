"""Application service for controlled OPS-03 role-profile lifecycles."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from core.enums import Actor
from core.ops_foundation import (
    OpsFoundationRepository,
    RoleProfileLifecycleError,
    RoleProfileNotFoundError,
    StaleRoleProfileError,
)
from core.role_profiles import (
    RoleProfile,
    RoleProfileCreate,
    RoleProfileDraftEdit,
    RoleProfilePublicationResult,
    RoleProfileRevision,
    RoleProfileState,
    RoleProfileTransition,
)
from core.schemas import AuditEntry, Provenance


class RoleProfileService:
    """Own server identity, provenance, validation, and mutation orchestration."""

    def __init__(
        self,
        repository: OpsFoundationRepository,
        *,
        now_factory: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self.repository = repository
        self._now_factory = now_factory or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or uuid4

    @staticmethod
    def _actor(value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("actor must be non-empty")
        return normalized

    def _now(self) -> datetime:
        value = self._now_factory()
        if value.tzinfo is None:
            raise ValueError("role-profile service clock must be timezone-aware")
        return value

    def _new_profile_id(self) -> str:
        return f"RPF-{self._id_factory().hex}"

    def _new_token(self) -> str:
        return self._id_factory().hex

    def _human_provenance(self, actor: str, at: datetime) -> Provenance:
        return Provenance(
            created_by=Actor.HUMAN,
            agent_name=actor,
            created_at=at,
            human_confirmed=True,
            confirmed_by=actor,
            confirmed_at=at,
        )

    @staticmethod
    def _profile_summary(profile: RoleProfile) -> dict[str, Any]:
        return {
            "profile_id": profile.profile_id,
            "version_number": profile.version_number,
            "state": profile.state.value,
            "effective_from": profile.effective_from.isoformat(),
            "effective_until": (
                profile.effective_until.isoformat() if profile.effective_until else None
            ),
            "parent_profile_id": profile.parent_profile_id,
        }

    def _audit(
        self,
        *,
        actor: str,
        action: str,
        at: datetime,
        detail: dict[str, Any],
    ) -> AuditEntry:
        return AuditEntry(
            entry_id=f"AUD-{self._id_factory()}",
            bid_id=None,
            actor=actor,
            action=action,
            detail=json.dumps(detail, sort_keys=True, separators=(",", ":")),
            timestamp=at,
        )

    def get_profile(self, profile_id: str) -> RoleProfile:
        """Return one stable profile or raise a typed missing-record error."""
        profile = self.repository.get_role_profile(profile_id)
        if profile is None:
            raise RoleProfileNotFoundError(f"Role profile not found: {profile_id}")
        return profile

    def list_profiles(self) -> list[RoleProfile]:
        """Return controlled history without mutation."""
        return self.repository.list_role_profiles()

    def list_children(self, profile_id: str) -> list[RoleProfile]:
        """Return direct child revisions without mutation."""
        return self.repository.list_role_profile_children(profile_id)

    def list_audit(self, profile_id: str) -> list[AuditEntry]:
        """Return audit events associated with one profile version."""
        return self.repository.list_role_profile_audit(profile_id)

    def effective_profile(self, *, as_of: date) -> RoleProfile | None:
        """Resolve the effective profile using an explicit working date."""
        return self.repository.effective_role_profile(as_of)

    def create_profile(
        self,
        data: RoleProfileCreate | dict[str, Any],
        actor: str,
    ) -> RoleProfile:
        """Create one human-authored DRAFT and its audit atomically."""
        request = RoleProfileCreate.model_validate(data)
        normalized_actor = self._actor(actor)
        now = self._now()
        provenance = self._human_provenance(normalized_actor, now)
        return self.repository.create_role_profile_draft(
            request,
            profile_id=self._new_profile_id(),
            created_by=normalized_actor,
            created_at=now,
            version_token=self._new_token(),
            provenance=provenance,
            audit_factory=lambda profile: self._audit(
                actor=normalized_actor,
                action="role_profile_created",
                at=now,
                detail={
                    **self._profile_summary(profile),
                    "action": "create",
                    "before_state": None,
                    "after_state": RoleProfileState.DRAFT.value,
                    "actor": normalized_actor,
                    "provenance_source": Actor.HUMAN.value,
                },
            ),
        )

    def edit_draft(
        self,
        profile_id: str,
        data: RoleProfileDraftEdit | dict[str, Any],
        actor: str,
    ) -> RoleProfile:
        """Persist authored DRAFT fields behind optimistic concurrency."""
        request = RoleProfileDraftEdit.model_validate(data)
        normalized_actor = self._actor(actor)
        current = self.get_profile(profile_id)
        if current.version_token != request.expected_version_token:
            raise StaleRoleProfileError(f"Stale role profile token for {profile_id}")
        if current.state != RoleProfileState.DRAFT:
            raise RoleProfileLifecycleError("Only DRAFT role profiles can be edited")
        now = self._now()
        values = request.model_dump(exclude={"expected_version_token"})
        updated = current.model_copy(
            update={
                **values,
                "version_token": self._new_token(),
            }
        )
        audit = self._audit(
            actor=normalized_actor,
            action="role_profile_draft_updated",
            at=now,
            detail={
                **self._profile_summary(updated),
                "action": "draft_update",
                "before": self._profile_summary(current),
                "after": self._profile_summary(updated),
                "actor": normalized_actor,
                "provenance_source": (
                    updated.provenance.created_by.value if updated.provenance else "legacy_unknown"
                ),
            },
        )
        self.repository.update_role_profile_draft(
            updated,
            expected_token=request.expected_version_token,
            audit_entry=audit,
        )
        return updated

    def revise_profile(
        self,
        profile_id: str,
        data: RoleProfileRevision | dict[str, Any],
        actor: str,
    ) -> RoleProfile:
        """Create one child DRAFT and consume the parent's expected token."""
        request = RoleProfileRevision.model_validate(data)
        normalized_actor = self._actor(actor)
        current = self.get_profile(profile_id)
        if current.version_token != request.expected_version_token:
            raise StaleRoleProfileError(f"Stale role profile token for {profile_id}")
        now = self._now()
        provenance = self._human_provenance(normalized_actor, now)
        _, child = self.repository.create_role_profile_revision(
            profile_id,
            expected_parent_token=request.expected_version_token,
            child_profile_id=self._new_profile_id(),
            child_version_token=self._new_token(),
            parent_version_token=self._new_token(),
            created_by=normalized_actor,
            created_at=now,
            provenance=provenance,
            audit_factory=lambda before, after, created: self._audit(
                actor=normalized_actor,
                action="role_profile_revised",
                at=now,
                detail={
                    **self._profile_summary(created),
                    "action": "revise",
                    "parent_profile_id": before.profile_id,
                    "child_profile_id": created.profile_id,
                    "before_state": before.state.value,
                    "after_state": after.state.value,
                    "actor": normalized_actor,
                    "provenance_source": Actor.HUMAN.value,
                },
            ),
        )
        return child

    def publish_profile(
        self,
        profile_id: str,
        data: RoleProfileTransition | dict[str, Any],
        actor: str,
    ) -> RoleProfilePublicationResult:
        """Publish a complete DRAFT, atomically superseding a PUBLISHED parent."""
        request = RoleProfileTransition.model_validate(data)
        normalized_actor = self._actor(actor)
        current = self.get_profile(profile_id)
        if current.version_token != request.expected_version_token:
            raise StaleRoleProfileError(f"Stale role profile token for {profile_id}")
        current.validate_for_publication()
        now = self._now()

        def audits(
            before: RoleProfile,
            published: RoleProfile,
            parent_before: RoleProfile | None,
            parent_after: RoleProfile | None,
        ) -> list[AuditEntry]:
            entries = [
                self._audit(
                    actor=normalized_actor,
                    action="role_profile_published",
                    at=now,
                    detail={
                        **self._profile_summary(published),
                        "action": "publish",
                        "before_state": before.state.value,
                        "after_state": published.state.value,
                        "parent_profile_id": published.parent_profile_id,
                        "actor": normalized_actor,
                        "provenance_source": (
                            published.provenance.created_by.value
                            if published.provenance
                            else "legacy_unknown"
                        ),
                    },
                )
            ]
            if (
                parent_before is not None
                and parent_after is not None
                and parent_before.effective_until != parent_after.effective_until
            ):
                entries.append(
                    self._audit(
                        actor=normalized_actor,
                        action="role_profile_superseded",
                        at=now,
                        detail={
                            **self._profile_summary(parent_after),
                            "action": "supersede",
                            "parent_profile_id": parent_before.profile_id,
                            "child_profile_id": published.profile_id,
                            "before_state": parent_before.state.value,
                            "after_state": parent_after.state.value,
                            "before_effective_until": (
                                parent_before.effective_until.isoformat()
                                if parent_before.effective_until
                                else None
                            ),
                            "after_effective_until": (
                                parent_after.effective_until.isoformat()
                                if parent_after.effective_until
                                else None
                            ),
                            "actor": normalized_actor,
                            "provenance_source": Actor.HUMAN.value,
                        },
                    )
                )
            return entries

        return self.repository.publish_role_profile_draft(
            profile_id,
            expected_token=request.expected_version_token,
            parent_expected_token=request.parent_expected_version_token,
            published_token=self._new_token(),
            parent_token=self._new_token(),
            audit_factory=audits,
        )

    def retire_profile(
        self,
        profile_id: str,
        data: RoleProfileTransition | dict[str, Any],
        actor: str,
    ) -> RoleProfile:
        """Retire one PUBLISHED profile behind optimistic concurrency."""
        request = RoleProfileTransition.model_validate(data)
        normalized_actor = self._actor(actor)
        current = self.get_profile(profile_id)
        if current.version_token != request.expected_version_token:
            raise StaleRoleProfileError(f"Stale role profile token for {profile_id}")
        now = self._now()
        return self.repository.retire_role_profile_version(
            profile_id,
            expected_token=request.expected_version_token,
            retired_token=self._new_token(),
            audit_factory=lambda before, retired: self._audit(
                actor=normalized_actor,
                action="role_profile_retired",
                at=now,
                detail={
                    **self._profile_summary(retired),
                    "action": "retire",
                    "before_state": before.state.value,
                    "after_state": retired.state.value,
                    "actor": normalized_actor,
                    "provenance_source": Actor.HUMAN.value,
                },
            ),
        )
