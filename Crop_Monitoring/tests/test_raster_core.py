"""Raster engine analysis modules on synthetic data (no Earth Engine).

Each test pins a failure the Dhaswadi Kharif 2026 audit found in the point
engine, or a property the replacement must have.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from src.raster import phenology, reference, smooth, sowing, stress, yield_index
from src.raster.grid import Grid, field_masks, grid_for

START = date(2026, 3, 1)
AS_OF = date(2026, 10, 2)
DAYS = smooth.daily_axis(START, AS_OF)


def _curve(crop: str, sow: date, days=DAYS) -> np.ndarray:
    c = reference.DEFAULT_CURVES[crop]
    das = np.array([(d - sow).days for d in days], float)
    v = c.value(das)
    return np.where(das < 0, c.base, v)


def _observe(truth: np.ndarray, every: int = 5, cloud_gap=(date(2026, 6, 20), date(2026, 7, 25)),
             rng=None, haze=0.25):
    """Clear looks every `every` days, none in a monsoon gap, some hazy (low)."""
    rng = rng or np.random.default_rng(0)
    obs_dates, vals = [], []
    for i in range(0, len(DAYS), every):
        d = DAYS[i]
        if cloud_gap[0] <= d <= cloud_gap[1]:
            continue
        v = truth[i] + rng.normal(0, 0.015)
        if rng.random() < haze:
            v -= rng.uniform(0.08, 0.25)       # residual cloud only ever lowers NDVI
        obs_dates.append(d)
        vals.append(v)
    return obs_dates, np.array(vals, np.float32)[:, None]


# ── smoothing ───────────────────────────────────────────────────────────────
def test_whittaker_upper_envelope_tracks_truth_through_haze_and_gaps():
    truth = _curve("Soyabean", date(2026, 6, 22))
    od, ov = _observe(truth)
    z = smooth.smooth_series(od, ov, np.ones_like(ov), START, AS_OF)[:, 0]
    ok = np.array([d >= date(2026, 5, 1) for d in DAYS])
    assert np.nanmean(np.abs(z[ok] - truth[ok])) < 0.05
    assert abs(int(np.argmax(z)) - int(np.argmax(truth))) <= 10


# ── phenology: the April sowing bug ─────────────────────────────────────────
def _pheno(truth, od=None, ov=None):
    if od is None:
        od, ov = _observe(truth)
    z = smooth.smooth_series(od, ov, np.ones_like(ov), START, AS_OF)
    return phenology.detect(DAYS, z, od, ov, np.ones_like(ov), date(2026, 6, 1))


def test_green_at_start_is_not_a_greenup():
    # Perennial-like canopy already green on 1 March and through the season.
    truth = np.full(len(DAYS), 0.55) + 0.25 * np.sin(np.linspace(0, np.pi, len(DAYS)))
    od, ov = _observe(truth, haze=0.0)
    p = _pheno(truth, od, ov)
    assert p.status[0] == phenology.STATUS_GREEN_AT_START
    assert p.sos_i[0] == -1


NO_GAP = (date(2027, 1, 1), date(2027, 1, 2))


def test_bare_then_rise_dates_the_greenup_after_sowing():
    sow = date(2026, 6, 25)
    truth = _curve("Soyabean", sow)
    od, ov = _observe(truth, cloud_gap=NO_GAP)
    p = _pheno(truth, od, ov)
    assert p.status[0] == phenology.STATUS_OK
    sos = p.days[p.sos_i[0]]
    assert sow + timedelta(days=5) <= sos <= sow + timedelta(days=35)
    assert p.sos_gap[0] <= 10


def test_greenup_inside_a_monsoon_gap_carries_the_gap_length():
    p = _pheno(_curve("Soyabean", date(2026, 6, 25)))       # no looks 20 Jun - 25 Jul
    assert p.status[0] == phenology.STATUS_OK
    assert p.sos_gap[0] >= 30


def test_flat_bare_field_is_no_cycle():
    truth = np.full(len(DAYS), 0.16)
    od, ov = _observe(truth, haze=0.0)
    assert _pheno(truth, od, ov).status[0] == phenology.STATUS_NO_CYCLE


# ── curve fit and crop check ────────────────────────────────────────────────
def _fit(truth, crop, sow_from=date(2026, 5, 1), sow_to=date(2026, 8, 15), gap=None):
    od, ov = _observe(truth) if gap is None else _observe(truth, cloud_gap=gap)
    z = smooth.smooth_series(od, ov, np.ones_like(ov), START, AS_OF)[:, 0]
    support = np.zeros(len(DAYS))
    for d in od:
        k = (d - START).days
        support[max(0, k - 5):k + 6] = 1.0
    return z, support, reference.fit_curve(DAYS, z, support, reference.DEFAULT_CURVES[crop], sow_from, sow_to)


@pytest.mark.parametrize("crop,sow", [("Soyabean", date(2026, 6, 24)), ("Cotton", date(2026, 6, 18))])
def test_curve_fit_recovers_sowing(crop, sow):
    _, _, f = _fit(_curve(crop, sow), crop, gap=NO_GAP)
    assert f is not None
    assert f.rise_observed
    assert abs((f.sowing - sow).days) <= 8


def test_curve_fit_says_when_the_rise_was_not_seen():
    _, _, f = _fit(_curve("Soyabean", date(2026, 6, 24)), "Soyabean")   # rise in the gap
    assert f is not None and not f.rise_observed


def test_soybean_labelled_cotton_is_flagged_by_october():
    z, support, _ = _fit(_curve("Soyabean", date(2026, 6, 24)), "Soyabean")
    out = reference.crop_check(DAYS, z, support, "Cotton", reference.DEFAULT_CURVES,
                               date(2026, 5, 1), date(2026, 8, 15), AS_OF)
    assert out["checked"] is True
    assert out["disagrees"] is True
    assert out["best_alternative"] in ("Soyabean", "Maize", "Jowar", "Groundnut", "Bajra")
    assert "Soyabean" in out["candidates"]


def test_real_cotton_is_not_flagged():
    z, support, _ = _fit(_curve("Cotton", date(2026, 6, 18)), "Cotton")
    out = reference.crop_check(DAYS, z, support, "Cotton", reference.DEFAULT_CURVES,
                               date(2026, 5, 1), date(2026, 8, 15), AS_OF)
    assert out["disagrees"] is False


def test_crop_check_waits_until_crops_can_be_separated():
    early = date(2026, 8, 5)
    days = smooth.daily_axis(START, early)
    truth = _curve("Soyabean", date(2026, 6, 24), days)
    z = truth.copy()
    support = np.ones(len(days))
    out = reference.crop_check(days, z, support, "Cotton", reference.DEFAULT_CURVES,
                               date(2026, 5, 1), date(2026, 7, 31), early)
    assert out["checked"] is False


# ── sowing ──────────────────────────────────────────────────────────────────
def _rain(onset: date, false_start: date | None = None) -> dict:
    r = {}
    d = date(2026, 5, 1)
    while d <= date(2026, 9, 30):
        r[d] = 0.0
        d += timedelta(days=1)
    if false_start:
        for k in range(3):
            r[false_start + timedelta(days=k)] = 30.0      # 90 mm then a long break
    for k in range(0, 90, 2):
        r[onset + timedelta(days=k)] = 12.0
    return r


def test_onset_skips_a_false_start_and_restarts_the_count():
    # 90 mm on 2-4 June then a long break; steady rain from 20 June, 12 mm every
    # other day, so 75 mm accumulates again by the 7th rain day (2 July).
    o = sowing.monsoon_onset(_rain(date(2026, 6, 20), false_start=date(2026, 6, 2)), 2026)
    assert o.false_starts and o.false_starts[0] <= date(2026, 6, 5)
    assert o.date == date(2026, 7, 2)


def test_may_optical_cue_does_not_become_a_rainfed_sowing_date():
    onset = sowing.monsoon_onset(_rain(date(2026, 6, 12)), 2026)
    cues = [sowing.SowingCue("optical_fit", date(2026, 5, 8), 7.0)]
    r = sowing.estimate("Cotton", (6, 7), 2026, onset, cues)
    assert r.date is None or r.date >= onset.date - timedelta(days=7)


def test_april_optical_cue_does_not_drag_rainfed_sowing_into_april():
    onset = sowing.monsoon_onset(_rain(date(2026, 6, 12)), 2026)
    cues = [sowing.SowingCue("optical_fit", date(2026, 4, 17), 7.0),
            sowing.SowingCue("radar", date(2026, 6, 16), 9.0)]
    r = sowing.estimate("Cotton", (5, 7), 2026, onset, cues)
    assert r.status == "estimated"
    assert date(2026, 6, 5) <= r.date <= date(2026, 7, 5)
    assert "optical_fit" not in r.sources


def test_irrigated_evidence_allows_may_sowing():
    onset = sowing.monsoon_onset(_rain(date(2026, 6, 12)), 2026)
    cues = [sowing.SowingCue("optical_fit", date(2026, 5, 18), 7.0)]
    r = sowing.estimate("Cotton", (5, 7), 2026, onset, cues, irrigated=True)
    assert r.status == "estimated"
    assert abs((r.date - date(2026, 5, 18)).days) <= 7


def test_canopy_up_before_the_window_gets_no_sowing_date():
    onset = sowing.monsoon_onset(_rain(date(2026, 6, 12)), 2026)
    cues = [sowing.SowingCue("optical_fit", date(2026, 4, 20), 7.0),
            sowing.SowingCue("radar", date(2026, 4, 24), 9.0)]
    r = sowing.estimate("Sugarcane", (1, 12), 2026, onset, cues, irrigated=True,
                        window_start=date(2026, 5, 1))
    assert r.status == "before_window" and r.date is None
    # The same early irrigated crop sown inside the window keeps its date.
    cues = [sowing.SowingCue("optical_fit", date(2026, 5, 18), 7.0)]
    r = sowing.estimate("Cotton", (5, 7), 2026, onset, cues, irrigated=True,
                        window_start=date(2026, 5, 1))
    assert r.status == "estimated"


def test_no_cue_invents_no_date():
    onset = sowing.monsoon_onset(_rain(date(2026, 6, 12)), 2026)
    r = sowing.estimate("Cotton", (5, 7), 2026, onset, [])
    assert r.status == "no_cue" and r.date is None


def test_radar_emergence_uses_absolute_bare_and_canopy_signatures():
    dates = [date(2026, 5, 1) + timedelta(days=6 * i) for i in range(25)]
    # Bare (VH -22.5, CR -10.5); a wet-soil spell in June raises VH but not CR.
    vh = np.array([-15.5 if d >= date(2026, 7, 20) else (-19.5 if date(2026, 6, 12) <= d < date(2026, 6, 25) else -22.5)
                   for d in dates])
    cr = np.array([-7.2 if d >= date(2026, 7, 20) else -10.6 for d in dates])
    got = sowing.radar_emergence(dates, vh, cr)
    assert got is not None and got >= date(2026, 7, 20)


def test_radar_emergence_needs_a_bare_look_first():
    dates = [date(2026, 5, 1) + timedelta(days=6 * i) for i in range(25)]
    vh = np.full(len(dates), -15.5)          # vegetated from 1 May (perennial / earlier crop)
    cr = np.full(len(dates), -7.0)
    assert sowing.radar_emergence(dates, vh, cr) is None


def test_sar_reference_file_matches_calibration():
    ref = sowing.load_sar_reference()
    assert ref["bare_vh_max_db"] == -20.0 and ref["canopy_cr_min_db"] == -9.0


# ── stress ──────────────────────────────────────────────────────────────────
def _field(fid, sow, shift=0.0, crop="Cotton"):
    truth = _curve(crop, sow) + shift
    return stress.FieldCurve(fid, crop, sow, DAYS, {"ndvi": truth.astype(np.float32),
                                                    "ndre": (truth * 0.6).astype(np.float32),
                                                    "ndmi": (truth * 0.5 - 0.05).astype(np.float32)})


def test_uniformly_stressed_farm_is_detected_against_its_cohort():
    rng = np.random.default_rng(3)
    peers = [_field(f"p{i}", date(2026, 6, 15) + timedelta(days=int(rng.integers(-6, 7))),
                    float(rng.normal(0, 0.02))) for i in range(12)]
    target = _field("t", date(2026, 6, 15), shift=-0.18)
    ref = stress.reference_for(target, peers + [target])
    looks = []
    for d in (date(2026, 8, 20), date(2026, 9, 1), date(2026, 9, 12)):
        k = (d - START).days
        v = float(target.values["ndvi"][k])
        looks.append(stress.Look(d, v, v * 0.6, v * 0.5 - 0.05, np.full(40, v, np.float32)))
    recs = stress.score_looks(target, looks, ref)
    assert recs[0]["class"] in ("moderate", "severe") and recs[0]["confirmed"] is False
    assert recs[1]["confirmed"] is True
    s = stress.summarise(recs)
    assert s["latest_class"] in ("moderate", "severe") and s["confirmed_looks"] >= 2


def test_healthy_farm_in_a_healthy_cohort_is_healthy():
    peers = [_field(f"p{i}", date(2026, 6, 15), 0.01 * (i % 3 - 1)) for i in range(12)]
    target = _field("t", date(2026, 6, 15))
    ref = stress.reference_for(target, peers + [target])
    d = date(2026, 9, 1)
    v = float(target.values["ndvi"][(d - START).days])
    recs = stress.score_looks(target, [stress.Look(d, v, v * 0.6, v * 0.5 - 0.05, np.full(40, v))], ref)
    assert recs[0]["class"] == "healthy"


def test_waterlogging_is_typed_from_open_water_pixels():
    peers = [_field(f"p{i}", date(2026, 6, 15)) for i in range(12)]
    target = _field("t", date(2026, 6, 15))
    ref = stress.reference_for(target, peers + [target])
    d = date(2026, 8, 10)
    v = float(target.values["ndvi"][(d - START).days])
    lk = stress.Look(d, v, v * 0.6, v * 0.5, np.full(40, v), mndwi_share=0.35, rain_3d_mm=80)
    assert stress.score_looks(target, [lk], ref)[0]["type"] == "Waterlogging"


# ── yield index ─────────────────────────────────────────────────────────────
def test_yield_is_index_only_without_district_figures_and_ranks_fields():
    peers = [_curve("Cotton", date(2026, 6, 15)) * (1 + 0.03 * (i % 5 - 2)) for i in range(12)]
    ints = [yield_index.canopy_integral(DAYS, p, date(2026, 6, 15), "Cotton", AS_OF)[0] for p in peers]
    low, share = yield_index.canopy_integral(DAYS, peers[0] * 0.8, date(2026, 6, 15), "Cotton", AS_OF)
    out = yield_index.estimate(low, share, ints, 0.0, None)
    assert out["basis"] == "index_only" and out["yield_t_ha"] is None
    assert out["yield_index"] < 0.9
    dist = yield_index.DistrictYield("Cotton", 1.2, 0.2, [2021, 2022, 2023], "official")
    with_d = yield_index.estimate(low, share, ints, 0.0, dist)
    assert with_d["basis"] == "district_anchored"
    assert with_d["yield_p10"] < with_d["yield_t_ha"] < with_d["yield_p90"]
    assert "not field-calibrated" in with_d["label"]


# ── grid and masks ──────────────────────────────────────────────────────────
def test_field_interior_excludes_edge_pixels():
    lon, lat = 76.85, 18.80
    d = 0.0007          # ~75 m square
    geom = {"type": "Polygon", "coordinates": [[[lon, lat], [lon + d, lat], [lon + d, lat + d],
                                                [lon, lat + d], [lon, lat]]]}
    g = grid_for([geom], pad_m=20)
    m = field_masks(g, [{"field_id": "f", "geometry": geom}])["f"]
    assert m.n_full > m.n_interior > 0
    assert m.n_interior <= 0.6 * m.n_full
    assert not m.low_resolution


# ── crop groups (in-season answer when crop names are not yet decidable) ────
def test_crop_group_separates_short_and_long_season_by_october():
    for crop, sow, want in (("Soyabean", date(2026, 6, 24), "short_season_kharif"),
                            ("Cotton", date(2026, 6, 18), "long_season_kharif")):
        z, support, _ = _fit(_curve(crop, sow), crop)
        g = reference.crop_group(DAYS, z, support, reference.DEFAULT_CURVES,
                                 date(2026, 4, 15), date(2026, 8, 31), AS_OF)
        assert g["status"] == "decided", g
        assert g["group"] == want


def test_crop_group_is_not_decided_too_early():
    early = date(2026, 8, 5)
    days = smooth.daily_axis(START, early)
    z = _curve("Soyabean", date(2026, 6, 24), days)
    g = reference.crop_group(days, z, np.ones(len(days)), reference.DEFAULT_CURVES,
                             date(2026, 4, 15), date(2026, 7, 31), early)
    assert g["group"] is None and g["status"] == "too_early"


def test_landsat_calibration_uses_per_pair_fits_not_a_diluted_pooled_fit():
    from src.raster import indices as ix
    rng = np.random.default_rng(1)
    s2_dates, ls_dates, s2v, lsv = [], [], [], []
    for k in range(6):
        d = date(2026, 3, 5) + timedelta(days=16 * k)
        truth = rng.uniform(0.1, 0.7, (60, 60)).astype(np.float32) + 0.05 * k
        s2_dates.append(d)
        ls_dates.append(d + timedelta(days=1))
        s2v.append(truth)
        lsv.append(((truth + 0.12) / 1.3 + rng.normal(0, 0.01, truth.shape)).astype(np.float32))
    s2 = {n: np.stack(s2v) for n in ix.SHARED_INDICES}
    ls = {n: np.stack(lsv) for n in ix.SHARED_INDICES}
    cal = ix.fit_cross_calibration(s2_dates, s2, ls_dates, ls)["ndvi"]
    assert cal["applied"] and cal["pairs"] == 6
    assert abs(cal["slope"] - 1.3) < 0.05 and abs(cal["offset"] + 0.12) < 0.03


def _looks(crop: str, sow: date) -> tuple[list[date], list[float]]:
    curve = reference.DEFAULT_CURVES[crop]
    dates, values = [], []
    d = date(2026, 5, 20)
    while d <= date(2026, 10, 2):
        das = (d - sow).days
        values.append(float(curve.base if das < 0 else curve.value(np.array([das]))[0]))
        dates.append(d)
        d += timedelta(days=10)
    return dates, values


def test_cotton_shaped_looks_are_named_cotton_not_the_model_class():
    dates, values = _looks("Cotton", date(2026, 6, 25))
    named = reference.name_from_looks(
        dates, values, date(2026, 10, 4), curves=reference.DEFAULT_CURVES)
    assert named and named["crop"] == "Cotton"
    assert 0.45 <= named["confidence"] <= 0.72


def test_soybean_shaped_looks_are_named_soybean():
    dates, values = _looks("Soyabean", date(2026, 6, 28))
    named = reference.name_from_looks(
        dates, values, date(2026, 10, 4), curves=reference.DEFAULT_CURVES)
    assert named and named["crop"] == "Soyabean"


def test_a_flat_canopy_is_not_named_from_the_reference():
    dates = [date(2026, 5, 20) + timedelta(days=10 * i) for i in range(14)]
    named = reference.name_from_looks(
        dates, [0.2] * len(dates), date(2026, 10, 4), curves=reference.DEFAULT_CURVES)
    assert named is None


def test_an_undecided_group_does_not_invent_a_crop_name():
    assert reference.local_kharif_name({"status": "too_early", "group": None,
                                        "candidates": ["Cotton"]}) is None
    assert reference.local_kharif_name({
        "status": "decided", "group": "long_season_kharif",
        "candidates": ["Sugarcane"], "best_rmse": 0.02, "next_group_rmse": 0.05,
    }) is None
