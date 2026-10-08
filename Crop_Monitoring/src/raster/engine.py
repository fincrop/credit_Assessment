"""One village run: pixel stacks -> per-field records + raster products.

    fields (classified polygons) ──> grid + masks
    Sentinel-2 / Landsat / Sentinel-1 per scene ──> indices, cross-calibration
    field-mean series (clear looks only) ──> Whittaker ──> phenology, curve fit
    weather ──> onset, soil water bucket
    sowing posterior ── crop check ── stage / harvest window
    cohort references ──> stress per clear look ──> persistence, type
    reproductive-window integral ──> district-anchored yield index
    raster products (COG + overlay) from the SAME pixels

Field numbers and maps come from the same pixels, so a field's stressed share
equals the share of its pixels in stressed classes on that date.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from src.library import get_prior, stage_name
from src.raster import indices as ix
from src.raster import phenology as ph
from src.raster import products as pr
from src.raster import reference as rf
from src.raster import smooth as sm
from src.raster import sowing as sw
from src.raster import stress as st
from src.raster import yield_index as yi
from src.raster.grid import FieldMask, Grid, field_masks, grid_for
from src.raster.stack import LANDSAT_WEIGHT, SceneStack

logger = logging.getLogger(__name__)

ENGINE_VERSION = "raster_v1"
# Classification answers with no crop name. Such fields can still be monitored
# (sowing, stage, condition, crop group) when the caller includes them.
UNKNOWN_CROPS = {"", "Unknown", "Unclassified", "Abstained", "Insufficient data", "Not requested", "Others"}
# Kharif data starts 1 May: the pre-sowing state (bare soil by optical or radar)
# is seen in May, before the earliest kharif sowing. Nothing earlier is used.
SEASON_START = {"kharif": (5, 1), "rabi": (9, 15)}
FIELD_CLEAR_MIN = 0.6         # share of a field's pixels clear for its mean to count
PRODUCT_CLEAR_MIN = 0.05      # share of field pixels clear for a dated map to be written
MIN_CLEAR_LOOKS = 6

# Crop calendar sowing months, from the shared calendar (backend crop_analysis).
def _fit_bounds(year: int, season: str, onset: Optional[date], irrigated: bool) -> tuple[date, date]:
    """Days the curve fit may call sowing.

    Rainfed kharif is not searched from April. A canopy that is already up in
    May is weeds, an orchard, or irrigation, and irrigation is allowed only
    when radar saw bare soil and then a canopy before the monsoon.
    """
    if season == "kharif":
        if irrigated:
            return date(year, 5, 1), date(year, 8, 15)
        start = date(year, 6, 1)
        if onset is not None:
            start = max(date(year, 5, 25), onset - timedelta(days=7))
        return start, date(year, 8, 15)
    return date(year, 9, 15), date(year, 12, 31)


def _sow_months(crop: str, season: str) -> tuple[int, int]:
    try:
        from crop_analysis.crop_calendar import CALENDAR
        for w in CALENDAR.get(crop, []):
            if w.name == season or (season == "kharif" and w.name == "late_kharif"):
                return w.sow
    except Exception:  # noqa: BLE001
        pass
    return (6, 7) if season == "kharif" else (10, 12)


@dataclass
class VillageRequest:
    fields: list[dict]               # field_id, crop, geometry, area_ha, confidence, classification
    year: int
    as_of: date
    season: str = "kharif"
    out_dir: Optional[Path] = None   # raster products; None = no products
    cache_root: Optional[Path] = None
    district_yields: list[dict] = field(default_factory=list)
    sowing_records: dict[str, date] = field(default_factory=dict)   # farmer / client dates
    name: str = ""
    lineage: dict = field(default_factory=dict)
    village_normal: bool = False     # 2019-2025 village normal (extra Earth Engine reductions)
    curves: Optional[dict] = None    # reference curves; default: labelled file over parametric
    window_start: Optional[date] = None   # first imagery day; None uses the season start


@dataclass
class Inputs:
    grid: Grid
    s2: Optional[SceneStack]
    landsat: Optional[SceneStack]
    s1: Optional[SceneStack]
    weather: list                    # WeatherDay list (village level)


def fetch_inputs(req: VillageRequest, start: date, progress: Callable[[str], None]) -> Inputs:
    from src.raster import fetch

    grid = grid_for([f["geometry"] for f in req.fields], pad_m=100)
    root = req.cache_root or Path(__file__).resolve().parents[2] / "cache"
    cache = fetch.cache_dir(root, grid)
    progress(f"Sentinel-2 scenes {start}..{req.as_of} on a {grid.width}x{grid.height} 10 m grid")
    s2 = fetch.fetch_s2(grid, start, req.as_of, cache)
    progress(f"Landsat 8/9 scenes ({s2.n} Sentinel-2 dates kept)")
    ls = fetch.fetch_landsat(grid, start, req.as_of, cache)
    progress("Sentinel-1 radar scenes")
    s1 = fetch.fetch_s1(grid, start, req.as_of, cache)
    progress("Village weather (CHIRPS, ERA5-Land, IMERG, NASA POWER)")
    weather = fetch.fetch_weather(grid, start, req.as_of, cache)
    return Inputs(grid, s2, ls, s1, weather)


# ── field-level series from pixel stacks ────────────────────────────────────
def _labels(grid: Grid, masks: dict[str, FieldMask]) -> tuple[np.ndarray, list[str]]:
    """Label image of each field's statistic pixels (0 = none)."""
    lab = np.zeros(grid.shape, np.int32)
    ids = list(masks)
    for i, fid in enumerate(ids, 1):
        lab[masks[fid].stat_pixels() & (lab == 0)] = i
    return lab, ids


def _field_means(stack_idx: dict[str, np.ndarray], lab: np.ndarray, n: int) -> tuple[dict, np.ndarray]:
    """Per date, per field: mean of each index over clear stat pixels, and clear share."""
    flat = lab.ravel()
    total = np.bincount(flat, minlength=n + 1).astype(float)
    T = next(iter(stack_idx.values())).shape[0]
    means = {k: np.full((T, n), np.nan, np.float32) for k in stack_idx}
    share = np.zeros((T, n), np.float32)
    for t in range(T):
        ref = stack_idx["ndvi"][t].ravel()
        ok = np.isfinite(ref) & (flat > 0)
        cnt = np.bincount(flat[ok], minlength=n + 1).astype(float)
        share[t] = (cnt[1:] / np.maximum(total[1:], 1)).astype(np.float32)
        for k, arr in stack_idx.items():
            v = arr[t].ravel()
            okk = ok & np.isfinite(v)
            s = np.bincount(flat[okk], weights=v[okk], minlength=n + 1)
            c = np.bincount(flat[okk], minlength=n + 1)
            with np.errstate(invalid="ignore", divide="ignore"):
                means[k][t] = (s[1:] / c[1:]).astype(np.float32)
    return means, share


def _radar_fields(s1: Optional[SceneStack], lab: np.ndarray, n: int):
    """Field-mean VH and VV (dB of the linear mean) on every Sentinel-1 look.

    Orbits are merged: the cross-ratio (VH - VV) that the bare / canopy
    signatures rely on cancels most of the incidence-angle difference.
    Returns (dates, vh (R, N), vv (R, N)).
    """
    if s1 is None or not s1.n:
        return [], np.zeros((0, n), np.float32), np.zeros((0, n), np.float32)
    flat = lab.ravel()
    out_vh, out_vv = [], []
    for t in range(s1.n):
        row = []
        for band in ("VH", "VV"):
            v = s1.bands[band][t].ravel()
            ok = np.isfinite(v) & (v > 0) & (flat > 0)
            sm = np.bincount(flat[ok], weights=v[ok], minlength=n + 1)
            cn = np.bincount(flat[ok], minlength=n + 1)
            with np.errstate(invalid="ignore", divide="ignore"):
                row.append((10 * np.log10(sm[1:] / cn[1:])).astype(np.float32))
        out_vh.append(row[0])
        out_vv.append(row[1])
    order = sorted(range(s1.n), key=lambda i: s1.dates[i])
    return ([s1.dates[i] for i in order], np.stack(out_vh)[order], np.stack(out_vv)[order])


def _stage(crop: str, das: Optional[int], stretch: float, curve: rf.CropCurve, harvested: bool) -> str:
    if harvested:
        return "harvested"
    if das is None:
        return "unknown"
    if das < 0:
        return "pre-sowing"
    tau = das / max(curve.season_days * stretch, 1.0)
    return stage_name(get_prior(crop), min(tau, 1.0))


def _harvest_window(crop: str, sow: date, stretch: float, curve: rf.CropCurve) -> dict:
    if crop == "Cotton":
        # Rainfed Marathwada cotton is picked several times, October to January.
        start = max(sow + timedelta(days=int(140 * stretch)), date(sow.year, 10, 1))
        end = min(sow + timedelta(days=int(240 * stretch)), date(sow.year + 1, 1, 31))
        return {"kind": "multi_pick", "start": start.isoformat(), "end": end.isoformat()}
    end_das = curve.season_days * stretch
    return {"kind": "single", "start": (sow + timedelta(days=int(end_das - 12))).isoformat(),
            "end": (sow + timedelta(days=int(end_das + 12))).isoformat()}


def run(req: VillageRequest, inputs: Optional[Inputs] = None,
        progress: Callable[[str], None] = lambda m: logger.info(m)) -> dict:
    y = req.year
    start = req.window_start or date(y, *SEASON_START.get(req.season, (5, 1)))
    if inputs is None:
        inputs = fetch_inputs(req, start, progress)
    grid = inputs.grid
    masks = field_masks(grid, [{"field_id": f["field_id"], "geometry": f["geometry"]} for f in req.fields])
    lab, ids = _labels(grid, masks)
    n = len(ids)
    by_id = {str(f["field_id"]): f for f in req.fields}
    days = sm.daily_axis(start, req.as_of)
    curves_lib = req.curves or rf.load_curves()

    progress("Indices and Landsat cross-calibration")
    s2_idx = ix.s2_indices(inputs.s2) if inputs.s2 is not None and inputs.s2.n else {}
    ls_idx = ix.landsat_indices(inputs.landsat) if inputs.landsat is not None and inputs.landsat.n else {}
    cal = {}
    if s2_idx and ls_idx:
        cal = ix.fit_cross_calibration(inputs.s2.dates, s2_idx, inputs.landsat.dates, ls_idx)
        ls_idx = ix.apply_calibration(ls_idx, cal)

    progress("Field series from clear looks")
    s2_means, s2_share = _field_means(s2_idx, lab, n) if s2_idx else ({}, np.zeros((0, n)))
    ls_means, ls_share = _field_means(ls_idx, lab, n) if ls_idx else ({}, np.zeros((0, n)))
    obs_dates = list(inputs.s2.dates if s2_idx else []) + list(inputs.landsat.dates if ls_idx else [])
    w_s2 = (s2_share >= FIELD_CLEAR_MIN).astype(np.float32)
    w_ls = (ls_share >= FIELD_CLEAR_MIN).astype(np.float32) * LANDSAT_WEIGHT
    W = np.concatenate([w_s2, w_ls]) if len(obs_dates) else np.zeros((0, n), np.float32)

    def stacked(name: str) -> np.ndarray:
        a = s2_means.get(name, np.full((len(w_s2), n), np.nan, np.float32))
        b = ls_means.get(name, np.full((len(w_ls), n), np.nan, np.float32))
        return np.concatenate([a, b]) if len(obs_dates) else np.zeros((0, n), np.float32)

    order = sorted(range(len(obs_dates)), key=lambda i: obs_dates[i])
    od = [obs_dates[i] for i in order]
    W = W[order]
    series = {k: stacked(k)[order] for k in ("ndvi", "ndre", "ndmi")}
    smooth = {k: sm.smooth_series(od, np.where(W > 0, v, np.nan), W, start, req.as_of)
              for k, v in series.items()}
    sar_ref = sw.load_sar_reference()
    r_dates, r_vh, r_vv = _radar_fields(inputs.s1, lab, n)
    r_cr = r_vh - r_vv
    r_bare = sw.radar_bare(r_vh, r_cr, sar_ref) if len(r_dates) else None
    pheno = ph.detect(days, smooth["ndvi"], od, series["ndvi"], W, date(y, 6, 1),
                      radar_dates=r_dates, radar_bare=r_bare)
    support = np.zeros((len(days), n), np.float32)
    for t, d in enumerate(od):
        k = (d - start).days
        sel = W[t] > 0
        support[max(0, k - 5):k + 6, sel] = 1.0

    progress("Monsoon onset and soil water")
    rain = {w.date: float(w.rain_p50) for w in inputs.weather}
    et0 = {w.date: float(w.et0) for w in inputs.weather}
    onset = sw.monsoon_onset(rain, y)
    bucket = st.water_balance(rain, et0, start, req.as_of)

    progress(f"Sowing, crop check and stage for {n} fields")
    recs: dict[str, dict] = {}
    fcurves: list[st.FieldCurve] = []
    for j, fid in enumerate(ids):
        f = by_id[fid]
        crop = f["crop"]
        unknown = crop in UNKNOWN_CROPS
        m = masks[fid]
        z = smooth["ndvi"][:, j]
        # After an observed harvest the curve belongs to the NEXT crop (2023
        # Marathwada soybean fields green again with a rabi crop from December).
        # Fits stop 15 days after harvest so that regrowth never reads as this
        # crop's season.
        sup = support[:, j].copy()
        if pheno.harvest_i[j] >= 0 and pheno.peak_final[j]:
            sup[pheno.harvest_i[j] + 15:] = 0.0
        rec: dict = {
            "field_id": fid, "crop": crop, "area_ha": round(m.area_ha, 4),
            "n_pixels": m.n_full, "n_interior_pixels": m.n_interior,
            "pixel_basis": m.pixel_basis, "low_resolution": m.low_resolution,
            "n_clear_looks": int((W[:, j] > 0).sum()),
            "last_clear_observation": max((d for d, w in zip(od, W[:, j]) if w > 0), default=None),
            "classification": f.get("classification") or {}, "qa_flags": [],
        }
        if rec["last_clear_observation"]:
            rec["last_clear_observation"] = rec["last_clear_observation"].isoformat()
        status = int(pheno.status[j])
        rec["phenology_status"] = {0: "ok", 1: "green_at_start", 2: "no_cycle", 3: "no_data"}[status]
        if rec["n_clear_looks"] < MIN_CLEAR_LOOKS:
            rec["qa_flags"].append("few_clear_looks")
        if m.pixel_basis == "full":
            rec["qa_flags"].append("edge_pixels_in_statistics")

        radar_cue = None
        if len(r_dates):
            emerg = sw.radar_emergence(r_dates, r_vh[:, j], r_cr[:, j], sar_ref)
            if emerg is not None:
                radar_cue = (emerg, "VH and cross-ratio vs bare-soil reference")
        # Optical green-up before the monsoon is not irrigation. Radar has to
        # have seen bare soil and then a canopy, and that canopy has to be up
        # well before onset.
        irrigated = bool(
            radar_cue is not None and onset.date
            and radar_cue[0] < onset.date - timedelta(days=10)
        )
        fit_lo, fit_hi = _fit_bounds(y, req.season, onset.date, irrigated)
        cls_in = f.get("classification") or {}
        if isinstance(f.get("confidence"), (int, float)):
            rec["confidence"] = round(float(f["confidence"]), 4)
            rec["confidence_basis"] = "model"
        elif isinstance(cls_in.get("p_top1"), (int, float)):
            rec["confidence"] = round(float(cls_in["p_top1"]), 4)
            rec["confidence_basis"] = "model"

        def _sowing_for(name: str, crv) -> tuple:
            """Cues and posterior under one crop's reference and lags."""
            cues: list[sw.SowingCue] = []
            ft = None
            mo = _sow_months(name, req.season)
            lo = date(y, mo[0], 1) - timedelta(days=15)
            hi = date(y, min(mo[1] + 1, 12), 15)
            if not irrigated:
                lo, hi = max(lo, fit_lo), min(hi, fit_hi)
            if status == ph.STATUS_OK and crv is not None:
                ft = rf.fit_curve(days, z, sup, crv, lo, hi)
                if ft is not None:
                    sigma = sw.OPTICAL_FIT_SIGMA if ft.rise_observed else 2.2 * sw.OPTICAL_FIT_SIGMA
                    cues.append(sw.SowingCue("optical_fit", ft.sowing, sigma,
                                             f"rmse {ft.rmse:.3f}, rise seen {ft.rise_support:.0%}"))
                elif pheno.sos_i[j] >= 0:
                    gap = max(int(pheno.sos_gap[j]), 0)
                    cues.append(sw.SowingCue(
                        "optical_sos",
                        days[pheno.sos_i[j]] - timedelta(days=sw.lag_for(sw.OPTICAL_SOS_LAG, name)),
                        sw.OPTICAL_SOS_SIGMA + 0.3 * max(0, gap - 10)))
            if radar_cue is not None:
                cues.append(sw.SowingCue("radar", radar_cue[0] - timedelta(days=sw.radar_lag(crv)),
                                         sw.RADAR_SIGMA, radar_cue[1]))
            if fid in req.sowing_records:
                d = req.sowing_records[fid]
                res = sw.SowingResult("provided", d, d - timedelta(days=2), d + timedelta(days=2),
                                      ["farm_record"], "record", onset.date, "Sowing date from the farm record.")
            else:
                res = sw.estimate(name, mo, y, onset, cues, irrigated=irrigated, window_start=start)
            return res, ft, lo, hi

        # Crop group: decidable in season even where the crop name is not.
        # The search starts at the monsoon, not in April.
        group = None
        if status == ph.STATUS_OK:
            group = rf.crop_group(days, z, sup, curves_lib, fit_lo, fit_hi, req.as_of)
        rec["crop_group"] = group

        # The classifier often prints Others for a cotton or soybean field
        # (it leaned Onion, Banana, or Grapes). When the canopy matches the
        # cotton or soybean reference and not the other group, use that name.
        # Fallow, a field already green on 1 May, and an undecided curve stay unnamed.
        named = rf.local_kharif_name(group) if unknown and status == ph.STATUS_OK else None
        if named:
            crop = named["crop"]
            unknown = False
            rec["crop"] = crop
            rec["confidence"] = named["confidence"]
            rec["confidence_basis"] = "reference_curve"
            rec["reference_note"] = named["note"]
            rec["qa_flags"].append("named_from_reference")
            kept = dict(rec["classification"])
            kept["status"] = "reference"
            kept["reference_crop"] = crop
            rec["classification"] = kept

        curve = None if unknown else curves_lib.get(crop)
        stage_crop, stage_curve = crop, curve
        if unknown:
            # Still no name. Sowing can be estimated for the group, but yield
            # and a crop stage are not claimed.
            bc = (group or {}).get("best_crop_curve")
            if bc and bc in curves_lib:
                sres, fit, sow_from, sow_to = _sowing_for(bc, curves_lib[bc])
                stage_crop, stage_curve = bc, curves_lib[bc]
            else:
                sres, fit, sow_from, sow_to = _sowing_for("Unknown", None)
            rec["qa_flags"].append("crop_unknown")
        else:
            sres, fit, sow_from, sow_to = _sowing_for(crop, curve)

        # Crop check against reference curves
        check = None
        if not unknown and status == ph.STATUS_OK and fit is not None:
            check = rf.crop_check(days, z, sup, crop, curves_lib, sow_from, sow_to, req.as_of)
            rec["phenology_check"] = {k: v for k, v in check.items() if k != "fits"}
            if check.get("disagrees"):
                rec["qa_flags"].append("phenology_disagrees_with_class")
                # Dates under the claimed crop's curve would be wrong twice over
                # (cotton lags on a soybean field). Re-estimate under the best
                # alternative and say so; the crop name itself is not changed.
                alt = check["best_alternative"]
                alt_res, alt_fit, _, _ = _sowing_for(alt, curves_lib.get(alt))
                if alt_res.date or alt_res.p10:
                    sres, fit = alt_res, alt_fit
                    stage_crop, stage_curve = alt, curves_lib.get(alt)
                    sres.note = (f"Estimated with the {alt} reference: the phenology check disputes "
                                 f"{crop}. " + sres.note)
        rec["sowing"] = sres.to_dict()
        sow = sres.date or (sres.p10 + (sres.p90 - sres.p10) / 2 if sres.p10 and sres.p90 else None)
        stretch = fit.stretch if fit is not None else 1.0
        das = (req.as_of - sow).days if sow else None
        harvested = bool(pheno.harvest_i[j] >= 0 and pheno.peak_final[j]
                         and (stage_crop != "Cotton" or (das or 0) >= 140))
        rec["das"] = das
        stage = _stage(stage_crop, das, stretch, stage_curve, harvested) if stage_curve else "unknown"
        if unknown:
            rec["stage"] = "crop_unknown"
            rec["stage_if_alternative"] = {"crop": f"{(group or {}).get('best_group')} ({stage_crop} curve)",
                                           "stage": stage}
        elif stage_crop != crop:
            rec["stage"] = "disputed"
            rec["stage_if_alternative"] = {"crop": stage_crop, "stage": stage}
        else:
            rec["stage"] = stage
        rec["harvest"] = {"observed": harvested,
                          "date": days[pheno.harvest_i[j]].isoformat() if harvested else None}
        if sow and stage_curve is not None:
            rec["harvest"]["window"] = _harvest_window(stage_crop, sow, stretch, stage_curve)
        rec["peak"] = days[pheno.peak_i[j]].isoformat() if status == ph.STATUS_OK else None
        rec["status"] = _record_status(rec)
        recs[fid] = rec
        sig = ((sres.p90 - sres.p10).days / 2.56) if sres.p10 and sres.p90 else 0.0
        # A field whose curve disputes its label must not shape that crop's
        # stress reference (113 such "cotton" fields on Dhaswadi 2026 would
        # pull the cotton cohort toward a short-season curve).
        if unknown:
            # Only a DECIDED group is a cohort; an undecided field would mix
            # short- and long-season curves into the reference.
            decided = (group or {}).get("group")
            cohort_crop = f"group:{decided}" if decided else "group:undecided"
        elif "phenology_disagrees_with_class" in rec["qa_flags"]:
            cohort_crop = f"{crop}|disputed"
        else:
            cohort_crop = crop
        fcurves.append(st.FieldCurve(fid, cohort_crop, sow if sres.status in ("estimated", "provided") else None,
                                     days, {k: smooth[k][:, j] for k in smooth}, sowing_sigma=sig))

    progress("Stress against same-crop cohorts")
    curve_by_id = {c.field_id: c for c in fcurves}
    bank = st.ReferenceBank(fcurves, fallbacks=curves_lib)
    s2_dates = inputs.s2.dates if s2_idx else []
    pixel_ndvi = {}
    for j, fid in enumerate(ids):
        pix = masks[fid].stat_pixels()
        pixel_ndvi[fid] = [s2_idx["ndvi"][t][pix] for t in range(len(s2_dates))] if s2_idx else []
    for j, fid in enumerate(ids):
        rec = recs[fid]
        target = curve_by_id[fid]
        if target.sowing is None or rec["phenology_status"] != "ok":
            rec["stress"] = {"status": "condition_unavailable",
                             "reason": "no sowing date or no crop cycle"}
            rec["stress_looks"] = []
            continue
        if target.crop == "group:undecided":
            rec["stress"] = {"status": "condition_unavailable",
                             "reason": "crop and crop group not yet known; no comparable fields"}
            rec["stress_looks"] = []
            continue
        ref = bank.get(target)
        looks = []
        for t, d in enumerate(s2_dates):
            if s2_share[t, j] < FIELD_CLEAR_MIN:
                continue
            mnd = s2_idx["mndwi"][t][masks[fid].stat_pixels()]
            mnd = mnd[np.isfinite(mnd)]
            looks.append(st.Look(
                d, float(s2_means["ndvi"][t, j]), float(s2_means["ndre"][t, j]), float(s2_means["ndmi"][t, j]),
                pixel_ndvi[fid][t], float((mnd > 0).mean()) if len(mnd) else 0.0,
                sum(rain.get(d - timedelta(days=k), 0.0) for k in range(3)), bucket.get(d),
            ))
        base_crop = target.crop.split("|")[0]
        sen_das = int(curves_lib[base_crop].t_fall - 15) if base_crop in curves_lib else None
        looks_scored = st.score_looks(target, looks, ref, sen_das)
        rec["stress_looks"] = looks_scored
        rec["stress"] = st.summarise(looks_scored)
        rec["stress"]["reference_fields"] = ref.n_fields

    progress("Yield index")
    for crop in sorted({r["crop"] for r in recs.values()}):
        if crop in UNKNOWN_CROPS:
            for r in recs.values():
                if r["crop"] == crop:
                    r["yield"] = {"basis": "withheld", "note": "No crop name; yield needs a named crop."}
            continue
        members = [fid for fid, r in recs.items() if r["crop"] == crop and curve_by_id[fid].sowing]
        ints = {}
        for fid in members:
            c = curve_by_id[fid]
            ints[fid] = yi.canopy_integral(days, c.values["ndvi"], c.sowing, crop, req.as_of,
                                           reference_value=lambda das, cc=curves_lib.get(crop): (
                                               float(cc.value(np.array([das]))[0]) if cc else np.nan))
        district = yi.district_yield(req.district_yields, crop)
        peers = [v[0] for v in ints.values()]
        for fid in members:
            r = recs[fid]
            if "phenology_disagrees_with_class" in r["qa_flags"]:
                r["yield"] = {"basis": "withheld", "note": "Crop disputed by the phenology check."}
                continue
            sev = (r.get("stress") or {}).get("severity_index") or 0.0
            r["yield"] = yi.estimate(ints[fid][0], ints[fid][1],
                                     [v[0] for k, v in ints.items() if k != fid], sev, district)
        for fid, r in recs.items():
            if r["crop"] == crop and "yield" not in r:
                r["yield"] = {"basis": "withheld", "note": "No sowing date, so no reproductive window."}

    village_condition = None
    if req.village_normal:
        progress("Village multi-year normal (2019-2025)")
        from src.raster import village_normal as vn
        cur = {}
        d = date(y, 5, 1)
        while d <= req.as_of:
            k = (d - start).days + 5
            if 0 <= k < len(days):
                vals = smooth["ndvi"][k]
                vals = vals[np.isfinite(vals)]
                if len(vals):
                    cur[d] = float(np.median(vals))
            d += timedelta(days=vn.BIN_DAYS)
        try:
            village_condition = vn.compute(grid, cur, onset.date)
        except Exception as exc:  # noqa: BLE001
            village_condition = {"status": "unavailable", "reason": str(exc)[:200]}

    products = None
    if req.out_dir is not None:
        progress("Raster products")
        products = _write_products(req, inputs, masks, lab, ids, s2_idx, s2_share, recs, curve_by_id, bank)

    doc = _document(req, recs, masks, onset, cal, products, inputs, curves_lib)
    doc["village_condition"] = village_condition
    return doc


def _record_status(rec: dict) -> str:
    """Plan §7 agreement rule. With no field visits, `confirmed` needs all three:
    the classifier (status confirmed: calibrated p and margin passed, cycle
    complete), the phenology curve check (decisive and agreeing), and sowing
    evidence. Older classification results carry no status (their confidence
    was renormalised to 1.0), so they can never be confirmed here."""
    cls = (rec.get("classification") or {}).get("status")
    check = rec.get("phenology_check") or {}
    if "crop_unknown" in rec["qa_flags"]:
        return "crop_unknown"
    if "phenology_disagrees_with_class" in rec["qa_flags"]:
        return "phenology_disagrees"
    if rec["phenology_status"] == "no_data":
        return "insufficient_evidence"
    if cls == "intercrop":
        return "intercrop"
    agrees = (cls == "confirmed" and check.get("checked") and not check.get("disagrees")
              and rec["phenology_status"] == "ok"
              and rec["sowing"]["status"] in ("estimated", "provided"))
    return "confirmed" if agrees else "provisional"


def _write_products(req, inputs, masks, lab, ids, s2_idx, s2_share, recs, curve_by_id, bank):
    idx = pr.ProductIndex(req.out_dir)
    grid = inputs.grid
    in_fields = lab > 0
    if s2_idx:
        for t, d in enumerate(inputs.s2.dates):
            clear = float(np.isfinite(s2_idx["ndvi"][t][in_fields]).mean()) if in_fields.any() else 0.0
            if clear < PRODUCT_CLEAR_MIN:
                continue
            for name in ("ndvi", "ndre", "ndmi"):
                idx.add(name, s2_idx[name][t], grid, when=d, sensor="Sentinel-2", clear_fraction=clear)
            # Anomaly and stress class from the same pixels as the field records.
            z = np.full(grid.shape, np.nan, np.float32)
            cls = np.full(grid.shape, np.nan, np.float32)
            for j, fid in enumerate(ids):
                rec = recs[fid]
                look = next((lk for lk in rec.get("stress_looks", []) if lk["date"] == d.isoformat()), None)
                c = curve_by_id[fid]
                if look is None or c.sowing is None or look.get("das") is None:
                    continue
                ref = bank.get(c)
                das = look["das"]
                if not (0 <= das < st.MAX_DAS) or "ndvi" not in ref.median or not np.isfinite(ref.median["ndvi"][das]):
                    continue
                pix = masks[fid].stat_pixels()
                zz = (s2_idx["ndvi"][t] - ref.median["ndvi"][das]) / ref.effective_spread("ndvi", das, c.sowing_sigma)
                z[pix] = zz[pix]
                c_px = np.select([~np.isfinite(zz), zz >= st.Z_MILD, zz >= st.Z_MODERATE, zz >= st.Z_SEVERE],
                                 [np.nan, 1, 2, 3], 4).astype(np.float32)
                if look.get("class") == "establishing":
                    c_px = np.where(np.isfinite(zz), 5, np.nan).astype(np.float32)
                cls[pix] = c_px[pix]
            if np.isfinite(z).any():
                idx.add("anomaly", z, grid, when=d, sensor="Sentinel-2", clear_fraction=clear,
                        note="NDVI z-score vs same-crop fields at the same days after sowing")
                idx.add("stress_class", cls, grid, when=d, sensor="Sentinel-2", clear_fraction=clear,
                        classes=pr.STRESS_CLASSES)
    if inputs.s1 is not None and inputs.s1.n:
        for t, d in enumerate(inputs.s1.dates):
            vh = inputs.s1.bands["VH"][t]
            from scipy.ndimage import uniform_filter
            # 3x3 boxcar in linear power for display; field statistics average more pixels.
            lin = np.where(np.isfinite(vh), vh, 0.0)
            cnt = uniform_filter(np.isfinite(vh).astype(float), 3)
            with np.errstate(invalid="ignore", divide="ignore"):
                db = 10 * np.log10(uniform_filter(lin, 3) / cnt)
            db[~np.isfinite(vh)] = np.nan
            meta = inputs.s1.meta[t] if inputs.s1.meta else {}
            idx.add("vh", db.astype(np.float32), grid, when=d,
                    sensor=f"Sentinel-1 {meta.get('pass', '')} orbit {meta.get('relative_orbit', '')}",
                    clear_fraction=float(np.isfinite(vh[in_fields]).mean()) if in_fields.any() else None)
    sow = np.full(grid.shape, np.nan, np.float32)
    stage = np.full(grid.shape, np.nan, np.float32)
    stage_codes = {"pre-sowing": 1, "emergence": 2, "establishment": 2, "vegetative": 3, "tillering": 3,
                   "grand growth": 3, "flowering": 4, "boll": 4, "reproductive": 4, "pod fill": 4,
                   "heading": 4, "grain fill": 4, "fill": 4, "bulking": 4, "maturity": 5,
                   "ripening": 5, "harvested": 6}
    for fid in ids:
        r = recs[fid]
        pix = masks[fid].full
        if r["sowing"].get("date"):
            sow[pix] = date.fromisoformat(r["sowing"]["date"]).timetuple().tm_yday
        code = stage_codes.get(r.get("stage"))
        if code:
            stage[pix] = code
    idx.add("sowing_doy", sow, grid, scale="sowing_doy", note="Field sowing estimate, painted per field")
    idx.add("stage", stage, grid, when=req.as_of, classes=pr.STAGE_CLASSES, note="Stage as of the run date")
    idx.save()
    return idx.items


def _iso(d):
    return d.isoformat() if isinstance(d, date) else d


def _document(req: VillageRequest, recs: dict, masks: dict, onset: sw.Onset, cal: dict,
              products: Optional[list], inputs: Inputs, curves_lib: dict) -> dict:
    """Records in the §7 contract, plus the farms / zones / fields shapes the
    existing monitoring page already draws."""
    by_id = {str(f["field_id"]): f for f in req.fields}
    farms, zones, features = [], [], []
    stress_counts: dict[str, int] = {}
    for fid, r in recs.items():
        f = by_id[fid]
        s = r.get("stress") or {}
        latest = s.get("latest_type") or (s.get("latest_class") if s.get("latest_class") == "healthy" else None)
        label = (latest or "Unspecified").replace("healthy", "Healthy")
        stress_counts[label] = stress_counts.get(label, 0) + 1
        y = r.get("yield") or {}
        sow = r["sowing"]
        farms.append({
            "field_id": fid, "crop": r["crop"], "area_ha": f.get("area_ha") or r["area_ha"],
            "confidence": r.get("confidence"),
            "confidence_basis": r.get("confidence_basis"),
            "status": r["status"], "sowing_date": sow.get("date"), "sowing_p10": sow.get("p10"),
            "sowing_p90": sow.get("p90"), "harvest_date": r["harvest"].get("date"),
            "stage": r["stage"], "yield_t_ha": y.get("yield_t_ha"), "yield_index": y.get("yield_index"),
            "stress": label if latest else None, "qa_flags": r["qa_flags"],
            "crop_group": (r.get("crop_group") or {}).get("group"),
        })
        zones.append({
            "zone_id": f"{fid}-z1", "kind": "crop", "crop": r["crop"], "source_field_id": fid,
            "pixel_count": r["n_interior_pixels"], "split_reason": "boundary",
            "sowing": {"known": sow["status"] in ("estimated", "provided"), "date": sow.get("date"),
                       "early": sow.get("p10"), "late": sow.get("p90"),
                       "sources": sow.get("sources"), "note": sow.get("note"), "status": sow["status"]},
            "harvest": {"observed": r["harvest"]["observed"], "date": r["harvest"].get("date"),
                        "early": (r["harvest"].get("window") or {}).get("start"),
                        "late": (r["harvest"].get("window") or {}).get("end"),
                        "note": "Cotton is picked several times, October to January."
                        if r["crop"] == "Cotton" else ""},
            "progress": {"stage": r["stage"], "peak": r.get("peak"),
                         "harvest_window": [(r["harvest"].get("window") or {}).get("start"),
                                            (r["harvest"].get("window") or {}).get("end")]},
            "yield": ({"t_ha": y.get("yield_t_ha"), "low": y.get("yield_p10"), "high": y.get("yield_p90"),
                       "reference_pool": y.get("basis"), "note": y.get("label") or y.get("note")}
                      if y.get("yield_index") is not None else None),
            "intervals": [{"date": lk["date"], "kind": "optical",
                           "stress": {"type": lk.get("type") or ("Healthy" if lk.get("class") == "healthy" else None),
                                      "stressed_fraction": lk.get("stressed_fraction"),
                                      "class": lk.get("class"), "confirmed": lk.get("confirmed")}}
                          for lk in r.get("stress_looks", [])],
        })
        props = {
            "field_id": fid, "zone_id": f"{fid}-z1", "crop": r["crop"], "crop_name": r["crop"],
            "confidence": r.get("confidence"), "confidence_basis": r.get("confidence_basis"),
            "status": r["status"], "stress": label, "stage": r["stage"],
            "sowing_date": sow.get("date"), "sowing_p10": sow.get("p10"), "sowing_p90": sow.get("p90"),
            "harvest_date": r["harvest"].get("date"), "yield_t_ha": y.get("yield_t_ha"),
            "yield_low": y.get("yield_p10"), "yield_high": y.get("yield_p90"),
            "yield_index": y.get("yield_index"), "area_ha": f.get("area_ha") or r["area_ha"],
            "pixel_basis": r["pixel_basis"], "qa_flags": ",".join(r["qa_flags"]),
            "crop_group": (r.get("crop_group") or {}).get("group"),
            "color": _STRESS_COLORS.get(label, _STRESS_COLORS["Unspecified"]),
        }
        features.append({"type": "Feature", "geometry": f["geometry"], "properties": props})

    sowings = sorted(fm["sowing_date"] for fm in farms if fm.get("sowing_date"))
    yields = [fm["yield_t_ha"] for fm in farms if isinstance(fm.get("yield_t_ha"), (int, float))]
    confs = [fm["confidence"] for fm in farms if isinstance(fm.get("confidence"), (int, float))]
    return {
        "engine": ENGINE_VERSION,
        "name": req.name, "season": req.season, "as_of": req.as_of.isoformat(),
        "crop": sorted({r["crop"] for r in recs.values()})[0] if len({r["crop"] for r in recs.values()}) == 1 else "multiple",
        "crops": sorted({r["crop"] for r in recs.values()}),
        "farm_count": len(farms), "zone_count": len(zones),
        "confidence": round(float(np.mean(confs)), 4) if confs else None,
        "farms": farms, "zones": zones,
        "fields": {"type": "FeatureCollection", "features": features},
        "records": [{k: v for k, v in r.items() if k != "stress_looks"} for r in recs.values()],
        "cluster_summary": {
            "cluster_id": req.lineage.get("cluster_id"), "village": req.lineage.get("village"),
            "farm_count": len(farms), "excluded_count": 0,
            "mean_yield_t_ha": round(float(np.mean(yields)), 3) if yields else None,
            "sowing_earliest": sowings[0] if sowings else None,
            "sowing_latest": sowings[-1] if sowings else None,
            "stress_counts": stress_counts,
            "status_counts": _count(fm["status"] for fm in farms),
            "crop_group_counts": _count(((r.get("crop_group") or {}).get("group") or
                                         f"undecided:{(r.get('crop_group') or {}).get('status', 'no_fit')}")
                                        for r in recs.values()),
            "crop_group_area_ha": _area_by_group(recs),
        },
        "skipped": [],
        "onset": {"date": _iso(onset.date), "cumulative_mm": round(onset.cumulative_mm, 1),
                  "false_starts": [_iso(d) for d in onset.false_starts], "note": onset.note},
        "calibration": cal,
        "grid": {"epsg": inputs.grid.epsg, "width": inputs.grid.width, "height": inputs.grid.height,
                 "res_m": inputs.grid.res, "bounds_lonlat": inputs.grid.lonlat_bounds()},
        "observations": {
            "s2_dates": [d.isoformat() for d in (inputs.s2.dates if inputs.s2 else [])],
            "landsat_dates": [d.isoformat() for d in (inputs.landsat.dates if inputs.landsat else [])],
            "s1_dates": [d.isoformat() for d in (inputs.s1.dates if inputs.s1 else [])],
        },
        "rasters": {"products": products} if products is not None else None,
        "lineage": req.lineage,
        "models": {"engine": ENGINE_VERSION, "reference_curves": {
            k: {"source": c.source, "n_fields": c.n_fields} for k, c in sorted(curves_lib.items())}},
        "limits": [
            "Field numbers come from the same 10 m pixels as the maps. Fields under 8 interior "
            "pixels are flagged low_resolution and carry no within-field map.",
            "Weather (rain, temperature, soil water) is village-level: CHIRPS 5 km, ERA5-Land 9 km.",
            "Stress compares each field with same-crop fields at the same days after sowing. "
            "Pests and diseases are not identified.",
            "Yield is a relative index; tonnes per hectare appear only when an official district "
            "average is supplied, and are not field-calibrated.",
            "Reference crop curves: " + ", ".join(
                f"{k} ({c.source})" for k, c in sorted(curves_lib.items())) + ".",
        ],
    }


def _area_by_group(recs: dict) -> dict:
    out: dict = {}
    for r in recs.values():
        g = (r.get("crop_group") or {}).get("group") or "undecided"
        out[g] = round(out.get(g, 0.0) + float(r.get("area_ha") or 0.0), 2)
    return out


def _count(items) -> dict:
    out: dict = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out


_STRESS_COLORS = {
    "Healthy": "#009E73", "Water stress": "#0072B2", "Nutrient deficiency": "#E69F00",
    "Waterlogging": "#56B4E9", "Canopy damage (cause unconfirmed)": "#CC79A7",
    "Sub-optimal growth": "#F0E442", "Unspecified": "#8F8779",
}
