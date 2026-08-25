# OPS-05B — Bid-Stage Vendor Document Requirements and Handover

## Authority and redesign reason

- Branch: `ops-05-vendor-document-control`.
- Published baseline/HEAD: `0b6cc283b2e5e51bd5979b74405d119b4156e477`.
- Recovery pointer: `backup/ops-05-pre-implementation-20260817`.
- Pre-OPS-05R snapshot: `d85fb955e3945a8c4c453096a27c185686071289`.
- Pre-OPS-05B redesign snapshot: `ba1729da4e854e076f7b29e08178bdb5d73f7434`.

OPS-05R manual browser acceptance failed. Although its integrity controls passed automated and independent review, the product was difficult to navigate, appeared to be a standalone project-management application, obscured its relationship to the active bid, and overemphasized post-award submission/review tracking.

OPS-05B supersedes the unpublished OPS-05/OPS-05R/OPS-05U design. ContractIQ is used by the bid manager during quotation. An awarded bid is handed to another execution owner or system; ContractIQ produces the vendor-document handover information but does not manage the awarded project.

The completed independent role-alignment review dated 2026-08-21 confirmed this bid-stage direction. Its test-collection and navigation failures were observations from a concurrently changing worktree; the stabilization run verifies the frozen candidate directly. The superseded post-award engine remains recoverable only in the two retained stashes and is not active in production code or the browser.

Production verification before redesign found no OPS-05 tables, triggers, indexes, views or migration markers. The unpublished migration is therefore replaced rather than upgraded. Disposable databases made by earlier unpublished versions are not supported.

## Primary outcome and boundary

The Bid is the only parent context:

```text
Bid
  → Supplier/equipment package
    → Customer VDRL requirements
      → Manufacturer bid-stage verification
  → Handover report
```

VDRL means **Vendor Document Requirement List — the customer’s list of documents and submission stages expected from the supplier.**

There is no Project entity, awarded-project conversion, execution lifecycle, actual submission register, customer review-return cycle, resubmission tracking, closeout, InEight integration, external network operation, notification, automatic file transfer or My Work creation.

## Browser workflow

1. Open or create an authoritative Bid.
2. Open **Vendor Document Requirements** within that Bid.
3. Add a Bid-owned **Supplier/equipment package**.
4. Import the customer/EPCM VDRL CSV or enter customer requirements manually.
5. Bulk-assign the proposed manufacturer and internal owner.
6. Record manufacturer confirmation, committed stages/timing, evidence and exceptions inline in the compliance register.
7. Record clarification/deviation references and the bid disposition without duplicating existing control domains.
8. Resolve the deterministic readiness reasons.
9. Export the vendor-document handover CSV for the execution owner.

Routine workflow never exposes raw JSON, ruleset payloads, obligation rows, database terms or execution status codes. The generic identity-free VDRL template is installed automatically and read-only. Template information is secondary under administration.

## Supplier/equipment package

Every package belongs to an existing Bid and records:

- Package name and code.
- Customer/EPCM.
- Proposed manufacturer and optional contact.
- Internal owner.
- Customer VDR/VDRL reference and revision.
- Anticipated award/PO date and optional forecast delivery date.
- Notes.
- Automatic generic template identity/version.
- Server-owned ID, token, provenance and timestamps.

Normal browser creation cannot create an unassociated package. Invalid Bid IDs return retained HTTP 422 feedback with no package or audit mutation.

## Preserved customer requirement

Each package-specific VDRL row stores the customer values separately from the manufacturer response:

- Requirement code, deliverable title and description.
- Required/not-required and package applicability.
- Requested submission stages.
- Timing anchor and offset.
- Original contractual timing text and separately calculated/preserved contractual date.
- Customer notes.
- Source row/reference and VDRL revision.

The original fields are immutable after creation. Repository update operations only write manufacturer-verification columns, and a SQLite trigger rejects direct overwriting of customer fields.

The customer VDRL format and stage vocabulary may vary by EPC/customer. The generic template supplies common bid-stage choices, while imported original values remain authoritative.

## Manufacturer verification

Typed statuses:

- `NOT_REVIEWED`
- `AWAITING_MANUFACTURER`
- `CONFIRMED_COMPLIANT`
- `CONFIRMED_WITH_EXCEPTION`
- `CLARIFICATION_REQUIRED`
- `CANNOT_COMPLY`
- `NOT_APPLICABLE`

Separate response fields record manufacturer, response source/contact, owner, request/response dates, committed stages/timing, evidence, notes, proposed exception, commercial impact, bid disposition/approval, clarification/deviation references, unresolved action and handover note.

Validation rules include:

- Received confirmation statuses require a response date.
- `CONFIRMED_WITH_EXCEPTION` requires a modification or exception.
- `CANNOT_COMPLY` requires a reason and bid disposition.
- `CLARIFICATION_REQUIRED` requires a question/action.
- `NOT_APPLICABLE` requires a reason.
- An approved disposition requires a non-empty disposition.

Clarification and deviation references are explicit cross-domain references. OPS-05B does not create competing clarification/compliance records because the current repository has no safe authoritative mutation link for this specialized row.

## Register, filters and atomic bulk work

The package workspace is the primary VDRL compliance register. Each row immediately shows the customer requirement, requested stage/timing, manufacturer, verification status, commitment, exception/clarification, owner and every readiness reason. Inline expandable editors avoid repeated page navigation.

The cross-bid register supports intersection filters for Bid, package, requirement code, stage, verification status, manufacturer, owner, commercial impact and attention. Unresolved rows sort first with stable requirement-code/ID tie-breaking.

Bulk changes support manufacturer, owner and the response-independent `NOT_REVIEWED`/`AWAITING_MANUFACTURER` statuses. Every selected row carries an expected version. Any stale, foreign, duplicate or invalid target rolls back the complete batch and audit event.

## Deterministic readiness

Readiness counts total applicable, not reviewed, awaiting manufacturer, compliant, confirmed with exception, clarification required, cannot comply, unknown commercial impact and individually ready rows.

An applicable required row blocks handover when it is:

- Not reviewed or awaiting manufacturer.
- Clarification required.
- Cannot comply without an approved Bid disposition.
- Confirmed with exception without an approved Bid disposition.
- Missing manufacturer, internal owner or required response evidence.
- Carrying unknown commercial impact with an exception/non-compliance.

Every blocker is explicit. A package is ready only when it contains at least one applicable required row and no blockers. Readiness never uses actual post-award submission state.

## CSV import

Normalized CSV requires `requirement_code`, `title` and `required`. Optional columns preserve stages, anchors/offsets, original timing/date, notes, source reference/revision, applicability and declared required total.

- Preview is read-only and reports row errors.
- Duplicate codes, invalid anchors/dates/booleans and declared-total mismatches fail visibly.
- The 33-declared/37-marked fixture is rejected.
- Confirmation requires the exact previewed SHA-256 and commits all rows plus one audit event atomically.
- No XLSX, PDF/OCR or network dependency is added.

## Handover output

The CSV handover begins with Bid, customer/EPCM, package, manufacturer, source VDRL and readiness summary. Each row includes original customer requirement, requested stages/timing, manufacturer status, response source/date/notes, commitment, evidence, exception, commercial impact, bid disposition, unresolved action, owner and handover note.

This is a report/snapshot for transfer. It does not create or manage an execution project and does not mutate Bid lifecycle state.

## Persistence and audit

Unpublished migration ID: `ops_05_bid_stage_vdrl_v1`.

New additive tables only:

- `vendor_vdrl_templates`
- `vendor_bid_packages`
- `vendor_bid_requirements`
- `vendor_document_schema_migrations`

Existing production tables/columns and historical records are untouched. Initialization is idempotent. Foreign keys link packages to the existing `bids` table. The original customer-requirement trigger strengthens separation from response data.

Accepted package, requirement, import, verification and bulk actions write shared `audit_log` evidence atomically with the mutation. IDs, actors, timestamps, provenance and versions are server-owned. Stale/invalid operations mutate neither record nor audit.

## Removed unpublished execution design

The following OPS-05R-only concepts and browser routes are removed rather than retained as dead surfaces:

- VDDL execution-document records.
- Document-to-milestone execution obligations and actual completion.
- Actual submissions and revision history.
- Customer/EPCM review codes/returns.
- Resubmission and repeated-rejection attention.
- Execution repository/transmittal metadata.
- Post-submission cancellation/discard lifecycle.
- Execution/closeout reports and document detail page.

TASK-08 controlled documents and TASK-12 deliverables remain unchanged. The handover export may reference evidence text, but OPS-05B does not duplicate or mutate those domains.

## Automated and manual acceptance

Tests cover browser Bid creation/opening, Bid-owned package creation, invalid Bid rejection, original-value immutability, all status validation, atomic bulk/CSV behavior, 33/37 discrepancy, filter intersections, readiness explanations, handover content, stale rejection, migration restart/integrity/foreign keys, no execution routes/terminology and no network call. OPS-01–03 and TASK-07–18 regression behavior must remain green.

Manual acceptance uses a fresh isolated database, starts at Home → Bids, creates a Bid/package, imports/enters requirements, records compliant/exception/clarification responses, confirms readiness explanations, verifies customer values remain unchanged and exports the handover CSV. No production database is used.

## Definition of done

Focused/full pytest, OPS/TASK validators and ASGI acceptances, strict scoped mypy, changed-file Ruff/format and `git diff --check` pass; temporary migration restart/integrity/foreign keys pass; production database hash is unchanged; both retained stashes/protected lockfiles remain untouched; nothing is staged, committed or pushed; status is `AWAITING MANUAL BROWSER ACCEPTANCE`.
