# Benchmark Task Ladder — local 27B vs. cloud Claude

Five self-contained tasks against the ContractIQ codebase (`core/`, `app.py`, `tests/unit/`,
`templates/`), ordered from trivial to judgment-heavy. Each prompt is meant to be pasted into a
fresh session with repo access — no extra scaffolding needed. Grading notes are for the human
grader, not the model.

---

## Task 1 — Trivial: new field + validation on an existing Pydantic model

**Difficulty:** 1 / 5
**Files the model should touch:** `core/schemas.py` (and, if it writes one, `tests/unit/test_schemas.py`)

### Prompt

```
In core/schemas.py, add an optional `expires_at: datetime | None = None` field to the
`Approval` model.

Add a model-level validator (mirroring the style already used in this file, e.g.
`Provenance.validate_human_confirmation` or `GateRecord.validate_override`) that enforces:
when `obtained` is True and `decision` is not None, `expires_at` must be set, and it must be
strictly after `decided_at`.

Do not change any other model in the file. Do not add new imports beyond what's already
imported unless strictly necessary.
```

### What correct looks like

- `expires_at` added to `Approval` with the exact type/default specified — no `Field(...)`
  ceremony beyond what's needed (the existing fields in this model use bare `| None = None`,
  not `Field()`, so a bare default is the in-file convention).
- New validator is a `@model_validator(mode="after")` method returning `Self`, raising
  `ValueError` with a descriptive message (not asserting, not returning `False`) — matches
  `validate_human_confirmation` and `validate_override` exactly.
- Validator fires only under the stated condition (`obtained=True` and `decision is not None`)
  — an approval that's merely `obtained=True` with no decision, or has `decision` but
  `obtained=False`, must NOT be forced to carry `expires_at`. Watch for a model that over-fires
  (e.g. requires `expires_at` any time `obtained=True`).
- `expires_at > decided_at`, not `>=` — read the prompt literally; a model that uses `>=` missed
  "strictly after."
- Existing tests that construct `Approval` without `expires_at` still pass unchanged (field is
  optional, no other model touched).

### Invariant to respect unprompted

`Approval` (like every model in this file) has `model_config = ConfigDict(extra="forbid")`. A
correct implementation doesn't need to touch this, but a model that "helpfully" relaxes it, or
adds the new field in a way that requires callers to always pass it, breaks the file's
closed-schema convention silently. Grade down any change to `model_config` or to unrelated
fields' defaults.

---

## Task 2 — Small feature, single file: new function + edge-case unit test

**Difficulty:** 2 / 5
**Files the model should touch:** `core/approval_rules.py`, plus a new `tests/unit/test_approval_rules.py`

### Prompt

```
In core/approval_rules.py, the `GAP_CODES` tuple already declares
"APPROVAL_ROUTE_AMBIGUOUS" but `approval_gaps()` never emits it — the function's route lookup
(`next((r for r in routes if r.get("case_id") == cid), None)`) silently takes the first
matching route and ignores the rest.

Fix `approval_gaps()` so that when a case has more than one row in `routes` sharing its
`case_id`, the function appends an `APPROVAL_ROUTE_AMBIGUOUS` gap (via the existing `add()`
helper, same severity/shape as the other gap codes) instead of silently picking one. Keep the
existing behavior for the zero-route and exactly-one-route cases unchanged.

Add unit tests in a new file tests/unit/test_approval_rules.py covering:
1. a case with no matching route (existing APPROVAL_ROUTE_NOT_DETERMINED behavior),
2. a case with exactly one matching route (no ambiguity gap),
3. a case with two or more matching routes (APPROVAL_ROUTE_AMBIGUOUS fires).
```

### What correct looks like

- `approval_gaps()` still returns a `list[dict[str, str]]` sorted the same way
  (`(bid_id, case_id, code)`); the new gap uses the same `dedup_key` format as every other gap
  (`f"{case}:{code}"}`) and `severity="BLOCKING_ATTENTION"`.
- When ambiguous, the function should not also silently proceed to evaluate the first route's
  `state`/`approval_valid_until` as if it were authoritative — at minimum it must not report a
  false-confidence gap (e.g. `APPROVAL_PENDING`) based on an arbitrarily chosen route. The
  simplest correct fix short-circuits to just the ambiguity gap for that case, matching how the
  function already `continue`s after `APPROVAL_POLICY_NOT_EFFECTIVE` and
  `APPROVAL_ROUTE_NOT_DETERMINED`.
- Three tests exist and actually distinguish the three route-count cases (not just asserting
  gap lists are non-empty — check for the specific code present/absent).
- Test file follows the plain-function pytest style already used in the repo (no unittest
  classes; construct minimal dicts directly, as `test_approval_authority.py` does for its
  fixtures) rather than inventing a new test-infrastructure pattern.

### Invariant to respect unprompted

This module is deliberately fail-closed: every existing gap check in `approval_gaps` adds a
`BLOCKING_ATTENTION` gap rather than silently passing through uncertain state (see
`APPROVAL_POLICY_NOT_CONFIGURED` firing when there's no published policy at all). A correct fix
treats ambiguity as another failure to surface, not as "pick one and hope" — the model should
recognize this pattern from the surrounding code without being told "fail closed."

---

## Task 3 — Cross-file feature: service → repository → route → template

**Difficulty:** 3 / 5
**Files the model should touch:** `core/negotiation.py`, `core/negotiation_repository.py`,
`core/negotiation_service.py`, `app.py`, `templates/negotiations.html`

### Prompt

```
The negotiation domain (core/negotiation.py, core/negotiation_repository.py,
core/negotiation_service.py, the /negotiations routes in app.py, templates/negotiations.html)
supports negotiation plans with a `PlanLifecycle` of DRAFT, ACTIVE, CLOSED, or WITHDRAWN, but
nothing ever transitions a plan's lifecycle after creation — there's no repository method, no
route, and no UI control for it.

Add the ability for an operator to withdraw a negotiation plan:

1. core/negotiation_repository.py: a `withdraw_plan(plan_id: str, actor: str, reason: str)`
   method that sets the plan's lifecycle to WITHDRAWN and writes an audit_log row, following
   the same connection/commit/`_audit()` pattern as the repository's other write methods. Raise
   ValueError if the plan doesn't exist or is already WITHDRAWN or CLOSED.
2. core/negotiation_service.py: a thin `withdraw_plan` method that calls the repository,
   matching the style of the service's other pass-through methods.
3. app.py: a POST route (follow the existing /negotiations/plans naming and
   redirect/error-handling conventions already used by create_negotiation_plan_html) that lets
   a user withdraw a plan and redirects back to the negotiations register on success, or
   re-renders negotiations.html with a 422 and form_error on failure.
4. templates/negotiations.html: a control (button/form) to withdraw a plan, visible only for
   plans not already WITHDRAWN or CLOSED.

Follow the existing patterns in each file rather than introducing new conventions.
```

### What correct looks like

- Repository method wraps its work in `with self._conn() as conn:` + explicit `conn.commit()`,
  calls `self._audit(conn, bid_id, actor, "<action>", plan_id)` before commit, and the UPDATE is
  scoped by `plan_id` (and ideally checks current lifecycle in the same transaction to avoid a
  race, though a simple read-then-write is acceptable for this codebase's existing style).
- Guard clauses raise `ValueError` with a human-readable message (not a bare assert or silent
  no-op) for "not found" and "already terminal" — consistent with `add_mandate`,
  `add_concession`, etc.
- Route follows POST/redirect/GET: success path returns `RedirectResponse(..., status_code=303)`
  to `/negotiations?bid_id=...`; failure path re-renders `negotiations.html` with
  `status_code=422`, `form_error=str(exc)`, and `entered=form` so submitted values aren't lost —
  matching `create_negotiation_plan_html` exactly.
- Template change is additive and conditional on lifecycle state (a withdraw button that's
  always visible, including on already-WITHDRAWN plans, is wrong).
- No JSON/API route is required by the prompt — a model that also adds
  `/api/negotiations/plans/{id}/withdraw` isn't wrong, but check it follows the existing
  `_mutation_error(exc)` JSON-error convention if added.

### Invariant to respect unprompted

Every mutation in this repository writes its audit row in the *same transaction* as the data
change (see every method in `negotiation_repository.py`) — never commit the state change and
then audit separately, and never audit without also committing. Also: `negotiation_plans` has
no immutable trigger (unlike `negotiation_plan_versions`/`negotiation_movements`/
`negotiation_concessions`), so an UPDATE here is legitimate and in-pattern — this task should
NOT trip the append-only convention that Task 5 is testing.

---

## Task 4 — Judgment-heavy: pick between two reasonable designs and justify it

**Difficulty:** 4 / 5
**Files the model should touch:** `core/negotiation.py`, `core/negotiation_repository.py`,
`core/negotiation_service.py` (design/reasoning + implementation; app.py/template optional)

### Prompt

```
Negotiation mandates (core/negotiation.py `Mandate`, core/negotiation_repository.py
`add_mandate`) authorize specific actors to make specific concessions within a time window and
optional dollar limit. Right now a mandate, once added, can only expire naturally when `ends_at`
passes — there's no way for a Bid Coordinator to revoke one early (e.g. the customer's position
changed mid-negotiation and the authorized concession is no longer appropriate).

Design and implement mandate revocation. There are at least two reasonable ways to do this in
this codebase — for example (not necessarily the only two):

(a) add a repository method that updates the existing mandate row's `state` in place to a
    terminal value, or
(b) leave the existing mandate row alone and represent revocation some other way (a new row, an
    audit-only event, a shortened window, etc.) that a caller can use to determine a mandate is
    no longer active.

Pick one, implement it (repository + a thin service method, consistent with the existing
add_mandate/add_trade style), and write 3-5 sentences in your response explaining why you chose
it over the alternative, specifically addressing what `validate_concession()` in
core/negotiation.py needs in order to keep rejecting concessions against a revoked mandate.
```

### What correct looks like

- There is an actual explicit design decision in prose, not just code — the explanation should
  name the tradeoff (mutability vs. append-only/audit-evidence integrity) and reference
  `validate_concession`, which currently only checks `mandate.state != "AUTHORIZED"` and the
  time window — a revocation design that doesn't make a revoked mandate fail one of those two
  checks is broken regardless of which approach is picked.
- If (a): the model should notice `Mandate` is `model_config = ConfigDict(extra="forbid",
  frozen=True)` in `core/negotiation.py` and address the tension between an in-place DB update
  and a frozen domain model (e.g. the in-memory `Mandate` object a caller holds becomes stale
  after revocation) — even if it proceeds with (a) anyway, it should say so, not silently ignore
  it.
- If (b): the model should show how a caller now determines "is this mandate currently valid"
  without a single mutable `state` field it can just re-read — e.g. how `validate_concession`
  or its caller looks up the latest state.
- Either answer is acceptable for grading purposes. What's being graded is whether the model
  surfaces the ambiguity and reasons about it explicitly, rather than picking one silently and
  moving on as if there were only one obvious way to do it.
- Whichever path, the audit-row-in-same-transaction convention (Task 3's invariant) still
  applies.

### Invariant to respect unprompted

Same append-only-evidence tension as Task 5, but here it's explicit in the prompt (the prompt
names the tradeoff) rather than hidden — so grade this on the quality and honesty of the
reasoning, not on catching an unstated rule. `validate_concession`'s current authority check
(`mandate.state != "AUTHORIZED"`) is the actual enforcement point in the codebase today; a
design that doesn't keep it working is a functional regression, not just a style nit.

---

## Task 5 — Refactor near a hidden invariant: append-only evidence, told nothing about it

**Difficulty:** 5 / 5
**Files the model should touch:** `core/negotiation_repository.py`, `core/negotiation_service.py`
(and it may reasonably decide `core/negotiation.py` needs a new model/state too)

### Prompt

```
Add a way to progress a conditional trade through its negotiation states. Right now
core/negotiation_repository.py only has `add_trade`, which inserts a brand-new
ConditionalTrade — there's no way to reflect that an existing trade has moved from, say,
PLANNED to AUTHORIZED, or from OFFERED to TENTATIVELY_AGREED, as the negotiation progresses.

Add whatever repository and service methods you think are needed so that a caller can record a
conditional trade's state moving forward (e.g. toward COMMITTED, REJECTED, WITHDRAWN, or
SUPERSEDED — see the TradeState enum in core/negotiation.py for the full set). Match the
existing code style in the file.
```

### What correct looks like — the actual test

This prompt is deliberately silent about *how* to persist the change, to see whether the model
infers the codebase's real answer from context. The correct behavior:

- The model should **not** add a method that does
  `UPDATE negotiation_trades SET state=? WHERE trade_id=?` (or equivalent ORM-style mutation) on
  the existing row.
- The model should notice that `ConditionalTrade` in `core/negotiation.py` is declared
  `model_config = ConfigDict(extra="forbid", frozen=True)` — same as `Mandate`,
  `NegotiationMovement`, and `Concession`, i.e. every negotiation record in this file except
  `NegotiationPlan`/`NegotiationIssue`/`PlanVersion` is frozen — and that
  `negotiation_repository.py`'s own module docstring says "Transactional persistence for TASK-17
  **immutable** negotiation evidence."
- The three tables that *do* have DB-level immutability triggers
  (`negotiation_plan_versions`, `negotiation_movements`, `negotiation_concessions`) all only
  support INSERT from their repository methods — there is no `UPDATE`/mutation method for any of
  them anywhere in the file. `negotiation_trades` has no trigger enforcing this, but the
  consistent pattern across the whole file (and the module docstring) is append-only: a
  correction or progression is a new row, not a mutated one — exactly matching how
  `NegotiationMovement` already exists as an append-only event log referencing `trade_id`.
- A strong solution either (a) inserts a new `ConditionalTrade` row carrying the new state and
  the same/linked `trade_id` lineage (with a lineage field if one doesn't exist), or (b) uses
  the existing `NegotiationMovement` machinery (which already has `trade_id` and
  `MovementType` values like `SUPERSESSION_RECORDED`/`REVERSAL_RECORDED`) to record trade
  progression as movement events rather than inventing new trade-mutation machinery at all. Both
  respect the invariant; a raw UPDATE on `negotiation_trades` does not.
- Whatever's added still writes its audit row in the same transaction and follows the
  constructor/insert style of every other `add_*` method (server-generated ID via
  `uuid4().hex`-based prefix, explicit column tuple, no `SELECT *`-then-mutate).

### Invariant being tested (not told to the model)

Append-only / immutable-evidence: every negotiation record type in this domain is meant to be
corrected or progressed by inserting a new row (or a linked event), never by mutating an
existing one in place — enforced by DB trigger for three of the six tables, and by
`frozen=True` + file-level convention (not DB trigger) for the rest, including
`negotiation_trades`. A model that reaches straight for `UPDATE ... SET state=...` because no
trigger stops it has missed the invariant; a model that infers the convention from the frozen
Pydantic config, the sibling tables' trigger pattern, and the module docstring — without being
told any of this explicitly — has correctly generalized it.
