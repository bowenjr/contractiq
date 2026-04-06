"""
ContractIQ — 7-Pillar Analysis Engine
Orchestrates all LLM analysis tasks using the 7-pillar commercial framework.

progress_callback signature: (step_num, total_steps, step_name, message, percent)
  step_num 1-7 maps to the seven tasks; step_num 8 = complete.
"""

import json
from pathlib import Path
from typing import Callable, Dict, Any, List, Optional

from .llm_client import LMStudioClient
from .document_processor import DocumentProcessor
from .pillars import ALL_PILLARS, PILLAR_MAP, get_weights

_CONFIG_PATH = Path(__file__).parent.parent / "config.json"
_POSITIONS_PATH = Path(__file__).parent.parent / "positions.json"

SYSTEM_PROMPT = (
    "You are ContractIQ, an expert contract and bid review assistant "
    "for a Bids and Contracts Manager working on major infrastructure "
    "and construction projects in Canada. You have deep expertise in "
    "Canadian construction law, CCDC contracts, FIDIC, NEC, back-to-back "
    "subcontracts, procurement, and commercial risk assessment. "
    "Your analysis must be exhaustive — nothing can be missed. Every "
    "clause and every obligation matters. Always cite the source text "
    "that supports your findings. Respond only with valid JSON."
)

ProgressCB = Optional[Callable[[int, int, str, str, int], None]]


def _load_max_chars() -> int:
    try:
        cfg = json.loads(_CONFIG_PATH.read_text())
        return int(cfg.get("max_document_chars", 80000))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return 80000


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

    # ── Public entry point ────────────────────────────────────────────────────

    def run_full_analysis(
        self,
        text: str,
        filename: str,
        progress_callback: ProgressCB = None,
    ) -> Dict[str, Any]:
        """
        Run the complete 7-pillar analysis pipeline.
        progress_callback(step_num, total_steps, step_name, message, percent)
        """
        total_steps = 7

        def _cb(step_num: int, step_name: str, message: str, percent: int) -> None:
            if progress_callback:
                progress_callback(step_num, total_steps, step_name, message, percent)

        # Detect doc type first (cheap, uses only first 3000 chars)
        doc_meta = self._detect_document_type(text, filename)
        doc_type = doc_meta.get("doc_type", "General Contract")
        confidence = doc_meta.get("confidence", "Low")
        char_count = len(text)
        page_est = max(1, char_count // 2000)
        print(
            f"  Document: {char_count:,} chars | ~{page_est} pages | "
            f"Type: {doc_type} ({confidence})"
        )

        # Load company positions
        positions = _load_positions()

        # Build extraction text for large documents
        extraction_text = self._build_extraction_text(text)

        results: Dict[str, Any] = {
            "doc_type": doc_type,
            "doc_type_confidence": confidence,
            "counterparty": doc_meta.get("counterparty"),
            "contract_value": doc_meta.get("value_preview"),
        }

        # ── Step 1: Classify and summarise ───────────────────────────────────
        _cb(1, "Classification", "Classifying document and summarising...", 2)
        print(f"  [1/7] Classifying: {filename}")
        classify = self._classify_and_summarise(extraction_text, doc_type)
        results.update(classify)
        # Preserve doc_type from detection (more reliable than from summarise)
        results["doc_type"] = doc_type

        # ── Step 2: Extract parties ───────────────────────────────────────────
        _cb(2, "Parties", "Mapping parties and relationships...", 14)
        print("  [2/7] Mapping parties...")
        results["parties"] = self._extract_parties(extraction_text, doc_type)

        # ── Step 3: 7-Pillar analysis ─────────────────────────────────────────
        _cb(3, "Pillar Analysis", "Starting pillar analysis...", 28)
        print("  [3/7] Analysing 7 pillars...")
        results["pillars"] = self._analyse_all_pillars(
            text, doc_type, positions, _cb
        )

        # ── Step 4: Extract dates ─────────────────────────────────────────────
        _cb(4, "Dates", "Extracting critical dates and deadlines...", 72)
        print("  [4/7] Extracting dates...")
        results["dates"] = self._extract_all_dates(text, _cb)

        # ── Step 5: Extract obligations ───────────────────────────────────────
        _cb(5, "Obligations", "Extracting obligations and notice requirements...", 80)
        print("  [5/7] Extracting obligations...")
        results["obligations"] = self._extract_obligations(extraction_text, doc_type)

        # ── Step 6: Calculate risk score (pure Python) ────────────────────────
        _cb(6, "Risk Score", "Calculating risk score...", 88)
        print("  [6/7] Calculating risk score...")
        results["risk_score"] = self._calculate_risk_score(
            results["pillars"], doc_type
        )

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

    def _detect_document_type(self, text: str, filename: str) -> Dict:
        snippet = text[:3000]
        doc_types = (
            "Subcontract Agreement, Prime Contract, Bid/Tender Response, "
            "RFP/Tender Document, Change Order/Variation, Claim Letter, "
            "Correspondence/Letter, NDA/Confidentiality, Purchase Order, "
            "Insurance Certificate, Meeting Minutes, Contract Amendment, "
            "General Contract"
        )
        prompt = (
            f"Filename: {filename}\n\n"
            f"First 3000 characters of document:\n---\n{snippet}\n---\n\n"
            f"Classify this document. Possible types: {doc_types}\n\n"
            "Return JSON:\n"
            '{"doc_type": "...", "confidence": "High/Medium/Low", '
            '"counterparty": "counterparty name or null", '
            '"value_preview": "contract value if visible or null"}'
        )
        result = self.llm.complete_json(prompt, SYSTEM_PROMPT, temperature=0.05)
        if "error" in result:
            return {"doc_type": "General Contract", "confidence": "Low",
                    "counterparty": None, "value_preview": None}
        return result

    # ── Step 1: Classify and summarise ───────────────────────────────────────

    def _classify_and_summarise(self, text: str, doc_type: str) -> Dict:
        prompt = (
            f"Document type: {doc_type}\n\n"
            f"Document text:\n---\n{text[:15000]}\n---\n\n"
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
            f"Document text:\n---\n{text[:15000]}\n---\n\n"
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
        text: str,
        doc_type: str,
        positions: Dict,
        cb: Callable,
    ) -> List[Dict]:
        extraction_text = self._build_extraction_text(text)
        pillar_results = []

        for i, pillar in enumerate(ALL_PILLARS, 1):
            step_name = f"Pillar: {pillar.name}"
            msg = f"Analysing {pillar.icon} {pillar.name} pillar ({i}/7)..."
            # Percent range 28–70 split across 7 pillars
            pct = 28 + int(42 * (i - 1) / 7)
            cb(3, step_name, msg, pct)
            print(f"  [3/7] {msg}")
            result = self._analyse_single_pillar(
                extraction_text, pillar, doc_type, positions
            )
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
            f"Analyse this document for the {pillar.name} pillar. "
            "Be exhaustive. Cite source text for every finding.\n\n"
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
        )

        result = self.llm.complete_json(prompt, SYSTEM_PROMPT, max_tokens=4096)
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
        if len(text) <= 20_000:
            return self._extract_dates_single(text)

        # Chunk into 8000-char windows with 500-char overlap
        chunks = self.doc_processor.chunk_text(text, max_chars=8000, overlap=500)
        partial: List[Dict] = []
        for i, chunk in enumerate(chunks, 1):
            cb(4, "Dates", f"Extracting dates — chunk {i}/{len(chunks)}...",
               72 + int(6 * i / len(chunks)))
            result = self._extract_dates_single(chunk)
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
