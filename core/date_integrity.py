"""Deterministic checks on dates and evidence quotations proposed by the analysis model.

The model may suggest a due date or quote a sentence. Neither is trusted: a date is *stated* only
when it is printed in the passage, *calculated* only by this module from a recorded trigger and an
unambiguous calendar rule, and anything else is a *proposal* a person must confirm. A quotation is
kept only when it can be found, word for word, in the passage it claims to come from.

No model is called here (AGENTS.md section 3: control logic is deterministic).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

_MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "fourteen": 14,
    "thirty": 30,
}
_RULE = re.compile(
    r"(?P<n>\d+|" + "|".join(_NUMBER_WORDS) + r")\s+"
    r"(?P<kind>calendar\s+days?|business\s+days?|working\s+days?|days?)\s+"
    r"(?P<direction>before|after)\s+(?:the\s+)?(?:rfp\s+)?(?P<event>closing|issue)\b",
    re.IGNORECASE,
)
DUE_DATE_BASIS_MARKER = "Due date basis:"
MAX_QUOTE_CHARS = 500
QUOTE_CONTEXT_CHARS = 250


class DueDateBasis(StrEnum):
    """How much a due date can be trusted."""

    STATED = "STATED"  # printed in the customer's document
    CALCULATED = "CALCULATED"  # computed here from a recorded trigger and a calendar-day rule
    PROPOSED = "PROPOSED"  # suggested by the model; a person must confirm it
    UNKNOWN = "UNKNOWN"  # no date


@dataclass(frozen=True)
class DueDateEvidence:
    """The trusted classification of one proposed due date."""

    basis: DueDateBasis
    value: date | None
    """The date to use. Set for STATED and CALCULATED only."""
    proposed: date | None
    """The date the model suggested. Informational unless the basis is PROPOSED."""
    rule_text: str | None
    """The customer's wording of the date or timing rule, verified to occur in the passage."""

    @property
    def effective(self) -> date | None:
        """The date that may be used without a person confirming it."""
        return self.value if self.basis in {DueDateBasis.STATED, DueDateBasis.CALCULATED} else None


def _collapse(text: str) -> str:
    return " ".join(text.split())


def date_is_stated(passage: str, value: date) -> bool:
    """Whether ``value`` is written in ``passage`` in a common date format."""
    month = _MONTHS[value.month - 1]
    short = month[:3]
    day = rf"0?{value.day}(?:st|nd|rd|th)?"
    month_pattern = rf"(?:{month}|{short}\.?)"
    patterns = (
        rf"{value.year}[-/.]0?{value.month}[-/.]0?{value.day}\b",
        rf"\b{day}[\s\-/,]+(?:of\s+)?{month_pattern}[\s\-/,]+{value.year}\b",
        rf"\b{month_pattern}[\s\-/]+{day}\b[\s,\-/]+{value.year}\b",
    )
    text = _collapse(passage)
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _contains(passage: str, fragment: str) -> bool:
    return bool(fragment.strip()) and _collapse(fragment).lower() in _collapse(passage).lower()


def classify_due_date(
    passage: str,
    model_date: date | None,
    model_text: str | None,
    triggers: Mapping[str, date] | None = None,
) -> DueDateEvidence:
    """Classify a model-suggested due date against the passage and recorded triggers.

    ``triggers`` maps ``"closing"`` and ``"issue"`` to dates a person recorded on the Bid.
    """
    rule_text = model_text.strip() if model_text and _contains(passage, model_text) else None
    if model_date is not None and date_is_stated(passage, model_date):
        return DueDateEvidence(DueDateBasis.STATED, model_date, model_date, rule_text)
    rule = _RULE.search(rule_text) if rule_text else None
    if rule is not None:
        kind = rule.group("kind").lower()
        trigger = (triggers or {}).get(rule.group("event").lower())
        if kind.startswith("calendar") and trigger is not None:
            raw = rule.group("n").lower()
            count = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
            offset = timedelta(days=count)
            computed = (
                trigger - offset
                if rule.group("direction").lower() == "before"
                else trigger + offset
            )
            return DueDateEvidence(DueDateBasis.CALCULATED, computed, model_date, rule_text)
    if model_date is not None:
        # Business-day rules are not calculated: "Business Day" is the customer's to define.
        return DueDateEvidence(DueDateBasis.PROPOSED, None, model_date, rule_text)
    return DueDateEvidence(DueDateBasis.UNKNOWN, None, None, rule_text)


_CELL_LABEL = re.compile(r"(?<![A-Za-z0-9])[A-Z]{1,3}\d{1,6}: ")


def _fold(passage: str, drop_cell_labels: bool) -> tuple[str, list[int]]:
    """Case- and spacing-folded text with each character's index in ``passage``.

    A spreadsheet passage labels every cell (``B78: Provide State``); with ``drop_cell_labels`` the
    labels are left out, so a quotation of the cell values alone still matches.
    """
    skipped: set[int] = set()
    if drop_cell_labels:
        for label in _CELL_LABEL.finditer(passage):
            skipped.update(range(label.start(), label.end()))
    collapsed: list[str] = []
    origin: list[int] = []
    previous_space = True
    for index, char in enumerate(passage):
        if index in skipped:
            continue
        if char.isspace():
            if not previous_space:
                collapsed.append(" ")
                origin.append(index)
            previous_space = True
        else:
            collapsed.append(char.lower())
            origin.append(index)
            previous_space = False
    return "".join(collapsed), origin


def locate_quote(passage: str, quote: str | None) -> tuple[int, int] | None:
    """Return the ``(start, end)`` span of ``quote`` in ``passage``, ignoring case and spacing.

    A quotation of spreadsheet cell values matches even though the passage prefixes each cell with
    its coordinate; the returned span then covers those labels too.
    """
    if not quote or not quote.strip():
        return None
    needle = _collapse(quote).lower()
    if len(needle) < 12:
        return None
    for drop_cell_labels in (False, True):
        haystack, origin = _fold(passage, drop_cell_labels)
        found = haystack.find(needle)
        if found >= 0:
            return origin[found], origin[found + len(needle) - 1] + 1
    return None


def evidence_window(passage: str, span: tuple[int, int], limit: int = 2000) -> str:
    """The quoted sentence with surrounding context, at most ``limit`` characters."""
    start, end = span
    first = max(0, start - QUOTE_CONTEXT_CHARS)
    last = min(len(passage), end + QUOTE_CONTEXT_CHARS)
    if first > 0:
        boundary = passage.find(" ", first, start)
        first = boundary + 1 if boundary >= 0 else first
    if last < len(passage):
        boundary = passage.rfind(" ", end, last)
        last = boundary if boundary >= 0 else last
    window = passage[first:last].strip()
    return window[:limit]


def deliverable_date_note(
    description: str, fixed_due_date: object, created_by_discovery: bool
) -> str | None:
    """How an accepted deliverable's due date is known, or ``None`` when nothing needs saying.

    Deliverables accepted from a discovery finding record the basis in their description. One
    accepted before that was recorded, with a date, is reported as unverified: its date came from
    the analysis and nothing checked it against the source.
    """
    for line in description.splitlines():
        if line.startswith(DUE_DATE_BASIS_MARKER):
            return line[len(DUE_DATE_BASIS_MARKER) :].strip().rstrip(".")
    if created_by_discovery and fixed_due_date:
        return "proposed by the analysis; not checked against the source"
    return None
