# OPS-07 — Requirements, Scope, and Manufacturer Coverage

## Role problem and product boundary

The Bids and Contracts Manager needs one Bid-context workflow to trace what the customer required, the controlled source, the proposed disposition, accountable owner, internal contributor, reviewer, scope/interface coverage, manufacturer confirmation, unresolved action, and accepted handover. This is pre-award control through accepted Bid-to-execution handover only. It does not manage execution, submissions, construction, commissioning, closeout, external integrations, or customer resubmission cycles.

## Authoritative domains and semantics

TASK-09 remains the requirement register; TASK-10 remains scope/interfaces; OPS-05B remains bid-stage manufacturer/VDRL verification; TASK-07/OPS-02 remains My Work; TASK-06 remains the only gate engine. Owner is accountable for closure. Contributor is the single primary internal input provider and is nullable; NULL means not assigned. Reviewer validates the response. Manufacturer is external and is associated only through a package junction, never stored as contributor.

## Schema and migration

The restart-safe additive migration adds nullable `requirements.contributor`, with no historical backfill, plus `requirement_manufacturer_links` and constrained FK-backed `domain_work_item_links`. Existing requirement↔scope and interface↔scope junctions are reused. Foreign keys, uniqueness, exactly-one-source checks, immutable domain records, and existing lifecycle constraints remain in force. Reopening the repositories is idempotent; migration verification uses only temporary databases.

## Relationships and My Work

Requirement↔scope and requirement↔manufacturer links validate both records and same-Bid ownership before an atomic insert. The package link identifies responsibility for coverage. OPS-07W adds a separate, deliberate requirement↔exact VDRL verification-row junction identifying the response/evidence that supports that customer requirement. It is same-Bid, FK-backed, unique and audited; no historical package link is guessed or backfilled. Duplicate links are rejected. Unlinking deletes only the junction and writes audit; it never deletes an authoritative record. Interfaces are derived through authoritative scope links. Domain↔work links identify exactly one requirement, scope item, interface, or package and a purpose; the source remains authoritative while My Work carries action status and follow-up. Standalone work is unchanged and work creation remains deliberate.

## Readiness behavior

G2 adapts active TASK-10 scope and interface facts, not legacy extracted scope, and treats incomplete scope or open interfaces as unresolved. G3 reads OPS-05B package responses. A link alone, no response, awaiting response, exception, clarification, cannot-comply, or a nominal confirmation without response source/date is unresolved. Only confirmed compliant (with actual response evidence) or not applicable is clear. Classification scaling and TASK-06 materiality/override policy are unchanged; no LLM participates.

## Browser workflows

The Bid Requirements & Scope section lists authoritative requirements, links to typed creation/detail, provides explicit row selection and bulk responsibility assignment, and exports handover. Requirement detail separates original text from response, edits typed metadata/workflow, links/unlinks scope and Bid packages, derives interfaces, links back to Manufacturers & Coverage, and returns to the Bid/register. Empty states explain the next action. Routine workflows use forms or typed controls, never raw JSON or hand-entered URLs.

OPS-07W source registration returns to the retained requirement form and selects the new immutable version before the final requirement save. Its server-issued Bid-bound operation identity makes identical sequential/concurrent replay return the same controlled document/version/audit result; no requirement is created before final save. Contextual requirement/scope identifiers are same-Bid validated and visibly preselected. Exact manufacturer evidence is navigable in both directions: requirement detail links the verification row, and the package/VDRL row lists the TASK-09 requirements that deliberately rely on it.

The browser command explicitly partitions source-upload and retained-state fields from strict `RequirementCreate` input; unknown multipart fields are rejected rather than ignored. Requirement entry and detail visibly separate the customer's original wording from our proposed Bid response, group responsibilities with exact source evidence, and provide contextual coverage/My Work next actions. Bid creation and the persistent workspace header explain the deterministic governance recommendation, triggers, required existing controls, adjacent-level differences and any audited upward override.

## Bulk atomicity, concurrency, and audit

Bulk assignment accepts independent owner/contributor/reviewer values. Blank omitted values preserve data; explicit Clear controls erase a role. Every selected ID, same-Bid membership, active lifecycle, and expected version is validated before mutation under one immediate transaction. One missing, foreign, duplicate, invalid, or stale row rolls back all changes and audit entries. Every changed requirement increments its version and receives an audit event. Contributor create/edit and individual metadata changes use existing optimistic concurrency and audit conventions.

## Reporting and handover

The deterministic Bid CSV includes Bid identity, requirement/source, disposition/response, all three roles, linked scope/interfaces/packages, deliberately linked exact verification status and response evidence, commercial impact, unresolved actions, My Work references, and a conservative readiness state. Unrelated VDRL rows in an associated package are excluded. Missing exact confirmation is emitted as `UNANSWERED`/`UNRESOLVED`, never compliant.

## Empty, invalid, stale, and missing behavior

Empty registers guide creation. Invalid browser submissions return understandable 422 feedback and bulk selections/entries are retained. Stale writes return conflict feedback with no partial mutation. Missing authoritative records and exports return 404. GET routes are read-only and navigation creates no audit.

## Explicit exclusions

No parallel domain, second readiness engine, AI compliance conclusion, multi-contributor model, post-award workflow, global redesign, external-system integration, automated mass work creation, or unrelated persisted-domain change is included.

## Acceptance criteria

Contributor is nullable, editable, filterable, audited, concurrency-protected, bulk assignable, and exported. Scope/package links are same-Bid, unique, auditable, and removable. Silence remains unresolved. G2/G3 use authoritative records. Bid context and back navigation persist. Handover is stable and complete. Migration initialization/restart/FK/integrity pass. Existing My Work and OPS-05B bid-stage behavior remain intact.

## Manual browser acceptance

1. Start the application against a fresh temporary database and document root.
2. Create/open a Bid and select Requirements & Scope.
3. Create an internal requirement with owner and contributor; confirm original text and response are separate.
4. Edit contributor, response, disposition, and reviewer through typed controls.
5. Select two requirements and bulk assign each responsibility; repeat with a stale page and confirm no partial update.
6. Link/unlink scope and confirm related requirements/interface navigation retains the Bid.
7. Associate a Bid manufacturer package and confirm association alone is shown unresolved.
8. Record an OPS-05B response and verify G3 reflects the actual status/evidence.
9. Export handover twice and confirm identical bytes and conservative unanswered status.
10. Confirm Back to Bid, 404 behavior, empty guidance, and that no post-award execution workflow is advertised.

## Recovery procedure

Do not modify the production database. Discard this unstaged candidate and restore files from `backup/ops-07-pre-implementation-20260828` at `901c99787ef0d29f844f13565bf53da4d0c8031f`; preserve protected `uv.lock`, stashes, backups, settings, and secrets. Recreate a fresh isolated database after recovery rather than reversing the additive migration in place.
