"""
Compatibility shim: risk_assessment → credit_assessment-shaped dict.

Keeps the existing dashboard (credit_score / component_scores / risk_category)
alive after the index_v5 cutover. component_scores are SCALAR floats — never
the nested {score, inputs, drivers} objects from sub_indices.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def sub_index_score(val: Any, default: Optional[float] = None) -> Optional[float]:
    """
    Plot-level sub_indices are ``{score, inputs, drivers}``. Farmer-level
    aggregation stores the same keys as bare floats. Readers that assume one
    shape crash the other (AttributeError: float has no attribute 'get').
    """
    if isinstance(val, dict):
        val = val.get("score")
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def risk_block(assessment: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Plot docs use ``risk_assessment``; multi-farm roll-ups use ``farmer_level``."""
    if not isinstance(assessment, dict):
        return {}
    ra = assessment.get("risk_assessment")
    if isinstance(ra, dict) and ra.get("sub_indices"):
        return ra
    fl = assessment.get("farmer_level")
    if isinstance(fl, dict) and fl.get("sub_indices"):
        return fl
    return ra if isinstance(ra, dict) else (fl if isinstance(fl, dict) else {})


def legacy_credit_shim(risk_assessment: Dict[str, Any]) -> Dict[str, Any]:
    """Map a RiskIndexEngine result into a legacy credit_assessment payload."""
    ra = risk_assessment or {}
    sub = ra.get("sub_indices") or {}

    component_scores: Dict[str, float] = {}
    for key, val in sub.items():
        score = sub_index_score(val)
        if score is not None:
            component_scores[key] = score

    weights = ra.get("weights") or {}
    try:
        component_weights = {k: float(v) for k, v in weights.items()}
    except (TypeError, ValueError):
        component_weights = dict(weights) if isinstance(weights, dict) else {}

    weak = ra.get("weak_sub_indices") or ra.get("weak_components") or []
    if not isinstance(weak, list):
        weak = list(weak) if weak else []

    reason_codes: List[Any] = ra.get("reason_codes") or []
    messages: List[str] = []
    for rc in reason_codes:
        if isinstance(rc, dict):
            msg = rc.get("message") or rc.get("code") or rc.get("reason")
            if msg:
                messages.append(str(msg))
        elif rc:
            messages.append(str(rc))
    scoring_narrative = "; ".join(messages) if messages else (
        f"Agronomic risk index {ra.get('index_score', 'n/a')} "
        f"({ra.get('risk_category', 'UNKNOWN')})"
    )

    gate = ra.get("confidence_gate")
    confidence = None
    if gate is not None:
        try:
            confidence = float(gate)
        except (TypeError, ValueError):
            confidence = gate

    return {
        "credit_score": float(ra.get("index_score") or 0.0),
        "risk_category": ra.get("risk_category", "UNKNOWN"),
        "component_scores": component_scores,
        "component_weights": component_weights,
        "weak_components": list(weak),
        "method": ra.get("method", "risk_index_v5_rule_based"),
        "confidence": confidence,
        "confidence_gate": gate,
        "scoring_narrative": scoring_narrative,
        "reason_codes": reason_codes,
        "index_version": ra.get("index_version", "index_v5"),
        "positioning": ra.get("positioning", "agronomic_risk_index"),
        "no_repayment_calibration": True,
    }
