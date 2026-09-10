# OPS-09 — Proposal Readiness, Exchange and Bid Issue Control

## Business purpose and boundary

ContractIQ is the authoritative pre-award system for Bid facts, deterministic proposal readiness,
approval evidence, manual customer-issue control, and the immutable record of exactly what was
issued. Jason's role ends at the pre-award Bid/handover boundary. Proposal Studio owns templates,
reusable content, editing, layout, DOCX generation, and PDF generation. OPS-09 exchanges versioned
local files only. It adds no network, email, InEight, template, generation, or post-award execution
capability.

The source was a Jason-authorized, hash-verified recovery bundle rather than a Git checkout. The
original Proposal Studio repository and source commit are unavailable. ContractIQ therefore claims
byte provenance and Jason's authorization, not upstream commit provenance. Vendored files and the
non-executable reference evidence live at `contracts/proposal-studio/v1/`.

Raw hashes:

- package schema: `3416365b51abedf3dcc7f17996ecb08467997b660679ae890c5624761d472462`
- manifest schema: `da5c24e75371498109ba28d81d8840753a30c28bbfb9554fc2a5f288649ef408`
- package example: `8943882932419d3c8ffc03e1b1552433d5c6b807d5fa69dea039275ac245c3fc`
- manifest example: `ef30b682e058039b8eab9d8e98c564785b657bc5dc1b518635300e8868304c80`
- contract document: `864e578a33e8fd17c6f753bb92d6333c9d83ae82728d10746221d74dea27ea30`
- incomplete reference test: `5e16ba335763de0cd03a76546467a597310aaa3180d217ec4568053257bdbec0`
- canonical example package: `f087af73dc3306d0ca174ffaa6df464f5c6102a86929926a038797e9b71b310b`

## Reuse inventory and projection decisions

| Exchange fact | Existing authority | Projection | Missing fact | Decision |
|---|---|---|---|---|
| Bid identity/revision | `bids` | ID, project, deterministic updated-at revision and source hash | none | reuse |
| Customer/opportunity | `bids` | customer/project/location/due date | stable customer/opportunity IDs and RFP reference | emit nullable V1 values; do not invent |
| Controlled sources | `documents`, `document_versions` | exact current version, media type and raw hash | transport layout | reference only; no file bundling |
| Requirements/responses | `requirements` | active rows and normalized disposition | none | reuse current authoritative version |
| Scope/interface | `scope_interface_items`, `scope_interfaces` | offer position and dependency boundary | none | reuse; unresolved facts block |
| Manufacturer package/commitment | `vendor_bid_packages`, `vendor_bid_requirements` | package and verified response | separate equipment rows | package may have empty equipment list |
| VDRL | `vendor_bid_requirements` | frozen V1 wording/status only | detailed schedule cycles | visible V1 limitation |
| Commercial positions | current `commercial_position_versions` | customer/proposed wording and normalized status | none | reuse; never substitute legacy generic fields |
| Qualifications/deviations | qualifying requirement dispositions | proposal-facing projection linked to requirement | standalone qualification register | do not fabricate a new authority |
| Contract risk | `contract_issues` and latest assessment/review | proposal-issue subset/status | none | material unapproved risks block |
| Decisions/approvals | `approvals`, `decision_cases`, `approval_routes` | existing IDs, authority/policy references and states | universal person identity | preserve existing vocabulary |
| Pricing | selected `scenario_baselines` and version | scenario reference/hash only | customer-facing price breakdown | explicitly deferred by V1 |
| Delivery | pre-award/customer-facing `deliverable_items` | wording, date and state | none | missing commitment blocks |
| Proposal inputs | explicit domain fields above | empty bounded list | no distinct authoritative scalar inputs | do not fabricate |
| Supporting documents | controlled document/version | identity, revision, media type and raw hash | package transport path | `relative_path: null` |
| Readiness/gates | `evaluate_readiness` plus existing domain services | composed blocker list with correction links | exchange evidence | OPS-09 adds only exchange/issue checks |
| Audit/concurrency | `audit_log`, SQLite transactional patterns | atomic audit and candidate version tokens | none | reuse |
| Proposal/negotiation workspace | Bid workspace, proposal family, negotiation domains | issue-control entry in same Bid context | exchange history | additive UI only |
| Handover/reports | `BidHandoverService`, formula-safe CSV | issued baseline/history/change evidence | none | include; generation alone is never issue |

## Workflow and readiness

The ordinary browser path is Bid workspace → Proposal & Negotiation → Proposal readiness and issue
control. It shows one status, exact blockers/correction destinations, latest export, manifest/artifact
verification, approval state, latest customer issue, progressive-disclosure history, and one primary
next action. Successful POSTs use PRG. Browser validation failures return retained HTML with 422;
missing/cross-Bid records return 404 and stale tokens return 409.

The assessment composes the existing deterministic gate/readiness service; it is not a second gate
engine and never calls an LLM. Additional issue-control checks cover controlled sources, requirement
responses, scope/interfaces, manufacturer/VDRL evidence and exceptions, commercial positions,
qualification disposition, material contract-risk review, accountability, selected pricing reference,
delivery commitment, current source hashes, manifest validity, artifact hashes, approvals, and
candidate supersession. Record population never implies completion.

States are `NOT_STARTED`, `IN_PROGRESS`, `READY_FOR_EXPORT`, `EXPORTED`,
`GENERATED_ARTIFACTS_RECEIVED`, `STALE`, `AWAITING_APPROVAL`, `APPROVED_FOR_ISSUE`, `ISSUED`,
`SUPERSEDED`, and `BLOCKED`. Each blocker names its authoritative area, record where applicable,
business explanation, and correction URL.

## Canonical export, import and staleness rules

Contract V1 uses Draft 2020-12, strict object properties, required arrays (`[]`, never `null`), nullable
singular values, UTC `Z` timestamps, bounded identifiers, safe relative paths, deterministic array
ordering, and lowercase SHA-256. Parsing rejects BOM, malformed UTF-8, duplicate object keys, and
non-finite numbers before schema validation. Canonical JSON sorts object keys, preserves verified V1
array order, uses UTF-8/non-ASCII text, compact separators and no non-finite numbers. Package SHA-256
is over canonical bytes; artifact hashes are over raw bytes.

An export is bound to one Bid and one source projection. Area hashes cover only facts actually in the
package. An unchanged projection replays the latest immutable export without another audit row; a
changed projection receives a new server ID and `supersedes_export_id`. Every historical export stays
downloadable.

Manifest import validates all JSON, schema identity/version, additional fields, ordering,
section-disjointness, package identity/hash, successful generation status, exact artifact set,
basename/normalization safety, media type implied by artifact role, and raw hashes before mutation.
Absolute paths, traversal, separators, duplicate normalized destinations, missing/extra files,
cross-Bid packages, stale exports and hash mismatches fail. A failure writes no database record, file,
audit, or readiness state. A success atomically stores immutable manifest/artifact evidence, a mutable
versioned candidate, and audit evidence.

Staleness compares current per-area canonical hashes with the export snapshot, never merely
`updated_at`. The UI names changed areas and requires a successor export/candidate. An issued baseline
remains historical truth; a later customer issue requires a current successor candidate.

## Approval, customer issue and immutable baseline

Approval rechecks current source hashes, composed readiness, artifact bytes, optimistic version, and
existing required approval/route states. The candidate approval transition creates links to the
existing approvals; it does not invent approval identity. The issue form records a manual external
event and performs no transmission. It captures revision, issue time/method, recipient or destination
reference, offered validity, note, artifact identities/hashes, export/package, manifest, approvals,
actor and server timestamp.

Issue runs in one immediate transaction and rechecks candidate version/state, readiness, approval,
staleness, and artifact bytes. It creates an append-only event, immutable full baseline, immutable
artifact/approval snapshots, candidate transition, and audit. Any injected persistence/audit failure
rolls back all database writes. Database triggers prohibit update/delete of exports, receipts,
artifacts, issue events, baselines, and issued snapshot junctions. A later revision points to the prior
candidate/baseline rather than overwriting it.

## Additive migration, audit, concurrency and security

`ops_09_proposal_exchange_issue_control_v1` creates only OPS-09 tables, indexes, foreign keys and
immutability triggers using idempotent `IF NOT EXISTS` statements. There is no backfill and no
destructive migration. Server UUIDs, configured actor and UTC timestamps are authoritative. Candidate
mutations use `WHERE version=?` optimistic concurrency. Every successful state-changing transaction
includes audit evidence; GETs are read-only.

All exchange values remain inert data. No URL, command, macro, executable media type, template action,
network call or automatic file opening is supported. Artifact basenames are validated before joining
to the configured storage root. Storage paths are server-controlled. JSON size and upload size are
bounded at the route. The default local artifact root is data-scoped and is not created at startup.

## UI and handover/report design

The issue-control page preserves the standard Bid header and direct return to Proposal & Negotiation,
uses shared styling and business labels, and avoids routine raw JSON. Advanced immutable history is a
`details` region. The Bid handover HTML/CSV receives issue revision/date, artifact identities/hashes,
package ID/hash, manifest identity/hash, approval references, validity, superseded history, detected
post-issue changes and remaining actions. `Issued` derives only from an immutable customer-issue
baseline—not generation or approval.

## Executable tests and acceptance

Independent ContractIQ tests cover the frozen valid schemas/examples and canonical hash plus duplicate
keys, ordering, unknown fields, absolute/traversal/ambiguous paths, manifest/package mismatch,
artifact mismatch, export replay, changed-source staleness, valid import, cross-Bid protection, stale
tokens, audit rollback, approval enforcement, manual issue, immutable triggers, successor history,
handover/report integration, GET non-mutation, retained HTML errors and Bid-context navigation. The
socketless ASGI workflow measures actual actions, context departures/loss, duplicate entry,
prerequisite surprises, dead ends and raw-JSON/manual-URL requirements.

The bundle's incomplete reference test is not executed or represented as passing. Its missing original
fixtures are not reconstructed; independently authored ContractIQ negatives cover equivalent cases.

## Deferred V1 limitations

- Customer-facing totals, currency presentation, alternates, taxes and line-item breakdown remain out
  of V1; only the selected scenario reference crosses the boundary.
- Supporting-document transport/package layout remains undefined; references/hashes only.
- VDRL schedule granularity remains limited to frozen V1 wording/status.
- Approval identity remains ContractIQ's existing opaque authority/policy vocabulary.
- Proposal Studio source commit provenance remains unavailable.
- Network/email/InEight integration, generation, template editing and post-award execution are out of
  scope.
