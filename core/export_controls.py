"""Pure shared controls for outward CSV data and manufacturer evidence."""

from collections.abc import Iterable

DANGEROUS_CSV_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def csv_safe_cell(value: object) -> object:
    """Neutralize spreadsheet formulas without changing persisted source data."""
    if not isinstance(value, str):
        return value
    trimmed = value.lstrip()
    if not (value.startswith(("\t", "\r", "\n")) or trimmed.startswith(DANGEROUS_CSV_PREFIXES[:4])):
        return value
    return "'" + value


def csv_safe_row(values: Iterable[object]) -> list[object]:
    """Return an export-only neutralized row suitable for ``csv.writer``."""
    return [csv_safe_cell(value) for value in values]


def _present(value: object | None) -> bool:
    return value is not None and bool(str(value).strip())


def manufacturer_confirmation_clear(
    verification_status: object,
    response_source: object | None,
    response_received_date: object | None,
) -> bool:
    """Decide authoritative OPS-05B confirmation from status and actual evidence."""
    status = str(verification_status)
    if status == "NOT_APPLICABLE":
        return True
    return (
        status == "CONFIRMED_COMPLIANT"
        and _present(response_source)
        and _present(response_received_date)
    )


def manufacturer_confirmation_issue(
    verification_status: object,
    response_source: object | None,
    response_received_date: object | None,
) -> str | None:
    """Explain why a recorded manufacturer result remains unresolved."""
    if manufacturer_confirmation_clear(
        verification_status, response_source, response_received_date
    ):
        return None
    status = str(verification_status)
    if status == "CONFIRMED_COMPLIANT":
        missing: list[str] = []
        if not _present(response_source):
            missing.append("response source")
        if not _present(response_received_date):
            missing.append("response date")
        return "Recorded compliant confirmation lacks " + " and ".join(missing)
    return f"Manufacturer verification remains {status.replace('_', ' ').lower()}"
