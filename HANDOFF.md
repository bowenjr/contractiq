# Handoff — TASK-01

## Status
COMPLETE

## Files created
- pyproject.toml (66 lines)
- core/enums.py (128 lines)
- core/schemas.py (150 lines)
- tests/conftest.py (39 lines)
- tests/unit/test_schemas.py (119 lines)
- tests/unit/test_pillars.py (34 lines)
- tests/unit/test_llm_client.py (33 lines)
- .github/workflows/ci.yml (18 lines)
- static/.gitkeep (1 line)
- HANDOFF.md (183 lines)

## Files modified
- .gitignore — added required virtualenv, tool-cache, package metadata, database,
  SQLite, and output artifact exclusions.

## Test results
`pytest` — 26 passed, 0 failed
`ruff check` — pass
`mypy` (new files) — pass; 2 source files checked
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
  Created wheel for contractiq: filename=contractiq-0.2.0-0.editable-py3-none-any.whl size=2863 sha256=28897cac7046668bf1300b27a0313ccc303dd468cea6f4c6941e29e25ad9031c
  Stored in directory: /tmp/pip-ephem-wheel-cache-4tnplntl/wheels/20/9a/d3/382869a6f4126bddc7de8dc4b0953478ff12551b12d218b8b0
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
collecting ... collected 26 items

tests/unit/test_llm_client.py::test_parse_plain_json PASSED              [  3%]
tests/unit/test_llm_client.py::test_parse_json_in_markdown_fence PASSED  [  7%]
tests/unit/test_llm_client.py::test_parse_json_after_leading_prose PASSED [ 11%]
tests/unit/test_llm_client.py::test_malformed_json_returns_error_shape PASSED [ 15%]
tests/unit/test_pillars.py::test_all_pillars_contains_exactly_seven_members PASSED [ 19%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[money] PASSED [ 23%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[time] PASSED [ 26%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[scope] PASSED [ 30%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[risk_liability] PASSED [ 34%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[relationships] PASSED [ 38%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[administration] PASSED [ 42%]
tests/unit/test_pillars.py::test_each_pillar_has_characterisation_content[exit] PASSED [ 46%]
tests/unit/test_pillars.py::test_pillar_weights_are_floats_in_valid_range PASSED [ 50%]
tests/unit/test_pillars.py::test_weights_sum_to_one_for_each_document_type PASSED [ 53%]
tests/unit/test_schemas.py::test_every_model_instantiates_from_valid_minimal_data PASSED [ 57%]
tests/unit/test_schemas.py::test_bid_rejects_internal_due_date_after_customer_due_date PASSED [ 61%]
tests/unit/test_schemas.py::test_bid_rejects_malformed_bid_id PASSED     [ 65%]
tests/unit/test_schemas.py::test_bid_rejects_win_probability_above_100 PASSED [ 69%]
tests/unit/test_schemas.py::test_bid_defaults_to_local_only PASSED       [ 73%]
tests/unit/test_schemas.py::test_provenance_rejects_unattributed_human_confirmation PASSED [ 76%]
tests/unit/test_schemas.py::test_provenance_from_ai_is_unconfirmed PASSED [ 80%]
tests/unit/test_schemas.py::test_gate_override_requires_residual_risk_note PASSED [ 84%]
tests/unit/test_schemas.py::test_models_forbid_unknown_fields PASSED     [ 88%]
tests/unit/test_schemas.py::test_pillar_id_matches_existing_pillars PASSED [ 92%]
tests/unit/test_schemas.py::test_salvaged_taxonomies_have_expected_member_counts PASSED [ 96%]
tests/unit/test_schemas.py::test_obligation_type_uses_short_codes_as_values PASSED [100%]

============================== 26 passed in 0.11s ==============================
app imports OK
```

## Decisions I made
- Used a temporary SQLite file for `tmp_db`, because the existing `Database` opens a new
  connection for each operation and therefore cannot preserve a plain `:memory:` database.
- `Provenance.from_human(who)` records the human as both creator and confirmer, with the same
  UTC timestamp for creation and confirmation.
- Added a setuptools build backend and limited package discovery to `core`, because editable
  installation otherwise rejects the repository's flat `core/` and `templates/` layout.
- Scoped Ruff away from the named legacy Python modules. An unscoped first run found 493
  pre-existing violations; modifying those modules is explicitly out of scope. New and future
  files remain linted by `ruff check .`.
- Added a Ruff per-file exception for `UP042` in `core/enums.py`, because TASK-01 explicitly
  requires every enum to inherit `str, Enum` rather than `StrEnum`.

## Deviations from the task spec
- Added `static/.gitkeep`, which was not in the listed files. `app.py` mounts `static/` during
  import and README documents the directory, but no `static/` entry existed in git. Without
  this placeholder, the required `python -c "import app"` validation and `python app.py` smoke
  test fail on a clean checkout before reaching any TASK-01 code.

## Concerns for review
- Ruff's legacy exclusions should be reduced incrementally when later tasks intentionally touch
  those modules.
- A standalone strict-mypy audit of legacy files found substantial debt: `core/database.py`
  181 errors; `core/excel_generator.py` 94; `core/document_preprocessor.py` 47; `app.py` 45;
  `core/analysis_engine.py` 44; `core/report_generator.py` 39; `core/knowledge_bootstrap.py` 18;
  `core/knowledge_io.py` 16; `core/knowledge_engine.py` 15; `core/document_processor.py` 15; and
  `core/llm_client.py` 5. The configured `mypy` command remains strict only for the two new
  domain files, as required.
- FastAPI reports that the existing `@app.on_event("startup")` API is deprecated during the
  app smoke test. No existing application code was changed.

## Reporting requirements from the task
- Actual doc-type weight sums from `test_pillars.py`: Subcontract Agreement `1.000`; Prime
  Contract `1.001`; Bid/Tender Response `1.000`; RFP/Tender Document `1.000`; Change
  Order/Variation `1.000`; Claim Letter `1.000`; General Contract `0.986`.
- No database file, uploaded document, or generated report artifact is currently tracked in git.
- Existing strict-mypy findings are listed under "Concerns for review"; `core/database.py` and
  `core/excel_generator.py` are the largest future remediation areas.
