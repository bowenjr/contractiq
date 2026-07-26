# Assessment — Existing Repos vs BidMaster Architecture

**Date:** 2026-07-11
**Reviewer:** Claude (architect)
**Repos surveyed:** `bowenjr/contractiq`, `bowenjr/ai-legal-review`, `bowenjr/EPC_AI_System`

---

## Verdict, up front

**Do not redo `contractiq`. Extend it.**

You said you'd "probably need to redo." Having read it, that would be a mistake. ContractIQ is roughly **60–70% of the analysis half of BidMaster**, it is already local-first, it already talks to Alice, and the domain thinking in it is sound. Throwing it away would cost you weeks and buy you nothing.

What's missing from ContractIQ is precisely the thing I argued should be built first anyway: **the bid lifecycle spine** — the Bid entity, classification, stage gates, readiness engine, approvals, and provenance. That's additive work, not a rewrite.

So the plan changes from *"build BidMaster from scratch"* to *"build the spine, and dock ContractIQ's analysis engine to it."*

---

## Repo-by-repo

### `contractiq` — KEEP AND EXTEND (this is the foundation)

**What's there and good:**

| Component | LOC | Assessment |
|---|---|---|
| `core/pillars.py` | 403 | **Excellent.** 7-pillar framework (Money, Time, Scope, Risk/Liability, Relationships, Administration, Exit) with key questions, red-flag patterns, missing-protection patterns, and **document-type weightings**. This is real domain modelling. Keep entirely. |
| `core/database.py` | 1,033 | **Strong.** 18 tables. Findings, scope items, obligations, negotiation issues — plus an entire **company knowledge layer** (company_positions, escalation_rules, commercial_term_library, clause_playbooks, review_routing_rules, supplier_intelligence, jurisdiction_rules). That knowledge layer is a genuine asset and is exactly what §4.3.14 "organizational capability" asks for. |
| `core/analysis_engine.py` | 1,091 | **Good.** Full pipeline: doc-type detection → classification → party extraction → per-pillar analysis → date extraction → obligation extraction → review priority → recommendations. Chunked per-pillar to manage context. Sound design. |
| `core/llm_client.py` | 161 | **Good, and already right.** OpenAI-compatible, points at Alice (`10.0.0.10:1234`), robust JSON parsing with fence-stripping, sensible tuple timeouts, useful error messages. This *is* the provider abstraction — it just needs an interface extracted around it. |
| `core/knowledge_engine.py` + `knowledge_bootstrap.py` + `knowledge_io.py` | 1,346 | **Valuable.** Seeded company positions, import/export. This is the "standard positions library" I put at v0.7 — you already have it. |
| `core/excel_generator.py` | 703 | Keep. Register exports to Excel are a v0.5 item you've already solved. |
| `core/report_generator.py` | 887 | Keep. |
| `core/document_preprocessor.py` + `document_processor.py` | 1,224 | Keep. PDF/DOCX extraction already works. |
| `app.py` (FastAPI + Jinja) | 899 | Keep. You already have a working web UI — better than the Streamlit plan. **Revise the roadmap: FastAPI stays, drop Streamlit.** |

**What's structurally wrong (and must be fixed):**

1. **It is document-centric, not bid-centric.** `documents` is the root entity; `project_id` is a nullable, unenforced TEXT column. BidMaster needs `Bid` as the root aggregate, with documents as artifacts *belonging to* a bid. This is the single most important change — and it's a migration plus a new table, not a rewrite.

2. **No provenance, no human-confirm.** LLM output is written straight into `clause_findings`, `scope_items`, `obligations`, `negotiation_issues` as if authoritative. This violates the core rule of the role: *evidence over memory, functional accountability*. Every one of those tables needs `created_by`, `agent_name`, `model`, `source_location`, `human_confirmed`, `confirmed_by`, `confirmed_at`.

3. **No lifecycle at all.** No bid classification (§8.1/§8.2), no stage gates G0–G7 (§8.3), no readiness-hold engine (§8.4), no approvals register, no concession log, no PO reconciliation, no handover. This is the entire deterministic spine — all of it net-new.

4. **No compliance matrix.** `scope_items` is adjacent but is not a requirement-decomposition matrix (§B.3). Needs a proper `requirements` table with mandatory/scored/informational priority, planned response, evidence ref, and final location.

5. **No supplier register / silence detection.** Nothing models manufacturer coverage or the critical "silence ≠ compliance" rule.

6. **Zero tests. No type checking. Dataclasses, not Pydantic.** Non-negotiable for a system that will hold real commercial exposure. Every new module gets Pydantic v2 + pytest + mypy from day one; existing modules get tests retrofitted as we touch them.

7. **`positions.json` is a stub** ("Your Company Name", generic positions). The knowledge tables are built but not populated with real Westburne positions. That's a data task, not a code task — and it must stay out of the public repo.

**Verdict: keep every module. Add the spine around it. Retrofit provenance and tests as we go.**

---

### `ai-legal-review` — ARCHIVE (superseded)

This is the forked Claude Code skill pack. It is 100% Markdown prompt content plus two ReportLab scripts. It has no runtime.

ContractIQ's `pillars.py` + `analysis_engine.py` already do everything the skill pack's prompts describe, but *in working Python against a local model*. The skill pack's only residual value is as **prompt source material** — the clause taxonomy in `agents/legal-clauses.md`, the obligation taxonomy in `agents/legal-terms.md`, and the risk-scoring rubric in `agents/legal-risks.md` may be worth mining to enrich the pillar `key_questions` and `red_flag_patterns`.

**Verdict: archive the repo. Mine the agent prompts for pillar enrichment when convenient. It is not a dependency and never becomes one.** The earlier architecture doc I wrote for it is now obsolete — ContractIQ supersedes it.

---

### `EPC_AI_System` — ARCHIVE (dead end)

Oct 2025. 1,250 LOC of embedding/NLP experiments — `visualize_embeddings`, `validate_embeddings`, `process_tokens`, `robust_legal_analysis`. Pre-LLM-pipeline thinking. Superseded entirely by ContractIQ's approach.

**Verdict: archive. Nothing to migrate.** The one thing worth remembering is that it had a CI workflow — we'll want one in BidMaster.

---

## Revised architecture: what actually gets built

ContractIQ becomes the **analysis layer**. We build the **spine** around it and dock them together.

```
┌──────────────────────────────────────────────────────────┐
│  BID LIFECYCLE SPINE            ← NET NEW (v0.1)         │
│  Bid · Classifier · Gates G0-G7 · Readiness · Approvals  │
│  Concessions · Reconciliation · Handover · Audit log     │
├──────────────────────────────────────────────────────────┤
│  COMPLIANCE & SUPPLIER REGISTERS ← NET NEW (v0.2/0.3)    │
│  Requirements matrix · Supplier coverage & silence flags │
├──────────────────────────────────────────────────────────┤
│  ANALYSIS LAYER                  ← EXISTS (contractiq)   │
│  7 Pillars · analysis_engine · clause_findings ·         │
│  obligations · negotiation_issues · scope_items          │
├──────────────────────────────────────────────────────────┤
│  KNOWLEDGE LAYER                 ← EXISTS (contractiq)   │
│  company_positions · escalation_rules · term library ·   │
│  clause_playbooks · routing rules · supplier intel       │
├──────────────────────────────────────────────────────────┤
│  INFRASTRUCTURE                  ← EXISTS (contractiq)   │
│  llm_client (Alice) · doc processors · Excel · PDF ·     │
│  report generator · FastAPI + Jinja UI                   │
└──────────────────────────────────────────────────────────┘
                    ↑
        Provenance + human-confirm retrofitted
        across every analysis-layer table
```

### Roadmap changes vs. my original design

| Original plan | Revised |
|---|---|
| Build from scratch in new `bidmaster` repo | **Evolve `contractiq` in place.** Rename later if you want. |
| Streamlit UI at v0.5 | **Drop Streamlit.** FastAPI + Jinja already exists and is better. |
| OpenAI first, Alice later | **Alice first** — already working. Add an OpenAI provider as *fallback*, not primary. Inverts my earlier assumption, and it's the right way round: your documents are confidential. |
| Knowledge base / RAG at v0.7 | **Already exists.** Populate it with real positions instead of building it. |
| Excel export at v0.5 | **Already exists.** |
| ai-legal-review = the contract module | **Superseded by ContractIQ's pillar engine.** |

That's roughly **three phases of work you don't have to do.**

---

## Revised v0.1 — "The Spine"

Everything here is net-new and deterministic. No LLM calls.

| Task | Title | Notes |
|---|---|---|
| TASK-01 | Test harness, Pydantic schemas, provenance model | Retrofit foundation. Pydantic v2 + pytest + mypy + ruff + CI. |
| TASK-02 | Bid entity + migration to bid-centric model | `bids` table; `documents.bid_id` FK; backfill. |
| TASK-03 | Provenance retrofit across analysis tables | Add provenance cols + human-confirm workflow. |
| TASK-04 | Classifier — levels 0–4 + risk triggers | §8.1 / §8.2, pure rules. |
| TASK-05 | Approvals register + gate rules G0–G7 | §8.3, pure rules over the DB. |
| TASK-06 | Readiness engine + override + audit log | §8.4. This is your hold authority in code. |
| TASK-07 | Bid dashboard ("what's on fire") | New FastAPI route + template. |

**v0.1 exit:** every live bid tracked, auto-classified, gated, with a deterministic hold/clear verdict and an audited override path — sitting on top of the analysis engine you already have.

---

## Immediate housekeeping

1. **`positions.json` and the knowledge tables must not hold real Westburne positions in a public repo.** Right now `positions.json` is a harmless stub — keep it that way as a *template*. Real positions live in `data/` (gitignored) or a private submodule. Confirm before we populate anything.
2. **Archive `ai-legal-review` and `EPC_AI_System`** so Codex is never confused about which repo is live.
3. **Add CI** (`.github/workflows/ci.yml` — ruff, mypy, pytest). `EPC_AI_System` had one; ContractIQ doesn't.
