"""
ContractIQ — 7-Pillar Analysis Engine
Orchestrates all LLM analysis tasks using the 7-pillar commercial framework.

progress_callback signature: (step_num, total_steps, step_name, message, percent)
  step_num 1-7 maps to the seven tasks; step_num 8 = complete.
"""

import json
import re
from pathlib import Path
from typing import Callable, Dict, Any, List, Optional

from .llm_client import LMStudioClient
from .document_processor import DocumentProcessor
from .pillars import ALL_PILLARS, PILLAR_MAP, get_weights

_CONFIG_PATH = Path(__file__).parent.parent / "config.json"
_POSITIONS_PATH = Path(__file__).parent.parent / "positions.json"

# Keywords used to route each pillar to relevant document sections
_PILLAR_KEYWORDS: Dict[str, List[str]] = {
    "money": [
        "payment", "invoice", "price", "cost", "fee", "retention", "holdback",
        "variation", "milestone", "liquidated", "damages", "escalation", "valuation",
        "amount", "sum", "money", "rate", "lump sum",
    ],
    "time": [
        "time", "schedule", "completion", "delay", "milestone", "extension",
        "notice", "programme", "deadline", "commencement", "practical completion",
        "long stop", "suspension", "date",
    ],
    "scope": [
        "scope", "work", "service", "supply", "design", "specification",
        "drawing", "standard", "interface", "exclusion", "provisional",
        "prime cost", "change", "variation", "deliverable",
    ],
    "risk_liability": [
        "liability", "indemnity", "insurance", "warranty", "risk",
        "force majeure", "limitation", "consequential", "guarantee",
        "bond", "fitness", "defect",
    ],
    "relationships": [
        "subcontract", "assign", "novate", "personnel", "back-to-back",
        "flow-down", "step-in", "consent", "approval", "administrator",
        "party", "parties",
    ],
    "administration": [
        "notice", "dispute", "arbitration", "governing", "law",
        "jurisdiction", "confidential", "record", "audit",
        "communication", "instruction", "general",
    ],
    "exit": [
        "termination", "terminate", "completion", "handover", "defect",
        "final account", "suspension", "insolvency", "convenience",
        "cause", "remedy", "cure",
    ],
}

SYSTEM_PROMPT = """You are a senior legal and commercial advisor \
with 20+ years of experience in Canadian construction and \
commercial contract law, infrastructure procurement, equipment \
supply, and contract negotiation. You review contracts on behalf \
of a Bids and Contracts Manager at a major electrical equipment \
supplier and systems integrator operating across Canada.

Your role is to protect your client's commercial and legal \
position by identifying every risk, obligation, and exposure \
in the documents you review — including risks that are not \
immediately obvious, obligations buried in definitions or \
schedules, and asymmetries that favour the other party.

YOUR ANALYTICAL APPROACH:

Assume the other party's lawyers drafted this document to favour \
their client. Every ambiguity, every undefined term, every \
cross-reference to an external document, and every silence on \
a key commercial point is potentially intentional. Identify all \
of them.

Look beyond the obvious clauses. The most dangerous provisions \
are often:
- Definitions that expand obligations beyond their apparent scope
- Cross-references to documents not provided or not reviewed
- Silence on key protections that would normally be standard
- Boilerplate language that has been subtly modified
- Schedules and appendices that override the main agreement
- Deemed acceptance provisions triggered by inaction
- Notification requirements with short time bars
- Broad indemnities buried in operational clauses
- IP and confidentiality provisions with unlimited duration
- Unlimited liability hidden in warranty or fitness for purpose \
  obligations

YOUR LEGAL KNOWLEDGE BASE:

Canadian Construction and Commercial Law:
- Ontario Construction Act — lien rights, trust provisions, \
  prompt payment, adjudication
- British Columbia, Alberta, and other provincial lien acts
- CCDC 2 (Stipulated Price), CCDC 5A/5B (Construction Management),\
  CCDC 14 (Design-Build), CCDC 17 (Stipulated Price Subcontract)
- CCA 1 subcontract and standard subcontract forms
- Sale of Goods Act and implied terms in supply contracts
- Consumer Protection Act carve-outs in commercial contracts
- Canadian competition law implications in exclusive arrangements

Contract Structures:
- Back-to-back and flow-down subcontract structures
- P3 and AFP project delivery models
- Design-Build, Design-Bid-Build, EPC, EPCM
- Alliance and integrated project delivery
- Framework agreements and call-off contracts
- Master supply agreements with purchase order releases
- Equipment supply with installation and commissioning
- Service agreements, maintenance contracts, AMAs
- Licensing and technology transfer agreements
- NDAs, teaming agreements, and MOU structures
- Letters of intent and their binding effect

Risk and Liability:
- Limitation of liability — caps, exclusions, and carve-outs
- Consequential loss — definition, exclusion, and carve-outs
- Indemnity — mutual vs one-sided, cross-indemnities
- Insurance — required types, limits, additional insured, \
  subrogation waiver
- Performance bonds, labour and material bonds, \
  parent company guarantees, letters of credit
- Fitness for purpose vs reasonable skill and care
- Strict liability provisions in supply contracts
- Product liability and latent defects
- Professional indemnity and errors and omissions

Commercial and Financial:
- Pay-when-paid and pay-if-paid — drafting and enforceability
- Prompt payment obligations under Construction Act
- Retention and holdback — statutory vs contractual
- Variation and change order mechanisms — entitlement and valuation
- Provisional sums and prime cost items
- Price escalation and adjustment mechanisms
- Liquidated damages — genuine pre-estimate, penalties, caps
- Bonus provisions and performance incentives
- Set-off and withholding rights
- Final account mechanisms and time bars
- Foreign exchange risk in multi-currency contracts

Time and Schedule:
- Extension of time — grounds, notice requirements, time bars
- Concurrent delay — apportionment approaches
- Prevention principle and time at large
- Acceleration — instructed vs constructive
- Practical completion — definition and consequences
- Sectional completion and partial possession
- Defects liability period obligations
- Limitation periods — contractual and statutory

Procurement and Supply:
- Scope definition risk in equipment supply contracts
- Interface obligations between packages
- Employer-furnished information and materials
- Design review and approval obligations
- Factory acceptance testing — rights and obligations
- Inspection and rejection rights
- Title and risk of loss in transit
- Delivery terms — Incoterms and their implications
- Spare parts and special tools obligations
- Obsolescence and end-of-life provisions

Post-Award and Administration:
- Notice provisions — form, method, timing, consequences of failure
- Variation instruction requirements
- Dispute escalation and resolution — negotiation, mediation, \
  adjudication, arbitration, litigation
- Governing law and jurisdiction — enforceability considerations
- Confidentiality — scope, duration, exceptions, survival
- Assignment and novation — consent requirements
- Audit rights — scope, frequency, cost
- Record keeping — what, how long, access
- Key personnel — nomination, replacement, approval

Exit and Termination:
- Termination for cause — grounds, cure rights, consequences
- Termination for convenience — scope, compensation, exclusions
- Termination for insolvency — automatic vs discretionary
- Suspension — rights, duration, compensation, deemed termination
- Consequences of termination — demobilization, payment, IP
- Survival provisions — what continues after termination
- Step-in rights — triggers and procedures

WHAT TO LOOK FOR THAT IS NOT EVIDENT:

1. HIDDEN SCOPE EXPANSION
   Defined terms like "Works", "Services", "Deliverables", or \
   "Project" that include more than the obvious scope. \
   Cross-references that bring in broader obligations.

2. SILENT OBLIGATIONS
   Things the contract assumes you will do but does not explicitly \
   state — often found in definitions, recitals, and by \
   cross-reference to standards, codes, or other documents.

3. ASYMMETRIC DRAFTING
   Obligations that apply to one party but not the other. \
   Rights that the employer has but the contractor does not. \
   Time bars on contractor claims but not employer claims.

4. INCORPORATED DOCUMENTS
   Any reference to another document — prime contract, \
   employer's requirements, specifications, standards — \
   that is not provided. Each one is a potential source of \
   unknown obligations.

5. DEEMED PROVISIONS
   Anything deemed accepted, deemed approved, deemed complete, \
   or deemed waived by passage of time or failure to act. \
   These are traps for the unwary.

6. NOTIFICATION TIME BARS
   Requirements to notify within very short periods as a \
   condition precedent to any claim or entitlement. \
   Missing these extinguishes rights entirely.

7. BROAD WARRANTIES AND FITNESS FOR PURPOSE
   Any obligation to achieve a result, meet a performance \
   standard, or be fit for purpose that goes beyond reasonable \
   skill and care. Combined with unlimited liability this is \
   catastrophic exposure.

8. UNLIMITED DURATION OBLIGATIONS
   Confidentiality, IP restrictions, non-compete, or warranty \
   obligations with no end date or indefinite survival.

9. UNILATERAL RIGHTS
   Employer rights to change scope, extend time, suspend, \
   reduce quantities, or modify terms without compensation \
   or consent.

10. PRICE CERTAINTY RISKS
    Fixed prices over long durations with no escalation. \
    Provisional sums that may be instructed in full. \
    Variations without agreed rates. Open-ended cost-plus \
    obligations. Currency exposure.

YOUR OUTPUT REQUIREMENTS:

For every finding:
- Cite the exact article, section, or clause number
- Quote the specific contract language that gives rise to the risk
- Explain the legal and commercial consequence in plain English
- State what the market standard or CCDC position would be
- Recommend a specific negotiation position or redline approach
- Flag whether legal review is required before signing
- Identify which internal team should own the response:
  Contracts, Legal, Estimating, Operations, or Management

Be exhaustive. Incomplete analysis of a commercial contract \
causes real financial and legal harm. A risk not identified \
is a risk not managed.

Respond only with valid JSON as instructed."""

_PILLAR_SUFFIX = """

IMPORTANT ANALYSIS INSTRUCTIONS:
- Be exhaustive — identify every provision related to this pillar
- Look beyond obvious clauses — check definitions, schedules, \
  appendices, and cross-references
- Flag every asymmetry that favours the other party
- Identify every silence where a protective clause is missing
- Quote specific contract language for every finding
- State the market standard for comparison where relevant
- If a provision incorporates an external document by reference, \
  flag it as a critical unknown risk
- This analysis will be reviewed by a contracts manager and lawyer\
  before any negotiation — be thorough and precise

Do not summarise. Do not abbreviate. Identify everything."""

ProgressCB = Optional[Callable[[int, int, str, str, int], None]]


def _load_config() -> Dict:
    try:
        return json.loads(_CONFIG_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _load_max_chars() -> int:
    cfg = _load_config()
    return int(cfg.get("max_document_chars", 60000))


def _load_positions() -> Dict:
    try:
        return json.loads(_POSITIONS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


class AnalysisEngine:
    MAX_CHARS = _load_max_chars()

    def __init__(self, llm: LMStudioClient):
        self.llm = llm
        self.doc_processor = DocumentProcessor()
        cfg = _load_config()
        self.MAX_CHARS_PER_PILLAR      = int(cfg.get("max_chars_per_pillar",      12_000))
        self.MAX_CHARS_CLASSIFICATION  = int(cfg.get("max_chars_classification",   2_000))
        self.MAX_CHARS_PARTIES         = int(cfg.get("max_chars_parties",          5_000))
        self.MAX_CHARS_DATES           = int(cfg.get("max_chars_dates",           60_000))

    # ── Public entry point ────────────────────────────────────────────────────

    def run_full_analysis(
        self,
        text: str,
        filename: str,
        preprocessed: Optional[Dict] = None,
        progress_callback: ProgressCB = None,
        cancel_check: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """
        Run the complete 7-pillar analysis pipeline.
        progress_callback(step_num, total_steps, step_name, message, percent)

        preprocessed: output dict from DocumentPreprocessor.preprocess().
          When provided, structured_markdown and section_index are used so
          each pillar receives only its relevant sections (max 15 000 chars).
        """
        total_steps = 7

        def _cb(step_num: int, step_name: str, message: str, percent: int) -> None:
            if progress_callback:
                progress_callback(step_num, total_steps, step_name, message, percent)

        # Choose analysis source and section routing map
        if preprocessed:
            analysis_source = preprocessed.get("structured_markdown") or text
            section_index   = preprocessed.get("section_index", {})
            raw_chars       = len(text)
            proc_chars      = len(analysis_source)
            reduction       = round(100 * (1 - proc_chars / max(1, raw_chars)))
            print(
                f"  Using pre-processed markdown: {proc_chars:,} chars "
                f"({reduction}% reduction from raw)"
            )
        else:
            analysis_source = text
            section_index   = {}
            print(f"  Using raw text: {len(text):,} chars")

        # Detect doc type (first 2000 chars only — title, parties, recitals)
        doc_meta   = self._detect_document_type(text, filename)
        doc_type   = doc_meta.get("doc_type", "General Contract")
        confidence = doc_meta.get("confidence", "Low")
        char_count = len(text)
        page_est   = max(1, char_count // 2000)
        print(
            f"  Document: {char_count:,} chars | ~{page_est} pages | "
            f"Type: {doc_type} ({confidence})"
        )

        positions = _load_positions()

        # Extraction window for steps that need whole-doc context
        extraction_text = self._build_extraction_text(analysis_source)

        results: Dict[str, Any] = {
            "doc_type":            doc_type,
            "doc_type_confidence": confidence,
            "counterparty":        doc_meta.get("counterparty"),
            "contract_value":      doc_meta.get("value_preview"),
        }

        # ── Step 1: Classify and summarise ───────────────────────────────────
        _cb(1, "Classification", "Classifying document and summarising...", 2)
        print(f"  [1/7] Classifying: {filename}")
        classify = self._classify_and_summarise(
            analysis_source[:self.MAX_CHARS_CLASSIFICATION * 8], doc_type
        )
        results.update(classify)
        results["doc_type"] = doc_type  # detection is more reliable

        # ── Step 2: Extract parties ───────────────────────────────────────────
        _cb(2, "Parties", "Mapping parties and relationships...", 14)
        print("  [2/7] Mapping parties...")
        results["parties"] = self._extract_parties(
            analysis_source[:self.MAX_CHARS_PARTIES], doc_type
        )

        # ── Step 3: 7-Pillar analysis (section-routed) ────────────────────────
        _cb(3, "Pillar Analysis", "Starting pillar analysis...", 28)
        print("  [3/7] Analysing 7 pillars...")
        results["pillars"] = self._analyse_all_pillars(
            analysis_source, section_index, doc_type, positions, _cb, cancel_check
        )

        # ── Step 4: Extract dates (max 3 LLM calls) ───────────────────────────
        _cb(4, "Dates", "Extracting critical dates and deadlines...", 72)
        print("  [4/7] Extracting dates...")
        results["dates"] = self._extract_all_dates(text, _cb)

        # ── Step 5: Extract obligations ───────────────────────────────────────
        _cb(5, "Obligations", "Extracting obligations and notice requirements...", 80)
        print("  [5/7] Extracting obligations...")
        results["obligations"] = self._extract_obligations(extraction_text, doc_type)

        # ── Step 6: Risk score (pure Python) ──────────────────────────────────
        _cb(6, "Risk Score", "Calculating risk score...", 88)
        print("  [6/7] Calculating risk score...")
        results["risk_score"] = self._calculate_risk_score(results["pillars"], doc_type)

        # ── Step 7: Recommendations ───────────────────────────────────────────
        _cb(7, "Recommendations", "Generating recommendations...", 94)
        print("  [7/7] Generating recommendations...")
        results["recommendations"] = self._generate_recommendations(
            results["pillars"], results["risk_score"], positions, doc_type
        )

        return results

    # ── Document text preparation ─────────────────────────────────────────────

    def _build_extraction_text(self, text: str) -> str:
        """For large docs, build an intelligent ~60 000-char extraction window."""
        if len(text) <= 60_000:
            return text
        head = text[:20_000]
        tail = text[-10_000:]
        middle_text = text[20_000:-10_000]
        middle_len = len(middle_text)
        samples = []
        if middle_len > 0:
            for i in range(5):
                start = int(middle_len * i / 5)
                samples.append(middle_text[start:start + 6_000])
        sep = "\n\n[...document section continues...]\n\n"
        return head + sep + sep.join(samples) + sep + tail

    # ── Document type detection ───────────────────────────────────────────────

    def _get_pillar_text(
        self,
        markdown: str,
        section_index: Dict,
        pillar,
    ) -> str:
        """
        Return only the sections of markdown relevant to this pillar.
        Uses section_index (heading → keyword hits) for fast lookup,
        then falls back to content keyword scanning.
        Caps output at MAX_CHARS_PER_PILLAR.
        """
        keywords = _PILLAR_KEYWORDS.get(pillar.pillar_id, [])
        max_chars = self.MAX_CHARS_PER_PILLAR

        # Headings matched via the pre-built index
        matched_headings: set = set()
        for kw in keywords:
            for heading in section_index.get(kw, []):
                matched_headings.add(heading)

        # Split markdown into ## / ### blocks
        blocks = re.split(r"(?=^##+ )", markdown, flags=re.MULTILINE)

        matched_parts: List[str] = []
        for block in blocks:
            heading_line = block.split("\n", 1)[0].strip("# ").strip()
            is_match = heading_line in matched_headings

            if not is_match:
                # Content scan: 3+ keyword hits = relevant
                block_lower = block.lower()
                hits = sum(1 for kw in keywords if kw in block_lower)
                is_match = hits >= 3

            if is_match:
                matched_parts.append(block)

        matched_text = "\n".join(matched_parts)
        n_sections   = len(matched_parts)
        chars        = len(matched_text)
        print(f"  {pillar.name}: routing to {n_sections} sections ({chars:,} chars)")

        if len(matched_text) > max_chars:
            matched_text = matched_text[:max_chars]
        elif len(matched_text) < 3_000:
            # Always include opening definitions/general provisions
            preamble = markdown[:5_000]
            matched_text = preamble + "\n\n" + matched_text
            if len(matched_text) > max_chars:
                matched_text = matched_text[:max_chars]

        # Hard cap — safety net regardless of all routing logic above
        if len(matched_text) > max_chars:
            print(f"  WARNING: {pillar.name} text hard-capped at {max_chars:,} chars")
            matched_text = matched_text[:max_chars]

        return matched_text

    def _detect_document_type(self, text: str, filename: str) -> Dict:
        snippet = text[:self.MAX_CHARS_CLASSIFICATION]
        doc_types = (
            "Subcontract Agreement, Prime Contract, Bid/Tender Response, "
            "RFP/Tender Document, Change Order/Variation, Claim Letter, "
            "Correspondence/Letter, NDA/Confidentiality, Purchase Order, "
            "Insurance Certificate, Meeting Minutes, Contract Amendment, "
            "General Contract"
        )
        prompt = (
            f"Filename: {filename}\n\n"
            f"Opening of document (first {self.MAX_CHARS_CLASSIFICATION} chars — "
            f"title, parties, recitals):\n---\n{snippet}\n---\n\n"
            f"Classify this document. Possible types: {doc_types}\n\n"
            "Return JSON:\n"
            '{"doc_type": "...", "confidence": "High/Medium/Low", '
            '"counterparty": "counterparty name or null", '
            '"value_preview": "contract value if visible or null"}'
        )
        result = self.llm.complete_json(prompt, SYSTEM_PROMPT, temperature=0.05,
                                        context_label="document type detection")
        if "error" in result:
            return {"doc_type": "General Contract", "confidence": "Low",
                    "counterparty": None, "value_preview": None}
        return result

    # ── Step 1: Classify and summarise ───────────────────────────────────────

    def _classify_and_summarise(self, text: str, doc_type: str) -> Dict:
        prompt = (
            f"Document type: {doc_type}\n\n"
            f"Document text:\n---\n{text}\n---\n\n"
            "Provide a classification and executive summary.\n\n"
            "Return JSON with EXACTLY these fields:\n"
            "{\n"
            '  "executive_summary": "3-4 sentence plain English summary of the document, '
            'parties, and core commercial arrangement",\n'
            '  "key_subject": "main subject matter in one sentence",\n'
            '  "contract_value": "monetary value if stated, else null",\n'
            '  "contract_duration": "duration or term if stated, else null",\n'
            '  "governing_law": "jurisdiction/governing law if stated, else null"\n'
            "}"
        )
        result = self.llm.complete_json(prompt, SYSTEM_PROMPT)
        if "error" in result:
            return {
                "executive_summary": "Analysis could not generate a summary.",
                "key_subject": None, "contract_value": None,
                "contract_duration": None, "governing_law": None,
            }
        return result

    # ── Step 2: Extract parties ───────────────────────────────────────────────

    def _extract_parties(self, text: str, doc_type: str) -> Dict:
        prompt = (
            f"Document type: {doc_type}\n\n"
            f"Document text:\n---\n{text}\n---\n\n"
            "Identify all parties and their obligations.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "parties": [\n'
            '    {"name": "...", "role": "e.g. Owner/Contractor/Subcontractor/Employer", '
            '"description": "...", "key_obligations": ["..."]}\n'
            "  ],\n"
            '  "relationship_summary": "one sentence describing the commercial relationship"\n'
            "}"
        )
        result = self.llm.complete_json(prompt, SYSTEM_PROMPT)
        if "error" in result:
            return {"parties": [], "relationship_summary": ""}
        return result

    # ── Step 3: 7-Pillar analysis ─────────────────────────────────────────────

    def _analyse_all_pillars(
        self,
        markdown: str,
        section_index: Dict,
        doc_type: str,
        positions: Dict,
        cb: Callable,
        cancel_check: Optional[Callable] = None,
    ) -> List[Dict]:
        pillar_results = []

        for i, pillar in enumerate(ALL_PILLARS, 1):
            if cancel_check and cancel_check():
                raise InterruptedError("Analysis cancelled by user")
            step_name = f"Pillar: {pillar.name}"
            msg = f"Analysing {pillar.icon} {pillar.name} pillar ({i}/7)..."
            pct = 28 + int(42 * (i - 1) / 7)
            cb(3, step_name, msg, pct)
            print(f"  [3/7] {msg}")

            if section_index:
                pillar_text = self._get_pillar_text(markdown, section_index, pillar)
            else:
                pillar_text = self._build_extraction_text(markdown)

            result = self._analyse_single_pillar(pillar_text, pillar, doc_type, positions)
            pillar_results.append(result)

        return pillar_results

    def _analyse_single_pillar(
        self,
        text: str,
        pillar,
        doc_type: str,
        positions: Dict,
    ) -> Dict:
        questions_block = "\n".join(
            f"  {i+1}. {q}" for i, q in enumerate(pillar.key_questions)
        )
        red_flags_block = "\n".join(
            f"  - {r}" for r in pillar.red_flag_patterns
        )
        positions_block = ""
        if positions.get("standard_positions"):
            pos_lines = "\n".join(
                f"  {k}: {v}"
                for k, v in positions["standard_positions"].items()
            )
            positions_block = (
                f"\nCompany standard positions for reference — flag any deviations:\n"
                f"{pos_lines}\n"
            )

        prompt = (
            f"PILLAR: {pillar.name} ({pillar.description})\n"
            f"DOCUMENT TYPE: {doc_type}\n"
            f"{positions_block}\n"
            f"KEY QUESTIONS TO ANSWER:\n{questions_block}\n\n"
            f"RED FLAGS TO LOOK FOR:\n{red_flags_block}\n\n"
            f"DOCUMENT TEXT:\n---\n{text}\n---\n\n"
            f"Analyse this document for the {pillar.name} pillar.\n\n"
            "Return JSON with EXACTLY this structure:\n"
            "{\n"
            f'  "pillar_id": "{pillar.pillar_id}",\n'
            f'  "pillar_name": "{pillar.name}",\n'
            '  "status": "Good|Issues Found|Critical Issues|Not Applicable",\n'
            '  "summary": "2-3 sentence summary of this pillar",\n'
            '  "score": <integer 0-100 where 100=fully protected, 0=critical risk>,\n'
            '  "findings": [\n'
            '    {\n'
            '      "finding": "finding title",\n'
            '      "detail": "detailed explanation",\n'
            '      "severity": "Critical|High|Medium|Low|Info",\n'
            '      "clause_reference": "clause number/heading or null",\n'
            '      "source_excerpt": "verbatim quote max 200 chars or null",\n'
            '      "deviation_from_position": "how this deviates from company position or null",\n'
            '      "recommended_action": "specific action to take"\n'
            "    }\n"
            "  ],\n"
            '  "red_flags": [\n'
            '    {\n'
            '      "flag": "flag title",\n'
            '      "severity": "Critical|High|Medium|Low",\n'
            '      "description": "description",\n'
            '      "location": "where in document",\n'
            '      "source_excerpt": "verbatim quote max 200 chars or null"\n'
            "    }\n"
            "  ],\n"
            '  "missing_protections": ["list of missing clauses or protections"],\n'
            '  "negotiation_points": [\n'
            '    {\n'
            '      "issue": "issue title",\n'
            '      "current_position": "what the document currently says",\n'
            '      "primary_ask": "what to negotiate for",\n'
            '      "fallback": "minimum acceptable position",\n'
            '      "priority": "High|Medium|Low",\n'
            '      "requires_legal": true|false\n'
            "    }\n"
            "  ],\n"
            '  "questions_answered": {\n'
            '    "question text": "answer or Not Found"\n'
            "  }\n"
            "}"
            + _PILLAR_SUFFIX
        )

        chars = len(prompt)
        print(f"  Sending {chars:,} chars to LLM for {pillar.name} analysis")
        result = self.llm.complete_json(prompt, SYSTEM_PROMPT, max_tokens=4096,
                                        context_label=f"{pillar.name} pillar analysis")
        if "error" in result or not result.get("pillar_id"):
            return {
                "pillar_id": pillar.pillar_id,
                "pillar_name": pillar.name,
                "status": "Issues Found",
                "summary": "Analysis could not complete for this pillar.",
                "score": 50,
                "findings": [],
                "red_flags": [],
                "missing_protections": [],
                "negotiation_points": [],
                "questions_answered": {},
            }
        return result

    # ── Step 4: Extract all dates ─────────────────────────────────────────────

    def _extract_all_dates(self, text: str, cb: Callable) -> Dict:
        max_chars = self.MAX_CHARS_DATES  # default 60 000

        if len(text) <= max_chars:
            cb(4, "Dates", "Extracting dates — single pass...", 73)
            return self._extract_dates_single(text[:max_chars])

        # Document exceeds limit — split into max 3 equal passes
        third = len(text) // 3
        chunks = [
            text[:third],
            text[third: third * 2],
            text[third * 2:],
        ]
        partial: List[Dict] = []
        for i, chunk in enumerate(chunks, 1):
            cb(4, "Dates", f"Extracting dates — pass {i}/3...", 72 + i * 2)
            result = self._extract_dates_single(chunk[:max_chars])
            if "error" not in result:
                partial.append(result)

        if not partial:
            return {"effective_date": None, "expiry_date": None,
                    "key_dates": [], "notice_periods": [],
                    "time_sensitive_obligations": []}
        if len(partial) == 1:
            return partial[0]
        return self._consolidate_dates(partial)

    def _extract_dates_single(self, text: str) -> Dict:
        prompt = (
            f"Extract all important dates, deadlines, and time-sensitive provisions.\n\n"
            f"Document text:\n---\n{text}\n---\n\n"
            "Return JSON:\n"
            "{\n"
            '  "effective_date": "contract start/effective date or null",\n'
            '  "expiry_date": "contract end/expiry date or null",\n'
            '  "key_dates": [\n'
            '    {"description": "...", "date": "...", "is_deadline": true, "importance": "High|Medium|Low"}\n'
            "  ],\n"
            '  "notice_periods": [\n'
            '    {"trigger": "what requires notice", "period": "notice period required"}\n'
            "  ],\n"
            '  "time_sensitive_obligations": ["list of time-sensitive obligations"]\n'
            "}"
        )
        return self.llm.complete_json(prompt, SYSTEM_PROMPT)

    def _consolidate_dates(self, partials: List[Dict]) -> Dict:
        combined = json.dumps(partials, indent=2)[:6000]
        prompt = (
            "Below are date findings from multiple sections of the same document. "
            "Consolidate into one result. Deduplicate. Keep the most specific version.\n\n"
            f"Partial findings:\n{combined}\n\n"
            "Return JSON:\n"
            "{\n"
            '  "effective_date": "contract start/effective date or null",\n'
            '  "expiry_date": "contract end/expiry date or null",\n'
            '  "key_dates": [\n'
            '    {"description": "...", "date": "...", "is_deadline": true, "importance": "High|Medium|Low"}\n'
            "  ],\n"
            '  "notice_periods": [\n'
            '    {"trigger": "what requires notice", "period": "notice period required"}\n'
            "  ],\n"
            '  "time_sensitive_obligations": ["list of time-sensitive obligations"]\n'
            "}"
        )
        result = self.llm.complete_json(prompt, SYSTEM_PROMPT)
        if "error" in result:
            # Fall back: merge manually
            merged: Dict = {"effective_date": None, "expiry_date": None,
                            "key_dates": [], "notice_periods": [],
                            "time_sensitive_obligations": []}
            for p in partials:
                if not merged["effective_date"] and p.get("effective_date"):
                    merged["effective_date"] = p["effective_date"]
                if not merged["expiry_date"] and p.get("expiry_date"):
                    merged["expiry_date"] = p["expiry_date"]
                merged["key_dates"].extend(p.get("key_dates", []))
                merged["notice_periods"].extend(p.get("notice_periods", []))
                merged["time_sensitive_obligations"].extend(
                    p.get("time_sensitive_obligations", [])
                )
            return merged
        return result

    # ── Step 5: Extract obligations ───────────────────────────────────────────

    def _extract_obligations(self, text: str, doc_type: str) -> List[Dict]:
        prompt = (
            f"Document type: {doc_type}\n\n"
            f"Document text:\n---\n{text[:20000]}\n---\n\n"
            "Extract every obligation from this document. For each obligation identify "
            "the responsible party, obligation type, description, trigger event, "
            "deadline, and whether notice is required.\n\n"
            "Obligation types: notice|payment|delivery|approval|reporting|insurance|other\n\n"
            "Return JSON:\n"
            "{\n"
            '  "obligations": [\n'
            '    {\n'
            '      "party": "responsible party",\n'
            '      "obligation_type": "notice|payment|delivery|approval|reporting|insurance|other",\n'
            '      "description": "what must be done",\n'
            '      "trigger": "triggering event or condition",\n'
            '      "deadline": "deadline or timeframe or null",\n'
            '      "notice_required": "notice requirements or null"\n'
            "    }\n"
            "  ]\n"
            "}"
        )
        result = self.llm.complete_json(prompt, SYSTEM_PROMPT)
        if "error" in result:
            return []
        return result.get("obligations", [])

    # ── Step 6: Calculate risk score (pure Python) ────────────────────────────

    def _calculate_risk_score(
        self, pillar_results: List[Dict], doc_type: str
    ) -> Dict:
        weights = get_weights(doc_type)

        pillar_breakdown = []
        critical_count = 0
        high_count = 0
        weighted_risk_sum = 0.0
        weight_total = 0.0

        for pr in pillar_results:
            pid = pr.get("pillar_id", "")
            score = pr.get("score", 50)
            if not isinstance(score, (int, float)):
                score = 50
            score = max(0, min(100, score))

            weight = weights.get(pid, 1 / 7)
            pillar_risk = 100 - score
            weighted_risk_sum += pillar_risk * weight
            weight_total += weight

            # Count flags
            for flag in pr.get("red_flags", []):
                sev = flag.get("severity", "")
                if sev == "Critical":
                    critical_count += 1
                elif sev == "High":
                    high_count += 1

            pillar_breakdown.append({
                "pillar_id": pid,
                "pillar_name": pr.get("pillar_name", pid),
                "score": score,
                "status": pr.get("status", "Issues Found"),
                "weight": round(weight, 3),
            })

        # Weighted average risk (0-100)
        if weight_total > 0:
            base_score = weighted_risk_sum / weight_total
        else:
            base_score = 50.0

        # Adjust for flag counts
        adjustment = min(critical_count * 5 + high_count * 2, 30)
        overall_score = round(min(base_score + adjustment, 100))

        if overall_score <= 25:
            level = "Low"
        elif overall_score <= 50:
            level = "Medium"
        elif overall_score <= 75:
            level = "High"
        else:
            level = "Critical"

        return {
            "overall_score": overall_score,
            "level": level,
            "critical_flags": critical_count,
            "high_flags": high_count,
            "pillar_breakdown": pillar_breakdown,
            "score_rationale": (
                f"Overall risk score {overall_score}/100 ({level}). "
                f"{critical_count} critical flag(s) and {high_count} high flag(s) identified "
                f"across {len(pillar_results)} pillars."
            ),
        }

    # ── Step 7: Generate recommendations ─────────────────────────────────────

    def _generate_recommendations(
        self,
        pillar_results: List[Dict],
        risk_score: Dict,
        positions: Dict,
        doc_type: str,
    ) -> Dict:
        # Pass pillar summaries, not raw text
        pillar_summary = []
        for pr in pillar_results:
            pillar_summary.append({
                "pillar": pr.get("pillar_name"),
                "status": pr.get("status"),
                "score": pr.get("score"),
                "summary": pr.get("summary"),
                "red_flags_count": len(pr.get("red_flags", [])),
                "critical_issues": [
                    f.get("finding") for f in pr.get("findings", [])
                    if f.get("severity") in ("Critical", "High")
                ][:5],
                "top_negotiation_points": pr.get("negotiation_points", [])[:3],
            })

        prompt = (
            f"Document type: {doc_type}\n"
            f"Risk score: {risk_score.get('overall_score')}/100 "
            f"({risk_score.get('level')})\n\n"
            f"Pillar analysis summary:\n{json.dumps(pillar_summary, indent=2)}\n\n"
            "Generate actionable recommendations for the Contracts Manager.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "overall_recommendation": "Approve|Approve with Amendments|Negotiate|Reject",\n'
            '  "recommendation_rationale": "2-3 sentence explanation",\n'
            '  "immediate_actions": [\n'
            '    {"priority": 1, "action": "...", "reason": "...", "pillar": "...", '
            '"owner": "Contracts|Legal|Estimating|Operations|Management"}\n'
            "  ],\n"
            '  "negotiation_points": [\n'
            '    {"issue": "...", "current_position": "...", "primary_ask": "...", '
            '"fallback": "...", "priority": "High|Medium|Low", "requires_legal": false}\n'
            "  ],\n"
            '  "before_signing": ["checklist items"],\n'
            '  "legal_escalation_items": ["items requiring legal review"],\n'
            '  "estimating_review_items": ["items for estimating team"],\n'
            '  "operations_review_items": ["items for operations team"],\n'
            '  "key_risks_summary": "paragraph summarising the top 3-5 risks"\n'
            "}"
        )
        result = self.llm.complete_json(prompt, SYSTEM_PROMPT)
        if "error" in result:
            return {
                "overall_recommendation": "Negotiate",
                "recommendation_rationale": "Unable to generate recommendations. Manual review required.",
                "immediate_actions": [],
                "negotiation_points": [],
                "before_signing": [],
                "legal_escalation_items": [],
                "estimating_review_items": [],
                "operations_review_items": [],
                "key_risks_summary": "",
            }
        return result
