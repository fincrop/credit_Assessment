"""Olofsson et al. (2014) worked example + hand-computed synthetic cases."""

import math

import pandas as pd
import pytest

from evaluation import area

Z = 1.96

# Olofsson et al. 2014, section 5 / Tables 8-9 (Landsat pixels of 0.09 ha).
CLS = ["deforestation", "forest_gain", "stable_forest", "stable_nonforest"]
COUNTS = pd.DataFrame([[66, 0, 5, 4],
                       [0, 55, 8, 12],
                       [1, 0, 153, 11],
                       [2, 1, 9, 313]], index=CLS, columns=CLS)
PIXELS = {"deforestation": 200_000, "forest_gain": 150_000,
          "stable_forest": 3_200_000, "stable_nonforest": 6_450_000}
AREAS = {k: v * 0.09 for k, v in PIXELS.items()}


@pytest.fixture(scope="module")
def olofsson():
    return area.from_counts(COUNTS, AREAS, z=Z)


def test_olofsson_published_areas(olofsson):
    # Published bias-adjusted areas (ha) and 95% CI half-widths
    pub = {"deforestation": (21158, 6158), "forest_gain": (11686, 3756),
           "stable_forest": (285770, 15510), "stable_nonforest": (581386, 16282)}
    for k, (a, ci) in pub.items():
        r = olofsson["per_class"][k]["area"]
        assert r["value"] == pytest.approx(a, abs=1.0), k
        assert r["half_width"] == pytest.approx(ci, abs=1.0), k


def test_olofsson_published_accuracies(olofsson):
    ua = {"deforestation": (0.88, 0.07), "forest_gain": (0.73, 0.10),
          "stable_forest": (0.93, 0.04), "stable_nonforest": (0.96, 0.02)}
    pa = {"deforestation": 0.75, "forest_gain": 0.85, "stable_forest": 0.93,
          "stable_nonforest": 0.96}
    for k, (v, ci) in ua.items():
        r = olofsson["per_class"][k]["users_accuracy"]
        assert round(r["value"], 2) == v and round(r["half_width"], 2) == ci, k
    for k, v in pa.items():
        assert round(olofsson["per_class"][k]["producers_accuracy"]["value"], 2) == v, k
    oa = olofsson["overall_accuracy"]
    assert round(oa["value"], 2) == 0.95 and round(oa["half_width"], 2) == 0.02


def _olofsson_eq7(counts, n_area, j):
    """Independent implementation of Olofsson et al. 2014 eq. 7."""
    cls = list(counts.index)
    n_i = counts.sum(axis=1)
    N = {c: n_area[c] for c in cls}
    Nj = sum(N[i] / n_i[i] * counts.loc[i, j] for i in cls)
    P = (N[j] / n_i[j] * counts.loc[j, j]) / Nj
    U = counts.loc[j, j] / n_i[j]
    t1 = N[j] ** 2 * (1 - P) ** 2 * U * (1 - U) / (n_i[j] - 1)
    t2 = sum(N[i] ** 2 * (counts.loc[i, j] / n_i[i]) * (1 - counts.loc[i, j] / n_i[i]) / (n_i[i] - 1)
             for i in cls if i != j)
    return P, math.sqrt((t1 + P * P * t2) / Nj ** 2)


def test_producers_accuracy_se_matches_eq7(olofsson):
    for j in CLS:
        P, se = _olofsson_eq7(COUNTS, PIXELS, j)
        r = olofsson["per_class"][j]["producers_accuracy"]
        assert r["value"] == pytest.approx(P, rel=1e-9)
        assert r["se"] == pytest.approx(se, rel=1e-9)


def test_error_matrix_proportions_sum_to_one(olofsson):
    tot = sum(sum(r.values()) for r in olofsson["error_matrix_proportions"].values())
    assert tot == pytest.approx(1.0)
    assert olofsson["error_matrix_proportions"]["deforestation"]["deforestation"] == pytest.approx(0.02 * 66 / 75)


def _synthetic():
    rows = ([("A", "A")] * 3 + [("A", "B")] + [("B", "B"), ("B", "A")])
    return pd.DataFrame([{"map_label": m, "reference_label": r, "stratum": m} for m, r in rows])


def test_synthetic_hand_computed():
    r = area.stratified_estimate(_synthetic(), {"A": 80.0, "B": 20.0}, z=Z)
    pa, pb = r["per_class"]["A"], r["per_class"]["B"]
    assert pa["area"]["value"] == pytest.approx(70.0)
    assert pb["area"]["value"] == pytest.approx(30.0)
    assert pa["area"]["se"] == pytest.approx(100 * math.sqrt(0.05))
    assert pa["users_accuracy"]["value"] == pytest.approx(0.75)
    assert pa["users_accuracy"]["se"] == pytest.approx(0.25)
    assert pb["users_accuracy"]["se"] == pytest.approx(0.5)
    assert pa["producers_accuracy"]["value"] == pytest.approx(0.6 / 0.7)
    assert pa["producers_accuracy"]["se"] == pytest.approx(math.sqrt(81.6326530612 / 4900), rel=1e-6)
    assert pb["producers_accuracy"]["value"] == pytest.approx(1 / 3)
    assert r["overall_accuracy"]["value"] == pytest.approx(0.7)
    assert r["overall_accuracy"]["se"] == pytest.approx(math.sqrt(0.05))
    assert pa["area"]["map_area"] == 80.0
    assert pa["area"]["map_minus_estimate_pct"] == pytest.approx(100 * (80 - 70) / 70)


def test_strata_different_from_map_classes():
    # S1 (all mapped A): ref A share .75 ; S2 (flagged, mixed map): ref A share .5
    rows = [("S1", "A", "A"), ("S1", "A", "A"), ("S1", "A", "A"), ("S1", "A", "B"),
            ("S2", "A", "A"), ("S2", "B", "B"), ("S2", "B", "B"), ("S2", "B", "A")]
    df = pd.DataFrame(rows, columns=["stratum", "map_label", "reference_label"])
    r = area.stratified_estimate(df, {"S1": 50.0, "S2": 50.0}, map_areas={"A": 62.5, "B": 37.5})
    assert not r["strata_equal_map_classes"]
    # area proportion of A = .5*.75 + .5*.5
    assert r["per_class"]["A"]["area_proportion"]["value"] == pytest.approx(0.625)
    # UA_A = (.5*.75 + .5*.25) / (.5*1 + .5*.25)
    assert r["per_class"]["A"]["users_accuracy"]["value"] == pytest.approx(0.8)
    assert r["per_class"]["A"]["area"]["map_area"] == 62.5


def test_errors_for_missing_strata():
    with pytest.raises(ValueError):
        area.stratified_estimate(_synthetic(), {"A": 80.0, "B": 20.0, "C": 5.0})
    with pytest.raises(ValueError):
        area.stratified_estimate(_synthetic(), {"A": 80.0})


def test_summary_table(olofsson):
    t = area.summary_table(olofsson)
    assert len(t) == 4 and "estimated_area" in t.columns
