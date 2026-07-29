import json
from decimal import Decimal

from core.bid_repository import BidRepository
from core.classifier import ClassificationInput, classify
from core.classifier_config import (
    DEFAULT_TRIGGER_FLOORS,
    ClassifierConfig,
    load_classifier_config,
)
from core.classifier_service import classify_and_store
from core.enums import BidLevel, RiskTrigger
from core.schemas import Bid


def test_zero_triggers_and_value_below_first_paid_band_is_level_zero() -> None:
    result = classify(ClassificationInput(estimated_value=Decimal("49999")))

    assert result.level == BidLevel.LEVEL_0
    assert result.value_band_level == BidLevel.LEVEL_0
    assert result.trigger_floor_level == BidLevel.LEVEL_0
    assert result.fired_triggers == []


def test_value_in_level_two_band_without_triggers_is_level_two() -> None:
    result = classify(ClassificationInput(estimated_value=Decimal("750000")))

    assert result.level == BidLevel.LEVEL_2
    assert result.value_band_level == BidLevel.LEVEL_2


def test_liquidated_damages_trigger_beats_level_zero_value() -> None:
    result = classify(
        ClassificationInput(
            estimated_value=Decimal("1000"),
            triggers=[RiskTrigger.LIQUIDATED_DAMAGES],
        )
    )

    assert result.level == BidLevel.LEVEL_4
    assert result.trigger_floor_level == BidLevel.LEVEL_4


def test_level_three_value_beats_level_two_trigger_floor() -> None:
    result = classify(
        ClassificationInput(
            estimated_value=Decimal("1000000"),
            triggers=[RiskTrigger.MULTIPLE_MANUFACTURERS],
        )
    )

    assert result.level == BidLevel.LEVEL_3
    assert result.trigger_floor_level == BidLevel.LEVEL_2


def test_epc_epcm_hint_raises_low_value_bid_to_level_three() -> None:
    result = classify(ClassificationInput(estimated_value=Decimal("1000"), is_epc_epcm=True))

    assert result.level == BidLevel.LEVEL_3


def test_multiple_triggers_use_maximum_floor_and_sort_highest_first() -> None:
    result = classify(
        ClassificationInput(
            estimated_value=Decimal("1000"),
            triggers=[
                RiskTrigger.MULTIPLE_MANUFACTURERS,
                RiskTrigger.NON_STANDARD_TERMS,
                RiskTrigger.LIQUIDATED_DAMAGES,
            ],
        )
    )

    assert result.level == BidLevel.LEVEL_4
    assert result.fired_triggers == [
        RiskTrigger.LIQUIDATED_DAMAGES,
        RiskTrigger.NON_STANDARD_TERMS,
        RiskTrigger.MULTIPLE_MANUFACTURERS,
    ]


def test_rationale_contains_winning_factor_and_is_non_empty() -> None:
    result = classify(
        ClassificationInput(
            estimated_value=Decimal("1000"),
            triggers=[RiskTrigger.LIQUIDATED_DAMAGES],
        )
    )

    assert result.rationale
    assert "Trigger LIQUIDATED_DAMAGES forces Level 4" in result.rationale


def test_classify_is_deterministic_for_same_input() -> None:
    inp = ClassificationInput(
        estimated_value=Decimal("400000"),
        triggers=[RiskTrigger.WARRANTY_EXTENSION],
        strategic_customer=True,
    )
    config = ClassifierConfig(
        value_bands=[
            (Decimal("0"), BidLevel.LEVEL_0),
            (Decimal("100000"), BidLevel.LEVEL_1),
        ],
        trigger_floors=dict(DEFAULT_TRIGGER_FLOORS),
    )

    assert classify(inp, config) == classify(inp, config)


def test_custom_config_changes_classification_outcome() -> None:
    config = ClassifierConfig(
        value_bands=[
            (Decimal("0"), BidLevel.LEVEL_0),
            (Decimal("1000"), BidLevel.LEVEL_3),
        ],
        trigger_floors=dict(DEFAULT_TRIGGER_FLOORS),
    )

    result = classify(ClassificationInput(estimated_value=Decimal("1000")), config)

    assert result.level == BidLevel.LEVEL_3


def test_malformed_config_json_raises_value_error(tmp_path) -> None:
    config_path = tmp_path / "classifier_config.json"
    config_path.write_text("{not valid JSON", encoding="utf-8")

    try:
        load_classifier_config(config_path)
    except ValueError as exc:
        assert "Invalid classifier config" in str(exc)
    else:
        raise AssertionError("Malformed classifier config did not raise ValueError")


def test_classify_and_store_persists_result_and_audits_rationale(
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    inp = ClassificationInput(
        estimated_value=Decimal("1000"),
        triggers=[RiskTrigger.LIQUIDATED_DAMAGES],
    )

    result = classify_and_store(bid_repo, valid_bid.bid_id, inp, actor="jason")

    stored_bid = bid_repo.get_bid(valid_bid.bid_id)
    assert stored_bid is not None
    assert stored_bid.classification == BidLevel.LEVEL_4
    assert stored_bid.risk_triggers == [RiskTrigger.LIQUIDATED_DAMAGES]
    audit_entries = bid_repo.list_audit(valid_bid.bid_id)
    assert len(audit_entries) == 1
    assert audit_entries[0].actor == "jason"
    assert audit_entries[0].action == "bid_classified"
    assert json.loads(audit_entries[0].detail)["rationale"] == result.rationale
