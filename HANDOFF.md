# Handoff — OPS-02

## Status
ACCEPTED FOR CONTINUED DEVELOPMENT

## Branch and baseline
- Branch: `ops-02-operational-command-center`
- Baseline and current HEAD: `72eaf4b1bd56d5f9220925ed8f45f1dab86cbe05`
- Recovery pointer: `backup/ops-02-pre-implementation-20260817`
- No staging, commit, push, merge, rebase, main-branch change, or pull request occurred.

## Files created
- `docs/tasks/OPS-02-operational-command-center.md` — authoritative OPS-02 specification.
- `scripts/validate_ops_02.py` — deterministic service/projection acceptance using an isolated temporary database.
- `scripts/asgi_acceptance_ops02.py` — dependency-free in-process ASGI acceptance using isolated temporary storage.

## Files modified
- `HANDOFF.md` — replaced the accepted OPS-01R handoff with this OPS-02 implementation record.
- `app.py` — typed My Work filters, safe register navigation, fixed local-date projection boundary, and read-only My Day work-item presentation.
- `core/my_day.py` — centralized pure attention-reason, actionable-date, tier, and deterministic ordering functions.
- `core/work_item_repository.py` — parameterized register filters without changing persistence or migration behavior.
- `core/work_item_service.py` — read-only operational-register projection with bounded bid-context resolution and safe display formatting for stale/value errors.
- `core/work_items.py` — typed Pydantic v2 register filter and projection models.
- `scripts/validate_task_07.py` — modernized synthetic WAITING, BLOCKED, and COMPLETED fixtures for accepted OPS-01R lifecycle requirements; updated the non-exclusive due-today count.
- `templates/my_day.html` — removed obsolete work-item mutation forms/JavaScript and linked active/archive rows to My Work.
- `templates/my_work.html` — Quick Capture anchor and deterministic current/history/all register controls and presentation.
- `templates/role_framework.html` — normalized operational navigation and domain labels.
- `templates/work_item_detail.html` — complete general-field controls, status-scoped lifecycle sections, and safe filter-preserving back navigation.
- `tests/unit/test_document_ui.py` — fixed application-date injection after removal of the public My Day time-travel parameter.
- `tests/unit/test_my_day.py` — centralized fixed-date attention and deterministic ordering regression coverage.
- `tests/unit/test_ops_navigation.py` — read-only My Day and Quick Capture navigation coverage.
- `tests/unit/test_requirement_ui.py` — fixed application-date injection after removal of the public My Day time-travel parameter.
- `tests/unit/test_work_item_repository.py` — intersection-filter and contradictory-context coverage.
- `tests/unit/test_work_item_ui.py` — command-center filters plus dependency-free HTML POST/redirect/reload persistence, audit, invalid-write, and stale-write coverage.
- `tests/unit/test_work_items.py` — typed register-filter validation coverage.

## Implementation summary
- My Work is the authoritative operational register with allowlisted current/history/all, status, category, responsibility-domain, explicit context, bid, and attention filters using intersection semantics.
- Bid context is batch-resolved to a human-readable project label plus stable identifier; standalone work remains first-class.
- Active ordering reuses one pure My Day attention contract: blocked, overdue, today, other currently due attention, upcoming, scheduled, then unscheduled; priority, earliest actionable date, and stable ID break ties.
- My Day preserves TASK-06 and TASK-09–18 control projections while work-item mutation is removed. Active and archived work links to `/my-work/{id}`, and Add Work links to `/my-work#quick-capture`.
- One work item retains all applicable attention reasons while appearing once. Completed and cancelled items remain outside active attention.
- Safe allowlisted query reconstruction preserves appropriate My Work filters through editor/back navigation without accepting arbitrary return URLs.
- Existing service mutation routing, atomic audit writes, optimistic concurrency, hard-delete protection, JSON APIs, Quick Capture PRG, and invalid-input retention are unchanged.

## Manual-acceptance persistence remediation
- Initial manual acceptance entered structured waiting values while the record status remained OPEN. Read-only production audit inspection confirmed the four affected saves all carried target status OPEN and stored null structured waiting fields.
- The route, Pydantic model, service, repository SQL, audit serialization, redirect, GET projection, and template rendering were proven to persist the waiting fields when the submitted status is WAITING. The earliest failing boundary was the editor: it exposed every lifecycle section simultaneously, so non-applicable values could be entered and were silently removed by established state-specific clearing.
- Waiting, blocked, completion, and cancellation sections are now visible, enabled, and submitted only for their selected lifecycle status. Required inputs use the authoritative field names.
- Priority and the existing contribution-candidate value were supported downstream but missing from the HTML editor; both are now exposed and round-trip through the same path.
- Legacy `waiting_on` and `blocker_note` remain synchronized from their structured counterparts for compatibility; they are not substitutes for structured values.
- A real stale HTML form POST exposed a second defect: the route caught `StaleWorkItemError` as a `ValueError` but passed it to a formatter that assumed Pydantic `ValidationError`, producing a 500 page. The formatter now safely renders both classes, so stale writes return retained HTTP 422 feedback while record and audit remain unchanged.
- No production lifecycle validation, state-specific clearing, audit, versioning, SQL, or schema rule was weakened.

## Manual browser acceptance — 2026-08-17
- Manual browser re-acceptance: PASS, user-verified on Armoury on 2026-08-17.
- The re-test covered status-scoped lifecycle controls, editable priority and contribution candidate, complete Waiting/Blocked/Completed/Cancelled persistence, independent dates, and stale-version feedback.
- The initial failure was confirmed as lifecycle-specific values entered while status remained OPEN; the accepted repair prevents that ambiguous interaction without weakening lifecycle validation.
- Publication commit is pending; this handoff is included in the intended OPS-02 publication commit.

## TASK-07 compatibility maintenance
- The historical validator's synthetic WAITING fixture gained `waiting_party_label`, `waiting_owed`, and fixed `chase_date`.
- Its synthetic BLOCKED fixture gained `blocker_description` and `resolution_owner` while preserving `blocker_note`, priority, and due date.
- Its COMPLETED creation and transition fixtures gained deterministic non-empty `completion_outcome` values.
- Adding the fixed follow-up date made two unique items match a today attention reason at the validator's existing explicit `as_of=2026-08-05`: one `DUE_TODAY`, one `FOLLOW_UP_TODAY`. The validator's count changed from 1 to 2 while its exclusive primary-bucket assertion remains unchanged.
- A focused regression proves a blocked item due today appears once, retains `BLOCKED` and `DUE_TODAY`, contributes once to each applicable count, and retains blocked tier 0.
- These were test-infrastructure compatibility corrections. Production lifecycle validation was not weakened and no assertion was skipped, removed, or xfailed.

## Test results
- Focused OPS-02 suite: `49 passed, 0 failed, 38 warnings`.
- Full suite: `300 passed, 0 failed, 40 warnings` (all 289 published baseline tests plus 11 OPS-02 tests).
- Warnings remain existing FastAPI `on_event` deprecations.
- Scoped `ruff format --check` on changed Python files excluding `app.py`: 14 files already formatted.
- Scoped `ruff check` on changed Python files excluding `app.py`: PASS.
- Direct `ruff check app.py`: 10 inherited findings on lines attributed by `git blame` to earlier commits; OPS-02 introduced no Ruff finding. Repository-wide Ruff compliance is not claimed.
- `git diff --check`: PASS.
- `mypy`: not run and no mypy compliance is claimed.

## Validation command output
```
TASK-07 validation: PASS
TASK-08 validation: PASS
TASK-08R validation: PASS
TASK-09 validation: PASS
TASK-10 validation: PASS
TASK-11 validation: PASS
TASK-11F ASGI acceptance: PASS
TASK-12 validation: PASS
TASK-12 ASGI acceptance: PASS
TASK-13 validation: PASS
TASK-13 ASGI acceptance: PASS
TASK-14 validation: PASS
TASK-14 ASGI acceptance: PASS
TASK-15 validation: PASS
TASK-15 ASGI acceptance: PASS
TASK-16 validation: PASS
TASK-16 ASGI acceptance: PASS
TASK-17 validation: PASS
TASK-17 ASGI acceptance: PASS
TASK-18 validation: PASS
TASK-18 ASGI acceptance: PASS
OPS-01 validation: PASS
OPS-01 ASGI acceptance: PASS
OPS-02 validation: PASS
OPS-02 ASGI acceptance: PASS
```

## Database and compatibility
- No database migration or persisted-domain redesign was added.
- Automated validation used temporary isolated databases and storage only.
- No tracked database or backup appears in the changed-file inventory.
- Existing SQLite databases, work-item IDs, audit history, versioning, context associations, and contribution-candidate values are preserved.
- No dependency, lock, Python, Ruff, mypy, or tool-configuration file was modified.
- `uv.lock` and `uv.lock.armoury-generated-20260811` remain untouched, untracked, and unstaged.

## Decisions I made
- Kept attention reasons non-exclusive and ordering tiers exclusive. Reason/count semantics are independent from primary bucket and ordering semantics.
- Kept the route tied to the established application local-date source and injected fixed dates only at service/test boundaries; no public `as_of` query was added.
- Used explicit `context=any|standalone|bid` plus a separate `bid_id`, rejecting contradictory combinations with HTTP 422.
- Did not add contribution-candidate review, role-profile authoring, non-bid context-link management, TASK-19, integrations, Alice prioritization, or broad redesign.

## Deviations from the task spec
- None. TASK-07 compatibility changes were explicitly authorized during verification.

## Concerns for review
- Direct `app.py` lint retains 10 inherited findings; they were deliberately not remediated under OPS-02.

## Publication safeguards
- Manual browser acceptance and re-acceptance are complete.
- No migration or persisted-domain redesign was introduced.
- Production lifecycle validation was not weakened.
- `uv.lock` and `uv.lock.armoury-generated-20260811` remain explicitly excluded.
- Publication is limited to the current OPS-02 feature branch; no merge, rebase, main modification, force-push, or pull request is authorized.
