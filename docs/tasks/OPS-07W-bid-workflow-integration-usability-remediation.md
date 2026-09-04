# OPS-07W — Bid Workflow Integration and Usability Remediation

## Role problem and confirmed failures

Jason needs to progress one pre-award Bid without learning register topology. OPS-07 proved the underlying records and links, but manual acceptance found unexplained classification, late source prerequisites, conflated customer/response wording, undiscoverable responsibility and bulk controls, read-only entry points, and fragmented return paths.

## Boundary and authoritative domains

This work integrates the existing pre-award domains; it does not replace them. TASK-09 owns customer requirements, TASK-10 owns scope/interfaces, OPS-05B owns manufacturer VDRL verification, TASK-07/OPS-02 owns My Work, TASK-06 alone owns readiness, and the existing TASK-12–18 services own deliverables, commercial, risk, authority, and proposal records. There is no post-award workflow, external integration, AI compliance decision, generic navigator state, or second gate engine.

## Whole-Bid navigator

The Bid overview provides a compact, non-linear eight-stage Setup and Response Navigator: setup/classification; controlled sources; requirements/responses; scope/interfaces; manufacturers/coverage; commercial/risk/approvals; proposal readiness; handover. Each derived stage has Not started, In progress, Complete, or Blocked status, one primary operational action, one detail link, one short completeness statement, and at most the first three next actions. Completeness is explicitly separate from TASK-06 readiness. GET requests never mutate. Counts are resolved in one Bid-scoped aggregate query, independent of unrelated portfolio size.

## Classification policy

The existing deterministic classifier remains authoritative. Value, EPC/EPCM, strategic-customer and configured risk-trigger facts produce a minimum level. Level 1 begins G1 bid/no-bid approval; Level 2 begins G4 margin approval; Level 3 is the floor for $1M+, EPC/EPCM, strategic customers and configured Level-3 triggers; Level 4 is the highest trigger-driven level. No unsupported approval distinction is claimed.

A selected level may equal or exceed the recommendation. An upward selection requires rationale; a downward selection is rejected without mutation or audit. Creation writes the Bid, assessment, triggers and audit atomically. Reassessment is protected by the Bid `updated_at` snapshot, is append-only, and cannot silently lower existing governance. Legacy Bids display “Legacy/manual classification — review classification” without fabricated evidence.

The creation assessment and persistent Bid header provide a business-language guide for every supported level. The guide derives required controls from the same classification-control projection used by the gate presentation, shows the facts and deterministic rationale that set the minimum, distinguishes a saved upward override, explains adjacent-level differences, and tells the user to assess facts before selecting the recommendation or a higher level with a reason. Templates contain no duplicate gate thresholds.

## Controlled-source sequence and requirement semantics

Before explicit entry the form explains that a controlled logical document, exact immutable version and locator are required. Users may choose an existing version or use the no-JavaScript-safe contextual Revision 0 upload in the same retained form. Invalid explicit requirements are never persisted. Controlled evidence remains immutable and Bid-scoped.

`statement` is labelled **Customer Requirement Statement (original wording)** and preserves customer wording with controlled provenance. `response_text` is labelled **Our Proposed Bid Response** and describes the proposed Bid position, qualification, exception, or clarification. They remain separate persisted and audited facts and separate export columns.

The multipart browser boundary is a strict typed adapter. It allowlists TASK-09 requirement fields separately from controlled-source upload fields, the Bid-bound operation token, and retained-form navigation state. Only the requirement partition reaches `RequirementCreate`; only the source partition reaches controlled-source registration. Unknown fields and client-controlled identities remain rejected. The rendered form is organized as Customer requirement, Our Bid response, and Responsibility and source, with examples and business-language significance labels. Successful source registration explicitly confirms that values were retained and that final requirement creation is still required.

## Responsibility semantics

The create/detail/edit surfaces present Accountable owner (closes the requirement), Primary internal contributor (supplies internal input), and Response reviewer (validates the proposed response). Reviewer assignment is not a review outcome. All three support filters, audit, export, optimistic concurrency, and an explicit bulk toolbar. Bulk operations validate every row and version before one transaction; invalid or stale input rolls back all data and audit. Clearing is explicit.

## Scope and interfaces

HTML/PRG adapters use TASK-10 services for typed create, edit, withdraw, requirement–scope link/unlink and scope–interface link/unlink. Contextual creation keeps the Bid and originating requirement/scope visible. All relationships are same-Bid and duplicate constrained; invalid submissions retain HTML with 422, missing records return 404, and successful mutations audit within their transaction. G2 continues to consume only TASK-10 data.

## Manufacturer responsibility and exact evidence

`requirement_manufacturer_links` means a package is responsible for coverage. The additive `requirement_verification_links` junction means an exact OPS-05B VDRL verification row supplies evidence for that requirement. It is FK-backed, indexed, unique, Bid-scoped, package-association constrained, and audited on link/unlink. Many requirements may use a row and a requirement may use many rows. No historical association is guessed or backfilled.

Only explicitly linked rows appear as exact requirement evidence or in the requirements handover. All linked applicable rows must satisfy the shared manufacturer predicate: CONFIRMED_COMPLIANT needs nonblank response source and received date; NOT_APPLICABLE follows existing policy; silence, awaiting, exception, clarification, cannot-comply, missing rows, and incomplete evidence remain unresolved. Package-wide aggregation is not represented as exact evidence. The broader G3 package policy is not weakened.

The UI explains that a Customer Requirement is what the customer requires while a VDRL/VDDL Requirement is the requested supplier document/confirmation. Package association alone displays unresolved guidance.

## Contextual My Work

Requirement, scope, interface and manufacturer package detail can open source-aware Quick Capture. Source identity and Bid ownership are server-derived. Work and its FK-backed domain link are inserted and audited atomically, then return to the authoritative source. Invalid, missing, cross-Bid, duplicate-purpose, or lifecycle-invalid input creates neither row nor audit. Standalone My Work remains unchanged. Lifecycle-wide uniqueness after completed/cancelled work remains deferred.

## Adjacent pre-award authoring

The navigator links to browser-operable adapters over existing services: standard commercial-factor initialization and exception items; contract issue entry followed by its existing source/assessment/review detail; exact gate-consumed approval records distinctly from richer decision cases; proposal-family applicability; and pre-award deliverable entry. Rich decision cases never claim to satisfy G1/G4. No customer submission assurance or post-award tracking is advertised.

## Return-context security and navigation

Return destinations are constrained by known source kinds and server-derived IDs/Bid ownership. Arbitrary URLs, client-selected Bid ownership and cross-Bid destinations are not accepted. Missing/stale origins fall back to authoritative detail or 404. Every workflow provides Back to Bid and contextual return links; normal use requires no constructed URL.

## Migration, concurrency, transactions and audit

OPS-07W creates only `bid_classification_assessments`, normalized assessment-trigger rows, and `requirement_verification_links`, plus indexes and a migration marker. Migration is additive, idempotent, restart-safe and performs no guessed backfill. Initialization and production-shaped restart tests require empty `foreign_key_check` and `integrity_check=ok`.

Mutable records use their existing versions or Bid timestamp snapshots. Server actor/time and Bid ownership are authoritative. Relationship, compound creation, classification, work and audit changes share a transaction or use explicit safe compensation for package creation. Stale, duplicate, invalid and cross-Bid operations produce no partial audit or domain state.

## Errors, empty states and browser behavior

Missing records return 404. Validation and stale browser submissions return understandable retained HTML 422. Successful writes use 303 PRG. GET navigation produces no data/audit changes. Empty stages explain the next action. Primary actions never target JSON or a read-only dead end.

## Reporting and readiness

TASK-06 remains the only verdict. The navigator shows derived setup/response completeness separately. Requirements handover remains deterministic and CSV-safe, includes exact VDRL row code/title and response provenance, and never calls incomplete evidence confirmed. Existing package VDRL handover remains bid-stage only.

## Corrected workflow integration

Gate-consumed approval creation writes its established `audit_log` event in the same transaction; duplicate or failed approval/audit writes leave neither partial record. Navigator status is presentation only: no relevant facts is Not started, partial facts are In progress, exact authoritative completion is Complete, and an applicable existing blocker is Blocked. Manufacturer completion requires every package-associated TASK-09 requirement to have deliberately linked exact evidence satisfying the shared manufacturer predicate; package existence and unrelated VDRL rows never complete the stage.

Contextual scope and interface identifiers are validated against the active Bid and rendered as the selected option. Package VDRL rows batch-display the exact TASK-09 requirement backlinks that rely on them. Inline controlled-source registration uses PRG with a short-lived opaque server-side form-state token: it creates only the controlled document/version, returns to the populated requirement form with that exact version selected, and creates the requirement only on the final save. A separate server-issued, Bid-bound HMAC operation token deterministically assigns the existing document/version/audit primary identities, so sequential or concurrent replay returns the same evidence without another row or audit. Changed tokens, changed payloads and cross-Bid reuse fail without mutation. No idempotency table, business domain or migration is added. The token contains no return URL and is same-Bid constrained.

Workflow measurement counts each visible navigation choice, form submission, or save as one user action. Automatic redirects are HTTP exchanges, not user actions; visiting an authoritative register is a workspace departure, while context loss means the Bid/origin cannot be recovered. The executable representative trace discovers every target in rendered HTML, executes it, and asserts 27 user actions, nine workspace departures, and zero context losses. The deliberate source-operation replay is a robustness probe outside the happy-path action count.

## Automated acceptance

Focused unit, repository, migration, concurrency, audit, HTML-route, query-count and end-to-end tests cover classification floors/overrides; source-first retained entry; separate statement/response; responsibilities/bulk; TASK-10 authoring and links; package responsibility and exact evidence; contextual My Work atomicity; adjacent authoring separation; return security; deterministic completeness/readiness/export; CSV safety; initialization/restart/FK/integrity; and absence of post-award claims. `scripts/validate_ops_07w.py` exercises repositories in isolated storage and `scripts/asgi_acceptance_ops07w.py` drives the in-process ASGI application without a socket.

## Manual acceptance

1. Start the documented isolated runtime and create the Example EPCM Level 3 Bid.
2. Confirm classification rationale before creation and the persistent Bid header/navigator afterward.
3. Upload Customer Electrical Specification Revision 0 from requirement entry and create the explicit arc-flash requirement with separate proposed response and all three responsibilities.
4. Use the visible bulk toolbar and confirm retained stale/invalid feedback.
5. Create/link scope, create/link an interface, and navigate both directions.
6. Create/associate the LV MCC package; confirm association alone remains unresolved.
7. Record the manufacturer compliance-letter VDRL response, link that exact row, and confirm unrelated rows do not appear.
8. Create linked My Work from each supported source and return to it.
9. Initialize commercial factors, add risk, record gate approval separately from a decision case, and create proposal/deliverable records.
10. Review TASK-06 readiness, export handover, and return to Bid. Confirm no JSON/manual URL/dead end or unexplained prerequisite.

## Explicit exclusions

No OPS-08, global redesign, generic skipping, persisted navigator state, new readiness policy, automatic bulk work creation, lifecycle-wide My Work uniqueness migration, post-award submission/commissioning/closeout, external system integration, or invented approval policy.

## Rollback and recovery

No repository history is rewritten. Restore the retained scoped stash recorded in HANDOFF to recover the accepted OPS-07 candidate; the baseline recovery ref restores pre-OPS-07 HEAD. Production data is never initialized or migrated during development verification.
