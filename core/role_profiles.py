"""Typed role-profile domain and lifecycle command models for OPS-03."""

from datetime import date, datetime
from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.schemas import Provenance
from core.work_items import ResponsibilityDomain


class RoleProfileState(str, Enum):  # noqa: UP042 - persisted string contract
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    RETIRED = "RETIRED"


ROLE_PROFILE_STATE_LABELS: dict[RoleProfileState, str] = {
    RoleProfileState.DRAFT: "Draft",
    RoleProfileState.PUBLISHED: "Published",
    RoleProfileState.RETIRED: "Retired",
}


def _trim_required(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    return normalized


def _trim_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


class RoleProfileAuthoring(BaseModel):
    """Author-controlled profile content shared by create and draft edit."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=300)
    organization: str | None = Field(default=None, max_length=300)
    mission: str = Field(default="", max_length=10_000)
    boundaries: str = Field(default="", max_length=10_000)
    coordination: str = Field(default="", max_length=10_000)
    outcomes: str = Field(default="", max_length=10_000)
    cadence: str = Field(default="", max_length=10_000)
    domains: list[ResponsibilityDomain] = Field(default_factory=list)
    effective_from: date
    effective_until: date | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        return _trim_required(value, "title")

    @field_validator("organization")
    @classmethod
    def normalize_organization(cls, value: str | None) -> str | None:
        return _trim_optional(value)

    @field_validator("mission", "boundaries", "coordination", "outcomes", "cadence")
    @classmethod
    def normalize_narrative(cls, value: str) -> str:
        return value.strip()

    @field_validator("domains")
    @classmethod
    def deduplicate_domains(
        cls,
        values: list[ResponsibilityDomain],
    ) -> list[ResponsibilityDomain]:
        selected = set(values)
        return [domain for domain in ResponsibilityDomain if domain in selected]

    @model_validator(mode="after")
    def validate_effective_window(self) -> Self:
        if self.effective_until is not None and self.effective_until < self.effective_from:
            raise ValueError("effective_until must be on or after effective_from")
        return self


class RoleProfileCreate(RoleProfileAuthoring):
    """Request to create one server-owned DRAFT profile."""


class RoleProfileDraftEdit(RoleProfileAuthoring):
    """Authorable DRAFT replacement guarded by an expected token."""

    expected_version_token: str = Field(min_length=1, max_length=200)

    @field_validator("expected_version_token")
    @classmethod
    def normalize_expected_token(cls, value: str) -> str:
        return _trim_required(value, "expected_version_token")


class RoleProfileRevision(BaseModel):
    """Request to copy one controlled profile into a child DRAFT."""

    model_config = ConfigDict(extra="forbid")

    expected_version_token: str = Field(min_length=1, max_length=200)

    @field_validator("expected_version_token")
    @classmethod
    def normalize_expected_token(cls, value: str) -> str:
        return _trim_required(value, "expected_version_token")


class RoleProfileTransition(BaseModel):
    """Expected tokens for a publish or retirement operation."""

    model_config = ConfigDict(extra="forbid")

    expected_version_token: str = Field(min_length=1, max_length=200)
    parent_expected_version_token: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )

    @field_validator("expected_version_token")
    @classmethod
    def normalize_expected_token(cls, value: str) -> str:
        return _trim_required(value, "expected_version_token")

    @field_validator("parent_expected_version_token")
    @classmethod
    def normalize_parent_token(cls, value: str | None) -> str | None:
        return _trim_optional(value)


class RoleProfile(BaseModel):
    """One persisted role-profile version."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str
    version_number: int = Field(ge=1)
    title: str = Field(max_length=300)
    organization: str | None = Field(default=None, max_length=300)
    mission: str = Field(max_length=10_000)
    boundaries: str = Field(max_length=10_000)
    coordination: str = Field(max_length=10_000)
    outcomes: str = Field(max_length=10_000)
    cadence: str = Field(max_length=10_000)
    domains: list[ResponsibilityDomain]
    effective_from: date
    effective_until: date | None = None
    state: RoleProfileState
    created_by: str
    created_at: datetime
    parent_profile_id: str | None = None
    version_token: str = Field(min_length=1)
    provenance: Provenance | None = None

    @field_validator("domains")
    @classmethod
    def deduplicate_domains(
        cls,
        values: list[ResponsibilityDomain],
    ) -> list[ResponsibilityDomain]:
        selected = set(values)
        return [domain for domain in ResponsibilityDomain if domain in selected]

    @model_validator(mode="after")
    def validate_effective_window(self) -> Self:
        if self.effective_until is not None and self.effective_until < self.effective_from:
            raise ValueError("effective_until must be on or after effective_from")
        return self

    def validate_for_publication(self) -> Self:
        """Apply the stricter DRAFT-to-PUBLISHED completeness gate."""
        if not self.title.strip():
            raise ValueError("Publishing requires a non-empty title")
        if not self.mission.strip():
            raise ValueError("Publishing requires a non-empty mission")
        if not self.domains:
            raise ValueError("Publishing requires at least one responsibility domain")
        if self.provenance is None:
            raise ValueError("Publishing requires human-authored provenance")
        if not self.provenance.human_confirmed:
            raise ValueError("Publishing requires human-authored provenance")
        if self.provenance.created_by.value != "human":
            raise ValueError("Publishing requires human-authored provenance")
        return self


class RoleProfilePublicationResult(BaseModel):
    """Profiles affected by one controlled publication operation."""

    model_config = ConfigDict(extra="forbid")

    published: RoleProfile
    superseded_parent: RoleProfile | None = None
