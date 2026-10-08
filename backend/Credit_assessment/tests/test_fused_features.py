"""Fused optical + Sentinel-1 season features (cloud-robust classifier inputs)."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from crop_analysis import fused_features as ff

YEAR = 2026


class _LinearImputer:
    """Stand-in for the trained SAR->NDVI model: NDVI from the cross-ratio."""

    def predict(self, X):
        cr = X[:, 2]
        return np.clip(0.15 + (cr + 10.5) * 0.2, 0.05, 0.9)


IMP = ff.SarNdviImputer(_LinearImputer(), 0.09, 0.88)


def _series(cloud_from=None, cloud_to=None, optical=True):
    s1, refl = [], []
    d = date(YEAR, 5, 5)
    while d <= date(YEAR, 12, 25):
        t = (d - date(YEAR, 6, 20)).days
        ndvi = 0.15 if t < 10 else min(0.8, 0.15 + 0.012 * (t - 10))
        cr = -10.5 + (ndvi - 0.15) / 0.2
        s1.append({"date": d.isoformat(), "VV": -12.0 + 0.5 * (ndvi > 0.3), "VH": -12.0 + cr})
        cloudy = cloud_from and cloud_from <= d <= cloud_to
        if optical and not cloudy:
            red = 0.08
            nir = red * (1 + ndvi) / (1 - ndvi)
            refl.append({"date": d.isoformat(), "B4": red, "B8": nir, "B5": (red + nir) / 2.2,
                         "B11": 0.3 - 0.2 * ndvi})
        d += timedelta(days=10)
    return s1, refl


def test_steps_after_as_of_are_missing():
    s1, refl = _series()
    f, _ = ff.build(s1, refl, YEAR, IMP, as_of=date(YEAR, 8, 1))
    steps = ff.grid_dates(YEAR)
    seen = [k for k, d in enumerate(steps) if d <= date(YEAR, 8, 1)]
    assert np.isfinite(f[f"ndvi_f_{seen[-1]:02d}"])
    assert np.isnan(f[f"ndvi_f_{seen[-1] + 1:02d}"])
    assert f["season_seen_steps"] == len(seen)


def test_monsoon_gap_is_filled_from_radar():
    s1, refl = _series(cloud_from=date(YEAR, 6, 25), cloud_to=date(YEAR, 8, 20))
    f, cur = ff.build(s1, refl, YEAR, IMP, as_of=date(YEAR, 10, 1))
    assert len(cur.imputed_days) >= 4
    k = next(i for i, d in enumerate(ff.grid_dates(YEAR)) if d >= date(YEAR, 8, 1))
    # Levels are offsets from the field's pre-season floor (0.15 here).
    assert np.isfinite(f[f"ndvi_f_{k:02d}"]) and f[f"ndvi_f_{k:02d}"] > 0.25
    assert f[f"obs_{k:02d}"] == 0.0          # flagged as not optically observed
    assert 0 < f["frac_observed"] < 1


def test_radar_only_field_still_gets_a_curve():
    s1, _ = _series(optical=False)
    f, cur = ff.build(s1, [], YEAR, IMP, as_of=date(YEAR, 10, 1))
    assert ff.has_any_data(f)
    assert f["n_opt"] == 0 and f["n_sar"] > 10
    assert np.isfinite(f["ndvi_max"]) and f["ndvi_max"] > 0.5


def test_levels_are_relative_to_the_field_floor():
    s1, refl = _series()
    f, _ = ff.build(s1, refl, YEAR, IMP, as_of=date(YEAR, 10, 1))
    # A uniform offset (a brighter background, a tree-lined bund) leaves the
    # model inputs unchanged.
    s1b = [{**r, "VH": r["VH"] + 1.5, "VV": r["VV"] + 1.5} for r in s1]
    fb, _ = ff.build(s1b, refl, YEAR, IMP, as_of=date(YEAR, 10, 1))
    for k in (3, 8, 12):
        assert abs(f[f"vh_{k:02d}"] - fb[f"vh_{k:02d}"]) < 1e-6
    assert "ndvi_max" not in ff.feature_names() and "ndvi_min_pre" not in ff.feature_names()
    assert f["ndvi_max"] > 0.5                    # still absolute in the dict (fallow test)


def test_no_data_at_all():
    f, _ = ff.build([], [], YEAR, IMP, as_of=date(YEAR, 10, 1))
    assert not ff.has_any_data(f)


def test_feature_vector_is_complete_and_ordered():
    s1, refl = _series()
    f, _ = ff.build(s1, refl, YEAR, IMP)
    names = ff.feature_names()
    assert set(names) <= set(f)
    assert len(names) == len(set(names)) == 5 * ff.N_STEPS + 14 + 3 * ff.N_ALIGNED + 7
