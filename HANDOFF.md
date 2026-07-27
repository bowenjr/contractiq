# Handoff — TASK-02

## Status
COMPLETE

## Files created
- core/bid_repository.py (448 lines)
- tests/unit/test_bid_repository.py (289 lines)
- tests/unit/test_migration_safety.py (21 lines)

## Files modified
- pyproject.toml — added `core/bid_repository.py` to the default strict-mypy target
  and skipped analysis of the imported legacy `core.database` implementation.
- tests/conftest.py — added the reusable `bid_repo` fixture.
- HANDOFF.md — replaced the TASK-01 handoff with this TASK-02 record.

## Test results
`pytest` — 41 passed, 0 failed
`ruff check` — pass
`mypy` (new files) — pass; 1 source file checked
`python -u app.py` — startup complete on `0.0.0.0:8000`; shut down cleanly after smoke test

## Validation command output
```text
Obtaining file:///home/bowen/dev/projects/contractiq
  Installing build dependencies: started
  Installing build dependencies: finished with status 'done'
  Checking if build backend supports build_editable: started
  Checking if build backend supports build_editable: finished with status 'done'
  Getting requirements to build editable: started
  Getting requirements to build editable: finished with status 'done'
  Preparing editable metadata (pyproject.toml): started
  Preparing editable metadata (pyproject.toml): finished with status 'done'
Requirement already satisfied: fastapi>=0.111.0 in ./.venv/lib/python3.13/site-packages (from contractiq==0.2.0) (0.140.0)
Requirement already satisfied: uvicorn>=0.29.0 in ./.venv/lib/python3.13/site-packages (from uvicorn[standard]>=0.29.0->contractiq==0.2.0) (0.51.0)
Requirement already satisfied: python-multipart>=0.0.9 in ./.venv/lib/python3.13/site-packages (from contractiq==0.2.0) (0.0.32)
Requirement already satisfied: jinja2>=3.1.4 in ./.venv/lib/python3.13/site-packages (from contractiq==0.2.0) (3.1.6)
Requirement already satisfied: pymupdf>=1.24.0 in ./.venv/lib/python3.13/site-packages (from contractiq==0.2.0) (1.28.0)
Requirement already satisfied: python-docx>=1.1.0 in ./.venv/lib/python3.13/site-packages (from contractiq==0.2.0) (1.2.0)
Requirement already satisfied: reportlab>=4.2.0 in ./.venv/lib/python3.13/site-packages (from contractiq==0.2.0) (5.0.0)
Requirement already satisfied: openpyxl>=3.1.0 in ./.venv/lib/python3.13/site-packages (from contractiq==0.2.0) (3.1.5)
Requirement already satisfied: pandas>=2.0.0 in ./.venv/lib/python3.13/site-packages (from contractiq==0.2.0) (3.0.5)
Requirement already satisfied: requests>=2.32.0 in ./.venv/lib/python3.13/site-packages (from contractiq==0.2.0) (2.34.2)
Requirement already satisfied: pydantic>=2.6 in ./.venv/lib/python3.13/site-packages (from contractiq==0.2.0) (2.13.4)
Requirement already satisfied: pytest>=8 in ./.venv/lib/python3.13/site-packages (from contractiq==0.2.0) (9.1.1)
Requirement already satisfied: pytest-cov in ./.venv/lib/python3.13/site-packages (from contractiq==0.2.0) (7.1.0)
Requirement already satisfied: ruff in ./.venv/lib/python3.13/site-packages (from contractiq==0.2.0) (0.16.0)
Requirement already satisfied: mypy in ./.venv/lib/python3.13/site-packages (from contractiq==0.2.0) (2.3.0)
Requirement already satisfied: starlette>=0.46.0 in ./.venv/lib/python3.13/site-packages (from fastapi>=0.111.0->contractiq==0.2.0) (1.3.1)
Requirement already satisfied: typing-extensions>=4.8.0 in ./.venv/lib/python3.13/site-packages (from fastapi>=0.111.0->contractiq==0.2.0) (4.16.0)
Requirement already satisfied: typing-inspection>=0.4.2 in ./.venv/lib/python3.13/site-packages (from fastapi>=0.111.0->contractiq==0.2.0) (0.4.2)
Requirement already satisfied: annotated-doc>=0.0.2 in ./.venv/lib/python3.13/site-packages (from fastapi>=0.111.0->contractiq==0.2.0) (0.0.4)
Requirement already satisfied: MarkupSafe>=2.0 in ./.venv/lib/python3.13/site-packages (from jinja2>=3.1.4->contractiq==0.2.0) (3.0.3)
Requirement already satisfied: et-xmlfile in ./.venv/lib/python3.13/site-packages (from openpyxl>=3.1.0->contractiq==0.2.0) (2.0.0)
Requirement already satisfied: numpy>=1.26.0 in ./.venv/lib/python3.13/site-packages (from pandas>=2.0.0->contractiq==0.2.0) (2.5.1)
Requirement already satisfied: python-dateutil>=2.8.2 in ./.venv/lib/python3.13/site-packages (from pandas>=2.0.0->contractiq==0.2.0) (2.9.0.post0)
Requirement already satisfied: annotated-types>=0.6.0 in ./.venv/lib/python3.13/site-packages (from pydantic>=2.6->contractiq==0.2.0) (0.8.0)
Requirement already satisfied: pydantic-core==2.46.4 in ./.venv/lib/python3.13/site-packages (from pydantic>=2.6->contractiq==0.2.0) (2.46.4)
Requirement already satisfied: iniconfig>=1.0.1 in ./.venv/lib/python3.13/site-packages (from pytest>=8->contractiq==0.2.0) (2.3.0)
Requirement already satisfied: packaging>=22 in ./.venv/lib/python3.13/site-packages (from pytest>=8->contractiq==0.2.0) (26.2)
Requirement already satisfied: pluggy<2,>=1.5 in ./.venv/lib/python3.13/site-packages (from pytest>=8->contractiq==0.2.0) (1.6.0)
Requirement already satisfied: pygments>=2.7.2 in ./.venv/lib/python3.13/site-packages (from pytest>=8->contractiq==0.2.0) (2.20.0)
Requirement already satisfied: six>=1.5 in ./.venv/lib/python3.13/site-packages (from python-dateutil>=2.8.2->pandas>=2.0.0->contractiq==0.2.0) (1.17.0)
Requirement already satisfied: lxml>=3.1.0 in ./.venv/lib/python3.13/site-packages (from python-docx>=1.1.0->contractiq==0.2.0) (6.1.1)
Requirement already satisfied: pillow>=9.0.0 in ./.venv/lib/python3.13/site-packages (from reportlab>=4.2.0->contractiq==0.2.0) (12.3.0)
Requirement already satisfied: charset-normalizer in ./.venv/lib/python3.13/site-packages (from reportlab>=4.2.0->contractiq==0.2.0) (3.4.9)
Requirement already satisfied: idna<4,>=2.5 in ./.venv/lib/python3.13/site-packages (from requests>=2.32.0->contractiq==0.2.0) (3.18)
Requirement already satisfied: urllib3<3,>=1.26 in ./.venv/lib/python3.13/site-packages (from requests>=2.32.0->contractiq==0.2.0) (2.7.0)
Requirement already satisfied: certifi>=2023.5.7 in ./.venv/lib/python3.13/site-packages (from requests>=2.32.0->contractiq==0.2.0) (2026.7.22)
Requirement already satisfied: anyio<5,>=3.6.2 in ./.venv/lib/python3.13/site-packages (from starlette>=0.46.0->fastapi>=0.111.0->contractiq==0.2.0) (4.14.2)
Requirement already satisfied: click>=7.0 in ./.venv/lib/python3.13/site-packages (from uvicorn>=0.29.0->uvicorn[standard]>=0.29.0->contractiq==0.2.0) (8.4.2)
Requirement already satisfied: h11>=0.8 in ./.venv/lib/python3.13/site-packages (from uvicorn>=0.29.0->uvicorn[standard]>=0.29.0->contractiq==0.2.0) (0.16.0)
Requirement already satisfied: httptools>=0.8.0 in ./.venv/lib/python3.13/site-packages (from uvicorn[standard]>=0.29.0->contractiq==0.2.0) (0.8.0)
Requirement already satisfied: python-dotenv>=0.13 in ./.venv/lib/python3.13/site-packages (from uvicorn[standard]>=0.29.0->contractiq==0.2.0) (1.2.2)
Requirement already satisfied: pyyaml>=5.1 in ./.venv/lib/python3.13/site-packages (from uvicorn[standard]>=0.29.0->contractiq==0.2.0) (6.0.3)
Requirement already satisfied: uvloop>=0.15.1 in ./.venv/lib/python3.13/site-packages (from uvicorn[standard]>=0.29.0->contractiq==0.2.0) (0.22.1)
Requirement already satisfied: watchfiles>=0.20 in ./.venv/lib/python3.13/site-packages (from uvicorn[standard]>=0.29.0->contractiq==0.2.0) (1.2.0)
Requirement already satisfied: websockets>=13.0 in ./.venv/lib/python3.13/site-packages (from uvicorn[standard]>=0.29.0->contractiq==0.2.0) (16.1.1)
Requirement already satisfied: mypy_extensions>=1.0.0 in ./.venv/lib/python3.13/site-packages (from mypy->contractiq==0.2.0) (1.1.0)
Requirement already satisfied: pathspec>=1.0.0 in ./.venv/lib/python3.13/site-packages (from mypy->contractiq==0.2.0) (1.1.1)
Requirement already satisfied: librt>=0.13.0 in ./.venv/lib/python3.13/site-packages (from mypy->contractiq==0.2.0) (0.13.0)
Requirement already satisfied: ast-serialize<1.0.0,>=0.6.0 in ./.venv/lib/python3.13/site-packages (from mypy->contractiq==0.2.0) (0.6.0)
Requirement already satisfied: coverage>=7.10.6 in ./.venv/lib/python3.13/site-packages (from coverage[toml]>=7.10.6->pytest-cov->contractiq==0.2.0) (7.15.2)
Building wheels for collected packages: contractiq
  Building editable for contractiq (pyproject.toml): started
  Building editable for contractiq (pyproject.toml): finished with status 'done'
  Created wheel for contractiq: filename=contractiq-0.2.0-0.editable-py3-none-any.whl size=2863 sha256=5c9fcd925ecea11d87fb758d911f18c87e5efded53b65a8f767f548582d1d258
  Stored in directory: /tmp/pip-ephem-wheel-cache-t573y2o3/wheels/20/9a/d3/382869a6f4126bddc7de8dc4b0953478ff12551b12d218b8b0
Successfully built contractiq
Installing collected packages: contractiq
  Attempting uninstall: contractiq
    Found existing installation: contractiq 0.2.0
    Uninstalling contractiq-0.2.0:
      Successfully uninstalled contractiq-0.2.0
Successfully installed contractiq-0.2.0

[notice] A new release of pip is available: 26.0.1 -> 26.1.2
[notice] To update, run: pip install --upgrade pip
All checks passed!
Success: no issues found in 1 source file
============================= test session starts ==============================
platform linux -- Python 3.13.13, pytest-9.1.1, pluggy-1.6.0 -- /home/bowen/dev/projects/contractiq/.venv/bin/python
cachedir: .pytest_cache
rootdir: /home/bowen/dev/projects/contractiq
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.14.2, cov-7.1.0
collecting ... collected 41 items

tests/unit/test_bid_repository.py::test_create_and_get_bid_round_trips_every_field PASSED [  2%]
tests/unit/test_bid_repository.py::test_get_unknown_bid_returns_none PASSED [  4%]
tests/unit/test_bid_repository.py::test_create_duplicate_bid_raises_value_error PASSED [  7%]
tests/unit/test_bid_repository.py::test_list_bids_returns_all_and_filters_by_status PASSED [  9%]
tests/unit/test_bid_repository.py::test_update_bid_changes_field_and_bumps_updated_at PASSED [ 12%]
tests/unit/test_bid_repository.py::test_update_bid_upserts_when_bid_does_not_exist PASSED [ 14%]
tests/unit/test_bid_repository.py::test_attach_list_and_detach_document PASSED [ 17%]
tests/unit/test_bid_repository.py::test_existing_create_document_path_defaults_bid_id_to_null PASSED [ 19%]
tests/unit/test_bid_repository.py::test_approval_round_trips_provenance PASSED [ 21%]
tests/unit/test_bid_repository.py::test_update_approval_persists_full_model PASSED [ 24%]
tests/unit/test_bid_repository.py::test_upsert_gate_record_updates_without_duplicate PASSED [ 26%]
tests/unit/test_bid_repository.py::test_overridden_gate_round_trips_residual_risk_note PASSED [ 29%]
tests/unit/test_bid_repository.py::test_append_and_list_audit_with_optional_bid_filter PASSED [ 31%]
tests/unit/test_bid_repository.py::test_schema_evolution_is_idempotent_and_bid_id_is_nullable_once PASSED [ 34%]
tests/unit/test_llm_client.py::test_parse_plain_json PASSED              [ 36%]
tests/unit/test_llm_client.py::test_parse_json_in_markdown_fence PASSED  [ 39%]
tests/unit/test_llm_client.py::test_parse_json_after_leading_prose PASSED [ 41%]
tests/unit/test_llm_client.py::test_malformed_json_returns_error_shape PASSED [ 43%]
tests/unit/test_migration_safety.py::test_bid_migration_preserves_pre_existing_documents PASSED [ 46%]
tests/unit/test_pillars.py::test_all_pillars_contains_exactly_seven_members PASSED [ 48%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[money] PASSED [ 51%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[time] PASSED [ 53%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[scope] PASSED [ 56%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[risk_liability] PASSED [ 58%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[relationships] PASSED [ 60%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[administration] PASSED [ 63%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[exit] PASSED [ 65%]
tests/unit/test_pillars.py::test_pillar_weights_are_floats_in_valid_range PASSED [ 68%]
tests/unit/test_pillars.py::test_weights_sum_to_one_for_each_document_type PASSED [ 70%]
tests/unit/test_schemas.py::test_every_model_instantiates_from_valid_minimal_data PASSED [ 73%]
tests/unit/test_schemas.py::test_bid_rejects_internal_due_date_after_customer_due_date PASSED [ 75%]
tests/unit/test_schemas.py::test_bid_rejects_malformed_bid_id PASSED     [ 78%]
tests/unit/test_schemas.py::test_bid_rejects_win_probability_above_100 PASSED [ 80%]
tests/unit/test_schemas.py::test_bid_defaults_to_local_only PASSED       [ 82%]
tests/unit/test_schemas.py::test_provenance_rejects_unattributed_human_confirmation PASSED [ 85%]
tests/unit/test_schemas.py::test_provenance_from_ai_is_unconfirmed PASSED [ 87%]
tests/unit/test_schemas.py::test_gate_override_requires_residual_risk_note PASSED [ 90%]
tests/unit/test_schemas.py::test_models_forbid_unknown_fields PASSED     [ 92%]
tests/unit/test_schemas.py::test_pillar_id_matches_existing_pillars PASSED [ 95%]
tests/unit/test_schemas.py::test_salvaged_taxonomies_have_expected_member_counts PASSED [ 97%]
tests/unit/test_schemas.py::test_obligation_type_uses_short_codes_as_values PASSED [100%]

============================= 41 passed in 12.10s ==============================
app imports OK
```

## Decisions I made
- Implemented the required "bid documents" capability as
  `BidRepository.list_documents_for_bid()` rather than a SQL view, matching the task's helper
  option and the existing document dict-row style.
- `update_bid()` uses SQLite `ON CONFLICT` as a full-object upsert and writes a copied model with
  `updated_at=datetime.now(UTC)`, leaving the caller's Pydantic object unchanged.
- Added a typed `_conn()` adapter in the new repository and configured mypy to skip analysis of
  the imported legacy `core.database` module. This keeps the new file strict without changing or
  suppressing errors inside the new repository.
- Used an explicit `PRAGMA table_info(documents)` guard for `bid_id`, rather than the legacy
  exception-swallowing migration loop, because TASK-02 explicitly requires an inspected,
  idempotent `ALTER TABLE`.

## Deviations from the task spec
- None.

## Concerns for review
- `audit_log.bid_id` intentionally has no foreign key because the supplied SQL defines none.
- The existing `Database._evolve_schema()` does not inspect `PRAGMA table_info`; it attempts each
  `ALTER TABLE` and catches every `Exception`. TASK-03 should use explicit column inspection so
  genuine migration failures are not mistaken for harmless duplicate-column errors.
- Existing analysis tables contain live rows and currently have no provenance fields. TASK-03
  will need an additive/backfill-compatible strategy; adding a new non-null provenance column
  directly would be unsafe for those rows.
- The user-provided untracked TASK-02 spec and its Windows `Zone.Identifier` sidecar were
  preserved unchanged and are intentionally outside this implementation commit.

## Reporting requirements from the task
- `PRAGMA table_info(documents)` after constructing `BidRepository` twice:

```text
PRAGMA table_info(documents) bid_id rows:
{'cid': 37, 'name': 'bid_id', 'type': 'TEXT', 'notnull': 0, 'dflt_value': None, 'pk': 0}
bid_id column count: 1
bid_id nullable (notnull=0): True
```

- The existing document path remains valid:

```text
existing create_document bid_id: None
```

- `_evolve_bid_schema()` is safe to run repeatedly. This was verified by constructing two
  `BidRepository` instances over the same temp-file `Database`, asserting that construction did
  not raise, and confirming via the PRAGMA output above that exactly one nullable `bid_id` column
  exists.
- TASK-03 note: the legacy evolution method broadly swallows `ALTER TABLE` errors and the
  existing analysis tables may already contain rows. Provenance retrofits should inspect columns
  explicitly and preserve those existing rows during additive migration/backfill.
