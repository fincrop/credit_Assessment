"""
Phenology metrics and peak-detection hygiene.

Two defects:

  * The double-logistic fit produces green-up (m_s) and senescence (m_a) rate
    parameters and they were DISCARDED — _phenometrics_from_fit returned only
    sos/pos/eos/base/amp. Length of season was never named, and the AUC on the
    cycle is computed over the WALKED window on raw NDVI without subtracting
    the soil baseline, so it carries the background with it.

  * Peak detection had NO prominence criterion: a bare local maximum with a
    0.005 tolerance, so two noise wiggles on a flat signal both qualified as
    crop peaks. A config key for this existed and was never read.

These tests call _phenometrics_from_fit directly, so they do not depend on
scipy's curve_fit (see PipelineConfig.PHENO_FIT_ENABLED).
"""

from __future__ import annotations

import os

os.environ.setdefault("PHENO_FIT_DISABLE", "1")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from config import PipelineConfig as P  # noqa: E402
from crop_analysis.crop_cycle_detector import CropCycleDetector as D  # noqa: E402


def _params(base=0.15, amp=0.60, sos=40.0, m_s=0.10, eos=140.0, m_a=0.10):
    return {"base": base, "amp": amp, "sos": sos, "m_s": m_s, "eos": eos, "m_a": m_a}


def _metrics(params, t_lo=0.0, t_hi=200.0, amp_frac=0.20):
    return D._phenometrics_from_fit(params, t_lo, t_hi, amp_frac)


# ── the rates that were being thrown away ─────────────────────────────────

def test_greenup_and_senescence_rates_are_exported():
    m = _metrics(_params())
    for key in ("greenup_rate", "senescence_rate",
                "greenup_slope_param", "senescence_slope_param"):
        assert key in m, f"{key} missing — the fit computes it and drops it"


def test_faster_greenup_yields_a_higher_greenup_rate():
    slow = _metrics(_params(m_s=0.05))["greenup_rate"]
    fast = _metrics(_params(m_s=0.25))["greenup_rate"]
    assert fast > slow


def test_faster_senescence_yields_a_higher_senescence_rate():
    slow = _metrics(_params(m_a=0.05))["senescence_rate"]
    fast = _metrics(_params(m_a=0.25))["senescence_rate"]
    assert fast > slow


def test_rates_scale_with_amplitude():
    """A taller canopy established over the same days grew faster."""
    small = _metrics(_params(amp=0.30))["greenup_rate"]
    large = _metrics(_params(amp=0.60))["greenup_rate"]
    assert large > small


# ── length of season ──────────────────────────────────────────────────────

def test_length_of_season_is_named_and_consistent():
    m = _metrics(_params(sos=40.0, eos=140.0))
    assert "los_days" in m
    assert m["los_days"] == pytest.approx(m["eos_offset"] - m["sos_offset"], abs=1.0)


def test_a_longer_season_reports_a_longer_los():
    short = _metrics(_params(sos=40.0, eos=110.0))["los_days"]
    long_ = _metrics(_params(sos=40.0, eos=170.0))["los_days"]
    assert long_ > short


# ── baseline-subtracted integral ──────────────────────────────────────────

def test_small_integral_is_baseline_subtracted():
    """
    Raising the soil floor while holding the canopy amplitude constant must not
    increase the productive integral — that is the whole point of subtracting
    the baseline.
    """
    low_soil = _metrics(_params(base=0.10, amp=0.60))["small_integral"]
    high_soil = _metrics(_params(base=0.30, amp=0.60))["small_integral"]
    assert high_soil == pytest.approx(low_soil, rel=0.05), (
        "small_integral moved with the soil background"
    )


def test_small_integral_grows_with_canopy_amplitude():
    small = _metrics(_params(amp=0.30))["small_integral"]
    large = _metrics(_params(amp=0.70))["small_integral"]
    assert large > small


def test_small_integral_is_non_negative():
    assert _metrics(_params())["small_integral"] >= 0.0


# ── ordering sanity ───────────────────────────────────────────────────────

def test_phenology_dates_are_ordered():
    m = _metrics(_params())
    assert m["sos_offset"] < m["pos_offset"] < m["eos_offset"]


# ── prominence ────────────────────────────────────────────────────────────

def test_prominence_threshold_is_configured():
    assert hasattr(P, "CROP_CYCLE_MIN_PROMINENCE")
    assert P.CROP_CYCLE_MIN_PROMINENCE > 0


def test_noise_wiggles_on_a_plateau_do_not_qualify_as_peaks():
    """
    A high but flat signal with small ripples: every ripple is a local maximum
    above the peak gate, and without prominence each became a crop peak.
    """
    n = 60
    rng = np.random.default_rng(11)
    plateau = 0.70 + 0.02 * rng.standard_normal(n)
    dates = [__import__("datetime").datetime(2023, 1, 1)
             + __import__("datetime").timedelta(days=10 * i) for i in range(n)]

    cycles = D()._detect_cycles(
        plateau, plateau, plateau, plateau, plateau, dates, [], 10.0,
        low_cvi=P.CROP_CYCLE_MIN_BASELINE_CVI,
        peak_cvi=P.CROP_CYCLE_MIN_PEAK_CVI,
        min_rise=P.CROP_CYCLE_MIN_CVI_RISE,
        min_days=P.CROP_CYCLE_MIN_DURATION_DAYS,
        max_days=P.CROP_CYCLE_MAX_DURATION_DAYS,
        max_days_after_peak=P.CROP_CYCLE_MAX_DAYS_AFTER_PEAK,
        harvest_ndvi_low=0.36,
    )
    assert cycles == [], f"{len(cycles)} phantom cycle(s) from plateau noise"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
