# Handoff — TASK-06

## Status
COMPLETE

## Files created
- core/materiality.py (28 lines)
- core/readiness.py (141 lines)
- core/readiness_service.py (164 lines)
- tests/unit/test_materiality.py (12 lines)
- tests/unit/test_readiness.py (158 lines)
- tests/unit/test_readiness_service.py (234 lines)

## Files modified
- core/gates.py — made G1 bid/no-bid approval proportional so the task-required Level-0/no-approval case is clear; higher-level behavior is unchanged.
- HANDOFF.md — replaced the TASK-05 handoff with this TASK-06 record.

## Test results
`pytest` — 169 passed, 0 failed
`ruff format --check` (changed Python files) — pass
`ruff check` (task validation scope) — pass
`mypy` (new files, strict mode) — pass; 3 source files checked
`python app.py` compatibility — `import app` passed; app import/startup path remains intact

## Validation command output

```text
$ pip install -e ".[dev]" && \
  ruff check core/readiness.py core/readiness_service.py core/materiality.py tests/ && \
  mypy core/readiness.py core/readiness_service.py core/materiality.py && \
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
Requirement already satisfied: fastapi>=0.111.0 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from contractiq==0.2.0) (0.141.1)
Requirement already satisfied: uvicorn>=0.29.0 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from uvicorn[standard]>=0.29.0->contractiq==0.2.0) (0.52.1)
Requirement already satisfied: python-multipart>=0.0.9 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from contractiq==0.2.0) (0.0.32)
Requirement already satisfied: jinja2>=3.1.4 in /usr/lib/python3/dist-packages (from contractiq==0.2.0) (3.1.6)
Requirement already satisfied: pymupdf>=1.24.0 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from contractiq==0.2.0) (1.28.0)
Requirement already satisfied: python-docx>=1.1.0 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from contractiq==0.2.0) (1.2.0)
Requirement already satisfied: reportlab>=4.2.0 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from contractiq==0.2.0) (5.0.0)
Requirement already satisfied: openpyxl>=3.1.0 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from contractiq==0.2.0) (3.1.5)
Requirement already satisfied: pandas>=2.0.0 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from contractiq==0.2.0) (3.0.5)
Requirement already satisfied: requests>=2.32.0 in /usr/lib/python3/dist-packages (from contractiq==0.2.0) (2.32.5)
Requirement already satisfied: pydantic>=2.6 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from contractiq==0.2.0) (2.13.4)
Requirement already satisfied: pytest>=8 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from contractiq==0.2.0) (9.1.1)
Requirement already satisfied: pytest-cov in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from contractiq==0.2.0) (7.1.0)
Requirement already satisfied: ruff in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from contractiq==0.2.0) (0.16.1)
Requirement already satisfied: mypy in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from contractiq==0.2.0) (2.3.0)
Requirement already satisfied: starlette>=0.46.0 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from fastapi>=0.111.0->contractiq==0.2.0) (1.4.0)
Requirement already satisfied: typing-extensions>=4.8.0 in /usr/lib/python3/dist-packages (from fastapi>=0.111.0->contractiq==0.2.0) (4.15.0)
Requirement already satisfied: typing-inspection>=0.4.2 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from fastapi>=0.111.0->contractiq==0.2.0) (0.4.2)
Requirement already satisfied: annotated-doc>=0.0.2 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from fastapi>=0.111.0->contractiq==0.2.0) (0.0.5)
Requirement already satisfied: MarkupSafe>=2.0 in /usr/lib/python3/dist-packages (from jinja2>=3.1.4->contractiq==0.2.0) (3.0.3)
Requirement already satisfied: et-xmlfile in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from openpyxl>=3.1.0->contractiq==0.2.0) (2.0.0)
Requirement already satisfied: numpy>=2.3.3 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from pandas>=2.0.0->contractiq==0.2.0) (2.5.1)
Requirement already satisfied: python-dateutil>=2.8.2 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from pandas>=2.0.0->contractiq==0.2.0) (2.9.0.post0)
Requirement already satisfied: annotated-types>=0.6.0 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from pydantic>=2.6->contractiq==0.2.0) (0.8.0)
Requirement already satisfied: pydantic-core==2.46.4 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from pydantic>=2.6->contractiq==0.2.0) (2.46.4)
Requirement already satisfied: iniconfig>=1.0.1 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from pytest>=8->contractiq==0.2.0) (2.3.0)
Requirement already satisfied: packaging>=22 in /usr/lib/python3/dist-packages (from pytest>=8->contractiq==0.2.0) (26.0)
Requirement already satisfied: pluggy<2,>=1.5 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from pytest>=8->contractiq==0.2.0) (1.6.0)
Requirement already satisfied: pygments>=2.7.2 in /usr/lib/python3/dist-packages (from pytest>=8->contractiq==0.2.0) (2.19.2)
Requirement already satisfied: six>=1.5 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from python-dateutil>=2.8.2->pandas>=2.0.0->contractiq==0.2.0) (1.17.0)
Requirement already satisfied: lxml>=3.1.0 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from python-docx>=1.1.0->contractiq==0.2.0) (6.1.1)
Requirement already satisfied: pillow>=9.0.0 in /usr/lib/python3/dist-packages (from reportlab>=4.2.0->contractiq==0.2.0) (12.1.1)
Requirement already satisfied: charset-normalizer in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from reportlab>=4.2.0->contractiq==0.2.0) (3.4.9)
Requirement already satisfied: chardet>=3.0.2 in /usr/lib/python3/dist-packages (from requests>=2.32.0->contractiq==0.2.0) (5.2.0)
Requirement already satisfied: idna<4,>=2.5 in /usr/lib/python3/dist-packages (from requests>=2.32.0->contractiq==0.2.0) (3.11)
Requirement already satisfied: urllib3<3,>=1.21.1 in /usr/lib/python3/dist-packages (from requests>=2.32.0->contractiq==0.2.0) (2.6.3)
Requirement already satisfied: certifi>=2017.4.17 in /usr/lib/python3/dist-packages (from requests>=2.32.0->contractiq==0.2.0) (2026.1.4)
Requirement already satisfied: anyio<5,>=3.6.2 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from starlette>=0.46.0->fastapi>=0.111.0->contractiq==0.2.0) (4.14.2)
Requirement already satisfied: click>=7.0 in /usr/lib/python3/dist-packages (from uvicorn>=0.29.0->uvicorn[standard]>=0.29.0->contractiq==0.2.0) (8.1.8)
Requirement already satisfied: h11>=0.8 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from uvicorn>=0.29.0->uvicorn[standard]>=0.29.0->contractiq==0.2.0) (0.16.0)
Requirement already satisfied: httptools>=0.8.0 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from uvicorn[standard]>=0.29.0->contractiq==0.2.0) (0.8.0)
Requirement already satisfied: python-dotenv>=0.13 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from uvicorn[standard]>=0.29.0->contractiq==0.2.0) (1.2.2)
Requirement already satisfied: pyyaml>=5.1 in /usr/lib/python3/dist-packages (from uvicorn[standard]>=0.29.0->contractiq==0.2.0) (6.0.3)
Requirement already satisfied: uvloop>=0.15.1 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from uvicorn[standard]>=0.29.0->contractiq==0.2.0) (0.22.1)
Requirement already satisfied: watchfiles>=0.20 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from uvicorn[standard]>=0.29.0->contractiq==0.2.0) (1.2.0)
Requirement already satisfied: websockets>=13.0 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from uvicorn[standard]>=0.29.0->contractiq==0.2.0) (17.0.1)
Requirement already satisfied: mypy_extensions>=1.0.0 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from mypy->contractiq==0.2.0) (1.1.0)
Requirement already satisfied: pathspec>=1.0.0 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from mypy->contractiq==0.2.0) (1.1.1)
Requirement already satisfied: librt>=0.13.0 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from mypy->contractiq==0.2.0) (0.13.0)
Requirement already satisfied: ast-serialize<1.0.0,>=0.6.0 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from mypy->contractiq==0.2.0) (0.6.0)
Requirement already satisfied: coverage>=7.10.6 in /tmp/contractiq-task06-validation-20260805/lib/python3.14/site-packages (from coverage[toml]>=7.10.6->pytest-cov->contractiq==0.2.0) (7.15.3)
Building wheels for collected packages: contractiq
  Building editable for contractiq (pyproject.toml): started
  Building editable for contractiq (pyproject.toml): finished with status 'done'
  Created wheel for contractiq: filename=contractiq-0.2.0-0.editable-py3-none-any.whl size=2863 sha256=0acc8c6fda3e6fc8a9748788f49b64a3b1bbdbd26552079852fface9ccdb60dc
  Stored in directory: /tmp/pip-ephem-wheel-cache-ugduohzk/wheels/f3/46/ef/248d02a946645227f69ced2ff8584bf1bafa070593e8fdc672
Successfully built contractiq
Installing collected packages: contractiq
  Attempting uninstall: contractiq
    Found existing installation: contractiq 0.2.0
    Uninstalling contractiq-0.2.0:
      Successfully uninstalled contractiq-0.2.0
Successfully installed contractiq-0.2.0
All checks passed!
Success: no issues found in 3 source files
============================= test session starts ==============================
platform linux -- Python 3.14.4, pytest-9.1.1, pluggy-1.6.0 -- /tmp/contractiq-task06-validation-20260805/bin/python3.14
cachedir: .pytest_cache
rootdir: /home/bowen/dev/projects/contractiq
configfile: pyproject.toml
testpaths: tests
plugins: cov-7.1.0, anyio-4.14.2, typeguard-4.4.4
collecting ... collected 169 items

tests/unit/test_bid_repository.py::test_create_and_get_bid_round_trips_every_field PASSED [  0%]
tests/unit/test_bid_repository.py::test_get_unknown_bid_returns_none PASSED [  1%]
tests/unit/test_bid_repository.py::test_create_duplicate_bid_raises_value_error PASSED [  1%]
tests/unit/test_bid_repository.py::test_list_bids_returns_all_and_filters_by_status PASSED [  2%]
tests/unit/test_bid_repository.py::test_update_bid_changes_field_and_bumps_updated_at PASSED [  2%]
tests/unit/test_bid_repository.py::test_update_bid_upserts_when_bid_does_not_exist PASSED [  3%]
tests/unit/test_bid_repository.py::test_attach_list_and_detach_document PASSED [  4%]
tests/unit/test_bid_repository.py::test_existing_create_document_path_defaults_bid_id_to_null PASSED [  4%]
tests/unit/test_bid_repository.py::test_approval_round_trips_provenance PASSED [  5%]
tests/unit/test_bid_repository.py::test_update_approval_persists_full_model PASSED [  5%]
tests/unit/test_bid_repository.py::test_upsert_gate_record_updates_without_duplicate PASSED [  6%]
tests/unit/test_bid_repository.py::test_overridden_gate_round_trips_residual_risk_note PASSED [  7%]
tests/unit/test_bid_repository.py::test_append_and_list_audit_with_optional_bid_filter PASSED [  7%]
tests/unit/test_bid_repository.py::test_schema_evolution_is_idempotent_and_bid_id_is_nullable_once PASSED [  8%]
tests/unit/test_classifier.py::test_zero_triggers_and_value_below_first_paid_band_is_level_zero PASSED [  8%]
tests/unit/test_classifier.py::test_value_in_level_two_band_without_triggers_is_level_two PASSED [  9%]
tests/unit/test_classifier.py::test_liquidated_damages_trigger_beats_level_zero_value PASSED [ 10%]
tests/unit/test_classifier.py::test_level_three_value_beats_level_two_trigger_floor PASSED [ 10%]
tests/unit/test_classifier.py::test_epc_epcm_hint_raises_low_value_bid_to_level_three PASSED [ 11%]
tests/unit/test_classifier.py::test_multiple_triggers_use_maximum_floor_and_sort_highest_first PASSED [ 11%]
tests/unit/test_classifier.py::test_rationale_contains_winning_factor_and_is_non_empty PASSED [ 12%]
tests/unit/test_classifier.py::test_classify_is_deterministic_for_same_input PASSED [ 13%]
tests/unit/test_classifier.py::test_custom_config_changes_classification_outcome PASSED [ 13%]
tests/unit/test_classifier.py::test_malformed_config_json_raises_value_error PASSED [ 14%]
tests/unit/test_classifier.py::test_classify_and_store_persists_result_and_audits_rationale PASSED [ 14%]
tests/unit/test_classifier_config.py::test_missing_config_file_returns_defaults PASSED [ 15%]
tests/unit/test_classifier_config.py::test_valid_config_file_overrides_defaults PASSED [ 15%]
tests/unit/test_classifier_config.py::test_malformed_config_file_raises_value_error PASSED [ 16%]
tests/unit/test_gate_service.py::test_margin_approval_re_evaluation_flips_g4_record PASSED [ 17%]
tests/unit/test_gate_service.py::test_unconfirmed_finding_blocks_g5_until_confirmed PASSED [ 17%]
tests/unit/test_gate_service.py::test_absent_requirements_table_is_not_assessable_and_does_not_block PASSED [ 18%]
tests/unit/test_gate_service.py::test_one_audit_entry_is_written_per_evaluation PASSED [ 18%]
tests/unit/test_gates.py::test_g0_is_met_for_complete_bid_and_unmet_for_zero_value PASSED [ 19%]
tests/unit/test_gates.py::test_g1_requires_obtained_bid_no_bid_approval PASSED [ 20%]
tests/unit/test_gates.py::test_g2_blocks_confirmed_included_unpriced_scope_item PASSED [ 20%]
tests/unit/test_gates.py::test_g2_is_met_when_confirmed_scope_rows_are_priced_and_owned PASSED [ 21%]
tests/unit/test_gates.py::test_g2_unconfirmed_gap_rows_do_not_block PASSED [ 21%]
tests/unit/test_gates.py::test_g2_strategy_is_not_assessable_without_register PASSED [ 22%]
tests/unit/test_gates.py::test_g3_not_assessable_passes_in_v01 PASSED    [ 23%]
tests/unit/test_gates.py::test_g4_margin_rule_depends_on_bid_level[level_3-unmet] PASSED [ 23%]
tests/unit/test_gates.py::test_g4_margin_rule_depends_on_bid_level[level_0-met] PASSED [ 24%]
tests/unit/test_gates.py::test_g4_required_legal_approval_blocks_when_not_obtained PASSED [ 24%]
tests/unit/test_gates.py::test_g5_unconfirmed_material_rule[counts0-unmet] PASSED [ 25%]
tests/unit/test_gates.py::test_g5_unconfirmed_material_rule[counts1-met] PASSED [ 26%]
tests/unit/test_gates.py::test_g5_blocks_when_g4_prior_result_is_unmet PASSED [ 26%]
tests/unit/test_gates.py::test_g5_compliance_matrix_is_not_assessable_when_absent PASSED [ 27%]
tests/unit/test_gates.py::test_only_not_assessable_conditions_pass_and_name_missing_registers PASSED [ 27%]
tests/unit/test_gates.py::test_evaluate_all_threads_prior_gate_results_into_g5 PASSED [ 28%]
tests/unit/test_gates.py::test_gate_evaluation_is_deterministic PASSED   [ 28%]
tests/unit/test_llm_client.py::test_parse_plain_json PASSED              [ 29%]
tests/unit/test_llm_client.py::test_parse_json_in_markdown_fence PASSED  [ 30%]
tests/unit/test_llm_client.py::test_parse_json_after_leading_prose PASSED [ 30%]
tests/unit/test_llm_client.py::test_malformed_json_returns_error_shape PASSED [ 31%]
tests/unit/test_materiality.py::test_every_current_condition_is_material[g0.bid_complete] PASSED [ 31%]
tests/unit/test_materiality.py::test_every_current_condition_is_material[g1.bid_no_bid_approved] PASSED [ 32%]
tests/unit/test_materiality.py::test_every_current_condition_is_material[g2.no_scope_gaps] PASSED [ 33%]
tests/unit/test_materiality.py::test_every_current_condition_is_material[g2.strategy_recorded] PASSED [ 33%]
tests/unit/test_materiality.py::test_every_current_condition_is_material[g3.suppliers_supported] PASSED [ 34%]
tests/unit/test_materiality.py::test_every_current_condition_is_material[g4.high_findings_have_authority] PASSED [ 34%]
tests/unit/test_materiality.py::test_every_current_condition_is_material[g4.margin_approved] PASSED [ 35%]
tests/unit/test_materiality.py::test_every_current_condition_is_material[g4.required_approvals] PASSED [ 36%]
tests/unit/test_materiality.py::test_every_current_condition_is_material[g5.mandatory_requirements_complete] PASSED [ 36%]
tests/unit/test_materiality.py::test_every_current_condition_is_material[g5.no_unconfirmed_material] PASSED [ 37%]
tests/unit/test_materiality.py::test_every_current_condition_is_material[g5.prior_gates_passed] PASSED [ 37%]
tests/unit/test_materiality.py::test_every_current_condition_is_material[g6.concessions_approved] PASSED [ 38%]
tests/unit/test_materiality.py::test_every_current_condition_is_material[g7.award_matches_offer] PASSED [ 39%]
tests/unit/test_materiality.py::test_every_current_condition_is_material[g7.handover_accepted] PASSED [ 39%]
tests/unit/test_materiality.py::test_unknown_conditions_fail_safe_to_material PASSED [ 40%]
tests/unit/test_migration_safety.py::test_bid_migration_preserves_pre_existing_documents PASSED [ 40%]
tests/unit/test_migration_safety.py::test_provenance_retrofit_accepts_legacy_obligation_values PASSED [ 41%]
tests/unit/test_pillars.py::test_all_pillars_contains_exactly_seven_members PASSED [ 42%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[money] PASSED [ 42%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[time] PASSED [ 43%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[scope] PASSED [ 43%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[risk_liability] PASSED [ 44%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[relationships] PASSED [ 44%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[administration] PASSED [ 45%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[exit] PASSED [ 46%]
tests/unit/test_pillars.py::test_pillar_weights_are_floats_in_valid_range PASSED [ 46%]
tests/unit/test_pillars.py::test_weights_sum_to_one_for_each_document_type PASSED [ 47%]
tests/unit/test_provenance_retrofit.py::test_default_clause_finding_write_is_ai_unconfirmed PASSED [ 47%]
tests/unit/test_provenance_retrofit.py::test_human_authorship_does_not_implicitly_confirm_finding PASSED [ 48%]
tests/unit/test_provenance_retrofit.py::test_confirm_clause_finding_and_missing_id PASSED [ 49%]
tests/unit/test_provenance_retrofit.py::test_analysis_row_confirmation_round_trip[scope-item] PASSED [ 49%]
tests/unit/test_provenance_retrofit.py::test_analysis_row_confirmation_round_trip[obligation] PASSED [ 50%]
tests/unit/test_provenance_retrofit.py::test_analysis_row_confirmation_round_trip[negotiation-issue] PASSED [ 50%]
tests/unit/test_provenance_retrofit.py::test_count_unconfirmed_before_and_after_confirmations PASSED [ 51%]
tests/unit/test_provenance_retrofit.py::test_backfill_stamps_rows_with_honest_legacy_provenance PASSED [ 52%]
tests/unit/test_provenance_retrofit.py::test_migration_is_idempotent_and_preserves_confirmation PASSED [ 52%]
tests/unit/test_provenance_retrofit.py::test_existing_read_paths_preserve_business_fields PASSED [ 53%]
tests/unit/test_readiness.py::test_all_met_gates_are_clear PASSED        [ 53%]
tests/unit/test_readiness.py::test_material_scope_gap_holds_bid PASSED   [ 54%]
tests/unit/test_readiness.py::test_not_assessable_conditions_are_advisory_not_blockers PASSED [ 55%]
tests/unit/test_readiness.py::test_override_clears_only_blocker_but_keeps_risk_visible PASSED [ 55%]
tests/unit/test_readiness.py::test_overriding_one_of_multiple_blockers_leaves_hold PASSED [ 56%]
tests/unit/test_readiness.py::test_same_inputs_and_time_produce_identical_report PASSED [ 56%]
tests/unit/test_readiness.py::test_pure_engine_requires_injected_time PASSED [ 57%]
tests/unit/test_readiness_service.py::test_missing_required_margin_approval_holds_bid PASSED [ 57%]
tests/unit/test_readiness_service.py::test_override_writes_gate_and_audit_then_clears_bid PASSED [ 58%]
tests/unit/test_readiness_service.py::test_empty_override_note_is_rejected_before_any_write PASSED [ 59%]
tests/unit/test_readiness_service.py::test_override_persists_across_fresh_assessment PASSED [ 59%]
tests/unit/test_readiness_service.py::test_unconfirmed_finding_holds_then_confirmation_clears PASSED [ 60%]
tests/unit/test_readiness_service.py::test_level_zero_bid_without_approvals_is_clear PASSED [ 60%]
tests/unit/test_schemas.py::test_every_model_instantiates_from_valid_minimal_data PASSED [ 61%]
tests/unit/test_schemas.py::test_bid_rejects_internal_due_date_after_customer_due_date PASSED [ 62%]
tests/unit/test_schemas.py::test_bid_rejects_malformed_bid_id PASSED     [ 62%]
tests/unit/test_schemas.py::test_bid_rejects_win_probability_above_100 PASSED [ 63%]
tests/unit/test_schemas.py::test_bid_defaults_to_local_only PASSED       [ 63%]
tests/unit/test_schemas.py::test_provenance_rejects_unattributed_human_confirmation PASSED [ 64%]
tests/unit/test_schemas.py::test_provenance_from_ai_is_unconfirmed PASSED [ 65%]
tests/unit/test_schemas.py::test_gate_override_requires_residual_risk_note PASSED [ 65%]
tests/unit/test_schemas.py::test_models_forbid_unknown_fields PASSED     [ 66%]
tests/unit/test_schemas.py::test_pillar_id_matches_existing_pillars PASSED [ 66%]
tests/unit/test_schemas.py::test_salvaged_taxonomies_have_expected_member_counts PASSED [ 67%]
tests/unit/test_schemas.py::test_obligation_type_uses_short_codes_as_values PASSED [ 68%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[performance-PERF] PASSED [ 68%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[delivery obligation-PERF] PASSED [ 69%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[payment-PAY] PASSED [ 69%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[Payment obligation-PAY] PASSED [ 70%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[Financial-PAY] PASSED [ 71%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[notice-NOTC] PASSED [ 71%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[notification obligation-NOTC] PASSED [ 72%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[approval-APPR] PASSED [ 72%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[consent requirement-APPR] PASSED [ 73%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[reporting-RPT] PASSED [ 73%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[reporting requirement-RPT] PASSED [ 74%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[insurance-INS] PASSED [ 75%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[insurance requirement-INS] PASSED [ 75%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[compliance-COMP] PASSED [ 76%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[regulatory compliance-COMP] PASSED [ 76%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[restrictive covenant-REST] PASSED [ 77%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[condition precedent-COND] PASSED [ 78%]
tests/unit/test_taxonomy.py::test_normalize_obligation_type_known_variants[survival obligation-SURV] PASSED [ 78%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[failure to give notice-negative] PASSED [ 79%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[failure to notify-negative] PASSED [ 79%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[auto-renew-negative] PASSED [ 80%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[auto-renewal-negative] PASSED [ 81%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[date-based-calendar] PASSED [ 81%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[recurring schedule-calendar] PASSED [ 82%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[specific date-calendar] PASSED [ 82%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[event-based-event] PASSED [ 83%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[triggering event-event] PASSED [ 84%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[upon receipt of invoice-event] PASSED [ 84%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[condition-based-condition] PASSED [ 85%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[if condition is met-condition] PASSED [ 85%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[milestone-based-milestone] PASSED [ 86%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[project milestone-milestone] PASSED [ 86%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[within 10 days of acceptance-milestone] PASSED [ 87%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[rolling period-rolling] PASSED [ 88%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[recurring-calendar] PASSED [ 88%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[within 30 days of the effective date-rolling] PASSED [ 89%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[ongoing-continuous] PASSED [ 89%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[at all times-continuous] PASSED [ 90%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[throughout the term-continuous] PASSED [ 91%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[deemed acceptance-negative] PASSED [ 91%]
tests/unit/test_taxonomy.py::test_normalize_trigger_known_variants[failure to object-negative] PASSED [ 92%]
tests/unit/test_taxonomy.py::test_canonical_obligation_values_pass_through[PERF] PASSED [ 92%]
tests/unit/test_taxonomy.py::test_canonical_obligation_values_pass_through[PAY] PASSED [ 93%]
tests/unit/test_taxonomy.py::test_canonical_obligation_values_pass_through[NOTC] PASSED [ 94%]
tests/unit/test_taxonomy.py::test_canonical_obligation_values_pass_through[INS] PASSED [ 94%]
tests/unit/test_taxonomy.py::test_canonical_obligation_values_pass_through[SURV] PASSED [ 95%]
tests/unit/test_taxonomy.py::test_canonical_trigger_values_pass_through[calendar] PASSED [ 95%]
tests/unit/test_taxonomy.py::test_canonical_trigger_values_pass_through[event] PASSED [ 96%]
tests/unit/test_taxonomy.py::test_canonical_trigger_values_pass_through[condition] PASSED [ 97%]
tests/unit/test_taxonomy.py::test_canonical_trigger_values_pass_through[milestone] PASSED [ 97%]
tests/unit/test_taxonomy.py::test_canonical_trigger_values_pass_through[rolling] PASSED [ 98%]
tests/unit/test_taxonomy.py::test_canonical_trigger_values_pass_through[continuous] PASSED [ 98%]
tests/unit/test_taxonomy.py::test_canonical_trigger_values_pass_through[negative] PASSED [ 99%]
tests/unit/test_taxonomy.py::test_unrecognized_and_none_values_pass_through PASSED [100%]

============================= 169 passed in 1.70s ==============================
app imports OK
```

The repository host's system Python is PEP 668 managed, so the command was run in the isolated validation environment at `/tmp/contractiq-task06-validation-20260805`. The first system-Python install attempt stopped at the PEP 668 guard before running project checks; the complete successful output above is from the required command in that clean environment.

## Decisions I made
- `assess_readiness` raises `ValueError` when `now` is omitted. This preserves the specified optional signature while making a hidden clock structurally impossible in the pure engine; the service always injects `datetime.now(UTC)`.
- The pure signature has no `bid_id` input, so it returns the schema's neutral empty bid ID and `evaluate_readiness` attaches the requested ID without introducing persistence into the pure module.
- G1 treats Level-0 bids as not requiring bid/no-bid approval to satisfy the explicit proportionality requirement that a Level-0 bid with no approvals is CLEAR. Levels 1–4 retain the prior approval requirement.
- When all direct G2/G3/G4 source blockers are genuinely overridden, the derived `g5.prior_gates_passed` cascade is removed as satisfied rather than falsely marked overridden. Every blocker carrying `overridden=True` therefore maps to an actual recorded human decision.
- `request_override` performs the authorization decision and returns the recomputed CLEAR/HOLD result, as required by the override protocol and service tests. `ESCALATE` remains available in the verdict enum for a future pre-decision routing entry point; TASK-06 defines no separate routing-only service function.
- GateRecord and AuditEntry writes use one SQLite transaction so a successful override cannot persist only half of the required record pair.

## Deviations from the task spec
- None.

## Concerns for review
- The existing `gate_records` schema has one row per bid/gate, while readiness overrides are per condition. The latest override on a gate is visible in that gate row; the audit log is the authoritative history for all condition-level overrides.
- Claude should review the explicit handling of the derived G5 prior-gate cascade and the Level-0 G1 proportionality adjustment, which resolve interactions between inherited TASK-05 rules and TASK-06's required integration outcomes.

## Reporting requirements from the task
- Confirmed: `core/readiness.py` and `core/materiality.py` have no DB, clock-call, network, or LLM imports. `readiness.py` imports only the `datetime` type and cannot obtain the current time; `materiality.py` imports nothing.
- Confirmed: `request_override` strips and rejects an empty or whitespace-only risk note before assessment or any write. `tests/unit/test_readiness_service.py::test_empty_override_note_is_rejected_before_any_write` proves both rejection and zero GateRecord/audit writes.
- Confirmed: `tests/unit/test_readiness_service.py::test_unconfirmed_finding_holds_then_confirmation_clears` proves the end-to-end unconfirmed finding → G5 HOLD → human confirmation → CLEAR chain.
- Whole-suite total: 169 tests, all passing.
- Future UI persistence note: overrides are loaded first from OVERRIDDEN GateRecords and then from structured `readiness_override` audit entries. Audit detail is JSON with `condition_id` and `risk_note`; audit entries are authoritative for multiple per-condition overrides on the same gate, while the single gate row reflects the most recent override for that gate.
