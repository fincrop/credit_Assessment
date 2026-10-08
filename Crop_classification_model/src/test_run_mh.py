"""
Fast unit tests for the Marathwada retrain (src/run_mh.py, src/mh_metrics.py).

    cd Crop_classification_model
    python -m pytest -q src/test_run_mh.py
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from src import mh_metrics as M
from src._bootstrap import DATA

SPLIT = DATA / "splits" / "mh2023_split.json"


# ── Wilson interval ───────────────────────────────────────────────────────
def test_wilson_known_values():
    lo, hi = M.wilson_interval(170, 200)                 # p = 0.85, n = 200
    assert lo == pytest.approx(0.7949, abs=1e-3)
    assert hi == pytest.approx(0.8930, abs=1e-3)
    lo, hi = M.wilson_interval(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.2775, abs=1e-3)
    lo, hi = M.wilson_interval(10, 10)
    assert hi == pytest.approx(1.0) and lo == pytest.approx(0.7225, abs=1e-3)


def test_wilson_empty_and_invalid():
    assert all(np.isnan(M.wilson_interval(0, 0)))
    with pytest.raises(ValueError):
        M.wilson_interval(5, 3)


def test_per_class_report_counts_abstention_as_miss_not_fp():
    yt = ["Cotton", "Cotton", "Soyabean", "Tur"]
    yp = ["Cotton", None, "Cotton", None]
    r = M.per_class_report(yt, yp, ["Cotton", "Soyabean", "Tur"])
    assert r["Cotton"]["recall"] == 0.5 and r["Cotton"]["precision"] == 0.5
    assert r["Tur"]["recall"] == 0.0 and r["Tur"]["n_predicted"] == 0
    assert r["Tur"]["precision"] is None


# ── label shift ───────────────────────────────────────────────────────────
def test_label_shift_identity_when_priors_equal():
    p = np.array([[0.6, 0.3, 0.1], [0.2, 0.2, 0.6]])
    pr = {"A": 0.5, "B": 0.3, "C": 0.2}
    np.testing.assert_allclose(M.apply_label_shift(p, ["A", "B", "C"], pr, pr), p, atol=1e-9)


def test_label_shift_matches_bayes_rule():
    p = np.array([[0.5, 0.5]])
    out = M.apply_label_shift(p, ["A", "B"], {"A": 0.5, "B": 0.5}, {"A": 0.8, "B": 0.2})
    np.testing.assert_allclose(out, [[0.8, 0.2]], atol=1e-9)
    # rows stay normalised and order-preserving within the shift direction
    p = np.random.default_rng(0).dirichlet(np.ones(4), size=50)
    out = M.apply_label_shift(p, list("ABCD"), dict(zip("ABCD", [.25] * 4)),
                              {"A": .4, "B": .3})
    np.testing.assert_allclose(out.sum(1), 1.0, atol=1e-9)
    # unlisted classes share the residual 0.3 in proportion to train prior
    assert (out[:, 0] / p[:, 0]).mean() > (out[:, 2] / p[:, 2]).mean()


def test_load_district_shares_template_is_empty(tmp_path):
    f = tmp_path / "s.csv"
    f.write_text("district,state,crop,area_ha,year,source\n")
    assert M.load_district_shares(f).empty
    assert M.load_district_shares(tmp_path / "missing.csv").empty
    f.write_text("district,state,crop,area_ha,year,source\n"
                 "Latur,Maharashtra,Soybean,300,2022-23,x\nLatur,Maharashtra,Arhar/Tur,100,2022-23,x\n")
    pr = M.region_prior(M.load_district_shares(f))
    assert pr == {"Soyabean": 0.75, "Tur": 0.25}


# ── split hygiene ─────────────────────────────────────────────────────────
@pytest.mark.skipif(not SPLIT.exists(), reason="no frozen split")
def test_split_exclusion_never_leaks():
    split = json.loads(SPLIT.read_text(encoding="utf-8"))
    p = pd.read_parquet(DATA / "00_parcels_mh_full.parquet", columns=["geom_hash"])
    m = M.training_split_mask(p.geom_hash, split)
    tr = set(p.geom_hash[m])
    assert not tr & set(split["test_geom_hashes"])
    assert not tr & set(split["excluded_from_training"])
    assert set(split["test_geom_hashes"]) <= set(split["excluded_from_training"])
    assert len(tr) + len(set(split["excluded_from_training"]) & set(p.geom_hash)) == len(p)


def test_assert_no_leak_raises():
    from src.run_mh import _assert_no_leak
    split = {"excluded_from_training": ["a", "b"], "test_geom_hashes": ["b"]}
    _assert_no_leak(["c", "d"], split)
    with pytest.raises(AssertionError):
        _assert_no_leak(["c", "a"], split)


def test_trainset_aug_copies_follow_parent_rows():
    from src.run_mh import TrainSet
    F = pd.DataFrame({"geom_hash": ["g1", "g2", "g3"], "Crop_Name": ["A", "B", "A"],
                      "block_id": ["b1", "b1", "b2"], "qa_checked": [None, None, None],
                      "f": [1.0, 2.0, 3.0]})
    A = pd.DataFrame({"geom_hash": ["g1", "g3", "zz"], "Crop_Name": ["A", "A", "A"], "f": [1.5, 3.5, 9.0]})
    T = TrainSet(F, A, ["f"], ["A", "B"], 0.5)
    assert len(T.parent) == 2                        # copy of an unknown parcel dropped
    X, y, w = T.fit_arrays(np.array([1, 2]))         # g2, g3 -> only g3's copy joins
    assert sorted(X[:, 0].tolist()) == [2.0, 3.0, 3.5]
    assert w.mean() == pytest.approx(1.0)


# ── weights ───────────────────────────────────────────────────────────────
def test_qa_weights_only_unchecked_soy_halved():
    w = M.qa_weights([False, True, None, np.nan, False, pd.NA],
                     ["Soyabean", "Soyabean", "Soyabean", "Cotton", "Cotton", "Soyabean"])
    assert w.tolist() == [0.5, 1, 1, 1, 1, 1]


def test_class_weights_capped_and_balanced():
    y = np.array([0] * 900 + [1] * 90 + [2] * 10)
    blocks = np.arange(len(y)).astype(str)            # one row per block: no block effect
    w = M.mh_sample_weights(y, blocks, cap=3.0)
    per = {c: w[y == c][0] for c in range(3)}
    assert per[2] / per[1] == pytest.approx(1.0)       # both hit the 3x cap
    assert per[1] / per[0] == pytest.approx(3.0 / (1000 / (3 * 900)))
    assert w.mean() == pytest.approx(1.0)
    w2 = M.mh_sample_weights(y, blocks, qa_weight=np.where(np.arange(len(y)) == 0, 0.5, 1.0))
    assert w2[0] / w2[1] == pytest.approx(0.5)


def test_block_declustering():
    y = np.zeros(5, dtype=int)
    w = M.mh_sample_weights(y, np.array(["a", "a", "a", "a", "b"]))
    assert w[4] / w[0] == pytest.approx(2.0)          # 1/sqrt(1) vs 1/sqrt(4)


# ── abstain / ECE / OOD ───────────────────────────────────────────────────
def test_abstain_mask_matches_cropdetector_order():
    p = np.array([[0.30, 0.25, 0.45], [0.60, 0.35, 0.05], [0.9, 0.05, 0.05]])
    rule = {"p_min": 0.35, "gap_min": 0.10, "n_scenes_min": 8}
    m = M.abstain_mask(p, np.array([10, 10, 5]), rule)
    assert m.tolist() == [False, False, True]
    m = M.abstain_mask(np.array([[0.40, 0.35, 0.25]]), np.array([10]), rule)
    assert m.tolist() == [True]                       # gap 0.05 < 0.10


def test_ece_perfect_and_overconfident():
    p = np.array([[1.0, 0.0], [0.0, 1.0]])
    assert M.ece(p, np.array([0, 1])) == 0.0
    assert M.ece(np.array([[0.9, 0.1]] * 10), np.zeros(10, dtype=int) + 1) == pytest.approx(0.9)


def test_ood_flags_far_points():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(500, 6))
    ood = M.fit_ood(X, list("abcdef"))
    assert M.ood_score(ood, X).mean() == pytest.approx(1.0, rel=0.15)
    far = np.full((1, 6), 8.0)
    assert M.ood_score(ood, far)[0] > ood["threshold"]
