# OPS-06 — Bid Control Centre Foundation

## Authorization and baseline

- Authorized branch: `ops-06-bid-control-center-foundation`.
- Published OPS-05B baseline: `c35a191bce91d7b698a2bca779570f2e7d0bfa33`.
- Recovery pointer: `backup/ops-06-pre-implementation-20260825`.
- OPS-05B manual browser acceptance: PASS.

## Job responsibility supported

OPS-06 supports the Manager, Strategic Bids & Contracts in controlling the complete pre-award Bid workflow from intake through accepted handover. It organizes existing ContractIQ capability around the Bid rather than around implementation modules or separate registers.

ContractIQ's boundary is:

```text
Bid intake and qualification
→ Bid development and control
→ submission and negotiation
→ award reconciliation
→ accepted Bid-to-execution handover
```

ContractIQ stops at accepted handover. Project execution and post-award vendor-document control belong to another person or system.

## User decision supported

The operating shell helps the Bid manager answer:

- Which Bid or action needs attention first?
- What gate is the Bid at and what prevents it advancing?
- Which controls apply to this Bid's existing classification?
- Where is the authoritative record that resolves each issue?
- What must happen next before submission, award or handover?

## Authoritative inputs

- Existing `Bid` identity, ownership, classification, dates, status and current gate.
- Existing deterministic gate and readiness services.
- Existing requirements, scope, supplier, VDRL, commercial, contract-risk, decision, deliverable, proposal, negotiation and work-item services.
- Existing My Day attention projection and its explicit working date.
- Existing controlled-document evidence and audit/provenance records.

No input is copied into a new table. Display links preserve the source of truth.

## Effort reduced and resulting output

OPS-06 replaces navigation across unrelated registers with:

- My Day as the default operational entry point.
- A deterministically ordered Bid portfolio.
- One six-section Bid workspace.
- A persistent Bid header and gate/readiness rail.
- Administration and Reports landing pages that expose only valid existing capability.

The output is a consistent path from attention to the exact authoritative record, normally within two navigation actions and without re-entering Bid/customer context.

## Role-aligned navigation

Normal top-level navigation is exactly:

1. My Day
2. Bids
3. My Work
4. Reports
5. Administration

Operational registers remain available through the active Bid workspace. Role Framework, Knowledge and VDRL template reference are under Administration. Published routes and JSON APIs remain compatible.

## My Day contract

`/` renders My Day directly. My Day remains read-only and groups existing projections as:

1. Critical deadlines
2. Bid blockers
3. Waiting on others
4. Decisions and approvals needed
5. Award or handover attention
6. Standalone personal work

The route supplies the application working date. Automated tests inject a fixed date. Items link to their authoritative Bid section, record, or My Work editor. My Day creates no work item and performs no operational mutation.

## Bid portfolio contract

The Bids page shows Bid ID, customer/project, classification, current gate, customer/internal deadlines, owner, readiness, highest blocker, waiting count and next action.

Filters are typed and allowlisted for current/history/all, status, classification, readiness, owner and deadline attention. The truthful readiness choices are Any, Clear and Hold; the current readiness engine does not emit an escalation verdict. Combined filters use intersection semantics. Invalid or contradictory values return a retained, plain-language HTML 422 page without mutation or audit.

The deterministic ordering is:

1. Held or readiness-blocked
2. Overdue internal deadline
3. Customer deadline within the next 14 calendar days
4. Pending decision/approval
5. Other active
6. Submitted
7. Won/lost/no-bid history

Within a tier, dates, project name and stable Bid ID provide deterministic tie-breaking. The route uses one batch control-centre projection instead of resolving display fields inside the template.

## Six-section Bid workspace

- `/bids/{bid_id}` — Overview & Plan
- `/bids/{bid_id}/requirements-scope` — Requirements & Scope
- `/bids/{bid_id}/manufacturers-coverage` — Manufacturers & Coverage
- `/bids/{bid_id}/commercial-contract` — Commercial & Contract
- `/bids/{bid_id}/proposal-negotiation` — Proposal & Negotiation
- `/bids/{bid_id}/award-handover` — Award & Handover

Each section uses the same active Bid projection, header, section navigation and readiness rail. Opening one workspace evaluates readiness and attention for that Bid only; it does not load the portfolio-wide My Day projection. Existing registers remain authoritative, receive `bid_id` through safe links, and provide a direct return to the relevant Bid workspace section. OPS-05B appears only within Manufacturers & Coverage and retains its bid-stage handover boundary.

## Gate, readiness and classification presentation

The rail displays the existing current gate, its business description, current readiness verdict, active blockers, waiting items, decisions, next gate and next action. The current engine emits CLEAR or HOLD. My Day treats every future non-CLEAR verdict as blocker attention, but no unsupported escalation option is offered by the Bid portfolio filter.

Blocker descriptions come from the existing readiness report. Presentation metadata adds the existing Bid owner, an understandable owing-party category, relevant Bid deadline, source-register explanation and exact workspace destination; it does not alter the verdict.

Classification presentation mirrors existing gate rules:

- Bid intake completeness applies to all Levels.
- Bid/no-bid approval applies to Level 1–4, not Level 0.
- Margin approval applies to Level 2–4, not Level 0–1.
- Existing risk triggers are shown without inventing authorities, policies or thresholds.

OPS-06 does not create a decision mutation surface because the existing browser decision service does not provide a settled optimistic-concurrency HTML command for that action.

## Administration and Reports

Administration links to existing Role Framework, Knowledge/standard positions and read-only bid-stage VDRL template information. Raw template configuration is not part of routine work.

Reports links only to existing valid outputs: Bid-stage VDRL handover exports for existing packages and the existing knowledge/reference export. OPS-06 creates no management intelligence, execution report or closeout report.

## Integrity and compatibility

- No database migration or new table.
- GET requests are read-only and do not append audit evidence.
- Existing PRG, validation, optimistic concurrency, provenance and atomic audit rules are unchanged.
- Existing routes and JSON APIs remain available.
- No automatic My Work creation.
- No external network or InEight integration.
- Automated work uses isolated databases and document roots.

## Targeted review remediation

- Bid-scoped requirements, documents, scope, supplier, commercial, contract-risk, decision, deliverable, proposal and negotiation registers provide a direct business-language return to the relevant workspace section.
- A single-Bid workspace uses the authoritative readiness engine and bid-scoped attention sources without projecting unrelated Bids.
- Invalid Bid filters retain every submitted value in an understandable HTML 422 response. Current/history status contradictions are rejected explicitly.
- The nonfunctional ESCALATE portfolio choice is absent until an authorized rule can produce it.
- My Day selects readiness blocker attention by `verdict != CLEAR`, preserving future non-clear verdicts without changing current filter choices.

The following independent-review observations remain for manual usability judgment and are not changed in this bounded remediation: whether My Day should show every blocker or only the aggregate/top blocker (N5), the quality of derived evidence/owner/owing-party/date labels (N7), and raw metric-key presentation in section summaries (N8).

## Browser acceptance criteria

- ContractIQ opens on My Day and exposes the five role-aligned destinations.
- New Bid is immediately reachable.
- Every Bid opens one six-section workspace with an identical header and rail.
- Level 1 and Level 3 show different required controls based on existing rules.
- Every displayed blocker explains its effect and resolution destination.
- My Day attention opens an authoritative record or Bid section.
- Vendor Document Requirements are under Manufacturers & Coverage.
- Administration contains configuration/reference tools; Reports contains only supported outputs.
- Routine pages expose no TASK/OPS identifiers, schema terms, raw JSON or API instructions.
- Missing Bids return 404; GET requests do not mutate.
- Every bid-scoped operational register returns directly to its originating Bid section.
- Invalid portfolio filters render retained HTML feedback, and a single workspace remains constant-cost as unrelated Bids are added.

## Exclusions

OPS-06 does not implement new requirements, scope authoring, commercial/contract authoring, proposal assembly, submission baseline, negotiation control, award reconciliation, handover acceptance, opportunity/CRM, post-award execution, vendor submission/review cycles, AI recommendations, authentication redesign, broad visual redesign or repository refactoring.

## Definition of done

The OPS-06 validator and dependency-free ASGI acceptance, focused and full pytest, all applicable prior OPS/TASK validators and ASGI scripts, changed-file Ruff/format, strict scoped mypy and `git diff --check` pass. The production database hash remains unchanged. Changes remain unstaged and uncommitted pending independent review and manual browser acceptance.
