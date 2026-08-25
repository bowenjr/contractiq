# Handoff — OPS-05B Stabilization

## Status
ACCEPTED FOR CONTINUED DEVELOPMENT

## Manual browser acceptance — 2026-08-25

- OPS-05B manual browser acceptance: PASS, user-verified on Armoury on 2026-08-25.
- The frozen bid-stage Vendor Document Requirements and handover workflow is functionally accepted for publication.
- Observed minor UI and usability improvements are explicitly deferred and are not publication blockers.
- No post-acceptance redesign, polish or feature change was made.

## Branch, baseline and recovery

- Branch: `ops-05-vendor-document-control`.
- HEAD/baseline: `0b6cc283b2e5e51bd5979b74405d119b4156e477`.
- Recovery pointer: `backup/ops-05-pre-implementation-20260817` at the baseline.
- Retained pre-OPS-05B redesign stash: `ba1729da4e854e076f7b29e08178bdb5d73f7434`.
- Retained pre-usability-remediation stash: `d85fb955e3945a8c4c453096a27c185686071289`.
- Nothing is staged, committed or pushed.

## Implementation summary

- OPS-05B is a Bid-owned, bid-stage Vendor Document Requirement List (VDRL) verification and handover workflow. ContractIQ ends at formal handover; execution belongs to another person or system.
- Added browser Bid list, controlled Bid creation with atomic audit and PRG, and a stable Bid workspace.
- Added Bid-owned supplier/equipment packages, preserved original customer VDRL requirements, separate manufacturer verification, commercial impact, Bid disposition, readiness reasons, atomic bulk updates, atomic CSV preview/import and handover CSV.
- Customer requirement values are immutable after creation. Manufacturer silence remains `NOT_REVIEWED` or `AWAITING_MANUFACTURER`; it is never inferred as compliance.
- Readiness is deterministic and blocks unresolved silence, clarification, non-compliance, unapproved exceptions, missing manufacturer/owner/evidence and unknown exception-related commercial impact.
- Package creation requires an existing Bid. There is no normal standalone-package path, Project conversion, post-award submission/review/resubmission/closeout workflow, InEight integration, external network behavior or automatic My Work creation.
- The generic identity-free VDRL template is automatic and read-only. Raw configuration is secondary administration, not routine Bid work.
- The handover CSV preserves Bid/package identity, the original customer requirement, requested stages/timing, manufacturer status, response source/date/notes, commitment, evidence, exception, commercial impact, Bid disposition, owner, unresolved action and handover note.
- The superseded post-award implementation is absent from active code/routes/templates and remains recoverable only in the retained stashes.

## Completed role-alignment review reconciliation

- Read `/home/bowen/reviews/contractiq-role-alignment-review-20260821.md` without modifying it.
- Claude's stale `AttentionCode` collection failure was already corrected in the frozen worktree; the vendor tests use `VerificationStatus`, `CommercialImpact`, `BidDisposition`, readiness, bulk verification, import and handover models.
- Claude's navigation failure was already corrected in the frozen worktree; the exact formerly failing test passes unchanged.
- No additional architecture or navigation redesign was performed.
- Stabilization added explicit manufacturer response source/date/notes columns to the handover CSV and corresponding unit, validator and HTML-ASGI acceptance assertions.

## Files created

- `core/vendor_document_control.py` — typed Pydantic v2 Bid-stage VDRL models and deterministic readiness.
- `core/vendor_document_repository.py` — additive unpublished SQLite schema, immutable originals, atomic writes, audit and concurrency.
- `core/vendor_document_service.py` — Bid-owned package/requirement workflow, verification, CSV import, readiness and handover.
- `docs/tasks/OPS-05-vendor-document-control.md` — authoritative OPS-05B specification and role boundary.
- `scripts/asgi_acceptance_ops05.py` — isolated dependency-free browser acceptance.
- `scripts/validate_ops_05.py` — isolated deterministic OPS-05B validator.
- `templates/bids.html` — browser Bid list/create surface.
- `templates/vendor_document_package.html` — Bid-linked VDRL compliance workspace.
- `templates/vendor_document_register.html` — filtered cross-Bid compliance register.
- `templates/vendor_documents.html` — Bid-stage queue and package creation surface.
- `tests/unit/test_vendor_document_control.py` — domain/repository/service/migration regression coverage.

## Files modified

- `app.py` — Bid browser routes and bid-stage Vendor Document Requirements HTML routes.
- `core/bid_repository.py` — atomic Bid creation plus audit.
- `templates/bid_detail.html` — linked Vendor Document Requirements section.
- `templates/index.html`, `templates/my_day.html`, `templates/my_work.html`, `templates/role_framework.html`, `templates/documents.html`, `templates/document_detail.html`, `templates/requirements.html`, `templates/requirement_detail.html` — settled operational navigation labels and links only.
- `tests/unit/test_bid_repository.py` — atomic Bid/audit coverage.
- `tests/unit/test_ops_navigation.py` — settled navigation coverage.
- `HANDOFF.md` — this stabilization record; prior accepted history remains below.

## Removed from the active unpublished implementation

- The recovered pre-redesign files `templates/vendor_document_detail.html`, `templates/vendor_document_reports.html`, `docs/tasks/OPS-05R-vendor-document-usability-remediation.md` and `docs/tasks/OPS-05U-vendor-document-workflow-integration.md` are not present in the active worktree. They were untracked and remain recoverable in the retained stashes.
- Actual execution submissions, revision/review-return cycles, resubmission attention, execution milestone fulfillment, document cancellation/discard and closeout routes are not active.

## Test results

- Direct navigation regression: `1 passed, 0 failed, 2 warnings`.
- Vendor/Bid focused tests: `33 passed, 0 failed`.
- Focused operational/navigation suite: `61 passed, 0 failed, 38 warnings`.
- Full unexcluded pytest suite: `336 passed, 0 failed` (336 collected; only existing FastAPI `on_event` deprecation warnings).
- OPS-05B validator: PASS.
- OPS-05B dependency-free ASGI acceptance: PASS.
- OPS-01, OPS-02 and OPS-03 validators and ASGI acceptances: PASS.
- TASK-07 through TASK-18 validators: PASS, including TASK-08R.
- TASK-11 through TASK-18 ASGI acceptances: PASS.
- Migration initialization/restart/integrity/foreign-key test: `1 passed`.
- Strict scoped mypy on `core/bid_repository.py` and the three vendor production modules, with imported legacy-module diagnostics silenced: PASS (`Success: no issues found in 4 source files`).
- Ruff format check: PASS (`10 files already formatted`).
- Ruff check on changed Python excluding `app.py`: PASS.
- Direct `app.py` Ruff: exactly 10 inherited findings. `git blame --contents` attributes all flagged lines to earlier commits; OPS-05B adds none. Repository-wide Ruff compliance is not claimed.
- `git diff --check`: PASS.
- Repository-wide mypy compliance is not claimed.

## Validation command output

```text
OPS-05B validation: PASS
OPS-05B ASGI acceptance: PASS
OPS-01 validation / ASGI acceptance: PASS / PASS
OPS-02 validation / ASGI acceptance: PASS / PASS
OPS-03 validation / ASGI acceptance: PASS / PASS
TASK-07 through TASK-18 validators: PASS
TASK-11 through TASK-18 ASGI acceptance: PASS
```

## Migration and production-data safeguards

- Unpublished additive migration: `ops_05_bid_stage_vdrl_v1`.
- New isolated-database tables: `vendor_vdrl_templates`, `vendor_bid_packages`, `vendor_bid_requirements`, `vendor_document_schema_migrations`.
- Production verification before redesign found no OPS-05 table or migration marker. No production migration was run.
- Production `data/contractiq.db` SHA-256 before and after verification: `65111b90ad39b7db9944027907208524350018b7df3adceeab060c5c100fe1ea`.
- Temporary initialization, restart, `PRAGMA integrity_check` and `PRAGMA foreign_key_check` passed.
- No database or backup is staged or tracked by this work.
- `uv.lock` and `uv.lock.armoury-generated-20260811` remain untouched, untracked and unstaged.
- `.claude/settings.local.json` remained clean during stabilization; no automatic permission change required restoration.

## Decisions I made

- The completed role-alignment review is authoritative input, not a request for another redesign. Its two P0 failures were verified against the frozen worktree and were already corrected.
- Existing clarification/deviation domains do not expose a safe authoritative relationship for these specialized rows, so OPS-05B stores explicit references and handover actions rather than creating competing records.
- Manufacturer response source/date/notes were added to the handover export because those existing fields are material handover evidence.

## Deviations from the task spec

- None in product scope. A supplemental `python app.py` smoke check reached ContractIQ's startup banner but the managed sandbox denied opening a listening socket. The required ASGI acceptances passed without network access, and no background server was started.

## Concerns for review

- Manual browser acceptance remains required. Do not treat the automated evidence as user acceptance.
- TASK-12 remains unchanged. Any later consolidation with vendor-document requirements requires separate program authorization.
- The inherited FastAPI `on_event` warnings and 10 inherited `app.py` Ruff findings remain outside OPS-05B.

## Manual browser acceptance

Start ContractIQ with a fresh isolated database and document root:

```bash
cd /home/bowen/dev/projects/contractiq
CONTRACTIQ_DB_PATH=/tmp/contractiq-ops05b-manual-20260821.db \
CONTRACTIQ_DOCUMENT_ROOT=/tmp/contractiq-ops05b-manual-documents-20260821 \
UV_CACHE_DIR=/tmp/contractiq-uv-cache \
uv run uvicorn app:app --host 127.0.0.1 --port 8015
```

Open `http://127.0.0.1:8015/`, then:

1. Open **Bids**, create a realistic Bid, and confirm its workspace opens after the redirect.
2. Open **Vendor Document Requirements** from that Bid and create a linked supplier/equipment package.
3. Add customer VDRL requirements manually, then preview a CSV. Confirm a declared 33/marked 37 mismatch is rejected without importing rows.
4. Assign manufacturer and internal owner in bulk. Confirm the selected rows update together.
5. Record compliant, exception, clarification-required and cannot-comply responses. Leave one row silent and confirm it remains unresolved.
6. Confirm every readiness blocker links to the affected requirement and no exception/non-compliance is ready without the required evidence and approved Bid disposition.
7. Export the handover CSV and verify original customer requirements, manufacturer responses/commitments, evidence, exceptions, commercial impact, disposition, owners and unresolved actions.
8. Confirm breadcrumbs and links always return to the parent Bid and no post-award submission, customer-review, resubmission or execution-project workflow is advertised.

Stop with `Ctrl-C`. Do not use the production database for this acceptance.

# Prior accepted OPS-03 record

## Branch and baseline
- Branch: `ops-03-role-framework-authoring`
- Published OPS-02 baseline: `73a1adf356e231338e02c4d0ca94e302d4d97ce1`
- Recovery pointer: `backup/ops-03-pre-implementation-20260817`
- Publication is limited to the OPS-03 feature branch; no merge, rebase, main-branch change, force-push, pull request, or OPS-04 work is authorized.

## OPS-03 implementation
- Added the authoritative specification at `docs/tasks/OPS-03-role-framework-authoring.md`.
- Added typed Pydantic v2 role-profile models and a shared service boundary for browser and JSON operations.
- Added browser creation, stable detail/edit, publication, revision, retirement, history, relationship, provenance, audit, and explicit-date effective-profile workflows.
- DRAFT profiles require a bounded title and effective start but may remain narratively incomplete; publication additionally requires mission, at least one allowlisted responsibility domain, valid human-authored provenance, a valid effective window, and no prohibited overlap.
- Responsibility domains use the existing 13-domain taxonomy, deduplicate in deterministic taxonomy order, and do not reclassify work items or alter the global taxonomy.
- Published and retired authored content is immutable. Revise creates one child DRAFT, copies authorable content, allocates a globally unique version inside the write transaction, and consumes the parent's expected token.
- Publishing a child of a PUBLISHED parent atomically closes the parent's effective window at the day before the child's start, publishes the child, rotates affected tokens, and writes publication and supersession audit evidence. A RETIRED parent is never reactivated.
- Effective lookup accepts an explicit working date at the service boundary, returns zero or one PUBLISHED version, excludes RETIRED profiles, performs no writes, and reports invalid overlapping legacy data instead of silently choosing.
- Existing JSON route paths and successful response shape were preserved; mutations now reject server-owned identity, version, actor, timestamp, token, and provenance inputs instead of trusting them.
- No delete path, LLM/network call, work-item change, My Day/My Work change, schema migration, or persisted-domain redesign was added.

## OPS-03 files created
- `docs/tasks/OPS-03-role-framework-authoring.md`
- `core/role_profiles.py`
- `core/role_profile_service.py`
- `templates/role_profile_detail.html`
- `tests/unit/test_role_profiles.py`
- `tests/unit/test_role_profile_service.py`
- `tests/unit/test_role_profile_ui.py`
- `scripts/validate_ops_03.py`
- `scripts/asgi_acceptance_ops03.py`

## OPS-03 files modified
- `app.py` — shared service wiring plus state-appropriate HTML and compatible hardened JSON routes.
- `core/ops_foundation.py` — typed role-profile hydration and transactional lifecycle, version, supersession, concurrency, and audit operations in the existing repository.
- `templates/role_framework.html` — setup/effective/history browser entry point and create navigation.
- `HANDOFF.md` — OPS-03 implementation evidence and narrow corrections to stale OPS-02 publication/projection wording.

## OPS-03 automated evidence
- Focused role/operations/navigation suite: `68 passed, 0 failed, 48 warnings`.
- Full suite: `317 passed, 0 failed, 50 warnings` (all 300 published tests plus 17 OPS-03 tests).
- `OPS-03 validation: PASS`.
- `OPS-03 ASGI acceptance: PASS`; the dependency-free test exercises actual HTML POST/303/GET behavior and verifies repository state and rendered content using an isolated temporary database.
- OPS-01 and OPS-02 validators and ASGI acceptance: PASS.
- Every available TASK-07 through TASK-18 validator and applicable TASK-11 through TASK-18 ASGI acceptance script: PASS. No standalone TASK-06 validator exists in the repository.
- Changed-file `ruff format --check`: PASS (9 files formatted).
- Changed Python files other than `app.py`, `ruff check`: PASS.
- Direct `ruff check app.py` retains exactly 10 inherited pre-OPS-03 findings; no new OPS-03 Ruff finding remains and repository-wide Ruff compliance is not claimed.
- Strict mypy on the two new production modules with imported legacy modules silenced: PASS (`--strict --follow-imports=silent`). Repository-wide mypy compliance is not claimed.
- `git diff --check`: PASS.
- Remaining warnings are existing FastAPI `on_event` deprecation warnings.

## OPS-03 database and safeguards
- Existing `ops_role_profiles` and `audit_log` storage is reused; no migration was added.
- Global version allocation, profile mutation, token rotation, and audit evidence execute within SQLite transactions using the existing database patterns.
- Validators and tests use temporary isolated databases. Final publication does not compare the production database hash because accepted My Work testing intentionally changed production data; no database or backup is included in OPS-03 source control.
- No dependency, lockfile, tool configuration, database, database backup, or TASK-06–18 specification changed.
- `uv.lock` and `uv.lock.armoury-generated-20260811` remain untouched, untracked, and unstaged.

## OPS-03 manual browser acceptance — 2026-08-17
- Manual browser acceptance: PASS, user-verified on Armoury on 2026-08-17.
- Accepted coverage included draft creation and retained editing, publication validation, controlled revision and supersession, retirement, stable version history, parent/child navigation, provenance, audit presentation, optimistic-concurrency feedback, and effective-profile behavior.
- OPS-03 is accepted for publication and continued development. OPS-04 remains dormant.

# Prior accepted OPS-02 record

## OPS-02 publication
- Branch: `ops-02-operational-command-center`
- Published commit: `73a1adf356e231338e02c4d0ca94e302d4d97ce1`
- Manual browser acceptance: PASS on Armoury, 2026-08-17.

## Files created
- `docs/tasks/OPS-02-operational-command-center.md` — authoritative OPS-02 specification.
- `scripts/validate_ops_02.py` — deterministic service/projection acceptance using an isolated temporary database.
- `scripts/asgi_acceptance_ops02.py` — dependency-free in-process ASGI acceptance using isolated temporary storage.

## Files modified
- `HANDOFF.md` — replaced the accepted OPS-01R handoff with this OPS-02 implementation record.
- `app.py` — typed My Work filters, safe register navigation, fixed local-date projection boundary, and read-only My Day work-item presentation.
- `core/my_day.py` — centralized pure attention-reason, actionable-date, tier, and deterministic ordering functions.
- `core/work_item_repository.py` — parameterized register filters without changing persistence or migration behavior.
- `core/work_item_service.py` — read-only operational-register projection with bounded bid-context resolution and safe display formatting for stale/value errors.
- `core/work_items.py` — typed Pydantic v2 register filter and projection models.
- `scripts/validate_task_07.py` — modernized synthetic WAITING, BLOCKED, and COMPLETED fixtures for accepted OPS-01R lifecycle requirements; updated the non-exclusive due-today count.
- `templates/my_day.html` — removed obsolete work-item mutation forms/JavaScript and linked active/archive rows to My Work.
- `templates/my_work.html` — Quick Capture anchor and deterministic current/history/all register controls and presentation.
- `templates/role_framework.html` — normalized operational navigation and domain labels.
- `templates/work_item_detail.html` — complete general-field controls, status-scoped lifecycle sections, and safe filter-preserving back navigation.
- `tests/unit/test_document_ui.py` — fixed application-date injection after removal of the public My Day time-travel parameter.
- `tests/unit/test_my_day.py` — centralized fixed-date attention and deterministic ordering regression coverage.
- `tests/unit/test_ops_navigation.py` — read-only My Day and Quick Capture navigation coverage.
- `tests/unit/test_requirement_ui.py` — fixed application-date injection after removal of the public My Day time-travel parameter.
- `tests/unit/test_work_item_repository.py` — intersection-filter and contradictory-context coverage.
- `tests/unit/test_work_item_ui.py` — command-center filters plus dependency-free HTML POST/redirect/reload persistence, audit, invalid-write, and stale-write coverage.
- `tests/unit/test_work_items.py` — typed register-filter validation coverage.

## Implementation summary
- My Work is the authoritative operational register with allowlisted current/history/all, status, category, responsibility-domain, explicit context, bid, and attention filters using intersection semantics.
- Bid context is batch-resolved to a human-readable project label plus stable identifier; standalone work remains first-class.
- Active ordering reuses one pure My Day attention contract: blocked, overdue, today, other currently due attention, upcoming, scheduled, then unscheduled; priority, earliest actionable date, and stable ID break ties.
- My Day preserves the implemented TASK-06 readiness and TASK-09 plus TASK-11–15 control collections while work-item mutation is removed. TASK-16–18 routes remain available but do not have dedicated My Day attention categories. Active and archived work links to `/my-work/{id}`, and Add Work links to `/my-work#quick-capture`.
- One work item retains all applicable attention reasons while appearing once. Completed and cancelled items remain outside active attention.
- Safe allowlisted query reconstruction preserves appropriate My Work filters through editor/back navigation without accepting arbitrary return URLs.
- Existing service mutation routing, atomic audit writes, optimistic concurrency, hard-delete protection, JSON APIs, Quick Capture PRG, and invalid-input retention are unchanged.

## Manual-acceptance persistence remediation
- Initial manual acceptance entered structured waiting values while the record status remained OPEN. Read-only production audit inspection confirmed the four affected saves all carried target status OPEN and stored null structured waiting fields.
- The route, Pydantic model, service, repository SQL, audit serialization, redirect, GET projection, and template rendering were proven to persist the waiting fields when the submitted status is WAITING. The earliest failing boundary was the editor: it exposed every lifecycle section simultaneously, so non-applicable values could be entered and were silently removed by established state-specific clearing.
- Waiting, blocked, completion, and cancellation sections are now visible, enabled, and submitted only for their selected lifecycle status. Required inputs use the authoritative field names.
- Priority and the existing contribution-candidate value were supported downstream but missing from the HTML editor; both are now exposed and round-trip through the same path.
- Legacy `waiting_on` and `blocker_note` remain synchronized from their structured counterparts for compatibility; they are not substitutes for structured values.
- A real stale HTML form POST exposed a second defect: the route caught `StaleWorkItemError` as a `ValueError` but passed it to a formatter that assumed Pydantic `ValidationError`, producing a 500 page. The formatter now safely renders both classes, so stale writes return retained HTTP 422 feedback while record and audit remain unchanged.
- No production lifecycle validation, state-specific clearing, audit, versioning, SQL, or schema rule was weakened.

## Manual browser acceptance — 2026-08-17
- Manual browser re-acceptance: PASS, user-verified on Armoury on 2026-08-17.
- The re-test covered status-scoped lifecycle controls, editable priority and contribution candidate, complete Waiting/Blocked/Completed/Cancelled persistence, independent dates, and stale-version feedback.
- The initial failure was confirmed as lifecycle-specific values entered while status remained OPEN; the accepted repair prevents that ambiguous interaction without weakening lifecycle validation.
- OPS-02 was published at `73a1adf356e231338e02c4d0ca94e302d4d97ce1` after this acceptance.

## TASK-07 compatibility maintenance
- The historical validator's synthetic WAITING fixture gained `waiting_party_label`, `waiting_owed`, and fixed `chase_date`.
- Its synthetic BLOCKED fixture gained `blocker_description` and `resolution_owner` while preserving `blocker_note`, priority, and due date.
- Its COMPLETED creation and transition fixtures gained deterministic non-empty `completion_outcome` values.
- Adding the fixed follow-up date made two unique items match a today attention reason at the validator's existing explicit `as_of=2026-08-05`: one `DUE_TODAY`, one `FOLLOW_UP_TODAY`. The validator's count changed from 1 to 2 while its exclusive primary-bucket assertion remains unchanged.
- A focused regression proves a blocked item due today appears once, retains `BLOCKED` and `DUE_TODAY`, contributes once to each applicable count, and retains blocked tier 0.
- These were test-infrastructure compatibility corrections. Production lifecycle validation was not weakened and no assertion was skipped, removed, or xfailed.

## Test results
- Focused OPS-02 suite: `49 passed, 0 failed, 38 warnings`.
- Full suite: `300 passed, 0 failed, 40 warnings` (all 289 published baseline tests plus 11 OPS-02 tests).
- Warnings remain existing FastAPI `on_event` deprecations.
- Scoped `ruff format --check` on changed Python files excluding `app.py`: 14 files already formatted.
- Scoped `ruff check` on changed Python files excluding `app.py`: PASS.
- Direct `ruff check app.py`: 10 inherited findings on lines attributed by `git blame` to earlier commits; OPS-02 introduced no Ruff finding. Repository-wide Ruff compliance is not claimed.
- `git diff --check`: PASS.
- `mypy`: not run and no mypy compliance is claimed.

## Validation command output
```
TASK-07 validation: PASS
TASK-08 validation: PASS
TASK-08R validation: PASS
TASK-09 validation: PASS
TASK-10 validation: PASS
TASK-11 validation: PASS
TASK-11F ASGI acceptance: PASS
TASK-12 validation: PASS
TASK-12 ASGI acceptance: PASS
TASK-13 validation: PASS
TASK-13 ASGI acceptance: PASS
TASK-14 validation: PASS
TASK-14 ASGI acceptance: PASS
TASK-15 validation: PASS
TASK-15 ASGI acceptance: PASS
TASK-16 validation: PASS
TASK-16 ASGI acceptance: PASS
TASK-17 validation: PASS
TASK-17 ASGI acceptance: PASS
TASK-18 validation: PASS
TASK-18 ASGI acceptance: PASS
OPS-01 validation: PASS
OPS-01 ASGI acceptance: PASS
OPS-02 validation: PASS
OPS-02 ASGI acceptance: PASS
```

## Database and compatibility
- No database migration or persisted-domain redesign was added.
- Automated validation used temporary isolated databases and storage only.
- No tracked database or backup appears in the changed-file inventory.
- Existing SQLite databases, work-item IDs, audit history, versioning, context associations, and contribution-candidate values are preserved.
- No dependency, lock, Python, Ruff, mypy, or tool-configuration file was modified.
- `uv.lock` and `uv.lock.armoury-generated-20260811` remain untouched, untracked, and unstaged.

## Decisions I made
- Kept attention reasons non-exclusive and ordering tiers exclusive. Reason/count semantics are independent from primary bucket and ordering semantics.
- Kept the route tied to the established application local-date source and injected fixed dates only at service/test boundaries; no public `as_of` query was added.
- Used explicit `context=any|standalone|bid` plus a separate `bid_id`, rejecting contradictory combinations with HTTP 422.
- Did not add contribution-candidate review, role-profile authoring, non-bid context-link management, TASK-19, integrations, Alice prioritization, or broad redesign.

## Deviations from the task spec
- None. TASK-07 compatibility changes were explicitly authorized during verification.

## Concerns for review
- Direct `app.py` lint retains 10 inherited findings; they were deliberately not remediated under OPS-02.

## Publication safeguards
- Manual browser acceptance and re-acceptance are complete.
- No migration or persisted-domain redesign was introduced.
- Production lifecycle validation was not weakened.
- `uv.lock` and `uv.lock.armoury-generated-20260811` remain explicitly excluded.
- Publication is limited to the current OPS-02 feature branch; no merge, rebase, main modification, force-push, or pull request is authorized.
