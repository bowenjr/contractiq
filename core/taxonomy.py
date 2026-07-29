"""Deterministic normalization for legacy obligation taxonomy values."""

from core.enums import ObligationType, TriggerType

OBLIGATION_TYPE_VARIANTS: dict[str, ObligationType] = {
    "performance": ObligationType.PERFORMANCE,
    "performance obligation": ObligationType.PERFORMANCE,
    "perform": ObligationType.PERFORMANCE,
    "delivery": ObligationType.PERFORMANCE,
    "delivery obligation": ObligationType.PERFORMANCE,
    "payment": ObligationType.PAYMENT,
    "payment obligation": ObligationType.PAYMENT,
    "pay": ObligationType.PAYMENT,
    "financial": ObligationType.PAYMENT,
    "financial obligation": ObligationType.PAYMENT,
    "monetary": ObligationType.PAYMENT,
    "notice": ObligationType.NOTICE,
    "notice obligation": ObligationType.NOTICE,
    "notification": ObligationType.NOTICE,
    "notification obligation": ObligationType.NOTICE,
    "approval": ObligationType.APPROVAL,
    "approval obligation": ObligationType.APPROVAL,
    "consent": ObligationType.APPROVAL,
    "consent requirement": ObligationType.APPROVAL,
    "report": ObligationType.REPORTING,
    "reporting": ObligationType.REPORTING,
    "reporting obligation": ObligationType.REPORTING,
    "reporting requirement": ObligationType.REPORTING,
    "insurance": ObligationType.INSURANCE,
    "insurance obligation": ObligationType.INSURANCE,
    "insurance requirement": ObligationType.INSURANCE,
    "compliance": ObligationType.COMPLIANCE,
    "compliance obligation": ObligationType.COMPLIANCE,
    "regulatory compliance": ObligationType.COMPLIANCE,
    "restriction": ObligationType.RESTRICTIVE,
    "restrictive": ObligationType.RESTRICTIVE,
    "restrictive covenant": ObligationType.RESTRICTIVE,
    "conditional": ObligationType.CONDITIONAL,
    "conditional obligation": ObligationType.CONDITIONAL,
    "condition precedent": ObligationType.CONDITIONAL,
    "survival": ObligationType.SURVIVAL,
    "survival obligation": ObligationType.SURVIVAL,
    "surviving obligation": ObligationType.SURVIVAL,
}

TRIGGER_VARIANTS: dict[str, TriggerType] = {
    "calendar date": TriggerType.CALENDAR,
    "calendar-based": TriggerType.CALENDAR,
    "date based": TriggerType.CALENDAR,
    "date-based": TriggerType.CALENDAR,
    "fixed date": TriggerType.CALENDAR,
    "recurring schedule": TriggerType.CALENDAR,
    "specific date": TriggerType.CALENDAR,
    "event": TriggerType.EVENT,
    "event based": TriggerType.EVENT,
    "event-based": TriggerType.EVENT,
    "triggering event": TriggerType.EVENT,
    "upon occurrence": TriggerType.EVENT,
    "upon receipt of invoice": TriggerType.EVENT,
    "condition based": TriggerType.CONDITION,
    "condition-based": TriggerType.CONDITION,
    "condition precedent": TriggerType.CONDITION,
    "depends on a condition": TriggerType.CONDITION,
    "if condition is met": TriggerType.CONDITION,
    "milestone based": TriggerType.MILESTONE,
    "milestone-based": TriggerType.MILESTONE,
    "project milestone": TriggerType.MILESTONE,
    "completion milestone": TriggerType.MILESTONE,
    "within 10 days of acceptance": TriggerType.MILESTONE,
    "rolling period": TriggerType.ROLLING,
    "recurring": TriggerType.CALENDAR,
    "periodic": TriggerType.ROLLING,
    "within 30 days of the effective date": TriggerType.ROLLING,
    "ongoing": TriggerType.CONTINUOUS,
    "at all times": TriggerType.CONTINUOUS,
    "throughout the term": TriggerType.CONTINUOUS,
    "failure to give notice": TriggerType.NEGATIVE,
    "failure to notify": TriggerType.NEGATIVE,
    "auto-renew": TriggerType.NEGATIVE,
    "auto-renewal": TriggerType.NEGATIVE,
    "automatic renewal": TriggerType.NEGATIVE,
    "deemed acceptance": TriggerType.NEGATIVE,
    "failure to object": TriggerType.NEGATIVE,
    "missed notice window": TriggerType.NEGATIVE,
    "time-barred claim": TriggerType.NEGATIVE,
}

_CANONICAL_OBLIGATION_TYPES = {member.value for member in ObligationType}
_CANONICAL_TRIGGERS = {member.value for member in TriggerType}


def normalize_obligation_type(raw: str | None) -> str | None:
    """Return a canonical obligation code when ``raw`` is confidently recognized."""
    if raw is None or raw in _CANONICAL_OBLIGATION_TYPES:
        return raw
    normalized = OBLIGATION_TYPE_VARIANTS.get(raw.strip().casefold())
    return normalized.value if normalized is not None else raw


def normalize_trigger(raw: str | None) -> str | None:
    """Return a canonical trigger value when ``raw`` is confidently recognized."""
    if raw is None or raw in _CANONICAL_TRIGGERS:
        return raw
    normalized = TRIGGER_VARIANTS.get(raw.strip().casefold())
    return normalized.value if normalized is not None else raw
