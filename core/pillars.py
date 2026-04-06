"""
ContractIQ — 7-Pillar Commercial Framework
Defines the core analysis pillars and document-type weightings.
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class Pillar:
    pillar_id: str
    name: str
    icon: str
    description: str
    key_questions: List[str]
    red_flag_patterns: List[str]
    missing_protection_patterns: List[str]
    weight_by_doc_type: Dict[str, float] = field(default_factory=dict)


MONEY = Pillar(
    pillar_id="money",
    name="Money",
    icon="💰",
    description="Payment terms, pricing, variations, liquidated damages, and financial exposure.",
    key_questions=[
        "What is the total contract value or pricing structure?",
        "What are the payment terms and schedule?",
        "Are there any pay-when-paid or pay-if-paid provisions?",
        "What retention or holdback applies and when is it released?",
        "How are variations or change orders valued and approved?",
        "Are there liquidated damages or delay penalties? What rate?",
        "Are there any price escalation or adjustment clauses?",
        "What are the invoicing requirements and payment periods?",
        "Is there a limit on the total value of variations?",
        "What happens to payment if the contract is terminated?",
    ],
    red_flag_patterns=[
        "Pay-when-paid or pay-if-paid provisions",
        "Retention above 10% or no release mechanism",
        "No variation valuation mechanism",
        "Liquidated damages with no cap",
        "Fixed price with no escalation over long duration",
        "Unilateral right to adjust scope without price adjustment",
        "Invoice approval periods exceeding 30 days",
        "No interest on late payment",
    ],
    missing_protection_patterns=[
        "No variation / change order pricing mechanism",
        "No interest clause for late payment",
        "No retention release milestones defined",
        "No LD cap specified",
        "No escalation clause for multi-year contract",
    ],
    weight_by_doc_type={
        "Subcontract Agreement": 0.25,
        "Prime Contract": 0.143,
        "Bid/Tender Response": 0.25,
        "RFP/Tender Document": 0.20,
        "Change Order/Variation": 0.40,
        "Claim Letter": 0.30,
        "General Contract": 0.20,
    },
)

TIME = Pillar(
    pillar_id="time",
    name="Time",
    icon="⏱",
    description="Programme, milestones, extension of time, delays, time-bars, and completion.",
    key_questions=[
        "What is the contract start date and completion date?",
        "What are the key milestones and their dates?",
        "What notice period is required for extension of time claims?",
        "What grounds exist for extension of time?",
        "What constitutes practical or substantial completion?",
        "What is the defects liability period?",
        "Are there any time-bar provisions that extinguish claims?",
        "What happens if the programme is not maintained?",
        "What are the notice periods for termination?",
        "Are there any long-stop dates or sunset clauses?",
    ],
    red_flag_patterns=[
        "Short notice periods for EOT claims (under 7 days)",
        "No extension of time entitlement for employer risk events",
        "Time-bar clauses extinguishing claims without notice",
        "Ambiguous completion definition",
        "No milestone schedule referenced or attached",
        "Liquidated damages without a corresponding EOT mechanism",
        "Contractor responsible for third-party delays outside control",
        "No long-stop date for project abandonment",
    ],
    missing_protection_patterns=[
        "No EOT clause for owner-caused delays",
        "No programme requirement on employer",
        "No suspension entitlement",
        "Completion definition not defined",
        "No force majeure extension provision",
    ],
    weight_by_doc_type={
        "Subcontract Agreement": 0.15,
        "Prime Contract": 0.143,
        "Bid/Tender Response": 0.15,
        "RFP/Tender Document": 0.15,
        "Change Order/Variation": 0.20,
        "Claim Letter": 0.30,
        "General Contract": 0.143,
    },
)

SCOPE = Pillar(
    pillar_id="scope",
    name="Scope",
    icon="📋",
    description="Scope definition, design responsibility, interfaces, specifications, and inclusions/exclusions.",
    key_questions=[
        "How precisely is the scope of work defined?",
        "What documents form the scope (drawings, specs, schedules)?",
        "What is explicitly excluded from the scope?",
        "Who is responsible for design — contractor, employer, or split?",
        "What interfaces exist with other contractors or packages?",
        "What employer-supplied information or items are required?",
        "What standards and specifications must be met?",
        "What constitutes a variation versus base scope?",
        "Are there any provisional sums or prime cost items?",
        "What inspection, testing, and quality requirements apply?",
    ],
    red_flag_patterns=[
        "Scope defined by reference to documents not provided",
        "Vague scope language creating unlimited obligation",
        "Design responsibility ambiguity",
        "Interface obligations with undefined third parties",
        "All-inclusive language overriding exclusions",
        "Fitness for purpose standard without liability cap",
        "Scope subject to unilateral employer variation",
        "Referenced specifications not attached or identified",
    ],
    missing_protection_patterns=[
        "No defined exclusions list",
        "No interface management protocol",
        "Employer-supplied drawings/info not listed",
        "No provisional sums schedule",
        "No quality/inspection plan referenced",
    ],
    weight_by_doc_type={
        "Subcontract Agreement": 0.20,
        "Prime Contract": 0.143,
        "Bid/Tender Response": 0.30,
        "RFP/Tender Document": 0.30,
        "Change Order/Variation": 0.30,
        "Claim Letter": 0.03,
        "General Contract": 0.143,
    },
)

RISK_LIABILITY = Pillar(
    pillar_id="risk_liability",
    name="Risk & Liability",
    icon="⚖️",
    description="Liability cap, indemnities, insurance, force majeure, warranties, and risk allocation.",
    key_questions=[
        "What is the liability cap and what does it cover?",
        "Are consequential losses excluded? By both parties?",
        "What indemnity obligations exist and are they mutual?",
        "What insurance is required — types, limits, duration?",
        "Are performance bonds or parent company guarantees required?",
        "How is force majeure defined and what are the consequences?",
        "Who bears the risk of unforeseeable ground conditions?",
        "Is there a fitness for purpose obligation?",
        "Are third party claims covered by indemnity?",
        "What warranties are given and for how long?",
    ],
    red_flag_patterns=[
        "No liability cap or unlimited liability exposure",
        "One-sided indemnity favouring employer only",
        "Consequential loss exclusion applies to one party only",
        "Insurance requirements exceeding market availability",
        "Back-to-back liability without sight of head contract",
        "Fitness for purpose obligation without corresponding cap",
        "Force majeure definition narrower than industry standard",
        "Unlimited warranty period",
    ],
    missing_protection_patterns=[
        "No mutual consequential loss exclusion",
        "No liability cap",
        "No force majeure clause",
        "No professional indemnity requirement",
        "No gross negligence carve-out from liability cap",
    ],
    weight_by_doc_type={
        "Subcontract Agreement": 0.20,
        "Prime Contract": 0.143,
        "Bid/Tender Response": 0.15,
        "RFP/Tender Document": 0.10,
        "Change Order/Variation": 0.05,
        "Claim Letter": 0.20,
        "General Contract": 0.20,
    },
)

RELATIONSHIPS = Pillar(
    pillar_id="relationships",
    name="Relationships",
    icon="👥",
    description="Parties, subcontracting, flow-down, key personnel, assignment, and stakeholder obligations.",
    key_questions=[
        "Who are the contracting parties and what is their authority?",
        "What back-to-back or flow-down obligations apply?",
        "Is subcontracting permitted and under what conditions?",
        "Are there key personnel requirements?",
        "What approval and consent rights does the employer have?",
        "What step-in rights exist?",
        "Can the contract be assigned or novated?",
        "What reporting obligations exist?",
        "What are the stakeholder management requirements?",
        "Who is the contract administrator and what are their powers?",
    ],
    red_flag_patterns=[
        "Back-to-back obligations without sight of head contract",
        "Unrestricted right to withhold subcontracting consent",
        "Key personnel removal without due process",
        "Overly broad step-in rights",
        "Assignment permitted without consent",
        "Contract administrator acting as judge of own decisions",
        "Flow-down broader than relevant subcontract scope",
        "Undefined approval periods creating programme risk",
    ],
    missing_protection_patterns=[
        "No defined approval timelines for consent requests",
        "No key personnel protection clause",
        "No novation mechanism on project transfer",
        "Head contract not disclosed for flow-down assessment",
        "No step-in protections",
    ],
    weight_by_doc_type={
        "Subcontract Agreement": 0.10,
        "Prime Contract": 0.143,
        "Bid/Tender Response": 0.05,
        "RFP/Tender Document": 0.03,
        "Change Order/Variation": 0.03,
        "Claim Letter": 0.01,
        "General Contract": 0.10,
    },
)

ADMINISTRATION = Pillar(
    pillar_id="administration",
    name="Administration",
    icon="🔧",
    description="Notices, records, disputes, instructions, governing law, audit, and document control.",
    key_questions=[
        "What form must notices take and how must they be delivered?",
        "What are the dispute escalation steps and timeframes?",
        "What records must be kept and for how long?",
        "What meetings and reports are required?",
        "What is the instruction and direction mechanism?",
        "What approval and review periods apply?",
        "What are the document control requirements?",
        "What audit rights exist?",
        "What is the governing law and jurisdiction?",
        "What confidentiality obligations apply?",
    ],
    red_flag_patterns=[
        "Notice requirements that are impractical to comply with",
        "No dispute escalation ladder before arbitration",
        "No time limit on contract administrator decisions",
        "Extensive record-keeping with undefined retention period",
        "Audit rights with no cost recovery mechanism",
        "Governing law in unfamiliar jurisdiction",
        "Confidentiality with no carve-outs for legal obligations",
        "No defined communication hierarchy",
    ],
    missing_protection_patterns=[
        "No notice period for employer instruction disputes",
        "No escalation ladder before formal dispute",
        "Audit rights without cost cap",
        "No document retention period defined",
        "No defined meeting schedule",
    ],
    weight_by_doc_type={
        "Subcontract Agreement": 0.05,
        "Prime Contract": 0.143,
        "Bid/Tender Response": 0.05,
        "RFP/Tender Document": 0.20,
        "Change Order/Variation": 0.01,
        "Claim Letter": 0.15,
        "General Contract": 0.10,
    },
)

EXIT = Pillar(
    pillar_id="exit",
    name="Exit",
    icon="🚪",
    description="Termination rights, completion, defects, final account, post-contract obligations, and disputes.",
    key_questions=[
        "What are the grounds for termination for cause?",
        "Is there a right to terminate for convenience?",
        "What compensation is payable on termination for convenience?",
        "What is the practical completion and handover process?",
        "What defects notification and rectification process applies?",
        "How is the final account settled and in what timeframe?",
        "What post-termination obligations survive?",
        "What dispute resolution mechanism applies — arbitration or litigation?",
        "Are there any limitation of actions provisions?",
        "What happens to materials and equipment on termination?",
    ],
    red_flag_patterns=[
        "Termination for convenience with minimal compensation",
        "Cure period on termination too short to be practical",
        "No final account mechanism or timeline",
        "Defects liability period exceeding industry standard",
        "Post-termination obligations extending indefinitely",
        "Dispute resolution in inconvenient forum",
        "Limitation period shorter than statutory minimum",
        "No step-in protection before termination",
    ],
    missing_protection_patterns=[
        "No termination for convenience compensation clause",
        "No final account timeline",
        "No dispute resolution mechanism",
        "No handover/completion checklist",
        "Surviving obligations not defined",
    ],
    weight_by_doc_type={
        "Subcontract Agreement": 0.05,
        "Prime Contract": 0.143,
        "Bid/Tender Response": 0.05,
        "RFP/Tender Document": 0.02,
        "Change Order/Variation": 0.01,
        "Claim Letter": 0.01,
        "General Contract": 0.10,
    },
)

# Ordered list — used by analysis engine and report generator
ALL_PILLARS: List[Pillar] = [
    MONEY, TIME, SCOPE, RISK_LIABILITY, RELATIONSHIPS, ADMINISTRATION, EXIT
]

PILLAR_MAP: Dict[str, Pillar] = {p.pillar_id: p for p in ALL_PILLARS}

# ── Document-type pillar weightings ──────────────────────────────────────────
# Each dict must sum to 1.0.  Unknown doc types fall back to "General Contract".

_EQUAL = 1 / 7  # ~0.1429

DOCUMENT_TYPE_WEIGHTS: Dict[str, Dict[str, float]] = {
    "Subcontract Agreement": {
        "money": 0.25, "time": 0.15, "scope": 0.20,
        "risk_liability": 0.20, "relationships": 0.10,
        "administration": 0.05, "exit": 0.05,
    },
    "Prime Contract": {
        "money": _EQUAL, "time": _EQUAL, "scope": _EQUAL,
        "risk_liability": _EQUAL, "relationships": _EQUAL,
        "administration": _EQUAL, "exit": _EQUAL,
    },
    "Bid/Tender Response": {
        "money": 0.25, "scope": 0.30, "time": 0.15,
        "risk_liability": 0.15, "relationships": 0.05,
        "administration": 0.05, "exit": 0.05,
    },
    "RFP/Tender Document": {
        "scope": 0.30, "administration": 0.20, "money": 0.20,
        "time": 0.15, "risk_liability": 0.10,
        "relationships": 0.03, "exit": 0.02,
    },
    "Change Order/Variation": {
        "money": 0.40, "scope": 0.30, "time": 0.20,
        "risk_liability": 0.05, "relationships": 0.03,
        "administration": 0.01, "exit": 0.01,
    },
    "Claim Letter": {
        "time": 0.30, "money": 0.30, "risk_liability": 0.20,
        "administration": 0.15, "scope": 0.03,
        "relationships": 0.01, "exit": 0.01,
    },
    "General Contract": {
        "money": _EQUAL, "time": _EQUAL, "scope": _EQUAL,
        "risk_liability": _EQUAL, "relationships": _EQUAL,
        "administration": _EQUAL, "exit": _EQUAL,
    },
}

# Aliases that map to one of the weight profiles above
_DOC_TYPE_ALIASES: Dict[str, str] = {
    "NDA/Confidentiality": "General Contract",
    "Purchase Order": "Subcontract Agreement",
    "Insurance Certificate": "General Contract",
    "Meeting Minutes": "General Contract",
    "Contract Amendment": "General Contract",
    "Correspondence/Letter": "General Contract",
}


def get_weights(doc_type: str) -> Dict[str, float]:
    """Return pillar weightings for a given document type."""
    if doc_type in DOCUMENT_TYPE_WEIGHTS:
        return DOCUMENT_TYPE_WEIGHTS[doc_type]
    resolved = _DOC_TYPE_ALIASES.get(doc_type, "General Contract")
    return DOCUMENT_TYPE_WEIGHTS[resolved]
