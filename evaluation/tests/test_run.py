"""End-to-end CLI test on a synthetic village (no Earth Engine)."""

import json

import pandas as pd
import pytest

from evaluation import run


def test_cli_end_to_end(tmp_path, synthetic_fc):
    res = tmp_path / "results.geojson"
    res.write_text(json.dumps(synthetic_fc))
    d1 = tmp_path / "d1"
    assert run.main(["sample", "--results", str(res), "--out", str(d1), "--n", "80", "--min", "10"]) == 0
    for f in ("sample.csv", "strata.csv", "map_areas.csv", "interpretation_sheet_A.csv",
              "interpretation_sheet_B.csv", "adjudication_sheet_C.csv",
              "KEY_do_not_share_with_interpreters.csv", "labels_allowed.txt"):
        assert (d1 / f).exists(), f
    sheet = pd.read_csv(d1 / "interpretation_sheet_A.csv", dtype=str, keep_default_na=False)
    assert list(sheet.columns) == ["sample_id", "lat", "lon", "chip_dir", "label", "confidence", "notes"]
    key = pd.read_csv(d1 / "KEY_do_not_share_with_interpreters.csv", dtype=str)

    # Simulated interpreters (test only): A copies the map class, B disagrees on 3 items.
    truth = dict(zip(key["sample_id"], key["map_class"].replace({"Abstained": "Other"})))
    a = sheet.assign(label=sheet["sample_id"].map(truth), confidence="high")
    b = a.copy()
    b.loc[b.index[:3], "label"] = "Fallow"
    a.to_csv(d1 / "A.csv", index=False)
    b.to_csv(d1 / "B.csv", index=False)
    c = pd.DataFrame({"sample_id": b["sample_id"][:3], "label": a["label"][:3],
                      "confidence": "medium", "notes": ""})
    c.to_csv(d1 / "C.csv", index=False)
    assert run.main(["kappa", "--a", str(d1 / "A.csv"), "--b", str(d1 / "B.csv"),
                     "--key", str(d1 / "KEY_do_not_share_with_interpreters.csv"),
                     "--c", str(d1 / "C.csv"), "--out", str(d1)]) == 0
    agree = json.loads((d1 / "d1_agreement.json").read_text())
    assert agree["n_disagreements"] <= 3 and agree["usable_for_gating"] is True

    assert run.main(["area", "--reference", str(d1 / "reference.csv"), "--strata", str(d1 / "strata.csv"),
                     "--map-areas", str(d1 / "map_areas.csv"), "--out", str(d1)]) == 0
    est = json.loads((d1 / "d1_area.json").read_text())
    assert est["kind"] == "area_estimate" and not est["strata_equal_map_classes"]
    assert est["total_area"] == pytest.approx(sum(f["properties"]["area_ha"] for f in synthetic_fc["features"]))

    # D4 with empty templates -> not available
    ref = tmp_path / "ref"
    assert run.main(["templates", "--ref-dir", str(ref)]) == 0
    assert run.main(["official", "--results", str(res), "--ref-dir", str(ref), "--level", "village",
                     "--name", "X", "--season", "kharif", "--year", "2026", "--out", str(tmp_path)]) == 0
    off = json.loads((tmp_path / "official_checks.json").read_text())
    assert off["crop_shares"]["status"] == "not_available"

    # D3 with one record
    recs = tmp_path / "records.csv"
    pd.DataFrame([{"record_id": "1", "field_id": "1", "boundary": "", "crop": "Cotton",
                   "season": "kharif", "year": 2026, "sowing_date": "2026-06-20", "harvest_date": "",
                   "yield_value": "", "yield_unit": "", "irrigated": "", "source": "test",
                   "consent": "true"},
                  {"record_id": "2", "field_id": "2", "boundary": "", "crop": "Cotton",
                   "season": "kharif", "year": 2026, "sowing_date": "", "harvest_date": "",
                   "yield_value": "", "yield_unit": "", "irrigated": "", "source": "test",
                   "consent": "false"}]).to_csv(recs, index=False)
    assert run.main(["client", "--records", str(recs), "--results", str(res), "--out", str(tmp_path / "d3")]) == 0
    cs = json.loads((tmp_path / "d3" / "client_summary.json").read_text())
    assert cs["n_matched"] == 1 and cs["n_rejected"] == 1

    # Gates
    assert run.main(["gates", str(d1 / "d1_agreement.json"), str(d1 / "d1_area.json"),
                     str(tmp_path / "official_checks.json"), str(tmp_path / "d3" / "client_summary.json"),
                     "--results", str(res), "--out", str(tmp_path / "gates")]) == 0
    rep = json.loads((tmp_path / "gates" / "gate_report.json").read_text())
    g = {x["id"]: x for x in rep["gates"]}
    assert g["reference_kappa"]["result"] == "PASS"
    assert g["classification_precision_recall"]["result"] == "NOT MEASURED"  # no held-out metrics
    assert g["area_vs_official"]["result"] == "NOT MEASURED"
    assert g["classification_abstain_rate"]["result"] in ("PASS", "FAIL")
    assert (tmp_path / "gates" / "gate_report.md").exists()


def test_cli_metrics_and_sowing(tmp_path):
    p = tmp_path / "heldout.csv"
    pd.DataFrame({"y_true": ["Cotton", "Cotton", "Soyabean", "Soyabean"],
                  "y_pred": ["Cotton", "Soyabean", "Soyabean", "Soyabean"],
                  "p_top1": [0.9, 0.6, 0.8, 0.95]}).to_csv(p, index=False)
    out = tmp_path / "m.json"
    assert run.main(["metrics", "--csv", str(p), "--out", str(out), "--threshold", "0.7"]) == 0
    m = json.loads(out.read_text())
    assert m["per_class"]["Cotton"]["recall"]["value"] == 0.5 and m["coverage"]["coverage"]["k"] == 3
    s = tmp_path / "sow.csv"
    pd.DataFrame({"field_id": ["1", "2"], "sowing_date": ["2026-06-10", "2026-07-10"],
                  "survey_date": ["2026-07-01", "2026-07-01"]}).to_csv(s, index=False)
    so = tmp_path / "s.json"
    assert run.main(["sowing", "--csv", str(s), "--source", "mh2023_cotton", "--out", str(so)]) == 0
    assert json.loads(so.read_text())["upper_bound"]["violation_rate"]["k"] == 1
