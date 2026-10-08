import json
from unittest.mock import MagicMock

import pandas as pd
import pytest

from evaluation import d1_sampling as d1
from evaluation.tests.conftest import DHASWADI


# --------------------------------------------------------------------------- allocation
def test_allocate_minimum_then_proportional():
    a = d1.allocate({"A": 1000, "B": 100, "C": 10}, {"A": 900, "B": 90, "C": 10}, 300, 30)
    assert a == {"A": 260, "B": 30, "C": 10}


def test_allocate_caps_at_stratum_size():
    a = d1.allocate({"A": 5, "B": 1000}, {"A": 500, "B": 500}, 100, 2)
    assert a == {"A": 5, "B": 95}


def test_allocate_minimums_exceed_total():
    a = d1.allocate({"A": 50, "B": 50, "C": 50}, {"A": 1, "B": 1, "C": 1}, 60, 30)
    assert a == {"A": 30, "B": 30, "C": 30}


def test_allocate_rounding_sums_to_total():
    a = d1.allocate({"A": 500, "B": 500, "C": 500}, {"A": 1, "B": 1, "C": 1}, 100, 10)
    assert sum(a.values()) == 100 and max(a.values()) - min(a.values()) <= 1


# --------------------------------------------------------------------------- sampling
def test_draw_sample_deterministic_and_stratified(synthetic_fc):
    s1, st1 = d1.draw_sample(synthetic_fc, n_total=100, strata_min=10, seed=3)
    s2, _ = d1.draw_sample(synthetic_fc, n_total=100, strata_min=10, seed=3)
    s3, _ = d1.draw_sample(synthetic_fc, n_total=100, strata_min=10, seed=4)
    assert s1["field_id"].tolist() == s2["field_id"].tolist()
    assert s1["field_id"].tolist() != s3["field_id"].tolist()
    assert s1["field_id"].is_unique
    strata = dict(zip(st1["stratum"], st1["n_sample"]))
    assert set(strata) == {"Cotton", "Soyabean", "Fallow", "Other/Abstained", "Monitoring-flagged"}
    assert strata["Monitoring-flagged"] == 8  # all of them (fewer than the minimum)
    assert sum(strata.values()) == 100
    assert (s1.groupby("stratum").size() == pd.Series(strata)).all()
    assert {"field_id", "stratum", "lat", "lon", "area_ha"} <= set(s1.columns)
    flagged = s1[s1["stratum"] == "Monitoring-flagged"]
    assert (flagged["map_class"] == "Cotton").all()


def test_draw_sample_uniform_selection(synthetic_fc):
    s, st = d1.draw_sample(synthetic_fc, n_total=60, strata_min=5, seed=1, selection="uniform")
    assert len(s) == st["n_sample"].sum()
    with pytest.raises(ValueError):
        d1.draw_sample(synthetic_fc, selection="bogus")


@pytest.mark.skipif(not DHASWADI.exists(), reason="Dhaswadi result not present")
def test_draw_sample_on_dhaswadi():
    s, st = d1.draw_sample(DHASWADI, n_total=300, strata_min=30, seed=11)
    assert len(s) == 300
    assert set(st["stratum"]) == {"Cotton", "Fallow", "Other/Abstained"}
    assert (st["n_sample"] >= 30).all()
    assert st["n_fields"].sum() == 2360


# --------------------------------------------------------------------------- blind sheet
def test_interpretation_sheet_is_blind(synthetic_fc):
    s, _ = d1.draw_sample(synthetic_fc, n_total=80, strata_min=5, seed=2)
    sheet, key = d1.interpretation_sheet(s, seed=5)
    assert list(sheet.columns) == d1.SHEET_COLUMNS
    for col in sheet.columns:
        assert col not in d1.FORBIDDEN_SHEET_COLUMNS
    assert (sheet["label"] == "").all() and (sheet["confidence"] == "").all()
    assert sheet["sample_id"].is_unique and len(sheet) == len(s)
    # sample ids do not follow field_id or stratum order
    fids = key["field_id"].astype(int).tolist()
    assert fids != sorted(fids)
    assert key["stratum"].tolist() != sorted(key["stratum"].tolist())
    assert {"sample_id", "field_id", "stratum", "map_class"} <= set(key.columns)
    # key and sheet line up by sample id and location
    m = key.merge(sheet, on="sample_id")
    orig = s.set_index("field_id")
    for _, r in m.head(10).iterrows():
        assert orig.loc[r["field_id"], "lat"] == pytest.approx(r["lat"])
    with pytest.raises(ValueError):
        d1.assert_blind(sheet.assign(crop="Cotton"))
    with pytest.raises(ValueError):
        d1.assert_blind(sheet.assign(p_top1=0.9))


# --------------------------------------------------------------------------- chips
def test_months_between():
    m = d1.months_between("2026-05-15", "2026-10-31")
    assert [x[0] for x in m] == ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
    assert m[0][1] == "2026-05-01" and m[-1][2] == "2026-11-01"


def test_chip_box_at_least_600m():
    w, s, e, n = d1.chip_box(18.8, 76.8)
    assert (n - s) * 111_320 == pytest.approx(600, rel=1e-6)


class FakeSource:
    stretch = d1.STRETCH
    cs_threshold = 0.6

    def __init__(self):
        self.calls = []

    def month_counts(self, box, months):
        return [3] * (len(months) - 1) + [0]

    def thumb_url(self, geom, box, start, end, kind, dimensions=256):
        self.calls.append((kind, start))
        return f"https://example.invalid/{kind}/{start}"


PNG = b"\x89PNG\r\n\x1a\nfake"


def test_make_chips_with_fake_source(tmp_path, synthetic_fc):
    s, _ = d1.draw_sample(synthetic_fc, n_total=10, strata_min=2, seed=2)
    sheet, key = d1.interpretation_sheet(s)
    chip_in = key.merge(sheet[["sample_id", "lat", "lon"]], on="sample_id").head(2)
    src = FakeSource()
    rec = d1.make_chips(chip_in, tmp_path, "2026-05-01", "2026-07-31", source=src,
                        fetch=lambda url: PNG)
    sid = chip_in["sample_id"].iloc[0]
    d = tmp_path / sid
    assert (d / "tc_2026-05.png").read_bytes() == PNG
    assert (d / "fc_2026-06.png").exists()
    assert not (d / "tc_2026-07.png").exists()  # zero scenes -> skipped
    assert set(rec["status"]) == {"ok", "no_scene"}
    html = (d / "contact_sheet.html").read_text(encoding="utf-8")
    assert "2026-05" in html and "tc_2026-05.png" in html and "no Sentinel-2 scene" in html
    for leak in ("Cotton", "Soyabean", "Fallow", "Abstained", "stratum", str(chip_in["field_id"].iloc[0]) + "<"):
        assert leak not in html
    assert (tmp_path / "index.html").exists()
    man = json.loads((tmp_path / "manifest.json").read_text())
    assert man["cloud_mask"]["threshold"] == 0.6 and man["stretch"]["fc"]["bands"] == ["B8", "B4", "B3"]
    # cached on rerun
    rec2 = d1.make_chips(chip_in, tmp_path, "2026-05-01", "2026-07-31", source=src,
                         fetch=lambda url: (_ for _ in ()).throw(RuntimeError("no network")))
    assert set(rec2["status"]) == {"cached", "no_scene"}


def test_make_chips_records_fetch_errors(tmp_path, synthetic_fc):
    s, _ = d1.draw_sample(synthetic_fc, n_total=10, strata_min=2, seed=2)
    def boom(url):
        raise RuntimeError("HTTP 500")
    rec = d1.make_chips(s.head(1), tmp_path, "2026-05-01", "2026-06-30", source=FakeSource(),
                        fetch=boom)
    assert set(rec["status"]) == {"error", "no_scene"}
    assert rec.loc[rec["status"] == "error", "error"].str.contains("HTTP 500").all()
    assert "fetch failed" in (tmp_path / s["field_id"].iloc[0] / "contact_sheet.html").read_text()


def test_ee_chip_source_with_mock_ee():
    ee = MagicMock()
    ee.List.return_value.getInfo.return_value = [4, 0]
    final = (ee.Image.constant.return_value.toByte.return_value.rename.return_value
             .blend.return_value.blend.return_value)
    final.getThumbURL.return_value = "https://earthengine.example/thumb.png"
    src = d1.EEChipSource(ee_module=ee, initialise=False)
    box = [76.8, 18.8, 76.81, 18.81]
    months = d1.months_between("2026-05-01", "2026-06-30")
    assert src.month_counts(box, months) == [4, 0]
    url = src.thumb_url({"type": "Point", "coordinates": [76.805, 18.805]}, box,
                        "2026-05-01", "2026-06-01", "fc")
    assert url == "https://earthengine.example/thumb.png"
    names = [c.args[0] for c in ee.ImageCollection.call_args_list if c.args]
    assert d1.S2_COLLECTION in names and d1.CS_COLLECTION in names
    vis_kwargs = (ee.ImageCollection.return_value.filterBounds.return_value.filterDate
                  .return_value.linkCollection.return_value.map.return_value.median
                  .return_value.visualize.call_args.kwargs)
    assert vis_kwargs["bands"] == ["B8", "B4", "B3"] and vis_kwargs["max"] == [5000, 2500, 2500]
    params = final.getThumbURL.call_args.args[0]
    assert params["format"] == "png"


# --------------------------------------------------------------------------- labels
def _key(n=10):
    return pd.DataFrame({"sample_id": [f"D1-{i:04d}" for i in range(1, n + 1)],
                         "field_id": [str(100 + i) for i in range(n)],
                         "stratum": ["Cotton"] * 5 + ["Soyabean"] * 5,
                         "map_class": ["Cotton"] * 5 + ["Soyabean"] * 5})


def _sheet(labels, conf="high"):
    return pd.DataFrame({"sample_id": [f"D1-{i:04d}" for i in range(1, len(labels) + 1)],
                         "label": labels, "confidence": [conf] * len(labels), "notes": ""})


def test_import_labels_kappa_disagreements_adjudication():
    a = ["Cotton"] * 4 + ["soyabean"] * 3 + ["Fallow"] * 3
    b = ["cotton"] * 3 + ["Soyabean"] * 4 + ["Fallow"] * 2 + ["Cotton"]
    c = pd.DataFrame({"sample_id": ["D1-0004"], "label": ["Soyabean"], "confidence": ["medium"],
                      "notes": ["harvested mid-Oct"]})
    summary, dis, ref = d1.import_labels(_sheet(a), _sheet(b, "medium"), _key(), c)
    # po=.8, pe=(4*4+3*4+3*2)/100=.34, kappa=.46/.66
    assert summary["kappa"]["kappa"] == pytest.approx(0.46 / 0.66)
    assert summary["usable_for_gating"] is False and "not used for gating" in summary["reason"]
    assert summary["n_disagreements"] == 2 and set(dis["sample_id"]) == {"D1-0004", "D1-0010"}
    r = ref.set_index("sample_id")
    assert r.loc["D1-0004", "reference_label"] == "Soyabean"
    assert r.loc["D1-0004", "resolution"] == "adjudicated"
    assert r.loc["D1-0010", "resolution"] == "unresolved"
    assert r.loc["D1-0001", "reference_confidence"] == "medium"  # min(high, medium)
    assert r.loc["D1-0005", "reference_label"] == "Soyabean"  # synonym normalised
    assert summary["reference_complete"] is False
    assert set(summary["per_class_kappa"]) == {"Cotton", "Soyabean", "Fallow"}


def test_import_labels_usable_when_kappa_high():
    a = ["Cotton"] * 5 + ["Soyabean"] * 5
    summary, dis, ref = d1.import_labels(_sheet(a), _sheet(a), _key())
    assert summary["kappa"]["kappa"] == pytest.approx(1.0)
    assert summary["usable_for_gating"] is True and dis.empty
    assert (ref["resolution"] == "agreed").all()


def test_import_labels_missing_and_bad_confidence():
    a = ["Cotton"] * 5 + ["Soyabean"] * 4 + [""]
    sa = _sheet(a)
    sa.loc[0, "confidence"] = "very sure"
    summary, _, ref = d1.import_labels(sa, _sheet(["Cotton"] * 5 + ["Soyabean"] * 5), _key())
    assert summary["n_both_labelled"] == 9 and summary["n_missing_label"] == 1
    assert any(i["issue"].startswith("confidence") for i in summary["issues"])
