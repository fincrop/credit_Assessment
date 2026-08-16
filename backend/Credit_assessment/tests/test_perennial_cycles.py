"""
Perennial / plantation detection and scoring.

Before this branch existed, a productive orchard returned ZERO cycles. With no
trough, the sow walk-back defaults to peak-190d and harvest to peak+40d, giving
230 days against a 195-day cap — so every candidate was rejected on duration,
silently and totally. Zero cycles then read downstream as crop_intensity 0 and
fallow_fraction 1.0, so a mango grove was scored as ABANDONED LAND.

Decision D-6 makes perennials a fundable product, so this is P0.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

os.environ.setdefault("PHENO_FIT_DISABLE", "1")  # see test_cycle_detection_signal_v2

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from assessment.risk_index_engine import RiskIndexEngine  # noqa: E402
from crop_analysis.crop_cycle_detector import CropCycleDetector  # noqa: E402
from data_acquisition.satellite_collector import SatelliteDataCollector  # noqa: E402

STEP = 10
N_3YR = 108  # ~3 years of 10-day bins


def _idx(ndvi):
    return {"NDVI_mean": ndvi, "EVI_mean": ndvi * 0.8, "NDMI_mean": ndvi * 0.45 - 0.05}


def _build(ndvi_profile, start="2021-06-01"):
    t0 = datetime.strptime(start, "%Y-%m-%d")
    scenes, dates = [], []
    for i, v in enumerate(ndvi_profile):
        d = (t0 + timedelta(days=i * STEP)).strftime("%Y-%m-%d")
        dates.append(d)
        scenes.append({"date": d, "missing": False, "cloud_cover": 5.0, "indices": _idx(v)})
    cd = {"scenes": scenes, "dates": dates}
    cd = SatelliteDataCollector._build_composite_signal(cd, field_area_ha=1.5)
    cd["ndvi_values"] = [s["indices"]["NDVI_mean"] for s in scenes]
    cd["evi_values"] = [s["indices"]["EVI_mean"] for s in scenes]
    cd["ndmi_values"] = [s["indices"]["NDMI_mean"] for s in scenes]
    return cd


def _detect(cd):
    det = CropCycleDetector()
    cycles = det.detect_cycles(
        dates=cd["dates"], ndvi_values=cd["ndvi_values"],
        evi_values=cd["evi_values"], ndmi_values=cd["ndmi_values"],
        scenes=cd["scenes"], grid_step_days=float(STEP),
        composite_smooth_values=cd.get("vs_smooth"),
    )
    return cycles, det.last_detection_meta


# ── profiles ──────────────────────────────────────────────────────────────

def _orchard(n=N_3YR):
    """Mature orchard: high canopy year-round, mild seasonal breathing."""
    rng = np.random.default_rng(3)
    seasonal = 0.05 * np.sin(np.linspace(0, 6 * np.pi, n))
    return list(0.74 + seasonal + 0.012 * rng.standard_normal(n))


def _annual_double_crop(n=N_3YR):
    per_year = n // 3
    half = per_year // 2
    one = (list(np.linspace(0.12, 0.78, half // 2))
           + list(np.linspace(0.78, 0.12, half - half // 2)))
    return (one * 6)[:n]


def _barren(n=N_3YR):
    rng = np.random.default_rng(5)
    return list(0.13 + 0.015 * rng.standard_normal(n))


# ── detection ─────────────────────────────────────────────────────────────

def test_orchard_produces_cycles_instead_of_nothing():
    """The headline fix: an orchard used to return zero cycles."""
    cycles, meta = _detect(_build(_orchard()))
    assert cycles, "orchard produced no cycles — it would score as abandoned land"
    assert meta["perennial_detected"] is True
    assert meta["cycle_kind"] == "perennial"


def test_orchard_yields_one_production_year_per_year():
    cycles, _ = _detect(_build(_orchard()))
    assert 2 <= len(cycles) <= 4, f"expected ~3 production years, got {len(cycles)}"


def test_perennial_cycles_are_labelled_as_such():
    cycles, _ = _detect(_build(_orchard()))
    for c in cycles:
        d = c.to_dict()
        assert d["cycle_kind"] == "perennial"
        assert d["crop_type"] == "PERENNIAL"


def test_perennial_branch_does_not_hijack_an_annual_crop():
    """A sown crop must still be detected as annual, not folded into perennial."""
    cycles, meta = _detect(_build(_annual_double_crop()))
    assert cycles
    assert meta["perennial_detected"] is False
    assert all(c.to_dict()["cycle_kind"] == "annual" for c in cycles)


def test_barren_land_is_not_promoted_to_perennial():
    """Flat is not enough — it must be flat AND vegetated."""
    cycles, meta = _detect(_build(_barren()))
    assert meta["perennial_detected"] is False
    assert cycles == []


def test_perennial_cycles_carry_observation_provenance():
    cycles, _ = _detect(_build(_orchard()))
    for c in cycles:
        d = c.to_dict()
        assert 0.0 <= d["observed_fraction"] <= 1.0
        assert d["n_bins"] > 0


# ── scoring ───────────────────────────────────────────────────────────────

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


def _healthy_orchard_score():
    years = [
        {"season": f"year_{i}", "yield_potential_score": 72.0, "n_scenes": 36,
         "yield_detail": {"peak_cvi": 0.78, "yield_index_basis": "internal_cvi_auc"},
         "anomaly_events": []}
        for i in range(1, 4)
    ]
    return _score(
        {"cycle_kind": "perennial", "lookback_years": 3.0,
         "total_seasons_analyzed": 3, "seasons_with_crops": 3},
        {"seasonal_performance": years, "n_complete_cycles": 3},
    )


def _abandoned_score():
    return _score(
        {"total_seasons_analyzed": 6, "seasons_with_crops": 0},
        {"seasonal_performance": [], "n_complete_cycles": 0},
    )


def test_healthy_orchard_scores_well_above_abandoned_land():
    """The defect in one assertion: an orchard used to BE abandoned land."""
    orchard = _healthy_orchard_score()["index_score"]
    abandoned = _abandoned_score()["index_score"]
    assert orchard > abandoned + 20, (
        f"orchard {orchard} vs abandoned {abandoned} — too close"
    )


def test_orchard_is_not_capped_by_the_annual_intensity_ladder():
    """
    cycles_per_year is ~1 for any perennial, which the annual ladder caps at
    60/100 no matter how well the planting is run.
    """
    lu = _healthy_orchard_score()["sub_indices"]["landuse"]
    assert lu["score"] > 60.0, (
        f"well-run orchard capped at {lu['score']} by the annual ladder"
    )


def test_orchard_landuse_is_scored_on_perennial_drivers():
    lu = _healthy_orchard_score()["sub_indices"]["landuse"]
    assert lu["inputs"]["cycle_kind"] == "perennial"
    for driver in ("canopy_persistence", "production_continuity",
                   "inter_annual_stability"):
        assert driver in lu["drivers"]


def test_perennial_reports_fallow_as_not_applicable_not_zero():
    """Rule P-1: a perennial has no fallow fraction; it must not read as 0.0."""
    lu = _healthy_orchard_score()["sub_indices"]["landuse"]
    assert lu["inputs"]["fallow_fraction"] is None
    assert lu["inputs"]["fallow_basis"] == "not_applicable_perennial"
    assert lu["inputs"]["cycles_per_year"] is None


def test_single_observed_year_is_not_treated_as_stable():
    one_year = _score(
        {"cycle_kind": "perennial", "lookback_years": 3.0},
        {"seasonal_performance": [
            {"season": "year_1", "yield_potential_score": 72.0, "n_scenes": 36,
             "yield_detail": {"peak_cvi": 0.78}, "anomaly_events": []}],
         "n_complete_cycles": 1},
    )
    lu = one_year["sub_indices"]["landuse"]
    assert lu["drivers"]["inter_annual_stability"] <= 60.0
    # And missing production years must cost continuity.
    assert lu["drivers"]["production_continuity"] < 50.0


def test_annual_farm_scoring_is_unchanged_by_the_perennial_branch():
    seasons = [
        {"season": f"cycle_{i}", "yield_potential_score": 70.0, "n_scenes": 12,
         "yield_detail": {"peak_cvi": 0.70}, "anomaly_events": []}
        for i in range(1, 6)
    ]
    annual = _score(
        {"total_seasons_analyzed": 6, "seasons_with_crops": 5,
         "fallow_fraction": 0.18, "lookback_years": 3.0},
        {"seasonal_performance": seasons, "n_complete_cycles": 5},
    )
    lu = annual["sub_indices"]["landuse"]
    assert lu["inputs"]["fallow_basis"] == "measured"
    assert "cycles_per_year" in lu["inputs"] and lu["inputs"]["cycles_per_year"] is not None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
