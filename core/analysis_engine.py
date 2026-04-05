"""
Analysis Engine for ContractIQ.
Orchestrates all LLM-powered analysis tasks on a contract document.

Each analysis task is a focused, structured prompt that returns JSON.
Tasks:
  1. Document classification & summary
  2. Party & obligation mapping
  3. Key clause extraction
  4. Risk scoring & red flags
  5. Critical dates & deadlines
  6. Bid-specific analysis (if applicable)
  7. Recommendations
"""

import json
from typing import Dict, Any, List
from .llm_client import LMStudioClient
from .document_processor import DocumentProcessor


SYSTEM_LEGAL = """You are ContractIQ, an expert contract and bid review assistant for a Bids and Contracts Manager.
You have deep expertise in contract law, commercial agreements, risk assessment, and procurement.
Your analysis is precise, professional, and actionable.
Always respond only with valid JSON as instructed."""


class AnalysisEngine:
    def __init__(self, llm: LMStudioClient):
        self.llm = llm
        self.doc_processor = DocumentProcessor()

    def run_full_analysis(self, text: str, filename: str) -> Dict[str, Any]:
        """
        Run the complete analysis pipeline. Returns a structured results dict.
        Handles large documents by chunking where necessary.
        """
        # Truncate if very large (keep first ~12000 chars for main analysis)
        # This is configurable based on your model's context window
        analysis_text = text[:14000] if len(text) > 14000 else text
        
        results = {}

        # Step 1: Document classification + executive summary
        print(f"  [1/6] Classifying document: {filename}")
        classification = self._classify_document(analysis_text, filename)
        results.update(classification)

        # Step 2: Party & obligation mapping
        print("  [2/6] Mapping parties and obligations...")
        results["parties"] = self._extract_parties(analysis_text)

        # Step 3: Key clause extraction
        print("  [3/6] Extracting key clauses...")
        results["clauses"] = self._extract_clauses(analysis_text)

        # Step 4: Risk scoring
        print("  [4/6] Scoring risks...")
        results["risk_score"] = self._score_risk(analysis_text, results.get("clauses", {}))

        # Step 5: Critical dates and deadlines
        print("  [5/6] Identifying dates and deadlines...")
        results["dates"] = self._extract_dates(analysis_text)

        # Step 6: Recommendations
        print("  [6/6] Generating recommendations...")
        results["recommendations"] = self._generate_recommendations(
            analysis_text, results.get("risk_score", {}), results.get("clauses", {})
        )

        # Bid-specific analysis if applicable
        doc_type = results.get("doc_type", "")
        if any(kw in doc_type.lower() for kw in ["bid", "tender", "rfp", "rfq", "proposal", "quotation"]):
            print("  [+] Running bid-specific analysis...")
            results["bid_analysis"] = self._analyse_bid(analysis_text)

        return results

    # ── Individual Analysis Tasks ────────────────────────────────────────────

    def _classify_document(self, text: str, filename: str) -> Dict:
        prompt = f"""Analyse this legal/commercial document and return classification and executive summary.

Document filename: {filename}
Document text (first portion):
---
{text[:4000]}
---

Return JSON with EXACTLY these fields:
{{
  "doc_type": "type of document e.g. Service Agreement, NDA, Construction Contract, Bid/Tender, Purchase Order, etc.",
  "executive_summary": "3-4 sentence plain English summary of what this document is, who the parties are, and the core commercial arrangement",
  "contract_value": "monetary value if stated, else null",
  "contract_duration": "duration or term if stated, else null",
  "governing_law": "jurisdiction/governing law if stated, else null",
  "key_subject": "the main subject matter in one sentence"
}}"""
        return self.llm.complete_json(prompt, SYSTEM_LEGAL)

    def _extract_parties(self, text: str) -> Dict:
        prompt = f"""From this contract text, identify all parties and their key obligations.

Contract text:
---
{text[:5000]}
---

Return JSON with EXACTLY this structure:
{{
  "parties": [
    {{
      "name": "Party name",
      "role": "their role e.g. Client, Contractor, Supplier, Buyer, Seller, Service Provider",
      "description": "brief description of who they are",
      "key_obligations": ["obligation 1", "obligation 2", "obligation 3"]
    }}
  ],
  "relationship_summary": "one sentence describing the commercial relationship"
}}"""
        return self.llm.complete_json(prompt, SYSTEM_LEGAL)

    def _extract_clauses(self, text: str) -> Dict:
        prompt = f"""Extract and analyse the key clauses from this contract. Focus on commercially and legally significant clauses.

Contract text:
---
{text[:7000]}
---

Return JSON with EXACTLY this structure:
{{
  "payment_terms": {{
    "found": true/false,
    "summary": "description of payment terms",
    "details": "specific amounts, dates, conditions"
  }},
  "termination": {{
    "found": true/false,
    "summary": "how the contract can be terminated",
    "notice_period": "notice period if stated"
  }},
  "liability": {{
    "found": true/false,
    "summary": "liability provisions",
    "cap": "liability cap amount if stated",
    "exclusions": ["list of excluded liabilities"]
  }},
  "indemnity": {{
    "found": true/false,
    "summary": "indemnity provisions"
  }},
  "intellectual_property": {{
    "found": true/false,
    "summary": "IP ownership and rights"
  }},
  "confidentiality": {{
    "found": true/false,
    "summary": "confidentiality obligations"
  }},
  "dispute_resolution": {{
    "found": true/false,
    "summary": "how disputes are resolved",
    "method": "arbitration/litigation/mediation"
  }},
  "warranties": {{
    "found": true/false,
    "summary": "warranties given by each party"
  }},
  "force_majeure": {{
    "found": true/false,
    "summary": "force majeure provisions"
  }},
  "auto_renewal": {{
    "found": true/false,
    "summary": "any auto-renewal provisions"
  }},
  "missing_protections": ["list of important clauses that appear to be absent"]
}}"""
        return self.llm.complete_json(prompt, SYSTEM_LEGAL)

    def _score_risk(self, text: str, clauses: Dict) -> Dict:
        clauses_summary = json.dumps(clauses, indent=2)[:1500]
        prompt = f"""Conduct a risk assessment of this contract from the perspective of a Bids and Contracts Manager.

Contract text excerpt:
---
{text[:5000]}
---

Identified clauses summary:
{clauses_summary}

Return JSON with EXACTLY this structure:
{{
  "overall_score": <integer 0-100 where 0=no risk, 100=extreme risk>,
  "level": "<one of: Low, Medium, High, Critical>",
  "score_rationale": "2-3 sentences explaining the overall score",
  "red_flags": [
    {{
      "flag": "name of the risk",
      "severity": "<Low/Medium/High/Critical>",
      "description": "what the risk is",
      "clause_reference": "where in the document"
    }}
  ],
  "one_sided_clauses": ["list any clauses that appear heavily one-sided"],
  "missing_protections": ["list important missing protective clauses"],
  "financial_risk": "<Low/Medium/High> - assessment of financial exposure",
  "legal_risk": "<Low/Medium/High> - assessment of legal exposure",
  "operational_risk": "<Low/Medium/High> - assessment of operational/delivery risk"
}}"""
        return self.llm.complete_json(prompt, SYSTEM_LEGAL)

    def _extract_dates(self, text: str) -> Dict:
        prompt = f"""Extract all important dates, deadlines, and time-sensitive provisions from this contract.

Contract text:
---
{text[:6000]}
---

Return JSON with EXACTLY this structure:
{{
  "effective_date": "contract start/effective date or null",
  "expiry_date": "contract end/expiry date or null",
  "key_dates": [
    {{
      "description": "what this date is for",
      "date": "the date or timeframe",
      "is_deadline": true/false,
      "importance": "<Low/Medium/High>"
    }}
  ],
  "notice_periods": [
    {{
      "trigger": "what requires notice",
      "period": "notice period required"
    }}
  ],
  "time_sensitive_obligations": ["list of time-sensitive obligations"]
}}"""
        return self.llm.complete_json(prompt, SYSTEM_LEGAL)

    def _generate_recommendations(self, text: str, risk_score: Dict, clauses: Dict) -> Dict:
        risk_summary = json.dumps(risk_score, indent=2)[:1000]
        prompt = f"""Based on the contract analysis, provide actionable recommendations for the Contracts Manager.

Contract excerpt:
---
{text[:3000]}
---

Risk assessment summary:
{risk_summary}

Return JSON with EXACTLY this structure:
{{
  "immediate_actions": [
    {{
      "priority": "<1-5, 1=urgent>",
      "action": "specific action to take",
      "reason": "why this is needed"
    }}
  ],
  "negotiation_points": [
    {{
      "clause": "which clause to negotiate",
      "current_position": "what it says now",
      "recommended_change": "what to push for"
    }}
  ],
  "before_signing": ["checklist of things to confirm before signing"],
  "overall_recommendation": "<one of: Approve, Approve with Amendments, Negotiate, Reject>",
  "recommendation_rationale": "2-3 sentence explanation of the overall recommendation"
}}"""
        return self.llm.complete_json(prompt, SYSTEM_LEGAL)

    def _analyse_bid(self, text: str) -> Dict:
        prompt = f"""This appears to be a bid, tender, or procurement document. Conduct a bid-specific analysis.

Document text:
---
{text[:6000]}
---

Return JSON with EXACTLY this structure:
{{
  "bid_type": "type of bid (e.g. Fixed Price, Time & Materials, Cost Plus, etc.)",
  "scope_clarity": "<Clear/Partial/Vague> - how clearly the scope is defined",
  "scope_gaps": ["list any gaps or ambiguities in the scope of work"],
  "pricing_analysis": {{
    "total_value": "total bid value if stated",
    "pricing_model": "description of pricing structure",
    "payment_schedule": "when payments are due",
    "variations_provision": "how changes/variations are handled"
  }},
  "compliance_requirements": ["list of compliance/regulatory requirements mentioned"],
  "evaluation_criteria": ["list of evaluation criteria if stated"],
  "submission_requirements": ["list of what must be submitted"],
  "exclusions": ["list any stated exclusions or limitations"],
  "bid_bonds_guarantees": "any bonds or guarantees required",
  "bid_risks": [
    {{
      "risk": "description of bid-specific risk",
      "mitigation": "suggested mitigation"
    }}
  ]
}}"""
        return self.llm.complete_json(prompt, SYSTEM_LEGAL)
