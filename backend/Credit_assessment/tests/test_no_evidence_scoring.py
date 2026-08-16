"""
Scoring behaviour when there is no evidence of cultivation.

The land-cover gate is the first line of defence, but it is confidence-banded —
a borderline parcel is scored, not rejected. So the score itself must also
behave sanely on land with no cultivation signal.

Before these fixes, a parcel with zero detected cycles scored:
    landuse    15.0   (coverage 0, and NO fallow penalty — see below)
    vigor      50.0   (neutral default)
    stability  84.0   (no anomalies == "stable")
    weather   ~60
    -> index ~41 / HIGH / SUCCESS

Two of those were perverse:
  * fallow_fraction is only set when land-utilization runs, which needs at
    least one cycle — so a plot with NO cycles took no fallow penalty at all,
    while a genuine farm with gaps did.
  * stability rewards the absence of stress anomalies, and nothing stressful
    happens to a parking lot.
"""

from __future__ import annotations

import pytest

from assessment.risk_index_engine import RiskIndexEngine

WEATHER = {
    "weather_risk_score": 40.0,
    "forward_exposure": {"exposure_score": 32.0},
    "backward_resilience": {"mean_resilience_score": 70.0},
}
QUALITY = {"valid_fraction": 0.88, "mean_bin_quality": 0.82, "sar_fallback_fraction": 0.0}


def _score(cropping, performance):
    return RiskIndexEngine().score({
        "cropping_analysis": cropping,
        "performance_analysis": performance,
        "weather_analysis": WEATHER,
        "signal_quality_summary": QUALITY,
        "lookback_years": 3.0,
    })


def _dead_land():
    """Cloud-free, well observed — and nothing ever grew."""
    return _score(
        {"total_seasons_analyzed": 6, "seasons_with_crops": 0},
        {"seasonal_performance": [], "n_complete_cycles": 0},
    )


def _working_farm():
    """Two cycles a year, healthy, with some stress events."""
    seasons = [
        {"season": f"cycle_{i}", "yield_potential_score": 70.0, "n_scenes": 12,
         "yield_detail": {"peak_cvi": 0.70, "yield_index_basis": "internal_cvi_auc"},
         "anomaly_events": [{"type": "water_stress", "impact": "MEDIUM",
                             "date": f"2023-0{i}-14", "scene_index": i}]}
        for i in range(1, 6)
    ]
    return _score(
        {"total_seasons_analyzed": 6, "seasons_with_crops": 5,
         "fallow_fraction": 0.18, "lookback_years": 3.0},
        {"seasonal_performance": seasons, "n_complete_cycles": 5},
    )


def _fallow_farm():
    """A real farm that cropped once in three years."""
    seasons = [
        {"season": "cycle_1", "yield_potential_score": 58.0, "n_scenes": 11,
         "yield_detail": {"peak_cvi": 0.62, "yield_index_basis": "internal_cvi_auc"},
         "anomaly_events": []}
    ]
    return _score(
        {"total_seasons_analyzed": 6, "seasons_with_crops": 1,
         "fallow_fraction": 0.72, "lookback_years": 3.0},
        {"seasonal_performance": seasons, "n_complete_cycles": 1},
    )


# ── the ordering that must hold ───────────────────────────────────────────

def test_dead_land_scores_below_a_working_farm():
    assert _dead_land()["index_score"] < _working_farm()["index_score"]


def test_dead_land_scores_below_even_a_mostly_fallow_farm():
    """
    The case that was actually inverted: a real farm that lay fallow was
    penalised, while land with no cycles at all was not.
    """
    dead = _dead_land()["index_score"]
    fallow = _fallow_farm()["index_score"]
    assert dead < fallow, (
        f"dead land ({dead}) outscored a genuine fallow farm ({fallow})"
    )


def test_dead_land_is_not_merely_high_risk_but_clearly_worst():
    assert _dead_land()["risk_category"] in ("HIGH", "VERY_HIGH")


# ── the specific defects ──────────────────────────────────────────────────

def test_zero_cycles_incurs_a_full_fallow_penalty():
    """Zero cycles over the lookback IS total fallow, not missing data."""
    lu = _dead_land()["sub_indices"]["landuse"]["inputs"]
    assert lu["fallow_fraction"] == 1.0
    assert lu["fallow_basis"] == "inferred_no_cycles"


def test_absence_of_anomalies_is_not_treated_as_stability():
    stability = _dead_land()["sub_indices"]["stability"]
    assert stability["score"] <= 30.0, (
        f"land with no vegetation scored {stability['score']} on stability"
    )
    assert stability["inputs"]["no_cultivation_evidence"] is True


def test_no_yield_evidence_does_not_default_to_neutral():
    vigor = _dead_land()["sub_indices"]["vigor"]
    assert vigor["score"] <= 30.0, (
        f"no observed growth scored {vigor['score']} on vigor"
    )


def test_a_real_farm_still_reports_measured_fallow():
    lu = _working_farm()["sub_indices"]["landuse"]["inputs"]
    assert lu["fallow_basis"] == "measured"
    assert lu["fallow_fraction"] == pytest.approx(0.18, abs=1e-6)


def test_working_farm_is_unharmed_by_these_changes():
    """The fixes must bite only where there is no evidence."""
    farm = _working_farm()
    assert farm["sub_indices"]["stability"]["inputs"]["no_cultivation_evidence"] is False
    assert farm["index_score"] > 50.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
