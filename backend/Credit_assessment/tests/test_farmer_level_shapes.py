"""
Farmer-level aggregation stores sub_indices as scalars. Plot-level engine
output stores {score, inputs, drivers}. Readers that assume one shape crash
the other — this is the AttributeError / KeyError that failed the job and
the /v1/report 500.
"""

from __future__ import annotations

from ai_integration.counterfactual_engine import CounterfactualEngine
from ai_integration.groq_report_generator import _risk_view
from ai_integration.shap_explainer import SHAPExplainer
from assessment.legacy_credit_shim import sub_index_score


def _farmer_level_doc():
    return {
        "farmer_id": "13528946442",
        "status": "SUCCESS",
        "index_version": "index_v5",
        "farmer_level": {
            "index_score": 70.8,
            "raw_index": 79.6,
            "risk_category": "LOW",
            "confidence_gate": 0.89,
            "weights": {"landuse": 30, "vigor": 25, "stability": 20, "weather": 25},
            "sub_indices": {
                "landuse": 62.8,
                "vigor": 82.9,
                "stability": 97.6,
                "weather": 65.9,
                "data_confidence": 72.4,
            },
            "weak_sub_indices": [],
        },
    }


def test_sub_index_score_accepts_both_shapes():
    assert sub_index_score({"score": 62.8}) == 62.8
    assert sub_index_score(62.8) == 62.8
    assert sub_index_score(None) is None
    assert sub_index_score(None, 50.0) == 50.0


def test_counterfactual_on_farmer_level_does_not_keyerror():
    out = CounterfactualEngine().generate(_farmer_level_doc())
    assert out["current_score"] == 70.8
    assert isinstance(out["scenarios"], list)


def test_shap_on_farmer_level_does_not_crash():
    out = SHAPExplainer._rule_based_shap(_farmer_level_doc())
    assert out["attribution_available"] is True
    assert "landuse" in out["feature_contributions"]


def test_groq_risk_view_reads_farmer_level_scalars():
    rv = _risk_view(_farmer_level_doc())
    assert rv["score"] == 70.8
    assert rv["component_scores"]["landuse"] == 62.8
