from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
EXAMPLES = CONTRACTS / "examples"
INVALID = EXAMPLES / "invalid"

PACKAGE_SCHEMA_PATH = CONTRACTS / "proposal-package-v1.schema.json"
MANIFEST_SCHEMA_PATH = CONTRACTS / "proposal-generation-manifest-v1.schema.json"
PACKAGE_EXAMPLE_PATH = EXAMPLES / "proposal-package-v1.example.json"
MANIFEST_EXAMPLE_PATH = EXAMPLES / "proposal-generation-manifest-v1.example.json"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validator(path: Path) -> Draft202012Validator:
    schema = load_json(path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def canonical_json_bytes(value: Any) -> bytes:
    """Canonical form used by fixtures.

    V1 contract data permits integers but no non-integer JSON numbers, making this
    serialization byte-equivalent to RFC 8785 JCS for the contract fixtures.
    Production adapters must use RFC 8785 JCS as specified by the contract doc.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_canonical(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def assert_sorted(items: list[Any], key: Callable[[Any], Any], label: str) -> None:
    assert items == sorted(items, key=key), f"{label} is not in deterministic V1 order"


def assert_unique_ids(items: list[dict[str, Any]], field: str, label: str) -> None:
    values = [item[field] for item in items]
    assert len(values) == len(set(values)), f"{label} contains duplicate {field} values"


def validate_package_semantics(package: dict[str, Any]) -> None:
    assert_sorted(package["requirements"], lambda x: x["requirement_id"], "requirements")
    assert_unique_ids(package["requirements"], "requirement_id", "requirements")

    for key, id_field in (
        ("inclusions", "scope_item_id"),
        ("exclusions", "scope_item_id"),
        ("interfaces", "interface_id"),
    ):
        values = package["scope"][key]
        assert_sorted(values, lambda x, f=id_field: x[f], f"scope.{key}")
        assert_unique_ids(values, id_field, f"scope.{key}")

    assert_sorted(package["manufacturer_packages"], lambda x: x["package_id"], "manufacturer_packages")
    assert_unique_ids(package["manufacturer_packages"], "package_id", "manufacturer_packages")
    supporting_ids = {d["document_id"] for d in package["supporting_documents"]}
    for item in package["manufacturer_packages"]:
        assert_sorted(item["equipment"], lambda x: x["equipment_id"], f"manufacturer_packages.{item['package_id']}.equipment")
        assert_unique_ids(item["equipment"], "equipment_id", f"manufacturer_packages.{item['package_id']}.equipment")
        assert_sorted(item["commitments"], lambda x: x["commitment_id"], f"manufacturer_packages.{item['package_id']}.commitments")
        assert_unique_ids(item["commitments"], "commitment_id", f"manufacturer_packages.{item['package_id']}.commitments")
        for commitment in item["commitments"]:
            assert commitment["evidence_document_ids"] == sorted(commitment["evidence_document_ids"])
            assert set(commitment["evidence_document_ids"]) <= supporting_ids

    rules = (
        ("vdrl_commitments", "vdrl_id"),
        ("commercial_positions", "commercial_position_id"),
        ("qualifications_and_deviations", "item_id"),
        ("proposal_issue_risks", "risk_id"),
        ("decisions_and_approvals", "decision_id"),
        ("delivery_commitments", "delivery_commitment_id"),
        ("proposal_inputs", "input_key"),
        ("supporting_documents", "document_id"),
    )
    for key, id_field in rules:
        items = package[key]
        assert_sorted(items, lambda x, f=id_field: x[f], key)
        assert_unique_ids(items, id_field, key)

    requirement_ids = {r["requirement_id"] for r in package["requirements"]}
    for item in package["qualifications_and_deviations"]:
        assert item["related_requirement_ids"] == sorted(item["related_requirement_ids"])
        assert set(item["related_requirement_ids"]) <= requirement_ids


def validate_manifest_semantics(manifest: dict[str, Any]) -> None:
    assert_sorted(manifest["included_sections"], lambda x: (x["order"], x["section_key"]), "included_sections")
    assert_sorted(manifest["excluded_sections"], lambda x: (x["order"], x["section_key"]), "excluded_sections")
    assert_sorted(manifest["manual_narratives"], lambda x: x["section_key"], "manual_narratives")
    assert_sorted(manifest["manual_overrides"], lambda x: x["target_path"], "manual_overrides")
    assert_sorted(
        manifest["warnings"],
        lambda x: (x["severity"], x["code"], x["target"] or "", x["message"]),
        "warnings",
    )
    assert_sorted(
        manifest["unresolved_placeholders"],
        lambda x: (x["placeholder"], x["section_key"] or ""),
        "unresolved_placeholders",
    )

    included = {x["section_key"] for x in manifest["included_sections"]}
    excluded = {x["section_key"] for x in manifest["excluded_sections"]}
    assert included.isdisjoint(excluded), "a section cannot be both included and excluded"


@pytest.mark.parametrize("schema_path", [PACKAGE_SCHEMA_PATH, MANIFEST_SCHEMA_PATH])
def test_v1_schemas_are_valid_draft_2020_12(schema_path: Path) -> None:
    Draft202012Validator.check_schema(load_json(schema_path))


def test_valid_proposal_package_fixture() -> None:
    package = load_json(PACKAGE_EXAMPLE_PATH)
    validator(PACKAGE_SCHEMA_PATH).validate(package)
    validate_package_semantics(package)


def test_valid_generation_manifest_fixture_and_package_hash() -> None:
    package = load_json(PACKAGE_EXAMPLE_PATH)
    manifest = load_json(MANIFEST_EXAMPLE_PATH)
    validator(MANIFEST_SCHEMA_PATH).validate(manifest)
    validate_manifest_semantics(manifest)
    assert manifest["source_proposal_package"]["package_id"] == package["package_id"]
    assert manifest["source_proposal_package"]["sha256"] == sha256_canonical(package)


@pytest.mark.parametrize(
    ("schema_path", "fixture"),
    [
        (PACKAGE_SCHEMA_PATH, INVALID / "proposal-package-v1.invalid-absolute-path.json"),
        (MANIFEST_SCHEMA_PATH, INVALID / "proposal-generation-manifest-v1.invalid-path.json"),
        (MANIFEST_SCHEMA_PATH, INVALID / "proposal-generation-manifest-v1.invalid-success-placeholder.json"),
    ],
)
def test_schema_invalid_fixtures_are_rejected(schema_path: Path, fixture: Path) -> None:
    errors = list(validator(schema_path).iter_errors(load_json(fixture)))
    assert errors, f"fixture unexpectedly validated: {fixture.name}"


@pytest.mark.parametrize(
    ("fixture", "semantic_validator"),
    [
        (INVALID / "proposal-package-v1.invalid-order.json", validate_package_semantics),
        (INVALID / "proposal-generation-manifest-v1.invalid-order.json", validate_manifest_semantics),
    ],
)
def test_semantically_invalid_order_fixtures_are_rejected(
    fixture: Path, semantic_validator: Callable[[dict[str, Any]], None]
) -> None:
    data = load_json(fixture)
    schema_path = PACKAGE_SCHEMA_PATH if "proposal-package" in fixture.name else MANIFEST_SCHEMA_PATH
    validator(schema_path).validate(data)
    with pytest.raises(AssertionError):
        semantic_validator(data)


@pytest.mark.parametrize(
    "bad_path",
    [
        "/etc/passwd",
        "../secret.pdf",
        "documents/../secret.pdf",
        "C:/Windows/System32/file.pdf",
        r"C:\\Windows\\file.pdf",
        "documents//file.pdf",
    ],
)
def test_supporting_document_paths_reject_absolute_and_traversal(bad_path: str) -> None:
    package = load_json(PACKAGE_EXAMPLE_PATH)
    package["supporting_documents"][0]["relative_path"] = bad_path
    errors = list(validator(PACKAGE_SCHEMA_PATH).iter_errors(package))
    assert errors


def test_additional_properties_are_rejected() -> None:
    package = load_json(PACKAGE_EXAMPLE_PATH)
    package["unexpected"] = "must fail"
    errors = list(validator(PACKAGE_SCHEMA_PATH).iter_errors(package))
    assert errors


def test_non_utc_timestamp_is_rejected() -> None:
    package = load_json(PACKAGE_EXAMPLE_PATH)
    package["generated_at"] = "2026-09-08T10:30:00-04:00"
    errors = list(validator(PACKAGE_SCHEMA_PATH).iter_errors(package))
    assert errors


def test_empty_collections_are_valid_and_null_collections_are_not() -> None:
    package = load_json(PACKAGE_EXAMPLE_PATH)
    for key in (
        "requirements",
        "manufacturer_packages",
        "vdrl_commitments",
        "commercial_positions",
        "qualifications_and_deviations",
        "proposal_issue_risks",
        "decisions_and_approvals",
        "delivery_commitments",
        "proposal_inputs",
        "supporting_documents",
    ):
        candidate = json.loads(json.dumps(package))
        candidate[key] = []
        validator(PACKAGE_SCHEMA_PATH).validate(candidate)

        candidate[key] = None
        assert list(validator(PACKAGE_SCHEMA_PATH).iter_errors(candidate))


def test_macro_enabled_supporting_document_is_rejected() -> None:
    package = load_json(PACKAGE_EXAMPLE_PATH)
    doc = package["supporting_documents"][0]
    doc["relative_path"] = "documents/macro-enabled.docm"
    doc["media_type"] = "application/vnd.ms-word.document.macroEnabled.12"
    errors = list(validator(PACKAGE_SCHEMA_PATH).iter_errors(package))
    assert errors


def test_declared_media_type_must_match_safe_file_extension() -> None:
    package = load_json(PACKAGE_EXAMPLE_PATH)
    doc = package["supporting_documents"][0]
    doc["relative_path"] = "documents/payload.exe"
    doc["media_type"] = "application/pdf"
    errors = list(validator(PACKAGE_SCHEMA_PATH).iter_errors(package))
    assert errors


def test_external_url_or_action_property_is_rejected() -> None:
    package = load_json(PACKAGE_EXAMPLE_PATH)
    doc = package["supporting_documents"][0]
    doc["action_url"] = "https://example.invalid/run"
    errors = list(validator(PACKAGE_SCHEMA_PATH).iter_errors(package))
    assert errors
