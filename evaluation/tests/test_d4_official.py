import pandas as pd
import pytest

from evaluation import d4_official as d4

RESULTS = pd.DataFrame({
    "field_id": [str(i) for i in range(6)],
    "crop": ["Cotton", "Cotton", "Cotton", "Soyabean", "Soyabean", "Fallow"],
    "area_ha": [20.0, 20.0, 20.0, 20.0, 20.0, 20.0],
    "yield_t_ha": [0.5, 0.6, 0.7, 1.0, 1.2, None],
    "sowing_date": ["2026-06-10", "2026-06-20", "2026-07-05", "2026-06-15", "2026-06-25", None],
})


def test_reference_templates_are_header_only():
    for name, cols in d4.TEMPLATES.items():
        df = pd.read_csv(d4.REFERENCE_DIR / f"{name}.csv")
        assert list(df.columns) == cols


def test_empty_templates_give_not_available(tmp_path):
    d4.write_templates(tmp_path)
    cfg = {"crop_shares": {"level": "village", "name": "Dhaswadi", "season": "kharif", "year": 2026},
           "yields": [{"district": "Latur", "crop": "Cotton", "year": 2026}],
           "sowing": [{"state": "Maharashtra", "crop": "Cotton", "year": 2026}]}
    out = d4.run_all(RESULTS, cfg, ref_dir=tmp_path)
    assert out["crop_shares"]["status"] == d4.NA
    assert out["yields"][0]["status"] == d4.NA
    assert out["sowing"][0]["status"] == d4.NA
    assert d4.run_all(RESULTS, {}, ref_dir=tmp_path)["crop_shares"]["status"] == d4.NA


def test_missing_file_and_bad_columns(tmp_path):
    assert d4.load_table("district_yields", tmp_path)[1] == d4.NA
    _, st, msg = d4.load_table("district_yields", table=pd.DataFrame({"x": [1]}))
    assert st == "invalid" and "missing columns" in msg


def test_write_templates_never_overwrites(tmp_path):
    p = tmp_path / "district_yields.csv"
    p.write_text("district,crop,year,yield_t_ha,source\nX,Cotton,2020,0.5,user\n")
    d4.write_templates(tmp_path)
    assert "X,Cotton" in p.read_text()


def test_mapped_shares_exclude_fallow():
    m = d4.mapped_crop_shares(RESULTS)
    assert m["shares_pct"] == {"Cotton": pytest.approx(60.0), "Soyabean": pytest.approx(40.0)}
    assert m["unclassified_share_pct"] == pytest.approx(100 * 20 / 120)


def _official(cotton, soy, year=2026):
    return pd.DataFrame([
        {"level": "village", "name": "Dhaswadi", "state": "Maharashtra", "crop": "Cotton",
         "area_ha": cotton, "season": "Kharif", "year": year, "source": "user-supplied"},
        {"level": "village", "name": "Dhaswadi", "state": "Maharashtra", "crop": "Soyabean",
         "area_ha": soy, "season": "Kharif", "year": year, "source": "user-supplied"},
    ])


def test_crop_share_flag_beyond_15_pts():
    r = d4.compare_crop_shares(RESULTS, "village", "Dhaswadi", "kharif", 2026, official=_official(30, 70))
    assert r["status"] == "review"
    rows = {x["crop"]: x for x in r["comparisons"][0]["rows"]}
    assert rows["Cotton"]["diff_pts"] == pytest.approx(30.0) and rows["Cotton"]["flag"]
    assert rows["Soyabean"]["diff_pts"] == pytest.approx(-30.0)
    assert r["max_abs_diff_pts_major"] == pytest.approx(30.0)


def test_crop_share_consistent_and_year_filter():
    r = d4.compare_crop_shares(RESULTS, "village", "Dhaswadi", "kharif", 2026, official=_official(55, 45))
    assert r["status"] == "consistent" and r["max_abs_diff_pts_major"] == pytest.approx(5.0)
    r2 = d4.compare_crop_shares(RESULTS, "village", "Dhaswadi", "kharif", 2025, official=_official(55, 45))
    assert r2["status"] == d4.NA


def _yields(vals, crop="Cotton"):
    return pd.DataFrame([{"district": "Latur", "crop": crop, "year": y, "yield_t_ha": v,
                          "source": "user-supplied"} for y, v in vals])


def test_yield_within_and_outside_range():
    hist = [(2019, 9.9), (2021, 0.4), (2022, 0.55), (2023, 0.5), (2024, 0.65), (2025, 0.6), (2026, 9.9)]
    r = d4.village_yield_vs_district(RESULTS, "Latur", "Cotton", 2026, yields=_yields(hist))
    assert r["status"] == "within_range"
    assert r["district_years"] == [2021, 2022, 2023, 2024, 2025]  # 5 prior years, 2019/2026 excluded
    assert r["village_mean_t_ha"] == pytest.approx(0.6)
    assert r["district_min_t_ha"] == 0.4 and r["district_max_t_ha"] == 0.65
    r2 = d4.village_yield_vs_district(RESULTS, "Latur", "Cotton", 2026,
                                      yields=_yields([(2023, 1.0), (2024, 1.1), (2025, 1.2)]),
                                      yield_basis="district_anchored")
    assert r2["status"] == "below_range" and "caveat" in r2
    r3 = d4.village_yield_vs_district(RESULTS, "Latur", "Cotton", 2026,
                                      yields=_yields([(2024, 1.1), (2025, 1.2)]))
    assert r3["status"] == d4.NA


def test_sowing_cdf_vs_progress():
    prog = pd.DataFrame([
        {"state": "Maharashtra", "district": "", "crop": "Cotton", "week_ending": w,
         "cumulative_sown_pct": p, "source": "user-supplied"}
        for w, p in [("2026-06-14", 20), ("2026-06-28", 60), ("2026-07-12", 100)]])
    r = d4.sowing_vs_progress(RESULTS, "Maharashtra", "Cotton", 2026, progress=prog)
    w = {x["week_ending"]: x for x in r["weeks"]}
    assert w["2026-06-14"]["village_cdf_pct"] == pytest.approx(100 / 3)
    assert w["2026-06-28"]["village_cdf_pct"] == pytest.approx(200 / 3)
    assert w["2026-07-12"]["diff_pts"] == pytest.approx(0.0)
    assert r["max_abs_diff_pts"] == pytest.approx(100 / 3 - 20)
    assert r["status"] == "consistent" and r["official_level"] == "state"
    # normalisation to the final value (reported as % of normal area)
    prog2 = prog.assign(cumulative_sown_pct=[10, 30, 50])
    r2 = d4.sowing_vs_progress(RESULTS, "Maharashtra", "Cotton", 2026, progress=prog2)
    assert r2["weeks"][0]["official_pct"] == pytest.approx(20.0)
