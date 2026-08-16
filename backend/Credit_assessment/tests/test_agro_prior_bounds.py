"""
Agro/eco prior bounds on the cycle-detection peak threshold.

REGRESSION. The prior's clamp was an ABSOLUTE [0.20, 0.35], written for
signal_v1's per-parcel rank scale. Under signal_v2's absolute scale 0.35 is
BARE SOIL, so any parcel with an eco profile had its configured 0.55 gate
silently dragged down to bare-soil level — re-creating the threshold inversion
signal_v2 removed.

Observed on real data before the fix:
    Soft agro prior -> peak_cvi 0.550 -> 0.350
    Found 0 crop cycle(s)
on a farm whose previous assessment scored 71.2.

The bound is now relative to the configured threshold, so it cannot go stale
the next time the scale changes.
"""

from __future__ import annotations

import pytest

from config import PipelineConfig as P
from crop_analysis.crop_cycle_detector import _soft_apply_agro_profile

CONFIGURED = P.CROP_CYCLE_MIN_PEAK_CVI
BARE_SOIL = P.CROP_CYCLE_MIN_BASELINE_CVI


def _apply(profile, peak=None):
    peak = CONFIGURED if peak is None else peak
    return _soft_apply_agro_profile(peak, 1.15, profile)


# ── the regression ────────────────────────────────────────────────────────

def test_prior_never_pushes_the_gate_to_bare_soil():
    """The exact failure: a real farm returning zero cycles."""
    eco = {"ndvi_threshold_delta": -0.05, "cycle_min_peak_floor": 0.12}
    peak, _, _ = _apply(eco)
    assert peak > BARE_SOIL, (
        f"prior produced peak_cvi {peak}, at or below bare soil {BARE_SOIL}"
    )


def test_even_an_extreme_prior_stays_above_bare_soil():
    for profile in (
        {"min_peak_cvi": 0.01},
        {"peak_cvi_floor": 0.0},
        {"ndvi_threshold_delta": -0.9},
        {"cycle_min_peak_floor": -1.0},
        {"min_peak_cvi": 0.0, "ndvi_threshold_delta": -0.5,
         "cycle_min_peak_floor": -1.0},
    ):
        peak, _, _ = _apply(profile)
        assert peak > BARE_SOIL, f"{profile} -> {peak}"


def test_threshold_ordering_is_preserved_under_any_prior():
    """low_cvi < peak_cvi must hold, or every sow/harvest walk terminates."""
    for delta in (-0.5, -0.1, 0.0, 0.1, 0.5):
        peak, _, _ = _apply({"ndvi_threshold_delta": delta})
        assert BARE_SOIL < peak


# ── the prior still does something ────────────────────────────────────────

def test_prior_still_nudges_the_threshold():
    """Bounded, not ignored — a regional signal should still matter."""
    eco = {"ndvi_threshold_delta": -0.05, "cycle_min_peak_floor": 0.12}
    peak, _, applied = _apply(eco)
    assert peak != CONFIGURED, "the prior had no effect at all"
    assert applied["peak_cvi_before"] == pytest.approx(CONFIGURED)


def test_adjustment_is_bounded_relative_to_the_configured_value():
    peak, _, applied = _apply({"min_peak_cvi": 0.01})
    lo, hi = applied["peak_cvi_bounds"]
    assert lo <= peak <= hi
    assert hi <= CONFIGURED * 1.25, "upward bound drifted too far"


def test_a_raising_prior_is_also_bounded():
    """Symmetry: a prior must not be able to make detection impossible either."""
    peak, _, _ = _apply({"min_peak_cvi": 0.99})
    assert peak <= CONFIGURED * 1.25


def test_bounds_scale_with_the_configured_threshold():
    """
    The whole point of making this relative: change the scale and the bound
    follows, instead of going stale.
    """
    _, _, low_cfg = _apply({"min_peak_cvi": 0.0}, peak=0.30)
    _, _, high_cfg = _apply({"min_peak_cvi": 0.0}, peak=0.80)
    assert high_cfg["peak_cvi_bounds"][1] > low_cfg["peak_cvi_bounds"][1]


def test_no_profile_is_a_no_op():
    peak, exp, applied = _apply(None)
    assert peak == CONFIGURED and applied == {}


def test_expected_cycle_rate_still_passes_through():
    _, exp, applied = _apply({"expected_cycles_per_year": 2.4})
    assert exp == pytest.approx(2.4)
    assert applied["expected_cycles_per_year"] == pytest.approx(2.4)


def test_ndvi_delta_is_scaled_onto_the_vs_scale():
    """
    The delta is on the raw NDVI scale but applied to a VS-scale threshold.
    Scaling by the NDVI span keeps the nudge proportionate instead of assuming
    the two scales are interchangeable.
    """
    _, _, applied = _apply({"ndvi_threshold_delta": -0.05})
    assert "ndvi_threshold_delta_scaled" in applied
    assert abs(applied["ndvi_threshold_delta_scaled"]) < abs(applied["ndvi_threshold_delta"])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
