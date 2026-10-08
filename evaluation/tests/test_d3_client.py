import json

import pandas as pd
import pytest
from shapely.geometry import box

from evaluation import d3_client as d3
from evaluation.tests.conftest import DHASWADI_ANALYSIS, square

ACRE = 0.40468564224


def test_unit_conversion():
    assert d3.to_t_ha(10, "q_acre") == pytest.approx(10 * 0.1 / ACRE)  # 2.4711 t/ha
    assert d3.to_t_ha(10, "q_acre") == pytest.approx(2.4710538, rel=1e-6)
    assert d3.to_t_ha(400, "kg_acre") == pytest.approx(0.4 / ACRE)
    assert d3.to_t_ha(20, "q_ha") == pytest.approx(2.0)
    assert d3.to_t_ha(1.7, "t_ha") == pytest.approx(1.7)


def _rec(**kw):
    r = {"record_id": "r1", "field_id": "2", "boundary": "", "crop": "Cotton", "season": "kharif",
         "year": "2026", "sowing_date": "2026-06-20", "harvest_date": "", "yield_value": "8",
         "yield_unit": "q_acre", "irrigated": "no", "source": "bank branch", "consent": "true"}
    r.update(kw)
    return r


def test_validation_accepts_good_record_and_normalises():
    clean, errors = d3.validate_records([_rec(crop="kapas")])
    assert errors == []
    row = clean.iloc[0]
    assert row["crop"] == "Cotton" and not row["irrigated"]
    assert row["yield_t_ha"] == pytest.approx(0.8 / ACRE)


@pytest.mark.parametrize("kw,col", [
    ({"consent": "false"}, "consent"),
    ({"consent": ""}, "consent"),
    ({"field_id": "", "boundary": ""}, "field_id"),
    ({"yield_unit": "bags_acre"}, "yield_unit"),
    ({"yield_unit": ""}, "yield_unit"),
    ({"yield_value": "-3"}, "yield_value"),
    ({"season": "monsoon"}, "season"),
    ({"year": "26"}, "year"),
    ({"sowing_date": "20/06/2026"}, "sowing_date"),
    ({"harvest_date": "2026-06-01"}, "harvest_date"),
    ({"irrigated": "sometimes"}, "irrigated"),
    ({"source": ""}, "source"),
    ({"boundary": "POLYGON((bad", "field_id": ""}, "boundary"),
])
def test_validation_errors(kw, col):
    clean, errors = d3.validate_records([_rec(**kw)])
    assert clean.empty
    assert col in {e["column"] for e in errors}
    assert all(e["message"] for e in errors)


def test_json_schema_is_consistent():
    jsonschema = pytest.importorskip("jsonschema")
    good = {"field_id": "2", "crop": "Cotton", "season": "kharif", "year": 2026,
            "yield_value": 8, "yield_unit": "q_acre", "source": "x", "consent": True}
    jsonschema.validate(good, d3.JSON_SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**good, "consent": False}, d3.JSON_SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        bad = dict(good)
        bad.pop("field_id")
        jsonschema.validate(bad, d3.JSON_SCHEMA)


def _results():
    b1 = box(76.800, 18.800, 76.801, 18.801)
    b2 = box(76.801, 18.800, 76.802, 18.801)
    feats = [
        {"type": "Feature", "geometry": b1.__geo_interface__,
         "properties": {"field_id": "1", "crop": "Cotton", "sowing_date": "2026-06-25",
                        "sowing_p10": "2026-06-15", "sowing_p90": "2026-07-05",
                        "yield_t_ha": 1.2, "yield_p10": 0.8, "yield_p90": 1.6, "area_ha": 1.17}},
        {"type": "Feature", "geometry": b2.__geo_interface__,
         "properties": {"field_id": "2", "crop": "Soyabean", "sowing_date": "2026-07-20",
                        "yield_t_ha": 1.0}},
    ]
    return {"type": "FeatureCollection", "features": feats}


def test_match_by_id_and_by_overlap():
    shifted = box(76.8002, 18.800, 76.8012, 18.801)    # 80% overlap with field 1 -> IoU .667
    far = box(76.8008, 18.800, 76.8018, 18.801)        # largest overlap: field 2, IoU .8/1.2
    recs = [
        _rec(record_id="a", field_id="2", crop="Cotton", sowing_date="2026-07-01",
             yield_value="20", yield_unit="q_ha"),
        _rec(record_id="b", field_id="", boundary=shifted.wkt, sowing_date="2026-06-20",
             yield_value="10", yield_unit="q_ha"),
        _rec(record_id="c", field_id="999", boundary=json.dumps(far.__geo_interface__),
             crop="Soyabean", yield_value="", yield_unit=""),
        _rec(record_id="d", field_id="", boundary=box(77.5, 19.5, 77.501, 19.501).wkt),
    ]
    clean, errors = d3.validate_records(recs)
    assert errors == []
    comp, log = d3.match_records(clean, _results())
    c = comp.set_index("record_id")
    assert c.loc["a", "match_method"] == "field_id" and c.loc["a", "crop_match"] == False  # noqa: E712
    assert c.loc["a", "sowing_error_days"] == 19
    assert c.loc["a", "yield_pct_error"] == pytest.approx(-50.0)
    assert c.loc["b", "match_method"] == "spatial" and c.loc["b", "matched_field_id"] == "1"
    assert c.loc["b", "iou"] == pytest.approx(0.8 / 1.2, rel=1e-3)
    assert c.loc["b", "sowing_error_days"] == 5 and c.loc["b", "sowing_in_p10_p90"] == True  # noqa: E712
    assert c.loc["b", "yield_pct_error"] == pytest.approx(20.0)
    assert c.loc["b", "yield_in_p10_p90"] == True  # noqa: E712  0.8 <= 1.0 <= 1.6
    assert c.loc["c", "matched_field_id"] == "2"
    assert c.loc["d", "match_method"] == "unmatched"
    types = log.groupby("record_id")["type"].apply(set).to_dict()
    assert "crop_mismatch" in types["a"] and "sowing_error" in types["a"] and "yield_error" in types["a"]
    assert types["d"] == {"unmatched"}
    assert "b" not in types  # within tolerances


def test_low_iou_is_not_matched():
    sliver = box(76.8009, 18.8000, 76.8010, 18.8010)   # 10% of field 1, IoU 0.1
    clean, _ = d3.validate_records([_rec(record_id="s", field_id="", boundary=sliver.wkt)])
    comp, log = d3.match_records(clean, _results())
    assert comp.iloc[0]["match_method"] == "unmatched"
    assert "best IoU" in log.iloc[0]["detail"]


def test_summary_counts_and_yield_threshold():
    clean, _ = d3.validate_records([_rec(record_id=str(i), field_id="1", sowing_date="2026-06-20",
                                         yield_value="10", yield_unit="q_ha") for i in range(5)])
    comp, _ = d3.match_records(clean, _results())
    s = d3.summarise(comp)
    assert s["n_matched"] == 5 and s["sowing"]["median_abs_error_days"] == 5
    assert s["yield_by_crop"]["Cotton"]["enough_records"] is False
    assert s["yield_by_crop"]["Cotton"]["aggregate_pct_error"] == pytest.approx(20.0)
    assert "not a random sample" in s["note"]


@pytest.mark.skipif(not DHASWADI_ANALYSIS.exists(), reason="monitoring result not present")
def test_load_monitoring_analysis_json():
    df = d3.load_results(DHASWADI_ANALYSIS)
    assert len(df) > 100 and df["field_id"].notna().all()
    assert df["geometry"].notna().mean() > 0.9
    assert df["sowing_p10"].notna().any()
