# OPS-08 — Commercial and Contract Risk Workspace

## Status and schema decision

Implementation is complete and awaiting manual browser acceptance. Inventory proved that the existing authoritative commercial domain could not represent several required OPS-08 facts; the Director subsequently authorized the additive immutable position/version and configurable-topic schema documented below.

## Role boundary

OPS-08 is a pre-award, Bid-linked workspace for a Bids and Contracts Manager to identify customer commercial and contractual terms, maintain exact source traceability, record the proposed Bid position, coordinate risk/negotiation/decision/approval work, and produce proposal-input and Bid-to-execution handover outputs. It does not generate a customer proposal, administer an awarded contract, perform external integration, or use an LLM.

## Inventory and reused authoritative domains

- TASK-13 Commercial: `CommercialItem` owns Bid, title, description, category, basis role, materiality, owner, due date, lifecycle and optimistic version. Immutable `AssessmentVersion` owns applicability, pricing treatment, amount/currency, evidence basis/target, rationale and validity. `CommercialLink` already connects requirements, scope, interfaces, supplier evidence, deliverables, controlled document versions and other commercial items. `CommercialReview` is independent assessment review, not approval. Initialization is explicit, Bid-scoped and duplicate-avoiding, but writes one aggregate audit even when it creates zero rows.
- TASK-14 Contract Risk: `ContractIssue`, exact `RiskSource.locator`, `RiskAssessment`, `RiskReview`, and `RiskLink` are authoritative. Customer/company/target/fallback positions, deterministic likelihood/consequence rating, exposure and escalation belong here only when a contract risk exists. Commercial-item links already exist. Risk activation requires source and assessment; assessments and reviews are append-only.
- TASK-15 Approval Authority: decision cases, subject links, immutable packages, routes and approval events are authoritative. Commercial item/assessment, contract issue/assessment and scenario family/version are supported subjects. This domain is distinct from gate-consumed Bid approval records. Existing browser authoring only creates a basic case or gate approval; package, routing and event workflows are API-only.
- TASK-16 Commercial Scenarios: families, immutable versions, deterministic calculations, independent reviews, comparisons and append-only baseline selections are authoritative. Arithmetic uses `Decimal`; the current browser surface is read-only. The domain does not distinguish working/recommended/approved/included-in-Bid selections.
- TASK-17 Negotiation: plans, immutable plan versions, mandates, trades, append-only movements and concessions are authoritative. Position text lives in immutable plan-version issue JSON and movements are keyed by free-form `issue_code`; there is no commercial-item FK or browser mutation workflow.
- Decisions and negotiations: decision subject links can target commercial/risk/scenario records; negotiation records cannot currently target a commercial item exactly.
- Proposals and deliverables: existing Bid-scoped domains remain authoritative and are browser-visible; OPS-08 proposal input must be an export, not a replacement proposal domain.
- Controlled documents: immutable exact document versions are authoritative. Commercial links can target a version, but TASK-13 has no commercial source locator. OPS-07W has a durable Bid-bound source-registration operation token and retained-form PRG flow that should be generalized/reused rather than copied.
- Requirements/responses: original customer statement and proposed response are separate authoritative requirement facts. They may link to a commercial item but must not be copied into it.
- Scope/interfaces and manufacturer coverage: existing same-Bid links and exact supplier evidence remain authoritative; OPS-08 should project them.
- My Work/My Day: authoritative contextual work links currently support requirement, scope, interface and manufacturer sources. Commercial, risk, negotiation and approval source kinds are missing relationships/routing.
- Readiness/gates: TASK-06 remains the only gate verdict. Commercial and risk gap engines are deterministic inputs; the OPS-07W navigator is presentation only.
- Whole-Bid handover: OPS-07W consumes commercial items, assessments, risks, approvals, negotiations, scenarios and work. OPS-08 must enrich this projection from the same records.
- Audit/provenance/concurrency: server actor/time/identity, Pydantic validation, transaction-local audit, immutable evidence versions and mutable-record version checks are established conventions.
- Shared UI: OPS-07W provides `/static/style.css`, primary navigation, page shell, Bid header, cards, tables, badges, alerts and responsive components.

## Existing relationships and missing relationships

Existing commercial links cover requirement, scope, interface, supplier request/response, deliverable, document version and commercial lineage. Risk links cover commercial items and the adjacent source domains. Approval subject links cover commercial, risk and scenario subjects.

Missing relationship-only connections include commercial item to decision case, negotiation plan/issue, approval route/event, manufacturer package/commitment, and contextual My Work. These could be additive FK junction tables once their exact cardinality and lifecycle are approved.

The blocker is not limited to relationships: required commercial-position facts do not exist.

## Missing authoritative facts

The following cannot be represented without overloading semantically different fields:

- Customer original commercial/contract position, distinct from `CommercialItem.description` and from a risk-only `RiskAssessment.customer_position`.
- Our proposed Bid position, distinct from pricing `CommercialTreatment` and from a risk-only company position.
- Review disposition with the required semantics: Not reviewed, Accept, Qualify, Clarification required, Reject and Not applicable.
- Exact commercial source locator (clause/section/page/locator) tied to the selected controlled version. `CommercialLink` stores only the version identity.
- Disposition reason and next action with disposition-specific lifecycle rules.
- Financial, schedule and operational impacts on a commercial term when no contract risk exists.
- Contributor/reviewer assignment and required approver on the commercial review item. `CommercialReview.reviewer` records an immutable review event, not an assignment.
- Negotiation state/current outcome tied exactly to the commercial item.
- Scenario roles beyond baseline: working, recommended, approved and included in proposed Bid position.
- A configurable authoritative topic vocabulary. `CommercialCategory` is a fixed enum and the initialization tuple omits many required contract topics.

Using requirement statement/response would duplicate the requirement domain; using risk assessment would fabricate a risk for every term; using commercial description/rationale would conflate customer wording, company wording and reasoning; using negotiation JSON would lack direct identity and mutable review workflow. All are unsafe alternatives.

## Authorized schema and migration decision

Migration `ops_08_commercial_contract_risk_workspace_v1` is additive and creates:

- `commercial_topics` stable identities and immutable `commercial_topic_versions` snapshots.
- `commercial_positions` stable Bid/topic identities and immutable `commercial_position_versions`.
- Version numbers plus immutable/no-delete triggers; the stable parent advances only by optimistic compare-and-set.
- Customer position, proposed position, disposition, disposition reason, interpretation, source locator, next action, three impact summaries, contributor, assigned reviewer, required approver, negotiation state and current outcome.
- Server-controlled ID, actor, timestamps and Provenance; AI-created records remain unconfirmed.
- Unique `(commercial_item_id, version_number)` and indexes by Bid/disposition/owner.
- Transactional append plus audit; expected commercial-item version protects edits.
- Configured topics carry stable key, label, help, group, order, lifecycle, governance JSON and provenance. Retirement prevents future initialization while preserving references.

Relationship junctions connect positions to requirements, scope, interfaces, risk, negotiations, decisions, approval routes and manufacturer evidence; a dedicated work junction preserves both stable position identity and exact current version. Service methods enforce same-Bid identity, uniqueness and audit.

No existing table or historical row is replaced or rewritten. TASK-13 through TASK-17 remain authoritative for their established facts and are projected/linked rather than duplicated.

## Implemented workflow

The Bid Commercial & Contract workspace shows Bid/governance context, deterministic readiness and blockers, then a topic-grouped review matrix. Explicit initialization creates unreviewed topics idempotently. A row editor records an immutable position version against an exact controlled source and links existing risk/contextual work. Bulk ownership validates every Bid/version before one transaction. Qualifications and proposal input are derived from positions. Scenario, approval, risk and negotiation surfaces remain their authoritative domains. Successful writes use 303 PRG; validation/stale failures use retained HTML 422.

## Status, completion and readiness semantics

New rows are Not reviewed; existence never implies completion. Empty text and customer silence remain unresolved. Not applicable requires reason; Accept requires exact source and accountable responsibility; Qualify requires proposed wording; Clarification requires owner and next action; Reject requires reason and escalation. Required approval, material open risk, unresolved negotiation, unapproved required scenario, stale evidence and missing source/owner remain blockers. Completion is derived deterministically by the existing readiness/gate engine and presented as Not started, In progress, Blocked or Complete; there is no manual completion flag or second gate engine.

## Governance scaling

Saved and recommended Bid classification plus the existing control projection determine proportionate required reviews and approvals. Governance never hides a known term or material risk. Approval routing remains TASK-15 and gate approval remains the existing gate-consumed record; neither is duplicated.

## Browser behavior and outputs

All pages use the OPS-07W shared shell and support direct contextual navigation without raw JSON or entered IDs. Advanced/history controls remain secondary. Planned deterministic, Bid-scoped, formula-safe outputs are commercial summary, derived qualifications/deviations, risk summary, approval status, negotiation positions, scenario comparison, unresolved actions, OPS-09 proposal input and the OPS-07W handover commercial section.

## Audit, concurrency and security

GET is read-only. Mutations use server identity/actor/time and transactional audit. Mutable roots use optimistic versions; evidence/history is append-only. Cross-Bid, stale, duplicate and unknown-enum submissions fail atomically with retained HTML and no audit. No internal exception text, client provenance, external call or LLM is permitted.

## Acceptance criteria

The 25 focused behaviors, representative five-term scenario, validator, rendered-control ASGI flow, full regression suite, deterministic validators, styling checks, strict typing, isolated integrity checks and two-resolution visual checks stated in the implementation brief remain the acceptance contract after schema authorization.

## Explicitly deferred scope

OPS-09 proposal generation, post-award contract administration, external/InEight integration, authentication changes, AI analysis, destructive migration, historical rewrite, replacement domains, CSV import and automatic work duplication remain deferred.
