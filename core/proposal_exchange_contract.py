"""Strict, dependency-free validation for Proposal Studio Exchange Contract V1."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, NoReturn, cast

JsonObject = dict[str, Any]
SortKey = Callable[[JsonObject], tuple[str, ...]]

CONTRACT_VERSION = "1.0.0"
PACKAGE_SCHEMA_ID = "urn:proposal-studio:contract:proposal-package:v1"
MANIFEST_SCHEMA_ID = "urn:proposal-studio:contract:proposal-generation-manifest:v1"
CONTRACT_ROOT = Path(__file__).resolve().parents[1] / "contracts" / "proposal-studio" / "v1"
PACKAGE_SCHEMA_PATH = CONTRACT_ROOT / "proposal-package-v1.schema.json"
MANIFEST_SCHEMA_PATH = CONTRACT_ROOT / "proposal-generation-manifest-v1.schema.json"


class ExchangeContractError(ValueError):
    """Raised when an exchange document violates the frozen V1 contract."""


def _reject_constant(value: str) -> NoReturn:
    raise ExchangeContractError(f"Non-finite JSON number is not permitted: {value}")


def _strict_object(pairs: list[tuple[str, Any]]) -> JsonObject:
    value: JsonObject = {}
    for key, item in pairs:
        if key in value:
            raise ExchangeContractError(f"Duplicate JSON object member: {key}")
        value[key] = item
    return value


def strict_json_loads(raw: bytes) -> JsonObject:
    """Parse one UTF-8 JSON object, rejecting BOM, duplicate keys and NaN values."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ExchangeContractError("UTF-8 BOM is not permitted")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ExchangeContractError("Exchange JSON must be strict UTF-8") from exc
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise ExchangeContractError(f"Invalid JSON: {exc.msg} at line {exc.lineno}") from exc
    if not isinstance(parsed, dict):
        raise ExchangeContractError("Exchange document must be a JSON object")
    return cast(JsonObject, parsed)


def canonical_json_bytes(value: object) -> bytes:
    """Serialize with the frozen V1 canonical JSON algorithm."""
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ExchangeContractError("Value cannot be serialized as canonical V1 JSON") from exc


def canonical_sha256(value: object) -> str:
    """Return lowercase SHA-256 of canonical V1 JSON."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def raw_sha256(value: bytes) -> str:
    """Return lowercase SHA-256 of raw bytes."""
    return hashlib.sha256(value).hexdigest()


def _schema_type_matches(instance: object, expected: str) -> bool:
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    return False


def _resolve_ref(root: JsonObject, reference: str) -> JsonObject:
    if not reference.startswith("#/"):
        raise ExchangeContractError(f"Unsupported external schema reference: {reference}")
    current: object = root
    for token in reference[2:].split("/"):
        if not isinstance(current, dict) or token not in current:
            raise ExchangeContractError(f"Broken schema reference: {reference}")
        current = current[token]
    if not isinstance(current, dict):
        raise ExchangeContractError(f"Schema reference is not an object: {reference}")
    return cast(JsonObject, current)


def _format_valid(value: str, expected: str) -> bool:
    try:
        if expected == "date":
            return date.fromisoformat(value).isoformat() == value
        if expected == "date-time":
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return True
    except ValueError:
        return False
    return True


def _fail(path: str, message: str) -> NoReturn:
    raise ExchangeContractError(f"{path}: {message}")


def _validate_schema(instance: object, schema: JsonObject, root: JsonObject, path: str) -> None:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        _validate_schema(instance, _resolve_ref(root, reference), root, path)

    if "const" in schema and instance != schema["const"]:
        _fail(path, f"must equal {schema['const']!r}")
    enum = schema.get("enum")
    if isinstance(enum, list) and instance not in enum:
        _fail(path, f"must be one of {enum!r}")

    one_of = schema.get("oneOf")
    if isinstance(one_of, list):
        successes = 0
        for option in one_of:
            try:
                _validate_schema(instance, cast(JsonObject, option), root, path)
            except ExchangeContractError:
                continue
            successes += 1
        if successes != 1:
            _fail(path, "must match exactly one allowed shape")

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for item in all_of:
            part = cast(JsonObject, item)
            condition = part.get("if")
            if isinstance(condition, dict):
                try:
                    _validate_schema(instance, cast(JsonObject, condition), root, path)
                except ExchangeContractError:
                    continue
                then = part.get("then")
                if isinstance(then, dict):
                    _validate_schema(instance, cast(JsonObject, then), root, path)
            else:
                _validate_schema(instance, part, root, path)

    expected_type = schema.get("type")
    if isinstance(expected_type, str) and not _schema_type_matches(instance, expected_type):
        _fail(path, f"must be {expected_type}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        if isinstance(required, list):
            for field in required:
                if isinstance(field, str) and field not in instance:
                    _fail(path, f"missing required field {field!r}")
        properties = schema.get("properties", {})
        property_map = cast(JsonObject, properties) if isinstance(properties, dict) else {}
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(instance) - set(property_map))
            if unknown:
                _fail(path, f"unknown field(s): {', '.join(unknown)}")
        for key, value in instance.items():
            child = property_map.get(key)
            if isinstance(child, dict):
                _validate_schema(value, cast(JsonObject, child), root, f"{path}.{key}")

    if isinstance(instance, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(instance) < minimum:
            _fail(path, f"requires at least {minimum} item(s)")
        if isinstance(maximum, int) and len(instance) > maximum:
            _fail(path, f"permits at most {maximum} item(s)")
        if schema.get("uniqueItems") is True:
            canonical = [canonical_json_bytes(value) for value in instance]
            if len(canonical) != len(set(canonical)):
                _fail(path, "items must be unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, value in enumerate(instance):
                _validate_schema(
                    value,
                    cast(JsonObject, item_schema),
                    root,
                    f"{path}[{index}]",
                )

    if isinstance(instance, str):
        minimum_length = schema.get("minLength")
        maximum_length = schema.get("maxLength")
        if isinstance(minimum_length, int) and len(instance) < minimum_length:
            _fail(path, f"must contain at least {minimum_length} character(s)")
        if isinstance(maximum_length, int) and len(instance) > maximum_length:
            _fail(path, f"must contain at most {maximum_length} character(s)")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, instance) is None:
            _fail(path, "does not match the required pattern")
        format_name = schema.get("format")
        if isinstance(format_name, str) and not _format_valid(instance, format_name):
            _fail(path, f"must be a valid {format_name}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if isinstance(instance, float) and not math.isfinite(instance):
            _fail(path, "must be finite")
        minimum_value = schema.get("minimum")
        maximum_value = schema.get("maximum")
        if isinstance(minimum_value, (int, float)) and instance < minimum_value:
            _fail(path, f"must be at least {minimum_value}")
        if isinstance(maximum_value, (int, float)) and instance > maximum_value:
            _fail(path, f"must be at most {maximum_value}")


def validate_against_schema(value: JsonObject, schema_path: Path) -> None:
    """Validate against the frozen schema subset used by V1 without network resolution."""
    schema = strict_json_loads(schema_path.read_bytes())
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise ExchangeContractError("Frozen schema does not declare Draft 2020-12")
    _validate_schema(value, schema, schema, "$")


def _assert_ordered(
    items: Sequence[JsonObject], key: SortKey, label: str, identity: str | None = None
) -> None:
    if list(items) != sorted(items, key=key):
        raise ExchangeContractError(f"{label} is not in deterministic V1 order")
    if identity is not None:
        values = [str(item[identity]) for item in items]
        if len(values) != len(set(values)):
            raise ExchangeContractError(f"{label} contains duplicate {identity} values")


def _field_sort_key(field: str) -> SortKey:
    def key(item: JsonObject) -> tuple[str, ...]:
        return (str(item[field]),)

    return key


def validate_package_semantics(package: JsonObject) -> None:
    """Validate V1 ordering, identity uniqueness and package cross-references."""
    rules = (
        ("requirements", "requirement_id"),
        ("manufacturer_packages", "package_id"),
        ("vdrl_commitments", "vdrl_id"),
        ("commercial_positions", "commercial_position_id"),
        ("qualifications_and_deviations", "item_id"),
        ("proposal_issue_risks", "risk_id"),
        ("decisions_and_approvals", "decision_id"),
        ("delivery_commitments", "delivery_commitment_id"),
        ("proposal_inputs", "input_key"),
        ("supporting_documents", "document_id"),
    )
    for field, identity in rules:
        items = cast(list[JsonObject], package[field])
        _assert_ordered(items, _field_sort_key(identity), field, identity)
    scope = cast(JsonObject, package["scope"])
    for field, identity in (
        ("inclusions", "scope_item_id"),
        ("exclusions", "scope_item_id"),
        ("interfaces", "interface_id"),
    ):
        items = cast(list[JsonObject], scope[field])
        _assert_ordered(items, _field_sort_key(identity), f"scope.{field}", identity)
    document_ids = {
        str(item["document_id"]) for item in cast(list[JsonObject], package["supporting_documents"])
    }
    for package_item in cast(list[JsonObject], package["manufacturer_packages"]):
        equipment = cast(list[JsonObject], package_item["equipment"])
        commitments = cast(list[JsonObject], package_item["commitments"])
        _assert_ordered(equipment, _field_sort_key("equipment_id"), "equipment", "equipment_id")
        _assert_ordered(
            commitments,
            _field_sort_key("commitment_id"),
            "manufacturer commitments",
            "commitment_id",
        )
        for commitment in commitments:
            evidence = cast(list[str], commitment["evidence_document_ids"])
            if evidence != sorted(evidence) or not set(evidence) <= document_ids:
                raise ExchangeContractError("Manufacturer evidence references are invalid")
    requirement_ids = {
        str(item["requirement_id"]) for item in cast(list[JsonObject], package["requirements"])
    }
    for item in cast(list[JsonObject], package["qualifications_and_deviations"]):
        related = cast(list[str], item["related_requirement_ids"])
        if related != sorted(related) or not set(related) <= requirement_ids:
            raise ExchangeContractError("Qualification requirement references are invalid")


def validate_manifest_semantics(manifest: JsonObject) -> None:
    """Validate V1 generation-manifest ordering and section disjointness."""
    _assert_ordered(
        cast(list[JsonObject], manifest["included_sections"]),
        lambda item: (f"{int(item['order']):05d}", str(item["section_key"])),
        "included_sections",
    )
    _assert_ordered(
        cast(list[JsonObject], manifest["excluded_sections"]),
        lambda item: (f"{int(item['order']):05d}", str(item["section_key"])),
        "excluded_sections",
    )
    _assert_ordered(
        cast(list[JsonObject], manifest["manual_narratives"]),
        _field_sort_key("section_key"),
        "manual_narratives",
    )
    _assert_ordered(
        cast(list[JsonObject], manifest["manual_overrides"]),
        _field_sort_key("target_path"),
        "manual_overrides",
    )
    _assert_ordered(
        cast(list[JsonObject], manifest["warnings"]),
        lambda item: (
            str(item["severity"]),
            str(item["code"]),
            str(item["target"] or ""),
            str(item["message"]),
        ),
        "warnings",
    )
    _assert_ordered(
        cast(list[JsonObject], manifest["unresolved_placeholders"]),
        lambda item: (str(item["placeholder"]), str(item["section_key"] or "")),
        "unresolved_placeholders",
    )
    included = {
        str(item["section_key"]) for item in cast(list[JsonObject], manifest["included_sections"])
    }
    excluded = {
        str(item["section_key"]) for item in cast(list[JsonObject], manifest["excluded_sections"])
    }
    if not included.isdisjoint(excluded):
        raise ExchangeContractError("A section cannot be both included and excluded")


def validate_package(package: JsonObject) -> None:
    """Validate one parsed proposal package against all frozen V1 rules."""
    validate_against_schema(package, PACKAGE_SCHEMA_PATH)
    validate_package_semantics(package)


def validate_manifest(manifest: JsonObject) -> None:
    """Validate one parsed generation manifest against all frozen V1 rules."""
    validate_against_schema(manifest, MANIFEST_SCHEMA_PATH)
    validate_manifest_semantics(manifest)


def area_hashes(package: JsonObject) -> dict[str, str]:
    """Hash the authoritative V1 areas used for deterministic staleness detection."""
    fields: Mapping[str, tuple[str, ...]] = {
        "bid_identity": ("bid", "customer", "opportunity"),
        "requirements": ("requirements",),
        "scope_and_interfaces": ("scope",),
        "manufacturer_and_vdrl": ("manufacturer_packages", "vdrl_commitments"),
        "commercial_and_risk": (
            "commercial_positions",
            "qualifications_and_deviations",
            "proposal_issue_risks",
        ),
        "decisions_and_approvals": ("decisions_and_approvals",),
        "pricing": ("selected_pricing_scenario",),
        "delivery_and_inputs": ("delivery_commitments", "proposal_inputs"),
        "supporting_documents": ("supporting_documents",),
    }
    return {
        area: canonical_sha256({field: package[field] for field in members})
        for area, members in fields.items()
    }
