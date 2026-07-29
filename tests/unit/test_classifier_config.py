from decimal import Decimal

import pytest

from core.classifier_config import (
    DEFAULT_TRIGGER_FLOORS,
    DEFAULT_VALUE_BANDS,
    load_classifier_config,
)
from core.enums import BidLevel, RiskTrigger


def test_missing_config_file_returns_defaults(tmp_path) -> None:
    config = load_classifier_config(tmp_path / "missing-classifier-config.json")

    assert config.value_bands == DEFAULT_VALUE_BANDS
    assert config.trigger_floors == DEFAULT_TRIGGER_FLOORS


def test_valid_config_file_overrides_defaults(tmp_path) -> None:
    config_path = tmp_path / "classifier_config.json"
    config_path.write_text(
        """
        {
          "value_bands": [["0", "level_0"], ["1000", "level_3"]],
          "trigger_floors": {"unclear_scope": "level_4"}
        }
        """,
        encoding="utf-8",
    )

    config = load_classifier_config(config_path)

    assert config.value_bands == [
        (Decimal("0"), BidLevel.LEVEL_0),
        (Decimal("1000"), BidLevel.LEVEL_3),
    ]
    assert config.trigger_floors == {RiskTrigger.UNCLEAR_SCOPE: BidLevel.LEVEL_4}


def test_malformed_config_file_raises_value_error(tmp_path) -> None:
    config_path = tmp_path / "classifier_config.json"
    config_path.write_text('{"value_bands": "not-a-list"}', encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid classifier config"):
        load_classifier_config(config_path)
