# SALVAGE.md — Extracted Assets from Retired Repos

**Date:** 2026-07-11
**Retired repos:** `bowenjr/ai-legal-review`, `bowenjr/EPC_AI_System`
**Status:** Everything of value has been extracted into this document. **The repos can be safely deleted.**

This file is the permanent record. Nothing else from those repos needs to survive.

---

## Summary of findings

| Repo | Verdict | Salvaged |
|---|---|---|
| `ai-legal-review` | Superseded by ContractIQ's 7-pillar engine (231 red-flag patterns vs ~30, EPC-specific vs freelancer/SaaS-generic) | **4 assets** — see below |
| `EPC_AI_System` | Pre-LLM embeddings dead end. CI workflow outdated (`checkout@v2`, py3.9). | **Nothing.** |

---

## SALVAGE 1 — Obligation Type taxonomy

**Source:** `ai-legal-review/agents/legal-terms.md`
**Problem it solves:** ContractIQ's `obligations` table has `obligation_type TEXT` with **no constraint**. The LLM invents a new label every run ("Payment", "payment obligation", "Financial"), so the column can't be filtered, grouped, or counted reliably.

**Action:** Add as `ObligationType` enum in `core/enums.py`; constrain the column.

```python
class ObligationType(str, Enum):
    PERFORMANCE  = "PERF"   # delivery of work, services, or goods
    PAYMENT      = "PAY"    # monetary transfers
    NOTICE       = "NOTC"   # required communications or notifications
    APPROVAL     = "APPR"   # required consent or sign-off actions
    REPORTING    = "RPT"    # submission of information or documentation
    INSURANCE    = "INS"    # maintenance of insurance coverage
    COMPLIANCE   = "COMP"   # adherence to laws, regulations, standards
    RESTRICTIVE  = "REST"   # abstention from specified activities
    CONDITIONAL  = "COND"   # triggered only if a specific event occurs
    SURVIVAL     = "SURV"   # continues after contract termination
```

Directly relevant to the role: `NOTC`, `APPR`, and `COND` are exactly the obligations that get missed post-award and cause claims to be time-barred.

---

## SALVAGE 2 — Obligation Trigger taxonomy

**Source:** `ai-legal-review/agents/legal-terms.md`
**Problem it solves:** ContractIQ's `obligations.trigger TEXT` is likewise unconstrained free text.

**Action:** Add as `TriggerType` enum; constrain the column.

```python
class TriggerType(str, Enum):
    CALENDAR   = "calendar"    # fixed date or recurring schedule — "On January 1 of each year"
    EVENT      = "event"       # occurrence activates it — "Upon receipt of invoice"
    CONDITION  = "condition"   # depends on a condition — "If Contractor fails to cure within 30 days"
    MILESTONE  = "milestone"   # tied to project phase — "Within 10 days of Acceptance"
    ROLLING    = "rolling"     # from a variable start point — "Within 30 days of the Effective Date"
    CONTINUOUS = "continuous"  # ongoing throughout the term — "At all times during the Term"
    NEGATIVE   = "negative"    # triggered by FAILURE to act — "If Party fails to give notice, auto-renews"
```

**`NEGATIVE` is the one that matters most.** Obligations triggered by *inaction* (auto-renewal on failure to notify, deemed acceptance on failure to object, waived claims on missed notice windows) are the single most commonly missed category in contract administration, and they map directly to the time-bar risk in the `time` pillar. Being able to query `WHERE trigger = 'negative'` across a live contract is a genuinely useful capability.

---

## SALVAGE 3 — Negotiation priority tiers

**Source:** `ai-legal-review/skills/legal-negotiate/SKILL.md`
**Problem it solves:** ContractIQ's `negotiation_issues` table has free-text `severity` and `status`, but **no priority tier** — nothing that says "this one is a walk-away."

**Action:** Add as `NegotiationPriority` enum; add the column to `negotiation_issues`.

```python
class NegotiationPriority(str, Enum):
    MUST_CHANGE = "must_change"    # dealbreaker — do not sign without this
    SHOULD_CHANGE = "should_change"  # negotiate hard, but tradeable
    NICE_TO_CHANGE = "nice_to_change"  # raise if leverage permits
```

Maps directly to the report's §4.3.11 requirement to define *"opening, target and minimum positions, tradeable items"* before negotiation. The existing `primary_ask` / `fallback` / `counterparty_position` columns already support this — the tier is the missing piece that makes a negotiation plan actionable.

---

## SALVAGE 4 — Six contract-hygiene missing-protection patterns

**Source:** `ai-legal-review/skills/legal-missing/SKILL.md`
**Gap analysis:** ContractIQ already has 35 `missing_protection_patterns` across the 7 pillars, and they are **better** than ai-legal-review's — properly EPC-specific (no EOT clause, no retention release milestones, no interface management protocol, head contract not disclosed for flow-down assessment).

But six generic **contract-hygiene** items are genuinely absent from ContractIQ's list. They're boring, they're boilerplate, and their absence is exactly the kind of thing that gets missed precisely *because* it's boilerplate:

**Add to the `administration` pillar's `missing_protection_patterns`:**

```python
"No severability clause",
"No entire agreement / integration clause",
"No waiver provision (single non-enforcement waives future rights)",
"No assignment restriction (counterparty may transfer without consent)",
"No written-amendment requirement (oral variations become arguable)",
"Notice delivery method not specified (disputes over whether notice was validly given)",
```

The last two matter most in your context. *"No written-amendment requirement"* is how verbal site instructions become contractual variations. *"Notice delivery method not specified"* is how a validly-served claim notice gets challenged. Both are live risks in EPC work.

**Everything else in ai-legal-review's missing-protections list is either already covered by ContractIQ or is freelancer/SaaS-specific (non-compete scope, IP assignment, moral rights) and irrelevant to distribution work.**

---

## What was explicitly NOT salvaged, and why

| Asset | Reason |
|---|---|
| Clause taxonomy (`legal-clauses.md`) | Freelancer/SaaS lens — Non-Compete, IP Assignment, Moral Rights, GDPR. ContractIQ's 7 pillars are strictly better for EPC/distribution. |
| Risk scoring rubric (`legal-risks.md`) | 1–10 severity scale. ContractIQ already has severity + a 0–100 risk score with pillar weighting. Redundant. |
| Compliance frameworks (`legal-compliance.md`) | GDPR / CCPA / ADA / PCI-DSS / CAN-SPAM / SOC 2. Website-compliance auditing. Zero relevance to electrical distribution bids. |
| Document generators (`legal-nda`, `legal-terms`, `legal-privacy`, `legal-agreement`) | Generate NDAs, ToS, privacy policies. Not your job function. |
| PDF report generator (`scripts/generate_legal_pdf.py`) | ContractIQ's `report_generator.py` (887 LOC) is more capable. |
| Report template (`templates/contract-review-template.md`) | Superseded by ContractIQ's report generator. |
| `install.sh` / `uninstall.sh` | Claude Code skill-pack installers. Actively harmful to keep — they imply a Claude runtime dependency. |
| **All of `EPC_AI_System`** | Embeddings/NLP experiments (`visualize_embeddings`, `process_tokens`, `robust_legal_analysis`). Pre-LLM-pipeline thinking, fully superseded. CI workflow is outdated and being rewritten in TASK-01 anyway. |

---

## Where these land

All four salvaged assets are folded into the ContractIQ roadmap:

| Salvage | Lands in |
|---|---|
| 1. `ObligationType` enum | **TASK-01** — added to `core/enums.py` |
| 2. `TriggerType` enum | **TASK-01** — added to `core/enums.py` |
| 3. `NegotiationPriority` enum | **TASK-01** — added to `core/enums.py` |
| 4. Six hygiene patterns | **TASK-03** — appended to `administration` pillar in `pillars.py` |

Enum values are recorded verbatim above, so TASK-01 and TASK-03 are fully self-contained. **No task depends on the retired repos existing.**

---

## Clearance

✅ **`bowenjr/ai-legal-review` — cleared for deletion.**
✅ **`bowenjr/EPC_AI_System` — cleared for deletion.**

Everything of value is captured in this document. Commit this file to `contractiq/docs/SALVAGE.md` before deleting, so the provenance of these four assets is permanently recorded in the live repo.
