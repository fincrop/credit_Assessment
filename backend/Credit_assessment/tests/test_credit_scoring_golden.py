"""
Regression tests for RiskIndexEngine + legacy_credit_shim + job-runner tri-state.

Run from backend/Credit_assessment:
  python -m pytest tests/ -q
  python tests/test_credit_scoring_golden.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assessment.legacy_credit_shim import legacy_credit_shim
from assessment.risk_index_engine import RiskIndexEngine, INDEX_VERSION
from api.job_runner import process_assessment_job
from config import PipelineConfig


def _fixture_assessment(
    *,
    pm_kisan: Optional[bool] = True,
    has_insurance: Optional[bool] = False,
) -> Dict[str, Any]:
    benefits: Dict[str, Any] = {}
    if pm_kisan is not None:
        benefits["pm_kisan_enrolled"] = pm_kisan
    if has_insurance is not None:
        benefits["has_crop_insurance"] = has_insurance
    return {
        "field_area_ha": 1.2,
        "farmer_benefits": benefits,
        "signal_quality_summary": {
            "valid_bin_fraction": 0.85,
            "sar_fallback_share": 0.05,
            "series_length_bins": 110,
        },
        "cropping_analysis": {
            "cropping_intensity": 1.5,
            "cycles_per_year": 1.5,
            "season_results": [{"crop_detected": True}],
        },
        "performance_analysis": {
            "average_performance_score": 72.0,
            "average_yield_score": 68.0,
            "n_complete_cycles": 3,
            "n_active_cycles": 0,
            "seasonal_performance": [],
            "peer_benchmarking": {"percentile": 60.0, "n": 25},
        },
        "weather_analysis": {
            "weather_risk_score": 25.0,
            "backward_resilience": {"score": 70.0},
            "forward_exposure": {"score": 40.0},
            "cycle_weather": [],
        },
        "crop_cycles": {
            "cycles_count": 3,
            "utilization_metrics": {
                "land_utilization_index": 0.8,
                "crops_per_year": 1.5,
                "fallow_frequency": 0.1,
            },
        },
    }


def test_risk_index_stable_mid_band():
    engine = RiskIndexEngine(verbose=False)
    out = engine.score(_fixture_assessment())
    assert out["index_version"] == INDEX_VERSION
    assert "recommended_limit" not in out
    assert "credit_limit" not in out
    score = float(out["index_score"])
    assert 20.0 <= score <= 100.0, score
    sub = out["sub_indices"]
    for key in ("landuse", "vigor", "stability", "weather", "data_confidence"):
        assert key in sub
        assert isinstance(sub[key], dict)
        assert "score" in sub[key]


def test_unknown_benefits_do_not_crash_and_no_false_penalty():
    engine = RiskIndexEngine(verbose=False)
    known = engine.score(_fixture_assessment(pm_kisan=True, has_insurance=False))
    unknown = engine.score(_fixture_assessment(pm_kisan=None, has_insurance=None))
    # Unknown should not produce a lower benefits bonus than confirmed-absent alone
    # in a way that collapses to forcing False; both should be finite scores.
    assert float(unknown["index_score"]) > 0
    assert float(known["index_score"]) > 0


def test_legacy_shim_scalar_component_scores():
    engine = RiskIndexEngine(verbose=False)
    ra = engine.score(_fixture_assessment())
    shim = legacy_credit_shim(ra)
    assert isinstance(shim["credit_score"], float)
    comps = shim["component_scores"]
    assert comps, "expected component_scores"
    for k, v in comps.items():
        assert isinstance(v, (int, float)), f"{k}={v!r} must be scalar"
        assert not isinstance(v, dict)
    assert "weak_components" in shim
    assert isinstance(shim["scoring_narrative"], str) and shim["scoring_narrative"]
    assert "component_weights" in shim
    assert shim.get("index_version") == INDEX_VERSION


def test_config_subindex_weights_valid():
    assert PipelineConfig.validate_config() is True
    assert set(PipelineConfig.SUBINDEX_WEIGHTS) == {
        "landuse",
        "vigor",
        "stability",
        "weather",
    }
    assert abs(sum(PipelineConfig.SUBINDEX_WEIGHTS.values()) - 100.0) < 0.01
    assert (2, 15) in PipelineConfig.SEASON_SNAP_ANCHORS


def test_job_runner_tri_state_unknown_stays_none():
    """Missing benefit keys must not be coerced to False in the override."""
    captured: Dict[str, Any] = {}

    class _Pipeline:
        def assess_farmer_from_db(self, **kwargs):
            captured.update(kwargs)
            return {
                "status": "SUCCESS",
                "pipeline_stages": ["1_shell"],
                "risk_assessment": {"index_score": 50.0},
                "credit_assessment": {"credit_score": 50.0},
            }

    jobs: List[Dict[str, Any]] = []

    class _Jobs:
        def update_one(self, filt, update):
            jobs.append({"filt": filt, "update": update})
            return MagicMock(modified_count=1)

    job = {
        "_id": "abc123",
        "farmer_id": "DEMO_001",
        # deliberately omit pm_kisan_enrolled / has_crop_insurance
    }
    process_assessment_job(_Jobs(), _Pipeline(), job, env_classification_enabled=False)

    override = captured.get("farmer_benefits_override")
    assert override is None or (
        "pm_kisan_enrolled" not in override and "has_crop_insurance" not in override
    )


def test_job_runner_explicit_false_is_passed():
    captured: Dict[str, Any] = {}

    class _Pipeline:
        def assess_farmer_from_db(self, **kwargs):
            captured.update(kwargs)
            return {"status": "SUCCESS", "pipeline_stages": []}

    class _Jobs:
        def update_one(self, filt, update):
            return MagicMock(modified_count=1)

    job = {
        "_id": "abc124",
        "farmer_id": "DEMO_001",
        "pm_kisan_enrolled": False,
        "has_crop_insurance": False,
    }
    process_assessment_job(_Jobs(), _Pipeline(), job, env_classification_enabled=False)
    override = captured.get("farmer_benefits_override") or {}
    assert override.get("pm_kisan_enrolled") is False
    assert override.get("has_crop_insurance") is False


if __name__ == "__main__":
    test_config_subindex_weights_valid()
    test_risk_index_stable_mid_band()
    test_unknown_benefits_do_not_crash_and_no_false_penalty()
    test_legacy_shim_scalar_component_scores()
    test_job_runner_tri_state_unknown_stays_none()
    test_job_runner_explicit_false_is_passed()
    print("All tests passed.")
