# OPS-11BZ — Bid Package Review Consolidation

Status: implemented; awaiting manual browser acceptance

## Purpose

OPS-11BY made individual received-file review understandable, but a routine customer package with
20–30 files still rendered one repetitive review form per file. OPS-11BZ retains the immutable
receipt, managed-copy, provenance, audit, addendum and Bid Basis controls while making a common
review decision a folder-level operation.

## Implementation

- Routine wording is **Include in Bid basis review?** with the stored
  `NOT_ASSESSED` / `ELIGIBLE` / `EXCLUDED` values presented as **Not yet decided**, **Include** and
  **Exclude**.
- `group_received_files()` projects immutable received paths into deterministic top-level customer
  folder groups, with an explicit **Files at package root** group. Each group has total, awaiting,
  included, excluded and controlled-document counts. Groups are native collapsed `<details>`;
  only the first group needing work opens initially.
- A release-wide bulk form can select individual files or select/clear one displayed folder without
  a request. It accepts a common eligibility decision and an optional explicit common content form.
  It never proposes a form from an extension.
- `BulkFileDispositionCreate` carries every selected file and its current immutable disposition
  token. `add_file_dispositions_bulk()` starts an immediate SQLite transaction, validates the Bid,
  release, non-empty unique selection and every current token before its first insert, then writes
  one immutable disposition event and one audit event for each changed file. A stale, missing,
  cross-Bid, cross-release, duplicated or audit-failing selection rolls back all rows. Selected
  no-op rows are checked but gain no new event.
- Controlled-document categories retain their persisted enum values and now render through plain
  business labels in the intake control form and document register/detail pages.
- The workflow projection now counts files awaiting review from the recorded eligibility decision,
  not the unknown content-form placeholder. A bulk exclusion therefore clears the review count
  without asserting a document kind.

## Boundaries retained

No migration, dependency, parser, OCR, AI classification, extension-based content conclusion or
new authoritative domain was added. The received copy is still the source for the existing one-step
**Put under document control** operation; no routine path uploads it again. Addendum directives,
acknowledgements, technical hashes and custody evidence remain conditional/advanced as established
by OPS-11BY.

## Verification scope

Focused coverage exercises deterministic grouping, root and special-character folders, folder
selection controls, mixed selected/unselected rows, atomic success, stale/cross-Bid/cross-release/
duplicate rejection, injected audit rollback, empty selection, retained HTML validation, immutable
event/audit cardinality, no-op behavior, category labels, lack of extension assertion and primary
workflow presentation. The existing OPS-11 ASGI and validator suite, directly affected OPS-06 and
OPS-09 validators, fresh initialization/integrity checks and the full test collection also run.

## Deferred to OPS-11C

Content-form proposals, document-purpose classification, OCR, extraction, drawing recognition,
title inference from contents, requirement extraction and addendum-impact analysis remain deferred.
Any future proposal must be content-based and human-confirmed; file extension remains insufficient.
