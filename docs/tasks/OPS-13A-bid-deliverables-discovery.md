# OPS-13A — Bid Deliverables Discovery

## Purpose and boundary

After a customer Bid package is ingested (OPS-11), help find, verify, assign and complete the
documents/forms the customer requires *with the Bid submission* (pre-award only). Post-award
vendor-document submission/execution tracking (TASK-12's existing supplier-facing deliverable
flows) is out of scope and untouched.

## Reuse inventory (written before any migration, per project convention)

| Concern | Existing authority | OPS-13A use |
|---|---|---|
| Authoritative Bid deliverable register | `core/deliverables.py::Deliverable` + `core/deliverable_repository.py::DeliverableRepository` (table `deliverable_items`, migration `task_12_vendor_data_control_v1`) | Confirmed proposals become real `Deliverable` rows: `direction=COMPANY_TO_CUSTOMER`, `lifecycle_phase=WITH_BID`. No new deliverable table. |
| Deliverable browser authoring flow | `app.py` `/deliverables`, `/deliverables/{id}`, `/api/deliverables/*`; `core/deliverable_service.py::DeliverableService` | Confirmation calls `create()` → `add_link(DOCUMENT_VERSION, ...)` → `activate()`, the exact existing sequence a human uses today. |
| Readiness/proposal-view integration | `core/proposal_exchange_service.py` lines ~340-347: already selects `deliverable_items` with `workflow_state<>'CANCELLED' AND lifecycle_phase IN ('WITH_BID','PRE_AWARD','BOTH') AND direction IN ('COMPANY_TO_CUSTOMER','SUPPLIER_TO_CUSTOMER_VIA_COMPANY')` | **No change needed.** An activated OPS-13A deliverable is picked up automatically — this is the entire "flows into readiness/proposal views" requirement, satisfied by reuse. |
| Bid Basis (current governing package documents) | `core/bid_package_repository.py::BidPackageRepository.basis_register()` / `bid_basis_snapshot_documents` (`basis_role` GOVERNING/QUALIFICATION/EXCLUSION) | Discovery runs against the current snapshot's GOVERNING document versions for a Bid — the same set OPS-09 already treats as authoritative supporting evidence. |
| Managed document bytes | `core/document_service.py::DocumentService.open_download(document_version_id) -> (DocumentVersion, BinaryIO)` | Read managed PDF/DOCX/XLSX bytes for deterministic extraction. No new storage adapter. |
| Controlled document identity | `core/document_control.py::ControlledDocument`, `DocumentVersion` (`storage_key`, `sha256_digest`, `media_type`, `original_filename`) | Source identity for every extraction unit and proposal citation. |
| Locator field naming precedent | `core/ops08.py` `commercial_position_versions` (`source_document_version_id`, `clause`, `section`, `page`, `source_locator`) | Mirrored field names for OPS-13A source citations. |
| Append-only "flag without overwrite" precedent | `core/bid_package_repository.py` `bid_addendum_directives` / `bid_addendum_dispositions` — self-FK `supersedes_*_id`, unique partial index so only the latest correction is live | Mirrored for addendum-affects-proposal/deliverable flags: a new row is appended, nothing is updated in place. |
| Bulk human-review decision precedent | `core/bid_package_intake.py::BulkFileDispositionCreate` / `BulkFileReviewItem` (concurrency token per item, single `operation_id`, atomic) | Mirrored for the bulk accept/reject/defer decision payload. |
| Idempotent run/replay precedent | `bid_intake_processing_runs` (`UNIQUE(bid_id, operation_id)`) | Mirrored for `deliverable_discovery_runs` — replaying an operation_id returns the existing run, never re-inserts proposals. |
| Local LLM client | `core/llm_client.py::LMStudioClient.complete_json()` (JSON-mode, markdown-fence stripping) | Reused unchanged for candidate proposal generation. This is the **first** OPS-numbered feature to call it; the OPS series has had no LLM step before now (confirmed by inventory — only the legacy, unrelated `analysis_engine.py` module used it). |
| Human-confirmation provenance | `core/schemas.py::Provenance` (`agent_name`, `model`, `source_document_id`, `source_location`, `human_confirmed`, `confirmed_by`) and `core/enums.py::Actor` (`HUMAN`/`AI`/`SYSTEM`) | Used as-is on every confirmed `Deliverable`: `created_by=Actor.HUMAN` (a human clicked accept), with `agent_name`/`model`/`source_document_id`/`source_location` recording the AI proposal's origin — no new provenance fields needed. |
| PDF/DOCX/XLSX libraries | `fitz` (PyMuPDF), `python-docx`, `openpyxl` — already dependencies (`core/document_processor.py`, `core/document_preprocessor.py`) | Reused directly. `core/document_processor.py`'s own extraction is **not** reused as-is: it joins page text into one string and drops per-page locators, which OPS-13A's provenance requirement needs. A new, narrow extraction module preserves page/paragraph/sheet-cell locators instead of duplicating that lossy path. |
| HTML-422-not-JSON precedent | `app.py` `/bids` GET handler: catches `ValidationError`, calls `render(template, status_code=422, error=..., ...)` | Mirrored for the discovery review-queue routes. |

### Missing facts (justifying the additive migration)

None of the above tables can represent: an extraction attempt's provenance/failure state, an
LLM proposal (pre-confirmation, strictly schema-checked, possibly rejected/deferred and kept as
evidence), which extraction units justified a given proposal, a discovery run's idempotency
identity, or an addendum's effect on an existing proposal/deliverable. These are new,
non-authoritative facts (except the run and addendum-flag identity rows, which are themselves
audit-shaped evidence, not obligations) and must not be modeled as new `Deliverable` fields —
`Deliverable` is a `frozen`-adjacent-in-spirit authoritative obligation, not scratch space.

## Additive migration (idempotent, `CREATE TABLE IF NOT EXISTS`, no destructive DDL)

Migration id: `ops_13a_deliverable_discovery_v1`, applied by a new
`DeliverableDiscoveryRepository`, following the exact `_migrate()` shape of
`DeliverableRepository`/`bid_package_repository`.

| Table | Purpose | Mutability |
|---|---|---|
| `deliverable_discovery_runs` | One discovery attempt: bid, Bid Basis snapshot used, operation_id (`UNIQUE(bid_id, operation_id)`), status, actor, timestamps | Insert-only |
| `deliverable_discovery_extractions` | One extraction unit: document_version_id, locator_type (PAGE/PARAGRAPH/SHEET_CELL), locator, bounded text excerpt, status (OK/UNREADABLE), failure_reason | Insert-only, regenerable cache (not authoritative evidence) |
| `deliverable_discovery_proposals` | One consolidated candidate deliverable: bid_id, run_id, title, category, criticality (REQUIRED/CONDITIONAL/UNCLEAR), condition_text, due_date (nullable = "not stated"), requested_format, requested_recipient, proposed_owner, dedup_key, created_at | Insert-only; a proposal's own facts never change after creation |
| `deliverable_discovery_proposal_sources` | Many-to-one: every source citation (document_version_id + locator + excerpt) backing one proposal — this is how duplicate mentions consolidate without losing evidence | Insert-only |
| `deliverable_discovery_review_events` | Append-only human decision per proposal: PENDING→ACCEPTED/REJECTED/DEFERRED, reviewer, rationale, `linked_deliverable_id` when accepted, `supersedes_event_id` self-FK (re-reviewing a DEFERRED item appends, never overwrites) | Insert-only, supersede-chain |
| `deliverable_discovery_addendum_flags` | Append-only: an addendum directive flags a proposal or an already-accepted deliverable as potentially affected, with `supersedes_flag_id` for corrections | Insert-only, supersede-chain |
| `deliverable_discovery_schema_migrations` | Migration marker | Insert-only |

Rollback/recovery: additive-only, so rollback is "stop writing to the new tables" — no data from
any existing table is altered. The migration itself is guarded by `CREATE TABLE IF NOT EXISTS`
and a single `INSERT OR IGNORE` marker row inside one `BEGIN IMMEDIATE` transaction, identical in
shape to `DeliverableRepository._migrate()`; if it fails partway the transaction rolls back and
no table is left partially created. Recovery from a bad run is simply re-running discovery with a
new `operation_id` — extraction/proposal rows are additive evidence, never referenced by FK from
any pre-existing authoritative table, so they can be ignored or (manually, out of band) deleted
without touching `deliverable_items` or anything upstream.

### One necessary, minimal, pre-existing-bug fix in scope

`DeliverableRepository.add_link()`'s `DOCUMENT_VERSION` target-lookup runs
`SELECT bid_id FROM document_versions WHERE document_version_id=?` — but `document_versions` has
no `bid_id` column (confirmed against its `CREATE TABLE` in `core/document_repository.py`; `bid_id`
lives on the parent `documents` row). This mapping is untested (`grep` confirms no test exercises
`DOCUMENT_VERSION` linking) and would raise `sqlite3.OperationalError` the first time anything
tries to link a deliverable to a document version — which is exactly what confirming an OPS-13A
proposal must do. Fixed to join through `documents` for `bid_id`, mirroring the already-correct
pattern at `core/bid_package_repository.py:1009`
(`SELECT v.*,d.bid_id,d.control_managed FROM document_versions v JOIN documents d ...`). This is
a one-line query fix required for OPS-13A's core capability to function, not a redesign; the
identical latent bug also exists in `contract_risk_repository.py` and `commercial_repository.py`
and is *not* touched — out of scope, flagged in HANDOFF.md for a separate ticket.

## Workflow

1. From a Bid's Package Intake section, a **"Find required Bid submissions"** action links to
   `GET /bids/{bid_id}/deliverable-discovery`.
2. `POST /bids/{bid_id}/deliverable-discovery/run` (idempotent on `operation_id`, a hidden form
   field regenerated per page load) triggers: locate the current Bid Basis snapshot → for each
   GOVERNING document version, deterministic PDF/DOCX/XLSX extraction with locators → bounded
   local-LLM candidate proposal generation per extraction unit → schema validation and
   source-id verification (only real, already-extracted `document_version_id`+locator pairs may
   be cited; anything else is dropped and counted, never silently accepted) → consolidation of
   duplicate mentions by `dedup_key` (normalized title+category+bid) into one proposal with
   multiple source citations → persisted as `PENDING` proposals.
3. The review queue groups proposals (unambiguous MANDATORY-with-single-source rows first) and
   supports bulk-accept of a selected, unambiguous subset in one atomic operation, or per-row
   accept/reject/defer with a rationale. Accept creates a real `Deliverable`
   (`workflow_state` DRAFT→link→ACTIVE) via the existing service; reject/defer only append a
   review event — no obligation is created, and readiness/proposal views are unaffected.
4. An addendum directive that touches a governing document already cited by a proposal or an
   accepted deliverable appends a `deliverable_discovery_addendum_flags` row for human review;
   it never mutates the proposal or the deliverable.
5. Export produces a downloadable, self-contained JSON package (schema-versioned, no raw file
   bytes, only the already-extracted bounded excerpts) for a future Work ChatGPT import. Import
   validates the same schema and rejects any proposal whose cited `document_version_id`+locator
   is not found in this Bid's own extraction cache — this is what makes fabricated or cross-Bid
   findings unimportable — then persists into the same review queue under a fresh run.

## Out of scope (unchanged)

Supplier enquiries, general requirement extraction, proposal generation/issue, OPS-10,
post-award TASK-12 vendor-facing deliverable flows, a live Work ChatGPT network connection, OCR.

## Known limitation

No OCR. A scanned/image-only PDF page is recorded as an `UNREADABLE` extraction unit (explicit,
visible in the review queue) rather than silently treated as "no requirement found" — but its
actual content is not recovered in this release.

## Implementation status

Implemented and verified as of 2026-09-28; left unstaged and uncommitted per instruction. See
`HANDOFF.md`'s OPS-13A section for the full change list, verification results (544/544 full
suite, scoped ruff/mypy/`git diff --check` clean), live-server smoke check, known limitations,
and the manual acceptance checklist. One design change from this document during implementation:
a necessary minimal fix to `DeliverableRepository.add_link()`'s pre-existing `DOCUMENT_VERSION`
join bug, and one small additive read-only method `BidPackageRepository.list_current_directives()`
— both documented in HANDOFF.md with rationale.

---

## OPS-13A-F — Owner pursuit decision and issue-phase senior approvals (acceptance contract)

Source: OPS-13A intake/decision-flow review, Addendum B (`.ops13a-review-out/OPS-13A-intake-decision-flow-review.md`).
Defects being corrected (all reproduced in Chromium and by service calls before this change):
(1) a `BID_NO_BID` approval row with `obtained=True` and free text "Do not pursue" satisfied G1 while the Bid stayed Active, and
the Bid then reached `ISSUED`; (2) `NO_BID` was never settable; (3) pending senior approvals (G4) blocked even draft export, and
cascaded into G5; (4) `approvals_clear` was true for an empty approval set; (5) My Day and the Bid workspace chose different next actions.

### F.1 Domain decisions (fixed; do not re-litigate)

1. **Owner decision is a new append-only fact**, not an `Approval`. Table `bid_owner_decisions` (additive, `CREATE TABLE IF NOT EXISTS`,
   immutable by trigger like the other evidence tables): `decision_id`, `bid_id`, `decision` ∈ {`PURSUE`,`DO_NOT_PURSUE`}, `reason` (required,
   1–500 chars), `decided_by`, `decided_at`, `supersedes_decision_id` (nullable, unique per predecessor), `evidence_snapshot_json`
   (frozen summary of the evidence shown when deciding), `provenance_json`. Pydantic model `frozen=True`. Current decision = the row no
   other row supersedes. Changing a decision inserts a new row naming `expected_current_decision_id`; stale/missing expectation → `ValueError`.
   Re-recording the identical current decision → `ValueError` (no no-op write). The decision, the audit-log row and (for DO_NOT_PURSUE or a
   reopening PURSUE) the `bids.status` change are written in **one transaction**.
2. **Bid status**: `DO_NOT_PURSUE` → `NO_BID`. A later `PURSUE` that supersedes a `DO_NOT_PURSUE` → `ACTIVE`. A decision change is refused
   (`ValueError`) when the Bid is `SUBMITTED`, `WON` or `LOST`, or when a customer issue baseline already exists.
3. **G1** (`g1.owner_pursuit_decided`) is met iff the **current** owner decision is `PURSUE` (Level 0 exempt as today). The `approvals` table is
   **never** consulted for G1. Legacy `BID_NO_BID` approval rows are preserved untouched, listed as "Legacy approval record — not an owner
   decision", and are never converted. G1 is evaluated only for Bids whose status is `ACTIVE`/`HELD`; `SUBMITTED`/`WON`/`LOST` Bids are not
   re-gated retroactively (policy assumption — report it). `NO_BID` is a stopped state, not a blocker to "resolve".
4. **Legacy default state**: a Bid with no owner-decision row has state `NO_DECISION` ("No owner decision recorded"). No data migration
   writes decision rows. G1 is unmet for such Bids, exactly as it was unmet for a Bid without an obtained approval.
5. **Phases.** Gate conditions are tagged. *Development-phase* conditions block draft export, approve-for-issue, customer issue and
   handover (as today). *Issue-phase* conditions — `g4.margin_approved`, `g4.required_approvals`, `g4.high_findings_have_authority`, and a new
   `g6.senior_approvals_current` — block **only** approve-for-issue, customer issue and handover. `g5.prior_gates_passed` must evaluate
   G2–G4 **excluding** issue-phase conditions. `ReadinessReport` exposes development blockers and issue-phase blockers separately; the
   readiness verdict (HOLD/CLEAR) is driven by development blockers only; issue-phase blockers are reported as "pending before issue".
6. **Required senior approvals are derived, not self-declared.** Required when any of: Bid Level ≥ 2 under a temporary governance-level
   rule; any commercial position with an explicit `required_approver`; any existing required approval row; or any open approval route.
   `QUALIFY` and `REJECT` require commercial review but do not themselves create a senior-approval requirement, and no approver role is
   inferred from a disposition or value. No monetary-value or margin-percentage threshold is enforced. When required,
   **approve-for-issue and customer issue fail unless** (a) a published, effective authority policy exists and covers the approval role/type
   through the existing authority-policy/approval-route rules, (b) every derived requirement has matching obtained evidence with authority,
   evidence and decision, and (c) each approval identifies the currently selected scenario-baseline record. A selected pricing baseline alone
   is not authority. Legacy approvals with no baseline identifier remain untouched history and never satisfy current authority; selecting a
   later baseline makes an earlier approval non-current. `approvals_clear` means "required set complete and all satisfied"; an empty approval
   set with a non-empty derived requirement is **not** clear.
7. **Overrides.** The generic readiness override must be refused for `g1.owner_pursuit_decided` and for every issue-phase condition.
8. **Draft export** is allowed when only issue-phase conditions are unmet, and is clearly marked **DRAFT — pending before issue: …** in the
   UI and download filename. The frozen exchange-contract hash/format must not change. If the package projection cannot be built without a
   pricing basis, draft export is refused with the projection error and this is recorded in "Implementation status" (do not fake it).
9. **Gate-approval form**: `/decisions` no longer offers `BID_NO_BID`; a POST naming it is refused with a message pointing to the owner
   decision. Margin/Legal/Credit/Finance/Executive remain, relabelled as issue-phase senior approvals.
10. **Next action** has one source of truth in workflow order: (1) review received files → (2) initial requirements assessment (discovery run
    or finished with any outcome, or ≥1 customer requirement) → (3) owner decision → existing stages. My Day and the Bid workspace both use it.
    The owner decision is never shown as an intake blocker before steps 1–2, but the decision page is reachable (secondary link) at any time
    after a package exists, and shows evidence and unresolved questions (undecided files, partial/failed discovery files, zero requirements,
    zero accepted deliverables). A finished-with-failures discovery does **not** prevent the decision.
11. `NO_BID` Bids leave the active My Day queue (existing `HISTORY_BID_STATUSES`), the workspace shows "Stopped by owner decision — <reason>"
    with one primary action "Record a new decision", and downstream stages show Not applicable.

### F.2 State-transition matrix

| # | Current owner decision | Action | Result | Bid status | Error / message |
|---|---|---|---|---|---|
| T1 | NO_DECISION | record PURSUE + reason | decision row (no predecessor), audit, G1 met | ACTIVE | — |
| T2 | NO_DECISION | record DO_NOT_PURSUE + reason | decision row, audit, same transaction sets status | NO_BID | — |
| T3 | PURSUE | record DO_NOT_PURSUE, `expected_current_decision_id` correct | new row supersedes; status change; audit | NO_BID | — |
| T4 | DO_NOT_PURSUE | record PURSUE + reason, expected id correct | new row supersedes; audit; status restored | ACTIVE | — |
| T5 | any | same decision as current | refused | unchanged | `ValueError` "already the current decision" |
| T6 | any | stale or missing `expected_current_decision_id` (when a current one exists) | refused, nothing written | unchanged | `ValueError` |
| T7 | any | empty/oversize reason, unknown decision value | refused | unchanged | `ValueError` |
| T8 | PURSUE | Bid status SUBMITTED/WON/LOST or issued baseline exists | refused | unchanged | `ValueError` |
| T9 | any | update/delete a decision row | refused by trigger | unchanged | DB error "immutable" |
| T10 | any | audit write fails mid-transaction | nothing persisted, status unchanged | unchanged | exception propagates |
| T11 | legacy approval rows only | open Bid | state NO_DECISION; legacy rows listed, unchanged | unchanged | — |

### F.3 Gate-action matrix (the acceptance contract; D = development-phase blockers present, I = issue-phase unmet)

`ALLOWED` = no refusal from this contract (other unrelated blockers may still refuse). `REFUSED` = service raises `ValueError` and nothing is written.

| # | Scenario | G1 | Draft export | Approve-for-issue | Customer issue | Handover | Next action (My Day = workspace) |
|---|---|---|---|---|---|---|---|
| G-1 | NO_DECISION, files unreviewed | unmet (not shown as blocker yet) | REFUSED (G1) | REFUSED | REFUSED | not ready | Review received files |
| G-2 | NO_DECISION, files reviewed, no requirements assessment | unmet | REFUSED | REFUSED | REFUSED | not ready | Run discovery / add requirement (assessment) |
| G-3 | NO_DECISION, files reviewed, assessment done (even PARTIAL discovery) | unmet | REFUSED | REFUSED | REFUSED | not ready | Record owner decision |
| G-4 | PURSUE, D=0, I=0 (Level 0/1, nothing exceptional) | met | ALLOWED | ALLOWED | ALLOWED | ready only after ISSUED chain | next stage in workflow order |
| G-5 | **DO_NOT_PURSUE** (status NO_BID) | not met | REFUSED "stopped by owner decision" | REFUSED | REFUSED | not ready (stopped) | Record a new decision (stopped) |
| G-6 | Later changed DO_NOT_PURSUE → PURSUE | met | ALLOWED subject to D | ALLOWED subject to D, I | same | same | next stage |
| G-7 | Later changed PURSUE → DO_NOT_PURSUE after a candidate was APPROVED_FOR_ISSUE | not met | REFUSED | **REFUSED** (re-assessed) | **REFUSED** | not ready | Record a new decision |
| G-8 | PURSUE, senior approval required, **no authority policy** | met | ALLOWED (marked DRAFT) | REFUSED "authority policy not published" | REFUSED | not ready | continue draft / configure policy |
| G-9 | PURSUE, required, policy present, **pricing basis missing** | met | ALLOWED iff projection valid (else REFUSED with projection error; record which) | REFUSED "pricing basis missing" | REFUSED | not ready | select pricing basis |
| G-10 | PURSUE, required, policy present, **approval missing/pending/rejected/stale/expired** | met | ALLOWED (marked DRAFT) | REFUSED | REFUSED | not ready | obtain senior approvals |
| G-11 | PURSUE, all required approvals obtained against current basis | met | ALLOWED | ALLOWED | ALLOWED; issued baseline freezes approval references | ready | handover |
| G-12 | Old exploit: Level 3, `BID_NO_BID` approval obtained with text "Do not pursue" + obtained MARGIN row, no owner decision | **unmet** | **REFUSED** | REFUSED | REFUSED | not ready | Record owner decision (the old row is ignored) |
| G-13 | Old exploit via browser: POST `/decisions/gate-approvals` with `approval_type=bid_no_bid` | — | — | — | — | — | refused with message pointing to the owner decision; nothing written |
| G-14 | Generic readiness override requested for `g1.owner_pursuit_decided` or any issue-phase condition | refused | — | — | — | — | `ValueError` |
| G-15 | Legacy Bid (approval rows, no decision), status ACTIVE | unmet | REFUSED | REFUSED | REFUSED | not ready | follows G-1..G-3 |
| G-16 | Legacy Bid status SUBMITTED/WON/LOST | not re-gated | n/a | n/a | n/a | unchanged from today | unchanged |
| G-17 | Draft export while I>0 | — | record + UI + filename marked DRAFT; frozen contract hash unchanged | — | — | — | — |
| G-18 | `approvals_clear` with derived requirement and empty approval set | — | — | REFUSED | REFUSED | — | — |

Every REFUSED row must be proven by (a) a direct service-call test and (b) where a route exists, a rendered/route test. The rendered
owner-decision form must show: Bid context, evidence available, unresolved questions, a two-outcome choice, one reason field, no
Authority/Evidence fields, decision history, and (when stopped) the "Record a new decision" explanation.

### F.4 Delegation units (sequential; each verified before the next)

- **F-A** Domain: model, additive migration + immutable triggers, repository/service (one-transaction write + audit), `GateContext`/G1 change, legacy default, override refusal, tests for T1–T11 and G-12, G-14, G-15, G-16.
- **F-B** Issue phase: phase tagging, ReadinessReport split, G5 cascade, derived requirements + policy/pricing checks, `approvals_clear`, draft marker, proposal export/approve/issue/handover re-wiring, tests for G-4..G-11, G-13 (service part), G-17, G-18.
- **F-C** Presentation: owner-decision route and template, `/decisions` form change, unified next action (My Day = workspace), stopped-state workspace, evidence/unresolved panel, draft labelling, route/render tests for G-1..G-3, G-5, G-13.

### F.5 Implementation status (OPS-13A-F)

Implemented against F.1-F.3 above; nothing in the manual database was migrated (verified on a copy only).

- **Domain (F-A):** `core/owner_decisions.py`, `core/owner_decision_repository.py` (table `bid_owner_decisions`, immutable + single-current + supersession-scope triggers, one transaction for decision + audit + `bids.status`), `core/owner_decision_service.py`, `core/owner_decision_evidence.py` (frozen evidence snapshot, shared `initial_requirements_assessment_available`). G1 is `g1.owner_pursuit_decided`; it never reads `approvals`.
- **Issue phase (F-B):** `core/readiness_phases.py` (phase tags), `core/senior_approvals.py` (derived requirements), `ReadinessReport.issue_blockers`, G5 excludes issue-phase conditions, new `g6.senior_approvals_current`, proposal `assess` / `export_package` / `approve_candidate` / `issue`, draft marker (`is_draft`, `pending_before_issue`, `DRAFT-` filename), handover consumes issue-phase blockers, overrides refused for G1 and issue-phase conditions.
- **Presentation (F-C):** owner-decision page and routes, `/decisions` refuses `bid_no_bid`, ten-stage workflow with an Owner decision stage, My Day = workspace next action, stopped-state workspace, "Before issue" items on the Bid page, draft notice on the issue-control page.
- **G-9 finding:** the package projection builds without a pricing basis (`"selected_pricing_scenario":null`), so a draft export without a pricing basis is allowed and marked; approve/issue refuse.
- **Policy clarification:** Level >= 2 uses a temporary governance-level margin-approval rule only; value and margin thresholds are not enforced. Commercial positions derive senior approval only from an explicit `required_approver`. `SUBMITTED`/`WON`/`LOST` Bids are not re-gated retroactively. Legacy `bid_no_bid` approval rows remain "not an owner decision" and are never converted. A selected baseline is not approval authority: current authority also requires baseline-bound evidence and an effective published policy covering the role/type.
- **Defects found by verification and fixed:** the evidence collector selected a non-existent `files_failed` column (caught by the UI tests; verified against the real schema read-only); the control status/button treated issue-phase items as blocking draft export; the download route ignored `suggested_filename`; the My Day card headlined G1 before its step; the draft notice showed on a stopped Bid.

### F.6 Deferred inputs (not implemented, need authoritative facts)

- Monetary value bands.
- Margin-percentage thresholds.
- Exceptional-term taxonomy.
- Role/approver mapping for `required_approver`.
- Authority-policy authoring (roles, stages and rules). The APIs can create and publish supplied policies, but no default policy or authoring form is invented.
- Approval expiry and re-approval triggers other than the specified selected-baseline change.
