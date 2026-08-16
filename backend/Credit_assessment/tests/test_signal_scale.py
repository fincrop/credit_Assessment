"""
Tests for the vegetation signal (VS) scale — signal_v2.

The property that matters: the SAME ground surface must produce the SAME signal
value on every parcel. Under signal_v1 each parcel's series was rescaled to its
own extremes, so a barren plot and a thriving field were mathematically
indistinguishable — and absolute thresholds applied to that meant nothing.
"""

from __future__ import annotations

import numpy as np
import pytest

from config import PipelineConfig as P
from data_acquisition.satellite_collector import SatelliteDataCollector
from utils.data_processing import DataProcessor


# ── surfaces, expressed as (NDVI, EVI, NDMI) ──────────────────────────────
WATER    = (-0.30, -0.05, 0.30)
CONCRETE = (0.05, 0.03, -0.10)
BARE     = (0.12, 0.10, -0.05)
EARLY    = (0.32, 0.24, 0.12)
MIDVEG   = (0.45, 0.35, 0.20)
CANOPY   = (0.72, 0.58, 0.32)


def _scene(surface, date="2023-06-02", missing=False):
    ndvi, evi, ndmi = surface
    return {
        "date": date, "missing": missing, "cloud_cover": 5.0,
        "indices": {"NDVI_mean": ndvi, "EVI_mean": evi, "NDMI_mean": ndmi},
    }


def _vs_series(surfaces, field_area_ha=1.0):
    """Run the real composite builder over a synthetic parcel."""
    scenes = [
        _scene(s, date=f"2023-{1 + i // 3:02d}-{1 + (i % 3) * 10:02d}")
        for i, s in enumerate(surfaces)
    ]
    cd = {"scenes": scenes, "dates": [s["date"] for s in scenes]}
    out = SatelliteDataCollector._build_composite_signal(cd, field_area_ha=field_area_ha)
    return [s["indices"].get("VS_mean") for s in out["scenes"]]


# ── the core property: cross-parcel comparability ─────────────────────────

def test_same_surface_gives_same_signal_on_different_parcels():
    """
    A bare-soil observation must read the same whether it sits in a barren
    parcel or between two crop cycles. signal_v1 failed this outright.
    """
    barren = _vs_series([BARE] * 4 + [CONCRETE] * 2)
    cropped = _vs_series([BARE, EARLY, CANOPY, MIDVEG, BARE, CONCRETE])

    assert barren[0] == pytest.approx(cropped[0], abs=1e-6), (
        "the same bare-soil surface produced different signal values"
    )
    assert barren[-1] == pytest.approx(cropped[-1], abs=1e-6)


def test_barren_parcel_does_not_span_the_full_range():
    """
    The old per-parcel min-max guaranteed every parcel reached ~0 and ~1,
    which is how dead ground manufactured crop cycles out of noise.
    """
    noise = [(0.12 + 0.01 * (i % 3), 0.10, -0.05) for i in range(9)]
    vs = [v for v in _vs_series(noise) if v is not None]
    assert max(vs) < 0.40, f"barren parcel reached VS {max(vs):.3f}"
    assert max(vs) - min(vs) < 0.10, "noise was stretched into a large swing"


def test_barren_parcel_never_reaches_the_peak_threshold():
    """The whole point: dead ground must not clear the crop-peak gate."""
    noise = [(0.12 + 0.01 * (i % 3), 0.10, -0.05) for i in range(9)]
    vs = [v for v in _vs_series(noise) if v is not None]
    assert max(vs) < P.CROP_CYCLE_MIN_PEAK_CVI


def test_real_crop_clears_the_peak_threshold():
    """...while a genuine canopy still does."""
    season = [BARE, EARLY, MIDVEG, CANOPY, CANOPY, MIDVEG, EARLY, BARE]
    vs = [v for v in _vs_series(season) if v is not None]
    assert max(vs) >= P.CROP_CYCLE_MIN_PEAK_CVI


# ── ordering: the scale must track vegetation density ─────────────────────

def test_surfaces_are_ordered_by_vegetation_density():
    vs = _vs_series([WATER, CONCRETE, BARE, EARLY, MIDVEG, CANOPY])
    assert vs == sorted(vs), f"VS is not monotonic in vegetation density: {vs}"


def test_water_scores_below_bare_soil():
    """
    kNDVI = tanh(NDVI^2) destroyed the sign of NDVI, mapping water (-0.30) and
    sparse crop (+0.30) to the identical value. NDVI as backbone preserves it.
    """
    vs = _vs_series([WATER, BARE])
    assert vs[0] < vs[1]


def test_knndvi_would_have_conflated_water_and_sparse_crop():
    """Documents the defect that motivated the backbone change."""
    kndvi = lambda x: float(np.tanh(x ** 2))  # noqa: E731
    assert kndvi(-0.30) == pytest.approx(kndvi(0.30), abs=1e-9)
    # The replacement does not have this property.
    lo, hi = P.INDEX_PHYSICAL_RANGES["NDVI"]
    n = lambda x: float(DataProcessor.normalize_fixed_range(np.array([x]), lo, hi)[0])  # noqa: E731
    assert n(-0.30) < n(0.30)


# ── fixed-range normaliser ────────────────────────────────────────────────

def test_fixed_range_is_independent_of_the_other_values():
    a = DataProcessor.normalize_fixed_range(np.array([0.5, 0.1]), -0.2, 0.9)
    b = DataProcessor.normalize_fixed_range(np.array([0.5, 0.8]), -0.2, 0.9)
    assert a[0] == pytest.approx(b[0]), "value changed because its neighbours did"


def test_fixed_range_clips_out_of_range_values():
    out = DataProcessor.normalize_fixed_range(np.array([-0.9, 1.5]), -0.2, 0.9)
    assert out[0] == 0.0 and out[1] == 1.0


def test_fixed_range_preserves_nan_as_nan():
    out = DataProcessor.normalize_fixed_range(np.array([np.nan, 0.5]), -0.2, 0.9)
    assert np.isnan(out[0]), "a missing observation must not become 0.0"


def test_fixed_range_rejects_invalid_bounds():
    with pytest.raises(ValueError):
        DataProcessor.normalize_fixed_range(np.array([0.5]), 0.9, 0.9)


# ── thresholds are coherent ───────────────────────────────────────────────

def test_baseline_threshold_is_below_peak_threshold():
    """signal_v1 had these inverted (0.30 baseline > 0.28 peak)."""
    assert P.CROP_CYCLE_MIN_BASELINE_CVI < P.CROP_CYCLE_MIN_PEAK_CVI


def test_thresholds_sit_between_bare_soil_and_full_canopy():
    vs = _vs_series([BARE, CANOPY])
    bare_vs, canopy_vs = vs[0], vs[1]
    assert bare_vs < P.CROP_CYCLE_MIN_BASELINE_CVI, (
        "bare soil must fall below the baseline gate, or harvest is never detected"
    )
    assert P.CROP_CYCLE_MIN_PEAK_CVI < canopy_vs, (
        "a full canopy must clear the peak gate"
    )


def test_relaxation_floor_stays_above_bare_soil():
    """Relaxation may miss a marginal crop; it must never invent one."""
    bare_vs = _vs_series([BARE])[0]
    assert P.CROP_CYCLE_RELAXED_PEAK_FLOOR > bare_vs


# ── provenance ────────────────────────────────────────────────────────────

def test_signal_version_is_stamped():
    cd = {"scenes": [_scene(BARE), _scene(CANOPY)], "dates": ["2023-06-02", "2023-06-12"]}
    out = SatelliteDataCollector._build_composite_signal(cd, field_area_ha=1.0)
    summary = out["signal_quality_summary"]
    assert summary["signal_version"] == P.SIGNAL_VERSION
    assert summary["normalization"] == "fixed_range"
    assert summary["backbone"] == "NDVI"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
