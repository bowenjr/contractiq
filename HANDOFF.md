# Handoff — OPS-12

## Status

IMPLEMENTATION COMPLETE, FROZEN UNSTAGED — AWAITING MANUAL BROWSER ACCEPTANCE AND A DECISION ON
THREE STALE TEST ASSERTIONS (see "Deviations from the task spec" below; nothing else is
outstanding).

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
ruff check (changed .py files): All checks passed!
ruff format --check (changed .py files): 3 files already formatted
mypy (project-configured `uv run mypy`, strict, scoped allowlist incl. new module): Success: no issues found in 29 source files
git diff --check: clean

Focused: tests/unit/test_ops12_role_productivity.py — 13 passed
Focused: tests/unit/test_bid_control_center.py — 6 passed
Focused: tests/unit/test_my_day.py, test_my_day_service.py, test_ops06_ui.py — 18 passed
scripts/validate_ops_02.py: PASS
scripts/validate_ops_06.py: PASS
scripts/asgi_acceptance_ops06.py: PASS
scripts/validate_ops_11.py: PASS
scripts/asgi_acceptance_ops11.py / ops11bx / ops11by: PASS

Full suite (`.venv/bin/python -m pytest -q` — see note below on invocation): 522 passed, 3 failed
```

**Note on `uv run pytest` vs `python -m pytest`:** `uv run pytest -q` fails at *collection* with
`ModuleNotFoundError: No module named 'tests'` / `'scripts'` for three test modules that import
across `tests/unit/*` or `scripts/*` as if they were packages. This reproduces identically on an
unmodified worktree checked out at the starting SHA `c1159ac` — it is a pre-existing environment/
invocation quirk, not something this branch introduced or broke, and not a code bug. Running
`.venv/bin/python -m pytest` (which inserts the invocation directory into `sys.path`) works
correctly on both the baseline and this branch. I did not change any test/pytest configuration to
"fix" this, per the instruction not to chase unrelated stale-fixture failures without
authorization — flagging it here for awareness since it will affect anyone using `uv run pytest`
directly in this repo.

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

**None in the implementation itself.** One open item requires a decision before it can be
called fully clean:

Three pre-existing tests fail against this branch because they assert on literal implementation
details of the exact mechanism OPS-12 is required to replace:

1. `tests/unit/test_ops08w.py::test_my_day_uses_one_primary_bid_summary_and_business_status` —
   asserts the old `data-bid-summary="{{ summary.bid.bid_id }}"` template attribute is present,
   and that the string `"Commercial review incomplete"` (part of the deleted
   `_my_day_bid_summaries()`'s fallback text) is present in `app.py`.
2. `tests/unit/test_ops08w.py::test_connected_bid_workflow_acceptance` — asserts the rendered
   `/my-day` page contains the literal marker `data-bid-summary="`.
3. `tests/unit/test_work_item_ui.py::test_ui_api_creates_transitions_completes_and_reopens_audited_item`
   — asserts the rendered `/my-day` page contains the literal heading text
   `"Completed and cancelled history (1)"`, which OPS-12 explicitly renames to the spec's
   required section name **"Recently completed"**.

All three are a **direct, correct consequence of doing exactly what OPS-12 asks** (consolidating
the duplicate per-Bid summary mechanism onto the shared portfolio projection, and using the
spec's literal five section names) — not a bug in the new code. Per the task's scope-discipline
instruction, I stopped and am reporting this rather than editing the tests myself. If you want
me to update these three assertions to match the new (intentionally different) markup/copy, say
so and I'll do it as a small, separate, focused follow-up; I have not touched them.

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
7. Decide on the three flagged stale test assertions (see "Deviations from the task spec").

OPS-12 IMPLEMENTATION STATUS: AWAITING MANUAL BROWSER ACCEPTANCE
