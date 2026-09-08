# OPS-08W — Bid Workflow Usability Consolidation

## Correction purpose

OPS-08W corrects the uncommitted OPS-08 candidate after manual workflow review. The authoritative capabilities are retained, but routine work is consolidated around one selected Bid. The correction removes avoidable register hopping, duplicate-looking initial entry, weak contextual actions, implementation-language status labels, repeated My Day attention rows, and unnecessary multi-lineage Role Framework choices.

## Bid-centred interaction model

The Bid is the persistent working context. The normal, revisable flow is:

Customer requirements → Scope and interfaces → Manufacturers and supplier coverage → Commercial positions, risks and approvals → Proposal inputs → Bid handover.

Each Bid section displays the Bid identity and section navigation. Contextual actions carry the server-validated Bid and source identity into the existing authoritative editor, then return to the exact originating section. Advanced editors remain available for history, relationships, lifecycle changes, and controlled withdrawal. GET navigation is read-only. Missing, stale, or cross-Bid context fails closed with retained HTML feedback.

## Authoritative domains retained

- TASK-09 owns customer requirements and proposed responses.
- TASK-10 owns scope items, interfaces, and their relationships.
- OPS-05B/OPS-07 owns manufacturer or supplier packages, exact evidence, and requirement coverage.
- OPS-08/TASK-13 owns configurable review topics and immutable commercial-position versions.
- TASK-14 owns contract risks and assessments.
- TASK-15 owns decision and approval records.
- TASK-17 owns negotiation plans and outcomes.
- TASK-07/OPS-02 owns My Work.
- TASK-06 remains the only readiness and gate engine.
- Existing proposal and whole-Bid handover services remain the downstream projections.

No duplicate register, alternate readiness engine, automatic authoritative record, or general workflow framework is introduced.

## Derived values

Bid section completion, commercial readiness concerns, proposal inputs, grouped My Day counts, highest-priority next action, manufacturer coverage summaries, and whole-Bid handover views are derived from authoritative records. They are not persisted as competing business facts. All underlying reasons remain available to deterministic readiness and reporting.

## Contextual entry and return navigation

Scope and interface records can be created within the Bid Scope & Interfaces section using PRG. A contextual interface preselects its originating scope item. Manufacturer/equipment packages are created with the Bid fixed and return to Manufacturers & Coverage. Commercial topic actions prepopulate safe topic, source, owner, and Bid context for the existing risk, decision/approval, negotiation, and My Work domains. Advanced pages provide a direct return to their originating Bid section, and all server mutations revalidate same-Bid ownership.

## Human confirmation

Configured topics and derived readiness concerns are prompts for review only. Creating or revising a commercial position, contract risk, decision or approval request, negotiation, or work item requires a visible form submission. Human-created records retain server-owned actor, time, identity, and confirmed provenance. GET requests never manufacture records or audit events.

## Acceptance criteria

- A user can follow the six-part Bid flow without constructing a URL or re-entering known Bid/topic context.
- Scope creation returns to the Bid and does not require a second save; contextual interface creation keeps the scope selected.
- Manufacturer/equipment packages are presented as the parent concept, with VDRL responsibilities shown as supporting coverage.
- Commercial topics distinguish configuration, authored position, readiness concern, risk, and approval records, with explicit contextual actions and bidirectional relationship display.
- Proposal inputs derive from existing authoritative requirements, responses, scope, interfaces, manufacturer evidence, commercial positions, decisions, approvals, and negotiations, with their origins visible.
- My Day renders one primary summary per active Bid, groups repeated reasons, preserves the most severe blocker, and keeps standalone My Work separate.
- Administration exposes one normal My Role lineage, controlled revision, collapsed history, and advanced responsibility-domain configuration.
- HTML 422 responses retain entered values; optimistic concurrency, same-transaction audit, cross-Bid rejection, lifecycle validation, immutable history, and formula-safe exports remain intact.
- A dependency-free ASGI scenario proves the representative rendered-control workflow, persisted relationships, exact returns, and no GET mutation.

## Explicitly deferred

Final customer proposal generation (OPS-09), post-award submissions and customer review, resubmission, execution milestones, closeout, award/PO reconciliation, external or InEight integration, AI/LLM behavior, authentication redesign, destructive schema work, historical rewrites, and unrelated visual redesign remain deferred.
