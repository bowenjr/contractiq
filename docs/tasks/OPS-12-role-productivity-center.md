# OPS-12 — Role Productivity and Bid Portfolio Consolidation

## Authorization and baseline

- Authorized branch: `local/ops-12-role-productivity-center`.
- Starting SHA: `c1159ac4d5ac12c83b48ae4a5b4d69a795987528`.
- Recovery pointer: `backup/local-ops-12-pre-implementation-20260925`.
- Annotated acceptance tag `ops-11bz-accepted-20260925` dereferences to the same SHA.

## Purpose

Make ContractIQ immediately useful for managing the daily workload and Bid portfolio: replace
repetitive underlying blocker rows with concise Bid-level operational summaries in My Day and
the Bids portfolio, while preserving direct access to authoritative details. This is a
workflow-projection and usability release — it introduces no new readiness engine, no new
persisted status, and no schema change.

## What already existed (reused, not duplicated)

- `core/readiness.py::assess_readiness()` — the only readiness engine.
- `core/gates.py` / `core/gate_service.py` — the only gate evaluation.
- `core/bid_control_center.py::project_bid_portfolio()` / `BidControlCenterService.portfolio()`
  — already the batched per-Bid summary source used by the Bids page (highest blocker, waiting
  count, next action, deadline attention, sort tier).
- `core/my_day.py::project_my_day()` / `MyDayService.get_my_day()` — the only My Day work-item
  projection; unchanged.
- `core/proposal_exchange_service.py::assess()` — the only proposal-status authority.
- `app.py::_my_day_bid_summaries()` (removed) was a **second, redundant, N+1** implementation
  of the same per-Bid summary idea already served correctly (and batched) by `portfolio()`. It
  looped every Bid and called `BidControlCenterService.workspace()` (several DB round trips per
  Bid) plus a per-Bid `work_item_repository.list(bid_id)` call. Removing it and routing My Day
  through the same shared projection as the Bids page is the core of this release.

## Architecture

`core/bid_control_center.py` gained:

- `MyDayBidBucket` (`REQUIRES_ACTION`, `WAITING`, `ON_TRACK_OR_UPCOMING`, `HISTORY`) — a pure
  classification computed from fields already on `BidPortfolioRow` (blockers, readiness
  verdict, deadline attention, decision count, overdue work count, other open attention count,
  waiting count). `REQUIRES_ACTION` is checked before `WAITING`, so a Bid is never shown as
  merely waiting when a higher-priority direct action exists.
- New `BidPortfolioRow` fields: `additional_blocker_count`, `overdue_work_count`,
  `other_attention_count`, `supplier_response_position`, `last_activity`, `my_day_bucket`,
  `evidence_links` (a Bid's card only links to a section whose underlying signal is actually
  present, instead of four static links regardless of state).
- `last_activity_by_bid()` — a pure reduction over already-loaded `AuditEntry` rows (one
  existing batch query, `BidRepository.list_audit(bid_id=None)`) plus `Bid.updated_at` as
  fallback. This reports the most recent **recorded audit activity**, not a filtered
  "human-only" signal — the codebase has no existing classification of audit action strings
  into human versus system origin to reuse, so no such filter was invented.
- Three new `BidPortfolioView` members — `UPCOMING`, `DORMANT`, `ISSUED` — layered on the
  existing `CURRENT`/`HISTORY`/`ALL` views without changing what `CURRENT` means (still
  `ACTIVE`+`HELD`+`SUBMITTED`, exactly as today). `DORMANT_AFTER_DAYS = 14` is an explicit,
  named presentation-policy constant, not an inferred business fact, and is intentionally
  independent of the (also 14-day) deadline-attention horizon even though the numbers currently
  match.
- `build_bid_portfolio()` — the same projection, callable from an **already-loaded**
  `MyDayProjection` so the `/my-day` route does not trigger a second, expensive
  `MyDayService.get_my_day()` call. `BidControlCenterService.portfolio()` is the only caller
  that loads a fresh projection (for `/bids`, which does not already have one).

`app.py`:

- `_my_day_bid_summaries()` deleted.
- `/my-day` now calls `_my_day_bid_rows_by_bucket()`, which reuses the route's already-loaded
  `MyDayProjection`, adds one batched `work_item_repository.list(active_only=True)` call and
  one batched `bid_repository.list_audit()` call (both new, both O(1) per request, not per
  Bid), and groups the shared `BidPortfolioRow`s by `my_day_bucket`.
- `_bids_browser_context()` passes `GATE_LABELS` through for the new "Current workflow gate"
  column.

Templates:

- `templates/my_day.html` — the old single "Active Bids" bucket is replaced with the five named
  sections: **Bids requiring action**, **Waiting for others**, **On track or upcoming**, **My
  standalone work** (unchanged), **Recently completed** (unchanged, renamed heading only). Each
  Bid card shows Bid/customer, current workflow gate, deadline, readiness position, highest
  blocker with a "+N more" count, next action as a direct link to its authoritative
  destination, waiting/overdue counts, owner and last recorded activity, plus a details
  disclosure of only the evidence links relevant to that Bid. The existing collapsed "Detailed
  blocker evidence and operational queues" section (the flat, one-row-per-fact drill-down) is
  kept unchanged as the underlying-evidence layer.
- `templates/bids.html` — "Stage / gate" is split into "Lifecycle/status" and "Current workflow
  gate"; added "Supplier response", "Proposal" (a link to the existing
  `/bids/{id}/proposal-issue-control` page — see Decisions below), and "Last recorded activity"
  columns; "Highest blocker" now shows a "(+N more)" suffix. The filter dropdown automatically
  picks up the three new views since it iterates `list(BidPortfolioView)`.

## Decisions made (with the Codex architecture review reconciled)

A Codex read-only architecture review (`codex exec`, GPT-5.6 Terra equivalent per the
project's model routing, read-only sandbox instructions honored — confirmed via `git status`
before and after) was run against a written design proposal before any implementation. Its
seven correctness/scope findings and four open-question answers were folded into the design
above before writing code. Two decisions are worth recording explicitly:

1. **Proposal position is not eagerly computed for every portfolio row.**
   `ProposalExchangeService.assess()` is the sole authority and is expensive — roughly
   15-20 SQL statements per Bid (package projection, history, approvals). Calling it once per
   current-status Bid for every portfolio/My Day render would add on the order of 540-720+
   queries at 36 Bids on top of the existing per-Bid cost `MyDayService.get_my_day()` already
   has. Writing a second, lighter-weight batch primitive would mean re-deriving
   `ProposalControlStatus` outside `assess()` — exactly the duplicated-authoritative-logic this
   release is required to avoid. Per the review's explicit recommendation, the Bids table
   instead links directly to the existing authoritative `/bids/{id}/proposal-issue-control`
   page rather than showing a live-computed status inline.
2. **The `/my-day` route reuses its already-loaded `MyDayProjection`** rather than calling
   `BidControlCenterService.portfolio()` directly (which would call
   `MyDayService.get_my_day()` a second time). This was a real bug caught by the review before
   implementation, not after — see the query measurements below for the before/after effect.

## Query and performance measurements

Measured with `sqlite3.Connection.set_trace_callback` counting every SQL statement executed
during one `GET /bids` and one `GET /my-day`, at 1, 12 and 36 Bids, comparing this branch
against an unmodified worktree checked out at the starting SHA:

| Bids | `/bids` before | `/bids` after | `/my-day` before | `/my-day` after |
|-----:|----------------:|--------------:|------------------:|------------------:|
|    1 |              61 |            63 |                111 |                 65 |
|   12 |             523 |           525 |               1123 |                527 |
|   36 |            1531 |          1533 |               3331 |               1535 |

`/bids` is essentially unchanged (+2 constant, from the one new `list_audit()` call). `/my-day`
drops from roughly double `/bids`' cost to matching it almost exactly, because the old
duplicate-projection-load bug and the old per-Bid `workspace()`/`work_item_repository.list()`
calls are both gone. Growth is linear in Bid count (~42 queries/Bid) on both pages — this
reflects `MyDayService.get_my_day()`'s own pre-existing per-Bid supplier/deliverable-attention
loops (unrelated to this task, not modified) — not anything new added by OPS-12. Neither page
does a per-Bid `assess()` call; a single-Bid workspace remains independent of portfolio size
(unchanged, `core/bid_control_center.py::workspace()` was not touched — confirmed by the
existing `test_single_bid_workspace_query_count_is_portfolio_size_independent` still passing).

## Schema

No migration. No new table or column. All new `BidPortfolioRow` fields are derived
presentation fields computed from already-loaded data (readiness reports, work items, audit
rows, and the existing My Day attention lists); nothing new is persisted.

## Out of scope (untouched)

OPS-11 addendum UX, document extraction, local-LLM Bid analysis, supplier directory/enquiry/
quotation comparison, Proposal Studio, OPS-10 award/offer reconciliation, post-award project
management, email/notification integration, broad restyling. `core/readiness.py`,
`core/gates.py`, `core/bid_workflow.py`, `core/proposal_exchange_service.py` internals, and
`core/work_item_service.py`'s My Work register/filter logic are all unmodified.
