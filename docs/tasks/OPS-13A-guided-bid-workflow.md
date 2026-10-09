# OPS-13A — Guided Bid workflow (journey definition)

Status: design for the OPS-13A redesign. Reuses existing registers, services and controls; adds
no database stage, table or gate. The six tasks are a **projection** (`core/guided_workflow.py`)
over the existing ten-stage spine (`core/bid_workflow.py`), so My Day, the Bids list and the Bid
workspace read one model.

## Principles

* A task is *passed* when its **hard requirements** are met. Hard requirements come only from
  existing business rules and the phase they apply to. Everything else is **complete later**:
  visible, linked, never blocking the next task.
* A task that is passed with items still open is `DONE_OPEN` ("Done — items open"). Incomplete
  analysis stays visibly incomplete; it never traps the Bid.
* The owner decision (task D) is the transparent acknowledgement of incomplete analysis. The
  owner-decision evidence snapshot already freezes "N files not reviewed / N discovery files
  partial". No new acknowledgement record is added.
* Document processing needs no senior approvals. Draft preparation needs no issue-phase
  approvals (those are `pending_before_issue`). AI findings stay proposals until accepted.
* "Discovery" is *deliverables* discovery. Copy must not imply it extracts every technical,
  contractual or commercial requirement.

## The six tasks

| # | Task | Objective | Hard requirements (essential now) | Complete later | Done when | Forward action / destination |
|---|------|-----------|-----------------------------------|----------------|-----------|------------------------------|
| A | Receive customer package | Get the customer's files into the Bid | ≥1 release received; no expected release missing | confirm customer reference, record notices | `releases_received ≥ 1` and `expected_missing == 0` | Import customer bid package → `/package-intake-addenda/import?mode=initial`; after import → release page `#files` (task B) |
| B | Choose and analyse files | Say which files ContractIQ may read and run deliverables discovery | ≥1 file Included; no active job; a discovery run finished as SUCCEEDED or PARTIAL — **or** requirements/accepted deliverables already exist (manual route) | undecided files; retry incomplete passages; Include more files | see rules | active job → View discovery progress (job page); 0 Included → Choose files for analysis (`release#files`); Included, no run → Run deliverables discovery (`/deliverable-discovery`); FAILED → Retry (job page), alternative: add requirements manually |
| C | Review findings, set initial requirements | Accept/reject proposed deliverables; record requirements | assessment available (`initial_requirements_assessment_available`) and no *pending* proposals; **or** owner decision already recorded | remaining pending proposals; no requirement recorded yet | see rules | Review N proposed deliverables → `/deliverable-discovery#proposals`; "Continue to owner decision" is always offered from here |
| D | Owner pursuit decision | Pursue or stop the Bid | decision recorded (PURSUE). Does **not** require every file decided or every finding reviewed | — | `decision is PURSUE` (DO_NOT_PURSUE ⇒ STOPPED) | Record owner decision → `/owner-decision` |
| E | Prepare the Bid | Scope, suppliers, commercial response, controlled sources, draft proposal | development-gate check clear and no blocking `whole_bid` outstanding item (excludes issue-phase approvals) | `pending_before_issue` approvals | verdict `clear` and no blocking `whole_bid` item | first blocking item's resolve action, else first unsettled step (documents → requirements → scope → manufacturers → commercial → proposal) |
| F | Confirm basis and approvals, issue | Confirm Bid Basis, obtain required approvals, issue to customer | Bid Basis published with no unincorporated release (this is where *every received file decided* is required — `verify_release` refuses an unreviewed file); each `pending_before_issue` approval; proposal issued | — | `bid_status in {submitted, won, lost}` | first unmet need's action; issue is on `/proposal-issue-control` |

Continuation: each task offers **Continue to [next task]**. It is enabled whenever the task is
passed, and for C it is *always* enabled (it leads to the owner decision, whose evidence records
what was left open).

Return without losing context: every page that belongs to a task shows a compact banner —
task name, what remains, **Back to task**, **Continue to [next task]** — selected by the page
path (`BidWorkflowProjection.task_for_page`), not by query string.

## Mapping to the existing spine

`stages` (10) and `current_stage_key` are retained, with two corrections:

1. **Owner decision (stage 3)** is no longer held back by undecided files
   (`files_requiring_review`); it needs only the initial requirements assessment
   (the existing `initial_requirements_assessment_available` rule).
2. **Package intake (stage 2)** is settled when task B is passed. New status
   `StageStatus.COMPLETE_OPEN` ("Done — items open", settled, slug `complete-open`) is used when
   B is passed but undecided files, a PARTIAL run or unanalysed Included files remain.
   `COMPLETE` only when nothing is open.
3. `intake_control_action` (document control) must not demand that every file is decided before
   governing sources; undecided-file review is a task-F need.

`BidWorkflowProjection.next_action_label/path/stage_label` are derived from the current task so
that every consumer agrees.
