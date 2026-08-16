"""
End-to-end cycle detection against the signal_v2 scale.

Covers the behaviours the Phase 2 changes are supposed to produce:
a real crop is still detected, barren ground is not, a short-duration crop
survives smoothing, and a cycle whose peak was reconstructed inside a cloud gap
is marked as inferred rather than passing as observed.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

# The double-logistic refinement is an optional enrichment and is not what these
# tests exercise. It is disabled here because scipy's curve_fit descends into
# LAPACK, which aborts the interpreter outright on some platforms (see
# PipelineConfig.PHENO_FIT_ENABLED) — a crash no test framework can report.
os.environ.setdefault("PHENO_FIT_DISABLE", "1")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from config import PipelineConfig as P
from crop_analysis.crop_cycle_detector import CropCycleDetector
from data_acquisition.satellite_collector import SatelliteDataCollector

STEP = 10  # days per bin, matches CONTINUOUS_SCENE_INTERVAL_DAYS


def _surface_to_indices(ndvi):
    """Plausible EVI/NDMI for a given NDVI, so the composite is realistic."""
    return {"NDVI_mean": ndvi, "EVI_mean": ndvi * 0.8, "NDMI_mean": ndvi * 0.45 - 0.05}


def _build(ndvi_profile, start="2022-06-01", drop_bins=()):
    """
    Turn an NDVI profile into the collector's continuous_data, running the real
    composite builder so the detector sees a genuine signal_v2 series.

    drop_bins marks indices as missing (cloud), which is what forces imputation.
    """
    t0 = datetime.strptime(start, "%Y-%m-%d")
    scenes, dates = [], []
    for i, ndvi in enumerate(ndvi_profile):
        d = (t0 + timedelta(days=i * STEP)).strftime("%Y-%m-%d")
        dates.append(d)
        if i in drop_bins:
            scenes.append({"date": d, "missing": True, "cloud_cover": None,
                           "indices": {k: np.nan for k in _surface_to_indices(0.0)}})
        else:
            scenes.append({"date": d, "missing": False, "cloud_cover": 5.0,
                           "indices": _surface_to_indices(ndvi)})
    cd = {"scenes": scenes, "dates": dates}
    cd = SatelliteDataCollector._build_composite_signal(cd, field_area_ha=1.5)
    cd["ndvi_values"] = [
        None if s.get("missing") else s["indices"]["NDVI_mean"] for s in scenes
    ]
    cd["evi_values"] = [
        None if s.get("missing") else s["indices"]["EVI_mean"] for s in scenes
    ]
    cd["ndmi_values"] = [
        None if s.get("missing") else s["indices"]["NDMI_mean"] for s in scenes
    ]
    return cd


def _detect(cd):
    return CropCycleDetector().detect_cycles(
        dates=cd["dates"],
        ndvi_values=cd["ndvi_values"],
        evi_values=cd["evi_values"],
        ndmi_values=cd["ndmi_values"],
        scenes=cd["scenes"],
        grid_step_days=float(STEP),
        composite_smooth_values=cd.get("vs_smooth"),
    )


# ── profiles ──────────────────────────────────────────────────────────────

def _season(peak=0.78, length=14, base=0.12):
    """A single rise-and-fall crop cycle, `length` bins long."""
    half = length // 2
    up = list(np.linspace(base, peak, half))
    down = list(np.linspace(peak, base, length - half))
    return up + down


FLAT_BARREN = [0.12 + 0.012 * ((i * 7) % 5) for i in range(60)]
ONE_SEASON = [0.12] * 8 + _season() + [0.12] * 8
TWO_SEASONS = [0.12] * 6 + _season() + [0.12] * 6 + _season(peak=0.74) + [0.12] * 6


# ── detection behaviour ───────────────────────────────────────────────────

def test_a_real_crop_cycle_is_detected():
    cycles = _detect(_build(ONE_SEASON))
    assert len(cycles) >= 1, "a clear rise-and-fall season was not detected"


def test_two_seasons_are_detected_separately():
    cycles = _detect(_build(TWO_SEASONS))
    assert len(cycles) >= 2, f"expected 2 cycles, got {len(cycles)}"


def test_barren_ground_yields_no_cycles():
    """
    The headline Phase 2 outcome. Under per-parcel min-max this same profile
    was stretched to span 0-1 and produced spurious cycles from pure noise.
    """
    cycles = _detect(_build(FLAT_BARREN))
    assert cycles == [], f"barren ground produced {len(cycles)} phantom cycle(s)"


def test_short_duration_crop_survives_smoothing():
    """
    An ~80-day crop. The old 70-day moving average flattened these below the
    peak gate before detection ever ran.
    """
    short = [0.12] * 6 + _season(peak=0.76, length=8) + [0.12] * 6
    cycles = _detect(_build(short))
    assert len(cycles) >= 1, "short-duration crop was smoothed away"


def test_detection_uses_the_whittaker_signal():
    det = CropCycleDetector()
    cd = _build(ONE_SEASON)
    det.detect_cycles(
        dates=cd["dates"], ndvi_values=cd["ndvi_values"],
        evi_values=cd["evi_values"], ndmi_values=cd["ndmi_values"],
        scenes=cd["scenes"], grid_step_days=float(STEP),
        composite_smooth_values=cd.get("vs_smooth"),
    )
    assert det.last_detection_meta.get("signal_source_used") == "composite_vs_whittaker"


# ── provenance on detected cycles ─────────────────────────────────────────

def test_fully_observed_cycle_is_marked_observed():
    cycles = _detect(_build(ONE_SEASON))
    c = cycles[0].to_dict()
    assert c["peak_observed"] is True
    assert c["observed_fraction"] == pytest.approx(1.0, abs=1e-6)


def test_cycle_peaking_inside_a_cloud_gap_is_flagged_as_inferred():
    """
    A fabricated peak must never be indistinguishable from an observed one.
    Blank out the bins around the peak so the gap filler reconstructs it.
    """
    profile = ONE_SEASON
    peak_bin = int(np.argmax(profile))
    gap = tuple(range(peak_bin - 2, peak_bin + 3))
    cycles = _detect(_build(profile, drop_bins=gap))
    if not cycles:
        pytest.skip("no cycle detected across the gap; nothing to assert")
    c = cycles[0].to_dict()
    assert c["peak_observed"] is False, "a reconstructed peak was reported as observed"
    assert c["observed_fraction"] < 1.0
    assert c["n_observed_bins"] < c["n_bins"]


def test_observed_fraction_is_reported_for_every_cycle():
    for cycles in (_detect(_build(ONE_SEASON)), _detect(_build(TWO_SEASONS))):
        for c in cycles:
            d = c.to_dict()
            assert 0.0 <= d["observed_fraction"] <= 1.0
            assert d["n_bins"] > 0


# ── threshold coherence guard ─────────────────────────────────────────────

def test_config_thresholds_are_not_inverted():
    assert P.CROP_CYCLE_MIN_BASELINE_CVI < P.CROP_CYCLE_MIN_PEAK_CVI


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
