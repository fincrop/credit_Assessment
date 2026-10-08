import json

import pandas as pd
import pytest

from evaluation import area, gates, metrics
from evaluation.gates import FAIL, NM, PASS


def _by_id(rep):
    return {g["id"]: g for g in rep["gates"]}


def test_nothing_measured_is_never_pass():
    rep = gates.evaluate({})
    assert rep["counts"][PASS] == 0 and rep["counts"][FAIL] == 0
    assert rep["counts"][NM] == len(gates.GATES)
    assert all(g["consequence"] == g["status_if_not_met"] for g in rep["gates"])
    assert "Classification" in rep["components_not_releasable"]


def _heldout(acc_cotton=0.95, n=200):
    yt, yp, conf = [], [], []
    for c in ("Cotton", "Soyabean", "Tur"):
        k = int(n * acc_cotton)
        yt += [c] * n
        yp += [c] * k + ["Other"] * (n - k)
        conf += [acc_cotton] * n
    return metrics.classification_report(yt, yp, confidence=conf, source="mh2023_heldout")


def _d1(kappa_ok=True, ua=0.95):
    n = 100
    rows = []
    for c in ("Cotton", "Soyabean"):
        k = int(n * ua)
        rows += [{"map_label": c, "reference_label": c, "stratum": c}] * k
        rows += [{"map_label": c, "reference_label": "Fallow", "stratum": c}] * (n - k)
    rows += [{"map_label": "Fallow", "reference_label": "Fallow", "stratum": "Fallow"}] * 50
    est = area.stratified_estimate(pd.DataFrame(rows), {"Cotton": 500.0, "Soyabean": 300.0, "Fallow": 200.0})
    ag = {"kind": "d1_agreement", "kappa": {"kappa": 0.8 if kappa_ok else 0.6, "n": 250,
                                            "interpretation": "substantial"},
          "usable_for_gating": kappa_ok, "reason": "kappa 0.6 < 0.7" if not kappa_ok else "ok"}
    return ag, est


def test_classification_gate_needs_both_sources():
    rep = gates.evaluate({"heldout": _heldout()})
    g = _by_id(rep)["classification_precision_recall"]
    assert g["result"] == NM
    assert g["value"]["mh2023_heldout"]["Cotton"]["status"] == PASS
    assert _by_id(rep)["classification_calibration"]["result"] == PASS  # ECE 0 by construction


def test_classification_gate_pass_with_both_and_fail_on_low_recall():
    ag, est = _d1()
    rep = gates.evaluate({"heldout": _heldout(), "d1_agreement": ag, "d1_area": est})
    g = _by_id(rep)
    assert g["classification_precision_recall"]["result"] == PASS
    assert g["reference_kappa"]["result"] == PASS
    rep2 = gates.evaluate({"heldout": _heldout(acc_cotton=0.80), "d1_agreement": ag, "d1_area": est})
    assert _by_id(rep2)["classification_precision_recall"]["result"] == FAIL


def test_low_kappa_blocks_d1_gates():
    ag, est = _d1(kappa_ok=False)
    rep = gates.evaluate({"heldout": _heldout(), "d1_agreement": ag, "d1_area": est})
    g = _by_id(rep)
    assert g["reference_kappa"]["result"] == FAIL
    assert g["area_vs_d1"]["result"] == NM and "not usable" in g["area_vs_d1"]["reason"]
    assert g["classification_precision_recall"]["result"] == NM


def test_area_vs_d1_gate():
    ag, est = _d1(ua=0.95)
    # map 500 vs estimate 500*.95 = 475 -> +5.3% (pass); Fallow excluded from the crop check
    g = _by_id(gates.evaluate({"d1_agreement": ag, "d1_area": est}))["area_vs_d1"]
    assert g["result"] == PASS and set(g["value"]) == {"Cotton", "Soyabean"}
    ag, est = _d1(ua=0.80)  # +25% -> fail
    assert _by_id(gates.evaluate({"d1_agreement": ag, "d1_area": est}))["area_vs_d1"]["result"] == FAIL


def test_calibration_and_abstain(tmp_path):
    h = _heldout()
    h["ece"] = 0.08
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": None, "properties": {"crop": "Cotton", "area_ha": 1}},
        {"type": "Feature", "geometry": None, "properties": {"crop": "Abstained", "area_ha": 1}},
        {"type": "Feature", "geometry": None, "properties": {"crop": "Soyabean", "status": "abstained", "area_ha": 2}},
        {"type": "Feature", "geometry": None, "properties": {"crop": "Fallow", "area_ha": 1}}]}
    ab = gates.abstain_rate(fc)
    assert ab["value"] == pytest.approx(0.5) and ab["area_weighted"] == pytest.approx(0.6)
    g = _by_id(gates.evaluate({"heldout": h, "abstain": ab}))
    assert g["classification_calibration"]["result"] == FAIL
    assert g["classification_abstain_rate"]["result"] == FAIL
    assert g["classification_abstain_rate"]["consequence"] == "Released; abstentions shown"


def test_official_sowing_client_external():
    official = {"kind": "official_checks", "crop_shares": {
        "status": "review", "max_abs_diff_pts_major": 40.0,
        "comparisons": [{"source": "user-supplied"}]}}
    sow = {"kind": "sowing_checks", "source": "mh2023_cotton",
           "upper_bound": {"violation_rate": {"value": 0.02, "k": 2, "n": 100}},
           "optical_radar": {"status": "measured", "agreement_rate": {"value": 0.7, "k": 70, "n": 100}},
           "client": {"status": "not_available"}}
    client = {"kind": "client_comparison", "sowing": {"n": 12, "median_abs_error_days": 5.0,
                                                      "p90_abs_error_days": 14.0},
              "yield_by_crop": {"Cotton": {"n": 12, "enough_records": False,
                                           "field_median_abs_pct_error": 10.0}}}
    ext = {"kind": "gate_values", "values": {"delineation_median_iou": {"value": 0.65, "source": "MH benchmark"},
                                             "raster_checks_pass_rate": 0.98}}
    rep = gates.evaluate(gates.load_inputs([official, sow, client, ext]))
    g = _by_id(rep)
    assert g["area_vs_official"]["result"] == FAIL
    assert g["sowing_upper_bound"]["result"] == PASS
    assert g["sowing_optical_radar"]["result"] == FAIL
    assert g["sowing_abs_error"]["result"] == PASS
    assert g["yield_error"]["result"] == NM and "30" in g["yield_error"]["reason"]
    assert g["delineation_iou"]["result"] == PASS
    assert g["raster_integrity"]["result"] == FAIL
    assert g["stress_plausibility"]["result"] == NM
    assert g["insufficient_evidence"]["result"] == NM


def test_yield_gate_with_enough_records():
    client = {"kind": "client_comparison", "yield_by_crop": {
        "Cotton": {"n": 35, "enough_records": True, "field_median_abs_pct_error": 20.0,
                   "aggregate_pct_error": 5.0},
        "Soyabean": {"n": 31, "enough_records": True, "field_median_abs_pct_error": 30.0,
                    "aggregate_pct_error": 5.0}}}
    g = _by_id(gates.evaluate({"client": client}))["yield_error"]
    assert g["result"] == FAIL and g["value"]["Cotton"]["status"] == PASS


def test_bad_input_becomes_not_measured():
    rep = gates.evaluate({"heldout": {"kind": "classification_metrics", "ece": "oops"}})
    assert _by_id(rep)["classification_calibration"]["result"] == NM


def test_report_files(tmp_path):
    rep = gates.evaluate({})
    jp, mp = gates.write_report(rep, tmp_path)
    md = mp.read_text(encoding="utf-8")
    assert "NOT MEASURED" in md and "Status if not met" in md and "Not released" in md
    assert json.loads(jp.read_text())["kind"] == "gate_report"
    assert md.count("\n| ") == len(gates.GATES) + 1


def test_load_inputs_roles(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"kind": "classification_metrics", "source": "mh2023_heldout", "ece": 0.01}))
    inp = gates.load_inputs([p, {"kind": "classification_metrics", "source": "d1_naive"}])
    assert "heldout" in inp and "d1_metrics" in inp
    with pytest.raises(ValueError):
        gates.load_inputs([{"kind": "mystery"}])
