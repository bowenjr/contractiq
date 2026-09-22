# OPS-11BX — Bid Workflow Experience Consolidation

**Status:** Implemented and verified — awaiting Jason's manual browser acceptance
**Branch:** `ops-11-bid-package-intake-addendum-control`
**Baseline HEAD:** `5b92c2764d01000f5aae55e3dc6002271d1e0bad`
**Supersedes presentation decisions in:** OPS-06, OPS-07W, OPS-08W (navigator and next-action
presentation only — no authority is moved)

---

## 1. Problem

Manual acceptance of OPS-11B confirmed that after creating a Bid, the primary Bid workspace did
not make package ingestion the obvious next step. The page led with Intake HOLD, gate blockers,
Level 3 controls, an eight-stage setup navigator, "Next best actions", "Overview & Plan" and
"Current control position". Its navigator recommended *Register customer source*, *Add customer
requirement* and *Add scope item*, and never presented Package Intake & Addenda as a stage.

Package intake existed only as a seventh workspace section disconnected from the operational
sequence. "Complete Bid intake" was used for Bid identity and classification while OPS-11 used
"Package Intake" for received customer files. The workspace read as separately developed modules
attached to one page rather than one Bid workflow.

## 2. Product requirement

ContractIQ must guide the user through the sequence of the work:

1. Bid setup
2. Package intake and addenda
3. Controlled customer documents
4. Requirements and responses
5. Scope and interfaces
6. Manufacturers and supplier coverage
7. Commercial, contract risk and approvals
8. Proposal and negotiation
9. Bid Basis and handover

One visible workflow spine, one primary next action, one consistent Bid context.

## 3. Authority boundary

This task creates **no** authority.

- TASK-06 remains the only readiness engine. Its verdict and blockers arrive already evaluated.
- OPS-07W `build_navigator` remains the only source of stage completeness for the stages it
  scores. This task relabels and re-sequences that projection; it does not recompute it.
- OPS-11 package intake remains the only source of received-package facts.
- No schema, migration, business domain, lifecycle rule or readiness engine was added or changed.
- `core/bid_workflow.py` reads no storage, no database connection and no clock.

No schema or authoritative-domain change was found to be necessary during implementation.

## 4. Design

### 4.1 One projection

`core/bid_workflow.py` exposes `project_bid_workflow()`, which takes the Bid status, the OPS-07W
navigator stages, OPS-11 intake facts, the TASK-06 verdict and the already-derived outstanding
items, and returns one `BidWorkflowProjection`: nine `WorkflowStage` rows, one current stage key,
one next action, one compact status line, one readiness line and the outstanding items.

### 4.2 Stage vocabulary

Exactly six statuses are renderable anywhere: **Not started, In progress, Blocked, Ready for
review, Complete, Not applicable**. A partially populated stage is never "Not started". A stopped
pursuit marks every stage after Bid setup "Not applicable".

### 4.3 The single next action

Derived deterministically from the earliest incomplete prerequisite: the first stage that is not
settled and is actionable. For a newly created Bid this is package ingestion. Exactly one
`a.button-lg` is rendered per page; the acceptance script fails if a second appears.

### 4.4 Future stages

A stage beyond the current position whose status is Not started **or Blocked** is future work. It
stays visible for orientation but is dimmed, carries no emphasis marker and advertises no action.
`_PREMATURE` in `core/bid_workflow.py` and the `future` predicate in `templates/bid_detail.html`
apply the same rule, so the projection and the template cannot disagree. The current stage keeps
its action even when blocked.

### 4.5 Blocker headings

The gate engine states each condition positively. A heading must say what is missing, so
`BLOCKER_HEADINGS`, `INTAKE_HEADINGS` and `PROPOSAL_HEADINGS` map each authoritative gate
condition id, package-intake attention code and proposal-readiness blocker code to a short
negative heading. Two guard tests fail if the gate engine or the proposal service ever emits a
code with no heading, so a new authoritative condition cannot silently reach a user as a satisfied
requirement. The engine, the ids and the evaluated detail are unchanged. Every
surface that lists a blocker — the Bid workspace, the proposal issue-control page, the Bid
Handover and My Day — uses the negative heading and carries the evaluated detail as evidence.

### 4.6 Page hierarchy

Compact Bid header → primary next action → nine-stage navigator → compact status and readiness
strip → current-section content → collapsed `<details>` for outstanding blockers, waiting items,
governance controls and gate evidence.

### 4.7 One Bid context

Every Bid workspace section renders the same header, navigator, next-action hierarchy, return
navigation, terminology and status language. Exactly one navigator row is marked as the stage
being viewed, even where two spine stages share one workspace section (2+3, 4+5). The current
workflow stage is marked separately from the section being viewed.

Registers reached from a Bid carry a Bid-named return link. Stage actions prefer the form inside
the Bid workspace over the whole-portfolio register where the Bid workspace carries that form.

## 5. Implementation

### New

- `core/bid_workflow.py` — the projection, stage vocabulary, blocker-heading maps.
- `tests/unit/test_bid_workflow.py` — 21 tests.
- `scripts/asgi_acceptance_ops11bx.py` — rendered socketless walkthrough of one whole Bid.
- `docs/tasks/OPS-11BX-bid-workflow-experience-consolidation.md` — this document.

### Changed

| File | Change |
| --- | --- |
| `app.py` | Builds `PackageIntakeFacts` and calls `project_bid_workflow`; documents register returns to Package intake; vendor-document register carries Bid return context; commercial setup returns to the Bid; My Day and the three handover call sites use negative blocker headings |
| `templates/bid_detail.html` | Compact header, one primary action, nine-stage navigator, compact status strip, collapsed detail; exactly one viewed stage; current stage marked; future stages dimmed and action-free |
| `templates/_package_intake_section.html` | Release detail no longer repeats the parent section; import page heading no longer duplicates the primary action |
| `core/bid_control_center.py` | `BidBlockerView.heading` from `blocker_heading()` |
| `core/proposal_exchange.py` | `ProposalBlocker.heading` — optional, presentation only |
| `core/proposal_exchange_service.py` | Gate and intake blockers carry negative headings; evaluated detail becomes the evidence line |
| `templates/proposal_issue_control.html` | Renders the negative heading above the evidence |
| `templates/my_day.html` | Package-intake attention rows use the negative heading instead of the raw attention code |
| `pyproject.toml` | `core/bid_workflow.py` added to the strict-mypy scope |
| `templates/documents.html` | Preselects the Bid in context |
| `templates/document_detail.html` | Bid-named return link |
| `templates/vendor_documents.html` | Bid-named return link |
| `templates/requirements.html` | `id="create"` so the five existing `#create` links resolve |
| `templates/bid_handover.html`, `templates/scope_item_detail.html`, `templates/contract_risk_detail.html`, `templates/commercial_position_detail.html` | Breadcrumbs and return links use the spine's stage names |
| `tests/unit/test_ops06_ui.py`, `scripts/asgi_acceptance_ops06.py`, `scripts/asgi_acceptance_ops07w.py` | Updated to the corrected navigation contract |

### Deliberate contract changes

Three existing expectations encoded the navigation this task corrects and were updated, not
worked around:

1. `/documents` returns to `package-intake-addenda`, not `requirements-scope` — controlled
   customer documents are stage 3.
2. `/vendor-documents` is covered by the register-return sweep — it previously had no return link.
3. The manufacturer workflow action targets `/bids/<id>/manufacturers-coverage#add-manufacturer-package`.

## 6. Acceptance

`scripts/asgi_acceptance_ops11bx.py` drives the real ASGI application and measures every control
as it executes. No count is hard-coded.

```text
measured_user_actions:                        43
measured_page_departures:                     34
get_mutations:                                 0
context_losses:                                0
raw_json_or_manual_url_requirements:           0
actions_from_creation_to_package_selector:     1
```

It covers: Bid creation; the primary action being "Import customer bid package"; import of a
nested synthetic package; return to the same Bid; Package intake advancing from Not started;
controlled-document review becoming the next action; controlled-source creation; requirement and
response; scope and interface; manufacturer coverage; commercial and risk; proposal readiness;
Bid Basis and handover remaining stage 9; no stage losing Bid context; no routine action requiring
raw JSON or a manual URL; exactly one primary action per page; every stage-action anchor resolving
to a real element; no blocker written as a satisfied requirement on any surface; and GET
navigation creating no mutation and no audit record.

## 7. Verification

Focused workflow/OPS-11/OPS-06 tests **47 passed**. Full unexcluded pytest **484 passed**. OPS-11
validator and ASGI acceptance **PASS**. OPS-06, OPS-07W and OPS-09 validators and ASGI acceptance
**PASS**. Ruff format and check **PASS**. Strict mypy on the changed core modules **PASS**. Fresh
database initialization, reinitialization, foreign-key enforcement, `foreign_key_check` and
`integrity_check` **PASS**. `git diff --check` clean.

Rendered browser review at 1366×768 and 1920×1080: no horizontal clipping, primary action above
the fold on every Bid workspace page, no duplicated headings, Bid-named return navigation
everywhere.

## 8. Open judgement calls for manual acceptance

1. `/bids/<id>/proposal-issue-control` measures 5626 px and `/bids/<id>/handover` 5750 px at
   1366×768. Their length is their own numbered controls and reconciled evidence, not duplication.
   Shortening them is an OPS-09 / handover presentation decision, not a workflow-spine one.
2. `/bids/<id>/handover` and `/bids/<id>/proposal-issue-control` do not carry the nine-stage
   navigator. They are detail pages beneath a section and already carry the Bid header, breadcrumb
   and a named return link; adding the spine would work against "do not make every page equally
   dense".

## 8a. Residual observation

My Day's supplier, deliverable, commercial and contract-risk attention rows still render their
domain code title-cased (for example "No Response"). Those codes belong to domains this task did
not review, and inventing business headings for them would create language without an authority
to back it. Recorded for a future task rather than guessed at here.

## 9. Out of scope

No migration, schema change, dependency, checklist, Work AI, extraction/OCR, archive expansion,
external integration or post-award capability was added. The OPS-11 storage boundary was not
redesigned or weakened.
