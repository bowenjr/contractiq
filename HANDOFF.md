# Handoff — OPS-09

## Status

COMPLETE — implementation, automated verification, and manual browser acceptance are complete.

OPS-09 IMPLEMENTATION STATUS:
ACCEPTED FOR PUBLICATION

No file has been staged, committed, pushed, merged, rebased, reset, restored, or stashed.

## Starting baseline

- Branch: `ops-09-proposal-readiness-issue-control`
- Starting HEAD: `1f0262b50529687ab6604c29393c5c37cbf0b5e6`
- Starting remote: `origin/ops-09-proposal-readiness-issue-control` at the same SHA
- Starting recovery pointer: `backup/ops-09-pre-implementation-20260908` at the same SHA
- Starting parity: `0 0`
- Starting index/worktree: clean
- Starting repository manifest: `/tmp/contractiq-ops09-starting-repository-manifest.sha256` (7,562 files)
- Retained stashes: two pre-existing OPS-05 stashes; neither was changed
- `data/contractiq.db`: absent at start and after verification

## Source-contract provenance and validation

Jason explicitly authorized the recovered bundle at
`/home/bowen/dev/projects/proposal-studio-exchange-contract-v1` as the authoritative frozen V1
source. The original Proposal Studio Git repository and source commit are unavailable. This
implementation therefore claims Jason-authorized, hash-verified byte provenance, not upstream Git
commit provenance.

The source bundle was read without modification. Its contract version is `1.0.0`; both schemas use
JSON Schema Draft 2020-12. Both examples parse as strict UTF-8 JSON, validate against their frozen
schemas, follow the documented ordering/null/list/path rules, and agree on package identity/hash.

Raw SHA-256 values, rechecked after implementation:

- Package schema: `3416365b51abedf3dcc7f17996ecb08467997b660679ae890c5624761d472462`
- Generation-manifest schema: `da5c24e75371498109ba28d81d8840753a30c28bbfb9554fc2a5f288649ef408`
- Package example: `8943882932419d3c8ffc03e1b1552433d5c6b807d5fa69dea039275ac245c3fc`
- Generation-manifest example: `ef30b682e058039b8eab9d8e98c564785b657bc5dc1b518635300e8868304c80`
- Contract document: `864e578a33e8fd17c6f753bb92d6333c9d83ae82728d10746221d74dea27ea30`
- Incomplete reference test: `5e16ba335763de0cd03a76546467a597310aaa3180d217ec4568053257bdbec0`
- Canonical example-package SHA-256: `f087af73dc3306d0ca174ffaa6df464f5c6102a86929926a038797e9b71b310b`

All six vendored source files are byte-identical to the authorized bundle. The supplied reference
test was not executed because its five original invalid fixtures were not supplied. It is retained
outside the collected test tree as non-executable reference evidence. No missing Proposal Studio
evidence was reconstructed or represented as original.

## Reuse inventory

| Exchange fact | Existing authority reused | OPS-09 projection/decision |
|---|---|---|
| Bid/customer/opportunity | `bids` | Reuse names, location, due date and deterministic revision; nullable V1 IDs remain null |
| Controlled sources | `documents`, `document_versions` | Exact current version/media/raw hash; reference only, no transport invention |
| Requirements/responses | `requirements` | Active authoritative statement, response and disposition |
| Scope/interfaces | `scope_interface_items`, `scope_interfaces` | Offer boundaries and dependencies; unresolved/owner gaps block |
| Manufacturer/VDRL | `vendor_bid_packages`, `vendor_bid_requirements` | Existing response/evidence status; empty equipment list where no equipment authority exists |
| Commercial | Current `commercial_position_versions` | Current authoritative wording/status; missing wording remains missing and blocks |
| Qualifications/deviations | Requirement dispositions | Project only when authoritative proposal wording exists; no duplicate register |
| Contract risks | `contract_issues`, latest assessment/review | Material risk requires accepted review of the current assessment |
| Decisions/approvals | `approvals`, `decision_cases`, `approval_routes` | Preserve existing authority/policy vocabulary and IDs |
| Pricing | Selected `scenario_baselines`/version | Reference and fingerprint only; no customer-facing breakdown |
| Delivery | Pre-award/customer-facing `deliverable_items` | Existing commitment wording/date/status; absence blocks |
| Proposal inputs | Existing domain facts | No separate scalar facts exist, so V1 list remains empty rather than fabricated |
| Supporting documents | Controlled document/version | Identity/revision/media/hash; `relative_path: null` because transport is undefined |
| Readiness/gates | `evaluate_readiness` and existing services | Compose, do not replace, the deterministic gate engine |
| Audit/concurrency | `audit_log`, SQLite transaction patterns | Same-transaction audit and mutable-candidate version tokens |
| Handover/report | `BidHandoverService`, existing CSV controls | Add issued-offer evidence; generation/approval never means issued |

The full reuse matrix and design are in `docs/tasks/OPS-09-proposal-readiness-issue-control.md`.

## Specification and migration

The OPS-09 specification documents the role boundary, contract hashes, reuse decisions, workflow,
readiness, canonicalization, import security, staleness, approval/issue rules, immutable baselines,
audit/concurrency, additive migration, UI, tests, and deferred V1 limits.

Migration `ops_09_proposal_exchange_issue_control_v1` is additive and idempotent. It adds ten
OPS-09 tables, three indexes, and fourteen update/delete-prevention triggers. IDs, actor and time are
server controlled. Export, manifest, artifact, issue-event, issued-baseline and issued snapshot rows
are append-only. Candidates alone are mutable and use an integer optimistic-concurrency token. There
is no backfill, destructive alteration, production migration, or invented historical data.

Fresh isolated initialization and restart used
`/tmp/contractiq-ops09-restart-nFrGlk/contractiq.db` and temporary document/artifact roots. Results:

```text
fresh initialization: PASS
restart initialization: PASS
foreign_key_check: []
integrity_check: ok
ops09 migration: ops_09_proposal_exchange_issue_control_v1
PRODUCTION DATABASE ABSENT
```

## Workflow and control rules

The browser workflow is Bid workspace → Proposal & Negotiation → Proposal readiness and issue
control. It retains the Bid header/breadcrumbs and exposes one next action, exact blockers with
authoritative correction links, current export, generation evidence, artifact verification, approval
status, manual issue, and progressive-disclosure immutable history.

- Readiness composes the existing deterministic gate report and authoritative domain facts; no LLM
  is imported into control logic and record population alone never means complete.
- Export creates a deterministic canonical V1 package for one Bid/source snapshot. Unchanged facts
  replay the latest immutable export without a second audit; changed facts create a successor.
- Strict parsing rejects invalid UTF-8, BOMs, duplicate object members and non-finite numbers before
  schema validation. Contract validation enforces required/additional fields and semantic ordering.
- Import validates package/Bid identity, canonical source hash, successful generation, exact artifact
  set, filenames, media roles, unsafe/ambiguous paths and raw-byte hashes before mutation.
- Failed imports retain understandable HTML and create no file, database row, audit or readiness
  transition. Successful imports move files and commit manifest/artifact/candidate/audit evidence as
  one controlled operation, with rollback cleanup on failure.
- Staleness compares deterministic per-area hashes of the facts actually exported. Changed areas are
  named; a new current export immediately marks the prior candidate superseded. An issued baseline
  remains historical truth.
- Approval and issue recheck current source hashes, readiness, existing approval authority, artifact
  bytes, candidate identity/state and version. The issue action only records a manual customer event;
  it sends nothing.
- Issue atomically creates the event, full immutable baseline, artifact/approval snapshots, candidate
  transition and audit. Injected audit failure rolls back the entire transaction.
- Handover shows revision/date, package and manifest identity/hash, artifact identities/hashes,
  approval references, validity, superseded issues, later changes and remaining actions.

## Files created

- `contracts/proposal-studio/v1/CONTRACTIQ_EXCHANGE_CONTRACT_V1.md` (765 lines)
- `contracts/proposal-studio/v1/PROVENANCE.md` (24 lines)
- `contracts/proposal-studio/v1/examples/proposal-generation-manifest-v1.example.json` (63 lines)
- `contracts/proposal-studio/v1/examples/proposal-package-v1.example.json` (284 lines)
- `contracts/proposal-studio/v1/proposal-generation-manifest-v1.schema.json` (543 lines)
- `contracts/proposal-studio/v1/proposal-package-v1.schema.json` (1,336 lines)
- `contracts/proposal-studio/v1/reference/test_exchange_contract_v1.py` (255 lines)
- `core/proposal_exchange.py` (157 lines)
- `core/proposal_exchange_contract.py` (396 lines)
- `core/proposal_exchange_service.py` (1,965 lines)
- `docs/tasks/OPS-09-proposal-readiness-issue-control.md` (157 lines)
- `scripts/asgi_acceptance_ops09.py` (221 lines)
- `scripts/validate_ops_09.py` (66 lines)
- `templates/proposal_issue_control.html` (19 lines)
- `tests/unit/test_ops09.py` (724 lines)

## Files modified

- `app.py` — initializes OPS-09 on configured storage and adds Bid-context export/import/approval/
  issue/download routes with PRG and retained 404/409/422 HTML.
- `core/handover.py` — optionally composes issued-offer evidence into the existing read-only handover.
- `pyproject.toml` — includes the three new typed production modules in strict configured mypy.
- `templates/bid_detail.html` — adds the Proposal & Negotiation entry/status projection.
- `templates/bid_handover.html` — presents issued-offer evidence without equating generation with issue.
- `HANDOFF.md` — this implementation and verification record.

## Test results

- Focused OPS-09 suite: `9 passed, 2 warnings`; zero failed.
- Full unexcluded suite: `445 passed, 90 warnings`; zero failed/skipped/collection errors using
  `.venv/bin/python -m pytest`.
- A preliminary direct `.venv/bin/pytest` invocation collected no tests because the existing
  `tests/unit/test_ops08w.py` could not import the repository `scripts` namespace when the executable
  entry point omitted the repository root from `sys.path`. The repository-compatible module
  invocation above collected and passed all 445 tests.
- OPS-09 validator: pass.
- OPS-09 dependency-free ASGI acceptance: pass.
- OPS-06 validator/ASGI: pass.
- OPS-07 validator (`31 passed`) and ASGI: pass.
- OPS-07W validator (`70 passed`) and ASGI: pass.
- OPS-08 validator/ASGI: pass.
- OPS-08W validator/ASGI: pass.
- Directly affected TASK-18 handover validator/ASGI: pass.
- `ruff format --check`/`ruff check` on changed Python: pass.
- Configured strict `mypy`: `Success: no issues found in 26 source files`.
- Strict scoped mypy on the three new production modules: pass.
- `git diff --check`: pass.

The 90 warnings are existing FastAPI `on_event` deprecation warnings.

## Validation command output

```text
user actions: 5
departures from bid context: 0
context losses: 0
duplicate data entry: 0
prerequisite surprises: 0
dead ends: 0
raw json or manual url requirements: 0
OPS-09 validation: PASS
OPS-09 ASGI acceptance: PASS
```

The measured workflow performs five rendered mutations: prepare/download package, import manifest
and artifacts, confirm approved-for-issue, record manual customer issue, then prepare a successor
after changing an authoritative Bid fact. Redirects and GETs are not counted as actions.

## Visual findings

Rendered route tests and ASGI acceptance verify the shared stylesheet, Bid breadcrumbs/header,
business-language labels, correction links, retained errors, forms, history and absence of routine
raw JSON/manual URLs. Static responsive inspection found a two-column control grid and four-column
summary at desktop widths, bounded content width, wrapping forms/hashes, overflow-safe tables, and a
single-column breakpoint below 900 px; these rules are compatible with both 1366×768 and 1920×1080.

Literal screenshot inspection at 1366×768 and 1920×1080 remains outstanding because the execution
environment has no Chromium, Chrome, Firefox, Playwright, Selenium, WebKit, WeasyPrint or other HTML
rendering engine installed. No network installation was attempted. This is the sole reason the task
is awaiting manual browser acceptance rather than marked complete.

## Candidate manifest

The final implementation candidate (excluding `HANDOFF.md`, whose report contains this value) is
listed in `/tmp/contractiq-ops09-final-candidate.sha256`.

- Manifest file count: 20
- Manifest SHA-256: `90714c975776c7f258ee37588f01be03cbd7dc5252a1a00937b25b56fc2cbc5a`

## Manual browser acceptance runtime and checklist

Run only against isolated temporary storage:

```bash
OPS09_MANUAL_ROOT="$(mktemp -d /tmp/contractiq-ops09-manual-XXXXXX)"
CONTRACTIQ_DB_PATH="$OPS09_MANUAL_ROOT/contractiq.db" \
CONTRACTIQ_DOCUMENT_ROOT="$OPS09_MANUAL_ROOT/documents" \
CONTRACTIQ_PROPOSAL_ARTIFACT_ROOT="$OPS09_MANUAL_ROOT/proposal-artifacts" \
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
```

1. At both 1366×768 and 1920×1080, open a realistic Bid → Proposal & Negotiation → Proposal
   readiness and issue control. Confirm the Bid identity and return path remain visible and no
   controls overlap or clip.
2. Review every blocker and correction link. Confirm the main next action is unambiguous and that
   prerequisite text appears before each disabled action.
3. Prepare/download a package, regenerate from it in the separate Proposal Studio, and import its
   manifest plus exact DOCX/PDF files. Confirm no JSON editing or URL entry is requested.
4. Try one bad artifact/hash and confirm retained HTML says nothing changed. Then import the valid
   generation, approve, and record the manual customer issue.
5. Inspect immutable history and Bid handover. Confirm generation/approval alone never displays as
   issued, and the issued revision exposes package/manifest/artifact/approval evidence.
6. Change one included authoritative fact, return to issue control, and confirm the changed area and
   successor action are visible while the prior issued baseline is unchanged.

## Decisions I made

- Used the Jason-authorized recovered bundle as frozen authority while explicitly preserving the
  missing upstream-commit limitation.
- Implemented a dependency-free evaluator for the exact schema vocabulary used by the frozen files,
  plus independent ordering/path/cross-reference rules, so runtime validation performs no network
  resolution and adds no dependency/lockfile change.
- Used nullable/empty V1 values only where the contract permits them. Missing required business facts
  are blockers; commercial text and qualification wording are not fabricated to satisfy schema.
- Derived source hashes from exact projected values, not `updated_at`; the timestamp-derived Bid
  revision is descriptive while per-area canonical hashes control staleness.
- Kept Proposal Studio artifact bytes in configured local controlled storage and referenced existing
  supporting source documents because V1 does not define source-document transport.

## Deviations from the task spec

- The incomplete upstream reference test was intentionally not run because its original invalid
  fixtures were not supplied, exactly as directed. Equivalent negative cases are executable in the
  independent ContractIQ suite.
- Automated pixel/screenshot inspection could not run because no browser/rendering engine exists in
  the environment. Static responsive and rendered-response inspection passed; manual viewport review
  is required.
- The task instructed a branch/commit/push discipline through standing rules but explicitly prohibited
  staging, committing or pushing for OPS-09. The task-specific prohibition was followed.

## Concerns for review

- Manually assess density of the readiness/blocker/history page at 1366×768 and confirm the 900 px
  breakpoint and horizontal table scrolling feel appropriate.
- Review whether the frozen V1's reference-only supporting-document model is operationally sufficient;
  ContractIQ deliberately does not invent a file transport layout.
- Review the dependency-free schema evaluator when V2 is designed. It intentionally implements only
  the JSON Schema keywords present in the frozen V1 schemas and rejects unsupported external `$ref`s.

## Reporting requirements from the task

- Starting SHA, source provenance/hashes, reuse inventory, migration, workflow/control rules, changed
  files, test/static/database results, workflow measurements, visual limitation, deferred V1 limits,
  production-database proof, candidate manifest, manual acceptance runtime/checklist and repository
  state are all reported above or below.
- Deferred: customer-facing pricing breakdown, supporting-file transport, richer VDRL detail, stronger
  approval-person identity, upstream Proposal Studio commit provenance, generation/templates/layout,
  network/email/InEight integration and post-award execution.
- `data/contractiq.db` remains absent. `uv.lock` remains absent and was not created.
- `.claude/settings.local.json` is unchanged from the starting manifest at
  `b5eed657a6add4e6f6dd25f4138ee95ee0cac908c34bdcbbf0bc2eaaba6d134e`.

## Exact repository status

Ending verification preserved HEAD, remote and recovery pointer at
`1f0262b50529687ab6604c29393c5c37cbf0b5e6`, parity `0 0`, and an empty index. Both retained OPS-05
stashes remain. The protected local settings hash matches the starting manifest, `uv.lock` remains
absent, and `data/contractiq.db` remains absent. Exact `git status --short --branch`:

```text
## ops-09-proposal-readiness-issue-control...origin/ops-09-proposal-readiness-issue-control
 M HANDOFF.md
 M app.py
 M core/handover.py
 M pyproject.toml
 M templates/bid_detail.html
 M templates/bid_handover.html
?? contracts/
?? core/proposal_exchange.py
?? core/proposal_exchange_contract.py
?? core/proposal_exchange_service.py
?? docs/tasks/OPS-09-proposal-readiness-issue-control.md
?? scripts/asgi_acceptance_ops09.py
?? scripts/validate_ops_09.py
?? templates/proposal_issue_control.html
?? tests/unit/test_ops09.py
```

## Final manual acceptance and publication authorization

- OPS-09 manual browser acceptance: **PASS**
- Machine: `Armoury`
- Date: `2026-09-10`
- Jason's assessment: Functionality appears acceptable; refinements may be identified through
  real-world use.
- Automated evidence recorded above is accepted: focused OPS-09 tests (9 passed), full suite
  (445 passed), predecessor validators and ASGI acceptance, ruff, scoped strict mypy, fresh
  database initialization/restart, foreign-key check, integrity check, and the OPS-09 validator
  and dependency-free ASGI acceptance.
- Frozen product candidate was rechecked before publication against the 20-file manifest:
  `90714c975776c7f258ee37588f01be03cbd7dc5252a1a00937b25b56fc2cbc5a`.
- Exact files authorized for the OPS-09 commit are this `HANDOFF.md` plus the 20 frozen candidate
  paths listed in the Candidate manifest section above.
- No production database is included. No source bundle from
  `/home/bowen/dev/projects/proposal-studio-exchange-contract-v1` is included. No machine-local
  configuration, runtime storage, cache, virtual environment, screenshot, lockfile, or dependency
  artifact is included.
- Publication authorization: authorized by the 2026-09-10 manual acceptance instruction.
