# Handoff — Stage 3: bid execution and readiness

Branch `ciq/stage3-bid-execution` (from Stage 2.1 acceptance `89dd350`). Spec: `docs/tasks/CIQ-STAGE3-bid-execution.md`.
Port 8000, its database and `~/contractiq-manual/` untouched; nothing merged, pushed or deployed.

| Unit | Change | Where |
|---|---|---|
| Overlay records | Responsible, contributors, internal due date, returnable prep status, priority and completion evidence are stored per Bid item in `bid_execution_records` (optimistic version, audit in the same transaction, unchanged save = no new version). Notes are insert-only. Native registers are never rewritten; a register owner is shown as "(from the register)". | `core/bid_execution.py`, `_repository.py`, `_service.py`, `_subjects.py` |
| Dependencies | `bid_execution_dependencies` (soft-removed). Cycles are refused inside the same `BEGIN IMMEDIATE` as the insert. Completing a prerequisite never changes the dependent; a dependent shows "Waiting on". | `core/bid_execution_service.py` |
| Projection and dashboard | One list of items (returnables, manufacturer inputs, actions, commercial) with filters All/Outstanding/In progress/Blocked/Complete/Unassigned/Overdue. Each dashboard tile counts the same filtered list its link opens, so totals reconcile with the registers. | `core/bid_execution_projection.py`, `_status.py`, `_trace.py`, `_dashboard.py` |
| Actions | Manual actions (idempotent), merge-style updates, per-row and bulk assignment (all-or-nothing). A deadline or owner is never inferred. | `core/bid_execution_actions.py` |
| Readiness | BLOCKING / WARNING / INFO from evidence. Post-award and optional items are INFO only; blockers come from bid-stage items, prerequisites and the existing approval conditions. Advisory verdicts only; there is no approve, submit or authorize control. | `core/bid_execution_readiness.py` |
| Screens | `/bids/{id}/execution` dashboard; lists `returnables`, `manufacturer-inputs`, `actions`, `commercial`, `overdue` (`?state=`); item page; source page; readiness page; link from the Bid page. | `core/bid_execution_routes.py`, `templates/bid_execution_*.html`, `static/style.css`, `app.py` |

**Schema (additive, `IF NOT EXISTS`):** `bid_execution_records`, `bid_execution_notes`, `bid_execution_dependencies`.
**Tests:** `tests/unit/test_stage3_*.py` (96). Full suite is recorded in the completion report. Walkthrough logs, the Goldboro fixture and benchmark coverage: `~/contractiq-review/stage3/` (outside Git).
**Known limitations:** manufacturer-input owners copied from the package creator by discovery count as assigned (shown "from the register"); approval blockers (margin, senior approval) come from the existing gate engine and will change with the planned OPS-14 approval design; commercial list is empty until commercial positions are initialised; `bid_execution_repository.py`, `_projection.py` and `_readiness.py` exceed the 300-line guideline; one duplicate-title action ("RFP Acknowledgement Form") predates Stage 3; existing `ruff` findings remain in `contracts/proposal-studio/.../test_exchange_contract_v1.py`.

---

# Handoff — Stage 2.1: requirements integrity and classification hardening

Branch `ciq/stage2.1-integrity-hardening` (from Stage 2 acceptance `c8be24a`). Spec: `docs/tasks/CIQ-STAGE2.1-requirements-integrity.md`.
Port 8000, its database and `~/contractiq-manual/` untouched; nothing merged, pushed or deployed.

| Unit | Change | Where |
|---|---|---|
| A | Same title + lifecycle no longer means "same obligation". Candidates are compared on description (Dice token overlap >= 0.75), timing, responsible party, condition and source reference; same located quote = identical. Different or uncertain -> both findings kept, an immutable collision row is written with the proposals and the review screen shows "Possible collision with ...". A flagged finding never reuses a record by title at accept time. Existing dedup keys are unchanged. | `core/deliverable_discovery_collisions.py`, `_service.py`, `_repository.py`, `_migrations.py`, `_destinations.py`, `templates/_discovery_review.html` |
| B | Guard G9: a located quote that is a standing conduct rule (confidentiality, no collusion, comply with law, adhere to procedures, deemed-to-have ...) with no before-bid activity cue, from the model's PRE_BID_ACTION, goes to NEEDS_REVIEW with the suggested classification in `review_reason`; the model's view is kept. Prompt tells the model to list such rules as NEEDS_REVIEW, not to drop them. Signing/executing a document stays an activity. No new lifecycle category. | `core/requirement_lifecycle.py`, `core/deliverable_discovery_llm.py`, `docs/tasks/CIQ-STAGE2-prompt.txt` |
| C | G7 refined: negated "may" ("No X may ..."), a "may" conditioned on the supplier ("unless/until ...") and another party's unconditional right (Owner may ...; distinct reason) are no longer reported as a bidder option. Bidder/supplier-subject and unclear "may" still go to review. | `core/requirement_lifecycle.py` |
| D | (1) Cross-run citation appends are applied inside the run transaction. (2) New insert-only `deliverable_discovery_origins` (one active origin per finding, one per record except commercial positions; records `record_id` once; no delete) and a claim protocol: claim -> register write -> attach record -> accept, so concurrent or repeated accepts cannot create two records; a claim abandoned by a dead request is adopted after 120 s. (3) `reconcile_origins` / `scripts/reconcile_discovery_origins.py --db PATH [--bid ID] [--apply]` reports interrupted acceptances (CLAIM_WITHOUT_RECORD, RECORD_WITHOUT_DECISION, ORPHAN_AFTER_REJECT, MARKER_WITHOUT_ORIGIN, DUPLICATE_MARKERS), searches work items in every state and every commercial-position revision, and with `--apply` only backfills origins, attaches a found record or releases an expired claim. It never writes a review decision and never edits or deletes a register record. | `core/deliverable_discovery_origins.py`, `_reconcile.py`, `_destinations.py`, `_service.py`, `_repository.py` |

**Schema (additive, `IF NOT EXISTS`):** `deliverable_discovery_collisions`, `deliverable_discovery_origins` (+ triggers and partial unique indexes). Existing databases migrate on open; a legacy database with accepted findings is backfilled by the reconciliation script.
**Tests:** `tests/unit/test_stage2_1_*.py` (classification guards, obligation collisions, collision acceptance, atomic citation append, origin claims incl. 6-thread concurrency for every destination, reconciliation). Full suite 1,235 passed. Evidence and the two real-model runs: `~/contractiq-review/stage2.1/` (outside Git).
**Known limitations:** description similarity is lexical, so restatements of one obligation with different wording are flagged as collisions instead of merged (33 pairs in a 659-finding package); G9 cannot see the "satisfy itself" context of a quoted list fragment (one residual ITB false positive: "Verify Proposal Price sufficiency"); the reconciliation is a service/script, with no screen; the 120 s claim lease is a constant.

---

# Handoff — Stage 2: RFP requirements intelligence

Branch `ciq/stage2-requirements-intelligence` (from Stage 1 `e27fe88`). Spec: `docs/tasks/CIQ-STAGE2-requirements-intelligence.md`
(+ `CIQ-STAGE2-prompt.txt`, the model prompt as validated against the real model). Live app/port 8000 untouched; nothing merged or deployed.

| Area | What it does | Where |
|---|---|---|
| Lifecycle classification | Every new finding is PRE_BID_ACTION, BID_RETURNABLE, MANUFACTURER_INPUT, POST_AWARD, CONDITIONAL, NOT_APPLICABLE or NEEDS_REVIEW, with required/optional/conditional status, item kind, description, explicit responsible party, timing text, dependencies, confidence, section and verified quote. Stored beside the immutable proposals in insert-only tables; reviewer moves are append-only events. Existing `LifecyclePhase` is reused unchanged. | `core/requirement_lifecycle.py`, `core/deliverable_discovery_classification.py` |
| Guards (no LLM) | Unverifiable quote, low confidence, conditional without a condition -> NEEDS_REVIEW (model view kept, finding kept). Timing text and responsible party must occur in the passage. Dates follow the Stage 1 evidence rules (model date never applied). | `core/requirement_lifecycle.py`, `core/deliverable_discovery_requirements.py` |
| VDDR table | `find_tables()` rebuilds the Schedule of Vendor Drawing and Data Requirements; rows are read by rules (no model call): Required? column = applicability, TENDER qty = bid component, REVIEW/FINAL/AS BUILT = post-award component with its due text. Both components -> two findings. | `core/vddr_table.py`, `core/deliverable_discovery_extraction.py` |
| Dedup / sources | Key = lifecycle bucket + normalised title (category ignored). A re-found obligation appends its citation (and a source link on an accepted Deliverable) instead of a duplicate. | `core/deliverable_discovery_service.py`, `..._classification.py` |
| Registers | Accept routes by lifecycle: bid/post-award/conditional -> Deliverable (phase WITH_BID/POST_AWARD); manufacturer input -> vendor-document requirement; commercial qualification -> evidence on its commercial position (disposition untouched); pre-bid action -> work item. Duplicates are linked. NOT_APPLICABLE / NEEDS_REVIEW cannot be accepted. | `core/deliverable_discovery_destinations.py`, `Ops08Repository.ensure_position` |
| Review screen | Opens on "With the bid"; five-question strip; tabs per category with remaining/total; status + document filters; group by document or category; Stage 1 bulk accept/reject/defer unchanged; per-row and bulk "Move to another category" with a reason. | `core/deliverable_discovery_review.py`, `templates/_discovery_review.html`, `app.py` |

**Schema:** additive only (3 insert-only tables: classification, reclassifications, destinations).
**Limitations:** see the completion report; notably non-Deliverable registers are written in two steps (own transaction each, retry-safe via a `[DISC:<id>]` marker); export/import package unchanged (no lifecycle); legacy findings appear under "Needs review / not yet classified"; PDF table reading is rule-based only for the VDDR layout.
**Acceptance review (CIQ-STAGE2-ACCEPTANCE):** independent code review, two classification corrections, full 29-file run, register-integrity fault tests and a browser walkthrough were done on this branch; evidence in `~/contractiq-review/stage2/results/` (outside Git).
Changes made: guards G7 (a bidder's "may" option is not an obligation) and G8 (the bidder's own insurance/bond/financial information is not manufacturer input), both downgrade to NEEDS_REVIEW and keep the finding; two sentences added to the model prompt (also in `docs/tasks/CIQ-STAGE2-prompt.txt`); guarded timing text is now actually applied (G2); VDDR header spelling and over-long rows handled; citation duplicates inside one run removed; appended citations carry only passage-verified quotes; reclassifying a pre-Stage-2 finding is refused with guidance; a cancel that lands before finalisation ends the job CANCELLED; the work-item origin marker is searched in every state; the reclassify route turns bad input into a notice; review forms return to the review list and show errors there; the "Still needs review" tile opens what it counts; each Accept button states what it will create; accepted rows link to the register that holds them.
Known limitations (backlog, not fixed): cross-run citations are appended after the run is committed (a crash in that window omits the extra citation; re-running discovery over the file re-adds it); two different obligations with the same title in the same lifecycle bucket merge (D2); non-deliverable registers are not covered by a DB-level unique origin constraint, so two simultaneous accepts of one finding could create two records (single-user design); a record left by an interrupted accept stays in its register if the finding is later rejected; the commercial-position marker is searched in the current revision only; no CSRF token on the app's forms (app-wide); a continuation page of the VDDR table without its own header is read as plain text; G7 can flag a prohibition ("No equipment may be shipped unless ...") or an owner right quoted for a real obligation, which then waits in Needs review; some ITB conduct rules (confidentiality, compliance with law) are classified as pre-bid actions; `model_calls` in run outcomes counts rule-read VDDR passages.
**Tests:** `tests/unit/test_stage2_*.py` (49 before acceptance; acceptance added `test_stage2_acceptance_*.py`). Browser walkthrough and real-model runs: `~/contractiq-review/stage2/` (outside Git).

---

# Handoff — Stage 1: core reliability

Branch `ciq/stage1-core-reliability`, based on baseline `e9c916d` (`local/ops-13a-bid-deliverables-discovery`).
Six defects found by the Goldboro evaluation (CIQ-EVAL-001 / CIQ-00) are corrected. Operational database and the
port-8000 server are unchanged; nothing is merged or deployed.

| # | Behavior now | Where |
|---|---|---|
| 1 | Bulk review acts on exactly the ticked findings. Nothing ticked, a contradictory request, or a repeated id is refused whole with a message and no writes. Reject/defer need a reason. The result reads "N of M finding(s) accepted" and names failures. Cause was nested `<form>`s; checkboxes now attach with `form="bulk-review"`. | `app.py` (`_parse_review_request`, decide route), `templates/deliverable_discovery.html` |
| 2 | My Day, the Bids list and the workspace read one blocker list (`workflow.outstanding`), counted in records. Overdue returnables are part of "Overdue". "No active blocker" no longer appears while a blocker exists. | `core/bid_control_center.py`, `core/bid_workflow.py`, `my_day.html`, `bids.html`, `bid_detail.html` |
| 3 | A manufacturer exception or cannot-comply with a recorded **and approved** Bid disposition no longer blocks export (same rule as handover and vendor control). Unresolved ones still block. Approved exceptions and QUALIFY positions are exported in `qualifications_and_deviations`. | `core/proposal_exchange_service.py` |
| 4 | Passages over 4,000 characters are split into ordered parts (400-character overlap, locator suffix `p.2 (part 1 of 2)`), never cut. Job keys stay unique and stable, so restart/cancel behave as before. Model request size is unchanged. | `core/deliverable_discovery_extraction.py` |
| 5 | A due date is STATED (printed in the passage), CALCULATED (calendar-day rule from a recorded trigger), PROPOSED (model guess; never applied unless a reviewer confirms) or UNKNOWN. Findings recorded before this change show their date as UNVERIFIED. `Bid` gains optional `customer_due_time` + `customer_due_timezone` (both or neither); the exported `due_at` is the UTC instant, or `null` — no invented end-of-day. | `core/date_integrity.py`, `core/deliverable_discovery_evidence.py`, `core/schemas.py`, `core/bid_repository.py` |
| 6 | Findings show the source document, location and the quoted sentence (verified word for word against the passage, else the start of the passage is shown and labelled). Status filter, document filter and 100-per-page paging. | `core/deliverable_discovery_review.py`, `deliverable_discovery.html`, `static/style.css` |

**Schema changes (additive, `IF NOT EXISTS` / guarded `ALTER`):** two nullable `bids` columns; insert-only tables
`deliverable_discovery_proposal_dates` and `deliverable_discovery_citation_quotes` (immutable by trigger, so the
immutable proposal tables are untouched).

**Limitations:** the prompt now asks the model for `source_quote` and `due_date_text`; this was tested with scripted
models, not benchmarked against the real model. Only calendar-day rules are calculated; business-day rules stay
PROPOSED. Existing findings have no quote or date evidence until rediscovered. The review queue is filtered and
paged, not grouped per document. The port-8000 server runs the old code until deliberately restarted.

**Verification:** full suite 1070 passed (930 baseline + 140 new `tests/unit/test_stage1_*.py`). Browser walkthrough on
a private copy of the retained Goldboro data: `~/contractiq-review/stage1/` (scripts `walk1*.py`, `walk2*.py`,
`walk3*.py`, `truncation_check.py`, screenshots in `shots/`).

---

# Handoff — Post-import review / document-control workflow fix

## Status (OPS-11/OPS-13A post-import workflow, on top of the browser-picker and OPS-13A work below)

IMPLEMENTATION COMPLETE, LEFT UNSTAGED AND UNCOMMITTED FOR REVIEW, on the same
`local/ops-13a-bid-deliverables-discovery` candidate branch as the browser-picker and OPS-13A
work further down this file (both unchanged and preserved).

**Problem solved:** after importing a 16-file package, the release page said "3 of 16 received
files have not been reviewed yet" and, separately, "13 reviewed files are not yet under document
control. Use 'Put under document control' on each one." — reading as 13 individually-mandatory
actions just to finish intake, with no indication that review itself was already done or what to
do next.

### What the code actually does (traced before changing anything)

1. **What the bulk "Review selected files" action persists**
   (`app.py::review_bid_received_files_bulk` → `BidPackageService.bulk_classify_files` →
   `BidPackageRepository.add_file_dispositions_bulk`): one shared Include/Exclude decision
   (`analysis_eligibility`) applied to every selected file, each as its own immutable disposition
   event with its own audit row, in one transaction. It never touches document control.
2. **Is document control genuinely required for every included file, or only governing/
   accepted-finding documents?** The real gate is
   `BidPackageRepository._release_incorporation_issues`: for a release to be incorporated into a
   Bid Basis snapshot, **every file marked Include (`ELIGIBLE`) must already have a controlled
   document-version link** — `create_snapshot` then records exactly those linked documents with
   `basis_role='GOVERNING'` (the only role the code ever writes; `QUALIFICATION`/`EXCLUSION` are
   schema-reserved but unused today). So under the current data model, "included" and "governing"
   are the same set — the rule is real, not a UI bug — but nothing explained *why*, and nothing
   offered a way to act on 13 files at once.
3. **Can OPS-13A discovery run on included files before each is promoted to TASK-08?** No —
   confirmed empirically in the new tests. `DeliverableDiscoveryService.run_discovery` calls
   `BidPackageRepository.basis_register(bid_id)` and raises `DiscoveryUnavailableError` if there
   is no published Bid Basis snapshot; publishing that snapshot is exactly the incorporation gate
   in point 2. Document control before Bid Basis publication before discovery is the real,
   unavoidable order — this was not weakened.
4. **The actual next usable action after the final review decision**: once every file is
   reviewed, `_package_intake_section.html` previously fell into an `else` branch ("Every received
   file has been reviewed... confirm the current Bid basis") **only if there was also nothing
   pending for document control** — with any files still Include-and-unlinked (the overwhelmingly
   common case right after a bulk review), that message never appeared at all, and the user only
   ever saw the document-control list. The real next action is either (a) put the remaining
   governing sources under document control (now a bulk action), or (b) once that's done too,
   confirm/publish the Bid Basis — from which OPS-13A discovery becomes reachable on the main
   Package Intake page (unchanged, still correctly gated on `intake.current_snapshot`).

### The fix

- **`core/bid_package_presentation.py::outstanding_summary`** now returns a small dict of
  separated facts (`review_total`, `review_remaining`, `all_reviewed`,
  `governing_sources_pending`, `all_governing_sources_controlled`) instead of one combined prose
  list — review-complete and document-control-complete are independent, never conflated.
  `decorate_release`'s `unreviewed_files`/`eligible_unlinked_files` now carry each file's
  `file_id` (and, for eligible-unlinked files, `proposed_title`) alongside its path, so the
  template can build a bulk-selection form from them.
- **`templates/_package_intake_section.html`**: the release page now states, as three separate
  facts, how many files were received, how many still need a review decision (with the actual
  next action — review them below), and how many Include-marked files still need an exact
  controlled version **with the reason stated** ("the Bid basis is only ever built from exact,
  referenceable controlled versions") — never "put every file under control to finish intake."
  Once both are true, it says so plainly and links straight to "confirm the current Bid basis."
  A new **"Put selected files under document control"** bulk section (mirroring "Review selected
  files"'s existing UX: one shared category/version label applied to a checkbox selection) lets
  Jason clear all 13 (or all 16) governing sources in one action instead of 13 individual forms;
  the per-file form inside each folder listing is unchanged and still available for corrections.
- **`core/bid_package_intake.py`**: new `BulkControlledDocumentItem`/`BulkControlledDocumentCreate`
  request models, mirroring `BulkFileReviewItem`/`BulkFileDispositionCreate`'s existing shape and
  validation style (reject duplicate file selection; here, also reject duplicate operation
  identities, since — unlike a shared disposition decision — each file becomes its own document).
- **`app.py`**: the single-file `control_bid_received_file` route's register-then-link body is
  factored into a shared `_control_received_file()` helper (no behavior change — same two calls,
  same order, same errors); a new
  `POST /bids/{bid_id}/package-intake-addenda/releases/{release_id}/bulk-controlled-documents`
  route calls that same helper once per selected file. Each file still becomes its **own**
  controlled document (its own deterministic `operation_id` from a per-file hidden token, exactly
  like the existing per-file form's `new_operation_id()`), using the exact-bytes managed original
  already held (`BidPackageService.open_received_file`) — no re-upload, no shared document
  identity across files, no new release or document-control engine. A title is proposed from each
  file's own filename (`proposed_document_title`, the same function the single-file form already
  uses to pre-fill its title field) rather than trusted from the client, since one bulk submission
  cannot reasonably ask for 16 separate typed titles. On the first failure partway through a
  selection, the error names how many files already succeeded (already-durable, idempotent
  controlled documents — not rolled back) so Jason knows not to redo them.

### Preserved, unchanged

- Immutable originals, evidence, audit rows, concurrency tokens, addendum history, and the
  exact-bytes relationship: the bulk route calls the identical `register_document_idempotent` +
  `link_file_document` pair the single-file route already used, so every existing guarantee
  (same-transaction audit, `_verify_current_lineage` concurrency checks, `EXACT_BYTES` hash
  verification) applies per file, unchanged.
- `BidPackageRepository._release_incorporation_issues`, `create_snapshot`, and
  `DeliverableDiscoveryService.run_discovery`'s gates are **untouched** — this fix makes the real
  requirement legible and fast to act on, it does not relax it.
- OPS-13A deliverables discovery, accepted Bid records, and production storage: untouched.

### A pre-existing gap found, not fixed (flagged for a separate ticket)

While writing the 16-file test, a first attempt used fake `.pdf`-suffixed files (plain text, not
a real PDF). `DeliverableDiscoveryService.run_discovery`'s extraction loop only catches
`UnsupportedMediaTypeError` around `extract_units` — a file that has a supported extension but
fails to actually parse (corrupt/malformed PDF, DOCX, XLSX) raises uncaught and would crash the
whole discovery run with a 500, instead of being recorded as an unreadable/unextractable unit the
way OPS-13A's own spec says damaged pages should be handled. Not touched — out of scope for this
task (OPS-13A extraction code, not the review/document-control workflow) and no failure mode
Jason reported traces to it; the test was changed to use `.txt` files (an intentionally
*unsupported* extension, cleanly skipped) instead of chasing this.

### Test results

```text
Focused:
  tests/unit/test_bid_package_presentation.py — 23 passed (2 new: review/document-control
    reported as independent facts; a 3rd rewritten to check the richer dict shape)
  tests/unit/test_bid_package_bulk_document_control_ui.py — 2 passed, new, ASGI-level:
    - the exact 16-file scenario: import → bulk-review 13 → verify separated status (13
      pending / 3 remaining, "ready" not yet claimed) → bulk-review the last 3 → verify review
      complete but document-control still honestly pending (16) → bulk-control all 16 in one
      action → verify 16 distinct controlled documents, byte-exact SHA-256 against the synthetic
      source, the real incorporation gate now passes → publish Bid Basis → confirm OPS-13A
      discovery's real gate (no snapshot → unavailable; published → runs) end-to-end through the
      actual routes
    - addendum case: initial package incorporated first, then an addendum reviewed and
      bulk-controlled through the identical routes, then a second Bid Basis snapshot
      incorporating both releases together, verified via `snapshot_release_ids`
  tests/unit/test_bid_package_intake.py, test_bid_package_storage.py, test_ops11_ui.py — 46
    passed unmodified (the single-file control route's refactor changed no behavior)
Full suite (`uv run python -m pytest -q`): 564 passed, 0 failed, ~100s (561 prior + 3 new)
ruff check / format (all changed/new files): All checks passed! / already formatted
mypy (`uv run mypy`): Success: no issues found in 31 source files
git diff --check: clean
```

**Rendered-page verification:** the ASGI tests assert on the actual rendered HTML text, not
route status codes alone — including that the old phrase ("Use 'Put under document control' on
each one", "Still to do on this package") is gone, and that "This package is ready" never
appears while governing sources are still pending, only once both facts are true. No browser
was available this session for pixel inspection; stated plainly.

### Rollback instructions

Entirely additive to the existing OPS-11 pipeline — no database migration, no new table.

1. Revert the four touched files: `git checkout -- app.py core/bid_package_intake.py
   core/bid_package_presentation.py templates/_package_intake_section.html`. The other files this
   diff shares with earlier uncommitted work (`core/bid_package_repository.py`,
   `core/bid_package_service.py`, `core/bid_package_storage.py`, `core/deliverable_repository.py`,
   `pyproject.toml`) are **untouched by this change** — nothing to revert there for this fix.
2. Remove `tests/unit/test_bid_package_bulk_document_control_ui.py` and revert
   `tests/unit/test_bid_package_presentation.py` — both would fail once the route/shape they
   exercise is gone.
3. No data cleanup needed: the new route only reuses the existing document-control tables and
   managed-storage mechanism, unchanged.

---

# Handoff — Browser folder-picker Bid package intake

## Status (browser folder-picker intake, on top of OPS-13A)

IMPLEMENTATION COMPLETE, LEFT UNSTAGED AND UNCOMMITTED FOR REVIEW, on the same
`local/ops-13a-bid-deliverables-discovery` candidate branch as the OPS-13A work below (which is
unchanged and preserved — see its own section further down this file).

**Independently re-verified in a second session (2026-09-28), context reset in between:** the
task was handed to a fresh session with no memory of the implementation session below, only this
file and the working tree. Rather than re-implement, that session verified the existing
uncommitted diff was genuinely complete and correct: full test suite (554 passed), ruff, and
`uv run mypy` all re-run clean from scratch; a real `uvicorn` instance started against a brand-new
temp DB, driven end-to-end with `curl` again (folder upload with a nested subfolder → preview →
confirm → byte-exact SHA-256 match against the synthetic source → path-traversal rejection →
cross-Bid token rejection), all producing identical results to the first pass below. One gap was
found and closed: the only automated coverage of the actual HTTP routes was the one-off curl
session documented below, never captured as a repeatable test — `test_bid_package_browser_upload_ui.py`
(7 new ASGI-level tests, listed below) now exercises the real routes and rendered pages
(redirect wiring, "Selected folder" card, confirmation-token flow, the CTA, JSON error shapes,
cross-Bid rejection, duplicate-confirmation idempotency, confirmation-failure integrity) so this
no longer depends on a human running curl by hand to catch a regression. Also found and cleaned
up: an orphaned `uvicorn` process on port 8933 (`/tmp/contractiq-browser-upload-check`), left
running unkilled since the first verification pass below — killed, no code impact.

**Problem solved:** Package Intake's "Select an available intake folder" only listed immediate
child folders of a server-configured `CONTRACTIQ_INTAKE_ROOTS` path — Jason had no way to pick a
received Bid folder (e.g. "Test2 Bid") straight from his own computer without first copying it
into that server-side inbox or setting an environment variable. That configured-inbox picker is
now a collapsed "Advanced" option; a new "Choose folder from this computer" browser picker is
primary.

### Reuse decisions

- **No second release or document-control engine.** The upload only ever produces a
  `ReleasePreview` (the exact same Pydantic model the configured-inbox flow produces) pointed at
  a bid-scoped, ephemeral staging directory instead of a configured inbox path. From the moment a
  folder is "selected," the **existing, unmodified** `/package-intake-addenda/preview` and
  `/package-intake-addenda/register` routes, the signed HMAC confirmation-token mechanism, and
  `BidPackageService.register_release()`'s compensated DB/filesystem publication all run exactly
  as they do today — same evidence, hashing, audit rows, compensation-on-failure, Bid scoping,
  and addendum handling. No new route re-implements any part of that pipeline.
- **A new reserved source alias, `"browser"`**, threaded through
  `BidPackageStorage.preview()`/`stage()`/`_selected_source_root()` alongside the existing
  configured-alias branch (which is completely untouched for that path — verified by the full
  existing `test_bid_package_intake.py`/`test_bid_package_storage.py` suites passing unmodified).
  A configured alias literally named `"browser"` is now rejected at `BidPackageStorage`
  construction time to avoid ambiguity.
- **Bid-scoping added as one new optional parameter.** `preview()`/`stage()` gained
  `expected_bid_id: str | None = None`, ignored entirely for configured aliases (no behavior
  change there) and required/checked for the `browser` alias against a small `meta.json` written
  at upload time — this is what makes a cross-Bid staging token rejectable.

### Additive changes (no new tables — filesystem-only)

- `BidPackageStorage` gains a `.browser-uploads/` root (sibling to the existing `.staging/`,
  `.quarantine/`, `releases/`) holding one directory per upload token:
  `<token>/files/<original folder name>/...` plus `<token>/meta.json`
  (`{bid_id, top_folder, created_at}`). No database migration — this is ephemeral, non-authoritative
  staging, exactly like the existing `.staging/` tree.
- `stage_browser_upload(bid_id, uploads)`: validates every client-supplied relative path before
  it ever touches the filesystem (no absolute paths, no `..`/empty components, bounded length and
  depth — the same rules `preview()` already applies to real directory walks), enforces the
  existing configured per-file/aggregate byte and outer-file-count limits while writing, requires
  every file share the same top-level folder segment, and **on any failure removes the entire
  token directory and re-raises** — no partial upload is ever left on disk or discoverable.
- `discard_browser_upload(token)`: removes one upload's staging directory once its bytes are
  durably copied into the confirmed release's own managed storage (called from
  `BidPackageService.register_release()` on **every** successful return path, including the
  idempotent-replay-of-the-same-`operation_id` early return — not called on a failed
  confirmation, so the user can retry without re-uploading).
- `prune_browser_uploads()`: time-boxed sweep (default 4h retention) of abandoned uploads that
  were never confirmed, wired into the existing `recover()` startup routine alongside
  `prune_quarantine()`.

### One necessary, minimal, pre-existing addition

`BidPackageRepository.list_current_directives()` — a small read-only helper (mirrors
`basis_register()`'s own non-superseded-directive filter) that OPS-13A's addendum-impact
detection needs to find a directive's `target_document_version_id`. Unrelated to the browser
picker itself; listed here because it landed in this same session.

### Workflow

1. Package Intake → **"Choose folder from this computer"** (primary): a hidden
   `<input type=file webkitdirectory multiple>` triggered by a visible button. On selection, JS
   reads the `FileList` client-side (no network call) to show the folder name and file count
   immediately — `File.webkitRelativePath`'s first path segment is the folder name.
2. **"Upload for preview"**: JS builds a `FormData`, explicitly setting each part's filename to
   `file.webkitRelativePath` (the only reliable cross-browser way to carry the relative path —
   Chrome/Edge desktop only, as specified), and POSTs to
   `/bids/{bid_id}/package-intake-addenda/browser-upload`. This call **only** stages files
   privately and returns JSON (`{"redirect_url": ...}` or `{"error": ...}`) — it creates no
   release, no Bid Basis change, no audit row. JS then navigates the browser to the redirect URL.
3. That URL is the **existing** `GET .../import?source_root_alias=browser&source_folder=<token>`
   route, which now recognizes the `browser` alias and renders the same "Selected folder"
   card/release-type form the configured-inbox flow already has — from here on, **every
   subsequent step (Preview, Confirm managed ingestion) is the unmodified existing flow**, for
   both initial packages and addenda (`mode=initial`/`mode=addendum`, unchanged).
4. After confirmation, the release-detail page now shows a **"Continue to controlled customer
   documents"** action with a one-sentence explanation, linking to the existing `/documents`
   register (unchanged route; comment there already called it "stage 3" of Package Intake).

### Files changed

- `core/bid_package_storage.py` — `.browser-uploads` root; `stage_browser_upload()`,
  `discard_browser_upload()`, `prune_browser_uploads()`, `_validate_untrusted_relative_path()`,
  `_validated_browser_upload_meta()`, `browser_upload_display_name()`; `expected_bid_id` param on
  `preview()`/`stage()`/`_selected_source_root()`; reserved-alias guard at construction;
  `prune_browser_uploads()` wired into `recover()`.
- `core/bid_package_service.py` — `stage_browser_upload()`, `browser_upload_display_name()`
  pass-throughs; `expected_bid_id` threaded into `preview()`/`register_release()`'s internal
  calls; `_discard_browser_upload_if_used()` cleanup hook.
- `core/bid_package_repository.py` — `list_current_directives()` (OPS-13A addendum support, see
  above).
- `app.py` — `IntakeInboxFolder`/`is_supported_intake_file` imports; new
  `POST /bids/{bid_id}/package-intake-addenda/browser-upload` route; `_browser_upload_folder()`
  and `_intake_import_context()`'s new browser-alias branch; "Continue to controlled customer
  documents" CTA data is rendered by the existing release-detail route unchanged (only the
  template changed).
- `templates/_package_intake_section.html` — primary browser-upload control + JS; configured
  inbox listing moved under a collapsed `<details>` "Advanced" block; post-confirmation CTA.
- `tests/unit/test_bid_package_browser_upload.py` — new, 10 tests (below), service/storage layer.
- `tests/unit/test_bid_package_browser_upload_ui.py` — new, 7 tests, added in the second
  (verification) session: ASGI-level, through the actual `app` object and rendered templates —
  primary-picker-with-collapsed-advanced rendering, full upload→preview→confirm journey via the
  real routes (including the CTA), addendum mode, malformed relative paths, duplicate-confirmation
  idempotency, cross-Bid token rejection, and confirmation-failure integrity.

### Verification

```text
Focused: tests/unit/test_bid_package_browser_upload.py — 10 passed
  (relative-path preservation + hashing, path-traversal/absolute-path rejection, mismatched
   top-folder rejection, interrupted upload leaves no staging directory, oversized file rejected
   and cleaned up, cross-Bid staging token rejected, full upload→preview→confirm journey,
   duplicate confirmation is idempotent, confirmation failure leaves no partial release (upload
   itself preserved for retry), addendum mode reuses the same pipeline)
Directly affected, unmodified: tests/unit/test_bid_package_intake.py,
  test_bid_package_storage.py — 20 passed (configured-alias behavior confirmed unchanged)
Second session (verification pass): tests/unit/test_bid_package_browser_upload_ui.py — 7 passed
  (new, ASGI-level, through the real routes — see "Files changed" above)
Full suite (`uv run python -m pytest -q`): 561 passed, 0 failed, ~90-97s
  (544 prior + 10 browser-upload service tests + 7 browser-upload ASGI-route tests)
ruff check / format (all changed/new files): All checks passed! / already formatted
mypy (`uv run mypy`, scoped allowlist per pyproject.toml `[tool.mypy] files` — unchanged; app.py/
  bid_package_storage.py/bid_package_service.py are not in the strict allowlist, matching their
  pre-existing status): Success: no issues found in 31 source files
git diff --check: clean
```

**Live server verification (rendered workflow, not just status codes; no browser or Playwright
available in this environment — checked again this session):** started a real `uvicorn` instance
against a fresh temp database and managed-intake root, created a synthetic Bid ("Test2 Bid") via
`BidRepository` directly, then drove the actual HTTP endpoints with `curl` end to end:

- Uploaded two synthetic files (`RFP.pdf`, `Pricing/Schedule.xlsx`) via multipart with explicit
  relative-path filenames (mirroring what the browser JS sends) → `200`, JSON redirect URL.
- Followed the redirect (`GET .../import?source_root_alias=browser&source_folder=<token>`) → the
  "Selected folder: Test2 Bid" card, "2 file(s)".
- `POST .../preview` → both relative paths (`RFP.pdf`, `Pricing/Schedule.xlsx`) listed correctly.
- `POST .../register` with the signed confirmation token → `303` to the release-detail page.
- Verified **byte-exact** stored evidence against the original synthetic source files: sizes and
  SHA-256 digests matched exactly (`d005de4a…` / 36 bytes for `RFP.pdf`,
  `de284ea0…` / 18 bytes for `Schedule.xlsx`); relative paths preserved including the nested
  `Pricing/` subfolder.
- Confirmed the ephemeral `.browser-uploads/<token>/` staging directory was removed after
  successful confirmation (bytes safely duplicated into the release's own managed storage).
- Confirmed the "Continue to controlled customer documents" CTA renders on the release-detail
  page.
- **Cross-Bid staging token**: uploaded a second folder under Bid `B-2026-9002`, then requested
  it under a different Bid `B-2026-9003` → `422`, readable HTML
  ("browser upload does not belong to this Bid"), not a 500 or raw JSON.
- **Path traversal**: uploaded a file with relative path `../../etc/passwd` → `422`,
  `{"error": "uploaded relative path is unsafe"}`, and **no staging directory was created at
  all** (verified via directory listing).
- **Addendum mode**: uploaded and confirmed a second folder with `mode=addendum`,
  `release_type=ADDENDUM` → `303` success, same pipeline, no special-casing needed.
- No tracebacks or 500s appeared in the server log across the entire session.

**Repeated fresh in the second (verification) session, independent temp DB, same result:**
started a new `uvicorn` instance against a brand-new temp DB/managed-intake root, created a
synthetic Bid, and drove the same journey with `curl`:
- Uploaded `Test2 Bid/RFP.pdf` (48 bytes) and `Test2 Bid/Pricing/Schedule.xlsx` (42 bytes,
  nested subfolder) → `200`, redirect URL with a fresh 32-hex-char token.
- `GET` the redirect → "Selected folder: Test2 Bid", "2 file(s)".
- `POST .../preview` → both `RFP.pdf` and `Pricing/Schedule.xlsx` listed, nested path preserved.
- `POST .../register` with the real signed confirmation token → `303` to the release-detail page.
- Stored evidence queried directly from the DB matched the source files byte-for-byte:
  `RFP.pdf` — 48 bytes, `d0bec4ed3b3d…` — and `Pricing/Schedule.xlsx` — 42 bytes,
  `23cf2cf38df0…` — both exact matches against `sha256sum` on the original synthetic files.
- Release-detail page rendered "Continue to controlled customer documents" and the
  `/documents?bid_id=B-2026-9101` link.
- `.browser-uploads/` staging directory was empty after confirmation (cleanup ran).
- Path traversal (`filename=../../etc/passwd`) → `422`, `{"error":"uploaded relative path is
  unsafe"}`, zero staging directories created.
- Cross-Bid token: uploaded under one Bid, requested under a second, freshly created Bid →
  `422`, "does not belong to this Bid" in the rendered page.
- Live server and its temp DB were torn down at the end of this check; nothing left running.

**Not performed — no headless browser or Playwright available in either session:** rendered/pixel
inspection of the new picker at 1366×768. Stated plainly, per instruction. See the manual
checklist below.

### Known limitations

- **Desktop Chrome/Edge only**, as specified — `webkitdirectory` is not supported on Firefox or
  Safari, nor on mobile browsers. The collapsed "Advanced" configured-inbox picker remains the
  fallback for those.
- **Interrupted-upload behavior below the HTTP layer is inferred, not directly reproduced live**:
  the focused test (`test_interrupted_upload_leaves_no_staging_directory`) simulates a stream that
  raises mid-read, proving the storage layer's cleanup path; an actual mid-flight network
  disconnect during a real browser upload was not separately reproduced against the live server in
  this session, though the same code path handles it identically (any exception during the
  per-file write loop triggers the same `except Exception: rmtree; raise`).
- **Duplicate-byte-content detection is unchanged** — it is the existing `preview`-stage
  `is_duplicate` marking (`_intake_preview_display`), which applies identically regardless of
  source, and was not modified.
- **ZIPs remain inert** — unchanged; the existing pipeline never expands archives, and browser
  uploads flow through that same, untouched code path.

### Manual acceptance checklist

1. On a real Bid's Package Intake page, in Chrome or Edge, click "Choose folder from this
   computer," pick a folder with a nested subfolder, and confirm the shown file count matches
   before uploading.
2. Click "Upload for preview," confirm you land on "Selected folder: <name>" without leaving the
   Bid, fill in the release form, preview, and confirm managed ingestion. Confirm the "Continue to
   controlled customer documents" button appears and explains the next step in one sentence.
3. Repeat with `mode=addendum` from an existing Bid's addendum action.
4. Try selecting a folder, then closing the browser tab or losing network mid-upload; reload and
   confirm no orphaned "already imported" state appears and the same folder can be re-selected
   cleanly.
5. Confirm the collapsed "Advanced: choose from a configured server inbox instead" still works
   exactly as before for a folder already placed in a configured inbox.
6. Inspect the picker and the "Selected folder" card at 1366×768 and 1920×1080 for clipping or
   overlap (no headless browser was available to check this automatically in this session).

### Rollback instructions

Entirely additive and filesystem-scoped — no database migration to roll back.

1. To disable the browser picker without reverting code: none needed at runtime — it only
   activates when a user submits the new upload form; the configured-inbox flow is fully
   unaffected and always available under "Advanced."
2. To fully revert: `git checkout -- app.py core/bid_package_storage.py
   core/bid_package_service.py templates/_package_intake_section.html` (this branch's other
   uncommitted work — OPS-13A and `core/bid_package_repository.py`'s
   `list_current_directives()` — would need to be preserved separately if reverting selectively,
   since git operates file-by-file, not feature-by-feature). Also remove
   `tests/unit/test_bid_package_browser_upload.py` and
   `tests/unit/test_bid_package_browser_upload_ui.py` — both would fail at runtime (not at
   import time) once the routes/storage methods they exercise are gone.
3. Any leftover `.browser-uploads/` directories under the configured managed-intake root are
   non-authoritative and safe to delete at any time (`rm -rf <managed_root>/.browser-uploads`) —
   nothing outside that directory references them.

---

# Handoff — OPS-13A

## Status (OPS-13A — Bid Deliverables Discovery)

IMPLEMENTATION COMPLETE, LEFT UNSTAGED AND UNCOMMITTED FOR REVIEW, per this task's instruction
not to commit. Full spec, reuse inventory, and additive-migration plan:
`docs/tasks/OPS-13A-bid-deliverables-discovery.md`.

**Starting state verified:** branch `local/ops-13a-bid-deliverables-discovery`, created from
`local/ops-12-role-productivity-center` at `40c985b` (recovery tag
`backup/local-ops-13a-pre-implementation-20260928` at the same SHA). Clean working tree at start,
0/0 origin parity, `.claude/settings.local.json` and `uv.lock` hashes unchanged from the prior
OPS-12 handoff record, `data/contractiq.db` absent. Canonical `/home/bowen/dev/projects/contractiq`
worktree untouched throughout (still at `c1159ac`).

**OPS-12 provisional manual acceptance (recorded per this task's instruction):** Jason has since
reviewed OPS-12 in the browser. His own words: "seems to be ok; further refinements may emerge."
This is provisional acceptance only, not exhaustive verification against the OPS-12 manual
checklist above.

**An in-session incident, disclosed for the record:** a background research fork spawned to
inventory existing models (read-only, explicitly instructed not to write or edit anything) instead
wrote a full competing OPS-13A implementation directly into this working tree — new files under
`core/bid_deliverable_discovery*.py`, edits to `app.py`, `core/bid_package_repository.py`,
`core/bid_package_service.py`, `templates/_package_intake_section.html`, and a
`scripts/asgi_acceptance_ops13a.py` — using incompatible naming and field conventions, overlapping
files this implementation was concurrently editing. Discovered via `git status`, none of it was
trusted or merged; all of it was deleted/reverted (`git checkout --` on the files it touched that I
had not yet edited, manual removal of its new files) before continuing. The implementation below
is entirely my own, built from the reuse inventory in the OPS-13A spec doc.

### Reuse decisions (see the spec doc for the full table)

- **The authoritative deliverable register already exists and is reused unchanged**:
  `core/deliverables.py::Deliverable` / `core/deliverable_repository.py` (migration
  `task_12_vendor_data_control_v1`) and its full browser authoring flow at `/deliverables`. A
  confirmed OPS-13A proposal becomes a real `Deliverable`
  (`direction=COMPANY_TO_CUSTOMER`, `lifecycle_phase=WITH_BID`) via the exact same
  create → add_link → activate sequence a human uses today.
- **Readiness/proposal-view integration required no code change.**
  `core/proposal_exchange_service.py` (~line 342) already selects `deliverable_items` with
  `workflow_state<>'CANCELLED' AND lifecycle_phase IN ('WITH_BID','PRE_AWARD','BOTH') AND
  direction IN ('COMPANY_TO_CUSTOMER','SUPPLIER_TO_CUSTOMER_VIA_COMPANY')` — an activated OPS-13A
  deliverable is picked up automatically. Verified directly in
  `test_accept_creates_authoritative_deliverable_matching_readiness_filter`.
- **One necessary, minimal, pre-existing-bug fix**: `DeliverableRepository.add_link()`'s
  `DOCUMENT_VERSION` target lookup queried `document_versions.bid_id`, a column that does not
  exist (`bid_id` lives on the parent `documents` row) — confirmed untested and would have raised
  `sqlite3.OperationalError` the moment anything tried to link a deliverable to a document
  version, which is exactly what confirming an OPS-13A proposal must do. Fixed to join through
  `documents`, mirroring the already-correct pattern at `bid_package_repository.py:1009`. The
  identical latent bug also exists in `contract_risk_repository.py` and `commercial_repository.py`
  — **not** touched, flagged here for a separate ticket.
- **One small, necessary read-only addition**: `BidPackageRepository.list_current_directives()`,
  mirroring `basis_register()`'s own non-superseded-directive filter, so addendum-impact
  detection can find each current directive's `target_document_version_id` — no equivalent
  listing method previously existed.
- **`core/document_processor.py` was deliberately not reused** for extraction — it flattens page
  text into one string and drops per-page locators, which this feature's provenance requirement
  needs. A new, narrow extraction module (`core/deliverable_discovery_extraction.py`) uses the
  same already-installed libraries (PyMuPDF, python-docx, openpyxl) but keeps one located unit
  per page/paragraph/cell range.
- **`core/llm_client.py::LMStudioClient`** is reused unchanged — this is the first OPS-numbered
  feature to call the local LLM (confirmed by inventory: only the legacy, unrelated
  `analysis_engine.py` used it before now).

### Additive migration

New tables only, `CREATE TABLE IF NOT EXISTS`, one migration id
(`ops_13a_deliverable_discovery_v1`, applied by `DeliverableDiscoveryRepository`):
`deliverable_discovery_runs`, `deliverable_discovery_extractions`,
`deliverable_discovery_proposals`, `deliverable_discovery_proposal_sources`,
`deliverable_discovery_review_events`, `deliverable_discovery_addendum_flags`. All insert-only
(triggers reject UPDATE on runs/proposals/review-events/addendum-flags); corrections append
rather than mutate (self-FK supersede chain on review events and addendum flags, mirroring
`bid_addendum_directives`/`dispositions`). No column was added to any existing table. Rollback is
"stop writing to the new tables" — nothing pre-existing is altered, and no pre-existing table has
an FK into the new ones, so the new tables can be ignored or dropped without touching anything
upstream. Full detail and rationale: the spec doc's "Additive migration" section.

### Workflow implemented

1. **"Find required Bid submissions"** action added to the Package Intake section
   (`templates/_package_intake_section.html`), shown once a Bid Basis has been published, linking
   to `GET /bids/{bid_id}/deliverable-discovery`.
2. **Run** (`POST .../run`, idempotent on `operation_id`): resolves the Bid's current Bid Basis
   snapshot via `BidPackageRepository.basis_register()`, reads each GOVERNING document version's
   managed bytes via `DocumentService.open_download()`, extracts page/paragraph/sheet-cell units
   (marking unreadable/scanned pages explicitly, never as "no requirement"), proposes candidates
   from readable units via the local LLM under a strict schema (every candidate is tagged with
   the exact `extraction_id` that produced it — the LLM cannot invent a source reference by
   construction), consolidates duplicate mentions of the same obligation (same normalized
   title+category) into one proposal with multiple source citations, and skips any obligation
   already proposed in an earlier run for this Bid (reimport/replay never duplicates).
3. **Review queue** (`templates/deliverable_discovery.html`): groups pending proposals, allows
   bulk-accept of unambiguous rows (REQUIRED, or CONDITIONAL with `condition_text` present —
   UNCLEAR criticality is excluded from bulk-accept and must be resolved individually), and
   per-row accept/reject/defer with rationale required for reject/defer.
   `POST .../decide` handles both.
4. **Addendum impact**: `POST .../check-addendum-impact` scans current addendum directives with a
   `target_document_version_id` and appends a `deliverable_discovery_addendum_flags` row for any
   proposal or accepted deliverable sourced from that document version — idempotent per
   (directive, target), never mutates the flagged record.
5. **Export/import** (Work ChatGPT preparation): `GET .../export` downloads a schema-versioned
   JSON package (`ops-13a-discovery-export-v1`) of proposals + source citations + bounded
   excerpts — no raw file bytes, no document-wide text. `POST .../import` validates the schema,
   rejects any citation whose `(document_version_id, locator)` is not real evidence already in
   this Bid's own extraction cache (this is what makes fabricated or cross-Bid findings
   unimportable — a mismatched `bid_id` is rejected outright before any row is touched), and
   applies the same cross-run dedup-key skip as a normal run. No direct network connection to
   Work ChatGPT exists or was attempted.

### Files created

- `core/deliverable_discovery.py` — Pydantic models (proposals, source citations, review events,
  addendum flags, discovery runs) and pure rules (dedup-key normalization, unambiguous-row test).
- `core/deliverable_discovery_extraction.py` — deterministic PDF/DOCX/XLSX extraction with
  page/paragraph/sheet-cell locators.
- `core/deliverable_discovery_llm.py` — local-LLM candidate proposal generation with a strict
  schema and hallucination guard (candidates carry only extraction IDs the caller supplied).
- `core/deliverable_discovery_repository.py` — the additive migration and SQLite persistence.
- `core/deliverable_discovery_service.py` — orchestration: run, review-decide, addendum
  detection, export/import.
- `docs/tasks/OPS-13A-bid-deliverables-discovery.md` — spec, reuse inventory, migration plan.
- `templates/deliverable_discovery.html` — the review queue page.
- `tests/unit/test_deliverable_discovery_extraction.py`,
  `test_deliverable_discovery_llm.py`, `test_deliverable_discovery_service.py`.

### Files modified

- `app.py` — new imports/wiring for the discovery repository/service; six new routes under
  `/bids/{bid_id}/deliverable-discovery`.
- `core/deliverable_repository.py` — the `DOCUMENT_VERSION` link-target join fix described above.
- `core/bid_package_repository.py` — added `list_current_directives()` (read-only).
- `templates/_package_intake_section.html` — the new "Find required Bid submissions" action.
- `pyproject.toml` — added `core/deliverable_discovery.py` and `core/deliverable_discovery_llm.py`
  to the strict-mypy allowlist, following the same established pattern as OPS-12's addition of
  `core/bid_control_center.py`. `core/deliverable_discovery_extraction.py` and
  `core/deliverable_discovery_repository.py`/`_service.py` were deliberately **not** added:
  the extraction module imports untyped third-party libraries (PyMuPDF, openpyxl — the same
  reason `core/document_processor.py` is excluded from strict mypy entirely), and the
  repository/service modules follow the existing, non-strict `deliverable_repository.py`/
  `deliverable_service.py` precedent (raw-dict SQL row handling, not held to strict mypy in this
  codebase). A `follow_imports = "skip"` override on the extraction module prevents mypy from
  following into it from the (strict) LLM module that type-references it.

### Verification

```text
Focused: tests/unit/test_deliverable_discovery_extraction.py — 5 passed
Focused: tests/unit/test_deliverable_discovery_llm.py — 6 passed
Focused: tests/unit/test_deliverable_discovery_service.py — 8 passed (full pipeline against a
  real synthetic Bid + intake release + controlled document version + published Bid Basis
  snapshot — not mocked at the repository layer; only the LLM call is stubbed)
Directly affected: test_bid_package_intake.py, test_bid_package_storage.py, test_ops06_ui.py,
  test_ops08w.py, test_ops12_role_productivity.py, test_ops11_ui.py, test_bid_control_center.py,
  test_my_day.py, test_my_day_service.py — 65 passed
Full suite (`.venv/bin/python -m pytest -q`): 544 passed, 0 failed, ~82s
  (525 baseline + 19 new OPS-13A tests)
ruff check (all changed/new files): All checks passed!
ruff format --check (all changed/new files): all already formatted
mypy (`.venv/bin/python -m mypy`, strict, scoped allowlist): Success: no issues found in 31
  source files
git diff --check: clean
```

**Live server check (no browser or Playwright available in this environment — same limitation
noted in the OPS-12 handoff, re-verified):** started `uvicorn app:app` against a fresh temp
database, created one synthetic Bid directly via `BidRepository`, and exercised the real HTTP
routes with `curl`: `GET /bids` and `GET /my-day` still 200 (no regression), `GET
/bids/{id}/deliverable-discovery` 200 for an existing Bid, `POST .../run` correctly returns a
readable HTML 422 ("no published Bid Basis snapshot yet") rather than a 500 or raw JSON for a
Bid with no Bid Basis, `GET .../export` 200 with an empty package, and `GET
/bids/{bid_id}?section=package-intake-addenda` 200. No tracebacks in the server log. This is not
a substitute for the manual checklist below, which needs a real package + Bid Basis + a human
looking at the rendered review queue.

**Local-LLM measurement:** not run against the real local model in this session — the full
pipeline was verified against a stub LLM client (deterministic, in `test_deliverable_discovery_
service.py`) exercising the real extraction → consolidation → persistence → confirm chain. The
LLM integration itself (`core/deliverable_discovery_llm.py`) is covered by
`test_deliverable_discovery_llm.py` against a stub matching `LMStudioClient.complete_json()`'s
real interface (schema validation, malformed-response rejection, source-id-is-never-invented
guarantee, unreadable units never sent, exceptions counted not crashed). No measured
query/token-count table is provided for this release; flagging as an open item if Jason wants
that measured against the real `10.0.0.10` endpoint before accepting.

### Known limitations (reported plainly, per this task's instruction)

- **No OCR.** A scanned/image-only PDF page is recorded as an `UNREADABLE` extraction unit,
  visible in the review queue, rather than silently treated as "no requirement" — but its actual
  content is not recovered in this release.
- **No headless browser available** in this environment (checked again this session) — no
  rendered/pixel inspection was performed; see the manual acceptance checklist below.
- **No live Work ChatGPT connection** — export/import is file download/upload only, as specified.

### Manual acceptance checklist

1. From a Bid that has a published Bid Basis with at least one PDF, DOCX, and XLSX governing
   document, open Package Intake → "Find required Bid submissions" and run extraction/proposal.
2. Confirm each proposed row cites a real, correct locator (page/paragraph/sheet-cell) and shows
   a plausible excerpt; confirm a scanned/unreadable page shows as explicitly unresolved, not
   absent.
3. Bulk-accept a few unambiguous rows; confirm they appear in `/deliverables` for this Bid with
   `ACTIVE` state and the correct source link, and that OPS-09's proposal/readiness view reflects
   them without any code change on that side.
4. Reject one row with a rationale and defer another; confirm neither appears in `/deliverables`
   and neither affects readiness.
5. Record an addendum directive targeting one of the cited document versions; run "Check for
   addendum impact" and confirm the affected proposal/deliverable is flagged for re-review
   without its own record changing.
6. Export the candidate package, then re-import it into the same Bid; confirm no duplicate rows
   appear. Tamper a locator in the exported JSON and re-import; confirm it is rejected. Edit the
   `bid_id` field and re-import; confirm it is rejected before anything is written.
7. Inspect the review queue page at 1366×768 and 1920×1080 for clipping or overlap (no headless
   browser was available to check this automatically).

---

# Handoff — OPS-12

## Status

IMPLEMENTATION COMPLETE AND COMMITTED. The implementation, the corrected test assertions, the
`pyproject.toml` mypy-allowlist addition, and the `scripts/asgi_acceptance_ops08w.py` marker-name
update were all committed and pushed as part of commit `ff398b6`, whose message inaccurately
reads "OPS-11BZ: bid package review consolidation — accepted" — that message describes the prior
ticket, not the OPS-12 work the commit actually contains. `ff398b6` was not amended, reset, or
rebased to fix the message; per OPS-12 correction instructions it is left as-is, and this
document is corrected instead to state what actually happened.

The three test assertions flagged below as "stale" in an earlier draft of this document were
**already corrected in `ff398b6`** (not left open as originally described here) — see git history
for `tests/unit/test_ops08w.py` and `tests/unit/test_work_item_ui.py` in that commit. All three
corrections were verified demonstrably correct against actual rendered output (see "Verification"
below); no implementation bugs were found. The full suite passes at 525/525 as committed — this
document's earlier claim of "522 passed, 3 failed" against a "frozen unstaged" tree was stale and
did not reflect the actually-committed state.

Manual browser visual acceptance (checklist below) remains genuinely open — no headless browser
or Playwright is available in this environment (verified again during this correction pass), so
it has not been performed, and this document makes no claim that Jason has manually accepted it.

**Update (2026-09-28, start of OPS-13A):** Jason has since reviewed OPS-12 in the browser himself.
His own words: "seems to be ok; further refinements may emerge." This is recorded as provisional
manual acceptance only — it is not a claim of an exhaustive browser acceptance against the full
checklist above, which remains available if a more thorough pass is wanted later.

## Files created

- `docs/tasks/OPS-12-role-productivity-center.md`
- `tests/unit/test_ops12_role_productivity.py` (13 focused tests)

## Files modified

- `core/bid_control_center.py` — the shared batched Bid-summary projection: `MyDayBidBucket`,
  new `BidPortfolioRow` fields (`additional_blocker_count`, `overdue_work_count`,
  `other_attention_count`, `supplier_response_position`, `last_activity`, `my_day_bucket`,
  `evidence_links`), `last_activity_by_bid()`, three new `BidPortfolioView` members
  (`UPCOMING`/`DORMANT`/`ISSUED`), `build_bid_portfolio()`.
- `app.py` — removed the redundant N+1 `_my_day_bid_summaries()`; `/my-day` now reuses its
  already-loaded `MyDayProjection` through the shared projection instead of loading it twice;
  `_bids_browser_context()` passes `GATE_LABELS` through.
- `templates/my_day.html` — five named sections (Bids requiring action / Waiting for others /
  On track or upcoming / My standalone work / Recently completed); per-Bid cards with a direct
  link on the primary action and Bid-specific evidence links.
- `templates/bids.html` — Lifecycle/status, Current workflow gate, Supplier response, Proposal
  (link), Last recorded activity columns; "+N more" on Highest blocker.
- `pyproject.toml` — added `core/bid_control_center.py` to the existing strict-mypy allowlist
  (`[tool.mypy] files` and the matching override), following the project's own established
  pattern for scoping strict typing to changed/new modules.

## Query and performance measurements

See `docs/tasks/OPS-12-role-productivity-center.md` for the full before/after table (measured
via `sqlite3.Connection.set_trace_callback` at 1/12/36 Bids, compared against an unmodified
worktree at the starting SHA). Headline: `/my-day` drops from roughly 2x the cost of `/bids`
(111/1123/3331 queries) to matching it almost exactly (65/527/1535 queries) by removing a
duplicate-projection-load bug and the old per-Bid `workspace()` calls; `/bids` itself is
unchanged (+2 constant). Remaining linear growth (~42 queries/Bid on both pages) is pre-existing
inside `MyDayService.get_my_day()` and was not modified.

## Automated workflow acceptance

All performed via the existing dependency-free ASGI harness pattern (`scripts/asgi_acceptance_*`
style, no `httpx`/`starlette.testclient` dependency):

- A multi-blocker Bid appears exactly once in its live bucket — `test_additional_blocker_count_excludes_overridden_and_counts_the_rest` and `test_each_active_bid_appears_in_exactly_one_live_bucket`.
- Highest blocker and additional-blocker count are correct — same tests.
- Waiting vs. direct-action classification never conflicts — `test_bid_requiring_action_beats_waiting_when_both_present`, `test_bid_is_waiting_only_when_no_higher_priority_action_exists`.
- Every primary action reaches its authoritative destination — reuses the existing, unmodified `next_action_destination` computation (`core/bid_control_center.py::_next_action`); the template now renders it as a link (previously plain text on My Day).
- No GET mutates records or audit history — unchanged from OPS-06's existing guarantee; not modified by this task.
- Existing readiness verdicts are unchanged — `core/readiness.py` untouched; confirmed by `test_bid_control_center.py` and `test_ops06_ui.py` still passing unmodified.
- Standalone work remains accessible — `/my-day`'s standalone section is untouched; confirmed by the smoke check hitting `/my-day` and `/my-work`.
- Invalid filters return HTML 422, not JSON — confirmed live: `GET /bids?view=upcoming&status=won` → `422`, `content-type: text/html`.
- Query measurements satisfy the scaling requirement — see table above; `/bids` near-constant overhead, `/my-day` no longer doubles the cost.
- OPS-11 package intake/addendum controls remain reachable — `scripts/validate_ops_11.py`, `scripts/asgi_acceptance_ops11.py`, `scripts/asgi_acceptance_ops11bx.py`, `scripts/asgi_acceptance_ops11by.py` all still pass unmodified.
- No post-award workflow was introduced — confirmed by inspection; award/handover code paths untouched.

## Verification

```text
ruff check (changed .py files: app.py, core/bid_control_center.py,
  scripts/asgi_acceptance_ops08w.py, tests/unit/test_ops08w.py,
  tests/unit/test_ops12_role_productivity.py, tests/unit/test_work_item_ui.py):
  All checks passed!
ruff format --check (same changed files): 6 files already formatted
mypy (`.venv/bin/python -m mypy`, strict, scoped allowlist incl. new module):
  Success: no issues found in 29 source files
git diff --check (c1159ac..ff398b6): clean

Focused: tests/unit/test_ops12_role_productivity.py — 13 passed
Focused: tests/unit/test_bid_control_center.py, test_my_day.py, test_my_day_service.py,
  test_ops06_ui.py, test_ops11_ui.py — 29 passed
scripts/validate_ops_02.py: PASS
scripts/validate_ops_06.py: PASS
scripts/asgi_acceptance_ops06.py: PASS
scripts/validate_ops_11.py: PASS

Full suite (`.venv/bin/python -m pytest -q`, run against committed HEAD ff398b6):
  525 passed, 0 failed, 90.47s
```

This is the final verified evidence as of this correction pass (2026-09-28), superseding the
"522 passed, 3 failed" figure from the earlier draft of this document, which described a
pre-commit working-tree state that no longer matches what was actually pushed.

**Note on `uv run pytest` vs `python -m pytest`:** `uv run pytest -q` fails at *collection* with
`ModuleNotFoundError: No module named 'tests'` / `'scripts'` for three test modules
(`tests/unit/test_bid_package_intake.py`, `tests/unit/test_ops08w.py`,
`tests/unit/test_ops11_ui.py`) that import across `tests/unit/*` or `scripts/*` as if they were
packages. Re-verified during this correction pass: this reproduces identically on the canonical
`/home/bowen/dev/projects/contractiq` worktree checked out at the unmodified starting SHA
`c1159ac` (read-only check; that worktree was not otherwise touched) — it is a pre-existing
environment/invocation quirk, not something this branch introduced or broke, and not a code bug.
`.venv/bin/python -m pytest` (which inserts the invocation directory into `sys.path`) is the
supported invocation for this repo and works correctly on both the baseline and this branch. No
test/pytest configuration or product code was changed to mask this — per the instruction not to
alter product code to hide an environment-only issue — flagging it here for awareness since it
will affect anyone using `uv run pytest` directly in this repo.

## Decisions I made

- **Proposal position is a link, not a live-computed status**, on both the Bids table and (by
  omission) My Day cards. `ProposalExchangeService.assess()` costs ~15-20 queries per Bid with no
  batch form; computing it for every portfolio row would have added 540-720+ queries at 36 Bids.
  This was reviewed with Codex before implementation, which recommended the same conclusion.
  Writing a lighter batch primitive was rejected as duplicating `assess()`'s authoritative logic.
- **"Current workflow gate" reuses `bid.current_gate` + `GATE_LABELS`**, not
  `core.bid_workflow.project_bid_workflow()`'s finer nine-stage OPS-07W spine. The coarser gate
  label is already free (already on `Bid`, already used by the existing Bids table) and the
  finer spine is not currently wired into any batched multi-Bid path; pulling it in would add
  scope and a new per-Bid batching problem for a field the spec did not require to be the finer
  granularity.
- **"Last recorded activity" is the most recent `audit_log` row**, including system-generated
  entries (for example gate re-evaluations), not a filtered "human activity only" signal — the
  codebase has no existing classification of audit actions into human vs. system origin, and
  inventing one was out of scope. Labeled accordingly in the UI ("Last recorded activity", not
  "Last human action").
- **`DORMANT_AFTER_DAYS = 14`** is an explicit, named presentation-policy constant with a
  boundary test (`test_dormant_view_boundary_is_exactly_fourteen_days`), not an inferred
  business fact — it happens to match the existing deadline-attention horizon but is
  intentionally independent of it.
- **`CURRENT` view's existing meaning (`ACTIVE`+`HELD`+`SUBMITTED`) was preserved unchanged.**
  `UPCOMING`/`DORMANT`/`ISSUED` were added as new, additional views rather than redefining
  `CURRENT`, per the Codex review's explicit flag that narrowing `CURRENT` to `ACTIVE` only
  would silently hide Held/Submitted Bids from the default portfolio view.
- **The My Day bucket signal set is: blockers, readiness verdict, deadline attention, decision
  count, overdue work-item count, and other open attention (requirement/deliverable/commercial/
  contract-risk/intake) count.** All are already-batched data (no new per-Bid queries). This was
  a direct correction from the Codex review, which found the first draft's bucket predicate
  under-counted (a Bid with overdue work but no overdue Bid *deadline* would have been shown as
  on-track without it).

## Deviations from the task spec

**None in the implementation itself.** An earlier draft of this document flagged three
pre-existing test assertions as stale and left open pending a decision. That decision was made
and applied before `ff398b6` was committed — the assertions were updated, not left open:

1. `tests/unit/test_ops08w.py::test_my_day_uses_one_primary_bid_summary_and_business_status` —
   updated from the old `data-bid-summary="{{ summary.bid.bid_id }}"` / `"Commercial review
   incomplete"` assertions to `data-bid-row="{{ row.bid.bid_id }}"` / `"Needs attention"`,
   matching `templates/my_day.html` lines 170 and 173 as actually rendered.
2. `scripts/asgi_acceptance_ops08w.py` (which `test_connected_bid_workflow_acceptance` calls via
   `main()`) — its `/my-day` marker updated from `data-bid-summary="` to `data-bid-row="` to match
   the same template change.
3. `tests/unit/test_work_item_ui.py::test_ui_api_creates_transitions_completes_and_reopens_audited_item`
   — updated from `"Completed and cancelled history (1)"` to `"Recently completed (1)"`, matching
   `templates/my_day.html` line 259 and the spec's required section name.

All three were a **direct, correct consequence of doing exactly what OPS-12 asks**
(consolidating the duplicate per-Bid summary mechanism onto the shared portfolio projection, and
using the spec's literal five section names), verified in this correction pass by re-reading the
actual template output against each updated assertion — not a bug in the new code, and not a case
of loosening a test to paper over one. No further action needed on these three.

## Concerns for review

- The `uv run pytest` vs. `python -m pytest` invocation difference noted above is unrelated to
  this task but will surface for any future session — worth a one-line `pyproject.toml`
  `[tool.pytest.ini_options]` fix (e.g. `pythonpath = ["."]`) at some point, on its own ticket.
- No headless browser (no Playwright, no local Chromium) is available in this environment, so I
  could not perform a literal rendered-pixel inspection at 1366×768/1920×1080 myself — see the
  next section.

## Browser inspection

**Not performed as a rendered/pixel check** — no Playwright or headless Chromium is installed in
this environment (checked: `python3 -c "import playwright"` and common Chromium binaries both
absent). What I verified instead: the real `uvicorn` dev server boots cleanly against a fresh
database and serves `GET /my-day` and `GET /bids` with `200`; the new My Day/Bids markup reuses
the same existing CSS classes (`.section`, `.panel`, `.hold`, `.table-wrap`/`table`) already used
elsewhere on these same pages at the existing breakpoints (`@media (max-width: 1050px)` and
`@media (max-width: 700px)`, both untouched), so it inherits the same responsive behavior rather
than introducing new layout risk. The manual acceptance checklist below covers the actual visual
check both viewports need.

## Local-LLM and Codex model-routing results

- **Local LLM read-only inventory** (`clc-delegate`, model reported as `qwen3.8-27b-q6k` at
  `http://10.0.0.10:8081` — note this doesn't match either model/port documented in global
  CLAUDE.md for `cl`/local mode; worth checking that config) — **timed out at the full 1500s
  budget with zero output** (`exit_code=124`, `timed_out=true` in its own meta log; the
  wrapper's own `echo "EXIT=$?"` masked this as an apparent exit 0 — caught by checking the
  meta log directly rather than trusting the wrapper). I did not retry it; I completed the same
  inventory myself directly (file reads) plus one research fork, cross-verified against the real
  files before designing anything.
- **Codex architecture review** (`codex-delegate`, read-only instructions given; `git status`
  confirmed clean before and after despite the `workspace-write` sandbox) — completed
  successfully in one call. Verdict: "proceed with named changes." All seven correctness points
  and four open-question answers were reconciled into the design before any code was written;
  see "Decisions I made" above and `docs/tasks/OPS-12-role-productivity-center.md` for the full
  reconciliation.

## Protected-file and worktree safeguards

- `.claude/settings.local.json` SHA-256 unchanged: `3df3b79e09c09bec3c2f4f008fa9446f76fe54f6c4432665bd0f0bcf34ab188c`.
- `uv.lock` SHA-256 unchanged: `7ca4f65818245285964603f5eaf1eab0c48a51be9f8c9195d64844cdd684d672`.
- `data/contractiq.db` remains absent.
- Git stash stack empty throughout (one self-inflicted temporary stash during a mypy-comparison
  check was immediately restored via `git stash apply <sha>` + `git stash drop`, never `pop`;
  verified nothing was lost — final working tree diff matches what was intended).
- Canonical `/home/bowen/dev/projects/contractiq` and all three `contractiq-bench-*` worktrees
  untouched and clean, except a pre-existing untracked `docs/bench/` directory in
  `contractiq-bench-tasks` that predates this session and was never read or written by it
  (flagged at the start of this session, not caused by it).
- Two temporary scratch git worktrees I created myself for measurement/comparison purposes
  (`baseline-worktree`, `baseline-check`, both under `/tmp`) were removed with
  `git worktree remove --force` after use; `git worktree list` now shows the original five
  worktrees only.
- No staging, commit, push, merge, rebase, reset, restore, or clean was performed.

## Exact manual runtime command

```bash
CONTRACTIQ_DB_PATH=/tmp/contractiq-ops12-manual/app.db \
CONTRACTIQ_DOCUMENT_ROOT=/tmp/contractiq-ops12-manual/documents \
uv run python -m uvicorn app:app --reload
```

## Manual acceptance checklist

1. Create 2-3 Bids with different situations (one with a material blocker, one purely waiting on
   a work item or supplier, one clean) and confirm each appears **exactly once**, in the
   expected one of "Bids requiring action" / "Waiting for others" / "On track or upcoming".
2. Confirm each Bid card's primary-action link goes directly to the correct authoritative page
   (not just the Bid overview), and that the "underlying evidence" disclosure only lists links
   relevant to that Bid's actual state.
3. On the Bids page, confirm the new Lifecycle/status, Current workflow gate, Supplier response,
   Proposal (link), and Last recorded activity columns render sensibly, and exercise the new
   Upcoming/Dormant/Issued filter views.
4. Confirm `GET /bids?view=upcoming&status=won` (an invalid combination) returns a readable HTML
   error, not raw JSON.
5. Confirm OPS-11 package intake is still reachable from a Bid's overview page.
6. Inspect both pages at 1366×768 and 1920×1080 for clipping or overlapping content.

(Item 7 in an earlier draft — deciding the three flagged stale test assertions — is resolved;
see "Deviations from the task spec" above.)

## OPS-12 correction and evidence-reconciliation pass (2026-09-28)

This document previously described OPS-12 as "frozen unstaged" pending a decision, with a
522-pass/3-fail result. That was inaccurate: `ff398b6` had already committed and pushed the full
implementation, the resolved test-assertion corrections, and the supporting `pyproject.toml` /
`scripts/asgi_acceptance_ops08w.py` changes, under a commit message that describes the prior
OPS-11BZ ticket instead. This pass independently re-verified branch/HEAD/parity, re-ran the full
suite against the actually-committed code, re-read the three corrected assertions against actual
template output, re-confirmed the `uv run pytest` collection issue reproduces identically on the
unmodified canonical worktree at `c1159ac`, and re-checked protected-file hashes and worktree
integrity — all matching this document's prior claims except the stale status/results text, which
is now corrected above. No implementation changes were made; only this document was corrected.
Jason's manual browser acceptance has not occurred and is not claimed here — no headless browser
or Playwright is available in this environment (re-verified), so the manual acceptance checklist
above (items 1-6) remains genuinely open.

OPS-12 IMPLEMENTATION STATUS: COMMITTED (ff398b6, under an inaccurate OPS-11BZ commit message) —
AWAITING MANUAL BROWSER ACCEPTANCE ONLY


---

# OPS-13A Phase 1 — governing-source selection (status, appended 2026-09-29)

## Status

IMPLEMENTED AND VERIFIED; left unstaged, uncommitted, unpushed on
`local/ops-13a-bid-deliverables-discovery` (HEAD `40c985b`). Not yet manually accepted. Promotion,
manual acceptance and the deferred issues below are explicitly NOT started.

**Handoff-file incident (recorded for the reviewer):** during Task 1b-ii the Codex worker
overwrote this file (913 lines -> 125) because `AGENTS.md` §6 tells workers to overwrite
`HANDOFF.md`. The pre-Codex file was recovered byte-exact from
`~/.claude/file-history/90c2c2db-dd26-4c81-b00d-d50817920a15/7888f00c5075b982@v3`
(SHA-256 `471cc80c…` == recorded baseline) and restored; the Codex version is kept as evidence at
`/tmp/HANDOFF.codex-1bii-125lines.md` (SHA-256 `3eef3af4…`). All later worker prompts forbade
editing this file. This section was written by the orchestrator, not a worker.

## What changed

- **Domain/persistence (1a):** `GoverningDisposition {UNSET,GOVERNING,NOT_GOVERNING}` and
  `GoverningSelectionItem/Create` in `core/bid_package_intake.py`; new append-only, supersede-chain
  table `bid_received_file_governing_events` (additive `IF NOT EXISTS` migration, immutable/no-delete
  triggers, unique-supersede index) in `core/bid_package_repository.py`;
  `add_governing_selection_bulk` (mirrors `add_file_dispositions_bulk`: one transaction, audit row
  per event, all-or-nothing, stale-token check, only ELIGIBLE files may be GOVERNING, no-op creates
  nothing) and `current_governing_disposition`; `BidPackageService.select_governing_sources`.
  Design choice: separate table rather than a column on the disposition events (positional 13-value
  inserts; the current disposition event id is the review concurrency token). The existing
  migration test's exact counts moved 18->19 tables, 36->38 triggers (direct consequence).
- **Bid Basis rules (1b-i):** snapshot membership, `_release_incorporation_issues` and
  `candidate_snapshot_document_count` use only files that are ELIGIBLE **and** explicitly GOVERNING
  with a controlled link. GOVERNING without a link blocks ("governing file … lacks an exact
  controlled-version link"); ELIGIBLE-but-unselected files neither enter nor block. Link
  semantics unchanged (existing `_validate_document_version`; no new EXACT_BYTES requirement).
- **Discovery before publication (1b-ii):** `run_discovery` reads current ELIGIBLE received files
  with no snapshot; extraction/citation identity is exactly one of `document_version_id` or
  `received_file_id`; additive discovery migration; `DeliverableTargetType.RECEIVED_FILE` /
  `DeliverableRelation.SOURCE_RECEIVED_FILE`; accepting a received-file proposal creates and
  activates the deliverable atomically with review event and audit.
- **Presentation (1c):** release detail exposes per-file governing state; presentation adds
  `governing_candidates`, `governing_unlinked_files`, and `discovery_ready`/`basis_ready` flags; new
  `POST …/releases/{release_id}/bulk-governing-sources`; template separates "Include for analysis"
  (-> Find required Bid submissions) from "governing Bid Basis source" (-> exact controlled version
  -> confirm Bid basis); bulk document control now lists only GOVERNING-unlinked files.
- **Snapshot carry-forward correction (this correction):** `create_snapshot` and
  `candidate_snapshot_document_count` skip a document carried forward from the prior snapshot only
  when its source received file's CURRENT governing state is explicitly `NOT_GOVERNING`. Files with
  no governing event (pre-governing snapshots), currently GOVERNING files (incl. re-selected),
  NULL-source rows and unrelated sources carry forward as before; prior snapshots/events/documents
  are never modified.

## Delegation route actually used

| Unit | Route | Outcome |
|---|---|---|
| 1a | local (qwen3.8-27b-q6k, 10.0.0.10:8081, 1200s) -> Codex | local BUDGET_EXCEEDED (exit 124, no changes); Codex pass |
| 1b-i | local (900s) -> Codex | local BUDGET_EXCEEDED (exit 124, no changes); Codex pass |
| 1b-ii | CODEX_DIRECT (cross-layer, migration coupled to behavior) | pass; overwrote HANDOFF.md (recovered) |
| 1c-i | CODEX_DIRECT; first launch hung at startup with no task output (1800s), re-launched split A/B with stdin from /dev/null | pass |
| 1c-ii | CODEX_DIRECT | pass; surfaced the carry-forward defect |
| correction | CODEX (narrow) | pass |

Every Codex run's own log header showed `model: gpt-5.6-sol`, `reasoning effort: high`
(`~/llama-logs/codex-delegate-*.err.log`). The orchestrator independently re-ran tests and diffed
file hashes after every unit.

## Verification (final code state)

```text
Focused (governing_basis, governing_ui, governing_selection, intake): 40 passed
Full suite (uv run python -m pytest -q): 606 passed, 0 failed
ruff check / ruff format --check (all changed .py): All checks passed! / 26 files already formatted
mypy: Success: no issues found in 31 source files
git diff --check: clean; index empty
```

Scenarios covered by tests: mixed 16-file package (5 governing, analysis-only remainder, 2
excluded), analysis-only files used by discovery but not in the snapshot, addendum second snapshot
carrying forward the first, invalid selection (EXCLUDED / NOT_ASSESSED / stale token / duplicate /
empty -> HTTP 422, no events), cross-Bid file/release/link rejection without mutation, discovery
before Bid Basis publication leaving earlier runs unchanged after later publication,
un-selection dropped only from the next snapshot.

## Remaining / deferred (not started)

- Controlled-version "promotion" of a received-file-sourced deliverable/citation.
- Addendum-impact detection matches only document-version sources; received-file-sourced proposals
  are not flagged.
- Pre-existing: a corrupt file with a supported extension crashes discovery extraction (uncaught
  parse error) instead of recording an UNREADABLE unit.
- `AGENTS.md` §6 (workers overwrite HANDOFF.md) conflicts with the orchestrator's append convention.
- Un-selection is honoured only for the next snapshot; EXCLUDED/UNSET files are not treated as
  un-selected (by design).

---

## OPS-13A Phase 1 follow-up: three workflow gaps closed (2026-09-29)

Status: candidate only. Nothing staged, committed or pushed. HEAD 40c985b. AGENTS.md untouched.
The first 913 lines of this file are byte-identical to the pre-session content (SHA-256
471cc80c…d632); this section is append-only.

Baseline: `git status --short` had 31 entries (13 modified, 18 untracked), not the 12/19 in
the earlier prose report.

### Gap 1: controlled-version promotion (Codex gpt-5.6-sol / high, log-verified)
- New additive table `deliverable_discovery_source_promotions` (marker
  `ops_13a_deliverable_discovery_promotions_v3`), immutable (UPDATE/DELETE triggers),
  UNIQUE(bid_id, operation_id) and UNIQUE(deliverable_id, citation_id).
- `core/deliverable_discovery_promotion.py::promote_source`, one BEGIN IMMEDIATE txn:
  requires the proposal's current decision ACCEPTED for that deliverable, a received-file
  citation, and an EXISTING current Package Intake file-to-version link of EXACT_BYTES or
  REPRESENTS_VERSION matching the supplied controlled version (same Bid, control_managed).
  It never creates a file-to-version link. It inserts the promotion row and an ADDITIVE
  DOCUMENT_VERSION/SOURCE_DOCUMENT_VERSION deliverable link, with two audit rows in the same txn.
  The original citation row and RECEIVED_FILE link are untouched. Replay is idempotent;
  a second promotion of the same citation raises "already promoted".
- Service `promote_source_to_controlled_version`, route
  `POST /bids/{bid_id}/deliverable-discovery/promote-source`, template display and form.

### Gap 2: addendum impact for received-file sources (local attempt exceeded budget; Codex)
- Local qwen3.8-27b-q6k, 900 s: BUDGET_EXCEEDED (exit 124, 9 tool calls, no final result,
  NO_CHANGES). Escalated automatically to Codex gpt-5.6-sol / high: PASS.
- `received_file_ids_for_version` (repository read). `flag_addendum_effect` now also flags
  proposals citing, and deliverables linked to, received files whose CURRENT
  EXACT_BYTES/REPRESENTS_VERSION link targets the directive's controlled version. Append-only,
  once per (directive, item); prior decisions and review events are never modified.
- Existing test `test_deliverable_discovery_service.py` was edited: its received-file case
  asserted `flags == []`, which encoded the gap being closed.
- Found by the E2E test and fixed directly by the orchestrator (about 10 lines): the base
  rationale did not name the controlled version, and a promoted deliverable (both links) took
  the generic path. It now names directive, controlled version and, when matched, the
  received file.

### Gap 3: extraction failure (Codex gpt-5.6-sol / high)
- First Codex invocation hung 29 min at "Reading additional input from stdin..." with no
  session created (tooling fault, NO_CHANGES). Retried once with `< /dev/null`: PASS. Use
  `< /dev/null` with codex-delegate in background runs.
- `extract_units` never raises for a parse failure of a supported extension: it returns one
  UNREADABLE `file` unit (mid-way failures keep the units already extracted). The library-not-
  installed ImportError is preserved. `run_discovery` records UNREADABLE units when a received
  file cannot be opened or read instead of silently skipping. Unsupported types are still skipped.
- `list_unreadable_extractions` (latest run per file), a "Files that could not be read"
  section on the discovery page stating these files were NOT analysed.

### Synthetic end-to-end test
`tests/unit/test_deliverable_discovery_e2e_workflow.py` (Codex-written, orchestrator-run).
Flow: mixed package (PDF, DOCX, XLSX, corrupt PDF, unsupported file) -> mark eligible ->
discovery before any Bid Basis -> accept a cited deliverable (reject/defer/leave pending
others) -> governing selection -> exact controlled-version links + promotion -> publish
Bid Basis -> addendum + directive -> impact flags. Measured step times (seconds):
receive 0.50, mark 0.04, discovery 0.56, review 0.02, governing 0.01, link+promote 0.06,
publish basis 0.02, addendum 0.04, impact 0.08, final checks 0.01. The corrupt file stayed
listed unreadable, no proposal cites it, and the rest of the run was usable.

### Verification (orchestrator-run)
- Full suite: 627 passed (606 before, plus 21 new tests). Run with
  `uv run python -m pytest` (bare `uv run pytest` cannot import `scripts`).
- Scoped ruff check and format on core/, app.py, tests/unit: clean. `git diff --check`: clean.
- mypy on the six discovery modules with `--follow-imports=silent`: clean. Without it, one
  PRE-EXISTING error remains at core/bid_package_storage.py:293 (no-any-return); the file is
  byte-identical to the baseline, so it is not from this session.
- `core/deliverable_discovery_repository.py` (739 lines) and `..._service.py` (729) exceed the
  300-line standard. This was already true in Phase 1; not refactored here.

### Files changed this session (vs baseline)
Modified: app.py, core/deliverable_discovery.py, core/deliverable_discovery_extraction.py,
core/deliverable_discovery_repository.py, core/deliverable_discovery_service.py,
templates/deliverable_discovery.html, tests/unit/test_deliverable_discovery_service.py.
New: core/deliverable_discovery_promotion.py, and tests/unit/test_deliverable_discovery_
{promotion,addendum_received,extraction_failure,e2e_workflow}.py.

Manual acceptance has not been started.

## OPS-13A-F — Owner pursuit decision and issue-phase approvals (candidate, unstaged)

Replaces the bid/no-bid approval checkbox with an append-only owner decision (PURSUE / DO_NOT_PURSUE) and moves senior value, margin and
exceptional-terms approvals to the controlled issue phase. Contract, matrices and implementation status: `docs/tasks/OPS-13A-bid-deliverables-discovery.md`
section "OPS-13A-F" (F.1-F.5). Additive migration only (`bid_owner_decisions`, 4 triggers, 2 columns on `proposal_exchange_exports`); the manual database on
port 8000 (`/home/bowen/contractiq-manual/active/`) has NOT been migrated and that server has NOT been restarted. Restart it on the candidate only after review.

## OPS-13A-F finalization (owner-decision + intake candidate) — final evidence

Policy clarifications implemented: Level 2+ senior approval is a temporary governance-level rule (no dollar-value or margin-% thresholds are enforced or claimed);
approval must identify the selected pricing baseline (`approvals.pricing_baseline_id`, additive) and satisfy the existing authority policy; QUALIFY/REJECT positions
require review and senior approval only when an explicit `required_approver` is assigned; historic submitted/won/lost records and legacy approvals are preserved;
a missing authority policy blocks approval-for-issue and customer issue with a Resolve link to `/decisions?bid_id=…#authority-policies` while intake and drafting stay available.

Intake defects closed: zero-file guidance with Apply disabled; exclusion reason only when Exclude is chosen (inline validation, server validation retained); visible selection
controls and "K of N selected (M in collapsed folders)"; optional common file kind clearly labelled; notices report files actually changed vs already set ("No change" on repeat);
Resolve links land on visible controls (`#files`, `#governing-sources`, `#document-control`, `#publish-basis`); My Day and Bid workspace share the same next action
(workspace blocker links now receive intake facts incl. governing counts); "Development gate check" / "Not yet assessed: …" scoped labels (zero requirements/scope never read as Bid ready).

Verification: Chromium at 1366x768 and 1920x1080 on a copy of the manual DB (isolated port, since stopped) — items 1-8 and policy landing targets observed visible and in view.
Focused regression set 502 passed; ONE final full suite: 802 passed. ruff clean except pre-existing errors in `contracts/proposal-studio/v1/reference/test_exchange_contract_v1.py`;
`mypy --strict` on readiness/control-center/presentation/senior_approvals/workflow clean; `git diff --check` clean. Nothing staged, committed or pushed.

### Pending policy inputs (deferred — authoritative facts required, NOT implemented)
- Monetary value bands and margin-percentage thresholds for senior approval.
- Exceptional-term taxonomy.
- Role/approver mapping for `required_approver`.
- Authority-policy authoring (roles, stages, rules) and who may publish one.
- Approval expiry / re-approval rules.

## OPS-13A final UI corrections — correction evidence

Four manual-acceptance findings corrected; candidate remains unstaged, uncommitted and unpushed. Port 8000 and `/home/bowen/contractiq-manual` untouched; all browser work used throw-away databases on ports 8791/8792 (since stopped). Core policy/migration logic was NOT changed (the authority-policy HTML routes call the existing `approval_service.create_policy` / `publish_policy`), so the full suite was not repeated.

Route: CODEX_DIRECT (gpt-5.6-sol, effort high, 1800 s budget; exit 0; ~16 min). Authorization-adjacent policy surface plus shared templates/next-action code made decomposition risky.

### Changes
1. Owner-decision Resolve — blockers link to `/bids/{id}/owner-decision` with a validated same-Bid `return` path (open redirects refused), carried through form errors and success.
2. Authority-policy surface — `/decisions#authority-policies` now has a draft form (name, scope, description, effective dates, roles, rules, stages, each explained in business language) and a per-draft "Publish this draft" action via `POST /decisions/authority-policies[/{id}/publish]`. Inputs are retained on error with what/how-to-fix text. No defaults assert authority; nothing pre-published. API unchanged. Missing authority decision: none — existing roles/rules/stages semantics suffice. Rules and stages remain JSON because the existing model accepts arbitrary dimensions.
3. Stopped Bid — Prepare/export/download actions removed when DO_NOT_PURSUE applies; next action shows the stopped reason and "Record owner decision". Backend rejection kept.
4. Bulk review — sticky bar holds only "K of N selected" and Apply; optional controls moved into a disclosure outside it; exclusion reason shown only when Exclude is chosen; generic "Correct this authoritative fact" replaced by specific labels (Record owner decision / Review governing sources / Configure approval policy / …). I restored a "Review selected files" heading that the rework dropped (`test_ops11_ui` asserts it).

### Verification
- Chromium 1366x768 and 1920x1080, each on a fresh synthetic DB: 29/29 checks passed at both. Flow performed through the UI: Configure approval policy link -> invalid rules JSON rejected with guidance and inputs retained -> draft created (not effective) -> published -> margin gate approval recorded -> approval changed facts so the prior package went stale (expected) -> prepared successor package, imported generation evidence -> approve-for-issue enabled -> customer issue recorded -> Bid Issued. Policy was never supplied by scripts. (The seed script supplied only the Bid, Level 3 classification, PURSUE decision and initial package.)
- Stopped Bid: owner decision DO_NOT_PURSUE recorded through the owner-decision form; issue-control and workspace show no Prepare/Export action, show the stop reason and "Record owner decision".
- Bulk review: sticky bar 64 px high, contains only count and Apply; no horizontal overflow at either viewport.
- Legacy-baseline evidence gap: `test_issue_phase_approvals.py` upgrades a synthetic legacy OBTAINED approval with no `pricing_baseline_id` through the real additive migration and proves it cannot satisfy the issue-phase approval requirement; historical rows are not altered.
- Tests: new `tests/unit/test_ops13a_final_ui_corrections.py` + affected suites. Focused set (ops13a corrections, issue-phase, owner-decision, gate resolve path, review flow, policy clarification, ops09, intake steps, presentation) 74 passed. Broader related suites (ops07/08w/09/11, gates, readiness, owner decision, bid package, issue-phase, work-item UI; 243 tests): 242 passed, 1 failed on first run — `test_issue_phase_outstanding::test_draft_assessment_renders_pending_before_issue_notice` rendered the template in a bare Jinja environment lacking the new `proposal_resolve_label` global; fixed in that test (stand-in global), now 3/3 passing. The 'Review selected files' heading fix was also re-covered by `test_ops11_ui` in that run. Ruff check/format clean on changed Python; `mypy --strict` clean on proposal_exchange_service, readiness, bid_control_center, senior_approvals; `git diff --check` clean.
- Run tests with `PYTHONPATH=. uv run pytest …` (known `tests` import-collection issue).

### Incident and notes
- Codex overwrote HANDOFF.md despite the instruction to append (worker rule in AGENTS.md). The original (1119 lines) was recovered byte-exact from the delegate log; blob hash `60a7d283` matched the pre-delegation file; this section was then appended. Codex's replacement summary is kept only in the scratchpad.
- Observed but not changed: per-file rows in the bulk-review list render with large spacing/centred checkbox; this CSS is not part of this change.
- Browser tooling: Chromium needed `libnspr4`/`libnss3`/`libasound2` extracted to a scratch dir (no system install).

## OPS-13A authority-policy authoring simplification — evidence

Candidate remains unstaged, uncommitted and unpushed. Port 8000, Jason's manual database and the canonical worktree untouched; browser runs used throw-away databases on ports 8791/8792 (stopped). No Codex call was made for this unit. No core policy or migration logic changed (the form builds the existing `AuthorityPolicy` and calls `approval_service.create_policy` / `publish_policy`), so no full suite was run.

### What the current Level 2+ workflow needs (inspected, not assumed)
`core/senior_approvals.py` (`_policy_roles`, `_approval_is_policy_covered`): a gate approval counts only while an effective PUBLISHED policy lists its approval-control code or its recorded authority among the policy's roles (or stage roles), case-insensitively. Rules and stages are consumed only by decision-case routing (`approval_rules.match_policy`). The policy authored in the earlier Chromium run (`MARGIN` role, one match-all rule, one stage) therefore used rules/stages the Level 2+ check never reads.

### Behaviour
- Ordinary inputs: name, scope, description, effective from/until, tick-boxes for the five existing approval controls (Margin, Legal, Credit, Finance, Executive; nothing pre-ticked), and "Other approved authorities" (one per line). Rules and stages are built as empty lists behind the form: no roles, stages, thresholds or approval rules are invented. A policy covering nothing is refused.
- Raw JSON (routing rules, approval stages) lives only under "Advanced policy configuration"; blank is valid. Advanced JSON is stored verbatim; stage roles must still be declared above (error says how to fix).
- Existing policies are never edited or discarded (the service has no edit path; none was added). The table now shows "Covers", a "View full configuration" disclosure (roles/rules/stages), and "Start a new draft from this policy", which pre-fills the form (controls, authorities, rules/stages verbatim incl. unsupported dimensions; dates left blank).
- `proposal_resolve_label` moved from `app.py` to `core/bid_workflow.py` beside `proposal_heading`; `test_issue_phase_outstanding` now uses the production helper and asserts the displayed labels (and absence of the generic wording). The earlier stand-in is gone.

### Delegation
- ROUTE LOCAL_DECOMPOSED. Template/layout units to the local model; route integration, tests and verification by Claude.
- Attempt 1 (whole section, 900 s): BUDGET_EXCEEDED, exit 124, one Read then deliberation, NO_CHANGES (verified by file diff).
- Attempt 2 (narrowed, create form block only, 600 s): PASS — exit 0; first launch used a wrong line range (50–64 is correct, I had said 51–64) and was stopped by me before any edit; relaunched with the corrected range. Diff against the pre-copy shows only the form block changed (plus a minor scope-hint wording difference).
- Unit B (policies table line, 600 s): PASS — exit 0; line 73 byte-identical to the specified replacement; no other line changed.
- Claude edits: `app.py` routes/helpers, tests, and one intro-sentence fix in `decisions.html` ("in Roles" -> "among the approval controls and authorities chosen below").
- Orchestration slip, no effect: while stopping the first mis-ranged delegate I sent SIGTERM to PID 548576, which was the WSL `/init`, not the delegate wrapper. It ignored the signal; port 8000 stayed up (HTTP 200) throughout.

### Verification
- Chromium 1366x768 and 1920x1080, fresh synthetic DB each: 32/32 checks passed at both. Journey with the ordinary form only (no JSON typed): form shows advanced JSON hidden and nothing ticked -> submit with no control refused ("Choose at least one approval control...") with name/scope/description/effective-from retained -> tick Margin + one typed authority -> draft created (not effective) -> Publish -> policy effective -> gate approval recorded -> successor package prepared and imported -> approve-for-issue -> customer issue -> Bid Issued. Earlier checks (stopped Bid, owner-decision link, bulk-review sticky bar 64 px, no horizontal overflow) re-run and passing.
- Tests: `tests/unit/test_ops13a_final_ui_corrections.py` now has ordinary-form create/publish/Level-2 coverage, input retention (incl. unknown control and empty coverage), and advanced-JSON/unsupported-dimension preservation incl. the copy-from-existing prefill. Affected set (ops13a corrections, issue-phase outstanding/approvals, bid_workflow, next-action, policy clarification, resolve path, owner-decision UI, ops09): 82 passed.
- Ruff check/format clean on changed Python; `mypy --strict core/bid_workflow.py core/proposal_exchange_service.py` clean; `git diff --check` clean.

## OPS-13A — Package intake next-action progression (unstaged, uncommitted)

**Cause (evidenced, B-2026-0001, read-only):** 12 received files = 2 Included, 10 undecided, 0 Excluded; one
discovery job `PARTIAL` (11 passages, 3 failed). `_package_intake_stage` (core/bid_workflow.py) returned
"Review received package files" whenever `files_requiring_review > 0` (NOT_ASSESSED count) and knew nothing
about Included files or discovery jobs, so undecided files outranked discovery. Not a stale projection:
Bid workspace and My Day both read `workflow.next_action_label/path`; the link went to
`releases/<id>#files` (anchor exists). Incorrect priority + discovery state absent from the projection.

**Change:** `PackageIntakeFacts` gains discovery facts; new `intake_discovery_action` / `intake_secondary_action`;
`_package_intake_stage` offers: active job → View discovery progress; 0 Included (discovery_known) → Choose
files for analysis (#files); ≥1 Included → Run deliverables discovery; PARTIAL → Review partial discovery
results (job page); FAILED → Retry deliverables discovery (job page); SUCCEEDED+pending → Review proposed
deliverables (#proposals, anchor added). `BidWorkflowProjection` carries a secondary "Finish reviewing files"
shown in bid_detail, My Day, Bids list. Facts populated by `_discovery_intake_facts` in app.py from existing
job/proposal services. No gates, schema, routes or approval semantics changed.

**Routing:** LOCAL_DECOMPOSED. Unit A (one attempt) BUDGET_EXCEEDED/NO_CHANGES; A1 (narrowed) local PASS;
A2 local BUDGET_EXCEEDED/NO_CHANGES → Codex gpt-5.6-sol/high PASS. Unit B and C by Claude.

**Verification:** isolated copies of the manual DB (zero Included / 1 Included / active / proposals pending /
PARTIAL / FAILED); Bid page and My Day agree; all destinations 200 with anchors; headless Chromium at
1366×768 and 1920×1080. Affected tests 94+30 passed; ruff clean; git diff --check clean.

**Ambiguity:** stage 2 stays "In progress" (and primary) while discovery work is outstanding, even after all
files are decided; other stages stay reachable via the stage nav.

## OPS-13A — Guided Bid workflow redesign and browser validation (unstaged, uncommitted, unpushed)

Journey definition: `docs/tasks/OPS-13A-guided-bid-workflow.md`. Six user tasks (A receive package, B choose and
analyse files, C review findings / initial requirements, D owner decision, E prepare the Bid, F Bid Basis + approvals +
issue) are a projection (`core/guided_workflow.py`) over the existing 10-stage spine; `next_action_*` on
`BidWorkflowProjection` derive from the current task so Bid workspace, My Day and Bids agree. Hard requirements come
from existing rules only (`initial_requirements_assessment_available`, proposal-readiness blockers with
`BlockerPhase.DEVELOPMENT`, `verify_release` for "every file decided" -> task F). The owner decision is the transparent
acknowledgement of incomplete analysis (its evidence snapshot already records it); no new table, gate or event.

### Root causes and repairs
- Partial loop: stage 3 was gated on undecided files and stage 2 stayed In progress while discovery was PARTIAL.
  Now stage 2 can be `COMPLETE_OPEN` ("Done — items open"); undecided files / partial runs are "complete later" items.
- Exclusion error: `_intake_error_response` always built the import-folder context, so any release-scoped error rendered
  the folder picker plus a raw-enum panel. Release-scoped errors now re-render the release file review; the backend
  guard is unchanged and mapped to a field error ("Enter why these selected files are excluded, or choose Include.");
  selection, choices and values are retained, the reason field is labelled/required with focus; the per-file form
  got the same treatment (client-side check too).
- Job page: findings with citations, what was not analysed, Retry incomplete analysis (POST run), Include more files,
  Continue to owner decision.
- Found during the walkthrough and fixed: scope items could never be Complete/Accepted from the UI (hard requirement,
  no control) -> work/review/reviewer/responsible-party fields added to scope and interface detail; a blank customer
  position failed as "Input should be a valid string" -> blank is now an empty string.
- Legacy registers (requirements, documents, scope, suppliers, commercial detail, ...) had no Bid context; they and all
  supporting pages now show a banner (task, what remains, Back to this task, Continue). Bid selected from path/query or
  from the rendered record's `bid_id`.

### Delegation
- U2 progression model + tests: CODEX_DIRECT (gpt-5.6-sol, high) PASS, independently re-verified.
- Job-page template: local PASS by repo evidence (no final result event; diff matches spec exactly).
- Banner and panel templates: local attempted twice concurrently, both ended with no file (BUDGET_EXCEEDED/NO_CHANGES);
  escalated to Codex PASS. Claude edits: app.py plumbing, middleware/context, release/intake template edits, banner
  restyle, small model fixes (labels, pages, `proposal_issued`), all tests added/adjusted.

### Browser evidence (Chromium, 1366x768 and 1920x1080; isolated environments under ~/contractiq-review)
`scripts/journey.py` run per size: 63/63 checks each (logs in ~/contractiq-review/state/journey-{s,l}.log, shots in
~/contractiq-review/shots). Fresh synthetic Bid: create -> import -> invalid exclusion (client + server) -> Include ->
discovery PARTIAL -> findings -> continue with findings unreviewed -> owner decision -> E needs (real requirements) ->
document control, requirement, scope review, 33 commercial factors, proposal family -> F: Bid Basis, policy, margin
approval, addendum import and re-basis, package, issue. Jason's state (copy of live DB, 12/2/10/PARTIAL): B Done — items
open, current D, My Day agrees, job page shows findings/gaps/retry, retry runs, owner decision, then E.
Fixtures: stand-in discovery model (`fake_llm.py`); synthetic PDFs/DOCX/XLSX; pricing-scenario baseline via API
(no browser control exists); Proposal Studio manifest + DOCX/PDF stand-ins (external tool); commercial factors disposed
Not Applicable (Accept needs customer position + exact source).

### Unresolved product decisions
- No browser control to select a pricing baseline (blocks senior approval at Level >= 2); needs an owner.
- Standard commercial factors: initialising creates 33 blocking items; consider a lighter path.
- Banner "Continue to" from a Done task points at the next task even if that is already Done.
- Navigator stage statuses (OPS-07W) still demand gate approvals for "Complete"; shown only as informational registers.

### Verification
Full suite, ruff, mypy --strict (guided_workflow, bid_workflow, guided_context), git diff --check: see final line below.
Final: full suite 842 passed; ruff check/format clean; mypy --strict clean on guided_workflow, bid_workflow, guided_context; git diff --check clean. Nothing staged/committed/pushed.

## OPS-13A — Finish the guided workflow (unstaged, uncommitted, unpushed)

Four defects from the earlier journey closed without redesigning the guided workflow. No gate, readiness, approval or
issue rule changed; no schema change; no new pricing register.

### Changes
1. **Pricing baseline in the browser** (existing `ScenarioService`/`ScenarioRepository`, unchanged). New page
   `GET /bids/{id}/pricing-baseline` (+ POST `families`, `versions`, `versions/{id}/reviews`, `select`) in `app.py`,
   `templates/pricing_baseline.html`. Amounts, currency and cost lines are typed by the user (blank = field error); margin
   is calculated, never entered; reviewer must differ from the actor (existing rule, surfaced as a field message);
   unreviewed versions cannot be selected (existing rule). `return_to` is validated (local path only, `//` rejected).
   `decisions.html` states which baseline an approval will bind (code, version, revenue, margin, selection id) or, when
   none exists, links to the page with `return_to` back to the approval. `bid_detail.html` shows a "Pricing baseline"
   line. Tests: `tests/unit/test_pricing_baseline_ui.py`.
2. **Continue** (`core/guided_workflow.py`): targets the first later task that is not Done / Done-items-open / Not
   applicable; none when nothing is left (issued/terminal Bids) and never targets E/F on a stopped Bid. My Day, Bids and
   the Bid workspace share the model so they agree. Completed tasks remain reachable by the task strip (`?task=`).
3. **Stage register** (`core/bid_workflow.py`, `core/bid_control_center.py`): navigator-scored stages derive status from
   DEVELOPMENT-phase outstanding items; issue-phase approvals no longer count toward preparation. Wording: "Preparation
   complete" / "Preparation remaining: N item(s)" and, on the commercial stage, "Approvals required before issue: N
   pending (these do not hold up drafting)". A genuinely blocked manufacturer stage stays Blocked.
4. **Known-missing release**: it WAS an early gate. Task A required `expected_missing == 0` and `_package_intake_stage`
   returned BLOCKED before any file review, so with an addendum expected, B..F stayed Upcoming and the Bid's primary
   action was "Import the expected customer release". Now A passes on `releases_received >= 1` and is "Done — items
   open" (shows in the panel under "Left open from earlier tasks"); stage 2 is computed as without the missing release
   plus "; N expected release(s) not received"; Task F gained the first need "Receive every release known to be
   missing" (restates the existing issue-time attention item `EXPECTED_RELEASE_MISSING`, blocking=True; no rule changed).
   Backend check (read-only): nothing in the discovery/intake services reads that attention item, so analysis and review
   of already-received files were never refused at the service level; the gate was only in the progression model.

### Routing
- U1 pricing controls: CODEX_DIRECT (gpt-5.6-sol, high) PASS.
- U2 Continue: LOCAL_DIRECT -> local BUDGET_EXCEEDED / NO_CHANGES (no final result in 900 s) -> Codex gpt-5.6-sol/high PASS.
- U4 missing release: LOCAL_DIRECT -> local BUDGET_EXCEEDED / NO_CHANGES -> Codex gpt-5.6-sol/high PASS.
- U3 stage register: CODEX_DIRECT (gpt-5.6-sol, high) PASS.
- Claude: Task A "Done — items open" one-line state change and the assertion it required, integration, Chromium run.
- Workers did not edit HANDOFF.md. No Claude subagents/forks used.

### Browser evidence (Chromium 1366x768 and 1920x1080; isolated env ~/contractiq-review/fresh, port 8792)
Script `~/contractiq-review/scripts/j13.py`; logs `~/contractiq-review/state/j13-{s,l}.log`; shots `~/contractiq-review/shots/{s,l}-m*|p*`.
65/65 checks at each size. **Discovery here is the deterministic stand-in model (`fake_llm.py`), NOT llama.cpp analysis**;
document fixtures are synthetic; pricing amounts (100000.00 revenue / 70000.00 cost, CAD) are typed by the tester as
fixture data. No API/script was used for the pricing step (the old `seed_pricing.py` is not called).
- Expected addendum recorded through the form; A shows "Done — items open", B is current, files are chosen, discovery runs
  (PARTIAL), findings reviewed, owner decision, Continue into E all proceed with the addendum still missing; the warning
  stays visible (B/F panel carry-over, stage 2 summary, Package intake, My Day, Bids).
- Approval with no baseline: form explains and links to `/pricing-baseline?return_to=...`; blank version refused; self
  review refused ("independent"); accepted review by another reviewer; baseline selected; browser returns to the approval
  form, which names BASE-1 v1 / 30.00% margin / selection id; margin approval recorded (303); DB approval row's
  `pricing_baseline_id` equals the selected baseline; Bid page shows the baseline.
- From Task A (Done) the Continue link reads "Continue to Confirm Bid Basis and approvals, then issue"; B, C, D, E views
  likewise skip every completed task.
- Register after E: stages 4-6, 8, 9 "Complete / Preparation complete"; commercial shows "Approvals required before
  issue: 2 pending" then "none pending" after the approval.

### Verification
144 affected tests pass (bid_workflow, guided_workflow, ops13a_guided_ui, pricing_baseline_ui, ops09, issue_phase_approvals,
gate_service, gates, ops06/07/08w/11 UI, readiness, bid_repository); ruff check and format clean on core/, app.py,
tests/unit; mypy --strict clean on guided_workflow, bid_workflow, guided_context; git diff --check clean. Full suite and
the 63-check journey not repeated (no core business rule changed). Port 8000 and the canonical worktree untouched.

### Remaining limitations
- Real llama.cpp discovery was not exercised in this verification.
- Task F's first need is now "Receive every release known to be missing", so while an addendum is outstanding F's primary
  action is the addendum import even if other issue controls could be done first.
- Cosmetic: in the stage register the "Done — items open" badge slightly overlaps the summary column at 1366px.
- The pricing form supports one revenue line and up to three cost lines in one currency (no FX assumptions collected).
- Navigator stage 7 (manufacturers) still reads "Not started" when no manufacturer package applies.

## Workflow and automation review — 2026-10-06 (unstaged, uncommitted, unpushed)

Full report (outside Git): `~/contractiq-review/r1006/report/ContractIQ-workflow-review-2026-10-06.md`.
Evidence (outside Git): `~/contractiq-review/r1006/{shots,state,scripts,fixtures}`. No customer content here.

### State
Branch `local/ops-13a-bid-deliverables-discovery`, HEAD `40c985b` (unchanged). Port 8000 not restarted, no form
submitted there; live DB bytes identical before/after. Review ran on isolated copies (ports 8793/8794/8795).

### Changes in this session (permitted wording / redundant-confirmation edits only)
- Removed four "I confirm this authoritative … should be created" tick boxes (contract risk, approval request,
  negotiation plan, My Work action) and the three matching two-line checks in `app.py`. The value was never stored.
- 28 plainly equivalent wording replacements across 16 templates (for example "Save requirement and response",
  "Save scope item", "Save", "Import package", "Advanced: source details"). Exact list:
  `~/contractiq-review/r1006/state/l3-spec.json`; pre-edit copies: `…/state/pre-l3/`.
- **Templates hot-reload into the running port-8000 process; `app.py` does not until restart.** The three
  templates therefore keep a hidden `confirm_authoritative=on` input so the running process still accepts the forms.
  After the next restart of port 8000 those three hidden inputs can be deleted.
- Worktree: 74 modified (was 67), 80 untracked. No schema, gate, approval, readiness or navigation change.

### Checks
Byte-exact verification of all 18 files against the specification; other 346 files unchanged by hash; ruff
check/format on `app.py` clean; `git diff --check` clean; affected tests (39 files) 255 passed before and 255 passed after; Chromium check of
the four forms on a not-restarted server (old code) and a restarted one (new code); five read-only page loads on
port 8000 returned 200. Full suite not repeated.

### Routing
L1 confirmation trace: local PASS. L2 approval-blocker inventory: local BUDGET_EXCEEDED / NO_CHANGES (unit sized too
large by the orchestrator; facts established by direct reading; not escalated). L3 replacements: local PASS.
Codex not used. Workers did not edit this file.

### Findings to act on (detail and evidence in the report)
- Real llama.cpp (`qwen3.8-27b-q6k`) fails 5 of 6 synthetic passages in the app's current request shape: the reply
  is cut at 1,024 tokens of hidden reasoning with an empty answer. With reasoning off per request: 5 of 5 valid.
- Following "Retry incomplete analysis" for an unreadable scan moves a Bid from task D back to task B.
- A manually added deliverable cannot be activated in the browser (JSON API only), so task E can dead-end.
- Suppliers, enquiries and quotations have no browser controls.
- An approval recorded with authority, evidence and decision still does not count without a published authority
  policy and a reviewed pricing baseline; the requirement itself is inferred from the governance level.
- An expected addendum is not closed when a release with the same reference is imported.
- Three pages overflow horizontally at 1366 px (`static/style.css:57`).

### Not done (design for review first)
Approval simplification, lighter Bid creation, import-and-analyse, Bid-summary suggestions, commercial start-empty,
record-offer-sent, Work ChatGPT document package. See report sections 4–7 and its implementation sequence.

## Confirmed decisions for the planned redesign — 2026-10-06 (documentation only; nothing implemented)

Recorded from Jason after the workflow review above. Detail, backend impact and acceptance criteria: report
section 11 (`~/contractiq-review/r1006/report/ContractIQ-workflow-review-2026-10-06.md`). Where this section
disagrees with the review entry above or with earlier design notes, **this section governs**.

### Decision 1 — Record offer sent
- Offers are prepared in Word/Excel. Provide a straightforward **Record offer sent** path: files sent, recipient,
  date, method. The existing Proposal Studio export / import / issue route is preserved as an optional path.
- Implementation notes (not done): `ProposalExchangeService.issue` and `issued_offer_baselines` cannot hold a
  directly sent offer (`candidate_id`, `export_id`, `package_id`, `receipt_id`, `manifest_sha256` are `NOT NULL`).
  Plan: two additive `IF NOT EXISTS` tables (offer-sent record, its files with name/size/SHA-256), append-only,
  audit in the same transaction, files through managed storage. `proposal_issued` (`app.py:802`) becomes true when
  either record exists. The record also freezes: documents used for the offer, the approval that applied, changes
  since that approval, qualifications/clarifications included, returnables omitted or qualified.

### Decision 2 — Changes after approval
- Changes after approval **do not invalidate the approval and do not block sending**. The original approval is
  preserved; changes are displayed as information. Jason may explicitly choose **Request approval again** when he
  considers a change substantial or risky. No acknowledgement is required to continue.
- This reverses current behaviour: `core/senior_approvals.py:262-286` (`legacy` / `stale` pricing-baseline
  problems) and the "history, not current approval authority" text in `templates/decisions.html` are to be removed.
  The approval rule becomes: unanswered → open; `required = 0` → clear; `required = 1` → clear at `APPROVED`.
  The snapshot stored with an approval is used for display only ("Changed since approval"), never in the rule.
  A re-request appends a Pending row referencing the earlier row; no approved row is ever edited.
- It also supersedes the review's own proposal (report 6.2 / 6.3 item 2: "Approval still applies" / "Ask again").

### Decision 3 — Qualifications, clarification requests, returnables
- Open qualifications and clarification requests **may accompany an offer when explicitly included**.
- Each required returnable must be **ready**, **intentionally omitted**, or **covered by a stated qualification**.
- **No additional approval process**: no reviewer or sign-off for qualifications, omissions or deviations.
- Existing rules this replaces (not changed yet): negotiation must be `RESOLVED` for Qualify / Reject /
  Clarification positions (`core/gate_service.py:115-125`); Reject / Clarification mapped to `UNRESOLVED`
  (`core/proposal_exchange_service.py:666-670`, `UNRESOLVED_COMMERCIAL_POSITION`); requirement response Clarify /
  Option `UNRESOLVED` (`:426-431`, `UNRESOLVED_REQUIREMENT`); deviation qualifications blocked until requirement
  `review_state = ACCEPTED` (`:470`, `:1178-1181`, `UNDISPOSED_QUALIFICATION`).
- Returnable states map to existing response values (Comply + Complete / Exclude / Deviate or Clarify with an
  included qualification); "include with this offer" needs one small additive, append-only selection.

### Pre-send checklist that results (the only things that stop "Record offer sent")
"Approval required?" answered (No, or Yes at Approved) · every required returnable in one of the three states ·
every expected addendum received or dismissed · files, recipient, date, method entered. Information only, never a
stop: changes since approval; open qualifications not included; negotiation state; unreviewed commercial topics.

### Revised implementation order
1. Dependable analysis and honest file labels (unchanged first step).
2. **Check and send** — simplified approval + Record offer sent + qualifications/returnable states, as one unit
   (moved up and merged; route: Codex for rules, migrations and the evidence record, local for templates).
3. Bid summary suggestions and lighter Bid creation. 4. Import-and-analyse, single document pass, document index.
5. Returnables → requirements in one click; missing deliverable/supplier buttons. 6. Commercial start-empty.
7. Later: highlights import, addendum comparison, conflicts, Work ChatGPT package, supplier enquiry.

### Assumptions made while recording these (to be confirmed before unit 2 is built)
A1 *Reject* is treated like *Qualify* (a stated deviation that may accompany the offer). A2 After "Request
approval again" the status is Pending and stays on the checklist until Approved. A3 The checklist must be clear
before the offer-sent record can be saved. A4 Qualifications not included are shown, not blocked. A5 "Intentionally
omitted" needs no reason. A6 Files sent are stored as protected copies. Still open from the review: whether a bare
"No" suffices for "Approval required?", and the 33-topic commercial list.

## Dependable discovery analysis and honest file handling — 2026-10-06 (unstaged, uncommitted, unpushed)

Scope: first unit of `docs/tasks/OPS-14-single-user-workflow-agreed-design.md` only. Not included: approval, offer-sent,
supplier or broader workflow redesign. Branch `local/ops-13a-bid-deliverables-discovery`, HEAD `40c985b`; nothing staged;
canonical worktree untouched; live port 8000 not restarted and its database byte-identical before and after. No schema
change, no new stored value, no migration.

### What changed
1. **Discovery request** (`core/llm_client.py`, `core/deliverable_discovery_llm.py`). New additive
   `LMStudioClient.complete_structured` returns the reply text with the server's `finish_reason`; `chat`, `complete` and
   `complete_json` are unchanged, so no other LLM consumer is affected. Discovery alone sends
   `chat_template_kwargs: {"enable_thinking": false}` and a JSON-schema `response_format`, with a 4096-token allowance.
   A reply cut off at the limit (`finish_reason == "length"`) is *truncated*; unparseable or list-less replies are
   *invalid*. Either gets **one** corrective retry (8192 tokens when truncated); a second failure is stored as the existing
   `INVALID_OUTPUT` with a `truncated: ` or `invalid: ` reason prefix. Transport failures are never retried here, so the
   three-failure endpoint circuit breaker, cancellation, per-passage persistence and idempotency are as before.
2. **Honest file handling at preview** (`core/analysis_capability.py` new; `BidPackageStorage.read_preview_file`,
   `BidPackageService.preview_capabilities`; `app.py` preview display; `_package_intake_section.html`). Each previewed
   file shows *Can be analysed*, *Stored only — this file type cannot be analysed (PDF, Word and Excel only)*,
   *No readable text found — OCR may be needed*, *Could not be opened …* or *Not checked in this preview …*. The preview
   calls no model and writes nothing; capability is display-only and changes no eligibility, governing-document
   selection or review decision. The extractor's page message no longer asserts "scanned image".
   Limits: 8 s total and 25 MB per file; beyond either a file is *Not checked* and a note says so.
3. **Retry loop removed** (`core/deliverable_discovery_progress.py` new; one read-only query in
   `core/deliverable_discovery_repository.py`; `app.py:_discovery_intake_facts`; `core/guided_workflow.py`,
   `core/bid_workflow.py`). Progress is derived per currently Included file from every run: a full analysis from any
   run counts; otherwise the best partial result. Unsupported / no-text files are counted separately and never offered
   an ordinary retry; a later failed job no longer replaces earlier results or moves the Bid back. Newly Included or
   never-analysed files still show as outstanding. There is no "once complete, always complete" rule.
4. **Presentation** (`core/deliverable_discovery_presentation.py` new; `deliverable_discovery_job.html`,
   `deliverable_discovery.html`; `static/style.css`). Plain statuses instead of state codes; one consistent set of file
   and passage counts that separates "text extracted" from "analysed by the model"; a reason and a "what to do" per
   file; Retry shown only when it can help; job, run and document-version identifiers moved into *Details*. Heading
   overflow fixed (`.page-heading > :first-child { min-width: 0 }`).

### Live-server note
The live process keeps the old Python until restarted, but reloads templates at once. Every new template variable is
therefore guarded with `is defined` and falls back to the previous markup; verified on two servers that were not
restarted and by GET on port 8000 (HTTP 200, no horizontal scroll). Until port 8000 is restarted it shows the old
counts and the old Retry behaviour, with the new headings.

### Real-model results (llama.cpp `http://10.0.0.10:8080`, `qwen3.8-27b-q6k`; isolated copy on port 8795; synthetic package)
- Before this change the same request shape lost 5 of 6 passages to truncation (reasoning consumed the allowance).
- Background job started by click from the Bid page: 11 Included files, 16 passages, **16 analysed, 0 unusable**, 171 s,
  23 findings; job ends "partly complete" only because two files cannot be read (a `.dwg` and a text-less PDF), each
  with an honest status; no Retry offered; the Bid moved on to task 3.
- All 11 returnables known to be in the synthetic package were found. All 26 citations behind the 25 final findings name the
  right file and locator, and their quoted text was re-extracted and found at that locator.
- Forced failures through the app: a truncating endpoint (3 jobs) and an unreachable endpoint (3 jobs) each left the
  23 findings and the Bid's position unchanged, showed "answer was cut off" versus "model could not be reached", and
  made exactly one corrective request per truncated passage (4096 then 8192 tokens). A real retry then analysed the two
  addendum files in 23 s (25 findings). The 9 already-analysed files were never re-sent.
- Repeat submissions: two submissions while a job ran returned the same job; one operation id posted three times
  created one job; no duplicate findings (0 duplicate keys).

### Extraction limitations found (not fixed here)
- **Post-award items proposed as Bid submissions: 8 of 25** — 7 as *Required* (certified GA drawings, inspection and
  test plan, IOM manuals, material/test certificates, from the "Documents after award" clause and the VDRL rows marked
  "after PO") and 1 correctly *Conditional* (performance security). No equipment was proposed as a submission.
- One vague finding ("Bid"); the same returnable found in two documents is listed twice when worded differently
  (data sheets ×3, performance curves ×2, Form F-02 ×2).
- The quoted text is the whole page / sheet passage, not the sentence; locators are page- or sheet-level.
- Conflicting closing dates in the package (cover letter, instructions, addendum) are carried per finding, not reconciled.
- A superseded data-sheet revision was analysed like any other Included file. No OCR.
- A valid reply with no findings is reported as "no returnables found in this file"; that is the model's answer, not
  proof of absence.

### Other limitations
- Preview check is bounded (8 s, 25 MB per file) and synchronous; one very large workbook can still take seconds.
- A retry job still re-checks unreadable files (cheap, no model call) and so can end "did not produce results"; the
  Bid's progress no longer depends on that, but the latest-analysis link shows that label.
- Owner-decision evidence line still quotes the latest job only. Lower sections of the review page (addendum flags,
  promoted sources) still show identifiers. The hidden `confirm_authoritative` shim from the earlier review remains.

### Delegation record
| Unit | Worker | Outcome |
|---|---|---|
| U3 progress from per-file results | Codex gpt-5.6-sol, high | PASS |
| U2 preview capability (classifier, safe read, display) | Codex gpt-5.6-sol, high | PASS |
| U1 discovery request | local qwen3.8-27b → Codex | local BUDGET_EXCEEDED at 1800 s, no changes; Codex PASS (first launch hung on stdin: orchestrator error) |
| U4a wording module | local → Codex → orchestrator | local BUDGET_EXCEEDED, no changes; Codex stopped by account usage limit (until 2026-10-10); written by the orchestrator |
| U4b page wiring and templates | orchestrator | Codex unavailable; local had exceeded budget on file-writing units |
| U5 test assertions for reworded pages | local qwen3.8-27b | PASS in about 10 minutes |
Orchestrator edits to delegated work: a guard in `_discovery_intake_facts`; a bare `assert` replaced by an
`INVALID_OUTPUT` outcome; the preview column hidden when old code cannot fill it; one test updated because unusable
output now costs two calls per passage (`test_nontransport_and_invalid_failures_do_not_trip_breaker[invalid]`).

### Verification
Full suite: see the line appended below. Scoped: `ruff check` clean on all changed files (`core/llm_client.py` unchanged
at its 21 earlier findings), `mypy --strict` clean on the three new modules, `deliverable_discovery_llm.py`,
`guided_workflow.py`, `bid_workflow.py`; `git diff --check` clean. Browser: Chromium (Playwright build 1234) for all
clicks and both sizes, 1366×768 and 1920×1080, no horizontal scroll on Bid, intake, preview, job, review, owner-decision,
Bids and My Day pages; real Chrome 153 (Windows, headless, separate profile) rendered the job page at both sizes.

### Rollback
Snapshot of every file before this unit: `~/contractiq-review/r1006/state/impl/pre-impl-snapshot.tgz` (hashes in
`hashes-before.json`, list in `changed-files.txt`). New files to remove: `core/analysis_capability.py`,
`core/deliverable_discovery_presentation.py`, `core/deliverable_discovery_progress.py`, their three test files,
`tests/unit/test_bid_package_preview_capability.py`, `docs/tasks/OPS-14-single-user-workflow-agreed-design.md`.
Files to restore from the snapshot: `app.py`, `core/bid_package_service.py`, `core/bid_package_storage.py`,
`core/bid_workflow.py`, `core/deliverable_discovery_extraction.py`, `core/deliverable_discovery_llm.py`,
`core/deliverable_discovery_repository.py`, `core/guided_workflow.py`, `core/llm_client.py`, `static/style.css`,
three templates (`_package_intake_section.html`, `deliverable_discovery.html`, `deliverable_discovery_job.html`),
nine test files, and this section of `HANDOFF.md`. No data migration to undo.

Full suite on the final state (`uv run python -m pytest -q -p no:cacheprovider tests`): **930 passed**, 0 failed, 7 min 19 s.
Servers started for this work (ports 8793, 8794, 8795 and two stand-in endpoints on 9102, 9103) were stopped after
checking each by port and command line; ports 8000, 8792 and 9101 were not touched.
