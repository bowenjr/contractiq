# Handoff — TASK-03

## Status
COMPLETE

## Files created
- core/taxonomy.py (105 lines)
- tests/unit/test_provenance_retrofit.py (286 lines)
- tests/unit/test_taxonomy.py (82 lines)

## Files modified
- core/database.py — added the additive provenance migration/backfill, provenance stamping on
  all four analysis write paths, human-confirm methods, unconfirmed counts, and obligation
  taxonomy normalization/logging.
- pyproject.toml — added `core/taxonomy.py` to the strict-mypy target and enabled strict checking
  for that module.
- tests/unit/test_migration_safety.py — proved a legacy free-text obligation write remains
  accepted and is softly normalized where recognized.
- HANDOFF.md — replaced the TASK-02 handoff with this TASK-03 record.

## Test results
`pytest` — 106 passed, 0 failed
`ruff format --check core/taxonomy.py tests/` — pass
`ruff check core/taxonomy.py tests/` — pass
`mypy core/taxonomy.py` — pass; 1 source file checked under strict mode
`python app.py` — application startup completed on `0.0.0.0:8000`; `GET /` returned HTTP 200;
shutdown completed cleanly

## Validation command output
The repository virtual-environment executables were used so the supplied command ran in the
project environment.

```text
$ .venv/bin/pip install -e ".[dev]" && \
  .venv/bin/ruff check core/taxonomy.py tests/ && \
  .venv/bin/mypy core/taxonomy.py && \
  .venv/bin/pytest -v && \
  .venv/bin/python -c "import app; print('app imports OK')"

Obtaining file:///home/bowen/dev/projects/contractiq
  Installing build dependencies: started
  Installing build dependencies: finished with status 'done'
  Checking if build backend supports build_editable: started
  Checking if build backend supports build_editable: finished with status 'done'
  Getting requirements to build editable: started
  Getting requirements to build editable: finished with status 'done'
  Preparing editable metadata (pyproject.toml): started
  Preparing editable metadata (pyproject.toml): finished with status 'done'
Requirement already satisfied: fastapi>=0.111.0 in ./.venv/lib/python3.13/site-packages
Requirement already satisfied: uvicorn>=0.29.0 in ./.venv/lib/python3.13/site-packages
Requirement already satisfied: python-multipart>=0.0.9 in ./.venv/lib/python3.13/site-packages
Requirement already satisfied: jinja2>=3.1.4 in ./.venv/lib/python3.13/site-packages
Requirement already satisfied: pymupdf>=1.24.0 in ./.venv/lib/python3.13/site-packages
Requirement already satisfied: python-docx>=1.1.0 in ./.venv/lib/python3.13/site-packages
Requirement already satisfied: reportlab>=4.2.0 in ./.venv/lib/python3.13/site-packages
Requirement already satisfied: openpyxl>=3.1.0 in ./.venv/lib/python3.13/site-packages
Requirement already satisfied: pandas>=2.0.0 in ./.venv/lib/python3.13/site-packages
Requirement already satisfied: requests>=2.32.0 in ./.venv/lib/python3.13/site-packages
Requirement already satisfied: pydantic>=2.6 in ./.venv/lib/python3.13/site-packages
Requirement already satisfied: pytest>=8 in ./.venv/lib/python3.13/site-packages
Requirement already satisfied: pytest-cov in ./.venv/lib/python3.13/site-packages
Requirement already satisfied: ruff in ./.venv/lib/python3.13/site-packages
Requirement already satisfied: mypy in ./.venv/lib/python3.13/site-packages
Building wheels for collected packages: contractiq
  Building editable for contractiq (pyproject.toml): started
  Building editable for contractiq (pyproject.toml): finished with status 'done'
Successfully built contractiq
Installing collected packages: contractiq
  Attempting uninstall: contractiq
    Found existing installation: contractiq 0.2.0
    Uninstalling contractiq-0.2.0:
      Successfully uninstalled contractiq-0.2.0
Successfully installed contractiq-0.2.0
All checks passed!
Success: no issues found in 1 source file
============================= test session starts ==============================
platform linux -- Python 3.13.13, pytest-9.1.1, pluggy-1.6.0
cachedir: .pytest_cache
rootdir: /home/bowen/dev/projects/contractiq
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.14.2, cov-7.1.0
collecting ... collected 106 items

tests/unit/test_bid_repository.py — 14 passed
tests/unit/test_llm_client.py — 4 passed
tests/unit/test_migration_safety.py — 2 passed
tests/unit/test_pillars.py — 10 passed
tests/unit/test_provenance_retrofit.py — 10 passed
tests/unit/test_schemas.py — 12 passed
tests/unit/test_taxonomy.py — 54 passed

============================= 106 passed in 25.22s =============================
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
- A default AI write uses `Provenance.from_ai(agent_name="analysis_engine", model="unknown")`.
  TASK-03 explicitly leaves real model-id wiring out of scope.
- Analysis-table inserts always store `human_confirmed = 0` and empty confirmation metadata,
  even when the supplied object came from `Provenance.from_human()`. This follows TASK-03's
  explicit authoring-is-not-confirming rule; only the four `confirm_*` methods can confirm a row.
- The provenance migration uses explicit `PRAGMA table_info` inspection before each guarded
  `ALTER TABLE`, then backfills only rows where `prov_created_by IS NULL`.
- Added `core.taxonomy` to the existing strict-mypy allow-list. Without that override, the
  repository's legacy wildcard suppression would make the requested mypy command report success
  without actually checking the new module.
- Normalizers use exact, case-insensitive matching after trimming surrounding whitespace.
  They do not use broad substring matching because that could silently misclassify contractual
  language.

## Deviations from the task spec
- None.

## Concerns for review
- Please review the mapping list below, especially `delivery` → `PERF`, `financial` → `PAY`,
  `periodic` → `rolling`, and the exact negative-trigger phrases.
- `Provenance.from_human()` itself currently sets `human_confirmed=True`; TASK-03 requires the
  opposite behavior for analysis-table authorship. The write boundary deliberately clears those
  confirmation fields rather than changing the shared TASK-01 model.
- `core/database.py` remains excluded by the repository's pre-existing Ruff configuration because
  it contains substantial legacy lint debt. The task's exact scoped Ruff command passed; the new
  taxonomy and all tests are formatted and lint-clean.
- The user-provided untracked TASK-02/TASK-03 specs and their Windows `Zone.Identifier` sidecars
  were preserved unchanged and are intentionally outside this commit.

## Reporting requirements from the task

### PRAGMA evidence

`PRAGMA table_info(clause_findings)` after running the migration twice:

```text
{'cid': 13, 'name': 'prov_created_by', 'type': 'TEXT', 'notnull': 0,
 'dflt_value': None, 'pk': 0}
{'cid': 14, 'name': 'prov_agent_name', 'type': 'TEXT', 'notnull': 0,
 'dflt_value': None, 'pk': 0}
{'cid': 15, 'name': 'prov_model', 'type': 'TEXT', 'notnull': 0,
 'dflt_value': None, 'pk': 0}
{'cid': 16, 'name': 'prov_source_location', 'type': 'TEXT', 'notnull': 0,
 'dflt_value': None, 'pk': 0}
{'cid': 17, 'name': 'prov_created_at', 'type': 'TEXT', 'notnull': 0,
 'dflt_value': None, 'pk': 0}
{'cid': 18, 'name': 'human_confirmed', 'type': 'INTEGER', 'notnull': 0,
 'dflt_value': '0', 'pk': 0}
{'cid': 19, 'name': 'confirmed_by', 'type': 'TEXT', 'notnull': 0,
 'dflt_value': None, 'pk': 0}
{'cid': 20, 'name': 'confirmed_at', 'type': 'TEXT', 'notnull': 0,
 'dflt_value': None, 'pk': 0}
provenance column count: 8
unique provenance column count: 8
```

All eight columns are nullable (`notnull: 0`) and occur exactly once.

### Backfill and migration idempotency

`test_backfill_stamps_rows_with_honest_legacy_provenance` inserts a raw row with NULL provenance,
runs the migration, and verifies:

```text
{'prov_created_by': 'ai',
 'prov_agent_name': 'legacy_import',
 'prov_model': None,
 'prov_created_at': '2026-07-28T10:36:35.221505+00:00',
 'human_confirmed': 0}
second migration unchanged: True
```

`test_migration_is_idempotent_and_preserves_confirmation` then runs the migration twice over an
already-confirmed row. It verifies every table has one of each provenance column and that
`human_confirmed`, `confirmed_by`, `confirmed_at`, and the original AI agent stamp are unchanged.
The separate PRAGMA proof reported `confirmation preserved: True`.

### Recognized free-text variants

Matching is case-insensitive, so capitalization variants of every phrase below are also
recognized. Canonical enum values pass through unchanged.

- `PERF`: `performance`, `performance obligation`, `perform`, `delivery`,
  `delivery obligation`
- `PAY`: `payment`, `payment obligation`, `pay`, `financial`, `financial obligation`,
  `monetary`
- `NOTC`: `notice`, `notice obligation`, `notification`, `notification obligation`
- `APPR`: `approval`, `approval obligation`, `consent`, `consent requirement`
- `RPT`: `report`, `reporting`, `reporting obligation`, `reporting requirement`
- `INS`: `insurance`, `insurance obligation`, `insurance requirement`
- `COMP`: `compliance`, `compliance obligation`, `regulatory compliance`
- `REST`: `restriction`, `restrictive`, `restrictive covenant`
- `COND`: `conditional`, `conditional obligation`, `condition precedent`
- `SURV`: `survival`, `survival obligation`, `surviving obligation`
- `calendar`: `calendar date`, `calendar-based`, `date based`, `date-based`, `fixed date`,
  `recurring schedule`, `specific date`
- `event`: `event`, `event based`, `event-based`, `triggering event`, `upon occurrence`,
  `upon receipt of invoice`
- `condition`: `condition based`, `condition-based`, `condition precedent`,
  `depends on a condition`, `if condition is met`
- `milestone`: `milestone based`, `milestone-based`, `project milestone`,
  `completion milestone`, `within 10 days of acceptance`
- `rolling`: `rolling period`, `recurring`, `periodic`,
  `within 30 days of the effective date`
- `continuous`: `ongoing`, `at all times`, `throughout the term`
- `negative`: `failure to give notice`, `failure to notify`, `auto-renew`, `auto-renewal`,
  `automatic renewal`, `deemed acceptance`, `failure to object`, `missed notice window`,
  `time-barred claim`

Canonical pass-through values are `PERF`, `PAY`, `NOTC`, `APPR`, `RPT`, `INS`, `COMP`, `REST`,
`COND`, `SURV`, `calendar`, `event`, `condition`, `milestone`, `rolling`, `continuous`, and
`negative`.

### Legacy values intentionally left unchanged

The current analysis prompt allows obligation type `other`; it remains `other` and is logged
because no confident canonical mapping exists. The trigger field is unconstrained natural
language, so phrases not in the exact map—verified with `after customer acceptance`—also remain
unchanged and are logged. No legacy value is rejected or dropped.
