# CIQ Stage 3 — Bid Execution and Readiness

Branch `ciq/stage3-bid-execution`, based on `89dd350` (Stage 2.1 acceptance).
Workers implement **only the unit named in their task** (S3-1 … S3-6 below). Stage 4 is out of scope.

## Purpose

Turn confirmed RFP requirements into a manageable bid-preparation process. A Bid Manager opens an
active Bid and sees what must be submitted, what must be obtained from manufacturers, what must be done
before bidding, who owns each item, when it is due, what is incomplete / blocked / overdue, which
commercial issues remain, and whether the Bid is ready for final submission **review**.
Build on the existing registers. Do **not** build another task manager.

Governance (do not model beyond this): the Bid Manager owns the Bid (`Bid.bc_owner`) and assigns tasks and
deadlines directly. No new permissions, no CRM, no approval hierarchy. Go/No-Go and approvals stay explicit
human decisions. Readiness is **advisory**. Nothing in this stage approves, submits or authorizes a Bid.

## Standing constraints (every unit)

- Migrations are additive: `CREATE ... IF NOT EXISTS`, guarded `ALTER`, never drop/rename/alter existing
  tables. Existing databases must open and work unchanged.
- Every mutation and its audit-log row are written in the **same** transaction (mirror
  `DeliverableRepository._audit` / `create`: `BEGIN IMMEDIATE`, write, audit, commit, rollback on error).
- Guard/validation failures raise `ValueError` with a message that says what went wrong **and how to fix
  it**. Never a bare `assert`, never a silent no-op, never a boolean standing in for failure.
- Stage 3 never modifies Stage 1/2/2.1 behaviour, the lifecycle categories, the discovery review screen's
  semantics, the gate engine or `core/readiness.py`. Native fields of existing registers
  (`work_items`, `deliverable_items`, `vendor_bid_requirements`, `commercial_positions`) are read or changed
  **only** through their existing services (`WorkItemService`, etc.). Stage 3 never edits them with raw SQL.
- No new LLM calls. Deterministic code only.
- **A date or an owner is never inferred.** Internal due dates and responsible persons come only from a user
  action (or from a value an existing register already holds from a verified source, which is shown with its
  origin and never copied into a Stage 3 field automatically).
- No internal identifiers in rendered text (no `WI-…`, `DEL-…`, `DDP-…`, SQL column names, enum names such as
  `IN_PROGRESS`). Ids appear only inside URLs. Use business wording ("Not started", "Waiting on a prerequisite").
- Files stay under ~300 lines where practical; new Python has full type hints and docstrings on classes and
  public methods; `ruff check` + `ruff format` clean on changed files; `mypy --strict` clean on new core modules
  (add them to `[tool.mypy] files` in `pyproject.toml`).
- Tests use temp databases; never touch port 8000, `~/contractiq-manual/` or any real database. No test makes a
  network call. Run `uv run python -m pytest` (plain `uv run pytest` fails to import `scripts`).
- Do not commit. Do not edit `HANDOFF.md`. Mirror a named existing pattern and **say which one** in the final
  message.

## Reuse map (what already exists — do not duplicate)

| Need | Existing mechanism |
|---|---|
| Actions / My Work | `work_items` via `WorkItemService` / `WorkItemRepository` (`core/work_items.py`, `core/work_item_service.py`). Statuses `OPEN, IN_PROGRESS, WAITING, BLOCKED, COMPLETED, CANCELLED`; priority `LOW..CRITICAL`; `due_date`; `completion_outcome`, `completion_evidence`; optimistic `version`. Pre-bid actions accepted from findings arrive as work items whose `details` contain the marker `[DISC:<proposal_id>]` (`origin_marker` in `core/deliverable_discovery_destinations.py`). |
| Returnables | `deliverable_items` (`core/deliverables.py`, `DeliverableRepository`). "With the Bid" = `lifecycle_phase in {WITH_BID, BOTH}`; `criticality` MANDATORY / CONDITIONAL / INFORMATIONAL; `condition_active`; `fixed_due_date` when `due_basis = FIXED_DATE`; `deliverable_links` (source links); `deliverable_submissions` (evidence). POST_AWARD deliverables exist and are **not** bid returnables. |
| Manufacturer inputs | `vendor_bid_requirements` joined to `vendor_bid_packages.bid_id` (`core/vendor_document_control.py`, `VendorDocumentRepository`). Outstanding = `required` and `applicable` and verification status not obtained. Reuse `ReadinessAttention` semantics from `readiness_for`. |
| Commercial / clarifications | `commercial_positions` and their current revision (`core/ops08.py`: `PositionDisposition`, `NegotiationState`, `due_date`, `owner`). |
| Approvals | `core/approval_*`, `core/senior_approvals.py`, `evaluate_readiness` (`core/readiness_service.py`) conditions such as `g4.required_approvals`, `g4.margin_approved`, `g6.senior_approvals_current`. Read-only. |
| Source traceability | `deliverable_discovery_destinations` (finding → record), `deliverable_discovery_proposal_sources` / `_citation_quotes` (document, locator, verified quote), `deliverable_links`, ops07 `link_work_item`. |
| Evidence | Controlled document versions (`core/document_repository.py`, `core/document_control.py`) and `deliverable_submissions.evidence_mode`. |
| Audit | `audit_log(entry_id, bid_id, actor, action, detail, timestamp)`. |
| Actor | `LOCAL_ACTOR` in `app.py` (single-user design, `docs/tasks/OPS-14-single-user-workflow-agreed-design.md`). |

## Data model (additive; created by `BidExecutionRepository._migrate`, `IF NOT EXISTS`)

Subject kinds (`ExecutionSubjectKind`): `WORK_ITEM`, `RETURNABLE`, `MANUFACTURER_INPUT`. URL slugs:
`work-item`, `returnable`, `manufacturer-input`.

1. **`bid_execution_records`** — one mutable, versioned overlay row per subject, created lazily on first
   assignment/edit: `execution_id` PK, `bid_id`, `subject_kind`, `subject_id`, `responsible` (free text person
   label, nullable), `contributors_json` (JSON list of text), `internal_due_date` (ISO, nullable),
   `prep_status` (nullable; used for RETURNABLE only), `priority` (nullable; RETURNABLE and MANUFACTURER_INPUT
   only), `blocked_reason` (nullable; RETURNABLE), `completion_evidence` (text, nullable),
   `evidence_document_version_id` (nullable), `version` (>=1), `created_at`, `updated_at`, `updated_by`;
   `UNIQUE(subject_kind, subject_id)`; `CHECK` on kind and status enums; hard-delete trigger aborts.
   For a WORK_ITEM subject the native fields stay authoritative for status, priority, due date and
   completion evidence; the overlay carries only responsible and contributors.
2. **`bid_execution_notes`** — insert-only progress notes: `note_id`, `bid_id`, `subject_kind`, `subject_id`,
   `body` (non-empty, <= 4000), `created_at`, `created_by`; triggers block UPDATE and DELETE.
3. **`bid_execution_dependencies`** — `dependency_id`, `bid_id`, `item_kind`, `item_id`, `prerequisite_kind`,
   `prerequisite_id`, `note`, `created_at`, `created_by`, `removed_at`, `removed_by`;
   partial unique index on `(item_kind,item_id,prerequisite_kind,prerequisite_id) WHERE removed_at IS NULL`;
   hard-delete trigger aborts; a removed row may be set once (`removed_at IS NULL` -> value), never cleared.

"Effective" values (rule used everywhere, tested): `responsible` = overlay.responsible, else the native
owner of the subject shown with the label "(from the register)" — `deliverable_items.owner` for a
returnable, `vendor_bid_requirements.internal_owner` for a manufacturer input; never written back.
**Caution:** `deliverable_items.owner` and package `internal_owner` may have been filled by RFP discovery or
package creation; the UI must label an effective owner that did not come from the overlay as "from the
register" so the Bid Manager can confirm or replace it. Nothing is assigned automatically.

Status model — **one display model, no new competing status enum in storage**. `PrepStatus` (display):
Not started / In progress / Blocked / Complete / Not required, plus the separate derived flag
`waiting_on_prerequisite`.
- WORK_ITEM: derived from native status — OPEN→Not started, IN_PROGRESS→In progress, WAITING→In progress
  (also show "Waiting on <party>"), BLOCKED→Blocked, COMPLETED→Complete, CANCELLED→Not required. Changes go
  through `WorkItemService.transition_work_item` / `edit_work_item`; keep its required fields (blocker +
  resolution owner for Blocked, completion outcome for Complete, cancellation reason for Not required) and
  ask for them in the form.
- RETURNABLE: stored overlay `prep_status` (default Not started when no overlay). `Complete` requires a
  completion reference (typed text) **or** an evidence document version that exists (validate through the
  document repository); `Blocked` requires `blocked_reason`; `Not required` requires a note (use the notes
  table). A deliverable whose `workflow_state = CANCELLED` shows Not required; `condition_active = False`
  shows Not required with the reason "condition not active". The deliverable's own lifecycle is untouched.
- MANUFACTURER_INPUT: **derived, read-only** from native verification: CONFIRMED_COMPLIANT and
  CONFIRMED_WITH_EXCEPTION→Complete; NOT_APPLICABLE or `applicable = 0`→Not required; AWAITING_MANUFACTURER→
  In progress; CLARIFICATION_REQUIRED and CANNOT_COMPLY→Blocked; NOT_REVIEWED→Not started. The item page
  links to the existing manufacturer-requirement screen to change verification.

Internal due date rule: WORK_ITEM → `work_items.due_date`; RETURNABLE and MANUFACTURER_INPUT →
overlay `internal_due_date`. Validation (service): a date after the Bid's `customer_due_date` is refused with
a fix message; a past date is allowed. Customer deadline shown for a returnable = `fixed_due_date` when
`due_basis = FIXED_DATE` (label "stated in the RFP"), else the Bid closing date (label "Bid closing").

Overdue = effective internal due date < `as_of` (a date argument, never a hidden clock) and display status not
Complete / Not required. Unassigned = no effective responsible and display status not Complete / Not required. Waiting on a prerequisite = at least one
active dependency whose prerequisite display status is not Complete / Not required.

## Dependencies (S3-2)

Allowed between any two subjects **of the same Bid** (all kinds). Self-dependency refused. Duplicate active
edge refused with a clear message. **Circular dependencies are prevented**: before inserting the edge, inside
the same `BEGIN IMMEDIATE` transaction, walk the active edges from the prerequisite; if the item is reachable,
refuse and name the loop in business wording ("A would depend on B, which already depends on A"). Removing an
edge sets `removed_at/removed_by` with an audit row. Completing a prerequisite **never** changes a dependent
item. Display of blocked items = derived flag above, never a stored status change.

## Unified item projection (S3-3)

`ExecutionItem` (pydantic, frozen) — one row shape for the three subject kinds and, read-only, for commercial
rows: `kind`, `subject_id` (URL use only), `bid_id`, `title`, `description`, `responsible`,
`responsible_origin` (`"assigned"`, `"register"`, or `None`), `contributors`, `internal_due_date`,
`customer_deadline`, `customer_deadline_label`, `status` (PrepStatus), `status_label`, `priority`,
`mandatory` (returnables), `critical` (priority HIGH/CRITICAL, or a mandatory returnable), `overdue`,
`unassigned`, `waiting_on_prerequisite`, `prerequisites` (title, status label, item URL), `dependents`,
`source_label` (document and locator when known), `source_url`, `evidence`, `last_updated`, `item_url`,
`origin` ("From RFP finding" when it traces to a finding, else "Added manually").
`ExecutionFilter` values (the only filters, shared by lists, dashboard tiles and readiness):
`all`, `outstanding`, `in_progress`, `blocked`, `complete`, `unassigned`, `overdue`, `waiting`, `not_required`.
`blocked` = display status Blocked **or** waiting on a prerequisite.

Counting rule (the reconciliation contract, tested): every dashboard count is `len(items(kind, filter))` from
the same function that renders the linked list. No second query for totals.

Commercial rows (read-only): `commercial_positions` for the Bid; **Unresolved clarification** = current
disposition CLARIFICATION_REQUIRED and negotiation state not RESOLVED; **Decision needed** = disposition
NOT_REVIEWED with a non-empty customer position, or REJECT / QUALIFY without a recorded approver where the
position requires one (use the existing ops08 attention/blocker logic, do not reinvent it). Each links to the
existing position screen.

## Dashboard (S3-3 projection, S3-5 page)

Header: customer, project, bid reference (`bid_id`), submission deadline (date, plus time and zone when
recorded, otherwise "time not recorded"), bid owner (`bc_owner`), bid status, days to deadline.
Tiles, each `count` + link to the filtered list (`/bids/{bid_id}/execution/<list>?state=<filter>`):
returnables (total, outstanding, complete, blocked, unassigned), manufacturer inputs (outstanding), pre-bid
actions (outstanding), unresolved clarifications, commercial decisions needed, overdue (all kinds), blocked
(all kinds), readiness verdict + blocking count. Optional/post-award counts appear as small informational text
("n post-award obligations recorded — not bid blockers"), never as a tile that reads like a problem.

## Readiness (S3-4) — `core/bid_execution_readiness.py`, pure function of a facts object

`evaluate_submission_readiness(facts) -> ExecutionReadiness` with `findings: list[ReadinessFinding]`
(`severity` BLOCKING | WARNING | INFO, `code`, `headline`, `count`, `detail`, `items` (title + item URL),
`resolve_label`, `resolve_url`), `verdict` and `advisory` text. No clock: `as_of` is a parameter. No LLM.

| Check | BLOCKING | WARNING | INFO |
|---|---|---|---|
| Mandatory returnables not Complete / Not required (WITH_BID/BOTH, MANDATORY, not cancelled, condition active) | each | — | optional / conditional-inactive / informational counts |
| Missing responsible person | on a mandatory returnable or a CRITICAL action | other required items | — |
| Overdue | CRITICAL / HIGH actions and mandatory returnables | other overdue items | — |
| Manufacturer inputs (required, applicable, not Complete) | Blocked (clarification / cannot comply) or not started or awaiting, if required | — | non-required |
| Mandatory clarifications | position CLARIFICATION_REQUIRED with a non-empty customer position, unresolved | same without a customer position | resolved count |
| Commercial / contractual decisions | — | decision needed (see above) | — |
| Recorded approvals where **explicitly required** | the existing approval conditions are active blockers in `evaluate_readiness` | — | none |
| Unresolved blockers | work item Blocked with HIGH/CRITICAL priority, or a mandatory item waiting on an incomplete prerequisite (name the prerequisite) | other Blocked / waiting items | — |
| Post-award obligations (`POST_AWARD`) and optional items | **never** | **never** | counts only |

A post-award obligation becomes a blocker **only** through its bid-stage action: an incomplete prerequisite
(any kind, including one that traces to a post-award finding) of a mandatory bid-stage item is reported as
the BLOCKING "waiting on prerequisite" finding. The post-award item itself is never BLOCKING.
Verdict: `NOT_READY` (>= 1 BLOCKING), `READY_WITH_WARNINGS`, `READY_FOR_REVIEW` (no BLOCKING or WARNING).
Wording: "Advisory — the Bid Manager decides." No button, field or route may approve, submit or authorize.

## Screens (S3-5)

Routes live in `core/bid_execution_routes.py` (`register_bid_execution_routes(app, *, render, ...)`), wired from
`app.py` with a few lines (mirror how `app.py` instantiates services); templates `templates/bid_execution_*.html`;
styles appended to `static/style.css`.
- `GET /bids/{bid_id}/execution` dashboard.
- `GET /bids/{bid_id}/execution/{returnables|manufacturer-inputs|actions|commercial|overdue}?state=<filter>` lists
  (returnables list has the seven practical filters All / Outstanding / In progress / Blocked / Complete /
  Unassigned / Overdue as tabs with counts; `overdue` is all kinds).
- `GET/POST /bids/{bid_id}/execution/items/{kind-slug}/{subject_id}` item page and forms (assign responsible +
  contributors, internal due date, status/priority, add note, add/remove prerequisite, completion reference or
  controlled document, "Open the originating requirement", "Open in My Work").
- `POST /bids/{bid_id}/execution/actions` add a manual action (title, details, responsible, due date, priority,
  optional source requirement, optional prerequisite). Idempotent: an identical open action (same title) is not
  duplicated; the response says the existing one was reused.
- Inline quick-assign on the returnables list (responsible + internal due date in the row, one Save).
- `GET /bids/{bid_id}/execution/readiness` readiness page (grouped findings, each with a link that resolves it).
- A "Bid execution" entry on the Bid overview and workspace navigation.
Every POST redirects (303) back to where the user was with a one-line result message ("Saved — …", or the
refusal text with how to fix it) and the next suggested open item of the same kind. Refusals render a notice,
never a 500. Keep the extraction/discovery panels out of these screens; a small "Source" link per item goes to
the discovery finding / document.

## Verification contract (S3-6 and my own acceptance)

Goldboro PE031 / IPG060 is the principal case. Benchmark and acceptance databases are read-only evidence
(`~/contractiq-review/stage2/full`, `stage2.1/run-itb`, …); work on copies.

## Units

- **S3-1** Execution records, notes and audit: models (`core/bid_execution.py`), schema + repository
  (`core/bid_execution_repository.py`), service (`core/bid_execution_service.py`): get/set overlay with
  optimistic version, contributor list, internal due date validation, returnable prep-status transitions with
  their required evidence, notes, audit in the same transaction, idempotent re-save (an unchanged save writes no
  new version and no audit row). Tests `tests/unit/test_stage3_records.py`.
- **S3-2** Dependencies with cycle prevention (`bid_execution_dependencies`), status mapping functions for the
  three kinds, and `source_trace(kind, subject_id)` (finding → document / locator / quote → review URL, through the
  existing destination and citation tables). Tests `tests/unit/test_stage3_dependencies.py`.
- **S3-3** Unified projection, `ExecutionFilter`, dashboard projection (`core/bid_execution_projection.py`,
  `core/bid_execution_dashboard.py`). Reconciliation tests `tests/unit/test_stage3_projection.py`.
- **S3-4** Readiness evaluator and facts builder. Tests `tests/unit/test_stage3_readiness.py` (incl. post-award
  never blocks, completion changes the verdict, owner/date never inferred).
- **S3-5** Routes, templates, navigation. Route tests `tests/unit/test_stage3_ui.py` (use
  `scripts.asgi_acceptance_ops08.request` or the repository's existing UI-test pattern).
- **S3-6** Fixes from the browser walkthrough.
