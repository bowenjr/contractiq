"""
KnowledgeEngine — enriches LLM findings with company knowledge base rules.

CRITICAL RULE: Rules can only ESCALATE severity upward, never reduce below
the LLM's original assessment.

Severity order (lowest → highest): Info → Low → Medium → High → Critical
"""

import json
from typing import Dict, List, Optional, Any


_SEVERITY_ORDER = ["Info", "Low", "Medium", "High", "Critical"]


def _max_severity(current: str, candidate: str) -> str:
    """Return the higher of two severity values."""
    ci = _SEVERITY_ORDER.index(current) if current in _SEVERITY_ORDER else 1
    ni = _SEVERITY_ORDER.index(candidate) if candidate in _SEVERITY_ORDER else 1
    return _SEVERITY_ORDER[max(ci, ni)]


class KnowledgeEngine:

    def __init__(self, db=None):
        self.db = db

    # ── Public API ────────────────────────────────────────────────────────────

    def enrich_findings(
        self,
        findings_list: List[Dict],
        document_context: Dict,
    ) -> List[Dict]:
        """
        Enrich a list of findings with knowledge base rules.
        document_context keys: counterparty, jurisdiction, product_families (list),
                                business_role, delivery_model.
        """
        if not findings_list or not self.db:
            return findings_list or []

        # Pre-load tables once for efficiency
        ctx = document_context or {}
        cache = self._build_cache(ctx)

        enriched = []
        for finding in findings_list:
            try:
                enriched.append(self._enrich_single(finding, ctx, cache))
            except Exception as e:
                print(f"  [Knowledge] Enrichment warning: {e}")
                enriched.append(finding)
        return enriched

    def get_knowledge_summary(self) -> Dict[str, int]:
        """Return active-row counts for all knowledge tables."""
        if not self.db:
            return {}
        counts = {}
        try:
            counts["company_positions"] = len(
                self.db.get_all_company_positions()
            )
        except Exception:
            counts["company_positions"] = 0
        try:
            counts["escalation_rules"] = len(
                self.db.get_all_escalation_rules()
            )
        except Exception:
            counts["escalation_rules"] = 0
        try:
            counts["product_profiles"] = len(
                self.db.get_all_product_risk_profiles()
            )
        except Exception:
            counts["product_profiles"] = 0
        try:
            counts["commercial_terms"] = len(
                self.db.get_all_commercial_terms()
            )
        except Exception:
            counts["commercial_terms"] = 0
        try:
            counts["supplier_profiles"] = len(
                self.db.get_all_supplier_intelligence()
            )
        except Exception:
            counts["supplier_profiles"] = 0
        try:
            counts["clause_playbooks"] = len(
                self.db.get_all_clause_playbooks()
            )
        except Exception:
            counts["clause_playbooks"] = 0
        return counts

    def get_supplier_context(self, counterparty: str) -> Optional[List[Dict]]:
        """Return all intelligence records for a counterparty."""
        if not self.db or not counterparty:
            return None
        try:
            return self.db.get_intel_for_counterparty(counterparty) or None
        except Exception:
            return None

    def get_jurisdiction_context(self, jurisdiction: str) -> Optional[List[Dict]]:
        """Return all rules for a jurisdiction (defaults to Ontario)."""
        if not self.db:
            return None
        jur = jurisdiction or "Ontario"
        try:
            return self.db.get_rules_for_jurisdiction(jur) or None
        except Exception:
            return None

    # ── Cache builder ─────────────────────────────────────────────────────────

    def _build_cache(self, ctx: Dict) -> Dict:
        """Pre-load all reference tables to avoid repeated DB calls per finding."""
        cache: Dict[str, Any] = {}
        try:
            cache["escalation_rules"] = self.db.get_all_escalation_rules(active_only=True)
        except Exception:
            cache["escalation_rules"] = []
        try:
            cache["positions"] = self.db.get_all_company_positions(active_only=True)
        except Exception:
            cache["positions"] = []
        try:
            cache["playbooks"] = self.db.get_all_clause_playbooks(active_only=True)
        except Exception:
            cache["playbooks"] = []
        try:
            cache["commercial_terms"] = self.db.get_all_commercial_terms(active_only=True)
        except Exception:
            cache["commercial_terms"] = []
        try:
            cache["routing_rules"] = self.db.get_all_routing_rules(active_only=True)
        except Exception:
            cache["routing_rules"] = []

        # Product term maps — keyed by product family
        product_families = ctx.get("product_families", [])
        cache["product_term_maps"] = {}
        for pf in product_families:
            try:
                cache["product_term_maps"][pf] = (
                    self.db.get_term_risks_for_product(pf)
                )
            except Exception:
                cache["product_term_maps"][pf] = []

        return cache

    # ── Single-finding enrichment pipeline ───────────────────────────────────

    def _enrich_single(self, finding: Dict, ctx: Dict, cache: Dict) -> Dict:
        result = dict(finding)
        # Preserve original LLM severity for Excel escalation tracking
        result["original_severity"] = result.get("severity", "Low")

        finding_text = (
            str(finding.get("finding", "")) + " " +
            str(finding.get("detail", "")) + " " +
            str(finding.get("source_excerpt", ""))
        ).lower()

        # Step 1: Escalation rules (highest priority)
        self._apply_escalation_rules(result, finding_text, cache)

        # Step 2: Product + term risk map
        self._apply_product_term_map(result, finding_text, ctx, cache)

        # Step 3: Company position — flag matching positions
        self._apply_position_check(result, finding_text, cache)

        # Step 4: Clause playbook match
        self._apply_playbook(result, finding_text, cache)

        # Step 5: Commercial term library
        self._apply_commercial_term(result, finding_text, cache)

        # Step 6: Routing
        self._apply_routing(result, finding_text, cache)

        # Step 7: Enrichment summary
        result["knowledge_enriched"] = True
        result["enrichment_sources"] = self._build_sources_summary(result)

        return result

    # ── Step 1: Escalation rules ──────────────────────────────────────────────

    def _apply_escalation_rules(
        self, result: Dict, finding_text: str, cache: Dict
    ) -> None:
        rules = cache.get("escalation_rules", [])
        triggered = []
        for rule in rules:
            kw_json = rule.get("trigger_keywords") or "[]"
            try:
                keywords = json.loads(kw_json) if isinstance(kw_json, str) else kw_json
            except Exception:
                keywords = []
            if any(str(kw).lower() in finding_text for kw in keywords if kw):
                triggered.append(rule)

        if not triggered:
            return

        # Sort by priority (lower number = higher priority)
        triggered.sort(key=lambda r: r.get("priority", 5))
        top = triggered[0]

        result["escalation_triggered"] = True
        result["escalation_rule_name"] = top.get("rule_name", "")
        result["escalation_note"] = top.get("escalation_note", "")

        # Escalate severity to at least minimum_severity
        min_sev = top.get("minimum_severity", "High")
        result["severity"] = _max_severity(result.get("severity", "Low"), min_sev)

        # Routing flags from rule
        if top.get("requires_legal"):
            result["requires_legal_review"] = True
        if top.get("requires_exec"):
            result["requires_management_review"] = True

        # Build escalation targets list
        targets = []
        if top.get("requires_legal"):
            targets.append("Legal")
        if top.get("requires_exec"):
            targets.append("Management")
        if targets:
            result["escalate_to"] = ", ".join(targets)

    # ── Step 2: Product term risk map ─────────────────────────────────────────

    def _apply_product_term_map(
        self, result: Dict, finding_text: str, ctx: Dict, cache: Dict
    ) -> None:
        product_term_maps = cache.get("product_term_maps", {})
        for pf, mappings in product_term_maps.items():
            for mapping in mappings:
                term = str(mapping.get("term_name", "")).lower()
                if term and term in finding_text:
                    concern = mapping.get("concern_level", "Medium")
                    result["severity"] = _max_severity(
                        result.get("severity", "Low"), concern
                    )
                    result["product_risk_modifier"] = concern
                    result["product_risk_reason"] = mapping.get(
                        "specific_concern", ""
                    )
                    result["matched_product_family"] = pf
                    result["product_recommended_action"] = mapping.get(
                        "recommended_action", ""
                    )
                    break  # one match per product family is enough

    # ── Step 3: Company position ──────────────────────────────────────────────

    def _apply_position_check(
        self, result: Dict, finding_text: str, cache: Dict
    ) -> None:
        positions = cache.get("positions", [])
        for pos in positions:
            clause_type = str(pos.get("clause_type", "")).lower()
            if not clause_type:
                continue
            # Check if the finding is about this clause type
            if clause_type in finding_text or any(
                word in finding_text
                for word in clause_type.split()
                if len(word) > 4
            ):
                result["matched_company_position"] = True
                result["position_clause_type"] = pos.get("clause_type", "")
                result["position_standard"] = pos.get("position_summary", "")
                result["position_acceptable"] = pos.get("acceptable_deviation", "")
                result["position_hard_limit"] = pos.get("hard_limit", "")
                # Flag deviation if hard limit language appears in finding
                hard_limit = str(pos.get("hard_limit", "")).lower()
                if hard_limit and any(
                    word in finding_text
                    for word in hard_limit.split()
                    if len(word) > 5
                ):
                    result["deviation_from_standard"] = True
                break  # match first relevant position

    # ── Step 4: Clause playbook ───────────────────────────────────────────────

    def _apply_playbook(
        self, result: Dict, finding_text: str, cache: Dict
    ) -> None:
        playbooks = cache.get("playbooks", [])
        for pb in playbooks:
            pattern = str(pb.get("trigger_pattern", "")).lower()
            clause_type = str(pb.get("clause_type", "")).lower()
            if (pattern and pattern in finding_text) or (
                clause_type and clause_type in finding_text
            ):
                result["matched_playbook"] = pb.get("playbook_name", "")
                result["playbook_recommended"] = pb.get("recommended_response", "")
                result["playbook_fallback"] = pb.get("fallback_position", "")
                result["playbook_situation"] = pb.get("situation_description", "")
                if pb.get("escalate"):
                    result["requires_management_review"] = True
                break

    # ── Step 5: Commercial term library ──────────────────────────────────────

    def _apply_commercial_term(
        self, result: Dict, finding_text: str, cache: Dict
    ) -> None:
        terms = cache.get("commercial_terms", [])
        for term in terms:
            name = str(term.get("term_name", "")).lower()
            if not name:
                continue
            # Also check individual significant words in term name
            if name in finding_text or any(
                w in finding_text for w in name.split() if len(w) > 5
            ):
                result["matched_commercial_term"] = term.get("term_name", "")
                result["term_our_standard"] = term.get("our_standard", "")
                result["term_minimum_acceptable"] = term.get(
                    "minimum_acceptable", ""
                )
                result["term_never_accept"] = term.get("never_accept", "")
                # Check if finding text suggests 'never accept' territory
                never = str(term.get("never_accept", "")).lower()
                if never and any(
                    w in finding_text for w in never.split() if len(w) > 5
                ):
                    result["severity"] = _max_severity(
                        result.get("severity", "Low"), "High"
                    )
                break

    # ── Step 6: Routing ───────────────────────────────────────────────────────

    def _apply_routing(
        self, result: Dict, finding_text: str, cache: Dict
    ) -> None:
        routing_rules = cache.get("routing_rules", [])
        assigned = False

        for rule in routing_rules:
            kw_json = rule.get("trigger_keywords") or "[]"
            try:
                keywords = (
                    json.loads(kw_json) if isinstance(kw_json, str) else kw_json
                )
            except Exception:
                keywords = []

            sev_match = True
            trigger_sev = rule.get("trigger_severity")
            if trigger_sev:
                current_sev = result.get("severity", "Low")
                ci = _SEVERITY_ORDER.index(current_sev) if current_sev in _SEVERITY_ORDER else 1
                ti = _SEVERITY_ORDER.index(trigger_sev) if trigger_sev in _SEVERITY_ORDER else 1
                sev_match = ci >= ti

            kw_match = any(str(kw).lower() in finding_text for kw in keywords if kw)
            pillar_match = True
            trigger_pillar = rule.get("trigger_pillar")
            if trigger_pillar:
                pillar_match = trigger_pillar == result.get("pillar_id", "")

            if sev_match and (kw_match or not keywords) and pillar_match:
                result["route_to"] = rule.get("route_to", "Contracts")
                if rule.get("routing_note"):
                    result["routing_note"] = rule.get("routing_note")
                assigned = True
                break

        # Default routing based on flags
        if not assigned:
            if result.get("requires_legal_review") or result.get(
                "requires_management_review"
            ):
                result["route_to"] = "Legal" if result.get(
                    "requires_legal_review"
                ) else "Management"
            else:
                result["route_to"] = "Contracts"

    # ── Enrichment sources summary ────────────────────────────────────────────

    def _build_sources_summary(self, result: Dict) -> List[str]:
        sources = []
        if result.get("escalation_triggered"):
            rule = result.get("escalation_rule_name", "")
            sources.append(f"Escalation rule: {rule}" if rule else "Escalation rule")
        if result.get("matched_product_family"):
            sources.append(f"Product risk: {result['matched_product_family']}")
        if result.get("matched_company_position"):
            ct = result.get("position_clause_type", "")
            sources.append(f"Company position: {ct}" if ct else "Company position")
        if result.get("matched_playbook"):
            sources.append(f"Playbook: {result['matched_playbook']}")
        if result.get("matched_commercial_term"):
            sources.append(f"Term: {result['matched_commercial_term']}")
        return sources
