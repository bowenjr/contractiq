# Handoff — TASK-04

## Status
COMPLETE

## Files created
- core/classifier.py (101 lines)
- core/classifier_config.py (91 lines)
- core/classifier_service.py (51 lines)
- tests/unit/test_classifier.py (157 lines)
- tests/unit/test_classifier_config.py (46 lines)

## Files modified
- core/taxonomy.py — corrected the `recurring` trigger variant from `ROLLING` to `CALENDAR`.
- tests/unit/test_taxonomy.py — updated the `recurring` normalization assertion to `calendar`.
- pyproject.toml — added all three new TASK-04 modules to the strict-mypy target and allowlist.
- HANDOFF.md — replaced the TASK-03 handoff with this TASK-04 record.

## Test results
`pytest` — 120 passed, 0 failed
`ruff format --check` (changed Python files) — pass
`ruff check` (task validation scope) — pass
`mypy` (new files, strict mode) — pass; 3 source files checked
`python app.py` — application startup completed; `GET /` returned HTTP 200; clean shutdown

## Validation command output

```text
$ pip install -e ".[dev]" && \
  ruff check core/classifier.py core/classifier_config.py core/classifier_service.py tests/ && \
  mypy core/classifier.py core/classifier_config.py core/classifier_service.py && \
  pytest -v && \
  python -c "import app; print('app imports OK')"

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
  Created wheel for contractiq: filename=contractiq-0.2.0-0.editable-py3-none-any.whl size=2863 sha256=9950c41d6fdf5f37131f1ea4b37920adfbd3ca69cb5e957e3f334970f759efda
  Stored in directory: /tmp/pip-ephem-wheel-cache-7lskhxfa/wheels/20/9a/d3/382869a6f4126bddc7de8dc4b0953478ff12551b12d218b8b0
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
Success: no issues found in 3 source files
============================= test session starts ==============================
platform linux -- Python 3.13.13, pytest-9.1.1, pluggy-1.6.0 -- /home/bowen/dev/projects/contractiq/.venv/bin/python
cachedir: .pytest_cache
rootdir: /home/bowen/dev/projects/contractiq
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.14.2, cov-7.1.0
collecting ... collected 120 items

tests/unit/test_bid_repository.py::test_create_and_get_bid_round_trips_every_field PASSED [  0%]
tests/unit/test_bid_repository.py::test_get_unknown_bid_returns_none PASSED [  1%]
tests/unit/test_bid_repository.py::test_create_duplicate_bid_raises_value_error PASSED [  2%]
tests/unit/test_bid_repository.py::test_list_bids_returns_all_and_filters_by_status PASSED [  3%]
tests/unit/test_bid_repository.py::test_update_bid_changes_field_and_bumps_updated_at PASSED [  4%]
tests/unit/test_bid_repository.py::test_update_bid_upserts_when_bid_does_not_exist PASSED [  5%]
tests/unit/test_bid_repository.py::test_attach_list_and_detach_document PASSED [  5%]
tests/unit/test_bid_repository.py::test_existing_create_document_path_defaults_bid_id_to_null PASSED [  6%]
tests/unit/test_bid_repository.py::test_approval_round_trips_provenance PASSED [  7%]
tests/unit/test_bid_repository.py::test_update_approval_persists_full_model PASSED [  8%]
tests/unit/test_bid_repository.py::test_upsert_gate_record_updates_without_duplicate PASSED [  9%]
tests/unit/test_bid_repository.py::test_overridden_gate_round_trips_residual_risk_note PASSED [ 10%]
tests/unit/test_bid_repository.py::test_append_and_list_audit_with_optional_bid_filter PASSED [ 10%]
tests/unit/test_bid_repository.py::test_schema_evolution_is_idempotent_and_bid_id_is_nullable_once PASSED [ 11%]
tests/unit/test_classifier.py::test_zero_triggers_and_value_below_first_paid_band_is_level_zero PASSED [ 12%]
tests/unit/test_classifier.py::test_value_in_level_two_band_without_triggers_is_level_two PASSED [ 13%]
tests/unit/test_classifier.py::test_liquidated_damages_trigger_beats_level_zero_value PASSED [ 14%]
tests/unit/test_classifier.py::test_level_three_value_beats_level_two_trigger_floor PASSED [ 15%]
tests/unit/test_classifier.py::test_epc_epcm_hint_raises_low_value_bid_to_level_three PASSED [ 15%]
tests/unit/test_classifier.py::test_multiple_triggers_use_maximum_floor_and_sort_highest_first PASSED [ 16%]
tests/unit/test_classifier.py::test_rationale_contains_winning_factor_and_is_non_empty PASSED [ 17%]
tests/unit/test_classifier.py::test_classify_is_deterministic_for_same_input PASSED [ 18%]
tests/unit/test_classifier.py::test_custom_config_changes_classification_outcome PASSED [ 19%]
tests/unit/test_classifier.py::test_malformed_config_json_raises_value_error PASSED [ 20%]
tests/unit/test_classifier.py::test_classify_and_store_persists_result_and_audits_rationale PASSED [ 20%]
tests/unit/test_classifier_config.py::test_missing_config_file_returns_defaults PASSED [ 21%]
tests/unit/test_classifier_config.py::test_valid_config_file_overrides_defaults PASSED [ 22%]
tests/unit/test_classifier_config.py::test_malformed_config_file_raises_value_error PASSED [ 23%]
tests/unit/test_llm_client.py::test_parse_plain_json PASSED              [ 24%]
tests/unit/test_llm_client.py::test_parse_json_in_markdown_fence PASSED  [ 25%]
tests/unit/test_llm_client.py::test_parse_json_after_leading_prose PASSED [ 25%]
tests/unit/test_llm_client.py::test_malformed_json_returns_error_shape PASSED [ 26%]
tests/unit/test_migration_safety.py::test_bid_migration_preserves_pre_existing_documents PASSED [ 27%]
tests/unit/test_migration_safety.py::test_provenance_retrofit_accepts_legacy_obligation_values PASSED [ 28%]
tests/unit/test_pillars.py::test_all_pillars_contains_exactly_seven_members PASSED [ 29%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[money] PASSED [ 30%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[time] PASSED [ 30%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[scope] PASSED [ 31%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[risk_liability] PASSED [ 32%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[relationships] PASSED [ 33%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[administration] PASSED [ 34%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[exit] PASSED [ 35%]
tests/unit/test_pillars.py::test_pillar_weights_are_floats_in_valid_range PASSED [ 35%]
tests/unit/test_pillars.py::test_weights_sum_to_one_for_each_document_type PASSED [ 36%]
tests/unit/test_provenance_retrofit.py::test_default_clause_finding_write_is_ai_unconfirmed PASSED [ 37%]
tests/unit/test_provenance_retrofit.py::test_human_authorship_does_not_implicitly_confirm_finding PASSED [ 38%]
tests/unit/test_provenance_retrofit.py::test_confirm_clause_finding_and_missing_id PASSED [ 39%]
tests/unit/test_provenance_retrofit.py::test_analysis_row_confirmation_round_trip[scope-item] PASSED [ 40%]
tests/unit/test_provenance_retrofit.py::test_analysis_row_confirmation_round_trip[obligation] PASSED [ 40%]
tests/unit/test_provenance_retrofit.py::test_analysis_row_confirmation_round_trip[negotiation-issue] PASSED [ 41%]
tests/unit/test_provenance_retrofit.py::test_count_unconfirmed_before_and_after_confirmations PASSED [ 42%]
tests/unit/test_provenance_retrofit.py::test_backfill_stamps_rows_with_honest_legacy_provenance PASSED [ 43%]
tests/unit/test_provenance_retrofit.py::test_migration_is_idempotent_and_preserves_confirmation PASSED [ 44%]
tests/unit/test_provenance_retrofit.py::test_existing_read_paths_preserve_business_fields PASSED [ 45%]
tests/unit/test_schemas.py::test_every_model_instantiates_from_valid_minimal_data PASSED [ 45%]
tests/unit/test_schemas.py::test_bid_rejects_internal_due_date_after_customer_due_date PASSED [ 46%]
tests/unit/test_schemas.py::test_bid_rejects_malformed_bid_id PASSED     [ 47%]
tests/unit/test_schemas.py::test_bid_rejects_win_probability_above_100 PASSED [ 48%]
tests/unit/test_schemas.py::test_bid_defaults_to_local_only PASSED       [ 49%]
tests/unit/test_schemas.py::test_provenance_rejects_unattributed_human_confirmation PASSED [ 50%]
tests/unit/test_schemas.py::test_provenance_from_ai_is_unconfirmed PASSED [ 50%]
tests/unit/test_schemas.py::test_gate_override_requires_residual_risk_note PASSED [ 51%]
tests/unit/test_schemas.py::test_models_forbid_unknown_fields PASSED     [ 52%]
tests/unit/test_schemas.py::test_pillar_id_matches_existing_pillars PASSED [ 53%]
tests/unit/test_schemas.py::test_salvaged_taxonomies_have_expected_member_counts PASSED [ 54%]
tests/unit/test_schemas.py::test_obligation_type_uses_short_codes_as_values PASSED [ 55%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[performance-PERF] PASSED [ 55%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[delivery obligation-PERF] PASSED [ 56%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[payment-PAY] PASSED [ 57%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[Payment obligation-PAY] PASSED [ 58%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[Financial-PAY] PASSED [ 59%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[notice-NOTC] PASSED [ 60%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[notification obligation-NOTC] PASSED [ 60%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[approval-APPR] PASSED [ 61%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[consent requirement-APPR] PASSED [ 62%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[reporting-RPT] PASSED [ 63%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[reporting requirement-RPT] PASSED [ 64%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[insurance-INS] PASSED [ 65%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[insurance requirement-INS] PASSED [ 65%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[compliance-COMP] PASSED [ 66%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[regulatory compliance-COMP] PASSED [ 67%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[restrictive covenant-REST] PASSED [ 68%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[condition precedent-COND] PASSED [ 69%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[survival obligation-SURV] PASSED [ 70%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[failure to give notice-negative] PASSED [ 70%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[failure to notify-negative] PASSED [ 71%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[auto-renew-negative] PASSED [ 72%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[auto-renewal-negative] PASSED [ 73%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[date-based-calendar] PASSED [ 74%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[recurring schedule-calendar] PASSED [ 75%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[specific date-calendar] PASSED [ 75%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[event-based-event] PASSED [ 76%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[triggering event-event] PASSED [ 77%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[upon receipt of invoice-event] PASSED [ 78%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[condition-based-condition] PASSED [ 79%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[if condition is met-condition] PASSED [ 80%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[milestone-based-milestone] PASSED [ 80%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[project milestone-milestone] PASSED [ 81%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[within 10 days of acceptance-milestone] PASSED [ 82%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[rolling period-rolling] PASSED [ 83%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[recurring-calendar] PASSED [ 84%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[within 30 days of the effective date-rolling] PASSED [ 85%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[ongoing-continuous] PASSED [ 85%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[at all times-continuous] PASSED [ 86%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[throughout the term-continuous] PASSED [ 87%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[deemed acceptance-negative] PASSED [ 88%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[failure to object-negative] PASSED [ 89%]
tests/unit/test_taxonomy.py::test_canonical_obligation_values_pass_through[PERF] PASSED [ 90%]
tests/unit/test_taxonomy.py::test_canonical_obligation_values_pass_through[PAY] PASSED [ 90%]
tests/unit/test_taxonomy.py::test_canonical_obligation_values_pass_through[NOTC] PASSED [ 91%]
tests/unit/test_taxonomy.py::test_canonical_obligation_values_pass_through[INS] PASSED [ 92%]
tests/unit/test_taxonomy.py::test_canonical_obligation_values_pass_through[SURV] PASSED [ 93%]
tests/unit/test_taxonomy.py::test_canonical_trigger_values_pass_through[calendar] PASSED [ 94%]
tests/unit/test_taxonomy.py::test_canonical_trigger_values_pass_through[event] PASSED [ 95%]
tests/unit/test_taxonomy.py::test_canonical_trigger_values_pass_through[condition] PASSED [ 95%]
tests/unit/test_taxonomy.py::test_canonical_trigger_values_pass_through[milestone] PASSED [ 96%]
tests/unit/test_taxonomy.py::test_canonical_trigger_values_pass_through[rolling] PASSED [ 97%]
tests/unit/test_taxonomy.py::test_canonical_trigger_values_pass_through[continuous] PASSED [ 98%]
tests/unit/test_taxonomy.py::test_canonical_trigger_values_pass_through[negative] PASSED [ 99%]
tests/unit/test_taxonomy.py::test_unrecognized_and_none_values_pass_through PASSED [100%]

============================= 120 passed in 11.33s =============================
app imports OK
```

Live startup smoke output:

```text
ContractIQ starting on http://localhost:8000
LM Studio: http://10.0.0.10:1234 | Read timeout: 3600s | Connect timeout: 30s |
Max doc chars: 60,000
INFO: Uvicorn running on http://0.0.0.0:8000
INFO: Started server process
INFO: Waiting for application startup.
Recovery check complete
Knowledge Base: 8 positions, 14 escalation rules, 6 product profiles, 15 commercial terms
INFO: Application startup complete.
INFO: 127.0.0.1 - "GET / HTTP/1.1" 200 OK
INFO: Shutting down
INFO: Application shutdown complete.
```

## Decisions I made
- The JSON override mirrors the declared structures: `value_bands` is a list of
  `[threshold, level]` pairs and `trigger_floors` is a mapping of trigger values to level values.
- A missing explicit config path behaves like the absent default runtime path and returns the
  committed placeholders. A present but malformed or invalid file raises a path-specific
  `ValueError`; it never silently falls back.
- Config files fully replace both structures rather than merging confidential values with
  placeholders.
- Equal-floor triggers retain their input order because Python's stable sort makes that behavior
  deterministic.
- `classify_and_store` raises `ValueError` for an unknown bid and records the result, sorted
  trigger set, and rationale list as JSON in a `bid_classified` audit entry.
- Added all new modules to the repository's strict-mypy allowlist so the required mypy command
  performs real checking despite the broad legacy suppression.

## Deviations from the task spec
- None.

## Concerns for review
- **Jason should confirm the Level-4/3/2 trigger-floor mapping.** The assignments below are the
  task's defensible interpretation of report §8.1, but the report does not state these floors as
  facts.
- The user-provided untracked TASK-02/TASK-03/TASK-04 specs and Windows `Zone.Identifier`
  sidecars were preserved unchanged and intentionally excluded from the commit.

## Reporting requirements from the task

### No LLM imports

Confirmed. There is no LLM, OpenAI, Anthropic, HTTP, or network import in any new TASK-04 file.
`core/classifier.py` has no DB, clock, network, or LLM dependency. Given the same
`ClassificationInput` and `ClassifierConfig`, `classify` returns the same
`ClassificationResult`; the deterministic-equality test passes.

The separately requested DB-facing `core/classifier_service.py` is the only new module that
touches persistence and the clock, solely to update the bid and create the required timestamped
audit entry. It delegates the classification decision itself to the pure function.

### Trigger-floor assignments

The committed defaults are:

- Level 4: `LIQUIDATED_DAMAGES`, `BONDS_OR_GUARANTEES`, `INTERNATIONAL_EXPOSURE`,
  `EPC_FLOWDOWN`
- Level 3: `NON_STANDARD_TERMS`, `EXTENDED_PAYMENT_OR_HOLDBACK`, `WARRANTY_EXTENSION`,
  `NON_CANCELLABLE_PRODUCT`
- Level 2: `MULTIPLE_MANUFACTURERS`, `SUBSTANTIAL_VENDOR_DATA`, `FIELD_SERVICES`,
  `LONG_DURATION`, `UNCLEAR_SCOPE`

**Jason should confirm the Level-4/3/2 floor mapping.** This is a judgment call: it is a
defensible reading of the report, not a floor assignment stated as fact in the report. The mapping
can be tuned through the gitignored runtime config after the pilot.

### `recurring` taxonomy correction

Confirmed. `TRIGGER_VARIANTS["recurring"]` now maps to `TriggerType.CALENDAR`, and the
parameterized taxonomy test asserts `normalize_trigger("recurring") == "calendar"`.

### Confidential threshold protection

Confirmed. No real Westburne thresholds or local classifier config were committed. The only
committed bands are the explicitly labeled placeholder `DEFAULT_VALUE_BANDS`:

- CAD 0 → Level 0
- CAD 50,000 → Level 1
- CAD 250,000 → Level 2
- CAD 1,000,000 → Level 3

At runtime, `data/classifier_config.json` overrides them when present. The repository already
gitignores the entire `data/` directory.
