# OPS-11B — Bid Package Intake and Addendum Control

**Status:** IMPLEMENTED — awaiting manual browser acceptance

**Baseline:** `5b92c2764d01000f5aae55e3dc6002271d1e0bad`

**Migration:** `ops_11_bid_package_intake_addendum_control_v1`

## Purpose and product boundary

OPS-11B gives the Bids and Contracts Manager one pre-award control path from a customer release
notice through immutable receipt, review, addendum disposition, controlled Bid Basis publication and
proposal-issue readiness. It implements three distinct ledgers:

1. Release Notice Ledger — what the customer says exists, will be issued, or should have arrived.
2. Received Package Ledger — exactly what ContractIQ received and copied to managed storage.
3. Bid Basis Ledger — the exact received releases and TASK-08 document versions governing the Bid.

Expected, received, reviewed, incorporated, acknowledged and customer-accepted are never aliases.
The same ingestion service handles initial packages, addenda, clarifications, revised packages and
other releases. Customer reference strings are evidence only; the application never parses “2”,
“Rev A” or a document number to infer contractual precedence.

This remains a pre-award Bid application. OPS-10 stays dormant. Post-award execution, portal/email
integration, watched folders, external synchronization, automatic communications, Work AI exchange,
AI finding promotion, OCR, extraction, archive expansion and automatic approval are out of scope.

## Existing authority reused

| Concern | Existing authority | OPS-11B integration |
|---|---|---|
| Bid identity, status and classification | `bids`, `BidRepository` | Every ledger root is Bid-owned by FK. |
| Logical documents and exact versions | TASK-08 `documents`, `document_versions` | Physical files link to exact versions; snapshots reuse those versions. |
| Original controlled-document storage | `ManagedDocumentStorage` | Preserved unchanged; intake has a separate release-level storage adapter/root. |
| Requirements, scope, manufacturer/VDRL, commercial and risk | Existing domain services/tables | No competing registers. Customer-facing qualifications reuse controlled requirements. |
| Decisions and approvals | `approvals`, `approval_routes` | Internal waivers and acknowledgement exceptions require obtained/approved evidence. |
| Proposal issue and staleness | OPS-09 `ProposalExchangeService` | Uses current Bid Basis versions as `supporting_documents` after intake population. |
| Readiness | Existing gate/readiness and OPS-09 assessment | Intake adds blockers to the existing proposal assessment, not a new engine. |
| My Work | `work_items` | Exact FK-backed links are deliberate and idempotent. |
| My Day | `MyDayService` | Outstanding required acknowledgements project as due/overdue attention. |
| Audit and provenance | `audit_log`, `Provenance` | Every authoritative mutation writes atomic audit and human-confirmed provenance. |

## Additive migration

Repository initialization applies an idempotent immediate transaction, creates indexes and immutable
row triggers, records one migration marker, and performs no destructive DDL or backfill.

| Table | Authoritative purpose |
|---|---|
| `bid_release_notices` | Immutable release assertions/corrections, channel and evidence |
| `bid_release_notice_items` | Optional customer-declared item inventory under a notice |
| `bid_release_channel_checks` | Manual portal/email/other observations |
| `bid_received_releases` | Immutable receipt identity, exact ordering facts and storage publication |
| `bid_release_notice_links` | Explicit notice-to-receipt resolution |
| `bid_received_files` | Immutable physical occurrence, original relative path, size/hash and opaque storage key |
| `bid_received_file_document_links` | Append-only physical-file to exact TASK-08 version relationship |
| `bid_received_file_disposition_events` | Append-only classification, eligibility, exclusion and duplicate decisions |
| `bid_addendum_directives` | Immutable customer change directive/correction lineage |
| `bid_addendum_dispositions` | Append-only authoritative directive decision/correction lineage |
| `bid_release_acknowledgement_events` | Append-only required/not-required/acknowledged/exception history |
| `bid_basis_snapshots` | Immutable Bid Basis identity, sequence, predecessor and canonical hash |
| `bid_basis_snapshot_releases` | Explicit release membership and incorporation sequence |
| `bid_basis_snapshot_documents` | Exact governing logical document/version and source release/file |
| `bid_intake_processing_runs` | Terminal registration/reverification attempts and replay identity |
| `bid_intake_processing_file_results` | Per-file terminal verification result |
| `bid_intake_work_item_links` | Exact FK relationship to one notice, release, file, directive or acknowledgement event |
| `bid_package_intake_schema_migrations` | Migration evidence |

The five checklist tables from the OPS-11A candidate are intentionally absent. Checklist templates,
versions, instances and instance items require a later migration after the real-package pilot.

All evidence/event/snapshot/junction tables reject `UPDATE` and `DELETE`. Corrections append a new row
with a self-FK to the current row; unique partial indexes prevent two corrections superseding the same
event. Physical duplicate occurrences across releases remain separate rows. Exact byte duplication is
derived from SHA-256 plus size; a duplicate designation is a current disposition event, not mutable
physical evidence.

## Release ordering and lifecycle

The Received Package Ledger stores three independent ordering facts:

- exact customer release/addendum reference, nullable when not supplied;
- customer issue date, nullable when not supplied; and
- server-assigned, monotonically increasing per-Bid receipt sequence plus actual received timestamp.

Missing references, duplicate references and customer issue dates apparently out of receipt sequence
produce visible human-review attention. These warnings never invent order. A Bid Basis snapshot
explicitly lists incorporated releases in the selected order.

Lifecycle:

```text
notice (files may be absent)
  -> mutation-free preview
  -> confirmed managed receipt
  -> immutable inventory + UNKNOWN / NOT_ASSESSED disposition
  -> append-only file review and exact TASK-08 links
  -> append-only directive dispositions
  -> eligible for full incorporation
  -> immutable successor Bid Basis snapshot
```

A release is partial when review/disposition work exists but the release is not in the current
snapshot. It is fully incorporated only through explicit membership in the current immutable
snapshot.

## Deterministic incorporation rules

Snapshot publication uses `BEGIN IMMEDIATE` and an expected current snapshot ID. It fails when:

- the managed registration verification is absent;
- any physical file remains `NOT_ASSESSED`;
- an `ELIGIBLE` file lacks an exact TASK-08 version relationship;
- a linked version is missing or belongs to another Bid;
- any current directive lacks `INCORPORATED` or `NOT_APPLICABLE` disposition;
- an internal waiver is presented as customer incorporation;
- a selected release belongs to another Bid;
- a prior incorporated release is silently dropped; or
- the expected predecessor is stale.

An approval-backed internal waiver is valid only for an internal administrative control. It remains
non-incorporating and therefore cannot resolve a customer directive. Required acknowledgement does
not prevent internal review or snapshot creation. It blocks OPS-09 proposal issue until a later
`ACKNOWLEDGED` event or an approval-backed `EXCEPTION_APPROVED` event supersedes it.

Snapshot construction carries forward prior documents, then applies selected releases in explicit
sequence. A newer exact version of the same TASK-08 logical document replaces the older version in
the successor snapshot. Historical snapshots and their members remain unchanged.

## Managed intake storage

The default is `COPY_MANAGED_ONLY`. Configuration is:

- `CONTRACTIQ_INTAKE_ROOTS`: authoritative allowlisted parent inbox. It accepts one plain absolute path,
  or multiple named absolute roots as a JSON object; `CONTRACTIQ_INTAKE_SOURCE_ROOTS` is a compatibility
  alias only and cannot be set together with the authoritative variable;
- `CONTRACTIQ_INTAKE_STORAGE_ROOT`: separate managed-intake root;
- `max_intake_outer_files`: default 1,000;
- `max_intake_total_bytes`: default 1 GiB;
- `max_intake_file_bytes`: default 250 MiB;
- `max_intake_relative_path_length`: default 2,048 characters; and
- `intake_quarantine_days`: default seven days.

If no source mapping is configured, the deployment-local database directory receives one default
landing location. Browser choices expose “Configured intake location N”, never absolute paths.

Preview recursively inventories regular files without database, managed-storage, source, audit or
in-memory workflow mutation. It rejects an unknown alias, symlink at any traversed component,
non-regular entry, unsafe/overlong relative path and count/individual/aggregate limit excess. It
hashes every source file and computes a canonical release fingerprint. Archives are ordinary
inventoried files; nothing opens or expands them.

The confirmation token is opaque, HMAC-bound to the Bid, source alias, form values, fingerprint and
server operation ID. Confirmation re-previews the source. A source change fails before staging.
Every file receives a deterministic stable internal ID. Copying uses no-follow source opens,
server-controlled `.bin` names, exclusive creation, bounded chunks, source descriptor stability,
independent source and destination SHA-256/size comparison, file `fsync`, and directory `fsync`.

Publication sequence:

1. copy all files into a same-filesystem operation-owned staging directory;
2. begin an immediate SQLite transaction and recheck the operation ID;
3. insert release, file, safe-default disposition and verification rows;
4. atomically rename the fully fsynced release directory into `releases/<opaque-release-id>`;
5. insert audit and commit; and
6. on any post-publication database failure, roll back and remove only that exact publication.

Filesystem publication failure rolls back every database row. Pre-publication failure moves the
staging directory to a seven-day quarantine. Startup quarantines interrupted staging and any opaque
published directory without a committed release, prunes expired quarantine, parses no document, and
prints no original filename. Reverification is append-only, covers every file, and replays by
operation ID without changing physical evidence.

The consistent backup/recovery unit is SQLite, existing controlled-document storage and managed
intake storage from one checkpoint. Database-only or either-storage-only backup is incomplete.

## Physical files and processing decisions

`bid_received_files` contains only server ID, release FK, original relative path/name, extension,
detected media type, size, SHA-256, opaque managed key, registration actor/time and provenance. It
contains no mutable classification conclusion.

The initial disposition is `UNKNOWN`, method `SAFE_DEFAULT`, eligibility `NOT_ASSESSED`, confidence
null. A reviewer appends a superseding event with `TEXTUAL`, `DRAWING`, `MIXED` or `UNKNOWN`, method,
optional confidence, `ELIGIBLE`/`EXCLUDED`/`NOT_ASSESSED`, required exclusion reason when excluded,
and optional exact-byte duplicate file. A PDF is never inferred to be a drawing from media type.

No Office macro, spreadsheet formula, embedded object, archive member or document parser executes.
OPS-11B adds no dependency and makes no subprocess or network call.

## Proposal issue and attention integration

Before intake population, OPS-09 retains its accepted controlled-current-document projection. Once a
Bid has any release notice or received release, its `supporting_documents` projection is the exact
current Bid Basis versions; no basis means no supporting documents. This changes the existing
OPS-09 area hash when the basis changes and stales the existing proposal output through the accepted
staleness mechanism.

The existing OPS-09 assessment also blocks issue for:

- expected notice without a fulfilling receipt or explicit resolution;
- received release absent from the current Bid Basis;
- current basis version no longer current/active;
- unresolved customer directive; and
- outstanding explicitly required acknowledgement.

Portal/email checks are advisory. Different latest recorded portal/email references are visible but
do not automatically block. My Day derives required acknowledgement attention and labels it due or
overdue. My Work creation is a deliberate button; an operation ID replays the same exact FK-backed
relationship instead of creating one item per warning automatically.

## Browser workflow

`Package Intake & Addenda` is the seventh Bid workspace section. Its header shows current basis,
latest known, received, inventoried, analyzed, partially incorporated and fully incorporated
positions, portal/email discrepancy and OPS-09 basis staleness.

The ordinary flow is:

```text
Bid -> Package Intake -> Register release -> Preview -> Confirm managed ingestion
    -> Review files/directives -> Incorporate selected releases -> Publish Bid Basis
```

The page provides release notice and channel-check forms, one common release form for all release
types, release review, file disposition, exact controlled-version link, directive/disposition,
acknowledgement, reverification, deliberate linked work, snapshot publication and direct return to
the Bid. POST/redirect/GET is used on success. Errors render understandable 422/404/409 context with
safe entered values retained. GET routes do not mutate or audit.

The user never enters JSON, internal IDs, managed paths or one file at a time during registration;
never renames or physically merges customer files; and never leaves the Bid to understand status.
Deferred Work AI/checklist/OCR/extraction actions are not advertised.

## Bid Basis Register and CSV

The printable register contains only the selected controlled snapshot:

- Bid ID, customer and project;
- snapshot ID/label/time;
- release references, type, issue date, received date/time and channel;
- exact document number, title, revision and TASK-08 version;
- applicable directive dispositions;
- acknowledgement reference where relevant; and
- controlled requirement exclusions/qualifications sourced to a snapshot version.

It omits absolute paths, hashes, internal-only notes, AI confidence and temporary processing data.
Controlled publication cannot contain unresolved incorporation items. Every CSV text cell is checked
after leading whitespace; values beginning with `=`, `+`, `-` or `@` are apostrophe-neutralized.

## Security, integrity and concurrency

- IDs, actors and authoritative timestamps are server-controlled.
- Provenance is human-confirmed only at explicit human form/service boundaries.
- Every mutation and audit row share a transaction.
- Immutable and append-only records reject update/delete by trigger.
- Correction self-FKs and expected snapshot IDs provide optimistic concurrency.
- Operation IDs provide sequential/concurrent replay protection.
- All relationship commands validate same-Bid FKs; the work link has exactly one checked target.
- Downloads contain Bid, release and file in the route and revalidate all three before path access.
- Managed originals have no static mount and are re-hashed before streaming.
- Input uses parameterized SQL; archive/parser work is absent and bounded by registration limits.
- No external network, automatic customer communication, InEight or runtime LLM is involved.

## Automated and manual acceptance

Synthetic tests cover preview non-mutation, source drift, source/destination hashes, symlinks,
allowlist/path/size/count bounds, inert ZIP receipt, duplicate physical occurrences, sequential and
concurrent replay, filesystem/database compensation, quarantine/restart, immutable evidence,
notice-without-receipt, notice resolution, directive correction, internal waiver, exact TASK-08
links, acknowledgement semantics, My Day/My Work relationships, immutable/stale snapshots,
OPS-09 unincorporated-release blocking and basis-hash staleness, safe CSV, retained HTML context,
Bid-scoped downloads and GET non-mutation.

`scripts/validate_ops_11.py` proves the exact table set, migration marker, triggers, FK/integrity and
runs `scripts/asgi_acceptance_ops11.py`. The socketless acceptance uses only generated synthetic bytes
and measures Bid-context retention, lack of raw JSON/manual URL requirements, GET non-mutation,
source identity and network isolation.

Manual browser acceptance:

1. Point the two intake environment settings to empty non-production test locations.
2. Create/select a synthetic Bid and open `Package Intake & Addenda`.
3. Put a few synthetic files and an inert ZIP into the configured landing location.
4. preview, confirm, and verify that the landing location is unchanged;
5. review every file, exclude the ZIP with a reason, and link eligible files to exact TASK-08 versions;
6. record a release notice, channel check, directive/disposition and acknowledgement;
7. publish a Bid Basis, print the register, and inspect its CSV in a text editor;
8. record another synthetic addendum and observe proposal issue blocking until incorporation; and
9. publish a successor basis and confirm OPS-09 reports changed supporting documents.

Do not use confidential customer files for this browser acceptance.

## Deferred capabilities

- all checklist template/version/instance tables and workflow;
- Work AI/Alice export, import, validation, staging, decisions and promotion;
- content extraction, OCR, mixed-PDF/page classification and drawing metadata;
- archive expansion and nested archive handling;
- additional media libraries, system packages and licensing decisions;
- customer portal/email APIs, watched directories and automatic communications; and
- any post-award execution or OPS-10 capability.

The later controlled real-package pilot may evaluate only the authorized aggregate targets already
documented in OPS-11A. Confidential names, content and hashes must not enter tests, Git, model input or
reports.
