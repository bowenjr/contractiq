# CIQ Stage 2.1 — Requirements integrity and classification hardening

Branch `ciq/stage2.1-integrity-hardening`, based on `c8be24a` (Stage 2 acceptance, CONDITIONAL PASS).
Evidence: `~/contractiq-review/stage2/results/acceptance/` (report, `findings-full.json` baseline).
Workers implement **only the unit named in their task**. Stage 3 is out of scope.

## Standing constraints (every unit)

- Keep the seven lifecycle categories (`DiscoveryLifecycle`); add no new category and no second
  requirements table/enum.
- Migrations are additive: `CREATE ... IF NOT EXISTS`, never drop/rename/alter existing tables.
  Existing databases must open and work unchanged. New evidence tables are insert-only (triggers
  block UPDATE/DELETE) like `deliverable_discovery_destinations`, except where a unit says otherwise.
- Every mutation and its audit row are written in the **same** transaction.
- Guard/validation failures raise `ValueError` with a message that says what went wrong and how to fix it.
- Deterministic code decides; an LLM is never the sole decision mechanism (no LLM in guards).
- Guards only downgrade to `NEEDS_REVIEW`; they never drop a finding. Keep every source citation.
- Mirror a named existing pattern and say which one in your final message.
- Do not touch port 8000, `~/contractiq-manual/`, or any real database. Tests use temp databases.
- Run `python -m pytest` on the files you touched plus their neighbours; run `ruff check` and
  `ruff format` on changed files. Do not commit. Do not edit `HANDOFF.md`.

## Unit B + C — classification guards (`core/requirement_lifecycle.py`, prompt in `core/deliverable_discovery_llm.py`)

Today `guard_classification` has G7 (`_PERMISSIVE_MAY_RE` without `_OBLIGATION_CUE_RE` → NEEDS_REVIEW,
"gives the bidder an option") and G8 (bidder-owned information as MANUFACTURER_INPUT).

### B — standing conduct rules are not pre-bid actions (new guard G9)

Distinguish four things in a quoted sentence: (1) an activity to complete before submitting a bid,
(2) a standing obligation governing bidder conduct, (3) a contractual right/permission, (4) a post-award
obligation. Only (1) may stay `PRE_BID_ACTION` (→ a My Work item).

- G9: when the model's lifecycle is `PRE_BID_ACTION` and the located quote is a standing conduct rule
  (confidentiality / non-disclosure / "shall not" / "must not" / comply with laws / no collusion /
  "adhere to" procedures / deemed-to-have / "at its own cost" / "only those matters ... will be ..."),
  and has **no** explicit before-bid activity cue ("prior to submitting", "before submitting",
  "complete(d) and return(ed)", "must be returned/submitted", "within N (business) days", "with its
  Proposal/bid"), set lifecycle `NEEDS_REVIEW` (never auto-create a My Work item).
- Keep the model's lifecycle in `model_lifecycle` (the existing suggestion mechanism). The
  `review_reason` must name the suggested classification explicitly, e.g. `Standing conduct rule that
  governs the bidder; not an activity to complete before bidding. Suggested: not a pre-bid action —
  reject, or keep as reference.` Reason precedence: an existing G1/G4/G5/G7 reason wins (as G8 does).
- Add a prompt sentence in the discovery prompt: a standing rule of conduct, a right or a permission
  is not a pre-bid action (mirror how G7's prompt sentence was added; test the prompt text).
- Regression benchmark = the Instructions to Bidders PRE_BID_ACTION findings recorded in
  `tests/fixtures/stage2_1_itb_prebid_quotes.json` (create it from the table below, quotes verbatim):
  KEEP `PRE_BID_ACTION`: "Form of Acknowledgement", "Obtain Required Approvals and Licenses".
  ROUTE to `NEEDS_REVIEW`: "Satisfy self on matters relevant to performance", "Notify Owner of RFP
  Document Issues", "Maintain Confidentiality of RFP Information", "Non-Disclosure of Proposal Content",
  "Comply with Applicable Laws", "Prohibition on Collusion and Anticompetitive Practices",
  "Non-Disclosure of Proposal to Other Bidders", "Written inquiries to Sole Point of Contact",
  "Adherence to communication procedures".
  Quotes (from `~/contractiq-review/stage2/results/acceptance/findings-full.json`, filter findings whose first
  source `doc` contains "Instructions to Bidders" and lifecycle PRE_BID_ACTION; use each finding's first
  source `quote` verbatim and its `excerpt` as the passage text). Plus neighbours that must NOT change:
  "must submit notice of intent" → PRE_BID_ACTION; "may be required to attend" → PRE_BID_ACTION;
  a conduct sentence that also says "prior to submitting any Proposal, obtain ..." stays PRE_BID_ACTION.

### C — G7 refinement (permissive vs prohibition / condition / another party's right)

False positives in the acceptance sample: `No equipment may be shipped by a Vendor, unless it is in
possession of an Inspection Release certificate ...` and `Owner may withhold payment of invoices until
Supplier furnishes such evidence.` Both fired G7 although they are a hold-point condition on the supplier.

- Negated/prohibitive "may" ("No X may ...", "may not", "shall not ... may") is not permissive.
- A "may" clause tied to something the supplier/bidder must do first ("unless", "until", "provided
  that", "subject to", "only if/when", "in the event") is a condition, not an option → G7 does not fire;
  the model's lifecycle stands.
- A right granted to another party (Owner/Purchaser/Buyer/Client/Company/Engineer "may" ...) with no
  such condition ("The Owner may audit compliance at any time.") is a right, not a bidder option:
  keep NEEDS_REVIEW, but with a distinct reason that says so and suggests "right held by the Owner —
  reject, or record separately" (conservative review is preserved; only the explanation changes).
- Bidder/supplier-subject permissive "may" ("Bidders may ... submit alternative Proposal(s)",
  "The Supplier may provide a representative ...") and unresolved/unknown subject: unchanged behaviour
  (still `NEEDS_REVIEW`, existing reason). `it may be necessary to report ...` keeps firing (uncertain).
- Regression tests must include all six G7 firings from the acceptance run (quotes in the same
  `findings-full.json`, `review_reason` containing `option`): expected after the change — Inspection
  Release Certificate and Evidence of no liens → NOT flagged; Alternative Proposal, Witness Performance
  Test, Status reporting → still flagged (option reason); Compliance audit access → flagged (Owner-right reason).

## Unit A — obligation collision handling

Problem: two different obligations with the same normalized title and lifecycle bucket merge into one
finding (`normalize_dedup_key` → `_consolidate` within a run; `split_known_duplicates` across runs;
`DestinationWriter` accept-time reuse by title). Evidence in the full run: VDDR rows 8.16 and 9.16
("Statutory Authority Approval Certificates") collapsed into one finding, and VDDR row 1.09 merged into
the model's same-titled POST_AWARD clause finding.

Required behaviour (all deterministic):

1. Genuinely identical obligations still merge: same title+bucket **and** equivalent material facts.
2. Material facts compared: description (normalised content-token overlap with a documented threshold
   constant), timing (due date/basis/timing text), responsible party, condition text, and source
   reference (source_section, the located quote, file+locator). Same located quote in the same
   file+locator = identical regardless of wording. Hard conflict (both sides non-empty and different
   timing, responsible party or condition) = different obligation. Differing descriptions below the
   threshold with no hard conflict = equivalence cannot be established = different.
3. Different/uncertain → **retain both findings** (never merge) and record a collision flag between them
   in a new additive insert-only table (e.g. `deliverable_discovery_collisions`: proposal_id, other_proposal_id,
   reasons JSON, created_at; UNIQUE pair; immutable triggers; created in the same transaction as the
   proposals, audited). Keep distinct dedup keys by adding a deterministic suffix derived from the
   findings' source fingerprint to the later finding's key — **existing keys of stored proposals must not change**.
4. Cross-run: a re-finding is appended to an existing finding only if equivalent to it; otherwise
   stored as a new flagged finding. Never change a reviewed finding's classification/decision. Appending
   a source citation to an equivalent reviewed finding stays allowed (existing behaviour).
5. Every source citation of every candidate is preserved (no citation dropped by a collision decision).
6. Accept-time reuse (`existing_deliverable`, vendor "Already in package", work item "Already an open work
   item") must not link a flagged-collision finding to a record merely because the title matches:
   such a finding creates/links its own record (origin-marker idempotency unchanged).
7. The review UI (`templates/_discovery_review.html`, via `list_review_queue`) shows a plain-language
   "Possible collision with: <title> (<document, page>) — they share a title but differ in <reasons>"
   note; no new controls.
8. VDDR rule rows: rows with the same description but different item codes are different obligations (they
   must all survive); a rule row and a model finding of the same title merge only if equivalent under the
   rules above, otherwise both are kept with a collision flag.

Tests: identical-title/different-obligation regressions for within-run, cross-run, accept-time reuse, VDDR 8.16/9.16
and 1.09 (use the real row strings), plus "identical obligation still merges" (including cross-document restatement
with the same quote and a hard-equal fact set) and "every citation preserved".

## Unit D — register integrity and provenance

Resolves acceptance findings D1 (citations appended after the run commit), R2 (split-write leaves a record
while the finding is Pending; orphan after later reject), R3 (concurrent double-accept can create two
records), R5 (marker lookup misses commercial-position history).

Split in two worker tasks:

**D-a — atomic citation append (D1).** Move the cross-run `append_citations` for a run into the same
transaction as `create_run` (pass the appends to the repository; reuse `append_citations_transaction`
inside the run transaction). A crash can no longer leave a run committed without its appended citations.
Idempotent (file+locator dedup unchanged). Test with an injected failure after the proposal insert.

**D-b — structured origin relation.**
- New additive table `deliverable_discovery_origins` (name may vary): one row per register record created
  by an accepted finding: `origin_id`, `proposal_id`, `bid_id`, `destination_kind`, `record_id` (NULL while
  the creation is claimed), `claimed_at`, `claimed_by`, `released_at`. Partial unique indexes: one
  active (unreleased) origin per `proposal_id`; one origin per `(destination_kind, record_id)` for
  DEPENDENT kinds other than COMMERCIAL_POSITION (many findings may add evidence to one position, so a
  position origin is identified by `(proposal_id)` and records the position id and revision/version).
  Triggers: no DELETE; UPDATE allowed only for the one-way transitions `record_id NULL→value` and
  `released_at NULL→value`, nothing else.
- Claim protocol in `DestinationWriter.register` (and the Deliverable create path where a record is
  created by the finding): (1) claim (BEGIN IMMEDIATE insert) → (2) search the register for the existing
  record by origin row first, then marker (all work-item states; all vendor packages; **every revision** of
  the commercial position) → (3) create the record only if none found → (4) set `record_id` on the origin.
  A second concurrent/repeated accept that cannot claim gets a `ValueError` naming "being accepted by another
  request; retry in a moment", unless the claim's `claimed_at` is older than a documented lease
  (default 120 s) in which case it adopts via an atomic conditional update. `accept_into_record`
  verifies the origin before writing the ACCEPTED event, in the same transaction.
- Safe repeated accept: retry after any failure reuses the same record (existing behaviour kept) and
  completes the origin.
- Reconciliation (explicit, read-first): `reconcile_origins(bid_id, *, apply=False)` returning a report of
  incomplete states — `CLAIM_WITHOUT_RECORD`, `RECORD_WITHOUT_DECISION` (record exists, finding still Pending —
  R2), `ORPHAN_AFTER_REJECT` (finding later rejected/deferred), `MARKER_WITHOUT_ORIGIN` (legacy records carrying
  `[DISC:<id>]` from before this table, incl. commercial history — R5), `DUPLICATE_MARKERS`. With
  `apply=True` it may only (a) backfill origin rows for marker records, (b) attach `record_id` to a claim when
  its record is found by marker, (c) release a claim that has no record after the lease. It **never** writes or
  changes a review decision, never deletes or edits a register record, and audits every change in the same
  transaction. Expose it as `scripts/reconcile_discovery_origins.py --db PATH [--bid ID] [--apply]`
  (default dry-run) and as a service method; surface incomplete records in the Bid's discovery review page
  as a plain notice (count + "see reconciliation") only if it is a small template change.
- Tests (temp file databases, real separate connections): double accept sequential; concurrent accept with a
  `threading.Barrier` across N threads for each destination kind → exactly one record, one origin, one
  ACCEPTED event, others get the "in progress" or "already accepted" ValueError; injected failure after
  register write → origin claimed+record set, finding Pending, reconcile reports RECORD_WITHOUT_DECISION and
  retry completes; injected failure before record creation → CLAIM_WITHOUT_RECORD, released after lease;
  later reject → ORPHAN_AFTER_REJECT reported, record untouched; legacy marker record (commercial record on a
  historical revision) → MARKER_WITHOUT_ORIGIN, backfilled by apply; reconcile never changes a reviewer
  decision; opening an existing Stage 2 database applies the migration twice without error.
