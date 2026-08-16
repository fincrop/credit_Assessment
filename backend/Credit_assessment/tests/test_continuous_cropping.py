"""
Continuously-cropped land must still yield cycles.

REGRESSION, found on real data. Farmer 14322905350 (western UP, wheat-rice)
produced ZERO cycles and scored 26.7 VERY_HIGH, despite:

    ndvi_p10 0.276 | p50 0.518 | p90 0.684 | max 0.784
    ndvi_amplitude       0.408
    frac_above_vegetated 0.663
    frac_below_bare      0.011      <- essentially never bare

Unmistakably cropped. The cause was a gap between the two cycle models:

  * its trough sits at VS 0.428, ABOVE the 0.35 bare-soil gate, so the sow
    walk-back never terminated and every candidate was rejected on duration;
  * p10 0.428 < 0.50 and amplitude 0.328 > 0.30, so the perennial branch did
    not fire either.

Intensive double-cropping leaves almost no fallow interval, and after 10-day
binning plus smoothing the trough never reaches bare soil. The bias therefore
fell hardest on the MOST productive farms — the worst possible direction.
"""

from __future__ import annotations

import os

os.environ.setdefault("PHENO_FIT_DISABLE", "1")

from datetime import datetime, timedelta  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from config import PipelineConfig as P  # noqa: E402
from crop_analysis.crop_cycle_detector import CropCycleDetector  # noqa: E402
from data_acquisition.satellite_collector import SatelliteDataCollector  # noqa: E402

STEP = 10


def _build(ndvi_profile, start="2023-06-01"):
    t0 = datetime.strptime(start, "%Y-%m-%d")
    scenes, dates = [], []
    for i, v in enumerate(ndvi_profile):
        d = (t0 + timedelta(days=i * STEP)).strftime("%Y-%m-%d")
        dates.append(d)
        scenes.append({
            "date": d, "missing": False, "cloud_cover": 5.0,
            "indices": {"NDVI_mean": v, "EVI_mean": v * 0.8,
                        "NDMI_mean": v * 0.45 - 0.05},
        })
    cd = {"scenes": scenes, "dates": dates}
    cd = SatelliteDataCollector._build_composite_signal(cd, field_area_ha=0.5)
    cd["ndvi_values"] = [s["indices"]["NDVI_mean"] for s in scenes]
    cd["evi_values"] = [s["indices"]["EVI_mean"] for s in scenes]
    cd["ndmi_values"] = [s["indices"]["NDMI_mean"] for s in scenes]
    return cd


def _detect(cd):
    det = CropCycleDetector()
    return det.detect_cycles(
        dates=cd["dates"], ndvi_values=cd["ndvi_values"],
        evi_values=cd["evi_values"], ndmi_values=cd["ndmi_values"],
        scenes=cd["scenes"], grid_step_days=float(STEP),
        composite_smooth_values=cd.get("vs_smooth"),
    ), det.last_detection_meta


def _double_cropped(n_years=3, trough=0.28, peak=0.78):
    """
    Wheat-rice rotation: two peaks a year, and the trough between them stays
    well above bare soil because the next crop goes in almost immediately.
    """
    per_half = 18  # ~180 days
    profile = []
    for _ in range(n_years * 2):
        up = list(np.linspace(trough, peak, per_half // 2))
        down = list(np.linspace(peak, trough, per_half - per_half // 2))
        profile += up + down
    return profile


# ── the regression ────────────────────────────────────────────────────────

def test_continuously_cropped_land_yields_cycles():
    """The headline: this returned zero cycles and scored VERY_HIGH."""
    cycles, _ = _detect(_build(_double_cropped()))
    assert cycles, (
        "continuously-cropped land produced no cycles — it would be scored as "
        "abandoned despite never going bare"
    )


def test_its_trough_really_is_above_the_bare_soil_gate():
    """Confirms the fixture reproduces the actual failure condition."""
    cd = _build(_double_cropped())
    vs = [v for v in cd["vs_smooth"] if v is not None]
    assert min(vs) > P.CROP_CYCLE_MIN_BASELINE_CVI, (
        f"fixture trough {min(vs):.3f} dips below the gate "
        f"{P.CROP_CYCLE_MIN_BASELINE_CVI} — not the case under test"
    )


def test_it_is_not_misread_as_perennial():
    """
    It swings too much to be a plantation. Detecting it as perennial would
    score it on canopy persistence instead of cropping intensity — right
    answer, wrong reason.
    """
    _, meta = _detect(_build(_double_cropped()))
    assert meta["perennial_detected"] is False


def test_multiple_cycles_are_found_not_one_merged_block():
    cycles, _ = _detect(_build(_double_cropped(n_years=3)))
    assert len(cycles) >= 2, f"expected several cycles, got {len(cycles)}"


# ── the fallback must not manufacture cycles ──────────────────────────────

def test_a_flat_high_signal_still_yields_no_cycles():
    """
    The relative-trough fallback removes the bare-soil requirement, so the
    guard against inventing cycles is now min_rise alone. A flat canopy has no
    rise and must still produce nothing.
    """
    rng = np.random.default_rng(7)
    flat = list(0.70 + 0.01 * rng.standard_normal(108))
    cycles, meta = _detect(_build(flat))
    annual = [c for c in cycles if c.to_dict()["cycle_kind"] == "annual"]
    assert annual == [], f"{len(annual)} phantom annual cycle(s) from a flat signal"


def test_barren_ground_still_yields_no_cycles():
    rng = np.random.default_rng(9)
    barren = list(0.13 + 0.015 * rng.standard_normal(108))
    cycles, _ = _detect(_build(barren))
    assert cycles == []


def test_a_normal_fallowing_farm_is_unaffected():
    """The fallback must not change behaviour where a real trough exists."""
    season = ([0.12] * 6 + list(np.linspace(0.12, 0.78, 7))
              + list(np.linspace(0.78, 0.12, 7)) + [0.12] * 6)
    cycles, _ = _detect(_build(season * 2))
    assert cycles


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
