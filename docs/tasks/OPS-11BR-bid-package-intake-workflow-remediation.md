# OPS-11BR — Bid Package Intake Workflow Remediation

**Status:** IMPLEMENTED — awaiting manual browser acceptance

## Root cause

OPS-11B established secure, immutable package control but led the normal browser experience with
release-notice and administrative controls. It treated the configured intake root as the package
selection boundary, so a user could not begin with the folder of files they had received. Manual
acceptance correctly found that operationally backwards.

## Corrected workflow

```text
Open Bid
  → Package Intake & Addenda
  → Import initial bid package
  → Select an available intake folder
  → Preview folder and file inventory
  → Confirm managed ingestion
  → Review received files and directives
  → Import addendum or revised package with the same workflow
  → Publish current Bid Basis
```

No release notice or portal/email check is required for initial-package receipt.

## Bid governance remediation

Bid creation now calculates and applies the existing deterministic minimum governance level without
requiring the user to repeat that selection. The Bid detail records the selected level and its stored
rationale. A user may first calculate the level and voluntarily select only a stricter level with a
rationale. Lower selections are not displayed as valid stricter choices and server-side validation
continues to reject them. No downgrade mechanism, classification engine, historical-Bid rewrite or
audit change was introduced.

## Inbox selector boundary

`BidPackageStorage` now treats each configured intake source as a parent inbox. The routine browser
lists only safe, immediate, non-symlink child directories. For each it presents a safe folder name,
file count, total size, last-modified summary and whether its current exact fingerprint already
matches an earlier receipt for the Bid. It never renders source absolute paths.

The selector:

- does not accept arbitrary paths;
- rejects traversal and symlinks;
- does not follow source symlinks or serve source files;
- refreshes through GET without database/audit mutation;
- shows a setup message when files are placed directly in an inbox instead of a package folder; and
- leaves current source-root-only service use compatible for non-browser legacy tests.

The selected immediate folder is carried in the protected preview confirmation token. It is used for
the existing source-drift check, hashing, fsync staging, atomic publication, compensation,
idempotency and audit. The configured root alias remains the persisted source-root authority; no
schema change was required.

## Authoritative intake configuration

`CONTRACTIQ_INTAKE_ROOTS` is read while `app.py` constructs the application and storage service, before
the ASGI application starts serving requests. A single plain **absolute** parent-inbox path is the normal
configuration. Multiple parent inboxes use a JSON object of safe aliases to absolute paths, for example
`{"primary":"/srv/contractiq/intake-a","secondary":"/srv/contractiq/intake-b"}`. There is no
delimiter format. `CONTRACTIQ_INTAKE_SOURCE_ROOTS` remains a compatibility alias with exactly the same
formats; setting both variables is rejected. An explicit empty, relative, missing, unreadable, non-directory
or symlinked root never falls back to a different inbox: startup or the read-only selector reports a safe
configuration error.

Folder discovery rereads the filesystem on every request. An immediate child folder is selectable when at
least one recognised regular file exists anywhere in its bounded hierarchy. DOCX and XLSX are recognised;
ZIP is shown and later retained as an inert inventory file, never expanded. Empty and unsupported-only
folders remain visible with an explanation rather than disappearing.

## Browser changes

The landing page now has a compact operational summary of current Bid Basis, latest receipt, latest
known addendum, unincorporated releases, expected/missing releases, review work, unresolved
directives, acknowledgement attention and proposal-readiness impact.

Before any receipt, the primary action is **Import initial bid package**. Afterwards it is **Import
addendum or revised package**. Both use the same selector, preview and managed-confirmation routes.
The preview shows selected folder, nested folders, relative files, detected types, byte sizes,
potential exact-byte duplicates, archive retention, unidentified file types and warnings. It does
not expose hashes or managed-storage data as the primary operating view.

Release notices are reframed under **Missing something? Record expected or missing addendum**. They
remain authoritative for missing-release readiness when deliberately recorded. Portal/email-check
history and its compatibility route remain available only for historical/back-end compatibility; the
routine UI no longer offers that control. Acknowledgements, technical evidence and correction
history are secondary/collapsed controls.

Business-language definitions explain received package, addendum, incorporated, Bid Basis and
expected/missing addendum. Work AI, OCR, extraction, archive expansion and checklist controls are
not advertised.

## Security and authority retained

- 18-table OPS-11 migration and immutable/append-only evidence remain unchanged.
- Exact TASK-08 version links, source-drift rejection, stale confirmation rejection and replay
  idempotency remain unchanged.
- Database/filesystem compensated publication, hashing, fsync, quarantine/recovery, audit,
  optimistic concurrency, formula-safe export and Bid-scoped downloads remain unchanged.
- OPS-09 continues to use the existing readiness and supporting-document staleness mechanisms.
- No GET route creates evidence or audit data. No external network, InEight or direct Work AI
  integration is used.

## Synthetic manual runtime

Use a parent inbox, not a selected package as the configured root:

```text
TEST_ROOT=/tmp/contractiq-ops11br-manual
CONTRACTIQ_INTAKE_ROOTS=$TEST_ROOT/intake-inbox
CONTRACTIQ_INTAKE_STORAGE_ROOT=$TEST_ROOT/managed-intake

$TEST_ROOT/intake-inbox/Initial-Package/
$TEST_ROOT/intake-inbox/Addendum-01/
```

Place only synthetic files beneath those two package folders. Do not use confidential customer files.

## Manual browser acceptance checklist

1. Create a synthetic Bid and confirm the calculated governance minimum is applied on normal create.
2. Confirm optional calculation offers only stricter governance choices and preserves an invalid
   lower attempt with an understandable error.
3. Open Package Intake, confirm **Import initial bid package** is the primary action, and confirm no
   portal/email check control appears.
4. Open the selector, refresh it, and confirm the two child folders are visible without absolute
   paths or audit changes.
5. Select `Initial-Package`, preview nested inventory, then confirm ingestion. Verify source bytes
   remain unchanged.
6. Review files, record exclusions/directives as needed, link exact controlled versions and publish
   an eligible Bid Basis.
7. Confirm the landing action becomes **Import addendum or revised package**, then ingest
   `Addendum-01` through the same selector and confirmation route.
8. Confirm a recorded expected/missing addendum is visible and affects readiness only when used.
9. Confirm the Bid Basis Register/CSV and OPS-09 staleness/readiness behaviour remain controlled.

## Automated coverage

Focused tests cover automatic/higher/lower governance behaviour; initial intake without notice or
channel check; safe folder listing; direct-file inbox setup guidance; symlink/traversal rejection;
refresh/preview non-mutation; nested preview; confirmation; duplicate/import identification;
addendum reuse of the same engine; Bid context; raw-path suppression; source-drift and existing
OPS-11 integrity/readiness/audit/export behaviour. The OPS-11 ASGI acceptance executes the actual
folder-selection, initial-ingestion, review/link, basis-publication and addendum-ingestion flow.

## Deferred

Checklist persistence, Work AI/Alice exchange, extraction/OCR/drawing analysis, archive expansion,
customer portal/email APIs, watched folders, external synchronization and post-award/OPS-10 work
remain deferred. The confidential pilot package remains outside automated tests and was not accessed.
