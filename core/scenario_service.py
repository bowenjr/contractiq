"""TASK-16 scenario workflow boundary."""

from __future__ import annotations

from typing import Any

from core.commercial_scenarios import (
    BaselineSelection,
    ScenarioFamily,
    ScenarioReview,
    ScenarioVersion,
    calculate_scenario,
)
from core.scenario_repository import ScenarioRepository


class ScenarioService:
    def __init__(self, repository: ScenarioRepository) -> None:
        self.repository = repository

    def create_family(self, value: ScenarioFamily, actor: str = "operator") -> ScenarioFamily:
        self.repository.create_family(value, actor)
        return value

    def calculate(self, value: ScenarioVersion, actor: str = "operator") -> Any:
        result = calculate_scenario(value)
        self.repository.add_version(value, result, actor)
        return result

    def review(self, value: ScenarioReview, bid_id: str, actor: str = "operator") -> ScenarioReview:
        if value.reviewer == actor:
            raise ValueError("scenario author cannot independently review")
        self.repository.add_review(value, bid_id, actor)
        return value

    def select_baseline(self, value: BaselineSelection) -> BaselineSelection:
        self.repository.select_baseline(value)
        return value

    def metrics(self, bid_id: str | None = None) -> dict[str, int]:
        families = self.repository.families(bid_id)
        return {
            "families_total": len(families),
            "families_active": sum(row["lifecycle"] == "ACTIVE" for row in families),
            "baseline_count": len(self.repository.baselines(bid_id)),
            "reviewed_versions": sum(
                bool(self.repository.reviews(row["scenario_version_id"]))
                for family in families
                for row in self.repository.versions(family["family_id"])
            ),
        }
