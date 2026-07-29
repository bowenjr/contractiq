"""Configuration for deterministic bid classification.

The committed values in this module are non-confidential placeholders. A local
``data/classifier_config.json`` file can replace them at runtime; ``data/`` is
gitignored so real Westburne thresholds are not committed.

The JSON file mirrors :class:`ClassifierConfig`, for example::

    {
      "value_bands": [["0", "level_0"], ["100000", "level_1"]],
      "trigger_floors": {"liquidated_damages": "level_4"}
    }
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from core.enums import BidLevel, RiskTrigger

DEFAULT_CONFIG_PATH = Path("data/classifier_config.json")

# Value bands (CAD). These are PLACEHOLDER defaults — the real bands live in
# data/classifier_config.json (gitignored) and override these at runtime.
DEFAULT_VALUE_BANDS: list[tuple[Decimal, BidLevel]] = [
    (Decimal("0"), BidLevel.LEVEL_0),
    (Decimal("50000"), BidLevel.LEVEL_1),
    (Decimal("250000"), BidLevel.LEVEL_2),
    (Decimal("1000000"), BidLevel.LEVEL_3),
    # Level 4 is reached by trigger, not value — see trigger floors.
]

DEFAULT_TRIGGER_FLOORS: dict[RiskTrigger, BidLevel] = {
    # Level 4 — exceptional risk (report §8.1 Level 4 wording)
    RiskTrigger.LIQUIDATED_DAMAGES: BidLevel.LEVEL_4,
    RiskTrigger.BONDS_OR_GUARANTEES: BidLevel.LEVEL_4,
    RiskTrigger.INTERNATIONAL_EXPOSURE: BidLevel.LEVEL_4,
    RiskTrigger.EPC_FLOWDOWN: BidLevel.LEVEL_4,
    # Level 3 — strategic
    RiskTrigger.NON_STANDARD_TERMS: BidLevel.LEVEL_3,
    RiskTrigger.EXTENDED_PAYMENT_OR_HOLDBACK: BidLevel.LEVEL_3,
    RiskTrigger.WARRANTY_EXTENSION: BidLevel.LEVEL_3,
    RiskTrigger.NON_CANCELLABLE_PRODUCT: BidLevel.LEVEL_3,
    # Level 2 — complex
    RiskTrigger.MULTIPLE_MANUFACTURERS: BidLevel.LEVEL_2,
    RiskTrigger.SUBSTANTIAL_VENDOR_DATA: BidLevel.LEVEL_2,
    RiskTrigger.FIELD_SERVICES: BidLevel.LEVEL_2,
    RiskTrigger.LONG_DURATION: BidLevel.LEVEL_2,
    RiskTrigger.UNCLEAR_SCOPE: BidLevel.LEVEL_2,
}


class ClassifierConfig(BaseModel):
    """Validated, frozen value bands and risk-trigger floors."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value_bands: list[tuple[Decimal, BidLevel]]
    trigger_floors: dict[RiskTrigger, BidLevel]

    @model_validator(mode="after")
    def validate_value_bands(self) -> Self:
        if not self.value_bands:
            raise ValueError("value_bands must contain at least one band")
        thresholds = [threshold for threshold, _level in self.value_bands]
        if len(set(thresholds)) != len(thresholds):
            raise ValueError("value_bands thresholds must be unique")
        return self


def _default_config() -> ClassifierConfig:
    return ClassifierConfig(
        value_bands=list(DEFAULT_VALUE_BANDS),
        trigger_floors=dict(DEFAULT_TRIGGER_FLOORS),
    )


def load_classifier_config(path: str | Path | None = None) -> ClassifierConfig:
    """Load a local classifier override, or return safe placeholder defaults."""
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return _default_config()

    try:
        raw_config = json.loads(config_path.read_text(encoding="utf-8"))
        return ClassifierConfig.model_validate(raw_config)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Invalid classifier config at {config_path}: {exc}") from exc
