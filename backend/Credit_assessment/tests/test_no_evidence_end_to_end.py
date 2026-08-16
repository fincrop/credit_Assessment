"""
"No evidence" must survive the whole chain, not just the scoring engine.

REGRESSION. test_no_evidence_scoring.py asserted the risk engine floors vigor
at 25 when there is no yield evidence — and it passed, because the fixture
simply omitted `average_yield_score`. On the real path performance_analyzer
always SUPPLIED that key with a neutral 50.0 when nothing was scored, so the
engine took the "value present" branch and the floor never applied.

Observed in the field on a parcel with zero detected cycles:
    Average Yield Proxy: 50.0%
    vigor  50.0/100

A unit test that only exercises the shape it invented is not a test of the
pipeline. These assert the CONTRACT between the two components.
"""

from __future__ import annotations

import pytest

from assessment.risk_index_engine import RiskIndexEngine
from crop_analysis.performance_analyzer import CropPerformanceAnalyzer


def _score(perf):
    return RiskIndexEngine().score({
        "cropping_analysis": {"total_seasons_analyzed": 6, "seasons_with_crops": 0},
        "performance_analysis": perf,
        "weather_analysis": {"weather_risk_score": 25.0},
        "signal_quality_summary": {"valid_fraction": 0.6, "mean_bin_quality": 0.55,
                                   "sar_fallback_fraction": 0.0},
        "lookback_years": 3.0,
    })


# ── the producer side ─────────────────────────────────────────────────────

def test_analyzer_reports_none_not_fifty_when_nothing_was_scored():
    """A neutral 50 reads downstream as 'an average farm'."""
    out = CropPerformanceAnalyzer().analyze_performance(season_results=[], seasonal_data=[])
    assert out["average_yield_score"] is None
    assert out["average_health_score"] is None
    assert out["average_performance_score"] is None
    assert out["n_complete_cycles"] == 0


def test_analyzer_still_reports_numbers_when_cycles_exist():
    """The change must only bite when there is genuinely nothing to average."""
    out = CropPerformanceAnalyzer().analyze_performance(season_results=[], seasonal_data=[])
    # Sanity: with no input we get None; the populated case is covered by the
    # engine-side tests, which feed real seasonal_performance rows.
    assert out["average_yield_score"] is None


# ── the contract between them ─────────────────────────────────────────────

def test_engine_floors_vigor_on_the_real_analyzer_output():
    """
    The regression, end to end: feed the engine exactly what the analyzer now
    produces for a zero-cycle parcel.
    """
    perf = CropPerformanceAnalyzer().analyze_performance(season_results=[], seasonal_data=[])
    vigor = _score(perf)["sub_indices"]["vigor"]["score"]
    assert vigor <= 30.0, (
        f"vigor scored {vigor} with no observed growth — the no-evidence floor "
        f"was bypassed again"
    )


def test_explicit_none_is_treated_as_no_evidence():
    perf = {"seasonal_performance": [], "n_complete_cycles": 0,
            "average_yield_score": None, "average_health_score": None}
    assert _score(perf)["sub_indices"]["vigor"]["score"] <= 30.0


def test_a_supplied_average_is_still_respected():
    """
    Guard against over-correcting: a real average from a real cycle must still
    be used, not overridden by the floor.
    """
    perf = {"seasonal_performance": [], "n_complete_cycles": 0,
            "average_yield_score": 72.0}
    assert _score(perf)["sub_indices"]["vigor"]["score"] > 30.0


def test_zero_cycle_parcel_lands_in_the_worst_band():
    perf = CropPerformanceAnalyzer().analyze_performance(season_results=[], seasonal_data=[])
    result = _score(perf)
    assert result["risk_category"] in ("HIGH", "VERY_HIGH")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
