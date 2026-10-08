"""
Area-classification decision rules (accuracy plan, Track B).

Each test pins one rule that the Dhaswadi Kharif 2026 audit found broken:
renormalising over a one-crop list, window past the run date, empty bins fed
to the model, Fallow meaning "not seen", and a version label that was not the
model.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from crop_analysis.area_classifier import (
    NO_DATA,
    NOT_REQUESTED,
    OTHERS,
    ClassifyInputs,
    apply_reference_crop,
    classify_objects,
    classify_without_cycle,
    cycle_is_complete,
    cycle_scenes,
    decide_crop,
    merge_field_objects,
    observation_window,
)
from crop_analysis.crop_calendar import season_consistent

KHARIF_CYCLE = {
    "sowing_date": "2026-06-20", "start_date": "2026-06-20",
    "peak_date": "2026-09-05", "harvest_date": "2026-12-10", "end_date": "2026-12-10",
    "duration_days": 173,
}
SOY_CYCLE = {
    "sowing_date": "2026-06-25", "start_date": "2026-06-25",
    "peak_date": "2026-08-20", "harvest_date": "2026-10-05", "end_date": "2026-10-05",
    "duration_days": 102,
}
SUPPORT = {
    "viable_threshold": 30,
    "counts": {
        "Cotton": {"DECCAN_PLATEAU": 48, "INDIA_UNSPECIFIED_PLAINS": 418},
        "Soyabean": {"DECCAN_PLATEAU": 148},
        "Tur": {"DECCAN_PLATEAU": 421},
        "Onion": {"DECCAN_PLATEAU": 429},
        "Chilli": {"SOUTHERN_PENINSULA": 309},
        "Wheat": {"DECCAN_PLATEAU": 20},
    },
}


def _pred(probs, abstained=False, reason=None):
    return {"all_probabilities": probs, "abstained": abstained, "abstain_reason": reason}


# ── B1: no renormalisation over the requested crops ──────────────────────────
def test_single_crop_request_does_not_relabel_soybean_as_cotton():
    out = decide_crop(
        _pred({"Soyabean": 0.80, "Tur": 0.12, "Cotton": 0.08}), SOY_CYCLE,
        allow=["Cotton"], season="kharif", ecoregion="DECCAN_PLATEAU", support=SUPPORT,
    )
    assert out["crop"] == NOT_REQUESTED
    assert out["status"] == "not_requested"
    assert out["model_top_crop"] == "Soyabean"
    assert out["confidence"] == pytest.approx(0.80)
    assert "Soyabean" in out["note"]


def test_requested_crop_keeps_its_unrenormalised_probability():
    out = decide_crop(
        _pred({"Cotton": 0.62, "Tur": 0.30, "Soyabean": 0.08}), KHARIF_CYCLE,
        allow=["Cotton"], season="kharif", ecoregion="DECCAN_PLATEAU", support=SUPPORT,
    )
    assert out["crop"] == "Cotton"
    assert out["confidence"] == pytest.approx(0.62)      # not 1.0
    assert out["p_top2"] == pytest.approx(0.30)
    assert out["margin"] == pytest.approx(0.32)


# ── B2: abstentions keep their real (low) probability ────────────────────────
def test_abstained_field_reports_its_low_probability():
    out = decide_crop(
        _pred({"Cotton": 0.22, "Soyabean": 0.20, "Tur": 0.18}, True, "p_max 0.22 < 0.25"),
        KHARIF_CYCLE, allow=["Cotton"], season="kharif",
        ecoregion="DECCAN_PLATEAU", support=SUPPORT,
    )
    assert out["crop"] == OTHERS
    assert out["status"] == "abstained"
    assert out["confidence"] == pytest.approx(0.22)
    assert out["abstain_reason"].startswith("p_max")
    assert out["model_top_crop"] == "Cotton"


# ── B5: region guard and season mask lower trust, never move the argmax ──────
def test_region_guard_abstains_on_a_crop_unseen_in_the_region():
    out = decide_crop(
        _pred({"Chilli": 0.70, "Cotton": 0.20}), KHARIF_CYCLE,
        season="kharif", ecoregion="DECCAN_PLATEAU", support=SUPPORT,
    )
    assert out["model_top_crop"] == "Chilli"
    assert out["region_support"] == "unsupported"
    assert out["crop"] == OTHERS
    assert out["status"] == "abstained"
    assert out["confidence"] == pytest.approx(0.70 * 0.15)


def test_season_mask_rejects_a_rabi_crop_on_a_kharif_cycle():
    out = decide_crop(
        _pred({"Wheat": 0.65, "Cotton": 0.25}), KHARIF_CYCLE,
        season="kharif", ecoregion="DECCAN_PLATEAU", support=SUPPORT,
        apply_region_guard=False,
    )
    assert out["season_consistent"] is False
    assert out["crop"] == OTHERS
    assert out["status"] == "abstained"
    assert out["model_top_crop"] == "Wheat"
    assert "calendar" in out["abstain_reason"]


def test_guards_can_be_switched_off():
    out = decide_crop(
        _pred({"Wheat": 0.65, "Cotton": 0.25}), KHARIF_CYCLE,
        season="kharif", ecoregion="DECCAN_PLATEAU", support=SUPPORT,
        apply_region_guard=False, apply_season_mask=False,
    )
    assert out["crop"] == "Wheat"


# ── intercrop and in-season status ───────────────────────────────────────────
def test_close_soybean_tur_split_is_reported_as_intercrop():
    out = decide_crop(
        _pred({"Soyabean": 0.41, "Tur": 0.36, "Cotton": 0.10}), SOY_CYCLE,
        season="kharif", ecoregion="DECCAN_PLATEAU", support=SUPPORT,
    )
    assert out["crop"] == "Soyabean+Tur"
    assert out["status"] == "intercrop"
    assert out["confidence"] == pytest.approx(0.77)


def test_intercrop_is_not_requested_when_neither_crop_was_asked_for():
    out = decide_crop(
        _pred({"Soyabean": 0.41, "Tur": 0.36}), SOY_CYCLE, allow=["Cotton"],
        season="kharif", ecoregion="DECCAN_PLATEAU", support=SUPPORT,
    )
    assert out["crop"] == NOT_REQUESTED


def test_deccan_kharif_holds_a_narrow_onion_lead():
    out = decide_crop(
        _pred({"Onion": 0.40, "Cotton": 0.29, "Soyabean": 0.16, "Tur": 0.08}),
        KHARIF_CYCLE, season="kharif", ecoregion="DECCAN_PLATEAU", support=SUPPORT,
    )
    assert out["crop"] == OTHERS
    assert out["status"] == "not_requested"
    assert out["model_top_crop"] == "Onion"
    assert "0.11" in out["note"]


def test_deccan_kharif_holds_rice_even_when_the_region_has_seen_it():
    support = json.loads(json.dumps(SUPPORT))
    support["counts"]["Rice"] = {"DECCAN_PLATEAU": 80}
    out = decide_crop(
        _pred({"Rice": 0.46, "Cotton": 0.28, "Soyabean": 0.14, "Tur": 0.08}),
        KHARIF_CYCLE, season="kharif", ecoregion="DECCAN_PLATEAU", support=support,
    )
    assert out["crop"] == OTHERS
    assert out["model_top_crop"] == "Rice"


def test_deccan_kharif_prints_onion_when_it_leads_by_a_wide_margin():
    out = decide_crop(
        _pred({"Onion": 0.72, "Cotton": 0.12, "Soyabean": 0.08, "Tur": 0.04}),
        KHARIF_CYCLE, season="kharif", ecoregion="DECCAN_PLATEAU", support=SUPPORT,
    )
    assert out["crop"] == "Onion"
    assert out["model_top_crop"] == "Onion"


def test_onion_hold_does_not_apply_outside_deccan_kharif():
    out = decide_crop(
        _pred({"Onion": 0.40, "Cotton": 0.29}), KHARIF_CYCLE,
        season="kharif", ecoregion="SOUTHERN_PENINSULA", support=SUPPORT,
        apply_region_guard=False,
    )
    assert out["crop"] == "Onion"


def test_unfinished_cycle_is_provisional():
    out = decide_crop(
        _pred({"Cotton": 0.70, "Tur": 0.20}), KHARIF_CYCLE,
        season="kharif", ecoregion="DECCAN_PLATEAU", support=SUPPORT,
        cycle_complete=False,
    )
    assert out["crop"] == "Cotton"
    assert out["status"] == "provisional"
    assert out["cycle_complete"] is False


# ── B3: window ends at the run date; only real in-cycle scenes ───────────────
def test_observation_window_stops_at_the_run_date():
    d0, d1 = observation_window(ClassifyInputs(season="kharif", year=2026,
                                               as_of=date(2026, 10, 2)))
    assert d0 == date(2026, 5, 1)
    assert d1 == date(2026, 10, 2)
    # A finished season keeps its full window.
    _, d1_past = observation_window(ClassifyInputs(season="kharif", year=2024,
                                                   as_of=date(2026, 10, 2)))
    assert d1_past == date(2024, 12, 15)


def test_as_of_is_parsed_from_the_request():
    assert ClassifyInputs.from_dict({"as_of": "2026-10-02"}).as_of == date(2026, 10, 2)


def _scene(d, ndvi=0.5, missing=False):
    return {"date": d, "missing": missing, "indices": {"NDVI_mean": float("nan") if missing else ndvi}}


def test_cycle_scenes_drop_missing_bins_and_out_of_cycle_dates():
    scenes = [
        _scene("2026-05-01"), _scene("2026-06-10"),
        _scene("2026-06-20"), _scene("2026-07-01", missing=True),
        _scene("2026-08-20"), _scene("2026-10-05"), _scene("2026-10-20"),
    ]
    got = [s["date"] for s in cycle_scenes(scenes, SOY_CYCLE)]
    # 5-day padding (CROP_DETECTOR_CYCLE_SCENE_PADDING_DAYS) either side.
    assert got == ["2026-06-20", "2026-08-20", "2026-10-05"]


def test_cycle_complete_needs_an_observation_on_or_after_harvest():
    scenes = [_scene("2026-08-20"), _scene("2026-09-30")]
    assert cycle_is_complete(SOY_CYCLE, scenes, date(2026, 10, 2)) is False
    scenes.append(_scene("2026-10-08"))
    assert cycle_is_complete(SOY_CYCLE, scenes, date(2026, 10, 10)) is True
    assert cycle_is_complete(KHARIF_CYCLE, scenes, date(2026, 10, 10)) is False


# ── B7: "no cycle" is not automatically Fallow ───────────────────────────────
def _season(ndvi, n=16, step=10, missing_every=None):
    out = []
    d = date(2026, 5, 1)
    for i in range(n):
        miss = bool(missing_every and i % missing_every == 0)
        out.append(_scene(date.fromordinal(d.toordinal() + i * step).isoformat(), ndvi, miss))
    return out


def test_bare_all_season_is_fallow():
    crop, note = classify_without_cycle(_season(0.18))
    assert crop == "Fallow"
    assert "bare" in note


def test_too_few_clear_looks_is_insufficient_data_not_fallow():
    crop, _ = classify_without_cycle(_season(0.18, n=6))
    assert crop == NO_DATA


def test_long_monsoon_gap_is_insufficient_data():
    scenes = _season(0.18, n=16)
    gap = [s for s in scenes if not ("2026-06-15" <= s["date"] <= "2026-08-20")]
    crop, note = classify_without_cycle(gap)
    assert crop == NO_DATA
    assert "gap" in note


def test_green_without_a_cycle_is_others():
    crop, note = classify_without_cycle(_season(0.62))
    assert crop == OTHERS
    assert "green" in note


# ── season calendar check ────────────────────────────────────────────────────
def test_season_consistent():
    assert season_consistent("Cotton", date(2026, 9, 5), "kharif") is True
    assert season_consistent("Soyabean", date(2026, 8, 20), "kharif") is True
    assert season_consistent("Wheat", date(2026, 9, 5), "kharif") is False
    assert season_consistent("Wheat", date(2027, 2, 10), "rabi") is True
    assert season_consistent("Sugarcane", date(2026, 9, 5), "kharif") is None


# ── export keys reach the polygons ───────────────────────────────────────────
def _square(x, y, d):
    return {"type": "Polygon", "coordinates": [[[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]]}


def test_merge_keeps_the_model_answer_on_each_field():
    objs = [{
        "field_id": 1, "geometry": _square(76.85, 18.80, 0.004), "area_ha": 18.0,
        "crop": NOT_REQUESTED, "confidence": 0.8, "status": "not_requested",
        "model_top_crop": "Soyabean", "top2_crop": "Tur", "p_top1": 0.8, "margin": 0.68,
        "boundary_source": "watershed",
    }]
    merged = merge_field_objects(objs, 0.2, dissolve_same_crop=False)
    assert merged[0]["model_top_crop"] == "Soyabean"
    assert merged[0]["status"] == "not_requested"
    assert merged[0]["margin"] == pytest.approx(0.68)


def test_snic_dissolve_does_not_merge_different_unrequested_crops():
    a = {"field_id": 1, "geometry": _square(76.850, 18.80, 0.003), "area_ha": 10.0,
         "crop": NOT_REQUESTED, "confidence": 0.8, "model_top_crop": "Soyabean"}
    b = {"field_id": 2, "geometry": _square(76.853, 18.80, 0.003), "area_ha": 10.0,
         "crop": NOT_REQUESTED, "confidence": 0.7, "model_top_crop": "Maize"}
    merged = merge_field_objects([a, b], 0.2, dissolve_same_crop=True)
    assert sorted(m["model_top_crop"] for m in merged) == ["Maize", "Soyabean"]


# ── end to end through classify_objects with fakes ───────────────────────────
def test_classify_objects_feeds_only_real_in_cycle_scenes(monkeypatch):
    seen = {}

    class FakeCropDetector:
        crop_names = ["Cotton", "Soyabean", "Tur"]

        def __init__(self, crop_model_path, latitude=None, longitude=None, verbose=True):
            pass

        def _classify_crop_chronological(self, scenes, cycle=None):
            seen["dates"] = [s["date"] for s in scenes]
            seen["missing"] = any(s.get("missing") for s in scenes)
            return _pred({"Soyabean": 0.80, "Tur": 0.12, "Cotton": 0.08})

    class FakeCycleDetector:
        def detect_cycles(self, **kwargs):
            return [SOY_CYCLE]

    monkeypatch.setattr("crop_analysis.crop_detector.CropDetector", FakeCropDetector)
    monkeypatch.setattr("crop_analysis.crop_cycle_detector.CropCycleDetector", FakeCycleDetector)

    bins = [date.fromordinal(date(2026, 5, 1).toordinal() + 10 * i).isoformat() for i in range(16)]
    rec = {"NDVI_mean": 0.5, "EVI_mean": 0.4, "NDMI_mean": 0.2}
    series = {b: rec for i, b in enumerate(bins) if i % 4 != 2}     # every 4th bin cloudy
    objects = [{
        "field_id": 1, "geometry": _square(76.85, 18.80, 0.003), "area_ha": 9.0,
        "centroid": {"lat": 18.80, "lng": 76.85}, "series": series,
    }]
    out = classify_objects(
        objects, bins,
        ClassifyInputs(season="kharif", year=2026, target_crops=["Cotton"],
                       as_of=date(2026, 10, 2)),
        lambda *a, **k: None,
    )
    assert seen["missing"] is False
    assert min(seen["dates"]) >= "2026-06-20" and max(seen["dates"]) <= "2026-10-10"
    o = objects[0]
    assert o["crop"] == NOT_REQUESTED and o["model_top_crop"] == "Soyabean"
    assert o["confidence"] == pytest.approx(0.80)
    assert o["ecoregion"] == "DECCAN_PLATEAU"
    assert o["n_obs_cycle"] == len(seen["dates"])
    assert out["window"]["end"] == "2026-10-02"
    assert out["model"]["name"] == "crop_classifier_tier1_mh_v1"


def test_a_cotton_shaped_others_field_is_named_from_the_reference():
    import sys
    from datetime import timedelta
    from pathlib import Path

    import numpy as np

    package = Path(__file__).resolve().parents[3] / "Crop_Monitoring"
    if str(package) not in sys.path:
        sys.path.insert(0, str(package))
    from src.raster.reference import load_curves

    curve = load_curves()["Cotton"]
    sow = date(2026, 6, 25)
    scenes = []
    d = date(2026, 5, 20)
    while d <= date(2026, 10, 2):
        das = (d - sow).days
        value = float(curve.base if das < 0 else curve.value(np.array([das]))[0])
        scenes.append({"date": d.isoformat(), "indices": {"NDVI_mean": value}})
        d += timedelta(days=10)
    obj = {"crop": OTHERS, "confidence": 0.29, "status": "not_requested", "model_top_crop": "Onion"}
    apply_reference_crop(obj, scenes, date(2026, 10, 4), "kharif")
    assert obj["crop"] == "Cotton"
    assert obj["status"] == "reference"
    assert obj["model_top_crop"] == "Onion"
    assert 0.45 <= obj["confidence"] <= 0.72

    fallow = {"crop": "Fallow", "confidence": 0.0, "status": "no_cycle"}
    apply_reference_crop(fallow, scenes, date(2026, 10, 4), "kharif")
    assert fallow["crop"] == "Fallow"
