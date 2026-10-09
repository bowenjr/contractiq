# CIQ Stage 2 — RFP requirements intelligence (build specification)

Branch `ciq/stage2-requirements-intelligence` (from Stage 1 `e27fe88`). Worktree
`/home/bowen/dev/projects/contractiq-stage1`. Do NOT touch the live app dir or port 8000. Do not commit
(the orchestrator commits). Do not start another unit. Run tests with `uv run python -m pytest` (never plain
`uv run pytest`). Ruff: `uv run ruff check` / `uv run ruff format` on changed files only.

Goal: discovery findings carry a **lifecycle classification** (what is due when / from whom), the review
screen defaults to bid-stage returnables, and a confirmed finding lands in the right existing register.

Invariants from `/home/bowen/dev/projects/CLAUDE.md` apply: mutation + audit row in one transaction;
frozen / "immutable" tables are corrected by INSERTING new rows (never UPDATE); guard failures raise
`ValueError` with an actionable message; name the existing function whose pattern you mirror.
All schema changes are additive (`CREATE TABLE IF NOT EXISTS`, guarded `ALTER TABLE ADD COLUMN`).
No test may make a network call. No customer content in Git (tests use synthetic text).

## 0. Vocabulary (new module `core/requirement_lifecycle.py`, pure, no I/O, no LLM)

```
DiscoveryLifecycle (StrEnum): PRE_BID_ACTION, BID_RETURNABLE, MANUFACTURER_INPUT, POST_AWARD,
                              CONDITIONAL, NOT_APPLICABLE, NEEDS_REVIEW
RequirementStatus: REQUIRED, OPTIONAL, CONDITIONAL, UNCLEAR
ItemKind: SUBMISSION_DOCUMENT, MANUFACTURER_DOCUMENT, MANUFACTURER_DATA, COMMERCIAL_QUALIFICATION,
          CLARIFICATION, ACTIVITY, OTHER
Confidence: HIGH, MEDIUM, LOW
AppliesAt: WITH_BID, POST_AWARD
DestinationKind: DELIVERABLE, VENDOR_DOCUMENT, COMMERCIAL_POSITION, WORK_ITEM
ClassifiedBy: MODEL, RULE
```

`ProposalClassification` (pydantic, `extra="forbid"`, frozen): `lifecycle`, `model_lifecycle`
(what the model/rule first said; equals `lifecycle` unless a guard downgraded it), `requirement_status`,
`item_kind`, `description` (<=300 chars), `applies_at: AppliesAt | None` (only when lifecycle is
CONDITIONAL), `commercial_topic: str | None` (a key of `core.ops08.STANDARD_TOPICS`; only for
COMMERCIAL_QUALIFICATION), `responsible_party: str | None`, `dependencies: str | None`, `confidence`,
`review_reason: str | None`, `source_section: str | None`, `classified_by`. Validators: CONDITIONAL lifecycle
requires `applies_at` is allowed to be None (reviewer must pick before accepting) but `applies_at` set on a
non-CONDITIONAL lifecycle is rejected; NEEDS_REVIEW requires `review_reason`.

Pure functions in the same module:

* `destination_for(c: ProposalClassification) -> DestinationKind | None` — deterministic routing:
  NOT_APPLICABLE and NEEDS_REVIEW -> None; item_kind COMMERCIAL_QUALIFICATION (any other lifecycle) ->
  COMMERCIAL_POSITION; PRE_BID_ACTION -> WORK_ITEM; MANUFACTURER_INPUT -> VENDOR_DOCUMENT;
  BID_RETURNABLE and POST_AWARD -> DELIVERABLE; CONDITIONAL -> DELIVERABLE when `applies_at` is set,
  else None (reviewer must reclassify first).
* `deliverable_phase(c) -> LifecyclePhase` (reuse `core.deliverables.LifecyclePhase`; do NOT add values):
  BID_RETURNABLE -> WITH_BID, POST_AWARD -> POST_AWARD, CONDITIONAL -> WITH_BID/POST_AWARD from applies_at.
* `deliverable_criticality(c)`: REQUIRED -> MANDATORY, OPTIONAL -> INFORMATIONAL, CONDITIONAL -> CONDITIONAL
  (requires condition_text), UNCLEAR -> None (cannot accept without a reviewer choice).
* `is_acceptable(c, condition_text) -> tuple[bool, str]` — whether the Accept button may be enabled and
  the reason when not (shown as the button title).
* `guard_classification(raw: dict-like candidate, passage_text, ...) -> ProposalClassification` — the
  deterministic guards, applied to every MODEL-produced classification before it is stored:
  G1 `source_quote` must be found word-for-word in the passage (`core.date_integrity.locate_quote`);
     if not, lifecycle := NEEDS_REVIEW, confidence := LOW, review_reason := "The supporting sentence was not
     found in the source text", model_lifecycle keeps the model's value.
  G2 `timing_text` must occur in the passage (whitespace/case-insensitive) else it is dropped to None.
  G3 `responsible_party` must occur in the passage (case-insensitive) else None.
  G4 `applies_at` kept only for CONDITIONAL; CONDITIONAL without condition_text -> NEEDS_REVIEW.
  G5 confidence LOW -> lifecycle NEEDS_REVIEW (model_lifecycle retained, review_reason required).
  G6 COMMERCIAL_QUALIFICATION: the model schema has no topic field — `commercial_topic` is the model's
     `category` upper-cased with spaces->underscores when that is a key of `core.ops08.STANDARD_TOPICS`,
     otherwise "QUALIFICATIONS". `category` itself stays the model's free text.
  A finding is NEVER dropped by a guard; it is downgraded to NEEDS_REVIEW and retained.

Legacy: a finding with NO classification row (recorded before Stage 2, or produced from a legacy-shaped
model reply `{"candidates": [...]}`) keeps working exactly as today (Accept creates the WITH_BID
company->customer Deliverable). Readers expose it as `lifecycle = "NEEDS_REVIEW"`, `legacy = True`,
`review_reason = "Not classified (recorded before lifecycle classification)"`.

## 1. UNIT A — persistence (side tables, models, repository, reclassification)

New module `core/deliverable_discovery_classification.py` mirroring
`core/deliverable_discovery_evidence.py` (insert-only side tables beside the immutable proposal tables; wired
into `DeliverableDiscoveryRepository._migrate` via `ensure_classification_tables(conn)` next to
`ensure_evidence_tables(conn)`; written inside `create_run`'s transaction next to `insert_proposal_evidence`;
read in `list_proposals` / `get_proposal` next to `attach_evidence`).

Tables (all with `*_no_update` and `*_no_delete` triggers like the evidence tables):

* `deliverable_discovery_proposal_classification(proposal_id PK -> proposals, lifecycle, model_lifecycle,
  requirement_status, item_kind, description, applies_at, commercial_topic, responsible_party, dependencies,
  confidence, review_reason, source_section, classified_by)` with CHECKs on the enum columns.
* `deliverable_discovery_reclassifications(event_id PK, proposal_id, bid_id, lifecycle, applies_at,
  item_kind, commercial_topic, rationale NOT NULL, reviewer, supersedes_event_id UNIQUE-when-not-null,
  created_at)`.
* `deliverable_discovery_destinations(destination_id PK, proposal_id UNIQUE, event_id, bid_id,
  destination_kind, record_id, reused INTEGER NOT NULL, note, created_at)` — one row per accepted proposal
  saying which register record it became (or was linked to).

`DeliverableProposal` (core/deliverable_discovery.py) gains optional `classification:
ProposalClassification | None = None`. `ProposalReviewEvent` gains optional `destination_kind` and
`destination_record_id`; its ACCEPTED validator now requires `linked_deliverable_id` only when
`destination_kind` is None or DELIVERABLE, and requires `destination_record_id` otherwise. (The review-event
table is unchanged; non-deliverable acceptances store `linked_deliverable_id` NULL and the destination row
carries the link.)

`normalize_dedup_key(bid_id, title, category, lifecycle=None)`: with `lifecycle=None` the result is
byte-identical to today (legacy and import paths unchanged). With a lifecycle bucket the key is
`f"{bid_id}:{bucket}:{normalized_title}"` — **category is ignored** (the model's free-text category varies
between documents) and the bucket is the lifecycle value lower-cased, except CONDITIONAL which is
`conditional-<applies_at or 'unset'>`. A tender-stage and a post-award component of the same document
therefore stay two findings.

Repository API (all return plain dicts, mirror existing method style):

* `list_proposals`/`get_proposal` rows gain: `classification` (dict or None), and flattened convenience
  keys `lifecycle`, `requirement_status`, `item_kind`, `description`, `responsible_party`, `dependencies`,
  `confidence`, `review_reason`, `source_section`, `applies_at`, `commercial_topic`, `classified_by`,
  `legacy` (bool) — values are the EFFECTIVE ones (base classification overlaid by the latest
  reclassification; `lifecycle_source` = "model" | "rule" | "reviewer" | "legacy"), plus `destination`
  (dict or None) and `reclassifications` (list).
* `reclassify(bid_id, proposal_ids, lifecycle, rationale, reviewer, *, applies_at=None, item_kind=None,
  commercial_topic=None) -> list[dict]` — per-proposal independent results (`{"proposal_id", "error"?}`),
  each in its own transaction: insert event superseding the current one, plus an audit row
  `deliverable_discovery_proposal_reclassified`. Refuses (ValueError, actionable) an ACCEPTED proposal, an
  empty rationale, `applies_at` with a non-CONDITIONAL lifecycle, and an unknown proposal/Bid.
* `append_citations(proposal_id, citations)` — INSERT-only: add new `SourceCitation`s (+ quotes) to an
  existing proposal when a later run re-finds the same obligation in another document/locator; skips a
  citation whose (file, locator) already exists; requires the extraction row to exist; audits
  `deliverable_discovery_citation_appended`. Used by Unit B for cross-run duplicates.
* `record_destination(conn, ...)` helper used inside the accept transaction (Unit C).
* `existing_dedup_index(bid_id) -> dict[str, str]` (dedup_key -> proposal_id) alongside the existing
  `existing_dedup_keys`.

Review queue (`DeliverableDiscoveryService.list_review_queue`): each row additionally has `acceptable`
(bool) and `accept_block_reason` from `is_acceptable` (legacy rows: acceptable exactly as today).

Tests `tests/unit/test_stage2_classification_repository.py`: migration is idempotent on a DB that already has
Stage 1 tables and rows; insert-only triggers; effective-overlay with 0/1/2 reclassifications; legacy rows
read as NEEDS_REVIEW+legacy; reclassify refusals; dedup key legacy-identical and category-independent;
append_citations idempotent; audit rows written in the same transaction (force a failure mid-way and assert
nothing persisted).

## 2. UNIT B — extraction, model contract, deterministic VDDR rows, consolidation

### 2.1 Model contract (`core/deliverable_discovery_llm.py`)

Replace `SYSTEM_PROMPT` and `CANDIDATE_RESPONSE_SCHEMA` with the Stage 2 contract; keep
`DISCOVERY_REQUEST_OPTIONS` mechanics (strict json_schema, `enable_thinking: false`). The reply is
`{"requirements": [ ... ]}`; each item has exactly: `title`, `description`, `lifecycle`,
`requirement_status`, `item_kind`, `condition_text`, `applies_at`, `responsible_party`, `timing_text`,
`due_date`, `dependencies`, `confidence`, `review_reason`, `source_section`, `source_quote`, `category`
(`requested_format`/`requested_recipient`/`proposed_owner` are dropped from the model schema; the fields stay
on the proposal and are filled only by legacy-shaped replies or by the reviewer)
(nullable-where-appropriate exactly like the existing schema style; all keys `required`, `additionalProperties:
false`). The system prompt text to use is in `docs/tasks/CIQ-STAGE2-prompt.txt` (verbatim; it was validated
against the real model — do not reword it). The user message is
`f"Document: {unit.document}\nPassage ({unit.locator_type} {unit.locator}):\n\n{unit.text}"`.

Backward compatibility: `_attempt_completion` accepts a reply that has a `requirements` list OR a legacy
`candidates` list. A legacy-shaped item (old `RawCandidate` fields) yields a `ProposedCandidate` with no
classification (unclassified legacy behaviour). The old Stage-1 scripted-LLM tests must keep passing
unchanged where they return `candidates`.

`RawCandidate` / `ProposedCandidate` gain the classification fields (all optional with defaults so stored
`candidates_json` from existing jobs still validates). `analyse_units` applies `guard_classification`
(passage text = `unit.text`) and stores the guarded classification on the `ProposedCandidate`
(`classification: ProposalClassification | None`). `max_tokens` stays 4096/8192-retry.

`RawUnit` (core/deliverable_discovery_extraction.py) gains `document: str = ""` and `kind: str = "TEXT"`.
Both runners (`DeliverableDiscoveryService.run_discovery` and `DiscoveryJobRunner._extract`) set `document`
to the file's original relative path (fallback: filename) using `dataclasses.replace`.

### 2.2 Table-aware PDF extraction and deterministic VDDR rows

PyMuPDF `page.find_tables()` recovers the "Schedule of Vendor Drawing and Data Requirements" table with its
header row intact, while plain page text scrambles the columns. New pure module `core/vddr_table.py`:

* `find_vddr_header(rows: list[list[str]]) -> int | None` — index of the header row: the first row that
  contains cells equal (case-insensitive, whitespace-collapsed) to `required?` and a cell starting with
  `tender`.
* `expand_header(header: list[str]) -> list[str]` — a header cell like `REVIEW QTY DUE` followed by an empty
  cell becomes `REVIEW QTY`, `REVIEW DUE` (same for FINAL); other cells keep their text; unnamed leftover
  cells become `COL<n>`.
* `render_rows(rows, header_idx) -> list[str]` — one self-contained line per data row, only for rows whose
  first cell matches `^\d+(\.\d+)+$`:
  `ROW 5.01 | DISC=EE | DESCRIPTION=General Arrangement Drawings | Required?=Y | TENDER QTY=1 | REVIEW QTY=1 |
  REVIEW DUE=4W ARO | FINAL QTY=3 | FINAL DUE=8W ARO | AS BUILT QTY=1 | FINAL STATUS=CERTIFIED | ... |
  COMMENTS=...` (empty cells omitted; comment whitespace collapsed). Section-title rows are skipped.
* `interpret_row_line(line: str) -> RowReading` — parse a rendered line back (the rendered text is the
  persisted passage and the quote, so interpretation must work from the text alone) and return
  item code, description, required (bool), tender_qty, stage quantities/dues, comments.
* `interpret_vddr_unit(text: str) -> list[ProposedCandidate-compatible dict]` — deterministic findings, one
  per component:
  - `Required?` not `Y` -> one NOT_APPLICABLE finding (classified_by RULE, HIGH, review_reason "VDDR row not
    marked Required", requirement_status REQUIRED? no: OPTIONAL, item_kind SUBMISSION_DOCUMENT).
  - `Required?=Y` and TENDER QTY is a positive integer -> BID_RETURNABLE finding (REQUIRED,
    SUBMISSION_DOCUMENT, HIGH), description "Vendor drawing/data item <code> to be supplied with the tender
    (qty <n>)".
  - `Required?=Y` and any REVIEW/FINAL/AS BUILT qty > 0 or a due value -> POST_AWARD finding (REQUIRED,
    SUBMISSION_DOCUMENT, HIGH); `timing_text` is built ONLY from cells present in the row, e.g.
    `REVIEW DUE=4W ARO; FINAL DUE=8W ARO` (so it is a substring-faithful rendering of the row; the row line is
    the quote); due date is never calculated (basis stays UNKNOWN/timing rule text only).
  - `Required?=Y` and neither tender nor later-stage data -> NEEDS_REVIEW (review_reason "Marked Required but no
    tender, review, final or as-built quantity is given").
  - Both tender and later-stage components -> TWO findings with the same title (different lifecycles).
  - classified_by RULE, source_section = the section/table name if known else None, responsible_party None.
* In `_extract_pdf`: for each page call `page.find_tables()` inside try/except (a failure falls back to
  today's plain-text units for that page). If a table whose header satisfies `find_vddr_header` is found,
  emit `RawUnit(kind="VDDR_TABLE", locator_type="PAGE", locator=f"p.{n} rows {first}-{last}" ...)` units made
  of whole rendered lines grouped up to `MAX_EXCERPT_CHARS` (never split a line; a single line over the limit
  is its own unit) and do NOT also emit the plain text of that page. Pages without a VDDR table are
  unchanged. Add `RawUnit.kind` to the persisted locator only via the locator text.
* `analyse_units`: a unit with `kind == "VDDR_TABLE"` is interpreted by `interpret_vddr_unit` with NO model
  call (outcome ANALYSED, candidate_count = number of findings) — it counts as an analysed passage in the
  outcome tables exactly like a model-analysed one; the safe_reason is None.

Tests: render/expand/interpret on SYNTHETIC rows including a row with both tender and review/final
components, a blank-Required row, a Required row with no data, a wrapped comment, and a section row;
extraction fallback when `find_tables` raises; `analyse_units` makes zero model calls for a VDDR unit
(assert the scripted LLM was never called); prompt contains no truncation of `unit.text`; guards G1–G6 each
with a case; legacy-shaped replies still produce unclassified candidates; schema validity.

### 2.3 Consolidation, dedup and the run (`_consolidate`, `run_discovery`, `DiscoveryJobRunner._finalize`)

* `_consolidate` builds `DeliverableProposal(classification=...)` from the candidate and uses the Stage 2
  dedup key (section 1) when a classification exists, the legacy key otherwise. `criticality` (legacy
  column) := REQUIRED for REQUIRED, CONDITIONAL for CONDITIONAL (condition_text present), else UNCLEAR.
  When several candidates merge, the finding keeps the classification of the highest-confidence member
  (ties: first) and every member's citation; dates still use `classify_due_date` with `timing_text`
  as `rule_text` input exactly as Stage 1 does for `due_date_text`.
* Cross-run duplicates (`dedup_key` already present for the Bid; also, for a BID_RETURNABLE finding, the
  legacy key `normalize_dedup_key(bid, title, category)`): do not insert a new proposal; call
  `append_citations` to attach the new source to the existing proposal. Same-run duplicates merge as today.
* Both runners share one helper for this (do not copy-paste). Restart/cancellation/idempotency behaviour of
  jobs is unchanged: passage keys, claim/lease and `candidates_json` recording keep working; VDDR units are
  passages like any other.

## 3. UNIT C — acceptance routes to the right register; duplicates

`DeliverableDiscoveryService._decide_one` / `_build_deliverable` become destination-aware. New module
`core/deliverable_discovery_destinations.py` (class `DestinationWriter`, constructed with the existing
`DeliverableRepository`, `VendorDocumentService`, `Ops08Repository`, `WorkItemService`, bid repository and the
discovery repository). Behaviour by `destination_for(effective classification)`:

* No classification row (legacy): unchanged path (WITH_BID Deliverable) — existing tests must stay green.
* DELIVERABLE: the existing atomic `accept_proposal` transaction, but `lifecycle_phase` =
  `deliverable_phase`, `criticality` = `deliverable_criticality` (reviewer override still allowed),
  `condition_text` from the finding, `description` = classification description + quote + source (existing
  `_description` shape), `required_format`/`recipient`/`owner` as today, `owner` never taken from
  `responsible_party` unless the reviewer supplies it. Due date rules exactly as Stage 1
  (`_accepted_due_date`): only stated/calculated/reviewer-confirmed dates are applied. POST_AWARD
  deliverables are advisory in readiness (existing `deliverable_gaps`), BID/WITH_BID unchanged.
  Duplicate: an existing non-cancelled Deliverable in the Bid with the same normalized title AND the same
  `lifecycle_phase` is **linked, not duplicated** (record_destination reused=1; the review event links that
  deliverable; add a `DeliverableLink` SOURCE_RECEIVED_FILE for the new proposal's primary source if absent).
* VENDOR_DOCUMENT (MANUFACTURER_INPUT): `VendorDocumentService.create_requirement` in the Bid's package:
  exactly one existing package -> use it; none -> create one named "RFP manufacturer inputs"
  (`package_code` "RFP-MFR-INPUTS", `proposed_manufacturer` "To be confirmed", `internal_owner` = acting user)
  via `create_package`; several -> require an explicit `package_id` (ValueError: "Choose a manufacturer
  package…"). `customer_requirement_code` = `DISC-<last 10 hex of proposal_id>`; `deliverable_title` = title;
  `description` = description; `original_contractual_timing` = verified timing text; `customer_notes` =
  quote; `source_row_reference` = "<document> <locator>"; `required` = (status != OPTIONAL). Duplicate: same
  normalized title already in the package -> link (reused=1).
* COMMERCIAL_POSITION: topic = classification.commercial_topic (default QUALIFICATIONS). Ensure the Bid's
  position for that topic exists (create just that one position row the same way `Ops08Repository.initialize`
  creates one — add a small `ensure_position(bid_id, topic_key, owner, actor)` method to `Ops08Repository`
  mirroring `initialize`), then `revise` it by appending the finding to `supporting_evidence`
  ("RFP discovery: <title> — <quote> (<doc> <locator>)") while carrying EVERY other field of the current
  revision forward unchanged (disposition, owner, positions are never altered by discovery). Duplicate: the
  proposal id already cited in supporting_evidence -> link only.
* WORK_ITEM (PRE_BID_ACTION): `WorkItemService.create_work_item(WorkItemCreate(bid_id, title, details=
  description + quote + source + 'Found by RFP discovery <proposal_id>', category CUSTOMER_REQUEST,
  due_date only when verified))`. Duplicate: an OPEN/IN_PROGRESS/WAITING/BLOCKED work item in the Bid whose
  normalized title matches -> link.
* Atomicity: the three non-deliverable registers have their own repositories/transactions. Order: (1) find an
  existing record by the origin marker (`DISC:<proposal_id>` stored in the record's provenance
  source_location / notes / evidence text) -> reuse it; else create the record (its own transaction + its own
  audit rows); (2) one discovery transaction writes the ACCEPTED review event, the destination row and the
  audit row `deliverable_discovery_proposal_reviewed`. A failure after (1) leaves a record with the origin
  marker; retrying the same decision links to it instead of duplicating. Say this in the module docstring.
* Not acceptable: NOT_APPLICABLE and NEEDS_REVIEW (non-legacy) proposals cannot be ACCEPTED (ValueError "Move
  it to a lifecycle category first" / "Confirmed not applicable items are rejected, not accepted"); they CAN be
  rejected or deferred (rejecting a NOT_APPLICABLE finding = "confirmed not applicable").
* Nothing is created for REJECTED/DEFERRED/PENDING findings.
* Cross-references: when `append_citations` adds a source to an already ACCEPTED proposal, the same call adds a
  SOURCE_RECEIVED_FILE `DeliverableLink` to its Deliverable (or appends to the other register's note-only
  evidence is NOT required — Deliverable links only; document that other registers keep their original
  source).

Tests `tests/unit/test_stage2_destinations.py` per destination: created once, audit rows present, re-accept
refused, duplicate linked not duplicated, origin-marker retry after an injected failure creates no second
record, due-date integrity (model-proposed date never applied), post-award phase value, conditional with and
without `applies_at`, NOT_APPLICABLE/NEEDS_REVIEW refusal, bulk accept of mixed destinations reports
per-finding results, nothing created for reject/defer.

## 4. UNIT D — the review screen (existing page, no new dashboard)

Routes and template stay `/bids/{bid_id}/deliverable-discovery` / `deliverable_discovery.html`; split
repeated markup into `templates/_discovery_findings_table.html` (include) to keep files readable.

* `core/deliverable_discovery_review.py`: `build_review_view` gains `lifecycle` (default `"bid"`) and
  `group` ("document" default | "lifecycle"). Category keys and labels:
  `bid` With the bid (BID_RETURNABLE) · `pre` Before bidding (PRE_BID_ACTION) · `mfr` From the manufacturer
  (MANUFACTURER_INPUT) · `post` After award (POST_AWARD) · `cond` Conditional or optional (CONDITIONAL) ·
  `na` Not applicable (NOT_APPLICABLE) · `review` Needs review (NEEDS_REVIEW incl. legacy) · `all`. Counts per
  category AND per status within the selected category (`counts` keeps meaning "this category's status
  counts"; add `lifecycle_counts` = {key: {"pending": n, "total": n}}). Unknown values fall back to defaults.
  Rows are grouped (document -> rows, or lifecycle -> document -> rows for `all`), paging stays 100 rows.
* Page: a five-question summary strip at the top, each tile showing pending / total and linking to its
  tab: "Include with the bid" (bid), "Obtain from the manufacturer" (mfr), "Clarify or arrange before
  bidding" (pre), "Owed after award" (post), "Still needs review" (review + cond). Then the tab row (all
  categories with pending counts), the existing status + document filters (both keep `lifecycle`), grouped
  table. Every row shows: title, description, requirement status, lifecycle badge (with "Model suggested X"
  when a guard downgraded it), responsible party if explicit, timing/date (existing Stage 1 date-basis
  wording), dependencies/conditions, confidence and review reason, each source (document name, location,
  section, verified quote highlighted as today) and, once accepted, the destination ("Created Deliverable
  …" / "Linked to existing …" with a link).
* Accept is disabled (with the reason as title) where `acceptable` is false. Per-row "Move to…" (select of
  lifecycle categories + reason, posts to the new reclassify route). Bulk bar gains "Move selected to…".
  Existing bulk accept/reject/defer and the Stage 1 validation (`_parse_review_request`) stay exactly as they
  are; `view_fields()` additionally carries `lifecycle` and `group` so the user stays in the same category
  after every action, with progress counts updated (decisions are persisted per finding, so moving between
  categories loses nothing).
* New route `POST /bids/{bid_id}/deliverable-discovery/reclassify` (form: repeated `proposal_id`, `lifecycle`
  key or enum value, optional `applies_at`, `reason` required) -> service/repository `reclassify`; success and
  failure summaries use the same `decide_summary`/`decide_error` notices.
* The accept summary tells the user where records went ("3 Deliverables created, 1 linked to existing, 2
  manufacturer requirements registered, 1 work item created").
* Deep link keeps `?lifecycle=…&status=…&document=…&group=…&page=…`. Default landing view = With the bid +
  To review.
* `_discovery_intake_facts` / My Day pending counts: pending findings counted for the intake next action are
  the **bid-stage** ones plus unclassified legacy ones (so 400 N/A or post-award rows no longer inflate the
  "pending findings" prompt); keep the existing key names, add `discovery_proposals_pending_total`.

Tests `tests/unit/test_stage2_review_view.py` (pure view: filtering, counts, grouping, paging, fallbacks,
legacy rows under Needs review) and `tests/unit/test_stage2_review_routes.py` (use the repo's
`scripts.asgi_acceptance_ops08.request(app.app, ...)` helper or the existing Stage 1 route-test helpers in
`tests/unit/test_stage1_review_routes.py`; default view shows only bid returnables; each tab shows its set;
checkbox form association preserved; reclassify route; bulk move; accept summary wording; the template has no
nested `<form>`; ids stay unique).

## 5. Out of scope (do not do)

No new database file or second requirements table; no change to readiness gates; no Stage 3; no network
calls in tests; no edits to `/home/bowen/dev/projects/contractiq*` other than this worktree; no rewording of
the validated prompt; no `git commit`.

## 6. Implementation notes (deviations from the text above, recorded after the build)

* Unit A was written by Codex (`gpt-5.6-sol`, high); Codex then hit its usage limit, so Units B–D were
  written by the orchestrating session (a local worker was judged unsuitable for new-module work).
* `normalize_dedup_key(bid, title, category, lifecycle=None, applies_at=None)` also accepts a
  `ProposalClassification` as the fourth argument.
* `is_acceptable(c, condition_text, criticality_chosen=False)` and `needs_criticality(...)` were added:
  an UNCLEAR requirement status (or CONDITIONAL status with no condition text outside the CONDITIONAL
  lifecycle) is settled by the reviewer choosing Mandatory/Informational while accepting, as Stage 1 did for
  UNCLEAR criticality. The review queue exposes `needs_criticality`.
* `discovery_proposals_pending` (intake next action) counts pending bid-stage, pre-bid, manufacturer and
  unclassified findings only; no new key was added (the intake facts model forbids extras).
* Deterministic VDDR timing is rendered into each row as `TIMING=Review 4W ARO; Final 8W ARO` so the text is
  a verifiable substring of the quoted row.
* Vendor-document, commercial-position and work-item records are created by their own repositories (own
  transaction and audit) before the discovery transaction; the `[DISC:<proposal_id>]` marker makes a retry
  idempotent. Deliverables remain fully atomic.
* `append_citations` adds a `SOURCE_RECEIVED_FILE` link to an already accepted Deliverable in the same
  transaction; other registers keep their original source.
* Export/import of the Work-ChatGPT package is unchanged (legacy shape, no lifecycle).
