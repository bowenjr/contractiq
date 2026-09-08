# Handoff — OPS-08W Bid Workflow Usability Consolidation

## Status

MANUAL ACCEPTANCE PASS — accepted for publication

## Manual acceptance

- OPS-08W manual acceptance: **PASS**.
- Accepted by Jason on **2026-09-08**.
- Platform: **Dell Latitude E6540**.
- No additional implementation correction was required.

OPS-08W corrects the current uncommitted OPS-08 candidate. Nothing has been staged, committed, pushed, rebased, reset, restored, or stashed.

## Root causes

- Bid work was split across authoritative domain registers, so navigation regularly lost the selected Bid and the exact originating section.
- Scope and interface creation exposed the maintenance page immediately after initial entry, which looked like a required second save.
- Vendor-document package terminology made VDRL responsibilities appear to be the parent business concept instead of supporting manufacturer/equipment coverage.
- Commercial readiness identified missing topics while the user still had to rediscover the correct risk, approval, work, and negotiation registers and re-enter context.
- Proposal and My Day views exposed low-level records and attention atoms instead of Bid-level projections from existing authoritative facts.
- Role-profile administration exposed multiple-lineage creation and routine responsibility-domain configuration despite the current one-user, one-role operating model.

## Implemented workflow corrections

- The selected Bid remains visible through a shared header, business-language breadcrumbs, and six revisable Bid sections: Overview & Plan, Requirements & Scope, Manufacturers & Coverage, Commercial & Contract, Proposal & Negotiation, and Award & Handover.
- Scope items can be created in the Bid section and return by PRG to `#scope-and-interfaces`; the created row appears immediately without a redundant detail-page save. Detail pages remain available for editing, relationships, history, concurrency control, audit, and withdrawal.
- Contextual interface entry preselects the originating scope item, posts the Bid origin through rendered controls, and returns to the same Bid section.
- Manufacturer/equipment packages can be created in the Bid section. Package presentation now leads with supplier, equipment/supply scope, requirement coverage, owner, response/evidence state, exceptions/actions, and supporting VDRL coverage.
- Commercial topics are actionable Bid rows. The UI distinguishes configured topics, authored positions, derived readiness concerns, authoritative risks, and approval requirements, and offers position review/revision, not-applicable disposition with a required reason, linked risk, clarification, negotiation, approval, and follow-up actions.
- Known Bid, topic, source position, and origin-section context is prepopulated. Risks, approval requests, negotiations, and work remain explicit confirmed user actions; GET requests create none of them.
- Created relationships are visible from the commercial position and the related risk, approval/decision, or negotiation surface. Advanced pages provide direct Bid-section return actions.
- Proposal inputs are derived from customer requirements/responses, scope, interfaces, manufacturer evidence, commercial positions, decisions, and negotiations, with an authoritative-origin link for each row.
- My Day now renders one primary summary per active Bid, preserves the most severe blocker, groups unresolved counts by business area, shows the nearest meaningful date, and keeps standalone My Work separate. Underlying attention reasons remain intact for deterministic reporting.
- Internal display text found during visual review (`BID_NO_BID`) was replaced with the business label “Bid/no-bid approval.”

## Role-profile simplification

- The user-facing concept is now **My Role** under Administration.
- With no lineage, initial setup remains available. Once a lineage exists, normal UI shows the effective role and controlled revision, hides creation of another lineage, and keeps prior versions in a collapsed History section.
- Responsibility domains remain because they are consumed by My Work categorization, role alignment, and reporting. Their effect is explained and configuration is placed in a collapsed advanced area.
- Persisted role history and the underlying lifecycle were not rewritten or deleted.

## Authoritative and derived data

- Existing Bid, controlled source, requirement/response, scope, interface, supplier/package, vendor-document, commercial-position, risk, decision/approval, negotiation, My Work, readiness, and handover domains remain authoritative.
- Bid summaries, grouped attention, proposal inputs, coverage counts, readiness effects, and whole-Bid handover views are derived projections and are not stored as duplicate business records.
- Commercial position versions remain immutable. Every authoritative risk, approval request, negotiation, and work item requires a POST from an explicit user action.

## Files created

- `docs/tasks/OPS-08W-bid-workflow-usability-consolidation.md` (56 lines) — correction specification and acceptance boundary.
- `scripts/asgi_acceptance_ops08w.py` (409 lines) — dependency-free connected Bid workflow acceptance.
- `scripts/validate_ops_08w.py` (16 lines) — OPS-08W validator entry point.
- `tests/unit/test_ops08w.py` (40 lines) — focused workflow, copy, aggregation, and boundary coverage.

## Files modified for OPS-08W

- `app.py` — Bid projections, contextual entry/return handling, proposal derivation, My Day aggregation, explicit linked-record browser flows, and one-lineage My Role routing.
- `core/gates.py` — business-language gate detail while preserving gate semantics.
- `templates/bid_detail.html` — persistent Bid context, section navigation, inline scope/interface/package entry, commercial topic actions, and derived proposal presentation.
- `templates/scope_interfaces.html` — contextual scope preselection, relationship explanation, and rendered return context.
- `templates/administration.html`, `templates/role_framework.html`, `templates/role_profile_detail.html` — My Role terminology and simplified normal flow.
- `templates/my_day.html` — one summary per Bid with grouped attention and separate My Work.
- `templates/commercial.html`, `templates/commercial_position_detail.html`, `templates/commercial_qualifications.html` — position review and linked context.
- `templates/contextual_work.html`, `templates/contract_risk_detail.html`, `templates/contract_risks.html`, `templates/decisions.html`, `templates/negotiations.html` — explicit confirmation and exact Bid return navigation.
- `templates/supplier_detail.html`, `templates/vendor_document_package.html` — manufacturer/package hierarchy and direct Bid return.
- `HANDOFF.md` — this implementation and verification record.

The working tree also retains the authorized uncommitted OPS-08 candidate in `core/ops08.py`, `core/gate_service.py`, `core/handover.py`, `pyproject.toml`, its templates, scripts, tests, and specification. Those files remain part of the combined candidate and were not staged.

## Migration impact

OPS-08W adds no migration. It uses the existing uncommitted additive OPS-08 schema for configurable topic versions, immutable commercial-position versions, and typed relationships. No table or column was removed, no destructive ALTER or semantic backfill was added, and no historical record was rewritten.

Fresh isolated initialization and restart both produced 106 tables. The OPS-08 migration marker `ops_08_commercial_contract_risk_workspace_v1` appeared exactly once on both starts. On both starts, `PRAGMA foreign_key_check` returned `[]` and `PRAGMA integrity_check` returned `ok`.

## Measured workflow and data-flow result

The dependency-free ASGI scenario completed the specified representative workflow through rendered browser controls and persisted relationships:

```text
OPS-08W ASGI acceptance: PASS
Workflow measurements: rendered user actions=26; departures from Bid workspace=6; lost context=0; duplicate data entry=0; raw JSON/manual URL requirements=0; prerequisite surprises=0; dead ends=0
```

The scenario creates/opens a Bid, registers a controlled source idempotently, creates a requirement and response, creates and links scope without a second save, adds a contextual interface, builds manufacturer coverage with confirmed and unresolved evidence, reviews commercial topics, explicitly creates linked risk/approval/work/negotiation records, verifies derived proposal inputs, readiness, My Day, and whole-Bid handover, checks all advanced return paths, and proves GET requests do not mutate data or add audit events.

## Test results

- Final focused selection: **131 passed, 34 warnings**.
- One full unexcluded suite: **436 passed, 88 warnings; 0 failed, 0 skipped, 0 collection errors**.
- OPS-08W validator and ASGI acceptance: **PASS**; measured workflow: **26 rendered actions, 6 Bid-workspace departures, 0 lost context, 0 duplicate entry, 0 raw JSON/manual URL requirements, 0 prerequisite surprises, 0 dead ends**.
- OPS-08 validator and ASGI acceptance: **PASS**.
- OPS-07 validator: **31 passed, 14 warnings**. The corrected OPS-07 ASGI acceptance: **PASS**.
- OPS-06 validator and ASGI acceptance: **PASS**.
- Directly affected TASK-08, TASK-08R, TASK-09, TASK-10, TASK-13, TASK-14, TASK-15, and TASK-16 validators: **PASS**.
- `ruff format --check` and `ruff check` on the final candidate plus corrected fixture: **PASS**.
- `mypy --strict core/ops08.py`: **PASS**.
- `git diff --check`: **PASS**.

The warnings are the existing FastAPI `on_event` deprecation warnings; no test failed.

## OPS-07 fixture diagnosis and correction

**Classification A — stale synthetic fixture.** The published OPS-07 ASGI fixture directly executed SQL that set `verification_status='CONFIRMED_COMPLIANT'` while omitting `response_received_date`. `ManufacturerVerification.validate_status_fields` has required the authoritative `response_received_date` for received-response statuses since the pre-OPS-07 vendor-document model. Normal browser and service updates validate through `RequirementVerificationUpdate` and `ManufacturerVerification` before repository persistence; the bulk path only permits non-received statuses. No accepted normal pre-OPS-08W workflow can persist this combination.

OPS-08W did not create or mutate the invalid row. Its Bid workspace projection reads package requirements through the authoritative repository, so it exposed the synthetic fixture's invalid state during rendering. The only correction was `scripts/asgi_acceptance_ops07.py`: the direct fixture update now supplies deterministic `response_received_date='2026-08-21'`, preserves the confirmed-compliant status, and asserts the date survives repository hydration and package-page rendering. Production validation, persistence, migrations, templates, collected tests, and configuration were not changed.

The 131-test focused and 436-test full-suite results remain valid because the sole post-suite change is this standalone ASGI fixture, which pytest does not collect.

## Validation command output

```text
OPS-08W ASGI acceptance: PASS
Workflow measurements: rendered user actions=26; departures from Bid workspace=6; lost context=0; duplicate data entry=0; raw JSON/manual URL requirements=0; prerequisite surprises=0; dead ends=0
OPS-08W validation: PASS
OPS-07 ASGI acceptance: PASS
```

## Final commands

All `uv` commands used `UV_CACHE_DIR=/tmp/contractiq-ops08w-uv-cache-20260906` and `PYTHONPATH=/home/bowen/dev/projects/contractiq`.

```text
uv run python -m pytest -q tests/unit/test_commercial.py tests/unit/test_commercial_scenarios.py tests/unit/test_contract_risk.py tests/unit/test_gate_service.py tests/unit/test_gates.py tests/unit/test_my_day.py tests/unit/test_my_day_service.py tests/unit/test_negotiation.py tests/unit/test_ops08.py tests/unit/test_ops08w.py tests/unit/test_ops_navigation.py tests/unit/test_proposals.py tests/unit/test_role_profile_service.py tests/unit/test_role_profile_ui.py tests/unit/test_role_profiles.py tests/unit/test_scope_interfaces.py tests/unit/test_supplier_assurance.py tests/unit/test_vendor_document_control.py tests/unit/test_work_item_repository.py tests/unit/test_work_item_ui.py tests/unit/test_work_items.py  -> 131 passed, 34 warnings
uv run python -m pytest -q                                           -> 436 passed, 88 warnings
uv run python scripts/validate_ops_08w.py                            -> PASS
uv run python scripts/asgi_acceptance_ops08w.py                      -> PASS
uv run python scripts/validate_ops_08.py                             -> PASS
uv run python scripts/asgi_acceptance_ops08.py                       -> PASS
uv run python scripts/validate_ops_07.py                             -> PASS
uv run python scripts/asgi_acceptance_ops07.py                       -> PASS
uv run python scripts/validate_ops_06.py                             -> PASS
uv run python scripts/asgi_acceptance_ops06.py                       -> PASS
uv run python scripts/validate_task_08.py, validate_task_08r.py,
  validate_task_09.py, validate_task_10.py, validate_task_13.py,
  validate_task_14.py, validate_task_15.py, validate_task_16.py      -> PASS
uv run ruff format --check [final changed Python]                    -> PASS
uv run ruff check [final changed Python]                             -> PASS
uv run mypy --strict core/ops08.py                                   -> PASS
git diff --check                                                     -> PASS
```

## Frozen manifests

- Historical original OPS-08/OPS-08W candidate: `/tmp/ops08w-final-manifest-start.sha256`, 30 files, SHA-256 `88e2616e5a7ee25bdfb036a1db7f23f59cb39926b208c87ff2b6cafcb0920a15`.
- That original set intentionally excluded the already-published OPS-07 ASGI fixture. A derived 31-file pre-fixture baseline, using the fixture's `HEAD` content, is `/tmp/ops08w-final-manifest-pre-fixture-extended.sha256`.
- Post-fixture frozen manifest: `/tmp/ops08w-final-manifest-post-fixture.sha256`, SHA-256 `bd5bd262088564255a3d578dd099876f2dbc3631e3a903e742266e8e8d4f606d`.
- The extended pre-fixture versus post-fixture comparison contained exactly one path: `scripts/asgi_acceptance_ops07.py`.
- Final regeneration at `/tmp/ops08w-final-manifest-end.sha256` is byte-for-byte identical to the post-fixture manifest.

## Visual review

- Fresh isolated visual data was created from the OPS-08W rendered-control acceptance workflow. Screenshots were captured after final contextual-control changes at 1366×768 and 1920×1080.
- Inspected: My Day, Bid overview, Scope & Interfaces, Manufacturers & Coverage, manufacturer/equipment package detail, Commercial & Contract, commercial-position detail, Contract Risks, Proposal & Negotiation, Bid Handover, and Administration/My Role.
- Persistent Bid identity and return destinations rendered correctly. The package and commercial-position detail pages exposed direct Bid returns; advanced package controls and My Role responsibility domains/history were collapsed.
- Cards, tables, labels, controls, and checkbox alignment were readable without observed clipping or overlap. The reviewed surfaces used business-language labels and contained no raw JSON or raw enum labels in the normal workflow.
- Screenshots are outside the repository under `/tmp/ops08w-final-*-1366.png` and `/tmp/ops08w-final-*-1920.png`. The isolated application server and browser processes were stopped.

## Decisions made

- No new schema was needed. Bid workflow state is navigation context and derived projection over existing authoritative domains.
- Responsibility domains were retained because repository inventory showed active My Work and reporting consumers; they were moved to advanced My Role configuration.
- Contextual related-record routes prevalidate Bid ownership before authoritative creation, then use the established domain services so IDs, actors, timestamps, provenance, lifecycle validation, optimistic concurrency, and same-transaction record/audit behavior remain server controlled.
- Readiness continues to use the accepted governance engine. A non-`CLEAR` verdict still requires attention, and aggregation never replaces or suppresses the highest-severity reason.

## Known limitations and deferred work

- Final customer proposal generation remains OPS-09.
- Post-award submissions, customer review/resubmission, execution milestones, closeout, award/PO reconciliation, InEight integration, external integrations, and AI/LLM behavior remain deferred.
- Negotiation status and relationships flow into the Bid and proposal projection. A changed negotiated position still becomes authoritative through the existing explicit commercial-position revision action; the system does not silently manufacture a new immutable version from a negotiation outcome.
- Advanced registers remain available for lifecycle history and specialist maintenance; OPS-08W concentrates normal work in the Bid without deleting those domains.

## Safeguards

- Stale-version and cross-Bid requests are rejected; duplicate links remain prevented.
- Authoritative writes retain same-transaction audit behavior and rollback tests.
- Commercial-position history and configurable topic-version history remain immutable.
- Controlled-source traceability and OPS-07W idempotent source registration remain intact.
- Formula-safe CSV exports passed focused and full regression coverage.
- GET mutation/audit counts are tested across contextual risk, approval, negotiation, and work entry.
- `data/contractiq.db` remains absent.
- `uv.lock` remains untracked and byte-for-byte unchanged (`4048b7aca3b2c5f8af7d9c0bdd7a57895233f1139bc8fc0eb3439d553cd72bc8`).
- `.claude/settings.local.json` retains its authorized local-only content and starting hash (`0ddf81cf26da4ffa38914ce3f999784c7ffe2c089cab7bbd2989fce1d0f90cf5`).
- The retained stash is unchanged.
- Final verification retained HEAD and recovery reference `1785c852f58eca3f77716fc19410674ac5c45565`, parent parity `0 0`, an empty index, and no `data/contractiq.db`.

## Manual browser acceptance

```bash
OPS08W_MANUAL_ROOT="$(mktemp -d /tmp/contractiq-ops08w-manual-XXXXXX)"
CONTRACTIQ_DB_PATH="$OPS08W_MANUAL_ROOT/app.db" CONTRACTIQ_DOCUMENT_ROOT="$OPS08W_MANUAL_ROOT/documents" UV_CACHE_DIR=/tmp/contractiq-ops08w-uv-cache-20260906 PYTHONPATH=/home/bowen/dev/projects/contractiq uv run uvicorn app:app --host 127.0.0.1 --port 8000
```

1. Start ContractIQ against isolated temporary storage and create a Bid.
2. From that Bid, walk left-to-right through Requirements & Scope, Manufacturers & Coverage, Commercial & Contract, Proposal & Negotiation, and Award & Handover; then move backward and revise one item.
3. In Requirements & Scope, add scope inline and use its **Add interface** row action. Confirm the scope is preselected and creation returns to the same Bid section.
4. Add a manufacturer/equipment package, open advanced details, and confirm **Back to Bid — Manufacturers & Coverage** returns to the originating Bid.
5. Review a commercial topic and explicitly create one linked risk, approval request, follow-up, and negotiation. Confirm every form carries the Bid/topic context and returns to Commercial & Contract.
6. Open Proposal & Negotiation and confirm each input names its authoritative origin. Open My Day and confirm one primary summary for the Bid plus a separate My Work area.
7. Open whole-Bid handover and verify the same requirement, scope, manufacturer evidence, position, risk, approval, negotiation, and unresolved action relationships.

## Deviations from the task specification

None.

## Concerns for manual review

- Confirm the amount of gate/readiness detail above each Bid section feels appropriate at 1366×768; the persistent context is intentionally prominent and pushes section-specific forms below the initial viewport.
- Confirm the commercial action density is acceptable for Jason’s routine review. The actions are explicit and contextual, but all available authoritative follow-ups are visible in the row.

OPS-08W FINAL VERIFICATION STATUS:
MANUAL ACCEPTANCE PASS — ACCEPTED FOR PUBLICATION
