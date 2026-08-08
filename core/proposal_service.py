"""TASK-18 proposal composition and render workflow."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from core.proposal_repository import ProposalRepository
from core.proposals import (
    ProposalFamily,
    ProposalProfile,
    ProposalReview,
    ProposalVersion,
    RenderArtifact,
    write_artifacts,
)


class ProposalService:
    def __init__(self, repository: ProposalRepository, artifact_root: Path) -> None:
        self.repository = repository
        self.artifact_root = artifact_root

    def create_profile(self, value: ProposalProfile, actor: str = "operator") -> ProposalProfile:
        self.repository.create_profile(value, actor)
        return value

    def create_family(self, value: ProposalFamily, actor: str = "operator") -> ProposalFamily:
        self.repository.create_family(value, actor)
        return value

    def add_version(self, value: ProposalVersion, actor: str = "operator") -> ProposalVersion:
        self.repository.add_version(value, actor)
        return value

    def review(self, value: ProposalReview, bid_id: str, actor: str = "operator") -> ProposalReview:
        self.repository.add_review(value, bid_id, actor)
        return value

    def render(self, value: ProposalVersion, actor: str = "operator") -> tuple[RenderArtifact, ...]:
        root = self.artifact_root / value.proposal_version_id
        artifacts = write_artifacts(value, root)
        self.repository.add_artifacts(artifacts, value.bid_id, actor)
        return artifacts

    def select_baseline(
        self, bid_id: str, version_id: str, actor: str, rationale: str, selected_at: datetime
    ) -> None:
        self.repository.select_baseline(bid_id, version_id, actor, rationale, selected_at)

    def metrics(self, bid_id: str | None = None) -> dict[str, int]:
        return self.repository.metrics(bid_id)
