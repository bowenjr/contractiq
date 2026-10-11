"""Pure deterministic comparison rules for discovery obligation collisions.

This module deliberately has no database or LLM dependency.  A title/lifecycle dedup key only
selects possible matches; these material facts decide whether two findings are the same obligation.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date

from core.deliverable_discovery import DeliverableProposal, SourceCitation

DESCRIPTION_TOKEN_OVERLAP_THRESHOLD = 0.75
"""Minimum Sørensen-Dice content-token overlap needed to establish description equivalence."""

COLLISION_KEY_MARKER = ":collision-"
SOURCE_FINGERPRINT_LENGTH = 16

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_VDDR_ITEM_RE = re.compile(r"vendor drawing and data requirements,\s*item\s+([^,;]+)", re.I)


@dataclass(frozen=True)
class CollisionSource:
    """The source-reference fields material to an equivalence decision."""

    file_id: str
    locator: str
    quote: str | None


@dataclass(frozen=True)
class ObligationFacts:
    """Normalised comparison input detached from storage and orchestration."""

    description: str | None
    due_date: date | None
    due_date_basis: str | None
    timing_text: str | None
    responsible_party: str | None
    condition_text: str | None
    source_section: str | None
    sources: tuple[CollisionSource, ...]


@dataclass(frozen=True)
class ObligationComparison:
    equivalent: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ExistingObligation:
    """One immutable stored proposal and the facts needed to compare a re-finding."""

    proposal_id: str
    dedup_key: str
    facts: ObligationFacts


@dataclass(frozen=True)
class DiscoveryCollision:
    """A canonical proposal pair to persist as immutable collision evidence."""

    proposal_id: str
    other_proposal_id: str
    reasons: tuple[str, ...]

    @classmethod
    def canonical(
        cls, proposal_id: str, other_proposal_id: str, reasons: tuple[str, ...]
    ) -> DiscoveryCollision:
        first, second = sorted((proposal_id, other_proposal_id))
        return cls(first, second, tuple(dict.fromkeys(reasons)))


def _normalise(value: str | None) -> str:
    return " ".join(_TOKEN_RE.findall((value or "").casefold()))


def _tokens(value: str | None) -> set[str]:
    return set(_TOKEN_RE.findall((value or "").casefold()))


def description_token_overlap(left: str | None, right: str | None) -> float:
    """Return Sørensen-Dice overlap for normalised content tokens, from 0.0 to 1.0."""
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return 2 * len(left_tokens & right_tokens) / (len(left_tokens) + len(right_tokens))


def _source(citation: SourceCitation) -> CollisionSource:
    return CollisionSource(
        file_id=str(citation.document_version_id or citation.received_file_id),
        locator=citation.locator,
        quote=citation.quote,
    )


def facts_from_proposal(proposal: DeliverableProposal) -> ObligationFacts:
    """Project a proposal onto only the facts used by the collision rules."""
    classification = proposal.classification
    return ObligationFacts(
        description=classification.description if classification is not None else None,
        due_date=proposal.due_date,
        due_date_basis=proposal.due_date_basis.value,
        timing_text=proposal.due_date_text,
        responsible_party=(
            classification.responsible_party if classification is not None else None
        ),
        condition_text=proposal.condition_text,
        source_section=classification.source_section if classification is not None else None,
        sources=tuple(_source(source) for source in proposal.sources),
    )


def base_dedup_key(dedup_key: str) -> str:
    """Return the historical title/lifecycle key without a collision suffix."""
    return dedup_key.split(COLLISION_KEY_MARKER, 1)[0]


def source_fingerprint(facts: ObligationFacts) -> str:
    """Stable fingerprint used only to distinguish a later colliding proposal's key."""
    material = "\n".join(
        sorted(
            f"{source.file_id}|{source.locator}|{_normalise(source.quote)}"
            for source in facts.sources
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:SOURCE_FINGERPRINT_LENGTH]


def collision_dedup_key(dedup_key: str, facts: ObligationFacts) -> str:
    """Add a deterministic source-derived suffix while leaving the base key untouched."""
    return f"{base_dedup_key(dedup_key)}{COLLISION_KEY_MARKER}{source_fingerprint(facts)}"


def _same_located_quote(left: ObligationFacts, right: ObligationFacts) -> bool:
    for left_source in left.sources:
        left_quote = _normalise(left_source.quote)
        if not left_quote:
            continue
        for right_source in right.sources:
            if (
                left_source.file_id == right_source.file_id
                and left_source.locator == right_source.locator
                and left_quote == _normalise(right_source.quote)
            ):
                return True
    return False


def _same_quote(left: ObligationFacts, right: ObligationFacts) -> bool:
    left_quotes = {_normalise(source.quote) for source in left.sources}
    right_quotes = {_normalise(source.quote) for source in right.sources}
    left_quotes.discard("")
    right_quotes.discard("")
    return bool(left_quotes & right_quotes)


def _timing(facts: ObligationFacts) -> tuple[str, str, str] | None:
    due = facts.due_date.isoformat() if facts.due_date is not None else ""
    text = _normalise(facts.timing_text)
    if not due and not text:
        return None
    return due, facts.due_date_basis or "", text


def _vddr_item(source_section: str | None) -> str | None:
    match = _VDDR_ITEM_RE.search(source_section or "")
    return _normalise(match.group(1)) if match else None


def compare_obligations(left: ObligationFacts, right: ObligationFacts) -> ObligationComparison:
    """Decide equivalence using source identity, hard conflicts, and description overlap.

    A verified quote at the same file and locator is identity evidence and wins immediately.
    Otherwise, non-empty unequal timing, responsible-party, or condition facts are hard conflicts.
    VDDR item codes are source identities: different codes cannot describe the same rule row, and a
    VDDR row cannot merge into a prose/model finding without the same verified quote.  Finally,
    descriptions must meet :data:`DESCRIPTION_TOKEN_OVERLAP_THRESHOLD`; missing descriptions are
    uncertain unless the findings carry the same verified quote.
    """
    if _same_located_quote(left, right):
        return ObligationComparison(True, ())

    reasons: list[str] = []
    left_timing = _timing(left)
    right_timing = _timing(right)
    if left_timing is not None and right_timing is not None and left_timing != right_timing:
        reasons.append("timing")

    for reason, left_value, right_value in (
        ("responsible party", left.responsible_party, right.responsible_party),
        ("condition", left.condition_text, right.condition_text),
    ):
        normal_left = _normalise(left_value)
        normal_right = _normalise(right_value)
        if normal_left and normal_right and normal_left != normal_right:
            reasons.append(reason)

    left_item = _vddr_item(left.source_section)
    right_item = _vddr_item(right.source_section)
    if left_item and right_item and left_item != right_item:
        reasons.append("source reference")
    elif bool(left_item) != bool(right_item) and not _same_quote(left, right):
        reasons.append("source reference")

    if reasons:
        return ObligationComparison(False, tuple(reasons))

    if _same_quote(left, right):
        return ObligationComparison(True, ())

    overlap = description_token_overlap(left.description, right.description)
    if overlap < DESCRIPTION_TOKEN_OVERLAP_THRESHOLD:
        return ObligationComparison(False, ("description",))
    return ObligationComparison(True, ())


def collisions_between(proposals: list[DeliverableProposal]) -> list[DiscoveryCollision]:
    """Return all non-equivalent pairs sharing a base title/lifecycle key."""
    collisions: list[DiscoveryCollision] = []
    for index, proposal in enumerate(proposals):
        for other in proposals[index + 1 :]:
            if base_dedup_key(proposal.dedup_key) != base_dedup_key(other.dedup_key):
                continue
            comparison = compare_obligations(
                facts_from_proposal(proposal), facts_from_proposal(other)
            )
            if not comparison.equivalent:
                collisions.append(
                    DiscoveryCollision.canonical(
                        proposal.proposal_id, other.proposal_id, comparison.reasons
                    )
                )
    return collisions


__all__ = [
    "COLLISION_KEY_MARKER",
    "DESCRIPTION_TOKEN_OVERLAP_THRESHOLD",
    "DiscoveryCollision",
    "ExistingObligation",
    "ObligationComparison",
    "ObligationFacts",
    "base_dedup_key",
    "collision_dedup_key",
    "collisions_between",
    "compare_obligations",
    "description_token_overlap",
    "facts_from_proposal",
]
