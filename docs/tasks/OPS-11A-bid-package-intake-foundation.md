# OPS-11A — Bid Package Intake and Addendum Control Foundation

**Status:** FOUNDATION ACCEPTED — OPS-11B implementation authorized on 2026-09-11

**Accepted baseline:** `5b92c2764d01000f5aae55e3dc6002271d1e0bad`

**Proposed implementation migration:** `ops_11_bid_package_intake_addendum_control_v1`

OPS-11A authorizes investigation and this specification only. It does not authorize production
code, migrations, schemas, routes, templates, tests, dependencies, database creation, or use of the
confidential pilot package. OPS-10 Award/Offer Reconciliation remains dormant.

### 2026-09-11 authorization record

Jason authorized `ops_11_bid_package_intake_addendum_control_v1` for OPS-11B with a reduced first
migration of exactly 18 tables. The five checklist tables proposed below are explicitly deferred
until after the manual real-package pilot. Work AI exchange/import, staged AI findings, extraction,
OCR, page/drawing analysis, archive expansion and all new processing dependencies are also deferred.

The authorized first implementation uses `COPY_MANAGED_ONLY`, separate allowlisted landing and
managed-intake roots, immutable physical-file evidence, append-only file dispositions, explicit
release ordering facts without parsing customer strings, approval-backed waiver semantics,
acknowledgement-based OPS-09 issue blocking, exact TASK-08 version incorporation, OPS-09 supporting
document hash staleness, and FK-backed My Work relationships. These decisions supersede any
candidate wording or open question later in this inventory.

## 1. Purpose and role alignment

OPS-11 defines the pre-award control boundary from receipt of a customer's Bid package through
addendum incorporation, a controlled Bid Basis, proposal-issue support, and accepted-Bid handover
evidence. It lets the Bids and Contracts Manager answer, from one Bid workspace:

- what the customer says has been released;
- what ContractIQ actually received and preserved;
- what has been inventoried and analyzed;
- which changes are partially or fully incorporated;
- which exact documents govern the Bid now; and
- whether the OPS-09 proposal baseline is stale against that basis.

This is evidence and control work, not project execution. Deterministic rules own gaps,
incorporation eligibility, staleness, and readiness. AI may propose classifications and findings but
must never pass a gate, declare incorporation complete, or create an authoritative business fact
without human review.

## 2. Scope boundary

### In scope for a future authorized implementation

- Three connected, Bid-owned ledgers: Release Notice, Received Package, and Bid Basis.
- One-time controlled snapshot from an allowlisted temporary landing directory into immutable
  managed storage.
- Initial packages, addenda, clarifications, revised packages, and other customer releases.
- Immutable registered releases, original filenames and relative paths, hashes, duplicate evidence,
  processing history, acknowledgements, and discrepancy warnings.
- Explicit addendum directives and dispositions, including prominently visible partial incorporation.
- Versioned immutable Bid Basis snapshots and a customer-facing Bid Basis Register.
- A future boundary for three distinct checklist libraries; checklist persistence is not in the
  authorized first migration.
- Relationship-based promotion of accepted findings into existing authoritative domains.
- Future-compatible stateless Work AI and local Alice exchange boundaries.
- Composition with existing readiness, My Work, My Day, OPS-09 issue control, audit, and handover.

### Out of scope

- OPS-10 Award/Offer Reconciliation or any modification to dormant OPS-10.
- Post-award project execution, manufacturer execution tracking, customer submission cycles, or
  another controlled-document domain.
- Customer portal, email, InEight, or other network synchronization; watched folders; automatic
  customer communications; and automatic acknowledgements.
- Direct Work AI API access, persistent Work AI knowledge, automatic AI approval, or AI gate logic.
- Replacements for requirements, scope, commercial, risk, manufacturer/VDRL, decision, approval,
  proposal, My Work, controlled-document, readiness, handover, or audit authorities.
- Content extraction, OCR, drawing extraction, page classification, dependency installation, and
  real-package processing in OPS-11A.

## 3. Repository inventory and semantic reuse

The inventory used direct source inspection of `core/`, `app.py`, existing OPS/TASK specifications,
tests, validators, and the frozen Proposal Studio V1 contract. It did not initialize the application
or a database.

| Existing authority | Current semantics and reuse decision | OPS-11 classification |
|---|---|---|
| `bids`; `BidRepository`; `Bid` | Own Bid ID, customer/type, project, dates, owners, value, classification, gate, status, risk triggers and inference policy. Package records must FK to this identity. | REUSE_AS_IS |
| OPS-06 Bid workspace/control centre | Owns the Bid-context shell, persistent header, readiness rail and navigation. Add Package Intake to the same workspace; do not create a second workspace. | REUSE_WITH_RELATIONSHIP |
| OPS-07W classification assessments | Own explicit classification evidence and deterministic policy explanation. Checklist applicability may read classification but never alter it. | REUSE_AS_IS |
| TASK-08/TASK-08R `documents`, `document_versions` | Own logical controlled documents, immutable versions, current/superseded lineage, hash, source metadata, provenance, integrity and withdrawal. They do not own release receipt, folder hierarchy, archives, multi-document files, or Bid Basis membership. | REUSE_WITH_RELATIONSHIP |
| `ManagedDocumentStorage` | Provides same-filesystem staging, streaming SHA-256, size bound, opaque keys, exclusive placement, post-place verification, safe resolution, symlink diagnostics and compensation. Release-level publication needs an adapter. | REUSE_WITH_ADAPTER |
| `config.json` / `CONTRACTIQ_DOCUMENT_ROOT` | Configure one managed root and 50 MiB per-file default. A separate allowlisted intake-root setting and release-level limits are absent. | REUSE_WITH_ADAPTER |
| `app.py` initialization/startup | Import constructs `Database`, repository migrations and managed services and creates legacy upload/report directories; startup recovers legacy analysis states and bootstraps knowledge. OPS-11 startup must never scan an intake root or parse files, and its bounded storage reconciliation must not print names. | REUSE_WITH_ADAPTER |
| `Provenance` | Owns creator type, agent/model, source document/location, timestamp and human confirmation. It correctly defaults `human_confirmed=False`. | REUSE_AS_IS |
| TASK-09 `requirements` | Owns original statement, controlled source/version and locator, response, disposition, responsibilities, review and lifecycle. Accepted intake findings promote through `RequirementService`. | REUSE_WITH_RELATIONSHIP |
| TASK-10 scope/interfaces and junctions | Own offer scope, exclusions/assumptions, responsibilities, interfaces and requirement relationships. | REUSE_WITH_RELATIONSHIP |
| TASK-11 supplier assurance | Owns suppliers, requests/items, immutable response versions, coverage and flow-down evidence. | REUSE_WITH_RELATIONSHIP |
| TASK-12 / OPS-05B VDRL | `deliverable_items` owns deliverable obligations; vendor packages/requirements own pre-award manufacturer/VDRL verification. `vendor_vdrl_templates` is purpose-specific, not an intake checklist. | REUSE_WITH_RELATIONSHIP |
| TASK-13 and OPS-08 commercial | Own commercial factors, customer/proposed positions, assessments, reviews, qualifications and related work. | REUSE_WITH_RELATIONSHIP |
| TASK-14 contract risk | Owns issues, exact sources, relationships, immutable assessments and reviews. | REUSE_WITH_RELATIONSHIP |
| TASK-15 decisions/approvals and legacy `approvals` | Own decision cases/packages, authority policy/routes/events and existing gate/issue approval evidence. Waivers must reference this authority. | REUSE_WITH_RELATIONSHIP |
| TASK-16 scenarios | Own immutable scenario versions/results/reviews and selected baselines. | REUSE_AS_IS |
| TASK-18 proposals | Own proposal families, immutable versions, reviews, artifacts and selected proposal baselines. This is distinct from Bid Basis. | REUSE_WITH_RELATIONSHIP |
| TASK-18 `BidHandoverService` | Produces a read-only whole-Bid handover projection. Add basis/addendum evidence as another projection, not a second handover. | REUSE_WITH_ADAPTER |
| My Work (`work_items`) and contextual links | Owns actions, waiting/blocking facts, priority, dates, lifecycle, version and provenance. `ops_work_context_links` allows only broad account/project/opportunity/initiative/case kinds; `domain_work_item_links` allows requirement/scope/interface/vendor-package targets. Neither can safely identify an intake notice/release/directive. | REUSE_WITH_RELATIONSHIP |
| My Day | Read-only deterministic attention. It should receive intake warnings through an adapter, never own intake records. | REUSE_WITH_ADAPTER |
| Gate/readiness modules | The sole deterministic stage-gate/readiness engine. OPS-11 supplies conditions through an adapter; it adds no LLM and no second verdict engine. | REUSE_WITH_ADAPTER |
| `audit_log` and repository transactions | Shared append-only audit store; repositories use `BEGIN IMMEDIATE`, rollback, same-transaction audit and UTC server timestamps. | REUSE_AS_IS |
| Optimistic concurrency | Existing domains use integer `version`, opaque `version_token`, expected current IDs and conditional updates. | REUSE_AS_IS |
| Existing idempotency | Controlled-source registration derives document/version/audit IDs from a Bid-bound operation UUID; OPS-09 replays an unchanged export by canonical projection hash; standard initializers use uniqueness/`INSERT OR IGNORE`. Intake should combine operation identity with an exact release fingerprint. | REUSE_WITH_ADAPTER |
| Migration patterns | Additive DDL, marker tables, indexes, FKs, checks, immutability triggers, restart/FK/integrity tests. There is no central migration runner. | REUSE_WITH_ADAPTER |
| OPS-09 Proposal Exchange/Issue Control | Owns immutable exports, manifest receipts, artifacts, candidates, approval links, customer issue events, issued offer baselines and area-hash staleness. | REUSE_WITH_ADAPTER |
| Proposal Studio Contract V1 | Frozen local-file strict/canonical JSON, bounded collections, raw hashes and no DB/network coupling. Parser and import-security patterns apply, but its schema is proposal-specific. | REUSE_WITH_ADAPTER |
| Legacy Knowledge Excel import/export | Table-specific, permissive and partially committing; unsuitable for authoritative intake/import. | DEFERRED |
| `export_controls.csv_safe_*` | Prevents spreadsheet-formula execution in CSV exports. | REUSE_AS_IS |
| Controlled-document download | Opaque safe storage resolution exists, but the route is keyed only by version ID and the app has no identity layer. Intake download must be Bid-scoped and ownership-checked. | REUSE_WITH_ADAPTER |

### Inventory conclusion

Existing authorities own accepted requirements, scope, manufacturer/VDRL, commercial, risk,
decision/approval, scenario, proposal, work, readiness, handover and audit facts. The repository does
not own customer release notices, received release identity, physical file receipt/original hierarchy,
addendum directive/disposition history, acknowledgement history, or immutable Bid Basis snapshots.
Those facts cannot safely be represented by renaming existing fields.

## 4. Required-fact classification

| Required OPS-11 fact | Authority/derivation | Classification |
|---|---|---|
| Bid identity, customer, classification, status and workspace | `bids` and OPS-06 | REUSE_AS_IS |
| Customer says a release exists | Release Notice Ledger | NEW_AUTHORITATIVE_FACT_REQUIRED |
| Notice channel/time/evidence and portal/email observation | Notice/check history | NEW_AUTHORITATIVE_FACT_REQUIRED |
| Notice exists but files are missing | Notice-to-release comparison | DERIVED_ONLY |
| Release identity/type/sequence/time/handling mode | Received Package Ledger | NEW_AUTHORITATIVE_FACT_REQUIRED |
| Stable physical file identity, original name/path and immutable bytes | Received Package Ledger/storage | NEW_AUTHORITATIVE_FACT_REQUIRED |
| Logical document identity/version | TASK-08 | REUSE_WITH_RELATIONSHIP |
| File-to-logical-version mapping | New junction to TASK-08 | NEW_AUTHORITATIVE_FACT_REQUIRED |
| Byte duplicate | Exact SHA-256 comparison | DERIVED_ONLY |
| Semantic revision | Human-confirmed TASK-08 relationship | REUSE_WITH_RELATIONSHIP |
| Content form | Received-file/page classification | NEW_AUTHORITATIVE_FACT_REQUIRED |
| Business purpose/category | TASK-08/destination domain | REUSE_AS_IS |
| Archive retained/excluded from repeated analysis | File plus processing disposition | NEW_AUTHORITATIVE_FACT_REQUIRED |
| Unsupported/encrypted/unreadable/extraction failure | Processing result | NEW_AUTHORITATIVE_FACT_REQUIRED |
| Drawing/page metadata | Future evidence boundary | DEFERRED |
| Addendum directive/materiality/disposition | Addendum control | NEW_AUTHORITATIVE_FACT_REQUIRED |
| Addendum gaps and discrepancies | Deterministic comparisons | DERIVED_ONLY |
| Acknowledgement evidence | Append-only event | NEW_AUTHORITATIVE_FACT_REQUIRED |
| Governing basis snapshot/membership | Bid Basis Ledger | NEW_AUTHORITATIVE_FACT_REQUIRED |
| Latest known/received/inventoried/analyzed/partial/full | Ledger/run projection | DERIVED_ONLY |
| Working view | Controlled snapshot plus labeled partial overlay | DERIVED_ONLY |
| Controlled baseline | Immutable Bid Basis snapshot | NEW_AUTHORITATIVE_FACT_REQUIRED |
| OPS-09 staleness | Existing area-hash engine fed by Bid Basis | REUSE_WITH_ADAPTER |
| Incoming completeness checklist status | New versioned library/instance | NEW_AUTHORITATIVE_FACT_REQUIRED |
| Response deliverables/internal controls | Existing domains plus checklist relationship | REUSE_WITH_RELATIONSHIP |
| Work action/attention | My Work/My Day plus intake relationship | REUSE_WITH_RELATIONSHIP |
| AI proposed findings | Non-authoritative staging | NEW_AUTHORITATIVE_FACT_REQUIRED |
| Accepted business finding | Existing destination service | REUSE_WITH_ADAPTER |
| Work AI/Alice exchange package/receipt | Future stateless exchange | DEFERRED |
| Gate/readiness verdict | Existing deterministic services | REUSE_AS_IS |
| Audit/provenance | Existing shared controls | REUSE_AS_IS |

## 5. Release Notice Ledger

A notice records what the customer says exists independently of file receipt. Channels are `EMAIL`,
`PORTAL`, `LETTER`, `TELEPHONE`, `MEETING`, `ADDENDUM_DOCUMENT`, and `OTHER`. Email-only notice is
valid. A notice may name an initial package, addendum, clarification, revised package, or other release
and may contain zero declared files.

Notice evidence is append-only. Corrections create a new notice with `supersedes_notice_id`. A
controlled email/letter/addendum version may be linked; telephone/meeting notices require a human
evidence note. A notice never fabricates receipt. Match to a received release is explicit and same-Bid.

Portal/email checks are separate append-only observations because a check can report “no change.”
Latest observations produce a discrepancy warning when normalized release references or sequence
claims disagree. Version 1 never logs in to a portal or reads email automatically.

## 6. Received Package Ledger

A received release is a one-time immutable registration of exactly what ContractIQ received. Types
are `INITIAL_PACKAGE`, `ADDENDUM`, `CLARIFICATION`, `REVISED_PACKAGE`, and `OTHER`. Each has one
handling mode: `WORK_AI_ORIGINALS_ALLOWED`, `WORK_AI_SANITIZED_ONLY`, `LOCAL_AI_ONLY`, or
`INVENTORY_ONLY`.

Registration retains every outer file, including archives, unsupported formats and duplicates.
Analysis eligibility is a processing disposition, never deletion. A duplicate archive remains
immutable evidence and is excluded from repeated analysis only by an explicit audited disposition.

## 7. Bid Basis Ledger

The Bid Basis is not “the newest folder.” It is a sequence of immutable snapshots containing exact
TASK-08 document-version IDs and the releases/notices incorporated. Each points to its predecessor;
history remains unchanged.

- **Latest known:** newest customer release asserted by notice/manual check.
- **Latest received:** newest registered release by customer ordering then received time.
- **Latest inventoried:** newest release with successful complete physical/content-form inventory.
- **Latest analyzed:** newest release whose eligible files have terminal success or reviewed exclusion;
  extraction failure prevents “complete.”
- **Latest partially incorporated:** newest release with incorporated and unresolved/partial relevant
  directives.
- **Latest fully incorporated:** newest release satisfying all full-incorporation rules.
- **Working view:** latest controlled snapshot plus explicit non-authoritative partial overlays.
- **Controlled baseline:** latest immutable eligible Bid Basis snapshot.

These labels must never collapse into one “latest” field.

## 8. Release and addendum lifecycle

Lifecycle labels are derived from append-only evidence and versioned runs:

```text
NOTICE_RECORDED -> RELEASE_OUTSTANDING -> RECEIPT_PREVIEWED (no mutation)
-> COPYING_AND_HASHING -> VERIFIED_FOR_PUBLICATION -> REGISTERED
-> INVENTORY_COMPLETE | INVENTORY_PARTIAL
-> ANALYSIS_COMPLETE | ANALYSIS_PARTIAL | ANALYSIS_EXCLUDED
-> INCORPORATION_NOT_STARTED -> INCORPORATION_PARTIAL
-> INCORPORATION_COMPLETE -> BID_BASIS_SNAPSHOT_CREATED
```

Failures create terminal attempt evidence without deleting a registered release. Retry creates a new
run against the same originals. Revised packages are not silently treated as addenda.

## 9. Partial and full incorporation

Partial incorporation is allowed and prominently shown, but creates no controlled snapshot. The
working view labels each overlay, source release, directive and affected domain record.

Full incorporation requires deterministic proof that:

1. release registration and original-file integrity are clear;
2. inventory is complete and each eligible file has an acceptable terminal processing result;
3. every material directive is human-confirmed and finally disposed;
4. incorporated directives link to resulting existing authoritative records/versions or an explicit
   customer-evidenced `NOT_APPLICABLE` decision;
5. no material directive is `UNASSESSED`, `PARTIAL`, `BLOCKED`, or `UNCERTAIN`;
6. every governing document is an exact TASK-08 version with integrity `OK`;
7. prior documents carry forward unless an explicit confirmed withdrawal/replacement removes them;
8. same-Bid, uniqueness, lineage and optimistic-concurrency checks pass.

Conservative default: an internal waiver does not mean the customer's change was fully incorporated.
Jason must authorize any alternative. Jason must also decide whether an outstanding required
acknowledgement blocks incorporation, OPS-09 issue, or only raises attention.

## 10. Addendum gaps and discrepancies

Deterministic, non-LLM checks report:

- notice with no matched release; customer sequence gaps/conflicts;
- portal/email latest-release disagreement;
- notice-declared item absent from receipt or received item not declared;
- same stated document/revision with different bytes;
- byte-identical files at other paths/in archives;
- revision regression/unlinked predecessor;
- directive target absent from prior basis;
- material directive without disposition/promotion evidence;
- delayed/partial inventory or analysis;
- required acknowledgement not recorded; and
- OPS-09 source area no longer matching controlled basis.

Absence from an addendum is never withdrawal. A later notice with no matching files creates a
missing-release warning.

## 11. Physical files, logical documents and versions

- A **received physical file** is immutable bytes/path in one release.
- A **logical document** is the TASK-08 `documents` identity.
- A **document version** is immutable TASK-08 `document_versions` lineage evidence.
- A physical file may contain zero, one or many logical documents.
- A logical version may be evidenced by a whole file or bounded page/sheet/member locator.
- SHA-256 equality proves byte identity; semantic revision requires human-confirmed relationship.

OPS-11 does not alter TASK-08 semantics. Physical receipt uses separate evidence and links to a
TASK-08 version only after accepted identification/promotion. Confidence never substitutes for human
confirmation.

## 12. Content form versus business purpose

`content_form` describes representation only: `TEXT_DOCUMENT`, `DRAWING`, `SPREADSHEET`, `FORM`,
`IMAGE`, `MIXED_DOCUMENT`, `ARCHIVE`, `CAD_FILE`, `UNKNOWN`.

Business purpose remains TASK-08 category or destination domain. A PDF may be `MIXED_DOCUMENT` while
logical parts support solicitation, specification, drawing, commercial and contractual purposes.
Extension/MIME are evidence, not business purpose.

## 13. Drawing and page-level future boundary

OPS-11A defines no extractor. Future page evidence must support page number, page content form,
region coordinates, method, confidence and physical source. Drawing proposals must support drawing
title/number, sheet, revision, discipline, type, status, scale, dimensions, orientation, title-block
region, related equipment, confidence and extraction method.

These remain staged until reviewed. Mixed PDFs may link page ranges to multiple controlled logical
versions. Rendered derivatives are processing artifacts, never registered originals.

## 14. Managed-storage architecture

### Roots and preview

- Configure allowlisted intake roots separately from the managed root; accept no arbitrary path.
- Require the source beneath one root and reject symlinks in every component/entry using `lstat` and
  no-follow opens; `Path.resolve` alone is insufficient.
- Preview is read-only: bounded metadata/path/type/size validation and optional streaming hashes. It
  creates no DB row, managed file, audit, or cache.
- Never watch, synchronize, modify, or retain the landing directory as authority.

### Copy and publication

1. Create an operation-owned temporary release directory on the managed filesystem.
2. Open source without links; capture pre/post `fstat`; stream to opaque file-ID destinations while
   hashing; flush and `fsync`.
3. Independently re-read/hash the staged destination. Reject changed source metadata, byte-count or
   hash mismatch.
4. Store original name/normalized relative path only as metadata; never construct destinations from it.
5. Validate release fingerprint/idempotency, start `BEGIN IMMEDIATE`, insert release/files/audit,
   atomically rename temp to final opaque directory, then commit.
6. On caught failure, roll back and remove only operation-owned uncommitted files.
7. Never overwrite/delete committed originals in normal behavior.

SQLite and filesystem rename cannot form one physical transaction, so use OPS-09-style compensation
plus explicit recovery reconciliation. Metadata cannot claim success until all copies are verified.

### Separation, authorization and archive boundary

- Originals: `<managed-root>/intake/releases/<release-id>/originals/<file-id>.bin`.
- Processing and exchange artifacts use separate opaque roots keyed by run/artifact IDs.
- None is mounted under `/static` or `/reports`.
- Download verifies user authorization when identity exists, Bid ownership, state, safe opaque key,
  symlink absence and current hash; the original display name is safely encoded.
- Register an archive as opaque original first. Member extraction is processing only. Reject absolute,
  drive/UNC, `..`, NUL/control, normalization collisions, duplicate names, links/devices/FIFOs,
  encrypted/unsupported members and unsupported compression. Stream members to opaque paths; never
  call unbounded `extractall`; never automatically expand nested archives in V1.

### Provisional limits requiring Jason approval

- 1,000 outer files and 1 GiB total bytes per release.
- Reuse the existing 50 MiB per-file default unless a distinct limit is authorized.
- 32 path components, 255 UTF-8 bytes/component and 1,024 bytes/relative path.
- 10,000 archive members, 50 MiB/member, 1 GiB expanded total, 100:1 compression ratio, depth 1.
- Per-file wall-clock, CPU, memory and file-descriptor limits for parser workers.

Limits are enforced during preview, copy and processing and recorded with runs.

## 15. Exact inventory fields

**Notice:** `notice_id`, `bid_id`, `release_kind`, `customer_release_reference`,
`customer_sequence`, `customer_release_date`, `channel`, `noticed_at`, `subject_or_summary`,
`files_expected`, `evidence_document_version_id`, `evidence_reference`, `evidence_note`,
`supersedes_notice_id`, `recorded_by`, `recorded_at`, `provenance_json`.

**Declared notice item:** `notice_item_id`, `notice_id`, `declared_name`, `document_number`,
`revision_label`, `item_kind`, `expected_status`, `sequence`, `provenance_json`.

**Channel check:** `check_id`, `bid_id`, `channel`, `checked_at`, `observed_latest_reference`,
`observed_sequence`, `observation`, `evidence_document_version_id`, `evidence_reference`,
`recorded_by`, `recorded_at`, `provenance_json`.

**Notice/release match:** `notice_release_link_id`, `bid_id`, `notice_id`, `release_id`,
`match_basis`, `linked_by`, `linked_at`, `provenance_json`.

**Received release:** `release_id`, `bid_id`, `release_kind`, `release_label`,
`customer_release_reference`, `customer_sequence`, `received_at`, `received_channel`,
`handling_mode`, `source_root_alias`, `idempotency_key`, `release_fingerprint_sha256`,
`outer_file_count`, `total_bytes`, `registered_by`, `registered_at`, `provenance_json`.

**Physical file:** `received_file_id`, `release_id`, `bid_id`, `original_filename`,
`original_relative_path`, `path_sort_key`, `byte_size`, `sha256_digest`, `managed_storage_key`,
`extension`, `declared_media_type`, `detected_media_type`, `content_form`,
`content_form_method`, `content_form_confidence`, `is_archive`, `is_analysis_eligible`,
`analysis_exclusion_reason`, `byte_duplicate_of_file_id`, `registered_at`, `provenance_json`.

**File/document link:** `link_id`, `bid_id`, `received_file_id`, `document_version_id`,
`relationship`, `page_start`, `page_end`, `sheet_or_member_locator`, `identification_method`,
`confidence`, `confirmed_by`, `confirmed_at`, `provenance_json`.

**Directive:** `directive_id`, `bid_id`, `release_id`, `source_received_file_id`,
`source_document_version_id`, `source_locator`, `directive_type`, `description`, `materiality`,
`target_document_id`, `target_document_version_id`, `sequence`, `provenance_json`, `created_at`.

**Disposition version:** `disposition_id`, `directive_id`, `version_number`, `state`, `decision`,
`rationale`, `result_target_type`, `result_target_id`, `approval_id`, `route_id`,
`expected_prior_version`, `decided_by`, `decided_at`, `provenance_json`.

**Basis snapshot:** `basis_snapshot_id`, `bid_id`, `sequence`, `label`,
`predecessor_snapshot_id`, `through_release_id`, `basis_sha256`, `created_by`, `created_at`,
`provenance_json`.

**Basis release member:** `basis_snapshot_release_id`, `basis_snapshot_id`, `release_id`,
`incorporation_role`, `created_at`.

**Basis document member:** `basis_snapshot_document_id`, `basis_snapshot_id`,
`document_version_id`, `originating_release_id`, `basis_action`, `governing_role`, `directive_id`,
`sequence`, `created_at`.

**Processing run:** `processing_run_id`, `bid_id`, `release_id`, `run_kind`,
`tool_contract_version`, `idempotency_key`, `state`, `started_by`, `started_at`, `completed_at`,
`limits_json`, `input_fingerprint_sha256`, `result_fingerprint_sha256`, `error_code`,
`error_summary`, `version`.

**Per-file result:** `processing_file_result_id`, `processing_run_id`, `received_file_id`, `state`,
`detected_media_type`, `content_form`, `page_or_sheet_count`, `encrypted`, `macro_present`,
`analysis_eligible`, `exclusion_reason`, `artifact_manifest_json`, `warning_json`, `error_code`,
`error_summary`, `started_at`, `completed_at`, `provenance_json`.

**Acknowledgement event:** `acknowledgement_event_id`, `bid_id`, `release_id`, `state`,
`required_basis`, `acknowledged_at`, `method`, `destination_reference`,
`evidence_document_version_id`, `note`, `recorded_by`, `recorded_at`, `provenance_json`.

**Checklist template/version/item:** template `template_id`, `library_kind`, `code`, `name`,
`lifecycle_state`, `created_by`, `created_at`, `provenance_json`; version `template_version_id`,
`template_id`, `version_number`, `applicability_json`, `fingerprint_sha256`, `approval_reference`,
`published_by`, `published_at`, `provenance_json`; item `template_item_id`,
`template_version_id`, `item_code`, `sequence`, `title`, `description`, `expected_evidence`,
`materiality`, `destination_domain`, `provenance_json`.

**Checklist instance/item:** instance `checklist_instance_id`, `bid_id`, `template_version_id`,
`context_type`, `release_id`, `basis_snapshot_id`, `proposal_version_id`,
`applicability_inputs_json`, `created_by`, `created_at`, `provenance_json`; item
`checklist_instance_item_id`, `checklist_instance_id`, `template_item_id`, `status`, `rationale`,
`evidence_type`, `evidence_id`, `owner`, `due_date`, `version`, `updated_by`, `updated_at`,
`provenance_json`.

**Intake/work link:** `intake_work_link_id`, `bid_id`, `work_item_id`, `purpose`, and exactly one of
`notice_id`, `release_id`, `directive_id`, `checklist_instance_item_id`, plus `created_by`,
`created_at`, `provenance_json`.

Raw text/large page structures do not belong in control rows; processing artifacts use separate
managed files, hashes and bounded manifests.

## 16. Processing-state machine

A run has `QUEUED`, `RUNNING`, and terminal `SUCCEEDED`, `PARTIAL`, `FAILED`, `INTERRUPTED`, or
`CANCELLED`. A file has `PENDING`, `IDENTIFIED`, `EXCLUDED`, `SUCCEEDED`, `UNSUPPORTED`,
`ENCRYPTED`, `UNREADABLE`, `FAILED`, or `INTERRUPTED`.

Registration succeeds only after every outer file is copied/verified. Inventory may be partial while
retaining all files. Analysis is incomplete if an eligible file fails; reviewed exclusion is distinct
from failure. Run history is append-only; only current run execution state/version is mutable.

## 17. User workflow

1. Open Bid → Package Intake & Addenda.
2. Choose initial package, addendum, clarification, or release notice.
3. Select an allowlisted source; preview counts, size, types, path warnings and handling mode without
   mutation.
4. Confirm once; ContractIQ snapshots the release without per-file entry.
5. Review inventory, duplicates, unsupported/encrypted items and discrepancies.
6. Confirm directives, dispositions and acknowledgement evidence.
7. Accept findings into existing authoritative registers from the same flow.
8. When deterministic rules pass, create the next immutable Bid Basis snapshot.
9. Open/export Bid Basis Register and resolve OPS-09 staleness.

The user never types JSON/internal IDs/managed paths, renames customer files, physically merges
addenda, leaves the Bid to understand status, or re-enters accepted facts.

## 18. Bid workspace page: Package Intake & Addenda

This is a seventh section in the existing workspace with the standard Bid header/readiness rail. Its
top panel shows current controlled Bid basis; latest known, received, inventoried, analyzed,
partially incorporated and fully incorporated releases; portal/email discrepancy; and
proposal-baseline staleness.

Primary actions are exactly: **Register Initial Package**, **Add Addendum**, **Add Clarification**,
**Record Release Notice**, **Record Portal/Email Check**, **Prepare Work AI Package**,
**Import Work AI Results**, and **Open Bid Basis Register**. Unimplemented or handling-mode-blocked
actions are disabled with reasons. Warnings link to exact authoritative evidence/actions.

## 19. Customer-facing Bid Basis Register

This deterministic projection of one snapshot identifies Bid, basis label/date/hash, predecessor,
incorporated releases/addenda, acknowledgement, and each governing document's title, number,
revision, issue/received date, source release and status. It excludes managed paths, internal notes,
AI confidence and internal commercial/risk content. Raw hashes appear only in an explicit advanced
evidence export.

Initial formats are HTML and formula-safe CSV. A working view is visibly non-controlled and is never
exported as the controlled customer-facing register.

## 20. OPS-09 proposal-baseline integration

OPS-09 remains issue authority. Its `supporting_documents` currently projects eligible current
controlled documents. A future adapter should project exact current Bid Basis versions in stable
snapshot order while preserving frozen Proposal Studio V1. Its supporting-document area hash then
detects basis changes without a second staleness engine.

A known unreceived release, unresolved material directive, partial incorporation, failed governing
integrity, or missing controlled basis blocks OPS-09 with a Package Intake correction link. A new
fully incorporated snapshot changes the projection and makes an older export/candidate stale.
Issued offer baselines remain immutable. Bid Basis is customer-source evidence; issued offer baseline
is what the company issued. OPS-10 reconciliation remains outside OPS-11.

## 21. Checklist-template architecture

There are exactly three library kinds, never one ambiguous checklist:

1. `INCOMING_PACKAGE_COMPLETENESS` — what the customer would normally provide.
2. `BID_RESPONSE_DELIVERABLES` — what the company must submit.
3. `INTERNAL_REVIEW_CONTROLS` — what must be checked before proposal issue.

Templates have stable identities; template versions and version items are immutable. Applicability
is a validated, canonical rule object over customer/EPC/EPCM, Bid classification, opportunity type,
equipment category, contract type, direct sale/packaged solution, technical complexity, commercial
risk, proposal type and a referenced previous **approved** customer pattern. Nothing is guessed.

Instance status is one of `NOT_ASSESSED`, `EXPECTED`, `RECEIVED`, `PARTIALLY_RECEIVED`, `MISSING`,
`MENTIONED_NOT_PROVIDED`, `POSSIBLY_PRESENT_UNIDENTIFIED`, `NOT_REQUESTED`, `NOT_APPLICABLE`,
`CUSTOMER_CONFIRMED_NOT_REQUIRED`, `INTERNALLY_WAIVED`, `SUPERSEDED`, `UNCERTAIN`.

An instance freezes its exact template version and applicability inputs. Reusable changes require
explicit user approval, a new version, provenance and audit; no Bid or AI result silently changes a
template. Response/internal items link to existing requirement, deliverable, proposal, approval or
readiness facts rather than replacing them. Internal waivers require existing decision/approval
evidence.

## 22. Work AI and Alice exchange boundary

No direct Work AI API is authorized. Future processing is:

```text
local preprocessing
  -> stateless package containing instructions, schemas and normalized evidence
  -> manual transfer by Jason
  -> returned JSON/Markdown/ZIP
  -> strict local validation and hash checks
  -> non-authoritative staged findings
  -> human decision
  -> promotion through an existing ContractIQ service
```

Every export includes schema ID/version, complete instructions, allowed finding types, locators,
handling mode, file/basis hashes and limits because persistent Work AI knowledge is unavailable.
Sanitized mode contains only locally generated, reviewed sanitized artifacts; originals/reversible
secrets are excluded. Inventory/local-only modes prevent Work AI export.

Returned content is inert data. Strict UTF-8 JSON rejects BOM, duplicate keys, unknown fields,
non-finite numbers, absolute/traversal paths and unbounded collections, following OPS-09. Markdown is
evidence, never executable instruction; returned ZIP uses the archive boundary. Alice consumes the
same normalized contracts locally with network disabled. AI findings start
`human_confirmed=False`.

Accepted findings promote exactly once through an existing domain service. A promotion link records
the finding and resulting destination ID/version/audit. No generic importer writes authoritative
tables directly.

## 23. Audit, provenance and optimistic concurrency

Every successful mutation writes `audit_log` in the same transaction: notice/check creation, release
registration, duplicate/exclusion decision, file/document link, directive/disposition,
acknowledgement, basis snapshot, checklist publication/instance update, run transition, finding
decision and promotion.

Every asserted new register fact carries `provenance_json`. AI staging records agent/model and exact
received-file/document locator with `human_confirmed=False`; acceptance supplies confirmer/time.
Readiness ignores unconfirmed material findings.

Mutable aggregates use positive integer `version` and `expected_version`; conditional updates occur
inside `BEGIN IMMEDIATE`. Immutable events/snapshots reject update/delete by triggers. GET and preview
never audit or mutate.

## 24. Idempotency and transactional behavior

- Server issues a Bid-bound operation UUID; deterministic IDs may use UUIDv5 like existing controlled
  source registration.
- `UNIQUE(bid_id,idempotency_key)` guarantees replay. Canonical request/fingerprint must match;
  changed payload under one key fails.
- Exact completed replay returns the same release without extra files/audit.
- Release fingerprint hashes canonical entries of normalized relative path, byte size and raw file
  SHA-256. It is DB evidence, never a confidential Git manifest.
- Same bytes in a genuine later customer release remain separate evidence with a duplicate warning.
- Validate all same-Bid FKs, paths, hashes, limits and expected versions before writes.
- Release/file/basis/junction/audit writes share one transaction plus filesystem compensation.
- Any validation/audit failure produces no partial authoritative record.

## 25. Security and resource controls

- Treat files/archives as hostile. Parsers run outside the web process with least privilege, no
  network and bounded CPU/memory/time/file descriptors under an operation-owned temp root.
- Reject symlinks at intake, staging, archive and download boundaries; never follow archive links.
- Use opaque server IDs, exclusive creation, mode `0600`, `fsync`, independent SHA-256 and no public
  static mounts.
- Never execute Office macros, formulas, DDE, embedded OLE, JavaScript, URLs or active content.
  Spreadsheet formulas are inert cached/text data only.
- Authorized subprocesses use fixed executables/arguments, `shell=False`, minimal environment,
  closed stdin, bounded captured output and hard timeout.
- Compare MIME/header, extension and parser results; disagreement is visible, not silently coerced.
- Logs/errors use safe IDs/summaries, not content or confidential paths.
- Bound bytes/count/depth/strings before parsing and make CSV exports formula-safe.

## 26. Failure, restart and recovery

- Temp-only interruption: mark run `INTERRUPTED`, do not publish, quarantine/retain under policy and
  permit idempotent retry only after revalidation.
- Final directory without committed DB release: identify from opaque operation marker, quarantine,
  never attach by guess.
- Committed DB release with missing/corrupt original: integrity hold and audit-visible incident; do
  not recopy from the non-authoritative landing folder automatically.
- Healthy committed release with interrupted processing: preserve originals and start a new run.
- Stale run lock: compare version/heartbeat transactionally so one worker claims recovery.
- Startup reconciliation is bounded, parses no documents and prints no original names.
- Backup includes SQLite plus all managed original, processing and exchange roots at a consistent
  checkpoint. Restore validates FK/integrity and stored hashes; DB-only backup is incomplete.

## 27. Authorized additive schema boundary

OPS-11A itself implemented no migration. Jason subsequently authorized this exact 18-table first
migration for OPS-11B:

| Proposed table | Purpose | Relationship |
|---|---|---|
| `bid_release_notices` | Immutable/corrected release assertions | FK `bids`, optional `document_versions`, self-FK |
| `bid_release_notice_items` | Customer-declared files/documents/revisions | FK notice |
| `bid_release_channel_checks` | Manual portal/email/other observations | FK `bids`, optional controlled evidence |
| `bid_received_releases` | Immutable registered release and handling mode | FK `bids` |
| `bid_release_notice_links` | Explicit notice/received matching | FKs notice/release |
| `bid_received_files` | Immutable physical receipt/storage evidence | FK release; byte duplicates remain derived |
| `bid_received_file_document_links` | Physical file/page/member to TASK-08 version | FKs file/`document_versions` |
| `bid_received_file_disposition_events` | Append-only classification, eligibility, exclusion and duplicate decisions | FK file, optional exact-byte duplicate file, correction self-FK |
| `bid_addendum_directives` | Immutable customer change instruction | FKs release/file/document/version |
| `bid_addendum_dispositions` | Immutable versioned decisions/results | FK directive; optional approval/route/typed result |
| `bid_release_acknowledgement_events` | Manual acknowledgement history | FK release; optional controlled evidence |
| `bid_basis_snapshots` | Immutable controlled basis lineage/hash | FK `bids`, predecessor, through-release |
| `bid_basis_snapshot_releases` | Releases represented in basis | FKs snapshot/release |
| `bid_basis_snapshot_documents` | Exact governing TASK-08 versions | FKs snapshot/version/release/directive |
| `bid_intake_processing_runs` | Execution/restart/idempotency control | FK release/`bids` |
| `bid_intake_processing_file_results` | Per-file terminal attempt evidence | FKs run/file |
| `bid_intake_work_item_links` | Exact intake-to-My Work relationship | FK work/`bids`; exactly one intake target |
| `bid_package_intake_schema_migrations` | Migration evidence | Migration ID PK |

Deferred checklist persistence: `bid_checklist_templates`, `bid_checklist_template_versions`,
`bid_checklist_template_version_items`, `bid_checklist_instances`, and
`bid_checklist_instance_items`. Their business architecture remains valid but requires a separate
post-pilot migration authorization.

The separate physical-file authority is required: TASK-08 versions require an already identified
logical document and cannot represent unidentified archives/mixed files or one physical file with
multiple logical documents. No existing table/column is repurposed.

## 28. Existing facts reused versus new authoritative facts

Reused: Bid identity/classification; logical documents/versions; requirements/responses;
scope/interfaces; supplier/manufacturer/VDRL; deliverables; commercial/risk; decisions/approvals;
scenarios; proposals/issued baselines; work; readiness; handover; audit and provenance.

New authorization: customer notices/checks; received release identity/handling mode; physical
receipt/path/storage evidence; physical/logical links; directives/dispositions; acknowledgements;
Bid Basis snapshots/memberships; processing attempts; and exact intake/work relationships.

The three checklist libraries/instances remain new authoritative facts but were not authorized for
the first migration.

Future Work AI/Alice export/import receipts, staged findings/decisions/promotion links are new facts
but belong in a separately authorized migration after local intake/review is stable.

## 29. Proposed migration identifier

`ops_11_bid_package_intake_addendum_control_v1`, stored in
`bid_package_intake_schema_migrations`.

It must be additive, forward-only, idempotent, restart-safe, non-destructive, have no guessed
backfill, and silently seed no templates. OPS-11A does not create it.

## 30. Index, uniqueness, foreign-key and trigger requirements

Required uniqueness:

- notice items `(notice_id,sequence)`;
- releases `(bid_id,idempotency_key)`, with non-unique fingerprint index for duplicate warnings;
- files `(release_id,original_relative_path)` and global `managed_storage_key`;
- file/version/locator link tuple;
- directives `(release_id,sequence)` and dispositions `(directive_id,version_number)`;
- basis `(bid_id,sequence)` and `(bid_id,basis_sha256)`;
- basis document `(snapshot,document_version,governing_role)`;
- processing run `(release,run_kind,idempotency_key)` and file result `(run,file)`;
- template `(library_kind,code)`, version `(template,version_number)`, item code per version, and
  instance item `(instance,template_item)`; and
- work-link target/purpose through partial indexes.

Indexes support Bid/time ordering, unmatched notices, release sequence, file hash, latest run,
unresolved material directives, snapshot history, checklist status and My Day warning queries.

All Bid facts FK to `bids`. Same-Bid consistency not expressible by a simple FK is enforced by
service plus defensive trigger. Polymorphic destinations use a fixed allowlist/service validation,
never input-derived table names.

`BEFORE UPDATE/DELETE` triggers protect notices/items/checks, registered releases/files/links,
directives/dispositions, acknowledgements, snapshots/memberships, terminal results and published
template versions/items. Only documented state/version fields on active runs, checklist instance
items and work links may update through audited repositories. Snapshot service calculates/rechecks
canonical hash and lineage.

## 31. Acceptance criteria

- Three ledgers remain distinct, Bid-owned and visible in one workspace.
- Notice without files is valid and creates outstanding-release warning.
- Releases/all outer files are immutable after atomic registration.
- Original names/hierarchy are metadata; managed paths opaque; hashes exactly match source.
- Failed publication leaves no partial authoritative release; every registered file stays visible on
  processing failure.
- Byte duplicate is distinct from semantic revision; archives remain retained evidence.
- Missing addendum items never withdraw prior documents.
- Material directives require human-confirmed final disposition before full incorporation.
- Partial incorporation is prominent and cannot create a controlled snapshot.
- Prior basis snapshots remain immutable; register names governing versions/addenda.
- Manual channel observations and acknowledgement are visible.
- OPS-09 uses exact basis and deterministic staleness/blockers.
- Checklist libraries are separate/versioned and never silently learn.
- AI remains staged until human review/existing-domain promotion.
- Existing readiness remains the sole gate engine.
- Replay is idempotent; stale writes fail; writes audit atomically; GET/preview is read-only.
- No original is static/public; hostile paths/archives/resources are bounded; no macro runs.

## 32. Synthetic-test strategy

Use generated files/temp roots and isolated SQLite only. Cover safe nesting, Unicode/case collisions,
traversal/absolute paths, symlinks, source mutation, exact copy/hash, collisions, injected
DB/audit/rename failures, crash recovery, zero/oversize/count limits, archive bombs/malicious members,
all release kinds, notice-without-files, sequence/channel gaps, duplicates versus revisions,
mixed/page links, unsupported/encrypted states, all directive/partial/full/carry-forward/withdrawal
rules, immutable snapshots, all checklist statuses/libraries/version rules, handling modes, strict
exchange/staging/exactly-once promotion with mocked destination services, cross-Bid access,
optimistic concurrency, trigger/audit rollback, restart/FK/integrity, query bounds, OPS-09 area-hash
staleness and immutable issued baselines.

Tests make no network call, execute no macro/shell and contain no confidential name, layout, content,
raw hash or derived text.

## 33. Real pilot strategy using aggregate facts only

The confidential pilot is future manual acceptance only. This specification uses supplied aggregates:
16 outer files, 15 analysis-eligible documents, one retained duplicate ZIP, active types 10 PDF /
1 DOCX / 2 XLSM / 2 XLSX, five folders, zero symlinks and approximately 29 MiB. Its customer-specific
layout is data, never a product requirement.

Future acceptance expects source byte identity, matching managed hashes, preserved hierarchy,
inventory of all 16, retained duplicate archive excluded from repeat analysis, visible processing
failures and idempotent replay. Only aggregate results may be reported. The package never enters Git,
automated fixtures, external models contrary to handling mode, or Git manifests of names/hashes.

## 34. Python/library feasibility analysis

### Existing environment

Declared dependencies are FastAPI, Uvicorn, multipart, Jinja2, PyMuPDF, python-docx, ReportLab,
openpyxl, pandas, requests and Pydantic v2. The environment is Python 3.13.13. Installed evaluated
candidates: PyMuPDF 1.28.0, python-docx 1.2.0 and openpyxl 3.1.5. System `file` 5.45 exists.
Absent: python-magic, pypdf, pdfplumber, PyMuPDF4LLM, OCRmyPDF, Tesseract, defusedxml, oletools,
Tika/Java, Docling, OpenCV, py7zr, 7-Zip, UnRAR and LibreOffice. OPS-11A adds nothing.

| Candidate | Intended capability | Installed / Python-system dependency | License concern | Security/compatibility concern | Role and phase |
|---|---|---|---|---|---|
| libmagic/python-magic | Header MIME detection | Wrapper absent; native libmagic/database (`file` present) | Wrapper MIT; libmagic BSD-style terms/notices require deployment review | Fallible; shared wrapper instance not thread-safe; no automatic decompression | **Primary detector**, OPS-11B after approval |
| pypdf | PDF structure/metadata/encryption/text | Absent; pure Python; crypto extra only for authorized decryption | BSD-3-Clause | Hostile PDF resource risk; layout/text limits | **Primary structural verifier**, bounded OPS-11B |
| pdfplumber | PDF character geometry/tables | Absent; pdfminer.six/Pillow | MIT | Best machine-generated, no OCR, slower/render surfaces | **Secondary verifier/benchmark**, later |
| PyMuPDF | Fast PDF parse/render/geometry | Installed 1.28.0; native MuPDF | AGPL-3.0 or commercial; legal decision required | Native parser/resource surface; already a project dependency | **Reject new OPS-11 use pending license**; then secondary OPS-11B |
| PyMuPDF4LLM | Layout-aware Markdown/JSON/text | Absent; may upgrade PyMuPDF/Layout | Same AGPL/commercial boundary | Dependency/model/OCR churn; proposal output only | **Optional benchmark**, later after license |
| OCRmyPDF | OCR layer/PDF repair | Absent; pikepdf/qpdf plus OCR/rasterizer tools | MPL-2.0 core; Ghostscript can add AGPL | Project warns hostile PDFs need isolation; rewrites files | **Optional derivative-only fallback**, later, `shell=False` |
| Tesseract | Local image OCR | Absent executable/trained data | Apache-2.0 | Accuracy/language/resource/image risks | **Optional OCR engine**, later |
| python-docx | DOCX paragraphs/tables | Installed 1.2.0 | MIT | OOXML ZIP/XML, external/embedded objects; no legacy DOC | **Primary DOCX extractor**, isolated OPS-11B |
| openpyxl | XLSX/XLSM metadata/cells | Installed 3.1.5 | MIT | Project warns XML attacks; formulas/macros/links inert; bounded read-only needed | **Primary spreadsheet extractor**, OPS-11B |
| defusedxml | XML entity/DTD hardening | Absent; Python-only | PSF | Not universal ZIP/resource protection or blanket lxml fix | **Required hardening/secondary verifier**, OPS-11B |
| oletools | OLE/VBA/XLM/DDE/embedded detection | Absent; Python + optional extras | BSD-style plus third-party notices | Analyzer parses hostile objects; never execute/deobfuscate as code | **Macro-presence detector only**, OPS-11B/later |
| Apache Tika | Broad MIME/metadata/text | Absent; Java absent, large parser/JVM stack | Apache-2.0 + transitive notices | Project says not a security boundary; DoS/crash; server has no auth | **Reject as primary**; isolated benchmark later |
| Docling | Unified layout/table/OCR conversion | Absent; large Python/native/model graph | MIT code; model licenses vary | Disable URLs/downloads; heavy resources; security advisories require audit/pinning | **Optional benchmark**, later |
| OpenCV | Deskew/segmentation/title-block preprocessing | Absent; native codecs/wheels | Apache-2.0 for 4.5+ | Image bombs/native codec risk; no document semantics | **Secondary image preprocessing**, later |
| stdlib `zipfile`/`tarfile` | ZIP/TAR inventory/bounded streaming | Included with Python | PSF | Default extraction is not policy; traversal/links/bombs/collisions | **Primary ZIP inventory**, OPS-11B; no nested auto-expansion |
| py7zr | 7z inventory/extraction | Absent; compression backends | LGPL-2.1-or-later legal review | Historical traversal CVE; bombs/complex codecs | **Optional pinned sandboxed fallback**, later |
| rarfile/libarchive adapters | RAR/broad formats | Absent; external/native tools often required | Tool/codec license varies | Native/external parser and portability risk | **Deferred/rejected for V1** |

Sources checked: [python-magic](https://pypi.org/project/python-magic/),
[pypdf](https://pypi.org/project/pypdf/), [pdfplumber](https://pypi.org/project/pdfplumber/),
[PyMuPDF](https://pymupdf.readthedocs.io/en/latest/about.html),
[PyMuPDF4LLM](https://pypi.org/project/pymupdf4llm/),
[OCRmyPDF](https://ocrmypdf.readthedocs.io/en/stable/introduction.html),
[Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html),
[python-docx](https://pypi.org/project/python-docx/), [openpyxl](https://pypi.org/project/openpyxl/),
[defusedxml](https://pypi.org/project/defusedxml/), [oletools](https://pypi.org/project/oletools/),
[Apache Tika security](https://tika.apache.org/security-model.html),
[Docling](https://pypi.org/project/docling/), [OpenCV](https://opencv.org/license/), and
[py7zr](https://pypi.org/project/py7zr/).

This is feasibility analysis, not legal advice. License approval, pinned versions, SBOM/CVE review,
offline install and synthetic security benchmarks precede any dependency change.

## 35. Authorized OPS-11B and deferred later capabilities

Authorized OPS-11B: core additive schema/models; allowlisted preview; release-level
snapshot, inventory and recovery; notices/checks; release/files/duplicates/acknowledgements; manual
directives/dispositions and deterministic basis snapshots; workspace/Register; My Work/My Day and
OPS-09 adapters; synthetic tests; no dependency changes.

Later separate authorization: page/mixed/drawing extraction; OCR/image processing; Work AI export /
import and sanitized packages; staged findings/decisions/promotions using proposed tables
`bid_intake_analysis_exports`, `bid_intake_analysis_imports`, `bid_intake_staged_findings`,
`bid_intake_finding_decisions`, `bid_intake_finding_promotion_links`; local Alice on the same
contracts; checklist tables/instances; extra archive formats; semantic-revision assistance and parser
benchmarks.

No portal/email connector, watched sync, automated communication/approval, post-award execution or
competing domain is implied.

## 36. Authorization resolution and future decisions

Jason's OPS-11B direction resolved the first-migration, physical-file, disposition, Bid Basis,
waiver, acknowledgement, storage, register, ordering, exact My Work relationship, duplicate and
quarantine decisions. No additional schema decision is required for the authorized implementation.

Future authorization is still required before checklist persistence, Work AI/Alice exchange,
content extraction, OCR, page/drawing classification, archive expansion, or any new dependency.
PyMuPDF and related licensing must be resolved before expanding its use. The next post-implementation
step is manual browser acceptance with synthetic data, followed later by a separately controlled
real-package pilot whose report contains aggregate results only.
