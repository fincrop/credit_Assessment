"""
Tier-2 feature blocks (crop_analysis/extra_features.py) and the CropDetector
contract around them. All offline: the builders are pure functions.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from crop_analysis.extra_features import (
    EMB_DIM,
    S1_GRID,
    add_climatology,
    all_extra_feature_names,
    blocks_required,
    build_embedding_features,
    build_extra_features,
    build_reflectance_features,
    build_s1_features,
    build_weather_features,
    embedding_year,
    s1_feature_names,
    weather_feature_names,
)
from crop_analysis.crop_detector import (
    COMPATIBLE_EXTRACTOR_VERSIONS,
    EXTRACTOR_VERSION,
    extractor_feature_names,
)

SOW, HAR, PEAK = date(2023, 6, 20), date(2023, 10, 20), date(2023, 8, 25)


def _s1_series(flood: bool):
    out = []
    d = date(2023, 5, 1)
    while d < date(2023, 12, 1):
        vv = -9.0
        if flood and date(2023, 7, 1) <= d <= date(2023, 7, 31):
            vv = -18.0                          # transplanted-paddy flooding dip
        out.append({"date": d.isoformat(), "VV": vv, "VH": -16.0 + (d.month - 5) * 0.3})
        d += timedelta(days=10)
    return out


def test_names_are_unique_and_disjoint_from_tier1():
    names = all_extra_feature_names()
    assert len(names) == len(set(names))
    assert not set(names) & set(extractor_feature_names())


def test_blocks_required_detects_each_block():
    assert blocks_required(extractor_feature_names()) == []
    assert blocks_required(["S1VH_t01", "gdd_total"]) == ["s1", "weather"]
    assert blocks_required(["EMB00"]) == ["emb"]


def test_s1_flooding_signature():
    wet = build_s1_features(_s1_series(True), SOW, HAR)
    dry = build_s1_features(_s1_series(False), SOW, HAR)
    assert set(wet) == set(s1_feature_names())
    assert wet["S1VV_drop"] > 5.0 > dry["S1VV_drop"]
    assert all(np.isfinite(wet[f"S1VH_t{t + 1:02d}"]) for t in range(S1_GRID))


def test_s1_missing_is_nan_not_zero():
    f = build_s1_features([], SOW, HAR)
    assert f["S1_n_obs"] == 0
    assert np.isnan(f["S1VH_t01"]) and np.isnan(f["S1VV_drop"])


def test_reflectance_peak_uses_nearest_bin():
    series = []
    for k in range(15):
        d = SOW + timedelta(days=10 * k)
        nir = 0.45 if d == date(2023, 8, 29) else 0.25
        series.append({"date": d.isoformat(), **{b: 0.1 for b in
                       ("B2", "B3", "B4", "B5", "B6", "B7", "B8A", "B11", "B12")}, "B8": nir})
    f = build_reflectance_features(series, SOW, HAR, PEAK)
    assert f["RB8_peak"] == pytest.approx(0.45)


def _daily(years=(2019, 2020, 2021, 2022, 2023)):
    rows = []
    for y in years:
        d = date(y, 1, 1)
        while d.year == y:
            rows.append({"date": d, "T2M": 28.0, "T2M_MAX": 34.0, "T2M_MIN": 22.0,
                         "PRECTOTCORR": 5.0 if 6 <= d.month <= 9 else 0.0, "RH2M": 60.0})
            d += timedelta(days=1)
    return add_climatology(pd.DataFrame(rows))


def test_weather_thermal_time():
    f = build_weather_features(_daily(), SOW, HAR, PEAK)
    assert set(f) == set(weather_feature_names())
    days = (HAR - SOW).days + 1
    assert f["gdd_total"] == pytest.approx(18.0 * days, rel=1e-3)     # (34+22)/2 - 10
    assert f["rain_anomaly_ratio"] == pytest.approx(1.0, rel=1e-2)    # a normal year (leap-day DOY shift)
    assert 0.0 < f["gdd_peak_fraction"] < 1.0


def test_weather_unavailable_is_nan():
    f = build_weather_features(None, SOW, HAR, PEAK)
    assert all(np.isnan(v) for v in f.values())


def test_embedding_renormalised_and_year():
    f = build_embedding_features([0.5] * EMB_DIM)
    v = np.array(list(f.values()))
    assert np.linalg.norm(v) == pytest.approx(1.0)
    assert all(np.isnan(x) for x in build_embedding_features(None).values())
    # rabi crop peaking in January belongs to the January year
    assert embedding_year(date(2022, 11, 1), date(2023, 3, 1), date(2023, 1, 20)) == 2023


def test_build_extra_features_only_requested_blocks():
    f = build_extra_features(["s1"], SOW, HAR, PEAK, s1_series=_s1_series(True))
    assert set(f) == set(s1_feature_names())


def test_extractor_version_accepts_older_bundles():
    assert EXTRACTOR_VERSION == "tier2_v1"
    assert {"tier0_v1", "tier1_v1", "tier2_v1"} <= COMPATIBLE_EXTRACTOR_VERSIONS
