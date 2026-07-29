"""Persistence wrapper for deterministic bid classification."""

import json
from datetime import UTC, datetime
from uuid import uuid4

from core.bid_repository import BidRepository
from core.classifier import ClassificationInput, ClassificationResult, classify
from core.classifier_config import ClassifierConfig
from core.schemas import AuditEntry


def classify_and_store(
    repo: BidRepository,
    bid_id: str,
    inp: ClassificationInput,
    config: ClassifierConfig | None = None,
    actor: str = "system",
) -> ClassificationResult:
    """Classify a bid, persist the decision, and append its audit evidence."""
    bid = repo.get_bid(bid_id)
    if bid is None:
        raise ValueError(f"Bid not found: {bid_id}")

    result = classify(inp, config)
    repo.update_bid(
        bid.model_copy(
            update={
                "classification": result.level,
                "risk_triggers": result.fired_triggers,
            }
        )
    )
    repo.append_audit(
        AuditEntry(
            entry_id=f"AUD-{uuid4()}",
            bid_id=bid_id,
            actor=actor,
            action="bid_classified",
            detail=json.dumps(
                {
                    "classification": result.level.value,
                    "rationale": result.rationale,
                    "risk_triggers": [trigger.value for trigger in result.fired_triggers],
                },
                sort_keys=True,
            ),
            timestamp=datetime.now(UTC),
        )
    )
    return result
