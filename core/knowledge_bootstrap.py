"""
Knowledge bootstrap — seeds reference tables on first run.
Only inserts rows when a table is empty; never overwrites user changes.

NOTE: product_risk_profiles and supplier_intelligence seed data are
TEMPLATES only. Update them to reflect your actual product portfolio
and counterparty experience before relying on knowledge-enriched output.
"""

from datetime import date
import json

TODAY = date.today().isoformat()


def bootstrap_knowledge(db) -> dict:
    """
    Seed all knowledge tables that are currently empty.
    Returns a dict of {table: rows_inserted}.
    """
    inserted = {}
    inserted.update(_seed_company_positions(db))
    inserted.update(_seed_insurance_positions(db))
    inserted.update(_seed_escalation_rules(db))
    inserted.update(_seed_commercial_terms(db))
    inserted.update(_seed_product_risk_profiles(db))
    inserted.update(_seed_supplier_intelligence(db))
    inserted.update(_seed_jurisdiction_rules(db))
    inserted.update(_seed_project_type_profiles(db))
    return inserted


# ── Company Positions ─────────────────────────────────────────────────────────

def _seed_company_positions(db) -> dict:
    if db.get_all_company_positions(active_only=False):
        return {}
    rows = [
        {
            "clause_type": "Limitation of Liability",
            "pillar": "liability",
            "position_summary": "Cap at 12 months fees paid under the contract",
            "acceptable_deviation": "Cap at 24 months fees if customer insists, for fixed-price deals only",
            "hard_limit": "Uncapped liability is never acceptable",
            "priority": 1,
            "active": 1,
            "created_date": TODAY,
            "notes": "Mutual cap preferred; if asymmetric, negotiate mutual application.",
        },
        {
            "clause_type": "Consequential Damages",
            "pillar": "liability",
            "position_summary": "Full mutual exclusion of indirect, consequential, incidental, special, and punitive damages",
            "acceptable_deviation": "Carve-out for breach of confidentiality or IP indemnity if mutual",
            "hard_limit": "One-sided carve-outs that expose us but not customer are not acceptable",
            "priority": 1,
            "active": 1,
            "created_date": TODAY,
            "notes": None,
        },
        {
            "clause_type": "Indemnification",
            "pillar": "liability",
            "position_summary": "Mutual indemnification; each party indemnifies the other for its own IP, negligence, and wilful misconduct",
            "acceptable_deviation": "Broader customer IP indemnity only if we have step-in rights to control defence",
            "hard_limit": "Broad indemnification without defence control is not acceptable",
            "priority": 2,
            "active": 1,
            "created_date": TODAY,
            "notes": None,
        },
        {
            "clause_type": "Governing Law",
            "pillar": "legal",
            "position_summary": "Ontario law and courts",
            "acceptable_deviation": "Province of customer's HQ if Canadian; Federal Courts for federal crown",
            "hard_limit": "US or foreign law without Canadian counsel sign-off is not acceptable",
            "priority": 3,
            "active": 1,
            "created_date": TODAY,
            "notes": None,
        },
        {
            "clause_type": "Payment Terms",
            "pillar": "commercial",
            "position_summary": "Net 30 from invoice date",
            "acceptable_deviation": "Net 45 for large enterprise customers with strong credit",
            "hard_limit": "Net 60+ or milestone-only payment without invoice trigger is not acceptable",
            "priority": 2,
            "active": 1,
            "created_date": TODAY,
            "notes": None,
        },
        {
            "clause_type": "Intellectual Property Ownership",
            "pillar": "ip",
            "position_summary": "We retain ownership of all pre-existing IP and our general methodologies; customer owns deliverable-specific custom work product",
            "acceptable_deviation": "Joint ownership of novel IP if customer funds development",
            "hard_limit": "Assignment of our background IP or platform IP is never acceptable",
            "priority": 1,
            "active": 1,
            "created_date": TODAY,
            "notes": "Distinguish: Background IP (ours), Foreground IP (customer-funded → customer), Residual Knowledge (ours).",
        },
        {
            "clause_type": "Termination for Convenience",
            "pillar": "commercial",
            "position_summary": "Mutual right; 30 days written notice; payment for all work completed plus committed costs",
            "acceptable_deviation": "60-day notice if project is multi-year; wind-down fee for fixed-price",
            "hard_limit": "Termination for convenience without payment for completed work is not acceptable",
            "priority": 2,
            "active": 1,
            "created_date": TODAY,
            "notes": None,
        },
        {
            "clause_type": "Service Levels / SLA",
            "pillar": "delivery",
            "position_summary": "SLA credits are sole remedy; credits capped at fees for the affected month",
            "acceptable_deviation": "Credits up to one month's fees if customer requires higher cap",
            "hard_limit": "SLA breach as grounds for termination without cure period is not acceptable",
            "priority": 2,
            "active": 1,
            "created_date": TODAY,
            "notes": None,
        },
    ]
    for r in rows:
        db.create_company_position(r)
    return {"company_positions": len(rows)}


# ── Insurance Positions ───────────────────────────────────────────────────────

def _seed_insurance_positions(db) -> dict:
    if db.get_all_insurance_positions(active_only=False):
        return {}
    rows = [
        {
            "insurance_type": "Commercial General Liability",
            "minimum_coverage_text": "$2,000,000 per occurrence / $5,000,000 aggregate",
            "minimum_coverage_amount": 2_000_000,
            "preferred_coverage_amount": 5_000_000,
            "required": 1,
            "notes": "Must name customer as additional insured.",
            "active": 1,
        },
        {
            "insurance_type": "Professional Liability / E&O",
            "minimum_coverage_text": "$2,000,000 per claim",
            "minimum_coverage_amount": 2_000_000,
            "preferred_coverage_amount": 5_000_000,
            "required": 1,
            "notes": "Run-off coverage required for 2 years post-project.",
            "active": 1,
        },
        {
            "insurance_type": "Cyber Liability",
            "minimum_coverage_text": "$2,000,000 per occurrence",
            "minimum_coverage_amount": 2_000_000,
            "preferred_coverage_amount": 5_000_000,
            "required": 1,
            "notes": "Required when processing personal information or connecting to customer systems.",
            "active": 1,
        },
        {
            "insurance_type": "Workers Compensation",
            "minimum_coverage_text": "Statutory limits per applicable jurisdiction",
            "minimum_coverage_amount": None,
            "preferred_coverage_amount": None,
            "required": 1,
            "notes": "Required in all Canadian provinces where staff are deployed.",
            "active": 1,
        },
        {
            "insurance_type": "Crime / Fidelity",
            "minimum_coverage_text": "$1,000,000",
            "minimum_coverage_amount": 1_000_000,
            "preferred_coverage_amount": 2_000_000,
            "required": 0,
            "notes": "Required when staff have access to customer financial systems or cash handling.",
            "active": 1,
        },
    ]
    for r in rows:
        db.create_insurance_position(r)
    return {"insurance_positions": len(rows)}


# ── Escalation Rules ──────────────────────────────────────────────────────────

def _seed_escalation_rules(db) -> dict:
    if db.get_all_escalation_rules(active_only=False):
        return {}
    rows = [
        # Liability
        {
            "rule_name": "Uncapped Liability",
            "pillar": "liability",
            "trigger_keywords": json.dumps(["unlimited liability", "uncapped", "no cap on liability", "without limit"]),
            "trigger_condition": "Contract contains uncapped or unlimited liability language",
            "minimum_severity": "Critical",
            "requires_legal": 1, "requires_exec": 1,
            "escalation_note": "Uncapped liability detected — Legal and Executive sign-off required.",
            "priority": 1, "active": 1,
        },
        {
            "rule_name": "Asymmetric Consequential Damages Waiver",
            "pillar": "liability",
            "trigger_keywords": json.dumps(["consequential damages", "indirect damages", "lost profits"]),
            "trigger_condition": "Consequential damages waiver protects customer but not supplier",
            "minimum_severity": "High",
            "requires_legal": 1, "requires_exec": 0,
            "escalation_note": "One-sided consequential damages exclusion — Legal review required.",
            "priority": 2, "active": 1,
        },
        # IP
        {
            "rule_name": "Background IP Assignment",
            "pillar": "ip",
            "trigger_keywords": json.dumps(["assign all intellectual property", "all IP shall vest", "work made for hire", "all rights title and interest"]),
            "trigger_condition": "Customer claims ownership of all IP including supplier background/pre-existing IP",
            "minimum_severity": "Critical",
            "requires_legal": 1, "requires_exec": 1,
            "escalation_note": "Potential background IP assignment — Legal and Executive sign-off required.",
            "priority": 1, "active": 1,
        },
        {
            "rule_name": "Open Source License Restriction",
            "pillar": "ip",
            "trigger_keywords": json.dumps(["open source", "GPL", "copyleft", "AGPL", "no open source"]),
            "trigger_condition": "Contract restricts or has conditions around open source software use",
            "minimum_severity": "High",
            "requires_legal": 1, "requires_exec": 0,
            "escalation_note": "Open source restrictions detected — Legal review required before committing.",
            "priority": 2, "active": 1,
        },
        # Privacy / Data
        {
            "rule_name": "Personal Information Processing",
            "pillar": "privacy",
            "trigger_keywords": json.dumps(["personal information", "personal data", "PIPEDA", "PHIPA", "GDPR", "privacy", "data subject"]),
            "trigger_condition": "Contract involves processing personal information",
            "minimum_severity": "High",
            "requires_legal": 1, "requires_exec": 0,
            "escalation_note": "Personal information processing — Privacy Legal review required.",
            "priority": 2, "active": 1,
        },
        {
            "rule_name": "Health Information",
            "pillar": "privacy",
            "trigger_keywords": json.dumps(["health information", "PHIPA", "PHI", "medical records", "patient data"]),
            "trigger_condition": "Contract involves health information subject to PHIPA or similar",
            "minimum_severity": "Critical",
            "requires_legal": 1, "requires_exec": 1,
            "escalation_note": "Health information (PHIPA) detected — Specialized Privacy Legal review and Executive sign-off required.",
            "priority": 1, "active": 1,
        },
        # Security
        {
            "rule_name": "Security Breach Notification Obligation",
            "pillar": "security",
            "trigger_keywords": json.dumps(["breach notification", "notify within", "security incident", "data breach"]),
            "trigger_condition": "Short breach notification window imposed on supplier",
            "minimum_severity": "High",
            "requires_legal": 1, "requires_exec": 0,
            "escalation_note": "Security breach notification obligation — confirm timeline is operationally achievable.",
            "priority": 2, "active": 1,
        },
        # Commercial
        {
            "rule_name": "Payment Terms Exceeding 60 Days",
            "pillar": "commercial",
            "trigger_keywords": json.dumps(["net 60", "net 90", "60 days", "90 days", "quarterly payment"]),
            "trigger_condition": "Payment terms exceed 60 days from invoice",
            "minimum_severity": "High",
            "requires_legal": 0, "requires_exec": 1,
            "escalation_note": "Extended payment terms — Finance and Executive review required.",
            "priority": 3, "active": 1,
        },
        {
            "rule_name": "Audit Rights — Broad Access",
            "pillar": "commercial",
            "trigger_keywords": json.dumps(["audit rights", "right to audit", "inspect books", "access to records"]),
            "trigger_condition": "Customer audit rights extend to supplier's internal systems or cost records",
            "minimum_severity": "Medium",
            "requires_legal": 1, "requires_exec": 0,
            "escalation_note": "Broad audit rights detected — scope and frequency must be negotiated.",
            "priority": 4, "active": 1,
        },
        # Termination
        {
            "rule_name": "Termination for Convenience Without Compensation",
            "pillar": "commercial",
            "trigger_keywords": json.dumps(["terminate for convenience", "terminate without cause", "terminate at will"]),
            "trigger_condition": "Termination for convenience with no payment for work in progress",
            "minimum_severity": "High",
            "requires_legal": 0, "requires_exec": 1,
            "escalation_note": "T4C without compensation — payment-for-completion clause must be added.",
            "priority": 2, "active": 1,
        },
        # Subcontracting
        {
            "rule_name": "Subcontracting Restrictions",
            "pillar": "delivery",
            "trigger_keywords": json.dumps(["no subcontracting", "prior written consent", "approved subcontractors", "subcontract prohibited"]),
            "trigger_condition": "Contract restricts or prohibits use of subcontractors without approval",
            "minimum_severity": "Medium",
            "requires_legal": 0, "requires_exec": 0,
            "escalation_note": "Subcontracting restrictions — verify against planned delivery model.",
            "priority": 4, "active": 1,
        },
        # Governing Law
        {
            "rule_name": "Foreign Governing Law",
            "pillar": "legal",
            "trigger_keywords": json.dumps(["governed by the laws of", "New York law", "Delaware law", "English law", "laws of the State"]),
            "trigger_condition": "Governing law is a foreign jurisdiction (non-Canadian)",
            "minimum_severity": "High",
            "requires_legal": 1, "requires_exec": 0,
            "escalation_note": "Foreign governing law — Canadian Legal counsel review required.",
            "priority": 2, "active": 1,
        },
        # Insurance
        {
            "rule_name": "Insurance Above Standard Thresholds",
            "pillar": "insurance",
            "trigger_keywords": json.dumps(["$5,000,000", "$10,000,000", "five million", "ten million"]),
            "trigger_condition": "Customer demands insurance coverage exceeding standard company thresholds",
            "minimum_severity": "High",
            "requires_legal": 0, "requires_exec": 1,
            "escalation_note": "Non-standard insurance requirement — Finance and Executive approval required.",
            "priority": 2, "active": 1,
        },
        # Mandatory compliance
        {
            "rule_name": "Security Clearance Required",
            "pillar": "security",
            "trigger_keywords": json.dumps(["security clearance", "reliability status", "secret clearance", "top secret", "government of canada security"]),
            "trigger_condition": "Contract requires staff to hold government security clearances",
            "minimum_severity": "Critical",
            "requires_legal": 1, "requires_exec": 1,
            "escalation_note": "Security clearance required — confirm existing clearances or timeline to obtain.",
            "priority": 1, "active": 1,
        },
    ]
    for r in rows:
        db.create_escalation_rule(r)
    return {"escalation_rules": len(rows)}


# ── Commercial Term Library ───────────────────────────────────────────────────

def _seed_commercial_terms(db) -> dict:
    if db.get_all_commercial_terms(active_only=False):
        return {}
    rows = [
        {
            "term_name": "Payment Terms",
            "term_category": "Payment",
            "pillar": "commercial",
            "our_standard": "Net 30 from invoice date",
            "minimum_acceptable": "Net 45",
            "never_accept": "Net 60 or greater; milestone-triggered with no invoice",
            "notes": None, "active": 1,
        },
        {
            "term_name": "Limitation of Liability Cap",
            "term_category": "Liability",
            "pillar": "liability",
            "our_standard": "12 months fees paid in the 12 months preceding the claim",
            "minimum_acceptable": "24 months fees for fixed-price engagements",
            "never_accept": "Uncapped; greater than total contract value",
            "notes": "Mutual cap strongly preferred.", "active": 1,
        },
        {
            "term_name": "Consequential Damages Exclusion",
            "term_category": "Liability",
            "pillar": "liability",
            "our_standard": "Full mutual exclusion of all indirect, consequential, special, incidental damages",
            "minimum_acceptable": "Mutual carve-out for confidentiality breach",
            "never_accept": "One-sided carve-out excluding customer but not supplier",
            "notes": None, "active": 1,
        },
        {
            "term_name": "Warranty Period",
            "term_category": "Delivery",
            "pillar": "delivery",
            "our_standard": "90-day defect warranty on deliverables",
            "minimum_acceptable": "30-day warranty minimum",
            "never_accept": "12+ month warranty without defined severity and response SLAs",
            "notes": None, "active": 1,
        },
        {
            "term_name": "Change Order Process",
            "term_category": "Scope",
            "pillar": "delivery",
            "our_standard": "Written change order required before any out-of-scope work commences",
            "minimum_acceptable": "Email approval with 5-business-day response window",
            "never_accept": "Verbal-only approval; retroactive scope claims",
            "notes": None, "active": 1,
        },
        {
            "term_name": "Termination for Convenience Notice",
            "term_category": "Termination",
            "pillar": "commercial",
            "our_standard": "30 days written notice; payment for all completed work and committed third-party costs",
            "minimum_acceptable": "14 days notice with full payment for work in progress",
            "never_accept": "Immediate termination; no payment for work in progress",
            "notes": None, "active": 1,
        },
        {
            "term_name": "Intellectual Property — Work Product Ownership",
            "term_category": "IP",
            "pillar": "ip",
            "our_standard": "Customer owns custom deliverables; we retain background IP and methodology",
            "minimum_acceptable": "License to customer for deliverables; we retain all IP",
            "never_accept": "Assignment of background IP; work-made-for-hire covering pre-existing tools",
            "notes": None, "active": 1,
        },
        {
            "term_name": "Confidentiality Duration",
            "term_category": "Confidentiality",
            "pillar": "legal",
            "our_standard": "5 years post-disclosure or project end, whichever is later",
            "minimum_acceptable": "3 years",
            "never_accept": "Perpetual with no carve-outs for publicly available information",
            "notes": None, "active": 1,
        },
        {
            "term_name": "Non-Solicitation of Staff",
            "term_category": "HR",
            "pillar": "legal",
            "our_standard": "12-month mutual non-solicitation (not non-hire) post-project",
            "minimum_acceptable": "18-month non-solicitation if mutual",
            "never_accept": "Non-hire clause (prevents staff from seeking employment independently); one-sided restricting only us",
            "notes": None, "active": 1,
        },
        {
            "term_name": "Dispute Resolution",
            "term_category": "Legal",
            "pillar": "legal",
            "our_standard": "Negotiation → Mediation → Binding Arbitration (Ontario)",
            "minimum_acceptable": "Ontario court litigation",
            "never_accept": "Foreign arbitration; mandatory litigation only in customer's jurisdiction",
            "notes": None, "active": 1,
        },
        {
            "term_name": "SLA Credits",
            "term_category": "Service Levels",
            "pillar": "delivery",
            "our_standard": "Credits as sole remedy; capped at fees for affected period",
            "minimum_acceptable": "Credits capped at one month's fees",
            "never_accept": "SLA breach as independent right to terminate; credit uncapped",
            "notes": None, "active": 1,
        },
        {
            "term_name": "Audit Rights",
            "term_category": "Governance",
            "pillar": "commercial",
            "our_standard": "Once per year with 30 days notice; limited to compliance with contract terms",
            "minimum_acceptable": "Twice per year with 15 days notice",
            "never_accept": "Unlimited audit frequency; access to unrelated cost records or staff",
            "notes": None, "active": 1,
        },
        {
            "term_name": "Force Majeure",
            "term_category": "Legal",
            "pillar": "legal",
            "our_standard": "Mutual; includes pandemic, government action, supply chain disruption; suspension with notice",
            "minimum_acceptable": "Mutual with defined list of FM events",
            "never_accept": "One-sided FM protecting only customer; cybersecurity incidents excluded",
            "notes": None, "active": 1,
        },
        {
            "term_name": "Subcontracting Rights",
            "term_category": "Delivery",
            "pillar": "delivery",
            "our_standard": "Right to subcontract without prior approval; we remain responsible",
            "minimum_acceptable": "Prior approval required (not to be unreasonably withheld)",
            "never_accept": "Blanket prohibition on subcontracting",
            "notes": None, "active": 1,
        },
        {
            "term_name": "Governing Law",
            "term_category": "Legal",
            "pillar": "legal",
            "our_standard": "Province of Ontario, Canada",
            "minimum_acceptable": "Any Canadian province; Federal Courts for crown contracts",
            "never_accept": "US or other foreign jurisdiction without Canadian counsel sign-off",
            "notes": None, "active": 1,
        },
    ]
    for r in rows:
        db.create_commercial_term(r)
    return {"commercial_term_library": len(rows)}


# ── Product Risk Profiles (TEMPLATE DATA — update before use) ─────────────────

def _seed_product_risk_profiles(db) -> dict:
    if db.get_all_product_risk_profiles(active_only=False):
        return {}
    rows = [
        {
            "product_family": "AI/ML Solutions",
            "risk_category": "IP",
            "typical_concern": "Training data ownership and model IP ownership are frequently disputed",
            "recommended_clause_language": "Training data remains customer's; model architecture and weights remain supplier's; output data is customer's",
            "pillar": "ip",
            "notes": "TEMPLATE — update with your specific AI offering details.",
            "active": 1,
        },
        {
            "product_family": "AI/ML Solutions",
            "risk_category": "Liability",
            "typical_concern": "Model accuracy and hallucination risk; customer may seek liability for AI-generated errors",
            "recommended_clause_language": "Outputs are advisory only; customer is responsible for validating AI outputs before acting on them",
            "pillar": "liability",
            "notes": "TEMPLATE — update with your specific AI offering details.",
            "active": 1,
        },
        {
            "product_family": "Managed Services",
            "risk_category": "Liability",
            "typical_concern": "Continuous operational responsibility creates ongoing SLA exposure",
            "recommended_clause_language": "SLA credits are sole remedy; credits capped at monthly fees; excused downtime for maintenance windows",
            "pillar": "delivery",
            "notes": "TEMPLATE — update with your managed services portfolio details.",
            "active": 1,
        },
        {
            "product_family": "Custom Software Development",
            "risk_category": "IP",
            "typical_concern": "Customer typically expects to own all custom-developed code",
            "recommended_clause_language": "Distinguish background IP (retained by supplier) from foreground IP (assigned to customer); license back rights required for supplier's reusable components",
            "pillar": "ip",
            "notes": "TEMPLATE — update with your development practice details.",
            "active": 1,
        },
        {
            "product_family": "Data Analytics / BI",
            "risk_category": "Privacy",
            "typical_concern": "Access to customer data for analytics creates privacy and data residency obligations",
            "recommended_clause_language": "Data processing agreement required; data used solely for contracted purpose; no retention post-project",
            "pillar": "privacy",
            "notes": "TEMPLATE — update with your analytics service details.",
            "active": 1,
        },
        {
            "product_family": "Cloud Migration",
            "risk_category": "Delivery",
            "typical_concern": "Migration scope creep and cutover risk are most common dispute areas",
            "recommended_clause_language": "Phased acceptance milestones; rollback rights during cutover window; go-live criteria defined in SOW",
            "pillar": "delivery",
            "notes": "TEMPLATE — update with your cloud practice details.",
            "active": 1,
        },
    ]
    for r in rows:
        db.create_product_risk_profile(r)
    return {"product_risk_profiles": len(rows)}


# ── Supplier Intelligence (TEMPLATE DATA — update before use) ─────────────────

def _seed_supplier_intelligence(db) -> dict:
    if db.get_all_supplier_intelligence(active_only=False):
        return {}
    rows = [
        {
            "counterparty": "Example Enterprise Customer",
            "intel_type": "General Intel",
            "clause_type": None,
            "their_standard_position": None,
            "flexibility_observed": None,
            "general_intel": "TEMPLATE — replace with your actual counterparty intelligence. Record what you know about this customer's contracting style, procurement team approach, and typical sticking points.",
            "source": "Template",
            "date_recorded": TODAY,
            "active": 1,
            "notes": "Delete this row and add real counterparty entries.",
        },
        {
            "counterparty": "Example Government Client",
            "intel_type": "Commercial Terms",
            "clause_type": "Limitation of Liability",
            "their_standard_position": "TEMPLATE — record the liability cap they typically offer (e.g., 2x contract value)",
            "flexibility_observed": "TEMPLATE — record where they have shown flexibility in past negotiations",
            "general_intel": None,
            "source": "Template",
            "date_recorded": TODAY,
            "active": 1,
            "notes": "Delete this row and add real counterparty entries.",
        },
        {
            "counterparty": "Example Subcontractor",
            "intel_type": "Commercial Terms",
            "clause_type": "Payment Terms",
            "their_standard_position": "TEMPLATE — terms they offer to us as supplier (e.g., Net 45 standard)",
            "flexibility_observed": "TEMPLATE — record flexibility observations",
            "general_intel": "TEMPLATE — general notes about working with this subcontractor",
            "source": "Template",
            "date_recorded": TODAY,
            "active": 1,
            "notes": "Delete this row and add real counterparty entries.",
        },
    ]
    for r in rows:
        db.create_supplier_intel(r)
    return {"supplier_intelligence": len(rows)}


# ── Jurisdiction Rules ────────────────────────────────────────────────────────

def _seed_jurisdiction_rules(db) -> dict:
    if db.get_all_jurisdiction_rules(active_only=False):
        return {}
    rows = [
        {
            "jurisdiction": "Ontario",
            "rule_category": "Privacy",
            "rule_name": "PIPEDA / Bill C-27 Application",
            "rule_description": "Federal PIPEDA applies to commercial activity involving personal information; Bill C-27 (CPPA) will eventually replace PIPEDA with stronger requirements",
            "impact_on_contract": "Data processing agreement, purpose limitation, and breach notification clauses required",
            "pillar": "privacy",
            "requires_legal": 1, "active": 1,
            "notes": None,
        },
        {
            "jurisdiction": "Ontario",
            "rule_category": "Privacy",
            "rule_name": "PHIPA — Health Information",
            "rule_description": "Ontario Personal Health Information Protection Act applies to health information custodians and agents",
            "impact_on_contract": "Express PHIPA agent agreement required; strict limits on collection, use, disclosure; mandatory breach notification",
            "pillar": "privacy",
            "requires_legal": 1, "active": 1,
            "notes": "PHIPA contracts require specialist review.",
        },
        {
            "jurisdiction": "Ontario",
            "rule_category": "Limitation Periods",
            "rule_name": "2-Year Basic Limitation Period",
            "rule_description": "Ontario Limitations Act 2002: basic limitation period is 2 years from discovery of claim",
            "impact_on_contract": "Contract cannot shorten the basic 2-year limitation period below Ontario statutory minimum",
            "pillar": "legal",
            "requires_legal": 1, "active": 1,
            "notes": None,
        },
        {
            "jurisdiction": "Ontario",
            "rule_category": "Employment",
            "rule_name": "Employment Standards Act — Contractor vs Employee",
            "rule_description": "ESA 2000 applies to employees; misclassification of employees as independent contractors creates liability",
            "impact_on_contract": "Subcontracting clauses must not create deemed employee relationships; independent contractor language must be accurate",
            "pillar": "delivery",
            "requires_legal": 1, "active": 1,
            "notes": None,
        },
        {
            "jurisdiction": "Federal Canada",
            "rule_category": "Privacy",
            "rule_name": "PIPEDA Federal Application",
            "rule_description": "PIPEDA applies to all federal works, undertakings, and businesses operating interprovincially",
            "impact_on_contract": "Privacy policy reference, purpose limitation, consent requirements",
            "pillar": "privacy",
            "requires_legal": 0, "active": 1,
            "notes": None,
        },
        {
            "jurisdiction": "Federal Canada",
            "rule_category": "Procurement",
            "rule_name": "Public Works PSPC Standard Terms",
            "rule_description": "Crown contracts use PWGSC Standard Acquisition Clauses and Conditions (SACC Manual)",
            "impact_on_contract": "SACC clauses are non-negotiable; focus review on unique contract terms and SOW",
            "pillar": "legal",
            "requires_legal": 1, "active": 1,
            "notes": "Reference SACC Manual at buyandsell.gc.ca.",
        },
    ]
    for r in rows:
        db.create_jurisdiction_rule(r)
    return {"jurisdiction_rules": len(rows)}


# ── Project Type Profiles ─────────────────────────────────────────────────────

def _seed_project_type_profiles(db) -> dict:
    if db.get_all_project_type_profiles(active_only=False):
        return {}
    rows = [
        {
            "project_type": "Fixed Price",
            "typical_risks": "Scope creep, change order disputes, milestone payment withholding, warranty claims post-delivery",
            "key_clauses_to_watch": json.dumps(["Acceptance Criteria", "Change Order Process", "Termination for Convenience", "Warranty", "Limitation of Liability"]),
            "recommended_pillars": json.dumps(["delivery", "commercial", "liability", "ip"]),
            "delivery_model": None,
            "notes": "Acceptance criteria must be precisely defined; change order rights are critical.",
            "active": 1,
        },
        {
            "project_type": "Time & Materials",
            "typical_risks": "Budget overrun claims, hours disputes, customer termination mid-project, staff substitution restrictions",
            "key_clauses_to_watch": json.dumps(["Rate Schedule", "Termination for Convenience", "Staff Substitution", "Expense Reimbursement", "Invoicing Frequency"]),
            "recommended_pillars": json.dumps(["commercial", "delivery", "legal"]),
            "delivery_model": None,
            "notes": "Ensure rate schedule is attached and expense reimbursement policy is clear.",
            "active": 1,
        },
        {
            "project_type": "Managed Services",
            "typical_risks": "SLA breach penalties, continuous liability exposure, key person dependency, audit rights overreach",
            "key_clauses_to_watch": json.dumps(["SLA", "Service Credits", "Key Personnel", "Audit Rights", "Business Continuity", "Exit Assistance"]),
            "recommended_pillars": json.dumps(["delivery", "liability", "commercial", "security"]),
            "delivery_model": None,
            "notes": "Exit assistance (transition-out) clause is critical for managed services.",
            "active": 1,
        },
    ]
    for r in rows:
        db.create_project_type_profile(r)
    return {"project_type_profiles": len(rows)}
