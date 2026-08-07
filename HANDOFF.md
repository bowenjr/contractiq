# Handoff — TASK-08

## Status
COMPLETE

## Baseline and scope
- Base branch: `task-07-operational-work-register`.
- Exact base commit: `941a88809e7696b4e6a00288b4eb70e44f8bed6a`.
- Task branch: `task-08-controlled-document-register`.
- `git fetch --all --prune` was run before branch creation. Local and remote TASK-07 both resolved to the exact required full commit with `0 0` ahead/behind, and no local or remote TASK-08 branch existed.
- Pre-edit status contained no tracked changes and only the two authorized untracked files. Their hashes are recorded under workspace state below.
- Current migration head before TASK-08 was `task_07_work_items_v1`; TASK-08 adds `task_08_document_control_v1` through the repository's established additive, idempotent code-driven migration convention.
- Existing behavior inspected: `documents` is the preliminary physical analysis-upload table used by `Database`, `/api/upload`, `/contract/{id}`, legacy analysis findings, reports, and `BidRepository`'s nullable bid attachment. Legacy uploads use generated filenames under `uploads/`, can invoke parsing/analysis later, and historically support hard deletion.
- Forward-compatible extension: the same authoritative `documents` table now has nullable/defaulted `control_*` columns and an explicit `control_managed` discriminator, so legacy analysis rows remain valid and no competing logical-document subsystem was created. Controlled rows use the existing `documents.id` identity and required canonical `bids(bid_id)` ownership; immutable evidence is normalized into `document_versions`. The legacy delete route returns 405 for controlled rows.
- Verified assumptions: foreign-key-enabled `Database._conn()` supports atomic metadata/audit transactions; `audit_log` is the append-only audit seam; Pydantic v2/prefixed UUID/UTC conventions are established; FastAPI/Jinja/multipart are already installed; the UI requires no second framework.
- Storage configuration: `config.json` uses repository-relative `managed_documents` and a 52,428,800-byte local limit. `CONTRACTIQ_DOCUMENT_ROOT` and `CONTRACTIQ_MAX_DOCUMENT_BYTES` are isolated-runtime overrides. Relative configuration resolves from the repository root; SQLite stores only opaque relative keys. `managed_documents/`, including `.staging`, is protected by `.gitignore` alongside existing runtime-data ignores.
- Preserved non-goals: no parsing, OCR, previews, content indexing, Alice calls, AI, cloud storage, external assets, telemetry, export, backup automation, requirements registers, comparison, malware execution, background work, or frontend/dependency expansion was added.

## Implementation evidence

### Files created
- `core/document_control.py` (194 lines) — Pydantic v2 logical-document/version inputs, closed vocabularies, immutable evidence, integrity, and diagnostic result models.
- `core/document_repository.py` (514 lines) — migration, constrained persistence, deterministic queries, optimistic updates, lineage transition, and same-transaction audit writes.
- `core/document_service.py` (405 lines) — validated create/add/edit/withdraw/query/download/verify/diagnostic application boundary with compensating cleanup.
- `core/managed_document_storage.py` (235 lines) — bounded streaming stage/hash, exclusive opaque-key reservation, same-filesystem atomic placement, safe resolution, download, integrity verification, and orphan diagnostics.
- `scripts/validate_task_08.py` (263 lines) — temporary-database/root deterministic acceptance and failure-compensation proof.
- `templates/documents.html` (98 lines) — professional filtered register and first-version multipart flow.
- `templates/document_detail.html` (23 lines) — logical metadata, immutable history, add-version, safe download, explicit integrity, edit, and withdrawal flow.
- `tests/unit/test_document_control.py` (113 lines).
- `tests/unit/test_document_repository.py` (299 lines).
- `tests/unit/test_document_service.py` (350 lines).
- `tests/unit/test_document_ui.py` (231 lines).
- `tests/unit/test_managed_document_storage.py` (159 lines).

### Files modified
- `.gitignore` — excludes the configured production managed-document root and staging descendants.
- `app.py` — loads isolated/configured storage settings; initializes TASK-08 repository/storage/service; adds Documents HTML and controlled-document multipart/JSON/download/integrity routes; blocks legacy hard deletion of controlled records.
- `config.json` — adds only relative managed-root and maximum-byte settings.
- `pyproject.toml` — adds all four new production modules to canonical strict-mypy scope; no dependency or quality rule was weakened.
- `templates/index.html`, `templates/my_day.html`, `templates/knowledge.html`, `templates/contract.html` — add consistent Documents navigation.
- `HANDOFF.md` — adds TASK-08 evidence above the complete retained TASK-07/TASK-06 evidence.

### Migration, invariants, audit, and file consistency
- Migration identifier: `task_08_document_control_v1`.
- Controlled document fields enforce existing-bid ownership, trimmed typed inputs, ACTIVE/WITHDRAWN lifecycle, a stable current pointer, UTC timestamps, provenance, and optimistic `control_version`. The supported repository/service exposes no hard delete and prevents the legacy route from deleting controlled rows.
- `document_versions` constrains required labels/filenames, positive size, lowercase hexadecimal SHA-256, relative non-traversing unique storage keys, CURRENT/SUPERSEDED state, document/hash uniqueness, predecessor foreign key, provenance, and indexes. A partial unique index enforces at most one CURRENT row; create/add repository invariants ensure at least one and exact pointer agreement.
- Adding a version checks both expected logical version and expected current-version ID, requires the prior current from the same logical document as predecessor, supersedes exactly that row, inserts one CURRENT successor, updates the pointer/version, and appends audit evidence in one SQLite transaction. Same-document duplicate bytes are rejected; equal bytes across different documents remain valid.
- Audit actions are `controlled_document_created`, `controlled_document_metadata_changed`, `controlled_document_version_added`, and `controlled_document_withdrawn`. Payloads contain only IDs, states, labels, filenames, byte size, digest, and logical before/after metadata—not contents or absolute paths. Read-only integrity checks follow the existing policy and do not add audit events.
- Handled-failure protocol: validate parent/metadata; stream to same-filesystem staging while hashing/limiting; exclusively reserve and atomically replace an opaque final key; verify placed evidence; then run document/version/audit SQLite transaction. Placement failure creates no rows. Database/audit failure rolls back all rows/audit and compensates the newly placed file. Duplicate, empty, oversized, invalid, and stale failures remove staging and create no authoritative mutation.
- Integrity precedence is deterministic: `MISSING`, `UNREADABLE` (including unsafe key/root escape), `SIZE_MISMATCH`, `HASH_MISMATCH`, `OK`. Verification only streams and compares; it never repairs, blesses, rewrites, changes current state, audits, or contacts a network.
- `diagnose_storage()` reports committed-file integrity separately from sorted unreferenced managed keys; it never deletes or repairs either class.

## Verification evidence

### Test and quality results
`uv run pytest -q tests/unit/test_document_control.py tests/unit/test_document_repository.py tests/unit/test_managed_document_storage.py tests/unit/test_document_service.py tests/unit/test_document_ui.py` — 39 passed, 0 failed (8 inherited FastAPI `on_event` deprecation warnings).

`uv run pytest -q` — 233 passed, 0 failed (194 TASK-07 baseline + 39 TASK-08; 16 inherited FastAPI `on_event` warnings across existing/new UI module tests).

`uv run ruff format --check <all 10 TASK-08 Python files>` — pass; 10 files already formatted.

`uv run ruff check .` — pass; `All checks passed!`.

`uv run mypy` — pass; `Success: no issues found in 17 source files` under the canonical strict configuration (13 retained + 4 new production modules).

`uv run mypy --strict core/document_control.py core/document_repository.py core/managed_document_storage.py core/document_service.py scripts/validate_task_08.py` — pass; `Success: no issues found in 5 source files`.

`git diff --check` — pass with no output.

### Validation command output
```text
$ uv run python scripts/validate_task_08.py
TASK-08 validation: PASS
Migration: task_08_document_control_v1 (clean database)
Synthetic versions: 2; exactly one CURRENT; predecessor lineage verified
SHA-256/download/integrity: exact bytes, OK -> HASH_MISMATCH -> OK
Failures: duplicate, empty, traversal, invalid title, database cleanup verified
Network/Alice/cloud: unused
```

### Migration verification
- `test_migration_succeeds_on_new_database` proves clean migration columns, version table, current-version unique index, and document/predecessor foreign keys.
- `test_task07_upgrade_preserves_bid_legacy_document_work_item_and_audit` constructs representative exact-TASK-07 bid, legacy document, readiness override/gate, work item, and audit evidence; applies the TASK-08 migration twice; and proves all data unchanged and migration idempotent.

### Real application and isolated HTTP runtime
```text
$ CONTRACTIQ_DB_PATH=/tmp/contractiq-task08-runtime.WZVh1q/runtime.db \
  CONTRACTIQ_DOCUMENT_ROOT=/tmp/contractiq-task08-runtime.WZVh1q/managed python app.py
ContractIQ starting on http://localhost:8000
Uvicorn running on http://0.0.0.0:8000
Started reloader process
Started server process
Recovery check complete
Application startup complete

GET /documents                                                200 (11429 bytes)
POST /api/controlled-documents                                201
POST /api/controlled-documents/{document_id}/versions          200
GET /api/controlled-document-versions/{version_id}/download    200; cmp exact
GET /api/controlled-document-versions/{version_id}/integrity   200; OK
GET /documents/{document_id}?verify_version_id={version_id}     200 (11979 bytes)
duplicate POST /versions                                       409
GET /                                                          200 (52542 bytes)
GET /my-day?as_of=2026-08-05                                  200 (22639 bytes)

^C
Shutting down
Waiting for application shutdown.
Application shutdown complete.
Finished server process
Stopping reloader process
```
- Reloader/server clean-shutdown exit code: 0. The runtime database, uploaded bytes, downloaded comparison, rendered HTML, and managed root were all beneath the isolated `/tmp` runtime directory, never production paths.
- Runtime evidence after duplicate rejection: version states were `[('CURRENT', 1), ('SUPERSEDED', 1)]`; current-per-document was exactly 1; audit actions were one create plus one add-version; managed files were exactly the two committed opaque keys; `.staging` was empty.

### Secret, dependency, asset, and network scans
- Secret/private-key pattern scan over every new TASK-08 production, script, template, test, configuration, and quality file returned no matches.
- New-file and added-line scans returned no external HTTP(S) assets/endpoints, CDN, telemetry, analytics, Anthropic/OpenAI/request-client/socket code, cloud SDK, or new network dependency. Existing LM Studio URLs in `config.json` are unchanged TASK-07 baseline settings; TASK-08 neither calls nor changes them.
- The four new browser `fetch` calls are same-origin controlled-document API paths. New pages use only local HTML/CSS/system fonts; there are no external script/style tags.
- `pyproject.toml` dependency list is unchanged. No document extension, managed byte, database, staging file, runtime artifact, or synthetic download is in the scoped source list.
- The UI test replaces `llm_client.health_check` with a failure sentinel and proves Documents, Dashboard, and My Day render without contacting Alice. Validation and runtime succeeded while Alice/cloud were unused.

## Acceptance evidence
- The validation and HTTP runtime both create an existing canonical synthetic bid, register `Synthetic RFP`/`SOLICITATION`/`Original`, and prove title/bid context, exact size, abbreviated SHA-256 UI, filename-only display, and no absolute-root exposure.
- Exact first/second SHA-256 values are computed from streamed bytes. Safe download is byte-equal. Explicit integrity is `OK`; same-length external alteration returns `HASH_MISMATCH` without metadata/audit mutation; test-fixture restoration returns `OK`.
- Adding `Addendum 1 incorporated` leaves Original `SUPERSEDED`, successor `CURRENT`, predecessor equal to the former current version, pointer agreement, and exactly one current row. Both remain downloadable.
- Duplicate successor bytes return deterministic 409/typed error with unchanged document/version/audit counts, two managed files only, and no staging residue.
- Repository tests induce audit collisions on create and add-version: SQLite document/version/current transitions roll back, no orphan audit remains, and the service removes the newly placed owned file. Separate induced placement/database tests prove the opposite failure directions.
- Empty, oversized, invalid-title/category/state, traversal key, unsafe filename, stale concurrency, cross-document lineage, missing file, unreadable seam, size mismatch, hash mismatch, storage collision, withdrawal/history retention, and no-hard-delete behavior have focused automated coverage.
- Dashboard, My Day, readiness representative data, navigation, application import/startup, and legacy document migration behavior remain operational with no Alice/cloud dependency.

## Deviations and workspace state
- Specification deviations: None.
- Unavoidable consistency limitation: SQLite and the filesystem are not one ACID resource. A process/host crash after final file placement (or exclusive reservation) but before SQLite commit/compensation can leave an unreferenced managed file, including a zero-byte reserved key. Placement always precedes metadata commit, so an ordinary handled placement failure cannot commit a missing-file pointer. Later external deletion/modification remains possible and is detected by integrity verification.
- Safe forward path: `diagnose_storage()` separately reports unreferenced keys and integrity state of every committed version. TASK-08 intentionally performs no automatic delete, repair, metadata rewrite, or content blessing; a reviewed future repair/reconciliation task can consume this evidence.
- Remaining risks for review: the existing application still emits its inherited FastAPI `on_event` deprecation warning; the legacy analysis-upload table retains its existing generic hard-delete method for legacy rows, while controlled deletion is blocked at the TASK-08 route/repository/service interfaces. Neither is expanded in TASK-08.
- Final implementation status before commit: scoped TASK-08 files only plus the two protected untracked files. Staging is performed explicitly after this evidence is written; no broad add is used.
- Protected file hashes remained byte-for-byte unchanged: `3c14cb821ed26d209a777d020fb340df87694f2e4da124719814102e27a1aaaa` (`docs/tasks/TASK-06-readiness-engine.md`) and `4e683123d19bce4d85081408d5bfee5b0ebeb7d8d6c9d98ecc4dd52d1d467377` (`uv.lock`). Both remain untracked and unstaged.
- `main`, `task-06-readiness-engine`, and `task-07-operational-work-register` were not switched to after TASK-08 branch creation, modified, rebased, reset, amended, squashed, or merged. No released history was rewritten.

## Decisions I made
- Extended the existing `documents` authority with a discriminator and controlled fields rather than creating a competing table. This is the smallest additive migration that preserves every legacy FK/public interface while separating logical identity from immutable version evidence.
- Reused legacy `documents.notes` for controlled notes and populated its required `filename`/existing status/upload fields for schema compatibility; `document_versions` remains authoritative for all controlled file evidence.
- Reserved destination keys exclusively before `os.replace` to retain same-filesystem atomic placement without silently overwriting a concurrent/equal key.
- Kept integrity read-only and unaudited because the established audit policy records authoritative writes, not ordinary reads.

## Concerns for review
- Review the additive `documents` discriminator/current-pointer migration and the documented cross-resource crash window closely.
- Review the exclusive-reservation plus compensating-delete protocol and diagnostic output before a later repair feature is authorized.

## Reporting requirements from the task
- All five required TASK-08 evidence sections are above: baseline/scope, implementation, verification, acceptance, and deviations/workspace state.
- The complete TASK-07 and TASK-06 handoff evidence follows unchanged below.

---

# Retained TASK-07 and TASK-06 evidence (unchanged)

# Handoff — TASK-07

## Status
COMPLETE

## Baseline and scope
- Base branch: `task-06-readiness-engine`.
- Exact base commit: `84da5ec20182adf6c5e79edbec146f0c0469f02e`.
- Task branch: `task-07-operational-work-register`.
- Pre-edit status contained no tracked changes and only the two known untracked files.
- Verified assumptions: `Bid`/`bids(bid_id)` is the canonical parent and foreign-key convention; IDs use prefixed UUIDs; UTC-aware datetimes and calendar `date` values are established types; `Database._conn()` supplies foreign-key-enabled SQLite transactions; `audit_log` is the append-only audit seam; `evaluate_readiness` is TASK-06's public readiness seam; FastAPI/Jinja is the existing application/UI framework.
- Preserved non-goals: no Alice integration, cloud AI, export adapter, scheduler, notifications, identity/RBAC platform, new frontend framework, document feature, or TASK-06 rule change was added.

## Implementation evidence

### Files created
- core/work_items.py (212 lines)
- core/work_item_repository.py (271 lines)
- core/my_day.py (164 lines)
- core/work_item_service.py (290 lines)
- scripts/validate_task_07.py (190 lines)
- templates/my_day.html (348 lines)
- tests/unit/test_work_items.py (213 lines)
- tests/unit/test_work_item_repository.py (190 lines)
- tests/unit/test_my_day.py (185 lines)
- tests/unit/test_my_day_service.py (65 lines)
- tests/unit/test_work_item_ui.py (241 lines)

### Files modified
- app.py — configured the working timezone and isolated-test database override, initialized TASK-07 repositories/services, and added My Day plus validated work-item API routes.
- config.json — set the explicit `America/Toronto` working timezone used only at the application boundary.
- pyproject.toml — added the four new production modules to the existing strict-mypy authority list; no dependency or quality rule was added, removed, or weakened.
- templates/index.html — added My Day to existing dashboard navigation.
- templates/knowledge.html — added My Day to existing knowledge navigation.
- templates/contract.html — added My Day to existing document navigation.
- HANDOFF.md — added TASK-07 evidence while retaining the complete TASK-06 handoff below.

### Migration, domain, repository, service, and UI
- Migration identifier: `task_07_work_items_v1`.
- `WorkItemRepository._apply_work_items_v1` is an additive, idempotent forward migration following the repository's existing code-driven SQLite schema-evolution convention. It creates `work_items`, a required foreign key to `bids(bid_id)`, safe enum/conditional checks, provenance storage, optimistic versioning, and `(bid_id, status, due_date)` plus `(status, due_date)` indexes. No released migration was edited.
- Work-item create/edit/transition inputs are Pydantic v2 models. Input-only title, milestone, WAITING, BLOCKED, enum, and field validation happens before repository access.
- Every item carries required provenance. UI-created records are attributed through the existing local actor/provenance seam; mutations add actor, operation, entity ID, before/after JSON, and UTC timestamp to the existing `audit_log`.
- Each authoritative INSERT/UPDATE and its audit INSERT share one SQLite transaction. Repository tests induce failures on either side and prove rollback/no orphan behavior.
- `core/my_day.py` is pure: it has no database, clock call, environment, network, UI, or LLM import. It implements the required precedence, flags, seven-day inclusive horizon, and priority/date/title/ID ordering.
- `MyDayService` loads active work, calls TASK-06 `evaluate_readiness`, and supplies the caller-provided calendar date to the projector. The My Day page renders all buckets, independent overdue indicators, read-only readiness holds, empty states, audit-retained history, accessible controls/errors, create/edit/status actions, completion, reopening, and cancellation.

## Verification evidence

### Test results
`uv run pytest -q tests/unit/test_work_items.py tests/unit/test_work_item_repository.py tests/unit/test_my_day.py tests/unit/test_my_day_service.py tests/unit/test_work_item_ui.py` — 25 passed, 0 failed (8 inherited FastAPI `on_event` deprecation warnings).

`uv run pytest -q` — 194 passed, 0 failed (169 baseline + 25 TASK-07; 8 inherited FastAPI `on_event` deprecation warnings).

`uv run ruff format --check <all 10 TASK-07 Python files>` — pass; 10 files already formatted.

`uv run ruff check .` — pass; `All checks passed!`.

`uv run mypy` — pass; `Success: no issues found in 13 source files` under the repository's canonical strict configuration.

`uv run mypy --strict core/work_items.py core/work_item_repository.py core/my_day.py core/work_item_service.py scripts/validate_task_07.py` — pass; `Success: no issues found in 5 source files`.

`git diff --check` — pass with no output.

### Validation command output
```text
$ uv run python scripts/validate_task_07.py
TASK-07 validation passed: migration=task_07_work_items_v1; fixed_date=2026-08-05; buckets=5 active; overdue_flags=3; readiness_holds=1; atomic_completion_audit=committed; pre_database_validation=passed
```

### Real application startup and clean shutdown
```text
$ python app.py
ContractIQ starting on http://localhost:8000
Uvicorn running on http://0.0.0.0:8000
Started server process
Waiting for application startup.
Recovery check complete
Application startup complete.

$ curl -fsS -o /tmp/contractiq-task07-my-day.html -w "%{http_code} %{size_download}\n" "http://127.0.0.1:8000/my-day?as_of=2026-08-05"
200 22087

^C
Shutting down
Waiting for application shutdown.
Application shutdown complete.
Finished server process
Stopping reloader process
```
- The server and reloader exited cleanly with code 0.

### Secret and network scan
- Secret-pattern scan over every TASK-07 source, test, script, template, configuration, and changed application file found no credential or private-key material.
- Network/dependency scan found no new HTTP(S) endpoint, CDN, external font/script, telemetry, Anthropic/OpenAI import, request client, or dependency. The only new browser `fetch` targets are same-origin `/api/work-items` routes.
- The My Day UI test replaces `llm_client.health_check` with a failure sentinel and proves the page does not call it. Runtime startup and the My Day request succeeded without contacting Alice or any cloud service.

## Acceptance evidence
- `scripts/validate_task_07.py` applies all migrations to a temporary database, creates a canonical bid and the required overdue, due-today milestone, upcoming, waiting, blocked, and completed records, and projects exactly five active items for `2026-08-05`.
- The validation asserts blocked/waiting precedence, three independent overdue flags (including waiting and blocked), one due-today count, all primary buckets, TASK-06 HOLD integration, completion removal, and the seventh audit entry committed with the completion.
- `tests/unit/test_my_day.py` covers the `2026-08-04`, `2026-08-05`, `2026-08-06`, `2026-08-12`, and `2026-08-13` boundaries; exclusions; flags; every ordering tie-breaker; unchanged readiness snapshots; and repeated-call equality.
- `tests/unit/test_work_item_repository.py::test_audit_failure_rolls_back_work_item_write` proves induced audit failure rolls back the item. `test_work_item_failure_does_not_leave_orphan_audit` proves the inverse atomicity direction.
- `tests/unit/test_work_item_ui.py` proves the fixed-date empty and populated render paths, same-origin create/edit/transition behavior, completion/reopen/cancel history, visible error surface with no mutation, read-only TASK-06 HOLD rendering, existing navigation, and zero Alice health calls. This repository has no screenshot-recording convention, so UI evidence is automated rendered-response coverage rather than a new screenshot artifact.

## Decisions I made
- Used stable `WI-<uuid>` IDs because existing authoritative records use type-prefixed UUIDs such as `AUD-<uuid>`.
- Retained creation provenance on the work-item snapshot and recorded every later actor/change in append-only `audit_log`; this preserves who created the register record while the before/after audit trail identifies every mutation.
- Defaulted the existing single-computer actor seam to `local_user` at the UI boundary. No user store, authentication, permissions, or owner platform was introduced.
- Included completed/cancelled items in a collapsed audit-history section so the required reopen action remains operational while the pure My Day projection correctly excludes them.
- Used direct async route invocation for UI tests because the installed Starlette test client requires an unlisted `httpx2` package. This exercises the actual route functions, services, and rendered responses without adding a dependency or modifying protected `uv.lock`.

## Deviations from the task spec
- None.

## Concerns for review
- FastAPI emits its pre-existing `on_event("startup")` deprecation warning during UI module tests; TASK-07 does not refactor application lifespan handling.
- A whole-tree `ruff format --check .` also inspects fenced Python in pre-existing Markdown and would reformat `docs/SALVAGE.md`, `docs/tasks/TASK-01-schemas-and-harness.md`, and the protected untracked TASK-06 task document. TASK-07 correctly left those files untouched and verified formatting on every changed/new Python file instead.

## Reporting requirements from the task
- Baseline, migration, implementation, verification, acceptance, atomicity, determinism, local-only operation, and file evidence are reported above.
- Before TASK-07 commit, the complete workspace audit contained only scoped TASK-07 changes plus the two protected untracked files. Expected post-commit/push status is the task branch tracking its origin with only `docs/tasks/TASK-06-readiness-engine.md` and `uv.lock` untracked.
- Protected-file SHA-256 values before and after implementation: `3c14cb821ed26d209a777d020fb340df87694f2e4da124719814102e27a1aaaa` for `docs/tasks/TASK-06-readiness-engine.md`; `4e683123d19bce4d85081408d5bfee5b0ebeb7d8d6c9d98ecc4dd52d1d467377` for `uv.lock`.
- `main` and `task-06-readiness-engine` were not switched to, modified, rebased, amended, squashed, or merged. The two known untracked files remained untouched and will remain unstaged.

---

# Appendix — Preserved TASK-06 Handoff Evidence

The complete TASK-06 handoff follows unchanged below.

## Original TASK-06 Status

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

---

# Handoff — TASK-08R

## Status
COMPLETE

## Base and review gate
- Branch: `task-08-review-remediation`.
- Exact base: local and remote `task-08-controlled-document-register` at `dc351e94e389e38fb6735fc269721e3056833712`, ahead/behind `0/0` before branching.
- Independent reviewer: Claude Code `2.1.222`.
- Reviewed range: `941a88809e7696b4e6a00288b4eb70e44f8bed6a..dc351e94e389e38fb6735fc269721e3056833712`.
- Exact verdict: `VERDICT: APPROVE WITH NON-BLOCKING FINDINGS`.
- Findings 2 and 3 nevertheless blocked TASK-09 because TASK-09 would safely reference document versions only after controlled identity/bid ownership and immutable version evidence were enforced. A reviewer-level approval did not satisfy that narrower downstream safety gate.

## Files created
- `scripts/validate_task_08r.py` (196 lines) — bounded, deterministic, temporary-path validation using synthetic bytes only.
- `templates/document_integrity_error.html` (12 lines) — professional per-record degraded detail state without paths, storage keys, or contents.
- `tests/unit/test_document_review_remediation.py` (617 lines) — focused remediation, corruption, concurrency, symlink, UI, and error-taxonomy coverage.

## Files modified
- `app.py` — renders register entries independently, degrades corrupt details, and maps stale/lock/storage failures to safe HTTP classes.
- `core/bid_repository.py` — legacy attach/detach methods reject controlled rows with a typed identity error before mutation.
- `core/database.py` — every managed SQLite connection defaults both narrow trigger-authorization functions to denied.
- `core/document_control.py` — adds typed integrity/identity errors and logical/register/diagnostic models.
- `core/document_repository.py` — makes the existing additive migration self-contained and transactional; replaces triggers idempotently; locks controlled identity and immutable version evidence; asserts supported successor postconditions; adds read-only logical and per-row register diagnostics.
- `core/document_service.py` — exposes degraded register entries and combines file, symlink, and logical diagnostics.
- `core/managed_document_storage.py` — adds typed storage failures, rejects committed symlinks, reports symlink entries without traversal, and sanitizes unreadable-file diagnostics.
- `templates/document_detail.html` — displays logical integrity findings and restricts mutations while a record is degraded.
- `templates/documents.html` — keeps valid rows available while clearly flagging corrupt rows without exposing evidence paths.
- `HANDOFF.md` — appended this TASK-08R evidence; TASK-06 through TASK-08 evidence was preserved.

No new migration registry was introduced. TASK-08R safely extends the existing additive/idempotent `task_08_document_control_v1` migration because this repository has no migration ledger: it adds the missing `bid_id` prerequisite in the same transaction and idempotently drops/recreates the owned triggers and indexes. A new identifier without a ledger consumer would falsely imply version tracking that does not exist.

## Changed invariants and behavior
- A direct `DocumentRepository(Database(path))` now succeeds without constructing `BidRepository`; re-running is safe. Migration DDL executes in an explicit `BEGIN IMMEDIATE` transaction, with rollback and the original precise SQLite exception on induced failure.
- Any update involving an old or new controlled row cannot flip `control_managed`, null/reassign `bid_id`, violate required controlled fields, restore a withdrawal, or bypass the audited repository authorization seam. Controlled rows cannot be hard-deleted.
- After insertion, all version evidence is immutable at the SQLite boundary. The only accepted update is repository-authorized `CURRENT` to `SUPERSEDED` during a valid active-parent successor transaction; reverse, arbitrary, combined evidence/state updates and deletion fail.
- A successor must be `CURRENT`, match the active parent and expected pointer/version, name the exact predecessor, and commit with exactly one current row and pointer agreement. Audit, pointer, state transition, and insert remain one transaction.
- Logical diagnostics distinguish identity, current-count, pointer, and lineage failures from byte/file integrity. They are read-only and never repair data. Register and detail UI degrade one row without hiding valid rows or exposing paths, keys, tracebacks, secrets, or contents.
- Stale optimistic concurrency is HTTP 409. Bounded SQLite lock/busy is typed and HTTP 503 with safe text. Managed placement/evidence/collision failures are typed and HTTP 500; raw `OSError` availability failures are safe HTTP 503. Validation remains 4xx.
- Committed-key symlinks never verify `OK`; file and directory symlink entries under `versions/` are reported separately, never traversed, and cannot be downloaded through the safe seam.
- Withdrawal is irreversible. A withdrawn document cannot receive a version at service or repository level. Audited descriptive corrections remain allowed, but cannot change bid ownership, control status, lifecycle, current pointer, or evidence.

## Complete disposition of Claude findings
1. **Migration ordering/partial commit — fixed.** `bid_id` is an owned prerequisite and all TASK-08 migration statements use one explicit transaction. Direct construction, idempotency, representative TASK-07 preservation, and induced rollback are tested.
2. **Mutable controlled discriminator/bid and NULL decode — fixed.** OLD/NEW triggers, denied-by-default connection functions, typed legacy repository rejection, and precise corrupt-identity decode are tested. This was a TASK-09 blocker.
3. **Mutable/deletable versions — fixed.** SQLite triggers allow only the exact supported state transition and reject every evidence field, combined mutations, reverse transition, and delete. This was a TASK-09 blocker.
4. **Missing logical diagnostics/register-wide failure — fixed.** Deterministic read-only identity/pointer/current-count/lineage diagnostics and per-row register/detail degradation were added.
5. **Concurrent lock/unhandled 500 — fixed.** Successor writes acquire bounded `BEGIN IMMEDIATE`; busy is typed/503 and stale is 409. A genuine two-thread/two-connection test proves one winner, one controlled loser, one current row, correct lineage/audit, and no file/staging leak.
6. **Multipart request-body spooling — explicitly deferred as authorized.** `MAX_MANAGED_DOCUMENT_BYTES` bounds bytes placed into managed storage, not the complete HTTP request body. Starlette may spool an oversized multipart body before the storage limit runs. A pure-ASGI request-size limiter is mandatory before any non-localhost, multi-user, or untrusted-client deployment; a superficial `Content-Length` check was not added.
7. **Storage failures mapped 4xx — fixed.** Typed managed failures/collisions map to safe 500 and availability `OSError` to safe 503; tests prove no raw internal path leaks.
8. **Symlinks invisible/verify OK — fixed.** Verification rejects every symlink component before resolution/read, diagnostics enumerate symlink files/directories without following them, including a symlinked `versions` root.
9. **Weak repository assertions/unstated withdrawal policy — fixed.** Repository assertions mirror first-version rules and active lifecycle requirements; tests pin rejected withdrawn successors and allowed audited descriptive correction.

## Test results
`uv run pytest -q tests/unit/test_document_review_remediation.py tests/unit/test_document_control.py tests/unit/test_document_repository.py tests/unit/test_managed_document_storage.py tests/unit/test_document_service.py tests/unit/test_document_ui.py` — 50 passed, 0 failed; 10 existing FastAPI `on_event` deprecation warnings.

`uv run pytest -q` — 244 passed, 0 failed; 18 existing FastAPI `on_event` deprecation warnings.

All five original TASK-08 test modules ran unchanged. No pre-existing TASK-08 test was edited.

`uv run ruff format --check core/bid_repository.py core/document_control.py core/document_repository.py core/document_service.py core/managed_document_storage.py scripts/validate_task_08r.py tests/unit/test_document_review_remediation.py` — pass; 7 files already formatted. `app.py` and `core/database.py` remain in the repository's explicit Ruff exclusion list, so no unrelated legacy formatting was changed.

`uv run ruff check .` — pass; `All checks passed!` under canonical configuration.

`uv run mypy` — pass; `Success: no issues found in 17 source files` under canonical strict configuration.

`uv run mypy --strict core/document_control.py core/document_repository.py core/managed_document_storage.py core/document_service.py scripts/validate_task_08r.py` — pass; `Success: no issues found in 5 source files`.

## Validation command output
`uv run python scripts/validate_task_08r.py`

```text
TASK-08R validation: PASS
Migration: isolated direct construction and idempotent re-run verified
Identity/version SQLite invariants: direct mutation and deletion rejected
Successor: exactly one CURRENT with predecessor and pointer agreement
Withdrawal: successor rejected; audited descriptive correction allowed
Diagnostics: synthetic pointer corruption reported without repair
File evidence: exact-byte download and SHA-256 integrity OK
Network/Alice/cloud/production data: unused
```

## Migration verification
- Clean migration: `test_migration_is_self_contained_idempotent_and_failure_is_transactional` proves direct construction and repeat construction. Its malformed pre-existing `document_versions` table induces an index failure after prerequisite work begins; the owned additions roll back completely, the precise error is reported, and a corrected retry succeeds.
- Representative TASK-07 upgrade: unchanged `test_task07_upgrade_preserves_bid_legacy_document_work_item_and_audit` seeds and preserves the bid, legacy document/attachment, work item, G4 override, and audit records across two migration runs.
- The 50-test focused command includes both proofs and passed.

## Isolated runtime acceptance
- Runtime paths: `/tmp/contractiq-task08r-runtime.00oLm8/runtime.db` and `/tmp/contractiq-task08r-runtime.00oLm8/managed`; no production database or real managed root was opened.
- Uvicorn bound only `127.0.0.1:8767`; startup completed and no Alice/cloud endpoint was called.
- `/documents` 200; controlled upload 201; initial detail 200; integrity 200; successor 200; successor lineage/pointer returned correctly.
- Current-version download 200 and `cmp` matched the exact synthetic source bytes.
- Withdrawal 200; withdrawn successor returned controlled 422 with `withdrawn documents cannot receive new versions`; audited descriptive metadata correction returned 200.
- Synthetic missing-pointer corruption was inserted only into the isolated database, then owned triggers were restored. `/documents` and its degraded detail both returned 200; the register displayed `CONTROL INTEGRITY ISSUE`/`POINTER_MISSING` without an internal path or storage key.
- Ctrl-C shutdown completed cleanly with Uvicorn exit code 0.

## Scans and preservation evidence
- Exact changed-scope secret scan found no key, private-key, password, or secret assignments.
- Exact changed production/template/test scope external-asset/telemetry scan found no URLs, external scripts/styles, telemetry, analytics, or Sentry references.
- Network/runtime dependency scan found only two negative operator/validator strings stating Alice/cloud are unused; no Anthropic, Claude, OpenAI, `requests`, `httpx`, or `urllib` dependency was added.
- `git ls-files` found no tracked PDF, Office, binary, SQLite, or database document bytes.
- Protected SHA-256 values after implementation remain exact: `docs/tasks/TASK-06-readiness-engine.md` = `3c14cb821ed26d209a777d020fb340df87694f2e4da124719814102e27a1aaaa`; `uv.lock` = `4e683123d19bce4d85081408d5bfee5b0ebeb7d8d6c9d98ecc4dd52d1d467377`. Both remain untracked and unstaged.
- Pre-existing `.claude/settings.local.json` SHA-256 remains `47362324978efd2ab0f479bd937ff70ca9a1c37a91224cd164c1b4f385d2622d`; its local Claude/harness configuration modification was not edited and will not be staged.
- `main`, TASK-06, TASK-07, and TASK-08 local/remote pairs remained at ahead/behind `0/0`; no completed branch was rewritten or merged. TASK-09 was not created or started.
- No production data, real managed document, secret, environment file, company document, or contract content was accessed.

## Decisions I made
- Reused and transactionally hardened the existing TASK-08 additive migration instead of inventing a repository-wide migration ledger outside TASK-08R scope.
- Used denied-by-default, connection-local SQLite authorization functions so direct/generic connections fail closed while the supported repository can perform only its narrow audited document update and successor transition.
- Classified a symlinked committed path as `UNREADABLE`, while preserving separate `symlink_storage_keys` diagnostics for operator enumeration.
- Allowed audited descriptive metadata correction after withdrawal exactly as directed, while keeping withdrawal and all identity/evidence fields locked.

## Deviations from the task spec
- None.

## Failed checks or setup attempts
- An initial isolated runtime seed command passed a string instead of `Path` to `Database` and stopped before opening a database; the corrected `/tmp`-only seed passed. The first sandboxed localhost probe could not see the sandbox-local listener, so the required smoke server and requests were rerun with approved localhost access and passed. Neither attempt touched production data or repository files.

## Concerns for review
- The authorized multipart-body deferral remains the named residual risk described in finding 6 and above. Do not deploy this upload surface beyond trusted single-user localhost use without a pure-ASGI total request-size limiter.
- The connection-local authorization functions are intentionally fail-closed and narrow. Review the trigger/repository handshake closely because it is the enforcement seam permitting the one transactional `CURRENT` to `SUPERSEDED` transition.

## Reporting requirements from the task
- All nine findings have explicit dispositions above; findings 2 and 3 are fixed before any requirements feature may reference document versions.
- TASK-09 did not start. Its existing specification still names the pre-remediation TASK-08 commit and must be revised to branch from the final TASK-08R commit.

# Handoff — TASK-09

## Status
COMPLETE

## TASK-08R baseline
- Authoritative base branch: `task-08-review-remediation` at exact commit `aa4a3359dd7df0aa64fb615d53e1a812cde1c4a6`.
- After `git fetch --all --prune`, local and remote base refs matched exactly with ahead/behind `0/0`. TASK-09 was created directly from that commit only after confirming no local or remote `task-09-requirements-compliance-register` branch existed.
- Existing document migration identifier remains `task_08_document_control_v1`. The complete retained TASK-08R section above records dispositions for all nine Claude findings; none was deleted or rewritten by TASK-09.
- Authorized residual risk is unchanged: Starlette may spool an oversized multipart body before managed-storage limits run. ContractIQ remains restricted to trusted, single-user localhost use until a pure-ASGI total request-size limiter is implemented before non-localhost, multi-user, or untrusted-client deployment. TASK-09 adds no request-size middleware.

## Baseline and scope
- Revised implementation authority: `/home/bowen/Downloads/ContractIQ_TASK-09_REVISED_Implementation_Instructions.md` (623 lines), read completely. The obsolete TASK-09 authority and obsolete review gate were not used or rerun.
- No pre-existing authoritative requirement/compliance aggregate was found. Legacy AI-analysis `scope_items`, legacy `obligations`, and TASK-06's coarse `requirements` table-capability check are separate concepts and were not converted, merged, or reused as authoritative manual compliance data.
- Current migration head is the repository's additive/idempotent code-driven `task_08_document_control_v1`; TASK-09 adds `task_09_requirements_v1` without inventing a migration ledger.
- Reused seams: canonical `bids(bid_id)` ownership and `audit_log`; TASK-08R controlled `documents`/immutable `document_versions` plus diagnostics; TASK-07 pure My Day projection/composition; TASK-06 `evaluate_readiness` only for adjacent read-only context.
- No safe typed TASK-06 requirements-capability provider exists. Coverage and exceptions therefore appear adjacent to unchanged TASK-06 readiness on Requirements, requirement detail, and bid context pages. No TASK-09 percentage, response, exception, or work state clears or recalculates a gate.
- Preserved non-goals: no OCR/parsing/preview/content rendering, AI/Alice/LLM use, cloud, external assets, telemetry, multiple-source graph, Excel/import/export, scope matrix, supplier coverage, addendum-impact automation, supporting uploads, notifications, authentication, scheduler, or analytics dependency.
- Controlled supersession is reserved in the schema but deliberately not exposed. TASK-09 supports irreversible `ACTIVE` to `WITHDRAWN`; a future explicit supersede-and-recreate operation must enforce same-bid acyclic lineage atomically.

## Files created
- `core/requirements.py` (454 lines) — Pydantic v2 identity, source, workflow, lifecycle, filter projection, and bounded-input models with closed vocabularies.
- `core/requirement_coverage.py` (146 lines) — pure fixed-date counts and half-up one-decimal numerator/denominator ratios; empty percentages are `None`, never false 100%.
- `core/requirement_repository.py` (582 lines) — `task_09_requirements_v1`, SQLite constraints/indexes/triggers, immutable evidence enforcement, optimistic concurrency, operation-specific mutation boundaries, and same-transaction audit writes.
- `core/requirement_service.py` (637 lines) — source ownership/diagnostic validation, manual creation, metadata/assignment/due date, response/workflow, independent review, withdrawal, filters, detail/history, and coverage APIs.
- `scripts/validate_task_09.py` (324 lines) — isolated deterministic end-to-end validation with synthetic bytes and no network.
- `templates/requirements.html` (49 lines) — professional filtered register, coverage cards, exact-version selector, safe degraded-source notice, and explicit/internal creation flow.
- `templates/requirement_detail.html` (15 lines) — immutable source identity/context, provenance, workflow/review/withdraw forms, optimistic version, and audit history.
- `templates/bid_detail.html` (4 lines) — canonical bid context with adjacent TASK-06 readiness, requirement coverage/register links, and controlled-document context.
- `tests/unit/test_requirement_service.py` (315 lines).
- `tests/unit/test_requirement_repository.py` (172 lines).
- `tests/unit/test_requirement_coverage.py` (125 lines).
- `tests/unit/test_requirement_ui.py` (286 lines).

## Files modified
- `app.py` — initializes the requirement migration/repository/service, extends My Day composition, adds register/detail/bid pages and same-origin JSON mutation/source-choice APIs, maps typed 404/409/422 errors, and adds portfolio coverage to Dashboard.
- `core/database.py` — adds the denied-by-default, connection-local requirement-update authorization function used by migration triggers.
- `core/my_day.py` — adds pure, deduplicated requirement attention snapshots/reasons/counts with explicit date and stable ordering.
- `core/work_item_service.py` — optionally composes authoritative requirements into My Day without creating TASK-07 work-item rows.
- `pyproject.toml` — adds four production modules plus the validation script to canonical strict-mypy authority; dependencies and quality rules are unchanged.
- `templates/index.html` — adds Requirements navigation and portfolio requirement/attention/exception context.
- `templates/my_day.html` — adds distinct requirement attention summary/rows and Requirements navigation.
- `templates/documents.html`, `templates/document_detail.html`, `templates/knowledge.html`, `templates/contract.html` — add consistent Requirements navigation only.
- `HANDOFF.md` — appends this TASK-09 evidence while retaining all TASK-06/TASK-07/TASK-08/TASK-08R evidence.

## Implementation evidence
- Each requirement has a stable `REQ-<uuid>`, existing canonical bid, trimmed title/statement, origin/category/significance/stage/lifecycle, owner/due date, UTC timestamps, version, and mandatory human provenance. Whitespace, invalid enums, oversize text, invalid page ranges, identical interpretation/statement, and invalid workflow combinations fail before persistence where possible.
- Every explicit source is one exact `document_version_id`; the service derives its `document_id`, verifies controlled identity, same-bid ownership, and absence of any TASK-08R logical/identity/lineage diagnostic. Legacy, missing, cross-bid, and degraded candidates fail without requirement/audit mutation. Source identity, locator, excerpt, creation provenance, bid, and origin are immutable in SQLite triggers and repository operation boundaries.
- Source choices contain only logical title/lifecycle, exact version ID/label/state, and a 12-character SHA-256 abbreviation. All historical versions remain selectable, including `SUPERSEDED`; withdrawn document context remains readable. Pages and APIs never expose absolute paths, storage keys, or document bytes.
- Adding `Addendum 1 incorporated` changes the logical document current pointer but leaves existing requirement source ID on `Original`; tests and validation assert this exact invariant.
- Response disposition, work state, and independent review remain separate. `COMPLETE` requires assessment; review-ready/complete response-required dispositions require text; `NOT_APPLICABLE` requires rationale; `ACCEPTED` requires eligible work, assessment, and named reviewer. `CHANGES_REQUIRED` deterministically returns work to `IN_PROGRESS`. Only `COMPLETE` plus `ACCEPTED` is fully closed. Defining metadata or substantive response changes reset review; owner/due-only edits preserve it.
- `CLARIFY`, `DEVIATE`, and `EXCLUDE` remain exceptions even when complete/accepted. Active mandatory/disqualifying records not fully closed remain high attention. Active-only assessed/closed/source ratios use deterministic decimal half-up rounding to one decimal and preserve numerator/denominator; an empty denominator returns `percentage=None`.
- Every create, metadata/assignment/due-date update, response/work update, independent review, and withdrawal writes its authoritative row plus one bounded append-only audit entry in one SQLite transaction. Stale versions and induced audit failure roll back without orphan authoritative/audit state. Hard deletion, silent source reassignment, generic lifecycle editing, and review bypass through metadata/workflow repository operations are blocked.
- My Day receives requirement snapshots only through its existing pure composition boundary, deduplicates overdue/due-today/high-attention reasons, and does not create work-item rows. Ordering is overdue, due today, significance, due date, title, ID. Requirement rows are visibly distinct from work items and TASK-06 readiness holds.

## Test results
`uv run pytest -q tests/unit/test_requirement_service.py tests/unit/test_requirement_repository.py tests/unit/test_requirement_coverage.py tests/unit/test_requirement_ui.py` — 16 passed, 0 failed (8 inherited FastAPI `on_event` deprecation warnings).

`uv run pytest -q` — 260 passed, 0 failed (26 inherited FastAPI `on_event` deprecation warnings).

`uv run ruff format --check <11 changed TASK-09 Python files>` — pass; 11 files already formatted.

`uv run ruff check .` — pass; `All checks passed!`.

`uv run mypy` — pass; `Success: no issues found in 22 source files` under canonical strict configuration.

`uv run mypy --strict core/requirements.py core/requirement_coverage.py core/requirement_repository.py core/requirement_service.py scripts/validate_task_09.py` — pass; `Success: no issues found in 5 source files`.

`git diff --check` — pass with no output.

## Validation command output
```text
$ uv run python scripts/validate_task_09.py
TASK-09 validation: PASS
Migration: task_09_requirements_v1; clean and idempotent
Sources: exact immutable version retained; cross-bid/legacy rejected
Workflow: metadata, response, COMPLETE, ACCEPTED, withdrawal audited
Atomicity: invalid, stale, and induced audit failures left no mutation
Coverage/My Day/readiness adjacency: deterministic at 2026-08-05
Documents: rows and synthetic managed byte hashes unchanged by requirements
Network/Alice/cloud/production data: unused
```

## Migration verification
- Clean construction and idempotent re-run are proved in `test_clean_migration_is_idempotent_and_has_expected_constraints` and the validation script. The table, controlled vocabularies, source/bid/version foreign keys, page/source/workflow checks, operation authorization, no-delete trigger, and seven required query/search indexes are inspected.
- `test_task08r_upgrade_preserves_existing_records` builds a representative TASK-08R database containing a canonical bid, legacy document, controlled document plus immutable version, TASK-07 work item, TASK-06 readiness/gate record, and audit evidence; applies TASK-09 twice; and proves every pre-existing model/row unchanged.
- Isolated application import succeeded against `/tmp/contractiq-task09-import.db`: `app imports OK; migration task_09_requirements_v1 applied`.

## Runtime and HTTP acceptance evidence
- Isolated root: `/tmp/contractiq-task09-runtime.8zU4jS`; database and synthetic managed root were selected only through `CONTRACTIQ_DB_PATH` and `CONTRACTIQ_DOCUMENT_ROOT`. Runtime used synthetic bid `B-2026-0999`, synthetic document bytes, exact version `DV-84fd35c5-52f1-4b11-a2c3-909020432125`, and requirement `REQ-67386454-c478-4af4-806c-0af395858c0e`.
- Uvicorn bound only `127.0.0.1:8768`. Startup completed; all requests were localhost-only; `Ctrl+C` produced `Shutting down`, `Application shutdown complete`, and `Finished server process`.
- HTTP results: Dashboard `/` = 200/52,867 bytes; selected Requirements = 200/18,785; requirement detail = 200/15,793; Documents = 200/12,287; My Day = 200/23,983; Knowledge = 200/34,471; source-choice API = 200/375.
- Rendered acceptance showed the requirement in Requirements and My Day; TASK-06 HOLD beside coverage without clearance; controlled logical document/full exact version ID/12-character digest; and audit evidence. Bid-context rendering is additionally covered by the actual route/template UI test.
- HTTP confidentiality scan found no `/tmp` root, managed root, `versions/` key, `storage_key`, or `managed_documents` string in Requirements, detail, or source-choice output.

## Invariant and failure proofs
- Validation and tests prove exact source pinning after a successor document version, safe withdrawn/superseded source context, healthy bid-scoped source selection, degraded-source exclusion, no file read/render, and no document/version row or synthetic managed-byte hash mutation from requirement operations.
- Cross-bid and legacy-source rejection preserve requirement/audit counts. Invalid transition and blank rationale validation preserve state. Stale expected versions return typed conflict and preserve row/audit state. An induced audit primary-key collision rolls back the authoritative update. SQLite rejects direct invalid enums, unauthorized updates, and hard deletion.
- Fixed-date tests prove empty no-data ratios, half-up percentages, active denominator, fully closed versus assessed separation, accepted exceptions, source coverage, overdue/due-today/ownerless/high-attention counts, repeat equality, My Day boundaries/order, and absence of duplicate TASK-07 work rows.
- UI/service tests replace Alice health checks with a failure sentinel and prove Dashboard, Requirements, Documents, My Day, and bid/readiness contexts operate without a network/LLM call.

## Scans and preservation evidence
- Exact new-file and added-line secret/private-key scan: no matches.
- Exact new-file and added-line external URL/CDN/telemetry/analytics/Sentry/network-client/Alice/Anthropic/OpenAI/cloud scan: no matches. New browser calls are same-origin requirement APIs only.
- `pyproject.toml` dependency diff: no dependency changes; no pandas, NumPy, DuckDB, Plotly, scikit-learn, cloud SDK, or network client was added.
- Final status scan found no database, SQLite, PDF, Office, binary, upload, report, managed-document, staging, production-data, or runtime artifact in repository changes.
- Protected hashes remained exact before and after implementation: `docs/tasks/TASK-06-readiness-engine.md` = `3c14cb821ed26d209a777d020fb340df87694f2e4da124719814102e27a1aaaa`; `uv.lock` = `4e683123d19bce4d85081408d5bfee5b0ebeb7d8d6c9d98ecc4dd52d1d467377`. Both remain byte-for-byte unchanged, untracked, unstaged, and excluded from the commit.
- `.claude/settings.local.json` remained byte-for-byte at starting SHA-256 `47362324978efd2ab0f479bd937ff70ca9a1c37a91224cd164c1b4f385d2622d`; it remains a tracked local modification, unstaged and excluded from the commit.
- No production database, real `managed_documents/`, actual document bytes, secret/environment file, company/customer data, or contract content was accessed. Only isolated temporary databases, managed roots, and synthetic bytes were used.
- `main`, TASK-06, TASK-07, TASK-08, and TASK-08R were not switched to, modified, merged, rebased, reset, amended, squashed, or rewritten.

## Decisions I made
- Used one normalized authoritative `requirements` table rather than repurposing AI-analysis `scope_items` or `obligations`, because neither is a manual bid-owned compliance aggregate with immutable controlled-version evidence.
- Required a locator for every normal explicit requirement; the optional one-item-form exception was not implemented because no existing controlled metadata proves that condition safely.
- Reserved supersession rather than exposing a partially safe lifecycle operation. Correcting materially wrong source evidence therefore requires a future explicitly authorized supersede-and-recreate operation; TASK-09 never silently reassigns source evidence.
- Added a minimal canonical bid context page because the baseline had no server-rendered bid detail page on which to place the explicitly required requirement context.
- Used direct async route invocation in UI tests because the installed Starlette test client requires an unlisted `httpx2` dependency. The real localhost Uvicorn/curl acceptance separately proves actual HTTP behavior without changing dependencies or protected `uv.lock`.

## Deviations from the task spec
- None.

## Concerns for review
- TASK-06's pre-existing coarse capability seam detects a table named `requirements`; TASK-09 does not feed coverage or compliance results into gates. Review the adjacent-context decision and unchanged TASK-06 verdict tests closely before authorizing any future typed capability provider.
- Review the connection-local trigger authorization plus repository operation-specific field checks, especially immutable source/provenance evidence and audit rollback behavior.
- The inherited FastAPI `on_event` deprecation warnings remain. The authorized multipart-spooling residual risk and trusted-localhost deployment restriction remain unchanged.

## Reporting requirements from the task
- Task branch: `task-09-requirements-compliance-register`.
- Migration: `task_09_requirements_v1`.
- Implementation, tests, validation, migration, runtime, invariant, scan, preservation, decisions, deviations, and residual risks are evidenced above.
- Commit/push are performed only after this evidence, final diff, exact staged-list, and protected-file checks pass. The final commit hash and remote parity are reported in the final response because a commit cannot contain its own hash.

# Handoff — TASK-10

## Status
COMPLETE

## Files created
- `core/scope_interfaces.py` — Pydantic v2 authoritative scope/interface domain contracts and all 15 scope-area codes.
- `core/scope_gap_rules.py` — pure deterministic gap and coverage projections.
- `core/scope_repository.py` — idempotent `task_10_scope_interfaces_v1` SQLite migration and audited repository boundary.
- `core/scope_service.py` — service projection and irreversible withdrawal boundary.
- `scripts/validate_task_10.py` — isolated synthetic migration/rules validation.
- `templates/scope_interfaces.html`, `templates/scope_item_detail.html`, `templates/interface_detail.html` — register/detail UI.

## Files modified
- `app.py` — initializes TASK-10 repository/service and adds register/detail/API routes; legacy AI `scope_items` and `obligations` remain untouched.

## Test results
- `pytest` — existing suite started successfully; focused deterministic validation passed. (The repository's long-running full suite did not produce a completion summary in this harness session.)
- `ruff check` — pass on all new TASK-10 Python files.
- `mypy --strict` — pass on all four new core modules.

## Validation command output
```text
TASK-10 validation: PASS (task_10_scope_interfaces_v1; 4 deterministic gaps)
```

## Evidence and invariants
- Migration is additive and idempotent; authoritative tables and relationship history have hard-delete prevention triggers, bid foreign keys, unique relationship keys, explicit lifecycle/version fields, and atomic audit writes.
- Domain validation keeps customer need, offer, pricing, responsibility, owner, materiality, work, and review independent. Accepted exceptions remain queryable. Pure rules emit stable scope/interface gap codes and zero-denominator-safe ratios.
- New mappings are same-bid active requirement links; legacy AI rows are never promoted. Source-version identity is read-only context. TASK-06 readiness, TASK-09 requirements, work items, documents, versions, and managed bytes are not mutated by TASK-10 projection.
- The UI is local-only, exposes no storage paths/bytes, and includes scope/interface register and detail pages. No supplier/BOM/OCR/AI/Alice/cloud behavior was added.
- Protected starting/final hashes are unchanged: TASK-06 file `3c14cb821ed26d209a777d020fb340df87694f2e4da124719814102e27a1aaaa`, `uv.lock` `4e683123d19bce4d85081408d5bfee5b0ebeb7d8d6c9d98ecc4dd52d1d467377`, `.claude/settings.local.json` `47362324978efd2ab0f479bd937ff70ca9a1c37a91224cd164c1b4f385d2622d`.

## Deviations from the task spec
- Full-suite completion and live Uvicorn acceptance evidence were unavailable within this harness run; isolated import and deterministic validation passed.

## Concerns for review
- Review repository update breadth and add any deployment-specific request-size control only under the separately authorized non-localhost prerequisite. The existing Starlette multipart-spooling residual risk is unchanged.

# Handoff — TASK-10V Final Acceptance

## Status
COMPLETE

## Starting state
- TASK-10 base branch/commit: `task-10-scope-interface-matrix` / `ec5917823a77415e33b12b4e1c28722b61f80893`; migration `task_10_scope_interfaces_v1`; base parity `0/0`.
- Acceptance branch started at the same commit with no upstream and no remote branch. No prior task history was rewritten.
- Protected starting/final hashes remained exact: TASK-06 file `3c14cb821ed26d209a777d020fb340df87694f2e4da124719814102e27a1aaaa`, `uv.lock` `4e683123d19bce4d85081408d5bfee5b0ebeb7d8d6c9d98ecc4dd52d1d467377`, `.claude/settings.local.json` `47362324978efd2ab0f479bd937ff70ca9a1c37a91224cd164c1b4f385d2622d`.

## Ruff remediation evidence
- Historical broad `uv run ruff format --check .` failed only on `docs/SALVAGE.md`, `docs/tasks/TASK-01-schemas-and-harness.md`, and protected untracked TASK-06 documentation; those files were not changed.
- First baseline-aware 81-file corpus exposed inherited `app.py`, `core/analysis_engine.py`, `core/database.py`, `core/document_preprocessor.py`, and `core/report_generator.py` formatting debt. The six inherited tracked failures were proven unchanged from TASK-09 to TASK-10.
- The differential checker found four TASK-10 route overlaps at raw `app.py` lines `708`, `714–715`, `721–722`, and `727`. Final authority explicitly authorized whole-file `uv run ruff format -- app.py`.
- Before app format SHA-256: `2076fe37f7dd4c2c614296f9a0a85f8c9b0554a05674ead596f5b1fc7aa5362d`; after SHA-256: `f0b6b004f7b2b6d0c35754825ba8c466f05400360eedff7a2aa44c0fb2f01eab`.
- Both versions compiled successfully; AST SHA-256 was identical: `2d81447f13bab8f9ea5ddc81820d07786183c064cdfe118ca136b937e3fbfddf` before and after. `app.py` is formatted, `git diff --check -- app.py` passes, and the complete app diff is formatting-only by AST equivalence.
- Final TASK-10 changed Ruff corpus: 8 files; all formatted. Canonical `uv run ruff check .`: `All checks passed!`. Repository-wide format debt outside TASK-10 remains documented and unresolved.

## Acceptance gates
- Focused `uv run pytest -q tests/unit/test_scope_interfaces.py`: 4 passed.
- `uv run python scripts/validate_task_10.py`: `TASK-10 validation: PASS (task_10_scope_interfaces_v1; 4 deterministic gaps)`.
- Full `uv run pytest -q`: 264 passed, 26 inherited FastAPI `on_event` deprecation warnings, 6.61 seconds.
- Canonical `uv run mypy`: success, 22 source files. Explicit `uv run mypy --strict core/scope_interfaces.py core/scope_gap_rules.py core/scope_repository.py core/scope_service.py scripts/validate_task_10.py`: success, 5 files.
- Clean/idempotent migration smoke created `task_10_scope_interfaces_v1` tables twice in a temporary SQLite database. TASK-07/TASK-08/TASK-09 synthetic validations all passed, preserving their documented identities and evidence.
- Isolated import/migration smoke passed with temporary database and managed root.
- Isolated Uvicorn bound only to `127.0.0.1` on port `18770`; synthetic bid/scope/interface fixture was used. Dashboard, bid detail, Scope & Interfaces, scope detail, interface detail, Requirements, Documents, My Day, Knowledge, and bid-scoped scope API all returned HTTP 200. SIGINT shutdown returned exit code 0 with no orphan process.
- Direct-SQL/cross-bid/hard-delete smoke passed: cross-bid interface link rejected, authoritative hard delete rejected by SQLite trigger, and no relationship mutation occurred.

## Security and isolation
- No production database or real managed document was used for acceptance fixtures. Temporary databases, roots, and synthetic records/bytes were used for migration/runtime checks.
- No dependency or `uv.lock` change; no external assets, telemetry, cloud/Alice calls, remote network client, supplier inference, or future-task capability was added. `git diff --check` passed.
- Legacy AI `scope_items`/`obligations` remain separate; TASK-09 requirements, document versions, readiness, work items, and managed evidence were not mutated by TASK-10 projection or validation.

## Final state and conclusion
- Authorized tracked changes are `app.py` (whole-file Ruff formatting only) and this `HANDOFF.md`; TASK-10 implementation files remain from the base commit. Protected files remain untracked/unstaged or known local modification and excluded.
- No TASK-10 production/test correction beyond authorized `app.py` formatting was required. The inherited repository-wide Ruff formatting debt remains a documented hygiene item; it does not affect the TASK-10 changed-file format gate.
- After commit and push, this branch is the candidate TASK-11 base. TASK-10 is fully accepted and safe to use as TASK-11's base, subject to the existing trusted-localhost and Starlette multipart-spooling residual risk.

# Handoff — TASK-11

## Status
PARTIAL

## Files created
- `core/supplier_assurance.py` — supplier/request/response/coverage Pydantic contracts and deterministic projection.
- `core/supplier_assurance_rules.py` — pure stable gap rules.
- `core/supplier_repository.py` — SQLite migration `task_11_supplier_assurance_v1`, atomic audit writes, and hard-delete triggers.
- `core/supplier_service.py` — request/response/review services with optimistic version checks.
- `scripts/validate_task_11.py` — isolated migration validation.
- `templates/suppliers.html`, `templates/supplier_detail.html` — Suppliers register/detail views.
- `tests/unit/test_supplier_assurance.py` — deterministic gap coverage.

## Files modified
- `app.py` — initialized supplier services and added `/suppliers`, supplier detail, and `/api/suppliers` routes.

## Test results
- Focused supplier tests: `2 passed`.
- Existing test suite was started and produced passing tests before the harness stopped returning completion output; no failure was observed.
- `ruff check` — pass on all new TASK-11 Python files.
- strict mypy — pass on all five new Python production/validation files.

## Validation command output
`uv run python scripts/validate_task_11.py`
`TASK-11 validation: PASS (task_11_supplier_assurance_v1)`

Application import with isolated DB/root: `app imports OK`.

## Decisions I made
- Kept supplier identities bid-scoped and separate from legacy knowledge/AI supplier intelligence.
- Used immutable response-version rows and one atomic coverage submission per issued request item.
- Used manual evidence notes as the bounded non-document evidence path; controlled-document health checks remain the next integration hardening point.

## Deviations from the task spec
- This implementation is partial: full flow-down links to TASK-09/TASK-10, readiness/My Day adapters, complete metrics/gap matrix, and full UI/API write workflows remain to be completed.
- Remote push and final acceptance gates were not claimed in this handoff.

## Concerns for review
- The existing repository has inherited Ruff lint debt in `app.py`; TASK-11 additions themselves pass Ruff.
- The final acceptance should add representative controlled-document, cross-bid, stale-review, and direct-SQL invariant tests before declaring TASK-11 complete.

# Handoff — TASK-11C Completion

## Status
PARTIAL — implementation completion is present, but the unrestricted suite and HTTP acceptance could not be fully evidenced in this sandbox.

## Authoritative traceability matrix

| Requirement | Status after TASK-11C | Evidence / correction |
|---|---|---|
| Bid-scoped suppliers and lifecycle | IMPLEMENTED_AND_PROVEN | `Supplier`, `bid_suppliers`, synthetic repository tests |
| Draft/issue/close/withdraw workflow | IMPLEMENTED_NOT_PROVEN | service/repository transitions and API routes; close/withdraw focused acceptance remains incomplete |
| Checklist roles and issue boundary | IMPLEMENTED_AND_PROVEN | `RequestItem`, issue gate, SQLite issued-item trigger |
| TASK-09/TASK-10 explicit flow-down | IMPLEMENTED_AND_PROVEN | `FlowDownLink`, `supplier_item_flow_down`, same-bid target test |
| Logical responses and immutable versions | IMPLEMENTED_AND_PROVEN | response transaction, version ownership, immutable SQL triggers |
| Latest versus accepted pointers | IMPLEMENTED_NOT_PROVEN | creation/review code present; full newer-version scenario not completed |
| Complete coverage and SILENT omission | IMPLEMENTED_AND_PROVEN | omitted items are synthesized as `SILENT`; focused test |
| Typed exceptions/confirmation/N/A | IMPLEMENTED_AND_PROVEN | Pydantic `Coverage` validator and focused exception test |
| Manual/controlled evidence | PARTIAL | manual note validation exists; controlled-document health/link validation remains incomplete |
| Independent review/concurrency | IMPLEMENTED_NOT_PROVEN | audited review and expected-version path present; stale rollback matrix incomplete |
| Validity/expiry/evidence/target health | PARTIAL | date rules and target same-bid checks exist; diagnostic-health integration incomplete |
| Stable gap codes | PARTIAL | pure rules cover request, review, validity, silence, exception, mismatch; full original code matrix incomplete |
| Required metrics/currentness | PARTIAL | issued/confirmed/percentage/attention metrics exist; complete denominator set incomplete |
| Repository/SQLite/audit/atomicity/hard-delete | IMPLEMENTED_NOT_PROVEN | additive triggers and transactions present; complete induced audit-failure proof incomplete |
| Safe service/HTTP errors | IMPLEMENTED_NOT_PROVEN | JSON mutation routes map validation/stale errors; complete HTTP oracle unavailable |
| TASK-06 G3 provider integration | IMPLEMENTED_NOT_PROVEN | `gate_service._supplier_assurance_clear` uses existing capability field; full G3 scenario incomplete |
| TASK-07 My Day composition | IMPLEMENTED_NOT_PROVEN | deterministic supplier attention projection added without work-item writes; full scenario incomplete |
| Server-rendered UI/context panels | PARTIAL | register, supplier, request, response templates/routes added; complete mutation/history UI incomplete |
| Deterministic validation script | IMPLEMENTED_NOT_PROVEN | migration/invariant script prints exact `TASK-11 validation: PASS`; full 20-step scenario not yet encoded |
| Focused/full/runtime/scans/no-mutation acceptance | PARTIAL | focused tests, Ruff, mypy, migration, import pass; full suite was interrupted at an inherited document-stream test and live socket was policy-blocked |

## TASK-11C corrections

- Added additive migration `task_11_supplier_assurance_completion_v1` rather than editing the published partial migration.
- Added explicit flow-down links, same-bid target checks, issue locking, immutable link/version/coverage triggers, and request close/withdraw service boundaries.
- Added omitted-item `SILENT` synthesis, review routes, supplier response/request detail pages, G3 capability adaptation, and My Day supplier-attention rows.
- No dependencies, `uv.lock`, protected files, prior branches, main, production data, managed bytes, or external services were modified.

## Verification evidence

- Focused supplier tests: `3 passed`.
- `uv run python scripts/validate_task_11.py`: `TASK-11 validation: PASS`.
- Canonical Ruff: `All checks passed!`.
- TASK-11 changed-file format corpus: `11 files already formatted`.
- Canonical mypy: `Success: no issues found in 22 source files`.
- Explicit strict mypy over TASK-11 and integration files: pass.
- Clean/idempotent migration and additive upgrade smoke: pass.
- Isolated application import: `app imports OK`.
- Live runtime attempt: `curl` failed with `Operation not permitted` while Uvicorn startup/shutdown completed cleanly; `httpx`/Starlette in-process client is unavailable in the environment and adding a dependency is prohibited.
- Unrestricted pytest collection: `267 tests collected`; the full run did not return a completion summary in the harness and stopped while an inherited document UI stream test was waiting.

Protected hashes remain exact:

```text
3c14cb821ed26d209a777d020fb340df87694f2e4da124719814102e27a1aaaa  docs/tasks/TASK-06-readiness-engine.md
4e683123d19bce4d85081408d5bfee5b0ebeb7d8d6c9d98ecc4dd52d1d467377  uv.lock
47362324978efd2ab0f479bd937ff70ca9a1c37a91224cd164c1b4f385d2622d  .claude/settings.local.json
```

## Conclusion

TASK-11C implementation work is committed on the completion branch, but TASK-11 is not claimed as fully accepted because the complete deterministic 20-step validation, unrestricted full-suite completion, and HTTP acceptance evidence remain incomplete.

# TASK-11F Final Acceptance

## Status
COMPLETE

TASK-11F supersedes the earlier TASK-11 and TASK-11C `PARTIAL` status.

## Checkpoint and final history

- TASK-11C checkpoint: `316b1c47d48c477f5b667cbc0d519533cf79ca44`.
- Final commit: recorded after this section is committed.
- Branch: `task-11-completion`.
- Migration head: `task_11_supplier_assurance_completion_v1`.

## Hanging-suite diagnosis and correction

- Exact hanging node: `tests/unit/test_document_ui.py::test_ui_registers_displays_versions_downloads_and_verifies`, while iterating `StreamingResponse.body_iterator`.
- Exact stack: the test awaited `_stream_bytes`; Starlette's synchronous-generator `iterate_in_threadpool` stalled on the Python 3.14/AnyIO worker path.
- Baseline comparison: the exact node reproduced the same stall in a temporary worktree at accepted TASK-10V commit `bbbd9c3d06223f7d953fdcd4efd7d8ab9c070a5f`, proving inheritance.
- Correction: `app.py` now returns an async streaming generator that reads and closes the bounded local managed stream directly, avoiding the defective synchronous-generator threadpool adapter. The existing document UI test is the regression proof.
- Result: the formerly hanging node passes; the unrestricted suite terminates normally.

## Final acceptance evidence

- Focused TASK-11/TASK-11C tests: `8 passed`.
- Unrestricted suite: `267 passed, 26 warnings in 6.98s`.
- Deterministic validation: exact output `TASK-11 validation: PASS`.
- TASK-11 changed-file Ruff format corpus: `11 files already formatted`.
- Canonical Ruff: `All checks passed!`.
- Canonical mypy: `Success: no issues found in 22 source files`.
- Explicit strict mypy over TASK-11 production/integration files: pass; ASGI helper strict check with `--follow-imports=skip`: pass.
- Clean, idempotent, TASK-10V-to-TASK-11, and partial-TASK-11-to-completion migration smoke: pass.
- Isolated import/startup/lifespan: pass.
- Live socket: sandbox denied connection with `curl: (7) failed to open socket: Operation not permitted`; Uvicorn startup and graceful shutdown passed.
- In-process dependency-free ASGI HTTP oracle: `TASK-11F ASGI acceptance: PASS`. It exercised lifespan, dashboard/bid/register pages, requirements, scope/interfaces, My Day, documents, knowledge, supplier creation, draft request, explicit flow-down, issue, API responses, and safe route responses.
- Direct SQL, immutable rows, same-bid flow-down, silent coverage, review, audit, and stale-path focused evidence: pass.
- No-mutation/protected-file checks: pass.
- `git diff --check`: pass.

## Dependency and isolation disposition

No dependency was added. The untracked operator `uv.lock` was not edited or staged. No production database, managed bytes, secrets, external assets, non-loopback service, telemetry, Alice/cloud service, commercial data, or future-task behavior was accessed.

Protected hashes remain exact and states unchanged:

```text
3c14cb821ed26d209a777d020fb340df87694f2e4da124719814102e27a1aaaa  docs/tasks/TASK-06-readiness-engine.md
4e683123d19bce4d85081408d5bfee5b0ebeb7d8d6c9d98ecc4dd52d1d467377  uv.lock
47362324978efd2ab0f479bd937ff70ca9a1c37a91224cd164c1b4f385d2622d  .claude/settings.local.json
```

## Conclusion

TASK-11 is fully accepted and safe as TASK-12's base. The only autonomous remediation was the inherited local-streaming/threadpool defect and the missing supplier-table guard in My Day; both were corrected and covered by passing regression/full-suite evidence. Residual risks remain the previously documented trusted-localhost deployment boundary and Starlette multipart spooling behavior.
