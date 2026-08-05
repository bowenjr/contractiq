"""Table-driven materiality rules for readiness conditions."""

MATERIALITY_BY_CONDITION: dict[str, bool] = {
    "g0.bid_complete": True,
    "g1.bid_no_bid_approved": True,
    "g2.no_scope_gaps": True,
    "g2.strategy_recorded": True,
    "g3.suppliers_supported": True,
    "g4.margin_approved": True,
    "g4.high_findings_have_authority": True,
    "g4.required_approvals": True,
    "g5.mandatory_requirements_complete": True,
    "g5.no_unconfirmed_material": True,
    "g5.prior_gates_passed": True,
    "g6.concessions_approved": True,
    "g7.award_matches_offer": True,
    "g7.handover_accepted": True,
}


def is_material(condition_id: str, detail: str) -> bool:
    """Return the explicit v0.1 materiality classification for a condition.

    All conditions defined in v0.1 are material by design. Unknown conditions
    fail safe to material until they are explicitly classified; ``detail`` is
    accepted for the stable public API but the v0.1 rules are table-driven.
    """
    return MATERIALITY_BY_CONDITION.get(condition_id, True)
