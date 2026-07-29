"""Pure deterministic bid classification rules."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from core.classifier_config import ClassifierConfig, load_classifier_config
from core.enums import BidLevel, RiskTrigger

_LEVEL_RANK: dict[BidLevel, int] = {
    BidLevel.LEVEL_0: 0,
    BidLevel.LEVEL_1: 1,
    BidLevel.LEVEL_2: 2,
    BidLevel.LEVEL_3: 3,
    BidLevel.LEVEL_4: 4,
}


class ClassificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    estimated_value: Decimal
    triggers: list[RiskTrigger] = Field(default_factory=list)
    is_epc_epcm: bool = False
    strategic_customer: bool = False


class ClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: BidLevel
    fired_triggers: list[RiskTrigger]
    value_band_level: BidLevel
    trigger_floor_level: BidLevel
    rationale: list[str]


def _level_label(level: BidLevel) -> str:
    return level.value.replace("_", " ").title()


def _format_value(value: Decimal) -> str:
    formatted = f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"${formatted}"


def _highest_level(levels: list[BidLevel]) -> BidLevel:
    return max(levels, key=_LEVEL_RANK.__getitem__)


def classify(
    inp: ClassificationInput,
    config: ClassifierConfig | None = None,
) -> ClassificationResult:
    """Classify structured bid facts using only transparent deterministic rules."""
    resolved_config = config if config is not None else load_classifier_config()

    eligible_bands = [
        (threshold, level)
        for threshold, level in resolved_config.value_bands
        if threshold <= inp.estimated_value
    ]
    value_band_level = (
        max(eligible_bands, key=lambda band: band[0])[1] if eligible_bands else BidLevel.LEVEL_0
    )

    trigger_floor_level = _highest_level(
        [resolved_config.trigger_floors.get(trigger, BidLevel.LEVEL_0) for trigger in inp.triggers]
        or [BidLevel.LEVEL_0]
    )
    hint_floor_level = (
        BidLevel.LEVEL_3 if inp.is_epc_epcm or inp.strategic_customer else BidLevel.LEVEL_0
    )
    level = _highest_level([value_band_level, trigger_floor_level, hint_floor_level])

    fired_triggers = sorted(
        inp.triggers,
        key=lambda trigger: _LEVEL_RANK[
            resolved_config.trigger_floors.get(trigger, BidLevel.LEVEL_0)
        ],
        reverse=True,
    )

    rationale = [f"Value {_format_value(inp.estimated_value)} → {_level_label(value_band_level)}"]
    rationale.extend(
        f"Trigger {trigger.name} forces "
        f"{_level_label(resolved_config.trigger_floors.get(trigger, BidLevel.LEVEL_0))}"
        for trigger in fired_triggers
    )
    if inp.is_epc_epcm:
        rationale.append("EPC/EPCM project → minimum Level 3")
    if inp.strategic_customer:
        rationale.append("Strategic customer → minimum Level 3")

    return ClassificationResult(
        level=level,
        fired_triggers=fired_triggers,
        value_band_level=value_band_level,
        trigger_floor_level=trigger_floor_level,
        rationale=rationale,
    )
