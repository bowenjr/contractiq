# Handoff — OPS-11BZ

## Status

MANUAL ACCEPTANCE RECORDED — PASS WITH DEFERRED USABILITY FINDINGS (2026-09-25)

## Files created

- `docs/tasks/OPS-11BZ-bid-package-review-consolidation.md` (57 lines)

## Files modified

- `app.py` — bulk-review endpoint, retained validation, category-label context, and decision-based review counts.
- `core/bid_package_intake.py`, `core/bid_package_repository.py`, `core/bid_package_service.py` — typed atomic bulk review.
- `core/bid_package_presentation.py` — deterministic customer-folder/root grouping and plain review wording.
- `core/document_control.py`, `templates/documents.html`, `templates/document_detail.html` — business category labels over unchanged values.
- `templates/_package_intake_section.html` — collapsed groups, folder selection, one bulk form, and optional explicit content form.
- `scripts/asgi_acceptance_ops11by.py` and focused OPS-11/document tests — consolidated behavior coverage.

## Test results

`pytest` — 512 passed, 0 failed. A combined `pytest -q` stream detached after partial progress, so the exact 512-test collection was completed in five non-overlapping pytest groups: 211 + 95 + 53 + 49 + 104 passed.

Focused OPS-11BZ/presentation/document-control/UI pytest — 42 passed, 0 failed.

`ruff format` / `ruff check` on changed Python — pass.

`mypy --strict` on the five changed core modules — pass.

## Validation command output

```text
OPS-11 validation: PASS
OPS-11 ASGI acceptance: PASS
OPS-11BX rendered workflow acceptance: PASS
OPS-11BY intake simplification acceptance: PASS
OPS-06 validation / ASGI acceptance: PASS / PASS
TASK-09 validation: PASS
foreign_keys=1 foreign_key_check=0 integrity_check=ok ops11_triggers=36
git diff --check: clean
```

## Decisions I made

- A group is the first slash-separated component of the immutable received relative path; root files have their own group. Ordering is root, then case-folded/original folder and path order.
- A selected no-op row is concurrency-checked but gains no replacement event/audit.
- Bulk review preserves `UNKNOWN` content form unless the user explicitly chooses one; it never infers content from an extension.
- The isolated verification first proved that an all-excluded package cannot publish a Bid Basis, then deliberately included one file and put its managed copy under control before confirming the basis. No source file was uploaded again.

## Deviations from the task spec

None. The combined full-suite terminal stream did not return a final status, so the same complete collection was verified in non-overlapping groups.

## Concerns for review

- The programmatic real-package Bid bypasses normal setup, so its global primary action remains Bid setup. Manual acceptance should begin at ordinary Bid creation to inspect the intended initial primary action.
- Cached Chromium required an existing local library bundle; no browser dependency was installed or added.

## Reporting requirements from the task

### Root causes and implementation

The page rendered immutable evidence as 20–30 independent forms, had no customer-folder projection, and counted unknown file kind as outstanding review. OPS-11BZ adds deterministic grouping and one transactional command while retaining per-file immutable events/audits and concurrency; review counts now use the recorded inclusion decision.

### Real-package and workflow measurements

An authorized 25-file package was copied once to a fresh `/tmp` inbox and ingested from that copy. It produced 4 top-level groups; one bulk submission changed 25 decisions; 0 files awaited review afterwards; 24 were excluded and one was placed under document control from its managed original before confirming a Bid Basis.

The common-disposition review is now **4 folder-select interactions + 1 bulk submission**, rather than **25 individual review submissions**. Folder disclosure causes 0 page departures. No source file was uploaded again. The prior OPS-11BY 25-file page was 13,157 px at 1366×768; the final collapsed review route is 1,907 px (2.48 viewports) at 1366×768 and 1,891 px at 1920×1080, with no horizontal overflow.

### Browser inspection

Headless Chromium inspected the isolated review route at 1366×768 and 1920×1080. There was no horizontal overflow and the collapsed page met the three-viewport target. Screenshots remain only under `/tmp`; no package filename, customer data, text or hash entered Git.

### Database, source and protected-file integrity

- Fresh initialization/restart: foreign keys on, no foreign-key violations, integrity `ok`, 36 OPS-11 immutability triggers.
- Source: 25 files; normalized manifest and per-file byte comparisons matched the temporary copy after testing. `/home/bowen/Substation` was never an intake root.
- `.claude/settings.local.json` SHA-256 remains `3df3b79e09c09bec3c2f4f008fa9446f76fe54f6c4432665bd0f0bcf34ab188c`; `uv.lock` remains `7ca4f65818245285964603f5eaf1eab0c48a51be9f8c9195d64844cdd684d672`; `data/contractiq.db` remains absent. No recovery reference or stash was touched.

### Deferred OPS-11C capabilities

No content-form proposal, document parsing, OCR, AI classification, drawing recognition, title inference, requirement extraction or addendum-impact analysis was added. Future classification must be content-based and human-confirmed.

### Exact manual runtime command

```bash
TEST_ROOT=/tmp/contractiq-ops11bz-manual
mkdir -p "$TEST_ROOT/intake-inbox" "$TEST_ROOT/documents" "$TEST_ROOT/managed-intake"
CONTRACTIQ_DB_PATH="$TEST_ROOT/contractiq.db" \
CONTRACTIQ_DOCUMENT_ROOT="$TEST_ROOT/documents" \
CONTRACTIQ_INTAKE_STORAGE_ROOT="$TEST_ROOT/managed-intake" \
CONTRACTIQ_INTAKE_ROOTS="$TEST_ROOT/intake-inbox" \
uv run python -m uvicorn app:app --reload
```

### Manual acceptance checklist

1. Create a Bid and confirm **Import customer bid package** is the only primary next action.
2. Import root and nested customer folders; confirm safe groups/counts and the first action-needed group open.
3. Select all in a folder, choose Include/Exclude/Not yet decided, and submit once.
4. Confirm selection controls do not navigate and untouched files have no new history.
5. Handle one individual exception, then put one file under document control without uploading it.
6. Confirm the current Bid basis and inspect both viewport sizes for clipping and competing actions.

OPS-11BZ STATUS: AWAITING MANUAL BROWSER ACCEPTANCE (superseded below)

## Manual acceptance record

OPS-11BZ MANUAL ACCEPTANCE: PASS WITH DEFERRED USABILITY FINDINGS

- Acceptance date: 2026-09-25
- Accepted implementation checkpoint: commit `8e7b2fb` ("WIP ops-11: bid package intake + addendum control (pre-fork checkpoint)"), on `ops-11-bid-package-intake-addendum-control`.
- Verification evidence for this checkpoint is the "Test results" and "Validation command output" sections above; no additional test run was performed to produce this acceptance record.

Accepted functional behaviour: initial bid-package ingestion, configured-folder discovery/preview, managed-copy ingestion, folder-grouped bulk review, controlled-document creation from the managed original, addendum recording and Bid Basis controls, and persistence/restart behaviour all work. No functional or evidence-integrity blocker was found during manual testing.

Deferred usability findings (accepted usability debt, not authorization to change OPS-11 during publication):

1. Adding and incorporating addenda needs a simpler, more guided workflow.
2. Package processing remains clunky and difficult to navigate.
3. Jason will document the desired workflow changes before further redesign.
4. No further OPS-11 workflow redesign is authorized by this acceptance record.

## Branch topology note

The local-LLM development line is maintained separately on branch `local-llm` (worktree `contractiq-local`). The canonical/original branch (`ops-11-bid-package-intake-addendum-control` and its successors) must not receive local-LLM experimentation.

OPS-11BZ STATUS: MANUAL ACCEPTANCE PASS WITH DEFERRED USABILITY FINDINGS — 2026-09-25
