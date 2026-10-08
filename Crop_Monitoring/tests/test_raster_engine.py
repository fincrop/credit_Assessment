"""End-to-end raster engine on a synthetic village (no Earth Engine).

14 cotton fields sown mid-June, 4 soybean fields LABELLED cotton (the Dhaswadi
failure), one perennial-green field, a June-July monsoon cloud gap, and radar
through the gap. Asserts the properties the audit found missing.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pytest

from src.models import WeatherDay
from src.raster import engine, reference
from src.raster.grid import field_masks, grid_for
from src.raster.stack import SceneStack

YEAR = 2026
AS_OF = date(2026, 10, 2)
LON0, LAT0 = 76.85, 18.80
SIDE = 0.0009                    # ~95 m fields: ~60 interior pixels


def _square(i: int) -> dict:
    x = LON0 + (i % 6) * (SIDE + 0.0002)
    y = LAT0 + (i // 6) * (SIDE + 0.0002)
    return {"type": "Polygon", "coordinates": [[[x, y], [x + SIDE, y], [x + SIDE, y + SIDE],
                                                [x, y + SIDE], [x, y]]]}


def _truth(kind: str, sow: date, d: date) -> float:
    if kind == "perennial":
        return 0.62 + 0.1 * np.sin((d - date(YEAR, 3, 1)).days / 60.0)
    c = reference.DEFAULT_CURVES[kind]
    das = (d - sow).days
    return float(c.base if das < 0 else c.value(np.array([das]))[0])


@pytest.fixture(scope="module")
def village(tmp_path_factory):
    rng = np.random.default_rng(7)
    fields, kinds, sows = [], {}, {}
    for i in range(20):
        if i == 19:
            kind, sow = "Soyabean", date(2026, 6, 22)      # crop unknown to classification
        elif i < 14:
            kind, sow = "Cotton", date(2026, 6, 14) + timedelta(days=int(rng.integers(-5, 8)))
        elif i < 18:
            kind, sow = "Soyabean", date(2026, 6, 20) + timedelta(days=int(rng.integers(-4, 5)))
        else:
            kind, sow = "perennial", date(2026, 3, 1)
        fid = f"f{i:02d}"
        kinds[fid], sows[fid] = kind, sow
        fields.append({"field_id": fid, "crop": "Unknown" if i == 19 else "Cotton",
                       "geometry": _square(i), "area_ha": 0.9,
                       "classification": {"status": "confirmed", "p_top1": 0.8, "margin": 0.5}})
    grid = grid_for([f["geometry"] for f in fields], pad_m=60)
    masks = field_masks(grid, fields)
    # Stressed cotton field: 20% lower canopy all season.
    stressed = "f03"

    s2_dates = [date(2026, 3, 3) + timedelta(days=5 * k) for k in range(0, 43)]
    s2_dates = [d for d in s2_dates if not (date(2026, 6, 22) <= d <= date(2026, 7, 28))]
    H, W = grid.shape
    bands = {b: np.full((len(s2_dates), H, W), np.nan, np.float32)
             for b in ("B2", "B3", "B4", "B5", "B6", "B7", "B8", "B11", "B12")}
    for t, d in enumerate(s2_dates):
        ndvi = np.full((H, W), 0.15, np.float32)          # bare surroundings
        for fid, m in masks.items():
            v = _truth(kinds[fid], sows[fid], d)
            if fid == stressed and kinds[fid] == "Cotton":
                v = 0.15 + (v - 0.15) * 0.75
            ndvi[m.full] = v
        ndvi = ndvi + rng.normal(0, 0.01, ndvi.shape).astype(np.float32)
        red = np.full((H, W), 0.08, np.float32)
        nir = red * (1 + ndvi) / (1 - ndvi)
        bands["B4"][t], bands["B8"][t] = red, nir
        bands["B2"][t], bands["B3"][t] = 0.05, 0.07
        bands["B5"][t] = (red + nir) / 2.2
        bands["B6"][t] = bands["B7"][t] = nir * 0.95
        bands["B11"][t] = 0.30 - 0.2 * ndvi
        bands["B12"][t] = 0.22 - 0.15 * ndvi
        if rng.random() < 0.15:                            # a partly cloudy scene
            for b in bands:
                bands[b][t, : H // 2] = np.nan
    s2 = SceneStack("s2", grid, s2_dates, bands, [{} for _ in s2_dates])

    s1_dates = [date(2026, 4, 2) + timedelta(days=6 * k) for k in range(30)]
    # Measured Marathwada signatures (reference/sar_reference.json): bare soil
    # VH ~ -22.5 dB with cross-ratio ~ -10.5 dB; full canopy VH ~ -16, CR ~ -7.
    vh = np.full((len(s1_dates), H, W), 10 ** (-22.5 / 10), np.float32)
    vv = np.full((len(s1_dates), H, W), 10 ** (-12.0 / 10), np.float32)
    for t, d in enumerate(s1_dates):
        for fid, m in masks.items():
            v = _truth(kinds[fid], sows[fid], d)
            # Canopy signature (VH >= -19, CR >= -9) switches on near NDVI 0.37,
            # as measured (sowing.RADAR_CANOPY_NDVI).
            g = min(max(v - 0.18, 0) / 0.6, 1.0)
            vh_db, cr_db = -22.5 + 11.0 * g, -10.5 + 4.7 * g
            vh[t][m.full] = 10 ** (vh_db / 10)
            vv[t][m.full] = 10 ** ((vh_db - cr_db) / 10)
    s1 = SceneStack("s1", grid, s1_dates, {"VV": vv, "VH": vh},
                    [{"pass": "DESCENDING", "relative_orbit": 63} for _ in s1_dates])

    weather = []
    d = date(2026, 3, 1)
    while d <= AS_OF:
        rain = 0.0
        if d >= date(2026, 6, 10) and (d - date(2026, 6, 10)).days % 2 == 0 and d <= date(2026, 9, 20):
            rain = 14.0
        weather.append(WeatherDay(date=d, rain_p50=rain, rain_p10=rain, rain_p90=rain,
                                  tmean=29.0, tmax=34.0, tmin=23.0, et0=4.5))
        d += timedelta(days=1)

    out = tmp_path_factory.mktemp("rasters")
    req = engine.VillageRequest(fields=fields, year=YEAR, as_of=AS_OF, out_dir=out, name="synthetic",
                                curves=dict(reference.DEFAULT_CURVES))
    doc = engine.run(req, engine.Inputs(grid, s2, None, s1, weather), progress=lambda m: None)
    return doc, kinds, sows, out, stressed


def _rec(doc, fid):
    return next(r for r in doc["records"] if r["field_id"] == fid)


def test_no_rainfed_field_is_sown_in_april(village):
    doc, kinds, sows, *_ = village
    for fid, kind in kinds.items():
        if kind == "perennial":
            continue
        s = _rec(doc, fid)["sowing"]
        if s["date"]:
            assert date.fromisoformat(s["date"]) >= date(2026, 6, 1), (fid, s)


def test_sowing_dates_are_close_to_truth(village):
    doc, kinds, sows, *_ = village
    errs = []
    for fid, kind in kinds.items():
        s = _rec(doc, fid)["sowing"]
        if kind != "perennial" and s["date"]:
            errs.append(abs((date.fromisoformat(s["date"]) - sows[fid]).days))
    assert len(errs) >= 14
    assert np.median(errs) <= 7
    assert np.percentile(errs, 90) <= 15


def test_soybean_labelled_cotton_is_flagged_and_its_yield_withheld(village):
    doc, kinds, *_ = village
    soy = [fid for fid, k in kinds.items() if k == "Soyabean"]
    flagged = [fid for fid in soy if _rec(doc, fid)["status"] == "phenology_disagrees"]
    assert len(flagged) >= 3
    for fid in flagged:
        assert _rec(doc, fid)["yield"]["basis"] == "withheld"
    cotton = [fid for fid, k in kinds.items() if k == "Cotton"]
    assert sum(_rec(doc, fid)["status"] == "phenology_disagrees" for fid in cotton) <= 1


def test_perennial_green_field_gets_no_sowing_date(village):
    doc, kinds, *_ = village
    fid = next(f for f, k in kinds.items() if k == "perennial")
    r = _rec(doc, fid)
    assert r["phenology_status"] in ("green_at_start", "no_cycle")
    assert r["sowing"]["date"] is None


def test_cotton_harvest_window_is_october_to_january(village):
    doc, kinds, *_ = village
    fid = next(f for f, k in kinds.items() if k == "Cotton")
    w = _rec(doc, fid)["harvest"]["window"]
    assert w["kind"] == "multi_pick"
    assert w["start"] >= "2026-10-01" and w["end"] <= "2027-01-31"


def test_uniformly_stressed_cotton_field_is_detected(village):
    doc, _, _, _, stressed = village
    s = _rec(doc, stressed)["stress"]
    assert s["status"] == "scored"
    assert s["looks_stressed"] >= 2 and s["confirmed_looks"] >= 1


def test_yield_is_an_index_without_district_figures(village):
    doc, kinds, *_ = village
    ys = [_rec(doc, f)["yield"] for f, k in kinds.items() if k == "Cotton"]
    assert all(y.get("yield_t_ha") is None for y in ys)
    idx = [y["yield_index"] for y in ys if y.get("yield_index") is not None]
    assert len(idx) >= 10 and np.std(idx) > 0.02          # not a constant
    stressed_idx = _rec(doc, village[4])["yield"]["yield_index"]
    assert stressed_idx < np.median(idx)


def test_products_exist_and_maps_agree_with_field_numbers(village):
    doc, _, _, out, stressed = village
    import rasterio
    items = json.loads((out / "products.json").read_text())["products"]
    kinds = {i["product"] for i in items}
    assert {"ndvi", "anomaly", "stress_class", "vh", "sowing_doy", "stage"} <= kinds
    # Index maps only on real clear dates.
    s2_dates = set(doc["observations"]["s2_dates"])
    assert all(i["date"] in s2_dates for i in items if i["product"] == "ndvi")
    # Raster / field consistency on one scored date.
    rec = _rec(doc, stressed)
    look = next(lk for lk in rec_looks(doc, stressed) if lk.get("stressed_fraction") is not None
                and lk["class"] not in ("establishing", "pre_sowing"))
    item = next(i for i in items if i["product"] == "stress_class" and i["date"] == look["date"])
    with rasterio.open(out / item["cog"]) as src:
        cls = src.read(1)
    grid = _grid_of(doc)
    from src.raster.grid import field_masks as fm
    zone = next(z for z in doc["zones"] if z["source_field_id"] == stressed)
    del zone
    feature = next(f for f in doc["fields"]["features"] if f["properties"]["field_id"] == stressed)
    m = fm(grid, [{"field_id": stressed, "geometry": feature["geometry"]}])[stressed]
    px = cls[m.stat_pixels()]
    px = px[np.isfinite(px)]
    share = float(np.mean(px >= 2))
    assert abs(share - look["stressed_fraction"]) <= 0.02
    assert rec["pixel_basis"] == "interior"


def rec_looks(doc, fid):
    zone = next(z for z in doc["zones"] if z["source_field_id"] == fid)
    return [{"date": iv["date"], "stressed_fraction": iv["stress"]["stressed_fraction"],
             "class": iv["stress"]["class"]} for iv in zone["intervals"]]


def _grid_of(doc):
    from src.raster.grid import grid_for as gf
    return gf([f["geometry"] for f in doc["fields"]["features"]], pad_m=60)


def test_every_cycle_gets_a_crop_group_and_groups_match_truth(village):
    doc, kinds, *_ = village
    decided_wrong = 0
    undecided = 0
    for fid, kind in kinds.items():
        if kind == "perennial":
            continue
        g = _rec(doc, fid).get("crop_group") or {}
        want = "short_season_kharif" if kind == "Soyabean" else "long_season_kharif"
        if g.get("status") == "decided":
            decided_wrong += g.get("group") != want
        else:
            undecided += 1
    # A decided group has to be the right one. A few fields can still be
    # ambiguous in October: the fit no longer searches from April, so the
    # bare-soil lead-in is shorter and the two groups separate less cleanly.
    assert decided_wrong == 0
    assert undecided <= 3
    assert "crop_group_counts" in doc["cluster_summary"]


def test_unlabelled_soybean_is_named_from_the_reference_curve(village):
    doc, *_ = village
    r = _rec(doc, "f19")
    assert r["crop"] == "Soyabean"
    assert r["status"] == "provisional"
    assert "named_from_reference" in r["qa_flags"]
    assert r["confidence_basis"] == "reference_curve"
    assert 0.45 <= r["confidence"] <= 0.72
    assert r["yield"]["basis"] != "withheld"
    assert "No crop name" not in (r["yield"].get("note") or "")
    assert r["sowing"]["date"] and r["sowing"]["date"] >= "2026-06-01"
    assert doc.get("confidence") is not None
