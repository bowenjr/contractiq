# Handoff — TASK-05

## Status
COMPLETE

## Files created
- core/gates.py (370 lines)
- core/gate_service.py (133 lines)
- tests/unit/test_gates.py (292 lines)
- tests/unit/test_gate_service.py (163 lines)

## Files modified
- pyproject.toml — added both new TASK-05 modules to the strict-mypy target and allowlist.
- HANDOFF.md — replaced the TASK-04 handoff with this TASK-05 record.

## Test results
`pytest` — 141 passed, 0 failed
`ruff format --check` (changed Python files) — pass
`ruff check` (task validation scope) — pass
`mypy` (new files, strict mode) — pass; 2 source files checked
`python app.py` — application startup completed; `GET /` returned HTTP 200; clean shutdown

## Validation command output

```text
$ pip install -e ".[dev]" && \
  ruff check core/gates.py core/gate_service.py tests/ && \
  mypy core/gates.py core/gate_service.py && \
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
  Created wheel for contractiq: filename=contractiq-0.2.0-0.editable-py3-none-any.whl size=2863 sha256=d51709a5253ee4af8f3e822826a1bff774653066b0918174627b05d4ad94310d
  Stored in directory: /tmp/pip-ephem-wheel-cache-hlrncztg/wheels/20/9a/d3/382869a6f4126bddc7de8dc4b0953478ff12551b12d218b8b0
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
Success: no issues found in 2 source files
============================= test session starts ==============================
platform linux -- Python 3.13.13, pytest-9.1.1, pluggy-1.6.0 -- /home/bowen/dev/projects/contractiq/.venv/bin/python
cachedir: .pytest_cache
rootdir: /home/bowen/dev/projects/contractiq
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.14.2, cov-7.1.0
collecting ... collected 141 items

tests/unit/test_bid_repository.py::test_create_and_get_bid_round_trips_every_field PASSED [  0%]
tests/unit/test_bid_repository.py::test_get_unknown_bid_returns_none PASSED [  1%]
tests/unit/test_bid_repository.py::test_create_duplicate_bid_raises_value_error PASSED [  2%]
tests/unit/test_bid_repository.py::test_list_bids_returns_all_and_filters_by_status PASSED [  2%]
tests/unit/test_bid_repository.py::test_update_bid_changes_field_and_bumps_updated_at PASSED [  3%]
tests/unit/test_bid_repository.py::test_update_bid_upserts_when_bid_does_not_exist PASSED [  4%]
tests/unit/test_bid_repository.py::test_attach_list_and_detach_document PASSED [  4%]
tests/unit/test_bid_repository.py::test_existing_create_document_path_defaults_bid_id_to_null PASSED [  5%]
tests/unit/test_bid_repository.py::test_approval_round_trips_provenance PASSED [  6%]
tests/unit/test_bid_repository.py::test_update_approval_persists_full_model PASSED [  7%]
tests/unit/test_bid_repository.py::test_upsert_gate_record_updates_without_duplicate PASSED [  7%]
tests/unit/test_bid_repository.py::test_overridden_gate_round_trips_residual_risk_note PASSED [  8%]
tests/unit/test_bid_repository.py::test_append_and_list_audit_with_optional_bid_filter PASSED [  9%]
tests/unit/test_bid_repository.py::test_schema_evolution_is_idempotent_and_bid_id_is_nullable_once PASSED [  9%]
tests/unit/test_classifier.py::test_zero_triggers_and_value_below_first_paid_band_is_level_zero PASSED [ 10%]
tests/unit/test_classifier.py::test_value_in_level_two_band_without_triggers_is_level_two PASSED [ 11%]
tests/unit/test_classifier.py::test_liquidated_damages_trigger_beats_level_zero_value PASSED [ 12%]
tests/unit/test_classifier.py::test_level_three_value_beats_level_two_trigger_floor PASSED [ 12%]
tests/unit/test_classifier.py::test_epc_epcm_hint_raises_low_value_bid_to_level_three PASSED [ 13%]
tests/unit/test_classifier.py::test_multiple_triggers_use_maximum_floor_and_sort_highest_first PASSED [ 14%]
tests/unit/test_classifier.py::test_rationale_contains_winning_factor_and_is_non_empty PASSED [ 14%]
tests/unit/test_classifier.py::test_classify_is_deterministic_for_same_input PASSED [ 15%]
tests/unit/test_classifier.py::test_custom_config_changes_classification_outcome PASSED [ 16%]
tests/unit/test_classifier.py::test_malformed_config_json_raises_value_error PASSED [ 17%]
tests/unit/test_classifier.py::test_classify_and_store_persists_result_and_audits_rationale PASSED [ 17%]
tests/unit/test_classifier_config.py::test_missing_config_file_returns_defaults PASSED [ 18%]
tests/unit/test_classifier_config.py::test_valid_config_file_overrides_defaults PASSED [ 19%]
tests/unit/test_classifier_config.py::test_malformed_config_file_raises_value_error PASSED [ 19%]
tests/unit/test_gate_service.py::test_margin_approval_re_evaluation_flips_g4_record PASSED [ 20%]
tests/unit/test_gate_service.py::test_unconfirmed_finding_blocks_g5_until_confirmed PASSED [ 21%]
tests/unit/test_gate_service.py::test_absent_requirements_table_is_not_assessable_and_does_not_block PASSED [ 21%]
tests/unit/test_gate_service.py::test_one_audit_entry_is_written_per_evaluation PASSED [ 22%]
tests/unit/test_gates.py::test_g0_is_met_for_complete_bid_and_unmet_for_zero_value PASSED [ 23%]
tests/unit/test_gates.py::test_g1_requires_obtained_bid_no_bid_approval PASSED [ 24%]
tests/unit/test_gates.py::test_g2_blocks_confirmed_included_unpriced_scope_item PASSED [ 24%]
tests/unit/test_gates.py::test_g2_is_met_when_confirmed_scope_rows_are_priced_and_owned PASSED [ 25%]
tests/unit/test_gates.py::test_g2_unconfirmed_gap_rows_do_not_block PASSED [ 26%]
tests/unit/test_gates.py::test_g2_strategy_is_not_assessable_without_register PASSED [ 26%]
tests/unit/test_gates.py::test_g3_not_assessable_passes_in_v01 PASSED    [ 27%]
tests/unit/test_gates.py::test_g4_margin_rule_depends_on_bid_level[level_3-unmet] PASSED [ 28%]
tests/unit/test_gates.py::test_g4_margin_rule_depends_on_bid_level[level_0-met] PASSED [ 29%]
tests/unit/test_gates.py::test_g4_required_legal_approval_blocks_when_not_obtained PASSED [ 29%]
tests/unit/test_gates.py::test_g5_unconfirmed_material_rule[counts0-unmet] PASSED [ 30%]
tests/unit/test_gates.py::test_g5_unconfirmed_material_rule[counts1-met] PASSED [ 31%]
tests/unit/test_gates.py::test_g5_blocks_when_g4_prior_result_is_unmet PASSED [ 31%]
tests/unit/test_gates.py::test_g5_compliance_matrix_is_not_assessable_when_absent PASSED [ 32%]
tests/unit/test_gates.py::test_only_not_assessable_conditions_pass_and_name_missing_registers PASSED [ 33%]
tests/unit/test_gates.py::test_evaluate_all_threads_prior_gate_results_into_g5 PASSED [ 34%]
tests/unit/test_gates.py::test_gate_evaluation_is_deterministic PASSED   [ 34%]
tests/unit/test_llm_client.py::test_parse_plain_json PASSED              [ 35%]
tests/unit/test_llm_client.py::test_parse_json_in_markdown_fence PASSED  [ 36%]
tests/unit/test_llm_client.py::test_parse_json_after_leading_prose PASSED [ 36%]
tests/unit/test_llm_client.py::test_malformed_json_returns_error_shape PASSED [ 37%]
tests/unit/test_migration_safety.py::test_bid_migration_preserves_pre_existing_documents PASSED [ 38%]
tests/unit/test_migration_safety.py::test_provenance_retrofit_accepts_legacy_obligation_values PASSED [ 39%]
tests/unit/test_pillars.py::test_all_pillars_contains_exactly_seven_members PASSED [ 39%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[money] PASSED [ 40%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[time] PASSED [ 41%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[scope] PASSED [ 41%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[risk_liability] PASSED [ 42%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[relationships] PASSED [ 43%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[administration] PASSED [ 43%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[exit] PASSED [ 44%]
tests/unit/test_pillars.py::test_pillar_weights_are_floats_in_valid_range PASSED [ 45%]
tests/unit/test_pillars.py::test_weights_sum_to_one_for_each_document_type PASSED [ 46%]
tests/unit/test_provenance_retrofit.py::test_default_clause_finding_write_is_ai_unconfirmed PASSED [ 46%]
tests/unit/test_provenance_retrofit.py::test_human_authorship_does_not_implicitly_confirm_finding PASSED [ 47%]
tests/unit/test_provenance_retrofit.py::test_confirm_clause_finding_and_missing_id PASSED [ 48%]
tests/unit/test_provenance_retrofit.py::test_analysis_row_confirmation_round_trip[scope-item] PASSED [ 48%]
tests/unit/test_provenance_retrofit.py::test_analysis_row_confirmation_round_trip[obligation] PASSED [ 49%]
tests/unit/test_provenance_retrofit.py::test_analysis_row_confirmation_round_trip[negotiation-issue] PASSED [ 50%]
tests/unit/test_provenance_retrofit.py::test_count_unconfirmed_before_and_after_confirmations PASSED [ 51%]
tests/unit/test_provenance_retrofit.py::test_backfill_stamps_rows_with_honest_legacy_provenance PASSED [ 51%]
tests/unit/test_provenance_retrofit.py::test_migration_is_idempotent_and_preserves_confirmation PASSED [ 52%]
tests/unit/test_provenance_retrofit.py::test_existing_read_paths_preserve_business_fields PASSED [ 53%]
tests/unit/test_schemas.py::test_every_model_instantiates_from_valid_minimal_data PASSED [ 53%]
tests/unit/test_schemas.py::test_bid_rejects_internal_due_date_after_customer_due_date PASSED [ 54%]
tests/unit/test_schemas.py::test_bid_rejects_malformed_bid_id PASSED     [ 55%]
tests/unit/test_schemas.py::test_bid_rejects_win_probability_above_100 PASSED [ 56%]
tests/unit/test_schemas.py::test_bid_defaults_to_local_only PASSED       [ 56%]
tests/unit/test_schemas.py::test_provenance_rejects_unattributed_human_confirmation PASSED [ 57%]
tests/unit/test_schemas.py::test_provenance_from_ai_is_unconfirmed PASSED [ 58%]
tests/unit/test_schemas.py::test_gate_override_requires_residual_risk_note PASSED [ 58%]
tests/unit/test_schemas.py::test_models_forbid_unknown_fields PASSED     [ 59%]
tests/unit/test_schemas.py::test_pillar_id_matches_existing_pillars PASSED [ 60%]
tests/unit/test_schemas.py::test_salvaged_taxonomies_have_expected_member_counts PASSED [ 60%]
tests/unit/test_schemas.py::test_obligation_type_uses_short_codes_as_values PASSED [ 61%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[performance-PERF] PASSED [ 62%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[delivery obligation-PERF] PASSED [ 63%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[payment-PAY] PASSED [ 63%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[Payment obligation-PAY] PASSED [ 64%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[Financial-PAY] PASSED [ 65%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[notice-NOTC] PASSED [ 65%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[notification obligation-NOTC] PASSED [ 66%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[approval-APPR] PASSED [ 67%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[consent requirement-APPR] PASSED [ 68%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[reporting-RPT] PASSED [ 68%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[reporting requirement-RPT] PASSED [ 69%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[insurance-INS] PASSED [ 70%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[insurance requirement-INS] PASSED [ 70%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[compliance-COMP] PASSED [ 71%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[regulatory compliance-COMP] PASSED [ 72%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[restrictive covenant-REST] PASSED [ 73%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[condition precedent-COND] PASSED [ 73%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[survival obligation-SURV] PASSED [ 74%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[failure to give notice-negative] PASSED [ 75%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[failure to notify-negative] PASSED [ 75%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[auto-renew-negative] PASSED [ 76%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[auto-renewal-negative] PASSED [ 77%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[date-based-calendar] PASSED [ 78%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[recurring schedule-calendar] PASSED [ 78%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[specific date-calendar] PASSED [ 79%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[event-based-event] PASSED [ 80%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[triggering event-event] PASSED [ 80%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[upon receipt of invoice-event] PASSED [ 81%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[condition-based-condition] PASSED [ 82%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[if condition is met-condition] PASSED [ 82%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[milestone-based-milestone] PASSED [ 83%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[project milestone-milestone] PASSED [ 84%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[within 10 days of acceptance-milestone] PASSED [ 85%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[rolling period-rolling] PASSED [ 85%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[recurring-calendar] PASSED [ 86%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[within 30 days of the effective date-rolling] PASSED [ 87%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[ongoing-continuous] PASSED [ 87%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[at all times-continuous] PASSED [ 88%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[throughout the term-continuous] PASSED [ 89%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[deemed acceptance-negative] PASSED [ 90%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[failure to object-negative] PASSED [ 90%]
tests/unit/test_taxonomy.py::test_canonical_obligation_values_pass_through[PERF] PASSED [ 91%]
tests/unit/test_taxonomy.py::test_canonical_obligation_values_pass_through[PAY] PASSED [ 92%]
tests/unit/test_taxonomy.py::test_canonical_obligation_values_pass_through[NOTC] PASSED [ 92%]
tests/unit/test_taxonomy.py::test_canonical_obligation_values_pass_through[INS] PASSED [ 93%]
tests/unit/test_taxonomy.py::test_canonical_obligation_values_pass_through[SURV] PASSED [ 94%]
tests/unit/test_taxonomy.py::test_canonical_trigger_values_pass_through[calendar] PASSED [ 95%]
tests/unit/test_taxonomy.py::test_canonical_trigger_values_pass_through[event] PASSED [ 95%]
tests/unit/test_taxonomy.py::test_canonical_trigger_values_pass_through[condition] PASSED [ 96%]
tests/unit/test_taxonomy.py::test_canonical_trigger_values_pass_through[milestone] PASSED [ 97%]
tests/unit/test_taxonomy.py::test_canonical_trigger_values_pass_through[rolling] PASSED [ 97%]
tests/unit/test_taxonomy.py::test_canonical_trigger_values_pass_through[continuous] PASSED [ 98%]
tests/unit/test_taxonomy.py::test_canonical_trigger_values_pass_through[negative] PASSED [ 99%]
tests/unit/test_taxonomy.py::test_unrecognized_and_none_values_pass_through PASSED [100%]

============================= 141 passed in 52.81s =============================
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
- Treated only explicit gap-like `gap_status` values (`gap`, `open`, `open gap`, `unresolved`,
  and `unresolved gap`, including hyphen variants) as open. Empty values and the existing
  `covered` value do not block; included, unpriced, or ownerless confirmed rows still block
  independently.
- Treated the future-register capability booleans as the task's availability boundary:
  unavailable is `NOT_ASSESSABLE`; available is `MET` until the future register task supplies
  its richer deterministic row result.
- Used the task's register names for capability detection: `requirements`, `supplier_responses`,
  `concession_log`, `reconciliation`, and `bid_strategy`. Supplier and strategy capabilities
  require a row for this bid; the other flags require the named table.
- Filtered `high_severity_findings` in the DB-facing service to human-confirmed rows whose
  severity is exactly `High` (case-insensitive). The pure rules consume only that assembled list.
- A direct G5 evaluation without G2–G4 results treats those dependencies as not passed. The
  public `evaluate_all_gates` path always threads the freshly computed G2–G4 results into G5.
- Added both new modules to the strict-mypy allowlist so the task's mypy command performs real
  checking despite the repository's broad legacy suppression.

## Deviations from the task spec
- None.

## Concerns for review
- G4's high-finding authority rule is intentionally best-effort: one obtained `LEGAL` or
  `EXECUTIVE` approval currently supplies global authority, while a finding can alternatively
  carry a non-empty `authority_note`. A richer finding/approval linkage should replace this once
  that register exists.
- Future register tasks must keep the table names above or update the DB-facing capability
  adapter. The pure gate functions and their condition IDs do not need to change.
- Capability presence alone does not expose future register row detail to `GateContext`; future
  register services should set the capability/result inputs only after their deterministic
  register checks are available.
- The user-provided untracked TASK-02/TASK-03/TASK-04/TASK-05 specs and Windows
  `Zone.Identifier` sidecars were preserved unchanged and intentionally excluded from the commit.

## Reporting requirements from the task

### Pure rules import block

Confirmed: `core/gates.py` has no DB, clock, network, OpenAI, Anthropic, or other LLM import. Its
complete import block is:

```python
from dataclasses import dataclass, replace
from enum import Enum

from pydantic import BaseModel, ConfigDict

from core.enums import ApprovalType, BidLevel, Gate
from core.schemas import Approval, Bid
```

`core/gate_service.py` is the only new module that imports `Database`, `datetime`, and persistence
schemas. It assembles `GateContext`, then delegates every gate decision to the pure engine.

### Condition IDs and v0.1 state

- `g0.bid_complete` — MET-capable
- `g1.bid_no_bid_approved` — MET-capable
- `g2.no_scope_gaps` — MET-capable
- `g2.strategy_recorded` — NOT_ASSESSABLE (`strategy`)
- `g3.suppliers_supported` — NOT_ASSESSABLE (`supplier`)
- `g4.margin_approved` — MET-capable
- `g4.high_findings_have_authority` — MET-capable
- `g4.required_approvals` — MET-capable
- `g5.mandatory_requirements_complete` — NOT_ASSESSABLE (`compliance_matrix`)
- `g5.no_unconfirmed_material` — MET-capable
- `g5.prior_gates_passed` — MET-capable
- `g6.concessions_approved` — NOT_ASSESSABLE (`concession_log`)
- `g7.award_matches_offer` — NOT_ASSESSABLE (`reconciliation`)
- `g7.handover_accepted` — NOT_ASSESSABLE (`handover`)

Every `NOT_ASSESSABLE` condition has zero blocking effect because `GateResult.passed` is true
exactly when `unmet_count == 0`. Summaries report the count and missing register names.

### G5 unconfirmed-material control

Confirmed. G5 sums unconfirmed `clause_findings`, `scope_items`, `obligations`, and
`negotiation_issues`; any nonzero total makes `g5.no_unconfirmed_material` `UNMET` and persists
its description as a G5 blocker.

The temp-DB integration test
`test_unconfirmed_finding_blocks_g5_until_confirmed` proves the full behavior: an unconfirmed
finding blocks G5, `confirm_clause_finding` clears that condition, and reevaluation removes its
persisted blocker. The pure parameterized test `test_g5_unconfirmed_material_rule` separately
proves nonzero and all-zero contexts.

### Best-effort rules to tighten later

- `g4.high_findings_have_authority`: replace global obtained LEGAL/EXECUTIVE approval or free-text
  `authority_note` with explicit finding-to-authority linkage.
- `g3.suppliers_supported`: once supplier rows exist, deterministically inspect mandatory-item
  silence flags rather than treating register capability as satisfied.
- `g5.mandatory_requirements_complete`: once TASK-10 supplies requirement rows, inspect mandatory
  status and evidence fields.
- `g6.concessions_approved`: once concessions exist, require an approver on every concession.
- `g7.award_matches_offer`: once reconciliation exists, inspect unresolved material discrepancies.
- `g7.handover_accepted`: remains capability-wired as NOT_ASSESSABLE until a handover register and
  explicit acceptance field exist.
