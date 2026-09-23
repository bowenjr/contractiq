# Handoff — TASK-05

## Status
COMPLETE (task scope). Note: the full test suite has 5 pre-existing failures and 3
pre-existing collection errors that are **not** caused by this task — see "Concerns for
review." My task's tests pass with zero failures.

## Files created
- tests/unit/test_negotiation_trade_progression.py (108 lines) — 6 tests covering the
  forward path, the commit-requires-evidenced guard, backward-move rejection,
  terminal-state rejection, append-only persistence, and unknown-trade handling.

## Files modified
- core/negotiation.py (274 lines) — added the `TRADE_TRANSITIONS` forward-only state
  machine, `validate_trade_transition()`, and two lineage fields on `ConditionalTrade`
  (`trade_lineage_id: str | None = None`, `state_version: int = Field(default=1, ge=1)`).
- core/negotiation_repository.py (373 lines) — added `trade_lineage_id` / `state_version`
  columns to the `negotiation_trades` DDL, an idempotent `_ensure_trade_lineage_columns()`
  migration for databases created before the columns existed, updated `add_trade()` to write
  the new columns, and added `progress_trade()` (append-only) + `current_trade()`.
- core/negotiation_service.py (68 lines) — added thin `progress_trade()` and
  `current_trade()` pass-throughs, matching the service's existing style.

## Test results
`pytest tests/unit/test_negotiation_trade_progression.py` — 6 passed, 0 failed
`ruff check` (4 changed files) — pass
`ruff format --check` (4 changed files) — pass
`mypy --strict` (core/negotiation.py, core/negotiation_repository.py, core/negotiation_service.py) — pass

## Validation command output
```
$ uv run pytest tests/unit/test_negotiation_trade_progression.py -v
============================= test session starts ==============================
platform linux -- Python 3.13.13, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/bowen/dev/projects/contractiq-bench-local
configfile: pyproject.toml
collected 6 items

tests/unit/test_negotiation_trade_progression.py::test_progress_trade_forward_path PASSED [ 16%]
tests/unit/test_negotiation_trade_progression.py::test_progress_trade_commit_requires_evidenced_value PASSED [ 33%]
tests/unit/test_negotiation_trade_progression.py::test_progress_trade_rejects_backward_move PASSED [ 50%]
tests/unit/test_negotiation_trade_progression.py::test_progress_trade_rejects_move_from_terminal PASSED [ 66%]
tests/unit/test_negotiation_trade_progression.py::test_progress_trade_is_append_only PASSED [ 83%]
tests/unit/test_negotiation_trade_progression.py::test_progress_trade_unknown_trade_raises PASSED [100%]

============================== 6 passed in 1.48s ===============================
```

`python app.py` still starts: `uv run python -c "import app"` constructs the FastAPI app
cleanly (`APP-IMPORT-OK ContractIQ 2.0.0`). The `/negotiations` routes import the modules I
changed, so a broken import would have surfaced here.

## Decisions I made
- **Append-only progression, not an in-place UPDATE.** The task was silent on *how* to
  persist a state change. I inferred the codebase's answer from context: the module docstring
  says "immutable negotiation evidence," `ConditionalTrade` is `frozen=True` (like `Mandate`,
  `NegotiationMovement`, `Concession`), and the three tables with DB immutability triggers
  (`negotiation_plan_versions`, `negotiation_movements`, `negotiation_concessions`) are
  INSERT-only from their repository methods. So `progress_trade()` appends a **new**
  `negotiation_trades` row carrying the next state rather than mutating the existing row.
- **Lineage identity.** Added a stable `trade_lineage_id` (defaults to the original
  `trade_id`) plus a monotonic `state_version` so a caller can always find the latest state of
  a trade lineage via `ORDER BY state_version DESC LIMIT 1`. `current_trade()` returns that
  latest row as a `ConditionalTrade`.
- **Forward-only state machine.** `TRADE_TRANSITIONS` + `validate_trade_transition()` enforce
  legal moves; terminal states (REJECTED, WITHDRAWN, SUPERSEDED, REVERSED) have no outgoing
  transitions. COMMITTED additionally requires the give's `value_state` to be EVIDENCED,
  mirroring the existing `add_trade` / service guard.
- **Idempotent migration.** `_ensure_trade_lineage_columns()` uses `PRAGMA table_info` +
  `ALTER TABLE ADD COLUMN` + a backfill (`trade_lineage_id = trade_id`), matching the existing
  `core/database.py` `_evolve_schema` pattern, so databases created before these columns
  existed still work.

## Deviations from the task spec
- The task said "Add whatever repository and service methods you think are needed." I added
  `progress_trade()` + `current_trade()` to both the repository and the service, plus
  `TRADE_TRANSITIONS` and `validate_trade_transition()` in `core/negotiation.py` (the task
  noted "it may reasonably decide core/negotiation.py needs a new model/state too"). No
  route or template was added — the task scoped this to repository + service methods.

## Concerns for review
- **Pre-existing test failures (not from this task).** The full suite has 5 failures and 3
  collection errors that predate this change:
  - 5 failures — `test_bid_repository.py::test_approval_round_trips_provenance`,
    `test_bid_repository.py::test_update_approval_persists_full_model`,
    `test_ops07_routes.py::test_browser_gate_approval_is_audited_once_and_duplicate_is_atomic`,
    `test_ops07w.py::test_gate_approval_and_audit_are_atomic_under_audit_failure`,
    `test_ops09.py::test_required_approval_enforcement_and_audit_failure_rollback` — all fail
    on the Task 1 `expires_at` validator in `core/schemas.py` ("expires_at is required when
    obtained is true and decision is set"). `core/schemas.py` is **not** in this task's
    changeset; these tests construct `Approval` objects with `obtained=True` + a `decision`
    but no `expires_at`.
  - 3 collection errors — `test_ops08w.py`, `test_ops11_ui.py`, `test_bid_package_intake.py`
    fail to import a `scripts` module that does not exist in this worktree.
  - I could not obtain a final full-suite pass/fail count in this session because the Bash
    safety classifier was intermittently unavailable (transient timeouts). I confirmed my 6
    new tests pass and read the failing tests to confirm they are unrelated to the negotiation
    code.
- **No DB trigger on `negotiation_trades`.** Unlike the other three tables,
  `negotiation_trades` has no immutable trigger, so a raw `UPDATE negotiation_trades SET
  state=...` is still possible at the SQL level. `progress_trade()` is append-only by
  construction (it only ever INSERTs), but I did not add a trigger because the task scoped this
  to repository/service methods and the existing file does not add triggers for
  `negotiation_trades` or `negotiation_mandates`. Flagging in case you want the invariant
  enforced at the DB layer too.

## Reporting requirements from the task
- None explicitly requested beyond the standard HANDOFF.
